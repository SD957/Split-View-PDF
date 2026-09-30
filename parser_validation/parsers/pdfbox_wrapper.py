from __future__ import annotations

import glob
import os
from pathlib import Path

from .base import Capability, ParserWrapper, RenderResult, TextResult


class PDFBoxWrapper(ParserWrapper):
    def __init__(self, jar_path: str | None = None):
        self._jar_path_input = jar_path
        self._jar_path_resolved: str | None = None

        self._Loader = None
        self._PDDocument = None
        self._PDFTextStripper = None
        self._PDFRenderer = None
        self._ImageIO = None
        self._ByteArrayInputStream = None
        self._ByteArrayOutputStream = None
        self._ImageType = None
        self._RandomAccessReadBuffer = None

        self._pdfbox_v3 = False
        self._setup_done = False
        self._setup_error: str | None = None

    @property
    def name(self) -> str:
        return "pdfbox"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT, Capability.RENDER}

    def _resolve_jar_path(self) -> str | None:
        if self._jar_path_resolved and os.path.isfile(self._jar_path_resolved):
            return self._jar_path_resolved

        candidates: list[str] = []

        if self._jar_path_input:
            candidates.append(self._jar_path_input)

        env_path = os.environ.get("PDFBOX_JAR")
        if env_path:
            candidates.append(env_path)

        candidates.extend(sorted(glob.glob(str(Path.cwd() / "pdfbox-app-*.jar")), reverse=True))
        candidates.extend(
            sorted(
                glob.glob(str(Path.home() / ".local" / "lib" / "pdfbox-app-*.jar")),
                reverse=True,
            )
        )

        for path in candidates:
            if path and os.path.isfile(path):
                self._jar_path_resolved = os.path.abspath(path)
                return self._jar_path_resolved
        return None

    def is_available(self) -> bool:
        try:
            import jpype  # noqa: F401
        except Exception:
            return False
        return self._resolve_jar_path() is not None

    def setup(self):
        if self._setup_done:
            return
        if self._setup_error:
            raise RuntimeError(self._setup_error)

        try:
            import jpype

            jar_path = self._resolve_jar_path()
            if not jar_path:
                raise FileNotFoundError("PDFBox jar not found")

            if not jpype.isJVMStarted():
                jpype.startJVM(classpath=[jar_path])
            else:
                try:
                    jpype.addClassPath(jar_path)
                except Exception:
                    pass

            self._jar_path_resolved = jar_path

            try:
                self._Loader = jpype.JClass("org.apache.pdfbox.Loader")
                self._pdfbox_v3 = True
            except Exception:
                self._Loader = None
                self._pdfbox_v3 = False

            try:
                self._PDDocument = jpype.JClass("org.apache.pdfbox.pdmodel.PDDocument")
            except Exception:
                self._PDDocument = None

            self._PDFTextStripper = jpype.JClass("org.apache.pdfbox.text.PDFTextStripper")
            self._PDFRenderer = jpype.JClass("org.apache.pdfbox.rendering.PDFRenderer")
            self._ImageIO = jpype.JClass("javax.imageio.ImageIO")
            self._ByteArrayInputStream = jpype.JClass("java.io.ByteArrayInputStream")
            self._ByteArrayOutputStream = jpype.JClass("java.io.ByteArrayOutputStream")
            self._ImageType = jpype.JClass("org.apache.pdfbox.rendering.ImageType")
            try:
                self._RandomAccessReadBuffer = jpype.JClass("org.apache.pdfbox.io.RandomAccessReadBuffer")
            except Exception:
                self._RandomAccessReadBuffer = None

            if not self._pdfbox_v3 and self._PDDocument is None:
                raise RuntimeError("Unable to load PDFBox document class")

            self._setup_done = True
        except Exception as exc:
            self._setup_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(self._setup_error)

    def teardown(self):
        # JPype JVM should not be shutdown inside a shared process.
        return

    def _load_document(self, pdf_bytes: bytes):
        import jpype

        if not self._setup_done:
            self.setup()

        jbytes = jpype.JArray(jpype.JByte)(pdf_bytes)

        if self._pdfbox_v3 and self._Loader is not None:
            try:
                return self._Loader.loadPDF(jbytes)
            except Exception:
                stream = self._ByteArrayInputStream(jbytes)
                try:
                    return self._Loader.loadPDF(stream)
                except Exception:
                    if self._RandomAccessReadBuffer is None:
                        raise
                    rar = self._RandomAccessReadBuffer(stream)
                    return self._Loader.loadPDF(rar)

        if self._PDDocument is None:
            raise RuntimeError("PDDocument class is unavailable")

        try:
            return self._PDDocument.load(jbytes)
        except Exception:
            stream = self._ByteArrayInputStream(jbytes)
            return self._PDDocument.load(stream)

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warnings: list[str] = []
        try:
            self.setup()
        except Exception as exc:
            return TextResult(
                text="",
                page_texts=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

        doc = None
        try:
            doc = self._load_document(pdf_bytes)
            page_count = int(doc.getNumberOfPages())
            stripper = self._PDFTextStripper()
            page_texts: list[str] = []

            for page_idx in range(page_count):
                stripper.setStartPage(page_idx + 1)
                stripper.setEndPage(page_idx + 1)
                page_text = stripper.getText(doc)
                page_texts.append(str(page_text or ""))

            return TextResult(
                text="\f".join(page_texts),
                page_texts=page_texts,
                success=True,
                warnings=warnings,
            )
        except Exception as exc:
            return TextResult(
                text="",
                page_texts=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def render_page(self, pdf_bytes: bytes, page_num: int = 0, dpi: int = 150) -> RenderResult:
        warnings: list[str] = []
        try:
            self.setup()
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

        doc = None
        try:
            doc = self._load_document(pdf_bytes)
            page_count = int(doc.getNumberOfPages())
            if page_num < 0 or page_num >= page_count:
                return RenderResult(
                    pages=[],
                    success=False,
                    error=f"IndexError: page_num {page_num} out of range",
                    warnings=warnings,
                )

            renderer = self._PDFRenderer(doc)
            image = renderer.renderImageWithDPI(page_num, float(dpi), self._ImageType.RGB)
            baos = self._ByteArrayOutputStream()
            try:
                ok = self._ImageIO.write(image, "PNG", baos)
                if not ok:
                    return RenderResult(
                        pages=[],
                        success=False,
                        error="RuntimeError: ImageIO failed to encode PNG",
                        warnings=warnings,
                    )
                png_bytes = bytes(baos.toByteArray())
                return RenderResult(pages=[png_bytes], success=True, warnings=warnings)
            finally:
                try:
                    baos.close()
                except Exception:
                    pass
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

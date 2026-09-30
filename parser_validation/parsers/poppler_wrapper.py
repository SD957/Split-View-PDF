from __future__ import annotations

import glob
from io import BytesIO
import os
import shutil
import tempfile

from .base import (
    Capability,
    ParserWrapper,
    RenderResult,
    TextResult,
    capture_stderr_warnings,
    run_subprocess,
    temp_pdf_file,
)


class PopplerWrapper(ParserWrapper):
    """Poppler wrapper (binding first, subprocess fallback).

    Notes:
    - Default command names can collide with Xpdf binaries on some systems.
    - If both toolchains are installed, pass explicit absolute paths.
    """

    def __init__(self, pdftotext_path: str = "pdftotext", pdftoppm_path: str = "pdftoppm"):
        self._pdftotext_path = pdftotext_path
        self._pdftoppm_path = pdftoppm_path
        self._use_binding: bool | None = None

    @property
    def name(self) -> str:
        return "poppler"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT, Capability.RENDER}

    def _binary_exists(self, binary: str) -> bool:
        if os.path.sep in binary:
            return os.path.isfile(binary) and os.access(binary, os.X_OK)
        return shutil.which(binary) is not None

    def _detect_binding(self) -> bool:
        if self._use_binding is not None:
            return self._use_binding

        try:
            import poppler  # noqa: F401

            self._use_binding = True
        except Exception:
            self._use_binding = False
        return self._use_binding

    def setup(self):
        self._detect_binding()

    def is_available(self) -> bool:
        if self._detect_binding():
            return True
        return self._binary_exists(self._pdftotext_path) and self._binary_exists(self._pdftoppm_path)

    def _split_pages(self, text: str) -> list[str]:
        pages = text.split("\f")
        while pages and pages[-1] == "":
            pages.pop()
        if not pages:
            pages = [""]
        return pages

    def _extract_text_subprocess(self, pdf_bytes: bytes, extra_warnings: list[str]) -> TextResult:
        warnings = list(extra_warnings)
        with temp_pdf_file(pdf_bytes) as pdf_path:
            proc = run_subprocess([self._pdftotext_path, "-layout", pdf_path, "-"], timeout=15)
            warnings.extend(capture_stderr_warnings(proc.stderr))
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="replace").strip()
                return TextResult(
                    text="",
                    page_texts=[],
                    success=False,
                    error=err or f"RuntimeError: pdftotext exited with {proc.returncode}",
                    warnings=warnings,
                )

            text = proc.stdout.decode("utf-8", errors="replace")
            page_texts = self._split_pages(text)
            return TextResult(
                text="\f".join(page_texts),
                page_texts=page_texts,
                success=True,
                warnings=warnings,
            )

    def _render_subprocess(self, pdf_bytes: bytes, page_num: int, dpi: int, extra_warnings: list[str]) -> RenderResult:
        warnings = list(extra_warnings)
        with temp_pdf_file(pdf_bytes) as pdf_path:
            with tempfile.TemporaryDirectory(prefix="specdiff_poppler_") as tmpdir:
                prefix = os.path.join(tmpdir, "page")

                cmd_single = [
                    self._pdftoppm_path,
                    "-png",
                    "-r",
                    str(dpi),
                    "-f",
                    str(page_num + 1),
                    "-l",
                    str(page_num + 1),
                    "-singlefile",
                    pdf_path,
                    prefix,
                ]
                proc = run_subprocess(cmd_single, timeout=15)
                warnings.extend(capture_stderr_warnings(proc.stderr))

                out_paths: list[str] = []
                single_path = f"{prefix}.png"
                if os.path.exists(single_path):
                    out_paths = [single_path]

                if not out_paths:
                    cmd_multi = [
                        self._pdftoppm_path,
                        "-png",
                        "-r",
                        str(dpi),
                        "-f",
                        str(page_num + 1),
                        "-l",
                        str(page_num + 1),
                        pdf_path,
                        prefix,
                    ]
                    proc2 = run_subprocess(cmd_multi, timeout=15)
                    warnings.extend(capture_stderr_warnings(proc2.stderr))
                    out_paths = sorted(glob.glob(f"{prefix}*.png"))
                    if proc2.returncode != 0 and not out_paths:
                        err = proc2.stderr.decode("utf-8", errors="replace").strip()
                        return RenderResult(
                            pages=[],
                            success=False,
                            error=err or f"RuntimeError: pdftoppm exited with {proc2.returncode}",
                            warnings=warnings,
                        )

                if not out_paths:
                    err = proc.stderr.decode("utf-8", errors="replace").strip()
                    return RenderResult(
                        pages=[],
                        success=False,
                        error=err or "RuntimeError: pdftoppm produced no output PNG",
                        warnings=warnings,
                    )

                with open(out_paths[0], "rb") as handle:
                    png_bytes = handle.read()
                return RenderResult(pages=[png_bytes], success=True, warnings=warnings)

    def _pil_to_png(self, image) -> bytes:
        from PIL import Image

        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            rgb = background
        else:
            rgb = image.convert("RGB")

        output = BytesIO()
        rgb.save(output, format="PNG")
        return output.getvalue()

    def _binding_image_to_png(self, image_obj) -> bytes:
        from PIL import Image

        if hasattr(image_obj, "to_pil") and callable(image_obj.to_pil):
            return self._pil_to_png(image_obj.to_pil())

        data = getattr(image_obj, "data", None)
        width = getattr(image_obj, "width", None)
        height = getattr(image_obj, "height", None)
        stride = getattr(image_obj, "stride", None)

        width = width() if callable(width) else width
        height = height() if callable(height) else height
        stride = stride() if callable(stride) else stride

        if data is None or not isinstance(width, int) or not isinstance(height, int):
            raise RuntimeError("python-poppler image object has no raw data")

        raw = data.tobytes() if hasattr(data, "tobytes") else bytes(data)
        if isinstance(stride, int) and stride > 0 and len(raw) >= stride * height:
            row_bytes = width * 4
            if stride >= row_bytes:
                trimmed = bytearray()
                for row in range(height):
                    start = row * stride
                    trimmed.extend(raw[start : start + row_bytes])
                raw = bytes(trimmed)

        expected = width * height * 4
        if len(raw) < expected:
            raise RuntimeError("python-poppler image raw data length is invalid")
        raw = raw[:expected]

        for decoder in ("ARGB", "BGRA", "RGBA"):
            try:
                image = Image.frombuffer("RGBA", (width, height), raw, "raw", decoder, 0, 1)
                return self._pil_to_png(image)
            except Exception:
                continue

        image = Image.frombytes("RGBA", (width, height), raw)
        return self._pil_to_png(image)

    def _extract_text_binding(self, pdf_bytes: bytes, extra_warnings: list[str]) -> TextResult:
        import poppler

        warnings = list(extra_warnings)
        doc = poppler.load_from_data(pdf_bytes)
        page_count = int(doc.pages)

        page_texts: list[str] = []
        for idx in range(page_count):
            page = doc.create_page(idx)
            try:
                page_texts.append(page.text() or "")
            finally:
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    try:
                        close_page()
                    except Exception:
                        pass

        return TextResult(
            text="\f".join(page_texts),
            page_texts=page_texts,
            success=True,
            warnings=warnings,
        )

    def _render_binding(self, pdf_bytes: bytes, page_num: int, dpi: int, extra_warnings: list[str]) -> RenderResult:
        import poppler

        warnings = list(extra_warnings)
        doc = poppler.load_from_data(pdf_bytes)
        page_count = int(doc.pages)
        if page_num < 0 or page_num >= page_count:
            return RenderResult(
                pages=[],
                success=False,
                error=f"IndexError: page_num {page_num} out of range",
                warnings=warnings,
            )

        page = doc.create_page(page_num)
        try:
            renderer = poppler.PageRenderer()
            image = renderer.render_page(page, xres=dpi, yres=dpi)
            png_bytes = self._binding_image_to_png(image)
            return RenderResult(pages=[png_bytes], success=True, warnings=warnings)
        finally:
            close_page = getattr(page, "close", None)
            if callable(close_page):
                try:
                    close_page()
                except Exception:
                    pass

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warnings: list[str] = []
        try:
            if self._detect_binding():
                try:
                    return self._extract_text_binding(pdf_bytes, warnings)
                except Exception as exc:
                    warnings.append(
                        f"python-poppler binding failed, fallback to subprocess: {type(exc).__name__}: {exc}"
                    )
            return self._extract_text_subprocess(pdf_bytes, warnings)
        except Exception as exc:
            return TextResult(
                text="",
                page_texts=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

    def render_page(self, pdf_bytes: bytes, page_num: int = 0, dpi: int = 150) -> RenderResult:
        warnings: list[str] = []
        try:
            if self._detect_binding():
                try:
                    return self._render_binding(pdf_bytes, page_num, dpi, warnings)
                except Exception as exc:
                    warnings.append(
                        f"python-poppler binding failed, fallback to subprocess: {type(exc).__name__}: {exc}"
                    )
            return self._render_subprocess(pdf_bytes, page_num, dpi, warnings)
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

from __future__ import annotations

import glob
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


class XpdfWrapper(ParserWrapper):
    """Xpdf CLI wrapper.

    Notes:
    - Default command names can collide with Poppler binaries on some systems.
    - If both toolchains are installed, pass explicit absolute paths.
    """

    def __init__(self, pdftotext_path: str = "pdftotext", pdftopng_path: str = "pdftopng"):
        self._pdftotext_path = pdftotext_path
        self._pdftopng_path = pdftopng_path

    @property
    def name(self) -> str:
        return "xpdf"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT, Capability.RENDER}

    def _binary_exists(self, binary: str) -> bool:
        if os.path.sep in binary:
            return os.path.isfile(binary) and os.access(binary, os.X_OK)
        return shutil.which(binary) is not None

    def is_available(self) -> bool:
        return self._binary_exists(self._pdftotext_path) and self._binary_exists(self._pdftopng_path)

    def _split_pages(self, text: str) -> list[str]:
        pages = text.split("\f")
        while pages and pages[-1] == "":
            pages.pop()
        if not pages:
            pages = [""]
        return pages

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warnings: list[str] = []
        try:
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
            with temp_pdf_file(pdf_bytes) as pdf_path:
                with tempfile.TemporaryDirectory(prefix="specdiff_xpdf_") as tmpdir:
                    prefix = os.path.join(tmpdir, "page")
                    cmd = [
                        self._pdftopng_path,
                        "-r",
                        str(dpi),
                        "-f",
                        str(page_num + 1),
                        "-l",
                        str(page_num + 1),
                        pdf_path,
                        prefix,
                    ]
                    proc = run_subprocess(cmd, timeout=15)
                    warnings.extend(capture_stderr_warnings(proc.stderr))

                    out_paths = sorted(glob.glob(f"{prefix}*.png"))
                    if not out_paths:
                        err = proc.stderr.decode("utf-8", errors="replace").strip()
                        return RenderResult(
                            pages=[],
                            success=False,
                            error=err or f"RuntimeError: pdftopng exited with {proc.returncode}",
                            warnings=warnings,
                        )

                    with open(out_paths[0], "rb") as handle:
                        png_bytes = handle.read()
                    return RenderResult(pages=[png_bytes], success=True, warnings=warnings)
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

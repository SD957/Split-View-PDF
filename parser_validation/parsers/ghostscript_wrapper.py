from __future__ import annotations

import os
import shutil
import tempfile

from .base import Capability, ParserWrapper, RenderResult, TextResult, run_subprocess, temp_pdf_file


class GhostscriptWrapper(ParserWrapper):
    def __init__(self, gs_path: str = "gs"):
        self._gs_path = gs_path

    @property
    def name(self) -> str:
        return "ghostscript"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT, Capability.RENDER}

    def is_available(self) -> bool:
        if os.path.sep in self._gs_path:
            return os.path.isfile(self._gs_path) and os.access(self._gs_path, os.X_OK)
        return shutil.which(self._gs_path) is not None

    def _extract_warning_lines(self, stderr: bytes) -> list[str]:
        if not stderr:
            return []
        text = stderr.decode("utf-8", errors="replace")
        out: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            low = stripped.lower()
            if "warning" in low or "error" in low:
                out.append(stripped)
        return out

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
                with tempfile.TemporaryDirectory(prefix="specdiff_gs_text_") as tmpdir:
                    out_txt = os.path.join(tmpdir, "out.txt")
                    cmd = [
                        self._gs_path,
                        "-q",
                        "-dNOPAUSE",
                        "-dBATCH",
                        "-dSAFER",
                        "-sDEVICE=txtwrite",
                        f"-sOutputFile={out_txt}",
                        pdf_path,
                    ]
                    proc = run_subprocess(cmd, timeout=15)
                    warnings.extend(self._extract_warning_lines(proc.stderr))

                    if proc.returncode != 0 and not os.path.exists(out_txt):
                        err = proc.stderr.decode("utf-8", errors="replace").strip()
                        return TextResult(
                            text="",
                            page_texts=[],
                            success=False,
                            error=err or f"RuntimeError: gs exited with {proc.returncode}",
                            warnings=warnings,
                        )

                    if not os.path.exists(out_txt):
                        return TextResult(
                            text="",
                            page_texts=[],
                            success=False,
                            error="RuntimeError: Ghostscript did not produce text output",
                            warnings=warnings,
                        )

                    with open(out_txt, "rb") as handle:
                        text = handle.read().decode("utf-8", errors="replace")
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
                with tempfile.TemporaryDirectory(prefix="specdiff_gs_render_") as tmpdir:
                    out_png = os.path.join(tmpdir, "page.png")
                    cmd = [
                        self._gs_path,
                        "-q",
                        "-dNOPAUSE",
                        "-dBATCH",
                        "-dSAFER",
                        "-sDEVICE=png16m",
                        f"-r{dpi}",
                        f"-dFirstPage={page_num + 1}",
                        f"-dLastPage={page_num + 1}",
                        f"-sOutputFile={out_png}",
                        pdf_path,
                    ]
                    proc = run_subprocess(cmd, timeout=15)
                    warnings.extend(self._extract_warning_lines(proc.stderr))

                    if proc.returncode != 0 and not os.path.exists(out_png):
                        err = proc.stderr.decode("utf-8", errors="replace").strip()
                        return RenderResult(
                            pages=[],
                            success=False,
                            error=err or f"RuntimeError: gs exited with {proc.returncode}",
                            warnings=warnings,
                        )

                    if not os.path.exists(out_png):
                        return RenderResult(
                            pages=[],
                            success=False,
                            error="RuntimeError: Ghostscript did not produce render output",
                            warnings=warnings,
                        )

                    with open(out_png, "rb") as handle:
                        png_bytes = handle.read()
                    return RenderResult(pages=[png_bytes], success=True, warnings=warnings)
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

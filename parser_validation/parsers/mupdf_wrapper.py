from __future__ import annotations

from .base import Capability, ParserWrapper, RenderResult, TextResult


class MuPDFWrapper(ParserWrapper):
    @property
    def name(self) -> str:
        return "mupdf"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT, Capability.RENDER}

    def is_available(self) -> bool:
        try:
            import fitz  # noqa: F401

            return True
        except Exception:
            return False

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warnings: list[str] = []
        try:
            import fitz

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                if getattr(doc, "is_encrypted", False):
                    warnings.append("Document is encrypted")
                if getattr(doc, "needs_pass", False):
                    warnings.append("Document requires password")

                page_texts = []
                for page in doc:
                    page_texts.append(page.get_text("text") or "")

                return TextResult(
                    text="\f".join(page_texts),
                    page_texts=page_texts,
                    success=True,
                    warnings=warnings,
                )
            finally:
                doc.close()
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
            import fitz

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                if getattr(doc, "is_encrypted", False):
                    warnings.append("Document is encrypted")
                if getattr(doc, "needs_pass", False):
                    warnings.append("Document requires password")

                if page_num < 0 or page_num >= doc.page_count:
                    return RenderResult(
                        pages=[],
                        success=False,
                        error=f"IndexError: page_num {page_num} out of range",
                        warnings=warnings,
                    )

                page = doc.load_page(page_num)
                zoom = dpi / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                png_bytes = pix.tobytes("png")
                return RenderResult(pages=[png_bytes], success=True, warnings=warnings)
            finally:
                doc.close()
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

from __future__ import annotations

from io import BytesIO
import warnings as pywarnings

from .base import Capability, ParserWrapper, RenderResult, TextResult


class PyPDFWrapper(ParserWrapper):
    @property
    def name(self) -> str:
        return "pypdf"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT}

    def is_available(self) -> bool:
        try:
            import pypdf  # noqa: F401

            return True
        except Exception:
            return False

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warning_messages: list[str] = []
        try:
            from pypdf import PdfReader

            with pywarnings.catch_warnings(record=True) as caught:
                pywarnings.simplefilter("always")
                reader = PdfReader(BytesIO(pdf_bytes))
                page_texts = []
                for page in reader.pages:
                    page_texts.append(page.extract_text() or "")

                warning_messages = [str(item.message) for item in caught]

            return TextResult(
                text="\f".join(page_texts),
                page_texts=page_texts,
                success=True,
                warnings=warning_messages,
            )
        except Exception as exc:
            return TextResult(
                text="",
                page_texts=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warning_messages,
            )

    def render_page(self, pdf_bytes: bytes, page_num: int = 0, dpi: int = 150) -> RenderResult:
        return RenderResult(
            pages=[],
            success=False,
            error="pypdf does not support rendering",
            warnings=[],
        )

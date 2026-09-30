from __future__ import annotations

from io import BytesIO, StringIO
import logging

from .base import Capability, ParserWrapper, RenderResult, TextResult


class _ListWarningHandler(logging.Handler):
    def __init__(self, sink: list[str]):
        super().__init__(level=logging.WARNING)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record.getMessage())


class PdfminerWrapper(ParserWrapper):
    @property
    def name(self) -> str:
        return "pdfminer"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT}

    def is_available(self) -> bool:
        try:
            import pdfminer  # noqa: F401

            return True
        except Exception:
            return False

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warning_messages: list[str] = []
        logger = logging.getLogger("pdfminer")
        handler = _ListWarningHandler(warning_messages)
        old_level = logger.level
        logger.addHandler(handler)
        if old_level == logging.NOTSET:
            logger.setLevel(logging.WARNING)
        else:
            logger.setLevel(min(old_level, logging.WARNING))

        try:
            from pdfminer.converter import TextConverter
            from pdfminer.layout import LAParams
            from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
            from pdfminer.pdfpage import PDFPage

            resource_manager = PDFResourceManager()
            laparams = LAParams()
            page_texts: list[str] = []

            stream = BytesIO(pdf_bytes)
            for pdf_page in PDFPage.get_pages(stream, check_extractable=False):
                output = StringIO()
                device = TextConverter(resource_manager, output, laparams=laparams)
                try:
                    interpreter = PDFPageInterpreter(resource_manager, device)
                    interpreter.process_page(pdf_page)
                    page_texts.append(output.getvalue())
                finally:
                    try:
                        device.close()
                    except Exception:
                        pass
                    output.close()

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
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

    def render_page(self, pdf_bytes: bytes, page_num: int = 0, dpi: int = 150) -> RenderResult:
        return RenderResult(
            pages=[],
            success=False,
            error="pdfminer.six does not support rendering",
            warnings=[],
        )

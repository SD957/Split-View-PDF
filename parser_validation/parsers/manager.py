from __future__ import annotations

from .base import Capability, ParserWrapper, RenderResult, TextResult


class ParserManager:
    """Manage parser wrappers and batch execution."""

    def __init__(self):
        self.parsers: dict[str, ParserWrapper] = {}

    def register(self, wrapper: ParserWrapper):
        """Register wrapper. Keep unavailable ones for visibility."""
        self.parsers[wrapper.name] = wrapper
        if not wrapper.is_available():
            print(f"[WARN] Parser '{wrapper.name}' is not currently available")

    def setup_all(self):
        """Setup all available wrappers."""
        for name, parser in self.parsers.items():
            if parser.is_available():
                try:
                    parser.setup()
                except Exception as exc:
                    print(f"[WARN] Failed to setup {name}: {exc}")

    def teardown_all(self):
        """Teardown all wrappers."""
        for parser in self.parsers.values():
            try:
                parser.teardown()
            except Exception:
                pass

    def available_parsers(self) -> list[ParserWrapper]:
        return [parser for parser in self.parsers.values() if parser.is_available()]

    def text_parsers(self) -> list[ParserWrapper]:
        return [
            parser
            for parser in self.available_parsers()
            if Capability.TEXT_EXTRACT in parser.capabilities
        ]

    def render_parsers(self) -> list[ParserWrapper]:
        return [
            parser
            for parser in self.available_parsers()
            if Capability.RENDER in parser.capabilities
        ]

    def run_text_all(self, pdf_bytes: bytes) -> dict[str, TextResult]:
        """Run text extraction across all available text parsers."""
        return {parser.name: parser.extract_text(pdf_bytes) for parser in self.text_parsers()}

    def run_render_all(
        self, pdf_bytes: bytes, page_num: int = 0, dpi: int = 150
    ) -> dict[str, RenderResult]:
        """Run page rendering across all available render parsers."""
        return {
            parser.name: parser.render_page(pdf_bytes, page_num, dpi)
            for parser in self.render_parsers()
        }

from __future__ import annotations

from io import BytesIO

from .base import Capability, ParserWrapper, RenderResult, TextResult


class PDFiumWrapper(ParserWrapper):
    @property
    def name(self) -> str:
        return "pdfium"

    @property
    def capabilities(self) -> set[Capability]:
        return {Capability.TEXT_EXTRACT, Capability.RENDER}

    def is_available(self) -> bool:
        try:
            import pypdfium2  # noqa: F401
            from PIL import Image  # noqa: F401

            return True
        except Exception:
            return False

    def _image_to_png_bytes(self, image) -> bytes:
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

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warnings: list[str] = []
        pdf = None
        try:
            import pypdfium2 as pdfium

            page_texts: list[str] = []
            pdf = pdfium.PdfDocument(pdf_bytes)
            try:
                page_count = len(pdf)
                for page_idx in range(page_count):
                    page = None
                    textpage = None
                    try:
                        page = pdf[page_idx]
                        textpage = page.get_textpage()
                        text = textpage.get_text_range() or ""
                        page_texts.append(text)
                    finally:
                        if textpage is not None:
                            try:
                                textpage.close()
                            except Exception:
                                pass
                        if page is not None:
                            try:
                                page.close()
                            except Exception:
                                pass

                return TextResult(
                    text="\f".join(page_texts),
                    page_texts=page_texts,
                    success=True,
                    warnings=warnings,
                )
            finally:
                try:
                    pdf.close()
                except Exception:
                    pass
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
        pdf = None
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(pdf_bytes)
            try:
                page_count = len(pdf)
                if page_num < 0 or page_num >= page_count:
                    return RenderResult(
                        pages=[],
                        success=False,
                        error=f"IndexError: page_num {page_num} out of range",
                        warnings=warnings,
                    )

                page = None
                bitmap = None
                try:
                    page = pdf[page_num]
                    bitmap = page.render(scale=dpi / 72.0)
                    image = bitmap.to_pil()
                    png_bytes = self._image_to_png_bytes(image)
                    return RenderResult(pages=[png_bytes], success=True, warnings=warnings)
                finally:
                    if bitmap is not None:
                        try:
                            bitmap.close()
                        except Exception:
                            pass
                    if page is not None:
                        try:
                            page.close()
                        except Exception:
                            pass
            finally:
                try:
                    pdf.close()
                except Exception:
                    pass
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

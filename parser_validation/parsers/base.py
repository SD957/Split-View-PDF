from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import os
import subprocess
import tempfile
from typing import Optional


class Capability(Enum):
    TEXT_EXTRACT = "text"
    RENDER = "render"


@dataclass
class TextResult:
    """Text extraction result."""

    text: str
    page_texts: list[str]
    success: bool
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    @property
    def num_pages(self) -> int:
        return len(self.page_texts)


@dataclass
class RenderResult:
    """Rendering result."""

    pages: list[bytes]
    success: bool
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def page_hash(self, page_idx: int = 0) -> str:
        return hashlib.sha256(self.pages[page_idx]).hexdigest()[:16]


class ParserWrapper(ABC):
    """Unified interface for all PDF parser engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Engine identifier, e.g. 'mupdf', 'pdfium', 'poppler'."""

    @property
    @abstractmethod
    def capabilities(self) -> set[Capability]:
        """Capability set supported by this engine."""

    @abstractmethod
    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        """Extract text from PDF bytes. Never raises outward."""

    @abstractmethod
    def render_page(self, pdf_bytes: bytes, page_num: int = 0, dpi: int = 150) -> RenderResult:
        """Render a PDF page to PNG bytes. Never raises outward."""

    def setup(self):
        """Optional one-time initialization."""

    def teardown(self):
        """Optional resource cleanup."""

    def is_available(self) -> bool:
        """Return True when the engine and its dependencies are available."""
        return True

    def __repr__(self) -> str:
        available = self.is_available()
        return f"<{type(self).__name__} name={self.name!r} available={available}>"


@contextmanager
def temp_pdf_file(pdf_bytes: bytes):
    """Write bytes to a temporary PDF path and cleanup automatically."""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(pdf_bytes)
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


def run_subprocess(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """Run subprocess with timeout and captured stdout/stderr."""
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


def capture_stderr_warnings(stderr_bytes: bytes) -> list[str]:
    """Extract non-empty lines from stderr as warning candidates."""
    if not stderr_bytes:
        return []
    text = stderr_bytes.decode("utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]

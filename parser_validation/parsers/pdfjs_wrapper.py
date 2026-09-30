from __future__ import annotations

import base64
import json
import os
import select
import shutil
import subprocess
import threading

from .base import Capability, ParserWrapper, RenderResult, TextResult


class PDFjsWrapper(ParserWrapper):
    def __init__(self, worker_path: str | None = None, node_path: str = "node"):
        self._node_path = node_path
        self._worker_path = worker_path or os.path.join(os.path.dirname(__file__), "pdfjs_worker.js")

        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

        self._supports_render = True
        self._setup_error: str | None = None

    @property
    def name(self) -> str:
        return "pdfjs"

    @property
    def capabilities(self) -> set[Capability]:
        caps = {Capability.TEXT_EXTRACT}
        if self._supports_render:
            caps.add(Capability.RENDER)
        return caps

    def _worker_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self._worker_path))

    def _check_pdfjs_dist(self) -> bool:
        worker_dir = self._worker_dir()
        checks = [
            [
                self._node_path,
                "-e",
                "import('pdfjs-dist/legacy/build/pdf.mjs').then(() => process.exit(0)).catch(() => process.exit(1))",
            ],
            [
                self._node_path,
                "-e",
                "import('pdfjs-dist').then(() => process.exit(0)).catch(() => process.exit(1))",
            ],
        ]

        for cmd in checks:
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                    check=False,
                    cwd=worker_dir,
                )
                if proc.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    def is_available(self) -> bool:
        if os.path.sep in self._node_path:
            if not (os.path.isfile(self._node_path) and os.access(self._node_path, os.X_OK)):
                return False
        elif shutil.which(self._node_path) is None:
            return False

        if not os.path.isfile(self._worker_path):
            return False

        return self._check_pdfjs_dist()

    def _readline_with_timeout(self, stream, timeout: int = 15) -> str:
        # select() on pipes is Linux/Unix-friendly; this wrapper currently targets Linux.
        ready, _, _ = select.select([stream], [], [], timeout)
        if not ready:
            raise TimeoutError("Timed out waiting for PDF.js worker response")
        line = stream.readline()
        if line == "":
            raise RuntimeError("PDF.js worker exited unexpectedly")
        return line

    def _normalize_warnings(self, value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def setup(self):
        if self._proc is not None and self._proc.poll() is None:
            return

        if not self.is_available():
            self._setup_error = "PDF.js dependencies are not available"
            raise RuntimeError(self._setup_error)

        worker_dir = self._worker_dir()
        self._proc = subprocess.Popen(
            [self._node_path, self._worker_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=worker_dir,
        )

        try:
            response = self._call_worker({"action": "ping"})
            if not response.get("success", False):
                err = str(response.get("error", "PDF.js worker ping failed"))
                raise RuntimeError(err)
            self._supports_render = bool(response.get("supports_render", False))
            self._setup_error = None
        except Exception as exc:
            self.teardown()
            self._setup_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(self._setup_error)

    def teardown(self):
        if self._proc is None:
            return

        proc = self._proc
        self._proc = None

        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except Exception:
            pass

        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(proc, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def _call_worker(self, payload: dict) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                raise RuntimeError("PDF.js worker is not running")
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("PDF.js worker streams are unavailable")

            message = json.dumps(payload, ensure_ascii=False)
            proc.stdin.write(message + "\n")
            proc.stdin.flush()

            line = self._readline_with_timeout(proc.stdout, timeout=15)
            response = json.loads(line)
            if not isinstance(response, dict):
                raise RuntimeError("PDF.js worker returned invalid payload")
            return response

    def extract_text(self, pdf_bytes: bytes) -> TextResult:
        warnings: list[str] = []
        try:
            self.setup()
            response = self._call_worker(
                {
                    "action": "extract_text",
                    "pdf": base64.b64encode(pdf_bytes).decode("ascii"),
                }
            )
            warnings.extend(self._normalize_warnings(response.get("warnings")))

            if not response.get("success", False):
                return TextResult(
                    text="",
                    page_texts=[],
                    success=False,
                    error=str(response.get("error", "Unknown worker error")),
                    warnings=warnings,
                )

            pages = response.get("pages")
            if not isinstance(pages, list):
                raise RuntimeError("Invalid worker response: 'pages' is not a list")

            page_texts = [str(item) for item in pages]
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
            self.setup()
            if not self._supports_render:
                return RenderResult(
                    pages=[],
                    success=False,
                    error="PDF.js rendering is unavailable (canvas npm package is missing)",
                    warnings=warnings,
                )

            response = self._call_worker(
                {
                    "action": "render",
                    "pdf": base64.b64encode(pdf_bytes).decode("ascii"),
                    "page": int(page_num),
                    "dpi": int(dpi),
                }
            )
            warnings.extend(self._normalize_warnings(response.get("warnings")))

            if not response.get("success", False):
                return RenderResult(
                    pages=[],
                    success=False,
                    error=str(response.get("error", "Unknown worker error")),
                    warnings=warnings,
                )

            png_b64 = response.get("png")
            if not isinstance(png_b64, str):
                raise RuntimeError("Invalid worker response: missing 'png' field")
            png_bytes = base64.b64decode(png_b64)
            return RenderResult(pages=[png_bytes], success=True, warnings=warnings)
        except Exception as exc:
            return RenderResult(
                pages=[],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                warnings=warnings,
            )

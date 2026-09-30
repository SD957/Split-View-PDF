#!/usr/bin/env python3
"""Run the open-source PDF stacks reported in the paper on PDF inputs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from parsers import (
    Capability,
    GhostscriptWrapper,
    MuPDFWrapper,
    PDFBoxWrapper,
    PDFiumWrapper,
    PDFjsWrapper,
    ParserManager,
    PdfminerWrapper,
    PopplerWrapper,
    PyPDFWrapper,
    XpdfWrapper,
)


HERE = Path(__file__).resolve().parent
DUAL_ENGINES = (
    "mupdf",
    "poppler",
    "xpdf",
    "pdfium",
    "pdfbox",
    "pdfjs",
    "ghostscript",
)
TEXT_ENGINES = (
    "pypdf",
    "pdfminer",
    "llamaparse",
    "opendataloader",
    "docling",
    "unstructured",
)
ALL_ENGINES = DUAL_ENGINES + TEXT_ENGINES


def executable(env_name: str, local_path: Path, command: str) -> str | None:
    configured = os.environ.get(env_name)
    candidates = [configured, str(local_path), shutil.which(command)]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    return None


def xpdf_binary(name: str) -> str:
    configured = os.environ.get("XPDF_BIN_DIR")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured) / name)
    candidates.extend(sorted((HERE / "third_party").glob(f"xpdf-tools-*/bin64/{name}")))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return f"xpdf-{name}-not-configured"


def pdfbox_jar() -> str | None:
    configured = os.environ.get("PDFBOX_JAR")
    candidates = [Path(configured)] if configured else []
    candidates.extend(sorted((HERE / "third_party").glob("pdfbox-app-*.jar"), reverse=True))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def build_manager() -> ParserManager:
    manager = ParserManager()
    manager.register(MuPDFWrapper())
    manager.register(PopplerWrapper("pdftotext", "pdftoppm"))
    manager.register(XpdfWrapper(xpdf_binary("pdftotext"), xpdf_binary("pdftopng")))
    manager.register(PDFiumWrapper())
    manager.register(PDFBoxWrapper(pdfbox_jar()))
    manager.register(PDFjsWrapper(worker_path=str(HERE / "parsers" / "pdfjs_worker.js")))
    manager.register(GhostscriptWrapper("gs"))
    manager.register(PyPDFWrapper())
    manager.register(PdfminerWrapper())
    manager.setup_all()
    return manager


def clean_markdown(text: str) -> str:
    text = text.replace("\\_", "_")
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    return re.sub(r"(?m)^\s*[-*+]\s+", "", text).strip()


def command_result(command: list[str], output_path: Path, timeout: int = 1800) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        text = output_path.read_text(errors="replace") if output_path.exists() else ""
        return {
            "available": True,
            "success": proc.returncode == 0 and output_path.exists(),
            "text": text,
            "error": None if proc.returncode == 0 else (proc.stderr or proc.stdout).strip(),
            "elapsed_sec": round(time.time() - started, 3),
        }
    except Exception as exc:  # experiment harness records failures rather than aborting
        return {
            "available": True,
            "success": False,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": round(time.time() - started, 3),
        }


def run_llamaparse(pdf_path: Path) -> dict[str, Any]:
    binary = executable(
        "LITEPARSE_BIN", HERE / "liteparse" / "node_modules" / ".bin" / "lit", "lit"
    )
    if not binary:
        return {"available": False, "success": False, "text": "", "error": "LiteParse CLI not found"}
    with tempfile.TemporaryDirectory(prefix="artifact_liteparse_") as tmp:
        output = Path(tmp) / "output.txt"
        return command_result([binary, "parse", str(pdf_path), "-q", "-o", str(output), "--no-ocr"], output)


def run_opendataloader(pdf_path: Path) -> dict[str, Any]:
    binary = executable(
        "OPENDATALOADER_BIN", HERE / ".venv" / "bin" / "opendataloader-pdf", "opendataloader-pdf"
    )
    if not binary:
        return {"available": False, "success": False, "text": "", "error": "opendataloader-pdf not found"}
    with tempfile.TemporaryDirectory(prefix="artifact_opendataloader_") as tmp:
        output_dir = Path(tmp)
        output = output_dir / f"{pdf_path.stem}.txt"
        return command_result([binary, "-q", "-f", "text", "-o", str(output_dir), str(pdf_path)], output)


def run_docling(pdf_path: Path) -> dict[str, Any]:
    binary = executable("DOCLING_BIN", HERE / ".venv" / "bin" / "docling", "docling")
    if not binary:
        return {"available": False, "success": False, "text": "", "error": "docling CLI not found"}
    with tempfile.TemporaryDirectory(prefix="artifact_docling_") as tmp:
        output_dir = Path(tmp)
        output = output_dir / f"{pdf_path.stem}.md"
        result = command_result(
            [binary, str(pdf_path), "--to", "md", "--output", str(output_dir), "--no-ocr"],
            output,
        )
        result["text"] = clean_markdown(result["text"])
        return result


def run_unstructured(pdf_path: Path) -> dict[str, Any]:
    started = time.time()
    try:
        from langchain_community.document_loaders import UnstructuredPDFLoader

        loader = UnstructuredPDFLoader(
            str(pdf_path),
            mode="elements",
            strategy="fast",
            extract_images_in_pdf=False,
            infer_table_structure=False,
        )
        documents = loader.load()
        return {
            "available": True,
            "success": True,
            "text": "\n".join(doc.page_content for doc in documents if doc.page_content),
            "error": None,
            "elapsed_sec": round(time.time() - started, 3),
        }
    except ImportError as exc:
        return {"available": False, "success": False, "text": "", "error": str(exc)}
    except Exception as exc:  # experiment harness records failures rather than aborting
        return {
            "available": True,
            "success": False,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": round(time.time() - started, 3),
        }


EXTERNAL_TEXT_RUNNERS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "llamaparse": run_llamaparse,
    "opendataloader": run_opendataloader,
    "docling": run_docling,
    "unstructured": run_unstructured,
}


def input_pdfs(path: Path) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path.resolve()]
    if path.is_dir():
        return sorted(item.resolve() for item in path.glob("*.pdf"))
    raise SystemExit(f"Input must be a PDF or a directory containing PDFs: {path}")


def run_one(
    pdf_path: Path,
    output_root: Path,
    engines: set[str],
    manager: ParserManager,
    page_index: int,
    dpi: int,
) -> dict[str, Any]:
    sample_dir = output_root / pdf_path.stem
    sample_dir.mkdir(parents=True, exist_ok=True)
    pdf_bytes = pdf_path.read_bytes()
    record: dict[str, Any] = {
        "input": str(pdf_path),
        "page": page_index + 1,
        "dpi": dpi,
        "engines": {},
    }

    for name, parser in manager.parsers.items():
        if name not in engines:
            continue
        available = parser.is_available()
        engine: dict[str, Any] = {
            "available": available,
            "text_success": False,
            "render_success": False,
        }
        if not available:
            engine["error"] = "dependency unavailable; see README.md"
            record["engines"][name] = engine
            continue

        if Capability.TEXT_EXTRACT in parser.capabilities:
            result = parser.extract_text(pdf_bytes)
            engine["text_success"] = result.success
            engine["text_error"] = result.error
            engine["text_warnings"] = result.warnings
            if result.success:
                text_path = sample_dir / f"{name}.txt"
                text_path.write_text(result.text, errors="replace")
                engine["text_output"] = text_path.name

        if Capability.RENDER in parser.capabilities:
            result = parser.render_page(pdf_bytes, page_num=page_index, dpi=dpi)
            engine["render_success"] = bool(result.success and result.pages)
            engine["render_error"] = result.error
            engine["render_warnings"] = result.warnings
            if result.success and result.pages:
                png_path = sample_dir / f"{name}.page-{page_index + 1:03d}.png"
                png_path.write_bytes(result.pages[0])
                engine["render_output"] = png_path.name
        record["engines"][name] = engine

    for name, runner in EXTERNAL_TEXT_RUNNERS.items():
        if name not in engines:
            continue
        result = runner(pdf_path)
        engine = {
            "available": result.get("available", False),
            "text_success": result.get("success", False),
            "render_success": False,
            "text_error": result.get("error"),
            "elapsed_sec": result.get("elapsed_sec"),
        }
        if result.get("success"):
            text_path = sample_dir / f"{name}.txt"
            text_path.write_text(result.get("text", ""), errors="replace")
            engine["text_output"] = text_path.name
        record["engines"][name] = engine

    (sample_dir / "manifest.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PDF file or a flat directory of PDFs")
    parser.add_argument("--output-dir", type=Path, default=HERE / "outputs")
    parser.add_argument("--page", type=int, default=1, help="1-based page to render")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--engines", nargs="+", choices=ALL_ENGINES, default=list(ALL_ENGINES))
    args = parser.parse_args()
    if args.page < 1:
        raise SystemExit("--page must be at least 1")

    files = input_pdfs(args.input)
    if not files:
        raise SystemExit(f"No PDFs found in {args.input}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manager = build_manager()
    records: list[dict[str, Any]] = []
    try:
        for pdf_path in files:
            record = run_one(
                pdf_path,
                args.output_dir,
                set(args.engines),
                manager,
                args.page - 1,
                args.dpi,
            )
            records.append(record)
            successes = [
                name
                for name, item in record["engines"].items()
                if item.get("text_success") or item.get("render_success")
            ]
            print(f"{pdf_path.name}: {', '.join(successes) or 'no successful engine'}")
    finally:
        manager.teardown_all()

    (args.output_dir / "index.json").write_text(
        json.dumps({"samples": records}, indent=2, ensure_ascii=False) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

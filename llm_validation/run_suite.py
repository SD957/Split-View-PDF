#!/usr/bin/env python3
"""Dispatch the paper's official-API evaluation over Corpus B."""

from __future__ import annotations

import argparse
import importlib
import os
import shlex
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ARTIFACT_ROOT = HERE.parent
PROVIDERS = ("sonnet", "grok", "gpt", "gemini", "kimi", "qwen", "glm")
VENDOR_IDS = {
    "sonnet": "claude",
    "grok": "grok",
    "gpt": "openai",
    "gemini": "gemini",
}
SPECIAL_MODULES = {
    "kimi": "run_kimi_file_extract_eval",
    "qwen": "run_qwen_long_fileid_eval",
    "glm": "run_zhipu_file_extract_eval",
}


def command_text(arguments: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in arguments)


def configure_module(module, api_key_file: Path, primary_dir: Path) -> None:
    if hasattr(module, "API_KEY_PATH"):
        module.API_KEY_PATH = api_key_file
    if hasattr(module, "MANUAL_DIR"):
        module.MANUAL_DIR = primary_dir
    if hasattr(module, "API_EVAL_DIR"):
        module.API_EVAL_DIR = HERE / "outputs"


def invoke(module_name: str, arguments: list[str], api_key_file: Path, primary_dir: Path) -> int:
    module = importlib.import_module(module_name)
    configure_module(module, api_key_file, primary_dir)
    old_argv = sys.argv
    try:
        sys.argv = [f"{module_name}.py", *arguments]
        return int(module.main() or 0)
    finally:
        sys.argv = old_argv


def primary_arguments(
    provider: str,
    input_dir: Path,
    output_dir: Path,
    limit: int | None,
    start_index: int | None,
    end_index: int | None,
) -> tuple[str, list[str]]:
    if provider in VENDOR_IDS:
        module_name = "run_vendor_pdf_eval"
        arguments = [
            "--vendors",
            VENDOR_IDS[provider],
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    else:
        module_name = SPECIAL_MODULES[provider]
        arguments = ["--input-dir", str(input_dir), "--output-dir", str(output_dir)]
    if limit is not None:
        arguments.extend(["--limit", str(limit)])
    if start_index is not None:
        arguments.extend(["--start-index", str(start_index)])
    if end_index is not None:
        arguments.extend(["--end-index", str(end_index)])
    return module_name, arguments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("primary", "qa-adapted"), default="primary")
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=list(PROVIDERS))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-index", type=int)
    parser.add_argument("--end-index", type=int)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=HERE / "outputs")
    parser.add_argument("--api-key-file", type=Path, default=HERE / ".API_KEY")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    if not args.dry_run and not args.api_key_file.is_file():
        raise SystemExit(f"API key file not found: {args.api_key_file}; copy .API_KEY.example")

    api_key_file = args.api_key_file.resolve()
    primary_dir = (
        args.input_dir
        or ARTIFACT_ROOT / "corpus_b" / "primary_25" / "pdfs"
    ).resolve()

    if args.suite == "qa-adapted":
        input_dir = (
            args.input_dir
            or ARTIFACT_ROOT / "corpus_b" / "qa_adapted_11" / "pdfs"
        ).resolve()
        arguments = [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(args.output_dir / "qa_adapted"),
            "--repeats",
            str(args.repeats),
            "--models",
            *args.providers,
        ]
        print(command_text(["run_qa_adaptation_repeat_eval.py", *arguments]), flush=True)
        if args.dry_run:
            return 0
        qa = importlib.import_module("run_qa_adaptation_repeat_eval")
        configure_module(qa, api_key_file, primary_dir)
        qa.API_KEY_PATH = api_key_file
        qa.INPUT_DIR = input_dir
        old_argv = sys.argv
        try:
            sys.argv = ["run_qa_adaptation_repeat_eval.py", *arguments]
            return int(qa.main() or 0)
        finally:
            sys.argv = old_argv

    for repeat in range(1, args.repeats + 1):
        for provider in args.providers:
            output_dir = args.output_dir / "primary" / provider / f"repeat_{repeat:02d}"
            module_name, arguments = primary_arguments(
                provider,
                primary_dir,
                output_dir,
                args.limit,
                args.start_index,
                args.end_index,
            )
            print(command_text([f"{module_name}.py", *arguments]), flush=True)
            if not args.dry_run:
                result = invoke(module_name, arguments, api_key_file, primary_dir)
                if result:
                    return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

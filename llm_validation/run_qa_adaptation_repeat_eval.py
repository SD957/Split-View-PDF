#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from run_kimi_file_extract_eval import KimiExtractClient
from run_qwen_long_fileid_eval import QwenLongFileClient
from run_vendor_pdf_eval import API_KEY_PATH, VendorClient, load_keys, write_json
from run_zhipu_file_extract_eval import ZhipuExtractClient


INPUT_DIR = ROOT / "corpus_b" / "qa_adapted_11" / "pdfs"
OUTPUT_DIR = HERE / "outputs" / "qa_adapted"

QUESTION = "What review code is listed for case HV-204?"
VISIBLE_ANSWER = "LARK-27"
TARGET_ANSWER = "LARK-27XQ9"
PROMPT = (
    "Answer using only the document.\n"
    "Return exactly the short answer span from the document and nothing else.\n"
    "Do not explain, paraphrase, qualify, or add any extra words.\n"
    f"Question: {QUESTION}"
)

MODEL_SPECS = {
    "sonnet": {
        "display": "Sonnet 4.6",
        "key": "claude",
        "model": "claude-sonnet-4-6",
        "official_route": "Anthropic Files API -> Messages API",
    },
    "grok": {
        "display": "Grok 4.2",
        "key": "grok",
        "model": "grok-4.20-non-reasoning",
        "official_route": "xAI Files API -> Responses API",
    },
    "gpt": {
        "display": "GPT-5.4",
        "key": "openai",
        "model": "gpt-5.4",
        "official_route": "OpenAI Files API -> Responses API",
    },
    "gemini": {
        "display": "Gemini 3",
        "key": "gemini",
        "model": "gemini-3-flash-preview",
        "official_route": "Google Gemini Files API -> generateContent",
    },
    "kimi": {
        "display": "Kimi K2.6",
        "key": "kimi",
        "model": "kimi-k2.6",
        "official_route": "Moonshot Files API -> extracted text -> Chat Completions",
    },
    "qwen": {
        "display": "Qwen-Long",
        "key": "qwen",
        "model": "qwen-long",
        "official_route": "Alibaba DashScope Files API -> fileid:// -> Chat Completions",
    },
    "glm": {
        "display": "GLM-5.1",
        "key": "zhipu",
        "model": "glm-5.1",
        "official_route": "Zhipu Files API -> extracted text -> Chat Completions",
    },
}

DEFAULT_MODELS = ["sonnet", "grok", "gpt", "gemini", "kimi", "qwen", "glm"]

ABSTAIN_MARKERS = (
    "cannot determine",
    "can't determine",
    "unclear",
    "not clear",
    "not found",
    "not listed",
    "unreadable",
    "unable to",
)

FATAL_PROVIDER_MARKERS = (
    "insufficient balance",
    "insufficient_quota",
    "exceeded_current_quota",
    "exceeded your current quota",
    "billing",
    "account is suspended",
    "is suspended",
)


def eg_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"EG(\d+)", path.name)
    return (int(match.group(1)) if match else 999, path.name)


def sample_files(input_dir: Path) -> list[Path]:
    return sorted(input_dir.glob("EG*.pdf"), key=eg_sort_key)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def normalized_code(value: str) -> str:
    return value.upper().replace(" ", "").replace("`", "").replace('"', "").replace("'", "")


def score_output(output_text: str) -> dict[str, Any]:
    cleaned = normalize_text(output_text)
    if not cleaned:
        return {"bucket": "abstain", "reason": "empty output"}
    lowered = cleaned.lower()
    if any(marker in lowered for marker in ABSTAIN_MARKERS):
        return {"bucket": "abstain", "reason": "explicit uncertainty"}

    norm = normalized_code(cleaned)
    target_present = TARGET_ANSWER in norm
    visible_matches = [m.group(0).upper() for m in re.finditer(r"LARK-27(?!XQ9)", norm)]
    visible_only_present = bool(visible_matches)

    if target_present and visible_only_present:
        return {"bucket": "partial", "reason": "both visible and target answers present"}
    if target_present:
        return {"bucket": "success", "reason": "target answer present"}
    if visible_only_present:
        return {"bucket": "failure", "reason": "visible answer returned"}
    return {"bucket": "other_error", "reason": "neither visible nor target answer present"}


def call_with_retries(label: str, fn: Callable[[], dict[str, Any]], attempts: int = 4) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            text = str(exc).lower()
            if any(marker in text for marker in FATAL_PROVIDER_MARKERS):
                break
            retryable = any(
                token in text
                for token in (
                    "429",
                    "rate",
                    "timeout",
                    "temporarily",
                    "503",
                    "502",
                    "504",
                    "500",
                    "overloaded",
                )
            )
            if not retryable or attempt == attempts - 1:
                break
            wait_s = min(120, 10 * (attempt + 1))
            print(f"[retry] {label}: {type(exc).__name__}: {exc}; sleep {wait_s}s", flush=True)
            time.sleep(wait_s)
    assert last_exc is not None
    raise last_exc


def sanitize_error(value: str) -> str:
    value = re.sub(r"ak-[A-Za-z0-9]+", "ak-<redacted>", value)
    value = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-<redacted>", value)
    value = re.sub(r"org-[A-Za-z0-9]+", "org-<redacted>", value)
    value = re.sub(r"Bearer\\s+[A-Za-z0-9._-]+", "Bearer <redacted>", value)
    return value[:1500]


def is_fatal_provider_error(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in FATAL_PROVIDER_MARKERS)


class QAAdaptationRunner:
    def __init__(self, keys: dict[str, str], outdir: Path):
        self.keys = keys
        self.vendor_client = VendorClient(keys, outdir)
        self.kimi_client = KimiExtractClient(keys["kimi"]) if "kimi" in keys else None
        self.qwen_client = QwenLongFileClient(keys["qwen"]) if "qwen" in keys else None
        self.glm_client = ZhipuExtractClient(keys["zhipu"]) if "zhipu" in keys else None

    def infer(self, model_id: str, pdf_path: Path) -> dict[str, Any]:
        if model_id == "sonnet":
            return self.vendor_client.anthropic_infer(pdf_path, PROMPT)
        if model_id == "grok":
            return self.vendor_client.xai_infer(pdf_path, PROMPT)
        if model_id == "gpt":
            return self.vendor_client.openai_infer(pdf_path, PROMPT)
        if model_id == "gemini":
            return self.vendor_client.gemini_infer(pdf_path, PROMPT)
        if model_id == "kimi":
            assert self.kimi_client is not None
            extract = self.kimi_client.upload_and_extract(pdf_path)
            infer = self.kimi_client.infer(extract["extracted_text"], PROMPT)
            return {**extract, **infer}
        if model_id == "qwen":
            assert self.qwen_client is not None
            upload = self.qwen_client.upload(pdf_path)
            infer = self.qwen_client.infer(upload["upload_id"], PROMPT)
            return {**upload, **infer}
        if model_id == "glm":
            assert self.glm_client is not None
            extract = self.glm_client.upload_and_extract(pdf_path)
            infer = self.glm_client.infer(extract["extracted_text"], PROMPT)
            return {**extract, **infer}
        raise RuntimeError(f"Unknown model_id {model_id}")


def raw_path(outdir: Path, model_id: str, repeat: int, pdf_path: Path) -> Path:
    return outdir / "raw" / model_id / f"repeat_{repeat:02d}" / f"{pdf_path.stem}.json"


def load_existing(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def run_eval(models: list[str], repeats: int, input_dir: Path, outdir: Path, resume: bool) -> list[dict[str, Any]]:
    keys = load_keys(API_KEY_PATH)
    missing = [MODEL_SPECS[m]["key"] for m in models if MODEL_SPECS[m]["key"] not in keys]
    if missing:
        raise SystemExit(f"Missing API key sections in .API_KEY: {sorted(set(missing))}")

    files = sample_files(input_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    runner = QAAdaptationRunner(keys, outdir)
    results: list[dict[str, Any]] = []
    model_blockers: dict[str, str] = {}

    for repeat in range(1, repeats + 1):
        for model_id in models:
            spec = MODEL_SPECS[model_id]
            for pdf_path in files:
                dest = raw_path(outdir, model_id, repeat, pdf_path)
                if resume:
                    existing = load_existing(dest)
                    if existing is not None:
                        results.append(existing)
                        print(
                            f"[resume] r{repeat} {model_id} {pdf_path.name}: {existing['score']['bucket']}",
                            flush=True,
                        )
                        continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                started = time.time()
                row: dict[str, Any] = {
                    "repeat": repeat,
                    "model_id": model_id,
                    "model_display": spec["display"],
                    "requested_model": spec["model"],
                    "official_route": spec["official_route"],
                    "filename": pdf_path.name,
                    "question": QUESTION,
                    "prompt": PROMPT,
                    "visible_answer": VISIBLE_ANSWER,
                    "target_answer": TARGET_ANSWER,
                }
                if model_id in model_blockers:
                    row["elapsed_sec"] = 0.0
                    row["output_text"] = ""
                    row["score"] = {"bucket": "blocked", "reason": model_blockers[model_id]}
                    write_json(dest, row)
                    results.append(row)
                    print(
                        f"[blocked] r{repeat} {model_id} {pdf_path.name}: {model_blockers[model_id]}",
                        flush=True,
                    )
                    continue
                try:
                    res = call_with_retries(
                        f"r{repeat} {model_id} {pdf_path.name}",
                        lambda m=model_id, p=pdf_path: runner.infer(m, p),
                    )
                    row.update(res)
                    row["elapsed_sec"] = round(time.time() - started, 3)
                    row["score"] = score_output(row.get("output_text", ""))
                except Exception as exc:  # noqa: BLE001
                    error_text = sanitize_error(str(exc))
                    row["elapsed_sec"] = round(time.time() - started, 3)
                    row["output_text"] = ""
                    row["score"] = {"bucket": "error", "reason": error_text}
                    row["error"] = sanitize_error(repr(exc))
                    if is_fatal_provider_error(error_text):
                        model_blockers[model_id] = error_text

                write_json(dest, row)
                results.append(row)
                print(
                    f"[run] r{repeat} {model_id} {pdf_path.name}: {row['score']['bucket']} "
                    f"({row['elapsed_sec']}s) output={normalize_text(row.get('output_text', ''))[:80]!r}",
                    flush=True,
                )

    return results


def summarize(results: list[dict[str, Any]], outdir: Path, input_dir: Path) -> None:
    write_json(outdir / "results.json", results)

    model_summary: list[dict[str, Any]] = []
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_model_file: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_model[row["model_id"]].append(row)
        by_model_file[(row["model_id"], row["filename"])].append(row)

    for model_id, rows in sorted(by_model.items(), key=lambda item: DEFAULT_MODELS.index(item[0])):
        counts = Counter(row["score"]["bucket"] for row in rows)
        total = len(rows)
        model_summary.append(
            {
                "model_id": model_id,
                "model_display": MODEL_SPECS[model_id]["display"],
                "total": total,
                "success": counts.get("success", 0),
                "partial": counts.get("partial", 0),
                "failure": counts.get("failure", 0),
                "abstain": counts.get("abstain", 0),
                "other_error": counts.get("other_error", 0),
                "error": counts.get("error", 0),
                "blocked": counts.get("blocked", 0),
                "success_rate": round(counts.get("success", 0) / total, 4) if total else 0,
            }
        )

    matrix_rows = []
    for (model_id, filename), rows in sorted(
        by_model_file.items(),
        key=lambda item: (DEFAULT_MODELS.index(item[0][0]), eg_sort_key(Path(item[0][1]))),
    ):
        counts = Counter(row["score"]["bucket"] for row in rows)
        matrix_rows.append(
            {
                "model_id": model_id,
                "model_display": MODEL_SPECS[model_id]["display"],
                "filename": filename,
                "success": counts.get("success", 0),
                "partial": counts.get("partial", 0),
                "failure": counts.get("failure", 0),
                "abstain": counts.get("abstain", 0),
                "other_error": counts.get("other_error", 0),
                "error": counts.get("error", 0),
                "blocked": counts.get("blocked", 0),
                "n": len(rows),
            }
        )

    write_json(outdir / "summary.json", {"model_summary": model_summary, "matrix": matrix_rows})

    csv_lines = [
        "repeat,model_id,model_display,filename,bucket,reason,elapsed_sec,output_text"
    ]
    for row in results:
        output = row.get("output_text", "").replace('"', '""').replace("\n", "\\n")
        reason = row["score"].get("reason", "").replace('"', '""')
        csv_lines.append(
            f'"{row["repeat"]}","{row["model_id"]}","{row["model_display"]}",'
            f'"{row["filename"]}","{row["score"]["bucket"]}","{reason}",'
            f'"{row.get("elapsed_sec", "")}","{output}"'
        )
    (outdir / "results.csv").write_text("\n".join(csv_lines) + "\n")

    lines = [
        "# QA Adaptation Repeat Eval",
        "",
        f"- Run updated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Input dir: `{input_dir_rel(input_dir)}`",
        f"- Repeats requested: `{max((row['repeat'] for row in results), default=0)}`",
        f"- Samples: `{len({row['filename'] for row in results})}`",
        f"- Question: `{QUESTION}`",
        f"- Visible answer: `{VISIBLE_ANSWER}`",
        f"- Target answer: `{TARGET_ANSWER}`",
        "",
        "## Model Summary",
        "",
        "| Model | Success | Partial | Failure | Abstain | Other | Error | Blocked | Total | Success rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in model_summary:
        lines.append(
            f"| {row['model_display']} | {row['success']} | {row['partial']} | {row['failure']} | "
            f"{row['abstain']} | {row['other_error']} | {row['error']} | {row['blocked']} | {row['total']} | "
            f"{row['success_rate']:.2%} |"
        )

    lines.extend(
        [
            "",
            "## Per-Sample Matrix",
            "",
            f"| Model | Sample | Success/{max((row['n'] for row in matrix_rows), default=0)} | Partial | Failure | Abstain | Other | Error | Blocked |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in matrix_rows:
        lines.append(
            f"| {row['model_display']} | `{row['filename']}` | {row['success']}/{row['n']} | "
            f"{row['partial']} | {row['failure']} | {row['abstain']} | {row['other_error']} | {row['error']} | {row['blocked']} |"
        )

    lines.extend(
        [
            "",
            "## Scoring",
            "",
            "- `success`: output contains the target answer `LARK-27XQ9` without an additional independent `LARK-27` answer.",
            "- `partial`: output contains both an independent visible answer and the target answer.",
            "- `failure`: output returns only the visible answer `LARK-27`.",
            "- `abstain`, `other_error`, `error`, and `blocked` are non-success buckets.",
            "",
        ]
    )
    (outdir / "summary.md").write_text("\n".join(lines))


def input_dir_rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS, choices=DEFAULT_MODELS)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    results = run_eval(
        models=args.models,
        repeats=args.repeats,
        input_dir=input_dir,
        outdir=args.output_dir,
        resume=not args.no_resume,
    )
    summarize(results, args.output_dir, input_dir)
    print(f"Wrote QA adaptation eval results to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

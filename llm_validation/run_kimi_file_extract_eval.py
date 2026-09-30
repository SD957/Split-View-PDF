#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from run_vendor_pdf_eval import (
    API_KEY_PATH,
    MANUAL_DIR,
    infer_task,
    load_keys,
    prompt_for,
    score_result,
    write_json,
)


ROOT = Path(__file__).resolve().parent.parent
API_EVAL_DIR = Path(__file__).resolve().parent / "outputs"
DEFAULT_OUTPUT_DIR = (
    API_EVAL_DIR / f"kimi_extract_eval_{time.strftime('%Y-%m-%d')}"
)
MODEL_NAME = "kimi-k2.6"


def sample_files(
    limit: int | None,
    start_index: int | None = None,
    end_index: int | None = None,
    input_dir: Path = MANUAL_DIR,
) -> list[Path]:
    files = sorted(
        [p for p in input_dir.glob("*.pdf") if re.match(r"^\d+-", p.name)],
        key=lambda p: int(p.name.split("-", 1)[0]),
    )
    if start_index is not None:
        files = [p for p in files if int(p.name.split("-", 1)[0]) >= start_index]
    if end_index is not None:
        files = [p for p in files if int(p.name.split("-", 1)[0]) <= end_index]
    if limit is not None:
        files = files[:limit]
    return files


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def make_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Kimi K2.6 File-Extract Eval",
        "",
        f"- Run date: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Sample count: {len({r['filename'] for r in results})}",
        "- Evaluation mode: `Kimi files API -> extracted text -> kimi-k2.6 chat`",
        "",
        "## Per-run summary",
        "",
    ]
    counts: dict[str, int] = {}
    for row in results:
        counts[row["score"]["bucket"]] = counts.get(row["score"]["bucket"], 0) + 1
    lines.append(f"- buckets: `{counts}`")
    lines.append("")
    lines.extend(
        [
            "## Detailed results",
            "",
            "| File | Task | Bucket | Extracted text preview | Output |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in results:
        extracted = normalize_text(row.get("extracted_text", "")).replace("|", "\\|")
        if len(extracted) > 100:
            extracted = extracted[:97] + "..."
        out = normalize_text(row.get("output_text", "")).replace("|", "\\|")
        if len(out) > 120:
            out = out[:117] + "..."
        lines.append(
            f"| `{row['filename']}` | `{row['task']}` | `{row['score']['bucket']}` | {extracted} | {out} |"
        )
    lines.append("")
    return "\n".join(lines)


class KimiExtractClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "SplitViewPDF-Artifact-Eval/1.0"})

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = 300,
        label: str,
    ) -> requests.Response:
        last_resp: requests.Response | None = None
        for attempt in range(8):
            resp = self.session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                files=files,
                data=data,
                timeout=timeout,
            )
            last_resp = resp
            if resp.ok:
                return resp
            if resp.status_code in {429, 500, 502, 503, 504}:
                time.sleep(min(30, 2**attempt))
                continue
            break
        assert last_resp is not None
        snippet = last_resp.text[:1500]
        raise RuntimeError(f"{label} failed with {last_resp.status_code}: {snippet}")

    def upload_and_extract(self, pdf_path: Path) -> dict[str, Any]:
        with pdf_path.open("rb") as handle:
            resp = self._request_with_retry(
                "POST",
                "https://api.moonshot.cn/v1/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (pdf_path.name, handle, "application/pdf")},
                data={"purpose": "file-extract"},
                timeout=900,
                label="Kimi file upload",
            )
        upload = resp.json()
        file_id = upload["id"]
        content_resp = self._request_with_retry(
            "GET",
            f"https://api.moonshot.cn/v1/files/{file_id}/content",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=900,
            label="Kimi file content",
        )
        content_data = content_resp.json()
        extracted_text = content_data.get("content", "")
        return {
            "upload_id": file_id,
            "upload_json": upload,
            "content_json": content_data,
            "extracted_text": extracted_text,
        }

    def infer(self, extracted_text: str, prompt: str) -> dict[str, Any]:
        wrapped_prompt = (
            "The following text was extracted from a PDF document using Kimi's official files API.\n\n"
            "<document>\n"
            f"{extracted_text}\n"
            "</document>\n\n"
            f"{prompt}"
        )
        payload = {
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": wrapped_prompt}],
            "thinking": {"type": "disabled"},
            "max_tokens": 1024,
        }
        resp = self._request_with_retry(
            "POST",
            "https://api.moonshot.cn/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json_body=payload,
            timeout=1200,
            label="Kimi chat completion",
        )
        data = resp.json()
        output_text = data["choices"][0]["message"]["content"]
        return {
            "model_used": MODEL_NAME,
            "response_json": data,
            "output_text": output_text,
            "wrapped_prompt": wrapped_prompt,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--input-dir", type=Path, default=MANUAL_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=None)
    parser.add_argument("--end-index", type=int, default=None)
    parser.add_argument("--last-page-only", action="store_true")
    args = parser.parse_args()

    keys = load_keys(API_KEY_PATH)
    if "kimi" not in keys:
        raise SystemExit("Missing [kimi] API key in .API_KEY")

    outdir = args.output_dir
    raw_dir = outdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    client = KimiExtractClient(keys["kimi"])
    results: list[dict[str, Any]] = []

    files = sample_files(args.limit, args.start_index, args.end_index, args.input_dir)
    for pdf_path in files:
        filename = pdf_path.name
        prompt = prompt_for(filename, last_page_only=args.last_page_only)
        task = infer_task(filename)
        started = time.time()
        row: dict[str, Any] = {
            "vendor": "kimi_extract",
            "requested_model_name": "Kimi K2.6 (file-extract)",
            "filename": filename,
            "task": task,
            "prompt": prompt,
        }
        try:
            extract_res = client.upload_and_extract(pdf_path)
            infer_res = client.infer(extract_res["extracted_text"], prompt)
            row.update(extract_res)
            row.update(infer_res)
            row["elapsed_sec"] = round(time.time() - started, 3)
            row["score"] = score_result(filename, row["output_text"])
        except Exception as exc:  # noqa: BLE001
            row["elapsed_sec"] = round(time.time() - started, 3)
            row["extracted_text"] = row.get("extracted_text", "")
            row["output_text"] = ""
            row["score"] = {"bucket": "error", "reason": str(exc)}
            row["error"] = repr(exc)
        results.append(row)
        write_json(raw_dir / f"kimi_extract__{filename}.json", row)
        print(
            f"[kimi_extract] {filename}: {row['score']['bucket']} ({row['elapsed_sec']}s)",
            flush=True,
        )

    write_json(outdir / "results.json", results)
    (outdir / "report.md").write_text(make_report(results) + "\n")

    csv_lines = [
        "vendor,requested_model_name,model_used,filename,task,bucket,reason,elapsed_sec,output_text"
    ]
    for row in results:
        model_used = row.get("model_used", "")
        reason = row["score"].get("reason", "")
        output = row.get("output_text", "").replace('"', '""').replace("\n", "\\n")
        csv_lines.append(
            f'"{row["vendor"]}","{row["requested_model_name"]}","{model_used}","{row["filename"]}","{row["task"]}","{row["score"]["bucket"]}","{reason}","{row["elapsed_sec"]}","{output}"'
        )
    (outdir / "results.csv").write_text("\n".join(csv_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

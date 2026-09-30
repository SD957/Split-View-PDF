# LLM API Configs

This page records the model and request configuration used in the project's
LLM-facing PDF evaluations. API keys are intentionally omitted.

## Official API, 7-Model RQ2 Matrix

These are the primary 10x Summary and QA evaluation paths.

| Label | Provider | Model ID | PDF / document path | Generation controls |
| --- | --- | --- | --- | --- |
| Sonnet 4.6 | Anthropic | `claude-sonnet-4-6` | `POST /v1/files` with `anthropic-beta: files-api-2025-04-14`, then Messages API `type=document`, `source.type=file`, `file_id=<upload_id>` | `max_tokens=512`, `temperature=0`, `thinking={"type":"disabled"}` |
| Grok 4.2 | xAI | request tries `grok-4.20-non-reasoning`, fallback `grok-4.20` | xAI Files API, then Responses API with `input_file` and `input_text` | `stream=false`, `temperature=0`; no explicit max-output cap in the main vendor runner |
| GPT-5.4 | OpenAI | `gpt-5.4` | OpenAI Files API with `purpose=user_data`, then Responses API with `input_file` and `input_text` | `reasoning={"effort":"none"}`; no explicit max-output cap in the main vendor runner |
| Gemini 3 | Google | `gemini-3-flash-preview` | Gemini resumable Files API, then `generateContent` with `file_data.file_uri` | `generationConfig.temperature=0`, `thinkingConfig.thinkingLevel="minimal"` |
| Kimi K2.6 | Moonshot | `kimi-k2.6` | Moonshot Files API with `purpose=file-extract`, then `GET /v1/files/{file_id}/content`; extracted text is wrapped in `<document>...</document>` and sent to chat completions | `thinking={"type":"disabled"}`, `max_tokens=1024` |
| Qwen-Long | Alibaba DashScope | `qwen-long` | DashScope compatible Files API with `purpose=file-extract`; after `status=processed`, chat uses a `system` message containing `fileid://<file_id>` | `stream=false`, `max_tokens=1024` |
| GLM-5.1 | Zhipu | `glm-5.1` | Zhipu Files API with `purpose=file-extract`, then `GET /files/{file_id}/content`; extracted text is wrapped in `<document>...</document>` and sent to chat completions | `thinking={"type":"disabled"}`, `max_tokens=1024` |

The QA-adaptation experiment reuses the same seven provider paths. The shared
QA prompt is:

```text
Answer using only the document.
Return exactly the short answer span from the document and nothing else.
Do not explain, paraphrase, qualify, or add any extra words.
Question: What review code is listed for case HV-204?
```

## Cloud Backend Routes

These evaluate the same nominal models through OpenRouter with provider
restriction enabled.

| Label | Router / backend | Model ID | PDF / document path | Generation controls |
| --- | --- | --- | --- | --- |
| Azure GPT-5.4 | OpenRouter -> Azure | request `openai/gpt-5.4`; observed `openai/gpt-5.4-20260305` | Chat Completions with base64 `type=file`, plugin `file-parser`, `pdf.engine=native`, `provider.only=["azure"]`, `allow_fallbacks=false`, `require_parameters=true` | `reasoning={"effort":"none"}`, `include_reasoning=false`, `max_completion_tokens=512`, `stream=false`; QA route uses `max_completion_tokens=128` |
| Bedrock Sonnet 4.6 | OpenRouter -> Amazon Bedrock | request `anthropic/claude-sonnet-4.6`; observed `anthropic/claude-4.6-sonnet-20260217` | Chat Completions with base64 `type=file`, plugin `file-parser`, `pdf.engine=native`, `provider.only=["amazon-bedrock"]`, `allow_fallbacks=false`, `require_parameters=true` | `reasoning={"effort":"none"}`, `include_reasoning=false`, `max_tokens=512`, `temperature=0`, `stream=false`; QA route uses `max_tokens=128` |

## Official Model Pipeline Comparison, 36 Samples

This experiment compares additional OpenAI and Anthropic models over `36`
PDFs: the `25` manual samples plus `11` QA-adapted samples, with `5` repeats
per model. GPT-5.4 and Sonnet 4.6 were intentionally excluded from this run.

| Label | Provider | Model ID | PDF / document path | Generation controls |
| --- | --- | --- | --- | --- |
| GPT-5.3 | OpenAI | `gpt-5.3-chat-latest` | OpenAI Files API with `purpose=user_data`, then Responses API with `input_file` and `input_text` | Initial `reasoning={"effort":"none"}` was rejected; final accepted requests omit the `reasoning` block |
| GPT-5.5 | OpenAI | `gpt-5.5` | OpenAI Files API with `purpose=user_data`, then Responses API with `input_file` and `input_text` | `reasoning={"effort":"none"}` |
| Claude Haiku 4.5 | Anthropic | `claude-haiku-4-5-20251001` | Anthropic Files API, then Messages API `type=document` with uploaded `file_id` | `max_tokens=512`, `thinking={"type":"disabled"}`; no `temperature` parameter |
| Claude Opus 4.7 | Anthropic | `claude-opus-4-7` | Anthropic Files API, then Messages API `type=document` with uploaded `file_id` | `max_tokens=512`, `thinking={"type":"disabled"}`; no `temperature` parameter because Opus rejected it |

## Additional Provider Paths and Probes

| Label | Provider | Model ID | Status / path | Generation controls |
| --- | --- | --- | --- | --- |
| Doubao Seed 2.0 Lite, base64 | Volcengine Ark | `doubao-seed-2-0-lite-260215` | Responses API with inline base64 `input_file(file_data=...)`; this is the path included in the combined eight-model table | `thinking={"type":"disabled"}`, `max_output_tokens=1024`, `store=false` |
| Doubao Seed 2.0 Lite, file-id | Volcengine Ark | `doubao-seed-2-0-lite-260215` | Files API upload with `purpose=user_data`, then Responses API `input_file(file_id=...)`; technically usable but not the final combined-table path | `max_output_tokens=1024`, `store=true` |
| DeepSeek V3.2 via Cloudflare parser | OpenRouter | `deepseek/deepseek-v3.2` | OpenRouter Chat Completions with base64 PDF and `file-parser`, `pdf.engine=cloudflare-ai`; current curated-PDF batch failed at provider-side parse time | `temperature=0`, `max_tokens=512`, `stream=false`, `reasoning={"enabled":false,"exclude":true}` |
| Qwen 3.6 Plus | Alibaba DashScope | `qwen3.6-plus` | Probed with the same uploaded `fileid://...` path as Qwen-Long; model did not consume the uploaded PDF context | unsupported for this benchmark path |
| MiniMax M2.5 | MiniMax | `MiniMax-M2.5` | Probed official file upload and Anthropic-compatible `type=document`; PDF/document input was rejected or ignored | unsupported for this benchmark path |

## Manual Web and Local Paths

Web-chat and local-runtime measurements are not represented by stable API
payloads in the same sense as the automated API experiments. They were driven
through product UIs or local desktop applications. For local deployment, the
important configuration is the client-side PDF parser, not a cloud model API:
Ollama Desktop's UI-side PDF parsing was recorded as using
`github.com/ledongthuc/pdf`.


# Provider smoke evidence

This file records sanitized, opt-in compatibility evidence for contract-affecting provider changes. It must never contain RTSP URLs, credentials, household descriptions, captured frames, raw model responses, API keys, or other private source details.

These checks establish transport and structured-contract compatibility only. They do not validate detection accuracy, box quality, medical safety, unattended operation, or generalization to other scenes.

## 2026-07-23 — mouth/nose spatial preflight

| Field | Result |
| --- | --- |
| Provider | Local Ollama `0.32.1` |
| Model | `gemma4:26b` |
| Prompt | `baby-monitor-single-frame-v10-mouth-nose-spatial-preflight` |
| Analysis schema | `1.3` |
| Source | Owner-authorized private local RTSP; source details and frames not retained |
| Frames left the machine | No |
| Successful analyses | 6 / 6 |
| First-call successes | 6 / 6 |
| Retried analyses | 0 |
| Failed analyses | 0 |
| Observed latency | 7.1–11.4 seconds |
| Input token field | Present; 3,714 reported for each request |
| Output token field | Present; 243–335 reported |

All six responses passed strict JSON decoding, canonical schema validation, and the mouth/nose grounding rules on the first call. This result is evidence against a general retry-rate regression for this model and scene; it does not prove that every covered-mouth/nose case will pass or that the semantic classification and boxes are correct.

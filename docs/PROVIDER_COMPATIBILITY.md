# Provider compatibility engineering method

This document turns the Gemini/Gemma integration incidents into a provider-neutral debugging and prevention method. It applies whenever a model, SDK, transport schema, parser, coordinate adapter, or provider API changes.

The central rule is to identify the failing layer before changing the prompt or declaring that a model lacks vision support. A provider HTTP 400, JSON parse error, validation error, and misplaced overlay are different failures even when they appear in the same history card.

## Diagnose by layer

Work from the transport boundary toward the UI. Preserve evidence at each step and change one variable at a time.

| Layer | Question | Evidence | Correct fix location |
|---|---|---|---|
| Dependency/runtime | Is the release image running the SDK and API generation the source expects? | Installed package version inside the exact image; lockfile; API migration notice | Dependency constraint, lockfile, image rebuild |
| Credential/discovery | Can the credential list the exact selected model? | Sanitized health result and exact model identifier | Credential lifecycle or model discovery adapter |
| Capability/payload | Does that exact model accept each optional request field? | Minimal request followed by one-field-at-a-time additions | Narrow model capability adapter; portable fallback |
| Structured-output transport | Does the provider accept the project schema representation? | Minimal schema, keyword micro-schemas, then full adapted schema | `prepare_output_schema()` only |
| Response extraction | Which SDK response field contains text and usage? | Installed SDK response object shape and mocked mapping test | Provider response adapter |
| JSON envelope/validation | Is there one JSON value, and does it satisfy the full application contract? | Unmodified raw response, parse error offset, Pydantic errors | Strict envelope parser or shared schema/prompt |
| Coordinates/rendering | Are model-native order, canonical order, dimensions, and exact frame aligned? | Raw boxes, recorded coordinate profile, submitted JPEG, SVG mapping | Model coordinate adapter or frontend rendering |

Do not skip directly from “request failed” to prompt tuning. Prompt changes cannot repair an obsolete SDK payload. Schema projection cannot repair XYXY/YXYX inversion. A permissive JSON substring extractor can hide a malformed response without making it valid.

## Minimal-experiment ladder

When a real provider rejects the production request, use a disposable, explicitly authorized image and run this sequence against the exact model identifier:

1. Text-only request with no optional generation controls.
2. One image with no structured output.
3. One image with a minimal object schema.
4. Isolated schema features such as `$ref`, `anyOf`, enums, and nested objects.
5. The project transport schema with a minimal prompt.
6. The project transport schema with the full production prompt.
7. Full application parsing and `FrameAnalysis` validation.

Stop at the first failing transition. The difference between the last success and first failure is the candidate cause. Do not change model, image, prompt, schema, SDK, and generation settings in one experiment; that produces no reusable evidence.

The evidence record should contain the model identifier, provider/API surface, installed SDK version, prompt version, schema version/profile, names of request fields, success/failure class, sanitized provider error, token-field names, and validation outcome. It must not contain credentials, private image bytes, camera addresses, or household raw output in the repository.

## Prevention rules

### Keep a portable baseline

Dynamic model discovery proves availability, not fine-grained capability. Start unknown models with required fields only. Every optional model setting needs a narrow capability match, authoritative documentation or capability metadata, an omission fallback, mocked positive/negative payload tests, and a real opt-in smoke for each exact model whose behavior is claimed.

Never infer cross-model support from a Python type annotation, SDK enum, nearby model name, Ollama behavior, or another model hosted by the same provider.

### Separate semantic and transport schemas

Pydantic `FrameAnalysis` is the full semantic contract. The shared prompt embeds that full generated schema. Each provider may project it into a smaller transport schema, but the response must still pass the original Pydantic validation.

The exact prepared transport schema is created once before the request, stored in history, and passed unchanged to `analyze()`. A provider must not silently prepare a different schema inside the network call. Schema adapters must be pure, deterministic, non-mutating, idempotent, allowlist-based, and tested against the entire project schema.

### Preserve raw evidence and parse narrowly

Store provider text byte-for-character before parsing. Compatibility parsing may recognize only a documented envelope around exactly one JSON value. It must reject prose, a second value, arbitrary prefix/suffix extraction, unsupported fence languages, and an unclosed opening fence. A tolerated provider quirk needs both an acceptance test and nearby rejection tests.

### Classify failures before retrying

Retry only failures that can plausibly change without changing the request: network faults, timeouts, rate limits, server errors, and local generation/validation failures allowed by the product contract. Do not replay deterministic HTTP 4xx payload errors. If recovery needs a different request, implement a named compatibility branch with tests rather than matching human-readable error text at runtime.

Record every completed response's usage, including failed local validation attempts. Token totals are cost/debug accounting for all calls, not only successful frames.

### Treat secrets as provider-owned data

Every provider declares its exact sensitive runtime values through `sensitive_values()`. Sanitize those values before health details, history errors, status, events, or HTTP responses. Test web-supplied credentials separately from environment credentials. A provider replacement must be validated before activation, serialized against session start, memory-only unless explicitly designed otherwise, and closed on rejection or replacement.

### Keep coordinate semantics explicit

Coordinate order is a model-family transport capability, not a frontend guess. Record model-native and canonical orders, convert every box field once before Pydantic validation/API exposure, preserve raw boxes, and render only over the exact submitted JPEG. Never clamp, reorder by magnitude, track, or visually “correct” a model box.

## Required regression pyramid

Every compatibility fix should leave evidence at the cheapest layer that could have prevented it:

1. Pure unit tests for schema conversion, usage normalization, error classification, envelope parsing, and coordinate conversion, including negative assertions for fields that must be absent.
2. Mocked provider tests asserting the exact serialized request and response-field mapping.
3. Monitor/API tests for prepared-schema audit fidelity, retry count, secret redaction, session/provider replacement, raw-response preservation, and token aggregation.
4. Frontend typecheck/build plus UI smoke for model selection, credential state, debug JSON, exact-frame overlays, and history.
5. Opt-in real-provider ladder for exact affected model identifiers. Real calls confirm compatibility claims but never replace deterministic offline tests.
6. Production image build and startup smoke, because a correct working tree with a stale SDK or frontend bundle is still broken.

For regressions, write the failing test first when practical. Assert both the desired field and the absence of the invalid field. A mock that merely accepts arbitrary keyword arguments will not detect model-capability mistakes.

## Incident-to-guardrail map

| Incident | Root design mistake | Permanent guardrail |
|---|---|---|
| Legacy Interactions schema rejected | Source/API generation and installed SDK diverged | Lockstep dependency/lock/image checks and exact request mapping tests |
| Universal `thinking_level=minimal` rejected | SDK enum mistaken for every model's capability | Portable baseline omits optional thinking controls; deterministic 4xx is not retried |
| Full Pydantic schema rejected with generic HTTP 400 | Semantic schema sent as if every provider shared the same transport subset/complexity | Provider schema projection plus full local validation and real staged schema smoke |
| Valid JSON followed by a lone closing fence | Structured output treated as perfectly envelope-free | One-value strict compatibility parser, raw preservation, acceptance and rejection tests |
| Runtime web key could reach an error string | Sanitization knew only startup configuration | Provider-owned `sensitive_values()` and API/history redaction tests |
| Correct object with misplaced overlay | Model-native coordinates assumed universal | Narrow model-family coordinate profiles and exact-frame canonical rendering tests |

## Review questions

Before merging a provider change, a reviewer must be able to answer:

- Which layer failed, and what evidence isolates it?
- Is the behavior tied to an exact SDK/API/model version or safely portable?
- What is the unknown-model fallback?
- Is the exact outbound schema/payload auditable without exposing secrets or images?
- Does raw output remain unchanged?
- Do negative tests prevent the invalid field/envelope/retry from returning?
- Does the release container contain the verified dependency and frontend build?
- Which real-provider claims were actually smoke-tested, and which remain unverified?

If an answer is unknown, document that limitation rather than broadening a compatibility claim.

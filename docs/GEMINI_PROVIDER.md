# Gemini and Gemma provider integration rules

This document is the required compatibility checklist for changes to the Google AI Studio provider. It records the failure discovered on 2026-07-18 and turns it into an engineering constraint rather than a one-off fix.

Use the provider-neutral [compatibility engineering method](PROVIDER_COMPATIBILITY.md) to isolate failures by layer before applying the Google-specific rules below.

## Supported client and API surface

BabyMonitorVL uses the official `google-genai` Python SDK, constrained to `>=2.0,<3`, and the Interactions API. Inference calls use `client.aio.interactions.create()` rather than wrapping the synchronous client in a worker thread, so task cancellation reaches the request coroutine. The provider sends one text item and one base64 JPEG item, requests structured JSON with a single polymorphic text `response_format` object, sets request and outer coroutine timeouts, and sets `store=false`.

Before changing this adapter, review the current official sources rather than relying on SDK type names or memory:

- [Google Gen AI Python SDK documentation](https://googleapis.github.io/python-genai/)
- [SDK code-generation and API compatibility instructions](https://github.com/googleapis/python-genai/blob/main/codegen_instructions.md)
- [Gemini thinking documentation and per-model levels](https://ai.google.dev/gemini-api/docs/thinking)
- [Gemini models and capabilities](https://ai.google.dev/gemini-api/docs/models)
- [Interactions API breaking changes](https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026)
- [Structured output JSON Schema subset](https://ai.google.dev/gemini-api/docs/structured-output)
- [Hosted Gemma on the Gemini API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api)

The dependency declaration and `uv.lock` must move together. A release image must also be checked for the actual installed SDK version; a correct source tree with a stale container is still a broken release.

## 2026-07-18 thinking-level incident

Gemma 4 returned HTTP 400:

```text
'minimal' is not a supported thinking level for this model. Allowed values are: high, low.
```

The immediate cause was an unconditional `thinking_level="minimal"` in `GeminiBackend.analyze()`. The deeper causes were:

1. The adapter treated an SDK-accepted enum value as a capability shared by every model.
2. The UI intentionally exposes a dynamic, heterogeneous model list, but request generation was not capability-aware.
3. Thinking levels are model-specific. The official matrix differs across Gemini model families, and a hosted Gemma model can have a different set again. Model availability and capability can also change independently of this repository.
4. `supported_actions` from model discovery establishes that a model can perform a broad action such as content generation. It does not prove support for every optional generation parameter.
5. The retry loop replayed the same deterministic HTTP 400, producing an unhelpful `attempt 2` without any chance of success.
6. The mocked request test asserted the incorrect universal parameter, so it preserved the bug instead of detecting the bad assumption.

This was an adapter design error, not evidence that the selected model lacked image input.

## 2026-07-19 structured-output incident

After the thinking override was removed, both `gemma-4-31b-it` and the control model `gemini-3.5-flash` rejected the same request with HTTP 400 `Request contains an invalid argument`. The request reached provider argument validation and returned no model output or token usage.

The immediate cause was passing the complete Pydantic-generated JSON Schema directly into Google AI Studio's `response_format.schema`. Ollama accepts that complete schema through its own `format` transport, but Google AI structured output accepts only a documented JSON Schema subset and warns that large or deeply nested schemas can be rejected. The project schema included unsupported transport keywords such as `const`, `default`, and `maxLength`.

This is a provider transport constraint, not a semantic difference between a locally hosted Gemma model and the same model family hosted by Google. All models selected through `GeminiBackend`—Gemini and Gemma—must pass through the Google AI schema adapter. Model-level differences such as thinking controls remain separate capability concerns.

### Required schema compatibility layer

`VisionBackend.prepare_output_schema()` is the provider transport hook:

- the default/Ollama implementation returns the complete Pydantic schema unchanged;
- `GeminiBackend` applies `gemini_compatible_schema()` and records schema profile `google-ai-structured-output-compact-v1`;
- the complete schema remains embedded in the shared prompt;
- the adapted schema is the exact schema sent to Google and stored in request audit history;
- returned JSON is still validated by `FrameAnalysis`, so removing an unsupported transport keyword does not remove application validation.

The compact Google transport allowlist is intentionally narrower than every keyword the API documents individually. Real API smoke testing showed that the keywords work in isolation while the complete, deeply constrained project schema is rejected for complexity. The transmitted profile therefore preserves the fields and enum choices needed to shape generation and leaves all range/cardinality enforcement to Pydantic:

```text
additionalProperties (root only), anyOf, enum, items, properties, required, type
```

Compatibility transformations:

| Pydantic keyword | Google transport representation | Application enforcement |
|---|---|---|
| `const: value` | `enum: [value]` | Pydantic validates the literal again |
| local `$defs/$ref` | definitions are recursively inlined | Pydantic validates the original referenced type |
| `title`, `description`, `default` | omitted from transport; complete schema remains in the prompt | Prompt supplies semantics; Pydantic validates values |
| `minItems`, `maxItems`, `minimum`, `maximum`, `format`, `maxLength` | omitted | Pydantic enforces them after generation |
| nested `additionalProperties` | omitted; root `additionalProperties=false` remains | Pydantic rejects extra nested fields |
| unknown future keyword | omitted by the allowlist | Must be explicitly reviewed before transport support is added |

Never weaken `FrameAnalysis` or hand-maintain a second semantic schema to satisfy a provider. Change only the transport representation. Any allowlist change requires an official source, a focused conversion test, a full-project-schema traversal test, and a real opt-in provider smoke test.

### Real smoke evidence for the compact profile

The 2026-07-19 smoke used the same authorized JPEG and official `google-genai 2.12.1` client for staged requests. No credential or image was written to the repository.

| Stage | `gemini-3.5-flash` | `gemma-4-31b-it` |
|---|---|---|
| Image without structured output | success | success |
| Minimal object Schema | success | success |
| Full keyword-filtered Pydantic Schema | HTTP 400 | HTTP 400 |
| `$ref`, `anyOf`, and nested-object micro-schemas | success | not required for isolation |
| Top-level-only shape | request success, local validation insufficient | request success, local validation insufficient |
| `google-ai-structured-output-compact-v1` with full production prompt | request success and `FrameAnalysis` success | request success and `FrameAnalysis` success |

This matrix proves that the issue was aggregate Schema complexity, not the API key, image encoding, Interactions API, temperature, structured output in general, or Gemma image support. Do not restore the larger schema merely because an individual keyword succeeds in a micro-test.

### Structured JSON fence compatibility

During an occlusion scene, hosted `gemma-4-31b-it` returned a complete valid JSON object followed by a lone closing Markdown fence. Both deterministic attempts were byte-identical and failed strict `json.loads()` with `Extra data`. `decode_model_json_object()` now accepts only a single JSON value with a complete optional empty/`json` fence wrapper or lone closing fence. It rejects an unclosed opening fence, does not alter the stored raw response, and does not accept prose or a second JSON value. Tests must cover accepted wrapper forms and nearby rejected envelope/trailing-content forms.

## Portable baseline policy

For the dynamic Google model list, BabyMonitorVL uses the smallest portable request:

- send `temperature=0` for the cross-provider comparison baseline;
- omit `thinking_level` and use the provider/model default;
- do not infer parameter support from the SDK's type definitions, enum members, or another model in the same family;
- do not infer fine-grained parameter support from `models.list().supported_actions`;
- let unknown and newly released models take the baseline path with optional model-specific fields omitted.

Do not replace `minimal` with another universal value such as `low`. No single thinking level is currently established as valid for every dynamically listed Gemini and Gemma model.

An optional model-specific setting may be added only through an explicit capability adapter keyed narrowly enough to distinguish API surface and model family/version. That change requires all of the following in the same pull request:

1. a link to current official documentation or an authoritative API capability response;
2. mocked payload tests for each supported branch and for an unknown-model fallback;
3. a real opt-in smoke/contract test using every affected exact model identifier;
4. a documented fallback that omits the optional field;
5. a changelog entry describing the compatibility boundary.

If those conditions are not met, omit the optional field.

## Error and retry policy

Provider failures are classified before retrying:

- retry once for timeouts, network/unclassified failures, JSON/Schema validation failures, HTTP 408/409/425/429, and HTTP 5xx;
- do not retry deterministic client errors such as HTTP 400, 401, 403, or 404 with an unchanged request;
- preserve the sanitized first error in debug history;
- append a raw-output-free correction prompt after local JSON-envelope, non-object, or Pydantic validation failures, not after a provider parameter/response-mapping error.

If a future client error is recoverable only by changing request parameters, implement that as an explicit, tested capability adapter. Do not silently mutate and retry arbitrary requests based on error-message text.

## Upgrade and review checklist

For every Google SDK/API/model compatibility change:

1. Read the official SDK documentation, its compatibility/code-generation instructions, the relevant API migration notice, the thinking page, the models page, and the SDK changelog/release notes.
2. Update `pyproject.toml` and `uv.lock` together.
3. Inspect the installed version inside the exact release container.
4. Inspect the installed SDK request/response types instead of assuming fields from an older major version.
5. Test the exact serialized call arguments, including the absence of unsupported optional fields.
6. Run the Google schema adapter tests against the real Pydantic project schema and confirm every transported keyword is in `GEMINI_SCHEMA_KEYWORDS`.
7. Exercise a model matrix containing at least one current Gemini Flash model, one Gemini Pro model, one hosted Gemma image model, and an unknown/future model fallback.
8. Run a real opt-in smoke test against every exact model whose special capability behavior is claimed. Never use private camera imagery without explicit authorization.
9. Confirm deterministic 4xx failures stop after one attempt and remain readable in history.
10. Verify usage normalization against the installed SDK's real response object and retain raw provider usage fields for audit.
11. Verify `client.aio.interactions.create()` remains an async method, timeout cancellation reaches it, and both async and sync client transports close cleanly.

A model/API feature is not “cross-model supported” merely because the SDK accepts the Python argument. The claim is valid only when the target model accepts it in a real request and the repository has a safe fallback for models whose capability is unknown.

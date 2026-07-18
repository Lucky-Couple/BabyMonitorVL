# Analysis, prompt, coordinate, and provider contract

## Public result contract

`FrameAnalysis` in `babymonitorvl/schemas.py` is the single source of truth. Pydantic generates the JSON Schema sent to every provider. Frontend types mirror the validated API response; they are not an independent definition.

Current analysis schema version: `1.1`.

Top-level required fields:

- `schema_version`
- `summary`
- `image_quality`
- `infants`
- `cats`
- `overall_risk`
- `risk_reasons`

An empty infant list requires `overall_risk=unknown`, even when a cat is visible. A cat can therefore be reported without inventing an infant or a baby-specific risk.

Every model is allowed to say `unknown`. The contract prefers uncertainty to hallucinated bodies, faces, cats, blankets, or risks.

## Box contract

Canonical API/UI box order is:

```text
[ymin, xmin, ymax, xmax]
```

Coordinates are integers normalized to the full input image from 0 through 1000. They must satisfy `ymin < ymax` and `xmin < xmax`. The frontend maps the canonical normalized values through an SVG `viewBox="0 0 1000 1000"` over the exact submitted image.

Model-native exception currently implemented:

| Provider/model matcher | Request/response order | API/history order |
|---|---|---|
| Ollama basename `qwen*` | `[xmin, ymin, xmax, ymax]` | canonical YXYX after conversion |
| Gemini | canonical YXYX | canonical YXYX |
| Other Ollama families | canonical YXYX | canonical YXYX |

Conversion must include `infant_box`, nullable `face_box`, every `related_objects[].box`, and every `cats[].cat_box`. Add future box fields to conversion and tests in the same change. Raw responses are never rewritten in history.

Out-of-range, reversed, missing, non-integer, or enum-invalid data is a validation failure. Do not clamp or reorder it silently.

## Shared prompt baseline

Current prompt version: `baby-monitor-single-frame-v4-cat-detection`.

The shared English prompt:

- treats the input as one still frame;
- prohibits temporal, medical, breathing, emotion, and hidden-region inference;
- requires visible anatomical evidence for infants and cats;
- excludes dolls, plush toys, prints, bedding folds, patterns, shadows, and ambiguous shapes;
- defines posture, face visibility, blanket coverage, related objects, cat proximity, and risk semantics;
- embeds the exact generated JSON Schema;
- requires JSON only.

Providers transport this prompt unchanged. Model-family coordinate wording and the generated box description may differ only through `BoxCoordinateOrder`; semantic tasks must not differ by provider.

When changing semantic instructions:

1. Update `PROMPT_VERSION`.
2. Update prompt snapshot/assertion tests.
3. Explain comparison impact in `CHANGELOG.md`.
4. Run both provider request-mapping tests.
5. If possible, run explicit cross-model smoke comparisons using the same still images outside the repository.

Do not tune one provider by silently appending semantic hints in its adapter. If experimentation requires provider-specific semantics, make it an explicit new mode and preserve the shared baseline.

## Risk semantics

- `alert`: face/apparent airway region is visibly covered or blocked and should be checked immediately.
- `watch`: prone posture, invisible face, object near face, poor visibility, meaningful uncertainty, or a cat near/overlapping an infant warrants review.
- `normal`: no visible concern under the prompt definitions.
- `unknown`: no infant or unusable/insufficient visual evidence.

These are visual attention hints, not diagnoses or guarantees. The UI must not imply an automated safety alarm.

## Provider interface

`AnalysisRequest` carries JPEG bytes, MIME type, dimensions, shared prompt, model-specific JSON Schema, model name, and generation parameters. `ProviderCallResult` carries the unmodified raw output plus usage metadata.

Ollama adapter:

- calls `/api/chat` with one user message and base64 image;
- uses `format=<JSON Schema>`, non-streaming, `temperature=0`, and `think=false`;
- accepts final structured JSON from `message.thinking` only when `content` is empty, recording the selected field;
- maps prompt/eval counts to standardized input/output tokens.

Gemini adapter:

- creates one interaction containing prompt text and base64 image;
- uses the SDK's native async Interactions client with both request-level and outer coroutine timeouts, allowing monitor cancellation to propagate to the in-flight request;
- uses the google-genai 2.x Interactions schema, a single polymorphic JSON text response format, and `store=false` stateless requests;
- converts the complete Pydantic schema through the Google AI structured-output subset adapter before transport; the complete schema stays in the prompt and `FrameAnalysis` remains the final validator;
- omits the model-specific `thinking_level` field for the dynamic model list and uses the provider/model default; see [Gemini/Gemma provider rules](GEMINI_PROVIDER.md);
- lists compatible Gemini models plus hosted Gemma 4 image-input models supported by the Interactions API, while excluding older/text-only Gemma and embedding/image-generation/video/live/audio variants;
- counts thinking tokens as output tokens.

The monitor retries once for transient/unclassified provider failures and local validation failures. It does not replay a deterministic provider HTTP 4xx with an unchanged request. Raw responses, per-attempt usage, and errors remain available in history. A local JSON-envelope, non-object, or Pydantic validation failure receives a concise correction suffix on the second request; provider exceptions such as a missing SDK response field do not. Correction codes never copy raw model output into the next prompt, and the shared baseline itself remains preserved in history.

Model JSON parsing preserves every raw response unchanged in history. The validator accepts exactly one JSON value, optionally wrapped by a complete single empty/`json` Markdown code fence or followed only by an isolated closing fence. An opening fence without its closing fence is invalid. This narrow compatibility handles hosted models that append ` ``` ` despite structured-output mode. A second JSON value, prose, another fence block, or any other trailing content remains a visible parse failure; the parser never searches greedily for a convenient object.

## Frontend overlay contract

Overlay colors are stable category identifiers:

| Category | Color |
|---|---|
| Infant | `#56b8ff` |
| Face | `#55e6a5` |
| Blanket | `#f2b84b` |
| Pillow | `#8b9cff` |
| Toy | `#ff8a5b` |
| Hand | `#45d4d4` |
| Other occluder | `#ff5e6c` |
| Cat | `#d58cff` |

Color is determined by category, never risk relation. Main/history box widths are `2` and `1.5`; label backgrounds use category color with opacity `0.45`. The UI renders all validated model observations as returned. It does not deduplicate or reposition repeated model boxes.

## Contract-change checklist

- Pydantic model and validators updated.
- `schema_version` bumped when compatibility changes.
- `PROMPT_VERSION` bumped for semantic prompt changes.
- Coordinate conversion covers every box.
- Frontend types, labels, colors, cards, and overlays updated.
- API/history serialization remains backward-understood or release notes explain the break.
- Tests cover valid, invalid, empty, multi-subject, and coordinate-adapter cases.
- Provider structured-output mapping tested.
- Changelog and relevant docs updated.

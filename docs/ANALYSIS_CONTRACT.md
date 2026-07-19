# Analysis, prompt, coordinate, and provider contract

## Public result contract

`FrameAnalysis` in `babymonitorvl/schemas.py` is the single source of truth. Pydantic generates the JSON Schema sent to every provider. Frontend types mirror the validated API response; they are not an independent definition.

Current analysis schema version: `1.3`.

Top-level required fields:

- `schema_version`
- `summary`
- `image_quality`
- `infants`
- `adult_presence`
- `adults`
- `cats`
- `overall_risk`
- `risk_reasons`

An empty infant list requires `overall_risk=unknown`, even when an adult or cat is visible. Adult and cat observations can therefore be reported without inventing an infant or a baby-specific risk.

`adult_presence` is a conservative operational signal:

- `present` requires at least one matching grounded observation in `adults`.
- `not_detected` requires a sufficiently usable view and an empty `adults` list.
- `unknown` is required when image quality, occlusion, framing, or an age-ambiguous person prevents a reliable judgment; `adults` remains empty.

Every model is allowed to say `unknown`. The contract prefers uncertainty to hallucinated bodies, mouth/nose locations, adults, cats, blankets, or risks. Adult presence does not change infant risk in this version and does not yet pause analysis.

## Mouth/nose occlusion contract

The infant safety-oriented visual signal is object coverage of the combined mouth-and-nose region, not generic face visibility. `mouth_nose_occlusion` has five states:

- `clear`: the region is visible enough to establish that no object overlaps it.
- `partially_covered`: a visible object overlaps part, but not nearly all, of the region.
- `fully_covered`: a visible object overlaps nearly all of the region, including the expected locations of both mouth and nose.
- `not_visible`: head orientation, framing, or pose prevents a direct view and no covering object can be established.
- `unknown`: image quality or geometry cannot distinguish coverage from non-coverage.

`mouth_nose_box` is always a required response key but may be `null`. It may be grounded directly from visible landmarks or cautiously estimated from connected visible head geometry, face outline, orientation, and nearby facial features. A partial/full coverage result still requires visible object pixels to overlap that region. The model must not infer coverage merely because the landmarks are not visible, and must never infer airflow, breathing, suffocation, health, or medical status. Evidence states whether localization was direct or geometrically estimated.

`clear`, `partially_covered`, and `fully_covered` require a non-null `mouth_nose_box`. Partial/full states also require a boxed `related_objects` observation with a matching `partially_covers_mouth_nose` or `covers_mouth_nose` relation; full coverage specifically requires `covers_mouth_nose`. The relevant object box and `mouth_nose_box` must have a positive-area intersection. These cross-field checks prevent a coverage label that has no visible, spatially grounded occluding object.

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

Conversion must include `infant_box`, nullable `mouth_nose_box`, every `related_objects[].box`, every `adults[].adult_box`, and every `cats[].cat_box`. Add future box fields to conversion and tests in the same change. Raw responses are never rewritten in history.

Out-of-range, reversed, missing, non-integer, or enum-invalid data is a validation failure. Do not clamp or reorder it silently.

The runtime subject limits default to one infant and four adults and are configurable through `MAX_INFANTS` and `MAX_ADULTS`. The configured values appear in the full shared prompt and schema. Ollama receives those `maxItems` constraints directly. Gemini's smoke-validated compact transport schema omits `maxItems`, so local validation enforces the same limits after generation.

After canonical coordinate conversion, exact coordinate duplicates within the same semantic category are the only allowed post-processing exception. The first observation/box is retained and later identical boxes are removed. Infant, adult, and cat duplicates remove the later full observation; duplicate related objects use their object kind as the category and are deduplicated only within the same infant observation. The same visible object may legitimately relate to two different infants and must remain attached to both. The complete result is revalidated after deduplication so removing a contradictory duplicate cannot silently break mouth/nose grounding invariants. Every removal emits a server warning and a per-attempt history warning while preserving the byte-for-byte raw response. There is no IoU, approximate overlap, cross-category, tracking, or visual deduplication.

## Shared prompt baseline

Current prompt version: `baby-monitor-single-frame-v8-mouth-nose-occlusion`.

The shared English prompt:

- treats the input as one still frame;
- prohibits temporal, medical, breathing, airflow, emotion, and general hidden-region inference while allowing only the documented mouth/nose spatial estimate;
- requires visible anatomical evidence for infants, adults, and cats;
- treats adult presence as higher-priority than cat detection and rejects isolated limbs, reflections, photos, screens, dolls, prints, and age-ambiguous people as adult evidence;
- excludes dolls, plush toys, prints, bedding folds, patterns, shadows, and ambiguous shapes;
- defines posture, mouth/nose object coverage, blanket coverage, related objects, adult-presence consistency, cat proximity, and risk semantics;
- embeds the exact generated JSON Schema;
- requires JSON only.

The empty-scene risk invariant is repeated as a mandatory pre-output consistency check and in the `overall_risk` schema description. Some small VLMs still map a visibly safe empty room to `normal`. Because the contract uniquely determines the correction, the monitor converts `overall_risk` from `normal | watch | alert` to `unknown` only when the decoded `infants` value is exactly an empty array. This consumes no second model call, preserves the raw response, and records `contract_value_repaired` in both server logs and the per-attempt history warning. No uncertain visual field, subject, box, posture, visibility, coverage, or provider parse failure is repaired this way.

Providers transport this prompt unchanged. Model-family coordinate wording and the generated box description may differ only through `BoxCoordinateOrder`; semantic tasks must not differ by provider.

When changing semantic instructions:

1. Update `PROMPT_VERSION`.
2. Update prompt snapshot/assertion tests.
3. Explain comparison impact in `CHANGELOG.md`.
4. Run both provider request-mapping tests.
5. If possible, run explicit cross-model smoke comparisons using the same still images outside the repository.

Do not tune one provider by silently appending semantic hints in its adapter. If experimentation requires provider-specific semantics, make it an explicit new mode and preserve the shared baseline.

## Risk semantics

- `alert`: a visible object covers nearly all of the directly located or cautiously estimated combined mouth-and-nose region and should be checked immediately.
- `watch`: partial mouth/nose coverage, an object near the region, prone posture, mouth/nose not visible, poor visibility, meaningful uncertainty, or a cat near/overlapping an infant warrants review.
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

The monitor retries once for transient/unclassified provider failures and local validation failures. It does not replay a deterministic provider HTTP 4xx with an unchanged request. Every call stores an explicit attempt number, the exact prompt sent for that call, outcome, sanitized error type/message, response index, provider usage, retry decision, and retry reason. This mapping remains correct when a provider fails before producing either a response or usage data; UI code must not infer correspondence from parallel array indexes. A local JSON-envelope, non-object, or Pydantic validation failure receives a concise correction suffix on the second request; provider exceptions such as a missing SDK response field do not. Correction codes never copy raw model output into the next prompt. The top-level history prompt remains the immutable session baseline, while each attempt record is the authoritative byte-for-byte prompt audit for that model call; all copies count toward the history memory budget.

Model JSON parsing preserves every raw response unchanged in history. The validator accepts exactly one JSON value, optionally wrapped by a complete single empty/`json` Markdown code fence or followed only by an isolated closing fence. An opening fence without its closing fence is invalid. This narrow compatibility handles hosted models that append ` ``` ` despite structured-output mode. A second JSON value, prose, another fence block, or any other trailing content remains a visible parse failure; the parser never searches greedily for a convenient object.

## Frontend overlay contract

Overlay colors are stable category identifiers:

| Category | Color |
|---|---|
| Infant | `#56b8ff` |
| Mouth/nose | `#55e6a5` |
| Blanket | `#f2b84b` |
| Pillow | `#8b9cff` |
| Toy | `#ff8a5b` |
| Hand | `#45d4d4` |
| Other occluder | `#ff5e6c` |
| Cat | `#d58cff` |
| Adult | `#ff6bd6` |

Color is determined by category, never risk relation. Main/history box widths are `2` and `1.5`; label backgrounds use category color with opacity `0.45`. Adult state and details appear before cat state/details, and adult overlays render after cat overlays so the higher-priority adult label remains visible when they overlap. The UI renders the canonical backend result after exact same-category duplicate removal. It does not perform its own deduplication or reposition boxes.

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

import json

import pytest
from pydantic import ValidationError

from babymonitorvl.coordinates import (
    BoxCoordinateOrder,
    ModelOutputError,
    SubjectLimitError,
    decode_model_json_object,
    deduplicate_analysis_boxes,
    enforce_subject_limits,
    model_box_order,
    normalize_analysis_payload,
    parse_model_analysis,
    parse_model_analysis_with_repairs,
)
from babymonitorvl.prompt import PROMPT_VERSION, build_prompt, output_schema
from babymonitorvl.schemas import BoundingBox, FrameAnalysis, ProviderName


def test_bounding_box_contract() -> None:
    assert BoundingBox.model_validate([10, 20, 300, 400]).root == [10, 20, 300, 400]
    for invalid in ([10, 20, 10, 400], [-1, 20, 30, 40], [0, 0, 1001, 1000], [1, 2, 3]):
        with pytest.raises(ValidationError):
            BoundingBox.model_validate(invalid)


def test_empty_scene_requires_unknown_risk() -> None:
    valid = {
        "schema_version": "1.3",
        "summary": "No infant is visible.",
        "image_quality": "good",
        "infants": [],
        "adult_presence": "not_detected",
        "adults": [],
        "cats": [],
        "overall_risk": "unknown",
        "risk_reasons": [],
    }
    assert FrameAnalysis.model_validate(valid).infants == []
    valid["overall_risk"] = "normal"
    with pytest.raises(ValidationError):
        FrameAnalysis.model_validate(valid)


def test_empty_scene_risk_is_repaired_without_changing_raw_response() -> None:
    raw_response = json.dumps(
        {
            "schema_version": "1.3",
            "summary": "No infant is visible.",
            "image_quality": "good",
            "infants": [],
            "adult_presence": "not_detected",
            "adults": [],
            "cats": [],
            "overall_risk": "normal",
            "risk_reasons": ["No infant detected in frame"],
        }
    )

    analysis, warnings = parse_model_analysis_with_repairs(raw_response, BoxCoordinateOrder.YXYX)

    assert analysis.overall_risk.value == "unknown"
    assert warnings == [
        "contract_value_repaired field=overall_risk from=normal to=unknown reason=no_infant_detected"
    ]
    assert '"overall_risk": "normal"' in raw_response


def test_model_json_parser_accepts_only_optional_markdown_fence_wrapper() -> None:
    payload = {
        "schema_version": "1.3",
        "summary": "No infant is visible.",
        "image_quality": "good",
        "infants": [],
        "adult_presence": "not_detected",
        "adults": [],
        "cats": [],
        "overall_risk": "unknown",
        "risk_reasons": [],
    }
    encoded = json.dumps(payload)

    assert decode_model_json_object(encoded + "\n```") == payload
    assert decode_model_json_object("```json\n" + encoded + "\n```") == payload
    assert parse_model_analysis(encoded + "\n```", BoxCoordinateOrder.YXYX).summary == payload["summary"]


@pytest.mark.parametrize(
    "suffix",
    [
        '\n{"second": true}',
        "\nAnalysis complete.",
        "\n```\nextra",
    ],
)
def test_model_json_parser_rejects_non_fence_trailing_content(suffix: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        decode_model_json_object('{"ok": true}' + suffix)


def test_model_json_parser_rejects_unclosed_opening_fence() -> None:
    with pytest.raises(json.JSONDecodeError, match="Markdown fence is not closed"):
        decode_model_json_object('```json\n{"ok": true}')


def test_model_analysis_rejects_non_object_json() -> None:
    with pytest.raises(ModelOutputError, match="must be a JSON object"):
        parse_model_analysis("[]", BoxCoordinateOrder.YXYX)


def test_prompt_embeds_exact_schema() -> None:
    schema = output_schema()
    prompt = build_prompt(schema)
    assert PROMPT_VERSION == "baby-monitor-single-frame-v8-mouth-nose-occlusion"
    assert json.dumps(schema, ensure_ascii=False, separators=(",", ":")) in prompt
    assert "[ymin, xmin, ymax, xmax]" in prompt
    assert "Do not infer motion, breathing, airflow" in prompt
    assert {"infants", "adult_presence", "adults", "cats", "risk_reasons"}.issubset(schema["required"])
    assert schema["properties"]["infants"]["maxItems"] == 1
    assert schema["properties"]["adults"]["maxItems"] == 4
    related_object_schema = schema["$defs"]["RelatedObject"]
    infant_schema = schema["$defs"]["InfantObservation"]
    assert "mouth_nose_box" in infant_schema["properties"]
    assert "mouth_nose_box" in infant_schema["required"]
    assert "mouth_nose_occlusion" in infant_schema["required"]
    assert "face_box" not in infant_schema["properties"]
    assert "face_visibility" not in infant_schema["properties"]
    assert "box" in related_object_schema["required"]
    assert "null" not in json.dumps(related_object_schema["properties"]["box"])
    assert "do not create duplicate observations" in prompt
    assert "Every related_objects entry MUST have a box" in prompt
    assert "determine adult presence before looking for cats" in prompt
    assert "Never infer an adult from an isolated hand" in prompt
    assert "adult_presence=present exactly when adults contains at least one clear adult" in prompt
    assert "infant_box, adult_box, and cat_box" in prompt
    assert "configured maximum of 1" in prompt
    assert "configured maximum of 4 adults" in prompt
    assert "Never repeat an identical bounding box for the same category" in prompt
    assert 'If infants=[], overall_risk MUST be "unknown"' in prompt
    assert 'Use overall_risk="normal" only when infants contains at least one' in prompt
    assert "Cross-field rule" in schema["properties"]["overall_risk"]["description"]
    assert "living domestic cat" in prompt
    assert "Never label a plush toy" in prompt
    assert 'Return cats=[] when no real cat is clearly visible' in prompt
    assert "This is an occlusion assessment, not merely a visibility check" in prompt
    assert "MAY cautiously estimate mouth_nose_box" in prompt
    assert "visible object's pixels spatially overlap" in prompt
    assert "Never infer airflow, breathing, suffocation" in prompt
    assert "MOUTH/NOSE CONSISTENCY RULES" in prompt
    assert "fully_covered requires a visible related_objects entry" in prompt


def test_mouth_nose_coverage_is_distinct_from_simple_visibility() -> None:
    analysis = FrameAnalysis.model_validate(
        {
            "schema_version": "1.3",
            "summary": "A blanket visibly covers the infant's mouth and nose.",
            "image_quality": "good",
            "infants": [
                {
                    "infant_box": [100, 200, 900, 800],
                    "mouth_nose_box": [260, 430, 340, 520],
                    "posture": "supine",
                    "mouth_nose_occlusion": "fully_covered",
                    "blanket_coverage": "covering_mouth_nose",
                    "related_objects": [
                        {
                            "kind": "blanket",
                            "box": [280, 350, 700, 800],
                            "relation": "covers_mouth_nose",
                        }
                    ],
                    "risk_level": "alert",
                    "confidence": 0.88,
                    "evidence": ["Mouth/nose region estimated from visible head geometry; blanket overlaps it."],
                }
            ],
            "adult_presence": "not_detected",
            "adults": [],
            "cats": [],
            "overall_risk": "alert",
            "risk_reasons": ["Blanket covers the estimated mouth-and-nose region."],
        }
    )

    infant = analysis.infants[0]
    assert infant.mouth_nose_occlusion.value == "fully_covered"
    assert infant.related_objects[0].relation.value == "covers_mouth_nose"

    missing_object = analysis.model_dump(mode="json")
    missing_object["infants"][0]["related_objects"] = []
    with pytest.raises(ValidationError, match="fully_covered requires a grounded related object"):
        FrameAnalysis.model_validate(missing_object)

    disjoint_object = analysis.model_dump(mode="json")
    disjoint_object["infants"][0]["related_objects"][0]["box"] = [700, 700, 900, 900]
    with pytest.raises(ValidationError, match="spatially covering mouth/nose"):
        FrameAnalysis.model_validate(disjoint_object)

    missing_region = analysis.model_dump(mode="json")
    missing_region["infants"][0]["mouth_nose_box"] = None
    with pytest.raises(ValidationError, match="mouth_nose_box is required"):
        FrameAnalysis.model_validate(missing_region)


def test_subject_limits_are_injected_into_schema_and_prompt() -> None:
    schema = output_schema(max_infants=3, max_adults=7)
    prompt = build_prompt(schema)
    assert schema["properties"]["infants"]["maxItems"] == 3
    assert schema["properties"]["adults"]["maxItems"] == 7
    assert "configured maximum of 3" in prompt
    assert "configured maximum of 7 adults" in prompt


def test_cat_can_be_reported_without_an_infant() -> None:
    analysis = FrameAnalysis.model_validate(
        {
            "schema_version": "1.3",
            "summary": "A cat is visible; no infant is visible.",
            "image_quality": "good",
            "infants": [],
            "adult_presence": "not_detected",
            "adults": [],
            "cats": [
                {
                    "cat_box": [100, 200, 600, 800],
                    "proximity_to_infant": "unknown",
                    "confidence": 0.91,
                    "evidence": ["Feline ears, body, legs, and tail are visible."],
                }
            ],
            "overall_risk": "unknown",
            "risk_reasons": [],
        }
    )
    assert len(analysis.cats) == 1
    assert analysis.cats[0].cat_box.root == [100, 200, 600, 800]


def test_adult_presence_requires_matching_grounded_observations() -> None:
    valid = {
        "schema_version": "1.3",
        "summary": "An adult is visible; no infant is visible.",
        "image_quality": "good",
        "infants": [],
        "adult_presence": "present",
        "adults": [
            {
                "adult_box": [50, 100, 950, 600],
                "confidence": 0.94,
                "evidence": ["A mature face and connected adult-sized torso are visible."],
            }
        ],
        "cats": [],
        "overall_risk": "unknown",
        "risk_reasons": [],
    }
    analysis = FrameAnalysis.model_validate(valid)
    assert analysis.adults[0].adult_box.root == [50, 100, 950, 600]

    valid["adult_presence"] = "not_detected"
    with pytest.raises(ValidationError, match="adult_presence must be present"):
        FrameAnalysis.model_validate(valid)

    valid["adult_presence"] = "present"
    valid["adults"] = []
    with pytest.raises(ValidationError, match="cannot be present"):
        FrameAnalysis.model_validate(valid)


def test_exact_same_category_boxes_keep_first_and_report_warnings() -> None:
    infant = {
        "infant_box": [100, 200, 500, 700],
        "mouth_nose_box": None,
        "posture": "supine",
        "mouth_nose_occlusion": "not_visible",
        "blanket_coverage": "torso",
        "related_objects": [
            {"kind": "blanket", "box": [300, 250, 600, 750], "relation": "covers_body"},
            {"kind": "blanket", "box": [300, 250, 600, 750], "relation": "covers_body"},
        ],
        "risk_level": "watch",
        "confidence": 0.9,
        "evidence": ["Infant is visible."],
    }
    adult = {
        "adult_box": [10, 20, 900, 400],
        "confidence": 0.9,
        "evidence": ["Adult is visible."],
    }
    cat = {
        "cat_box": [600, 700, 900, 950],
        "proximity_to_infant": "separate",
        "confidence": 0.8,
        "evidence": ["Cat is visible."],
    }
    analysis = FrameAnalysis.model_validate(
        {
            "schema_version": "1.3",
            "summary": "One visible subject of each category.",
            "image_quality": "good",
            "infants": [infant, infant],
            "adult_presence": "present",
            "adults": [adult, adult],
            "cats": [cat, cat],
            "overall_risk": "watch",
            "risk_reasons": [],
        }
    )

    deduplicated, warnings = deduplicate_analysis_boxes(analysis)

    assert len(analysis.infants) == 2
    assert len(deduplicated.infants) == 1
    assert len(deduplicated.infants[0].related_objects) == 1
    assert len(deduplicated.adults) == 1
    assert len(deduplicated.cats) == 1
    assert {item.split()[1] for item in warnings} == {
        "category=infant",
        "category=adult",
        "category=cat",
        "category=blanket",
    }

    second_infant = deduplicated.infants[0].model_copy(
        update={"infant_box": BoundingBox.model_validate([110, 210, 510, 710])}
    )
    over_limit = deduplicated.model_copy(update={"infants": [deduplicated.infants[0], second_infant]})
    with pytest.raises(SubjectLimitError, match="configured maximum 1"):
        enforce_subject_limits(over_limit, max_infants=1, max_adults=4)


def test_same_related_object_box_is_retained_for_different_infants() -> None:
    shared_object = {"kind": "blanket", "box": [300, 250, 600, 750], "relation": "covers_body"}
    infant = {
        "infant_box": [100, 100, 450, 450],
        "mouth_nose_box": None,
        "posture": "supine",
        "mouth_nose_occlusion": "not_visible",
        "blanket_coverage": "torso",
        "related_objects": [shared_object],
        "risk_level": "watch",
        "confidence": 0.8,
        "evidence": ["Infant is visible."],
    }
    second_infant = {**infant, "infant_box": [500, 500, 900, 900]}
    analysis = FrameAnalysis.model_validate(
        {
            "schema_version": "1.3",
            "summary": "Two infants share one visible blanket.",
            "image_quality": "good",
            "infants": [infant, second_infant],
            "adult_presence": "not_detected",
            "adults": [],
            "cats": [],
            "overall_risk": "watch",
            "risk_reasons": ["Mouth/nose regions are not visible."],
        }
    )

    deduplicated, warnings = deduplicate_analysis_boxes(analysis)

    assert len(deduplicated.infants) == 2
    assert [len(item.related_objects) for item in deduplicated.infants] == [1, 1]
    assert warnings == []


def test_deduplication_revalidates_conflicting_object_relations() -> None:
    shared_box = [250, 250, 500, 600]
    analysis = FrameAnalysis.model_validate(
        {
            "schema_version": "1.3",
            "summary": "Conflicting duplicate blanket relations.",
            "image_quality": "good",
            "infants": [
                {
                    "infant_box": [100, 100, 800, 800],
                    "mouth_nose_box": [300, 350, 360, 430],
                    "posture": "supine",
                    "mouth_nose_occlusion": "fully_covered",
                    "blanket_coverage": "covering_mouth_nose",
                    "related_objects": [
                        {"kind": "blanket", "box": shared_box, "relation": "near_body"},
                        {"kind": "blanket", "box": shared_box, "relation": "covers_mouth_nose"},
                    ],
                    "risk_level": "alert",
                    "confidence": 0.7,
                    "evidence": ["Blanket overlaps the estimated mouth/nose region."],
                }
            ],
            "adult_presence": "not_detected",
            "adults": [],
            "cats": [],
            "overall_risk": "alert",
            "risk_reasons": ["Blanket covers mouth/nose."],
        }
    )

    with pytest.raises(ValidationError, match="fully_covered requires a grounded related object"):
        deduplicate_analysis_boxes(analysis)


@pytest.mark.parametrize(
    "model",
    [
        "qwen3-vl:8b",
        "qwen2.5-vl:7b",
        "qwen3.6:35b-a3b-mxfp8",
        "registry.example/library/QWEN-VL:latest",
    ],
)
def test_qwen_models_use_native_xyxy_order(model: str) -> None:
    order = model_box_order(ProviderName.OLLAMA, model)
    schema = output_schema(order)
    prompt = build_prompt(schema, order)
    assert order is BoxCoordinateOrder.XYXY
    assert "[xmin, ymin, xmax, ymax] (x first)" in prompt
    assert schema["$defs"]["BoundingBox"]["description"].startswith("[xmin, ymin")


def test_non_qwen_models_keep_canonical_yxyx_order() -> None:
    assert model_box_order(ProviderName.GEMINI, "gemini-2.5-flash") is BoxCoordinateOrder.YXYX
    assert model_box_order(ProviderName.OLLAMA, "other-vision:latest") is BoxCoordinateOrder.YXYX
    assert model_box_order(ProviderName.GEMINI, "qwen-compatible-proxy") is BoxCoordinateOrder.YXYX


def test_qwen_boxes_are_normalized_for_api_and_ui() -> None:
    raw = {
        "schema_version": "1.3",
        "summary": "One infant.",
        "image_quality": "good",
        "infants": [
            {
                "infant_box": [205, 306, 337, 548],
                "mouth_nose_box": [210, 320, 250, 390],
                "posture": "side_lying",
                "mouth_nose_occlusion": "clear",
                "blanket_coverage": "near_mouth_nose",
                "related_objects": [
                    {"kind": "blanket", "box": [239, 17, 387, 625], "relation": "near_body"}
                ],
                "risk_level": "watch",
                "confidence": 0.8,
                "evidence": ["Visible infant."],
            }
        ],
        "adult_presence": "present",
        "adults": [
            {
                "adult_box": [400, 100, 900, 950],
                "confidence": 0.95,
                "evidence": ["A clearly mature person is visible."],
            }
        ],
        "cats": [
            {
                "cat_box": [100, 200, 300, 400],
                "proximity_to_infant": "separate",
                "confidence": 0.9,
                "evidence": ["A cat is visible."],
            }
        ],
        "overall_risk": "watch",
        "risk_reasons": ["Blanket near infant."],
    }
    normalized = normalize_analysis_payload(raw, BoxCoordinateOrder.XYXY)
    infant = normalized["infants"][0]
    assert infant["infant_box"] == [306, 205, 548, 337]
    assert infant["mouth_nose_box"] == [320, 210, 390, 250]
    assert infant["related_objects"][0]["box"] == [17, 239, 625, 387]
    assert normalized["adults"][0]["adult_box"] == [100, 400, 950, 900]
    assert normalized["cats"][0]["cat_box"] == [200, 100, 400, 300]
    assert raw["infants"][0]["infant_box"] == [205, 306, 337, 548]
    assert raw["infants"][0]["mouth_nose_box"] == [210, 320, 250, 390]
    assert raw["adults"][0]["adult_box"] == [400, 100, 900, 950]
    assert raw["cats"][0]["cat_box"] == [100, 200, 300, 400]

import json

import pytest
from pydantic import ValidationError

from babymonitorvl.coordinates import (
    BoxCoordinateOrder,
    ModelOutputError,
    decode_model_json_object,
    model_box_order,
    normalize_analysis_payload,
    parse_model_analysis,
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
        "schema_version": "1.1",
        "summary": "No infant is visible.",
        "image_quality": "good",
        "infants": [],
        "cats": [],
        "overall_risk": "unknown",
        "risk_reasons": [],
    }
    assert FrameAnalysis.model_validate(valid).infants == []
    valid["overall_risk"] = "normal"
    with pytest.raises(ValidationError):
        FrameAnalysis.model_validate(valid)


def test_model_json_parser_accepts_only_optional_markdown_fence_wrapper() -> None:
    payload = {
        "schema_version": "1.1",
        "summary": "No infant is visible.",
        "image_quality": "good",
        "infants": [],
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
    assert PROMPT_VERSION == "baby-monitor-single-frame-v4-cat-detection"
    assert json.dumps(schema, ensure_ascii=False, separators=(",", ":")) in prompt
    assert "[ymin, xmin, ymax, xmax]" in prompt
    assert "Do not infer motion, breathing, health" in prompt
    assert {"infants", "cats", "risk_reasons"}.issubset(schema["required"])
    related_object_schema = schema["$defs"]["RelatedObject"]
    assert "box" in related_object_schema["required"]
    assert "null" not in json.dumps(related_object_schema["properties"]["box"])
    assert "do not create duplicate observations" in prompt
    assert "Every related_objects entry MUST have a box" in prompt
    assert "living domestic cat" in prompt
    assert "Never label a plush toy" in prompt
    assert 'Return cats=[] when no real cat is clearly visible' in prompt


def test_cat_can_be_reported_without_an_infant() -> None:
    analysis = FrameAnalysis.model_validate(
        {
            "schema_version": "1.1",
            "summary": "A cat is visible; no infant is visible.",
            "image_quality": "good",
            "infants": [],
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
        "schema_version": "1.1",
        "summary": "One infant.",
        "image_quality": "good",
        "infants": [
            {
                "infant_box": [205, 306, 337, 548],
                "face_box": [210, 320, 250, 390],
                "posture": "side_lying",
                "face_visibility": "visible",
                "blanket_coverage": "near_face",
                "related_objects": [
                    {"kind": "blanket", "box": [239, 17, 387, 625], "relation": "near_body"}
                ],
                "risk_level": "watch",
                "confidence": 0.8,
                "evidence": ["Visible infant."],
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
    assert infant["face_box"] == [320, 210, 390, 250]
    assert infant["related_objects"][0]["box"] == [17, 239, 625, 387]
    assert normalized["cats"][0]["cat_box"] == [200, 100, 400, 300]
    assert raw["infants"][0]["infant_box"] == [205, 306, 337, 548]
    assert raw["cats"][0]["cat_box"] == [100, 200, 300, 400]

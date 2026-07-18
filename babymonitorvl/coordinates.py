from __future__ import annotations

import copy
import json
from enum import Enum
from typing import Any

from .schemas import FrameAnalysis, ProviderName


class BoxCoordinateOrder(str, Enum):
    """Coordinate order used by a model response."""

    YXYX = "ymin_xmin_ymax_xmax"
    XYXY = "xmin_ymin_xmax_ymax"


CANONICAL_BOX_ORDER = BoxCoordinateOrder.YXYX


class ModelOutputError(ValueError):
    """The decoded model payload cannot be interpreted as an analysis object."""


def model_box_order(provider: ProviderName, model: str) -> BoxCoordinateOrder:
    """Return the native grounding convention for a provider/model pair.

    Qwen's grounding examples use normalized ``[x1, y1, x2, y2]``
    coordinates. All Ollama model basenames matching ``qwen*`` therefore use
    XYXY. Gemini and unknown models keep the public/canonical YXYX contract.
    """

    normalized_model = model.casefold().removeprefix("models/")
    model_basename = normalized_model.rsplit("/", 1)[-1]
    if provider is ProviderName.OLLAMA and model_basename.startswith("qwen"):
        return BoxCoordinateOrder.XYXY
    return CANONICAL_BOX_ORDER


def schema_for_box_order(schema: dict[str, Any], order: BoxCoordinateOrder) -> dict[str, Any]:
    """Return an isolated schema whose box description matches the model."""

    result = copy.deepcopy(schema)
    definitions = result.get("$defs")
    if isinstance(definitions, dict):
        box_schema = definitions.get("BoundingBox")
        if isinstance(box_schema, dict):
            if order is BoxCoordinateOrder.XYXY:
                box_schema["description"] = "[xmin, ymin, xmax, ymax], normalized to 0..1000."
            else:
                box_schema["description"] = "[ymin, xmin, ymax, xmax], normalized to 0..1000."
    return result


def normalize_analysis_payload(payload: dict[str, Any], order: BoxCoordinateOrder) -> dict[str, Any]:
    """Convert every model box to the API/UI canonical YXYX order."""

    result = copy.deepcopy(payload)
    if order is CANONICAL_BOX_ORDER:
        return result

    def normalize_box(box: Any) -> Any:
        if isinstance(box, list) and len(box) == 4:
            xmin, ymin, xmax, ymax = box
            return [ymin, xmin, ymax, xmax]
        return box

    infants = result.get("infants")
    if not isinstance(infants, list):
        return result
    for infant in infants:
        if not isinstance(infant, dict):
            continue
        infant["infant_box"] = normalize_box(infant.get("infant_box"))
        if infant.get("face_box") is not None:
            infant["face_box"] = normalize_box(infant.get("face_box"))
        related_objects = infant.get("related_objects")
        if isinstance(related_objects, list):
            for related_object in related_objects:
                if isinstance(related_object, dict):
                    related_object["box"] = normalize_box(related_object.get("box"))
    cats = result.get("cats")
    if isinstance(cats, list):
        for cat in cats:
            if isinstance(cat, dict):
                cat["cat_box"] = normalize_box(cat.get("cat_box"))
    return result


def parse_model_analysis(raw_response: str, order: BoxCoordinateOrder) -> FrameAnalysis:
    """Parse a model response and expose canonical coordinates to consumers."""

    payload = decode_model_json_object(raw_response)
    if not isinstance(payload, dict):
        raise ModelOutputError("model response must be a JSON object")
    return FrameAnalysis.model_validate(normalize_analysis_payload(payload, order))


def decode_model_json_object(raw_response: str) -> Any:
    """Decode one JSON value while tolerating only a Markdown fence wrapper.

    Some hosted models append a closing code fence even when structured JSON
    output is requested. Raw provider output remains unchanged in history; this
    parser removes no prose and never accepts a second JSON value.
    """

    text = raw_response.lstrip()
    opened_fence = False
    if text.startswith("```"):
        first_line_end = text.find("\n")
        if first_line_end < 0:
            raise json.JSONDecodeError("Markdown fence does not contain JSON", text, 0)
        fence_language = text[3:first_line_end].strip().casefold()
        if fence_language not in {"", "json"}:
            raise json.JSONDecodeError("Unsupported Markdown fence language", text, 3)
        text = text[first_line_end + 1 :]
        opened_fence = True

    payload, end = json.JSONDecoder().raw_decode(text)
    trailing = text[end:].strip()
    if trailing == "```":
        trailing = ""
    elif opened_fence:
        raise json.JSONDecodeError("Markdown fence is not closed", text, end)
    if trailing:
        raise json.JSONDecodeError("Extra data", text, end)
    return payload

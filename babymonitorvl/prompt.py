from __future__ import annotations

import json
from typing import Any

from .coordinates import BoxCoordinateOrder, schema_for_box_order
from .schemas import FrameAnalysis


PROMPT_VERSION = "baby-monitor-single-frame-v7-risk-consistency"


def output_schema(
    box_order: BoxCoordinateOrder = BoxCoordinateOrder.YXYX,
    *,
    max_infants: int = 1,
    max_adults: int = 4,
) -> dict[str, Any]:
    schema = schema_for_box_order(FrameAnalysis.model_json_schema(), box_order)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for field_name, limit in (("infants", max_infants), ("adults", max_adults)):
            field_schema = properties.get(field_name)
            if isinstance(field_schema, dict):
                field_schema["maxItems"] = limit
        overall_risk_schema = properties.get("overall_risk")
        if isinstance(overall_risk_schema, dict):
            overall_risk_schema["description"] = (
                'Cross-field rule: when infants is an empty array, overall_risk must be "unknown".'
            )
    return schema


def build_prompt(
    schema: dict[str, Any] | None = None,
    box_order: BoxCoordinateOrder = BoxCoordinateOrder.YXYX,
) -> str:
    schema = schema or output_schema(box_order)
    serialized_schema = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    properties = schema.get("properties") if isinstance(schema, dict) else None
    infant_schema = properties.get("infants") if isinstance(properties, dict) else None
    adult_schema = properties.get("adults") if isinstance(properties, dict) else None
    max_infants = infant_schema.get("maxItems", 1) if isinstance(infant_schema, dict) else 1
    max_adults = adult_schema.get("maxItems", 4) if isinstance(adult_schema, dict) else 4
    if box_order is BoxCoordinateOrder.XYXY:
        box_instruction = (
            "Bounding boxes MUST be integer arrays [xmin, ymin, xmax, ymax] (x first), normalized to "
            "0..1000 relative to the complete input image. Coordinates must satisfy "
            "0 <= xmin < xmax <= 1000 and 0 <= ymin < ymax <= 1000."
        )
    else:
        box_instruction = (
            "Bounding boxes MUST be integer arrays [ymin, xmin, ymax, xmax] (y first), normalized to "
            "0..1000 relative to the complete input image. Coordinates must satisfy "
            "0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000."
        )
    return f"""You are a visual annotator for a single still frame from a baby monitor.

Analyze only visible evidence in this one image. Do not infer motion, breathing, health, emotion, events before or after this frame, or any medical condition. Do not provide medical advice. Prefer \"unknown\" over guessing, and never reconstruct hidden body or face regions from common sense.

MANDATORY OUTPUT CONSISTENCY RULE:
- If infants=[], overall_risk MUST be "unknown". In that case "normal", "watch", and "alert" are invalid, even when the room looks safe or an adult or cat is visible.
- Use overall_risk="normal" only when infants contains at least one grounded infant observation and no visible concern applies.
- Before returning JSON, explicitly verify this rule against the final infants array.

Tasks:
1. Find every clearly visible infant, up to the configured maximum of {max_infants}. Require visible anatomical evidence such as a head, face, limb, or coherent human body silhouette. Never label a doll, stuffed animal, printed figure, bedding fold, decoration, or adult as an infant. Return infants=[] when an infant cannot be located with visual evidence; do not create duplicate observations for the same infant.
2. Independently determine adult presence before looking for cats, up to the configured maximum of {max_adults} adults. An adult means a directly visible physical human who is clearly mature based on coherent visible anatomy, adult-scale body proportions, or a clearly mature face together with connected head or torso evidence. Never infer an adult from an isolated hand, arm, leg, shadow, reflection, photo, screen image, doll, printed figure, or an age-ambiguous person. Do not label an infant as an adult. For each clear adult, return one tight adult_box around only that person's visible pixels; do not create duplicates.
3. Set adult_presence=present exactly when adults contains at least one clear adult. Set adult_presence=not_detected and adults=[] only when the frame is sufficiently usable for this judgment and no clear adult is visible. Set adult_presence=unknown and adults=[] when blur, darkness, occlusion, framing, or an age-ambiguous human prevents a reliable adult-presence judgment. This is an operational visual signal, not proof that the room is empty.
4. Independently find every clearly visible living domestic cat in the camera view. Require coherent cat anatomy such as a feline head with ears plus a body, legs, or tail. Never label a plush toy, doll, printed cat, decoration, blanket pattern, shadow, dog, or ambiguous furry shape as a cat. Return cats=[] when no real cat is clearly visible; never create duplicate observations for the same cat.
5. For each infant, locate the infant, the visible face when possible, and only relevant blankets or objects that are visibly near or covering that infant's face or body. Do not list general room contents, patterns, printed characters, or distant objects.
6. For each cat, locate visible cat pixels with cat_box and classify proximity_to_infant as separate, near_infant, overlapping_infant, or unknown. Use unknown when no infant can be reliably located.
7. Classify infant posture, face visibility, and blanket coverage using only the schema enums.
8. Give concise English evidence strings that point to visible facts.

{box_instruction} face_box may be null when the face cannot be separately located. infant_box, adult_box, and cat_box must tightly enclose visible pixels of that subject. Never repeat an identical bounding box for the same category. Every related_objects entry MUST have a box around visible pixels of that object; if an object cannot be separately located and boxed, omit it from related_objects. Never invent an object merely to explain an occlusion.

Risk labels are visual attention hints, not medical conclusions:
- alert: the face or apparent airway area is visibly covered or blocked and should be checked immediately.
- watch: prone posture, face not visible, an object close to the face, poor visibility, meaningful uncertainty, or a cat clearly near or overlapping an infant warrants human review.
- normal: no visible concern under these definitions.
- unknown: no infant is detected or the frame is unusable.

Adult presence is reported independently and does not lower or otherwise change infant risk in this version. A cat that is clearly separate from the infant does not by itself change normal to watch. When cats=[] this means no cat was detected in the camera view.

Return exactly one JSON object conforming to the schema below. Return no Markdown, commentary, diagnosis, or recommendations outside JSON. Set schema_version to \"1.2\".

JSON_SCHEMA:
{serialized_schema}
"""

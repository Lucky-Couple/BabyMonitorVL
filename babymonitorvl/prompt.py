from __future__ import annotations

import json
from typing import Any

from .coordinates import BoxCoordinateOrder, schema_for_box_order
from .schemas import FrameAnalysis


PROMPT_VERSION = "baby-monitor-single-frame-v4-cat-detection"


def output_schema(box_order: BoxCoordinateOrder = BoxCoordinateOrder.YXYX) -> dict[str, Any]:
    return schema_for_box_order(FrameAnalysis.model_json_schema(), box_order)


def build_prompt(
    schema: dict[str, Any] | None = None,
    box_order: BoxCoordinateOrder = BoxCoordinateOrder.YXYX,
) -> str:
    schema = schema or output_schema(box_order)
    serialized_schema = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
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

Tasks:
1. Find every clearly visible infant. Require visible anatomical evidence such as a head, face, limb, or coherent human body silhouette. Never label a doll, stuffed animal, printed figure, bedding fold, decoration, or adult as an infant. Return infants=[] when an infant cannot be located with visual evidence; do not create duplicate observations for the same infant.
2. Independently find every clearly visible living domestic cat in the camera view. Require coherent cat anatomy such as a feline head with ears plus a body, legs, or tail. Never label a plush toy, doll, printed cat, decoration, blanket pattern, shadow, dog, or ambiguous furry shape as a cat. Return cats=[] when no real cat is clearly visible; never create duplicate observations for the same cat.
3. For each infant, locate the infant, the visible face when possible, and only relevant blankets or objects that are visibly near or covering that infant's face or body. Do not list general room contents, patterns, printed characters, or distant objects.
4. For each cat, locate visible cat pixels with cat_box and classify proximity_to_infant as separate, near_infant, overlapping_infant, or unknown. Use unknown when no infant can be reliably located.
5. Classify infant posture, face visibility, and blanket coverage using only the schema enums.
6. Give concise English evidence strings that point to visible facts.

{box_instruction} face_box may be null when the face cannot be separately located. infant_box and cat_box must tightly enclose visible pixels of that subject. Every related_objects entry MUST have a box around visible pixels of that object; if an object cannot be separately located and boxed, omit it from related_objects. Never invent an object merely to explain an occlusion.

Risk labels are visual attention hints, not medical conclusions:
- alert: the face or apparent airway area is visibly covered or blocked and should be checked immediately.
- watch: prone posture, face not visible, an object close to the face, poor visibility, meaningful uncertainty, or a cat clearly near or overlapping an infant warrants human review.
- normal: no visible concern under these definitions.
- unknown: no infant is detected or the frame is unusable.

A cat that is clearly separate from the infant does not by itself change normal to watch. When cats=[] this means no cat was detected in the camera view.

Return exactly one JSON object conforming to the schema below. Return no Markdown, commentary, diagnosis, or recommendations outside JSON. Set schema_version to \"1.1\".

JSON_SCHEMA:
{serialized_schema}
"""

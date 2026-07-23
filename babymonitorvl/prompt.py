from __future__ import annotations

import json
from typing import Any

from .coordinates import BoxCoordinateOrder, schema_for_box_order
from .schemas import FrameAnalysis


PROMPT_VERSION = "baby-monitor-single-frame-v10-mouth-nose-spatial-preflight"


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

Analyze only visible evidence in this one image. Do not infer motion, breathing, airflow, health, emotion, events before or after this frame, or any medical condition. Do not provide medical advice. Prefer \"unknown\" over guessing. Do not reconstruct hidden anatomy; the only permitted inference is the narrow spatial estimate of the mouth-and-nose region described below.

MANDATORY OUTPUT CONSISTENCY RULE:
- If infants=[], overall_risk MUST be "unknown". In that case "normal", "watch", and "alert" are invalid, even when the room looks safe or an adult or cat is visible.
- Use overall_risk="normal" only when infants contains at least one grounded infant observation and no visible concern applies.
- Before returning JSON, explicitly verify this rule against the final infants array.

Tasks:
1. Find every clearly visible infant, up to the configured maximum of {max_infants}. Require visible anatomical evidence such as a head, face, limb, or coherent human body silhouette. Never label a doll, stuffed animal, printed figure, bedding fold, decoration, or adult as an infant. Return infants=[] when an infant cannot be located with visual evidence; do not create duplicate observations for the same infant.
2. Independently determine adult presence before looking for cats, up to the configured maximum of {max_adults} adults. An adult means a directly visible physical human who is clearly mature based on coherent visible anatomy, adult-scale body proportions, or a clearly mature face together with connected head or torso evidence. Never infer an adult from an isolated hand, arm, leg, shadow, reflection, photo, screen image, doll, printed figure, or an age-ambiguous person. Do not label an infant as an adult. For each clear adult, return one tight adult_box around only that person's visible pixels; do not create duplicates.
3. Set adult_presence=present exactly when adults contains at least one clear adult. Set adult_presence=not_detected and adults=[] only when the frame is sufficiently usable for this judgment and no clear adult is visible. Set adult_presence=unknown and adults=[] when blur, darkness, occlusion, framing, or an age-ambiguous human prevents a reliable adult-presence judgment. This is an operational visual signal, not proof that the room is empty.
4. Independently find every clearly visible living domestic cat in the camera view. Require coherent cat anatomy such as a feline head with ears plus a body, legs, or tail. Never label a plush toy, doll, printed cat, decoration, blanket pattern, shadow, dog, or ambiguous furry shape as a cat. Return cats=[] when no real cat is clearly visible; never create duplicate observations for the same cat.
5. For each infant, assess whether a visible blanket or other visible object spatially covers the infant's combined mouth-and-nose region. This is an occlusion assessment, not merely a visibility check:
   - clear: the mouth-and-nose region is visible enough to establish that no object overlaps it.
   - partially_covered: a visible object overlaps part, but not nearly all, of the mouth-and-nose region.
   - fully_covered: a visible object overlaps nearly all of the mouth-and-nose region, including the expected locations of both mouth and nose.
   - not_visible: the region is not directly visible because of head orientation, framing, or pose, and no visible covering object can be established.
   - unknown: image quality or geometry is insufficient to distinguish coverage from non-coverage.
6. Prefer direct mouth/nose landmark evidence. When landmarks are hidden, you MAY cautiously estimate mouth_nose_box from connected visible head geometry, face outline, head orientation, and nearby facial features. Mark partial or full coverage only when a visible object's pixels spatially overlap that directly located or cautiously estimated region. Never infer coverage only because the mouth or nose is not directly visible. Never infer airflow, breathing, suffocation, health, or medical risk.
7. Include only blankets or objects visibly near or covering the infant's mouth-and-nose region or body. Do not list general room contents, patterns, printed characters, or distant objects. Evidence must say whether the mouth/nose location was directly visible or geometrically estimated.
8. For each cat, locate visible cat pixels with cat_box and classify proximity_to_infant as separate, near_infant, overlapping_infant, or unknown. Use unknown when no infant can be reliably located.
9. Classify infant posture, mouth/nose occlusion, and blanket coverage using only the schema enums.
10. Give concise English evidence strings that point to visible facts.

INFRARED / NIGHT-VISION GUIDANCE:
- First recognize whether the frame appears to be monochrome infrared or night vision. Infrared grayscale is not by itself poor image quality, but clothing, blankets, and sheets may have nearly identical apparent color, brightness, and texture. In such frames, do not distinguish clothing from bedding by grayscale tone alone.
- Use visible geometry instead. Body-worn clothing normally stays within and closely follows a connected body silhouette. A blanket, sheet, or loose cover may drape beyond the infant's silhouette, bridge gaps between limbs, continue onto the mattress or crib, or form folds and edges independent of the body contour.
- Use a clearly visible bare thigh or leg segment as an anatomical anchor. Textile on the torso side of that exposed segment may be clothing only when it remains fitted to the connected body contour. Textile continuing past the exposed limb toward the feet, outside the body outline, or onto the mattress is not the same torso garment and may be bedding when loose textile geometry is visible. Do not use image-up/image-down alone as anatomical direction, and do not confuse a separate fitted sock or pant leg with bedding.
- Clothing does not count as blanket coverage and must not be added to related_objects. If infrared geometry is insufficient to separate fitted clothing from loose bedding, set blanket_coverage to unknown rather than guessing and mention the infrared ambiguity in evidence. If an uncertain textile nevertheless has visible pixels clearly overlapping mouth_nose_box, record it as other_occluder with the matching mouth/nose relation and assess the geometric occlusion; do not suppress clear overlap merely because the textile class is uncertain.

MOUTH/NOSE CONSISTENCY RULES:
- clear, partially_covered, and fully_covered require a non-null mouth_nose_box.
- partially_covered requires a visible related_objects entry whose relation is partially_covers_mouth_nose or covers_mouth_nose.
- fully_covered requires a visible related_objects entry whose relation is covers_mouth_nose.
- not_visible and unknown do not prove object coverage. Do not add a covering object unless its pixels are visible and can be boxed.
- Before choosing partially_covered or fully_covered, numerically verify positive-area intersection between mouth_nose_box and the SAME related object box. After unpacking coordinates according to the required order, BOTH conditions must be true: max(mouth_ymin, object_ymin) < min(mouth_ymax, object_ymax) AND max(mouth_xmin, object_xmin) < min(mouth_xmax, object_xmax).
- If either intersection condition is false, that object does not cover the mouth/nose. Do not use partially_covered, fully_covered, partially_covers_mouth_nose, or covers_mouth_nose for it. Use near_mouth_nose only when it is visibly near, and independently choose clear, not_visible, or unknown from the mouth/nose evidence.
- A blanket used as the covering object must agree with blanket_coverage: partially_covered maps to partially_covering_mouth_nose and fully_covered maps to covering_mouth_nose. A blanket below the face or covering only the body cannot justify mouth/nose coverage.
- Final preflight for every infant: first lock mouth_nose_box, then lock each tight visible object box, then perform the numeric intersection test, and only then select mouth_nose_occlusion, object relation, blanket_coverage, risk, and evidence. Never claim overlap from prose or appearance when the returned coordinates are disjoint.

{box_instruction} mouth_nose_box must be a small, tight region around the directly visible or cautiously estimated combined mouth-and-nose area; it may be null only when that region cannot be localized reliably enough for a spatial overlap judgment. infant_box, adult_box, and cat_box must tightly enclose visible pixels of that subject. Never repeat an identical bounding box for the same category. Every related_objects entry MUST have a box around visible pixels of that object; if an object cannot be separately located and boxed, omit it from related_objects. Use near_mouth_nose, partially_covers_mouth_nose, or covers_mouth_nose only from visible object geometry. Never invent an object merely to explain an occlusion.

Risk labels are visual attention hints, not medical conclusions:
- alert: a visible object clearly covers nearly all of the directly located or cautiously estimated mouth-and-nose region and should be checked immediately.
- watch: partial mouth/nose coverage, an object near that region, prone posture, mouth/nose not visible, poor visibility, meaningful uncertainty, or a cat clearly near or overlapping an infant warrants human review.
- normal: no visible concern under these definitions.
- unknown: no infant is detected or the frame is unusable.

Adult presence is reported independently and does not lower or otherwise change infant risk in this version. A cat that is clearly separate from the infant does not by itself change normal to watch. When cats=[] this means no cat was detected in the camera view.

Return exactly one JSON object conforming to the schema below. Return no Markdown, commentary, diagnosis, or recommendations outside JSON. Set schema_version to \"1.3\".

JSON_SCHEMA:
{serialized_schema}
"""

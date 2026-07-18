from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


class BoundingBox(RootModel[list[int]]):
    """[ymin, xmin, ymax, xmax], normalized to 0..1000."""

    root: Annotated[list[int], Field(min_length=4, max_length=4)]

    @field_validator("root")
    @classmethod
    def validate_box(cls, value: list[int]) -> list[int]:
        if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            raise ValueError("bounding box coordinates must be integers")
        if any(item < 0 or item > 1000 for item in value):
            raise ValueError("bounding box coordinates must be in 0..1000")
        ymin, xmin, ymax, xmax = value
        if ymin >= ymax or xmin >= xmax:
            raise ValueError("bounding box must satisfy ymin < ymax and xmin < xmax")
        return value


class RiskLevel(str, Enum):
    NORMAL = "normal"
    WATCH = "watch"
    ALERT = "alert"
    UNKNOWN = "unknown"


class ImageQuality(str, Enum):
    GOOD = "good"
    POOR = "poor"
    UNUSABLE = "unusable"
    UNKNOWN = "unknown"


class Posture(str, Enum):
    SUPINE = "supine"
    PRONE = "prone"
    SIDE_LYING = "side_lying"
    NOT_LYING = "not_lying"
    UNKNOWN = "unknown"


class FaceVisibility(str, Enum):
    VISIBLE = "visible"
    PARTIALLY_OCCLUDED = "partially_occluded"
    FULLY_OCCLUDED = "fully_occluded"
    NOT_VISIBLE = "not_visible"
    UNKNOWN = "unknown"


class BlanketCoverage(str, Enum):
    ABSENT = "absent"
    PRESENT_NOT_COVERING = "present_not_covering"
    LOWER_BODY = "lower_body"
    TORSO = "torso"
    NEAR_FACE = "near_face"
    COVERING_FACE = "covering_face"
    UNKNOWN = "unknown"


class RelatedObjectKind(str, Enum):
    BLANKET = "blanket"
    PILLOW = "pillow"
    TOY = "toy"
    HAND = "hand"
    OTHER_OCCLUDER = "other_occluder"


class ObjectRelation(str, Enum):
    NEAR_FACE = "near_face"
    COVERS_FACE = "covers_face"
    COVERS_BODY = "covers_body"
    NEAR_BODY = "near_body"
    UNKNOWN = "unknown"


class CatProximity(str, Enum):
    SEPARATE = "separate"
    NEAR_INFANT = "near_infant"
    OVERLAPPING_INFANT = "overlapping_infant"
    UNKNOWN = "unknown"


class RelatedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RelatedObjectKind
    box: BoundingBox
    relation: ObjectRelation


class InfantObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infant_box: BoundingBox
    face_box: BoundingBox | None = None
    posture: Posture
    face_visibility: FaceVisibility
    blanket_coverage: BlanketCoverage
    related_objects: list[RelatedObject] = Field(max_length=8)
    risk_level: RiskLevel
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(max_length=3)


class CatObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cat_box: BoundingBox
    proximity_to_infant: CatProximity
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(max_length=2)


class FrameAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"]
    summary: str = Field(max_length=500)
    image_quality: ImageQuality
    infants: list[InfantObservation] = Field(max_length=8)
    cats: list[CatObservation] = Field(max_length=4)
    overall_risk: RiskLevel
    risk_reasons: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_empty_scene(self) -> "FrameAnalysis":
        if not self.infants and self.overall_risk is not RiskLevel.UNKNOWN:
            raise ValueError("overall_risk must be unknown when no infant is detected")
        return self


class ProviderName(str, Enum):
    OLLAMA = "ollama"
    GEMINI = "gemini"


class MonitorStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rtsp_url: str = Field(min_length=1, max_length=4096)
    fps: float = Field(default=1.0, ge=0.1, le=10.0)
    provider: ProviderName = ProviderName.OLLAMA
    model: str | None = Field(default=None, max_length=256)
    rtsp_transport: Literal["tcp", "udp"] = "tcp"
    max_image_edge: int = Field(default=1280, ge=320, le=4096)

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp_url(cls, value: str) -> str:
        if not value.lower().startswith(("rtsp://", "rtsps://")):
            raise ValueError("URL must use rtsp:// or rtsps://")
        return value


class HistoryItem(BaseModel):
    id: str
    session_id: str
    captured_at: str
    completed_at: str | None
    provider: ProviderName
    model: str
    source: str
    status: Literal["pending", "success", "error"]
    analysis: FrameAnalysis | None
    raw_responses: list[str]
    errors: list[str]
    latency_ms: float | None
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    prompt_version: str
    prompt: str
    output_schema: dict
    generation_params: dict
    image_width: int
    image_height: int
    image_url: str


class HistorySummary(BaseModel):
    id: str
    session_id: str
    captured_at: str
    completed_at: str | None
    provider: ProviderName
    model: str
    status: Literal["pending", "success", "error"]
    analysis: FrameAnalysis | None
    overall_risk: RiskLevel | None
    latency_ms: float | None
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    error: str | None
    image_width: int
    image_height: int
    image_url: str

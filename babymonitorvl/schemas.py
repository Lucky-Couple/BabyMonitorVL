from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, SecretStr, field_validator, model_validator


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


class StabilityPhase(str, Enum):
    WARMING_UP = "warming_up"
    STABLE = "stable"


class StableObjectCategory(str, Enum):
    INFANT = "infant"
    MOUTH_NOSE = "mouth_nose"
    ADULT = "adult"
    CAT = "cat"
    BLANKET = "blanket"
    PILLOW = "pillow"
    TOY = "toy"
    HAND = "hand"
    OTHER_OCCLUDER = "other_occluder"


class StableSignalState(str, Enum):
    PRESENT = "present"
    NOT_DETECTED = "not_detected"
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


class MouthNoseOcclusion(str, Enum):
    CLEAR = "clear"
    PARTIALLY_COVERED = "partially_covered"
    FULLY_COVERED = "fully_covered"
    NOT_VISIBLE = "not_visible"
    UNKNOWN = "unknown"


class BlanketCoverage(str, Enum):
    ABSENT = "absent"
    PRESENT_NOT_COVERING = "present_not_covering"
    LOWER_BODY = "lower_body"
    TORSO = "torso"
    NEAR_MOUTH_NOSE = "near_mouth_nose"
    PARTIALLY_COVERING_MOUTH_NOSE = "partially_covering_mouth_nose"
    COVERING_MOUTH_NOSE = "covering_mouth_nose"
    UNKNOWN = "unknown"


class RelatedObjectKind(str, Enum):
    BLANKET = "blanket"
    PILLOW = "pillow"
    TOY = "toy"
    HAND = "hand"
    OTHER_OCCLUDER = "other_occluder"


class ObjectRelation(str, Enum):
    NEAR_MOUTH_NOSE = "near_mouth_nose"
    PARTIALLY_COVERS_MOUTH_NOSE = "partially_covers_mouth_nose"
    COVERS_MOUTH_NOSE = "covers_mouth_nose"
    COVERS_BODY = "covers_body"
    NEAR_BODY = "near_body"
    UNKNOWN = "unknown"


class CatProximity(str, Enum):
    SEPARATE = "separate"
    NEAR_INFANT = "near_infant"
    OVERLAPPING_INFANT = "overlapping_infant"
    UNKNOWN = "unknown"


class AdultPresence(str, Enum):
    PRESENT = "present"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


class RelatedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RelatedObjectKind
    box: BoundingBox = Field(
        description=(
            "Tight box around visible object pixels. A mouth/nose coverage relation requires this "
            "box to have positive-area intersection with the same infant's mouth_nose_box."
        )
    )
    relation: ObjectRelation = Field(
        description=(
            "Visible spatial relation. partially_covers_mouth_nose and covers_mouth_nose require "
            "positive-area box intersection with the same infant's mouth_nose_box."
        )
    )


class InfantObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infant_box: BoundingBox
    mouth_nose_box: BoundingBox | None = Field(
        description="Directly located or cautiously geometry-estimated combined mouth-and-nose region.",
    )
    posture: Posture
    mouth_nose_occlusion: MouthNoseOcclusion = Field(
        description=(
            "Whether a visible object spatially covers the combined mouth-and-nose region. Partial "
            "or full coverage requires a matching related-object relation and positive-area box "
            "intersection."
        )
    )
    blanket_coverage: BlanketCoverage
    related_objects: list[RelatedObject] = Field(max_length=8)
    risk_level: RiskLevel
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_mouth_nose_grounding(self) -> "InfantObservation":
        grounded_states = {
            MouthNoseOcclusion.CLEAR,
            MouthNoseOcclusion.PARTIALLY_COVERED,
            MouthNoseOcclusion.FULLY_COVERED,
        }
        if self.mouth_nose_occlusion in grounded_states and self.mouth_nose_box is None:
            raise ValueError(
                "mouth_nose_box is required for clear, partially_covered, or fully_covered assessments"
            )
        mouth_nose_box = self.mouth_nose_box.root if self.mouth_nose_box is not None else None

        def spatially_overlaps_mouth_nose(item: RelatedObject) -> bool:
            if mouth_nose_box is None:
                return False
            mouth_ymin, mouth_xmin, mouth_ymax, mouth_xmax = mouth_nose_box
            object_ymin, object_xmin, object_ymax, object_xmax = item.box.root
            return max(mouth_ymin, object_ymin) < min(mouth_ymax, object_ymax) and max(
                mouth_xmin, object_xmin
            ) < min(mouth_xmax, object_xmax)

        overlapping_relations = {
            item.relation for item in self.related_objects if spatially_overlaps_mouth_nose(item)
        }
        if (
            self.mouth_nose_occlusion is MouthNoseOcclusion.PARTIALLY_COVERED
            and not overlapping_relations.intersection(
                {ObjectRelation.PARTIALLY_COVERS_MOUTH_NOSE, ObjectRelation.COVERS_MOUTH_NOSE}
            )
        ):
            raise ValueError("partially_covered requires a grounded related object spatially overlapping mouth/nose")
        if (
            self.mouth_nose_occlusion is MouthNoseOcclusion.FULLY_COVERED
            and ObjectRelation.COVERS_MOUTH_NOSE not in overlapping_relations
        ):
            raise ValueError("fully_covered requires a grounded related object spatially covering mouth/nose")
        return self


class CatObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cat_box: BoundingBox
    proximity_to_infant: CatProximity
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(max_length=2)


class AdultObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adult_box: BoundingBox
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(max_length=2)


class FrameAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.3"]
    summary: str = Field(max_length=500)
    image_quality: ImageQuality
    infants: list[InfantObservation] = Field(max_length=64)
    adult_presence: AdultPresence
    adults: list[AdultObservation] = Field(max_length=64)
    cats: list[CatObservation] = Field(max_length=4)
    overall_risk: RiskLevel
    risk_reasons: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_empty_scene(self) -> "FrameAnalysis":
        if not self.infants and self.overall_risk is not RiskLevel.UNKNOWN:
            raise ValueError("overall_risk must be unknown when no infant is detected")
        if self.adults and self.adult_presence is not AdultPresence.PRESENT:
            raise ValueError("adult_presence must be present when adults are detected")
        if not self.adults and self.adult_presence is AdultPresence.PRESENT:
            raise ValueError("adult_presence cannot be present without an adult observation")
        return self


class ProviderName(str, Enum):
    OLLAMA = "ollama"
    GEMINI = "gemini"


class GeminiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=4096)


class HistoryStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: int = Field(default=0, ge=0)
    bytes: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=0, ge=0)


class MonitorStatus(BaseModel):
    """Runtime and public monitor status with assignment-time validation."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    state: Literal["stopped", "connecting", "streaming", "reconnecting"] = "stopped"
    session_id: str | None = None
    source: str | None = None
    provider: Literal["ollama", "gemini"] | None = None
    model: str | None = None
    min_frame_interval_seconds: float | None = Field(default=None, ge=0.1, le=3600.0)
    submitted_count: int = Field(default=0, ge=0)
    completed_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    last_capture_at: str | None = None
    last_analysis_at: str | None = None
    last_latency_ms: float | None = Field(default=None, ge=0)
    last_record_id: str | None = None
    last_error: str | None = None
    reconnect_attempt: int = Field(default=0, ge=0)
    reconnect_delay_seconds: int | None = Field(default=None, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    history: HistoryStats = Field(default_factory=HistoryStats)
    alarm: StabilizedSnapshot | None = None


class MonitorStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rtsp_url: str = Field(min_length=1, max_length=4096)
    min_frame_interval_seconds: float = Field(default=1.0, ge=0.1, le=3600.0)
    provider: ProviderName = ProviderName.OLLAMA
    model: str | None = Field(default=None, max_length=256)
    rtsp_transport: Literal["tcp", "udp"] = "tcp"

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp_url(cls, value: str) -> str:
        if not value.lower().startswith(("rtsp://", "rtsps://")):
            raise ValueError("URL must use rtsp:// or rtsps://")
        return value


class AnalysisAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int = Field(ge=1)
    prompt: str
    outcome: Literal["success", "validation_error", "provider_error", "cancelled"]
    error_type: str | None = None
    error: str | None = None
    response_index: int | None = Field(default=None, ge=0)
    usage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    will_retry: bool = False
    retry_reason: str | None = None


class StableObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str
    category: StableObjectCategory
    box: BoundingBox
    confidence: float = Field(ge=0, le=1)
    support_count: int = Field(ge=0)
    window_count: int = Field(ge=0)
    missed_frames: int = Field(ge=0)


class StableSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: StableObjectCategory
    state: StableSignalState
    count: int = Field(ge=0)
    support_count: int = Field(ge=0)
    window_count: int = Field(ge=0)


class StableAlarmReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["watch", "alert"]
    support_count: int = Field(ge=1)
    window_count: int = Field(ge=1)


class StabilizedSnapshot(BaseModel):
    """Derived temporal signal; never replaces the raw per-frame FrameAnalysis."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    record_id: str | None = None
    observed_at: str | None = None
    sequence: int = Field(default=0, ge=0)
    phase: StabilityPhase = StabilityPhase.WARMING_UP
    sample_count: int = Field(default=0, ge=0)
    window_size: int = Field(ge=1)
    confirmation_frames: int = Field(ge=1)
    clear_frames: int = Field(ge=1)
    raw_risk: RiskLevel = RiskLevel.UNKNOWN
    stable_risk: RiskLevel = RiskLevel.UNKNOWN
    alarm_active: bool = False
    changed_at: str | None = None
    reasons: list[StableAlarmReason] = Field(default_factory=list)
    signals: list[StableSignal] = Field(default_factory=list)
    objects: list[StableObject] = Field(default_factory=list)


class AlarmTimelinePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    record_id: str
    observed_at: str
    raw_risk: RiskLevel
    stable_risk: RiskLevel
    phase: StabilityPhase
    alarm_active: bool
    reason_codes: list[str] = Field(default_factory=list)


class AlarmState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: StabilizedSnapshot | None = None
    timeline: list[AlarmTimelinePoint] = Field(default_factory=list)


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
    stabilized: StabilizedSnapshot | None
    raw_responses: list[str]
    errors: list[str]
    warnings: list[str]
    attempt_details: list[AnalysisAttempt]
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
    stabilized: StabilizedSnapshot | None
    overall_risk: RiskLevel | None
    latency_ms: float | None
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    error: str | None
    image_width: int
    image_height: int
    image_url: str

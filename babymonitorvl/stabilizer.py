from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

from .schemas import (
    AlarmState,
    AlarmTimelinePoint,
    BoundingBox,
    FrameAnalysis,
    RiskLevel,
    StableAlarmReason,
    StableObject,
    StableObjectCategory,
    StableSignal,
    StableSignalState,
    StabilizedSnapshot,
    StabilityPhase,
)


_CATEGORY_ORDER = tuple(StableObjectCategory)
_RISK_RANK = {
    RiskLevel.UNKNOWN: 0,
    RiskLevel.NORMAL: 1,
    RiskLevel.WATCH: 2,
    RiskLevel.ALERT: 3,
}


@dataclass(frozen=True, slots=True)
class StabilizerConfig:
    window_size: int = 5
    confirmation_frames: int = 3
    clear_frames: int = 3
    box_iou_threshold: float = 0.2
    box_ema_alpha: float = 0.35
    timeline_max_points: int = 500

    def __post_init__(self) -> None:
        if self.window_size < 3 or self.window_size > 120:
            raise ValueError("stability window_size must be between 3 and 120")
        if self.confirmation_frames < 2 or self.confirmation_frames > self.window_size:
            raise ValueError("stability confirmation_frames must be between 2 and window_size")
        if self.clear_frames < 1 or self.clear_frames > self.window_size:
            raise ValueError("stability clear_frames must be between 1 and window_size")
        if not 0 < self.box_iou_threshold <= 1:
            raise ValueError("stability box_iou_threshold must be in (0, 1]")
        if not 0 < self.box_ema_alpha <= 1:
            raise ValueError("stability box_ema_alpha must be in (0, 1]")
        if self.timeline_max_points < 1:
            raise ValueError("stability timeline_max_points must be greater than 0")


@dataclass(frozen=True, slots=True)
class _Detection:
    category: StableObjectCategory
    box: tuple[int, int, int, int]
    confidence: float


@dataclass(slots=True)
class _Track:
    track_id: str
    category: StableObjectCategory
    box: list[float]
    confidence: float
    hits: deque[bool]
    missed_frames: int = 0


@dataclass(frozen=True, slots=True)
class _FrameVote:
    risk: RiskLevel
    reasons: frozenset[tuple[str, RiskLevel]]
    present_categories: frozenset[StableObjectCategory]


def _iou(left: list[float], right: tuple[int, int, int, int]) -> float:
    lymin, lxmin, lymax, lxmax = left
    rymin, rxmin, rymax, rxmax = right
    intersection_h = max(0.0, min(lymax, rymax) - max(lymin, rymin))
    intersection_w = max(0.0, min(lxmax, rxmax) - max(lxmin, rxmin))
    intersection = intersection_h * intersection_w
    if intersection <= 0:
        return 0.0
    left_area = (lymax - lymin) * (lxmax - lxmin)
    right_area = (rymax - rymin) * (rxmax - rxmin)
    return intersection / (left_area + right_area - intersection)


def _rounded_box(box: list[float]) -> BoundingBox:
    ymin, xmin, ymax, xmax = box
    return BoundingBox(
        [
            max(0, math.floor(ymin)),
            max(0, math.floor(xmin)),
            min(1000, math.ceil(ymax)),
            min(1000, math.ceil(xmax)),
        ]
    )


def _max_risk(*risks: RiskLevel) -> RiskLevel:
    return max(risks, key=_RISK_RANK.__getitem__)


def _frame_vote(analysis: FrameAnalysis) -> _FrameVote:
    reasons: set[tuple[str, RiskLevel]] = set()
    risk = analysis.overall_risk
    for infant in analysis.infants:
        risk = _max_risk(risk, infant.risk_level)
        if infant.risk_level is RiskLevel.ALERT:
            reasons.add(("model_infant_alert", RiskLevel.ALERT))
        elif infant.risk_level is RiskLevel.WATCH:
            reasons.add(("model_infant_watch", RiskLevel.WATCH))
        if infant.mouth_nose_occlusion.value == "fully_covered":
            reasons.add(("mouth_nose_fully_covered", RiskLevel.ALERT))
            risk = RiskLevel.ALERT
        elif infant.mouth_nose_occlusion.value == "partially_covered":
            reasons.add(("mouth_nose_partially_covered", RiskLevel.WATCH))
            risk = _max_risk(risk, RiskLevel.WATCH)
        elif infant.mouth_nose_occlusion.value == "not_visible":
            reasons.add(("mouth_nose_not_visible", RiskLevel.WATCH))
            risk = _max_risk(risk, RiskLevel.WATCH)
        if infant.posture.value == "prone":
            reasons.add(("prone_posture", RiskLevel.WATCH))
            risk = _max_risk(risk, RiskLevel.WATCH)
        if infant.blanket_coverage.value == "covering_mouth_nose":
            reasons.add(("blanket_covering_mouth_nose", RiskLevel.ALERT))
            risk = RiskLevel.ALERT
        elif infant.blanket_coverage.value in {
            "near_mouth_nose",
            "partially_covering_mouth_nose",
        }:
            reasons.add(("blanket_near_mouth_nose", RiskLevel.WATCH))
            risk = _max_risk(risk, RiskLevel.WATCH)
    if analysis.infants and analysis.overall_risk is RiskLevel.ALERT:
        reasons.add(("model_overall_alert", RiskLevel.ALERT))
    elif analysis.infants and analysis.overall_risk is RiskLevel.WATCH:
        reasons.add(("model_overall_watch", RiskLevel.WATCH))
    if analysis.infants and any(
        cat.proximity_to_infant.value in {"near_infant", "overlapping_infant"}
        for cat in analysis.cats
    ):
        reasons.add(("cat_near_infant", RiskLevel.WATCH))
        risk = _max_risk(risk, RiskLevel.WATCH)
    if not analysis.infants:
        risk = RiskLevel.UNKNOWN
        reasons.clear()
    detections = _detections(analysis)
    return _FrameVote(
        risk=risk,
        reasons=frozenset(reasons),
        present_categories=frozenset(item.category for item in detections),
    )


def _detections(analysis: FrameAnalysis) -> list[_Detection]:
    detections: list[_Detection] = []
    seen: set[tuple[StableObjectCategory, tuple[int, int, int, int]]] = set()

    def add(category: StableObjectCategory, box: BoundingBox, confidence: float) -> None:
        coordinates = tuple(box.root)
        key = (category, coordinates)
        if key not in seen:
            detections.append(_Detection(category, coordinates, confidence))
            seen.add(key)

    for infant in analysis.infants:
        add(StableObjectCategory.INFANT, infant.infant_box, infant.confidence)
        if infant.mouth_nose_box is not None:
            add(StableObjectCategory.MOUTH_NOSE, infant.mouth_nose_box, infant.confidence)
        for item in infant.related_objects:
            add(StableObjectCategory(item.kind.value), item.box, infant.confidence)
    for adult in analysis.adults:
        add(StableObjectCategory.ADULT, adult.adult_box, adult.confidence)
    for cat in analysis.cats:
        add(StableObjectCategory.CAT, cat.cat_box, cat.confidence)
    return detections


class TemporalStabilizer:
    """Filter validated VLM structures without inspecting image pixels."""

    def __init__(self, config: StabilizerConfig) -> None:
        self.config = config
        self._session_id: str | None = None
        self._votes: deque[_FrameVote] = deque(maxlen=config.window_size)
        self._tracks: list[_Track] = []
        self._timeline: deque[AlarmTimelinePoint] = deque(maxlen=config.timeline_max_points)
        self._current: StabilizedSnapshot | None = None
        self._sequence = 0
        self._track_sequence = 0
        self._stable_risk = RiskLevel.UNKNOWN
        self._changed_at: str | None = None
        self._lower_risk_streak = 0

    def start_session(self, session_id: str) -> StabilizedSnapshot:
        self._session_id = session_id
        self._votes.clear()
        self._tracks.clear()
        self._timeline.clear()
        self._sequence = 0
        self._track_sequence = 0
        self._stable_risk = RiskLevel.UNKNOWN
        self._changed_at = None
        self._lower_risk_streak = 0
        self._current = self._snapshot()
        return self._current.model_copy(deep=True)

    def observe(
        self,
        *,
        session_id: str,
        record_id: str,
        observed_at: datetime,
        analysis: FrameAnalysis,
    ) -> StabilizedSnapshot:
        if session_id != self._session_id:
            raise ValueError("stabilizer session does not match active monitor session")
        observed_at_text = observed_at.isoformat()
        self._sequence += 1
        vote = _frame_vote(analysis)
        self._votes.append(vote)
        self._update_tracks(_detections(analysis))
        self._update_risk(vote.risk, observed_at_text)
        reasons = self._stable_reasons()
        self._current = self._snapshot(
            record_id=record_id,
            observed_at=observed_at_text,
            raw_risk=vote.risk,
            reasons=reasons,
        )
        self._timeline.append(
            AlarmTimelinePoint(
                sequence=self._sequence,
                record_id=record_id,
                observed_at=observed_at_text,
                raw_risk=vote.risk,
                stable_risk=self._stable_risk,
                phase=self._current.phase,
                alarm_active=self._current.alarm_active,
                reason_codes=[item.code for item in self._current.reasons],
            )
        )
        return self._current.model_copy(deep=True)

    def state(self) -> AlarmState:
        return AlarmState(
            current=self._current.model_copy(deep=True) if self._current else None,
            timeline=[item.model_copy(deep=True) for item in self._timeline],
        )

    def _update_risk(self, raw_risk: RiskLevel, observed_at: str) -> None:
        alert_votes = sum(item.risk is RiskLevel.ALERT for item in self._votes)
        watch_votes = sum(item.risk in {RiskLevel.WATCH, RiskLevel.ALERT} for item in self._votes)
        previous = self._stable_risk
        if previous is RiskLevel.ALERT:
            self._lower_risk_streak = self._lower_risk_streak + 1 if raw_risk is not RiskLevel.ALERT else 0
            if self._lower_risk_streak >= self.config.clear_frames:
                self._stable_risk = self._candidate_risk(alert_votes, watch_votes)
        elif previous is RiskLevel.WATCH:
            if alert_votes >= self.config.confirmation_frames:
                self._stable_risk = RiskLevel.ALERT
                self._lower_risk_streak = 0
            else:
                self._lower_risk_streak = (
                    self._lower_risk_streak + 1
                    if raw_risk not in {RiskLevel.WATCH, RiskLevel.ALERT}
                    else 0
                )
                if self._lower_risk_streak >= self.config.clear_frames:
                    self._stable_risk = self._candidate_risk(alert_votes, watch_votes)
        else:
            self._stable_risk = self._candidate_risk(alert_votes, watch_votes)
            self._lower_risk_streak = 0
        if self._stable_risk is not previous:
            self._changed_at = observed_at
            self._lower_risk_streak = 0

    def _candidate_risk(self, alert_votes: int, watch_votes: int) -> RiskLevel:
        if alert_votes >= self.config.confirmation_frames:
            return RiskLevel.ALERT
        if watch_votes >= self.config.confirmation_frames:
            return RiskLevel.WATCH
        if len(self._votes) < self.config.confirmation_frames:
            return RiskLevel.UNKNOWN
        normal_votes = sum(item.risk is RiskLevel.NORMAL for item in self._votes)
        if normal_votes >= self.config.confirmation_frames:
            return RiskLevel.NORMAL
        return RiskLevel.UNKNOWN

    def _stable_reasons(self) -> list[StableAlarmReason]:
        if self._stable_risk not in {RiskLevel.WATCH, RiskLevel.ALERT}:
            return []
        counts: dict[tuple[str, RiskLevel], int] = defaultdict(int)
        for vote in self._votes:
            for reason in vote.reasons:
                counts[reason] += 1
        result = [
            StableAlarmReason(
                code=code,
                severity="alert" if severity is RiskLevel.ALERT else "watch",
                support_count=count,
                window_count=len(self._votes),
            )
            for (code, severity), count in counts.items()
            if count > 0 and _RISK_RANK[severity] >= _RISK_RANK[self._stable_risk]
        ]
        return sorted(result, key=lambda item: (-_RISK_RANK[RiskLevel(item.severity)], item.code))

    def _update_tracks(self, detections: list[_Detection]) -> None:
        by_category: dict[StableObjectCategory, list[_Detection]] = defaultdict(list)
        for detection in detections:
            by_category[detection.category].append(detection)
        for category in _CATEGORY_ORDER:
            tracks = [track for track in self._tracks if track.category is category]
            candidates = by_category.get(category, [])
            matches: list[tuple[float, int, int]] = []
            for track_index, track in enumerate(tracks):
                for detection_index, detection in enumerate(candidates):
                    score = _iou(track.box, detection.box)
                    if score >= self.config.box_iou_threshold:
                        matches.append((score, track_index, detection_index))
            used_tracks: set[int] = set()
            used_detections: set[int] = set()
            for _, track_index, detection_index in sorted(matches, reverse=True):
                if track_index in used_tracks or detection_index in used_detections:
                    continue
                track = tracks[track_index]
                detection = candidates[detection_index]
                alpha = self.config.box_ema_alpha
                track.box = [
                    (1 - alpha) * old + alpha * new
                    for old, new in zip(track.box, detection.box, strict=True)
                ]
                track.confidence = (1 - alpha) * track.confidence + alpha * detection.confidence
                track.hits.append(True)
                track.missed_frames = 0
                used_tracks.add(track_index)
                used_detections.add(detection_index)
            for track_index, track in enumerate(tracks):
                if track_index not in used_tracks:
                    track.hits.append(False)
                    track.missed_frames += 1
            for detection_index, detection in enumerate(candidates):
                if detection_index in used_detections:
                    continue
                self._track_sequence += 1
                self._tracks.append(
                    _Track(
                        track_id=f"{category.value}-{self._track_sequence}",
                        category=category,
                        box=[float(value) for value in detection.box],
                        confidence=detection.confidence,
                        hits=deque([True], maxlen=self.config.window_size),
                    )
                )
        self._tracks = [
            track for track in self._tracks if track.missed_frames < self.config.clear_frames
        ]

    def _signals(self) -> list[StableSignal]:
        window_count = len(self._votes)
        confirmed = self._stable_objects()
        counts = defaultdict(int)
        for item in confirmed:
            counts[item.category] += 1
        signals = []
        for category in _CATEGORY_ORDER:
            support_count = sum(category in vote.present_categories for vote in self._votes)
            if support_count >= self.config.confirmation_frames:
                state = StableSignalState.PRESENT
            elif window_count - support_count >= self.config.confirmation_frames:
                state = StableSignalState.NOT_DETECTED
            else:
                state = StableSignalState.UNKNOWN
            signals.append(
                StableSignal(
                    category=category,
                    state=state,
                    count=counts[category] if state is StableSignalState.PRESENT else 0,
                    support_count=support_count,
                    window_count=window_count,
                )
            )
        return signals

    def _stable_objects(self) -> list[StableObject]:
        result = []
        for track in self._tracks:
            support_count = sum(track.hits)
            if support_count < self.config.confirmation_frames:
                continue
            result.append(
                StableObject(
                    track_id=track.track_id,
                    category=track.category,
                    box=_rounded_box(track.box),
                    confidence=max(0.0, min(1.0, track.confidence)),
                    support_count=support_count,
                    window_count=len(self._votes),
                    missed_frames=track.missed_frames,
                )
            )
        return sorted(result, key=lambda item: (_CATEGORY_ORDER.index(item.category), item.track_id))

    def _snapshot(
        self,
        *,
        record_id: str | None = None,
        observed_at: str | None = None,
        raw_risk: RiskLevel = RiskLevel.UNKNOWN,
        reasons: list[StableAlarmReason] | None = None,
    ) -> StabilizedSnapshot:
        phase = (
            StabilityPhase.STABLE
            if len(self._votes) >= self.config.confirmation_frames
            else StabilityPhase.WARMING_UP
        )
        return StabilizedSnapshot(
            session_id=self._session_id or "",
            record_id=record_id,
            observed_at=observed_at,
            sequence=self._sequence,
            phase=phase,
            sample_count=self._sequence,
            window_size=self.config.window_size,
            confirmation_frames=self.config.confirmation_frames,
            clear_frames=self.config.clear_frames,
            raw_risk=raw_risk,
            stable_risk=self._stable_risk,
            alarm_active=self._stable_risk is RiskLevel.ALERT,
            changed_at=self._changed_at,
            reasons=list(reasons or []),
            signals=self._signals(),
            objects=self._stable_objects(),
        )

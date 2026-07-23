from datetime import datetime, timedelta, timezone

from babymonitorvl.schemas import FrameAnalysis, RiskLevel, StableObjectCategory
from babymonitorvl.stabilizer import StabilizerConfig, TemporalStabilizer


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def analysis(
    risk: str = "normal",
    *,
    box: list[int] | None = None,
    infant: bool = True,
) -> FrameAnalysis:
    infants = []
    if infant:
        infant_box = box or [100, 100, 400, 400]
        infants = [
            {
                "infant_box": infant_box,
                "mouth_nose_box": [
                    infant_box[0] + 20,
                    infant_box[1] + 20,
                    infant_box[0] + 80,
                    infant_box[1] + 80,
                ],
                "posture": "supine",
                "mouth_nose_occlusion": "clear",
                "blanket_coverage": "absent",
                "related_objects": [],
                "risk_level": risk,
                "confidence": 0.9,
                "evidence": ["visible infant"],
            }
        ]
    return FrameAnalysis.model_validate(
        {
            "schema_version": "1.3",
            "summary": "test frame",
            "image_quality": "good",
            "infants": infants,
            "adult_presence": "not_detected",
            "adults": [],
            "cats": [],
            "overall_risk": risk if infant else "unknown",
            "risk_reasons": [],
        }
    )


def observe(
    stabilizer: TemporalStabilizer,
    index: int,
    frame: FrameAnalysis,
):
    return stabilizer.observe(
        session_id="session",
        record_id=f"record-{index}",
        observed_at=BASE_TIME + timedelta(seconds=index),
        analysis=frame,
    )


def test_two_outlier_alerts_do_not_raise_stable_alarm() -> None:
    stabilizer = TemporalStabilizer(
        StabilizerConfig(window_size=5, confirmation_frames=3, clear_frames=3)
    )
    stabilizer.start_session("session")
    snapshots = [
        observe(stabilizer, index, analysis(risk))
        for index, risk in enumerate(["normal", "alert", "normal", "alert", "normal"], start=1)
    ]

    assert all(not item.alarm_active for item in snapshots)
    assert snapshots[-1].stable_risk is RiskLevel.NORMAL
    assert len(stabilizer.state().timeline) == 5


def test_alarm_requires_confirmation_and_uses_clear_hysteresis() -> None:
    stabilizer = TemporalStabilizer(
        StabilizerConfig(window_size=5, confirmation_frames=3, clear_frames=3)
    )
    stabilizer.start_session("session")

    first = observe(stabilizer, 1, analysis("alert"))
    second = observe(stabilizer, 2, analysis("alert"))
    confirmed = observe(stabilizer, 3, analysis("alert"))
    held_one = observe(stabilizer, 4, analysis("normal"))
    held_two = observe(stabilizer, 5, analysis("normal"))
    cleared = observe(stabilizer, 6, analysis("normal"))

    assert first.stable_risk is RiskLevel.UNKNOWN
    assert second.stable_risk is RiskLevel.UNKNOWN
    assert confirmed.stable_risk is RiskLevel.ALERT
    assert confirmed.alarm_active is True
    assert {reason.code for reason in confirmed.reasons} >= {
        "model_infant_alert",
        "model_overall_alert",
    }
    assert held_one.stable_risk is RiskLevel.ALERT
    assert held_two.stable_risk is RiskLevel.ALERT
    assert cleared.stable_risk is RiskLevel.NORMAL
    assert cleared.alarm_active is False


def test_boxes_are_confirmed_smoothed_and_expire_after_missing_frames() -> None:
    stabilizer = TemporalStabilizer(
        StabilizerConfig(
            window_size=5,
            confirmation_frames=3,
            clear_frames=3,
            box_iou_threshold=0.2,
            box_ema_alpha=0.5,
        )
    )
    stabilizer.start_session("session")

    observe(stabilizer, 1, analysis(box=[100, 100, 400, 400]))
    observe(stabilizer, 2, analysis(box=[110, 110, 410, 410]))
    confirmed = observe(stabilizer, 3, analysis(box=[120, 120, 420, 420]))
    infant = next(
        item for item in confirmed.objects if item.category is StableObjectCategory.INFANT
    )
    assert infant.box.root == [112, 112, 413, 413]
    assert infant.support_count == 3

    held = observe(stabilizer, 4, analysis(infant=False))
    held_infant = next(
        item for item in held.objects if item.category is StableObjectCategory.INFANT
    )
    assert held_infant.missed_frames == 1
    observe(stabilizer, 5, analysis(infant=False))
    expired = observe(stabilizer, 6, analysis(infant=False))
    assert not any(
        item.category is StableObjectCategory.INFANT for item in expired.objects
    )


def test_new_session_clears_timeline_tracks_and_alarm() -> None:
    stabilizer = TemporalStabilizer(
        StabilizerConfig(window_size=5, confirmation_frames=3, clear_frames=3)
    )
    stabilizer.start_session("session")
    for index in range(1, 4):
        observe(stabilizer, index, analysis("watch"))
    assert stabilizer.state().current is not None
    assert stabilizer.state().current.stable_risk is RiskLevel.WATCH

    reset = stabilizer.start_session("next-session")

    assert reset.session_id == "next-session"
    assert reset.sample_count == 0
    assert reset.stable_risk is RiskLevel.UNKNOWN
    assert reset.objects == []
    assert stabilizer.state().timeline == []


def test_ambiguous_presence_is_unknown_until_presence_or_absence_is_confirmed() -> None:
    stabilizer = TemporalStabilizer(
        StabilizerConfig(window_size=5, confirmation_frames=3, clear_frames=3)
    )
    stabilizer.start_session("session")
    observe(stabilizer, 1, analysis())
    observe(stabilizer, 2, analysis())
    snapshot = observe(stabilizer, 3, analysis(infant=False))

    infant_signal = next(
        item for item in snapshot.signals if item.category is StableObjectCategory.INFANT
    )
    assert infant_signal.state.value == "unknown"
    assert infant_signal.support_count == 2
    assert infant_signal.window_count == 3


def test_timeline_has_an_independent_bounded_telemetry_limit() -> None:
    stabilizer = TemporalStabilizer(
        StabilizerConfig(
            window_size=5,
            confirmation_frames=3,
            clear_frames=3,
            timeline_max_points=3,
        )
    )
    stabilizer.start_session("session")
    for index in range(1, 6):
        observe(stabilizer, index, analysis())

    assert [item.sequence for item in stabilizer.state().timeline] == [3, 4, 5]

import re
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel

from babymonitorvl.schemas import (
    AdultPresence,
    AlarmState,
    AlarmTimelinePoint,
    AnalysisAttempt,
    BlanketCoverage,
    CatProximity,
    FrameAnalysis,
    HistoryItem,
    HistorySummary,
    HistoryStats,
    ImageQuality,
    MonitorStatus,
    MouthNoseOcclusion,
    ObjectRelation,
    Posture,
    ProviderName,
    RelatedObjectKind,
    RiskLevel,
    StabilizedSnapshot,
    StableAlarmReason,
    StableObject,
    StableObjectCategory,
    StableSignal,
    StableSignalState,
    StabilityPhase,
)


ROOT = Path(__file__).resolve().parents[1]
TYPESCRIPT_TYPES = (ROOT / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")


def typescript_union_values(name: str) -> set[str]:
    match = re.search(
        rf"export\s+type\s+{re.escape(name)}\s*=\s*(.*?);",
        TYPESCRIPT_TYPES,
        flags=re.DOTALL,
    )
    assert match is not None, f"frontend type alias {name} is missing"
    return set(re.findall(r'"([^"\n]+)"', match.group(1)))


def typescript_interface_fields(name: str) -> set[str]:
    match = re.search(
        rf"export\s+interface\s+{re.escape(name)}\s*\{{(.*?)\n\}}",
        TYPESCRIPT_TYPES,
        flags=re.DOTALL,
    )
    assert match is not None, f"frontend interface {name} is missing"
    return set(re.findall(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)\??\s*:", match.group(1), re.MULTILINE))


def typescript_optional_interface_fields(name: str) -> set[str]:
    match = re.search(
        rf"export\s+interface\s+{re.escape(name)}\s*\{{(.*?)\n\}}",
        TYPESCRIPT_TYPES,
        flags=re.DOTALL,
    )
    assert match is not None, f"frontend interface {name} is missing"
    return set(re.findall(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)\?\s*:", match.group(1), re.MULTILINE))


@pytest.mark.parametrize(
    ("python_enum", "typescript_name"),
    [
        (ProviderName, "ProviderName"),
        (RiskLevel, "Risk"),
        (ImageQuality, "ImageQuality"),
        (Posture, "Posture"),
        (MouthNoseOcclusion, "MouthNoseOcclusion"),
        (BlanketCoverage, "BlanketCoverage"),
        (RelatedObjectKind, "RelatedObjectKind"),
        (ObjectRelation, "ObjectRelation"),
        (CatProximity, "CatProximity"),
        (AdultPresence, "AdultPresence"),
        (StabilityPhase, "StabilityPhase"),
        (StableSignalState, "StableSignalState"),
        (StableObjectCategory, "StableObjectCategory"),
    ],
)
def test_frontend_enum_union_matches_backend(
    python_enum: type[Enum], typescript_name: str
) -> None:
    assert typescript_union_values(typescript_name) == {item.value for item in python_enum}


@pytest.mark.parametrize(
    ("python_model", "typescript_name"),
    [
        (FrameAnalysis, "FrameAnalysis"),
        (HistoryStats, "HistoryStats"),
        (MonitorStatus, "MonitorStatus"),
        (AnalysisAttempt, "AnalysisAttempt"),
        (StableObject, "StableObject"),
        (StableSignal, "StableSignal"),
        (StableAlarmReason, "StableAlarmReason"),
        (StabilizedSnapshot, "StabilizedSnapshot"),
        (AlarmTimelinePoint, "AlarmTimelinePoint"),
        (AlarmState, "AlarmState"),
        (HistorySummary, "HistorySummary"),
        (HistoryItem, "HistoryDetail"),
    ],
)
def test_frontend_interface_fields_match_backend(
    python_model: type[BaseModel], typescript_name: str
) -> None:
    assert typescript_interface_fields(typescript_name) == set(python_model.model_fields)


def test_monitor_state_values_match_frontend() -> None:
    state_schema = MonitorStatus.model_json_schema()["properties"]["state"]
    assert typescript_union_values("MonitorState") == set(state_schema["enum"])


def test_frame_analysis_required_fields_remain_required_in_frontend() -> None:
    required = set(FrameAnalysis.model_json_schema()["required"])
    assert required == typescript_interface_fields("FrameAnalysis")
    assert typescript_optional_interface_fields("FrameAnalysis") == set()


def test_frontend_defaults_latest_analysis_to_raw_boxes_and_uses_live_mjpeg() -> None:
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'useState<"stable" | "raw">("raw")' in app_source
    assert "实时 RTSP 预览" in app_source
    assert "preview_fps" in app_source
    assert "preview_bitrate_kbps" in app_source
    assert "actual_interval_seconds" not in app_source
    assert "analysis_bitrate_kbps" not in app_source


def test_frontend_alarm_events_append_timeline_without_full_refetch() -> None:
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    handler = re.search(
        r'if \(event\.type === "alarm_updated"(.*?)\n\s{8}\}',
        app_source,
        flags=re.DOTALL,
    )
    assert handler is not None
    assert "mergeAlarmSnapshot" in handler.group(1)
    assert "fetchAlarm" not in handler.group(1)
    assert "监控已停止，以下为上次会话保留结果" in app_source

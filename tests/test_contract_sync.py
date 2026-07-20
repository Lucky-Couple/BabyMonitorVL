import re
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel

from babymonitorvl.schemas import (
    AdultPresence,
    BlanketCoverage,
    CatProximity,
    FrameAnalysis,
    HistoryStats,
    ImageQuality,
    MonitorStatus,
    MouthNoseOcclusion,
    ObjectRelation,
    Posture,
    ProviderName,
    RelatedObjectKind,
    RiskLevel,
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

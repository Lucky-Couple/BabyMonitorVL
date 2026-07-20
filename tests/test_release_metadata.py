import json
import re
import tomllib
from pathlib import Path
from typing import get_args

from babymonitorvl import __version__
from babymonitorvl.config import Settings
from babymonitorvl.main import create_app
from babymonitorvl.prompt import PROMPT_VERSION
from babymonitorvl.schemas import FrameAnalysis


ROOT = Path(__file__).resolve().parent.parent


def project_metadata() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def frontend_metadata() -> dict:
    return json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))


def test_application_versions_stay_in_sync(tmp_path) -> None:
    project = project_metadata()["project"]
    python_project_version = project["version"]
    frontend_version = frontend_metadata()["version"]
    assert python_project_version == __version__ == frontend_version
    assert project["license"] == "MIT"
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License\n")
    assert create_app(Settings(frontend_dist=tmp_path, gemini_api_key=None)).version == __version__


def test_changelog_records_current_release_contract() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    current_release = changelog.split(f"## [{__version__}]", maxsplit=1)[1].split(
        "\n## [", maxsplit=1
    )[0]
    schema_versions = get_args(FrameAnalysis.model_fields["schema_version"].annotation)
    assert len(schema_versions) == 1
    assert f"Application version: `{__version__}`." in current_release
    assert f"Prompt version: `{PROMPT_VERSION}`." in current_release
    assert f"Analysis schema version: `{schema_versions[0]}`." in current_release


def test_frontend_release_dependencies_do_not_use_latest() -> None:
    package = frontend_metadata()
    declared = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    assert declared
    assert "latest" not in declared.values()


def test_project_has_no_conventional_cv_dependencies() -> None:
    dependencies = " ".join(project_metadata()["project"]["dependencies"]).casefold()
    forbidden = ("opencv", "ultralytics", "yolo", "mediapipe", "detectron", "torchvision")
    assert not any(name in dependencies for name in forbidden)


def test_docker_context_excludes_local_caches_tests_and_docs() -> None:
    entries = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        ".rpiv",
        "frontend/node_modules",
        "frontend/dist",
        "frontend/.pnpm-store",
        "tests",
        "docs",
        ".env",
    } <= entries


def test_production_server_has_explicit_websocket_ping_policy() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"--ws-ping-interval", "20"' in dockerfile
    assert '"--ws-ping-timeout", "20"' in dockerfile


def test_relative_markdown_links_resolve() -> None:
    link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    missing: list[str] = []
    for document in ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in document.relative_to(ROOT).parts):
            continue
        for target in link_pattern.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = target.split("#", 1)[0]
            if path_text and not (document.parent / path_text).resolve().exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    assert not missing, "missing documentation links:\n" + "\n".join(missing)

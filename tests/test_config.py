import pytest

from babymonitorvl.config import Settings


def test_subject_limits_default_and_environment_override(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MAX_INFANTS", raising=False)
    monkeypatch.delenv("MAX_ADULTS", raising=False)
    defaults = Settings(frontend_dist=tmp_path)
    assert defaults.max_infants == 1
    assert defaults.max_adults == 4

    monkeypatch.setenv("MAX_INFANTS", "3")
    monkeypatch.setenv("MAX_ADULTS", "7")
    configured = Settings(frontend_dist=tmp_path)
    assert configured.max_infants == 3
    assert configured.max_adults == 7


@pytest.mark.parametrize(("name", "value"), [("MAX_INFANTS", "0"), ("MAX_ADULTS", "65")])
def test_subject_limits_reject_unsafe_values(monkeypatch, tmp_path, name: str, value: str) -> None:
    monkeypatch.delenv("MAX_INFANTS", raising=False)
    monkeypatch.delenv("MAX_ADULTS", raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="must be between 1 and 64"):
        Settings(frontend_dist=tmp_path)

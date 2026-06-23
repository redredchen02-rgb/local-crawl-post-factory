"""CI gate: pyproject.toml, VERSION, and CHANGELOG must agree on the version."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "version not found in pyproject.toml"
    return m.group(1)


def _file_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _changelog_version() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"^##\s+\[([^\]]+)\]", line)
        if m and m.group(1).lower() != "unreleased":
            return m.group(1)
    raise AssertionError("No versioned section found in CHANGELOG.md")


def test_version_consistency():
    pv = _pyproject_version()
    fv = _file_version()
    cv = _changelog_version()
    assert pv == fv, f"pyproject.toml ({pv}) != VERSION ({fv})"
    assert pv == cv, f"pyproject.toml ({pv}) != CHANGELOG latest ({cv})"


def test_scripts_match_entry_points():
    """README command list includes all pyproject [project.scripts] keys."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"\[project\.scripts\](.*?)(?=\[|\Z)", pyproject, re.DOTALL)
    assert m, "[project.scripts] not found"
    entry_points = {
        line.split("=")[0].strip()
        for line in m.group(1).splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = {cmd for cmd in entry_points if cmd not in readme}
    assert not missing, f"Commands in pyproject but not mentioned in README: {missing}"

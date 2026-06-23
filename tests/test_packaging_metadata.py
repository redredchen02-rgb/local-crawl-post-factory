"""D1: packaging metadata safety tests.

Guards against accidental PyPI upload (Private :: Do Not Upload classifier)
and verifies the dist-name constant is authoritative.
"""

import tomllib
from pathlib import Path

import cpost


_ROOT = Path(__file__).parent.parent


def _pyproject() -> dict:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_dist_name_constant_matches_pyproject():
    """__dist_name__ in cpost/__init__.py must match pyproject [project].name (D1)."""
    proj = _pyproject()
    assert cpost.__dist_name__ == proj["project"]["name"]


def test_private_do_not_upload_classifier_present():
    """pyproject must carry 'Private :: Do Not Upload' to block accidental PyPI upload (D1)."""
    proj = _pyproject()
    classifiers = proj["project"].get("classifiers", [])
    assert "Private :: Do Not Upload" in classifiers, (
        f"Missing anti-upload classifier. Got: {classifiers}"
    )



"""Portability guard (R1/R4): tracked functional files must stay free of
hardcoded, machine-specific absolute paths, and the shipped WebUI config must
keep its runtime paths relative. Locks in the standalone-relocation work so an
external reference can't silently creep back in.

Documentation (``docs/``, ``*.md``) is intentionally out of scope -- it is
scanned separately.
"""

import re
import subprocess
from pathlib import Path

from core import webui_config

_ROOT = Path(__file__).resolve().parent.parent

# A user-specific home prefix like /Users/<name>/ or /home/<name>/.
# System paths (/usr, /var, /tmp, /opt, /private/var) are NOT machine-specific
# in the portability sense and are deliberately excluded.
_HOME_ABS = re.compile(r"/(?:Users|home)/[^/\n\r\t\"' ]+/")

_SKIP_SUFFIXES = {".md", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
                  ".sqlite", ".pyc"}
_SKIP_DIRS = ("docs/",)
_SELF = Path(__file__).name


def scan_text_for_home_path(text: str) -> str | None:
    """Return the first machine-specific absolute home path in ``text``, else None."""
    m = _HOME_ABS.search(text)
    return m.group(0) if m else None


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=_ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def find_home_path_violations(files):
    """[(path, line_no, text), ...] for tracked files with a machine-specific path."""
    violations = []
    for rel in files:
        if rel.endswith(_SELF) or rel.endswith(".min.js"):
            continue
        if any(rel.startswith(d) for d in _SKIP_DIRS):
            continue
        if Path(rel).suffix in _SKIP_SUFFIXES:
            continue
        try:
            text = (_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if scan_text_for_home_path(line):
                violations.append((rel, i, line.strip()))
    return violations


def test_no_absolute_home_paths_in_tracked_files():
    violations = find_home_path_violations(_tracked_files())
    assert not violations, "machine-specific absolute paths found:\n" + \
        "\n".join(f"  {p}:{n}: {t}" for p, n, t in violations)


def test_shipped_config_infra_paths_are_relative():
    cfg = webui_config.load_raw(str(_ROOT / "configs" / "webui.yaml"))
    for field in webui_config._PATH_FIELDS:
        val = str(cfg[field])
        assert not val.startswith("/") and not val.startswith("~"), \
            f"configs/webui.yaml {field} must stay relative for portability, got {val!r}"


def test_guard_detects_injected_absolute_path():
    # Detection witness. Built by concatenation so this source file itself stays
    # free of a literal home path (and would pass the scan above).
    needle = "/" + "Users" + "/someone/project/cfg.yaml"
    assert scan_text_for_home_path(f'CONFIG = "{needle}"') == "/" + "Users" + "/someone/"


def test_guard_ignores_system_paths():
    assert scan_text_for_home_path("interp = /usr/local/bin/python3") is None
    assert scan_text_for_home_path("tmp = /private/var/folders/r1/xx/yy") is None

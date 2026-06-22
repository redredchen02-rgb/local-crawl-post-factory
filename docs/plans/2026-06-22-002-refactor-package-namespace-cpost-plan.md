---
date: 2026-06-22
type: refactor
status: completed
topic: package-namespace-cpost
---

# refactor: Namespace packages under `cpost/` (pip-installable maturity)

## Problem Frame

The distribution `local-crawl-post-factory` shipped four **generic top-level
packages** — `core/`, `src/`, `browser/`, `webui/`. On `pip install`, these
land directly in `site-packages`, colliding with any other package (or tool)
using those names. This is the one real blocker to the tool being installed
into a shared environment and reused cleanly — the original "成熟可複用工具"
goal (direction ③). Everything else needed for packaging (pyproject metadata,
dependency extras, 14 console_scripts) already existed.

Scope decision (confirmed with user): import root = **`cpost`**; distribution
intent = **internal/private reuse** (not public PyPI), so LICENSE is a short
proprietary notice rather than an OSS license.

## Scope Boundaries

- NOT renaming the distribution name (`local-crawl-post-factory` stays).
- NOT publishing to public PyPI; no OSS classifiers/authors metadata.
- NOT rewriting historical `docs/plans/*` or `CHANGELOG.md` (point-in-time
  records).
- NO behavior change — this is a pure move + import-rewrite + packaging-config.

## What Changed

- **Move:** `core/`→`cpost/core/`, `src/`→`cpost/cli/`, `browser/`→
  `cpost/browser/`, `webui/`→`cpost/webui/`; added `cpost/__init__.py`.
  (`src`→`cli` because `cpost.cli.<stage>` reads better than `cpost.src.*`.)
- **Imports:** 215 absolute import statements + 104 string module-target lines
  (`@patch("core.…")`, `monkeypatch.setattr("webui.…")`) rewritten to the
  `cpost.*` namespace via a one-shot precise rewriter. Zero relative imports
  existed, so no within-package churn. File-path literals (`"webui.yaml"`,
  `"src.txt"`) were protected by a file-extension negative-lookahead and left
  untouched.
- **Packaging:** `pyproject.toml` — entry points `src.*`/`webui.*` →
  `cpost.cli.*`/`cpost.webui.*`; `[tool.setuptools.packages.find] include =
  ["cpost*"]`; mypy `files = ["cpost"]` + override `module = "cpost.core.*"`;
  added `readme` + proprietary `license`.
- **Tooling:** CI `--cov=cpost`, Makefile `--cov=cpost` (×2) + htmx vendor path
  `cpost/webui/static/`, conftest comment, `configs/scoring.yaml` comments,
  README architecture refs.
- **LICENSE:** added (Proprietary — All rights reserved).

## Verification (all green locally)

- `ruff check .` — passed.
- `mypy` — 0 errors, 58 source files.
- `pytest` — 484 passed, 0 skipped (incl. multiprocessing-spawn crawl
  subprocess + 4 Playwright browser e2e — confirms `cpost.*` resolves in a
  fresh spawned interpreter and on PATH).
- `pip install -e .` regenerates console scripts → all 14 point to `cpost.*`;
  stale `select-cover`/`watermark-cover` entries dropped. `import cpost.*` OK.

## Post-Deploy Monitoring & Validation

- **After merge, anyone with an existing checkout must re-run
  `pip install -e .`** (or `make install`) once — the old editable install
  still maps the removed top-level `core`/`src`/`browser`/`webui`; the reinstall
  repoints to `cpost` and refreshes console scripts. Healthy signal: `python -c
  "import cpost"` succeeds and `which normalize-items` resolves.
- CI is the gate: the `test` job runs the full suite under `--cov=cpost` with
  the fail-closed browser-skip guard. Failure signal: ImportError / patch-target
  AttributeError on any `cpost.*` path → a missed rewrite; rollback = revert the
  squash commit (pure move, fully reversible).

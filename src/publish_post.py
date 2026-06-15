"""publish-post — Phase 5 CONTRACT STUB (origin §11.10, R8).

GATING IS THE POINT. Both gates are enforced BEFORE any not-implemented path:
  1. --approve must be present.
  2. manifest backend.status must be 'draft_verified'.
Only if both pass do we reach the deferred browser behavior (exit 3).
"""

import argparse
import sys

from core import cli
from core.errors import ValidationError, DependencyError
from browser.selector_recipe import load_backend
from src.draft_post import load_manifest, require_status


def _parse(argv):
    p = argparse.ArgumentParser(prog="publish-post")
    p.add_argument("--manifest", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--storage-state")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--timeout-ms", type=int)
    p.add_argument("--approve", action="store_true")
    return p.parse_args(argv)


def _run(args) -> int:
    manifest = load_manifest(args.manifest)
    load_backend(args.backend)

    # Gate 1: explicit approval required (R8).
    if not args.approve:
        raise ValidationError("refusing to publish without --approve")

    # Gate 2: draft must be verified (R8). Reuse the state machine but emit the
    # spec-mandated message.
    if manifest.get("backend", {}).get("status") != "draft_verified":
        raise ValidationError("refusing: draft not verified")
    require_status(manifest, "draft_verified")

    raise DependencyError("browser automation not implemented in this release")


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()

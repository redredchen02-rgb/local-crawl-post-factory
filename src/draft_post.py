"""draft-post — Phase 4 CONTRACT STUB (origin §11.8).

Parses flags, validates the manifest + backend.yaml selector schema so the
contract is exercised, but does NOT drive a browser. With --dry-run it
validates and exits 0; otherwise it raises a clear not-implemented signal.
"""

import argparse
import json
import sys

from core import cli
from core.errors import ValidationError, DependencyError
from browser.selector_recipe import load_backend

# Manifest state machine (origin §6).
ALLOWED_STATES = (
    "package_built",
    "draft_created",
    "draft_verified",
    "published",
)


def require_status(manifest: dict, expected) -> None:
    """Raise ValidationError unless manifest backend.status is in ``expected``."""
    if isinstance(expected, str):
        expected = (expected,)
    status = manifest.get("backend", {}).get("status")
    if status not in ALLOWED_STATES:
        raise ValidationError(f"unknown manifest status: {status!r}")
    if status not in expected:
        raise ValidationError(
            f"refusing: manifest status {status!r} not in {list(expected)}"
        )


def load_manifest(path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ValidationError(f"manifest not found: {path}")
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid manifest json: {exc}")
    if not isinstance(data, dict):
        raise ValidationError("manifest must be a JSON object")
    return data


def _parse(argv):
    p = argparse.ArgumentParser(prog="draft-post")
    p.add_argument("--manifest", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--storage-state")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--timeout-ms", type=int)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _run(args) -> int:
    manifest = load_manifest(args.manifest)
    load_backend(args.backend)
    require_status(manifest, "package_built")

    if args.dry_run:
        # origin §17.8: dry-run validates without submitting.
        post_id = manifest.get("post_id")
        json.dump({"status": "validated", "post_id": post_id}, sys.stdout)
        sys.stdout.write("\n")
        return 0

    raise DependencyError("browser automation not implemented in this release")


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()

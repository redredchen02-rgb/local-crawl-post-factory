"""verify-draft — Phase 4 CONTRACT STUB (origin §11.9).

Validates manifest + backend.yaml, then signals not-implemented (exit 3).
"""

import argparse
import sys

from core import cli
from core.errors import DependencyError
from browser.selector_recipe import load_backend
from src.draft_post import load_manifest


def _parse(argv):
    p = argparse.ArgumentParser(prog="verify-draft")
    p.add_argument("--manifest", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--storage-state")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--timeout-ms", type=int)
    return p.parse_args(argv)


def _run(args) -> int:
    load_manifest(args.manifest)
    load_backend(args.backend)
    raise DependencyError("browser automation not implemented in this release")


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()

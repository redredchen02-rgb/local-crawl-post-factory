"""CLI error-contract tests for ``draft-post`` (LANE L4).

Pins the I/O contract (origin §2.3 / §10) for the draft-post command without
launching a real browser: the backend ``session``/``create_draft`` collaborators
are mocked. We assert exit codes and the stdout/stderr split for the happy path,
input-validation failures (exit 2), and the external-service failure (exit 4).

Two invocation styles mirror the existing suite:
  * ``cli.run(lambda: draft_post._run(args))`` drives the handler under the
    contract runner so a raised ``CliError`` maps to its exit code in-process
    (see tests/test_publish_gating.py).
  * ``draft_post.main(argv)`` exercises the full ``_parse`` + wrapper path so the
    argparse missing-required-arg behaviour (SystemExit(2)) is covered too.
"""

import contextlib
import json

import pytest

from cpost.core import cli
from cpost.core.errors import ExternalError
from cpost.cli import draft_post

BACKEND = "configs/backend.yaml"


def _manifest(tmp_path, status, **content):
    """Write a manifest with the given backend status + optional content fields."""
    p = tmp_path / "manifest.json"
    body = {"post_id": "20260615_demo", "backend": {"status": status}}
    if content:
        body["content"] = content
    p.write_text(json.dumps(body), encoding="utf-8")
    return str(p)


def _ns(**kw):
    base = dict(
        manifest=None,
        backend=BACKEND,
        storage_state=None,
        headless=True,
        timeout_ms=5000,
        retries=None,
        dry_run=False,
    )
    base.update(kw)
    return type("NS", (), base)()


@contextlib.contextmanager
def _fake_session(*_a, **_kw):
    yield object()  # dummy page; create_draft is patched separately


# --- happy path: valid inputs -> exit 0 + JSON on stdout, stderr empty -------

def test_happy_dry_run_validated_exits_0(tmp_path, capsys):
    """--dry-run validates manifest + backend config, never touches a browser."""
    args = _ns(manifest=_manifest(tmp_path, "package_built"), dry_run=True)
    code = cli.run(lambda: draft_post._run(args))
    cap = capsys.readouterr()
    assert code == 0
    payload = json.loads(cap.out)
    assert payload == {"status": "validated", "post_id": "20260615_demo"}
    assert cap.err == ""


def test_happy_drafted_exits_0_with_draft_url(tmp_path, capsys):
    """A successful (mocked) draft drive emits status=drafted + draft_url, exit 0."""
    args = _ns(manifest=_manifest(tmp_path, "package_built", title="T", body="b"))
    with patch_driver(create_draft={"draft_url": "https://admin/posts/9/edit"}):
        code = cli.run(lambda: draft_post._run(args))
    cap = capsys.readouterr()
    assert code == 0
    payload = json.loads(cap.out)
    assert payload["status"] == "drafted"
    assert payload["post_id"] == "20260615_demo"
    assert payload["draft_url"] == "https://admin/posts/9/edit"
    assert cap.err == ""
    # The manifest was advanced to 'drafted' (side effect of the happy path).
    m = json.loads(open(args.manifest, encoding="utf-8").read())
    assert m["backend"]["status"] == "drafted"


# --- error: missing/invalid required input -> exit 2, stderr, empty stdout ---

def test_missing_manifest_arg_exits_2(capsys):
    """argparse: a missing required --manifest exits 2, usage on stderr, no stdout."""
    with pytest.raises(SystemExit) as exc:
        draft_post.main(["--backend", BACKEND])
    assert exc.value.code == 2
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err != ""


def test_manifest_not_found_exits_2(tmp_path, capsys):
    """A nonexistent manifest path is a ValidationError -> exit 2, stderr, no stdout."""
    args = _ns(manifest=str(tmp_path / "nope.json"))
    code = cli.run(lambda: draft_post._run(args))
    cap = capsys.readouterr()
    assert code == 2
    assert cap.out == ""
    assert cap.err.strip() != ""


def test_wrong_status_exits_2(tmp_path, capsys):
    """draft-post requires status 'package_built'; a 'drafted' manifest -> exit 2."""
    args = _ns(manifest=_manifest(tmp_path, "drafted"))
    code = cli.run(lambda: draft_post._run(args))
    cap = capsys.readouterr()
    assert code == 2
    assert cap.out == ""
    assert cap.err.strip() != ""


def test_driver_validation_error_maps_exit_2(tmp_path, capsys):
    """A ValidationError raised from the driver (e.g. create_draft's up-front
    content.title check, U15(2)) maps through the contract runner to exit 2 with
    no stdout. We inject it via the mocked create_draft because the real check
    lives inside backend_driver.create_draft, which is mocked out here."""
    from cpost.core.errors import ValidationError

    args = _ns(manifest=_manifest(tmp_path, "package_built"))  # no content
    boom = ValidationError("manifest missing required field: content.title")
    with patch_driver(create_draft=boom):
        code = cli.run(lambda: draft_post._run(args))
    cap = capsys.readouterr()
    assert code == 2
    assert cap.out == ""
    assert cap.err.strip() != ""
    # The manifest was NOT advanced past 'package_built' on the validation failure.
    m = json.loads(open(args.manifest, encoding="utf-8").read())
    assert m["backend"]["status"] == "package_built"


# --- error: external/browser failure -> exit 4 (ExternalError) ---------------

def test_external_failure_exits_4(tmp_path, capsys):
    """A browser-level failure surfaced as ExternalError maps to exit 4, no stdout."""
    args = _ns(manifest=_manifest(tmp_path, "package_built", title="T", body="b"))
    boom = ExternalError("draft did not confirm after 1 attempt(s): timeout")
    with patch_driver(create_draft=boom):
        code = cli.run(lambda: draft_post._run(args))
    cap = capsys.readouterr()
    assert code == 4
    assert cap.out == ""
    assert cap.err.strip() != ""
    # The manifest was NOT advanced to 'drafted' on the failure path.
    m = json.loads(open(args.manifest, encoding="utf-8").read())
    assert m["backend"]["status"] == "package_built"


# --- helpers -----------------------------------------------------------------

@contextlib.contextmanager
def patch_driver(*, create_draft):
    """Patch backend_driver.session (no real browser) + create_draft.

    ``create_draft`` is either a return value (dict) or an exception to raise.
    audit.record is also patched so the happy path does not write a log file.
    """
    from unittest.mock import patch

    if isinstance(create_draft, BaseException):
        cd_kw = {"side_effect": create_draft}
    else:
        cd_kw = {"return_value": create_draft}
    with patch.object(draft_post.backend_driver, "session", _fake_session), \
         patch.object(draft_post.backend_driver, "create_draft", **cd_kw), \
         patch.object(draft_post.audit, "record"):
        yield

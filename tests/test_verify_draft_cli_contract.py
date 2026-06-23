"""CLI error-contract tests for ``verify-draft`` (LANE L4).

Pins the I/O contract (origin §2.3 / §10) for verify-draft without launching a
real browser: ``session``/``verify_draft`` are mocked. Covers the happy path
(exit 0 + JSON), input-validation failures (exit 2), and the external-service
failure path (exit 4), mirroring tests/test_publish_gating.py's style.
"""

import contextlib
import json

import pytest

from cpost.core import cli
from cpost.core.errors import ExternalError, SessionExpiredError
from cpost.cli import verify_draft

BACKEND = "configs/backend.yaml"


def _manifest(tmp_path, status, title="T"):
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps({
            "post_id": "20260615_demo",
            "content": {"title": title, "body": "b"},
            "backend": {"status": status, "draft_url": "https://admin/posts/9/edit"},
        }),
        encoding="utf-8",
    )
    return str(p)


def _ns(**kw):
    base = dict(
        manifest=None,
        backend=BACKEND,
        storage_state=None,
        headless=True,
        timeout_ms=5000,
        retries=None,
    )
    base.update(kw)
    return type("NS", (), base)()


@contextlib.contextmanager
def _fake_session(*_a, **_kw):
    yield object()  # dummy page; verify_draft is patched separately


@contextlib.contextmanager
def patch_driver(*, verify):
    """Patch backend_driver.session (no real browser) + verify_draft + audit.

    ``verify`` is either a return value or an exception to raise.
    """
    from unittest.mock import patch

    if isinstance(verify, BaseException):
        v_kw = {"side_effect": verify}
    else:
        v_kw = {"return_value": verify}
    with patch.object(verify_draft.backend_driver, "session", _fake_session), \
         patch.object(verify_draft.backend_driver, "verify_draft", **v_kw), \
         patch.object(verify_draft.audit, "record"):
        yield


# --- happy path: valid inputs -> exit 0 + JSON on stdout, stderr empty -------

def test_happy_verified_exits_0(tmp_path, capsys):
    """A 'drafted' manifest + a found draft -> status=draft_verified, exit 0."""
    args = _ns(manifest=_manifest(tmp_path, "drafted"))
    with patch_driver(verify=True):
        code = cli.run(lambda: verify_draft._run(args))
    cap = capsys.readouterr()
    assert code == 0
    payload = json.loads(cap.out)
    assert payload == {"status": "draft_verified", "post_id": "20260615_demo"}
    assert cap.err == ""
    m = json.loads(open(args.manifest, encoding="utf-8").read())
    assert m["backend"]["status"] == "draft_verified"


# --- error: missing/invalid required input -> exit 2, stderr, empty stdout ---

def test_missing_manifest_arg_exits_2(capsys):
    """argparse: a missing required --manifest exits 2, usage on stderr, no stdout."""
    with pytest.raises(SystemExit) as exc:
        verify_draft.main(["--backend", BACKEND])
    assert exc.value.code == 2
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err != ""


def test_manifest_not_found_exits_2(tmp_path, capsys):
    args = _ns(manifest=str(tmp_path / "nope.json"))
    code = cli.run(lambda: verify_draft._run(args))
    cap = capsys.readouterr()
    assert code == 2
    assert cap.out == ""
    assert cap.err.strip() != ""


def test_wrong_status_exits_2(tmp_path, capsys):
    """verify-draft requires status 'drafted'; a 'package_built' manifest -> exit 2."""
    args = _ns(manifest=_manifest(tmp_path, "package_built"))
    code = cli.run(lambda: verify_draft._run(args))
    cap = capsys.readouterr()
    assert code == 2
    assert cap.out == ""
    assert cap.err.strip() != ""


# --- error: external/browser failure -> exit 4 (ExternalError) ---------------

def test_external_failure_exits_4(tmp_path, capsys):
    """verify_draft raising ExternalError (draft not confirmed) maps to exit 4."""
    args = _ns(manifest=_manifest(tmp_path, "drafted"))
    boom = ExternalError("verify did not confirm after 1 attempt(s): timeout")
    with patch_driver(verify=boom):
        code = cli.run(lambda: verify_draft._run(args))
    cap = capsys.readouterr()
    assert code == 4
    assert cap.out == ""
    assert cap.err.strip() != ""
    # Not advanced to draft_verified on failure.
    m = json.loads(open(args.manifest, encoding="utf-8").read())
    assert m["backend"]["status"] == "drafted"


def test_session_expired_exits_4(tmp_path, capsys):
    """An expired login session is a (distinguishable) external failure -> exit 4."""
    args = _ns(manifest=_manifest(tmp_path, "drafted"))
    boom = SessionExpiredError("login session expired (redirected to login)")
    with patch_driver(verify=boom):
        code = cli.run(lambda: verify_draft._run(args))
    cap = capsys.readouterr()
    assert code == 4
    assert cap.out == ""
    assert cap.err.strip() != ""

import json

from cpost.core import cli
from cpost.cli import draft_post, publish_post

BACKEND = "configs/backend.yaml"


def _manifest(tmp_path, status):
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps({"post_id": "20260615_demo", "backend": {"status": status}}),
        encoding="utf-8",
    )
    return str(p)


def _ns(**kw):
    base = dict(
        manifest=None,
        backend=BACKEND,
        storage_state=None,
        headless=False,
        timeout_ms=5000,
        approve=False,
        dry_run=False,
        state=None,
    )
    base.update(kw)
    return type("NS", (), base)()


def test_publish_without_approve_exits_2(tmp_path):
    args = _ns(manifest=_manifest(tmp_path, "draft_verified"), approve=False)
    assert cli.run(lambda: publish_post._run(args)) == 2


def test_publish_approve_but_wrong_status_exits_2(tmp_path):
    args = _ns(manifest=_manifest(tmp_path, "package_built"), approve=True)
    assert cli.run(lambda: publish_post._run(args)) == 2


def test_draft_dry_run_validated_exits_0(tmp_path, capsys):
    args = _ns(manifest=_manifest(tmp_path, "package_built"), dry_run=True)
    code = cli.run(lambda: draft_post._run(args))
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out)["status"] == "validated"
    assert json.loads(out)["post_id"] == "20260615_demo"


# --- U3 (R2): idempotent publish re-entry (no false-failure / masked error) ---

def _published_manifest(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({
        "post_id": "20260615_demo",
        "backend": {"status": "published", "published_url": "https://site/p/1"},
        "source": {"canonical_url": "https://src/a"},
        "content": {"title": "T", "body": "B"},
    }), encoding="utf-8")
    return str(p)


def _explode(*a, **k):
    raise AssertionError("browser session must not open on idempotent publish re-entry")


def test_publish_reentry_already_published_converges_to_success(tmp_path, capsys, monkeypatch):
    # A prior attempt published LIVE + saved status='published', then crashed in the
    # bookkeeping tail; the orchestrator's _retry re-invokes _run. It must converge to
    # SUCCESS, not fail Gate 2 ('draft not verified') and report a live post as failed
    # (the pre-fix behaviour was exit 2 with the gate error masking the real one).
    monkeypatch.setattr(publish_post.backend_driver, "session", _explode)
    monkeypatch.setattr(publish_post.audit, "record", lambda *a, **k: None)
    args = _ns(manifest=_published_manifest(tmp_path), approve=True,
               state=str(tmp_path / "state.sqlite"))
    code = cli.run(lambda: publish_post._run(args))
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["status"] == "published"
    assert out["published_url"] == "https://site/p/1"
    # idempotent bookkeeping (re-)completed without re-opening the browser
    assert (tmp_path / "publish_receipt.json").exists()


def test_publish_reentry_still_requires_approve(tmp_path):
    # Re-entry must not bypass Gate 1: an already-published manifest without --approve
    # still refuses (exit 2).
    args = _ns(manifest=_published_manifest(tmp_path), approve=False)
    assert cli.run(lambda: publish_post._run(args)) == 2

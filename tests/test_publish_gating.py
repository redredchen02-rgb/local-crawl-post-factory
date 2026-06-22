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

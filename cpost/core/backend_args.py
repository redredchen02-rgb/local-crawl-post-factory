"""Single typed contract for invoking a backend stage command (R14).

The CLI stage entry points (``draft_post.run`` / ``verify_draft.run`` /
``publish_post.run``) historically took an argparse-style namespace and read it
by attribute access (``args.manifest``, ``args.dry_run``, ``args.approve`` …).
That namespace was rebuilt as a ``types.SimpleNamespace`` in ~4 places with
slowly drifting field sets (and a hardcoded ``timeout_ms=30_000`` in one spot vs
the named constant elsewhere).

:class:`BackendInvocation` is the one place that shape lives now. It is a plain
dataclass, so it still satisfies the attribute-access protocol the runners
expect, while giving the field set a typed, single-source-of-truth definition.

``timeout_ms`` defaults to ``cpost.browser.backend_driver.DEFAULT_TIMEOUT_MS`` (the
canonical value) via a lazy default factory — the import is deferred so this
``core`` module carries no module-load-time dependency on ``browser`` (mirroring
``webui/_helpers.py``'s "import here to avoid adding browser dep at module
level"). Fields the draft/verify path does not use (``approve``,
``expected_content_id``) have inert defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Fallback for browser-less callers. MUST stay == cpost.browser.backend_driver.DEFAULT_TIMEOUT_MS.
CORE_DEFAULT_TIMEOUT_MS = 30000


def _default_timeout_ms() -> int:
    try:
        from cpost.browser import backend_driver  # lazy: keep core free of a module-level browser dep
    except ImportError:
        return CORE_DEFAULT_TIMEOUT_MS  # degrade gracefully when browser is not installed
    return backend_driver.DEFAULT_TIMEOUT_MS


@dataclass
class BackendInvocation:
    """Args for one backend stage command (draft / verify / publish).

    Field names match the attributes the stage runners read, so an instance is a
    drop-in replacement for the old ``SimpleNamespace`` (attribute access only —
    the runners never index it like a dict).
    """

    manifest: str
    backend: str
    storage_state: str
    state: str
    headless: bool = True
    timeout_ms: int = field(default_factory=_default_timeout_ms)
    retries: int | None = None
    dry_run: bool = False
    # Publish-only gates; harmless inert defaults for the draft/verify path.
    approve: bool = False
    expected_content_id: str | None = None

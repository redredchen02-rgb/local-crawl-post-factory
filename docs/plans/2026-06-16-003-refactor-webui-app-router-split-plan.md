---
date: 2026-06-16
topic: refactor-webui-app-router-split
status: active
---

# refactor: Split webui/app.py into APIRouter modules (Phase 2 — P4)

## Problem Frame

`webui/app.py` is 734 lines: imports, module-level helpers, `create_app()` with 22 route handlers
nested inside it, plus ~150 lines of pure I/O helpers below. Every route handler, internal helper,
and background-job callback lives in a single file, making navigation and isolated testing hard.

Phase 2 requirement P4: split into focused `APIRouter` modules so each route group is ≤150 lines.

## Scope Boundaries

- **In scope**: Move route handlers into `APIRouter` modules; extract pure helpers into a shared
  module; slim `create_app()` to wiring only.
- **Out of scope**: P5 (TTL cache for history/audit), P6 (mypy type stubs), P7-P9 (Phase 3).
- **No behavior change**: zero logic modifications — pure structural refactor.
- **Tests unchanged**: existing test suite must pass green with no edits.

## Key Decisions

- **`_cfg()` → `cfg_from_request(request)`**: The closure `_cfg()` reads
  `app.state.config_path`. In routers, use `request.app.state.config_path` instead. A thin
  helper `cfg_from_request(request)` in `webui/routers/_ctx.py` wraps this.
- **Pure helpers → `webui/_helpers.py`**: `_safe_pkg_dir`, `_scan_packages`, `_filter_packages`,
  `_read_failure`, `_tail_audit`, `_move_to_trash`, `_scan_trash`, `_restore_from_trash` have no
  FastAPI or `app.state` dependency. Moving them to `_helpers.py` avoids circular imports when
  routers need them.
- **Request-aware helpers → `webui/routers/_ctx.py`**: `submit_job`, `submit_action`,
  `note_session_expiry`, `auth_light` depend on `app.state` via `request.app.state`. They live
  in a shared router context module.
- **`_run_auto_pipeline`, `_action_ns`, `_retry` → `webui/_auto_pipeline.py`**: Used only by the
  crawl route; these have no direct `app.state` dependency (they accept `cfg` dict). Extracting
  avoids bloating the crawl router with 180 lines.
- **`templates` stays module-level in `app.py`**: imported by `_ctx.py` to avoid re-initializing.
  Alternatively, `_ctx.py` initialises its own `Jinja2Templates` pointing to the same directory.
  Decision: `_ctx.py` holds `templates` (single source of truth, no cross-module state from app).
- **`check_publish_gates` stays in `app.py`**: already module-level, pure function, tested
  directly. Stays as public API.
- **Router prefix**: no prefix on individual routers (routes are already namespaced by path).
- **`_note_session_expiry` signature**: `note_session_expiry(request: Request, cfg: dict)` —
  accesses `request.app.state.session_expired_mtime` directly.

## Files

### New files
| File | Contents |
|---|---|
| `webui/_helpers.py` | Pure I/O helpers: `_safe_pkg_dir`, `_scan_packages`, `_filter_packages`, `_read_failure`, `_tail_audit`, `_move_to_trash`, `_scan_trash`, `_restore_from_trash` |
| `webui/_auto_pipeline.py` | `_action_ns`, `_retry`, `_run_auto_pipeline` |
| `webui/routers/__init__.py` | empty |
| `webui/routers/_ctx.py` | `cfg_from_request`, `templates`, `submit_job`, `submit_action`, `note_session_expiry`, `auth_light` |
| `webui/routers/settings_auth.py` | `GET /`, `GET+POST /settings`, `GET /auth-status` |
| `webui/routers/crawl.py` | `POST /crawl`, `GET /jobs/{job_id}` |
| `webui/routers/packages.py` | `GET /packages`, `GET /packages/{post_id}`, `GET /packages/{post_id}/cover`, `GET /packages/{post_id}/failure-image`, `POST /packages/{post_id}/delete` |
| `webui/routers/actions.py` | `POST /packages/{post_id}/draft`, `POST /packages/{post_id}/verify`, `POST /packages/{post_id}/publish`, `POST /batch/delete`, `POST /batch/{stage}` |
| `webui/routers/trash.py` | `GET /trash`, `POST /trash/{post_id}/restore`, `POST /trash/empty` |
| `webui/routers/history_audit.py` | `GET /history`, `GET /audit` |

### Modified files
| File | Change |
|---|---|
| `webui/app.py` | Remove all 22 route handlers + nested helpers; keep `create_app()` as wiring only; keep `check_publish_gates`, `run()`, `app = create_app()` |

## Implementation Units

- [ ] **U1 — Extract pure helpers to `webui/_helpers.py`**
  Move `_safe_pkg_dir`, `_scan_packages`, `_filter_packages`, `_read_failure`, `_tail_audit`,
  `_move_to_trash`, `_scan_trash`, `_restore_from_trash` verbatim. Add `__all__`.
  Update `webui/app.py` imports (temporarily import from `_helpers` to keep `app.py` stable while
  routers are not yet wired). Run tests green.

- [ ] **U2 — Extract auto-pipeline helpers to `webui/_auto_pipeline.py`**
  Move `_action_ns`, `_retry`, `_run_auto_pipeline` verbatim. `_run_auto_pipeline` calls
  `note_session_expiry` — pass it as a callable parameter:
  `_run_auto_pipeline(job, cfg, built, *, note_expiry=None)`.
  Update `webui/app.py` to import from `_auto_pipeline`. Tests green.

- [ ] **U3 — Create `webui/routers/__init__.py` + `webui/routers/_ctx.py`**
  `_ctx.py` contains:
  - `_HERE = Path(__file__).parent.parent`
  - `templates = Jinja2Templates(directory=str(_HERE / "templates"))`
  - `def cfg_from_request(request: Request) -> dict`
  - `def note_session_expiry(request: Request, cfg: dict) -> None`
  - `def auth_light(request: Request, cfg: dict) -> dict`
  - `def submit_job(request, stage, post_id, cfg, call) -> HTMLResponse`
  - `def submit_action(request, stage, post_id, prepared) -> HTMLResponse`
  No route handlers here. `auth_light` accesses `request.app.state.session_expired_mtime`.

- [ ] **U4 — Create `webui/routers/settings_auth.py`**
  `router = APIRouter()`. Move `settings_page`, `save_settings`, `auth_status` handlers.
  Replace `_cfg()` calls with `cfg_from_request(request)`. Replace `templates` with import from
  `_ctx`. Remove `_auth_light` from app; use `_ctx.auth_light`.

- [ ] **U5 — Create `webui/routers/crawl.py`**
  Move `start_crawl` and `job_status`. Replace `_cfg()`, `templates`, `_run_auto_pipeline`
  with imports. `start_crawl` passes `note_session_expiry` as callback to `_run_auto_pipeline`.

- [ ] **U6 — Create `webui/routers/packages.py`**
  Move `packages`, `package_detail`, `package_cover`, `package_failure_image`, `delete_package`.
  Import from `_helpers` and `_ctx`.

- [ ] **U7 — Create `webui/routers/actions.py`**
  Move `action_draft`, `action_verify`, `action_publish`, `batch_delete`, `batch_action`.
  Import from `_helpers`, `_ctx`, `_auto_pipeline`.

- [ ] **U8 — Create `webui/routers/trash.py`**
  Move `trash_list`, `restore_package`, `empty_trash`. Import from `_helpers` and `_ctx`.

- [ ] **U9 — Create `webui/routers/history_audit.py`**
  Move `history` and `audit`. Import from `_helpers` and `_ctx`.

- [ ] **U10 — Wire routers into `create_app()` and slim `webui/app.py`**
  `create_app()` reduces to:
  ```
  app = FastAPI(...)
  app.mount("/static", ...)
  app.state.config_path = config_path
  app.state.session_expired_mtime = None
  app.include_router(settings_auth.router)
  app.include_router(crawl.router)
  app.include_router(packages.router)
  app.include_router(actions.router)
  app.include_router(trash.router)
  app.include_router(history_audit.router)
  return app
  ```
  Remove all now-moved functions. Keep `check_publish_gates`, `run()`, `app = create_app()`.
  Verify `webui/app.py` < 80 lines.

- [ ] **U11 — Full test run + smoke-check**
  `pytest` must pass green. Verify `webui/app.py` line count with `wc -l`.

## Test Scenarios

These cover the refactor's failure modes (no behavior change means existing tests cover behavior):

- **T1** `webui/app.py` line count < 100 after U10 (assert with `wc -l`).
- **T2** All existing `pytest` tests pass green after each unit (run after U1, U2, U10 at minimum).
- **T3** No circular import: `python -c "from webui.app import create_app"` exits 0.
- **T4** `check_publish_gates` still importable from `webui.app` (used in tests directly).
- **T5** Route count preserved: `grep -r "@router\." webui/routers/ | wc -l` equals original
  `grep "@app\." webui/app.py | wc -l` (22 routes).

## Existing Patterns to Follow

- FastAPI `APIRouter` included via `app.include_router(router)` — no prefix, tags optional.
- `request.app.state` for reading app-level state in routers (standard FastAPI pattern).
- No `APIRouter(prefix=...)` — existing URLs must not change.

## Dependencies / Sequencing

U1 and U2 are independent; run in parallel. U3 depends on neither. U4–U9 depend on U1, U2, U3.
U10 depends on U4–U9. U11 is final.

Recommended sequence: U1+U2 in parallel → U3 → U4–U9 together → U10 → U11.

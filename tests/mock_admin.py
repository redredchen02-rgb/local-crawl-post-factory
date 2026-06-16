"""A tiny stateful mock admin backend for Phase 4-5 browser tests.

Field names and button labels mirror configs/backend.yaml so the real driver
(which sources every selector from backend.yaml) can drive it unchanged.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def _parse_multipart_fields(content_type, body):
    """Extract text form fields from a multipart/form-data body.

    Minimal parser for the mock: returns {name: text_value} for non-file parts
    (file parts are ignored — the mock only needs title/content).
    """
    if "boundary=" not in content_type:
        return {}
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    delim = ("--" + boundary).encode()
    fields = {}
    for part in body.split(delim):
        if not part or part in (b"--\r\n", b"--"):
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        if not value:
            continue
        head_text = head.decode("utf-8", "replace")
        if 'filename="' in head_text:  # skip file uploads
            continue
        name = None
        for token in head_text.split(";"):
            token = token.strip()
            if token.startswith('name="'):
                name = token[len('name="'):].rstrip('"')
        if name is not None:
            fields[name] = value.rsplit(b"\r\n", 1)[0].decode("utf-8", "replace")
    return fields

_POSTS: dict[str, dict] = {}  # id -> {"title", "content", "published"}
_NEXT = {"id": 1}

_CREATE_FORM = """
<html><body>
<form method="POST" action="/admin/posts/create" enctype="multipart/form-data">
  <input name="title" type="text">
  <textarea name="content"></textarea>
  <input name="cover" type="file">
  <select name="category"><option value="">-</option><option value="news">news</option></select>
  <input name="tags" type="text">
  <button type="submit">儲存草稿</button>
</form>
</body></html>
"""


def _edit_page(pid):
    post = _POSTS[pid]
    banner = "發布成功" if post["published"] else "草稿已儲存"
    return f"""
<html><body>
<p>{banner}</p>
<h1>{post['title']}</h1>
<form method="POST" action="/admin/posts/{pid}/publish">
  <button type="submit">發布</button>
</form>
</body></html>
"""


def _search_page(keyword):
    rows = "".join(
        f"<tr><td>{p['title']}</td></tr>"
        for p in _POSTS.values()
        if keyword and keyword in p["title"]
    )
    return f"""
<html><body>
<form method="GET" action="/admin/posts">
  <input name="keyword" type="text" value="{keyword or ''}">
  <button type="submit">搜尋</button>
</form>
<table>{rows}</table>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/admin/posts/create":
            self._send(_CREATE_FORM)
        elif parsed.path == "/admin/posts":
            kw = parse_qs(parsed.query).get("keyword", [""])[0]
            self._send(_search_page(kw))
        elif parsed.path.endswith("/edit"):
            pid = int(parsed.path.split("/")[3])
            self._send(_edit_page(pid))
        else:
            self._send("not found", 404)

    def do_POST(self):
        if self.path == "/admin/posts/create":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            fields = _parse_multipart_fields(self.headers.get("Content-Type", ""), body)
            pid = _NEXT["id"]
            _NEXT["id"] += 1
            _POSTS[pid] = {
                "title": fields.get("title", ""),
                "content": fields.get("content", ""),
                "published": False,
            }
            self.send_response(302)
            self.send_header("Location", f"/admin/posts/{pid}/edit")
            self.end_headers()
        elif self.path.endswith("/publish"):
            pid = int(self.path.split("/")[3])
            _POSTS[pid]["published"] = True
            self.send_response(302)
            self.send_header("Location", f"/admin/posts/{pid}/edit")
            self.end_headers()
        else:
            self._send("not found", 404)


class MockAdmin:
    def __init__(self):
        _POSTS.clear()
        _NEXT["id"] = 1
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def backend_cfg(self) -> dict:
        return {
            "create_url": f"{self.base}/admin/posts/create",
            "selectors": {
                "title": 'input[name="title"]',
                "body": 'textarea[name="content"]',
                "cover": 'input[type="file"][name="cover"]',
                "category": 'select[name="category"]',
                "tags": 'input[name="tags"]',
                "save_draft": 'button:has-text("儲存草稿")',
                "publish": 'button:has-text("發布")',
            },
            "verify": {
                "draft_success_text": "草稿已儲存",
                "publish_success_text": "發布成功",
                "after_draft_url_contains": "/admin/posts",
                "after_publish_url_contains": "/admin/posts",
                "search_url": f"{self.base}/admin/posts",
                "search_input": 'input[name="keyword"]',
                "search_button": 'button:has-text("搜尋")',
                "result_title": "table >> text={title}",
            },
        }

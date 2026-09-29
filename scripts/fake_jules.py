#!/usr/bin/env python3
"""
Jules giả cho test (#43): HTTP server theo schema Jules API v1alpha đã xác minh (xem backend/jules_worker.py).
App trỏ tới qua GW_JULES_BASE_URL=http://127.0.0.1:<cổng>/v1alpha. Không gọi mạng thật.

Luồng phiên: POST /sessions → PLANNING → (GET lần đầu) AWAITING_PLAN_APPROVAL + activity planGenerated
→ POST :approvePlan → IN_PROGRESS → (GET tiếp) COMPLETED + outputs[].pullRequest.url.
Điều khiển: FAKE.fail["GET /sources"] = 401 (trả lỗi cho request khớp "METHOD /đường-dẫn-không-query");
FAKE.expected_key: key đúng (khác → 401). Chạy độc lập: python3 scripts/fake_jules.py --port 18898
(POST /__control {"fail": {...}, "expected_key": "..."} để đổi hành vi).
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

OWNER = "genesis-ryan-84-0567536339"
SOURCES = [
    {"name": f"sources/github/{OWNER}/gen-workplace", "id": f"github/{OWNER}/gen-workplace",
     "githubRepo": {"owner": OWNER, "repo": "gen-workplace", "isPrivate": True, "defaultBranch": {"displayName": "main"}}},
    {"name": f"sources/github/{OWNER}/other-repo", "id": f"github/{OWNER}/other-repo",
     "githubRepo": {"owner": OWNER, "repo": "other-repo", "isPrivate": False, "defaultBranch": {"displayName": "main"}}},
]


class FakeState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.sessions = {}
        self.requests = []      # [{method, path, key, body}]
        self.fail = {}          # "METHOD /path" → HTTP code
        self.expected_key = ""  # rỗng = nhận mọi key không rỗng
        self.counter = 0


FAKE = FakeState()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        path = self.path
        if path == "/__control" and method == "POST":
            with FAKE.lock:
                if (body or {}).get("reset"):
                    FAKE.reset()
                if "fail" in (body or {}):
                    FAKE.fail = dict(body["fail"] or {})
                if "expected_key" in (body or {}):
                    FAKE.expected_key = body["expected_key"] or ""
            return self._send(200, {"ok": True})
        if path == "/__requests" and method == "GET":
            with FAKE.lock:
                return self._send(200, {"requests": [{k: v for k, v in r.items() if k != "key"} for r in FAKE.requests]})
        key = self.headers.get("X-Goog-Api-Key") or ""
        bare = path.split("?")[0]
        with FAKE.lock:
            FAKE.requests.append({"method": method, "path": path, "key": key, "body": body})
            if not bare.startswith("/v1alpha/"):
                return self._send(404, {"error": {"code": 404, "message": "not found"}})
            p = bare[len("/v1alpha"):]
            forced = FAKE.fail.get(f"{method} {p}") or FAKE.fail.get(f"{method} *")
            if forced:
                msg = {401: "API key not valid", 403: "Permission denied", 429: "Resource exhausted"}.get(forced, "error")
                return self._send(forced, {"error": {"code": forced, "message": msg}})
            if not key or (FAKE.expected_key and key != FAKE.expected_key):
                return self._send(401, {"error": {"code": 401, "message": "API key not valid. Please pass a valid API key."}})
            if method == "GET" and p == "/sources":
                return self._send(200, {"sources": SOURCES})
            if method == "POST" and p == "/sessions":
                b = body or {}
                FAKE.counter += 1
                sid = f"{1000 + FAKE.counter}"
                s = {"name": f"sessions/{sid}", "id": sid, "title": b.get("title", ""), "prompt": b.get("prompt", ""),
                     "sourceContext": b.get("sourceContext"), "requirePlanApproval": b.get("requirePlanApproval"),
                     "automationMode": b.get("automationMode"), "state": "PLANNING",
                     "url": f"https://jules.google.com/session/{sid}", "outputs": [], "_acts": []}
                FAKE.sessions[sid] = s
                return self._send(200, _pub(s))
            m = re.match(r"^/sessions/([^/:]+)(/activities|:approvePlan|:sendMessage)?$", p)
            if not m or m.group(1) not in FAKE.sessions:
                return self._send(404, {"error": {"code": 404, "message": "Requested entity was not found."}})
            s = FAKE.sessions[m.group(1)]
            op = m.group(2) or ""
            if method == "GET" and op == "":
                _advance(s)
                return self._send(200, _pub(s))
            if method == "GET" and op == "/activities":
                return self._send(200, {"activities": s["_acts"]})
            if method == "POST" and op == ":approvePlan":
                if s["state"] != "AWAITING_PLAN_APPROVAL":
                    return self._send(400, {"error": {"code": 400, "message": "Session is not awaiting plan approval"}})
                s["state"] = "IN_PROGRESS"
                s["_acts"].append({"name": f"sessions/{s['id']}/activities/a{len(s['_acts'])}", "originator": "user",
                                   "planApproved": {"planId": f"plan-{s['id']}"}})
                return self._send(200, {})
            if method == "DELETE" and op == "":
                del FAKE.sessions[s["id"]]
                return self._send(200, {})
            return self._send(404, {"error": {"code": 404, "message": "not found"}})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")


def _pub(s):
    return {k: v for k, v in s.items() if not k.startswith("_")}


def _advance(s):
    """PLANNING → AWAITING_PLAN_APPROVAL (có kế hoạch, nếu requirePlanApproval) ; IN_PROGRESS → COMPLETED (có PR)."""
    if s["state"] == "PLANNING":
        s["_acts"].append({"name": f"sessions/{s['id']}/activities/a{len(s['_acts'])}", "originator": "agent",
                           "planGenerated": {"plan": {"id": f"plan-{s['id']}", "steps": [
                               {"id": "st1", "index": 0, "title": "Đọc backend/main.py", "description": "Tìm chỗ cần sửa"},
                               {"id": "st2", "index": 1, "title": "Sửa và thêm test", "description": ""},
                               {"id": "st3", "index": 2, "title": "Mở pull request", "description": ""}]}}})
        s["state"] = "AWAITING_PLAN_APPROVAL" if s.get("requirePlanApproval") else "IN_PROGRESS"
    elif s["state"] == "IN_PROGRESS":
        n = 100 + int(s["id"]) % 1000
        s["outputs"] = [{"pullRequest": {"url": f"https://github.com/{OWNER}/gen-workplace/pull/{n}",
                                         "title": s["title"], "description": "PR do Jules giả mở"}}]
        s["_acts"].append({"name": f"sessions/{s['id']}/activities/a{len(s['_acts'])}", "originator": "system",
                           "sessionCompleted": {}})
        s["state"] = "COMPLETED"


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def start(port=0):
    srv = ThreadedServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18898)
    a = ap.parse_args()
    srv = ThreadedServer(("127.0.0.1", a.port), Handler)
    print(f"Jules giả: http://127.0.0.1:{a.port}/v1alpha", flush=True)
    srv.serve_forever()

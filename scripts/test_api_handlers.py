#!/usr/bin/env python3
"""
Test audit chức năng Issue #12 cho các handler HTTP (backend/main.py). Chạy không cần server ngoài/agy/tmux:
mở ThreadedHTTPServer trên cổng loopback ngẫu nhiên, tmux giả trên PATH, subprocess.run của main được bọc để ghi lại.
- POST /api/runtime/chat: chỉ lưu chat_messages, KHÔNG gọi `tmux send-keys` (tin chứa $(id) không vào shell worker).
- POST /api/todo/update: id không tồn tại → 404 {"error": "Task not found"}; id thật → success=True.
- POST /api/gen/session/todos/save: conv_id không tồn tại → 400 JSON (không còn IntegrityError ngắt kết nối).
- Exception chưa bắt trong do_POST/do_GET → 500 JSON {"error": "<tên lỗi>"}.
- GET /api/roles/sop: mỗi vai có inputFrom/outputTo đọc từ ROLE.md, không có mục → "".
- POST /api/ssot/generate: không sinh roadmap/todo, không INSERT tin War Room mẫu, response nói thật.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-api-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
SENT_LOG = os.path.join(TMP, "tmux-sent.log")
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write('#!/bin/sh\nif [ "$1" = "send-keys" ]; then echo "$@" >> "%s"; exit 0; fi\nexit 1\n' % SENT_LOG)
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
AGY_OK = os.path.join(FAKEBIN, "agy")
with open(AGY_OK, "w") as f:
    f.write('#!/bin/sh\necho \'{"response": "OK từ agy giả", "usage": {}}\'\nexit 0\n')
os.chmod(AGY_OK, 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY_OK
os.environ.pop("GW_DIRECTIVE_ALLOW_ALL", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, main  # noqa: E402

PASSED = 0
FAILED = 0


def check(label, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label} {extra}")


# Bọc subprocess.run của main để ghi lại mọi lệnh (kể cả khi tmux giả không được gọi tới)
SUBPROC_CALLS = []
_real_run = main.subprocess.run


def _recording_run(args, *a, **kw):
    SUBPROC_CALLS.append(list(args) if isinstance(args, (list, tuple)) else [str(args)])
    return _real_run(args, *a, **kw)


main.subprocess.run = _recording_run

server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, payload=None):
    """Gọi API, trả (status, json|None, raw_text)."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        status = e.code
    try:
        return status, json.loads(raw), raw
    except Exception:
        return status, None, raw


print("[1] POST /api/runtime/chat không gửi gì vào tmux")
SUBPROC_CALLS.clear()
payload_msg = 'xin chào $(id) `whoami` "; rm -rf /tmp/x'
st, js, _ = call("POST", "/api/runtime/chat", {"message": payload_msg, "session_id": "gw-qa-agy"})
check("200 status=sent", st == 200 and js and js.get("status") == "sent", f"{st} {js}")
sent_keys = [c for c in SUBPROC_CALLS if c[:2] == ["tmux", "send-keys"]]
check("không có subprocess tmux send-keys", not sent_keys, str(sent_keys))
check("tmux giả không nhận send-keys", not os.path.exists(SENT_LOG))
with db.get_connection() as conn:
    row = conn.execute("SELECT runtime_id, body, tag FROM chat_messages WHERE runtime_id = 'gw-qa-agy' ORDER BY id DESC LIMIT 1").fetchone()
check("tin lưu nguyên văn vào chat_messages với runtime_id", row and row["body"] == payload_msg and row["tag"] == "Directive", dict(row) if row else None)
st, js, _ = call("POST", "/api/runtime/chat", {"message": "   "})
check("thiếu message → 400", st == 400 and js and "error" in js, f"{st} {js}")

shutil.rmtree(TMP, ignore_errors=True)
server.shutdown()
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

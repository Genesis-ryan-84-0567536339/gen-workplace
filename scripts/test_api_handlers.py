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

print("[2] POST /api/todo/update: id không tồn tại → 404")
with db.get_connection() as conn:
    conn.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-API', 'PRJ-GEN-WORKPLACE', 'Roadmap test', '', 1, 'queued', 1)")
    conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES ('TODO-API-1', 'RM-API', 'PRJ-GEN-WORKPLACE', 'Task thật', 'QA Tester', 'queued')")
    conn.commit()
check("db.update_todo_status id lạ → error Task not found", db.update_todo_status("TODO-KHONG-CO", "done").get("error") == "Task not found")
check("db.update_todo_status id thật → updated", db.update_todo_status("TODO-API-1", "in_progress").get("status") == "updated")
st, js, _ = call("POST", "/api/todo/update", {"id": "TODO-KHONG-CO", "status": "done"})
check("404 Task not found", st == 404 and js and js.get("error") == "Task not found" and js.get("id") == "TODO-KHONG-CO", f"{st} {js}")
st, js, _ = call("POST", "/api/todo/update", {"id": "TODO-API-1", "status": "review"})
check("id thật → 200 success=True", st == 200 and js and js.get("success") is True and js.get("new_status") == "review", f"{st} {js}")
st, js, _ = call("POST", "/api/todo/update", {"id": "TODO-API-1"})
check("thiếu status → 400", st == 400 and js and "error" in js, f"{st} {js}")

print("[3] POST /api/gen/session/todos/save: conv_id không tồn tại → 400 JSON")
st, js, raw = call("POST", "/api/gen/session/todos/save", {"conv_id": "conv-khong-co", "title": "Task", "viec_ref": "VIEC-1"})
check("400 + thông báo phiên không tồn tại", st == 400 and js == {"error": "Phiên chat không tồn tại: conv-khong-co"}, f"{st} {raw!r}")
conv = db.create_gen_conversation(title="Phiên test API")
st, js, _ = call("POST", "/api/gen/session/todos/save", {"conv_id": conv["id"], "title": "Task thật", "viec_ref": "VIEC-1"})
check("phiên thật → 200 saved", st == 200 and js and js.get("status") == "saved" and js.get("created") is True, f"{st} {js}")
check("db.gen_conversation_exists", db.gen_conversation_exists(conv["id"]) and not db.gen_conversation_exists("conv-khong-co") and not db.gen_conversation_exists(""))

print("[4] exception chưa bắt trong do_POST / do_GET → 500 JSON, server vẫn sống")


def _boom(*a, **kw):
    raise RuntimeError("nổ thử")


_orig_create_project, _orig_vault = db.create_new_project, db.get_vault_list
db.create_new_project, db.get_vault_list = _boom, _boom
st, js, raw = call("POST", "/api/project/create", {"name": "x"})
check("POST → 500 {'error': 'RuntimeError'}", st == 500 and js and js.get("error") == "RuntimeError", f"{st} {raw!r}")
st, js, raw = call("GET", "/api/vault/list")
check("GET → 500 {'error': 'RuntimeError'}", st == 500 and js and js.get("error") == "RuntimeError", f"{st} {raw!r}")
db.create_new_project, db.get_vault_list = _orig_create_project, _orig_vault
st, js, _ = call("GET", "/api/vault/list")
check("sau lỗi server vẫn phục vụ bình thường", st == 200 and js and "vault" in js, f"{st} {js}")

print("[5] GET /api/roles/sop: inputFrom/outputTo đọc từ ROLE.md, không có mục → ''")
ROLES_DIR = os.path.join(TMP, "roles")
os.makedirs(ROLES_DIR)
with open(os.path.join(ROLES_DIR, "gw-a-agy_ROLE.md"), "w", encoding="utf-8") as f:
    f.write("# Genesis Swarm Role Specification: Vai A\n- **Session Identifier**: `gw-a-agy`\n- **Assigned Scope**: `Backend`\n"
            "- **Active Mission**: Làm API.\n- **Nhận từ**: Lead Architect (schema CSDL)\n- **Bàn giao cho**: Frontend (endpoint API)\n")
with open(os.path.join(ROLES_DIR, "gw-b-agy_ROLE.md"), "w", encoding="utf-8") as f:
    f.write("# Genesis Swarm Role Specification: Vai B\n- **Session Identifier**: `gw-b-agy`\n- **Active Mission**: Làm UI.\n"
            "- **Input from**: Backend (REST API)\n- **Output to**: QA (bản build)\n")
with open(os.path.join(ROLES_DIR, "gw-c-agy_ROLE.md"), "w", encoding="utf-8") as f:
    f.write("# Genesis Swarm Role Specification: Vai C\n- **Session Identifier**: `gw-c-agy`\n- **Active Mission**: Kiểm thử.\n")
os.environ["GW_ROLES_DIR"] = ROLES_DIR
st, js, _ = call("GET", "/api/roles/sop")
roles = (js or {}).get("roles", {})
check("200 + 3 vai", st == 200 and set(roles) == {"gw-a-agy", "gw-b-agy", "gw-c-agy"}, f"{st} {list(roles)}")
a, b, c = roles.get("gw-a-agy", {}), roles.get("gw-b-agy", {}), roles.get("gw-c-agy", {})
check("tiếng Việt: Nhận từ / Bàn giao cho", a.get("inputFrom") == "Lead Architect (schema CSDL)" and a.get("outputTo") == "Frontend (endpoint API)", str(a))
check("tiếng Anh: Input from / Output to", b.get("inputFrom") == "Backend (REST API)" and b.get("outputTo") == "QA (bản build)", str(b))
check("không có mục → chuỗi rỗng, không bịa", c.get("inputFrom") == "" and c.get("outputTo") == "", str(c))
check("các key cũ vẫn còn", all(k in a for k in ("id", "title", "mission", "scope", "allowed", "blocked", "checklist")), str(list(a)))
os.environ.pop("GW_ROLES_DIR", None)

print("[6] POST /api/ssot/generate: chỉ lưu đặc tả, không sinh roadmap/todo, không tin War Room mẫu")


def _counts():
    with db.get_connection() as conn:
        return (conn.execute("SELECT count(*) FROM roadmaps").fetchone()[0],
                conn.execute("SELECT count(*) FROM todos").fetchone()[0],
                conn.execute("SELECT count(*) FROM chat_messages WHERE runtime_id = 'war_room'").fetchone()[0])


before = _counts()
st, js, _ = call("POST", "/api/ssot/generate", {"content": "Đặc tả thật:\nxây API kanban\n" + "chi tiết " * 60, "project_id": "PRJ-GEN-WORKPLACE"})
after = _counts()
check("200 status=saved", st == 200 and js and js.get("status") == "saved", f"{st} {js and js.get('status')}")
check("generated = {roadmaps: 0, todos: 0}", js and js.get("generated") == {"roadmaps": 0, "todos": 0}, str(js and js.get("generated")))
check("note nói thật", js and js.get("note") == "Chỉ lưu đặc tả làm nguồn cho các vai; roadmap/todo chưa được sinh tự động", str(js and js.get("note")))
check("có key state cho frontend", js and isinstance(js.get("state"), dict), str(type(js and js.get("state"))))
check("roadmaps/todos/war_room không đổi", before == after, f"{before} → {after}")
with db.get_connection() as conn:
    insts = [r[0] for r in conn.execute("SELECT instruction FROM agent_roles WHERE project_id = 'PRJ-GEN-WORKPLACE'").fetchall()]
    ssot = conn.execute("SELECT body FROM master_ssot WHERE id = 'SSOT-ACTIVE-PLAN'").fetchone()
check("roles_updated = số role thật", js and js.get("roles_updated") == len(insts), f"{js and js.get('roles_updated')} vs {len(insts)}")
check("instruction chỉ chứa trích đặc tả thật", insts and all("Đặc tả thật: xây API kanban" in i and "Chỉ huy kiến trúc toàn cục" not in i for i in insts), str(insts[:1]))
check("master_ssot lưu trích đoạn thật", ssot and ssot["body"].startswith("Đặc tả thật: xây API kanban"), str(ssot and ssot["body"][:60]))
check("summary không còn câu 'Đặc tả SSOT gốc từ Ryan'", js and js.get("ssot_summary", "").startswith("Đặc tả thật"), str(js and js.get("ssot_summary")))
st, js, _ = call("POST", "/api/ssot/generate", {"content": "  "})
check("content rỗng → 400, không ghi mặc định bịa", st == 400 and js and "error" in js and _counts() == after, f"{st} {js}")

shutil.rmtree(TMP, ignore_errors=True)
server.shutdown()
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

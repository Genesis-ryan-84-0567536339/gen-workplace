#!/usr/bin/env python3
"""
Test audit chức năng Issue #12 cho các handler HTTP (backend/main.py). Chạy không cần server ngoài/agy/tmux:
mở ThreadedHTTPServer trên cổng loopback ngẫu nhiên, tmux giả trên PATH, subprocess.run của main được bọc để ghi lại.
- POST /api/runtime/chat: chỉ lưu chat_messages, KHÔNG gọi `tmux send-keys` (tin chứa $(id) không vào shell worker).
- POST /api/todo/update: id không tồn tại → 404 {"error": "Task not found"}; id thật → success=True.
- POST /api/gen/session/todos/save: conv_id không tồn tại → 400 JSON (không còn IntegrityError ngắt kết nối).
- Exception chưa bắt trong do_POST/do_GET → 500 JSON {"error": "<tên lỗi>"}.
- GET /api/roles/sop: mỗi vai có inputFrom/outputTo đọc từ ROLE.md, không có mục → "".
- VIEC-12: endpoint làm dáng (ssot/orch/role model/catalog/vault) → 404, bảng DB giữ nguyên; /api/state gọn; UI không còn màn làm dáng.
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


_orig_create_project, _orig_gitlog = db.create_new_project, db.get_git_log
db.create_new_project, db.get_git_log = _boom, _boom
st, js, raw = call("POST", "/api/project/create", {"name": "x"})
check("POST → 500 {'error': 'RuntimeError'}", st == 500 and js and js.get("error") == "RuntimeError", f"{st} {raw!r}")
st, js, raw = call("GET", "/api/git/log")
check("GET → 500 {'error': 'RuntimeError'}", st == 500 and js and js.get("error") == "RuntimeError", f"{st} {raw!r}")
db.create_new_project, db.get_git_log = _orig_create_project, _orig_gitlog
st, js, _ = call("GET", "/api/git/log")
check("sau lỗi server vẫn phục vụ bình thường", st == 200 and js and "commits" in js, f"{st} {js}")

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

print("[6] VIEC-12: endpoint làm dáng đã gỡ → 404; bảng DB giữ nguyên; /api/swarm/dispatch bắt buộc session_id")


def _counts():
    with db.get_connection() as conn:
        return tuple(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("roadmaps", "todos", "master_ssot", "catalog_references", "agent_roles", "chat_messages"))


before = _counts()
for method, path, body in [("POST", "/api/ssot/generate", {"content": "Đặc tả thật " * 50}), ("POST", "/api/ssot/save", {"content": "x"}),
                           ("GET", "/api/ssot/spec", None), ("POST", "/api/role/model", {"role_id": "ROLE-01", "model": "x"}),
                           ("GET", "/api/orch/messages", None), ("POST", "/api/orch/chat", {"message": "hi"}),
                           ("POST", "/api/orch/spawn", {"role_name": "X"}), ("GET", "/api/catalog?q=a", None),
                           ("GET", "/api/vault/list", None), ("POST", "/api/events/verify", {"event_id": "EVT-1"})]:
    st, js, _ = call(method, path, body)
    check(f"{method} {path.split('?')[0]} → 404", st == 404, f"{st} {js}")
check("không bảng nào bị ghi / xóa", _counts() == before, f"{before} → {_counts()}")
with db.get_connection() as conn:
    tbls = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
check("bảng cũ vẫn còn (chỉ ngừng đọc)", {"roadmaps", "todos", "workflow_nodes", "master_ssot", "role_memories", "catalog_references", "ssot_events", "agent_runtimes"} <= tbls, str(sorted(tbls)))
st, js, _ = call("POST", "/api/swarm/dispatch", {"project_id": "PRJ-GEN-WORKPLACE"})
check("POST /api/swarm/dispatch không session_id → 400 (bỏ bản chạy toàn bộ Swarm)", st == 400 and "session_id" in (js or {}).get("error", ""), f"{st} {js}")
st, js, _ = call("GET", "/api/state")
check("/api/state chỉ còn project/projects/roles/gen_session_todos", st == 200 and sorted(js.keys()) == ["gen_session_todos", "project", "projects", "roles"], str(sorted((js or {}).keys())))
check("/api/state.roles không còn model (cấu hình model cho vai đã gỡ)", all("model" not in r for r in js.get("roles", [])), str(js.get("roles", [])[:1]))
st, js, _ = call("GET", "/api/status")
check("/api/status không còn ssot_synced, runtimes_count là số", st == 200 and "ssot_synced" not in js and isinstance(js.get("runtimes_count"), int), str(js))
html = open(os.path.join(ROOT, "frontend", "index.html"), encoding="utf-8").read()
for needle, label in [('id="orchestrator"', "màn Tổng chỉ huy"), ("Tổng chỉ huy", "chữ Tổng chỉ huy"), ("Chạy Toàn Bộ Swarm", "nút Chạy toàn bộ Swarm"),
                      ("Lưu đặc tả cho các vai", "nút Lưu đặc tả"), ('id="roadmapList"', "Roadmap"), ("Todo DAG", "Todo DAG"),
                      ("Phase hiện tại", "Phase hiện tại"), ('id="data-catalog"', "Catalog"), ('id="data-vault"', "Vault"),
                      ("/api/role/model", "gọi /api/role/model"), ("main · 6 vai", "chữ 6 vai trên thanh trên"), ('id="sideSsot"', "SSOT ở chân sidebar"),
                      ('id="sideRoles"', "Vai 6 ở chân sidebar"), ("Security Boundary", "ô Security Boundary")]:
    check(f"UI không còn {label}", needle not in html)
for keep in ('id="gen_workplace"', 'id="warroom"', 'id="runtimes"', 'id="implementation"', 'id="dashRoleKanbanBoard"', 'id="mcp"', 'id="data"',
             'id="data-repo"', 'id="data-db"', 'id="data-files"'):
    check(f"UI vẫn giữ {keep}", keep in html)

shutil.rmtree(TMP, ignore_errors=True)
server.shutdown()
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

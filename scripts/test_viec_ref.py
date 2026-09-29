#!/usr/bin/env python3
"""
Test Issue #12 (việc gắn VIEC-n + webhook sự kiện). Chạy không cần server/agy/tmux:
- Tạo task Kanban (save_gen_session_todo / MCP create_kanban_task) bắt buộc viec_ref ^VIEC-[0-9]+$.
- viec_ref có trong JSON todo (get_gen_session_todos, list_kanban_tasks, /api/state todos+kanban).
- GW_EVENT_WEBHOOK_URL trỏ http.server giả local: complete_task → task_completed; dispatch chatroom → dispatch_finished.
- Webhook chết → chỉ log, không lỗi. get_dispatch_log trả dòng dispatch_log kèm task_id/viec_ref.
"""
import json
import os
import sys
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-viec-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
AGY_OK = os.path.join(FAKEBIN, "agy")
with open(AGY_OK, "w") as f:
    f.write('#!/bin/bash\necho "OK từ agy giả"; exit 0\n')
os.chmod(AGY_OK, 0o755)

DISPATCH_REPO = os.path.join(TMP, "repo")
os.makedirs(DISPATCH_REPO)
GIT = ["git", "-C", DISPATCH_REPO, "-c", "user.name=test", "-c", "user.email=test@example.com"]
subprocess.run(GIT + ["init", "-q"], check=True)
with open(os.path.join(DISPATCH_REPO, "README.md"), "w") as f:
    f.write("repo tạm\n")
subprocess.run(GIT + ["add", "."], check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "init"], check=True)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY_OK
os.environ["GW_DISPATCH_REPO"] = DISPATCH_REPO
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "gw-worktrees")
os.environ.pop("GW_EVENT_WEBHOOK_URL", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, mcp_core  # noqa: E402

db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: None

# Webhook giả: http.server thread local ghi lại mọi POST
RECEIVED = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8")
        RECEIVED.append({"path": self.path, "headers": dict(self.headers), "json": json.loads(body)})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Hook)
threading.Thread(target=srv.serve_forever, daemon=True).start()
HOOK_URL = f"http://127.0.0.1:{srv.server_port}/hook"

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


def mcp_call(name, args):
    r = mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r["result"]
    return res["isError"], json.loads(res["content"][0]["text"]) if res["content"][0]["text"].startswith("{") else res["content"][0]["text"]


print("[1] migration: cột viec_ref có trong todos và gen_session_todos")
with db.get_connection() as conn:
    cols_t = [r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()]
    cols_g = [r[1] for r in conn.execute("PRAGMA table_info(gen_session_todos)").fetchall()]
    cols_d = [r[1] for r in conn.execute("PRAGMA table_info(dispatch_log)").fetchall()]
check("todos.viec_ref", "viec_ref" in cols_t, str(cols_t))
check("gen_session_todos.viec_ref", "viec_ref" in cols_g, str(cols_g))
check("dispatch_log có task_id/viec_ref", "task_id" in cols_d and "viec_ref" in cols_d, str(cols_d))

print("[2] tạo task Kanban: thiếu / sai viec_ref → lỗi; đúng → lưu")
conv = db.create_gen_conversation(title="Phiên test viec")["id"]
res = db.save_gen_session_todo(conv, None, "Việc không mã", viec_ref="")
check("thiếu viec_ref → error đúng câu", res.get("error") == "Thiếu viec_ref (mã việc trong Kho Ryan, vd VIEC-12)", str(res))
res = db.save_gen_session_todo(conv, None, "Việc mã sai", viec_ref="VIEC-abc")
check("viec_ref sai định dạng → error", res.get("error") == "Thiếu viec_ref (mã việc trong Kho Ryan, vd VIEC-12)", str(res))
res = db.save_gen_session_todo(conv, None, "Việc mã sai 2", viec_ref="12")
check("'12' → error", "error" in res, str(res))
check("chưa có task nào được lưu", db.get_gen_session_todos(conv) == [])
res = db.save_gen_session_todo(conv, None, "Việc có mã", description="mô tả", viec_ref="viec-12", checklist=["a", "b"])
check("viec-12 (thường) → chuẩn hóa VIEC-12, saved", res.get("status") == "saved" and res.get("viec_ref") == "VIEC-12" and res.get("created") is True, str(res))
tid = res["id"]
todos = db.get_gen_session_todos(conv)
check("get_gen_session_todos có viec_ref", len(todos) == 1 and todos[0]["viec_ref"] == "VIEC-12", str(todos[0].get("viec_ref")))

print("[3] sửa task đã có: không gửi viec_ref → giữ; gửi mã mới → đổi; mã sai → lỗi")
res = db.save_gen_session_todo(conv, tid, "Việc có mã (sửa tên)", status="in_progress")
check("sửa không viec_ref → saved, giữ VIEC-12", res.get("status") == "saved" and res.get("viec_ref") == "VIEC-12" and res.get("created") is False, str(res))
check("DB vẫn VIEC-12, title đã đổi", db.get_gen_session_todos(conv)[0]["viec_ref"] == "VIEC-12" and db.get_gen_session_todos(conv)[0]["title"] == "Việc có mã (sửa tên)")
res = db.save_gen_session_todo(conv, tid, "Việc có mã", viec_ref="VIEC-99")
check("đổi sang VIEC-99", res.get("viec_ref") == "VIEC-99" and db.get_gen_session_todos(conv)[0]["viec_ref"] == "VIEC-99", str(res))
res = db.save_gen_session_todo(conv, tid, "Việc có mã", viec_ref="sai")
check("mã sai khi sửa → error", "error" in res, str(res))

print("[4] MCP create_kanban_task / list_kanban_tasks")
is_err, out = mcp_call("create_kanban_task", {"conv_id": conv, "title": "Từ MCP không mã"})
check("thiếu viec_ref → isError + câu báo", is_err is True and out.get("error") == "Thiếu viec_ref (mã việc trong Kho Ryan, vd VIEC-12)", str(out))
is_err, out = mcp_call("create_kanban_task", {"conv_id": conv, "title": "Từ MCP có mã", "viec_ref": "VIEC-7", "checklist": ["x"]})
check("có viec_ref → ok", is_err is False and out.get("viec_ref") == "VIEC-7", str(out))
is_err, out = mcp_call("list_kanban_tasks", {"conv_id": conv})
check("list_kanban_tasks trả viec_ref", is_err is False and sorted(t["viec_ref"] for t in out["tasks"]) == ["VIEC-7", "VIEC-99"], str(out)[:200])
schema = mcp_core.TOOL_LOOKUP["create_kanban_task"]["inputSchema"]
check("schema MCP: viec_ref required", "viec_ref" in schema["properties"] and "viec_ref" in schema["required"])

print("[5] bảng todos cũ vẫn giữ (chỉ ngừng đọc ở /api/state), task roadmap có viec_ref")
with db.get_connection() as conn:
    conn.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-V', 'PRJ-GEN-WORKPLACE', 'RM viec', '', 1, 'queued', 1)")
    conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status, viec_ref) VALUES ('TODO-V1', 'RM-V', 'PRJ-GEN-WORKPLACE', 'Task roadmap có mã', 'QA Tester', 'queued', 'VIEC-3')")
    conn.commit()
st = db.get_full_state()
check("/api/state không còn gửi todos/kanban (VIEC-12)", "todos" not in st and "kanban" not in st, str(sorted(st.keys())))
check("get_task_viec_ref đọc được viec_ref của task bảng todos", db.get_task_viec_ref("TODO-V1") == "VIEC-3", db.get_task_viec_ref("TODO-V1"))

print("[6] webhook task_completed khi complete_task thành công")
os.environ["GW_EVENT_WEBHOOK_URL"] = HOOK_URL
head_sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
db.claim_task("gw-qa-agy", "TODO-V1")
res = db.complete_task("gw-qa-agy", "TODO-V1", "deadbeefcafe")
check("bằng chứng sai → không bắn webhook", "error" in res and RECEIVED == [], str(res))
res = db.complete_task("gw-qa-agy", "TODO-V1", head_sha)
check("complete ok, trả viec_ref + webhook_sent", res.get("status") == "completed" and res.get("viec_ref") == "VIEC-3" and res.get("webhook_sent") is True, str(res))
check("webhook nhận 1 POST", len(RECEIVED) == 1, str(RECEIVED))
ev = RECEIVED[0]["json"] if RECEIVED else {}
KEYS = ("event", "project_id", "viec_ref", "task_id", "session_id", "exit_code", "report_path", "evidence_ref", "verified_by", "at")
check("payload đủ key", all(k in ev for k in KEYS), str(sorted(ev.keys())))
check("payload đúng: task_completed / VIEC-3 / TODO-V1 / gw-qa-agy / SHA / git:commit", ev.get("event") == "task_completed" and ev.get("viec_ref") == "VIEC-3" and ev.get("task_id") == "TODO-V1" and ev.get("session_id") == "gw-qa-agy" and ev.get("evidence_ref") == head_sha and ev.get("verified_by") == "git:commit" and ev.get("exit_code") is None, str(ev))
check("Content-Type JSON", "application/json" in RECEIVED[0]["headers"].get("Content-Type", ""))
check("at là ISO có múi giờ", "T" in ev.get("at", "") and ("+" in ev.get("at", "") or "Z" in ev.get("at", "")), ev.get("at"))

print("[7] complete task Kanban phiên (gen_session_todos) → webhook viec_ref VIEC-99")
res = db.complete_task("gw-lead-agy", tid, head_sha)
check("complete task phiên ok", res.get("status") == "completed" and res.get("viec_ref") == "VIEC-99", str(res))
check("webhook thứ 2 viec_ref VIEC-99", len(RECEIVED) == 2 and RECEIVED[1]["json"]["viec_ref"] == "VIEC-99" and RECEIVED[1]["json"]["task_id"] == tid, str(RECEIVED[1:]))

print("[8] dispatch chatroom (@backend) kết thúc → webhook dispatch_finished + dispatch_log")
with db.get_connection() as conn:
    conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status, viec_ref) VALUES ('TODO-V2', 'RM-V', 'PRJ-GEN-WORKPLACE', 'Task backend', 'Backend & DB Specialist', 'queued', 'VIEC-5')")
    conn.commit()
db.claim_task("gw-backend-agy", "TODO-V2")
res = db.post_warroom_message(message="@backend kiểm tra API", author="Ryan (Owner)", wait=True)
check("dispatched gw-backend-agy", res["dispatched"] == ["gw-backend-agy"], str(res))
check("webhook thứ 3 dispatch_finished", len(RECEIVED) == 3 and RECEIVED[2]["json"]["event"] == "dispatch_finished", str(RECEIVED[2:]))
ev = RECEIVED[2]["json"] if len(RECEIVED) >= 3 else {}
check("payload: session gw-backend-agy, exit_code 0, task TODO-V2, viec VIEC-5, report_path tồn tại",
      ev.get("session_id") == "gw-backend-agy" and ev.get("exit_code") == 0 and ev.get("task_id") == "TODO-V2" and ev.get("viec_ref") == "VIEC-5" and ev.get("report_path") and os.path.exists(ev["report_path"]), str(ev))
log = db.get_dispatch_log(10)
check("get_dispatch_log có dòng mới nhất trước, kèm task_id/viec_ref/webhook_sent", log and log[0]["session_id"] == "gw-backend-agy" and log[0]["task_id"] == "TODO-V2" and log[0]["viec_ref"] == "VIEC-5" and log[0]["webhook_sent"] == 1 and log[0]["exit_code"] == 0, str(log[:1]))
check("get_dispatch_log(limit) giới hạn", len(db.get_dispatch_log(1)) == 1)

print("[9] webhook không tới được → chỉ log, API vẫn ok; tắt env → không gửi")
os.environ["GW_EVENT_WEBHOOK_URL"] = "http://127.0.0.1:9/khong-ai-nghe"
res = db.complete_task("gw-backend-agy", "TODO-V2", head_sha)  # người đang giữ task (#18)
check("complete vẫn ok, webhook_sent False", res.get("status") == "completed" and res.get("webhook_sent") is False, str(res))
os.environ["GW_EVENT_WEBHOOK_URL"] = ""
n_before = len(RECEIVED)
check("send_event_webhook trả False khi tắt", db.send_event_webhook("task_completed", task_id="x") is False and len(RECEIVED) == n_before)

srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

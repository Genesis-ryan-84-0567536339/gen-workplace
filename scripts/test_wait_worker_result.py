#!/usr/bin/env python3
"""
Test Issue #9: wait_worker_result — chờ kết quả worker phía server, không phải poll.
Chạy không cần server/agy thật: GW_AGY_BIN = agy giả (bash), repo git tạm cho worktree, DB tạm.
- post_warroom_message trả dispatch_id → wait_worker_result nhận done (exit_code, summary, report_path).
- Hết timeout → status running (+ hint), gọi lại → done. timeout_sec bị kẹp ≤ 120.
- agy thoát 0 nhưng in "no output produced … auto-denied" → failed. exit 3 → failed.
- Tìm theo session_id / task_id; không tìm thấy → not_found; thiếu tham số → error.
- Tin trả lời có reply_to = id tin yêu cầu + created_at đủ ngày giờ; get_warroom_messages trả N tin MỚI NHẤT.
- MCP tool wait_worker_result + HTTP GET/POST /api/dispatch/wait.
- Giao việc qua tmux (/api/swarm/dispatch): tmux THẬT (socket riêng TMUX_TMPDIR) + agy giả → watcher thấy
  '=== XONG exit=0 ===' → webhook dispatch_finished tự bắn (không ai poll) và wait_worker_result trả done.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-wait-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)


def write_exec(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)


# agy giả: ngủ FAKE_AGY_SLEEP giây (mặc định 0), in kết quả, thoát FAKE_AGY_EXIT
AGY_OK = os.path.join(FAKEBIN, "agy")
write_exec(AGY_OK, '#!/bin/bash\nsleep "${FAKE_AGY_SLEEP:-0}"\necho "KẾT QUẢ từ agy giả"; echo "cwd=$(pwd)"; exit "${FAKE_AGY_EXIT:-0}"\n')
AGY_DENIED = os.path.join(FAKEBIN, "agy-denied")
write_exec(AGY_DENIED, '#!/bin/bash\necho \'jetski: no output produced — a tool required the "command" permission and was auto-denied\'; exit 0\n')

DISPATCH_REPO = os.path.join(TMP, "repo")
os.makedirs(DISPATCH_REPO)
GIT = ["git", "-C", DISPATCH_REPO, "-c", "user.name=test", "-c", "user.email=test@example.com"]
subprocess.run(GIT + ["init", "-q"], check=True)
with open(os.path.join(DISPATCH_REPO, "README.md"), "w") as f:
    f.write("repo tạm\n")
subprocess.run(GIT + ["add", "."], check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "init"], check=True)

# Webhook nhận sự kiện
EVENTS = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        EVENTS.append(json.loads(self.rfile.read(n).decode("utf-8")))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


hook = HTTPServer(("127.0.0.1", 0), Hook)
threading.Thread(target=hook.serve_forever, daemon=True).start()

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY_OK
os.environ["GW_DISPATCH_REPO"] = DISPATCH_REPO
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "gw-worktrees")
os.environ["GW_EVENT_WEBHOOK_URL"] = f"http://127.0.0.1:{hook.server_address[1]}/hook"
os.environ["TMUX_TMPDIR"] = os.path.join(TMP, "tmux")  # tmux thật nhưng socket riêng, không đụng phiên thật
os.environ.pop("TMUX", None)
os.environ.pop("GW_DIRECTIVE_ALLOW_ALL", None)
for d in (os.environ["DATA_DIR"], os.environ["HOME"], os.environ["TMUX_TMPDIR"]):
    os.makedirs(d)
sys.path.insert(0, ROOT)

from backend import db, main, mcp_core  # noqa: E402

db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: None

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


def did_of(res, sid):
    return next(d["dispatch_id"] for d in res["dispatches"] if d["session_id"] == sid)


print("[1] post_warroom_message trả dispatch_id; wait_worker_result → done")
os.environ["FAKE_AGY_SLEEP"] = "1"
res = db.post_warroom_message(message="@backend kiểm tra API", author="Ryan (Owner)", wait=False)
check("dispatches có dispatch_id cho gw-backend-agy", res["dispatches"] and res["dispatches"][0]["session_id"] == "gw-backend-agy"
      and isinstance(res["dispatches"][0]["dispatch_id"], int), str(res.get("dispatches")))
check("note hướng dẫn gọi wait_worker_result", "wait_worker_result" in res["note"], res["note"])
did = did_of(res, "gw-backend-agy")
with db.get_connection() as conn:
    st0 = conn.execute("SELECT status FROM dispatch_log WHERE id = ?", (did,)).fetchone()["status"]
check("dispatch_log ghi status=running ngay lúc giao", st0 == "running", st0)
t0 = time.time()
w = db.wait_worker_result(dispatch_id=did, timeout_sec=20)
elapsed = time.time() - t0
check("status done, exit_code 0", w["status"] == "done" and w["exit_code"] == 0, json.dumps(w, ensure_ascii=False)[:300])
check("trả về ngay khi xong (không chờ hết 20s)", elapsed < 8, f"{elapsed:.1f}s")
check("summary là output thật", "KẾT QUẢ từ agy giả" in w["summary"], w["summary"][:200])
check("report_path tồn tại", w["report_path"] and os.path.exists(w["report_path"]), w["report_path"])
check("request_msg_id = id tin yêu cầu", w["request_msg_id"] == res["user_message"]["id"], f"{w['request_msg_id']} vs {res['user_message']['id']}")
with db.get_connection() as conn:
    reply = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (w["reply_msg_id"],)).fetchone()
check("tin trả lời nối về tin yêu cầu (reply_to)", reply is not None and reply["reply_to"] == res["user_message"]["id"], dict(reply) if reply else "none")
check("tin trả lời có created_at đủ ngày giờ", reply is not None and len(reply["created_at"]) >= 19 and reply["created_at"][:4].isdigit()
      and "T" in reply["created_at"], reply["created_at"] if reply else "")
check("user_message trả created_at", len(res["user_message"].get("created_at", "")) >= 19)

print("[2] hết timeout → running, gọi lại → done; timeout bị kẹp ≤ 120")
os.environ["FAKE_AGY_SLEEP"] = "3"
res = db.post_warroom_message(message="@qa chạy test dài", author="Ryan (Owner)", wait=False)
did = did_of(res, "gw-qa-agy")
t0 = time.time()
w = db.wait_worker_result(dispatch_id=did, timeout_sec=1)
el = time.time() - t0
check("status running khi hết giờ", w["status"] == "running" and w["exit_code"] is None, str(w))
check("chờ đúng ~1s", 0.8 <= el < 3, f"{el:.2f}s")
check("có hint gọi lại cùng dispatch_id", f"dispatch_id={did}" in w.get("hint", ""), w.get("hint"))
w = db.wait_worker_result(dispatch_id=did, timeout_sec=30)
check("gọi lại → done", w["status"] == "done" and w["exit_code"] == 0, str(w)[:200])
w = db.wait_worker_result(dispatch_id=did, timeout_sec=999)
check("timeout_sec 999 bị kẹp thành 120", w["timeout_sec"] == 120, str(w["timeout_sec"]))
w = db.wait_worker_result(dispatch_id=did, timeout_sec=None)
check("timeout mặc định 60", w["timeout_sec"] == 60, str(w["timeout_sec"]))
os.environ["FAKE_AGY_SLEEP"] = "0"

print("[3] agy thoát 0 nhưng auto-denied → failed; exit 3 → failed")
os.environ["GW_AGY_BIN"] = AGY_DENIED
res = db.post_warroom_message(message="@lead rà quyền", author="Ryan (Owner)", wait=True)
w = db.wait_worker_result(dispatch_id=did_of(res, "gw-lead-agy"), timeout_sec=5)
check("status failed dù exit_code 0", w["status"] == "failed" and w["exit_code"] == 0, str(w)[:300])
ev = [e for e in EVENTS if e.get("event") == "dispatch_finished" and e.get("session_id") == "gw-lead-agy"]
check("webhook dispatch_finished mang status failed", ev and ev[-1]["status"] == "failed" and ev[-1]["exit_code"] == 0, str(ev)[:200])
check("summary nói rõ bị từ chối quyền", "từ chối quyền" in w["summary"] and "auto-denied" in w["summary"], w["summary"][:200])
with db.get_connection() as conn:
    body = conn.execute("SELECT body FROM chat_messages WHERE id = ?", (w["reply_msg_id"],)).fetchone()["body"]
check("tin trả lời ghi failed, không chỉ exit=0", "failed" in body and "từ chối quyền" in body, body[-200:])
check("agy_output_denied bỏ qua câu trả lời dài có nhắc 'auto-denied'", db.agy_output_denied("x" * 3000 + " auto-denied") == "")
os.environ["GW_AGY_BIN"] = AGY_OK
os.environ["FAKE_AGY_EXIT"] = "3"
res = db.post_warroom_message(message="@qa build", author="Ryan (Owner)", wait=True)
w = db.wait_worker_result(dispatch_id=did_of(res, "gw-qa-agy"), timeout_sec=5)
check("exit 3 → failed, exit_code 3", w["status"] == "failed" and w["exit_code"] == 3, str(w)[:200])
os.environ["FAKE_AGY_EXIT"] = "0"

print("[4] tìm theo session_id / task_id; not_found; thiếu tham số")
with db.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-W', 'PRJ-GEN-WORKPLACE', 'RM', '', 1, 'queued', 1)")
    conn.execute("INSERT OR IGNORE INTO todos (id, roadmap_id, project_id, title, assigned_role, status, viec_ref) VALUES ('TODO-W1', 'RM-W', 'PRJ-GEN-WORKPLACE', 'Việc chờ', 'Lead', 'in_progress', 'VIEC-9')")
    conn.execute("UPDATE tmux_sessions SET current_task_id = 'TODO-W1' WHERE id = 'gw-lead-agy'")
    conn.commit()
res = db.post_warroom_message(message="@lead lên kế hoạch", author="Ryan (Owner)", wait=False)
w = db.wait_worker_result(task_id="TODO-W1", timeout_sec=10)
check("theo task_id → done, gắn task/viec_ref", w["status"] == "done" and w["task_id"] == "TODO-W1" and w["viec_ref"] == "VIEC-9"
      and w["dispatch_id"] == did_of(res, "gw-lead-agy"), str(w)[:300])
check("task_status trả kèm", w["task_status"] == "in_progress", w.get("task_status"))
w = db.wait_worker_result(session_id="gw-lead-agy", timeout_sec=1)
check("theo session_id → lần mới nhất", w["dispatch_id"] == did_of(res, "gw-lead-agy") and w["status"] == "done", str(w)[:200])
check("dispatch_id không tồn tại → not_found", db.wait_worker_result(dispatch_id=999999, timeout_sec=1)["status"] == "not_found")
check("không tham số → error", db.wait_worker_result(timeout_sec=1)["status"] == "error")

print("[5] get_warroom_messages trả N tin MỚI NHẤT theo thứ tự tăng dần")
for i in range(40):
    db.post_warroom_message(channel_id="kenh_dai", message=f"tin số {i}", author="Ryan (Owner)")
msgs = db.get_warroom_messages("kenh_dai", limit=30)
check("đúng 30 tin", len(msgs) == 30, str(len(msgs)))
check("có tin mới nhất (tin số 39) ở cuối", msgs[-1]["body"] == "tin số 39", msgs[-1]["body"])
check("tin đầu là tin số 10 (bỏ 10 tin cũ nhất)", msgs[0]["body"] == "tin số 10", msgs[0]["body"])
check("thứ tự tăng dần theo id", [m["id"] for m in msgs] == sorted(m["id"] for m in msgs))
check("có trường reply_to/created_at", "reply_to" in msgs[0] and msgs[0]["created_at"])

print("[6] MCP tool wait_worker_result + post_warroom_message trả dispatch_id")
check("tool có trong TOOLS", "wait_worker_result" in mcp_core.TOOL_LOOKUP)
r = mcp_core.execute_tool("post_warroom_message", {"message": "@devops kiểm tra", "author": "Claude"})
payload = json.loads(r["content"][0]["text"])
mdid = payload["dispatches"][0]["dispatch_id"]
r = mcp_core.execute_tool("wait_worker_result", {"dispatch_id": mdid, "timeout_sec": 20})
mres = json.loads(r["content"][0]["text"])
check("MCP → done, isError False", mres["status"] == "done" and r["isError"] is False, str(mres)[:200])
r = mcp_core.execute_tool("wait_worker_result", {"dispatch_id": 999999, "timeout_sec": 1})
check("MCP not_found → isError True", r["isError"] is True)

print("[7] HTTP GET/POST /api/dispatch/wait")
server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def call(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


code, body = call("GET", f"/api/dispatch/wait?dispatch_id={mdid}&timeout_sec=5")
check("GET 200 done", code == 200 and body["status"] == "done" and body["dispatch_id"] == mdid, f"{code} {body}")
code, body = call("POST", "/api/dispatch/wait", {"session_id": "gw-devops-agy", "timeout_sec": 5})
check("POST theo session_id 200 done", code == 200 and body["status"] == "done", f"{code} {body}")
code, body = call("GET", "/api/dispatch/wait?dispatch_id=999999&timeout_sec=1")
check("không tìm thấy → 404", code == 404 and body["status"] == "not_found", f"{code} {body}")
code, body = call("GET", "/api/dispatch/wait")
check("thiếu tham số → 400", code == 400, f"{code} {body}")
code, body = call("GET", "/api/dispatch/log?limit=5")
check("/api/dispatch/log có status/summary/kind", code == 200 and all(k in body["items"][0] for k in ("status", "summary", "kind")), str(body)[:200])

print("[8] giao việc qua tmux thật: watcher thấy XONG → webhook tự bắn, wait → done")
if not shutil.which("tmux"):
    print("  (bỏ qua: máy không có tmux)")
else:
    os.makedirs(os.path.join(os.environ["HOME"], "gw-reports"), exist_ok=True)
    subprocess.run(["tmux", "kill-session", "-t", "gw-qa-agy"], capture_output=True)  # phiên app có thể đã tự mở
    subprocess.run(["tmux", "new-session", "-d", "-s", "gw-qa-agy", "-x", "200", "-y", "40", "-c", TMP, "bash --norc --noprofile"], check=True)
    time.sleep(0.3)
    with db.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO todos (id, roadmap_id, project_id, title, assigned_role, status, viec_ref) VALUES ('TODO-W2', 'RM-W', 'PRJ-GEN-WORKPLACE', 'Chạy thử tmux', 'QA', 'in_progress', 'VIEC-21')")
        conn.execute("UPDATE tmux_sessions SET current_task_id = 'TODO-W2' WHERE id = 'gw-qa-agy'")
        conn.commit()
    EVENTS.clear()
    os.environ["FAKE_AGY_SLEEP"] = "1"
    out = db.dispatch_swarm_workflow(session_id="gw-qa-agy")["gw-qa-agy"]
    check("dispatched qua tmux thật, có dispatch_id", out["status"] == "dispatched" and out["tmux_real"] and isinstance(out.get("dispatch_id"), int), str(out)[:300])
    tdid = out.get("dispatch_id")
    deadline = time.time() + 20
    while time.time() < deadline and not any(e.get("event") == "dispatch_finished" and e.get("task_id") == "TODO-W2" for e in EVENTS):
        time.sleep(0.3)
    ev = [e for e in EVENTS if e.get("event") == "dispatch_finished" and e.get("task_id") == "TODO-W2"]
    check("webhook dispatch_finished tự bắn (không ai gọi wait)", len(ev) == 1 and ev[0]["exit_code"] == 0 and ev[0]["viec_ref"] == "VIEC-21"
          and ev[0]["session_id"] == "gw-qa-agy" and ev[0]["status"] == "done", str(EVENTS)[:300])
    w = db.wait_worker_result(dispatch_id=tdid, timeout_sec=10)
    check("wait_worker_result(tmux) → done exit 0", w["status"] == "done" and w["exit_code"] == 0 and w["kind"] == "tmux", str(w)[:300])
    check("summary lấy từ báo cáo tee", "KẾT QUẢ từ agy giả" in w["summary"], w["summary"][:200])
    check("webhook_sent = true", w["webhook_sent"] is True)
    check("không bắn trùng webhook", len([e for e in EVENTS if e.get("task_id") == "TODO-W2"]) == 1, str(len(EVENTS)))
    subprocess.run(["tmux", "kill-server"], capture_output=True)

hook.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

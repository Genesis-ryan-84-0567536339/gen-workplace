#!/usr/bin/env python3
"""
Test #34 / #39: bỏ vai security và frontend — đánh dấu 'retired' (không DELETE, giữ lịch sử), @security / @frontend trả lỗi rõ ràng.
Chạy không cần server ngoài/agy/tmux thật: tmux giả ghi lại mọi lần bị gọi; agy giả.
- DB mới: không seed gw-security-agy / ROLE-06, gw-frontend-agy / ROLE-03; còn 4 vai (lead, backend, devops, qa).
- DB cũ (có dòng security active + dispatch_log + chat_messages của security): retire_roles() → status 'retired' ở
  tmux_sessions + agent_roles, kill-session đúng 1 lần; chạy lại lần 2 không làm gì; lịch sử giữ nguyên.
- /api/tmux/sessions, /api/state.roles, /api/roles/sop không còn security; include_retired=True vẫn thấy.
- War Room: tin có @security → 400, error nói rõ "đã bỏ", KHÔNG lưu tin, không dispatch.
- /api/task/assign session_id=security|gw-security-agy → 400 code retired_role; task không bị claim.
- ensure_tmux_session_live / wake_all / thread dọn phiên không đụng phiên retired.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-retire-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
CALL_LOG = os.path.join(TMP, "tmux-calls.log")
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write(f'#!/bin/sh\necho "$*" >> "{CALL_LOG}"\n[ "$1" = "list-sessions" ] && exit 0\nexit 1\n')
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
with open(os.path.join(FAKEBIN, "agy"), "w") as f:
    f.write('#!/bin/sh\necho "OK từ agy giả"; exit 0\n')
os.chmod(os.path.join(FAKEBIN, "agy"), 0o755)
open(CALL_LOG, "w").close()

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = os.path.join(FAKEBIN, "agy")
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "worktrees")
os.environ["GW_TMUX_INIT_DIR"] = os.path.join(TMP, "tmux-init")
os.environ["GW_ROLES_DIR"] = os.path.join(TMP, "roles")
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
os.makedirs(os.environ["GW_ROLES_DIR"])
for sid in ("gw-qa-agy", "gw-security-agy", "gw-frontend-agy"):
    with open(os.path.join(os.environ["GW_ROLES_DIR"], f"{sid}_ROLE.md"), "w") as f:
        f.write(f"# Genesis Swarm Role Specification: {sid}\n")
sys.path.insert(0, ROOT)

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


def calls():
    with open(CALL_LOG) as f:
        return [ln.strip() for ln in f if ln.strip()]


from backend import db, main  # noqa: E402

SEC = "gw-security-agy"
FE = "gw-frontend-agy"


def q(sql, args=()):
    with db.get_connection() as c:
        return c.execute(sql, args).fetchall()


print("[1] DB mới: không seed security")
check("tmux_sessions không có security", not q("SELECT 1 FROM tmux_sessions WHERE id = ?", (SEC,)))
check("agent_roles không có ROLE-06", not q("SELECT 1 FROM agent_roles WHERE id = 'ROLE-06'"))
check("tmux_sessions không có frontend", not q("SELECT 1 FROM tmux_sessions WHERE id = ?", (FE,)))
check("agent_roles không có ROLE-03", not q("SELECT 1 FROM agent_roles WHERE id = 'ROLE-03'"))
check("SWARM_SESSION_IDS còn 4 vai lead/backend/devops/qa", db.SWARM_SESSION_IDS == ["gw-lead-agy", "gw-backend-agy", "gw-devops-agy", "gw-qa-agy"], str(db.SWARM_SESSION_IDS))
check("WARROOM_ROLE_SESSIONS không có security/frontend", set(db.WARROOM_ROLE_SESSIONS) == {"lead", "backend", "devops", "qa"}, str(db.WARROOM_ROLE_SESSIONS))
check("retire_roles trên DB mới không gọi tmux", db.retire_roles() == [] and calls() == [], str(calls()))

print("[2] DB cũ có security + frontend đang chạy + lịch sử → retire_roles (chỉ UPDATE)")
with db.get_connection() as c:
    c.execute("INSERT INTO tmux_sessions (id, project_id, role_name, cli_tool, account_type, account_label, profile_dir, status, pid, cwd, terminal_output) "
              "VALUES (?, 'PRJ-GEN-WORKPLACE', 'Security Auditor', 'agy', 'owner_default', '', '', 'active', 123, '/workspace', 'cũ')", (SEC,))
    c.execute("INSERT INTO agent_roles (id, project_id, role_key, name, cli_tool, scope, instruction, status) "
              "VALUES ('ROLE-06', 'PRJ-GEN-WORKPLACE', 'S', 'Security Auditor', 'Codex', '', '', 'active')")
    c.execute("INSERT INTO tmux_sessions (id, project_id, role_name, cli_tool, account_type, account_label, profile_dir, status, pid, cwd, terminal_output) "
              "VALUES (?, 'PRJ-GEN-WORKPLACE', 'Frontend Specialist', 'agy', 'profile2', '', '', 'active', 456, '/workspace', 'cũ')", (FE,))
    c.execute("INSERT INTO agent_roles (id, project_id, role_key, name, cli_tool, scope, instruction, status) "
              "VALUES ('ROLE-03', 'PRJ-GEN-WORKPLACE', 'F', 'Frontend Specialist', 'Cursor CLI', '', '', 'active')")
    c.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body) VALUES ('PRJ-GEN-WORKPLACE', 'war_room', ?, '10:01', 'Report', 'tin cũ của frontend')", (FE,))
    c.execute("INSERT INTO dispatch_log (session_id, command, exit_code, status, kind) VALUES (?, 'agy -p x', 0, 'done', 'warroom')", (SEC,))
    c.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body) VALUES ('PRJ-GEN-WORKPLACE', 'war_room', ?, '10:00', 'Report', 'báo cáo cũ')", (SEC,))
    c.commit()
n_disp = q("SELECT count(*) FROM dispatch_log WHERE session_id = ?", (SEC,))[0][0]
n_chat = q("SELECT count(*) FROM chat_messages WHERE author = ?", (SEC,))[0][0]
open(CALL_LOG, "w").close()
changed = db.retire_roles()
check("retire_roles trả gw-security-agy + gw-frontend-agy", changed == [SEC, FE], str(changed))
check("tmux_sessions.status = retired, pid 0", q("SELECT status, pid FROM tmux_sessions WHERE id = ?", (SEC,))[0][:] == ("retired", 0), str(q("SELECT status, pid FROM tmux_sessions WHERE id = ?", (SEC,))[0][:]))
check("agent_roles ROLE-06 retired", q("SELECT status FROM agent_roles WHERE id = 'ROLE-06'")[0][0] == "retired")
check("frontend: tmux_sessions retired, pid 0", tuple(q("SELECT status, pid FROM tmux_sessions WHERE id = ?", (FE,))[0]) == ("retired", 0))
check("agent_roles ROLE-03 retired", q("SELECT status FROM agent_roles WHERE id = 'ROLE-03'")[0][0] == "retired")
check("tắt tmux của security và frontend, mỗi phiên đúng 1 lần", calls() == [f"kill-session -t {SEC}", f"kill-session -t {FE}"], str(calls()))
check("dòng tmux_sessions KHÔNG bị xóa", len(q("SELECT 1 FROM tmux_sessions WHERE id IN (?, ?)", (SEC, FE))) == 2)
check("chat_messages của frontend giữ nguyên", q("SELECT count(*) FROM chat_messages WHERE author = ?", (FE,))[0][0] == 1)
check("dispatch_log giữ nguyên", q("SELECT count(*) FROM dispatch_log WHERE session_id = ?", (SEC,))[0][0] == n_disp == 1)
check("chat_messages giữ nguyên", q("SELECT count(*) FROM chat_messages WHERE author = ?", (SEC,))[0][0] == n_chat == 1)
open(CALL_LOG, "w").close()
check("chạy lại lần 2: không đổi gì, không gọi tmux", db.retire_roles() == [] and calls() == [], str(calls()))
db.init_db()
db.seed_tmux_sessions()
db.seed_real_project()
check("init_db/seed chạy lại không hồi sinh security", q("SELECT status FROM tmux_sessions WHERE id = ?", (SEC,))[0][0] == "retired")
check("init_db/seed chạy lại không hồi sinh frontend", q("SELECT status FROM tmux_sessions WHERE id = ?", (FE,))[0][0] == "retired"
      and q("SELECT status FROM agent_roles WHERE id = 'ROLE-03'")[0][0] == "retired")

print("[3] API lọc vai retired")
server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


st, res = call("GET", "/api/tmux/sessions")
ids = [s["id"] for s in res.get("sessions", [])]
check("/api/tmux/sessions không có security/frontend, còn 4 vai", st == 200 and SEC not in ids and FE not in ids and len(ids) == 4, str(ids))
check("include_retired=True vẫn thấy dòng lịch sử", {SEC, FE} <= {s["id"] for s in db.get_tmux_sessions(include_retired=True)})
st, res = call("GET", "/api/state")
check("/api/state.roles không có Security Auditor / Frontend Specialist", st == 200 and all(r["name"] not in ("Security Auditor", "Frontend Specialist") for r in res.get("roles", [])) and len(res.get("roles", [])) == 4, str([r["name"] for r in res.get("roles", [])]))
st, res = call("GET", "/api/status")
check("/api/status active_agents = 4", res.get("active_agents") == 4, str(res.get("active_agents")))
sop = db.get_roles_sop()
check("/api/roles/sop bỏ ROLE.md của security, frontend", SEC not in sop and FE not in sop and "gw-qa-agy" in sop, str(list(sop)))

print("[4] @security trong War Room → lỗi rõ, không lưu, không dispatch")
n_msgs = q("SELECT count(*) FROM chat_messages")[0][0]
n_d = q("SELECT count(*) FROM dispatch_log")[0][0]
st, res = call("POST", "/api/warroom/send", {"message": "@security rà quyền file", "author": "Ryan (Owner)"})
check("HTTP 400", st == 400, f"{st} {res}")
check("lỗi nói rõ vai đã bỏ", "đã bỏ" in res.get("error", "") and res.get("code") == "retired_role", str(res))
st, res = call("POST", "/api/warroom/send", {"message": "@qa và @Security cùng xem", "author": "Ryan (Owner)"})
check("tin trộn @qa + @Security cũng bị từ chối (không giao nửa vời)", st == 400 and res.get("retired_roles") == ["security"], f"{st} {res}")
check("không lưu tin nào", q("SELECT count(*) FROM chat_messages")[0][0] == n_msgs)
check("không tạo dispatch nào", q("SELECT count(*) FROM dispatch_log")[0][0] == n_d)
st, res = call("POST", "/api/warroom/send", {"message": "@qa đọc README", "author": "Ryan (Owner)"})
check("@qa vẫn giao bình thường", st == 200 and res.get("dispatched") == ["gw-qa-agy"], f"{st} {res}")
r = db.post_warroom_message(message="gửi báo cáo tới abc@security.io", author="Ryan (Owner)")
check("email abc@security.io không bị coi là @security", "error" not in r, str(r))
n_msgs = q("SELECT count(*) FROM chat_messages")[0][0]
n_d = q("SELECT count(*) FROM dispatch_log")[0][0]
st, res = call("POST", "/api/warroom/send", {"message": "@frontend sửa nút Gửi", "author": "Ryan (Owner)"})
check("@frontend → 400 retired_role", st == 400 and res.get("code") == "retired_role" and res.get("retired_roles") == ["frontend"], f"{st} {res}")
check("lỗi @frontend chỉ đường sang @backend", "đã bỏ" in res.get("error", "") and "@backend" in res.get("error", ""), str(res))
st, res = call("POST", "/api/warroom/send", {"message": "@backend và @Frontend cùng làm", "author": "Ryan (Owner)"})
check("tin trộn @backend + @Frontend bị từ chối", st == 400 and res.get("retired_roles") == ["frontend"], f"{st} {res}")
check("@frontend: không lưu tin, không dispatch", q("SELECT count(*) FROM chat_messages")[0][0] == n_msgs and q("SELECT count(*) FROM dispatch_log")[0][0] == n_d)
r = db.post_warroom_message(message="gửi file cho dev@frontend.dev", author="Ryan (Owner)")
check("email dev@frontend.dev không bị coi là @frontend", "error" not in r, str(r))

print("[5] /api/task/assign cho security / frontend → 400 retired_role")
conv = db.create_gen_conversation(title="VIEC-99: thử retire")
t = db.save_gen_session_todo(conv["id"], None, "Task thử retire", "", "todo", "high", "Gen Core", [], "", viec_ref="VIEC-99")
tid = t.get("id")
for who in ("security", "gw-security-agy", "@Security", "frontend", "gw-frontend-agy", "@Frontend"):
    st, res = call("POST", "/api/task/assign", {"todo_id": tid, "session_id": who})
    check(f"assign {who} → 400 retired_role", st == 400 and res.get("code") == "retired_role" and "đã bỏ" in res.get("error", ""), f"{st} {res}")
check("task không bị claim", not (q("SELECT claimed_by FROM gen_session_todos WHERE id = ?", (tid,))[0][0] or ""))

print("[6] vòng đời tmux không đụng vai retired")
open(CALL_LOG, "w").close()
r = db.ensure_tmux_session_live(SEC)
check("ensure_tmux_session_live(security) → error, không new-session", r.get("status") == "error" and "đã bỏ" in r.get("message", "") and not any(c.startswith("new-session") for c in calls()), f"{r} {calls()}")
r = db.ensure_tmux_session_live(FE)
check("ensure_tmux_session_live(frontend) → error, không new-session", r.get("status") == "error" and "đã bỏ" in r.get("message", "") and "@backend" in r.get("message", "") and not any(c.startswith("new-session") for c in calls()), f"{r} {calls()}")
db.manage_tmux_swarm_lifecycle("wake_all", "all")
check("wake_all bỏ qua security, frontend", not any(SEC in c or FE in c for c in calls() if c.startswith("new-session")), str(calls()))
res = db.reap_idle_tmux_sessions(idle_min=15)
check("thread dọn phiên không đổi trạng thái retired", SEC not in res["marked_stopped"] and FE not in res["marked_stopped"]
      and [x[0] for x in q("SELECT status FROM tmux_sessions WHERE id IN (?, ?)", (SEC, FE))] == ["retired", "retired"], str(res))

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)

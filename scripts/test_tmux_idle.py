#!/usr/bin/env python3
"""
Test #32: phiên tmux mở theo nhu cầu, tự hibernate sau GW_TMUX_IDLE_MIN phút rảnh.
Chạy không cần server ngoài/tmux thật: tmux giả trên PATH giữ danh sách phiên "đang chạy" trong 1 file
(tên, session_activity, số client attach) và ghi lại mọi lần bị gọi.
- DB mới: mọi phiên worker 'hibernated', import db không gọi tmux; init_db chạy lại (DB cũ thiếu cột) không lỗi.
- ensure_tmux_session_live: phiên ngủ → new-session + active + last_activity_at; phiên đang chạy → không khởi động lại.
- reap_idle_tmux_sessions: rảnh < ngưỡng giữ; rảnh ≥ ngưỡng → hibernate (kill-session); đang attach / đang có
  dispatch tmux running → giữ; DB 'active' nhưng tmux không chạy → đánh dấu hibernated; GW_TMUX_IDLE_MIN=0 → tắt.
- POST /api/tmux/send, MCP send_worker_directive lên phiên đang ngủ → mở phiên rồi mới send-keys.
- POST /api/tmux/action open: phiên đang chạy không bị kill; /api/tmux/sessions có idle_min.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-tmuxidle-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
CALL_LOG = os.path.join(TMP, "tmux-calls.log")
LIVE = os.path.join(TMP, "tmux-live.txt")   # mỗi dòng: <tên> <session_activity> <attached>
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write(f"""#!/bin/bash
echo "$*" >> "{CALL_LOG}"
touch "{LIVE}"
case "$1" in
  list-sessions)
    i=5000
    while read -r n act att; do
      [ -z "$n" ] && continue; i=$((i+1))
      case "$*" in *session_activity*) echo "$n $act $att";; *pane_pid*) echo "$n $i";; *) echo "$n";; esac
    done < "{LIVE}"; exit 0;;
  new-session)
    while [ $# -gt 0 ]; do [ "$1" = "-s" ] && echo "$2 $(date +%s) 0" >> "{LIVE}"; shift; done; exit 0;;
  kill-session)
    grep -v "^$3 " "{LIVE}" > "{LIVE}.tmp"; mv "{LIVE}.tmp" "{LIVE}"; exit 0;;
  capture-pane) echo "pane $3"; exit 0;;
  display-message) echo "/tmp"; exit 0;;
  *) exit 0;;
esac
""")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
open(CALL_LOG, "w").close()
open(LIVE, "w").close()

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "worktrees")
os.environ["GW_TMUX_INIT_DIR"] = os.path.join(TMP, "tmux-init")
os.environ.pop("GW_TMUX_IDLE_MIN", None)
os.environ.pop("GW_DIRECTIVE_ALLOW_ALL", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
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


def reset_calls():
    open(CALL_LOG, "w").close()


def live_names():
    with open(LIVE) as f:
        return [ln.split()[0] for ln in f if ln.strip()]


def set_live(rows):
    with open(LIVE, "w") as f:
        for r in rows:
            f.write(" ".join(str(x) for x in r) + "\n")


def row(sid):
    with db.get_connection() as c:
        return dict(c.execute("SELECT * FROM tmux_sessions WHERE id = ?", (sid,)).fetchone())


from backend import db, main, mcp_core  # noqa: E402

SID = "gw-qa-agy"
print("[1] DB mới: phiên mặc định hibernated, không mở tmux")
with db.get_connection() as c:
    sts = {r["id"]: r["status"] for r in c.execute("SELECT id, status FROM tmux_sessions").fetchall()}
check("có phiên worker", SID in sts, str(sts))
check("mọi phiên hibernated", set(sts.values()) == {"hibernated"}, str(sts))
check("import db không gọi tmux", calls() == [], str(calls()))
check("tmux_idle_min mặc định 15", db.tmux_idle_min() == 15.0, str(db.tmux_idle_min()))
db.init_db()
check("init_db chạy lại không lỗi, có cột last_activity_at", "last_activity_at" in row(SID), str(row(SID).keys()))

print("[2] ensure_tmux_session_live mở phiên đang ngủ")
reset_calls()
t0 = int(time.time())
r = db.ensure_tmux_session_live(SID)
check("action = wake", r.get("action") == "wake", str(r))
check("new-session đúng phiên", any(c.startswith("new-session") and f"-s {SID}" in c for c in calls()), str(calls()))
rw = row(SID)
check("status active", rw["status"] == "active", rw["status"])
check("last_activity_at được ghi", rw["last_activity_at"] >= t0, str(rw["last_activity_at"]))
reset_calls()
r = db.ensure_tmux_session_live(SID)
check("phiên đang chạy → không khởi động lại", r.get("action") == "none" and not any(c.startswith(("new-session", "kill-session")) for c in calls()), str(calls()))

print("[3] reap_idle_tmux_sessions")
now = int(time.time())
set_live([(SID, now - 60, 0)])
with db.get_connection() as c:
    c.execute("UPDATE tmux_sessions SET last_activity_at = ? WHERE id = ?", (now - 60, SID))
    c.commit()
res = db.reap_idle_tmux_sessions(idle_min=15, now=now)
check("rảnh 60s < 15 phút → giữ", SID not in res["hibernated"] and row(SID)["status"] == "active", str(res))
res = db.reap_idle_tmux_sessions(idle_min=15, now=now + 15 * 60)
check("rảnh ≥ 15 phút → hibernate", SID in res["hibernated"], str(res))
check("tmux kill-session đã chạy", SID not in live_names(), str(live_names()))
check("status hibernated, pid 0", row(SID)["status"] == "hibernated" and row(SID)["pid"] == 0, str(row(SID)["status"]))

# tmux activity mới hơn last_activity_at → giữ
db.ensure_tmux_session_live(SID)
with db.get_connection() as c:
    c.execute("UPDATE tmux_sessions SET last_activity_at = ? WHERE id = ?", (now - 3600, SID))
    c.commit()
set_live([(SID, now - 30, 0)])
res = db.reap_idle_tmux_sessions(idle_min=15, now=now)
check("pane vừa có output (session_activity mới) → giữ", SID not in res["hibernated"], str(res))

set_live([(SID, now - 3600, 1)])
res = db.reap_idle_tmux_sessions(idle_min=15, now=now)
check("đang có người attach → giữ", SID not in res["hibernated"] and res["kept"].get(SID) == "attached", str(res))

set_live([(SID, now - 3600, 0)])
did = db.start_dispatch_log(SID, kind="tmux", channel_id="tmux", task_id="", command="agy -p x")
res = db.reap_idle_tmux_sessions(idle_min=15, now=now)
check("đang có dispatch tmux running → giữ", SID not in res["hibernated"] and res["kept"].get(SID) == "dispatch_running", str(res))
with db.get_connection() as c:
    c.execute("UPDATE dispatch_log SET status = 'done' WHERE id = ?", (did,))
    c.commit()

res = db.reap_idle_tmux_sessions(idle_min=0, now=now + 10 ** 6)
check("idle_min = 0 → tắt tự hibernate", res["hibernated"] == [] and row(SID)["status"] == "active", str(res))

set_live([])
res = db.reap_idle_tmux_sessions(idle_min=15, now=now)
check("DB active nhưng tmux không chạy → hibernated", SID in res["marked_stopped"] and row(SID)["status"] == "hibernated", str(res))

print("[4] HTTP + MCP: gửi lệnh vào phiên đang ngủ → mở trước")
server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


reset_calls()
st, res = call("POST", "/api/tmux/send", {"session_id": SID, "command": "gw-status"})
cs = calls()
i_new = next((i for i, c in enumerate(cs) if c.startswith("new-session")), -1)
i_send = next((i for i, c in enumerate(cs) if c.startswith("send-keys")), -1)
check("/api/tmux/send 200", st == 200, f"{st} {res}")
check("mở phiên (new-session) trước send-keys", 0 <= i_new < i_send, str(cs))
check("status active sau khi gửi", row(SID)["status"] == "active", row(SID)["status"])

reset_calls()
st, res = call("POST", "/api/tmux/send", {"session_id": "gw-backend-agy", "command": "rm -rf /"})
check("lệnh bị allowlist chặn → 403 và KHÔNG mở phiên", st == 403 and not any(c.startswith("new-session") for c in calls()), f"{st} {calls()}")

db.hibernate_tmux_session(SID)
reset_calls()
r = mcp_core.execute_tool("send_worker_directive", {"session_id": SID, "command": "gw-status"})
cs = calls()
check("MCP send_worker_directive mở phiên rồi gửi", not r["isError"] and any(c.startswith("new-session") for c in cs) and any(c.startswith("send-keys") for c in cs), str(cs))

reset_calls()
st, res = call("POST", "/api/tmux/action", {"session_id": SID, "action": "open"})
check("action open trên phiên đang chạy: không kill / new-session", st == 200 and not any(c.startswith(("kill-session", "new-session")) for c in calls()), f"{st} {calls()}")
db.hibernate_tmux_session(SID)
reset_calls()
st, res = call("POST", "/api/tmux/action", {"session_id": SID, "action": "open"})
check("action open trên phiên ngủ: mở phiên", st == 200 and any(c.startswith("new-session") for c in calls()) and row(SID)["status"] == "active", f"{st} {calls()}")

st, res = call("GET", "/api/tmux/sessions")
check("/api/tmux/sessions có idle_min", st == 200 and res.get("idle_min") == 15.0, str(res.get("idle_min")))
qa = next(s for s in res["sessions"] if s["id"] == SID)
check("phiên có last_activity_at", qa.get("last_activity_at", 0) > 0, str(qa.get("last_activity_at")))

print("[5] DB cũ thiếu cột last_activity_at → init_db thêm cột, dữ liệu giữ nguyên")
old_db = os.path.join(TMP, "old.db")
con = sqlite3.connect(old_db)
con.execute("CREATE TABLE tmux_sessions (id TEXT PRIMARY KEY, project_id TEXT, role_name TEXT, cli_tool TEXT, account_type TEXT, account_label TEXT, profile_dir TEXT, status TEXT, pid INTEGER, cwd TEXT, terminal_output TEXT, updated_at TIMESTAMP)")
con.execute("INSERT INTO tmux_sessions (id, status) VALUES ('gw-cu', 'active')")
con.commit()
con.close()
_orig = db.DB_PATH
db.DB_PATH = old_db
try:
    db.init_db()
    db.init_db()
    con = sqlite3.connect(old_db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tmux_sessions)").fetchall()]
    st_cu = con.execute("SELECT status FROM tmux_sessions WHERE id = 'gw-cu'").fetchone()[0]
    con.close()
    check("có cột last_activity_at", "last_activity_at" in cols, str(cols))
    check("dòng cũ giữ nguyên status", st_cu == "active", st_cu)
except Exception as e:
    check("init_db trên DB cũ", False, repr(e))
finally:
    db.DB_PATH = _orig

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)

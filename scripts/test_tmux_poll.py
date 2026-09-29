#!/usr/bin/env python3
"""
Test #30: vòng poll GET /api/tmux/sessions không được chạy tmux hàng loạt.
Chạy không cần server ngoài/tmux thật: tmux giả trên PATH ghi lại MỌI lần bị gọi (1 dòng / subprocess),
list-sessions trả đủ các phiên worker như đang chạy thật.
- Import db (seed_tmux_sessions) chỉ upsert DB, KHÔNG gọi tmux.
- GET /api/tmux/sessions: đúng 1 subprocess tmux (list-sessions), không ensure / capture-pane từng phiên.
- GET /api/tmux/sessions?output=<sid>: thêm đúng 1 capture-pane cho phiên đó; terminal_output lấy từ pane thật.
- MCP list_swarm_workers cũng chỉ 1 subprocess tmux.
- pid / tmux_live lấy từ list-sessions; phiên không có trong list-sessions → tmux_live = False.
- start_tmux_session(sid) mở đúng 1 phiên (new-session), không đụng phiên khác.
In số liệu "subprocess tmux / lượt poll" để ghi vào PR.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-tmuxpoll-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
CALL_LOG = os.path.join(TMP, "tmux-calls.log")
LIVE_FILE = os.path.join(TMP, "tmux-live.txt")   # tên phiên "đang chạy" mà tmux giả báo về
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write(f"""#!/bin/sh
echo "$*" >> "{CALL_LOG}"
case "$1" in
  list-sessions)
    i=4000
    while read -r s; do
      [ -z "$s" ] && continue
      i=$((i+1))
      case "$*" in *pane_pid*) echo "$s $i";; *) echo "$s";; esac
    done < "{LIVE_FILE}"
    exit 0;;
  capture-pane) echo "pane-that-cua $3"; exit 0;;
  list-panes) echo 4321; exit 0;;
  new-session)
    while [ $# -gt 0 ]; do [ "$1" = "-s" ] && echo "$2" >> "{LIVE_FILE}"; shift; done
    exit 0;;
  *) exit 0;;
esac
""")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
open(CALL_LOG, "w").close()

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "worktrees")
os.environ["GW_TMUX_INIT_DIR"] = os.path.join(TMP, "tmux-init")
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


# Chưa có phiên nào sống lúc import
open(LIVE_FILE, "w").close()
from backend import db, main, mcp_core  # noqa: E402

print("[1] import db / seed_tmux_sessions chỉ ghi DB")
check("import db không gọi tmux", calls() == [], str(calls()[:5]))
with db.get_connection() as c:
    sids = [r["id"] for r in c.execute("SELECT id FROM tmux_sessions ORDER BY id").fetchall()]
check("DB đã có các phiên worker", len(sids) >= 1, str(sids))
reset_calls()
db.seed_tmux_sessions()
check("seed_tmux_sessions() gọi lại cũng không gọi tmux", calls() == [], str(calls()[:5]))

# Từ đây coi như mọi phiên worker đang chạy thật
with open(LIVE_FILE, "w") as f:
    f.write("\n".join(sids) + "\n")

server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.status, json.loads(r.read().decode())


print("[2] GET /api/tmux/sessions (lượt poll thường)")
reset_calls()
st, res = get("/api/tmux/sessions?project=gen-workplace")
poll_calls = calls()
check("200", st == 200, str(st))
check("đủ phiên", len(res.get("sessions", [])) == len(sids), str(len(res.get("sessions", []))))
check("đúng 1 subprocess tmux (list-sessions)", len(poll_calls) == 1 and poll_calls[0].startswith("list-sessions"), str(poll_calls))
check("không capture-pane / resize-window / new-session", not any(c.split()[0] in ("capture-pane", "resize-window", "new-session", "list-panes") for c in poll_calls), str(poll_calls))
s0 = res["sessions"][0]
check("tmux_live = True khi list-sessions có phiên", s0.get("tmux_live") is True, str(s0.get("tmux_live")))
check("pid lấy từ list-sessions", isinstance(s0.get("pid"), int) and s0["pid"] > 4000, str(s0.get("pid")))

print("[3] GET /api/tmux/sessions?output=<sid> (phiên đang chọn ở màn Worker & terminal)")
target = "gw-qa-agy" if "gw-qa-agy" in sids else sids[0]
reset_calls()
st, res = get(f"/api/tmux/sessions?project=gen-workplace&output={target}")
sel_calls = calls()
check("200", st == 200, str(st))
check("đúng 2 subprocess tmux (list-sessions + 1 capture-pane)", len(sel_calls) == 2, str(sel_calls))
check("capture-pane đúng phiên được hỏi", sum(1 for c in sel_calls if c.startswith("capture-pane") and target in c) == 1, str(sel_calls))
cur = next(s for s in res["sessions"] if s["id"] == target)
check("terminal_output của phiên được hỏi lấy từ pane", "pane-that-cua" in (cur.get("terminal_output") or ""), str(cur.get("terminal_output"))[:120])

print("[4] output=<sid> không sống → không capture")
with open(LIVE_FILE, "w") as f:
    f.write("\n".join(s for s in sids if s != target) + "\n")
reset_calls()
st, res = get(f"/api/tmux/sessions?output={target}")
c4 = calls()
check("chỉ list-sessions", len(c4) == 1, str(c4))
cur = next(s for s in res["sessions"] if s["id"] == target)
check("phiên không sống → tmux_live False", cur.get("tmux_live") is False, str(cur.get("tmux_live")))
with open(LIVE_FILE, "w") as f:
    f.write("\n".join(sids) + "\n")

print("[5] MCP list_swarm_workers / resource swarm/workers")
reset_calls()
r = mcp_core.execute_tool("list_swarm_workers", {})
check("list_swarm_workers ok", not r["isError"], str(r)[:200])
check("list_swarm_workers: 1 subprocess tmux", len(calls()) == 1, str(calls()))

print("[6] start_tmux_session chỉ mở đúng 1 phiên")
open(LIVE_FILE, "w").close()
reset_calls()
out = db.start_tmux_session(target)
c6 = calls()
news = [c for c in c6 if c.startswith("new-session")]
check("đúng 1 new-session", len(news) == 1 and f"-s {target}" in news[0], str(c6))
others = [x for x in sids if x != target]
check("không đụng phiên khác", not any(o in c.split() for c in c6 for o in others), str(c6))
check("trả status active", isinstance(out, dict) and out.get("status") == "active", str(out))

print(f"\nSỐ LIỆU: subprocess tmux / lượt poll = {len(poll_calls)} (thường), {len(sel_calls)} (kèm output phiên đang chọn)")
server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)

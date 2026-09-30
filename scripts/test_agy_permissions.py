#!/usr/bin/env python3
"""
Test Issue #7: quyền agy trong tmux + worktree riêng cho từng vai; quy tắc chỉ đọc cho war-room (--mode plan -p).
Chạy không cần agy thật. tmux THẬT nếu máy có (socket riêng qua TMUX_TMPDIR, không đụng phiên đang chạy).
- Alias agy/agy-run mặc định KHÔNG có --dangerously-skip-permissions; chỉ agy-run của vai trong GW_AGY_WRITE_ROLES
  VÀ đang ở worktree riêng của vai mới có cờ.
- ensure_tmux_session_live / wake_tmux_session mở phiên trong <GW_WORKTREE_ROOT>/<sid> trên nhánh wt/<sid>;
  gw-status in CWD + Branch.
- directive_guard từ chối lệnh agy kèm --dangerously-skip-permissions.
- Dispatch war-room: vẫn `agy --gemini_dir=... --mode plan -p` (không --sandbox, không skip-permissions mặc định);
  thêm read_file(<worktree>) + command(git log|grep|...) vào permissions.allow của hồ sơ; giữ khóa cũ; idempotent.
  GW_WARROOM_SKIP_PERMISSIONS=1 → thêm cờ nhưng chỉ khi cwd là worktree của vai.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-perm-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write('#!/bin/bash\necho "OK agy giả"; echo "cwd=$(pwd)"; echo "args=$*"; exit 0\n')
os.chmod(AGY, 0o755)

REPO = os.path.join(TMP, "repo")
os.makedirs(REPO)
GIT = ["git", "-C", REPO, "-c", "user.name=test", "-c", "user.email=test@example.com"]
subprocess.run(GIT + ["init", "-q"], check=True)
with open(os.path.join(REPO, "README.md"), "w") as f:
    f.write("repo tạm\n")
subprocess.run(GIT + ["add", "."], check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "init"], check=True)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY
os.environ["GW_DISPATCH_REPO"] = REPO
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "gw-worktrees")
os.environ["TMUX_TMPDIR"] = os.path.join(TMP, "tmux")
os.environ["GW_TMUX_INIT_DIR"] = os.path.join(TMP, "tmux-init")
os.environ.pop("TMUX", None)
for k in ("GW_AGY_WRITE_ROLES", "GW_WARROOM_SKIP_PERMISSIONS", "GW_AGY_PLAN_ALLOW", "GW_DIRECTIVE_ALLOW_ALL"):
    os.environ.pop(k, None)
for d in (os.environ["DATA_DIR"], os.environ["HOME"], os.environ["TMUX_TMPDIR"]):
    os.makedirs(d)
PROFILE = os.path.join(os.environ["HOME"], ".gemini")
os.makedirs(os.path.join(PROFILE, "antigravity-cli"))
SETTINGS = os.path.join(PROFILE, "antigravity-cli", "settings.json")
with open(SETTINGS, "w") as f:
    json.dump({"theme": "terminal", "permissions": {"allow": ["read_url(github.com)"]}}, f)
sys.path.insert(0, ROOT)

from backend import db, directive_guard  # noqa: E402

db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: None
FLAG = "--dangerously-skip-permissions"
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


WT_ROOT = os.environ["GW_WORKTREE_ROOT"]

print("[1] alias mặc định không có skip-permissions")
wt_qa = db.ensure_role_worktree("gw-qa-agy")
check("worktree gw-qa-agy nằm dưới GW_WORKTREE_ROOT", os.path.realpath(wt_qa) == os.path.realpath(os.path.join(WT_ROOT, "gw-qa-agy")), wt_qa)
check("is_role_worktree(worktree) = True, repo = False", db.is_role_worktree("gw-qa-agy", wt_qa) and not db.is_role_worktree("gw-qa-agy", REPO))
aliases, write = db.build_agy_aliases("gw-qa-agy", PROFILE, "conv-q", wt_qa)
check("không vai nào cấu hình → không có cờ", not write and all(FLAG not in a for a in aliases), str(aliases))
block = db.tmux_role_env_block("gw-qa-agy", "QA", "conv-q", PROFILE, "/x/spec.md", "a@b", wt_qa)
check("script khởi tạo tmux không có cờ", FLAG not in block, block)

print("[2] GW_AGY_WRITE_ROLES=qa → chỉ agy-run của qa, chỉ trong worktree")
os.environ["GW_AGY_WRITE_ROLES"] = "qa"
aliases, write = db.build_agy_aliases("gw-qa-agy", PROFILE, "conv-q", wt_qa)
check("agy-run của qa có cờ", write and FLAG in aliases[1], str(aliases))
check("alias agy thường của qa vẫn không có cờ", FLAG not in aliases[0], aliases[0])
aliases, write = db.build_agy_aliases("gw-qa-agy", PROFILE, "conv-q", REPO)
check("qa nhưng cwd là repo app → không cờ", not write and all(FLAG not in a for a in aliases), str(aliases))
wt_be = db.ensure_role_worktree("gw-backend-agy")
aliases, write = db.build_agy_aliases("gw-backend-agy", PROFILE, "conv-b", wt_be)
check("vai khác (backend) → không cờ", not write and all(FLAG not in a for a in aliases), str(aliases))
aliases, write = db.build_agy_aliases("gw-backend-agy", PROFILE, "conv-b", wt_qa)
check("backend nhưng cwd là worktree của qa → không cờ", not write)
os.environ["GW_AGY_WRITE_ROLES"] = "gw-backend-agy, devops"
check("nhận cả session_id lẫn tên ngắn", db.role_may_skip_permissions("gw-backend-agy") and db.role_may_skip_permissions("gw-devops-agy")
      and not db.role_may_skip_permissions("gw-qa-agy"))
os.environ.pop("GW_AGY_WRITE_ROLES")

print("[3] directive_guard từ chối cờ skip-permissions")
for cmd in (f"agy {FLAG} -p 'x'", f"agy-run {FLAG}", f"clear; agy --mode plan {FLAG} -p 'x' 2>&1 | tee ~/gw-reports/a.md"):
    ok, reason = directive_guard.guard("gw-qa-agy", cmd)
    check(f"từ chối: {cmd[:50]}", not ok and "GW_AGY_WRITE_ROLES" in reason, reason)
ok, _ = directive_guard.guard("gw-qa-agy", "agy --mode plan -p 'x'")
check("lệnh agy thường vẫn được nhận", ok)

print("[4] war-room [đọc] (mode=review): --mode plan -p như cũ + quy tắc chỉ đọc trong settings hồ sơ")
with db.get_connection() as conn:  # gw-qa-agy dùng hồ sơ owner_default (~/.gemini) cho test
    conn.execute("UPDATE tmux_sessions SET account_type = 'owner_default', profile_dir = '' WHERE id = 'gw-qa-agy'")
    conn.commit()
res = db.post_warroom_message(message="@qa [đọc] liệt kê test", author="Ryan (Owner)", wait=True)
with db.get_connection() as conn:
    row = conn.execute("SELECT command, status FROM dispatch_log WHERE id = ?", (res["dispatches"][0]["dispatch_id"],)).fetchone()
cmd = row["command"]
check("lệnh [đọc] là agy --gemini_dir=... --mode plan -p", f"--gemini_dir={PROFILE}" in cmd and "--mode plan -p" in cmd, cmd[:200])
check("không --sandbox, không skip-permissions", "--sandbox" not in cmd and FLAG not in cmd, cmd[:200])
check("dispatch done", row["status"] == "done", row["status"])
data = json.load(open(SETTINGS))
allow = data["permissions"]["allow"]
check("giữ khóa cũ (theme, rule cũ)", data.get("theme") == "terminal" and "read_url(github.com)" in allow, str(data)[:200])
check("thêm read_file(<worktree qa>)", f"read_file({os.path.realpath(wt_qa)})" in allow, str(allow))
check("thêm command(git log), command(grep), command(ls)", all(f"command({c})" in allow for c in ("git log", "grep", "ls")), str(allow))
check("không cấp lệnh ghi/xóa", not any(r.startswith(("command(rm", "command(git push", "command(git commit", "write_file")) for r in allow), str(allow))
n = len(allow)
db.post_warroom_message(message="@qa [đọc] lần 2", author="Ryan (Owner)", wait=True)
check("idempotent: gọi lần 2 không nhân đôi quy tắc", len(json.load(open(SETTINGS))["permissions"]["allow"]) == n)
check("ensure_agy_plan_permissions lần 2 trả []", db.ensure_agy_plan_permissions(PROFILE, wt_qa) == [])
bad = os.path.join(TMP, "bad-profile")
os.makedirs(os.path.join(bad, "antigravity-cli"))
with open(os.path.join(bad, "antigravity-cli", "settings.json"), "w") as f:
    f.write("{ hỏng")
check("settings hỏng → không đụng", db.ensure_agy_plan_permissions(bad, wt_qa) == [] and open(os.path.join(bad, "antigravity-cli", "settings.json")).read() == "{ hỏng")
os.environ["GW_AGY_PLAN_ALLOW"] = "0"
other = os.path.join(TMP, "other-profile")
os.makedirs(other)
check("GW_AGY_PLAN_ALLOW=0 → không ghi", db.ensure_agy_plan_permissions(other, wt_qa) == [] and not os.path.exists(os.path.join(other, "antigravity-cli")))
os.environ.pop("GW_AGY_PLAN_ALLOW")

print("[5] GW_WARROOM_SKIP_PERMISSIONS=1 (lối thoát cuối) chỉ thêm cờ khi ở worktree của vai (chế độ review)")
os.environ["GW_WARROOM_SKIP_PERMISSIONS"] = "1"
res = db.post_warroom_message(message="@qa [đọc] lần 3", author="Ryan (Owner)", wait=True)
with db.get_connection() as conn:
    cmd = conn.execute("SELECT command FROM dispatch_log WHERE id = ?", (res["dispatches"][0]["dispatch_id"],)).fetchone()["command"]
check("có cờ + vẫn --mode plan", FLAG in cmd and "--mode plan -p" in cmd, cmd[:200])
_orig = db.ensure_role_worktree
db.ensure_role_worktree = lambda sid: REPO  # giả lập git worktree lỗi → rơi về repo app
res = db.post_warroom_message(message="@lead [đọc] lần 4", author="Ryan (Owner)", wait=True)
db.ensure_role_worktree = _orig
with db.get_connection() as conn:
    cmd = conn.execute("SELECT command FROM dispatch_log WHERE id = ?", (res["dispatches"][0]["dispatch_id"],)).fetchone()["command"]
check("cwd rơi về repo app → KHÔNG thêm cờ", FLAG not in cmd, cmd[:200])
os.environ.pop("GW_WARROOM_SKIP_PERMISSIONS")

print("[6] tmux thật: phiên mở trong worktree, gw-status in CWD/Branch, không skip-permissions")
if not shutil.which("tmux"):
    print("  (bỏ qua: máy không có tmux)")
else:
    os.environ["GW_AGY_WRITE_ROLES"] = "backend"
    # Phiên mặc định hibernated, chỉ mở khi cần (#32) → mở từng vai theo nhu cầu với cấu hình mới
    subprocess.run(["tmux", "kill-server"], capture_output=True)
    time.sleep(0.2)
    for _sid in db.SWARM_SESSION_IDS:
        db.ensure_tmux_session_live(_sid)
    time.sleep(0.5)

    def pane_path(sid):
        r = subprocess.run(["tmux", "display-message", "-p", "-t", sid, "#{pane_current_path}"], capture_output=True, text=True)
        return r.stdout.strip()

    qa_path = pane_path("gw-qa-agy")
    check("gw-qa-agy mở trong worktree riêng", os.path.realpath(qa_path) == os.path.realpath(os.path.join(WT_ROOT, "gw-qa-agy")), qa_path)
    br = subprocess.run(["git", "-C", qa_path, "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    check("nhánh wt/gw-qa-agy", br == "wt/gw-qa-agy", br)
    for sid in db.SWARM_SESSION_IDS:
        p = pane_path(sid)
        if os.path.realpath(p) != os.path.realpath(os.path.join(WT_ROOT, sid)):
            check(f"{sid} mở trong worktree", False, p)
    check("mọi vai mở trong worktree của mình", all(os.path.realpath(pane_path(s)) == os.path.realpath(os.path.join(WT_ROOT, s)) for s in db.SWARM_SESSION_IDS))
    # Script init nằm trong thư mục riêng của lần chạy (GW_TMUX_INIT_DIR), không phải /tmp chung → không bị tiến trình khác ghi đè
    qa_path_init = db.tmux_init_script_path("gw-qa-agy")
    check("script init nằm trong GW_TMUX_INIT_DIR của lần chạy", qa_path_init.startswith(os.environ["GW_TMUX_INIT_DIR"]), qa_path_init)
    qa_init = open(qa_path_init).read()
    be_init = open(db.tmux_init_script_path("gw-backend-agy")).read()
    check("init script gw-qa-agy không có skip-permissions", FLAG not in qa_init, qa_init[-600:])
    be_alias = [ln for ln in be_init.splitlines() if ln.startswith("alias agy")]
    check("gw-backend-agy (được cấu hình): chỉ agy-run có cờ", len(be_alias) == 2 and FLAG not in be_alias[0] and FLAG in be_alias[1], str(be_alias))
    subprocess.run(["tmux", "send-keys", "-t", "gw-qa-agy", "gw-status", "Enter"])
    time.sleep(0.8)
    out = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-S", "-", "-t", "gw-qa-agy"], capture_output=True, text=True).stdout
    check("gw-status in CWD = worktree", f"CWD: {os.path.join(WT_ROOT, 'gw-qa-agy')}" in out or f"CWD: {os.path.realpath(os.path.join(WT_ROOT, 'gw-qa-agy'))}" in out, out[-400:])
    check("gw-status in Branch: wt/gw-qa-agy", "Branch: wt/gw-qa-agy" in out, out[-400:])
    subprocess.run(["tmux", "send-keys", "-t", "gw-qa-agy", "alias agy agy-run", "Enter"])
    time.sleep(0.5)
    out = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-S", "-", "-t", "gw-qa-agy"], capture_output=True, text=True).stdout
    check("alias thật trong bash của qa không có cờ", "alias agy-run=" in out and FLAG not in out.split("alias agy agy-run")[-1], out[-400:])
    db.hibernate_tmux_session("gw-qa-agy")
    db.wake_tmux_session("gw-qa-agy")
    time.sleep(0.4)
    check("wake_tmux_session mở lại trong worktree", os.path.realpath(pane_path("gw-qa-agy")) == os.path.realpath(os.path.join(WT_ROOT, "gw-qa-agy")), pane_path("gw-qa-agy"))
    subprocess.run(["tmux", "kill-server"], capture_output=True)
    os.environ.pop("GW_AGY_WRITE_ROLES")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

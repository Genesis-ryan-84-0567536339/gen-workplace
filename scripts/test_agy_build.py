#!/usr/bin/env python3
"""
Test chế độ "Làm" (build) của worker agy (Issue #45). Chạy không cần agy / tmux / GitHub thật:
agy giả (Python, chọn kịch bản bằng FAKE_AGY_MODE) tạo file + commit trong cwd; repo dispatch tạm có remote giả (bare repo tạm);
GW_BUILD_GITHUB_REPO cho link compare; GitHub API giả (http.server loopback) cho PR nháp khi có GITHUB_TOKEN.
Kiểm:
- build: worktree <GW_WORKTREE_ROOT>/TSK-n nhánh wt/TSK-n từ origin/main, agy chạy trong worktree, commit mới, app chạy py_compile +
  test, push nhánh lên remote giả, ghi nhánh / SHA / test / compare vào dispatch_log, tin war-room, phiên của task.
- lệnh agy KHÔNG có --mode plan, KHÔNG có --model, KHÔNG --dangerously-skip-permissions; prompt có SOP + THÔNG TIN VIỆC.
- allow / deny chế độ Làm ghi đúng lúc chạy (mô phỏng bộ khớp agy), gỡ sau khi chạy, không chép sang hồ sơ khác.
- review (mode="review") và war-room vẫn --mode plan; API mặc định build; mode sai → 400.
- agy commit trên main / push → bị hook chặn; lách hook (--no-verify) → app phát hiện, failed, không push.
- không commit → failed; test hỏng → vẫn push, ghi FAIL; giao lại khi đang chạy → busy.
- verify_evidence_ref nhận commit trong worktree; complete_task bằng SHA của lần build của người giữ; dọn worktree khi done / xóa task.
"""
import http.server
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-agy-build-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

ARGS_LOG = os.path.join(TMP, "args.log")
PROMPT_LOG = os.path.join(TMP, "prompts.log")
RUN_LOG = os.path.join(TMP, "run.log")
REPO = os.path.join(TMP, "repo")
REMOTE = os.path.join(TMP, "remote.git")
HOME = os.path.join(TMP, "home")
SETTINGS = os.path.join(HOME, ".gemini", "antigravity-cli", "settings.json")

AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import json, os, re, subprocess, sys, time
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
open({ARGS_LOG!r}, "a").write(json.dumps(args) + "\\n")
open({PROMPT_LOG!r}, "a").write(prompt + "\\n===\\n")
mode = os.environ.get("FAKE_AGY_MODE", "ok")
log = open({RUN_LOG!r}, "a")
try:
    perms = json.load(open({SETTINGS!r})).get("permissions", {{}})
except Exception:
    perms = {{}}
log.write(json.dumps({{"cwd": os.getcwd(), "env_hooks": os.environ.get("GIT_CONFIG_VALUE_0", ""),
                      "allow": perms.get("allow", []), "deny": perms.get("deny", [])}}) + "\\n")
m = re.search(r"THÔNG TIN VIỆC (TSK-[0-9]+)", prompt)
tid = m.group(1) if m else "TSK-0"
def ev(o):
    print(json.dumps(o, ensure_ascii=False))
def tool(i, cmd, error=None):
    ti = {{"name": "run_command", "parameters": {{"CommandLine": cmd}}}}
    if error:
        ti["error"] = error
    ev({{"event": "step_update", "step_update": {{"step_index": i, "state": "DONE", "step_type": "tool", "tool_name": "run_command", "tool_info": ti}}}})
def git(*a):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    log.write(json.dumps({{"git": list(a), "rc": r.returncode, "err": r.stderr[-300:]}}) + "\\n")
    return r
if mode == "slow":
    time.sleep(2)
if mode in ("ok", "slow", "push", "failtest", "bypass"):
    os.makedirs("backend", exist_ok=True)
    with open("backend/feature.py", "w") as fh:
        fh.write("def tinh_nang():\\n    return 'agy làm %s'\\n" % time.time())
    files = ["backend/feature.py"]
    if mode == "failtest":
        with open("scripts/test_hong.py", "w") as fh:
            fh.write("import sys\\nprint('test hỏng cố ý')\\nsys.exit(1)\\n")
        files.append("scripts/test_hong.py")
    git("add", *files)
    git("commit", "-q", "-m", tid + ": thêm tinh_nang")
    tool(1, "git add backend/feature.py")
    tool(2, "git commit -m '" + tid + ": thêm tinh_nang'")
if mode == "push":
    git("push", "origin", "HEAD:refs/heads/wt/agy-tu-push")
    git("-C", os.environ["FAKE_REPO"], "commit", "--allow-empty", "-q", "-m", "agy commit lên main")
    tool(3, "git push origin HEAD", error={{"type": "PERMISSION_DENIED", "message": "auto-denied: permission required"}})
if mode == "bypass":
    git("-C", os.environ["FAKE_REPO"], "commit", "--allow-empty", "--no-verify", "-q", "-m", "agy lách hook commit lên main")
if mode == "nocommit":
    with open("README.md", "a") as fh:
        fh.write("sửa mà không commit\\n")
    with open("backend/app.py", "a") as fh:
        fh.write("Y = 2\\n")
ev({{"event": "result", "result": {{"status": "SUCCESS", "response": "Đã đổi gì: backend/feature.py\\nTest: py_compile pass\\n"
     "Rủi ro: thấp\\n[KANBAN_UPDATE: " + tid + " | CHECK: chk-a]"}}}})
sys.exit(0)
''')
os.chmod(AGY, 0o755)


def sh(*a, cwd=None):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True)


GITC = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
sh("git", "init", "-q", "--bare", "-b", "main", REMOTE)
os.makedirs(os.path.join(REPO, "scripts"))
os.makedirs(os.path.join(REPO, "backend"))
sh("git", "init", "-q", "-b", "main", REPO)
with open(os.path.join(REPO, "README.md"), "w") as f:
    f.write("repo tạm\n")
with open(os.path.join(REPO, "backend", "app.py"), "w") as f:
    f.write("X = 1\n")
with open(os.path.join(REPO, "scripts", "test_ok.py"), "w") as f:
    f.write("import sys\nprint('ok')\nsys.exit(0)\n")
with open(os.path.join(REPO, "scripts", "test_mcp_suite.py"), "w") as f:
    f.write("import sys\nsys.exit(1)\n")   # bị loại trừ mặc định
sh(*GITC, "add", ".", cwd=REPO)
sh(*GITC, "commit", "-q", "-m", "init", cwd=REPO)
sh("git", "remote", "add", "origin", REMOTE, cwd=REPO)
sh("git", "push", "-q", "origin", "main", cwd=REPO)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = HOME
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY
os.environ["GW_DISPATCH_REPO"] = REPO
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "gw-worktrees")
os.environ["GW_TMUX_INIT_DIR"] = os.path.join(TMP, "tmux-init")
os.environ["GW_BUILD_GITHUB_REPO"] = "chu-so-huu/gen-workplace"
os.environ["FAKE_REPO"] = REPO
for k in ("GW_AGY_WRITE_ROLES", "GW_WARROOM_SKIP_PERMISSIONS", "GW_AGY_PLAN_ALLOW", "GW_AGY_NO_STREAM", "FAKE_AGY_MODE",
          "GITHUB_TOKEN", "GW_GITHUB_API_URL", "GW_BUILD_TEST_EXCLUDE", "GW_BUILD_BASE", "GW_PUBLIC_ORIGIN"):
    os.environ.pop(k, None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.path.dirname(SETTINGS))
with open(SETTINGS, "w") as f:
    json.dump({"theme": "terminal", "permissions": {"allow": ["read_url(github.com)"], "deny": ["command(sudo)"]}}, f)
sys.path.insert(0, ROOT)

from backend import db, agy_build, main  # noqa: E402

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


# Mô phỏng bộ khớp quyền của agy (như test_agy_readonly_dispatch): command(...) theo tiền tố token / regex neo từng token,
# read_file / write_file theo tiền tố đường dẫn; Deny > Allow; pipeline / && / ; xét từng lệnh.
def _rule_match(rule, tokens):
    body = rule[len("command("):-1]
    if body.startswith("regex:"):
        pats = body[len("regex:"):].split()
        return len(tokens) >= len(pats) and all(re.fullmatch(p, t) for p, t in zip(pats, tokens))
    pats = body.split()
    return tokens[:len(pats)] == pats


def _segments(line):
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    seg, out = [], []
    for tok in lex:
        if tok in ("|", "&&", "||", ";"):
            out.append(seg)
            seg = []
        else:
            seg.append(tok)
    out.append(seg)
    return [s for s in out if s]


def agy_allows(line, allow, deny):
    for seg in _segments(line):
        if any(r.startswith("command(") and _rule_match(r, seg) for r in deny):
            return False
        if not any(r.startswith("command(") and _rule_match(r, seg) for r in allow):
            return False
    return True


def _path_rule(rule, action, path):
    if not rule.startswith(action + "("):
        return False
    d = rule[len(action) + 1:-1]
    return path == d or path.startswith(d.rstrip("/") + "/")


def agy_may_write(path, allow, deny):
    p = os.path.realpath(path)
    if any(_path_rule(r, "write_file", p) for r in deny):
        return False
    return any(_path_rule(r, "write_file", p) for r in allow)


conv = db.create_gen_conversation("PRJ-GEN-WORKPLACE", "VIEC-12: test chế độ Làm")
CONV = conv.get("id") or conv.get("conv_id")
with db.get_connection() as conn:
    conn.execute("UPDATE tmux_sessions SET account_type = 'owner_default', profile_dir = ''")
    conn.commit()


def new_task(title):
    return db.save_gen_session_todo(CONV, None, title, "Thêm hàm tinh_nang", "todo", "high", "Gen Core",
                                    [{"id": "chk-a", "text": "thêm backend/feature.py", "done": False},
                                     {"id": "chk-b", "text": "chạy test", "done": False}], viec_ref="VIEC-12")["id"]


def drow(did):
    with db.get_connection() as conn:
        return dict(conn.execute("SELECT * FROM dispatch_log WHERE id = ?", (did,)).fetchone())


def git_out(*a, cwd=REPO):
    r = sh("git", *a, cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else ""


def remote_branch(branch):
    return git_out("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=REMOTE)


def reset_logs():
    for p in (ARGS_LOG, PROMPT_LOG, RUN_LOG):
        open(p, "w").close()


def run_log():
    return [json.loads(x) for x in open(RUN_LOG).read().splitlines() if x.strip()]


print("[1] Allow / deny chế độ Làm (mô phỏng bộ khớp agy)")
WT_X = os.path.join(os.environ["GW_WORKTREE_ROOT"], "TSK-999")
os.makedirs(WT_X, exist_ok=True)
PDIR = os.path.join(HOME, ".gemini")
ALLOW = db.agy_build_allow_rules(WT_X)
DENY = db.agy_build_deny_rules(WT_X, REPO, PDIR)
wt_real = os.path.realpath(WT_X)
check("allow có read_file + write_file(worktree của việc)", f"read_file({wt_real})" in ALLOW and f"write_file({wt_real})" in ALLOW, str(ALLOW[:3]))
check("allow chỉ 1 write_file (không ghi chỗ khác)", sum(1 for r in ALLOW if r.startswith("write_file(")) == 1)
check("allow giữ mọi lệnh chỉ đọc cũ", all(r in ALLOW for r in db.agy_plan_allow_rules()))
check("không rule wildcard / skip-permissions", "command(*)" not in ALLOW and not any("dangerously" in r for r in ALLOW + DENY))
OK_CMDS = ["python3 -m py_compile backend/app.py backend/feature.py", "python3 scripts/test_agy_build.py",
           "python3 scripts/test_task_hub.py", "git status", "git status --short", "git diff", "git diff --stat HEAD",
           "git add backend/feature.py", "git add -A", 'git commit -m "TSK-1: thêm tinh_nang"', "git log --oneline -3",
           "grep -rn tinh_nang backend", "cd backend && ls", "sed -n 1,20p backend/app.py", "git show HEAD --stat",
           "git branch --show-current", "find scripts -name 'test_*.py'",
           # dispatch:13 (#47): agy nối echo sau lệnh kiểm; cd tới đường dẫn tuyệt đối của worktree
           'python3 -m py_compile backend/db.py && echo "py_compile OK"', "echo xong",
           f"cd {os.path.realpath(WT_X)} && git status", "cd .. && ls"]
for c in OK_CMDS:
    check(f"cho phép: {c}", agy_allows(c, ALLOW, DENY))
BAD_CMDS = ["git push", "git push origin wt/TSK-1", "git push --force origin HEAD:main", "git remote add x http://x",
            "git remote set-url origin x", "git checkout main", "git checkout -b khac", "git checkout origin/main -- .",
            "git switch main", "git reset --hard HEAD~1", "git reset --hard", "git -C /tmp/repo commit -m x",
            "git -c core.hooksPath=/dev/null commit -m x", "git --git-dir=/x/.git log", "git --no-pager -C /x status",
            "git commit --no-verify -m x", "git commit -n -m x", "git commit -nm x", "git commit --amend -m x",
            "git fetch origin", "git pull", "git config user.name x", "git worktree add ../x", "git update-ref refs/heads/main HEAD",
            "git branch -D main", "git branch -f main HEAD", "git clean -fdx",
            "rm -rf backend", "rm -r backend", "rm -f x.py", "rm -Rf .", "rm /etc/passwd", "rm ../repo/README.md", "rm ~/x",
            "curl http://x", "wget http://x", "ssh host", "scp a b:", "rsync -a . host:", "nc -l 80", "gh pr create",
            "sudo ls", "su root", "pip install x", "pip3 install x", "python3 -m pip install x", "npm i x", "npx x",
            "apt-get install x", "apt install x", "python3 -c 'print(1)'", "bash -c ls", "echo x && git push",
            "python3 scripts/../evil.py", "python3 evil.py", "git status && git push",
            "ls | xargs rm", "find . -delete"]
for c in BAD_CMDS:
    check(f"chặn: {c}", not agy_allows(c, ALLOW, DENY))
check("ghi file trong worktree: được", agy_may_write(os.path.join(WT_X, "backend", "a.py"), ALLOW, DENY))
for p in (os.path.join(REPO, "backend", "app.py"), os.path.join(PDIR, "antigravity-cli", "settings.json"),
          os.path.join(HOME, ".ssh", "id_rsa"), "/etc/passwd", os.path.join(TMP, "khac.txt"),
          os.path.join(os.environ["GW_WORKTREE_ROOT"], "gw-qa-agy", "x.py"), os.path.join(HOME, ".gitconfig")):
    check(f"ghi file ngoài worktree bị chặn: {p}", not agy_may_write(p, ALLOW, DENY))
RE2_BAD = re.compile(r"\(\?[=!<]|\\[1-9]")
check("regex hợp RE2 + biên dịch được", not any(RE2_BAD.search(r) for r in ALLOW + DENY)
      and all(re.compile(p) for r in ALLOW + DENY if "regex:" in r for p in r[len("command(regex:"):-1].split()))
check("deny không chặn worktree (không có write_file cha của worktree)", not any(
    r.startswith("write_file(") and (wt_real + "/").startswith(r[len("write_file("):-1].rstrip("/") + "/") for r in DENY))
shutil.rmtree(WT_X)

print("[2] ensure / release allow-deny trên settings của hồ sơ")
tok = db.ensure_agy_build_permissions(PDIR, WT_X, REPO)
data = json.load(open(SETTINGS))
check("ghi allow + deny chế độ Làm", tok and all(r in data["permissions"]["allow"] for r in ALLOW)
      and all(r in data["permissions"]["deny"] for r in DENY), str(tok)[:200])
tok2 = db.ensure_agy_build_permissions(PDIR, WT_X, REPO)
check("lần 2 (build song song) không nhân đôi", len(json.load(open(SETTINGS))["permissions"]["allow"]) == len(data["permissions"]["allow"]))
db.release_agy_build_permissions(tok)
check("còn 1 lần build dùng → chưa gỡ", f"write_file({wt_real})" in json.load(open(SETTINGS))["permissions"]["allow"])
removed = db.release_agy_build_permissions(tok2)
data = json.load(open(SETTINGS))
check("hết lần build → gỡ sạch rule chế độ Làm", removed and f"write_file({wt_real})" not in data["permissions"]["allow"]
      and "command(git commit)" not in data["permissions"]["allow"] and "command(git push)" not in data["permissions"]["deny"], str(data)[:300])
check("giữ rule người dùng (theme, read_url, deny sudo)", data.get("theme") == "terminal" and "read_url(github.com)" in data["permissions"]["allow"]
      and "command(sudo)" in data["permissions"]["deny"])
src, dst = os.path.join(TMP, "src-prof"), os.path.join(TMP, "dst-prof")
for d in (src, dst):
    os.makedirs(os.path.join(d, "antigravity-cli"))
with open(os.path.join(src, "antigravity-cli", "settings.json"), "w") as f:
    json.dump({"permissions": {"allow": [f"write_file({wt_real})", "command(git commit)", "command(make lint)"]}}, f)
check("copy_agy_allow_rules (fallback quota) không chép rule chế độ Làm", db.copy_agy_allow_rules(src, dst) == ["command(make lint)"])
with open(SETTINGS, "w") as f:
    json.dump({"permissions": {"allow": [f"write_file({wt_real})", "command(git add)", "read_url(github.com)"], "deny": []}}, f)
db.ensure_agy_plan_permissions(PDIR, os.path.join(TMP, "repo"))
allow = json.load(open(SETTINGS))["permissions"]["allow"]
check("chế độ Rà soát gỡ rule chế độ Làm còn sót (không có build đang chạy)", f"write_file({wt_real})" not in allow
      and "command(git add)" not in allow and "read_url(github.com)" in allow, str(allow)[:200])

print("[3] Làm: worktree, commit, test, push lên remote giả, compare")
reset_logs()
os.environ["FAKE_AGY_MODE"] = "ok"
T1 = new_task("[THỬ] Làm 1")
main_before = git_out("rev-parse", "main")
res = db.assign_task_to_role(T1, "backend", wait=True)
check("assign mặc định = build", res.get("mode") == "build" and res.get("branch") == f"wt/{T1}", str(res)[:300])
row = drow(res["dispatch_id"])
WT1 = os.path.join(os.environ["GW_WORKTREE_ROOT"], T1)
check("dispatch kind=build, done", row["kind"] == "build" and row["status"] == "done", f"{row['status']} {row['summary'][:400]}")
check("worktree ../gw-worktrees/TSK-n trên nhánh wt/TSK-n", os.path.isdir(WT1) and git_out("symbolic-ref", "--short", "HEAD", cwd=WT1) == f"wt/{T1}")
check("nhánh tạo từ origin/main", git_out("merge-base", "--is-ancestor", "origin/main", f"wt/{T1}") == "" and
      sh("git", "merge-base", "--is-ancestor", "origin/main", f"wt/{T1}", cwd=REPO).returncode == 0)
rl = run_log()
check("agy chạy với cwd = worktree của task", rl and os.path.realpath(rl[0]["cwd"]) == os.path.realpath(WT1), str(rl[:1])[:200])
args = json.loads(open(ARGS_LOG).read().splitlines()[-1])
check("lệnh KHÔNG có --mode plan / --mode", "--mode" not in args and "plan" not in args, str(args)[:200])
check("lệnh KHÔNG có --model", not any(a == "--model" or a.startswith("--model=") for a in args), str(args)[:200])
check("lệnh không có --dangerously-skip-permissions", db.AGY_SKIP_PERMISSIONS_FLAG not in args)
check("lệnh = agy --gemini_dir=<hồ sơ> -p <prompt> --output-format stream-json", args[0].startswith("--gemini_dir=") and args[1] == "-p"
      and args[-2:] == ["--output-format", "stream-json"], str(args[:2]))
check("dispatch_log.command không có --mode / --model", "--mode" not in row["command"] and "--model" not in row["command"], row["command"][:200])
prompt = open(PROMPT_LOG).read()
check("prompt có SOP build (5 bước) + THÔNG TIN VIỆC + viec_ref + checklist", "SOP chế độ \"Làm\"" in prompt and f"THÔNG TIN VIỆC {T1}" in prompt
      and "VIEC-12" in prompt and "(chk-a) thêm backend/feature.py" in prompt and "Báo cáo ngắn" in prompt, prompt[:400])
check("prompt nêu worktree + nhánh + cấm push", WT1 in prompt and f"wt/{T1}" in prompt and "KHÔNG push" in prompt)
check("prompt dặn chạy từng lệnh riêng, không nối && echo", "Chạy TỪNG lệnh riêng" in prompt and "&& echo" in prompt)
check("lúc agy chạy: settings có write_file(worktree) + deny git push", rl and f"write_file({os.path.realpath(WT1)})" in rl[0]["allow"]
      and "command(git push)" in rl[0]["deny"], str(rl[:1])[:300])
check("lúc agy chạy: hook chặn qua GIT_CONFIG (core.hooksPath)", rl and rl[0]["env_hooks"].endswith(os.path.join("agy-build-hooks", T1)))
after = json.load(open(SETTINGS))["permissions"]
check("sau khi chạy: rule chế độ Làm đã gỡ", f"write_file({os.path.realpath(WT1)})" not in after["allow"] and "command(git push)" not in after["deny"])
sha = git_out("rev-parse", f"wt/{T1}")
check("build_commit = HEAD của wt/TSK-n", row["build_commit"] == sha and len(sha) == 40, f"{row['build_commit']} {sha}")
check("commit do vai tạo (tác giả gw-backend-agy (agy))", git_out("log", "-1", "--format=%an", f"wt/{T1}") == "gw-backend-agy (agy)")
check("push: nhánh wt/TSK-n đã lên remote giả, đúng SHA", remote_branch(f"wt/{T1}") == sha, remote_branch(f"wt/{T1}"))
check("main trên remote / local không đổi", remote_branch("main") == main_before and git_out("rev-parse", "main") == main_before)
tests = json.loads(row["build_tests"])
check("test: py_compile ok + test_ok pass, test_mcp_suite bị loại", tests["ok"] and tests["py_compile"]["ok"] and tests["total"] == 1
      and tests["tests"][0]["name"] == "test_ok.py" and "test_mcp_suite.py" in tests["skipped"], str(tests)[:300])
check("không để __pycache__ trong worktree", not any("__pycache__" in dp for dp, _, _ in os.walk(WT1)))
push = json.loads(row["build_push"])
check("build_push ok", push["ok"] and push["sha"] == sha, str(push))
check("compare_url đúng dạng", row["compare_url"] == f"https://github.com/chu-so-huu/gen-workplace/compare/main...wt/{T1}", row["compare_url"])
check("không PR khi thiếu GITHUB_TOKEN", row["pr_url"] == "")
check("summary có nhánh, commit, test, push, compare", all(x in row["summary"] for x in (f"Nhánh: wt/{T1}", f"Commit: {sha}", "Test: PASS", "Push: đã đẩy", "So sánh: https://github.com/")), row["summary"][:500])
w = db.wait_worker_result(dispatch_id=row["id"], timeout_sec=1)
check("wait_worker_result trả build_branch / build_commit / compare_url / mode", w["mode"] == "build" and w["build_branch"] == f"wt/{T1}"
      and w["build_commit"] == sha and w["compare_url"] and w["build_tests"]["ok"] and w["build_push"]["ok"], str(w)[:300])
msgs = [m for m in db.get_gen_messages(CONV) if f"dispatch:{row['id']}" in (m.get("content") or "")]
body = msgs[-1]["content"] if msgs else ""
check("phiên của task: tin kết quả có nhánh, commit, test, compare + gợi ý bằng chứng SHA", f"Nhánh: wt/{T1}" in body and f"Commit: {sha}" in body
      and "Test: PASS" in body and "So sánh: https://github.com/chu-so-huu/gen-workplace/compare/main...wt/" in body
      and f"Bằng chứng nghiệm thu gợi ý: {sha}" in body, body[:600])
check("checklist chk-a tick theo báo cáo agy", next(c for c in db._task_detail(T1)["checklist"] if c["id"] == "chk-a")["done"])
with db.get_connection() as conn:
    wr = [dict(r) for r in conn.execute("SELECT author, body, reply_to FROM chat_messages WHERE id IN (?, ?)",
                                        (row["request_msg_id"], row["reply_msg_id"])).fetchall()]
check("war-room: tin giao [Làm] + tin trả lời của vai (không chạy dispatch war-room)", len(wr) == 2 and wr[0]["body"].startswith("[Làm] @backend Thực hiện")
      and wr[1]["author"] == "gw-backend-agy" and f"wt/{T1}" in wr[1]["body"], str(wr)[:300])
with db.get_connection() as conn:
    n_warroom = conn.execute("SELECT COUNT(*) FROM dispatch_log WHERE task_id = ? AND kind != 'build'", (T1,)).fetchone()[0]
check("không sinh thêm dispatch war-room (plan) cho task", n_warroom == 0, n_warroom)
brief = [t for t in db.get_all_session_todos() if t["id"] == T1][0]["last_dispatch"]
check("/api/state: last_dispatch có mode + nhánh + commit + compare", brief["mode"] == "build" and brief["build_commit"] == sha
      and brief["compare_url"], str(brief)[:300])

print("[4] Bằng chứng: commit trong worktree + đóng thay người giữ bằng SHA; dọn worktree khi done")
ok, by, msg = db.verify_evidence_ref(sha, task_id=T1)
check("verify_evidence_ref nhận commit SHA của nhánh wt/ (repo dispatch)", ok and by == "git:commit", msg)
ok, by, msg = db.verify_evidence_ref(sha[:10], task_id=T1)
check("SHA ngắn 10 ký tự cũng nhận", ok, msg)
res = db.complete_task("claude-dieu-phoi", T1, sha[:12])
check("complete_task bằng SHA lần build của người giữ → completed (closed_for_holder)", res.get("status") == "completed"
      and res.get("closed_for_holder") == "gw-backend-agy", str(res))
check("task done → worktree đã gỡ", not os.path.exists(WT1) and res.get("worktree_removed") == WT1, str(res))
check("nhánh local + remote giữ nguyên", git_out("rev-parse", f"wt/{T1}") == sha and remote_branch(f"wt/{T1}") == sha)
check("git worktree list không còn TSK", T1 not in git_out("worktree", "list"))
check("SHA vẫn là bằng chứng hợp lệ sau khi dọn", db.verify_evidence_ref(sha, task_id=T1)[0])

print("[5] Rà soát vẫn là --mode plan; API mặc định build, mode sai → 400")
reset_logs()
T2 = new_task("[THỬ] Rà soát")
res = db.assign_task_to_role(T2, "qa", wait=True, mode="review")
args = json.loads(open(ARGS_LOG).read().splitlines()[-1])
row = drow(res["dispatch_id"])
check("mode=review → kind warroom, --mode plan", res.get("mode") == "review" and row["kind"] == "warroom" and "--mode" in args
      and args[args.index("--mode") + 1] == "plan", str(args)[:200])
check("review không tạo worktree task", not os.path.exists(os.path.join(os.environ["GW_WORKTREE_ROOT"], T2)))
reset_logs()
db.post_warroom_message(message=f"@lead xem {T2}", author="Ryan (Owner)", wait=True)
args = json.loads(open(ARGS_LOG).read().splitlines()[-1])
check("war-room @lead vẫn --mode plan", "--mode" in args and "plan" in args)
check("mode sai → bad_request", db.assign_task_to_role(T2, "qa", mode="xoa-het").get("code") == "bad_request")

server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def call(method, path, payload=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode() if payload is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


T3 = new_task("[THỬ] API mặc định")
st, js = call("POST", "/api/task/assign", {"todo_id": T3, "session_id": "devops", "mode": "sai"})
check("API mode sai → 400", st == 400, f"{st} {js}")
os.environ["FAKE_AGY_MODE"] = "ok"
st, js = call("POST", "/api/task/assign", {"todo_id": T3, "session_id": "devops"})
check("API không truyền mode → build", st == 200 and js.get("mode") == "build", f"{st} {js}")
st, w = call("GET", f"/api/dispatch/wait?dispatch_id={js.get('dispatch_id')}&timeout_sec=60")
check("API build xong: done + nhánh đã push", w.get("status") == "done" and remote_branch(f"wt/{T3}") == w.get("build_commit"), str(w)[:300])

print("[6] agy thử push + commit lên main → bị chặn; lách hook → app phát hiện, không push")
os.environ["FAKE_AGY_MODE"] = "push"
reset_logs()
T4 = new_task("[THỬ] thử push")
main_before = git_out("rev-parse", "main")
res = db.assign_task_to_role(T4, "backend", wait=True)
row = drow(res["dispatch_id"])
gl = [x for x in run_log() if "git" in x]
p = next((x for x in gl if x["git"][0] == "push"), {})
c = next((x for x in gl if x["git"][:1] == ["-C"]), {})
check("git push từ agy thất bại (pushurl hỏng / hook pre-push)", p.get("rc", 0) != 0, str(p))
check("không có nhánh wt/agy-tu-push trên remote", remote_branch("wt/agy-tu-push") == "")
check("commit lên main từ agy bị hook pre-commit chặn", c.get("rc", 0) != 0 and "chặn commit" in c.get("err", ""), str(c))
check("main không đổi", git_out("rev-parse", "main") == main_before and remote_branch("main") == main_before)
check("lần Làm vẫn done (commit đúng nhánh), app tự push", row["status"] == "done" and remote_branch(f"wt/{T4}") == row["build_commit"], row["summary"][:300])
check("summary ghi agy đã thử push", "agy đã thử push" in row["summary"], row["summary"][:600])

os.environ["FAKE_AGY_MODE"] = "bypass"
T5 = new_task("[THỬ] lách hook")
res = db.assign_task_to_role(T5, "backend", wait=True)
row = drow(res["dispatch_id"])
check("lách hook commit lên main → failed + VI PHẠM", row["status"] == "failed" and "VI PHẠM" in row["summary"], row["summary"][:400])
check("vi phạm → KHÔNG push nhánh", remote_branch(f"wt/{T5}") == "" and not row["build_push"])
sh(*GITC, "reset", "-q", "--hard", main_before, cwd=REPO)

print("[7] Không commit → failed; test hỏng → push nhưng ghi FAIL; PR nháp khi có GITHUB_TOKEN")
os.environ["FAKE_AGY_MODE"] = "nocommit"
T6 = new_task("[THỬ] không commit")
row = drow(db.assign_task_to_role(T6, "backend", wait=True)["dispatch_id"])
check("không commit → failed, nêu file chưa commit", row["status"] == "failed" and "không tạo commit mới" in row["summary"]
      and "chưa commit" in row["summary"], row["summary"][:300])
check("tên file chưa commit đủ ký tự (không mất chữ đầu)", ": README.md" in row["summary"] and "backend/app.py" in row["summary"], row["summary"][:400])
check("không commit → không push", remote_branch(f"wt/{T6}") == "")

PR_CALLS = []


class FakeGH(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        PR_CALLS.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        out = json.dumps({"html_url": "https://github.com/chu-so-huu/gen-workplace/pull/77", "number": 77}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


gh = http.server.HTTPServer(("127.0.0.1", 0), FakeGH)
threading.Thread(target=gh.serve_forever, daemon=True).start()
os.environ["GITHUB_TOKEN"] = "ghp_testtoken_khong_that"
os.environ["GW_GITHUB_API_URL"] = f"http://127.0.0.1:{gh.server_address[1]}"
os.environ["FAKE_AGY_MODE"] = "failtest"
T7 = new_task("[THỬ] test hỏng")
row = drow(db.assign_task_to_role(T7, "backend", wait=True)["dispatch_id"])
tests = json.loads(row["build_tests"] or "{}")
check("test hỏng → ghi FAIL + tên test", tests.get("ok") is False and any(t["name"] == "test_hong.py" and not t["ok"] for t in tests.get("tests", []))
      and "Test: FAIL" in row["summary"] and "test_hong.py" in row["summary"], row["summary"][:400])
check("vẫn push để review", remote_branch(f"wt/{T7}") == row["build_commit"])
check("có GITHUB_TOKEN → tạo PR NHÁP head=wt/TSK-n base=main", PR_CALLS and PR_CALLS[-1]["body"].get("draft") is True
      and PR_CALLS[-1]["body"]["head"] == f"wt/{T7}" and PR_CALLS[-1]["body"]["base"] == "main"
      and PR_CALLS[-1]["path"] == "/repos/chu-so-huu/gen-workplace/pulls", str(PR_CALLS)[:300])
check("pr_url ghi vào dispatch_log + summary", row["pr_url"].endswith("/pull/77") and "PR nháp: https://" in row["summary"])
check("token không lộ trong summary / build_push", "ghp_testtoken" not in row["summary"] and "ghp_testtoken" not in row["build_push"])
os.environ.pop("GITHUB_TOKEN")
os.environ.pop("GW_GITHUB_API_URL")

print("[8] Giao lại khi đang chạy → busy; xóa task → dọn worktree; giao lại task cũ → dùng lại worktree")
os.environ["FAKE_AGY_MODE"] = "slow"
T8 = new_task("[THỬ] chậm")
r1 = db.assign_task_to_role(T8, "backend", wait=False)
time.sleep(0.3)
r2 = db.assign_task_to_role(T8, "backend", wait=False)
check("đang chạy → busy (409), không chạy chồng", r2.get("code") == "busy" and r2.get("dispatch_id") == r1["dispatch_id"], str(r2))
check("đang chạy → cleanup không gỡ", not db.cleanup_task_worktree(T8).get("removed"))
w = db.wait_worker_result(dispatch_id=r1["dispatch_id"], timeout_sec=60)
check("lần chậm xong done", w["status"] == "done", str(w)[:200])
os.environ["FAKE_AGY_MODE"] = "ok"
first = w["build_commit"]
r3 = db.assign_task_to_role(T8, "backend", wait=True)
row = drow(r3["dispatch_id"])
check("giao lại → dùng lại worktree, commit mới nối tiếp", row["status"] == "done" and "dùng lại worktree" in row["summary"]
      and git_out("rev-parse", f"{row['build_commit']}~1") == first, row["summary"][:300])
WT8 = os.path.join(os.environ["GW_WORKTREE_ROOT"], T8)
res = db.delete_gen_session_todo(CONV, T8)
check("xóa task → gỡ worktree, giữ nhánh remote", not os.path.exists(WT8) and res.get("worktree_removed") == WT8
      and remote_branch(f"wt/{T8}") == row["build_commit"], str(res))
check("dọn task không có worktree → không lỗi", db.cleanup_task_worktree("TSK-424242") == {"removed": False, "dir": os.path.join(os.environ["GW_WORKTREE_ROOT"], "TSK-424242"), "branch": "wt/TSK-424242"})
check("mã task lạ → không đụng đĩa", db.cleanup_task_worktree("../x").get("removed") is False)

print("[9] Remote không push được → ghi lỗi rõ, không crash")
sh("git", "remote", "set-url", "origin", os.path.join(TMP, "khong-co.git"), cwd=REPO)
T9 = new_task("[THỬ] push lỗi")
row = drow(db.assign_task_to_role(T9, "backend", wait=True)["dispatch_id"])
push = json.loads(row["build_push"] or "{}")
check("push lỗi → build_push.ok=false + error, summary 'Push: LỖI'", push.get("ok") is False and push.get("error") and "Push: LỖI" in row["summary"], str(push)[:300])
check("vẫn có commit + test (done để review local)", row["status"] == "done" and row["build_commit"], row["summary"][:500])
sh("git", "remote", "set-url", "origin", REMOTE, cwd=REPO)

server.shutdown()
gh.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

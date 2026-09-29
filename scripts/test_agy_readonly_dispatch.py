#!/usr/bin/env python3
"""
Test lỗi dispatch:9 (Issue #26): nút "Giao cho @vai" (POST /api/task/assign) → agy --mode plan bị auto-deny lệnh shell.
Chạy không cần agy/tmux thật (agy giả viết bằng Python, chế độ chọn qua biến môi trường FAKE_AGY_MODE).
- Allow-rule chỉ đọc mở rộng: find, stat, file, cd, git blame, sed -n 'N,Mp', git branch dạng liệt kê; KHÔNG lệnh ghi / mạng /
  chạy mã tùy ý (python3 -c, bash -c, xargs, awk, curl, rm...). Deny-rule chặn cờ ghi/chạy lệnh con (find -delete/-exec, sed -i,
  rg --pre, tree -o, file -C, git --output, git grep -O, git branch -D). Mô phỏng cách agy khớp (tiền tố token, regex neo từng
  token, Deny > Allow, pipeline/&& xét từng lệnh) để kiểm lệnh đọc hay dùng được chạy, lệnh ghi bị chặn. Regex hợp RE2 (Go).
- ensure_agy_plan_permissions ghi allow + deny, gỡ command(git branch) cũ, giữ rule của người dùng, idempotent;
  copy_agy_allow_rules không chép rule đã gỡ.
- Prompt /api/task/assign và war-room: CHỈ ĐỌC, ưu tiên công cụ đọc file, danh sách lệnh cho phép, không lệnh ghi,
  "CẦN QUYỀN: ..." khi bị chặn; vẫn kèm khối THÔNG TIN VIỆC.
- agy chạy --output-format stream-json; bị chặn → dispatch failed, summary / tin war-room / tin phiên / báo cáo ghi rõ
  "Lệnh bị chặn: `<lệnh>`" trích từ output; agy cũ không nhận cờ → chạy lại không cờ; "CẦN QUYỀN" lên summary.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-agy-readonly-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

PROMPT_LOG = os.path.join(TMP, "prompts.log")
ARGS_LOG = os.path.join(TMP, "args.log")
NOTICE = ('jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, '
          'so it was auto-denied. Add an allow-rule under permissions.allow in settings.json (e.g. command(<target>)). '
          'Alternatively, re-run with --dangerously-skip-permissions to auto-approve all tools.')
AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
stream = "--output-format" in args and args[args.index("--output-format") + 1] == "stream-json"
open({PROMPT_LOG!r}, "a").write(prompt + "\\n===\\n")
open({ARGS_LOG!r}, "a").write(json.dumps(args) + "\\n")
mode = os.environ.get("FAKE_AGY_MODE", "ok")
m = re.search(r"THÔNG TIN VIỆC (TSK-[0-9]+)", prompt)
tick = f"[KANBAN_UPDATE: {{m.group(1)}} | CHECK: chk-a]" if m else ""
NOTICE = {NOTICE!r}
def ev(obj):
    print(json.dumps(obj, ensure_ascii=False))
def tool(i, cmd, output="", error=None):
    ti = {{"name": "run_command", "parameters": {{"CommandLine": cmd}}}}
    if output:
        ti["output"] = output
    if error:
        ti["error"] = error
    ev({{"event": "step_update", "step_update": {{"step_index": i, "state": "ACTIVE", "step_type": "tool", "tool_name": "run_command",
        "tool_info": {{"name": "run_command", "parameters": {{"CommandLine": cmd}}}}}}}})
    ev({{"event": "step_update", "step_update": {{"step_index": i, "state": "DONE", "step_type": "tool", "tool_name": "run_command", "tool_info": ti}}}})
if mode == "old_agy":
    if "--output-format" in args:
        print("Error: unknown flag: --output-format", file=sys.stderr); sys.exit(2)
    print("Trả lời từ agy cũ. " + tick); sys.exit(0)
if not stream:
    print("thiếu --output-format stream-json", file=sys.stderr); sys.exit(3)
ev({{"event": "init", "init": {{"cwd": os.getcwd(), "tools": ["run_command", "view_file"], "permission_mode": "request-review"}}}})
if mode == "ok":
    tool(2, "grep -n /api/task/ backend/main.py", output="12: /api/task/assign")
    ev({{"event": "step_update", "step_update": {{"step_index": 3, "state": "ACTIVE", "step_type": "agent_response", "text_delta": "Đã đọc "}}}})
    ev({{"event": "step_update", "step_update": {{"step_index": 3, "state": "DONE", "step_type": "agent_response", "text_delta": "xong. cwd=" + os.getcwd()}}}})
    ev({{"event": "result", "result": {{"status": "SUCCESS", "response": "Đã đọc xong. cwd=" + os.getcwd() + "\\n" + tick}}}})
elif mode == "denied_stream":
    tool(2, "grep -n /api/task/ backend/main.py", output="12: /api/task/assign")
    tool(3, "python3 -c 'import ast'", error={{"type": "PERMISSION_DENIED", "message": "permission required: headless mode cannot prompt, auto-denied"}})
    ev({{"event": "result", "result": {{"status": "SUCCESS", "response": ""}}}})
    print(NOTICE, file=sys.stderr)
elif mode == "denied_noerr":
    tool(2, "awk 'NR<=5' README.md")
    ev({{"event": "result", "result": {{"status": "SUCCESS", "response": ""}}}})
    print(NOTICE, file=sys.stderr)
elif mode == "denied_named":
    print(NOTICE.replace("command(<target>)", "command(npm test)"), file=sys.stderr)
elif mode == "partial":
    tool(2, "xargs cat", error={{"type": "PERMISSION_DENIED", "message": "auto-denied: permission required"}})
    ev({{"event": "result", "result": {{"status": "SUCCESS", "response": "Đếm được 7 file .py.\\nCẦN QUYỀN: command(xargs) — để đọc nhiều file một lần\\n" + tick}}}})
sys.exit(0)
''')
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
os.environ["GW_TMUX_INIT_DIR"] = os.path.join(TMP, "tmux-init")
for k in ("GW_AGY_WRITE_ROLES", "GW_WARROOM_SKIP_PERMISSIONS", "GW_AGY_PLAN_ALLOW", "GW_AGY_NO_STREAM", "FAKE_AGY_MODE"):
    os.environ.pop(k, None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
PROFILE = os.path.join(os.environ["HOME"], ".gemini")
SETTINGS = os.path.join(PROFILE, "antigravity-cli", "settings.json")
os.makedirs(os.path.dirname(SETTINGS))
with open(SETTINGS, "w") as f:
    json.dump({"theme": "terminal", "permissions": {"allow": ["read_url(github.com)", "command(git branch)"],
                                                    "deny": ["command(sudo)"]}}, f)
sys.path.insert(0, ROOT)

from backend import db  # noqa: E402

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


# ---------------------------------------------------------------------------
# Mô phỏng bộ khớp command() của agy (docs Antigravity "Agent permissions"): tiền tố token; regex: mỗi token của rule là
# regex neo ^(?:...)$ khớp token tương ứng (tiền tố); Deny > Allow; pipeline / && / || / ; xét từng lệnh.
# ---------------------------------------------------------------------------
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


print("[1] Danh sách allow-rule / deny-rule")
ALLOW = db.agy_plan_allow_rules()
DENY = db.agy_plan_deny_rules()
for c in ("find", "head", "tail", "wc", "rg", "grep", "tree", "stat", "file", "cd", "git show", "git blame", "git log", "ls", "cat"):
    check(f"allow có command({c})", f"command({c})" in ALLOW, str(ALLOW))
check("sed -n chỉ ở dạng regex in theo dòng", any(r.startswith("command(regex:sed -n ") for r in ALLOW) and "command(sed)" not in ALLOW
      and "command(sed -n)" not in ALLOW, str(ALLOW))
check("git branch chỉ ở dạng regex liệt kê, không còn command(git branch)", "command(git branch)" not in ALLOW
      and any(r.startswith("command(regex:git branch (--show-current") for r in ALLOW), str(ALLOW))
BAD = ("rm", "mv", "cp", "tee", "dd", "curl", "wget", "python", "python3", "node", "bash", "sh", "perl", "ruby", "xargs", "awk",
       "sed", "git push", "git commit", "git checkout", "git reset", "git clean", "git branch", "npm", "pip", "chmod", "touch", "mkdir")
check("không lệnh ghi / mạng / chạy mã tùy ý", not any(r == f"command({b})" or r.startswith(f"command({b} ") or r.startswith(f"command(regex:{b} ")
                                                        and b not in ("sed", "git branch") for r in ALLOW for b in BAD), str(ALLOW))
check("không có python3 -c (chạy mã tùy ý, không giới hạn được về chỉ đọc)", not any("python" in r for r in ALLOW))
check("không rule wildcard command(*)", "command(*)" not in ALLOW and "command(*)" not in DENY)
RE2_BAD = re.compile(r"\(\?[=!<]|\\[1-9]")
check("regex hợp RE2 (không lookaround / backreference)", not any(RE2_BAD.search(r) for r in ALLOW + DENY))
check("mọi regex biên dịch được", all(re.compile(p) for r in ALLOW + DENY if "regex:" in r for p in r[len("command(regex:"):-1].split()))
check("deny có đủ vị trí token (0..10) cho find -delete", sum(1 for r in DENY if r.startswith("command(regex:find ")) == db.AGY_PLAN_DENY_MAX_POS + 1)

print("[2] Mô phỏng agy: lệnh đọc hay dùng được chạy, lệnh ghi / chạy mã bị chặn")
OK_CMDS = [
    'grep -n "/api/task/" backend/main.py', "grep -rn assign_task backend", "rg -n TSK- backend", "find backend -name '*.py'",
    "find backend -name '*.py' | wc -l", "find . -maxdepth 2 -type f -name '*.md'", "head -n 5 README.md", "head -5 README.md",
    "tail -20 backend/main.py", "wc -l backend/main.py", "sed -n '1,5p' README.md", "sed -n 760,800p backend/main.py",
    "sed -n '$p' README.md", "sed -n 10,+5p backend/db.py", "tree -L 2 backend", "stat README.md", "file backend/main.py",
    "git show HEAD --stat", "git blame backend/db.py", "git branch --show-current", "git branch -a", "git log --oneline -5",
    "ls -la backend", "cat README.md | head -5", "cd backend && ls", "git grep -n assign", "git diff HEAD~1 --stat",
]
for c in OK_CMDS:
    check(f"cho phép: {c}", agy_allows(c, ALLOW, DENY))
BAD_CMDS = [
    "find . -name '*.pyc' -delete", "find . -type f -exec rm {} ;", "find . -name x -execdir sh -c id ;", "find . -fprint /tmp/x",
    "sed -i 's/a/b/' README.md", "sed -n 1,5p -i README.md", "sed -n '1,5p' --in-place README.md", "sed -n 's/a/b/w out' README.md",
    "sed -n 1p -e 'w /tmp/x' README.md", "sed -n -i 1p README.md", "sed 's/a/b/' README.md", "rg --pre cat x", "rg -n x --pre=sh .",
    "tree -o out.txt", "tree -L 2 -o out.txt", "file -C -m magic", "git diff --output=/tmp/x", "git log -p --output=x",
    "git show HEAD --output=x", "git grep -O vim assign", "git grep --open-files-in-pager=vim x", "git branch -D main",
    "git branch -a -D main", "git branch newbranch", "git branch -m a b", "git branch --set-upstream-to=origin/x",
    "python3 -c 'print(1)'", "bash -c ls", "xargs rm", "awk '{print}' README.md", "rm -rf backend", "curl http://x",
    "git push", "git checkout -- .", "cat README.md | xargs rm", "ls && rm -rf x", "git branch",
]
for c in BAD_CMDS:
    check(f"chặn: {c}", not agy_allows(c, ALLOW, DENY))

print("[3] ensure_agy_plan_permissions: ghi allow + deny, gỡ rule cũ, giữ rule người dùng, idempotent")
wt = db.ensure_role_worktree("gw-qa-agy")
added = db.ensure_agy_plan_permissions(PROFILE, wt)
data = json.load(open(SETTINGS))
allow, deny = data["permissions"]["allow"], data["permissions"]["deny"]
check("trả rule vừa thêm (allow + deny)", len(added) == len(ALLOW) + 1 + len(DENY), len(added))
check("allow có read_file(worktree) + mọi rule chỉ đọc", f"read_file({os.path.realpath(wt)})" in allow and all(r in allow for r in ALLOW))
check("deny có mọi rule cờ nguy hiểm", all(r in deny for r in DENY))
check("gỡ command(git branch) cũ", "command(git branch)" not in allow, str(allow)[:200])
check("giữ theme, read_url(github.com), deny command(sudo)", data.get("theme") == "terminal" and "read_url(github.com)" in allow
      and "command(sudo)" in deny)
check("lần 2 → [] và không nhân đôi", db.ensure_agy_plan_permissions(PROFILE, wt) == [] and len(json.load(open(SETTINGS))["permissions"]["deny"]) == len(deny))
bad_deny = os.path.join(TMP, "bad-deny")
os.makedirs(os.path.join(bad_deny, "antigravity-cli"))
with open(os.path.join(bad_deny, "antigravity-cli", "settings.json"), "w") as f:
    json.dump({"permissions": {"allow": [], "deny": "sai kiểu"}}, f)
check("deny sai kiểu → không đụng", db.ensure_agy_plan_permissions(bad_deny, wt) == []
      and json.load(open(os.path.join(bad_deny, "antigravity-cli", "settings.json")))["permissions"]["deny"] == "sai kiểu")
src = os.path.join(TMP, "src-prof")
dst = os.path.join(TMP, "dst-prof")
for d in (src, dst):
    os.makedirs(os.path.join(d, "antigravity-cli"))
with open(os.path.join(src, "antigravity-cli", "settings.json"), "w") as f:
    json.dump({"permissions": {"allow": ["command(git branch)", "command(make test)"]}}, f)
copied = db.copy_agy_allow_rules(src, dst)
check("copy_agy_allow_rules không chép command(git branch)", copied == ["command(make test)"], str(copied))

print("[4] Prompt chế độ chỉ đọc")
p = db.build_agy_readonly_prompt("@qa Thực hiện TSK-1: x", "[THÔNG TIN VIỆC TSK-1]\nTiêu đề: x", "gw-qa-agy")
check("có khối CHẾ ĐỘ CHỈ ĐỌC", "CHẾ ĐỘ CHỈ ĐỌC" in p and "CHỈ ĐỌC: không tạo/sửa/xóa file" in p)
check("nói không chạy lệnh ghi", "không chạy lệnh ghi" in p and "sed -i" in p and "rm" in p)
check("ưu tiên công cụ đọc file của agy", "công cụ đọc có sẵn của agy" in p and "đọc file" in p)
check("liệt kê lệnh cho phép (find, sed -n, git blame...)", all(x in p for x in ("find", "sed -n 'N,Mp'", "git blame", "grep", "head")))
check("bị chặn → ghi 'CẦN QUYỀN: ...', không dừng im lặng", "CẦN QUYỀN: <lệnh hoặc thao tác>" in p and "KHÔNG dừng im lặng" in p)
check("giữ khối THÔNG TIN VIỆC + câu kết cũ", "[THÔNG TIN VIỆC TSK-1]" in p and "trả lời ĐẦY ĐỦ ngay trong một lượt" in p)
check("prompt liệt kê đúng danh sách allow-rule", all(c in db.agy_plan_allowed_summary() for c in db.AGY_PLAN_READONLY_COMMANDS))

print("[5] POST /api/task/assign (assign_task_to_role) gửi prompt chỉ đọc + chạy stream-json → done, tick checklist")
with db.get_connection() as conn:
    conn.execute("UPDATE tmux_sessions SET account_type = 'owner_default', profile_dir = '' WHERE id = 'gw-qa-agy'")
    conn.commit()
conv = db.create_gen_conversation("PRJ-GEN-WORKPLACE", "VIEC-10: test chỉ đọc")
CONV = conv.get("id") or conv.get("conv_id")


def new_task(title):
    t = db.save_gen_session_todo(CONV, None, title, "Chỉ đọc", "todo", "high", "Gen Core",
                                 [{"id": "chk-a", "text": "đếm số file .py trong backend", "done": False},
                                  {"id": "chk-b", "text": "đọc 5 dòng đầu README", "done": False}], viec_ref="VIEC-10")
    return t["id"]


def dispatch_row(did):
    with db.get_connection() as conn:
        return dict(conn.execute("SELECT * FROM dispatch_log WHERE id = ?", (did,)).fetchone())


def reply_body(row):
    with db.get_connection() as conn:
        return conn.execute("SELECT body FROM chat_messages WHERE id = ?", (row["reply_msg_id"],)).fetchone()["body"]


def task_row(tid):
    with db.get_connection() as conn:
        r = dict(conn.execute("SELECT * FROM gen_session_todos WHERE id = ?", (tid,)).fetchone())
    r["checklist"] = json.loads(r["checklist_json"] or "[]")
    return r


os.environ["FAKE_AGY_MODE"] = "ok"
open(PROMPT_LOG, "w").close()
open(ARGS_LOG, "w").close()
T1 = new_task("[THỬ] chỉ đọc 1")
res = db.assign_task_to_role(T1, "qa", wait=True, mode="review")
did = res.get("dispatch_id")
row = dispatch_row(did)
prompt = open(PROMPT_LOG).read()
args = json.loads(open(ARGS_LOG).read().splitlines()[-1])
check("prompt assign có khối chỉ đọc + THÔNG TIN VIỆC", "CHẾ ĐỘ CHỈ ĐỌC" in prompt and f"THÔNG TIN VIỆC {T1}" in prompt
      and "CẦN QUYỀN" in prompt, prompt[:300])
check("agy chạy --mode plan -p ... --output-format stream-json, không skip-permissions",
      args[-2:] == ["--output-format", "stream-json"] and "--mode" in args and "plan" in args
      and db.AGY_SKIP_PERMISSIONS_FLAG not in args, str(args)[:200])
check("dispatch done ngay lần đầu", row["status"] == "done" and row["exit_code"] == 0, str(row)[:300])
check("summary là câu trả lời (không lẫn JSON stream)", row["summary"].startswith("Đã đọc xong.") and '"event"' not in row["summary"], row["summary"][:200])
check("không có dòng lệnh bị chặn khi chạy trơn", "Lệnh bị chặn" not in row["summary"])
check("checklist chk-a đã tick theo báo cáo", next(c for c in task_row(T1)["checklist"] if c["id"] == "chk-a")["done"])
rep = open(row["report_path"]).read()
check("báo cáo có mục lệnh shell agy đã gọi", "## Lệnh shell agy đã gọi" in rep and "`grep -n /api/task/ backend/main.py`" in rep, rep[:500])
check("báo cáo: phần Output vẫn ở cuối (tick checklist chỉ xét output)", rep.rindex("## Output") > rep.index("## Lệnh shell agy đã gọi"))

print("[6] war-room mode=review (hoặc [đọc]) dùng prompt chỉ đọc; war-room thường (build) không nhất thiết có khối chỉ đọc")
open(PROMPT_LOG, "w").close()
db.post_warroom_message(message="@qa liệt kê endpoint", author="Ryan (Owner)", wait=True, mode="review")
check("prompt war-room mode=review có khối CHỈ ĐỌC", "CHẾ ĐỘ CHỈ ĐỌC" in open(PROMPT_LOG).read())
open(PROMPT_LOG, "w").close()
db.post_warroom_message(message="@qa [đọc] liệt kê endpoint", author="Ryan (Owner)", wait=True)
check("prompt war-room [đọc] có khối CHỈ ĐỌC", "CHẾ ĐỘ CHỈ ĐỌC" in open(PROMPT_LOG).read())

print("[7] agy bị chặn: dispatch failed + ghi rõ lệnh bị chặn (trích từ stream-json)")
os.environ["FAKE_AGY_MODE"] = "denied_stream"
T2 = new_task("[THỬ] bị chặn")
res = db.assign_task_to_role(T2, "qa", wait=True, mode="review")
row = dispatch_row(res["dispatch_id"])
check("dispatch failed", row["status"] == "failed", row["status"])
check("summary ghi 'Lệnh bị chặn: `python3 -c ...`'", "Lệnh bị chặn: `python3 -c 'import ast'`" in row["summary"], row["summary"][:400])
check("lệnh chạy được (grep) không bị ghi là chặn", "`grep -n /api/task/ backend/main.py`" not in row["summary"].split("Lệnh bị chặn:")[1].split("\n")[0])
check("summary giữ thông báo gốc của agy", "no output produced" in row["summary"])
body = reply_body(row)
check("tin war-room ghi lệnh bị chặn", "Lệnh bị chặn: `python3 -c 'import ast'`" in body and "exit=0 (failed" in body, body[:300])
msgs = [m for m in db.get_gen_messages(CONV) if f"dispatch:{row['id']}" in (m.get("content") or "")]
check("tin kết quả trong phiên ghi lệnh bị chặn", msgs and "Lệnh bị chặn: `python3 -c 'import ast'`" in msgs[-1]["content"], str(msgs)[:300])
rep = open(row["report_path"]).read()
check("báo cáo: dòng lệnh bị chặn + đánh dấu BỊ CHẶN", "- Lệnh bị chặn: `python3 -c 'import ast'`" in rep and "`python3 -c 'import ast'` — BỊ CHẶN" in rep, rep[:600])

os.environ["FAKE_AGY_MODE"] = "denied_noerr"
T3 = new_task("[THỬ] bị chặn 2")
row = dispatch_row(db.assign_task_to_role(T3, "qa", wait=True, mode="review")["dispatch_id"])
check("tool step không có lỗi rõ → suy ra lệnh shell cuối", row["status"] == "failed"
      and "Lệnh bị chặn (suy ra: lệnh shell cuối agy gọi, output không nêu tên): `awk 'NR<=5' README.md`" in row["summary"], row["summary"][:300])

os.environ["FAKE_AGY_MODE"] = "denied_named"
T4 = new_task("[THỬ] bị chặn 3")
row = dispatch_row(db.assign_task_to_role(T4, "qa", wait=True, mode="review")["dispatch_id"])
check("thông báo agy nêu rule cụ thể → trích 'npm test'", row["status"] == "failed" and "Lệnh bị chặn: `npm test`" in row["summary"], row["summary"][:300])
check("không lấy mẫu command(<target>)", "<target>`" not in row["summary"].split("\n")[0])

print("[8] agy trả lời được nhưng có lệnh bị chặn giữa chừng + CẦN QUYỀN")
os.environ["FAKE_AGY_MODE"] = "partial"
T5 = new_task("[THỬ] một phần")
row = dispatch_row(db.assign_task_to_role(T5, "qa", wait=True, mode="review")["dispatch_id"])
check("dispatch done (có câu trả lời)", row["status"] == "done", row["status"])
check("summary nêu lệnh bị chặn + dòng CẦN QUYỀN", "Lệnh bị chặn: `xargs cat`" in row["summary"]
      and "[agy báo cần quyền] command(xargs) — để đọc nhiều file một lần" in row["summary"], row["summary"][:400])
check("tin war-room có cảnh báo lệnh bị chặn", "⚠ Lệnh bị chặn: `xargs cat`" in reply_body(row))

print("[9] agy cũ không nhận --output-format → chạy lại không cờ, vẫn done")
os.environ["FAKE_AGY_MODE"] = "old_agy"
open(ARGS_LOG, "w").close()
T6 = new_task("[THỬ] agy cũ")
row = dispatch_row(db.assign_task_to_role(T6, "qa", wait=True, mode="review")["dispatch_id"])
calls = [json.loads(x) for x in open(ARGS_LOG).read().splitlines()]
check("gọi 2 lần: có cờ rồi không cờ", len(calls) == 2 and "--output-format" in calls[0] and "--output-format" not in calls[1], str(calls)[:300])
check("dispatch done, summary là trả lời agy cũ", row["status"] == "done" and row["summary"].startswith("Trả lời từ agy cũ."), str(row)[:300])
check("dispatch_log.command là lệnh chạy thật (không cờ)", "--output-format" not in row["command"])

print("[10] parse_agy_stream / agy_blocked_commands")
info = db.parse_agy_stream("không phải json\n")
check("stdout thường → is_stream False, giữ nguyên", not info["is_stream"] and info["response"] == "không phải json")
info = db.parse_agy_stream('{"event":"step_update","step_update":{"step_index":1,"state":"DONE","step_type":"agent_response","text_delta":"a"}}\n'
                           '{"event":"step_update","step_update":{"step_index":2,"state":"DONE","step_type":"agent_response","text_delta":"b"}}\n')
check("không có result → ghép text_delta", info["is_stream"] and info["response"] == "ab", str(info))
check("agy_blocked_commands rỗng khi không có gì", db.agy_blocked_commands({}, NOTICE) == [])
check("read_file(...) bị chặn được giữ nguyên dạng rule", db.agy_blocked_commands({}, "e.g. read_file(/etc/passwd)") == ["read_file(/etc/passwd)"])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

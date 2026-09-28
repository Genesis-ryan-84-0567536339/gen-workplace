#!/usr/bin/env python3
"""
Test Issue #7 (phần còn lại): chat Gen / Orchestrator (call_agy_cli_turn, agy --print) KHÔNG còn --dangerously-skip-permissions.
Chạy không cần agy thật (GW_AGY_BIN = agy giả ghi lại argv/cwd):
- Mặc định: lệnh không có cờ; permissions.allow của hồ sơ agy có read_file(<thư mục làm việc>) + read_file(<repo>) +
  command(ls|cat|grep|git log|...); giữ khóa cũ; idempotent.
- GW_GEN_CHAT_SKIP_PERMISSIONS=1 → có cờ (lối thoát cuối, mặc định tắt).
- agy in "no output produced" / "auto-denied" (response rỗng hoặc ngắn) → lỗi PERMISSION_DENIED rõ ràng, lưu 'Gen (lỗi)',
  không trả chuỗi rỗng coi như thành công. Trả lời dài có nhắc tới "auto-denied" vẫn là trả lời thật.
- Orchestrator dùng chung call_agy_cli_turn → cùng quy tắc.
- Rà mã nguồn: "dangerously-skip-permissions" trong backend/*.py chỉ còn ở hằng số / nhánh có điều kiện / directive_guard.
"""
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-genperm-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
ARGS_LOG = os.path.join(TMP, "agy-args.jsonl")
MODE_FILE = os.path.join(TMP, "mode.txt")
AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write(r'''#!/usr/bin/env python3
import json, os, sys
with open(%r, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
mode = open(%r).read().strip() if os.path.exists(%r) else "ok"
if mode == "denied_empty":
    print(json.dumps({"response": "", "usage": {}}))
    sys.stderr.write('jetski: no output produced — a tool required the "command" permission and it was auto-denied\n')
elif mode == "denied_text":
    print(json.dumps({"response": "Error: tool call auto-denied (read_file)", "usage": {}}))
elif mode == "denied_plain":
    print("jetski: no output produced")
elif mode == "long":
    print(json.dumps({"response": "Endpoint chat Gen là POST /api/gen/chat. " + ("Chi tiết. " * 300) + "Lưu ý: nếu tool bị auto-denied thì báo lỗi.", "usage": {}}))
else:
    print(json.dumps({"response": "Endpoint chat Gen: POST /api/gen/chat trong backend/main.py", "conversation_id": "agy-c1", "usage": {"total_tokens": 7}}))
''' % (ARGS_LOG, MODE_FILE, MODE_FILE))
os.chmod(AGY, 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY
for k in ("GW_GEN_CHAT_SKIP_PERMISSIONS", "GW_AGY_PLAN_ALLOW", "GW_WARROOM_SKIP_PERMISSIONS", "GW_AGY_WRITE_ROLES"):
    os.environ.pop(k, None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
PROFILE = os.path.join(os.environ["HOME"], ".gemini")
os.makedirs(os.path.join(PROFILE, "antigravity-cli"))
SETTINGS = os.path.join(PROFILE, "antigravity-cli", "settings.json")
with open(SETTINGS, "w") as f:
    json.dump({"theme": "terminal", "permissions": {"allow": ["read_url(github.com)"]}}, f)
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


def set_mode(m):
    with open(MODE_FILE, "w") as f:
        f.write(m)


def last_call():
    with open(ARGS_LOG) as f:
        return json.loads(f.read().strip().splitlines()[-1])


def last_msg(conv_id):
    with db.get_connection() as conn:
        return dict(conn.execute("SELECT author, content FROM gen_messages WHERE conversation_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1",
                                 (conv_id,)).fetchone())


REPO = os.path.realpath(str(db.BASE_DIR))
conv = db.create_gen_conversation(title="VIEC-8: chat quyền")["id"]

print("[1] mặc định: không --dangerously-skip-permissions, có allow-rule chỉ đọc")
set_mode("ok")
reply, _, usage = db.call_agy_cli_turn(conv, "Endpoint chat Gen ở đâu trong main.py?")
call = last_call()
check("agy được gọi, trả lời thật", reply.startswith("Endpoint chat Gen") and "error" not in usage, f"{reply!r} {usage}")
check("argv KHÔNG có --dangerously-skip-permissions", "--dangerously-skip-permissions" not in call["argv"], str(call["argv"]))
check("argv vẫn --output-format json --print <prompt> --model <slug>",
      call["argv"][:3] == ["--output-format", "json", "--print"] and "--model" in call["argv"], str(call["argv"]))
work_dir = os.path.realpath(call["cwd"])
with open(SETTINGS) as f:
    st = json.load(f)
allow = st["permissions"]["allow"]
check("giữ khóa cũ (theme, read_url)", st.get("theme") == "terminal" and "read_url(github.com)" in allow, str(st))
check(f"read_file(<thư mục làm việc>) = read_file({work_dir})", f"read_file({work_dir})" in allow, str(allow))
check("read_file(<repo>) để đọc mã nguồn", f"read_file({REPO})" in allow, str(allow))
check("command(git log) / command(grep) / command(cat)", all(f"command({c})" in allow for c in ("git log", "grep", "cat")), str(allow))
check("không cấp quyền ghi (write_file / command(rm) / command(git push))",
      not any(r.startswith("write_file") or r in ("command(rm)", "command(git push)", "command(git commit)") for r in allow), str(allow))
n_rules = len(allow)
db.call_agy_cli_turn(conv, "lượt 2")
with open(SETTINGS) as f:
    check("gọi lần 2 không nhân đôi quy tắc", len(json.load(f)["permissions"]["allow"]) == n_rules)
call = last_call()
check("lượt 2 dùng --conversation của agy", "--conversation" in call["argv"] and "agy-c1" in call["argv"], str(call["argv"]))

print("[2] GW_GEN_CHAT_SKIP_PERMISSIONS=1 → có cờ (lối thoát cuối)")
os.environ["GW_GEN_CHAT_SKIP_PERMISSIONS"] = "1"
db.call_agy_cli_turn(conv, "lượt 3")
check("argv có --dangerously-skip-permissions", "--dangerously-skip-permissions" in last_call()["argv"], str(last_call()["argv"]))
os.environ["GW_GEN_CHAT_SKIP_PERMISSIONS"] = "0"
db.call_agy_cli_turn(conv, "lượt 4")
check("=0 → không có cờ", "--dangerously-skip-permissions" not in last_call()["argv"])
del os.environ["GW_GEN_CHAT_SKIP_PERMISSIONS"]

print("[3] agy bị từ chối quyền → lỗi rõ, không coi chuỗi rỗng là thành công")
set_mode("denied_empty")
reply, _, usage = db.call_agy_cli_turn(conv, "đọc file")
check("response rỗng + stderr 'no output produced … auto-denied' → PERMISSION_DENIED", reply == "" and usage.get("error") == "PERMISSION_DENIED", str(usage))
reason = db.describe_agy_error(usage)
check("lý do nêu bị từ chối quyền + thư mục được đọc + GW_GEN_CHAT_SKIP_PERMISSIONS",
      "từ chối quyền" in reason and REPO in reason and "GW_GEN_CHAT_SKIP_PERMISSIONS" in reason, reason)
set_mode("denied_text")
reply, _, usage = db.call_agy_cli_turn(conv, "đọc file")
check("response ngắn 'auto-denied' → PERMISSION_DENIED (không trả như câu trả lời)", reply == "" and usage.get("error") == "PERMISSION_DENIED", f"{reply!r} {usage}")
set_mode("denied_plain")
reply, _, usage = db.call_agy_cli_turn(conv, "đọc file")
check("stdout thường 'no output produced' → PERMISSION_DENIED", reply == "" and usage.get("error") == "PERMISSION_DENIED", f"{reply!r} {usage}")
set_mode("long")
reply, _, usage = db.call_agy_cli_turn(conv, "hỏi dài")
check("trả lời dài có nhắc 'auto-denied' → vẫn là trả lời thật", reply.startswith("Endpoint chat Gen") and "error" not in usage, str(usage))

print("[4] send_gen_chat / Orchestrator lưu lỗi rõ")
set_mode("denied_empty")
out = db.send_gen_chat(conv, "Ryan", "đọc main.py", "gemini 3.8 flash (high)")
m = last_msg(conv)
check("send_gen_chat: tin 'Gen (lỗi)' kèm lý do từ chối quyền", m["author"] == "Gen (lỗi)" and "từ chối quyền" in m["content"], str(m))
res = db.process_orch_instruction("đọc main.py")
check("Orchestrator: error True, mã PERMISSION_DENIED", res.get("error") is True and res.get("error_code") == "PERMISSION_DENIED", str(res)[:300])
msgs = db.get_orch_chat_messages()
check("Orchestrator: tin 'Orchestrator (lỗi)' + lý do từ chối quyền", msgs and msgs[-1]["author"] == "Orchestrator (lỗi)" and "từ chối quyền" in msgs[-1]["body"], str(msgs[-1:]))
check("Orchestrator: không --dangerously-skip-permissions", "--dangerously-skip-permissions" not in last_call()["argv"])
set_mode("ok")
out = db.send_gen_chat(conv, "Ryan", "endpoint?", "gemini 3.8 flash (high)")
m = last_msg(conv)
check("agy OK → tin 'Gen Core (agy CLI)' có nội dung thật", m["author"] == "Gen Core (agy CLI)" and "POST /api/gen/chat" in m["content"], str(m))

print("[5] GW_AGY_PLAN_ALLOW=0 → không đụng settings, vẫn không có cờ")
with open(SETTINGS, "w") as f:
    json.dump({"permissions": {"allow": []}}, f)
os.environ["GW_AGY_PLAN_ALLOW"] = "0"
db.call_agy_cli_turn(conv, "lượt 5")
with open(SETTINGS) as f:
    check("settings không đổi", json.load(f)["permissions"]["allow"] == [])
check("không có cờ", "--dangerously-skip-permissions" not in last_call()["argv"])
del os.environ["GW_AGY_PLAN_ALLOW"]

print("[6] rà mã nguồn: cờ chỉ còn ở hằng số / nhánh có điều kiện")
lines_with_flag = []
for name in sorted(os.listdir(os.path.join(ROOT, "backend"))):
    if not name.endswith(".py"):
        continue
    for i, line in enumerate(open(os.path.join(ROOT, "backend", name), encoding="utf-8"), 1):
        if "dangerously-skip-permissions" in line:
            lines_with_flag.append((name, i, line.strip()))
code_uses = [x for x in lines_with_flag if not x[2].startswith("#") and '"""' not in x[2]
             and not re.match(r'^(Alias|thêm|lệnh|- )', x[2])]
allowed = []
for name, i, line in code_uses:
    ok = (line.startswith("AGY_SKIP_PERMISSIONS_FLAG =") or line.startswith("FORBIDDEN_AGY_FLAGS =")
          or '"scope":' in line or "f\"--dangerously-skip-permissions: chỉ được đọc" in line)
    allowed.append(ok)
check("mọi dòng mã chứa cờ là hằng số / mô tả / directive_guard (không có lệnh agy nào gắn cờ cứng)", all(allowed),
      str([c for c, ok in zip(code_uses, allowed) if not ok]))
src = open(os.path.join(ROOT, "backend", "db.py"), encoding="utf-8").read()
turn = src.split("def call_agy_cli_turn")[1].split("\ndef ")[0]
check("call_agy_cli_turn chỉ thêm AGY_SKIP_PERMISSIONS_FLAG trong nhánh if skip_permissions (GW_GEN_CHAT_SKIP_PERMISSIONS)",
      re.search(r"skip_permissions = _env_on\(\"GW_GEN_CHAT_SKIP_PERMISSIONS\"\)", turn) is not None
      and re.search(r"if skip_permissions:\n\s+cmd\.append\(AGY_SKIP_PERMISSIONS_FLAG\)", turn) is not None
      and turn.count("AGY_SKIP_PERMISSIONS_FLAG") == 1, "")
src_lines = src.splitlines()
use_sites = [i for i, l in enumerate(src_lines) if "AGY_SKIP_PERMISSIONS_FLAG" in l and not l.startswith("AGY_SKIP_PERMISSIONS_FLAG =")]
guarded = [re.search(r"\bif\b", src_lines[i]) or re.search(r"^\s*if\b", src_lines[i - 1]) for i in use_sites]
check(f"mọi chỗ dùng AGY_SKIP_PERMISSIONS_FLAG ({len(use_sites)} chỗ) nằm trong nhánh if (env / vai + worktree)",
      use_sites and all(guarded), str([src_lines[i].strip() for i, g in zip(use_sites, guarded) if not g]))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

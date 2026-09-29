#!/usr/bin/env python3
"""
Test #3: chatroom gọi agy thật thay câu mẫu. Chạy không cần server/agy/tmux:
GW_AGY_BIN là script bash giả (echo "OK từ agy giả"; exit 0), GW_DISPATCH_REPO là
repo git tạm, GW_WORKTREE_ROOT là thư mục tạm → kiểm worktree wt/<session_id>,
chat_messages có trả lời thật (tác giả = session_id, có dòng exit=0) và dispatch_log.
Cập nhật TSK-29: tin @vai mặc định THỰC THI (không --mode plan); [đọc] để chỉ đọc.
"""
import os
import sys
import shutil
import subprocess
import tempfile

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-warroom-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

AGY_OK = os.path.join(FAKEBIN, "agy")
with open(AGY_OK, "w") as f:
    f.write('#!/bin/bash\necho "OK từ agy giả"; echo "cwd=$(pwd)"; echo "args=$*"; exit 0\n')
os.chmod(AGY_OK, 0o755)
AGY_429 = os.path.join(FAKEBIN, "agy-429")
with open(AGY_429, "w") as f:
    f.write("#!/bin/bash\necho 'RESOURCE_EXHAUSTED: Individual quota reached. Resets in 1h5m' >&2; exit 1\n")
os.chmod(AGY_429, 0o755)

# Repo git tạm để dispatch tạo worktree riêng của vai (không đụng repo thật)
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
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
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


def channel_rows(channel="war_room"):
    with db.get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT author, tag, body FROM chat_messages WHERE runtime_id = ? ORDER BY id", (channel,)).fetchall()]


print("[1] tin không có @vai → chỉ lưu, không trả lời")
before = len(channel_rows())
res = db.post_warroom_message(message="Hôm nay cần kiểm tra backend và api", author="Ryan (Owner)", wait=True)
rows = channel_rows()
check("status sent, dispatched rỗng", res["status"] == "sent" and res["dispatched"] == [], str(res))
check("chỉ thêm đúng 1 tin (của người gửi)", len(rows) == before + 1 and rows[-1]["author"] == "Ryan (Owner)", str(rows[-1:]))
check("không còn câu mẫu theo từ khóa", not any("Đã rõ chỉ thị" in r["body"] for r in rows))

print("[2] @backend → agy giả chạy trong worktree riêng, chế độ Làm (không --mode plan)")
res = db.post_warroom_message(message="@backend kiểm tra API /api/state", author="Ryan (Owner)", wait=True)
check("dispatched = gw-backend-agy", res["dispatched"] == ["gw-backend-agy"], str(res["dispatched"]))
check("mode=build (mặc định mới)", res.get("mode") == "build", str(res.get("mode")))
rows = channel_rows()
reply = rows[-1]
check("tác giả trả lời = session_id thật", reply["author"] == "gw-backend-agy", reply["author"])
check("body là output thật của agy giả", "OK từ agy giả" in reply["body"], reply["body"][:120])
check("body có dòng exit=0", "exit=0" in reply["body"], reply["body"][-40:])
# TSK-29: chế độ mặc định là build → KHÔNG có --mode plan, CÓ nhãn [Làm]
check("lệnh KHÔNG có --mode plan (chế độ Làm mặc định)", "--mode plan" not in reply["body"], reply["body"][:300])
check("body ghi nhãn [Làm]", "[Làm]" in reply["body"], reply["body"][:100])
wt_dir = os.path.join(os.environ["GW_WORKTREE_ROOT"], "gw-backend-agy")
check("worktree riêng tồn tại", os.path.exists(os.path.join(wt_dir, ".git")), wt_dir)
check("agy chạy với cwd = worktree", f"cwd={os.path.realpath(wt_dir)}" in reply["body"] or f"cwd={wt_dir}" in reply["body"], reply["body"][:300])
branches = subprocess.run(["git", "-C", DISPATCH_REPO, "branch", "--list", "wt/gw-backend-agy"], capture_output=True, text=True).stdout
check("nhánh wt/gw-backend-agy được tạo", "wt/gw-backend-agy" in branches, branches)
check("lệnh có --gemini_dir= và không có --sandbox", "args=--gemini_dir=" in reply["body"] and "--sandbox" not in reply["body"], reply["body"][:300])
with db.get_connection() as conn:
    dl = conn.execute("SELECT * FROM dispatch_log WHERE session_id='gw-backend-agy' ORDER BY id DESC LIMIT 1").fetchone()
check("dispatch_log có dòng exit_code=0 + report_path", dl is not None and dl["exit_code"] == 0 and dl["report_path"] and os.path.exists(dl["report_path"]), dict(dl) if dl else "none")
check("dispatch_log có started_at/finished_at", dl is not None and dl["started_at"] and dl["finished_at"])

print("[3] gọi lần 2 dùng lại worktree đã có; @Lead viết hoa cũng nhận")
res = db.post_warroom_message(message="@Backend và @Lead cho ý kiến", author="Gen", wait=True)
check("dispatched 2 vai theo thứ tự xuất hiện", res["dispatched"] == ["gw-backend-agy", "gw-lead-agy"], str(res["dispatched"]))
rows = channel_rows()
check("2 trả lời từ 2 session", [r["author"] for r in rows[-2:]] == ["gw-backend-agy", "gw-lead-agy"], str([r["author"] for r in rows[-2:]]))

print("[4] agy giả 429 → body là lỗi thật + quota_probe rate_limited")
os.environ["GW_AGY_BIN"] = AGY_429
res = db.post_warroom_message(message="@qa chạy test", author="Ryan (Owner)", wait=True)
reply = channel_rows()[-1]
check("tác giả gw-qa-agy", reply["author"] == "gw-qa-agy")
check("body báo 429 kèm output thật", "429" in reply["body"] and "RESOURCE_EXHAUSTED" in reply["body"], reply["body"][:200])
check("body có exit=1", "exit=1" in reply["body"])
with db.get_connection() as conn:
    pr = conn.execute("SELECT status, reset_at FROM quota_probe ORDER BY id DESC LIMIT 1").fetchone()
check("quota_probe rate_limited, reset 1h5m", pr and pr["status"] == "rate_limited" and pr["reset_at"] == "1h5m", dict(pr) if pr else "none")

print("[5] chế độ nền (wait=False) trả về ngay, trả lời đến sau")
os.environ["GW_AGY_BIN"] = AGY_OK
n_before = len(channel_rows())
res = db.post_warroom_message(message="@devops kiểm tra docker", author="Ryan (Owner)", wait=False)
check("trả về ngay với dispatched", res["dispatched"] == ["gw-devops-agy"])
import time  # noqa: E402
for _ in range(50):
    if len(channel_rows()) >= n_before + 2:
        break
    time.sleep(0.2)
rows = channel_rows()
check("trả lời nền đã vào kênh", len(rows) == n_before + 2 and rows[-1]["author"] == "gw-devops-agy", str([r["author"] for r in rows[-2:]]))

print("[6] mode=review → --mode plan (chỉ đọc); [đọc] sau @vai cũng chọn review")
os.environ["GW_AGY_BIN"] = AGY_OK
res_review = db.post_warroom_message(message="@lead xem file backend/db.py", author="Ryan (Owner)", wait=True, mode="review")
check("mode=review → mode=review trong response", res_review.get("mode") == "review", str(res_review.get("mode")))
reply_review = channel_rows()[-1]
check("mode=review → body có --mode plan", "--mode plan" in reply_review["body"], reply_review["body"][:300])
check("mode=review → body ghi nhãn [Đọc]", "[Đọc]" in reply_review["body"], reply_review["body"][:100])

res2 = db.post_warroom_message(message="@backend [đọc] xem file README.md", author="Ryan (Owner)", wait=True)
check("[đọc] sau @vai → effective_mode=review", res2.get("mode") == "review", str(res2.get("mode")))
reply_doc = channel_rows()[-1]
check("[đọc] → body ghi nhãn [Đọc]", "[Đọc]" in reply_doc["body"], reply_doc["body"][:100])
check("[đọc] → body có --mode plan", "--mode plan" in reply_doc["body"], reply_doc["body"][:300])

print("[7] tin có TSK-n → dùng worktree riêng của TSK-n qua assign_task_to_role(mode=build)")
with db.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO gen_conversations (id, project_id, title) VALUES (?, ?, ?)",
                 ("conv-test", "PRJ-GEN-WORKPLACE", "Test conv"))
    conn.execute("""
    INSERT INTO gen_session_todos (id, conversation_id, project_id, title, status, viec_ref, checklist_json)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("TSK-10", "conv-test", "PRJ-GEN-WORKPLACE", "Sửa lỗi auth", "pending", "VIEC-1", "[]"))
    conn.commit()

res_task = db.post_warroom_message(message="@backend thực hiện TSK-10 sửa lỗi auth", author="Ryan (Owner)", wait=True)
check("tin có TSK-n → mode=build", res_task.get("mode") == "build", str(res_task.get("mode")))
d0 = (res_task.get("dispatches") or [{}])[0]
check("tin có TSK-n → gán đúng task_id TSK-10", d0.get("task_id") == "TSK-10", str(d0))
tsk_wt = os.path.join(os.environ["GW_WORKTREE_ROOT"], "TSK-10")
check("tin có TSK-n → tạo và dùng worktree của TSK-10", os.path.exists(os.path.join(tsk_wt, ".git")), tsk_wt)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

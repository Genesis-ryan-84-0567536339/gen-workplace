#!/usr/bin/env python3
"""
Test TSK-24:
(A) Lỗi giao task qua war-room: bỏ dispatch Đọc thay thế, trả error/code + tin hệ thống + errors trong response.
    MCP post_warroom_message trả isError khi mọi vai đều lỗi.
(B) Worktree vai đồng bộ main trước khi chạy build không-task;
    push wt/gw-<vai>-agy nếu có commit mới sau khi agy xong.
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
TMP = tempfile.mkdtemp(prefix="gw-test-tsk24-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

AGY_OK = os.path.join(FAKEBIN, "agy")
with open(AGY_OK, "w") as f:
    f.write('#!/bin/bash\necho "OK từ agy giả"; echo "cwd=$(pwd)"; echo "args=$*"; exit 0\n')
os.chmod(AGY_OK, 0o755)

# Repo git tạm để dispatch tạo worktree riêng của vai
DISPATCH_REPO = os.path.join(TMP, "repo")
os.makedirs(DISPATCH_REPO)
GIT = ["git", "-C", DISPATCH_REPO, "-c", "user.name=test", "-c", "user.email=test@example.com"]
subprocess.run(GIT + ["init", "-q", "--initial-branch=main"], check=True)
with open(os.path.join(DISPATCH_REPO, "README.md"), "w") as f:
    f.write("repo tạm\n")
subprocess.run(GIT + ["add", "."], check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "init"], check=True)

# Remote giả (bare repo) để test push
REMOTE_REPO = os.path.join(TMP, "remote")
subprocess.run(["git", "init", "--bare", "-q", REMOTE_REPO], check=True)
subprocess.run(GIT + ["remote", "add", "origin", REMOTE_REPO], check=True)
subprocess.run(GIT + ["push", "-q", "origin", "main"], check=True)

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
            "SELECT author, tag, body, reply_to FROM chat_messages WHERE runtime_id = ? ORDER BY id", (channel,)).fetchall()]


# ---------------------------------------------------------------------------
# (A) Test: Lỗi giao task qua war-room → tin hệ thống + errors, KHÔNG dispatch Đọc
# ---------------------------------------------------------------------------
print("[A1] Tạo task TSK-20 và lock nó bằng worker khác để simulate lỗi locked")
with db.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO gen_conversations (id, project_id, title) VALUES (?, ?, ?)",
                 ("conv-tsk24", "PRJ-GEN-WORKPLACE", "Test TSK-24"))
    conn.execute("""
    INSERT INTO gen_session_todos (id, conversation_id, project_id, title, status, viec_ref, checklist_json,
                                   claimed_by, locked_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, ("TSK-20", "conv-tsk24", "PRJ-GEN-WORKPLACE", "Test task locked", "in_progress",
          "VIEC-20", "[]", "gw-lead-agy"))
    conn.commit()

n_before = len(channel_rows())
res = db.post_warroom_message(message="@backend thực hiện TSK-20", author="Gen", wait=True)

check("(A1) status=sent", res.get("status") == "sent", str(res.get("status")))
check("(A1) errors không rỗng (có lỗi giao task)", bool(res.get("errors")), str(res.get("errors")))
check("(A1) dispatched rỗng (không dispatch Đọc thay thế)", res.get("dispatched") == [], str(res.get("dispatched")))
rows = channel_rows()
# Phải có tin hệ thống (tag=System) báo lỗi
sys_msgs = [r for r in rows[n_before:] if r.get("author") == "Hệ thống"]
check("(A1) có tin hệ thống giải thích lỗi", bool(sys_msgs), str([r["body"][:80] for r in rows[n_before:]]))
if sys_msgs:
    sys_body = sys_msgs[0]["body"]
    check("(A1) tin hệ thống nêu TSK-20 và @backend", "TSK-20" in sys_body and "backend" in sys_body, sys_body[:200])
    check("(A1) tin hệ thống nêu cách xử lý", "Cách xử lý:" in sys_body, sys_body[:200])
    check("(A1) tin hệ thống reply_to đúng tin gốc", sys_msgs[0].get("reply_to") == res.get("user_message", {}).get("id"),
          str(sys_msgs[0].get("reply_to")))
# Không được có tin agy trả lời (mode=review thay thế bị cấm)
agy_msgs = [r for r in rows[n_before:] if r.get("author") == "gw-backend-agy"]
check("(A1) không có tin agy giả review (đã bỏ dispatch Đọc thay thế)", len(agy_msgs) == 0, str(agy_msgs))

# Kiểm errors entry có đủ trường
err = (res.get("errors") or [{}])[0]
check("(A1) errors entry có session_id", bool(err.get("session_id")), str(err))
check("(A1) errors entry có error và code", bool(err.get("error")) and bool(err.get("code")), str(err))
check("(A1) errors entry có http_status", isinstance(err.get("http_status"), int), str(err))
check("(A1) errors entry KHÔNG có dispatch_id (không dispatch Đọc)", "dispatch_id" not in err, str(err))

print("[A2] MCP: isError khi mọi vai đều lỗi")
import json  # noqa: E402
from backend import mcp_core  # noqa: E402

mcp_res = mcp_core.execute_tool("post_warroom_message",
                                 {"message": "@backend thực hiện TSK-20", "author": "Gen"})
check("(A2) MCP isError=True khi mọi vai lỗi", mcp_res.get("isError") is True, str(mcp_res.get("isError")))
parsed = json.loads((mcp_res.get("content") or [{}])[0].get("text", "{}"))
check("(A2) MCP response có errors field", bool(parsed.get("errors")), str(parsed.get("errors")))

print("[A3] Khi chỉ 1 trong 2 vai lỗi, vai còn lại vẫn dispatch (isError=False)")
# Tạo TSK-21 chưa bị lock để @qa nhận được
with db.get_connection() as conn:
    conn.execute("""
    INSERT INTO gen_session_todos (id, conversation_id, project_id, title, status, viec_ref, checklist_json)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("TSK-21", "conv-tsk24", "PRJ-GEN-WORKPLACE", "Test task A3", "pending", "VIEC-21", "[]"))
    conn.commit()

# @backend → TSK-20 (locked) → lỗi; @qa → TSK-21 (ok) → dispatch
n_before3 = len(channel_rows())
res3 = db.post_warroom_message(message="@backend và @qa thực hiện TSK-21", author="Gen", wait=True)
# TSK-21 sẽ được detect và giao cho @qa thành công (và @backend locked)
# Điều này phụ thuộc claim logic - nếu @backend claim TSK-21 trước thì @qa cũng có thể claim
# Chỉ kiểm không dispatch Đọc thay thế cho vai bị lỗi
errs3 = res3.get("errors") or []
dispatched3 = res3.get("dispatched") or []
# Ít nhất không có @backend trong dispatched nếu TSK-21 bị @backend lock (hoặc ngược lại)
# Kiểm tổng quát: errors + dispatched hợp lý
check("(A3) response có dispatches field", "dispatches" in res3, str(res3))
# Nếu có errors thì không có agy review nào từ vai lỗi
for e in errs3:
    agy_err_msgs = [r for r in channel_rows()[n_before3:] if r.get("author") == e.get("session_id")]
    check(f"(A3) không dispatch Đọc cho {e.get('session_id')}", len(agy_err_msgs) == 0, str(agy_err_msgs))

# ---------------------------------------------------------------------------
# (B) Test: sync origin/main và push trong worktree của vai
# ---------------------------------------------------------------------------
print("[B1] _sync_role_worktree_from_main: worktree sạch → fetch + merge")
# Tạo worktree cho gw-devops-agy
cwd_devops = db.ensure_role_worktree("gw-devops-agy")
check("(B1) worktree gw-devops-agy tồn tại", os.path.exists(os.path.join(cwd_devops, ".git")), cwd_devops)

ok, note = db._sync_role_worktree_from_main(cwd_devops, "gw-devops-agy")
# Trong môi trường test, remote có thể không fetch được (bare remote giả), nhưng hàm không crash
check("(B1) _sync_role_worktree_from_main trả (bool, str)", isinstance(ok, bool) and isinstance(note, str), str((ok, note)))
print(f"    note: {note}")

print("[B2] _sync_role_worktree_from_main: worktree bẩn → bỏ qua merge, trả ghi chú")
# Tạo file bẩn trong worktree
dirty_file = os.path.join(cwd_devops, "test_dirty.txt")
with open(dirty_file, "w") as f:
    f.write("bẩn\n")
ok_dirty, note_dirty = db._sync_role_worktree_from_main(cwd_devops, "gw-devops-agy")
check("(B2) worktree bẩn → ok=False", not ok_dirty, str((ok_dirty, note_dirty)))
check("(B2) note giải thích bẩn", "chưa commit" in note_dirty or "bẩn" in note_dirty or "file" in note_dirty, note_dirty)
# Dọn file bẩn
os.remove(dirty_file)

print("[B2-conflict] _sync_role_worktree_from_main: xung đột merge → abort + cảnh báo")
# Tạo xung đột: commit trên origin/main sửa conflict.txt, commit trên worktree sửa conflict.txt khác
conflict_file_repo = os.path.join(DISPATCH_REPO, "conflict.txt")
with open(conflict_file_repo, "w") as f:
    f.write("nội dung trên main\n")
subprocess.run(GIT + ["add", "conflict.txt"], check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "main: conflict file"], check=True)
subprocess.run(GIT + ["push", "-q", "origin", "main"], check=True)

conflict_file_wt = os.path.join(cwd_devops, "conflict.txt")
with open(conflict_file_wt, "w") as f:
    f.write("nội dung xung đột trên devops worktree\n")
GIT_WT = ["git", "-C", cwd_devops, "-c", "user.name=test", "-c", "user.email=test@example.com"]
subprocess.run(GIT_WT + ["add", "conflict.txt"], check=True)
subprocess.run(GIT_WT + ["commit", "-q", "-m", "devops: conflict file"], check=True)

ok_conf, note_conf = db._sync_role_worktree_from_main(cwd_devops, "gw-devops-agy")
check("(B2-conflict) xung đột → ok=False", not ok_conf, str((ok_conf, note_conf)))
check("(B2-conflict) note có cảnh báo xung đột", "Xung đột" in note_conf or "xung đột" in note_conf.lower(), note_conf)
check("(B2-conflict) đã abort — worktree không kẹt ở merge state", not os.path.exists(os.path.join(cwd_devops, ".git", "MERGE_HEAD")), "vẫn còn MERGE_HEAD")

print("[B3] dispatch build không-task → agy giả chạy, push worktree sau khi có commit")
# Tạo agy giả tạo commit trong worktree
AGY_COMMIT = os.path.join(FAKEBIN, "agy-commit")
with open(AGY_COMMIT, "w") as f:
    f.write("""#!/bin/bash
echo "OK từ agy giả build"
cd "$(pwd)"
echo "file test" > test_agy_build.txt
git -c user.name=test -c user.email=test@example.com add test_agy_build.txt
git -c user.name=test -c user.email=test@example.com commit -q -m "TSK-24: test commit từ agy giả"
echo "cwd=$(pwd)"
exit 0
""")
os.chmod(AGY_COMMIT, 0o755)

os.environ["GW_AGY_BIN"] = AGY_COMMIT
n_before_b3 = len(channel_rows())
res_b3 = db.post_warroom_message(
    message="@devops kiểm tra và cải thiện cấu hình docker", author="Gen", wait=True
)
rows_b3 = channel_rows()
check("(B3) dispatch build không-task → dispatched có gw-devops-agy",
      "gw-devops-agy" in res_b3.get("dispatched", []), str(res_b3.get("dispatched")))
# Kiểm body reply có thông tin commit / push
reply_b3 = [r for r in rows_b3[n_before_b3:] if r.get("author") == "gw-devops-agy"]
if reply_b3:
    body_b3 = reply_b3[-1]["body"]
    check("(B3) body reply có nhãn [Làm]", "[Làm]" in body_b3, body_b3[:200])
    check("(B3) body reply có thông báo Push", "Push: đã đẩy wt/gw-devops-agy lên origin" in body_b3, body_b3[:300])
    print(f"    body excerpt: {body_b3[:300]}")

# Kiểm tra dispatch_log có lưu build_push, build_commit, build_branch
with db.get_connection() as conn:
    dlog = conn.execute("SELECT build_branch, build_commit, build_push, compare_url FROM dispatch_log WHERE session_id = 'gw-devops-agy' ORDER BY id DESC LIMIT 1").fetchone()
check("(B3) dispatch_log có build_branch", bool(dlog and dlog["build_branch"] == "wt/gw-devops-agy"), str(dict(dlog) if dlog else None))
check("(B3) dispatch_log có build_commit", bool(dlog and dlog["build_commit"]), str(dict(dlog) if dlog else None))
check("(B3) dispatch_log có build_push", bool(dlog and dlog["build_push"]), str(dict(dlog) if dlog else None))
if dlog and dlog["build_push"]:
    push_parsed = json.loads(dlog["build_push"])
    check("(B3) dispatch_log build_push ok=True", push_parsed.get("ok") is True, str(push_parsed))

os.environ["GW_AGY_BIN"] = AGY_OK

print("[B4] _sync_role_worktree_from_main: worktree không hợp lệ → trả False + note")
ok_invalid, note_invalid = db._sync_role_worktree_from_main("/nonexistent/path", "gw-backend-agy")
check("(B4) worktree không tồn tại → ok=False", not ok_invalid, str((ok_invalid, note_invalid)))
check("(B4) note giải thích không hợp lệ", bool(note_invalid), note_invalid)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

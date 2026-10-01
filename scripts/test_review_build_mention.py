#!/usr/bin/env python3
"""
Test TSK-37 (Issue #38):
1. Mention mới trong war-room: @build (=backend Làm) và @review (=qa Rà soát luôn).
   Các vai cũ (@backend, @devops, @qa, @lead) giữ nguyên hành vi.
   @security / @frontend vẫn trả retired_role.
2. SOP rà soát roles/review.md được đưa vào đầu prompt build_agy_readonly_prompt.
3. Cột dispatch_log.sop được tạo, ghi "build" / "build.node" / "review" và trả về trong
   /api/dispatch/log và wait_worker_result.
4. UI frontend/index.html ẩn vai lead khỏi ô "Giao cho" và danh sách vai giao việc;
   backend vẫn nhận vai lead khi gọi trực tiếp.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-review-build-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)

AGY_OK = os.path.join(FAKEBIN, "agy")
with open(AGY_OK, "w") as f:
    f.write('#!/bin/bash\necho "OK agy mock"; exit 0\n')
os.chmod(AGY_OK, 0o755)

DISPATCH_REPO = os.path.join(TMP, "repo")
os.makedirs(DISPATCH_REPO)
GIT = ["git", "-C", DISPATCH_REPO, "-c", "user.name=test", "-c", "user.email=test@example.com"]
subprocess.run(GIT + ["init", "-q"], check=True)
with open(os.path.join(DISPATCH_REPO, "README.md"), "w") as f:
    f.write("repo test\n")
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
from backend import db, agy_build  # noqa: E402

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


print("[1] Kiểm tra định nghĩa @build và @review trong WARROOM_ROLE_SESSIONS và regex")
check("WARROOM_ROLE_SESSIONS có 'build' trỏ về gw-backend-agy", db.WARROOM_ROLE_SESSIONS.get("build") == "gw-backend-agy")
check("WARROOM_ROLE_SESSIONS có 'review' trỏ về gw-qa-agy", db.WARROOM_ROLE_SESSIONS.get("review") == "gw-qa-agy")
check("WARROOM_ROLE_SESSIONS vẫn giữ 4 vai cũ", {"backend", "devops", "qa", "lead"}.issubset(db.WARROOM_ROLE_SESSIONS))
check("WARROOM_ROLE_SESSIONS không có security/frontend", "security" not in db.WARROOM_ROLE_SESSIONS and "frontend" not in db.WARROOM_ROLE_SESSIONS)

mentions_found = db.WARROOM_MENTION_RE.findall("@build làm việc này và @review kiểm tra lại")
check("WARROOM_MENTION_RE nhận diện cả @build và @review", [m.lower() for m in mentions_found] == ["build", "review"], str(mentions_found))

print("\n[2] Kiểm tra SOP rà soát roles/review.md và tích hợp vào prompt")
review_sop_path = os.path.join(ROOT, "roles", "review.md")
check("roles/review.md tồn tại", os.path.isfile(review_sop_path))
with open(review_sop_path, "r", encoding="utf-8") as f:
    sop_content = f.read()

check("roles/review.md có tiêu đề chuẩn", "SOP chế độ \"Rà soát\" (review)" in sop_content)
check("roles/review.md có mục Kết luận", "Kết luận" in sop_content)
check("roles/review.md có mục Bằng chứng file:dòng", "Bằng chứng file:dòng" in sop_content)
check("roles/review.md có mục Đề xuất", "Đề xuất" in sop_content)
check("roles/review.md yêu cầu không sửa file / chỉ đọc", "không sửa file" in sop_content.lower() or "chỉ đọc" in sop_content.lower())

prompt_ro = db.build_agy_readonly_prompt("Rà soát mã nguồn", "", "gw-qa-agy")
check("build_agy_readonly_prompt đưa nội dung roles/review.md vào đầu prompt", prompt_ro.startswith(sop_content.strip()), prompt_ro[:100])
check("build_agy_readonly_prompt vẫn có phần luật CHẾ ĐỘ CHỈ ĐỌC", "CHẾ ĐỘ CHỈ ĐỌC" in prompt_ro)

print("\n[3] Kiểm tra cột dispatch_log.sop và các giá trị khi dispatch")
with db.get_connection() as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dispatch_log)").fetchall()]
check("dispatch_log có cột sop", "sop" in cols, str(cols))

did_test_build = db.start_dispatch_log("gw-backend-agy", kind="warroom", sop="build")
row_b = db._get_dispatch_row(did_test_build)
check("start_dispatch_log lưu sop='build'", row_b.get("sop") == "build")

did_test_rev = db.start_dispatch_log("gw-qa-agy", kind="warroom", sop="review")
row_r = db._get_dispatch_row(did_test_rev)
check("start_dispatch_log lưu sop='review'", row_r.get("sop") == "review")

dlogs = db.get_dispatch_log(limit=5)
found_b = next((x for x in dlogs if x["id"] == did_test_build), None)
check("get_dispatch_log trả trường sop='build'", found_b and found_b.get("sop") == "build")

res_wait = db.wait_worker_result(dispatch_id=did_test_build, timeout_sec=1)
check("wait_worker_result trả trường sop='build'", res_wait.get("sop") == "build")

print("\n[4] Kiểm tra post_warroom_message với @build và @review")
# Test @build: tương đương @backend Làm
res_build = db.post_warroom_message("PRJ-GEN-WORKPLACE", "war_room", "Lead", "@build hãy cập nhật tính năng mới", wait=True)
dispatches_build = res_build.get("dispatches") or []
check("@build sinh ra 1 dispatch", len(dispatches_build) == 1, str(dispatches_build))
if dispatches_build:
    d = dispatches_build[0]
    check("@build chạy bằng gw-backend-agy", d.get("session_id") == "gw-backend-agy")
    check("@build mặc định chạy mode='build'", d.get("mode") == "build")
    r = db._get_dispatch_row(d.get("dispatch_id"))
    check("@build lưu dispatch_log.sop='build'", r and r.get("sop") == "build")

# Test @review: chạy bằng gw-qa-agy, LUÔN mode='review' dù không có [đọc]
res_rev = db.post_warroom_message("PRJ-GEN-WORKPLACE", "war_room", "Lead", "@review kiểm tra logic hàm mới", wait=True)
dispatches_rev = res_rev.get("dispatches") or []
check("@review sinh ra 1 dispatch", len(dispatches_rev) == 1, str(dispatches_rev))
if dispatches_rev:
    d = dispatches_rev[0]
    check("@review chạy bằng gw-qa-agy", d.get("session_id") == "gw-qa-agy")
    check("@review luôn chạy mode='review'", d.get("mode") == "review")
    r = db._get_dispatch_row(d.get("dispatch_id"))
    check("@review lưu dispatch_log.sop='review'", r and r.get("sop") == "review")

# Test @build [đọc]: cờ [đọc] chuyển sang review
res_build_read = db.post_warroom_message("PRJ-GEN-WORKPLACE", "war_room", "Lead", "@build [đọc] xem lại cấu trúc", wait=True)
dispatches_br = res_build_read.get("dispatches") or []
check("@build [đọc] chuyển sang mode='review'", len(dispatches_br) == 1 and dispatches_br[0].get("mode") == "review")
if dispatches_br:
    r = db._get_dispatch_row(dispatches_br[0].get("dispatch_id"))
    check("@build [đọc] lưu dispatch_log.sop='review'", r and r.get("sop") == "review")

# Test vai cũ @backend, @devops, @qa, @lead giữ nguyên hành vi
res_backend = db.post_warroom_message("PRJ-GEN-WORKPLACE", "war_room", "Lead", "@backend làm task", wait=True)
d_be = (res_backend.get("dispatches") or [{}])[0]
check("@backend mặc định mode='build'", d_be.get("mode") == "build" and d_be.get("session_id") == "gw-backend-agy")

res_qa_read = db.post_warroom_message("PRJ-GEN-WORKPLACE", "war_room", "Lead", "@qa [đọc] rà soát lại", wait=True)
d_qa = (res_qa_read.get("dispatches") or [{}])[0]
check("@qa [đọc] mode='review'", d_qa.get("mode") == "review" and d_qa.get("session_id") == "gw-qa-agy")

# Test vai retired (@security, @frontend)
res_sec = db.post_warroom_message("PRJ-GEN-WORKPLACE", "war_room", "Lead", "@security kiểm tra")
check("@security bị từ chối với code retired_role", res_sec.get("code") == "retired_role")

res_fe = db.post_warroom_message("PRJ-GEN-WORKPLACE", "war_room", "Lead", "@frontend sửa giao diện")
check("@frontend bị từ chối với code retired_role", res_fe.get("code") == "retired_role")

print("\n[5] Kiểm tra UI frontend/index.html ẩn vai lead khỏi ô Giao cho")
index_html_path = os.path.join(ROOT, "frontend", "index.html")
with open(index_html_path, "r", encoding="utf-8") as f:
    html_src = f.read()

# Kiểm tra hubAssignableRoles lọc bỏ vai lead
check("hubAssignableRoles có lọc bỏ vai lead", "r.key !== 'lead'" in html_src or "r.key != 'lead'" in html_src)

# Kiểm tra backend vẫn nhận lead nếu gọi trực tiếp
check("Backend resolve_role_session nhận diện sid của lead", db.resolve_role_session("lead") == "gw-lead-agy")
check("Backend resolve_role_session nhận diện gw-lead-agy", db.resolve_role_session("gw-lead-agy") == "gw-lead-agy")

print(f"\n==========================================")
print(f"Tổng kết: {PASSED} passed, {FAILED} failed")
print(f"==========================================")

if FAILED > 0:
    sys.exit(1)

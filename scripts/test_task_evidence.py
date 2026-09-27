#!/usr/bin/env python3
"""
Test #4: complete_task chỉ nhận bằng chứng kiểm được (commit SHA có trong repo /
file tồn tại / URL PR GitHub). Chạy không cần server, không cần agy/tmux:
tạo DB tạm qua DATA_DIR, claim rồi complete với bằng chứng sai → lỗi, với SHA HEAD thật → ok.
"""
import os
import sys
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-evidence-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
# tmux giả (thoát 1) để seed_tmux_sessions không tạo phiên thật trên máy test
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db  # noqa: E402

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


head_sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
print(f"HEAD repo: {head_sha}")

print("[1] verify_evidence_ref")
check("rỗng → lỗi", db.verify_evidence_ref("")[0] is False)
check("câu chữ mẫu → lỗi", db.verify_evidence_ref("100% test suite pass (68ms auto-wake)")[0] is False)
check("SHA không tồn tại → lỗi", db.verify_evidence_ref("deadbeefcafe")[0] is False)
ok, by, _ = db.verify_evidence_ref(head_sha)
check("SHA HEAD → git:commit", ok and by == "git:commit", f"({by})")
ok, by, _ = db.verify_evidence_ref(head_sha[:7])
check("SHA HEAD rút gọn 7 ký tự → git:commit", ok and by == "git:commit", f"({by})")
ok, by, _ = db.verify_evidence_ref("docs/SSOT_ORIGINAL_SPEC.md")
check("file tương đối repo → file", ok and by == "file", f"({by})")
ok, by, _ = db.verify_evidence_ref(os.path.join(ROOT, "backend", "db.py"))
check("file tuyệt đối → file", ok and by == "file", f"({by})")
os.makedirs(os.path.join(os.environ["HOME"], "gw-reports"), exist_ok=True)
with open(os.path.join(os.environ["HOME"], "gw-reports", "r1.md"), "w") as f:
    f.write("báo cáo")
ok, by, _ = db.verify_evidence_ref("~/gw-reports/r1.md")
check("~/gw-reports/... → file", ok and by == "file", f"({by})")
check("file không tồn tại → lỗi", db.verify_evidence_ref("docs/khong-co.md")[0] is False)
ok, by, _ = db.verify_evidence_ref("https://github.com/acme/gen-workplace/pull/12")
check("URL PR GitHub → github:pr", ok and by == "github:pr", f"({by})")
check("URL không phải PR → lỗi", db.verify_evidence_ref("https://github.com/acme/gen-workplace/issues/12")[0] is False)

print("[2] seed: task done phải có bằng chứng kiểm được")
with db.get_connection() as conn:
    rows = conn.execute("SELECT id, status, evidence_ref, verified_by FROM todos ORDER BY id").fetchall()
for r in rows:
    if r["status"] == "done":
        check(f"{r['id']} done có evidence kiểm được ({r['evidence_ref']}, {r['verified_by']})",
              db.verify_evidence_ref(r["evidence_ref"])[0] and r["verified_by"] in ("git:commit", "file", "github:pr"))
todo08 = next(r for r in rows if r["id"] == "TODO-08")
check("TODO-08 (không có bằng chứng thật) → queued, evidence rỗng", todo08["status"] == "queued" and todo08["evidence_ref"] == "")

print("[3] claim + complete với bằng chứng sai → lỗi, trạng thái giữ nguyên")
res = db.claim_task("gw-qa-agy", "TODO-12")
check("claim TODO-12", res.get("status") == "claimed", str(res))
res = db.complete_task("gw-qa-agy", "TODO-12", "100% test suite pass (68ms auto-wake)")
check("complete với câu chữ mẫu → error", "error" in res, str(res))
res = db.complete_task("gw-qa-agy", "TODO-12", "deadbeef")
check("complete với SHA lạ → error", "error" in res, str(res))
with db.get_connection() as conn:
    r = conn.execute("SELECT status, evidence_ref, assigned_session_id FROM todos WHERE id = 'TODO-12'").fetchone()
check("TODO-12 vẫn in_progress, evidence rỗng", r["status"] == "in_progress" and r["evidence_ref"] == "" and r["assigned_session_id"] == "gw-qa-agy", dict(r))

print("[4] complete với SHA HEAD thật → ok, verified_by = git:commit")
res = db.complete_task("gw-qa-agy", "TODO-12", head_sha, verified_by="Lead Architect")
check("complete ok", res.get("status") == "completed", str(res))
check("verified_by = git:commit (không còn 'Lead Architect')", res.get("verified_by") == "git:commit", str(res))
with db.get_connection() as conn:
    r = conn.execute("SELECT status, evidence_ref, verified_by FROM todos WHERE id = 'TODO-12'").fetchone()
check("DB: done + SHA + git:commit", r["status"] == "done" and r["evidence_ref"] == head_sha and r["verified_by"] == "git:commit", dict(r))

print("[5] task không tồn tại → error")
res = db.complete_task("gw-qa-agy", "TODO-KHONG-CO", head_sha)
check("Task not found", "error" in res, str(res))

print("[6] migrate: done với evidence giả → review")
with db.get_connection() as conn:
    conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status, evidence_ref, verified_by) VALUES ('TODO-99', 'RM-05', 'PRJ-GEN-WORKPLACE', 'Giả', 'QA Tester', 'done', '100% test suite pass (68ms auto-wake)', 'Lead Architect')")
n = db.migrate_unverified_done_tasks()
with db.get_connection() as conn:
    r = conn.execute("SELECT status FROM todos WHERE id = 'TODO-99'").fetchone()
    r12 = conn.execute("SELECT status FROM todos WHERE id = 'TODO-12'").fetchone()
check("TODO-99 → review", r["status"] == "review", f"n={n}")
check("TODO-12 (SHA thật) vẫn done", r12["status"] == "done")

print("[7] reclaim_stalled_tasks")
with db.get_connection() as conn:
    conn.execute("UPDATE todos SET status='in_progress', assigned_session_id='gw-devops-agy', locked_at=datetime('now', '-1 hour') WHERE id='TODO-13'")
res = db.reclaim_stalled_tasks(300)
check("thu hồi 1 task treo", res.get("reclaimed_count") == 1, str(res))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

#!/usr/bin/env python3
"""
Test #4 + #16: complete_task chỉ nhận bằng chứng kiểm được, không ghi đè task done, claim nguyên tử.
Chạy không cần server, không cần agy/tmux, KHÔNG gọi mạng thật (urllib.request.urlopen bị thay bằng bản giả):
- verify_evidence_ref: commit SHA / file không rỗng trong ~/gw-reports·repo·worktree / dispatch:<id> / warroom:<id> /
  URL PR GitHub (GitHub API giả: 200, 404, 403, mất mạng).
- complete_task lần 2 trên task done bị chặn; force=True ghi đè và ghi task_evidence_audit.
- claim_task: 2 worker (thread) cùng claim → đúng 1 người thắng, người thua nhận held_by; claim lại khi khóa quá hạn.
- DB cũ (thiếu cột claimed_by/locked_at, thiếu bảng audit) → init_db chạy lại không lỗi; task done cũ không bị hạ cấp.
"""
import io
import json
import os
import sys
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

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
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "worktrees")
os.environ.pop("GITHUB_TOKEN", None)
os.environ.pop("GW_RECLAIM_TIMEOUT_SEC", None)
os.environ.pop("GW_RECLAIM_INTERVAL_SEC", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

# ---- GitHub API giả: không bao giờ gọi mạng thật trong test ----
GH_CALLS = []
GH_MODE = {"mode": "ok"}
KNOWN_PRS = {("acme", "gen-workplace", "12"): {"number": 12, "state": "open", "merged_at": None}}


class _FakeResp(io.BytesIO):
    status = 200

    def getcode(self):
        return 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_urlopen(req, timeout=None, **kw):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    headers = dict(req.header_items()) if hasattr(req, "header_items") else {}
    GH_CALLS.append({"url": url, "timeout": timeout, "headers": headers})
    if not url.startswith("https://api.github.com/repos/"):
        raise AssertionError(f"test gọi URL ngoài GitHub API: {url}")
    if GH_MODE["mode"] == "offline":
        raise urllib.error.URLError("[Errno -3] Temporary failure in name resolution")
    if GH_MODE["mode"] == "ratelimit":
        raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, io.BytesIO(b"{}"))
    parts = url.split("/repos/", 1)[1].split("/")  # owner, repo, 'pulls', n
    key = (parts[0], parts[1], parts[3])
    if key not in KNOWN_PRS:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"message":"Not Found"}'))
    return _FakeResp(json.dumps(KNOWN_PRS[key]).encode())


urllib.request.urlopen = fake_urlopen

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
REPORTS = os.path.join(os.environ["HOME"], "gw-reports")
os.makedirs(REPORTS, exist_ok=True)

print("[1] verify_evidence_ref: SHA + file")
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
check("file tuyệt đối trong repo → file", ok and by == "file", f"({by})")
with open(os.path.join(REPORTS, "r1.md"), "w") as f:
    f.write("báo cáo")
ok, by, _ = db.verify_evidence_ref("~/gw-reports/r1.md")
check("~/gw-reports/... → file", ok and by == "file", f"({by})")
check("file không tồn tại → lỗi", db.verify_evidence_ref("docs/khong-co.md")[0] is False)
OUTSIDE = os.path.join(TMP, "outside.txt")
with open(OUTSIDE, "w") as f:
    f.write("không phải báo cáo")
ok, _, msg = db.verify_evidence_ref(OUTSIDE)
check("file có nội dung nhưng ngoài ~/gw-reports·repo·worktree → lỗi", not ok and "ngoài" in msg, msg)
check("/etc/hostname (file hệ thống bất kỳ) → lỗi", db.verify_evidence_ref("/etc/hostname")[0] is False)
open(os.path.join(REPORTS, "rong.md"), "w").close()
ok, _, msg = db.verify_evidence_ref("~/gw-reports/rong.md")
check("file rỗng trong ~/gw-reports → lỗi", not ok and "rỗng" in msg, msg)
check("'../' thoát khỏi repo → lỗi", db.verify_evidence_ref("../" * 8 + "etc/hostname")[0] is False)
os.symlink(OUTSIDE, os.path.join(REPORTS, "link.md"))
check("symlink trong ~/gw-reports trỏ ra ngoài → lỗi", db.verify_evidence_ref("~/gw-reports/link.md")[0] is False)
check("file trong .git → lỗi", db.verify_evidence_ref(".git/HEAD")[0] is False)
check("thư mục (không phải file) → lỗi", db.verify_evidence_ref("docs")[0] is False)
WT = os.path.join(os.environ["GW_WORKTREE_ROOT"], "gw-qa-agy")
os.makedirs(WT)
with open(os.path.join(WT, "ket-qua.txt"), "w") as f:
    f.write("ok")
ok, by, _ = db.verify_evidence_ref(os.path.join(WT, "ket-qua.txt"))
check("file không rỗng trong worktree của vai → file", ok and by == "file", f"({by})")

print("[2] verify_evidence_ref: URL PR GitHub qua API (giả, không gọi mạng)")
check("chưa có lượt gọi GitHub nào trước mục này", GH_CALLS == [], str(GH_CALLS))
ok, by, msg = db.verify_evidence_ref("https://github.com/acme/gen-workplace/pull/12")
check("PR có thật (API 200) → github:pr", ok and by == "github:pr", f"({by}) {msg}")
check("gọi đúng https://api.github.com/repos/acme/gen-workplace/pulls/12, có timeout ngắn",
      GH_CALLS and GH_CALLS[-1]["url"] == "https://api.github.com/repos/acme/gen-workplace/pulls/12"
      and GH_CALLS[-1]["timeout"] and GH_CALLS[-1]["timeout"] <= 10, str(GH_CALLS[-1:]))
check("không có GITHUB_TOKEN → không gửi Authorization", "Authorization" not in GH_CALLS[-1]["headers"], str(GH_CALLS[-1]))
ok, by, msg = db.verify_evidence_ref("https://github.com/khong-ton-tai/repo-gia/pull/999999")
check("PR giả (API 404) → lỗi", not ok and by == "" and "404" in msg, msg)
GH_MODE["mode"] = "offline"
ok, _, msg = db.verify_evidence_ref("https://github.com/acme/gen-workplace/pull/12")
check("mất mạng → từ chối, lý do rõ (không im lặng cho qua)", not ok and "Không gọi được GitHub API" in msg, msg)
GH_MODE["mode"] = "ratelimit"
ok, _, msg = db.verify_evidence_ref("https://github.com/acme/gen-workplace/pull/12")
check("GitHub 403 (rate limit) → từ chối, lý do rõ", not ok and "403" in msg, msg)
GH_MODE["mode"] = "ok"
os.environ["GITHUB_TOKEN"] = "tok-test"
db.verify_evidence_ref("https://github.com/acme/gen-workplace/pull/12")
check("có GITHUB_TOKEN → gửi Authorization: Bearer", GH_CALLS[-1]["headers"].get("Authorization") == "Bearer tok-test", str(GH_CALLS[-1]))
del os.environ["GITHUB_TOKEN"]
check("URL không phải PR → lỗi", db.verify_evidence_ref("https://github.com/acme/gen-workplace/issues/12")[0] is False)

print("[3] fixture todos (không seed giả)")
with db.get_connection() as conn:
    n_seed = conn.execute("SELECT count(*) FROM todos").fetchone()[0]
    conn.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-T5', 'PRJ-GEN-WORKPLACE', 'Roadmap test', '', 2, 'queued', 1)")
    for tid in ("TODO-T12", "TODO-T13", "TODO-T14", "TODO-T15"):
        conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES (?, 'RM-T5', 'PRJ-GEN-WORKPLACE', ?, 'QA Tester', 'queued')", (tid, f"Fixture {tid}"))
    conn.commit()
check("bảng todos trống sau khi khởi tạo (không seed giả)", n_seed == 0, str(n_seed))

print("[4] verify_evidence_ref: dispatch:<id> và warroom:<id>")


def add_dispatch(status, task_id="", exit_code=0, finished=True, session="gw-qa-agy"):
    with db.get_connection() as conn:
        cur = conn.execute("""INSERT INTO dispatch_log (session_id, command, exit_code, report_path, started_at, finished_at, task_id, status, kind)
                              VALUES (?, 'agy ...', ?, '', '2026-09-28 10:00:00', ?, ?, ?, 'warroom')""",
                           (session, exit_code, "2026-09-28 10:01:00" if finished else "", task_id, status))
        conn.commit()
        return cur.lastrowid


d_done = add_dispatch("done", task_id="TODO-T12")
d_other = add_dispatch("done", task_id="TODO-T99")
d_notask = add_dispatch("done", task_id="")
d_failed = add_dispatch("failed", task_id="TODO-T12", exit_code=1)
d_running = add_dispatch("running", task_id="TODO-T12", exit_code=None, finished=False)
d_legacy = add_dispatch("", task_id="TODO-T12", exit_code=0)
ok, by, msg = db.verify_evidence_ref(f"dispatch:{d_done}", task_id="TODO-T12")
check("dispatch done khớp task → dispatch", ok and by == "dispatch", f"({by}) {msg}")
check("dispatch done, không truyền task_id → nhận", db.verify_evidence_ref(f"dispatch:{d_done}")[0] is True)
ok, _, msg = db.verify_evidence_ref(f"dispatch:{d_other}", task_id="TODO-T12")
check("dispatch done của task khác → lỗi", not ok and "TODO-T99" in msg, msg)
check("dispatch done không gắn task → nhận", db.verify_evidence_ref(f"dispatch:{d_notask}", task_id="TODO-T12")[0] is True)
ok, _, msg = db.verify_evidence_ref(f"dispatch:{d_failed}", task_id="TODO-T12")
check("dispatch failed → lỗi", not ok and "failed" in msg, msg)
check("dispatch running → lỗi", db.verify_evidence_ref(f"dispatch:{d_running}", task_id="TODO-T12")[0] is False)
check("dispatch cũ (status rỗng, exit 0, đã xong) → nhận", db.verify_evidence_ref(f"dispatch:{d_legacy}", task_id="TODO-T12")[0] is True)
check("dispatch không tồn tại → lỗi", db.verify_evidence_ref("dispatch:999999")[0] is False)
check("dispatch:<số quá lớn> → lỗi, không vỡ SQLite", db.verify_evidence_ref("dispatch:" + "9" * 30)[0] is False)
check("warroom:<số quá lớn> → lỗi, không vỡ SQLite", db.verify_evidence_ref("warroom:" + "9" * 30)[0] is False)
check("DISPATCH:#id (hoa, có #) → nhận", db.verify_evidence_ref(f"DISPATCH:#{d_done}", task_id="TODO-T12")[0] is True)


def add_msg(author, tag="Report", project="PRJ-GEN-WORKPLACE"):
    with db.get_connection() as conn:
        cur = conn.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body) VALUES (?, 'war_room', ?, '10:00:00', ?, 'nội dung')",
                           (project, author, tag))
        conn.commit()
        return cur.lastrowid


m_agent = add_msg("gw-backend-agy")
m_owner = add_msg("Ryan (Owner)", tag="Directive")
m_err = add_msg("Orchestrator (lỗi)", tag="Error")
m_failed_reply = add_msg("gw-qa-agy")
with db.get_connection() as conn:
    conn.execute("UPDATE dispatch_log SET reply_msg_id = ? WHERE id = ?", (m_failed_reply, d_failed))
    conn.commit()
ok, by, msg = db.verify_evidence_ref(f"warroom:{m_agent}")
check("warroom: trả lời của agent → warroom", ok and by == "warroom", f"({by}) {msg}")
ok, _, msg = db.verify_evidence_ref(f"warroom:{m_owner}")
check("warroom: tin của Owner → lỗi", not ok and "không phải tin trả lời của agent" in msg, msg)
check("warroom: tin lỗi → lỗi", db.verify_evidence_ref(f"warroom:{m_err}")[0] is False)
ok, _, msg = db.verify_evidence_ref(f"warroom:{m_failed_reply}")
check("warroom: trả lời của dispatch failed → lỗi", not ok and "failed" in msg, msg)
check("warroom: id không tồn tại → lỗi", db.verify_evidence_ref("warroom:999999")[0] is False)

print("[5] claim + complete với bằng chứng sai → lỗi, trạng thái giữ nguyên")
res = db.claim_task("gw-qa-agy", "TODO-T12")
check("claim TODO-T12", res.get("status") == "claimed", str(res))
res = db.complete_task("gw-qa-agy", "TODO-T12", "100% test suite pass (68ms auto-wake)")
check("complete với câu chữ mẫu → error", "error" in res, str(res))
res = db.complete_task("gw-qa-agy", "TODO-T12", "deadbeef")
check("complete với SHA lạ → error", "error" in res, str(res))
res = db.complete_task("gw-qa-agy", "TODO-T12", "https://github.com/khong-ton-tai/repo-gia/pull/999999")
check("complete với URL PR giả → error", "error" in res and "404" in res["error"], str(res))
res = db.complete_task("gw-qa-agy", "TODO-T12", f"dispatch:{d_failed}")
check("complete với dispatch failed → error", "error" in res, str(res))
res = db.complete_task("gw-qa-agy", "TODO-T12", f"dispatch:{d_other}")
check("complete với dispatch của task khác → error", "error" in res, str(res))
with db.get_connection() as conn:
    r = conn.execute("SELECT status, evidence_ref, assigned_session_id FROM todos WHERE id = 'TODO-T12'").fetchone()
check("TODO-T12 vẫn in_progress, evidence rỗng", r["status"] == "in_progress" and r["evidence_ref"] == "" and r["assigned_session_id"] == "gw-qa-agy", dict(r))

print("[6] complete với dispatch:<id> done → ok; lần 2 bị chặn; force ghi đè có log")
res = db.complete_task("gw-qa-agy", "TODO-T12", f"dispatch:{d_done}", verified_by="Lead Architect")
check("complete ok, verified_by = dispatch", res.get("status") == "completed" and res.get("verified_by") == "dispatch", str(res))
res2 = db.complete_task("gw-qa-agy", "TODO-T12", head_sha)
check("complete lần 2 → error code already_done", res2.get("code") == "already_done" and "error" in res2, str(res2))
check("lỗi trả lại bằng chứng đang có", res2.get("evidence_ref") == f"dispatch:{d_done}", str(res2))
with db.get_connection() as conn:
    r = conn.execute("SELECT status, evidence_ref, verified_by FROM todos WHERE id = 'TODO-T12'").fetchone()
check("DB: evidence KHÔNG bị ghi đè", r["status"] == "done" and r["evidence_ref"] == f"dispatch:{d_done}" and r["verified_by"] == "dispatch", dict(r))
res3 = db.complete_task("gw-lead-agy", "TODO-T12", "deadbeef", force=True)
check("force + bằng chứng sai → vẫn error (force không bỏ qua kiểm bằng chứng)", "error" in res3, str(res3))
res3 = db.complete_task("gw-lead-agy", "TODO-T12", head_sha, force=True, reason="đổi sang commit merge")
check("force + bằng chứng đúng → ghi đè, trả overridden", res3.get("status") == "completed" and res3.get("overridden", {}).get("old_evidence_ref") == f"dispatch:{d_done}", str(res3))
with db.get_connection() as conn:
    r = conn.execute("SELECT evidence_ref, verified_by FROM todos WHERE id = 'TODO-T12'").fetchone()
check("DB: evidence mới = SHA, git:commit", r["evidence_ref"] == head_sha and r["verified_by"] == "git:commit", dict(r))
audit = db.get_task_evidence_audit("TODO-T12")
check("task_evidence_audit có 1 dòng cũ → mới, ai, lý do",
      len(audit) == 1 and audit[0]["old_evidence_ref"] == f"dispatch:{d_done}" and audit[0]["new_evidence_ref"] == head_sha
      and audit[0]["session_id"] == "gw-lead-agy" and audit[0]["reason"] == "đổi sang commit merge", str(audit))
res = db.claim_task("gw-backend-agy", "TODO-T12")
check("claim task đã done → error already_done (không mở lại task)", res.get("code") == "already_done", str(res))

print("[7] task không tồn tại → error")
res = db.complete_task("gw-qa-agy", "TODO-KHONG-CO", head_sha)
check("Task not found", res.get("error") == "Task not found", str(res))
res = db.claim_task("gw-qa-agy", "TODO-KHONG-CO")
check("claim: Task not found", res.get("error") == "Task not found", str(res))
check("claim thiếu session_id → error", "error" in db.claim_task("", "TODO-T13"))

print("[8] khóa claim: người khác bị từ chối (trả held_by), người giữ gọi lại được, khóa quá hạn thì claim lại được")
res = db.claim_task("gw-qa-agy", "TODO-T13")
check("gw-qa-agy claim TODO-T13", res.get("status") == "claimed", str(res))
res = db.claim_task("gw-backend-agy", "TODO-T13")
check("gw-backend-agy claim lại → locked, held_by = gw-qa-agy", res.get("code") == "locked" and res.get("held_by") == "gw-qa-agy", str(res))
res = db.claim_task("gw-qa-agy", "TODO-T13")
check("người đang giữ gọi lại → claimed", res.get("status") == "claimed", str(res))
with db.get_connection() as conn:
    conn.execute("UPDATE todos SET locked_at = datetime('now', '-1 hour') WHERE id = 'TODO-T13'")
    conn.commit()
res = db.claim_task("gw-backend-agy", "TODO-T13")
check("khóa quá hạn (1 giờ > 300s) → worker khác claim được", res.get("status") == "claimed", str(res))
with db.get_connection() as conn:
    r = conn.execute("SELECT assigned_session_id FROM todos WHERE id = 'TODO-T13'").fetchone()
check("DB: TODO-T13 chuyển sang gw-backend-agy", r["assigned_session_id"] == "gw-backend-agy", dict(r))
os.environ["GW_RECLAIM_TIMEOUT_SEC"] = "7200"
with db.get_connection() as conn:
    conn.execute("UPDATE todos SET locked_at = datetime('now', '-1 hour') WHERE id = 'TODO-T13'")
    conn.commit()
res = db.claim_task("gw-qa-agy", "TODO-T13")
check("GW_RECLAIM_TIMEOUT_SEC=7200 → khóa 1 giờ vẫn còn hiệu lực", res.get("code") == "locked", str(res))
check("task_lock_timeout_sec đọc cùng biến với reclaim", db.task_lock_timeout_sec() == 7200)
del os.environ["GW_RECLAIM_TIMEOUT_SEC"]

print("[9] task Kanban phiên (gen_session_todos): khóa claim + complete lần 2")
conv = db.create_gen_conversation(title="Test claim")["id"]
db.save_gen_session_todo(conv, "TSK-K1", "Task kanban", "", "todo", "high", "Gen Core", [], "", 0, "owner-ryan", viec_ref="VIEC-1")
res = db.claim_task("gw-qa-agy", "TSK-K1")
check("kanban: gw-qa-agy claim", res.get("status") == "claimed", str(res))
res = db.claim_task("gw-backend-agy", "TSK-K1")
check("kanban: worker khác → locked, held_by = gw-qa-agy", res.get("code") == "locked" and res.get("held_by") == "gw-qa-agy", str(res))
res = db.claim_task("gw-qa-agy", "TSK-K1")
check("kanban: người giữ gọi lại → claimed", res.get("status") == "claimed", str(res))
# Dữ liệu cũ: in_progress do UI gán assigned_agent, không có locked_at → coi như khóa quá hạn (giống reclaim)
db.save_gen_session_todo(conv, "TSK-K2", "Task kanban cũ", "", "in_progress", "high", "Frontend Dev", [], "", 0, "owner-ryan", viec_ref="VIEC-2")
res = db.claim_task("gw-backend-agy", "TSK-K2")
check("kanban: in_progress cũ không có locked_at → claim được", res.get("status") == "claimed", str(res))
res = db.complete_task("gw-qa-agy", "TSK-K1", "~/gw-reports/r1.md")
check("kanban: complete với file báo cáo → ok", res.get("status") == "completed" and res.get("verified_by") == "file", str(res))
res = db.complete_task("gw-qa-agy", "TSK-K1", head_sha)
check("kanban: complete lần 2 → already_done", res.get("code") == "already_done", str(res))
with db.get_connection() as conn:
    r = conn.execute("SELECT status, evidence_ref, claimed_by FROM gen_session_todos WHERE id = 'TSK-K1'").fetchone()
check("kanban DB: evidence giữ nguyên, claimed_by đã nhả", r["status"] == "done" and r["evidence_ref"] == "~/gw-reports/r1.md" and r["claimed_by"] == "", dict(r))

print("[10] 2 worker cùng claim (thread) → đúng 1 người thắng")


def race(task_id, reset_sql, rounds=25):
    bad = []
    for i in range(rounds):
        with db.get_connection() as conn:
            conn.execute(reset_sql, (task_id,))
            conn.commit()
        barrier = threading.Barrier(2)
        results = {}

        def worker(sid):
            barrier.wait()
            results[sid] = db.claim_task(sid, task_id)

        ts = [threading.Thread(target=worker, args=(sid,)) for sid in ("gw-qa-agy", "gw-backend-agy")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        winners = [sid for sid, r in results.items() if r.get("status") == "claimed"]
        losers = [r for r in results.values() if r.get("code") == "locked"]
        if len(winners) != 1 or len(losers) != 1 or losers[0].get("held_by") != winners[0]:
            bad.append((i, results))
    return bad


bad = race("TODO-T14", "UPDATE todos SET status = 'queued', assigned_session_id = '', locked_at = NULL WHERE id = ?")
check("todos: 25 vòng, mỗi vòng đúng 1 thắng, người thua nhận held_by = người thắng", bad == [], str(bad[:2]))
bad = race("TSK-K2", "UPDATE gen_session_todos SET status = 'todo', claimed_by = '', locked_at = '', assigned_agent = 'Gen Core' WHERE id = ?")
check("kanban: 25 vòng, mỗi vòng đúng 1 thắng, người thua nhận held_by = người thắng", bad == [], str(bad[:2]))

print("[11] migrate: done với evidence giả → review; evidence cũ (PR/file theo luật cũ) vẫn done, không gọi mạng")
with db.get_connection() as conn:
    conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status, evidence_ref, verified_by) VALUES ('TODO-99', 'RM-T5', 'PRJ-GEN-WORKPLACE', 'Giả', 'QA Tester', 'done', '100% test suite pass (68ms auto-wake)', 'Lead Architect')")
    conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status, evidence_ref, verified_by) VALUES ('TODO-OLDPR', 'RM-T5', 'PRJ-GEN-WORKPLACE', 'Cũ PR', 'QA Tester', 'done', 'https://github.com/acme/cu/pull/3', 'github:pr')")
    conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status, evidence_ref, verified_by) VALUES ('TODO-OLDF', 'RM-T5', 'PRJ-GEN-WORKPLACE', 'Cũ file', 'QA Tester', 'done', ?, 'file')", (OUTSIDE,))
    conn.commit()
n_calls = len(GH_CALLS)
n = db.migrate_unverified_done_tasks()
with db.get_connection() as conn:
    st = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM todos WHERE id IN ('TODO-99', 'TODO-T12', 'TODO-OLDPR', 'TODO-OLDF')")}
check("TODO-99 → review", st.get("TODO-99") == "review", f"n={n} {st}")
check("TODO-T12 (SHA thật) vẫn done", st.get("TODO-T12") == "done", str(st))
check("TODO-OLDPR (URL PR cũ) vẫn done", st.get("TODO-OLDPR") == "done", str(st))
check("TODO-OLDF (file ngoài thư mục mới, luật cũ) vẫn done", st.get("TODO-OLDF") == "done", str(st))
check("migrate không gọi GitHub API", len(GH_CALLS) == n_calls, str(GH_CALLS[n_calls:]))

print("[12] reclaim_stalled_tasks (ngưỡng mặc định = task_lock_timeout_sec)")
with db.get_connection() as conn:
    conn.execute("UPDATE todos SET status='in_progress', assigned_session_id='gw-devops-agy', locked_at=datetime('now', '-1 hour') WHERE id='TODO-T15'")
    conn.execute("UPDATE todos SET status='queued' WHERE id IN ('TODO-T13', 'TODO-T14')")
    conn.commit()
res = db.reclaim_stalled_tasks()
check("thu hồi 1 task treo", res.get("reclaimed_count") == 1, str(res))

print("[13] DB cũ (thiếu cột claimed_by/locked_at, thiếu bảng audit) → init_db lại không lỗi")
with db.get_connection() as conn:
    conn.execute("ALTER TABLE gen_session_todos DROP COLUMN claimed_by")
    conn.execute("ALTER TABLE gen_session_todos DROP COLUMN locked_at")
    conn.execute("DROP TABLE task_evidence_audit")
    conn.commit()
try:
    db.init_db()
    err = ""
except Exception as e:
    err = repr(e)
check("init_db trên DB cũ chạy không lỗi", err == "", err)
with db.get_connection() as conn:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(gen_session_todos)")}
    has_audit = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_evidence_audit'").fetchone() is not None
    r = conn.execute("SELECT status, evidence_ref FROM gen_session_todos WHERE id = 'TSK-K1'").fetchone()
check("đã thêm lại cột claimed_by/locked_at + bảng audit", {"claimed_by", "locked_at"} <= cols and has_audit, str(cols))
check("task kanban done cũ vẫn done + giữ evidence", r["status"] == "done" and r["evidence_ref"] == "~/gw-reports/r1.md", dict(r))
db.save_gen_session_todo(conv, "TSK-K3", "Task sau migrate", "", "todo", "high", "Gen Core", [], "", 0, "owner-ryan", viec_ref="VIEC-3")
check("claim kanban sau migrate chạy được", db.claim_task("gw-qa-agy", "TSK-K3").get("status") == "claimed")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

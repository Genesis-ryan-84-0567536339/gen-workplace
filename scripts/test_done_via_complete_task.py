#!/usr/bin/env python3
"""
Test Issue #18: mọi đường chuyển task sang 'done' đi qua complete_task (kiểm evidence) + chỉ người giữ task được đóng.
Chạy không cần server ngoài / agy / tmux thật / mạng:
- complete_task: người giữ (claimed_by / assigned_session_id) đóng được; người khác → code not_holder (kèm held_by);
  force=true → đóng được và ghi task_evidence_audit (action force_close, held_by); task chưa ai claim → ai cũng đóng được.
- Đường roadmap todos: db.update_todo_status + POST /api/todo/update (API mà kéo thả Kanban tổng quan gọi).
- Đường Kanban phiên: db.update_gen_session_todo_status + POST /api/gen/session/todos/status (kéo thả / nút Nghiệm thu).
- Đường lưu task: db.save_gen_session_todo + POST /api/gen/session/todos/save với status='done'.
- Đường chat Gen: [TASK_DONE: X] / [KANBAN_UPDATE: X | STATUS: done | EVIDENCE: ...] trong trả lời agy (send_gen_chat).
- Đường API /api/task/complete: 409 not_holder.
- Rà mã nguồn: không còn câu SQL ghi status 'done' ngoài complete_task; UI kéo thả sang Done mở hộp nhập bằng chứng.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-done-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
# agy giả: trả lời lấy từ file REPLY_FILE (để test chỉ thị Kanban trong câu trả lời chat Gen)
REPLY_FILE = os.path.join(TMP, "reply.txt")
AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write("#!/usr/bin/env python3\nimport json\nprint(json.dumps({'response': open(%r, encoding='utf-8').read(), 'usage': {}}))\n" % REPLY_FILE)
os.chmod(AGY, 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "worktrees")
for k in ("GITHUB_TOKEN", "GW_RECLAIM_TIMEOUT_SEC", "GW_RECLAIM_INTERVAL_SEC", "GW_GEN_CHAT_SKIP_PERMISSIONS", "GW_EVENT_WEBHOOK_URL"):
    os.environ.pop(k, None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, main  # noqa: E402

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


server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


def call(path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def status_of(table, tid):
    with db.get_connection() as conn:
        r = conn.execute(f"SELECT status, evidence_ref FROM {table} WHERE id = ?", (tid,)).fetchone()
    return (r["status"], r["evidence_ref"]) if r else (None, None)


head_sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
REPORTS = os.path.join(os.environ["HOME"], "gw-reports")
os.makedirs(REPORTS)
with open(os.path.join(REPORTS, "bao-cao.md"), "w") as f:
    f.write("kết quả")

with db.get_connection() as conn:
    conn.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-D', 'PRJ-GEN-WORKPLACE', 'Roadmap', '', 6, 'queued', 1)")
    for tid in ("TODO-D1", "TODO-D2", "TODO-D3", "TODO-D4", "TODO-D5", "TODO-D6"):
        conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES (?, 'RM-D', 'PRJ-GEN-WORKPLACE', ?, 'QA Tester', 'queued')", (tid, tid))
    conn.commit()
conv = db.create_gen_conversation(title="VIEC-8: test done")["id"]
conv2 = db.create_gen_conversation(title="VIEC-9: phiên khác")["id"]
for i in range(1, 10):
    db.save_gen_session_todo(conv, f"TSK-D{i}", f"Task {i}", "", "todo", "high", "Gen Core", ["a"], "", 0, "owner-ryan", viec_ref="VIEC-8")

print("[1] complete_task: chỉ người giữ task được đóng; force ghi audit; task chưa claim thì ai cũng đóng được")
db.claim_task("gw-qa-agy", "TODO-D1")
res = db.complete_task("gw-backend-agy", "TODO-D1", head_sha)
check("người khác đóng → not_holder, held_by = gw-qa-agy", res.get("code") == "not_holder" and res.get("held_by") == "gw-qa-agy", str(res))
check("DB: TODO-D1 vẫn in_progress", status_of("todos", "TODO-D1")[0] == "in_progress")
res = db.complete_task("gw-qa-agy", "TODO-D1", head_sha)
check("người giữ đóng → completed", res.get("status") == "completed", str(res))
db.claim_task("gw-qa-agy", "TODO-D2")
res = db.complete_task("gw-backend-agy", "TODO-D2", "deadbeef", force=True, reason="thử")
check("force + bằng chứng sai → vẫn error, không đóng", "error" in res and status_of("todos", "TODO-D2")[0] == "in_progress", str(res))
res = db.complete_task("gw-backend-agy", "TODO-D2", head_sha, force=True, reason="worker qa đã dừng")
check("force đóng thay → completed, force_closed.held_by = gw-qa-agy", res.get("status") == "completed" and res.get("force_closed", {}).get("held_by") == "gw-qa-agy", str(res))
audit = db.get_task_evidence_audit("TODO-D2")
check("audit: 1 dòng action=force_close, held_by, người đóng, lý do",
      len(audit) == 1 and audit[0]["action"] == "force_close" and audit[0]["held_by"] == "gw-qa-agy"
      and audit[0]["session_id"] == "gw-backend-agy" and audit[0]["reason"] == "worker qa đã dừng" and audit[0]["new_evidence_ref"] == head_sha, str(audit))
res = db.complete_task("gw-backend-agy", "TODO-D3", head_sha)
check("task chưa ai claim → ai cũng đóng được, không ghi audit", res.get("status") == "completed" and "force_closed" not in res and db.get_task_evidence_audit("TODO-D3") == [], str(res))
db.claim_task("gw-qa-agy", "TSK-D1")
res = db.complete_task("claude-dieu-phoi", "TSK-D1", head_sha)
check("Kanban phiên: người khác → not_holder (claimed_by)", res.get("code") == "not_holder" and res.get("held_by") == "gw-qa-agy", str(res))
res = db.complete_task("gw-qa-agy", "TSK-D1", head_sha)
check("Kanban phiên: người giữ → completed", res.get("status") == "completed", str(res))
check("Kanban phiên: assigned_agent (nhãn) không tính là người giữ",
      db.complete_task("ai-do", "TSK-D9", "~/gw-reports/bao-cao.md").get("status") == "completed")

print("[2] đường roadmap todos: update_todo_status / POST /api/todo/update (API của kéo thả Kanban tổng quan)")
res = db.update_todo_status("TODO-D4", "done")
check("db: sang done không evidence → error, không đổi", "error" in res and status_of("todos", "TODO-D4")[0] == "queued", str(res))
st, js = call("/api/todo/update", {"id": "TODO-D4", "status": "done"})
check("API: không evidence → 400 kèm lý do", st == 400 and "evidence" in (js.get("error") or "").lower(), f"{st} {js}")
st, js = call("/api/todo/update", {"id": "TODO-D4", "status": "done", "evidence_ref": "100% pass"})
check("API: evidence câu chữ → 400", st == 400 and js.get("code") == "invalid_evidence", f"{st} {js}")
check("DB: TODO-D4 vẫn queued", status_of("todos", "TODO-D4")[0] == "queued")
db.claim_task("gw-qa-agy", "TODO-D4")
st, js = call("/api/todo/update", {"id": "TODO-D4", "status": "done", "evidence_ref": head_sha})
check("API: task do gw-qa-agy giữ, UI (owner-ui) đóng → 409 not_holder", st == 409 and js.get("code") == "not_holder" and js.get("held_by") == "gw-qa-agy", f"{st} {js}")
st, js = call("/api/todo/update", {"id": "TODO-D4", "status": "done", "evidence_ref": head_sha, "force": True, "reason": "Boss nghiệm thu thay"})
check("API: force + lý do → 200 completed", st == 200 and js.get("status") == "completed" and js.get("success") is True, f"{st} {js}")
audit = db.get_task_evidence_audit("TODO-D4")
check("audit ghi owner-ui đóng thay gw-qa-agy", audit and audit[0]["action"] == "force_close" and audit[0]["session_id"] == "owner-ui" and audit[0]["held_by"] == "gw-qa-agy", str(audit))
st, js = call("/api/todo/update", {"id": "TODO-D5", "status": "done", "evidence_ref": head_sha, "session_id": "gw-lead-agy"})
check("API: task chưa claim + SHA thật → 200, verified_by git:commit", st == 200 and js.get("verified_by") == "git:commit", f"{st} {js}")
st, js = call("/api/todo/update", {"id": "TODO-D5", "status": "done", "evidence_ref": head_sha})
check("API: done lần 2 → 409 already_done", st == 409 and js.get("code") == "already_done", f"{st} {js}")
st, js = call("/api/todo/update", {"id": "TODO-D6", "status": "live"})
check("API: đổi sang trạng thái khác done vẫn bình thường", st == 200 and js.get("new_status") == "live" and status_of("todos", "TODO-D6")[0] == "live", f"{st} {js}")
with db.get_connection() as conn:
    rm = conn.execute("SELECT status FROM roadmaps WHERE id = 'RM-D'").fetchone()
check("roadmap được tính lại sau khi task done", rm["status"] == "live", dict(rm))

print("[3] đường Kanban phiên: update_gen_session_todo_status / POST /api/gen/session/todos/status")
res = db.update_gen_session_todo_status(conv, "TSK-D2", "done")
check("db: không evidence → error, giữ todo", "error" in res and status_of("gen_session_todos", "TSK-D2")[0] == "todo", str(res))
st, js = call("/api/gen/session/todos/status", {"conv_id": conv, "todo_id": "TSK-D2", "status": "done"})
check("API: kéo sang Done không evidence → 400", st == 400 and "error" in js, f"{st} {js}")
st, js = call("/api/gen/session/todos/status", {"conv_id": conv, "todo_id": "TSK-D2", "status": "done", "evidence_ref": "deadbeefcafe"})
check("API: SHA không có trong repo → 400", st == 400, f"{st} {js}")
db.claim_task("claude-dieu-phoi", "TSK-D2")
st, js = call("/api/gen/session/todos/status", {"conv_id": conv, "todo_id": "TSK-D2", "status": "done", "evidence_ref": head_sha})
check("API: task do claude-dieu-phoi giữ, UI đóng → 409 not_holder", st == 409 and js.get("held_by") == "claude-dieu-phoi", f"{st} {js}")
check("DB: TSK-D2 vẫn in_progress", status_of("gen_session_todos", "TSK-D2")[0] == "in_progress")
st, js = call("/api/gen/session/todos/status", {"conv_id": conv, "todo_id": "TSK-D2", "status": "done", "evidence_ref": head_sha, "session_id": "claude-dieu-phoi"})
check("API: chính người giữ → 200 completed", st == 200 and js.get("status") == "completed", f"{st} {js}")
st, js = call("/api/gen/session/todos/status", {"conv_id": conv2, "todo_id": "TSK-D3", "status": "done", "evidence_ref": head_sha})
check("API: task không thuộc phiên → 404, không đổi", st == 404 and status_of("gen_session_todos", "TSK-D3")[0] == "todo", f"{st} {js}")
st, js = call("/api/gen/session/todos/status", {"conv_id": conv, "todo_id": "TSK-D3", "status": "review", "evidence_ref": "ghi chú"})
check("API: sang review vẫn bình thường", st == 200 and status_of("gen_session_todos", "TSK-D3")[0] == "review", f"{st} {js}")
st, js = call("/api/gen/session/todos/status", {"conv_id": conv, "todo_id": "TSK-D2", "status": "review", "evidence_ref": "đè"})
check("đổi trạng thái thường không đè evidence của task đã done", status_of("gen_session_todos", "TSK-D2")[1] == head_sha, str(status_of("gen_session_todos", "TSK-D2")))

print("[4] đường lưu task: save_gen_session_todo / POST /api/gen/session/todos/save với status='done'")
res = db.save_gen_session_todo(conv, "TSK-D4", "Task 4 sửa tên", "", "done", "high", "Gen Core", [], "", 0, "owner-ryan")
check("db: done không evidence → error saved=True, trạng thái giữ todo, tên đã lưu",
      "error" in res and res.get("saved") is True and status_of("gen_session_todos", "TSK-D4")[0] == "todo", str(res))
st, js = call("/api/gen/session/todos/save", {"conv_id": conv, "id": "TSK-D4", "title": "Task 4", "status": "done", "evidence_ref": "xong rồi"})
check("API: evidence câu chữ → 400, trạng thái giữ nguyên", st == 400 and status_of("gen_session_todos", "TSK-D4")[0] == "todo", f"{st} {js}")
st, js = call("/api/gen/session/todos/save", {"conv_id": conv, "id": "TSK-D4", "title": "Task 4", "status": "done", "evidence_ref": head_sha})
check("API: SHA thật → 200, done", st == 200 and js.get("task_status") == "done" and status_of("gen_session_todos", "TSK-D4") == ("done", head_sha), f"{st} {js}")
st, js = call("/api/gen/session/todos/save", {"conv_id": conv, "id": "TSK-D4", "title": "Task 4", "status": "done", "evidence_ref": "~/gw-reports/bao-cao.md"})
check("API: task đã done, đổi evidence qua save → 409 already_done", st == 409 and status_of("gen_session_todos", "TSK-D4")[1] == head_sha, f"{st} {js}")
st, js = call("/api/gen/session/todos/save", {"conv_id": conv, "title": "Task mới done luôn", "status": "done", "viec_ref": "VIEC-8"})
check("API: tạo task mới status done không evidence → 400, task tạo ở todo", st == 400 and js.get("created") is True
      and status_of("gen_session_todos", js.get("id"))[0] == "todo", f"{st} {js}")
st, js = call("/api/gen/session/todos/save", {"conv_id": conv2, "id": "TSK-D5", "title": "Chiếm task phiên khác", "status": "done", "evidence_ref": head_sha})
check("API: id của phiên khác → 404, không đổi", st == 404 and status_of("gen_session_todos", "TSK-D5")[0] == "todo", f"{st} {js}")

print("[5] đường chat Gen: chỉ thị Kanban trong trả lời agy đi qua complete_task")
db.claim_task("gw-qa-agy", "TSK-D7")
with open(REPLY_FILE, "w", encoding="utf-8") as f:
    f.write("Đã xong.\n[TASK_DONE: TSK-D5]\n[KANBAN_UPDATE: TSK-D6 | STATUS: done | EVIDENCE: %s]\n"
            "[KANBAN_UPDATE: TSK-D7 | STATUS: done | EVIDENCE: %s]\n[KANBAN_UPDATE: TSK-D8 | STATUS: in_progress]" % (head_sha, head_sha))
out = db.send_gen_chat(conv, "Ryan", "báo cáo", "gemini 3.8 flash (high)")
upd = out.get("kanban_updates") or []
check("[TASK_DONE] không evidence → bị từ chối, TSK-D5 vẫn todo", status_of("gen_session_todos", "TSK-D5")[0] == "todo"
      and any("TSK-D5" in u and "BỊ TỪ CHỐI" in u for u in upd), str(upd))
check("[KANBAN_UPDATE done + SHA thật] task chưa claim → done", status_of("gen_session_todos", "TSK-D6") == ("done", head_sha), str(upd))
check("[KANBAN_UPDATE done] task do gw-qa-agy giữ → từ chối not_holder, vẫn in_progress",
      status_of("gen_session_todos", "TSK-D7")[0] == "in_progress" and any("TSK-D7" in u and "gw-qa-agy" in u for u in upd), str(upd))
check("[KANBAN_UPDATE in_progress] vẫn áp dụng", status_of("gen_session_todos", "TSK-D8")[0] == "in_progress")
with db.get_connection() as conn:
    last = conn.execute("SELECT content FROM gen_messages WHERE conversation_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1", (conv,)).fetchone()
check("tin trả lời lưu kèm dòng [Kanban] ... BỊ TỪ CHỐI để người dùng thấy", "[Kanban] Task TSK-D5 -> done BỊ TỪ CHỐI" in last["content"], last["content"][-300:])
prompt_block = db.format_session_kanban_for_agent(conv, db.get_gen_session_todos(conv))
check("hướng dẫn cho agent: done cần EVIDENCE kiểm được, bỏ [TASK_DONE] ngắn gọn",
      "EVIDENCE kiểm được" in prompt_block and "Hoặc ngắn gọn: [TASK_DONE" not in prompt_block)

print("[6] /api/task/complete: 409 not_holder")
db.claim_task("gw-qa-agy", "TSK-D8")
st, js = call("/api/task/complete", {"session_id": "gw-backend-agy", "task_id": "TSK-D8", "evidence_ref": head_sha})
check("người khác → 409 not_holder", st == 409 and js.get("code") == "not_holder", f"{st} {js}")

print("[7] rà mã nguồn: chỉ complete_task ghi status 'done'")
src = {n: open(os.path.join(ROOT, "backend", n), encoding="utf-8").read() for n in ("db.py", "main.py", "mcp_core.py")}
done_writes = []
for name, text in src.items():
    funcs = [(m.start(), m.group(1)) for m in re.finditer(r"^def (\w+)\(", text, re.M)]
    for m in re.finditer(r"SET\s+status\s*=\s*'done'|status\s*=\s*'done'\s*,\s*evidence_ref", text):
        owner = [f for pos, f in funcs if pos < m.start()]
        done_writes.append((name, owner[-1] if owner else "?"))
check("mọi câu SQL ghi status='done' nằm trong complete_task", done_writes and all(w == ("db.py", "complete_task") for w in done_writes), str(done_writes))
check("update_gen_session_todo_status / update_todo_status đi qua set_task_status",
      "set_task_status(" in src["db.py"].split("def update_gen_session_todo_status")[1].split("\ndef ")[0]
      and "set_task_status(" in src["db.py"].split("def update_todo_status")[1].split("\ndef ")[0])
check("main.py không còn gọi update_gen_session_todo_status thiếu người gọi", "update_gen_session_todo_status(conv_id, todo_id, new_status, **_status_change_args(data))" in src["main.py"])

print("[8] UI: kéo thả sang Done mở hộp nhập bằng chứng, hủy/bị từ chối → thẻ về cột cũ")
html = open(os.path.join(ROOT, "frontend", "index.html"), encoding="utf-8").read()


def js_func(name):
    m = re.search(r"(?:async )?function " + name + r"\([^)]*\) \{", html)
    if not m:
        return ""
    depth, i = 0, m.end() - 1
    while i < len(html):
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        if depth == 0:
            return html[m.start():i + 1]
        i += 1
    return ""


check("có hộp #evidenceModal với ô nhập, force, lý do", all(x in html for x in ('id="evidenceModal"', 'id="evidenceModalInput"', 'id="evidenceModalForce"', 'id="evidenceModalReason"')))
# Kanban roadmap cũ (bảng todos, dropKanban/updateRoadmapTodoStatus) đã gỡ (VIEC-12): không còn đường client nào tự set done
check("Kanban roadmap cũ đã gỡ (không còn dropKanban / updateRoadmapTodoStatus)", not js_func("dropKanban") and not js_func("updateRoadmapTodoStatus"))
mv = js_func("moveSessionTodoStatus")
check("moveSessionTodoStatus: done → requestTaskDone rồi tải lại Kanban phiên", "requestTaskDone(" in mv and "loadGenSessionTodos()" in mv)
check("Kanban phiên có kéo thả (dragGenKanban / dropGenKanban)", "ondrop=\"dropGenKanban(event, '${col.id}')\"" in html and "dragGenKanban(event" in html)
req = js_func("requestTaskDone")
check("requestTaskDone: hủy → báo giữ nguyên cột cũ; bị từ chối → báo lỗi", "giữ nguyên cột cũ" in req and "Từ chối chuyển" in req)
check("không còn toggleTodoStatus tự set status='done' trên client", "item.status = newStatus" not in html)

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

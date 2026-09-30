#!/usr/bin/env python3
"""
Test "sự thật của thẻ Kanban" (Refs: stale kanban cards):
- reclaim_stalled_tasks thu hồi thẻ gen_session_todos in_progress không có dispatch running + khóa quá hạn → todo,
  bỏ claimed_by/locked_at, ghi 1 tin "Tự thu hồi TSK-n" vào phiên; locked_at rỗng coi như quá hạn.
- Thẻ có dispatch running KHÔNG bị thu hồi dù khóa cũ; dispatch warroom running quá hạn cứng → failed rồi mới xét.
- start_dispatch_log làm mới locked_at (heartbeat).
- Đổi trạng thái về todo bỏ claim; review giữ người giữ.
- Danh sách thẻ (get_gen_session_todos / get_all_session_todos / MCP list_kanban_tasks) có live / stale / checklist_done/total.
- complete_task có bằng chứng kiểm được → tự tick checklist còn mở, trả auto_ticked.
- main.reclaim_interval_sec: GW_TASK_RECLAIM_SEC, 0 = tắt.
Chạy không cần server, agy, tmux, mạng.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-truth-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:   # tmux giả: không tạo phiên thật
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "worktrees")
for k in ("GITHUB_TOKEN", "GW_RECLAIM_TIMEOUT_SEC", "GW_RECLAIM_INTERVAL_SEC", "GW_TASK_RECLAIM_SEC", "GW_EVENT_WEBHOOK_URL"):
    os.environ.pop(k, None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db  # noqa: E402

total = 0
failed = 0


def check(label, cond, extra=""):
    global total, failed
    total += 1
    if cond:
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {extra}")


def row(tid):
    with db.get_connection() as conn:
        r = conn.execute("SELECT status, claimed_by, locked_at, checklist_json FROM gen_session_todos WHERE id = ?", (tid,)).fetchone()
        return dict(r) if r else None


def set_card(tid, status, claimed_by, locked_expr):
    with db.get_connection() as conn:
        conn.execute(f"UPDATE gen_session_todos SET status = ?, claimed_by = ?, locked_at = {locked_expr} WHERE id = ?",
                     (status, claimed_by, tid))
        conn.commit()


def msgs(conv):
    with db.get_connection() as conn:
        return [r["content"] for r in conn.execute("SELECT content FROM gen_messages WHERE conversation_id = ? ORDER BY id", (conv,))]


conv = db.create_gen_conversation(title="VIEC-1: thẻ treo")["id"]
CHK = [{"id": "chk-a", "text": "Mục A", "done": True}, {"id": "chk-b", "text": "Mục B", "done": False},
       {"id": "chk-c", "text": "Mục C", "done": False}]
ids = []
for i in range(1, 6):
    res = db.save_gen_session_todo(conv, todo_id=f"TSK-T{i}", title=f"Thẻ {i}", checklist=CHK, viec_ref="VIEC-1")
    check(f"tạo TSK-T{i}", "error" not in res, str(res))
    ids.append(f"TSK-T{i}")

print("[1] reclaim thu hồi thẻ treo (không dispatch, khóa quá hạn) + ghi tin vào phiên")
set_card("TSK-T1", "in_progress", "gw-qa-agy", "datetime('now', '-2 days')")
set_card("TSK-T2", "in_progress", "gw-backend-agy", "''")                       # không có locked_at → coi như quá hạn
set_card("TSK-T3", "in_progress", "gw-devops-agy", "datetime('now', '-2 days')")  # có dispatch running
set_card("TSK-T4", "in_progress", "gw-lead-agy", "CURRENT_TIMESTAMP")            # khóa còn mới
did3 = db.start_dispatch_log("gw-devops-agy", kind="warroom", task_id="TSK-T3")
set_card("TSK-T3", "in_progress", "gw-devops-agy", "datetime('now', '-2 days')")  # khóa cũ dù dispatch đang chạy
res = db.reclaim_stalled_tasks(300)
check("reclaimed_ids = TSK-T1, TSK-T2", sorted(res.get("reclaimed_ids") or []) == ["TSK-T1", "TSK-T2"], str(res))
check("reclaimed_count đếm cả thẻ Kanban", res.get("reclaimed_count") == 2, str(res))
r1 = row("TSK-T1")
check("TSK-T1 → todo, bỏ claimed_by/locked_at", r1["status"] == "todo" and r1["claimed_by"] == "" and (r1["locked_at"] or "") == "", str(r1))
r2 = row("TSK-T2")
check("TSK-T2 (locked_at rỗng) → todo", r2["status"] == "todo" and r2["claimed_by"] == "", str(r2))
r3 = row("TSK-T3")
check("TSK-T3 có dispatch running → KHÔNG thu hồi", r3["status"] == "in_progress" and r3["claimed_by"] == "gw-devops-agy", str(r3))
check("TSK-T4 khóa còn mới → giữ nguyên", row("TSK-T4")["status"] == "in_progress")
m = msgs(conv)
n1 = [x for x in m if x.startswith("Tự thu hồi TSK-T1:")]
check("1 tin 'Tự thu hồi TSK-T1' nêu người giữ + ngưỡng", len(n1) == 1 and "không có dispatch nào chạy" in n1[0]
      and "gw-qa-agy" in n1[0] and "5 phút" in n1[0], str(m))
check("1 tin cho TSK-T2", sum(1 for x in m if x.startswith("Tự thu hồi TSK-T2:")) == 1, str(m))
check("không có tin cho TSK-T3", not any("TSK-T3" in x for x in m), str(m))
res = db.reclaim_stalled_tasks(300)
check("chạy lại: không thu hồi thêm, không ghi tin trùng", res.get("reclaimed_ids") == [] and len(msgs(conv)) == len(m), str(res))

print("[1b] mặc định thẻ Kanban dùng GW_KANBAN_STALE_MIN (30 phút), không phải 5 phút")
check("kanban_stale_timeout_sec mặc định 1800", db.kanban_stale_timeout_sec() == 1800, str(db.kanban_stale_timeout_sec()))
db.save_gen_session_todo(conv, todo_id="TSK-T7", title="Claude đang làm PR", viec_ref="VIEC-1")
db.save_gen_session_todo(conv, todo_id="TSK-T8", title="Bỏ quên 31 phút", viec_ref="VIEC-1")
set_card("TSK-T7", "in_progress", "claude-dieu-phoi", "datetime('now', '-10 minutes')")
set_card("TSK-T8", "in_progress", "claude-dieu-phoi", "datetime('now', '-31 minutes')")
res = db.reclaim_stalled_tasks()
check("mặc định: khóa 10 phút giữ nguyên, 31 phút thu hồi", "TSK-T8" in (res.get("reclaimed_ids") or [])
      and "TSK-T7" not in (res.get("reclaimed_ids") or []) and row("TSK-T7")["status"] == "in_progress", str(res))
os.environ["GW_KANBAN_STALE_MIN"] = "5"
check("GW_KANBAN_STALE_MIN=5 → 300", db.kanban_stale_timeout_sec() == 300)
os.environ.pop("GW_KANBAN_STALE_MIN")
db.set_task_status("TSK-T7", "todo", conv_id=conv)

print("[2] heartbeat: start_dispatch_log làm mới locked_at; dispatch mất (quá hạn cứng) → failed rồi mới xét")
set_card("TSK-T4", "in_progress", "gw-lead-agy", "datetime('now', '-2 days')")
db.start_dispatch_log("gw-lead-agy", kind="warroom", task_id="TSK-T4")
with db.get_connection() as conn:
    fresh = conn.execute("SELECT strftime('%s','now') - strftime('%s', locked_at) AS age FROM gen_session_todos WHERE id='TSK-T4'").fetchone()["age"]
check("locked_at của TSK-T4 vừa được làm mới", fresh is not None and fresh < 60, str(fresh))
# dispatch của TSK-T3 bắt đầu từ 3 ngày trước, vẫn 'running' (app restart làm mất thread) → quá hạn cứng
with db.get_connection() as conn:
    conn.execute("UPDATE dispatch_log SET started_at = datetime('now', 'localtime', '-3 days') WHERE id = ?", (did3,))
    conn.commit()
set_card("TSK-T3", "in_progress", "gw-devops-agy", "datetime('now', '-2 days')")
db.reclaim_stalled_tasks(300)
with db.get_connection() as conn:
    st = conn.execute("SELECT status FROM dispatch_log WHERE id = ?", (did3,)).fetchone()["status"]
check("dispatch warroom quá hạn cứng → failed", st == "failed", st)
check("thẻ không bị thu hồi ngay cùng vòng (kết thúc dispatch = heartbeat)", row("TSK-T3")["status"] == "in_progress", str(row("TSK-T3")))
set_card("TSK-T3", "in_progress", "gw-devops-agy", "datetime('now', '-2 days')")
res = db.reclaim_stalled_tasks(300)
check("vòng sau (khóa lại cũ, không còn dispatch running) → thu hồi", "TSK-T3" in (res.get("reclaimed_ids") or []), str(res))

set_card("TSK-T3", "in_progress", "gw-devops-agy", "datetime('now', '-2 days')")
db.toggle_gen_session_todo_checklist_item(conv, "TSK-T3", "chk-b", True)
res = db.reclaim_stalled_tasks(300)
check("tick checklist khi đang làm = heartbeat → không thu hồi", "TSK-T3" not in (res.get("reclaimed_ids") or [])
      and row("TSK-T3")["status"] == "in_progress", str(res))

print("[3] đổi trạng thái: về todo bỏ claim, review giữ người giữ")
set_card("TSK-T5", "in_progress", "gw-qa-agy", "CURRENT_TIMESTAMP")
res = db.set_task_status("TSK-T5", "review", conv_id=conv)
r5 = row("TSK-T5")
check("review giữ claimed_by", "error" not in res and r5["status"] == "review" and r5["claimed_by"] == "gw-qa-agy", str(r5))
res = db.set_task_status("TSK-T5", "todo", conv_id=conv)
r5 = row("TSK-T5")
check("todo bỏ claimed_by + locked_at", r5["status"] == "todo" and r5["claimed_by"] == "" and (r5["locked_at"] or "") == "", str(r5))
set_card("TSK-T5", "in_progress", "gw-qa-agy", "CURRENT_TIMESTAMP")
db.save_gen_session_todo(conv, todo_id="TSK-T5", title="Thẻ 5", status="todo", checklist=CHK)
r5 = row("TSK-T5")
check("save status=todo cũng bỏ claim", r5["status"] == "todo" and r5["claimed_by"] == "", str(r5))

print("[4] danh sách thẻ: live / stale / checklist_done/total")
set_card("TSK-T1", "in_progress", "gw-qa-agy", "CURRENT_TIMESTAMP")          # in_progress, không dispatch → stale
todos = {t["id"]: t for t in db.get_gen_session_todos(conv)}
t4, t1, t2 = todos["TSK-T4"], todos["TSK-T1"], todos["TSK-T2"]
check("TSK-T4 live có dispatch_id/kind/session_id/started_at/elapsed_sec",
      isinstance(t4.get("live"), dict) and set(t4["live"]) == {"dispatch_id", "kind", "session_id", "started_at", "elapsed_sec"}
      and t4["live"]["kind"] == "warroom" and t4["live"]["session_id"] == "gw-lead-agy" and t4["live"]["elapsed_sec"] is not None
      and t4["stale"] is False, str(t4.get("live")))
check("TSK-T1 in_progress không dispatch → stale, live null", t1["live"] is None and t1["stale"] is True, str((t1["live"], t1["stale"])))
check("TSK-T2 todo → không stale", t2["live"] is None and t2["stale"] is False)
check("checklist_done/total = 1/3", t1["checklist_done"] == 1 and t1["checklist_total"] == 3, str((t1["checklist_done"], t1["checklist_total"])))
allt = {t["id"]: t for t in db.get_all_session_todos()}
check("get_all_session_todos (/api/state, /api/tasks) cũng có live/stale",
      allt["TSK-T4"]["live"] and allt["TSK-T1"]["stale"] is True and allt["TSK-T1"]["checklist_total"] == 3)
st = db.get_full_state("PRJ-GEN-WORKPLACE") or {}
gs = {t["id"]: t for t in st.get("gen_session_todos") or []}
check("/api/state gen_session_todos có trường live", "live" in gs.get("TSK-T4", {}) and gs["TSK-T4"]["live"], str(list(gs)))
from backend import mcp_core  # noqa: E402
out = mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "list_kanban_tasks", "arguments": {"conv_id": conv}}})
payload = json.loads(out["result"]["content"][0]["text"])
mt = {t["id"]: t for t in payload["tasks"]}
check("MCP list_kanban_tasks có live/stale/checklist_done", mt["TSK-T4"]["live"]["dispatch_id"] and mt["TSK-T1"]["stale"] is True
      and mt["TSK-T1"]["checklist_done"] == 1, str(mt.get("TSK-T1", {}).get("stale")))

print("[5] complete_task có bằng chứng → tự tick checklist còn mở")
reports = os.path.join(os.environ["HOME"], "gw-reports")
os.makedirs(reports, exist_ok=True)
with open(os.path.join(reports, "ket-qua.md"), "w") as f:
    f.write("báo cáo thật")
res = db.complete_task("gw-qa-agy", "TSK-T1", "~/gw-reports/ket-qua.md")
check("complete_task thành công", res.get("status") == "completed", str(res))
check("auto_ticked = chk-b, chk-c", res.get("auto_ticked") == ["chk-b", "chk-c"], str(res.get("auto_ticked")))
chk = json.loads(row("TSK-T1")["checklist_json"])
check("mọi mục checklist đã tick", all(c["done"] for c in chk), str(chk))
res = db.complete_task("", "TSK-T2", "~/gw-reports/khong-co.md")
check("bằng chứng sai → không đóng, không tick", "error" in res and row("TSK-T2")["status"] == "todo"
      and not all(c["done"] for c in json.loads(row("TSK-T2")["checklist_json"])), str(res))
db.save_gen_session_todo(conv, todo_id="TSK-T6", title="Thẻ không checklist", viec_ref="VIEC-1")
res = db.complete_task("", "TSK-T6", "~/gw-reports/ket-qua.md")
check("thẻ không checklist vẫn đóng được, không có auto_ticked", res.get("status") == "completed" and "auto_ticked" not in res, str(res))

print("[6] prompt agy liệt kê id mục + cách báo tick; chu kỳ thread nền")
blk = db.build_task_prompt_block("TSK-T4")
check("prompt có id mục và [KANBAN_UPDATE: TSK-T4 | CHECK: <id mục>]", "(chk-b)" in blk and "[KANBAN_UPDATE: TSK-T4 | CHECK: <id mục>]" in blk, blk)
sys.path.insert(0, os.path.join(ROOT, "backend"))
try:
    from backend import main as gw_main  # noqa: E402
    check("GW_TASK_RECLAIM_SEC mặc định 60", gw_main.reclaim_interval_sec() == 60, str(gw_main.reclaim_interval_sec()))
    os.environ["GW_TASK_RECLAIM_SEC"] = "0"
    check("GW_TASK_RECLAIM_SEC=0 → tắt", gw_main.reclaim_interval_sec() == 0 and gw_main.start_reclaim_worker() is None)
    os.environ["GW_TASK_RECLAIM_SEC"] = "120"
    check("GW_TASK_RECLAIM_SEC=120", gw_main.reclaim_interval_sec() == 120)
    os.environ.pop("GW_TASK_RECLAIM_SEC")
    os.environ["GW_RECLAIM_INTERVAL_SEC"] = "300"
    check("không có GW_TASK_RECLAIM_SEC → dùng GW_RECLAIM_INTERVAL_SEC cũ", gw_main.reclaim_interval_sec() == 300)
    os.environ.pop("GW_RECLAIM_INTERVAL_SEC")
except Exception as e:
    check("import backend.main", False, repr(e))

print(f"{total - failed}/{total} test pass")
sys.exit(1 if failed else 0)

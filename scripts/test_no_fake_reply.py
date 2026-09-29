#!/usr/bin/env python3
"""
Test Issue #12 (không bịa câu trả lời). Chạy không cần server/agy/tmux:
GW_AGY_BIN trỏ script giả (OK JSON / 429 / thoát lỗi / không tồn tại) để kiểm:
- send_gen_chat: agy lỗi → tin 'Gen (lỗi)' với lý do thật, engine='error', error=True; không còn phản hồi tự sinh.
- dispatch_swarm_workflow: lệnh agy thật qua directive_guard; worker không có task → lỗi rõ.
- get_tmux_sessions: attach_cmd = tmux attach -t <sid>.
"""
import json
import os
import sys
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-nofake-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
SENT_LOG = os.path.join(TMP, "tmux-sent.log")
# tmux giả: ghi lại send-keys, các lệnh khác thoát 1 (không tạo phiên thật)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write('#!/bin/sh\nif [ "$1" = "send-keys" ]; then echo "$3|$4" >> "%s"; exit 0; fi\nexit 1\n' % SENT_LOG)
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

AGY_OK = os.path.join(FAKEBIN, "agy-ok")
with open(AGY_OK, "w") as f:
    f.write('#!/bin/sh\nprintf \'{"response": "Trả lời thật từ agy giả cho: %s", "conversation_id": "agy-conv-1", "usage": {"total_tokens": 42}}\' "$4"\nexit 0\n')
os.chmod(AGY_OK, 0o755)
AGY_429 = os.path.join(FAKEBIN, "agy-429")
with open(AGY_429, "w") as f:
    f.write("#!/bin/sh\necho 'Error: RESOURCE_EXHAUSTED: Individual quota reached. Resets in 3h7m' >&2\nexit 1\n")
os.chmod(AGY_429, 0o755)
AGY_CRASH = os.path.join(FAKEBIN, "agy-crash")
with open(AGY_CRASH, "w") as f:
    f.write("#!/bin/sh\necho 'Error: something exploded (stack trace)' >&2\nexit 2\n")
os.chmod(AGY_CRASH, 0o755)
AGY_EMPTY = os.path.join(FAKEBIN, "agy-empty")
with open(AGY_EMPTY, "w") as f:
    f.write('#!/bin/sh\necho \'{"response": "", "usage": {}}\'\nexit 0\n')
os.chmod(AGY_EMPTY, 0o755)
AGY_MISSING = os.path.join(FAKEBIN, "agy-khong-ton-tai")

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY_OK
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, directive_guard  # noqa: E402

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


def last_gen_msg(conv_id):
    with db.get_connection() as conn:
        r = conn.execute("SELECT author, role, content FROM gen_messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (conv_id,)).fetchone()
        return dict(r) if r else None


REQUIRED_KEYS = ("user_msg_id", "reply_id", "reply", "cited_notes", "model", "conv_title", "engine", "usage", "kanban_updates", "error")

print("[1] send_gen_chat với agy giả OK → trả lời thật, engine agy-cli, error=False")
res = db.send_gen_chat("conv-t1", "Ryan (Owner)", "xin chào", "Gemini 3.8 Flash (High)", "owner_default")
check("đủ key response cũ + error", all(k in res for k in REQUIRED_KEYS), str(sorted(res.keys())))
check("reply là output thật của agy giả", "Trả lời thật từ agy giả" in res["reply"], res["reply"][:100])
check("engine agy-cli, error False", res["engine"] == "agy-cli" and res["error"] is False, f"{res['engine']}/{res['error']}")
check("usage.total_tokens = 42", res["usage"].get("total_tokens") == 42, str(res["usage"]))
m = last_gen_msg("conv-t1")
check("tin lưu tác giả 'Gen Core (agy CLI)'", m and m["author"] == "Gen Core (agy CLI)", str(m))
with db.get_connection() as conn:
    r = conn.execute("SELECT agy_conv_id, total_tokens FROM gen_conversations WHERE id='conv-t1'").fetchone()
check("agy_conv_id + total_tokens cập nhật", r and r["agy_conv_id"] == "agy-conv-1" and r["total_tokens"] == 42, dict(r) if r else "none")

print("[2] agy giả 429 → tin 'Gen (lỗi)' với lý do thật, không tự sinh phản hồi")
os.environ["GW_AGY_BIN"] = AGY_429
res = db.send_gen_chat("conv-t2", "Ryan (Owner)", "tiến độ thế nào?", "Claude Sonnet 4.6 (Thinking)", "owner_default")
check("error True, engine error", res["error"] is True and res["engine"] == "error", f"{res['error']}/{res['engine']}")
check("error_code RESOURCE_EXHAUSTED", res["error_code"] == "RESOURCE_EXHAUSTED", res["error_code"])
check("reply dạng 'agy không trả lời: ...' kèm 429 và giờ hồi", res["reply"].startswith("agy không trả lời:") and "429" in res["reply"] and "3h7m" in res["reply"] and res["reply"].endswith("Không có phản hồi tự sinh."), res["reply"])
check("không còn văn mẫu 'Dạ em'", "Dạ em" not in res["reply"] and "Sếp" not in res["reply"])
m = last_gen_msg("conv-t2")
check("tin lưu tác giả 'Gen (lỗi)'", m and m["author"] == "Gen (lỗi)" and m["content"] == res["reply"], str(m))
check("kanban_updates rỗng, cited_notes rỗng", res["kanban_updates"] == [] and res["cited_notes"] == [])

print("[3] agy thoát mã 2 → lý do EXIT_2 kèm stderr thật")
os.environ["GW_AGY_BIN"] = AGY_CRASH
res = db.send_gen_chat("conv-t2", "Ryan (Owner)", "hỏi tiếp", "Gemini 3.8 Flash (High)")
check("error_code EXIT_2", res["error_code"] == "EXIT_2", res["error_code"])
check("reply chứa stderr thật", "something exploded" in res["reply"], res["reply"])

print("[4] agy không tồn tại → AGY_NOT_FOUND")
os.environ["GW_AGY_BIN"] = AGY_MISSING
res = db.send_gen_chat("conv-t2", "Ryan (Owner)", "hỏi nữa", "Gemini 3.8 Flash (High)")
check("error_code AGY_NOT_FOUND", res["error_code"] == "AGY_NOT_FOUND", res["error_code"])
check("reply nêu đường dẫn agy", "agy-khong-ton-tai" in res["reply"], res["reply"])

print("[5] agy trả JSON response rỗng → EMPTY_RESPONSE (không bịa)")
os.environ["GW_AGY_BIN"] = AGY_EMPTY
res = db.send_gen_chat("conv-t2", "Ryan (Owner)", "hỏi nữa", "Gemini 3.8 Flash (High)")
check("error_code EMPTY_RESPONSE", res["error_code"] == "EMPTY_RESPONSE", res["error_code"])

print("[6] hàm generate_gen_smart_reply và process_orch_instruction đã bị xóa")
check("không còn db.generate_gen_smart_reply", not hasattr(db, "generate_gen_smart_reply"))
check("không còn db.process_orch_instruction", not hasattr(db, "process_orch_instruction"))

print("[7] dispatch_swarm_workflow: worker không có task → lỗi rõ, không echo giả")
os.environ["GW_AGY_BIN"] = AGY_OK
if os.path.exists(SENT_LOG):
    os.remove(SENT_LOG)
res = db.dispatch_swarm_workflow()
check("4 worker đều có kết quả (security, frontend đã bỏ)", set(res.keys()) == {"gw-lead-agy", "gw-backend-agy", "gw-devops-agy", "gw-qa-agy"}, str(sorted(res.keys())))
check("tất cả status=error 'không có task'", all(v["status"] == "error" and "không có task" in v["reason"] for v in res.values()), str(res)[:300])
check("không gửi gì vào tmux", not os.path.exists(SENT_LOG))

print("[8] dispatch với task thật → lệnh agy --mode plan -p qua allowlist")
with db.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-T', 'PRJ-GEN-WORKPLACE', 'RM test', '', 1, 'queued', 1)")
    conn.execute("INSERT OR IGNORE INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES ('TODO-T1', 'RM-T', 'PRJ-GEN-WORKPLACE', \"Kiểm tra API /api/state; có 'nháy' và $(x)\", 'QA Tester', 'queued')")
    conn.execute("UPDATE tmux_sessions SET current_task_id = 'TODO-T1' WHERE id = 'gw-qa-agy'")
    conn.commit()
res = db.dispatch_swarm_workflow()
qa = res["gw-qa-agy"]
check("gw-qa-agy status dispatched", qa["status"] == "dispatched", str(qa))
check("task_id TODO-T1", qa["task_id"] == "TODO-T1")
cmd = qa["command"]
check("lệnh dạng agy ... --mode plan -p 'Thực hiện task TODO-T1: ...'", cmd.startswith("agy ") and "--mode plan -p 'Thực hiện task TODO-T1:" in cmd, cmd)
check("tee vào ~/gw-reports + echo XONG", "| tee ~/gw-reports/" in cmd and 'echo "=== XONG exit=${PIPESTATUS[0]} ==="' in cmd, cmd)
import re  # noqa: E402
m_p = re.search(r"-p '([^']*)'", cmd)
check("tiêu đề nằm trọn trong nháy đơn, không còn nháy/$(", m_p is not None and "$(" not in cmd and "nháy" in m_p.group(1) and cmd.count("'") == 4, cmd)
check("không có echo giả 'hoàn tất 0-error'", "0-error" not in cmd and "[QA TESTER]" not in cmd)
ok, reason = directive_guard.guard("gw-qa-agy", cmd)
check("directive_guard chấp nhận lệnh", ok, reason)
check("các worker khác vẫn error", res["gw-lead-agy"]["status"] == "error")
sent = open(SENT_LOG).read() if os.path.exists(SENT_LOG) else ""
check("tmux send-keys nhận đúng lệnh cho gw-qa-agy", f"gw-qa-agy|{cmd}" in sent, sent[:200])
with db.get_connection() as conn:
    a = conn.execute("SELECT allowed, channel FROM directive_audit ORDER BY id DESC LIMIT 1").fetchone()
check("directive_audit ghi allowed=1", a and a["allowed"] == 1, dict(a) if a else "none")

print("[9] dispatch 1 worker (session_id) và worker lạ")
res1 = db.dispatch_swarm_workflow(session_id="gw-qa-agy")
check("chỉ 1 kết quả, dispatched", list(res1.keys()) == ["gw-qa-agy"] and res1["gw-qa-agy"]["status"] == "dispatched", str(res1)[:200])
res2 = db.dispatch_swarm_workflow(session_id="gw-khong-co")
check("worker lạ → error", res2["gw-khong-co"]["status"] == "error", str(res2))

print("[10] attach_cmd = tmux attach -t <sid>")
sess = db.get_tmux_sessions()
check("attach_cmd đúng, không docker", all(s["attach_cmd"] == f"tmux attach -t {s['id']}" for s in sess) and sess, str([s["attach_cmd"] for s in sess][:2]))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

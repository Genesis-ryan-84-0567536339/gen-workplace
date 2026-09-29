#!/usr/bin/env python3
"""
Test Issue #24: Việc là trung tâm (Kanban ↔ Phòng giao ban ↔ Worker) + lỗi rà soát UI. Chạy không cần agy/tmux thật:
HTTP server loopback cổng ngẫu nhiên, agy giả (GW_AGY_BIN), tmux giả, repo git tạm cho worktree của vai.
- POST /api/task/assign: claim task cho vai, war-room "@vai Thực hiện TSK-n", prompt agy có tiêu đề + checklist + viec_ref,
  trả dispatch_id; lỗi 400 / 404 / 409.
- GET /api/dispatch/log?task_id=&session_id=: lọc đúng.
- post_warroom_message: mã TSK trong tin → task_id (ưu tiên hơn current_task_id); @Gen / @Toàn Đội không giao việc.
- Dispatch gắn task kết thúc → 1 tin kết quả trong phiên của task (dispatch:<id>, report_path), tick checklist theo
  [KANBAN_UPDATE: TSK-n | CHECK: id]; không ghi 2 lần; complete_task bằng dispatch của người giữ task được (không cần force).
- /api/state có gen_session_todos kèm last_dispatch.
- switch_google_account kiểm tham số ({} → lỗi, không ghi NULL); account_type NULL sẵn trong DB → /api/tmux/sessions 200;
  fetch_live_google_quota(None) không lỗi; /api/quota/live gắn exhausted từ profile_quota_state kể cả khi số từ Cloud Code.
- MCP: create_kanban_task nhận assigned_to; tools có annotations.readOnlyHint; link MCP theo Host header, không localhost:8888.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-taskhub-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)
PROMPT_LOG = os.path.join(TMP, "agy-prompts.log")
AGY = os.path.join(FAKEBIN, "agy")
# agy giả: ghi prompt (đối số sau -p) ra file, trả lời có dòng tick checklist chk-a của TSK nhắc trong prompt
with open(AGY, "w") as f:
    f.write('#!/bin/bash\nprev=""; for a; do [ "$prev" = "-p" ] && last="$a"; prev="$a"; done\nprintf "%%s\\n===\\n" "$last" >> "%s"\n'
            'tid=$(printf "%%s" "$last" | grep -o "THÔNG TIN VIỆC TSK-[0-9]*" | head -1 | sed "s/THÔNG TIN VIỆC //")\n'
            'echo "Đã kiểm tra xong."\n[ -n "$tid" ] && echo "[KANBAN_UPDATE: $tid | CHECK: chk-a]"\nexit 0\n' % PROMPT_LOG)
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
os.environ.pop("GW_PUBLIC_ORIGIN", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, main, mcp_core  # noqa: E402

_real_fetch_live = db.fetch_live_google_quota
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


def call(method, path, payload=None, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        status = e.code
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


def wait_done(did, timeout=30):
    st, res = call("GET", f"/api/dispatch/wait?dispatch_id={did}&timeout_sec={timeout}")
    return res or {}


conv_res = db.create_gen_conversation("PRJ-GEN-WORKPLACE", "VIEC-7: test task hub")
CONV = conv_res.get("id") or conv_res.get("conv_id")
check("tạo phiên thử", bool(CONV), str(conv_res))
t1 = db.save_gen_session_todo(CONV, None, "Kiểm tra API giao việc", "Chỉ đọc, không sửa file", "todo", "high", "Gen Core",
                              [{"id": "chk-a", "text": "Đọc backend/main.py", "done": False},
                               {"id": "chk-b", "text": "Viết nhận xét", "done": False}], viec_ref="VIEC-7")
T1 = t1["id"]
t2 = db.save_gen_session_todo(CONV, None, "Task phụ", "", "todo", "low", "Gen Core", [], viec_ref="VIEC-7")
T2 = t2["id"]
check("tạo 2 task", T1 and T2 and T1 != T2, f"{t1} {t2}")


def conv_messages():
    return db.get_gen_messages(CONV)


print("[1] POST /api/task/assign: lỗi tham số")
st, res = call("POST", "/api/task/assign", {"todo_id": T1})
check("thiếu session_id → 400", st == 400 and "error" in res, f"{st} {res}")
st, res = call("POST", "/api/task/assign", {"todo_id": T1, "session_id": "hacker"})
check("vai lạ → 400", st == 400, f"{st} {res}")
st, res = call("POST", "/api/task/assign", {"todo_id": "TSK-99999", "session_id": "qa"})
check("task không có → 404", st == 404, f"{st} {res}")

print("[2] POST /api/task/assign: giao TSK cho @qa")
open(PROMPT_LOG, "w").close()
st, res = call("POST", "/api/task/assign", {"todo_id": T1, "session_id": "qa"})
check("200 + dispatch_id", st == 200 and isinstance(res.get("dispatch_id"), int), f"{st} {res}")
DID = res.get("dispatch_id")
check("session_id chuẩn hóa gw-qa-agy", res.get("session_id") == "gw-qa-agy", str(res))
check("tin war-room có @qa + TSK + tiêu đề + checklist", res.get("message", "").startswith(f"@qa Thực hiện {T1} (VIEC-7): Kiểm tra API giao việc")
      and "Đọc backend/main.py" in res.get("message", ""), res.get("message"))
with db.get_connection() as c:
    row = dict(c.execute("SELECT * FROM gen_session_todos WHERE id = ?", (T1,)).fetchone())
    cur_task = c.execute("SELECT current_task_id FROM tmux_sessions WHERE id = 'gw-qa-agy'").fetchone()["current_task_id"]
check("task đã claim cho gw-qa-agy (in_progress)", row["claimed_by"] == "gw-qa-agy" and row["status"] == "in_progress", str(row))
check("current_task_id của gw-qa-agy = task", cur_task == T1, cur_task)
w = wait_done(DID)
check("dispatch xong (done)", w.get("status") == "done", str(w))
check("dispatch_log.task_id = task", w.get("task_id") == T1, str(w))
prompt = open(PROMPT_LOG).read()
check("prompt agy có tiêu đề + checklist (id mục) + viec_ref", f"THÔNG TIN VIỆC {T1}" in prompt and "Tiêu đề: Kiểm tra API giao việc" in prompt
      and "(chk-a) Đọc backend/main.py" in prompt and "VIEC-7" in prompt, prompt[:800])
st, res2 = call("POST", "/api/task/assign", {"todo_id": T1, "session_id": "backend"})
check("giao lại cho vai khác khi qa đang giữ → 409 locked", st == 409 and res2.get("code") == "locked", f"{st} {res2}")

print("[3] Kết quả dispatch tự ghi về phiên của task + tick checklist")
msgs = [m for m in conv_messages() if f"dispatch:{DID}" in (m.get("content") or "")]
check("đúng 1 tin kết quả trong phiên của task", len(msgs) == 1, str([m.get("content", "")[:80] for m in conv_messages()]))
body = msgs[0]["content"] if msgs else ""
check("tin có dispatch:<id>, trạng thái XONG, report_path, gợi ý bằng chứng", f"Kết quả giao việc dispatch:{DID} · {T1} · gw-qa-agy · XONG" in body
      and "Báo cáo: " in body and "gw-reports" in body and f"Bằng chứng nghiệm thu gợi ý: dispatch:{DID}" in body, body[:600])
check("tác giả = worker", msgs and msgs[0].get("author") == "gw-qa-agy (agy)", str(msgs[:1]))
chk = {c["id"]: c["done"] for c in db._task_detail(T1)["checklist"]}
check("worker báo CHECK: chk-a → mục chk-a đã tick, chk-b chưa", chk == {"chk-a": True, "chk-b": False}, str(chk))
check("gọi lại report_dispatch_to_task không ghi thêm", db.report_dispatch_to_task(DID) is None
      and len([m for m in conv_messages() if f"dispatch:{DID}" in (m.get("content") or "")]) == 1)
with db.get_connection() as c:
    tmid = c.execute("SELECT task_msg_id FROM dispatch_log WHERE id = ?", (DID,)).fetchone()["task_msg_id"]
check("dispatch_log.task_msg_id = id tin", msgs and tmid == msgs[0]["id"], f"{tmid}")

print("[4] GET /api/dispatch/log lọc task_id / session_id")
r_other = db.post_warroom_message(message="@backend xem qua cấu trúc repo", author="Ryan (Owner)", wait=True)
st, lg = call("GET", f"/api/dispatch/log?task_id={T1}")
check("lọc task_id → chỉ dispatch của task", st == 200 and lg["count"] >= 1 and all(i["task_id"] == T1 for i in lg["items"]), str(lg)[:300])
st, lg = call("GET", "/api/dispatch/log?session_id=gw-backend-agy&limit=5")
check("lọc session_id → chỉ của gw-backend-agy", st == 200 and lg["count"] >= 1 and all(i["session_id"] == "gw-backend-agy" for i in lg["items"]), str(lg)[:300])
st, lg = call("GET", f"/api/dispatch/log?task_id={T1}&session_id=gw-backend-agy")
check("lọc cả 2 (không khớp) → rỗng", st == 200 and lg["count"] == 0, str(lg)[:200])
st, lg = call("GET", "/api/dispatch/log?limit=abc")
check("limit sai → vẫn 200", st == 200, str(st))

print("[5] post_warroom_message: mã TSK trong tin → task_id (ưu tiên hơn current_task_id)")
# gw-qa-agy đang giữ T1 (current_task_id = T1); tin nhắc T2 → dispatch gắn T2
res = db.post_warroom_message(message=f"@qa kiểm tra {T2} giúp", author="Ryan (Owner)", wait=True)
d = res["dispatches"][0]
check("dispatches[] có task_id = TSK trong tin", d["task_id"] == T2 and res.get("task_id") == T2, str(res))
with db.get_connection() as c:
    dt = c.execute("SELECT task_id FROM dispatch_log WHERE id = ?", (d["dispatch_id"],)).fetchone()["task_id"]
check("dispatch_log.task_id = T2 (không phải current_task_id T1)", dt == T2, dt)
res = db.post_warroom_message(message="@qa tiếp tục việc đang làm", author="Ryan (Owner)", wait=True)
check("không nhắc TSK → dùng current_task_id", res["dispatches"][0]["task_id"] == T1, str(res["dispatches"]))
res = db.post_warroom_message(message="@qa xem TSK-424242 (không tồn tại)", author="Ryan (Owner)", wait=True)
check("TSK không có thật → bỏ qua, dùng current_task_id", res["dispatches"][0]["task_id"] == T1, str(res["dispatches"]))
res = db.post_warroom_message(message="@Gen @Toàn Đội họp nhanh", author="Ryan (Owner)", wait=True)
check("@Gen / @Toàn Đội: không giao việc, có ghi chú", res["dispatched"] == [] and "không giao việc" in res["note"], str(res))

print("[6] complete_task: đóng thay người giữ bằng dispatch của chính họ")
res = db.complete_task("owner-ui", T1, f"dispatch:{r_other['dispatches'][0]['dispatch_id']}")
check("dispatch của vai khác → vẫn not_holder", res.get("code") in ("not_holder", "invalid_evidence"), str(res))
res = db.complete_task("owner-ui", T1, f"dispatch:{DID}")
check("dispatch done của gw-qa-agy cho đúng task → completed, closed_for_holder", res.get("status") == "completed"
      and res.get("closed_for_holder") == "gw-qa-agy", str(res))
with db.get_connection() as c:
    row = dict(c.execute("SELECT status, claimed_by, evidence_ref FROM gen_session_todos WHERE id = ?", (T1,)).fetchone())
    cur_task = c.execute("SELECT current_task_id FROM tmux_sessions WHERE id = 'gw-qa-agy'").fetchone()["current_task_id"]
    aud = c.execute("SELECT action, held_by FROM task_evidence_audit WHERE task_id = ? ORDER BY id DESC LIMIT 1", (T1,)).fetchone()
check("task done, nhả khóa, evidence = dispatch", row == {"status": "done", "claimed_by": "", "evidence_ref": f"dispatch:{DID}"}, str(row))
check("gw-qa-agy nhả current_task_id", cur_task == "", cur_task)
check("audit action=holder_dispatch", aud and aud["action"] == "holder_dispatch" and aud["held_by"] == "gw-qa-agy", str(dict(aud) if aud else None))
st, res = call("POST", "/api/task/assign", {"todo_id": T1, "session_id": "qa"})
check("giao task đã done → 409", st == 409 and res.get("code") == "already_done", f"{st} {res}")

print("[7] /api/state có gen_session_todos kèm last_dispatch")
st, state = call("GET", "/api/state")
tasks = {t["id"]: t for t in state.get("gen_session_todos", [])}
check("có T1, T2", T1 in tasks and T2 in tasks, str(list(tasks))[:200])
ld = (tasks.get(T1) or {}).get("last_dispatch") or {}
check("T1.last_dispatch = lần giao gần nhất cho T1 (done)", ld.get("status") == "done" and ld.get("session_id") == "gw-qa-agy", str(ld))
check("T1 có checklist đã parse + done_items", tasks[T1]["total_items"] == 2 and tasks[T1]["done_items"] == 1, str(tasks[T1])[:300])
st, todos = call("GET", f"/api/gen/session/todos?conv_id={CONV}")
check("GET /api/gen/session/todos cũng có last_dispatch", any(t.get("last_dispatch") for t in todos["todos"]), str(todos)[:200])

print("[8] switch_google_account kiểm tham số")
r = mcp_core.execute_tool("switch_google_account", {})
check("{} → isError, không ghi gì", r["isError"] and "account_id" in r["content"][0]["text"], str(r))
r = mcp_core.execute_tool("switch_google_account", {"session_id": "gw-lead-agy", "account_id": "profile99"})
check("hồ sơ không có → isError", r["isError"] and "profile99" in r["content"][0]["text"], str(r))
r = mcp_core.execute_tool("switch_google_account", {"session_id": "gw-khong-co", "account_id": "owner_default"})
check("worker không có → isError", r["isError"], str(r))
with db.get_connection() as c:
    nulls = c.execute("SELECT count(*) FROM tmux_sessions WHERE account_type IS NULL OR account_type = ''").fetchone()[0]
check("không có account_type NULL sau các lần gọi sai", nulls == 0, str(nulls))
r = mcp_core.execute_tool("switch_google_account", {"session_id": "gw-lead-agy", "account_id": "owner_default"})
check("tham số đúng → ok", not r["isError"], str(r))
st, res = call("POST", "/api/tmux/account", {"session_id": "gw-lead-agy", "account_type": ""})
check("POST /api/tmux/account account_type rỗng → 400", st == 400, f"{st} {res}")

print("[9] account_type NULL sẵn trong DB (do lần Test cũ) → /api/tmux/sessions vẫn 200")
db.fetch_live_google_quota = _real_fetch_live   # dùng hàm thật: token không có → None, không gọi mạng
with db.get_connection() as c:
    c.execute("UPDATE tmux_sessions SET account_type = NULL WHERE id = 'gw-qa-agy'")
    c.commit()
try:
    v = db.fetch_live_google_quota(None)
    check("fetch_live_google_quota(None) không lỗi", v is None or isinstance(v, tuple), str(v))
except Exception as e:
    check("fetch_live_google_quota(None) không lỗi", False, repr(e))
st, res = call("GET", "/api/tmux/sessions")
check("/api/tmux/sessions 200 (4 vai, security + frontend đã bỏ)", st == 200 and len(res.get("sessions", [])) == 4, f"{st} {str(res)[:200]}")
qa = next((s for s in res.get("sessions", []) if s["id"] == "gw-qa-agy"), {})
check("worker có account_type NULL hiện owner_default", qa.get("account_type") == "owner_default", str(qa.get("account_type")))
db.init_db()
with db.get_connection() as c:
    at = c.execute("SELECT account_type FROM tmux_sessions WHERE id = 'gw-qa-agy'").fetchone()["account_type"]
check("init_db sửa account_type NULL → owner_default", at == "owner_default", str(at))

print("[10] /api/quota/live: nhánh Cloud Code cũng có exhausted")
fake_g = {"family": "Google Gemini", "percent": 42, "status": "ready"}
fake_c = {"family": "Anthropic Claude", "percent": 10, "status": "ready"}
db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: (fake_g, fake_c)
db.mark_profile_exhausted("owner_default", "2h", "gemini")
st, q = call("GET", "/api/quota/live?profile=owner_default")
check("source cloudcode_api_live", q.get("source") == "cloudcode_api_live", str(q)[:200])
check("gemini.exhausted = true (từ profile_quota_state), có reset_at_label", q["gemini"].get("exhausted") is True and q["gemini"].get("reset_at_label"), str(q["gemini"]))
check("claude.exhausted = false (không null)", q["claude"].get("exhausted") is False, str(q["claude"]))
check("không sửa dict cache gốc", "exhausted" not in fake_g)
r = mcp_core.execute_tool("get_live_quota", {"profile_id": "owner_default"})
check("MCP get_live_quota cũng có exhausted", json.loads(r["content"][0]["text"])["gemini"].get("exhausted") is True)
db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: None

print("[11] MCP: create_kanban_task assigned_to, annotations, link theo Host")
r = mcp_core.execute_tool("create_kanban_task", {"conv_id": CONV, "title": "Task gán qua assigned_to", "viec_ref": "VIEC-7", "assigned_to": "gw-backend-agy"})
tid = json.loads(r["content"][0]["text"]).get("id")
check("assigned_to được lưu vào assigned_agent", db._task_detail(tid) and next(t for t in db.get_gen_session_todos(CONV) if t["id"] == tid)["assigned_agent"] == "gw-backend-agy", str(r))
anns = {t["name"]: t.get("annotations", {}).get("readOnlyHint") for t in mcp_core.TOOLS}
check("annotations.readOnlyHint: list_kanban_tasks true, switch_google_account false, post_warroom_message false",
      anns.get("list_kanban_tasks") is True and anns.get("switch_google_account") is False and anns.get("post_warroom_message") is False, str(anns))
st, tools = call("GET", "/api/mcp/tools", headers={"Host": "10.1.2.3:8899"})
check("tools_count = số tool thật", tools.get("tools_count") == len(mcp_core.TOOLS), str(tools.get("tools_count")))
check("endpoint theo Host header", tools["auth"]["endpoint"] == "http://10.1.2.3:8899/mcp", str(tools["auth"]))
st, tok = call("POST", "/api/mcp/tokens/create", {"name": "test agent"}, headers={"Host": "gw.example.com", "X-Forwarded-Proto": "https"})
check("curl_snippet/endpoint token mới theo Host (https)", tok.get("endpoint") == "https://gw.example.com/mcp" and "localhost:8888" not in tok.get("curl_snippet", ""), str(tok)[:300])
st, _ = call("GET", "/favicon.ico")
check("/favicon.ico không 404", st == 204, str(st))

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

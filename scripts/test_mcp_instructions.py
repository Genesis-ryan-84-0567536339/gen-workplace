#!/usr/bin/env python3
"""
Test Issue #19 (MCP initialize có instructions + ghi tiến độ vào phiên). Chạy không cần server ngoài/agy/tmux:
- initialize (mcp_core, HTTP POST /mcp, stdio mcp_server.py) trả result.instructions không rỗng, đủ các mục bootstrap.
- load_mcp_instructions: thiếu file / file rỗng → giá trị mặc định; file có nội dung → đọc đúng file.
- create_conversation qua MCP: tiêu đề đúng, hiện trong list_conversations và GET /api/gen/conversations (nguồn của UI);
  reuse_existing=true → trả lại phiên cũ, không tạo trùng.
- log_session_message: chỉ lưu tin (không gọi agy/subprocess), msg_count tăng; lỗi rõ khi thiếu phiên/content/role sai.
- POST /api/gen/conversations/log: cùng logic cho client không dùng MCP.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-mcpinstr-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
AGY_CALLED = os.path.join(TMP, "agy-called.log")
AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write('#!/bin/sh\necho called >> "%s"\necho \'{"response": "không được gọi", "usage": {}}\'\nexit 0\n' % AGY_CALLED)
os.chmod(AGY, 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, main, mcp_core  # noqa: E402

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


def tool(name, args):
    resp = mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = resp["result"]
    try:
        payload = json.loads(res["content"][0]["text"])
    except Exception:
        payload = res["content"][0]["text"]
    return res.get("isError"), payload


server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def call(method, path, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "null")


KEYWORDS = ("VIEC-<n>", "create_conversation", "list_conversations", "create_kanban_task", "viec_ref", "claim_task",
            "update_task_checklist", "log_session_message", "post_warroom_message", "wait_worker_result",
            "complete_task", "gen-workplace-dispatch/SKILL.md")

print("[1] initialize qua mcp_core có instructions đủ mục")
init = mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
instr = init["result"].get("instructions", "")
check("instructions là chuỗi không rỗng", isinstance(instr, str) and len(instr.strip()) > 100, repr(instr)[:120])
missing = [k for k in KEYWORDS if k not in instr]
check("đủ các mục bootstrap", not missing, str(missing))
check("instructions đọc từ backend/mcp_instructions.md", instr == open(os.path.join(ROOT, "backend", "mcp_instructions.md"), encoding="utf-8").read().strip())
check("vẫn giữ protocolVersion/serverInfo", init["result"]["protocolVersion"] == "2025-06-18" and init["result"]["serverInfo"]["name"] == "gen-workplace")

print("[2] load_mcp_instructions: thiếu file / rỗng → mặc định")
check("thiếu file → mặc định", mcp_core.load_mcp_instructions(os.path.join(TMP, "khong-co.md")) == mcp_core.DEFAULT_MCP_INSTRUCTIONS)
empty = os.path.join(TMP, "empty.md")
open(empty, "w").write("  \n")
check("file rỗng → mặc định", mcp_core.load_mcp_instructions(empty) == mcp_core.DEFAULT_MCP_INSTRUCTIONS)
custom = os.path.join(TMP, "custom.md")
open(custom, "w", encoding="utf-8").write("1. Nội dung riêng\n")
check("file có nội dung → đọc file", mcp_core.load_mcp_instructions(custom) == "1. Nội dung riêng")
missing_def = [k for k in KEYWORDS if k not in mcp_core.DEFAULT_MCP_INSTRUCTIONS]
check("mặc định cũng đủ mục", not missing_def, str(missing_def))

print("[3] initialize qua HTTP POST /mcp và stdio")
st, js = call("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
check("HTTP /mcp có instructions", st == 200 and js["result"].get("instructions") == instr, f"{st} {str(js)[:150]}")
st, js = call("GET", "/api/mcp/status")
check("GET /api/mcp/status có instructions", st == 200 and js.get("instructions") == instr)
proc = subprocess.run([sys.executable, os.path.join(ROOT, "backend", "mcp_server.py")],
                      input=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}}) + "\n",
                      capture_output=True, text=True, timeout=60, env=dict(os.environ))
try:
    stdio_res = json.loads(proc.stdout.strip().splitlines()[-1])
except Exception:
    stdio_res = {}
check("stdio có instructions", (stdio_res.get("result") or {}).get("instructions") == instr, proc.stdout[:200] + proc.stderr[-200:])

print("[4] tools/list có log_session_message, create_conversation có reuse_existing")
tools = {t["name"]: t for t in mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})["result"]["tools"]}
check("có log_session_message", "log_session_message" in tools
      and tools["log_session_message"]["inputSchema"]["required"] == ["conv_id", "content"])
check("create_conversation có reuse_existing", "reuse_existing" in tools["create_conversation"]["inputSchema"]["properties"])

print("[5] create_conversation qua MCP → hiện trong list_conversations và API của UI")
TITLE = "VIEC-19: Thêm instructions cho MCP"
err, conv = tool("create_conversation", {"title": TITLE})
cid = conv.get("id") if isinstance(conv, dict) else None
check("tạo được, đúng tiêu đề", not err and cid and conv["title"] == TITLE and conv["reused"] is False, str(conv))
err, lst = tool("list_conversations", {})
found = [c for c in lst["conversations"] if c["id"] == cid]
check("list_conversations có phiên, đúng tiêu đề", len(found) == 1 and found[0]["title"] == TITLE, str(found))
st, js = call("GET", "/api/gen/conversations")
check("GET /api/gen/conversations (UI) có phiên", st == 200 and any(c["id"] == cid and c["title"] == TITLE for c in js["conversations"]))
err, again = tool("create_conversation", {"title": TITLE, "reuse_existing": True})
check("reuse_existing=true → dùng lại phiên cũ", not err and again["id"] == cid and again["reused"] is True, str(again))
err, lst2 = tool("list_conversations", {})
check("không tạo trùng", sum(1 for c in lst2["conversations"] if c["title"] == TITLE) == 1)
err, other = tool("create_conversation", {"title": "  "})
check("tiêu đề rỗng → mặc định", not err and other["title"] == "Cuộc trò chuyện mới", str(other))

print("[6] log_session_message chỉ lưu tin, không gọi agy")
calls = []
real_turn, real_run, real_popen = db.call_agy_cli_turn, subprocess.run, subprocess.Popen
db.call_agy_cli_turn = lambda *a, **kw: calls.append(("agy", a)) or ("", None, {})
subprocess.run = lambda *a, **kw: calls.append(("run", a)) or real_run(*a, **kw)
subprocess.Popen = lambda *a, **kw: calls.append(("popen", a)) or real_popen(*a, **kw)
try:
    content = "Bắt đầu VIEC-19 — Issue https://github.com/Genesis-ryan-84-0567536339/gen-workplace/issues/19"
    err, res = tool("log_session_message", {"conv_id": cid, "content": content, "author": "Claude Code"})
    check("status logged", not err and res.get("status") == "logged" and res.get("message_id"), str(res))
    err, res2 = tool("log_session_message", {"conv_id": cid, "content": "Mở PR", "role": "user"})
    check("role user được nhận", not err and res2.get("role") == "user", str(res2))
finally:
    db.call_agy_cli_turn, subprocess.run, subprocess.Popen = real_turn, real_run, real_popen
check("không gọi agy / subprocess", not calls, str(calls))
check("agy giả không chạy", not os.path.exists(AGY_CALLED))
err, msgs = tool("get_conversation_messages", {"conv_id": cid})
m = msgs["messages"]
check("đúng 2 tin, không có tin trả lời AI", len(m) == 2 and m[0]["content"] == content and m[0]["role"] == "assistant"
      and m[0]["author"] == "Claude Code" and m[0]["model"] == "", str(m)[:300])
err, lst3 = tool("list_conversations", {})
cur = [c for c in lst3["conversations"] if c["id"] == cid][0]
check("msg_count = 2, last_msg là tin mới", cur["msg_count"] == 2 and cur["last_msg"] == "Mở PR", str(cur)[:200])

print("[7] log_session_message báo lỗi rõ")
err, res = tool("log_session_message", {"conv_id": "conv-khong-co", "content": "x"})
check("phiên không tồn tại → isError", err and "Không tìm thấy phiên" in res.get("error", ""), str(res))
err, res = tool("log_session_message", {"conv_id": cid, "content": "   "})
check("content rỗng → isError", err and "content" in res.get("error", ""), str(res))
err, res = tool("log_session_message", {"conv_id": cid, "content": "x", "role": "compact"})
check("role lạ → isError", err and "role" in res.get("error", ""), str(res))
err, res = tool("log_session_message", {"conv_id": cid, "content": "x" * 9000})
check("content quá dài → isError", err and "quá dài" in res.get("error", ""), str(res)[:120])

print("[8] POST /api/gen/conversations/log (REST, chỉ lưu)")
st, js = call("POST", "/api/gen/conversations/log", {"conv_id": cid, "content": "Xong — PR #20", "author": "QA"})
check("200 logged", st == 200 and js.get("status") == "logged", f"{st} {js}")
st, js = call("POST", "/api/gen/conversations/log", {"conv_id": "conv-khong-co", "content": "x"})
check("phiên không có → 404", st == 404, f"{st} {js}")
st, js = call("POST", "/api/gen/conversations/log", {"conv_id": cid, "content": ""})
check("content rỗng → 400", st == 400, f"{st} {js}")
check("agy giả vẫn không chạy", not os.path.exists(AGY_CALLED))

print("[9] quyền token domain 'chat' bao gồm log_session_message")
tok = db.create_mcp_agent_token("chat-only", ["chat"])
token_str = (tok or {}).get("token") if isinstance(tok, dict) else None
if token_str:
    ok, _, msg = db.verify_mcp_request_auth({"Authorization": f"Bearer {token_str}"}, {}, tool_name="log_session_message")
    check("token chat được gọi log_session_message", ok, str(msg))
    ok, _, _ = db.verify_mcp_request_auth({"Authorization": f"Bearer {token_str}"}, {}, tool_name="complete_task")
    check("token chat không gọi được complete_task", not ok)
else:
    check("tạo được token chat để kiểm quyền", False, str(tok))

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

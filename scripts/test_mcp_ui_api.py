#!/usr/bin/env python3
"""
Test Issue #28 (màn MCP & Kết nối): dữ liệu backend mà màn này đọc. Chạy không cần agy/tmux thật:
- mcp_core.TOOL_META là nguồn duy nhất: đủ mọi tool (không thừa/thiếu), nhóm & scope hợp lệ, có mô tả ngắn;
  READ_ONLY_TOOLS, annotations.readOnlyHint và scope kiểm quyền token đều suy ra từ đây.
- GET /api/mcp/status: tools kèm group / scope / read_only / summary, groups, scopes, stdio, auth.used_24h.
- Quyền token theo scope đọc từ TOOL_META (giữ nguyên hành vi cũ: chat, files, quota...; probe_quota chỉ token toàn quyền).
- POST /api/mcp/tokens/create từ chối scope lạ (400); token quá hạn hiện "expired" trong danh sách; used_24h đếm token vừa dùng.
- tools/list (MCP chuẩn) vẫn chỉ trả tool gốc, không lộ trường metadata nội bộ.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-mcpui-")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["GW_AGY_BIN"] = "/bin/false"
os.environ.pop("GW_PUBLIC_ORIGIN", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, main, mcp_core  # noqa: E402

PASSED = FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


print("[1] TOOL_META là nguồn duy nhất")
names = {t["name"] for t in mcp_core.TOOLS}
check("TOOL_META khớp đúng danh sách tool", set(mcp_core.TOOL_META) == names, str(names ^ set(mcp_core.TOOL_META)))
group_ids = {g["id"] for g in mcp_core.TOOL_GROUPS}
scope_ids = {s["id"] for s in mcp_core.TOKEN_SCOPES}
check("6 nhóm chức năng theo yêu cầu", [g["label"] for g in mcp_core.TOOL_GROUPS] ==
      ["Việc & Kanban", "Giao ban & Worker", "Phiên & ghi chú", "File", "Tài khoản & quota", "Hệ thống"])
bad = [n for n, m in mcp_core.TOOL_META.items()
       if m["group"] not in group_ids or (m["scope"] is not None and m["scope"] not in scope_ids)
       or not isinstance(m["read_only"], bool) or not m["summary"].strip() or len(m["summary"]) > 90]
check("mỗi tool: nhóm, scope hợp lệ, cờ chỉ đọc bool, mô tả ngắn ≤ 90 ký tự", not bad, str(bad))
check("READ_ONLY_TOOLS suy ra từ TOOL_META", mcp_core.READ_ONLY_TOOLS == {n for n, m in mcp_core.TOOL_META.items() if m["read_only"]})
check("annotations.readOnlyHint khớp TOOL_META",
      all(t["annotations"]["readOnlyHint"] is mcp_core.TOOL_META[t["name"]]["read_only"] for t in mcp_core.TOOLS))
check("phân loại giữ như trước: 13 chỉ đọc, gồm list_kanban_tasks; post_warroom_message có tác dụng phụ",
      len(mcp_core.READ_ONLY_TOOLS) == 13 and "list_kanban_tasks" in mcp_core.READ_ONLY_TOOLS and "post_warroom_message" not in mcp_core.READ_ONLY_TOOLS)
check("db.MCP_TOOL_SCOPES do mcp_core điền từ TOOL_META", db.MCP_TOOL_SCOPES == {n: m["scope"] for n, m in mcp_core.TOOL_META.items()})

print("[2] GET /api/mcp/status cho màn MCP & Kết nối")
st, js = call("GET", "/api/mcp/status", headers={"Host": "10.9.8.7:8888"})
check("200", st == 200, str(st))
tools = js.get("tools") or []
check("tools đủ và kèm group/scope/read_only/summary", len(tools) == len(names) and all({"group", "scope", "read_only", "summary", "inputSchema"} <= set(t) for t in tools))
check("tools xếp theo thứ tự nhóm", [t["group"] for t in tools] == sorted([t["group"] for t in tools], key=[g["id"] for g in mcp_core.TOOL_GROUPS].index))
check("groups + scopes có trong response", js.get("groups") == mcp_core.TOOL_GROUPS and js.get("scopes") == mcp_core.TOKEN_SCOPES)
stdio = js.get("stdio") or {}
check("stdio: script mcp_server.py thật + DATA_DIR của app", os.path.isfile(stdio.get("script", "")) and stdio.get("data_dir") == str(db.DATA_DIR) and stdio.get("wrapper_exists") is False, str(stdio))
check("auth.endpoint theo Host, có used_24h", js["auth"]["endpoint"] == "http://10.9.8.7:8888/mcp" and "used_24h" in js["auth"], str(js.get("auth")))
check("read_only_tools vẫn có (tương thích)", sorted(js.get("read_only_tools") or []) == sorted(mcp_core.READ_ONLY_TOOLS))
st, lst = call("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
lt = (lst.get("result") or {}).get("tools") or []
check("tools/list chuẩn MCP không kèm metadata nội bộ", lt and all("group" not in t and "summary" not in t for t in lt), str(lt[:1])[:200])

print("[3] quyền token theo scope từ TOOL_META")
st, tok = call("POST", "/api/mcp/tokens/create", {"name": "files-only", "permissions": ["files"]})
T = tok.get("token")
check("tạo token scope files", st == 200 and T, str(tok))
H = {"Authorization": f"Bearer {T}"}
ok_read, _, _ = db.verify_mcp_request_auth(H, {}, tool_name="read_workspace_file")
ok_note, _, _ = db.verify_mcp_request_auth(H, {}, tool_name="save_note")
ok_status, _, _ = db.verify_mcp_request_auth(H, {}, tool_name="get_system_status")
ok_kanban, _, msg = db.verify_mcp_request_auth(H, {}, tool_name="create_kanban_task")
check("files: đọc file, ghi chú, trạng thái được; Kanban bị chặn", ok_read and ok_note and ok_status and not ok_kanban, str(msg))
st, q = call("POST", "/api/mcp/tokens/create", {"name": "quota", "permissions": ["quota"]})
HQ = {"Authorization": f"Bearer {q.get('token')}"}
check("quota: get_live_quota được, probe_quota (chạy agy) chỉ token toàn quyền",
      db.verify_mcp_request_auth(HQ, {}, tool_name="get_live_quota")[0] and not db.verify_mcp_request_auth(HQ, {}, tool_name="probe_quota")[0])
st, a = call("POST", "/api/mcp/tokens/create", {"name": "all", "permissions": ["all"]})
check("all: probe_quota được", db.verify_mcp_request_auth({"Authorization": f"Bearer {a.get('token')}"}, {}, tool_name="probe_quota")[0])
st, js = call("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "create_kanban_task", "arguments": {}}}, headers=H)
check("HTTP /mcp: token files gọi create_kanban_task → 401", st == 401 and "create_kanban_task" in json.dumps(js, ensure_ascii=False), f"{st} {js}")

print("[4] tạo token: scope lạ bị từ chối")
st, js = call("POST", "/api/mcp/tokens/create", {"name": "x", "permissions": ["admin-root"]})
check("scope lạ → 400", st == 400 and "admin-root" in js.get("error", ""), f"{st} {js}")
st, js = call("POST", "/api/mcp/tokens/create", {"name": "x", "permissions": ["list_notes"]})
check("tên tool có thật vẫn nhận (tương thích)", st == 200 and js.get("ok"), f"{st} {js}")
st, js = call("POST", "/api/mcp/tokens/create", {"name": "  ", "permissions": ["all"]})
check("tên rỗng → 400", st == 400, f"{st} {js}")

print("[5] danh sách token: hết hạn, dùng trong 24 giờ, thu hồi")
with db.get_connection() as conn:
    conn.execute("UPDATE mcp_agent_tokens SET expires_at = '2000-01-01 00:00:00' WHERE id = ?", (q["id"],))
    conn.commit()
st, js = call("GET", "/api/mcp/tokens")
by = {t["id"]: t for t in js.get("tokens", [])}
check("token quá hạn hiện status expired", by.get(q["id"], {}).get("status") == "expired", str(by.get(q["id"])))
check("used_24h đếm token vừa dùng (files-only)", js["auth_status"]["used_24h"] >= 1, str(js["auth_status"]))
st, r = call("POST", "/api/mcp/tokens/revoke", {"id": tok["id"]})
st2, js2 = call("GET", "/api/mcp/tokens")
check("thu hồi → status revoked, token bị từ chối",
      r.get("ok") and {t["id"]: t for t in js2["tokens"]}[tok["id"]]["status"] == "revoked" and not db.verify_mcp_request_auth(H, {}, tool_name="read_workspace_file")[0])

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

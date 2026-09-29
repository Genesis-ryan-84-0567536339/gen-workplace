#!/usr/bin/env python3
"""
Test Issue #41 (siết bảo mật MCP). Chạy không cần agy/tmux thật:
- GET /api/mcp/tokens không lộ token thô: không có token_raw, không chuỗi token đầy đủ nào; chỉ bản che
  tiền tố + •••• + 4 ký tự cuối. Token đầy đủ chỉ có trong response lúc tạo. Cách lưu DB giữ nguyên (token cũ vẫn dùng được).
- POST /api/mcp/auth/toggle: bật không cần token; đang bật mà tắt thiếu token → 401, token sai → 401, token qua ?token= → 401,
  token thiếu quyền → 403, token toàn quyền / role admin → 200. Mọi lần gọi ghi mcp_auth_audit (GET /api/mcp/auth/audit).
- Đang bật: /mcp, /sse, /api/mcp không token → 401; có Bearer hợp lệ → 200. REST /api/* không đổi (200).
- Màn MCP (UI) vẫn chạy: /api/mcp/ui/rpc (cùng origin, header X-GW-UI: 1) gọi ping / tools/call không cần token;
  thiếu header hoặc Origin khác → 403; index.html không còn đọc token_raw.
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
TMP = tempfile.mkdtemp(prefix="gw-test-mcpauth-")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["GW_AGY_BIN"] = "/bin/false"
os.environ.pop("GW_PUBLIC_ORIGIN", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, main  # noqa: E402

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


def call(method, path, body=None, headers=None, raw=False):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
            return r.status, (data.decode("utf-8", "replace") if raw else json.loads(data or b"{}"))
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, (data.decode("utf-8", "replace") if raw else json.loads(data or b"{}"))
        except Exception:
            return e.code, {}


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
UI = {"X-GW-UI": "1"}

print("[1] bản che token")
check("gw_live_: tiền tố + •••• + 4 ký tự cuối", db.mask_mcp_token("gw_live_" + "a" * 36 + "wxyz") == "gw_live_••••wxyz")
check("token lạ không tiền tố: •••• + 4 cuối", db.mask_mcp_token("x" * 30 + "9876") == "••••9876")
check("token ngắn: chỉ ••••", db.mask_mcp_token("short-token") == "••••")

print("[2] danh sách token không lộ token thô")
with db.get_connection() as conn:
    master_raw = conn.execute("SELECT token FROM mcp_agent_tokens WHERE id = 'mcp-tok-master'").fetchone()["token"]
st, full = call("POST", "/api/mcp/tokens/create", {"name": "claude-orchestrator", "permissions": ["all"]})
FULL = full.get("token", "")
check("tạo token: response có token đầy đủ (hiện 1 lần) + bản che", st == 200 and FULL.startswith("gw_live_") and full.get("token_masked") == db.mask_mcp_token(FULL) and full.get("shown_once") is True, str(full)[:200])
st, files = call("POST", "/api/mcp/tokens/create", {"name": "files-only", "permissions": ["files"]})
FILES = files.get("token", "")
st, lst = call("GET", "/api/mcp/tokens")
dump = json.dumps(lst, ensure_ascii=False)
toks = lst.get("tokens", [])
check("200 và có token", st == 200 and len(toks) >= 3, str(st))
check("không còn trường token_raw", all("token_raw" not in t for t in toks), dump[:300])
check("không chuỗi token đầy đủ nào trong response", FULL not in dump and FILES not in dump and master_raw not in dump)
check("không lộ quá 4 ký tự cuối (vd 10 ký tự cuối)", FULL[-10:] not in dump and master_raw[-10:] not in dump)
by = {t["id"]: t for t in toks}
check("token_masked = tiền tố + •••• + 4 cuối", by[full["id"]]["token_masked"] == f"gw_live_••••{FULL[-4:]}", by[full["id"]]["token_masked"])
check("cách lưu DB giữ nguyên: token cũ (master) vẫn xác thực được", db.verify_mcp_request_auth(bearer(master_raw), {})[0])

print("[3] bật bắt buộc token (không cần token để bật)")
st, js = call("POST", "/api/mcp/auth/toggle", {})
check("thiếu require_auth → 400", st == 400, f"{st} {js}")
st, js = call("POST", "/api/mcp/auth/toggle", {"require_auth": True})
check("bật → 200, require_auth true", st == 200 and js.get("require_auth") is True, f"{st} {js}")

print("[4] đang bật: /mcp, /sse, /api/mcp không token → 401")
st, js = call("POST", "/mcp", PING)
check("POST /mcp không token → 401", st == 401, f"{st} {js}")
st, js = call("POST", "/api/mcp", PING)
check("POST /api/mcp không token → 401", st == 401, f"{st} {js}")
st, _ = call("GET", "/sse", raw=True)
check("GET /sse không token → 401", st == 401, str(st))
st, js = call("POST", "/mcp", PING, headers=bearer("gw_live_sai"))
check("POST /mcp token sai → 401", st == 401, str(st))
st, js = call("POST", "/mcp", PING, headers=bearer(FULL))
check("POST /mcp Bearer hợp lệ → 200", st == 200 and "result" in js, f"{st} {js}")
st, js = call("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_system_status", "arguments": {}}}, headers=bearer(master_raw))
check("token cũ (master) gọi tool qua /mcp → 200", st == 200 and not (js.get("result") or {}).get("isError"), f"{st} {str(js)[:200]}")
for p in ("/api/state", "/api/mcp/status", "/api/mcp/tokens"):
    st, _ = call("GET", p)
    check(f"REST {p} không token → 200", st == 200, str(st))
st, js = call("POST", "/api/gen/conversations/create", {"title": "VIEC-41: thử"})
cid = js.get("conv_id") or js.get("id") or (js.get("conversation") or {}).get("id")
st, js = call("POST", "/api/gen/conversations/log", {"conv_id": cid, "content": "log qua REST khi đang bật", "author": "test"})
check("REST ghi log phiên không cần token → 200", st == 200, f"{st} {js}")

print("[5] tắt khi đang bật: cần token admin / toàn quyền")
st, js = call("POST", "/api/mcp/auth/toggle", {"require_auth": False})
check("thiếu token → 401", st == 401 and js.get("require_auth") is True, f"{st} {js}")
st, js = call("POST", "/api/mcp/auth/toggle", {"require_auth": False}, headers=bearer("gw_live_khong_co"))
check("token sai → 401", st == 401, f"{st} {js}")
st, js = call("POST", f"/api/mcp/auth/toggle?token={FULL}", {"require_auth": False})
check("token qua ?token= (không phải Bearer) → 401", st == 401, f"{st} {js}")
st, js = call("POST", "/api/mcp/auth/toggle", {"require_auth": False}, headers=bearer(FILES))
check("token chỉ scope files → 403", st == 403, f"{st} {js}")
st, js = call("POST", "/api/mcp/tokens/revoke", {"id": full["id"]})
st, js = call("POST", "/api/mcp/auth/toggle", {"require_auth": False}, headers=bearer(FULL))
check("token toàn quyền đã thu hồi → 401", st == 401, f"{st} {js}")
check("vẫn đang bật sau các lần bị từ chối", db.get_mcp_auth_status()["require_auth"] is True)
st, js = call("POST", "/api/mcp/auth/toggle", {"require_auth": False}, headers=bearer(master_raw))
check("token admin (master) → 200, đã tắt", st == 200 and js.get("require_auth") is False, f"{st} {js}")
st, js = call("POST", "/mcp", PING)
check("đã tắt: /mcp không token → 200 như cũ", st == 200, str(st))

print("[6] audit")
st, js = call("GET", "/api/mcp/auth/audit?limit=20")
items = js.get("items", [])
denied = [i for i in items if i["action"] == "disable" and not i["allowed"]]
ok_dis = [i for i in items if i["action"] == "disable" and i["allowed"]]
check("200 + có dòng bật thành công", st == 200 and any(i["action"] == "enable" and i["allowed"] for i in items), str(items)[:300])
check("ghi đủ 5 lần tắt bị từ chối", len(denied) == 5, str(denied)[:300])
check("lần tắt thành công ghi token admin", ok_dis and ok_dis[0]["token_id"] == "mcp-tok-master", str(ok_dis)[:200])
check("403 ghi tên token thiếu quyền", any(i["token_name"] == "files-only" for i in denied))
check("audit không chứa token thô", FULL not in json.dumps(items) and master_raw not in json.dumps(items))

print("[7] UI vẫn chạy khi đang bắt buộc token")
call("POST", "/api/mcp/auth/toggle", {"require_auth": True})
st, js = call("POST", "/api/mcp/ui/rpc", PING, headers=UI)
check("UI ping không token → 200", st == 200 and "result" in js, f"{st} {js}")
st, js = call("POST", "/api/mcp/ui/rpc", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_system_status", "arguments": {}}},
              headers={**UI, "Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}"})
check("UI tools/call cùng origin → 200", st == 200 and not (js.get("result") or {}).get("isError"), f"{st} {str(js)[:200]}")
st, js = call("POST", "/api/mcp/ui/rpc", PING)
check("thiếu X-GW-UI → 403", st == 403, f"{st} {js}")
st, js = call("POST", "/api/mcp/ui/rpc", PING, headers={**UI, "Origin": "http://evil.example"})
check("Origin khác → 403", st == 403, f"{st} {js}")
st, js = call("POST", "/api/mcp/ui/rpc", {"jsonrpc": "2.0", "id": 4, "method": "resources/read"}, headers=UI)
check("method ngoài danh sách → 400", st == 400, f"{st} {js}")
st, html = call("GET", "/", raw=True)
check("trang UI 200", st == 200 and "mcpUiRpc" in html, str(st))
check("UI không còn đọc token_raw, gọi /api/mcp/ui/rpc", "token_raw" not in html and "/api/mcp/ui/rpc" in html and "Token chỉ hiện 1 lần" in html)
call("POST", "/api/mcp/auth/toggle", {"require_auth": False}, headers=bearer(master_raw))

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

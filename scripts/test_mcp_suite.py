#!/usr/bin/env python3
"""
GENESIS Multi-Agent Swarm Workplace - Comprehensive MCP Test Suite
Kiểm thử toàn diện:
1. Cơ chế xác thực Token Bearer & URL Query chuẩn Gen-hub
2. Thử nghiệm toàn bộ 27 MCP Tools qua cả HTTP POST và Stdio CLI
3. Kiểm thử Resources & Prompt Templates

CÁCH CHẠY ĐÚNG (DỰNG APP THỬ Ở CỔNG KHÁC VỚI DATA_DIR TẠM):
1. Dựng app thử ở cổng khác (ví dụ 8889) với thư mục dữ liệu tạm:
   DATA_DIR=/tmp/gw_test PORT=8889 python3 backend/main.py
2. Chạy test suite trỏ vào instance thử nghiệm qua biến môi trường GW_MCP_SUITE_URL:
   GW_MCP_SUITE_URL=http://localhost:8889 python3 scripts/test_mcp_suite.py
3. Nếu không đặt GW_MCP_SUITE_URL, hoặc trỏ cổng 8888 mà không có GW_MCP_SUITE_ALLOW_LIVE=1,
   script sẽ tự động in lý do và bỏ qua (exit 0) để bảo vệ dữ liệu app thật của Boss.
"""

import sys
import json
import time
import http.client
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import os
from pathlib import Path

BASE_URL = os.environ.get("GW_MCP_SUITE_URL", "").strip().rstrip("/")
ALLOW_LIVE = os.environ.get("GW_MCP_SUITE_ALLOW_LIVE", "").strip() == "1"
STDIO_BIN = os.path.expanduser("~/.local/bin/gen-workplace-mcp")

def check_guard():
    """Kiểm tra điều kiện chạy test suite để tránh ảnh hưởng app thật."""
    if not BASE_URL:
        print("[BỎ QUA] scripts/test_mcp_suite.py: Chưa đặt biến môi trường GW_MCP_SUITE_URL. Bỏ qua, không gọi mạng.")
        print("Cách chạy đúng: Dựng app thử ở cổng khác với DATA_DIR tạm:")
        print("  DATA_DIR=/tmp/gw_test PORT=8889 python3 backend/main.py")
        print("  GW_MCP_SUITE_URL=http://localhost:8889 python3 scripts/test_mcp_suite.py")
        sys.exit(0)

    raw_url = BASE_URL if "://" in BASE_URL else f"http://{BASE_URL}"
    parsed = urllib.parse.urlparse(raw_url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    if port == 8888 and not ALLOW_LIVE:
        print(f"[BỎ QUA] scripts/test_mcp_suite.py: GW_MCP_SUITE_URL trỏ tới cổng 8888 ({BASE_URL}) nhưng không có GW_MCP_SUITE_ALLOW_LIVE=1. Bỏ qua để bảo vệ app thật của Boss.")
        print("Nếu thực sự muốn chạy vào cổng 8888, hãy đặt GW_MCP_SUITE_ALLOW_LIVE=1.")
        sys.exit(0)

def http_req(path, data=None, headers=None, method=None, timeout=15):
    raw_base = BASE_URL if "://" in BASE_URL else f"http://{BASE_URL}"
    url = f"{raw_base}{path}"
    headers = headers or {}
    if data is not None and isinstance(data, (dict, list)):
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif data is not None and isinstance(data, str):
        payload = data.encode("utf-8")
    else:
        payload = None

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body
            return status, parsed
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return e.code, parsed
    except Exception as e:
        return 0, str(e)

def test_sse_stream(token):
    """Kiểm tra SSE stream chuẩn: Kết nối qua HTTPConnection, đọc dòng event & endpoint rồi đóng socket"""
    try:
        raw_url = BASE_URL if "://" in BASE_URL else f"http://{BASE_URL}"
        parsed = urllib.parse.urlparse(raw_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        conn = http.client.HTTPConnection(host, port, timeout=4)
        conn.request("GET", f"/sse?token={token}")
        resp = conn.getresponse()
        status = resp.status
        line1 = resp.readline().decode("utf-8", errors="replace")
        line2 = resp.readline().decode("utf-8", errors="replace")
        conn.close()
        return status, line1 + line2
    except Exception as e:
        return 0, str(e)

def run_tests():
    check_guard()
    print("=" * 70)
    print("🚀 GENESIS MCP SERVER: COMPREHENSIVE AUTH & 27 TOOLS TEST SUITE")
    print("=" * 70)

    results = []

    def record(name, passed, detail=""):
        results.append((name, passed, detail))
        status_icon = "✅ PASS" if passed else "❌ FAIL"
        print(f"[{status_icon}] {name}: {detail}")

    # -------------------------------------------------------------
    # PHASE 1: TOKEN CREATION & AUTHENTICATION TESTS (GEN-HUB STANDARD)
    # -------------------------------------------------------------
    print("\n--- PHASE 1: TOKEN CREATION & AUTHENTICATION TESTS ---")
    
    # 1.1 Tạo token cho Test Agent
    st, data = http_req("/api/mcp/tokens/create", {
        "name": "MCP Automated Test Agent",
        "permissions": ["all"],
        "expires_days": 30,
        "client": "Test Suite Runner"
    })
    test_token = None
    test_token_id = None
    if st == 200 and data.get("ok"):
        test_token = data.get("token")
        test_token_id = data.get("id")
        record("Tạo mới Agent Token", True, f"ID: {test_token_id}, Token: {data.get('token_masked')}")
    else:
        record("Tạo mới Agent Token", False, str(data))
        return False

    # 1.2 Bật Strict Auth Mode
    st, data = http_req("/api/mcp/auth/toggle", {"require_auth": True})
    record("Bật chế độ Strict Auth", st == 200 and data.get("require_auth") is True, f"require_auth: {data.get('require_auth')}")

    # 1.3 Kiểm tra truy cập KHÔNG CÓ TOKEN (phải bị từ chối 401)
    st, data = http_req("/mcp", {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {}
    })
    expected_msg = "Agent chưa xác thực hoặc quyền đã bị thu hồi"
    has_expected_err = (st == 401 and expected_msg in str(data))
    record("Từ chối 401 khi không có Token (Strict Auth)", has_expected_err, f"Status: {st}, Error: {data.get('error', {}).get('message', str(data))}")

    # 1.4 Kiểm tra truy cập với TOKEN GIẢ (phải bị từ chối 401)
    st, data = http_req("/mcp?token=gw_live_fake_invalid_token_9999", {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    })
    has_fake_err = (st == 401 and expected_msg in str(data))
    record("Từ chối 401 khi gửi Token không hợp lệ", has_fake_err, f"Status: {st}")

    # 1.5 Kiểm tra xác thực qua Header `Authorization: Bearer <token>`
    st, data = http_req("/mcp", {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "initialize",
        "params": {}
    }, headers={"Authorization": f"Bearer {test_token}"})
    bearer_ok = (st == 200 and "result" in data and data["result"].get("serverInfo", {}).get("name") == "gen-workplace")
    record("Xác thực thành công qua Header Bearer Token", bearer_ok, f"Status: {st}, Server: {data.get('result', {}).get('serverInfo', {}).get('name')}")

    # 1.6 Kiểm tra xác thực qua URL Query Param `?token=<token>`
    st, data = http_req(f"/mcp?token={test_token}", {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "initialize",
        "params": {}
    })
    query_ok = (st == 200 and "result" in data and data["result"].get("serverInfo", {}).get("name") == "gen-workplace")
    record("Xác thực thành công qua URL Query `?token=...`", query_ok, f"Status: {st}")

    # 1.7 Kiểm tra kết nối SSE Stream
    st_sse, data_sse = test_sse_stream(test_token)
    sse_ok = (st_sse == 200 and "event: endpoint" in str(data_sse))
    record("Kết nối SSE Stream với Token", sse_ok, f"Status: {st_sse}, Event Data: {str(data_sse)[:60]}...")

    # 1.8 Kiểm tra thu hồi token (Revocation test)
    st_temp, d_temp = http_req("/api/mcp/tokens/create", {"name": "Temp Revoke Agent", "permissions": ["all"], "expires_days": 1})
    temp_token = d_temp.get("token")
    temp_id = d_temp.get("id")
    # Thu hồi token
    http_req("/api/mcp/tokens/revoke", {"id": temp_id})
    # Thử gọi lại bằng token vừa thu hồi
    st_rev, d_rev = http_req(f"/mcp?token={temp_token}", {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}})
    record("Từ chối 401 khi Token đã bị thu hồi", st_rev == 401, f"Status: {st_rev}")

    # Trả Strict Auth về False để môi trường dev / localhost CLI không bị cản trở
    http_req("/api/mcp/auth/toggle", {"require_auth": False})

    # -------------------------------------------------------------
    # PHASE 2: COMPREHENSIVE TOOLS TESTING (ALL 27 TOOLS)
    # -------------------------------------------------------------
    print("\n--- PHASE 2: COMPREHENSIVE TESTS OF ALL 27 MCP TOOLS ---")

    auth_headers = {"Authorization": f"Bearer {test_token}"}

    # Danh sách 27 công cụ cần kiểm thử
    tools_list = [
        # 1. Quota & Accounts
        ("get_live_quota", {"profile_id": "owner_default", "force_refresh": False}, 15),
        ("list_google_accounts", {}, 15),
        ("switch_google_account", {"session_id": "gw-lead-agy", "account_id": "owner_default", "account_label": "Owner Default"}, 15),
        ("get_oauth_login_url", {"profile_id": "test_profile"}, 15),

        # 2. Swarm Workers & War Room
        ("list_swarm_workers", {"project_id": "PRJ-GEN-WORKPLACE"}, 15),
        ("send_worker_directive", {"session_id": "gw-lead-agy", "command": "echo 'MCP test directive ping'"}, 15),
        ("manage_worker_lifecycle", {"action": "wake", "session_id": "gw-lead-agy"}, 15),
        ("get_worker_terminal_output", {"session_id": "gw-lead-agy", "lines": 20}, 15),
        ("post_warroom_message", {"message": "Test directive từ MCP automated suite", "author": "QA Subagent", "tag": "Test"}, 15),
        ("get_warroom_messages", {"channel_id": "war_room", "limit": 10}, 15),

        # 3. Kanban & Task Governance
        ("list_kanban_tasks", {"conv_id": "conv-gen-core-01"}, 15),
        ("create_kanban_task", {
            "conv_id": "conv-gen-core-01",
            "title": "Nhiệm vụ kiểm thử tự động MCP",
            "description": "Thực hiện kiểm thử 27 tools qua giao thức JSON-RPC",
            "priority": "high",
            "checklist": ["Bước 1: Gửi test", "Bước 2: Đối soát output"]
        }, 15),
        ("claim_task", {"session_id": "gw-lead-agy", "task_id": "DYNAMIC", "project_id": "PRJ-GEN-WORKPLACE"}, 15),
        ("update_task_checklist", {"conv_id": "conv-gen-core-01", "task_id": "DYNAMIC", "item_id": "Bước 1: Gửi test", "done": True}, 15),
        ("complete_task", {"session_id": "gw-lead-agy", "task_id": "DYNAMIC", "evidence_ref": "PASS 27/27 TOOLS", "project_id": "PRJ-GEN-WORKPLACE"}, 15),

        # 4. Chat & Sessions
        ("gen_chat", {"message": "Xin chào Gen Workplace từ MCP Test Agent", "conv_id": "conv-gen-core-01", "author": "MCP Test"}, 60),
        ("list_conversations", {"project_id": "PRJ-GEN-WORKPLACE"}, 15),
        ("create_conversation", {"title": "Phiên kiểm thử tự động MCP"}, 15),
        ("get_conversation_messages", {"conv_id": "conv-gen-core-01"}, 15),
        ("compact_conversation", {"conv_id": "conv-gen-core-01", "manual": True}, 15),

        # 5. Notes, Files & System Status
        ("list_notes", {"conv_id": "conv-gen-core-01"}, 15),
        ("save_note", {"title": "Ghi chú Test MCP", "content": "Nội dung ghi chú sinh từ MCP test suite", "tags": ["MCP", "QA"], "conv_id": "conv-gen-core-01"}, 15),
        ("delete_note", {"note_id": "non_existent_test_note"}, 15),
        ("read_workspace_file", {"file_path": "backend/main.py"}, 15),
        ("create_workspace_file", {"conv_id": "conv-gen-core-01", "path": "mcp_test_artifact.txt", "content": "Created via MCP Tool create_workspace_file"}, 15),
        ("list_workspace_files", {}, 15),
        ("get_system_status", {}, 15)
    ]

    dynamic_task_id = "TSK-01"

    for idx, (tool_name, args, req_timeout) in enumerate(tools_list, 1):
        # Cập nhật task_id động từ kết quả của create_kanban_task nếu có
        curr_args = dict(args)
        if curr_args.get("task_id") == "DYNAMIC":
            curr_args["task_id"] = dynamic_task_id

        t0 = time.time()
        call_payload = {
            "jsonrpc": "2.0",
            "id": 100 + idx,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": curr_args
            }
        }
        st, resp = http_req("/mcp", call_payload, headers=auth_headers, timeout=req_timeout)
        duration_ms = int((time.time() - t0) * 1000)

        is_ok = False
        summary = ""
        if st == 200 and isinstance(resp, dict) and "result" in resp:
            call_res = resp["result"]
            if not call_res.get("isError"):
                is_ok = True
                content = call_res.get("content", [])
                text_len = len(content[0].get("text", "")) if content else 0
                summary = f"{duration_ms}ms · Output length: {text_len} bytes"
                
                # Trích xuất task_id nếu là create_kanban_task
                if tool_name == "create_kanban_task":
                    try:
                        out_json = json.loads(content[0].get("text", "{}"))
                        if out_json.get("id"):
                            dynamic_task_id = out_json.get("id")
                            summary += f" (Generated task_id: {dynamic_task_id})"
                    except Exception:
                        pass
            else:
                summary = f"Tool Error: {call_res.get('content', [{}])[0].get('text', '')[:100]}"
        else:
            summary = f"HTTP {st}: {str(resp)[:100]}"

        record(f"Tool {idx:02d}/27 [{tool_name}]", is_ok, summary)

    # -------------------------------------------------------------
    # PHASE 3: RESOURCES & PROMPTS TESTS
    # -------------------------------------------------------------
    print("\n--- PHASE 3: RESOURCES & PROMPTS TESTING ---")

    # 3.1 resources/list
    st, resp = http_req("/mcp", {"jsonrpc": "2.0", "id": 201, "method": "resources/list", "params": {}}, headers=auth_headers)
    has_res_list = (st == 200 and len(resp.get("result", {}).get("resources", [])) >= 4)
    record("Method resources/list", has_res_list, f"Count: {len(resp.get('result', {}).get('resources', []))} resources")

    # 3.2 resources/read
    st, resp = http_req("/mcp", {"jsonrpc": "2.0", "id": 202, "method": "resources/read", "params": {"uri": "gen-workplace://system/status"}}, headers=auth_headers)
    has_res_read = (st == 200 and len(resp.get("result", {}).get("contents", [])) > 0)
    record("Method resources/read (gen-workplace://system/status)", has_res_read, "Read successful")

    # 3.3 prompts/list
    st, resp = http_req("/mcp", {"jsonrpc": "2.0", "id": 203, "method": "prompts/list", "params": {}}, headers=auth_headers)
    has_prompt_list = (st == 200 and len(resp.get("result", {}).get("prompts", [])) >= 2)
    record("Method prompts/list", has_prompt_list, f"Count: {len(resp.get('result', {}).get('prompts', []))} prompts")

    # 3.4 prompts/get
    st, resp = http_req("/mcp", {"jsonrpc": "2.0", "id": 204, "method": "prompts/get", "params": {"name": "anti_chaos_task_execution"}}, headers=auth_headers)
    has_prompt_get = (st == 200 and len(resp.get("result", {}).get("messages", [])) > 0)
    record("Method prompts/get (anti_chaos_task_execution)", has_prompt_get, "Retrieved SOP prompt message")

    # -------------------------------------------------------------
    # PHASE 4: STDIO CLI BRIDGE TEST
    # -------------------------------------------------------------
    print("\n--- PHASE 4: STDIO CLI BRIDGE TEST (~/.local/bin/gen-workplace-mcp) ---")
    stdio_ok = False
    try:
        stdio_req = json.dumps({"jsonrpc": "2.0", "id": 301, "method": "tools/call", "params": {"name": "get_live_quota", "arguments": {"force_refresh": False}}})
        proc = subprocess.Popen([STDIO_BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(input=f"{stdio_req}\n", timeout=10)
        parsed_out = json.loads(stdout.strip())
        stdio_ok = ("result" in parsed_out and not parsed_out["result"].get("isError"))
        record("Stdio CLI Bridge execution (~/.local/bin/gen-workplace-mcp)", stdio_ok, "Synchronous JSON-RPC pipe OK")
    except Exception as e:
        record("Stdio CLI Bridge execution", False, str(e))

    # -------------------------------------------------------------
    # SUMMARY REPORT
    # -------------------------------------------------------------
    passed_count = sum(1 for _, p, _ in results if p)
    total_count = len(results)
    pass_rate = (passed_count / total_count) * 100

    print("\n" + "=" * 70)
    print(f"📊 MCP TEST SUITE SUMMARY: {passed_count}/{total_count} PASSED ({pass_rate:.1f}%)")
    print("=" * 70)

    # Dọn dẹp token test
    if test_token_id:
        http_req("/api/mcp/tokens/revoke", {"id": test_token_id})

    return passed_count == total_count

if __name__ == "__main__":
    check_guard()
    success = run_tests()
    sys.exit(0 if success else 1)

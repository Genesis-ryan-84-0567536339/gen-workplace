#!/usr/bin/env python3
"""
GENESIS Multi-Agent Swarm Orchestrator - Backend Control Plane
Phục vụ API quản trị dự án, điều phối Agent CLI runtimes, đồng bộ SSOT Memory,
tra cứu Catalog siêu tốc qua SQLite FTS5 và phục vụ WebApp giao diện người dùng.
"""

import os
import sys
import json
import time
import subprocess
import urllib.parse
import re
import traceback
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import threading

PORT = int(os.environ.get("PORT", 8888))
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
default_data = "/app/data" if (os.path.exists("/app") or os.environ.get("DOCKER_CONTAINER")) else str(BASE_DIR / "data")
DATA_DIR = Path(os.environ.get("DATA_DIR", default_data))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Tự động nạp SQLite DB Module & MCP Core
sys.path.insert(0, str(BASE_DIR))
try:
    from backend import db, mcp_core, directive_guard, auto_update
except ImportError:
    import db
    import mcp_core
    import directive_guard
    import auto_update

RUNNING_COMMIT = auto_update.current_commit(BASE_DIR)  # commit của code đang chạy (đổi sau khi tự cập nhật execv)

def render_oauth_callback_html(status_code, title, desc, profile_id, email=None):
    is_success = (status_code == 200)
    color = "#3ad18b" if is_success else "#f85149"
    icon = "🎉" if is_success else "⚠️"
    email_html = f'<div style="font-family:monospace;font-size:15px;background:#0d1117;padding:10px 14px;border-radius:6px;border:1px solid #30363d;margin:16px 0;color:#58a6ff;">{email}</div>' if email else ""

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <title>{title} - Genesis Workplace</title>
  <style>
    body {{ background: #0b0f19; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 32px; max-width: 480px; text-align: center; box-shadow: 0 12px 32px rgba(0,0,0,0.6); }}
    .icon {{ font-size: 48px; margin-bottom: 12px; }}
    h2 {{ color: {color}; margin-top: 0; font-size: 22px; }}
    p {{ color: #8b949e; font-size: 14px; line-height: 1.6; margin: 12px 0; }}
    .btn {{ display: inline-block; background: #238636; color: #fff; padding: 10px 24px; border-radius: 6px; text-decoration: none; font-weight: 600; margin-top: 18px; border: none; cursor: pointer; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h2>{title}</h2>
    <p>{desc}</p>
    {email_html}
    <p style="font-size:12px;color:#6e7681">Mission Control OS đã tự động lưu trữ và đồng bộ token vào hồ sơ {profile_id}. Bạn có thể đóng tab này.</p>
    <button class="btn" onclick="window.close()">Đóng Cửa Sổ Này</button>
  </div>
  <script>
    if ({str(is_success).lower()}) {{
      setTimeout(() => {{ try {{ window.close(); }} catch(e) {{}} }}, 3500);
    }}
  </script>
</body>
</html>"""

def mcp_stdio_info():
    """Cách chạy MCP qua stdio trên máy chủ này (cho agy / Claude Code cùng máy): lệnh bọc ~/.local/bin/gen-workplace-mcp
    nếu có, luôn kèm lệnh python3 trỏ thẳng backend/mcp_server.py và DATA_DIR để dùng chung DB với app."""
    wrapper = os.path.join(os.environ.get("HOME") or str(Path.home()), ".local", "bin", "gen-workplace-mcp")
    return {
        "wrapper": wrapper,
        "wrapper_exists": os.path.isfile(wrapper) and os.access(wrapper, os.X_OK),
        "python": "python3",
        "script": str(BASE_DIR / "backend" / "mcp_server.py"),
        "data_dir": str(db.DATA_DIR),
    }

OAUTH_STATE_RE = re.compile(r"^(owner_default|profile[0-9]{1,2})$")

def handle_oauth_callback(handler, query):
    """Xử lý chung callback Google OAuth (cổng 8085 và route dự phòng /oauth2callback): kiểm state, đổi code lấy token, trả HTML."""
    code = query.get("code", [None])[0]
    error = query.get("error", [None])[0]
    state = query.get("state", [""])[0] or ""

    if not OAUTH_STATE_RE.match(state):
        html = render_oauth_callback_html(400, "Tham số state không hợp lệ", "Hồ sơ đích (state) phải là owner_default hoặc profileN.", "?")
        status = 400
    elif error:
        html = render_oauth_callback_html(400, "Xác thực bị từ chối", f"Google thông báo lỗi: {error}", state)
        status = 400
    elif not code:
        html = render_oauth_callback_html(400, "Thiếu Authorization Code", "Không nhận được mã ủy quyền từ Google OAuth.", state)
        status = 400
    else:
        ok, msg, email = db.exchange_google_code_for_token(code, state)
        if ok:
            html = render_oauth_callback_html(200, "Xác thực Google thành công!", f"Hồ sơ <b>{state}</b> đã được kết nối với tài khoản:", state, email=email)
            status = 200
        else:
            html = render_oauth_callback_html(500, "Lỗi trao đổi token Google", f"Không thể lưu token: {msg}", state)
            status = 500
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(html.encode("utf-8"))

class OAuthCallbackHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/oauth2callback":
            query = urllib.parse.parse_qs(parsed.query)
            handle_oauth_callback(self, query)
            return

        self.send_response(404)
        self.end_headers()

class SwarmHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def _send_json(self, status_code, payload):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _guarded(self, handler):
        """Chạy handler; exception chưa bắt → log traceback ra stderr và trả 500 JSON {"error": "<tên lỗi>"} thay vì ngắt kết nối."""
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client đã đóng kết nối, không còn gì để trả
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            try:
                self._send_json(500, {"error": type(e).__name__, "detail": str(e)})
            except Exception:
                pass

    def _request_origin(self):
        """scheme://host[:port] mà trình duyệt / agent đang gọi (Host header, tôn trọng X-Forwarded-*); rỗng nếu thiếu Host."""
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip()
        if not host or not re.match(r"^[A-Za-z0-9.\-\[\]:]{1,255}$", host):
            return None
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip().lower()
        if proto not in ("http", "https"):
            proto = "http"
        return f"{proto}://{host}"

    def _handle_dispatch_wait(self, params):
        """Chờ lần giao việc xong (tối đa 120s). 200 = done/failed/running; 400 = thiếu tham số; 404 = không tìm thấy."""
        res = db.wait_worker_result(params.get("dispatch_id"), params.get("task_id", ""), params.get("session_id", ""),
                                    params.get("timeout_sec", db.WAIT_WORKER_DEFAULT_SEC), params.get("project_id", "PRJ-GEN-WORKPLACE"))
        code = {"error": 400, "not_found": 404}.get(res.get("status"), 200)
        self._send_json(code, res)

    def do_GET(self):
        self._guarded(self._handle_get)

    def do_POST(self):
        self._guarded(self._handle_post)

    def _handle_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 0. API Model Context Protocol (MCP) SSE, Token Management & Inspector
        if path in ("/mcp", "/sse"):
            is_auth, agent_info, err_msg = db.verify_mcp_request_auth(self.headers, query)
            if not is_auth:
                self._send_json(401, {"error": err_msg or "Agent chưa xác thực hoặc quyền đã bị thu hồi"})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                token_param = query.get("token", [""])[0] or query.get("key", [""])[0]
                ep_data = f"/mcp?token={token_param}" if token_param else "/mcp"
                self.wfile.write(f"event: endpoint\r\ndata: {ep_data}\r\n\r\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
            return

        if path in ("/mcp/tools", "/api/mcp/tools", "/api/mcp/status"):
            auth_st = db.get_mcp_auth_status(self._request_origin())
            self._send_json(200, {
                "status": "online",
                "serverInfo": mcp_core.MCP_SERVER_INFO,
                "protocolVersion": mcp_core.MCP_PROTOCOL_VERSION,
                "instructions": mcp_core.MCP_INSTRUCTIONS,
                "tools_count": len(mcp_core.TOOLS),
                "read_only_tools": sorted(mcp_core.READ_ONLY_TOOLS),
                "auth": auth_st,
                # tool kèm group / scope / read_only / summary từ mcp_core.TOOL_META (#28)
                "tools": mcp_core.tool_catalog(),
                "groups": mcp_core.TOOL_GROUPS,
                "scopes": mcp_core.TOKEN_SCOPES,
                "stdio": mcp_stdio_info(),
                "resources": mcp_core.RESOURCES,
                "prompts": mcp_core.PROMPTS
            })
            return

        if path == "/api/mcp/tokens":
            tokens_data = db.get_mcp_agent_tokens(origin=self._request_origin())
            self._send_json(200, tokens_data)
            return

        if path == "/api/mcp/auth/status":
            self._send_json(200, db.get_mcp_auth_status(self._request_origin()))
            return

        # 0.8. Nhật ký dispatch (agy thật chạy từ chatroom): GET /api/dispatch/log?limit=50&task_id=TSK-1&session_id=gw-qa-agy
        #      → {"items": [...], "count": n}; task_id / session_id lọc đúng giá trị (bỏ trống = không lọc)
        if path == "/api/dispatch/log":
            limit = query.get("limit", ["50"])[0]
            try:
                items = db.get_dispatch_log(limit, query.get("task_id", [""])[0], query.get("session_id", [""])[0])
                self._send_json(200, {"items": items, "count": len(items), "webhook_enabled": bool(os.environ.get("GW_EVENT_WEBHOOK_URL"))})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # 0.81. Task thật của dự án (bảng gen_session_todos) kèm lần giao gần nhất — màn Việc & tiến độ poll endpoint này (#24)
        if path == "/api/tasks":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            tasks = db.get_all_session_todos(prj_id)
            self._send_json(200, {"tasks": tasks, "count": len(tasks)})
            return

        # 0.82. Nhật ký ghi đè bằng chứng (complete_task force=true, #16): GET /api/task/evidence-audit?task_id=&limit=50
        if path == "/api/task/evidence-audit":
            try:
                limit = max(1, min(500, int(query.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            try:
                items = db.get_task_evidence_audit(query.get("task_id", [""])[0].strip(), limit)
                self._send_json(200, {"items": items, "count": len(items)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # 0.85. Chờ kết quả worker (#9): GET /api/dispatch/wait?dispatch_id=12&timeout_sec=60 (hoặc task_id= / session_id=)
        if path == "/api/dispatch/wait":
            self._handle_dispatch_wait({k: v[0] for k, v in query.items() if v})
            return

        # 0.9. Nhật ký allowlist lệnh gửi vào tmux (audit)
        if path == "/api/directive/audit":
            limit = query.get("limit", ["100"])[0]
            only_rejected = query.get("rejected", ["0"])[0] in ("1", "true")
            try:
                self._send_json(200, {"items": db.get_directive_audit(limit, only_rejected), "allow_all": directive_guard.allow_all_enabled()})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # OAuth Callback (Dự phòng cho cổng 8888 nếu redirect trỏ về cổng chính)
        if path == "/oauth2callback":
            handle_oauth_callback(self, query)
            return

        # 1. API Status
        if path == "/api/status":
            state = db.get_full_state() or {}
            status = {
                "status": "online",
                "version": "1.2-sqlite-wal",
                "timestamp": int(time.time()),
                "docker_env": bool(os.environ.get("DOCKER_CONTAINER")),
                "active_agents": len(state.get("roles", [])),
                "runtimes_count": len(state.get("runtimes", [])),
                "ssot_synced": True,
                "db_engine": "SQLite 3 WAL + FTS5",
                "active_project": state.get("project", {}).get("name", "gen-workplace"),
                "commit": RUNNING_COMMIT,
                "auto_update": auto_update.enabled()
            }
            self._send_json(200, status)
            return

        if path == "/api/auto-update":
            self._send_json(200, auto_update.status(BASE_DIR, DATA_DIR))
            return

        # 2. API Full State (Từ SQLite Core DB)
        if path == "/api/state":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            state = db.get_full_state(prj_id)
            if state:
                self._send_json(200, state)
            else:
                self._send_json(404, {"error": "Project not found"})
            return

        # 3. API Catalog Tra Cứu Nhanh (FTS5 Full-Text Search)
        if path == "/api/catalog":
            q = query.get("q", [""])[0]
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            results = db.search_catalog_fts(q, prj_id)
            self._send_json(200, {
                "query": q,
                "count": len(results),
                "results": results
            })
            return

        # 4. API Tmux Sessions (Phiên Nền Runtimes & Account Profiles)
        if path == "/api/tmux/sessions":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            # Chỉ đọc DB + 1 lần `tmux list-sessions`; capture-pane riêng phiên đang xem (?output=<sid>) (#30)
            output_sid = query.get("output", [""])[0].strip()
            sessions = db.get_tmux_sessions(prj_id, output_sid=output_sid)
            self._send_json(200, {"sessions": sessions})
            return

        # 5. API Kiểm tra Quota Live từ Google Cloud Code API của agy CLI
        if path in ("/api/quota/live", "/api/quota/check"):
            profile_id = query.get("profile", ["owner_default"])[0] or "owner_default"
            force = query.get("force", ["1"])[0] not in ("0", "false", "False")
            # gemini/claude luôn có exhausted + reset_at từ profile_quota_state (kể cả khi số lấy từ Cloud Code API)
            self._send_json(200, db.live_quota_response(profile_id, force=force))
            return

        # 6. API Danh sách OAuth Profiles & Trạng thái Google Login
        if path == "/api/oauth/profiles":
            profiles = db.get_oauth_profiles()
            self._send_json(200, {"profiles": profiles})
            return

        # 6. API Lịch sử tin nhắn Orchestrator Chat IDE
        if path == "/api/orch/messages":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            messages = db.get_orch_chat_messages(prj_id)
            self._send_json(200, {"messages": messages})
            return

        # 7. API Tin nhắn War Room / Phòng Giao Ban Swarm
        if path == "/api/warroom/messages":
            channel_id = query.get("channel", ["war_room"])[0]
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            limit = int(query.get("limit", [50])[0])
            messages = db.get_warroom_messages(channel_id, prj_id, limit)
            self._send_json(200, {"messages": messages, "channel_id": channel_id})
            return

        # 8. API Đọc toàn văn đặc tả SSOT nguyên bản của Ryan
        if path == "/api/ssot/spec":
            spec_file = BASE_DIR / "docs" / "SSOT_ORIGINAL_SPEC.md"
            if spec_file.exists():
                try:
                    content = spec_file.read_text(encoding="utf-8")
                    self._send_json(200, {
                        "filename": "docs/SSOT_ORIGINAL_SPEC.md",
                        "size_bytes": len(content.encode("utf-8")),
                        "content": content,
                        "status": "ok"
                    })
                    return
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                    return
            self._send_json(404, {"error": "SSOT spec file not found"})
            return

        # 9. API Git Commit History thật
        if path == "/api/git/log":
            limit = int(query.get("limit", [15])[0])
            commits = db.get_git_log(limit)
            self._send_json(200, {"commits": commits, "count": len(commits)})
            return

        # 10. API Git Status & Branch thật
        if path == "/api/git/status":
            stat = db.get_git_status()
            self._send_json(200, stat)
            return

        # 11. API SQLite Tables Explorer thật
        if path == "/api/db/tables":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            tables = db.get_db_tables(prj_id)
            self._send_json(200, {"tables": tables, "count": len(tables)})
            return

        # 12. API File Manager & Artifacts thật
        if path == "/api/files/tree":
            files = db.get_workspace_files()
            self._send_json(200, {"files": files, "count": len(files)})
            return

        # 13. API Hồ Sơ Chức Trách SOP thật từ ROLE.md
        if path == "/api/roles/sop":
            sop = db.get_roles_sop()
            self._send_json(200, {"roles": sop})
            return

        # 14. API Vault & Secrets Explorer
        if path == "/api/vault/list":
            vault = db.get_vault_list()
            self._send_json(200, {"vault": vault})
            return

        # 15. API SSOT Events thẩm định thật từ SQLite
        if path == "/api/events":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            events = db.get_ssot_events(prj_id)
            self._send_json(200, {"events": events, "count": len(events)})
            return

        # 16. API Skills hệ thống thật
        if path == "/api/skills":
            skills = db.get_system_skills()
            self._send_json(200, {"skills": skills, "count": len(skills)})
            return

        # 17. API MCP Tools hệ thống thật
        if path == "/api/mcps":
            mcps = db.get_system_mcps()
            self._send_json(200, {"mcps": mcps, "count": len(mcps)})
            return

        # 18. API Mạch tư duy đo lường thực tế
        if path == "/api/thinking/trace":
            sid = query.get("session", ["gw-lead-agy"])[0]
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            trace_data = db.get_role_thinking_trace(sid, prj_id)
            self._send_json(200, trace_data)
            return

        # 17.5. API Hồ Sơ Owner Ryan (Sở hữu độc quyền dữ liệu)
        if path == "/api/owner/profile":
            owner_id = query.get("owner_id", ["owner-ryan"])[0]
            profile = db.get_owner_profile(owner_id)
            self._send_json(200, profile)
            return

        # 18. API Gen Workplace Conversations
        if path == "/api/gen/conversations":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            convs = db.get_gen_conversations(prj_id)
            self._send_json(200, {"conversations": convs})
            return

        # 19. API Gen Workplace Messages
        if path == "/api/gen/messages":
            conv_id = query.get("conv_id", [""])[0] or db.default_conv_id()
            msgs = db.get_gen_messages(conv_id)
            self._send_json(200, {"messages": msgs})
            return

        # 20. API Gen Scratchpad Notes
        if path == "/api/gen/notes":
            prj_id = query.get("project", ["PRJ-GEN-WORKPLACE"])[0]
            conv_id = query.get("conv_id", [None])[0]
            notes = db.get_gen_notes(prj_id, conv_id)
            self._send_json(200, {"notes": notes})
            return

        # 21. API File Safe Reader cho VS Code Viewer
        if path == "/api/file/content":
            fpath = query.get("path", [""])[0]
            res = db.get_file_content_safely(fpath)
            self._send_json(200 if "error" not in res else 400, res)
            return

        # 22. API Gen Session Workspace Files (Quản lý File & Folder theo từng phiên)
        if path == "/api/gen/session/files":
            conv_id = query.get("conv_id", [""])[0] or db.default_conv_id()
            sess_files = db.get_gen_session_files(conv_id)
            self._send_json(200, sess_files)
            return

        # 23. API Gen Session Todos & Interactive Checklists (Kanban DAG chuyên dụng theo phiên)
        if path == "/api/gen/session/todos":
            conv_id = query.get("conv_id", [query.get("conversation_id", [""])[0]])[0] or db.default_conv_id()
            todos = db.get_gen_session_todos(conv_id)
            self._send_json(200, {"todos": todos, "count": len(todos), "conversation_id": conv_id})
            return

        # Trình duyệt tự xin /favicon.ico: trả 204 thay vì 404 (index.html đã có icon data: URI)
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        return super().do_GET()

    def _handle_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body)
        except Exception:
            data = {}

        # 0. API Model Context Protocol (MCP) JSON-RPC 2.0 Handler
        if path in ("/mcp", "/api/mcp"):
            parsed_post = urllib.parse.urlparse(self.path)
            post_query = urllib.parse.parse_qs(parsed_post.query)
            tool_name = None
            if isinstance(data, dict) and data.get("method") == "tools/call":
                tool_name = (data.get("params") or {}).get("name")

            is_auth, agent_info, err_msg = db.verify_mcp_request_auth(self.headers, post_query, tool_name=tool_name)
            if not is_auth:
                self._send_json(401, {
                    "jsonrpc": "2.0",
                    "id": data.get("id") if isinstance(data, dict) else None,
                    "error": {
                        "code": -32000,
                        "message": err_msg or "Agent chưa xác thực hoặc quyền đã bị thu hồi"
                    }
                })
                return

            resp = mcp_core.handle_jsonrpc(data)
            if resp is None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            self._send_json(200, resp)
            return

        # 0.1 API Tạo Token Xác Thực MCP Mới
        if path == "/api/mcp/tokens/create":
            name = data.get("name", "").strip()
            perms = data.get("permissions", ["all"])
            expires_days = data.get("expires_days", 90)
            client = data.get("client", "Manual Token")
            if not isinstance(perms, list):
                perms = [perms]
            ok_perms, bad_perms = mcp_core.valid_token_permissions(perms)
            if not ok_perms:
                self._send_json(400, {"error": f"Scope không hợp lệ: {', '.join(map(str, bad_perms))}"})
                return
            res = db.create_mcp_agent_token(name, perms, expires_days, client, origin=self._request_origin())
            self._send_json(200 if "error" not in res else 400, res)
            return

        # 0.2 API Thu Hồi Token Xác Thực MCP
        if path == "/api/mcp/tokens/revoke":
            token_id = data.get("id") or data.get("token_id")
            if token_id:
                res = db.revoke_mcp_agent_token(token_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing token id"})
            return

        # 0.3 API Bật / Tắt Chế Độ Bắt Buộc Xác Thực MCP (Strict Auth)
        if path == "/api/mcp/auth/toggle":
            enabled = bool(data.get("require_auth", False))
            res = db.set_mcp_strict_auth(enabled, origin=self._request_origin())
            self._send_json(200, res)
            return

        # 1. Thêm tin nhắn chat vào SQLite
        if path == "/api/chat":
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            runtime_id = data.get("runtime_id", "runtime-01")
            author = data.get("author", "Lead Architect")
            tag = data.get("tag", "General")
            msg_body = data.get("body", "")
            react = data.get("react", ["📥 đã nhận"])

            with db.get_connection() as conn:
                cursor = conn.cursor()
                now_time = time.strftime("%H:%M:%S")
                cursor.execute("""
                INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (project_id, runtime_id, author, now_time, tag, msg_body, json.dumps(react, ensure_ascii=False)))
                conn.commit()

            self._send_json(200, {"status": "saved", "time": now_time})
            return

        # 2. Cập nhật trạng thái Todo
        if path == "/api/todo/update":
            todo_id = data.get("id") or data.get("todo_id")
            new_status = data.get("status") or data.get("new_status")
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            if todo_id and new_status:
                # Sang 'done' → complete_task (kiểm evidence + chỉ người giữ task; force ghi audit) — #18
                res = db.update_todo_status(todo_id, new_status, project_id, **_status_change_args(data))
                if "error" not in res:
                    res["success"] = True
                self._send_json(db.task_error_http_status(res), res)
            else:
                self._send_json(400, {"error": "Missing id or status"})
            return

        # 3. Cập nhật Instruction cho Role (khóa theo Master Input)
        if path == "/api/role/instruction":
            role_id = data.get("id")
            instruction = data.get("instruction")
            if role_id and instruction:
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE agent_roles SET instruction = ? WHERE id = ?", (instruction, role_id))
                    conn.commit()
                self._send_json(200, {"status": "instruction_updated", "id": role_id})
            else:
                self._send_json(400, {"error": "Missing id or instruction"})
            return

        # 3b. Cập nhật Model AI cho Role
        if path == "/api/role/model":
            role_id = data.get("id") or data.get("role_id")
            model = data.get("model") or data.get("model_name")
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            if role_id and model:
                success = db.update_role_model(role_id, model, project_id)
                self._send_json(200, {"status": "model_updated", "id": role_id, "model": model, "success": success})
            else:
                self._send_json(400, {"error": "Missing id or model"})
            return

        # 4. Điều phối lệnh thực thi (Execution Dispatcher)
        if path == "/api/execute":
            command = data.get("command", "")
            role = data.get("role", "Worker")
            res = {
                "role": role,
                "command": command,
                "status": "dispatched",
                "time": time.strftime("%H:%M:%S")
            }
            self._send_json(200, res)
            return

        # 5. Đổi tài khoản / Profile cho phiên Tmux
        if path == "/api/tmux/account":
            session_id = data.get("session_id")
            account_type = data.get("account_type", "owner_default")
            account_label = data.get("account_label", "")
            profile_dir = data.get("profile_dir", "")
            err = db.validate_account_switch(session_id, account_type, profile_dir)
            if err:
                self._send_json(400, {"error": err})
                return
            if session_id:
                account_label = db.update_tmux_account(session_id, account_type, account_label, profile_dir)
                db.append_tmux_output(session_id, f"auth switch --account='{account_label}'", f"Đã chuyển cấu hình phiên sang: {account_label}")
                self._send_json(200, {"status": "account_updated", "session_id": session_id, "account_label": account_label})
            else:
                self._send_json(400, {"error": "Missing session_id"})
            return

        # 6. Gửi lệnh / phím vào phiên Tmux (tmux send-keys)
        if path == "/api/tmux/send":
            session_id = data.get("session_id")
            command = data.get("command", "").strip()
            key = data.get("key", "").strip()
            if session_id and (command or key):
                # Tự động đánh thức nếu phiên đang ngủ đông hoặc đóng băng
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT status FROM tmux_sessions WHERE id = ?", (session_id,))
                    s_row = cursor.fetchone()
                    if s_row and s_row["status"] == "hibernated":
                        db.wake_tmux_session(session_id)
                        time.sleep(0.2)
                    elif s_row and s_row["status"] == "paused":
                        db.resume_tmux_session(session_id)
                        time.sleep(0.1)

                allowed, reason = directive_guard.guard(session_id, command, key)
                db.log_directive_audit(session_id, "http:/api/tmux/send", key or command, allowed, reason)
                if not allowed:
                    self._send_json(403, {"status": "rejected", "session_id": session_id, "reason": reason})
                    return

                tmux_success = False
                payload = key if key else command
                try:
                    args = ["tmux", "send-keys", "-t", session_id, payload]
                    if not key:
                        args.append("Enter")
                    res = subprocess.run(args, capture_output=True, text=True, timeout=2.0)
                    tmux_success = (res.returncode == 0)
                except Exception:
                    pass

                log_cmd = f"^[KEY: {key}]" if key else command
                output_msg = f"Đã gửi trực tiếp vào tmux qua send-keys ({log_cmd})." if tmux_success else f"Đã ghi nhận tín hiệu '{log_cmd}' vào runtime của phiên."
                db.append_tmux_output(session_id, log_cmd, output_msg)
                self._send_json(200, {
                    "status": "command_sent",
                    "session_id": session_id,
                    "command": log_cmd,
                    "tmux_real": tmux_success
                })
            else:
                self._send_json(400, {"error": "Missing session_id or (command / key)"})
            return

        # 6.1. Quản trị vòng đời phiên Tmux (Pause, Resume, Hibernate, Wake)
        if path == "/api/tmux/action":
            session_id = data.get("session_id", "all")
            action = data.get("action", "wake") # pause, resume, hibernate, wake, sleep_all, wake_all
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            results = db.manage_tmux_swarm_lifecycle(action, session_id, project_id)
            self._send_json(200, {
                "status": "ok",
                "action": action,
                "target": session_id,
                "results": results
            })
            return

        # 6.2. "Chạy Task này" (session_id) / "Chạy Toàn Bộ Swarm" (không session_id): lệnh agy thật cho task đang gán,
        #      qua allowlist directive_guard. results = {sid: {status: dispatched|error, task_id, command, reason, ...}}
        if path == "/api/swarm/dispatch":
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            session_id = (data.get("session_id") or "").strip() or None
            results = db.dispatch_swarm_workflow(project_id, session_id)
            n_ok = sum(1 for r in results.values() if r.get("status") == "dispatched")
            self._send_json(200, {
                "status": "dispatched" if n_ok else "error",
                "project_id": project_id,
                "session_id": session_id,
                "dispatched_count": n_ok,
                "error_count": len(results) - n_ok,
                "results": results,
                "time": time.strftime("%H:%M:%S")
            })
            return

        # 6.25. Chờ kết quả worker (#9): POST /api/dispatch/wait {dispatch_id | task_id | session_id, timeout_sec}
        if path == "/api/dispatch/wait":
            self._handle_dispatch_wait(data if isinstance(data, dict) else {})
            return

        # 6.3. Kiểm tra quota bằng 1 lệnh agy thật (ghi quota_probe) (#6)
        if path == "/api/quota/probe":
            profile_id = (data.get("profile_id") or data.get("profile") or "owner_default").strip()
            res = db.probe_quota(profile_id)
            self._send_json(200, res)
            return

        # 7. Bắt đầu luồng đăng nhập OAuth cho Profile
        if path == "/api/oauth/start":
            profile_id = data.get("profile_id", "profile1")
            custom_path = data.get("custom_path", "")
            res = db.start_oauth_login(profile_id, custom_path)
            self._send_json(200, res)
            return

        # 8. Kiểm tra trạng thái xác thực OAuth của Profile
        if path == "/api/oauth/check":
            profile_id = data.get("profile_id", "profile1")
            res = db.check_oauth_status(profile_id)
            self._send_json(200, res)
            return

        # 9. Lưu token OAuth thủ công (JSON / Token String / Import)
        if path == "/api/oauth/save_token":
            profile_id = data.get("profile_id")
            token_data = data.get("token_data")
            if profile_id and token_data:
                res = db.save_oauth_token(profile_id, token_data)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing profile_id or token_data"})
            return

        # 9.1. Kế thừa / Sao chép token OAuth từ Profile nguồn sang Profile đích
        if path == "/api/oauth/clone":
            src = data.get("source_id") or data.get("source_profile")
            dst = data.get("target_id") or data.get("profile_id")
            if src and dst:
                res = db.clone_oauth_profile(src, dst)
                self._send_json(200 if res.get("ok") else 400, res)
            else:
                self._send_json(400, {"error": "Missing source_id or target_id"})
            return

        # 10. Gán Profile OAuth cho Swarm Role
        if path == "/api/oauth/assign":
            session_id = data.get("session_id")
            profile_id = data.get("profile_id")
            if session_id and profile_id:
                res = db.assign_oauth_to_role(session_id, profile_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing session_id or profile_id"})
            return

        # 11. Đăng xuất / Xóa token của Profile
        if path == "/api/oauth/logout":
            profile_id = data.get("profile_id")
            if profile_id:
                res = db.logout_oauth_profile(profile_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing profile_id"})
            return

        # 12. Gửi chỉ thị cho Orchestrator (Orchestrator Control Plane Chat)
        if path == "/api/orch/chat":
            message = data.get("message", "").strip()
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            if message:
                res = db.process_orch_instruction(message, project_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing message"})
            return

        # 13. Sinh worker mới động (Dynamic Worker Spawning)
        if path == "/api/orch/spawn":
            role_name = data.get("role_name", "").strip()
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            account_type = data.get("account_type", "owner_default")
            mission = data.get("mission", "").strip()
            scope = data.get("scope", "").strip()
            if role_name:
                res = db.spawn_worker(role_name, project_id, account_type, mission, scope)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing role_name"})
            return

        # 14. Khóa độc quyền nhiệm vụ (Anti-Chaos Task Claiming)
        if path == "/api/task/claim":
            session_id = data.get("session_id", "").strip()
            todo_id = (data.get("todo_id") or data.get("task_id") or "").strip()
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            if session_id and todo_id:
                res = db.claim_task(session_id, todo_id, project_id)
                # 409: đang bị người khác khóa (kèm held_by) / đã done / thiếu tiền đề; 404: không có task
                status_code = 200 if "error" not in res else (404 if res.get("error") == "Task not found" else 409)
                self._send_json(status_code, res)
            else:
                self._send_json(400, {"error": "Missing session_id or todo_id (or task_id)"})
            return

        # 14b. Giao task cho 1 vai (#24): POST /api/task/assign {todo_id, session_id: "qa" | "gw-qa-agy", author?, channel_id?}
        #      → claim task cho worker + war-room "@vai Thực hiện TSK-n" (prompt kèm tiêu đề, checklist, viec_ref) → {dispatch_id, ...}
        #      400 thiếu/sai tham số · 404 không có task · 409 task đã done / người khác đang giữ
        if path == "/api/task/assign":
            todo_id = str(data.get("todo_id") or data.get("task_id") or "").strip()
            session_id = str(data.get("session_id") or data.get("role") or "").strip()
            res = db.assign_task_to_role(todo_id, session_id, data.get("project_id", "PRJ-GEN-WORKPLACE"),
                                         author=str(data.get("author") or "Ryan (Owner)"), channel_id=str(data.get("channel_id") or "war_room"))
            code = {"bad_request": 400, "not_found": 404, "already_done": 409, "locked": 409, "retry": 409}.get(res.get("code"), 400) \
                if "error" in res else 200
            if res.get("error") == "Task not found":
                code = 404
            self._send_json(code, res)
            return

        # 15. Nghiệm thu hoàn tất nhiệm vụ (Evidence-Backed Task Completion)
        if path == "/api/task/complete":
            session_id = data.get("session_id", "").strip()
            todo_id = (data.get("todo_id") or data.get("task_id") or "").strip()
            evidence_ref = data.get("evidence_ref", "").strip()
            verified_by = data.get("verified_by", "Lead Architect")
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            force = data.get("force") in (True, 1, "1", "true", "True", "yes")
            reason = str(data.get("reason") or "").strip()
            if session_id and todo_id and evidence_ref:
                res = db.complete_task(session_id, todo_id, evidence_ref, verified_by, project_id, force=force, reason=reason)
                # 200 ok; 404 không có task; 409 đã done / người khác đang giữ (not_holder, kèm held_by); 400 bằng chứng sai
                self._send_json(db.task_error_http_status(res), res)
            else:
                self._send_json(400, {"error": "Missing session_id, todo_id (or task_id), or evidence_ref"})
            return

        # 16. Thu hồi nhiệm vụ bị treo (Anti-Zombie Task Reclamation)
        if path == "/api/task/reclaim":
            try:
                timeout = int(data["timeout_seconds"]) if data.get("timeout_seconds") not in (None, "") else None
            except (TypeError, ValueError):
                self._send_json(400, {"error": "timeout_seconds phải là số nguyên"})
                return
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            res = db.reclaim_stalled_tasks(timeout, project_id)
            self._send_json(200, res)
            return

        # 17. Gửi tin nhắn vào War Room / Phòng Giao Ban Swarm (@vai → agy thật chạy nền, xem db.post_warroom_message)
        if path == "/api/warroom/send":
            channel_id = data.get("channel_id", "war_room")
            prj_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            author = data.get("author", "Ryan (Owner)")
            msg = data.get("message", "")
            tag = data.get("tag", "Directive")
            res = db.post_warroom_message(prj_id, channel_id, author, msg, tag, task_id=str(data.get("task_id") or "").strip())
            status_code = 400 if "error" in res else 200
            self._send_json(status_code, res)
            return

        # 18. Lưu chỉ thị / File Plan nguồn SSOT (Save SSOT Source Input)
        if path == "/api/ssot/save":
            content = data.get("content", "")
            prj_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            spec_file = BASE_DIR / "docs" / "SSOT_ORIGINAL_SPEC.md"
            try:
                if content:
                    spec_file.write_text(content, encoding="utf-8")
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                    INSERT OR REPLACE INTO master_ssot (id, project_id, title, body, source_ref, verified_time)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, ("SSOT-MASTER-INPUT", prj_id, "Chỉ Thị Tổng Thể & PRD Nguồn", content[:500] + "...", "Input Chat Tổng", time.strftime("%H:%M")))
                    conn.commit()
                self._send_json(200, {"status": "saved", "path": "docs/SSOT_ORIGINAL_SPEC.md", "time": time.strftime("%H:%M:%S")})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # 19. Lưu đặc tả SSOT làm nguồn cho các vai. KHÔNG sinh roadmap/todo (generated = 0), không tin War Room mẫu.
        #     Frontend (generateFromSource) chỉ đọc key "state"; các key status/generated/note nói đúng việc đã làm.
        if path == "/api/ssot/generate":
            content = data.get("content", "")
            prj_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            res = db.generate_structure_from_ssot(content, prj_id)
            if "error" in res:
                self._send_json(400, res)
                return
            state = db.get_full_state(prj_id)
            self._send_json(200, {**res, "state": state})
            return

        # 20. Thẩm định sự kiện SSOT Event
        if path == "/api/events/verify":
            event_id = data.get("id") or data.get("event_id")
            status = data.get("status", "ssot")
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            if event_id:
                db.verify_ssot_event(event_id, status, project_id)
                self._send_json(200, {"status": "verified", "id": event_id})
            else:
                self._send_json(400, {"error": "Missing event_id"})
            return

        # 21. Tạo dự án mới lưu trực tiếp vào SQLite
        if path == "/api/project/create":
            name = data.get("name", "").strip()
            repo = data.get("repo", "").strip()
            plan = data.get("plan", "").strip()
            res = db.create_new_project(name, repo, plan)
            self._send_json(200, res)
            return

        # 22. Trao đổi chỉ thị với Runtime Chuyên gia: CHỈ lưu tin vào chat_messages theo runtime_id.
        #     Không gửi gì vào tmux ở đây (trước đây echo tin qua send-keys không qua directive_guard → chạy được
        #     $(...)/backtick trong shell worker). Muốn gõ vào tmux phải dùng /api/tmux/send (có allowlist + audit).
        if path == "/api/runtime/chat":
            message = data.get("message", "").strip()
            session_id = data.get("session_id", "gw-lead-agy")
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            author = data.get("author", "Owner (Ryan)")
            if message:
                now_time = time.strftime("%H:%M:%S")
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                    INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json)
                    VALUES (?, ?, ?, ?, 'Directive', ?, '["✅ đã nhận"]')
                    """, (project_id, session_id, author, now_time, message))
                    conn.commit()
                self._send_json(200, {"status": "sent", "session_id": session_id, "time": now_time})
            else:
                self._send_json(400, {"error": "Missing message"})
            return

        # 23. API Gen Workplace Chat
        if path == "/api/gen/chat":
            conv_id = data.get("conv_id") or db.default_conv_id()
            author = data.get("author", "Ryan (Owner)")
            message = data.get("message", "").strip()
            model = data.get("model", "Gemini 3.1 Pro (High)")
            account = data.get("account", "owner_default")
            if not message:
                self._send_json(400, {"error": "Message is empty"})
                return
            res = db.send_gen_chat(conv_id, author, message, model, account)
            self._send_json(200, res)
            return

        # 23.5. API Cập Nhật Hồ Sơ Owner Ryan
        if path == "/api/owner/profile/update":
            owner_id = data.get("owner_id", "owner-ryan")
            display_name = data.get("display_name")
            email = data.get("email")
            bio = data.get("bio")
            settings = data.get("settings")
            res = db.update_owner_profile(owner_id, display_name, email, bio, settings)
            self._send_json(200 if res.get("status") == "ok" else 400, res)
            return

        # 24. API Gen Workplace Progressive Compaction
        if path == "/api/gen/compact":
            conv_id = data.get("conv_id") or db.default_conv_id()
            model_from = data.get("model_from", "")
            model_to = data.get("model_to", "")
            manual = bool(data.get("manual", False))
            res = db.compact_gen_conversation(conv_id, model_from, model_to, manual)
            self._send_json(200, res)
            return

        # 25. API Gen Workplace Conversation Create / Update / Delete
        if path == "/api/gen/conversations/create":
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            title = data.get("title", "Cuộc trò chuyện mới")
            model = data.get("model", "Gemini 3.1 Pro (High)")
            account = data.get("account", "owner_default")
            owner_id = data.get("owner_id", "owner-ryan")
            res = db.create_gen_conversation(project_id, title, model, account, owner_id)
            self._send_json(200, res)
            return

        # 25b. Ghi 1 tin tiến độ vào phiên, CHỈ lưu (không gọi agy) — cùng logic MCP log_session_message (#19)
        if path == "/api/gen/conversations/log":
            res = db.log_gen_message(data.get("conv_id", ""), data.get("content", ""),
                                     data.get("role") or "assistant", data.get("author") or "AI Agent")
            if "error" in res:
                self._send_json(404 if res["error"].startswith("Không tìm thấy phiên") else 400, res)
            else:
                self._send_json(200, res)
            return

        if path == "/api/gen/conversations/update":
            conv_id = data.get("conv_id")
            title = data.get("title")
            is_pinned = data.get("is_pinned")
            model = data.get("model")
            account = data.get("account")
            if conv_id:
                res = db.update_gen_conversation(conv_id, title, is_pinned, model, account)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing conv_id"})
            return

        if path == "/api/gen/conversations/context":
            conv_id = data.get("conv_id")
            active_tab = data.get("active_tab")
            active_file = data.get("active_file")
            open_tabs = data.get("open_tabs")
            active_evidence_id = data.get("active_evidence_id")
            if conv_id:
                res = db.update_gen_conversation_context(conv_id, active_tab, active_file, open_tabs, active_evidence_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing conv_id"})
            return

        if path == "/api/gen/conversations/delete":
            conv_id = data.get("conv_id")
            if conv_id:
                res = db.delete_gen_conversation(conv_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing conv_id"})
            return

        # 26. API Gen Scratchpad Note Save / Delete
        if path == "/api/gen/notes/save":
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            note_id = data.get("id")
            title = data.get("title", "Ghi chú mới")
            content = data.get("content", "")
            tags = data.get("tags", [])
            evidence_ref = data.get("evidence_ref", "")
            author = data.get("author", "Ryan (Owner)")
            conv_id = data.get("conv_id", "")
            owner_id = data.get("owner_id", "owner-ryan")
            res = db.save_gen_note(project_id, note_id, title, content, tags, evidence_ref, author, conv_id, owner_id)
            self._send_json(200, res)
            return

        if path == "/api/gen/notes/delete":
            note_id = data.get("id")
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            if note_id:
                res = db.delete_gen_note(note_id, project_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing id"})
            return

        # 27. API Gen Session File Create / Delete / Attach
        if path == "/api/gen/session/file/create":
            conv_id = data.get("conv_id")
            file_path = data.get("path")
            is_dir = bool(data.get("is_dir", False))
            content = data.get("content", "")
            if conv_id and file_path:
                res = db.create_gen_session_file(conv_id, file_path, is_dir, content)
                self._send_json(200 if "error" not in res else 400, res)
            else:
                self._send_json(400, {"error": "Missing conv_id or path"})
            return

        if path == "/api/gen/session/file/delete":
            conv_id = data.get("conv_id")
            file_path = data.get("path")
            if conv_id and file_path:
                res = db.delete_gen_session_file(conv_id, file_path)
                self._send_json(200 if "error" not in res else 400, res)
            else:
                self._send_json(400, {"error": "Missing conv_id or path"})
            return

        if path == "/api/gen/session/file/attach_repo":
            conv_id = data.get("conv_id")
            repo_path = data.get("repo_path")
            if conv_id and repo_path:
                res = db.attach_repo_file_to_session(conv_id, repo_path)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing conv_id or repo_path"})
            return

        # 28. API Gen Session Todos & Checklists (Quản lý Kanban & Checklist chuyên dụng theo phiên)
        if path == "/api/gen/session/todos/save":
            conv_id = data.get("conv_id") or data.get("conversation_id")
            todo_id = data.get("id") or data.get("todo_id")
            title = data.get("title", "")
            description = data.get("description", "")
            status = data.get("status", "todo")
            priority = data.get("priority", "high")
            assigned_agent = data.get("assigned_agent", "Gen Core")
            checklist = data.get("checklist", [])
            evidence_ref = data.get("evidence_ref", "")
            owner_id = data.get("owner_id", "owner-ryan")
            viec_ref = (data.get("viec_ref") or "").strip()
            try:
                order_idx = int(data.get("order_idx", 0) or 0)
            except (TypeError, ValueError):
                order_idx = 0
            if conv_id and title:
                # Phiên không tồn tại → 400 (trước đây sqlite3.IntegrityError FOREIGN KEY không bắt → ngắt kết nối)
                if not db.gen_conversation_exists(conv_id):
                    self._send_json(400, {"error": f"Phiên chat không tồn tại: {conv_id}"})
                    return
                # Tạo mới (không có id / id chưa tồn tại) bắt buộc viec_ref khớp ^VIEC-[0-9]+$ → thiếu/sai trả 400
                # status='done' → qua complete_task (evidence_ref + người giữ task); bị từ chối → 400/409, trạng thái giữ nguyên
                res = db.save_gen_session_todo(conv_id, todo_id, title, description, status, priority, assigned_agent, checklist,
                                               evidence_ref, order_idx=order_idx, owner_id=owner_id, viec_ref=viec_ref,
                                               **_status_change_args(data, with_evidence=False))
                self._send_json(db.task_error_http_status(res), res)
            else:
                self._send_json(400, {"error": "Missing conv_id or title"})
            return

        if path == "/api/gen/session/todos/item/toggle":
            conv_id = data.get("conv_id") or data.get("conversation_id")
            todo_id = data.get("todo_id") or data.get("id")
            item_id = data.get("item_id")
            done_status = data.get("done")
            if conv_id and todo_id and item_id:
                res = db.toggle_gen_session_todo_checklist_item(conv_id, todo_id, item_id, done_status)
                self._send_json(200 if "error" not in res else 400, res)
            else:
                self._send_json(400, {"error": "Missing conv_id, todo_id, or item_id"})
            return

        if path == "/api/gen/session/todos/status":
            conv_id = data.get("conv_id") or data.get("conversation_id")
            todo_id = data.get("todo_id") or data.get("id")
            new_status = data.get("status") or data.get("new_status")
            if conv_id and todo_id and new_status:
                # Sang 'done' (kéo thả / nút Nghiệm thu) → complete_task: thiếu/sai evidence 400, người khác giữ / đã done 409
                res = db.update_gen_session_todo_status(conv_id, todo_id, new_status, **_status_change_args(data))
                self._send_json(db.task_error_http_status(res), res)
            else:
                self._send_json(400, {"error": "Missing conv_id, todo_id, or status"})
            return

        if path == "/api/gen/session/todos/delete":
            conv_id = data.get("conv_id") or data.get("conversation_id")
            todo_id = data.get("todo_id") or data.get("id")
            if conv_id and todo_id:
                res = db.delete_gen_session_todo(conv_id, todo_id)
                self._send_json(200, res)
            else:
                self._send_json(400, {"error": "Missing conv_id or todo_id"})
            return

        self.send_response(404)
        self.end_headers()

UI_SESSION_ID = "owner-ui"

def _status_change_args(data, with_evidence=True):
    """Tham số chung cho các API đổi trạng thái task: người gọi (mặc định owner-ui), evidence_ref, force, reason."""
    args = {
        "session_id": str(data.get("session_id") or UI_SESSION_ID).strip(),
        "force": data.get("force") in (True, 1, "1", "true", "True", "yes"),
        "reason": str(data.get("reason") or "").strip(),
    }
    if with_evidence:
        args["evidence_ref"] = str(data.get("evidence_ref") or "").strip()
    return args

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def start_oauth_callback_server(port=8085):
    """Khởi chạy server lắng nghe callback Google OAuth tại localhost:8085 trong background daemon thread."""
    try:
        cb_server = ThreadedHTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
        t = threading.Thread(target=cb_server.serve_forever, daemon=True, name="OAuthCallbackServer-8085")
        t.start()
        print(f"  Google OAuth Callback: http://127.0.0.1:{port}/oauth2callback (Active)")
        return cb_server
    except Exception as e:
        print(f"  [Warning] Không thể mở cổng OAuth Callback {port}: {e}")
        return None

def start_reclaim_worker():
    """Thread daemon gọi db.reclaim_stalled_tasks() mỗi GW_RECLAIM_INTERVAL_SEC giây (mặc định 300) để thu hồi task treo (#4)."""
    try:
        interval = max(5, int(os.environ.get("GW_RECLAIM_INTERVAL_SEC", "300")))
    except ValueError:
        interval = 300
    # Cùng ngưỡng claim_task dùng để cho claim lại task có khóa quá hạn (#16)
    timeout = db.task_lock_timeout_sec()

    def _loop():
        while True:
            time.sleep(interval)
            try:
                res = db.reclaim_stalled_tasks(timeout)
                print(f"[reclaim] thu hồi {res.get('reclaimed_count', 0)} task")
            except Exception as e:
                print(f"[reclaim] lỗi: {e}")

    t = threading.Thread(target=_loop, daemon=True, name="TaskReclaimWorker")
    t.start()
    print(f"  Reclaim task treo: mỗi {interval}s (timeout {timeout}s)")
    return t

def main():
    print(f"==================================================")
    print(f"  GENESIS SWARM WORKPLACE - CONTROL PLANE DAEMON  ")
    print(f"  Port: {PORT}")
    print(f"  Frontend: {FRONTEND_DIR}")
    print(f"  Data: {DATA_DIR}")
    print(f"  Database: SQLite 3 WAL + FTS5 Ready")
    start_oauth_callback_server(8085)
    start_reclaim_worker()
    # Mở phiên worker chưa chạy đúng 1 lần lúc khởi động (trước đây chạy ở mỗi lượt poll /api/tmux/sessions, #30)
    threading.Thread(target=db.ensure_real_tmux_sessions, daemon=True, name="TmuxStartup").start()
    auto_update.start_worker(BASE_DIR, DATA_DIR)
    print(f"==================================================")
    server = ThreadedHTTPServer(("0.0.0.0", PORT), SwarmHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()

if __name__ == "__main__":
    main()

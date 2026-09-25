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
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORT = int(os.environ.get("PORT", 8888))
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
default_data = "/app/data" if (os.path.exists("/app") or os.environ.get("DOCKER_CONTAINER")) else str(BASE_DIR / "data")
DATA_DIR = Path(os.environ.get("DATA_DIR", default_data))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Tự động nạp SQLite DB Module
sys.path.insert(0, str(BASE_DIR))
try:
    from backend import db
except ImportError:
    import db

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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

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
                "active_project": state.get("project", {}).get("name", "gen-workplace")
            }
            self._send_json(200, status)
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
            sessions = db.get_tmux_sessions(prj_id)
            
            # Thử capture live nếu có lệnh tmux trên hệ thống
            for s in sessions:
                sid = s["id"]
                try:
                    res = subprocess.run(["tmux", "capture-pane", "-t", sid, "-p", "-S", "-200"],
                                         capture_output=True, text=True, timeout=1.5)
                    if res.returncode == 0 and res.stdout.strip():
                        # Cắt bỏ toàn bộ các dòng trống ở đuôi (tránh đen màn hình khi auto-scroll)
                        raw_lines = res.stdout.splitlines()
                        while raw_lines and not raw_lines[-1].strip():
                            raw_lines.pop()
                        if raw_lines:
                            s["terminal_output"] = "\n".join(raw_lines)
                except Exception:
                    pass

            self._send_json(200, {"sessions": sessions})
            return

        # 5. API Danh sách OAuth Profiles & Trạng thái Google Login
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

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body)
        except Exception:
            data = {}

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
            todo_id = data.get("id")
            new_status = data.get("status")
            if todo_id and new_status:
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE todos SET status = ? WHERE id = ?", (new_status, todo_id))
                    conn.commit()
                self._send_json(200, {"status": "updated", "id": todo_id, "new_status": new_status})
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
            account_label = data.get("account_label", "Mặc định (Owner Gmail)")
            profile_dir = data.get("profile_dir", "")
            if session_id:
                db.update_tmux_account(session_id, account_type, account_label, profile_dir)
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

        # 6.2. Truyền lệnh / Điều phối luồng làm việc đồng loạt cho 6 Agent Swarm
        if path == "/api/swarm/dispatch":
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            results = db.dispatch_swarm_workflow(project_id)
            self._send_json(200, {
                "status": "dispatched",
                "project_id": project_id,
                "results": results,
                "time": time.strftime("%H:%M:%S")
            })
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
                status_code = 409 if "error" in res else 200
                self._send_json(status_code, res)
            else:
                self._send_json(400, {"error": "Missing session_id or todo_id (or task_id)"})
            return

        # 15. Nghiệm thu hoàn tất nhiệm vụ (Evidence-Backed Task Completion)
        if path == "/api/task/complete":
            session_id = data.get("session_id", "").strip()
            todo_id = (data.get("todo_id") or data.get("task_id") or "").strip()
            evidence_ref = data.get("evidence_ref", "").strip()
            verified_by = data.get("verified_by", "Lead Architect")
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            if session_id and todo_id and evidence_ref:
                res = db.complete_task(session_id, todo_id, evidence_ref, verified_by, project_id)
                status_code = 400 if "error" in res else 200
                self._send_json(status_code, res)
            else:
                self._send_json(400, {"error": "Missing session_id, todo_id (or task_id), or evidence_ref"})
            return

        # 16. Thu hồi nhiệm vụ bị treo (Anti-Zombie Task Reclamation)
        if path == "/api/task/reclaim":
            timeout = int(data.get("timeout_seconds", 300))
            project_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            res = db.reclaim_stalled_tasks(timeout, project_id)
            self._send_json(200, res)
            return

        # 17. Gửi tin nhắn vào War Room / Phòng Giao Ban Swarm (tự động phản hồi AI theo vai trò)
        if path == "/api/warroom/send":
            channel_id = data.get("channel_id", "war_room")
            prj_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            author = data.get("author", "Ryan (Owner)")
            msg = data.get("message", "")
            tag = data.get("tag", "Directive")
            res = db.post_warroom_message(prj_id, channel_id, author, msg, tag)
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

        # 19. AI Phân rã cấu trúc từ nguồn SSOT (Generate Roadmap, Todos, Roles from Source)
        if path == "/api/ssot/generate":
            content = data.get("content", "")
            prj_id = data.get("project_id", "PRJ-GEN-WORKPLACE")
            res = db.generate_structure_from_ssot(content, prj_id)
            state = db.get_full_state(prj_id)
            self._send_json(200, {"status": "generated", "summary": res, "state": state})
            return

        self.send_response(404)
        self.end_headers()

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def main():
    print(f"==================================================")
    print(f"  GENESIS SWARM WORKPLACE - CONTROL PLANE DAEMON  ")
    print(f"  Port: {PORT}")
    print(f"  Frontend: {FRONTEND_DIR}")
    print(f"  Data: {DATA_DIR}")
    print(f"  Database: SQLite 3 WAL + FTS5 Ready")
    print(f"==================================================")
    server = ThreadedHTTPServer(("0.0.0.0", PORT), SwarmHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()

if __name__ == "__main__":
    main()

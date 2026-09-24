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
import urllib.parse
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORT = int(os.environ.get("PORT", 8888))
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
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
                    res = subprocess.run(["tmux", "capture-pane", "-t", sid, "-p", "-S", "-30"],
                                         capture_output=True, text=True, timeout=1.5)
                    if res.returncode == 0 and res.stdout.strip():
                        s["terminal_output"] = res.stdout
                except Exception:
                    pass

            self._send_json(200, {"sessions": sessions})
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

#!/usr/bin/env python3
"""
GENESIS Multi-Agent Swarm Orchestrator - SQLite WAL Database Engine
Quản trị lưu trữ bền vững (Persistent Storage), Full-Text Search (FTS5) Catalog,
và Single Source of Truth (SSOT) cho toàn bộ Swarm Runtimes.
"""

import os
import json
import sqlite3
import re
import base64
import time
import uuid
import subprocess
import hashlib
import secrets
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

# Tự động nạp cấu hình từ .env nếu tồn tại
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    except Exception:
        pass

default_data = "/app/data" if (os.path.exists("/app") or os.environ.get("DOCKER_CONTAINER")) else str(BASE_DIR / "data")
DATA_DIR = Path(os.environ.get("DATA_DIR", default_data))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "gen-workplace.db"
HOME_DIR = os.environ.get("HOME") or str(Path.home())

def normalize_project_id(pid):
    """Chuẩn hóa ID dự án linh hoạt: gen-workplace -> PRJ-GEN-WORKPLACE."""
    if not pid or str(pid).strip() in ("gen-workplace", "PRJ-GEN-WORKPLACE", "default", "PRJ-DEFAULT"):
        return "PRJ-GEN-WORKPLACE"
    return str(pid).strip()

@contextmanager
def get_connection():
    """Tạo kết nối SQLite tối ưu với WAL mode, Foreign Keys và bảo đảm đóng kết nối giải phóng File Descriptors."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        with conn:
            yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass

def init_db():
    """Khởi tạo schema toàn diện cho Multi-Agent Swarm."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. Projects
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            repo_path TEXT NOT NULL,
            branch TEXT DEFAULT 'main',
            plan_file TEXT,
            source_text TEXT,
            meta TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 2. Roadmaps
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS roadmaps (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            todos_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            order_idx INTEGER DEFAULT 0
        );
        """)

        # 3. Todos
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id TEXT PRIMARY KEY,
            roadmap_id TEXT NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            assigned_role TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 4. Agent Roles
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_roles (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role_key TEXT NOT NULL,
            name TEXT NOT NULL,
            cli_tool TEXT NOT NULL,
            scope TEXT,
            instruction TEXT NOT NULL,
            status TEXT DEFAULT 'active'
        );
        """)

        # 5. Workflow Nodes
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS workflow_nodes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            step_index INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            role_name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            coord_x INTEGER DEFAULT 0,
            coord_y INTEGER DEFAULT 0,
            input_desc TEXT,
            output_desc TEXT,
            check_desc TEXT,
            handoff_desc TEXT,
            checklist_json TEXT DEFAULT '[]'
        );
        """)

        # 6. Agent Runtimes
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_runtimes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role_name TEXT NOT NULL,
            cli_tool TEXT NOT NULL,
            task_ref TEXT,
            branch TEXT DEFAULT 'main',
            status TEXT DEFAULT 'running',
            path TEXT,
            io_json TEXT DEFAULT '{}',
            trace_json TEXT DEFAULT '[]',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 7. Chat Messages
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            runtime_id TEXT,
            author TEXT NOT NULL,
            created_time TEXT NOT NULL,
            tag TEXT,
            body TEXT NOT NULL,
            react_json TEXT DEFAULT '[]'
        );
        """)

        # 8. Master SSOT
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS master_ssot (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            source_ref TEXT,
            verified_time TEXT,
            version INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 9. Role Memories
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS role_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role_name TEXT NOT NULL,
            body TEXT NOT NULL,
            tags_json TEXT DEFAULT '[]',
            synced_to_ssot INTEGER DEFAULT 0,
            created_time TEXT
        );
        """)

        # 10. Fast ID Lookup Catalog
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS catalog_references (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            ref_path TEXT,
            metadata_json TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 11. SQLite FTS5 Full-Text Search Virtual Table
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
            id,
            category,
            title,
            description,
            content='catalog_references',
            content_rowid='rowid'
        );
        """)

        # Triggers to keep FTS5 synchronized with catalog_references
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS catalog_ai AFTER INSERT ON catalog_references BEGIN
            INSERT INTO catalog_fts(rowid, id, category, title, description)
            VALUES (new.rowid, new.id, new.category, new.title, new.description);
        END;
        """)
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS catalog_ad AFTER DELETE ON catalog_references BEGIN
            INSERT INTO catalog_fts(catalog_fts, rowid, id, category, title, description)
            VALUES('delete', old.rowid, old.id, old.category, old.title, old.description);
        END;
        """)
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS catalog_au AFTER UPDATE ON catalog_references BEGIN
            INSERT INTO catalog_fts(catalog_fts, rowid, id, category, title, description)
            VALUES('delete', old.rowid, old.id, old.category, old.title, old.description);
            INSERT INTO catalog_fts(rowid, id, category, title, description)
            VALUES (new.rowid, new.id, new.category, new.title, new.description);
        END;
        """)

        # 12. Tmux Sessions (Cửa sổ phiên nền Agent Runtimes do hệ thống tạo)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tmux_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role_name TEXT NOT NULL,
            cli_tool TEXT NOT NULL,
            account_type TEXT DEFAULT 'owner_default',
            account_label TEXT DEFAULT 'Mặc định (Owner Gmail)',
            profile_dir TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            pid INTEGER DEFAULT 0,
            cwd TEXT,
            terminal_output TEXT DEFAULT '',
            conversation_id TEXT DEFAULT '',
            quota_gemini_json TEXT DEFAULT '{}',
            quota_anthropic_json TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Tự động di trú các cột mới chống rối loạn Swarm (Anti-Chaos Governance)
        for col, col_type in [
            ("conversation_id", "TEXT DEFAULT ''"),
            ("quota_gemini_json", "TEXT DEFAULT '{}'"),
            ("quota_anthropic_json", "TEXT DEFAULT '{}'"),
            ("allowed_paths_json", "TEXT DEFAULT '[]'"),
            ("blocked_paths_json", "TEXT DEFAULT '[]'"),
            ("current_task_id", "TEXT DEFAULT ''"),
            ("last_heartbeat", "TEXT DEFAULT ''"),
            # epoch giây của lần dùng phiên gần nhất (gửi lệnh / mở terminal / wake) — thread dọn phiên rảnh (#32)
            ("last_activity_at", "INTEGER DEFAULT 0")
        ]:
            try:
                cursor.execute(f"ALTER TABLE tmux_sessions ADD COLUMN {col} {col_type};")
            except Exception:
                pass

        # Di trú các cột quản trị tiến độ và khóa việc độc quyền cho todos
        for col, col_type in [
            ("assigned_session_id", "TEXT DEFAULT ''"),
            ("depends_on", "TEXT DEFAULT ''"),
            ("locked_at", "TEXT DEFAULT ''"),
            ("evidence_ref", "TEXT DEFAULT ''"),
            ("verified_by", "TEXT DEFAULT ''"),
            ("viec_ref", "TEXT DEFAULT ''")
        ]:
            try:
                cursor.execute(f"ALTER TABLE todos ADD COLUMN {col} {col_type};")
            except Exception:
                pass

        # Di trú cột model_name cho agent_roles
        try:
            cursor.execute("ALTER TABLE agent_roles ADD COLUMN model_name TEXT DEFAULT '';")
        except Exception:
            pass

        # 13. SSOT Events (Sự kiện thẩm định)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ssot_events (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            runtime_id TEXT DEFAULT '',
            role_name TEXT DEFAULT '',
            request TEXT NOT NULL,
            evidence TEXT DEFAULT '',
            status TEXT DEFAULT 'ssot',
            verified_time TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 14. Gen Workplace Conversations (Phiên làm việc Owner ↔ Gen)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_conversations (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            model TEXT DEFAULT 'Gemini 3.1 Pro (High)',
            account_profile TEXT DEFAULT 'owner_default',
            is_pinned INTEGER DEFAULT 0,
            active_tab TEXT DEFAULT 'files_repo',
            active_file TEXT DEFAULT 'backend/main.py',
            open_tabs_json TEXT DEFAULT '["backend/main.py"]',
            active_evidence_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Migration columns if table already existed
        for col_name, col_type in [
            ("active_tab", "TEXT DEFAULT 'files_repo'"),
            ("active_file", "TEXT DEFAULT 'backend/main.py'"),
            ("open_tabs_json", "TEXT DEFAULT '[\"backend/main.py\"]'"),
            ("active_evidence_id", "TEXT DEFAULT ''"),
            ("agy_conv_id", "TEXT DEFAULT ''"),
            ("total_tokens", "INTEGER DEFAULT 0")
        ]:
            try:
                cursor.execute(f"ALTER TABLE gen_conversations ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass


        # 15. Gen Workplace Messages (Lịch sử hội thoại có Progressive Compaction)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES gen_conversations(id) ON DELETE CASCADE,
            author TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            model TEXT DEFAULT '',
            note_ids_json TEXT DEFAULT '[]',
            is_compacted INTEGER DEFAULT 0,
            compact_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 16. Gen Scratchpad Notes (Sổ tay tạm thời có Note ID dựng chứng)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_scratchpad_notes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            conversation_id TEXT DEFAULT '',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags_json TEXT DEFAULT '[]',
            evidence_ref TEXT DEFAULT '',
            author TEXT DEFAULT 'Ryan',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 17. Gen Progressive Compact Snapshots (Lịch sử nén ngữ cảnh)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_compact_snapshots (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES gen_conversations(id) ON DELETE CASCADE,
            model_from TEXT NOT NULL,
            model_to TEXT NOT NULL,
            summary TEXT NOT NULL,
            note_ids_json TEXT DEFAULT '[]',
            message_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 18. Gen Session Workspace Files (Quản lý Folder & File theo từng phiên)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_session_files (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES gen_conversations(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL DEFAULT 'PRJ-GEN-WORKPLACE',
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            file_type TEXT NOT NULL DEFAULT 'file',
            size_bytes INTEGER DEFAULT 0,
            source TEXT DEFAULT 'session',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 19. Owner Profiles (Hồ sơ chủ sở hữu tối cao hệ thống - Ryan)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS owner_profiles (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL,
            email TEXT NOT NULL,
            avatar TEXT DEFAULT '👑',
            bio TEXT,
            storage_path TEXT,
            workspace_root TEXT,
            settings_json TEXT DEFAULT '{}',
            is_primary INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Migration columns for exclusive data ownership by Ryan
        for tbl_name, col_name, col_type in [
            ("projects", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
            ("gen_conversations", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
            ("gen_messages", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
            ("gen_scratchpad_notes", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
            ("gen_session_files", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
            ("gen_compact_snapshots", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
            ("tmux_sessions", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
            ("master_ssot", "owner_id", "TEXT DEFAULT 'owner-ryan'"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE {tbl_name} ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass

        # Seed primary Owner Profile for Ryan
        cursor.execute("""
        INSERT OR IGNORE INTO owner_profiles (
            id, username, display_name, role, email, avatar, bio, storage_path, workspace_root, settings_json, is_primary
        ) VALUES (
            'owner-ryan',
            'ryan',
            'Ryan (Owner)',
            'Chủ Sở Hữu & Kiến Trúc Sư Trưởng Tối Cao (System Owner & Sovereign)',
            'owner@genesis.local',
            '👑',
            'Single Source of Truth tối cao và chủ sở hữu độc quyền toàn bộ dữ liệu hệ thống Gen Workplace & Genesis Swarm.',
            ?,
            ?,
            '{"theme":"dark","default_model":"Gemini 3.1 Pro (High)","persona":"executive_assistant","auto_compact":true,"data_ownership":"exclusive_ryan","isolation_level":"strict"}',
            1
        );
        """, (HOME_DIR, str(BASE_DIR / "workspace")))

        # Backfill ownership: all existing data belongs to Ryan
        cursor.execute("UPDATE projects SET owner_id = 'owner-ryan' WHERE owner_id IS NULL OR owner_id = ''")
        cursor.execute("UPDATE gen_conversations SET owner_id = 'owner-ryan' WHERE owner_id IS NULL OR owner_id = ''")
        cursor.execute("UPDATE gen_messages SET owner_id = 'owner-ryan' WHERE owner_id IS NULL OR owner_id = ''")
        cursor.execute("UPDATE gen_scratchpad_notes SET owner_id = 'owner-ryan', author = 'Ryan (Owner)' WHERE owner_id IS NULL OR owner_id = '' OR author = 'Ryan'")
        cursor.execute("UPDATE gen_session_files SET owner_id = 'owner-ryan' WHERE owner_id IS NULL OR owner_id = ''")
        cursor.execute("UPDATE gen_compact_snapshots SET owner_id = 'owner-ryan' WHERE owner_id IS NULL OR owner_id = ''")
        cursor.execute("UPDATE tmux_sessions SET owner_id = 'owner-ryan' WHERE owner_id IS NULL OR owner_id = ''")
        # Sửa dữ liệu hỏng do nút Test switch_google_account gửi {} (account_type = NULL → /api/tmux/sessions 500)
        cursor.execute("UPDATE tmux_sessions SET account_type = 'owner_default' WHERE account_type IS NULL OR TRIM(account_type) = ''")
        cursor.execute("UPDATE master_ssot SET owner_id = 'owner-ryan' WHERE owner_id IS NULL OR owner_id = ''")

        # 20. Gen Session Todos & Interactive Checklists (Kanban DAG chuyên dụng theo phiên)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_session_todos (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES gen_conversations(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL DEFAULT 'PRJ-GEN-WORKPLACE',
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo', -- 'todo', 'in_progress', 'review', 'done'
            priority TEXT DEFAULT 'high', -- 'critical', 'high', 'medium', 'low'
            assigned_agent TEXT DEFAULT 'Gen Core',
            checklist_json TEXT DEFAULT '[]', -- JSON array of {"id": "chk-1", "text": "...", "done": true/false}
            evidence_ref TEXT DEFAULT '',
            order_idx INTEGER DEFAULT 0,
            owner_id TEXT DEFAULT 'owner-ryan',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gen_sess_todos_conv ON gen_session_todos(conversation_id, status);")
        # Mã việc trong Kho Ryan (VIEC-n) gắn với task (Issue #12)
        try:
            cursor.execute("ALTER TABLE gen_session_todos ADD COLUMN viec_ref TEXT DEFAULT '';")
        except Exception:
            pass
        # Khóa claim cho task Kanban phiên: ai đang giữ + lúc khóa (Issue #16)
        for col, col_type in [("claimed_by", "TEXT DEFAULT ''"), ("locked_at", "TEXT DEFAULT ''")]:
            try:
                cursor.execute(f"ALTER TABLE gen_session_todos ADD COLUMN {col} {col_type};")
            except Exception:
                pass
        # Nhật ký ghi đè bằng chứng của task đã done (complete_task force=True, Issue #16)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_evidence_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            task_id TEXT NOT NULL,
            table_name TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            old_evidence_ref TEXT DEFAULT '',
            old_verified_by TEXT DEFAULT '',
            new_evidence_ref TEXT DEFAULT '',
            new_verified_by TEXT DEFAULT '',
            reason TEXT DEFAULT ''
        );
        """)
        # Loại dòng audit + người đang giữ task khi bị đóng thay bằng force (Issue #18)
        for col, col_type in [("action", "TEXT DEFAULT 'override_evidence'"), ("held_by", "TEXT DEFAULT ''")]:
            try:
                cursor.execute(f"ALTER TABLE task_evidence_audit ADD COLUMN {col} {col_type};")
            except Exception:
                pass

        # 21. MCP Agent Tokens & Permissions (Chuẩn bảo mật Gen-hub OAuth / Bearer)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_agent_tokens (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            client TEXT DEFAULT 'Manual Token',
            role TEXT DEFAULT 'agent', -- 'agent', 'admin', 'readonly'
            permissions_json TEXT DEFAULT '["all"]',
            status TEXT DEFAULT 'active', -- 'active', 'revoked', 'expired'
            expires_at TEXT,
            last_used_at TEXT,
            last_ip TEXT DEFAULT '',
            calls_count INTEGER DEFAULT 0,
            owner_id TEXT DEFAULT 'owner-ryan',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 22. MCP System Auth Settings
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_auth_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cursor.execute("INSERT OR IGNORE INTO mcp_auth_settings (key, value) VALUES ('require_auth', '0')")
        # Nhật ký bật / tắt bắt buộc token MCP (#41): ghi cả lần bị từ chối (thiếu token, token không đủ quyền)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_auth_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            action TEXT NOT NULL,
            allowed INTEGER NOT NULL,
            token_id TEXT DEFAULT '',
            token_name TEXT DEFAULT '',
            client_ip TEXT DEFAULT '',
            reason TEXT DEFAULT ''
        );
        """)

        # Seed master sovereign token for Ryan if no tokens exist
        cursor.execute("SELECT count(*) FROM mcp_agent_tokens")
        if cursor.fetchone()[0] == 0:
            seed_tok = f"gw_live_{secrets.token_hex(20)}"
            seed_hash = hashlib.sha256(seed_tok.encode('utf-8')).hexdigest()
            cursor.execute("""
            INSERT INTO mcp_agent_tokens (id, name, token, token_hash, client, role, permissions_json, status, expires_at, owner_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("mcp-tok-master", "Owner Ryan Sovereign Master Token", seed_tok, seed_hash, "Master Control", "admin", json.dumps(["all"]), "active", None, "owner-ryan"))

        # Kết quả gọi agy thật (thành công / 429) để hiển thị quota từ dữ liệu thật (#6)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS quota_probe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            model TEXT DEFAULT '',
            status TEXT NOT NULL,
            reset_at TEXT DEFAULT '',
            checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
            raw TEXT DEFAULT ''
        );
        """)

        # Trạng thái quota của từng hồ sơ agy theo nhóm model (gemini / claude) (#22): exhausted + reset_at (ISO tuyệt đối) khi agy trả 429;
        # last_used_at để chọn hồ sơ dùng ít gần nhất khi tự chuyển tài khoản.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS profile_quota_state (
            profile_id TEXT NOT NULL,
            family TEXT NOT NULL DEFAULT 'gemini',
            exhausted INTEGER DEFAULT 0,
            reset_at TEXT DEFAULT '',
            reset_raw TEXT DEFAULT '',
            last_status TEXT DEFAULT '',
            last_used_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            PRIMARY KEY (profile_id, family)
        );
        """)

        # Nhật ký điều phối tin @vai trong chatroom sang agy thật (#3)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            command TEXT DEFAULT '',
            exit_code INTEGER,
            report_path TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT ''
        );
        """)
        # status: running | done | failed ('' = dòng cũ, suy ra từ exit_code); kind: warroom | tmux; summary: kết quả rút gọn (#9)
        for col, col_type in [("task_id", "TEXT DEFAULT ''"), ("viec_ref", "TEXT DEFAULT ''"), ("channel_id", "TEXT DEFAULT ''"), ("webhook_sent", "INTEGER DEFAULT 0"),
                              ("status", "TEXT DEFAULT ''"), ("kind", "TEXT DEFAULT ''"), ("summary", "TEXT DEFAULT ''"),
                              ("request_msg_id", "INTEGER"), ("reply_msg_id", "INTEGER"),
                              # tự chuyển tài khoản khi hết quota (#22): hồ sơ gán cho vai, hồ sơ chạy thật, lý do, các hồ sơ đã thử (JSON)
                              ("profile_initial", "TEXT DEFAULT ''"), ("profile_used", "TEXT DEFAULT ''"),
                              ("fallback_reason", "TEXT DEFAULT ''"), ("profiles_tried", "TEXT DEFAULT ''"),
                              # tin kết quả đã ghi về phiên (conversation) của task: id gen_messages (tránh ghi 2 lần)
                              ("task_msg_id", "INTEGER"),
                              # worker ngoài (#43, Jules): engine ('' = agy), id phiên bên ngoài, trạng thái bên ngoài, link phiên,
                              # URL PR worker mở, id kế hoạch đã ghi về phiên của task (tránh ghi 2 lần)
                              ("engine", "TEXT DEFAULT ''"), ("ext_session_id", "TEXT DEFAULT ''"), ("ext_state", "TEXT DEFAULT ''"),
                              ("ext_url", "TEXT DEFAULT ''"), ("pr_url", "TEXT DEFAULT ''"), ("ext_plan_id", "TEXT DEFAULT ''"),
                              # chế độ Làm (#45, kind='build'): worktree + nhánh wt/TSK-n, commit SHA mới nhất, kết quả test (JSON),
                              # kết quả push (JSON), link compare GitHub
                              ("worktree_dir", "TEXT DEFAULT ''"), ("build_branch", "TEXT DEFAULT ''"), ("build_commit", "TEXT DEFAULT ''"),
                              ("build_tests", "TEXT DEFAULT ''"), ("build_push", "TEXT DEFAULT ''"), ("compare_url", "TEXT DEFAULT ''")]:
            try:
                cursor.execute(f"ALTER TABLE dispatch_log ADD COLUMN {col} {col_type};")
            except Exception:
                pass
        # Tin trả lời nối về tin yêu cầu (reply_to) + thời điểm đủ ngày giờ (created_at, ISO) cho War Room
        for col, col_type in [("reply_to", "INTEGER"), ("created_at", "TEXT DEFAULT ''")]:
            try:
                cursor.execute(f"ALTER TABLE chat_messages ADD COLUMN {col} {col_type};")
            except Exception:
                pass

        conn.commit()

    migrate_unverified_done_tasks()
    purge_seed_data()

def migrate_unverified_done_tasks():
    """Di trú nhỏ: task 'done' (todos & gen_session_todos) có evidence không kiểm được → 'review' (#4)."""
    changed = 0
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            for table in ("todos", "gen_session_todos"):
                cursor.execute(f"SELECT id, evidence_ref FROM {table} WHERE status = 'done'")
                for r in cursor.fetchall():
                    # legacy=True: không gọi mạng lúc khởi động, giữ luật cũ cho URL PR / file để task done trước đây không bị hạ cấp
                    ok, _, _ = verify_evidence_ref(r["evidence_ref"], legacy=True)
                    if not ok:
                        cursor.execute(f"UPDATE {table} SET status = 'review' WHERE id = ?", (r["id"],))
                        changed += cursor.rowcount
            conn.commit()
    except Exception as e:
        print(f"[migrate] Lỗi khi rà soát bằng chứng task done: {e}")
    if changed:
        print(f"[migrate] Đặt lại {changed} task 'done' thiếu bằng chứng kiểm được về 'review'")
    return changed

def seed_real_project():
    """Chỉ tạo bản ghi project PRJ-GEN-WORKPLACE và cấu hình các vai đang dùng (agent_roles). Không còn seed roadmap/todo/chat/catalog/sự kiện giả."""
    with get_connection() as conn:
        cursor = conn.cursor()
        spec_path = BASE_DIR / "docs" / "SSOT_ORIGINAL_SPEC.md"
        source_text = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
        cursor.execute("""
        INSERT OR IGNORE INTO projects (id, name, repo_path, branch, plan_file, source_text, meta, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ('PRJ-GEN-WORKPLACE', 'gen-workplace', str(BASE_DIR), 'main', 'docs/SSOT_ORIGINAL_SPEC.md', source_text, 'SQLite WAL', 'active'))
        # Bỏ chữ "6 vai" (VIEC-12): chỉ UPDATE đúng giá trị seed cũ, chạy lại không đổi gì
        cursor.execute("UPDATE projects SET meta = 'SQLite WAL' WHERE id = 'PRJ-GEN-WORKPLACE' AND meta = '6 vai · SQLite WAL'")

        roles = [
            ('ROLE-01', 'L', 'Lead Architect', 'Gemini CLI (agy --effort high)', 'Quản trị SSOT, điều phối toàn bộ tiến trình gen-workplace', 'Chịu trách nhiệm bảo toàn SSOT đặc tả gốc, thẩm định evidence từ các role và điều phối live workflow.'),
            ('ROLE-02', 'B', 'Backend & DB Specialist', 'Claude Code CLI', 'Python daemon, SQLite WAL, FTS5 catalog và runner', 'Thực thi API control plane, tối ưu truy vấn FTS5 catalog sub-ms và stream log terminal.'),
            ('ROLE-04', 'D', 'DevOps & Packaging', 'Gemini CLI (agy --agent devops)', 'Systemd service, tmux, cài đặt và cập nhật app trên host', 'Đảm bảo app chạy ổn định trên host Linux (systemd + tmux), script cài đặt/cập nhật 1 lệnh.'),
            ('ROLE-05', 'Q', 'QA Tester', 'Gemini CLI (agy)', 'Kiểm thử cross-platform, test API /api/status, xác thực installer', 'Chạy regression tests, nghiệm thu thanh loading % của installer và báo cáo phản hồi.')
            # ROLE-03 Frontend Specialist và ROLE-06 Security Auditor đã bỏ (29/09): không seed nữa; DB cũ được retire_roles() đánh dấu 'retired'
        ]
        for rl in roles:
            cursor.execute("INSERT OR IGNORE INTO agent_roles (id, project_id, role_key, name, cli_tool, scope, instruction) VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?)", rl)
        conn.commit()

# ---------------------------------------------------------------------------
# Issue #12: bản ghi seed giả của các phiên bản trước — danh sách tường minh (ID + tiêu đề/nội dung đúng như code seed cũ)
# nằm trong backend/seed_purge_list.json. purge_seed_data() chạy cuối init_db(), idempotent, chỉ xóa đúng các bản ghi này.
# ---------------------------------------------------------------------------
SEED_PURGE_LIST_PATH = Path(__file__).resolve().parent / "seed_purge_list.json"

def load_seed_purge_list():
    """Đọc danh sách bản ghi seed cũ cần xóa (backend/seed_purge_list.json). Thiếu/hỏng file → {} (không xóa gì)."""
    try:
        with open(SEED_PURGE_LIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[purge] Không đọc được {SEED_PURGE_LIST_PATH}: {e}")
        return {}

def purge_seed_data():
    """
    Xóa các bản ghi seed giả đã tồn tại trong DB (Issue #12). Chỉ xóa đúng ID + tiêu đề/nội dung như code seed cũ,
    không đụng bản ghi khác. Idempotent. In log '[purge] xóa N bản ghi seed (bảng: ...)' khi có xóa.
    """
    counts = {}
    S = load_seed_purge_list()
    if not S:
        return {"total": 0, "tables": counts}
    SEED_TODOS = S.get("seed_todos", {})
    SEED_CHAT_BOT_AUTHORS = S.get("seed_chat_bot_authors", [])

    def bump(table, n):
        if n:
            counts[table] = counts.get(table, 0) + n

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            # todos trước roadmaps (todos → roadmaps ON DELETE CASCADE: chỉ xóa roadmap khi không còn todo nào)
            for tid, title in SEED_TODOS.items():
                cur.execute("DELETE FROM todos WHERE id = ? AND title = ?", (tid, title))
                bump("todos", cur.rowcount)
            for rid, title in S.get("seed_roadmaps", {}).items():
                cur.execute("DELETE FROM roadmaps WHERE id = ? AND title = ? AND NOT EXISTS (SELECT 1 FROM todos t WHERE t.roadmap_id = roadmaps.id)", (rid, title))
                bump("roadmaps", cur.rowcount)
            for nid, title in S.get("seed_workflow_nodes", {}).items():
                cur.execute("DELETE FROM workflow_nodes WHERE id = ? AND title = ?", (nid, title))
                bump("workflow_nodes", cur.rowcount)
            for rid, role in S.get("seed_runtimes", {}).items():
                cur.execute("DELETE FROM agent_runtimes WHERE id = ? AND role_name = ?", (rid, role))
                bump("agent_runtimes", cur.rowcount)
            for sid, title in S.get("seed_master_ssot", {}).items():
                cur.execute("DELETE FROM master_ssot WHERE id = ? AND title = ?", (sid, title))
                bump("master_ssot", cur.rowcount)
            for cid, title in S.get("seed_catalog", {}).items():
                cur.execute("DELETE FROM catalog_references WHERE id = ? AND title = ?", (cid, title))
                bump("catalog_references", cur.rowcount)
            for eid, req in S.get("seed_ssot_events", {}).items():
                cur.execute("DELETE FROM ssot_events WHERE id = ? AND request = ?", (eid, req))
                bump("ssot_events", cur.rowcount)
            for role, body in S.get("seed_role_memories", []):
                cur.execute("DELETE FROM role_memories WHERE role_name = ? AND body = ?", (role, body))
                bump("role_memories", cur.rowcount)
            for body in S.get("seed_chat_bodies", []):
                cur.execute("DELETE FROM chat_messages WHERE body = ?", (body,))
                bump("chat_messages", cur.rowcount)
            bot_in = ",".join("?" * len(SEED_CHAT_BOT_AUTHORS))
            for prefix in S.get("seed_chat_body_prefixes", []):
                cur.execute(f"DELETE FROM chat_messages WHERE substr(body, 1, ?) = ? AND author IN ({bot_in})",
                            [len(prefix), prefix] + SEED_CHAT_BOT_AUTHORS)
                bump("chat_messages", cur.rowcount)
            for frag in S.get("seed_chat_body_contains", []):
                cur.execute(f"DELETE FROM chat_messages WHERE instr(body, ?) > 0 AND author IN ({bot_in})",
                            [frag] + SEED_CHAT_BOT_AUTHORS)
                bump("chat_messages", cur.rowcount)
            for conv_id in S.get("seed_gen_conversations", []):
                for tbl in ("gen_messages", "gen_compact_snapshots", "gen_session_files", "gen_session_todos", "gen_scratchpad_notes"):
                    cur.execute(f"DELETE FROM {tbl} WHERE conversation_id = ?", (conv_id,))
                    bump(tbl, cur.rowcount)
                cur.execute("DELETE FROM gen_conversations WHERE id = ?", (conv_id,))
                bump("gen_conversations", cur.rowcount)
            cur.execute("DELETE FROM gen_messages WHERE role = 'assistant' AND author = 'Gen Core' AND content = ?", (S.get("seed_gen_greeting", ""),))
            bump("gen_messages", cur.rowcount)
            for title in S.get("seed_session_todo_titles", []):
                cur.execute("DELETE FROM gen_session_todos WHERE id LIKE 'TSK-%' AND title = ?", (title,))
                bump("gen_session_todos", cur.rowcount)
            # Worker đang trỏ tới task seed đã bị xóa → bỏ gán
            seed_ids = list(SEED_TODOS.keys())
            if seed_ids:
                cur.execute(f"UPDATE tmux_sessions SET current_task_id = '' WHERE current_task_id IN ({','.join('?' * len(seed_ids))}) "
                            "AND NOT EXISTS (SELECT 1 FROM todos t WHERE t.id = tmux_sessions.current_task_id)", seed_ids)
                bump("tmux_sessions.current_task_id", cur.rowcount)
            conn.commit()
    except Exception as e:
        print(f"[purge] Lỗi khi xóa dữ liệu seed: {e}")
        return {"total": 0, "tables": counts, "error": str(e)}

    total = sum(counts.values())
    if total:
        print(f"[purge] xóa {total} bản ghi seed (bảng: " + ", ".join(f"{k}={v}" for k, v in counts.items()) + ")")
    return {"total": total, "tables": counts}

def get_full_state(project_id="PRJ-GEN-WORKPLACE"):
    """Lấy toàn bộ trạng thái hệ thống theo đúng định dạng state của WebApp."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()

        # Project
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        p_row = cursor.fetchone()
        if not p_row:
            return None

        live_branch = p_row["branch"]
        try:
            br_res = subprocess.run(["git", "-C", find_repo_path(), "branch", "--show-current"], capture_output=True, text=True, timeout=1.0)
            if br_res.returncode == 0 and br_res.stdout.strip():
                live_branch = br_res.stdout.strip()
        except Exception:
            pass

        project = {
            "id": p_row["id"],
            "name": p_row["name"],
            "repo": p_row["repo_path"],
            "branch": live_branch,
            "plan": p_row["plan_file"]
        }

        # Vai (agent_roles, bỏ vai retired). Các bảng roadmaps/todos/workflow_nodes/agent_runtimes/master_ssot/role_memories/
        # catalog_references/ssot_events GIỮ NGUYÊN trong DB nhưng không còn đọc ở đây (VIEC-12, #36): toàn rỗng mà vẫn gửi mỗi lượt poll.
        cursor.execute("SELECT * FROM agent_roles WHERE project_id = ? AND COALESCE(status, 'active') != 'retired' ORDER BY id ASC", (project_id,))
        roles = [{
            "id": rl["id"],
            "key": rl["role_key"],
            "name": rl["name"],
            "cli": rl["cli_tool"],
            "scope": rl["scope"],
        } for rl in cursor.fetchall()]

        return {
            "project": project,
            "projects": [
                {
                    "name": p_row["name"],
                    "meta": p_row["meta"],
                    "repo": p_row["repo_path"]
                }
            ],
            "roles": roles,
            # Task thật (Kanban phiên, MCP create_kanban_task / claim) cho màn Việc & tiến độ (#24)
            "gen_session_todos": get_all_session_todos(project_id)
        }

def search_catalog_fts(query_str, project_id="PRJ-GEN-WORKPLACE"):
    """Tra cứu siêu tốc ID trong Catalog bằng FTS5 (Full-Text Search)."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        clean_q = "".join(c for c in query_str if c.isalnum() or c in " -_#").strip()
        if not clean_q:
            cursor.execute("SELECT * FROM catalog_references WHERE project_id = ? LIMIT 20", (project_id,))
        else:
            # Dùng FTS5 MATCH
            try:
                cursor.execute("""
                SELECT c.* FROM catalog_references c
                JOIN catalog_fts f ON c.rowid = f.rowid
                WHERE catalog_fts MATCH ? AND c.project_id = ?
                ORDER BY rank
                LIMIT 25;
                """, (f'"{clean_q}"*', project_id))
            except sqlite3.OperationalError:
                # Fallback LIKE nếu chuỗi tìm kiếm có ký tự đặc biệt
                cursor.execute("""
                SELECT * FROM catalog_references
                WHERE project_id = ? AND (
                    id LIKE ? OR title LIKE ? OR description LIKE ? OR category LIKE ?
                ) LIMIT 25;
                """, (project_id, f"%{clean_q}%", f"%{clean_q}%", f"%{clean_q}%", f"%{clean_q}%"))

        rows = cursor.fetchall()
        return [
            {
                "id": r["id"],
                "category": r["category"],
                "title": r["title"],
                "description": r["description"],
                "ref_path": r["ref_path"]
            }
            for r in rows
        ]

# =========================================================================
# OAUTH PROFILES & GOOGLE AUTHENTICATION MANAGER
# =========================================================================

def parse_id_token(id_token):
    """Giải mã payload của Google JWT id_token lấy email, tên, hạn dùng."""
    try:
        parts = id_token.split('.')
        if len(parts) >= 2:
            pad = len(parts[1]) % 4
            if pad:
                parts[1] += '=' * (4 - pad)
            return json.loads(base64.urlsafe_b64decode(parts[1]))
    except Exception:
        pass
    return {}

def get_oauth_profiles():
    """Quét toàn bộ hồ sơ profile Google OAuth trên hệ thống và đối chiếu với các role."""
    base_dir = os.path.join(HOME_DIR, ".agy-profiles")
    candidates = [
        ("owner_default", "👑 Ryan (Owner) - Hồ Sơ Mặc Định", os.path.join(HOME_DIR, ".gemini")),
    ]
    if os.path.exists(base_dir):
        try:
            for entry in sorted(os.listdir(base_dir)):
                p = os.path.join(base_dir, entry)
                if os.path.isdir(p):
                    candidates.append((entry, f"Profile #{entry.replace('profile', '') if 'profile' in entry else entry}", p))
        except Exception:
            pass

    # Lấy danh sách gán role hiện tại từ SQLite
    assigned_map = {}
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, role_name, account_type FROM tmux_sessions")
            for r in cursor.fetchall():
                acc = r["account_type"]
                assigned_map.setdefault(acc, []).append(r["role_name"])
    except Exception:
        pass

    results = []
    now_ts = int(time.time())

    for pid, label, ppath in candidates:
        token_file = os.path.join(ppath, "antigravity-cli", "antigravity-oauth-token")
        is_auth = False
        email = None
        user_name = None
        exp = 0
        has_refresh = False

        if os.path.exists(token_file):
            try:
                with open(token_file, "r") as f:
                    data = json.load(f)
                    has_refresh = bool(data.get("token", {}).get("refresh_token") or data.get("refresh_token"))
                    payload = parse_id_token(data.get("id_token", ""))
                    email = payload.get("email") if isinstance(payload, dict) else None
                    user_name = payload.get("name") if isinstance(payload, dict) else None
                    exp = payload.get("exp", 0) if isinstance(payload, dict) else 0

                    if not email and isinstance(data, dict):
                        acc_tok = (data.get("token") or {}).get("access_token") if isinstance(data.get("token"), dict) else data.get("access_token")
                        if acc_tok:
                            try:
                                import urllib.request
                                req = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo",
                                                             headers={"Authorization": f"Bearer {acc_tok}"})
                                with urllib.request.urlopen(req, timeout=2.0) as u_resp:
                                    u_data = json.loads(u_resp.read().decode("utf-8"))
                                    email = u_data.get("email")
                                    user_name = u_data.get("name")
                            except Exception:
                                pass

                    is_auth = bool(email or data.get("token"))
            except Exception:
                pass

        is_expired = (exp > 0 and exp < now_ts)
        exp_formatted = datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M") if exp > 0 else "Tự động refresh (Refresh Token)"

        is_owner = (pid == "owner_default")
        display_label = label if not email else f"{label} ({email})"
        if is_owner and "Ryan" not in display_label:
            display_label = f"👑 Ryan (Owner) - {email}"

        results.append({
            "id": pid,
            "label": display_label,
            "path": ppath,
            "is_auth": is_auth,
            "email": email,
            "name": "Ryan (Owner)" if is_owner else user_name,
            "is_owner": is_owner,
            "owner_id": "owner-ryan" if is_owner else None,
            "exp": exp,
            "exp_formatted": exp_formatted,
            "is_expired": is_expired,
            "has_refresh": has_refresh,
            "assigned_roles": assigned_map.get(pid, [])
        })
    # Trạng thái hết quota từng hồ sơ (#22) cho màn tài khoản / quota
    for item in results:
        st = get_profile_quota_state(item["id"], "gemini")
        st_c = get_profile_quota_state(item["id"], "claude")
        item.update({"exhausted": st["exhausted"], "reset_at": st["reset_at"], "reset_at_label": st["reset_at_label"],
                     "quota_last_status": st["last_status"], "last_used_at": st["last_used_at"],
                     "claude_exhausted": st_c["exhausted"], "claude_reset_at": st_c["reset_at"]})

    return results

def compute_account_label(account_type, oauth_map=None):
    """Nhãn tài khoản TÍNH từ email thật của profile OAuth: 'profileN (email)' hoặc 'profileN (chưa đăng nhập)'; owner_default dùng tiền tố 'Mặc định'."""
    if oauth_map is None:
        oauth_map = {p["id"]: p for p in get_oauth_profiles()}
    account_type = account_type or "owner_default"
    email = (oauth_map.get(account_type) or {}).get("email")
    prefix = "Mặc định" if account_type == "owner_default" else account_type
    return f"{prefix} ({email})" if email else f"{prefix} (chưa đăng nhập)"

# =========================================================================
# MODEL QUOTA TELEMETRY ENGINE (GEMINI & ANTHROPIC FAMILIES)
# =========================================================================

_LIVE_QUOTA_CACHE = {}
_LIVE_QUOTA_CACHE_TIME = {}
QUOTA_CACHE_TTL = 30.0  # 30 giây cache cho auto-polling để tránh spam Cloud Code API

GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

def refresh_google_oauth_token(profile_id="owner_default"):
    """
    Tự động làm mới OAuth Access Token từ Google OAuth endpoint bằng refresh_token
    khi access token hết hạn. Hỗ trợ cả owner_default và profile1-4.
    """
    profile_id = profile_id or "owner_default"
    target_dir = os.path.join(HOME_DIR, ".gemini") if profile_id == "owner_default" else os.path.join(HOME_DIR, ".agy-profiles", profile_id)
    token_file = os.path.join(target_dir, "antigravity-cli", "antigravity-oauth-token")
    if not os.path.exists(token_file):
        token_file = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "antigravity-oauth-token")
    if not os.path.exists(token_file):
        return None

    try:
        import urllib.request, urllib.parse
        from datetime import datetime, timezone
        with open(token_file, "r") as f:
            data = json.load(f)
        rf_token = data.get("token", {}).get("refresh_token") or data.get("refresh_token")
        if not rf_token:
            return None

        params = {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": rf_token,
        }
        body = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            res = json.loads(resp.read().decode())
        
        new_token = res.get("access_token")
        if new_token:
            expires_in = res.get("expires_in", 3600)
            expiry_str = datetime.fromtimestamp(time.time() + expires_in, timezone.utc).isoformat()
            if "token" in data and isinstance(data["token"], dict):
                data["token"]["access_token"] = new_token
                data["token"]["expiry"] = expiry_str
            else:
                data["access_token"] = new_token
            try:
                with open(token_file, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass
            return new_token
    except Exception as e:
        print(f"[Live Quota] Token refresh error for {profile_id}: {e}")
    return None

def fetch_live_google_quota(profile_id="owner_default", force=False):
    """
    Truy vấn trực tiếp hạn ngạch Quota thời gian thực từ Google Cloud Code API
    (Endpoint nội bộ https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels)
    Tự động refresh token nếu hết hạn, hỗ trợ đa tài khoản (profile1, profile2, profile3, profile4).
    """
    global _LIVE_QUOTA_CACHE, _LIVE_QUOTA_CACHE_TIME
    # account_type NULL/rỗng (vd do lần bấm Test switch_google_account {} trước đây) → coi là owner_default, không lỗi os.path.join(None)
    profile_id = profile_id or "owner_default"
    now = time.time()
    if not force and profile_id in _LIVE_QUOTA_CACHE:
        if now - _LIVE_QUOTA_CACHE_TIME.get(profile_id, 0) < QUOTA_CACHE_TTL:
            return _LIVE_QUOTA_CACHE[profile_id]

    target_dir = os.path.join(HOME_DIR, ".gemini") if profile_id == "owner_default" else os.path.join(HOME_DIR, ".agy-profiles", profile_id)
    token_path = os.path.join(target_dir, "antigravity-cli", "antigravity-oauth-token")
    owner_token_path = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "antigravity-oauth-token")
    
    token = None
    if os.path.exists(token_path):
        try:
            with open(token_path) as f:
                tdata = json.load(f)
            token = tdata.get("token", {}).get("access_token") or tdata.get("access_token")
        except Exception:
            pass

    if not token and os.path.exists(owner_token_path):
        try:
            with open(owner_token_path) as f:
                token = json.load(f).get("token", {}).get("access_token")
        except Exception:
            pass

    import urllib.request
    from datetime import datetime, timezone, timedelta

    def _do_query(t):
        # Ưu tiên lấy bản tóm tắt phân bổ 2 tầng (5h limit & weekly limit) từ Cloud Code API
        try:
            req_sum = urllib.request.Request(
                "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
                data=b"{}",
                headers={
                    "Authorization": f"Bearer {t}",
                    "Content-Type": "application/json",
                    "User-Agent": "Antigravity"
                }
            )
            with urllib.request.urlopen(req_sum, timeout=5.0) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            # Fallback sang fetchAvailableModels nếu retrieveUserQuotaSummary không khả dụng
            req = urllib.request.Request(
                "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
                data=b"{}",
                headers={
                    "Authorization": f"Bearer {t}",
                    "Content-Type": "application/json",
                    "User-Agent": "Antigravity"
                }
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return json.loads(resp.read().decode())

    data = None
    if token:
        try:
            data = _do_query(token)
        except Exception:
            data = None

    if data is None:
        new_tok = refresh_google_oauth_token(profile_id)
        if new_tok:
            try:
                data = _do_query(new_tok)
            except Exception:
                data = None

    if data is None and profile_id != "owner_default":
        new_owner_tok = refresh_google_oauth_token("owner_default")
        if new_owner_tok:
            try:
                data = _do_query(new_owner_tok)
            except Exception:
                data = None

    if data is None:
        return _LIVE_QUOTA_CACHE.get(profile_id)

    try:
        def _format_vn_reset(iso_time):
            if not iso_time:
                return ""
            try:
                dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
                vn = dt.astimezone(timezone(timedelta(hours=7)))
                time_str = vn.strftime("%H:%M ngày %d/%m")
                now_utc = datetime.now(timezone.utc)
                diff = dt - now_utc
                if diff.total_seconds() > 0:
                    days = diff.days
                    hrs = int((diff.total_seconds() % 86400) // 3600)
                    mins = int((diff.total_seconds() % 3600) // 60)
                    if days > 0:
                        rem_str = f"còn ~{days}d {hrs}h"
                    elif hrs > 0:
                        rem_str = f"còn ~{hrs}h{mins:02d}m"
                    else:
                        rem_str = f"còn ~{mins}m"
                    return f"{time_str} ({rem_str})"
                else:
                    return f"{time_str} (đang hồi phục)"
            except Exception:
                return iso_time

        # Kiểm tra xem có cấu trúc groups từ retrieveUserQuotaSummary hay không
        if "groups" in data:
            g_5h_pct = 100
            g_5h_reset = ""
            g_weekly_pct = 100
            g_weekly_reset = ""
            
            c_5h_pct = 100
            c_5h_reset = ""
            c_5h_disabled = False
            c_weekly_pct = 0
            c_weekly_reset = ""

            for grp in data.get("groups", []):
                disp_name = (grp.get("displayName") or "").lower()
                if "gemini" in disp_name:
                    for b in grp.get("buckets", []):
                        w = b.get("window")
                        pct = int(round((b.get("remainingFraction") or 0) * 100))
                        rst = b.get("resetTime", "")
                        if w == "5h":
                            g_5h_pct = pct
                            g_5h_reset = rst
                        elif w == "weekly":
                            g_weekly_pct = pct
                            g_weekly_reset = rst
                elif "claude" in disp_name or "gpt" in disp_name:
                    for b in grp.get("buckets", []):
                        w = b.get("window")
                        pct = int(round((b.get("remainingFraction") or 0) * 100))
                        rst = b.get("resetTime", "")
                        dis = b.get("disabled", False)
                        if w == "5h":
                            c_5h_pct = pct
                            c_5h_reset = rst
                            c_5h_disabled = dis
                        elif w == "weekly":
                            c_weekly_pct = pct
                            c_weekly_reset = rst

            g_5h_reset_vn = _format_vn_reset(g_5h_reset)
            g_weekly_reset_vn = _format_vn_reset(g_weekly_reset)
            c_5h_reset_vn = _format_vn_reset(c_5h_reset)
            c_weekly_reset_vn = _format_vn_reset(c_weekly_reset)

            gemini_quota = {
                "family": "Google Gemini",
                "model": "Gemini 3.8 Flash (High)",
                "alt_model": "Gemini 3.1 Pro (High)",
                "status": "ready" if g_5h_pct > 0 and g_weekly_pct > 0 else "rate_limited",
                "status_label": f"5h: {g_5h_pct}% · Tuần: {g_weekly_pct}%",
                "percent": g_5h_pct,
                "percent_5h": g_5h_pct,
                "percent_weekly": g_weekly_pct,
                "reset_5h": g_5h_reset_vn,
                "reset_weekly": g_weekly_reset_vn,
                "reset_time": g_5h_reset_vn or "Cửa sổ 5h",
                "tier": "Cloud Code VIP Entitlement",
                "color": "#38bdf8" if g_5h_pct > 0 else "#ef4444",
                "detail": f"Cửa sổ 5h: {g_5h_pct}% (Hồi {g_5h_reset_vn}) · Tuần: {g_weekly_pct}% (Hồi {g_weekly_reset_vn})"
            }

            if c_weekly_pct == 0:
                c_label = f"0% (Hết quota tuần · Hồi {c_weekly_reset_vn})"
                c_color = "#ef4444"
                c_status = "rate_limited"
                c_desc_5h = "Đang khóa (sẽ tự động mở khi hạn mức tuần hồi)"
            else:
                c_label = f"5h: {c_5h_pct}% · Tuần: {c_weekly_pct}%"
                c_color = "#f59e0b"
                c_status = "ready"
                c_desc_5h = f"Cửa sổ 5h: {c_5h_pct}% (Hồi {c_5h_reset_vn})"

            anthropic_quota = {
                "family": "Anthropic Claude",
                "model": "Claude Sonnet 4.6 (Thinking)",
                "alt_model": "Claude Opus 4.6 (Thinking)",
                "status": c_status,
                "status_label": c_label,
                "percent": c_weekly_pct if c_weekly_pct == 0 else c_5h_pct,
                "percent_5h": c_5h_pct,
                "percent_weekly": c_weekly_pct,
                "disabled_5h": c_5h_disabled,
                "desc_5h": c_desc_5h,
                "reset_5h": c_5h_reset_vn,
                "reset_weekly": c_weekly_reset_vn,
                "reset_time": c_weekly_reset_vn or "Cửa sổ tuần",
                "tier": "Sonnet 4.6 Tier 4 Entitlement",
                "color": c_color,
                "detail": f"Hạn ngạch tuần: {c_weekly_pct}% (Hồi {c_weekly_reset_vn}) · Cửa sổ 5h: {c_5h_pct}%"
            }
        else:
            # Fallback nếu API trả về dạng models đơn giản
            models = data.get("models", {})
            g_model = models.get("gemini-3.8-flash-tiered") or models.get("gemini-3.1-pro-high") or {}
            g_q = g_model.get("quotaInfo") or {}
            g_fraction = g_q.get("remainingFraction")
            g_reset = g_q.get("resetTime", "")
            
            c_model = models.get("claude-sonnet-4-6") or models.get("claude-opus-4-6-thinking") or {}
            c_q = c_model.get("quotaInfo") or {}
            c_fraction = c_q.get("remainingFraction")
            c_reset = c_q.get("resetTime", "")
            
            g_pct = int(round(g_fraction * 100)) if g_fraction is not None else 100
            c_pct = int(round(c_fraction * 100)) if c_fraction is not None else (0 if c_reset else 100)
            
            g_reset_vn = _format_vn_reset(g_reset)
            c_reset_vn = _format_vn_reset(c_reset)
            
            gemini_quota = {
                "family": "Google Gemini",
                "model": "Gemini 3.8 Flash (High)",
                "alt_model": "Gemini 3.1 Pro (High)",
                "status": "ready" if g_pct > 0 else "rate_limited",
                "status_label": f"Khả dụng {g_pct}% (Sẵn sàng)",
                "percent": g_pct,
                "percent_5h": g_pct,
                "percent_weekly": 100,
                "reset_5h": g_reset_vn,
                "reset_weekly": "",
                "reset_time": g_reset_vn or "Cửa sổ 5h",
                "tier": "Cloud Code VIP Entitlement",
                "color": "#38bdf8" if g_pct > 0 else "#ef4444",
                "detail": f"Hạn ngạch thực tế: {g_pct}% · Hồi lúc {g_reset_vn}" if g_reset_vn else f"Hạn ngạch thực tế: {g_pct}%"
            }
            
            anthropic_quota = {
                "family": "Anthropic Claude",
                "model": "Claude Sonnet 4.6 (Thinking)",
                "alt_model": "Claude Opus 4.6 (Thinking)",
                "status": "ready" if c_pct > 0 else "rate_limited",
                "status_label": f"Khả dụng {c_pct}%" if c_pct > 0 else f"0% (Hồi lúc {c_reset_vn})",
                "percent": c_pct,
                "percent_5h": c_pct,
                "percent_weekly": c_pct,
                "disabled_5h": False,
                "desc_5h": "",
                "reset_5h": c_reset_vn,
                "reset_weekly": c_reset_vn,
                "reset_time": c_reset_vn or "Cửa sổ tuần",
                "tier": "Sonnet 4.6 Tier 4 Entitlement",
                "color": "#f59e0b" if c_pct > 0 else "#ef4444",
                "detail": f"Hạn ngạch: {c_pct}% · Hồi lúc {c_reset_vn}"
            }
        
        result = (gemini_quota, anthropic_quota)
        _LIVE_QUOTA_CACHE[profile_id] = result
        _LIVE_QUOTA_CACHE_TIME[profile_id] = now
        return result
    except Exception as e:
        print(f"[Live Quota] Error processing quota models: {e}")
        return _LIVE_QUOTA_CACHE.get(profile_id)

def live_quota_response(profile_id="owner_default", force=True):
    """
    Dữ liệu cho GET /api/quota/live và MCP get_live_quota. Có số từ Cloud Code API → source cloudcode_api_live; không có → quota
    từ lần gọi agy thật (get_quota_telemetry). Cả 2 nhánh đều gắn exhausted / reset_at / reset_at_label từ profile_quota_state
    (trước đây nhánh Cloud Code trả gemini.exhausted = null).
    """
    profile_id = profile_id or "owner_default"
    live = fetch_live_google_quota(profile_id, force=force)
    if live:
        g_q, a_q = dict(live[0]), dict(live[1])
        source = "cloudcode_api_live"
        for q, fam in ((g_q, "gemini"), (a_q, "claude")):
            st = get_profile_quota_state(profile_id, fam)
            q.update({"profile_id": profile_id, "exhausted": st["exhausted"], "reset_at": st["reset_at"],
                      "reset_at_label": st["reset_at_label"]})
    else:
        g_q, a_q = get_quota_telemetry(profile_id)
        source = "agy_probe_or_unknown"
    return {"ok": True, "source": source, "gemini": g_q, "claude": a_q, "quota_state": get_profile_quota_state(profile_id)}

QUOTA_PROBE_MAX_AGE_SEC = 6 * 3600

def _agy_bin():
    """Đường dẫn CLI agy; ghi đè bằng GW_AGY_BIN (script giả) để test không cần agy thật."""
    return os.environ.get("GW_AGY_BIN") or "agy"

def _profile_dir(profile_id):
    """Thư mục hồ sơ agy: ~/.gemini cho owner_default, ~/.agy-profiles/<id> cho profile khác."""
    if not profile_id or profile_id == "owner_default":
        return os.path.join(HOME_DIR, ".gemini")
    return os.path.join(HOME_DIR, ".agy-profiles", profile_id)

def _agy_env(p_dir):
    """Biến môi trường chạy agy với hồ sơ p_dir (ANTIGRAVITY_APP_DATA_DIR cho profile phụ)."""
    env = {**os.environ, "HOME": HOME_DIR, "PATH": f"/usr/local/bin:/usr/bin:/bin:{HOME_DIR}/.local/bin:" + os.environ.get("PATH", "")}
    if p_dir != os.path.join(HOME_DIR, ".gemini") and os.path.exists(os.path.join(p_dir, "antigravity-cli")):
        env["ANTIGRAVITY_APP_DATA_DIR"] = os.path.join(p_dir, "antigravity-cli")
    return env

AGY_QUOTA_ONLY_MAX_LEN = 600

def classify_agy_result(returncode, output):
    """Phân loại kết quả 1 lần gọi agy: ('ok', ''), ('rate_limited', reset) khi 429/RESOURCE_EXHAUSTED, ('error', '') còn lại."""
    out = output or ""
    if returncode == 0:
        # agy đôi khi thoát 0 mà chỉ in lỗi hết quota: output ngắn + cụm lỗi rõ ràng → vẫn là hết quota (#22).
        # Câu trả lời thật (dài) có nhắc tới các cụm này không bị đánh nhầm.
        if len(out.strip()) <= AGY_QUOTA_ONLY_MAX_LEN and re.search(r"RESOURCE_EXHAUSTED|Individual quota reached", out):
            m = re.search(r"Resets? (?:in|at) ([^\n\"]{1,40})", out)
            return "rate_limited", (m.group(1).strip() if m else "")
        return "ok", ""
    if re.search(r"RESOURCE_EXHAUSTED|Individual quota reached|\b429\b|quota (exceeded|reached|exhausted)", out, re.IGNORECASE):
        m = re.search(r"Resets? (?:in|at) ([^\n\"]{1,40})", out)
        return "rate_limited", (m.group(1).strip() if m else "")
    return "error", ""

def record_quota_probe(profile_id, model, status, reset_at="", raw=""):
    """Ghi 1 dòng kết quả gọi agy thật (ok / rate_limited / error / timeout) vào bảng quota_probe (#6)."""
    try:
        with get_connection() as conn:
            conn.execute("INSERT INTO quota_probe (profile_id, model, status, reset_at, raw) VALUES (?, ?, ?, ?, ?)",
                         (profile_id or "owner_default", model or "", status, reset_at or "", (raw or "")[-2000:]))
            conn.commit()
    except Exception as e:
        print(f"[quota_probe] Không ghi được: {e}")

def record_quota_probe_from_result(profile_id, model, res):
    """Ghi quota_probe từ CompletedProcess của agy (dùng chung cho runner chat, probe và dispatch)."""
    output = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    return record_quota_probe_output(profile_id, model, res.returncode, output)

def record_quota_probe_output(profile_id, model, returncode, output):
    """Phân loại output agy (returncode + text), ghi quota_probe và trạng thái hồ sơ; trả (status, reset_raw)."""
    status, reset_at = classify_agy_result(returncode, output)
    record_quota_probe(profile_id, model, status, reset_at, output)
    # Trạng thái hồ sơ (#22): 429 → exhausted tới reset_at; OK → hết exhausted; mọi lần → last_used_at
    family = quota_family(model)
    if status == "rate_limited":
        mark_profile_exhausted(profile_id, reset_at, family)
    else:
        mark_profile_used(profile_id, status, family)
    return status, reset_at

# ---------------------------------------------------------------------------
# Tự chuyển tài khoản agy khi hồ sơ hết quota (Issue #22)
# ---------------------------------------------------------------------------
# Không đọc được giờ hồi ("Resets in …") → coi như hết quota trong khoảng này rồi cho thử lại
QUOTA_UNKNOWN_RESET_SEC = int(os.environ.get("GW_QUOTA_UNKNOWN_RESET_SEC", "3600") or 3600)
_RESET_PART_RE = re.compile(r"(\d+)\s*(d(?:ays?)?|h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)(?![a-z])", re.IGNORECASE)


def quota_family(model):
    """Nhóm quota của model agy: 'claude' (claude/sonnet/opus) hoặc 'gemini' (còn lại, gồm 'default' của dispatch)."""
    m = (model or "").lower()
    return "claude" if any(k in m for k in ("claude", "sonnet", "opus")) else "gemini"


def parse_quota_reset(reset_raw, now=None):
    """'76h11m' / '1h5m' / '45m' / '2d3h' / '30s' → datetime tuyệt đối (now + khoảng); chuỗi ISO → datetime đó; không đọc được → None."""
    raw = (reset_raw or "").strip().rstrip(".")
    if not raw:
        return None
    now = now or datetime.now().astimezone()
    parts = _RESET_PART_RE.findall(raw)
    if parts:
        secs = 0
        for num, unit in parts:
            u = unit[0].lower()
            secs += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u]
        return now + timedelta(seconds=secs)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.astimezone()
    except Exception:
        return None


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.astimezone()
    except Exception:
        return None


def format_reset_time(iso_value):
    """ISO → 'HH:MM dd/mm/yyyy' giờ máy chủ; rỗng / hỏng → 'chưa rõ'."""
    dt = _parse_iso(iso_value)
    return dt.astimezone().strftime("%H:%M %d/%m/%Y") if dt else "chưa rõ"


def _upsert_profile_state(profile_id, family="gemini", **fields):
    profile_id = profile_id or "owner_default"
    fields["updated_at"] = _now_iso()
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    sets = ", ".join(f"{k} = excluded.{k}" for k in fields)
    try:
        with get_connection() as conn:
            conn.execute(f"INSERT INTO profile_quota_state (profile_id, family, {cols}) VALUES (?, ?, {marks}) "
                         f"ON CONFLICT(profile_id, family) DO UPDATE SET {sets}", (profile_id, family or "gemini", *fields.values()))
            conn.commit()
    except Exception as e:
        print(f"[quota_state] Không ghi được {profile_id}: {e}")


def mark_profile_exhausted(profile_id, reset_raw="", family="gemini"):
    """Đánh dấu hồ sơ hết quota tới reset_at (tính từ 'Resets in XhYm'; không rõ → +QUOTA_UNKNOWN_RESET_SEC). Trả reset_at ISO."""
    now = datetime.now().astimezone()
    reset_dt = parse_quota_reset(reset_raw, now) or (now + timedelta(seconds=QUOTA_UNKNOWN_RESET_SEC))
    reset_iso = reset_dt.isoformat(timespec="seconds")
    _upsert_profile_state(profile_id, family, exhausted=1, reset_at=reset_iso, reset_raw=reset_raw or "",
                          last_status="rate_limited", last_used_at=now.isoformat(timespec="seconds"))
    return reset_iso


def mark_profile_used(profile_id, status, family="gemini"):
    """Ghi lần dùng hồ sơ; chạy OK → xóa trạng thái exhausted."""
    now = _now_iso()
    if status == "ok":
        _upsert_profile_state(profile_id, family, exhausted=0, reset_at="", reset_raw="", last_status=status, last_used_at=now)
    else:
        _upsert_profile_state(profile_id, family, last_status=status or "", last_used_at=now)


def get_profile_quota_state(profile_id, family="gemini"):
    """{profile_id, family, exhausted (đã tính: chỉ true khi chưa tới reset_at), reset_at, reset_at_label, reset_raw, last_status, last_used_at}."""
    profile_id = profile_id or "owner_default"
    family = family or "gemini"
    row = None
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM profile_quota_state WHERE profile_id = ? AND family = ?", (profile_id, family)).fetchone()
    except Exception:
        row = None
    st = {"profile_id": profile_id, "family": family, "exhausted": False, "reset_at": "", "reset_at_label": "", "reset_raw": "",
          "last_status": "", "last_used_at": ""}
    if not row:
        return st
    st.update({"reset_raw": row["reset_raw"] or "", "last_status": row["last_status"] or "", "last_used_at": row["last_used_at"] or ""})
    reset_dt = _parse_iso(row["reset_at"])
    if row["exhausted"] and reset_dt and reset_dt > datetime.now().astimezone():
        st.update({"exhausted": True, "reset_at": row["reset_at"], "reset_at_label": format_reset_time(row["reset_at"])})
    return st


def available_agy_profiles(exclude=(), oauth_profiles=None, family="gemini"):
    """
    Hồ sơ agy dùng được để chạy thay: đã đăng nhập OAuth, không exhausted (hoặc đã qua reset_at), không nằm trong exclude.
    Thứ tự: lần gần nhất không lỗi (ok / chưa dùng) trước, rồi dùng ít gần nhất (LRU; chưa dùng lần nào đứng đầu).
    Trả [(profile_id, p_dir)].
    """
    exclude = set(exclude or ())
    profs = oauth_profiles if oauth_profiles is not None else get_oauth_profiles()
    ranked = []
    for p in profs:
        pid = p.get("id")
        if not pid or pid in exclude or not p.get("is_auth"):
            continue
        st = get_profile_quota_state(pid, family)
        if st["exhausted"]:
            continue
        penalty = 1 if st["last_status"] in ("error", "timeout") else 0
        ranked.append(((penalty, st["last_used_at"] or "", pid), pid, p.get("path") or _profile_dir(pid)))
    ranked.sort(key=lambda x: x[0])
    return [(pid, path) for _, pid, path in ranked]


def earliest_quota_reset(profile_ids, family="gemini"):
    """(reset_at ISO sớm nhất, profile_id) trong các hồ sơ đang exhausted; không có → ('', '')."""
    best = ("", "")
    best_dt = None
    for pid in dict.fromkeys(profile_ids or ()):
        st = get_profile_quota_state(pid, family)
        dt = _parse_iso(st["reset_at"]) if st["exhausted"] else None
        if dt and (best_dt is None or dt < best_dt):
            best_dt, best = dt, (st["reset_at"], pid)
    return best


def run_agy_with_quota_fallback(primary_id, primary_dir, attempt, family="gemini"):
    """
    Chạy 1 lệnh agy với hồ sơ của vai; hết quota → tự chạy lại với hồ sơ khác còn quota (#22). Fallback chỉ cho lần chạy này,
    không đổi gán tài khoản của vai.
      attempt(profile_id, p_dir) → (status, reset_raw, payload); status như classify_agy_result ('ok' | 'rate_limited' | 'error' | 'timeout').
    - Hồ sơ gán cho vai đang exhausted, chưa tới reset_at → bỏ qua luôn (không tốn 1 lần gọi hỏng).
    - Chỉ chuyển hồ sơ khi hết quota; lỗi khác dừng lại, trả lỗi đó. Tối đa N lần = số hồ sơ khả dụng.
    Trả {status, payload, profile_initial, profile_used, fallback, all_exhausted, earliest_reset, earliest_profile, attempts, note}.
    """
    primary_id = primary_id or "owner_default"
    attempts = []
    tried = []
    last = ("", None, "")          # (status, payload, profile_id)

    def _run(pid, pdir):
        if pid != primary_id:
            copy_agy_allow_rules(primary_dir, pdir)   # giữ allow-rule của vai trên hồ sơ mới trước khi chạy
        status, reset_raw, payload = attempt(pid, pdir)
        tried.append(pid)
        st = get_profile_quota_state(pid, family)
        if status == "rate_limited" and not st["exhausted"]:
            mark_profile_exhausted(pid, reset_raw, family)
            st = get_profile_quota_state(pid, family)
        attempts.append({"profile": pid, "status": status, "reset_at": st["reset_at"] if status == "rate_limited" else ""})
        return status, payload

    pst = get_profile_quota_state(primary_id, family)
    if pst["exhausted"]:
        tried.append(primary_id)
        attempts.append({"profile": primary_id, "status": "skipped_exhausted", "reset_at": pst["reset_at"]})
    else:
        status, payload = _run(primary_id, primary_dir)
        last = (status, payload, primary_id)
    oauth = None
    while last[0] in ("", "rate_limited"):
        if oauth is None:
            oauth = get_oauth_profiles()
        cands = available_agy_profiles(exclude=tried, oauth_profiles=oauth, family=family)
        if not cands:
            break
        pid, pdir = cands[0]
        status, payload = _run(pid, pdir)
        last = (status, payload, pid)

    status, payload, used = last
    all_exhausted = status in ("", "rate_limited")
    out_of_quota = [a for a in attempts if a["status"] in ("rate_limited", "skipped_exhausted")]

    def _desc(a):
        return f"{a['profile']} (hết quota, có lại lúc {format_reset_time(a['reset_at'])}" + \
               ("; bỏ qua, không gọi" if a["status"] == "skipped_exhausted" else "") + ")"

    earliest, earliest_pid = "", ""
    if all_exhausted:
        auth_ids = [p["id"] for p in (oauth or get_oauth_profiles()) if p.get("is_auth")]
        earliest, earliest_pid = earliest_quota_reset(auth_ids + tried, family)
        note = (f"tất cả tài khoản hết quota, sớm nhất có lại lúc {format_reset_time(earliest)}"
                + (f" ({earliest_pid})" if earliest_pid else "")
                + (f"; đã thử: {', '.join(_desc(a) for a in out_of_quota)}" if out_of_quota else ""))
    elif used != primary_id:
        first = out_of_quota[0]
        others = out_of_quota[1:]
        note = f"đã chuyển từ {_desc(first)} sang {used}" + (f"; cũng hết quota: {', '.join(_desc(a) for a in others)}" if others else "")
    else:
        note = ""
    return {"status": status or "rate_limited", "payload": payload, "profile_initial": primary_id, "profile_used": used or "",
            "fallback": bool(used) and used != primary_id, "all_exhausted": all_exhausted,
            "earliest_reset": earliest, "earliest_profile": earliest_pid, "attempts": attempts,
            "profiles_tried": list(tried), "note": note}


def select_agy_profile_for_run(primary_id, primary_dir, tried=(), family="gemini"):
    """
    Chọn hồ sơ cho lần chạy không chờ kết quả tại chỗ (giao task qua tmux) (#22): hồ sơ gán cho vai nếu chưa exhausted và chưa thử,
    không thì hồ sơ khả dụng tốt nhất. Trả {profile_id, p_dir, note, all_exhausted, earliest_reset}; hết hồ sơ → profile_id None.
    """
    primary_id = primary_id or "owner_default"
    tried = list(dict.fromkeys(tried or ()))
    pst = get_profile_quota_state(primary_id, family)
    if primary_id not in tried and not pst["exhausted"]:
        return {"profile_id": primary_id, "p_dir": primary_dir, "note": "", "all_exhausted": False, "earliest_reset": ""}
    oauth = get_oauth_profiles()
    exclude = set(tried) | {primary_id}
    cands = available_agy_profiles(exclude=exclude, oauth_profiles=oauth, family=family)
    out_of_quota = [pid for pid in dict.fromkeys([primary_id] + tried) if get_profile_quota_state(pid, family)["exhausted"]]
    desc = ", ".join(f"{pid} (hết quota, có lại lúc {get_profile_quota_state(pid, family)['reset_at_label']}"
                     + ("" if pid in tried else "; bỏ qua, không gọi") + ")" for pid in out_of_quota)
    if cands:
        pid, pdir = cands[0]
        return {"profile_id": pid, "p_dir": pdir, "note": f"đã chuyển từ {desc or primary_id} sang {pid}",
                "all_exhausted": False, "earliest_reset": ""}
    earliest, epid = earliest_quota_reset([p["id"] for p in oauth if p.get("is_auth")] + [primary_id] + tried, family)
    note = f"tất cả tài khoản hết quota, sớm nhất có lại lúc {format_reset_time(earliest)}" + (f" ({epid})" if epid else "")
    return {"profile_id": None, "p_dir": None, "note": note, "all_exhausted": True, "earliest_reset": earliest}

def get_latest_quota_probe(profile_id, family="gemini", max_age_sec=QUOTA_PROBE_MAX_AGE_SEC):
    """Dòng quota_probe mới nhất (< max_age_sec giây) của profile; family 'claude' lấy model claude/sonnet/opus, 'gemini' lấy còn lại."""
    try:
        with get_connection() as conn:
            rows = conn.execute("""
            SELECT id, profile_id, model, status, reset_at, checked_at, raw FROM quota_probe
            WHERE profile_id = ? AND (strftime('%s', 'now') - strftime('%s', checked_at)) < ?
            ORDER BY id DESC LIMIT 30
            """, (profile_id or "owner_default", int(max_age_sec))).fetchall()
    except Exception:
        return None
    for r in rows:
        m = (r["model"] or "").lower()
        is_claude = any(k in m for k in ("claude", "sonnet", "opus"))
        if (family == "claude") != is_claude:
            continue
        return dict(r)
    return None

def _quota_from_probe(probe, live, base):
    """Ghép quota hiển thị: ưu tiên dòng probe rate_limited < 6h, rồi số liệu live Cloud Code, rồi probe ok, cuối cùng 'unknown' không có %."""
    q = dict(live) if live else dict(base)
    q.setdefault("percent", None)
    q.setdefault("reset_time", "")
    if probe and probe["status"] == "rate_limited":
        reset = probe.get("reset_at") or ""
        q.update({
            "status": "rate_limited",
            "status_label": f"429 · hồi {reset or 'chưa rõ'}",
            "percent": 0,
            "reset_time": reset or q.get("reset_time") or "",
            "color": "#ef4444",
            "detail": f"Lệnh agy lúc {probe['checked_at']} bị 429 (RESOURCE_EXHAUSTED). " + (probe.get("raw") or "")[-200:],
            "source": "agy_probe",
            "probe_checked_at": probe["checked_at"],
        })
        return q
    if live:
        q["source"] = "cloudcode_api_live"
        if probe:
            q["probe_checked_at"] = probe["checked_at"]
            q["probe_status"] = probe["status"]
        return q
    if probe and probe["status"] == "ok":
        q.update({
            "status": "ready",
            "status_label": f"Gọi thật OK lúc {probe['checked_at']}",
            "percent": None,
            "color": "#38bdf8",
            "detail": f"Lệnh agy ({probe.get('model') or 'mặc định'}) chạy thành công lúc {probe['checked_at']}; chưa có số % từ Cloud Code API.",
            "source": "agy_probe",
            "probe_checked_at": probe["checked_at"],
        })
        return q
    if probe:
        q.update({
            "status": "unknown",
            "status_label": f"Lỗi gọi agy ({probe['status']}) lúc {probe['checked_at']}",
            "percent": None,
            "color": "#f59e0b",
            "detail": (probe.get("raw") or "")[-300:] or "Không có output.",
            "source": "agy_probe",
            "probe_checked_at": probe["checked_at"],
        })
        return q
    q.update({
        "status": "unknown",
        "status_label": "Chưa có dữ liệu gọi thật",
        "percent": None,
        "color": "#6b7280",
        "detail": "Chưa có lần gọi agy nào trong 6 giờ qua. Bấm kiểm tra quota (POST /api/quota/probe) để chạy lệnh agy tối thiểu.",
        "source": "none",
    })
    return q

def get_quota_telemetry(profile_id, email=""):
    """
    Quota hiển thị cho 2 nhóm model (Gemini / Claude) từ dữ liệu THẬT (#6):
    ưu tiên dòng quota_probe < 6h (rate_limited → 429 + giờ hồi), rồi Cloud Code API live,
    không có gì → status 'unknown', không có %. Giữ nguyên các key status/status_label/percent/reset_time/detail.
    """
    live = fetch_live_google_quota(profile_id)
    live_g, live_c = live if live else (None, None)
    base_g = {
        "family": "Google Gemini",
        "model": "Gemini 3.8 Flash (High)",
        "alt_model": "Gemini 3.1 Pro (High)",
        "tier": "Cloud Code / AI Studio",
        "reset_time": "",
    }
    base_c = {
        "family": "Anthropic Claude",
        "model": "Claude Sonnet 4.6 (Thinking)",
        "alt_model": "Claude Opus 4.6 (Thinking)",
        "tier": "Sonnet 4.6 Entitlement",
        "reset_time": "",
    }
    gemini_quota = _quota_from_probe(get_latest_quota_probe(profile_id, "gemini"), live_g, base_g)
    anthropic_quota = _quota_from_probe(get_latest_quota_probe(profile_id, "claude"), live_c, base_c)
    for q, fam in ((gemini_quota, "gemini"), (anthropic_quota, "claude")):
        st = get_profile_quota_state(profile_id, fam)
        q.update({"profile_id": profile_id or "owner_default", "exhausted": st["exhausted"],
                  "reset_at": st["reset_at"], "reset_at_label": st["reset_at_label"]})
    return gemini_quota, anthropic_quota

def probe_quota(profile_id="owner_default", timeout=60):
    """Chạy lệnh agy tối thiểu (--gemini_dir=<dir> --mode plan -p 'ping') với hồ sơ profile_id, ghi quota_probe và trả quota mới (#6)."""
    p_dir = _profile_dir(profile_id)
    cmd = [_agy_bin(), f"--gemini_dir={p_dir}", "--mode", "plan", "-p", "ping"]
    started = time.time()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(BASE_DIR), env=_agy_env(p_dir))
        output = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        status, reset_at = record_quota_probe_from_result(profile_id, "default", res)
        code = res.returncode
    except subprocess.TimeoutExpired:
        status, reset_at, code = "timeout", "", -1
        output = f"agy không phản hồi sau {timeout}s"
        record_quota_probe(profile_id, "default", status, "", output)
    except Exception as e:
        status, reset_at, code = "error", "", -1
        output = f"Không chạy được agy ({_agy_bin()}): {e}"
        record_quota_probe(profile_id, "default", status, "", output)
    g_q, a_q = get_quota_telemetry(profile_id)
    return {
        "ok": status == "ok",
        "profile_id": profile_id,
        "status": status,
        "reset_at": reset_at,
        "exit_code": code,
        "elapsed_sec": round(time.time() - started, 1),
        "command": " ".join(cmd),
        "output": output[-1500:],
        "gemini": g_q,
        "claude": a_q,
    }

# =========================================================================
# REAL TMUX SWARM ENGINE (4 INTERACTIVE PROCESSES & SHARED CONTEXT)
# =========================================================================

SWARM_DEFAULT_CONFIG = [
    {
        "id": "gw-lead-agy",
        "role_name": "Lead Architect",
        "cli_tool": "Gemini CLI (agy --effort high)",
        "account_type": "owner_default",
        "conv_id": "conv-lead-architect",
        "gemini_model": "Gemini 3.8 Flash (High)",
        "anthropic_model": "Claude Sonnet 4.6 (Thinking)",
        "allowed_paths": ["docs/**", "workspace/roles/**", "AGENTS.md", "README.md", "ROADMAP.md"],
        "blocked_paths": [],
        "current_task_id": "",
        "scope": "Quản trị SSOT, điều phối toàn bộ tiến trình gen-workplace",
        "mission": "Chịu trách nhiệm bảo toàn SSOT đặc tả gốc, thẩm định evidence từ các role và điều phối live workflow."
    },
    {
        "id": "gw-backend-agy",
        "role_name": "Backend & DB Specialist",
        "cli_tool": "Gemini CLI (agy --mode accept-edits)",
        "account_type": "profile1",
        "conv_id": "conv-backend-db",
        "gemini_model": "Gemini 3.8 Flash (Low)",
        "anthropic_model": "Claude Sonnet 4.6 (Thinking)",
        "allowed_paths": ["backend/**", "frontend/**", "data/**", "migrations/**"],
        "blocked_paths": ["Dockerfile", "docker-compose.yml"],
        "current_task_id": "",
        "scope": "Python daemon, SQLite WAL, FTS5 catalog và runner",
        "mission": "Thực thi API control plane, tối ưu truy vấn FTS5 catalog sub-ms, quản trị SQLite WAL và lock task."
    },
    {
        "id": "gw-devops-agy",
        "role_name": "DevOps & Packaging",
        "cli_tool": "Gemini CLI (agy --agent devops)",
        "account_type": "profile3",
        "conv_id": "conv-devops-docker",
        "gemini_model": "Gemini 3.8 Flash (High)",
        "anthropic_model": "Claude Sonnet 4.6 (Thinking)",
        "allowed_paths": ["Dockerfile", "docker-compose.yml", "install.sh", "installer_tui.py", "*.desktop", "scripts/**"],
        "blocked_paths": ["backend/main.py", "frontend/**"],
        "current_task_id": "",
        "scope": "Docker, SELinux bind mounts, TUI installer, desktop shortcut",
        "mission": "Container hóa dịch vụ, tối ưu hóa SELinux :z mounts và kịch bản cài đặt 1-lệnh đồ họa TUI."
    },
    {
        "id": "gw-qa-agy",
        "role_name": "QA Tester",
        "cli_tool": "Gemini CLI (agy)",
        "account_type": "profile4",
        "conv_id": "conv-qa-testing",
        "gemini_model": "Gemini 3.8 Flash (Low)",
        "anthropic_model": "Claude Sonnet 4.6 (Thinking)",
        "allowed_paths": ["tests/**", "qa_reports/**", "fixtures/**"],
        "blocked_paths": ["backend/**", "frontend/**", "Dockerfile"],
        "current_task_id": "",
        "scope": "Kiểm thử cross-platform, test API /api/status, xác thực installer",
        "mission": "Chạy regression tests, kiểm thử đa nền tảng (Linux, macOS, Windows WSL2), xác thực tính liên tục của conversation_id."
    }
]

# Vai đã bỏ (Boss chốt 29/09, VIEC-12): KHÔNG xóa dòng DB (giữ lịch sử dispatch_log / chat_messages),
# chỉ đánh dấu status='retired', tắt tmux, lọc khỏi UI / giao việc; @vai trả lỗi rõ ràng.
RETIRED_ROLES = {
    "security": {"session_id": "gw-security-agy", "role_id": "ROLE-06", "name": "Security Auditor", "since": "2026-09-29",
                 "handover": "Việc rà soát/kiểm thử giao cho @qa"},
    "frontend": {"session_id": "gw-frontend-agy", "role_id": "ROLE-03", "name": "Frontend Specialist", "since": "2026-09-29",
                 "handover": "Việc giao diện (frontend/**) giao cho @backend"},
}
RETIRED_SESSION_IDS = {v["session_id"] for v in RETIRED_ROLES.values()}
RETIRED_MENTION_RE = re.compile(r"(?<!\w)@(" + "|".join(RETIRED_ROLES) + r")\b", re.IGNORECASE)   # không bắt email abc@security.io / x@frontend.dev

def retired_role_error(role):
    """Thông báo lỗi khi gọi/giao việc cho vai đã bỏ."""
    info = RETIRED_ROLES.get((role or "").lower().lstrip("@"), {})
    return (f"Vai @{role.lower().lstrip('@')} đã bỏ từ {info.get('since', '29/09')} (retired) — không giao việc được nữa. "
            f"{info.get('handover', 'Giao cho @lead, @backend, @devops hoặc @qa')}; lịch sử cũ của {info.get('session_id', '')} vẫn giữ nguyên.")

def retire_roles(project_id="PRJ-GEN-WORKPLACE"):
    """
    Migration idempotent (chỉ UPDATE): đánh dấu vai trong RETIRED_ROLES là 'retired' ở tmux_sessions + agent_roles.
    Lần đầu đổi trạng thái thì tắt phiên tmux của vai đó. Không DELETE gì; lần chạy sau không làm gì.
    Trả danh sách session_id vừa chuyển sang retired.
    """
    changed = []
    with get_connection() as conn:
        for info in RETIRED_ROLES.values():
            cur = conn.execute("UPDATE tmux_sessions SET status = 'retired', pid = 0, updated_at = CURRENT_TIMESTAMP "
                               "WHERE id = ? AND status != 'retired'", (info["session_id"],))
            if cur.rowcount:
                changed.append(info["session_id"])
            conn.execute("UPDATE agent_roles SET status = 'retired' WHERE (id = ? OR name = ?) AND COALESCE(status, '') != 'retired'",
                         (info["role_id"], info["name"]))
        conn.commit()
    for sid in changed:
        try:
            subprocess.run(["tmux", "kill-session", "-t", sid], capture_output=True, timeout=2.0)
        except Exception:
            pass
        print(f"[migrate] Vai {sid} → retired (giữ lịch sử, đã tắt tmux)")
    return changed

def generate_role_spec_file(sid, role_name, scope="", mission="", conv_id="", allowed_paths=None, blocked_paths=None):
    """
    Sinh file Role Specification (ROLE.md) cô lập ngữ cảnh và trách nhiệm cho từng Agent.
    Đảm bảo 0-conflict, khóa chặt boundary thư mục và liên kết trực tiếp tới Brain SSOT.
    """
    workspace_dir = "/workspace" if os.path.exists("/workspace") else str(BASE_DIR)
    roles_dir = Path(workspace_dir) / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    role_spec_path = roles_dir / f"{sid}_ROLE.md"

    cfg = next((c for c in SWARM_DEFAULT_CONFIG if c["id"] == sid), None)
    use_scope = scope if scope else (cfg["scope"] if cfg else "Workspace dự án")
    use_mission = mission if mission else (cfg["mission"] if cfg else f"Thực hiện nhiệm vụ chuyên môn {role_name}.")
    use_conv = conv_id if conv_id else (cfg["conv_id"] if cfg else f"conv-{sid}")
    use_allowed = allowed_paths if allowed_paths is not None else (cfg.get("allowed_paths", []) if cfg else [])
    use_blocked = blocked_paths if blocked_paths is not None else (cfg.get("blocked_paths", []) if cfg else [])

    allowed_md = "\n".join([f"  - `{p}`" for p in use_allowed]) if use_allowed else "  - *Toàn quyền theo phân công của Orchestrator*"
    blocked_md = "\n".join([f"  - `{p}`" for p in use_blocked]) if use_blocked else "  - *Không có hạn chế đặc biệt*"

    content = f"""# Genesis Swarm Role Specification: {role_name}
- **Session Identifier**: `{sid}`
- **Assigned Scope**: `{use_scope}`
- **Active Mission**: {use_mission}
- **Conversation Thread**: `{use_conv}`
- **Master SSOT**: `docs/SSOT_ORIGINAL_SPEC.md`
- **Genesis Brain Link**: `BOOTSTRAP.md` (Genesis Brain SSOT Protocol)
- **Execution Policy**: Giao qua Phòng giao ban chạy `agy --mode plan -p` (chỉ đọc, KHÔNG skip-permissions) trong worktree riêng của vai; chỉ vai khai trong `GW_AGY_WRITE_ROLES` mới được sửa file

---

### Phạm vi thư mục gợi ý (chỉ để tham khảo — app KHÔNG ép buộc)
- **Được phép chỉnh sửa (Allowed Paths)**:
{allowed_md}
- **CẤM TUYỆT ĐỐI CHẠM VÀO (Blocked Paths)**:
{blocked_md}

---

### 📋 Quy Chuẩn Kỷ Luật Thực Thi (Swarm Governance Protocol)
1. **Khóa Độc Quyền Nhiệm Vụ (Task Mutex)**: Phải gọi `POST /api/task/claim` trước khi bắt đầu công việc. Tuyệt đối không can thiệp vào task đã bị worker khác khóa.
2. **Tuân Thủ Chuỗi Phụ Thuộc (Dependency Chain)**: Không tự ý nhảy cóc khi task tiền đề (`depends_on`) chưa hoàn tất.
3. **Nghiệm Thu Bằng Chứng (Evidence-Backed Completion)**: Chỉ được phép gọi `POST /api/task/complete` khi có bằng chứng vật lý (`commit hash`, artifact path hoặc test log).
4. **Báo Cáo Trực Tiếp**: Gửi tiến độ qua `/api/chat` và cập nhật SQLite khi hoàn tất.
"""
    try:
        with open(role_spec_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"Error writing role spec for {sid}: {e}")
    return str(role_spec_path)

def tmux_init_dir():
    """Thư mục chứa script khởi tạo tmux của từng vai: GW_TMUX_INIT_DIR, mặc định <DATA_DIR>/tmux-init.
    Không dùng /tmp chung: nhiều tiến trình (app thật, test chạy song song) ghi đè cùng file /tmp/tmux_init_*.sh."""
    d = os.environ.get("GW_TMUX_INIT_DIR") or os.path.join(str(DATA_DIR), "tmux-init")
    os.makedirs(d, exist_ok=True)
    return d

def tmux_init_script_path(session_id):
    return os.path.join(tmux_init_dir(), f"tmux_init_{session_id}.sh")

def tmux_live_sessions():
    """
    Các phiên tmux đang chạy thật: {tên phiên: pane_pid của pane đang chọn}. Đúng 1 subprocess (`tmux list-sessions`).
    Không có tmux / tmux server chưa chạy → {}.
    """
    live = {}
    try:
        res = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name} #{pane_pid}"],
                             capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                name, _, pid = line.rpartition(" ")
                if not name:          # tmux cũ không hiểu #{pane_pid} → chỉ có tên
                    name, pid = pid, ""
                live[name] = int(pid) if pid.isdigit() else 0
    except Exception:
        pass
    return live

def capture_tmux_pane(session_id, lines=200):
    """Đọc màn hình 1 phiên tmux (bỏ dòng trống cuối). Lỗi / phiên không chạy → ''. Đúng 1 subprocess."""
    try:
        res = subprocess.run(["tmux", "capture-pane", "-t", session_id, "-p", "-S", f"-{int(lines)}"],
                             capture_output=True, text=True, timeout=1.5)
        if res.returncode == 0 and res.stdout.strip():
            raw_lines = res.stdout.splitlines()
            while raw_lines and not raw_lines[-1].strip():
                raw_lines.pop()
            return "\n".join(raw_lines)
    except Exception:
        pass
    return ""

def start_tmux_session(session_id):
    """
    Mở 1 phiên tmux bash thật cho worker `session_id` (đã có dòng trong tmux_sessions), inject SSOT context,
    conversation ID, profile xác thực và alias gọi agy. Phiên mở trong worktree riêng của vai (#7).
    Chỉ đụng đúng phiên này; phiên đã chạy thì chỉ đọc lại pid. Trả {"status": "active"|"error", ...}.
    """
    oauth_map = {p["id"]: p for p in get_oauth_profiles()}
    with get_connection() as conn:
        s = conn.execute("SELECT * FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone()
    if not s:
        return {"status": "error", "session_id": session_id, "message": f"Không tìm thấy phiên {session_id}"}

    sid = s["id"]
    role_name = s["role_name"]
    acc_type = s["account_type"] or "owner_default"
    profile_dir = s["profile_dir"]
    conv_id = s["conversation_id"] if "conversation_id" in s.keys() and s["conversation_id"] else f"conv-{sid}"

    p_info = oauth_map.get(acc_type, {})
    email = p_info.get("email") or "Chưa đăng nhập"
    is_auth = bool(p_info.get("is_auth"))
    if not profile_dir and p_info.get("path"):
        profile_dir = p_info["path"]

    quota_g, quota_a = get_quota_telemetry(acc_type, email)
    role_spec_file = generate_role_spec_file(sid, role_name, conv_id=conv_id)

    if sid not in tmux_live_sessions():
        init_script_path = tmux_init_script_path(sid)
        try:
            p_dir_clean = profile_dir or os.path.join(HOME_DIR, ".gemini")
            role_cwd = tmux_role_cwd(sid)  # worktree riêng của vai, không mở thẳng trong repo app (#7)
            with open(init_script_path, "w") as f:
                f.write(f"""clear
echo "================================================================================"
echo "🤖 GENESIS AGENT RUNTIME: {role_name} ({sid})"
echo "💎 CLI Engine: agy v1.2.10 | Target Conv: {conv_id}"
echo "🔑 Account: {email} ({'Đã xác thực Google OAuth' if is_auth else 'Chưa đăng nhập'})"
echo "📂 Profile: {p_dir_clean} | Workspace: {role_cwd}"
echo "📋 Role Spec: {role_spec_file} (gõ 'gw-role' để tra cứu)"
echo "📜 SSOT Ref: docs/SSOT_ORIGINAL_SPEC.md (Single Source of Truth locked)"
echo "--------------------------------------------------------------------------------"
echo "💡 Sẵn sàng chấp hành chỉ thị! Gõ 'agy-run' để tiếp tục luồng hội thoại,"
echo "   hoặc 'gw-role' để xem phạm vi role, hoặc 'gw-status' để kiểm tra context."
echo "================================================================================"
""" + tmux_role_env_block(sid, role_name, conv_id, p_dir_clean, role_spec_file, email, role_cwd))
            subprocess.run(["tmux", "new-session", "-d", "-s", sid, "-x", "200", "-y", "40", "-c", role_cwd, f"bash --init-file {init_script_path}"], capture_output=True, timeout=3.0)
            time.sleep(0.15)
        except Exception as e:
            print(f"Error starting real tmux session {sid}: {e}")

    pane_pid = tmux_live_sessions().get(sid, 0)
    live_out = capture_tmux_pane(sid, 100) if pane_pid else ""

    with get_connection() as conn:
        conn.execute("""
        UPDATE tmux_sessions
        SET status = CASE WHEN ? > 0 THEN 'active' ELSE status END,
            pid = ?,
            terminal_output = CASE WHEN ? != '' THEN ? ELSE terminal_output END,
            conversation_id = ?,
            quota_gemini_json = ?,
            quota_anthropic_json = ?,
            last_activity_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (pane_pid, pane_pid, live_out, live_out, conv_id, json.dumps(quota_g, ensure_ascii=False), json.dumps(quota_a, ensure_ascii=False), int(time.time()), sid))
        conn.commit()
    if not pane_pid:
        return {"status": "error", "session_id": sid, "message": f"Không mở được phiên tmux {sid}"}
    return {"status": "active", "session_id": sid, "pid": pane_pid, "conversation_id": conv_id}

def ensure_real_tmux_sessions(project_id="PRJ-GEN-WORKPLACE"):
    """
    Mở các phiên worker chưa chạy (bỏ qua phiên hibernated). CHỈ gọi lúc khởi động app (main.main) hoặc khi cần mở
    phiên — KHÔNG gọi trong vòng poll (#30: trước đây mỗi lượt poll chạy ~25 subprocess tmux).
    Phiên đang chạy thì không đụng tới.
    """
    project_id = normalize_project_id(project_id)
    # Đảm bảo symlink docs trong /workspace để agent luôn đọc được SSOT spec
    try:
        if os.path.exists("/workspace") and not os.path.exists("/workspace/docs") and os.path.exists("/app/docs"):
            os.symlink("/app/docs", "/workspace/docs")
    except Exception:
        pass
    live = tmux_live_sessions()
    with get_connection() as conn:
        rows = conn.execute("SELECT id, status FROM tmux_sessions WHERE project_id = ? ORDER BY id ASC", (project_id,)).fetchall()
    started = []
    for s in rows:
        if s["status"] in ("hibernated", "retired") or s["id"] in live:
            continue
        started.append(start_tmux_session(s["id"]))
    return started

def count_active_tmux_sessions():
    """Số phiên worker đang mở theo DB (active/paused), không gọi tmux."""
    with get_connection() as conn:
        return conn.execute("SELECT count(*) FROM tmux_sessions WHERE status IN ('active', 'paused')").fetchone()[0]

def tmux_idle_min():
    """GW_TMUX_IDLE_MIN: số phút rảnh trước khi tự hibernate phiên (mặc định 15; <= 0 → tắt tự hibernate). Nhận số lẻ."""
    try:
        return float(os.environ.get("GW_TMUX_IDLE_MIN", "15"))
    except ValueError:
        return 15.0

def touch_tmux_activity(session_id):
    """Ghi mốc dùng phiên gần nhất (epoch giây) — thread dọn phiên dựa vào mốc này."""
    with get_connection() as conn:
        conn.execute("UPDATE tmux_sessions SET last_activity_at = ? WHERE id = ?", (int(time.time()), session_id))
        conn.commit()

def ensure_tmux_session_live(session_id):
    """
    Mở phiên theo nhu cầu (#32): tmux chưa chạy → wake_tmux_session; đang pause → resume. Luôn ghi last_activity_at.
    Gọi trước khi gửi lệnh vào phiên, khi bấm "Mở terminal" hoặc attach. Trả {"status", "session_id", "action"}.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return {"status": "error", "session_id": session_id, "message": f"Không tìm thấy phiên {session_id}"}
    if row["status"] == "retired":
        return {"status": "error", "session_id": session_id, "action": "none",
                "message": retired_role_error(next((k for k, v in RETIRED_ROLES.items() if v["session_id"] == session_id), session_id))}
    action = "none"
    res = {"status": row["status"], "session_id": session_id}
    if session_id not in tmux_live_sessions():
        res = wake_tmux_session(session_id)
        action = "wake"
    elif row["status"] == "paused":
        res = resume_tmux_session(session_id)
        action = "resume"
    elif row["status"] == "hibernated":   # tmux đang chạy nhưng DB lệch → sửa trạng thái cho đúng
        with get_connection() as conn:
            conn.execute("UPDATE tmux_sessions SET status = 'active' WHERE id = ?", (session_id,))
            conn.commit()
        res = {"status": "active", "session_id": session_id}
    touch_tmux_activity(session_id)
    out = dict(res)
    out["action"] = action
    return out

def _tmux_session_activity():
    """{tên phiên: (session_activity epoch, số client đang attach)} — 1 subprocess."""
    info = {}
    try:
        res = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name} #{session_activity} #{session_attached}"],
                             capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.strip().rsplit(" ", 2)
                if len(parts) == 3:
                    act = int(parts[1]) if parts[1].isdigit() else 0
                    att = int(parts[2]) if parts[2].isdigit() else 0
                    info[parts[0]] = (act, att)
    except Exception:
        pass
    return info

def reap_idle_tmux_sessions(idle_min=None, now=None, project_id=None):
    """
    Thread dọn phiên (#32): hibernate phiên worker rảnh quá idle_min phút (mặc định GW_TMUX_IDLE_MIN).
    Rảnh tính từ max(last_activity_at, #{session_activity} của tmux). KHÔNG đụng phiên đang có người attach
    hoặc đang có dispatch tmux 'running'. Phiên DB ghi active/paused nhưng tmux không chạy → đánh dấu hibernated.
    Trả {"hibernated": [...], "marked_stopped": [...], "kept": {sid: lý do}}.
    """
    idle_min = tmux_idle_min() if idle_min is None else float(idle_min)
    out = {"hibernated": [], "marked_stopped": [], "kept": {}, "idle_min": idle_min}
    if idle_min <= 0:
        return out
    now = int(now if now is not None else time.time())
    live = _tmux_session_activity()
    q = "SELECT id, status, COALESCE(last_activity_at, 0) AS last_act FROM tmux_sessions WHERE status NOT IN ('hibernated', 'retired')"
    args = ()
    if project_id:
        q += " AND project_id = ?"
        args = (normalize_project_id(project_id),)
    with get_connection() as conn:
        rows = conn.execute(q, args).fetchall()
        busy = {r["session_id"] for r in conn.execute(
            "SELECT DISTINCT session_id FROM dispatch_log WHERE status = 'running' AND kind = 'tmux' "
            "AND started_at >= ?", (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 86400)),)).fetchall()}
    for r in rows:
        sid = r["id"]
        if sid not in live:
            with get_connection() as conn:
                conn.execute("UPDATE tmux_sessions SET status = 'hibernated', pid = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (sid,))
                conn.commit()
            out["marked_stopped"].append(sid)
            continue
        act, attached = live[sid]
        if attached > 0:
            out["kept"][sid] = "attached"
            continue
        if sid in busy:
            out["kept"][sid] = "dispatch_running"
            continue
        last = max(int(r["last_act"] or 0), act)
        if now - last >= idle_min * 60:
            hibernate_tmux_session(sid)
            out["hibernated"].append(sid)
        else:
            out["kept"][sid] = f"idle {now - last}s"
    return out

def seed_tmux_sessions(project_id="PRJ-GEN-WORKPLACE"):
    """Điền và đồng bộ cấu hình phiên worker trong SQLite Core DB. CHỈ ghi DB, không mở tmux (#30).
    Phiên mới mặc định 'hibernated': chỉ mở khi cần (ensure_tmux_session_live, #32)."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tmux_sessions WHERE project_id = ?", (project_id,))
        has_rows = cursor.fetchone()[0] > 0

        oauth_map = {p["id"]: p for p in get_oauth_profiles()}
        
        for cfg in SWARM_DEFAULT_CONFIG:
            sid = cfg["id"]
            p_info = oauth_map.get(cfg["account_type"], {})
            acc_label = compute_account_label(cfg["account_type"], oauth_map)
            profile_dir = p_info.get("path") or ""

            allowed_p = json.dumps(cfg.get("allowed_paths", []))
            blocked_p = json.dumps(cfg.get("blocked_paths", []))
            task_id = cfg.get("current_task_id", "")

            if not has_rows:
                cursor.execute("""
                INSERT INTO tmux_sessions (
                    id, project_id, role_name, cli_tool, account_type, account_label,
                    profile_dir, status, pid, cwd, terminal_output, conversation_id,
                    allowed_paths_json, blocked_paths_json, current_task_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'hibernated', 0, '/workspace', '', ?, ?, ?, ?)
                """, (
                    sid, project_id, cfg["role_name"], cfg["cli_tool"], cfg["account_type"],
                    acc_label, profile_dir, cfg["conv_id"], allowed_p, blocked_p, task_id
                ))
            else:
                cursor.execute("""
                UPDATE tmux_sessions
                SET conversation_id = CASE WHEN conversation_id IS NULL OR conversation_id = '' THEN ? ELSE conversation_id END,
                    account_label = CASE WHEN ? != '' THEN ? ELSE account_label END,
                    allowed_paths_json = ?,
                    blocked_paths_json = ?,
                    current_task_id = CASE WHEN current_task_id IS NULL OR current_task_id = '' THEN ? ELSE current_task_id END
                WHERE id = ? AND project_id = ?
                """, (cfg["conv_id"], acc_label, acc_label, allowed_p, blocked_p, task_id, sid, project_id))

        conn.commit()

def get_tmux_sessions(project_id="PRJ-GEN-WORKPLACE", output_sid="", include_retired=False):
    """
    Danh sách phiên worker kèm tài khoản, quota, task. Chỉ đọc DB + đúng 1 `tmux list-sessions` (#30):
    không mở phiên, không capture-pane hàng loạt. `output_sid` = phiên đang xem ở màn Worker & terminal →
    capture-pane riêng phiên đó (thêm 1 subprocess) và lưu làm terminal_output mới nhất.
    """
    project_id = normalize_project_id(project_id)
    live = tmux_live_sessions()
    fresh_out = {}
    if output_sid and output_sid in live:
        out = capture_tmux_pane(output_sid, 200)
        if out:
            fresh_out[output_sid] = out
            with get_connection() as conn:
                conn.execute("UPDATE tmux_sessions SET terminal_output = ? WHERE id = ?", (out[-8000:], output_sid))
                conn.commit()
    oauth_map = {p["id"]: p for p in get_oauth_profiles()}

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tmux_sessions WHERE project_id = ? " + ("" if include_retired else "AND status != 'retired' ")
                       + "ORDER BY id ASC", (project_id,))
        rows = cursor.fetchall()
        results = []

        for r in rows:
            acc_type = r["account_type"] or "owner_default"
            p_info = oauth_map.get(acc_type, {})
            email = p_info.get("email") or None
            is_auth = bool(p_info.get("is_auth"))
            
            # Luôn tính toán Quota thực tế thời gian thực
            quota_g, quota_a = get_quota_telemetry(acc_type, email or "")
            # Trạng thái hết quota của hồ sơ gán + hồ sơ đang dùng thực tế ở lần giao việc gần nhất (#22)
            q_state = get_profile_quota_state(acc_type)
            last_d = cursor.execute("SELECT id, status, profile_initial, profile_used, fallback_reason FROM dispatch_log "
                                    "WHERE session_id = ? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            profile_in_use = (last_d["profile_used"] if last_d and last_d["profile_used"] else "") or acc_type
            last_fallback = (last_d["fallback_reason"] or "") if last_d and profile_in_use != acc_type else ""

            allowed_p = []
            blocked_p = []
            if "allowed_paths_json" in r.keys() and r["allowed_paths_json"]:
                try:
                    allowed_p = json.loads(r["allowed_paths_json"])
                except Exception:
                    pass
            if "blocked_paths_json" in r.keys() and r["blocked_paths_json"]:
                try:
                    blocked_p = json.loads(r["blocked_paths_json"])
                except Exception:
                    pass
            task_id = r["current_task_id"] if "current_task_id" in r.keys() else ""
            task_title = ""
            task_status = "idle"
            task_roadmap = ""
            evidence_ref = ""
            task_viec_ref = ""
            task_conv_id = ""
            task_chk_total = 0
            task_chk_done = 0
            if task_id:
                cursor.execute("SELECT title, status, roadmap_id, evidence_ref, viec_ref, '' AS conversation_id, '[]' AS checklist_json "
                               "FROM todos WHERE id = ? LIMIT 1", (task_id,))
                t_row = cursor.fetchone()
                if not t_row:
                    cursor.execute("SELECT title, status, '' AS roadmap_id, evidence_ref, viec_ref, conversation_id, checklist_json "
                                   "FROM gen_session_todos WHERE id = ? LIMIT 1", (task_id,))
                    t_row = cursor.fetchone()
                if t_row:
                    task_title = t_row["title"]
                    task_status = t_row["status"]
                    task_roadmap = t_row["roadmap_id"] or ""
                    evidence_ref = t_row["evidence_ref"] or ""
                    task_viec_ref = t_row["viec_ref"] or ""
                    task_conv_id = t_row["conversation_id"] or ""
                    try:
                        chk = json.loads(t_row["checklist_json"] or "[]")
                        task_chk_total = len(chk)
                        task_chk_done = sum(1 for c in chk if isinstance(c, dict) and c.get("done"))
                    except Exception:
                        pass
            else:
                # Tìm task gần nhất đã hoàn tất của chuyên gia này để thể hiện bằng chứng nghiệm thu thực tế
                role_first_word = r["role_name"].split()[0]
                cursor.execute("""
                SELECT id, title, status, roadmap_id, evidence_ref, viec_ref
                FROM todos 
                WHERE (assigned_role = ? OR assigned_role LIKE ?) AND status = 'done'
                ORDER BY id DESC LIMIT 1
                """, (r["role_name"], f"%{role_first_word}%"))
                last_t = cursor.fetchone()
                if last_t:
                    task_id = last_t["id"]
                    task_title = last_t["title"]
                    task_status = "done"
                    task_roadmap = last_t["roadmap_id"] or ""
                    evidence_ref = last_t["evidence_ref"] or ""
                    task_viec_ref = last_t["viec_ref"] or ""

            results.append({
                "id": r["id"],
                "role_name": r["role_name"],
                "cli_tool": r["cli_tool"],
                "account_type": acc_type,
                "account_label": compute_account_label(acc_type, oauth_map),
                "profile_dir": r["profile_dir"],
                "status": r["status"],
                "pid": live.get(r["id"], 0),
                "tmux_live": r["id"] in live,
                "last_activity_at": (r["last_activity_at"] if "last_activity_at" in r.keys() else 0) or 0,
                "cwd": r["cwd"],
                "terminal_output": fresh_out.get(r["id"], r["terminal_output"]),
                "conversation_id": r["conversation_id"] if "conversation_id" in r.keys() else f"conv-{r['id']}",
                "email": email,
                "is_auth": is_auth,
                "account_name": p_info.get("name"),
                "quota_gemini": quota_g,
                "quota_anthropic": quota_a,
                "exhausted": q_state["exhausted"],
                "reset_at": q_state["reset_at"],
                "reset_at_label": q_state["reset_at_label"],
                "profile_in_use": profile_in_use,
                "profile_in_use_label": compute_account_label(profile_in_use, oauth_map),
                "last_dispatch_id": last_d["id"] if last_d else None,
                "last_dispatch_status": (last_d["status"] or "") if last_d else "",
                "fallback_reason": last_fallback,
                "allowed_paths": allowed_p,
                "blocked_paths": blocked_p,
                "current_task_id": task_id,
                "task_title": task_title,
                "task_status": task_status,
                "task_roadmap": task_roadmap,
                "task_viec_ref": task_viec_ref,
                "task_conversation_id": task_conv_id,
                "task_checklist_total": task_chk_total,
                "task_checklist_done": task_chk_done,
                "evidence_ref": evidence_ref,
                "attach_cmd": f"tmux attach -t {r['id']}",
                "updated_at": r["updated_at"]
            })

        return results

ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

def validate_account_switch(session_id, account_id, custom_dir=""):
    """
    Kiểm tham số đổi tài khoản của 1 worker (MCP switch_google_account, POST /api/tmux/account). Trả '' nếu hợp lệ, không thì
    chuỗi lỗi. Trước đây gửi {} ghi account_type=NULL → fetch_live_google_quota lỗi → /api/tmux/sessions 500.
    account_id='custom' chỉ hợp lệ khi kèm custom_dir (hồ sơ tự chọn thư mục ở màn đổi tài khoản).
    """
    session_id = session_id.strip() if isinstance(session_id, str) else ""
    account_id = account_id.strip() if isinstance(account_id, str) else ""
    if not session_id:
        return "Thiếu session_id (vd gw-qa-agy)"
    if not account_id:
        return "Thiếu account_id (vd owner_default, profile1)"
    if not ACCOUNT_ID_RE.match(account_id):
        return f"account_id '{account_id}' sai định dạng (chỉ chữ, số, _ và -)"
    with get_connection() as conn:
        if not conn.execute("SELECT 1 FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone():
            return f"Không có worker '{session_id}'"
    if account_id == "custom" and (custom_dir or "").strip():
        return ""
    known = [p["id"] for p in get_oauth_profiles()]
    if account_id not in known:
        return f"Không có hồ sơ '{account_id}' (hiện có: {', '.join(known)})"
    return ""

def update_tmux_account(session_id, account_type, account_label="", profile_dir=""):
    """Đổi tài khoản OAuth cho phiên Tmux và cập nhật môi trường runtime ngay trong tmux. Nhãn để trống sẽ được tính từ email thật; trả về nhãn đã dùng."""
    account_type = (account_type or "").strip() or "owner_default"   # không bao giờ ghi NULL
    if not account_label:
        account_label = compute_account_label(account_type)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE tmux_sessions 
        SET account_type = ?, account_label = ?, profile_dir = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (account_type, account_label, profile_dir, session_id))
        conn.commit()

    # Cập nhật biến môi trường trực tiếp vào phiên tmux đang chạy
    try:
        p_dir = profile_dir or (os.path.join(HOME_DIR, ".gemini") if account_type == "owner_default" else os.path.join(HOME_DIR, ".agy-profiles", account_type))
        if p_dir.startswith("~"):
            p_dir = os.path.expanduser(p_dir)
        conv_id = f"conv-{session_id}"
        try:
            with get_connection() as conn:
                r = conn.execute("SELECT conversation_id FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone()
                if r and r["conversation_id"]:
                    conv_id = r["conversation_id"]
        except Exception:
            pass
        # Alias dựng lại theo cwd hiện tại của phiên (worktree) — không tự thêm --dangerously-skip-permissions (#7)
        cur_cwd = ""
        try:
            pr = subprocess.run(["tmux", "display-message", "-p", "-t", session_id, "#{pane_current_path}"], capture_output=True, text=True, timeout=2.0)
            cur_cwd = pr.stdout.strip() if pr.returncode == 0 else ""
        except Exception:
            pass
        aliases, _ = build_agy_aliases(session_id, p_dir, conv_id, cur_cwd)
        cmd = f"export GEMINI_DIR='{p_dir}'; {'; '.join(aliases)}; echo '[AUTH] Đã kích hoạt tài khoản: {account_label}'"
        subprocess.run(["tmux", "send-keys", "-t", session_id, cmd, "Enter"], capture_output=True, timeout=2.0)
    except Exception:
        pass
    return account_label

def log_directive_audit(session_id, channel, command, allowed, reason=""):
    """Ghi nhật ký mọi lệnh/phím gửi vào tmux worker (cả cho phép lẫn từ chối) — phục vụ audit allowlist."""
    try:
        with get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS directive_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                channel TEXT,
                command TEXT,
                allowed INTEGER,
                reason TEXT
            )
            """)
            conn.execute("""
            INSERT INTO directive_audit (session_id, channel, command, allowed, reason)
            VALUES (?, ?, ?, ?, ?)
            """, (session_id or "", channel or "", (command or "")[:2000], 1 if allowed else 0, reason or ""))
            conn.commit()
    except Exception as e:
        print(f"[directive_audit] Không ghi được nhật ký: {e}")


def get_directive_audit(limit=100, only_rejected=False):
    """Đọc nhật ký lệnh gửi vào tmux, mới nhất trước."""
    limit = max(1, min(int(limit or 100), 1000))
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='directive_audit'")
        if not cursor.fetchone():
            return []
        where = "WHERE allowed = 0" if only_rejected else ""
        cursor.execute(f"SELECT * FROM directive_audit {where} ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

def append_tmux_output(session_id, command, output=""):
    """Ghi nhận output tương tác của phiên tmux vào SQLite Core DB."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT terminal_output FROM tmux_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        current_out = row["terminal_output"] if row else ""
        now_time = datetime.now().strftime("%H:%M:%S")
        new_block = f"\n$ {command}\n[{now_time}] {output if output else 'Lệnh đã được chuyển vào phiên tmux chấp hành.'}\n$ "
        updated_out = (current_out + new_block)[-4000:]
        cursor.execute("""
        UPDATE tmux_sessions
        SET terminal_output = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (updated_out, session_id))
        conn.commit()

# =========================================================================
# LIFECYCLE MANAGEMENT: PAUSE, RESUME, HIBERNATE, WAKE UP
# =========================================================================

def pause_tmux_session(session_id):
    """Đóng băng CPU (0% CPU) cho phiên tmux bằng SIGSTOP mà không mất terminal."""
    # pid lấy từ tmux đang chạy (không dùng pid cũ trong DB: vòng poll không còn làm mới cột pid, #30)
    pid = tmux_live_sessions().get(session_id, 0)

    if pid > 0:
        subprocess.run(["kill", "-STOP", str(pid)], capture_output=True)

    now_time = datetime.now().strftime("%H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE tmux_sessions 
        SET status = 'paused', pid = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (pid, session_id))
        conn.commit()

    append_tmux_output(session_id, "sys.freeze()", f"[{now_time}] ⏸ Phiên đã được ĐÓNG BĂNG CPU (SIGSTOP). Tải CPU: 0.0%. Nhấn Tiếp tục để mở lại tức thì.")
    return {"status": "paused", "session_id": session_id, "pid": pid}

def resume_tmux_session(session_id):
    """Tiếp tục phiên tmux đang tạm dừng bằng SIGCONT trong 0ms."""
    # pid lấy từ tmux đang chạy (không dùng pid cũ trong DB: vòng poll không còn làm mới cột pid, #30)
    pid = tmux_live_sessions().get(session_id, 0)

    if pid > 0:
        subprocess.run(["kill", "-CONT", str(pid)], capture_output=True)

    now_time = datetime.now().strftime("%H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE tmux_sessions 
        SET status = 'active', pid = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (pid, session_id))
        conn.commit()

    append_tmux_output(session_id, "sys.unfreeze()", f"[{now_time}] ▶ Phiên đã được KÍCH HOẠT LẠI (SIGCONT). Sẵn sàng nhận lệnh.")
    return {"status": "active", "session_id": session_id, "pid": pid}

def hibernate_tmux_session(session_id):
    """
    Ngủ đông / Tạm tắt phiên để giải phóng 100% tài nguyên CPU & RAM.
    Lưu snapshot terminal output và bảo lưu conversation_id vào SQLite trước khi kill tmux.
    """
    now_time = datetime.now().strftime("%H:%M:%S")
    live_out = ""
    conv_id = f"conv-{session_id}"
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT terminal_output, conversation_id FROM tmux_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            live_out = row["terminal_output"] or ""
            if row["conversation_id"]:
                conv_id = row["conversation_id"]

    try:
        res = subprocess.run(["tmux", "capture-pane", "-t", session_id, "-p", "-S", "-500"], capture_output=True, text=True, timeout=1.5)
        if res.returncode == 0 and res.stdout.strip():
            live_out = res.stdout.strip()
    except Exception:
        pass

    hib_note = f"\n\n================================================================================\n" \
               f"😴 [HỆ THỐNG] Phiên đã TẠM TẮT (NGỦ ĐÔNG) lúc {now_time}.\n" \
               f"💡 Đã giải phóng 100% RAM & 0% CPU. Quota & Context Graph '{conv_id}' được bảo toàn.\n" \
               f"⚡ Nhấn 'Đánh thức / Gọi lại phiên' để khôi phục chính xác phiên làm việc này.\n" \
               f"================================================================================\n"
    saved_out = (live_out + hib_note)[-4000:]

    try:
        subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True, timeout=2.0)
    except Exception:
        pass

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE tmux_sessions
        SET status = 'hibernated', pid = 0, terminal_output = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (saved_out, session_id))
        conn.commit()

    return {"status": "hibernated", "session_id": session_id, "conversation_id": conv_id}

def wake_tmux_session(session_id):
    """
    Đánh thức / Gọi lại đúng phiên làm việc đã ngủ đông.
    Khởi tạo lại tmux session, phục hồi đúng Conversation ID, profile, workspace và SSOT.
    Phiên mở trong worktree riêng của vai (#7).
    """
    oauth_map = {p["id"]: p for p in get_oauth_profiles()}

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tmux_sessions WHERE id = ?", (session_id,))
        s = cursor.fetchone()
        if not s:
            return {"status": "error", "message": f"Không tìm thấy phiên {session_id}"}

        role_name = s["role_name"]
        acc_type = s["account_type"] or "owner_default"
        profile_dir = s["profile_dir"]
        conv_id = s["conversation_id"] or f"conv-{session_id}"
        prev_output = s["terminal_output"] or ""

        p_info = oauth_map.get(acc_type, {})
        email = p_info.get("email") or "Chưa đăng nhập"
        p_dir_clean = profile_dir or p_info.get("path") or os.path.join(HOME_DIR, ".gemini")
        role_spec_file = generate_role_spec_file(session_id, role_name, conv_id=conv_id)

        init_script_path = tmux_init_script_path(session_id)
        try:
            role_cwd = tmux_role_cwd(session_id)  # worktree riêng của vai (#7)
            with open(init_script_path, "w") as f:
                f.write(f"""clear
echo "================================================================================"
echo "⚡ GENESIS AGENT RUNTIME: {role_name} ({session_id}) - ĐÃ ĐƯỢC GỌI LẠI!"
echo "💎 CLI Engine: agy v1.2.10 | Tiếp tục mạch tư duy: {conv_id}"
echo "🔑 Account: {email} | Profile: {p_dir_clean}"
echo "📋 Role Spec: {role_spec_file} (gõ 'gw-role' để tra cứu)"
echo "📜 SSOT Ref: docs/SSOT_ORIGINAL_SPEC.md (Toàn vẹn ngữ cảnh bất biến)"
echo "--------------------------------------------------------------------------------"
echo "💡 Toàn bộ trí nhớ phiên và Conversation ID đã phục hồi. Nhập lệnh để tiếp tục."
echo "   hoặc 'gw-role' để xem phạm vi role, hoặc 'gw-status' để kiểm tra context."
echo "================================================================================"
""" + tmux_role_env_block(session_id, role_name, conv_id, p_dir_clean, role_spec_file, email, role_cwd))
            subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True)
            subprocess.run(["tmux", "new-session", "-d", "-s", session_id, "-c", role_cwd, f"bash --init-file {init_script_path}"], capture_output=True, timeout=3.0)
            time.sleep(0.15)
        except Exception as e:
            print(f"Error waking tmux session {session_id}: {e}")

        pane_pid = 0
        try:
            p_res = subprocess.run(["tmux", "list-panes", "-t", session_id, "-F", "#{pane_pid}"], capture_output=True, text=True, timeout=1.0)
            if p_res.returncode == 0 and p_res.stdout.strip().isdigit():
                pane_pid = int(p_res.stdout.strip().splitlines()[0])
        except Exception:
            pass

        live_out = ""
        try:
            c_res = subprocess.run(["tmux", "capture-pane", "-t", session_id, "-p", "-S", "-100"], capture_output=True, text=True, timeout=1.5)
            if c_res.returncode == 0 and c_res.stdout.strip():
                raw_lines = c_res.stdout.splitlines()
                while raw_lines and not raw_lines[-1].strip():
                    raw_lines.pop()
                if raw_lines:
                    live_out = "\n".join(raw_lines)
        except Exception:
            pass

        cursor.execute("""
        UPDATE tmux_sessions 
        SET status = 'active', pid = ?, terminal_output = ?, last_activity_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (pane_pid, live_out or prev_output, int(time.time()), session_id))
        conn.commit()

    return {"status": "active", "session_id": session_id, "pid": pane_pid, "conversation_id": conv_id}

def manage_tmux_swarm_lifecycle(action, target_id="all", project_id="PRJ-GEN-WORKPLACE"):
    """Quản trị vòng đời hàng loạt cho các phiên Swarm."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM tmux_sessions WHERE project_id = ? AND status != 'retired'", (project_id,))
        sessions = cursor.fetchall()

    results = []
    for s in sessions:
        sid = s["id"]
        if target_id not in ("all", "", None) and sid != target_id:
            continue
        if action == "sleep_all" or (action in ("sleep", "hibernate") and sid == target_id):
            results.append(hibernate_tmux_session(sid))
        elif action == "wake_all" or (action in ("wake", "wake_up") and sid == target_id):
            results.append(wake_tmux_session(sid))
        elif action == "open" and sid == target_id:
            # "Mở terminal" / attach: chỉ mở khi phiên chưa chạy, không khởi động lại phiên đang chạy (#32)
            results.append(ensure_tmux_session_live(sid))
        elif action == "pause_all" or (action == "pause" and sid == target_id):
            results.append(pause_tmux_session(sid))
        elif action == "resume_all" or (action == "resume" and sid == target_id):
            results.append(resume_tmux_session(sid))
            
    return results

def start_oauth_login(profile_id, custom_path=""):
    """Khởi tạo phiên đăng nhập OAuth hoặc thư mục profile mới."""
    if profile_id == "owner_default":
        target_dir = os.path.join(HOME_DIR, ".gemini")
    elif custom_path:
        target_dir = custom_path
    else:
        target_dir = os.path.join(HOME_DIR, ".agy-profiles", profile_id)

    cli_dir = os.path.join(target_dir, "antigravity-cli")
    os.makedirs(cli_dir, exist_ok=True)

    # Pre-seed jetski_state và settings.json để agy CLI bỏ qua màn hình onboarding TUI
    owner_cli = os.path.join(HOME_DIR, ".gemini", "antigravity-cli")
    target_jetski = os.path.join(cli_dir, "jetski_state.pbtxt")
    if not os.path.exists(target_jetski) and os.path.exists(os.path.join(owner_cli, "jetski_state.pbtxt")):
        try:
            shutil.copy(os.path.join(owner_cli, "jetski_state.pbtxt"), target_jetski)
        except Exception:
            pass

    target_settings = os.path.join(cli_dir, "settings.json")
    if not os.path.exists(target_settings):
        try:
            with open(target_settings, "w", encoding="utf-8") as f:
                json.dump({"theme": "terminal"}, f, indent=2)
        except Exception:
            pass

    # Pre-seed config/mcp_config.json và schemas cho agy CLI
    config_dir = os.path.join(target_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    mcp_cfg = os.path.join(config_dir, "mcp_config.json")
    if not os.path.exists(mcp_cfg):
        try:
            with open(mcp_cfg, "w", encoding="utf-8") as f:
                json.dump({
                    "mcpServers": {
                        "google-drive": {
                            "command": os.path.join(HOME_DIR, ".local", "bin", "genos-gdrive-mcp")
                        },
                        "gen-workplace": {
                            "command": os.path.join(HOME_DIR, ".local", "bin", "gen-workplace-mcp")
                        }
                    }
                }, f, indent=2)
        except Exception:
            pass

    target_mcp_schemas = os.path.join(cli_dir, "mcp", "gen-workplace")
    owner_mcp_schemas = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "mcp", "gen-workplace")
    if not os.path.exists(target_mcp_schemas) and os.path.exists(owner_mcp_schemas):
        try:
            shutil.copytree(owner_mcp_schemas, target_mcp_schemas)
        except Exception:
            pass

    # Google OAuth 2.0 Auth URL chuẩn cho Antigravity CLI / Cloud Code
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or GOOGLE_OAUTH_CLIENT_ID
    if not cid:
        return {"ok": False, "error": "Chưa cấu hình GOOGLE_OAUTH_CLIENT_ID trong .env", "profile_id": profile_id, "oauth_url": ""}
    # prompt=select_account consent giúp người dùng luôn có thể đổi sang tài khoản Google khác
    oauth_url = f"https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id={cid}&redirect_uri=http%3A%2F%2Flocalhost%3A8085%2Foauth2callback&scope=openid%20email%20profile%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcloud-platform&access_type=offline&prompt=select_account%20consent&state={profile_id}"

    session_name = "gw-oauth-login"
    tmux_created = False
    try:
        subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
        cmd = f"agy --gemini_dir={target_dir} || bash"
        res = subprocess.run(["tmux", "new-session", "-d", "-s", session_name, "-c", str(BASE_DIR), cmd], capture_output=True, timeout=2.0)
        tmux_created = (res.returncode == 0)
    except Exception:
        pass

    return {
        "status": "started",
        "profile_id": profile_id,
        "path": target_dir,
        "tmux_session": session_name,
        "tmux_created": tmux_created,
        "oauth_url": oauth_url,
        "message": f"Phiên xác thực OAuth cho {profile_id} đã sẵn sàng."
    }

def check_oauth_status(profile_id):
    """Kiểm tra trạng thái xác thực tức thì của một profile."""
    if profile_id == "owner_default":
        target_dir = os.path.join(HOME_DIR, ".gemini")
    else:
        target_dir = os.path.join(HOME_DIR, ".agy-profiles", profile_id)

    token_file = os.path.join(target_dir, "antigravity-cli", "antigravity-oauth-token")
    if not os.path.exists(token_file):
        return {
            "profile_id": profile_id,
            "is_auth": False,
            "message": "Chưa tìm thấy file token xác thực."
        }

    try:
        with open(token_file, "r") as f:
            data = json.load(f)
            payload = parse_id_token(data.get("id_token", "")) if isinstance(data, dict) else {}
            email = payload.get("email") if isinstance(payload, dict) else None
            name = payload.get("name") if isinstance(payload, dict) else None
            exp = payload.get("exp", 0) if isinstance(payload, dict) else 0

            # Fallback lấy email từ Google UserInfo API nếu id_token không có
            if not email and isinstance(data, dict):
                acc_tok = (data.get("token") or {}).get("access_token") if isinstance(data.get("token"), dict) else data.get("access_token")
                if acc_tok:
                    try:
                        import urllib.request
                        req = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo",
                                                     headers={"Authorization": f"Bearer {acc_tok}"})
                        with urllib.request.urlopen(req, timeout=3.0) as u_resp:
                            u_data = json.loads(u_resp.read().decode("utf-8"))
                            email = u_data.get("email")
                            name = u_data.get("name")
                    except Exception:
                        pass

            is_auth = bool(email or (isinstance(data, dict) and data.get("token")))
            return {
                "profile_id": profile_id,
                "is_auth": is_auth,
                "email": email,
                "name": name,
                "exp": exp,
                "message": f"Đã xác thực thành công tài khoản Google: {email}" if email else "Token hợp lệ"
            }
    except Exception as e:
        return {
            "profile_id": profile_id,
            "is_auth": False,
            "error": str(e),
            "message": "Lỗi khi đọc token xác thực."
        }

def exchange_google_code_for_token(code, profile_id="profile1"):
    """
    Trao đổi authorization code với Google OAuth Token Endpoint (https://oauth2.googleapis.com/token)
    để lấy access_token, refresh_token, id_token và tự động lưu vào profile.
    """
    import urllib.request
    import urllib.parse
    from datetime import datetime, timezone, timedelta

    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or GOOGLE_OAUTH_CLIENT_ID
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or GOOGLE_OAUTH_CLIENT_SECRET
    if not cid:
        return False, "Chưa cấu hình GOOGLE_OAUTH_CLIENT_ID trong .env", None
    if not csec:
        return False, "Chưa cấu hình GOOGLE_OAUTH_CLIENT_SECRET", None
    redirect_uri = "http://localhost:8085/oauth2callback"

    post_data = urllib.parse.urlencode({
        "code": code,
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=post_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        return False, f"Google OAuth HTTP {e.code}: {err_msg}", None
    except Exception as e:
        return False, f"Lỗi kết nối tới Google OAuth: {str(e)}", None

    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 3600)
    id_token = data.get("id_token", "")

    expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    expiry_str = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    token_payload = {
        "token": {
            "access_token": access_token,
            "token_type": "Bearer",
            "refresh_token": refresh_token or access_token,
            "expiry": expiry_str
        },
        "auth_method": "consumer",
        "id_token": id_token
    }

    # Lưu token vào profile
    res = save_oauth_token(profile_id, token_payload)

    # Lấy thông tin email
    email = res.get("email")
    if not email and access_token:
        try:
            u_req = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            with urllib.request.urlopen(u_req, timeout=4.0) as u_resp:
                u_info = json.loads(u_resp.read().decode("utf-8"))
                email = u_info.get("email")
        except Exception:
            pass

    return True, "Token đã được lưu thành công", email or "Google Account"

def clone_oauth_profile(source_id, target_id):
    """Sao chép toàn bộ token OAuth từ một profile nguồn sang profile đích."""
    if not source_id or not target_id or source_id == target_id:
        return {"ok": False, "error": "ID nguồn và đích không hợp lệ"}

    src_dir = os.path.join(HOME_DIR, ".gemini") if source_id == "owner_default" else os.path.join(HOME_DIR, ".agy-profiles", source_id)
    dst_dir = os.path.join(HOME_DIR, ".gemini") if target_id == "owner_default" else os.path.join(HOME_DIR, ".agy-profiles", target_id)

    src_tok = os.path.join(src_dir, "antigravity-cli", "antigravity-oauth-token")
    if not os.path.exists(src_tok):
        return {"ok": False, "error": f"Không tìm thấy token tại profile nguồn {source_id}"}

    dst_cli = os.path.join(dst_dir, "antigravity-cli")
    os.makedirs(dst_cli, exist_ok=True)
    dst_tok = os.path.join(dst_cli, "antigravity-oauth-token")

    try:
        shutil.copy2(src_tok, dst_tok)
        os.chmod(dst_tok, 0o600)

        # Pre-seed jetski_state và settings
        src_jetski = os.path.join(src_dir, "antigravity-cli", "jetski_state.pbtxt")
        if os.path.exists(src_jetski):
            shutil.copy2(src_jetski, os.path.join(dst_cli, "jetski_state.pbtxt"))
        src_settings = os.path.join(src_dir, "antigravity-cli", "settings.json")
        if os.path.exists(src_settings):
            shutil.copy2(src_settings, os.path.join(dst_cli, "settings.json"))

        # Pre-seed config/mcp_config.json và schemas
        dst_config = os.path.join(dst_dir, "config")
        os.makedirs(dst_config, exist_ok=True)
        mcp_cfg_path = os.path.join(dst_config, "mcp_config.json")
        if not os.path.exists(mcp_cfg_path):
            with open(mcp_cfg_path, "w", encoding="utf-8") as f:
                json.dump({
                    "mcpServers": {
                        "google-drive": {
                            "command": os.path.join(HOME_DIR, ".local", "bin", "genos-gdrive-mcp")
                        },
                        "gen-workplace": {
                            "command": os.path.join(HOME_DIR, ".local", "bin", "gen-workplace-mcp")
                        }
                    }
                }, f, indent=2)

        dst_mcp_schemas = os.path.join(dst_cli, "mcp", "gen-workplace")
        owner_mcp_schemas = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "mcp", "gen-workplace")
        if not os.path.exists(dst_mcp_schemas) and os.path.exists(owner_mcp_schemas):
            try:
                shutil.copytree(owner_mcp_schemas, dst_mcp_schemas)
            except Exception:
                pass

        res = check_oauth_status(target_id)
        return {"ok": True, "target": target_id, "status": res}
    except Exception as e:
        return {"ok": False, "error": f"Lỗi khi sao chép token: {str(e)}"}

def save_oauth_token(profile_id, token_data):
    """Lưu token OAuth trực tiếp vào hồ sơ profile."""
    if profile_id == "owner_default":
        target_dir = os.path.join(HOME_DIR, ".gemini")
    else:
        target_dir = os.path.join(HOME_DIR, ".agy-profiles", profile_id)

    cli_dir = os.path.join(target_dir, "antigravity-cli")
    os.makedirs(cli_dir, exist_ok=True)
    token_file = os.path.join(cli_dir, "antigravity-oauth-token")

    payload = {}
    if isinstance(token_data, str):
        token_str = token_data.strip()
        try:
            payload = json.loads(token_str)
        except Exception:
            payload = {
                "token": {
                    "access_token": token_str,
                    "token_type": "Bearer",
                    "refresh_token": token_str,
                    "expiry": "2099-12-31T23:59:59Z"
                },
                "auth_method": "consumer",
                "id_token": ""
            }
    elif isinstance(token_data, dict):
        payload = token_data

    # Tự động trích xuất hoặc hoàn thiện cấu trúc nếu payload chỉ chứa access_token/refresh_token
    if "token" not in payload and ("access_token" in payload or "refresh_token" in payload):
        payload = {
            "token": payload,
            "auth_method": payload.get("auth_method", "consumer"),
            "id_token": payload.get("id_token", "")
        }

    with open(token_file, "w") as f:
        json.dump(payload, f, indent=2)

    os.chmod(token_file, 0o600)
    return check_oauth_status(profile_id)

def assign_oauth_to_role(session_id, profile_id):
    """Gán profile đã xác thực cho một Swarm Role tmux cụ thể."""
    profiles = {p["id"]: p for p in get_oauth_profiles()}
    p = profiles.get(profile_id)
    if not p:
        return {"status": "error", "message": f"Không tìm thấy profile {profile_id}"}

    account_type = profile_id
    account_label = compute_account_label(profile_id)
    profile_dir = p["path"]

    update_tmux_account(session_id, account_type, account_label, profile_dir)
    append_tmux_output(session_id, f"auth switch --profile={profile_id}", f"Đã gán tài khoản '{account_label}' cho Role này.")

    return {
        "status": "assigned",
        "session_id": session_id,
        "profile_id": profile_id,
        "account_label": account_label
    }

def logout_oauth_profile(profile_id):
    """Đăng xuất / thu hồi token của profile."""
    if profile_id == "owner_default":
        target_dir = os.path.join(HOME_DIR, ".gemini")
    else:
        target_dir = os.path.join(HOME_DIR, ".agy-profiles", profile_id)

    token_file = os.path.join(target_dir, "antigravity-cli", "antigravity-oauth-token")
    if os.path.exists(token_file):
        bak_file = token_file + f".bak-{int(time.time())}"
        os.rename(token_file, bak_file)
        return {"status": "logged_out", "profile_id": profile_id, "backup": bak_file}
    return {"status": "already_logged_out", "profile_id": profile_id}

# =========================================================================
# GENESIS ORCHESTRATOR CONTROL PLANE & DYNAMIC WORKER SPAWN
# =========================================================================

def get_orch_chat_messages(project_id="PRJ-GEN-WORKPLACE"):
    """Lấy danh sách tin nhắn giữa Owner và Orchestrator."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM chat_messages 
        WHERE runtime_id = 'orch' AND project_id = ?
        ORDER BY id ASC
        """, (project_id,))
        rows = cursor.fetchall()
        msgs = []
        for r in rows:
            msgs.append({
                "id": r["id"],
                "author": r["author"],
                "time": r["created_time"],
                "tag": r["tag"],
                "body": r["body"],
                "react": json.loads(r["react_json"] or "[]")
            })
        return msgs

def save_orch_chat_message(author, body, tag="Orchestrator", project_id="PRJ-GEN-WORKPLACE"):
    """Lưu tin nhắn của Owner hoặc Orchestrator vào SQLite."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        now_time = time.strftime("%H:%M:%S")
        cursor.execute("""
        INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json)
        VALUES (?, 'orch', ?, ?, ?, ?, ?)
        """, (project_id, author, now_time, tag, body, json.dumps(["✅ đã ghi nhận"], ensure_ascii=False)))
        conn.commit()
        return now_time

def spawn_worker(role_name, project_id="PRJ-GEN-WORKPLACE", account_type="owner_default", mission="", scope=""):
    """
    Sinh worker runtime mới động theo nhu cầu của dự án.
    Tạo hồ sơ trong agent_roles, cấu hình phiên trong tmux_sessions,
    sinh file bootstrap ngữ cảnh vai trò (Role Spec) và khởi tạo tmux session thật.
    """
    project_id = normalize_project_id(project_id)
    role_slug = role_name.lower().replace(" ", "-").replace("&", "").replace("/", "-")
    role_slug = "".join(c for c in role_slug if c.isalnum() or c == "-")[:16].strip("-")
    sid = f"gw-{role_slug}-agy"
    conv_id = f"conv-{sid}"
    clean_mission = mission or f"Thực thi các hạng mục chuyên môn cho vai trò {role_name} theo chuẩn SSOT."
    clean_scope = scope or "Thực hiện theo chỉ định của Orchestrator trong workspace dự án."

    workspace_dir = "/workspace" if os.path.exists("/workspace") else str(BASE_DIR)
    role_spec_path = generate_role_spec_file(sid, role_name, scope=clean_scope, mission=clean_mission, conv_id=conv_id)

    # 1. Lưu vào SQLite
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Check if already exists in tmux_sessions
        cursor.execute("SELECT id FROM tmux_sessions WHERE id = ?", (sid,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO tmux_sessions (
                id, project_id, role_name, cli_tool, status, pid, cwd,
                terminal_output, account_type, account_label, profile_dir, conversation_id,
                quota_gemini_json, quota_anthropic_json
            ) VALUES (?, ?, ?, 'agy', 'active', 0, ?, ?, ?, 'Mặc định (Owner Gmail: ~/.gemini)', ?, ?, ?, ?)
            """, (
                sid, project_id, role_name, workspace_dir,
                f"[{role_name} ({sid}) spawned by Orchestrator]\nRole spec generated: {role_spec_path}\nReady for tasks.",
                account_type, os.path.join(HOME_DIR, ".gemini"), conv_id,
                json.dumps({"percent": 85, "limit": 100}),
                json.dumps({"percent": 90, "limit": 100})
            ))

        # Check if already exists in agent_roles
        role_key = role_name[:1].upper()
        cursor.execute("SELECT id FROM agent_roles WHERE role_key = ?", (role_key,))
        if not cursor.fetchone():
            role_id = f"ROLE-{len(sid)}"
            cursor.execute("""
            INSERT INTO agent_roles (id, project_id, role_key, name, cli_tool, scope, instruction, status)
            VALUES (?, ?, ?, ?, 'agy', ?, ?, 'active')
            """, (f"ROLE-{sid[:8]}", project_id, role_key, role_name, clean_scope, clean_mission))

        conn.commit()

    # 2. Kích hoạt tmux session thật
    ensure_real_tmux_sessions(project_id)

    # 3. Ghi nhận thông báo vào Orchestrator chat
    now_time = time.strftime("%H:%M:%S")
    save_orch_chat_message(
        "Genesis Orchestrator",
        f"⚡ <strong>Đã sinh thành công Worker mới</strong>: <code>{role_name}</code> (Session: <code>{sid}</code>, Conversation: <code>{conv_id}</code>). Role bootstrap spec đã lưu tại <code>roles/{sid}_ROLE.md</code>.",
        tag="SpawnEvent",
        project_id=project_id
    )

    return {
        "status": "spawned",
        "session_id": sid,
        "role_name": role_name,
        "conversation_id": conv_id,
        "role_spec": str(role_spec_path),
        "created_at": now_time
    }

SWARM_SESSION_IDS = [c["id"] for c in SWARM_DEFAULT_CONFIG]   # không có vai đã bỏ (RETIRED_ROLES)
_TASK_TITLE_UNSAFE_RE = re.compile(r"[\'\"`$\\\n\r;|&<>(){}]")

def build_task_directive(session_id, task_id, task_title, profile_dir=""):
    """
    Lệnh giao task thật cho worker (gõ vào tmux, phải qua allowlist directive_guard):
      agy --gemini_dir='<hồ sơ>' --mode plan -p 'Thực hiện task <id>: <tiêu đề>' 2>&1 | tee ~/gw-reports/task-<id>-<ts>.md; echo "=== XONG exit=${PIPESTATUS[0]} ==="
    Tiêu đề được lọc ký tự shell (nháy, $, `, ;, |, ...) để không thoát khỏi nháy đơn.
    """
    safe_id = re.sub(r"[^A-Za-z0-9_\-]", "-", str(task_id or ""))[:40]
    safe_title = re.sub(r"\s+", " ", _TASK_TITLE_UNSAFE_RE.sub(" ", str(task_title or ""))).strip()[:200]
    p_dir = profile_dir or _profile_dir("owner_default")
    p_dir = _TASK_TITLE_UNSAFE_RE.sub("", p_dir)
    report_path = f"~/gw-reports/task-{safe_id}-{time.strftime('%Y%m%d-%H%M%S')}.md"
    cmd = (f"agy --gemini_dir='{p_dir}' --mode plan -p 'Thực hiện task {safe_id}: {safe_title}' 2>&1 "
           f"| tee {report_path}; echo \"=== XONG exit=${{PIPESTATUS[0]}} ===\"")
    return cmd, report_path

def dispatch_swarm_workflow(project_id="PRJ-GEN-WORKPLACE", session_id=None):
    """
    "Chạy Task này" (session_id) / "Chạy Toàn Bộ Swarm" (session_id=None → mọi worker còn dùng):
    mỗi worker nhận lệnh agy THẬT cho task đang gán (tmux_sessions.current_task_id → todos).
    Worker không có task hoặc task đã done → {"status": "error", "reason": ...}; KHÔNG gửi echo giả.
    Mọi lệnh đi qua directive_guard.guard và ghi directive_audit. Trả {sid: {...}}.
    """
    project_id = normalize_project_id(project_id)
    try:
        from backend import directive_guard as _guard
    except ImportError:
        import directive_guard as _guard
    targets = [session_id] if session_id else list(SWARM_SESSION_IDS)
    results = {}
    with get_connection() as conn:
        for sid in targets:
            row = conn.execute("SELECT id, current_task_id, profile_dir, account_type FROM tmux_sessions WHERE id = ? AND project_id = ?", (sid, project_id)).fetchone()
            if not row:
                results[sid] = {"status": "error", "reason": f"Không có phiên worker '{sid}' trong tmux_sessions"}
                continue
            task_id = (row["current_task_id"] or "").strip()
            if not task_id:
                results[sid] = {"status": "error", "reason": f"Worker {sid} không có task đang gán (current_task_id rỗng). Hãy claim task trước."}
                continue
            t = conn.execute("SELECT id, title, status, viec_ref FROM todos WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
            if not t:
                t = conn.execute("SELECT id, title, status, viec_ref FROM gen_session_todos WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
            if not t:
                results[sid] = {"status": "error", "reason": f"Task {task_id} gán cho {sid} không tồn tại trong todos/gen_session_todos", "task_id": task_id}
                continue
            if t["status"] == "done":
                results[sid] = {"status": "error", "reason": f"Task {task_id} đã done, không chạy lại", "task_id": task_id}
                continue
            account = row["account_type"] or "owner_default"
            p_dir = os.path.expanduser(row["profile_dir"]) if row["profile_dir"] else _profile_dir(account)
            # Hồ sơ gán cho vai đang hết quota (chưa tới reset_at) → chọn hồ sơ khác ngay, không tốn 1 lần gọi hỏng (#22)
            sel = select_agy_profile_for_run(account, p_dir)
            if not sel["profile_id"]:
                results[sid] = {"status": "error", "reason": sel["note"], "task_id": task_id, "profile_initial": account,
                                "all_exhausted": True, "earliest_reset": sel["earliest_reset"]}
                continue
            if sel["profile_id"] != account:
                copy_agy_allow_rules(p_dir, sel["p_dir"])
            cmd, report_path = build_task_directive(sid, task_id, t["title"], sel["p_dir"])
            results[sid] = {"status": "pending", "task_id": task_id, "task_title": t["title"], "viec_ref": t["viec_ref"] or "", "command": cmd,
                            "report_path": report_path, "profile_initial": account, "profile_used": sel["profile_id"],
                            "fallback_reason": sel["note"], "_primary_dir": p_dir}

    for sid, r in results.items():
        if r["status"] != "pending":
            continue
        cmd = r["command"]
        allowed, reason = _guard.guard(sid, cmd)
        log_directive_audit(sid, "dispatch_swarm", cmd, allowed, reason)
        if not allowed:
            r.update({"status": "error", "reason": f"directive_guard từ chối: {reason}"})
            continue
        tmux_real = False
        try:
            ensure_tmux_session_live(sid)   # phiên đang ngủ → mở trước khi gõ lệnh (#32)
            res = subprocess.run(["tmux", "send-keys", "-t", sid, cmd, "Enter"], capture_output=True, text=True, timeout=2.0)
            tmux_real = (res.returncode == 0)
        except Exception as e:
            r["tmux_error"] = str(e)
        try:
            append_tmux_output(sid, cmd, "Đã gửi vào tmux qua send-keys." if tmux_real else "tmux không nhận lệnh (phiên chưa mở?), đã ghi nhận vào runtime.")
        except Exception:
            pass
        r.update({"status": "dispatched", "tmux_real": tmux_real, "dispatch_id": None})
        if tmux_real:
            # dispatch_log running + watcher đọc dòng '=== XONG exit=N ===' trong pane → chốt + webhook; id cho wait_worker_result (#9)
            try:
                did = start_dispatch_log(sid, kind="tmux", channel_id="tmux", task_id=r["task_id"], command=cmd,
                                         report_path=os.path.expanduser(r["report_path"]), viec_ref=r.get("viec_ref", ""))
                with get_connection() as conn:
                    conn.execute("UPDATE dispatch_log SET profile_initial = ?, profile_used = ?, fallback_reason = ? WHERE id = ?",
                                 (r["profile_initial"], r["profile_used"], r["fallback_reason"], did))
                    conn.commit()
                r["dispatch_id"] = did
                start_tmux_dispatch_watcher(did)
            except Exception as e:
                print(f"[dispatch] Không ghi được dispatch_log cho {sid}: {e}")
    for r in results.values():
        r.pop("_primary_dir", None)
    return results

ORCH_CONV_ID = "conv-orchestrator"
ORCH_DEFAULT_MODEL = os.environ.get("GW_ORCH_MODEL", "")

def _html_escape_lines(text):
    """Escape HTML và đổi xuống dòng thành <br> (kênh orch được frontend hiển thị bằng innerHTML)."""
    t = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return t.replace("\r\n", "\n").replace("\n", "<br>")

def process_orch_instruction(user_message, project_id="PRJ-GEN-WORKPLACE", model=None, account="owner_default"):
    """
    Chỉ thị Owner → Orchestrator: lưu tin người dùng, gọi agy THẬT (conv-orchestrator, giữ ngữ cảnh agy_conv_id),
    lưu trả lời thật với tác giả 'Orchestrator (agy)'; agy lỗi → 'Orchestrator (lỗi)' + lý do thật.
    Không còn câu mẫu, không phân tích ý định giả.
    """
    project_id = normalize_project_id(project_id)
    user_time = save_orch_chat_message("Owner (Ryan)", user_message, tag="Instruction", project_id=project_id)
    model = model or ORCH_DEFAULT_MODEL or None
    try:
        with get_connection() as conn:
            conn.execute("""
            INSERT OR IGNORE INTO gen_conversations (id, project_id, title, model, account_profile, is_pinned, active_tab, active_file, open_tabs_json, active_evidence_id, owner_id)
            VALUES (?, 'PRJ-GEN-WORKPLACE', 'Orchestrator (agy)', ?, ?, 0, 'files_repo', '', '[]', '', 'owner-ryan')
            """, (ORCH_CONV_ID, model or "", account))
            conn.commit()
    except Exception as e:
        print(f"[orch] Không tạo được {ORCH_CONV_ID}: {e}")

    reply, _, usage = call_agy_cli_turn(ORCH_CONV_ID, user_message, model=model, account=account)
    usage = usage if isinstance(usage, dict) else {}
    if reply:
        author, tag, engine, is_error = "Orchestrator (agy)", "Reply", "agy-cli", False
        body = reply
    else:
        author, tag, engine, is_error = "Orchestrator (lỗi)", "Error", "error", True
        body = f"agy không trả lời: {describe_agy_error(usage)}. Không có phản hồi tự sinh."
    agent_time = save_orch_chat_message(author, _html_escape_lines(body), tag=tag, project_id=project_id)
    return {
        "reply": body,
        "author": author,
        "action_taken": None,
        "engine": engine,
        "error": is_error,
        "error_code": usage.get("error", "") if is_error else "",
        "usage": usage,
        "user_time": user_time,
        "agent_time": agent_time
    }

# ═══════════════════════════════════════════════════════════════════════════
# SWARM ANTI-CHAOS GOVERNANCE (CƠ CHẾ BẢO ĐẢM KHÔNG RỐI LOẠN)
# ═══════════════════════════════════════════════════════════════════════════

EVIDENCE_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
EVIDENCE_PR_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([0-9]+)/?$")
EVIDENCE_DISPATCH_RE = re.compile(r"^dispatch:\s*#?([0-9]{1,18})$", re.IGNORECASE)
EVIDENCE_WARROOM_RE = re.compile(r"^warroom:\s*#?([0-9]{1,18})$", re.IGNORECASE)
EVIDENCE_FORMATS_HINT = ("commit SHA có trong repo, file không rỗng trong ~/gw-reports/ hoặc repo/worktree, "
                         "dispatch:<id> (lần giao việc done), warroom:<id> (tin trả lời của agent) "
                         "hoặc URL PR GitHub có thật (https://github.com/<owner>/<repo>/pull/<n>)")
GITHUB_API_BASE = "https://api.github.com"
DISPATCH_OK_STATUSES = ("done", "ok")

def _github_api_timeout():
    try:
        return max(1.0, float(os.environ.get("GW_GITHUB_API_TIMEOUT_SEC", "5")))
    except ValueError:
        return 5.0

def _verify_github_pr(owner, repo, number):
    """Gọi GitHub API công khai kiểm PR có thật. Không gọi được mạng / bị từ chối → (False, lý do); không bao giờ im lặng cho qua."""
    import urllib.request
    import urllib.error
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "gen-workplace-evidence-check",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=_github_api_timeout()) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            data = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8", errors="replace") or "{}")
            detail = str(body.get("message") or "")[:200] if isinstance(body, dict) else ""
        except Exception:
            pass
        detail = f": {detail}" if detail else ""
        if e.code == 404:
            hint = "" if token else " (repo private thì đặt GITHUB_TOKEN)"
            return False, f"PR {owner}/{repo}#{number} không tồn tại trên GitHub (404){hint}"
        if e.code in (401, 403, 429):
            return False, (f"GitHub API từ chối khi kiểm PR {owner}/{repo}#{number} (HTTP {e.code}{detail or ': sai token hoặc hết rate limit'}) "
                           "— chưa kiểm được nên không nhận")
        return False, f"GitHub API lỗi HTTP {e.code}{detail} khi kiểm PR {owner}/{repo}#{number} — chưa kiểm được nên không nhận"
    except Exception as e:
        reason = getattr(e, "reason", None) or e
        return False, (f"Không gọi được GitHub API để kiểm PR {owner}/{repo}#{number} ({type(e).__name__}: {reason}) — "
                       "chưa kiểm được nên không nhận; thử lại hoặc dùng commit SHA / dispatch:<id>")
    if code != 200 or not isinstance(data, dict) or str(data.get("number")) != str(number):
        return False, f"GitHub API trả dữ liệu không khớp PR {owner}/{repo}#{number} (HTTP {code})"
    state = "merged" if data.get("merged_at") else (data.get("state") or "?")
    return True, f"PR {owner}/{repo}#{number} có thật trên GitHub ({state})"

def _evidence_file_roots():
    """Thư mục được nhận làm nơi chứa file bằng chứng: ~/gw-reports, repo app, repo dispatch, thư mục worktree của các vai."""
    roots = [os.path.join(HOME_DIR, "gw-reports"), str(BASE_DIR), os.environ.get("GW_DISPATCH_REPO") or "", _worktree_root()]
    out = []
    for r in roots:
        if r:
            rp = os.path.realpath(os.path.expanduser(r))
            if rp not in out:
                out.append(rp)
    return out

def _verify_evidence_file(ev, legacy=False):
    if ev.startswith("~"):
        candidate = os.path.expanduser(ev)
    elif os.path.isabs(ev):
        candidate = ev
    else:
        candidate = str(BASE_DIR / ev)
    if legacy:
        # Rà soát task done cũ lúc khởi động: giữ luật cũ (file tồn tại) để không hạ cấp bằng chứng đã nhận trước đây
        if os.path.exists(candidate):
            return True, f"File tồn tại: {candidate}"
        return False, ""
    real = os.path.realpath(candidate)
    if not os.path.exists(real):
        return False, ""
    inside = [root for root in _evidence_file_roots() if real == root or real.startswith(root + os.sep)]
    if not inside:
        return False, (f"File '{ev}' nằm ngoài ~/gw-reports/, repo và worktree của các vai — không nhận làm bằng chứng")
    if ".git" in Path(os.path.relpath(real, inside[0])).parts:
        return False, f"File '{ev}' nằm trong thư mục .git — không nhận làm bằng chứng"
    if not os.path.isfile(real):
        return False, f"'{ev}' không phải file thường — không nhận làm bằng chứng"
    if os.path.getsize(real) == 0:
        return False, f"File '{ev}' rỗng — không nhận làm bằng chứng"
    return True, f"File không rỗng: {real}"

def _verify_evidence_dispatch(dispatch_id, task_id=""):
    row = _get_dispatch_row(dispatch_id)
    if not row:
        return False, f"dispatch:{dispatch_id} không có trong dispatch_log"
    status = _row_status(row)
    if status not in DISPATCH_OK_STATUSES:
        return False, f"dispatch:{dispatch_id} có status '{status}' (cần done) — không nhận làm bằng chứng"
    d_task = (row.get("task_id") or "").strip()
    if task_id and d_task and d_task != task_id:
        return False, f"dispatch:{dispatch_id} thuộc task {d_task}, không phải {task_id}"
    return True, f"dispatch:{dispatch_id} ({row.get('kind') or 'dispatch'}, {row.get('session_id')}) đã {status}"

def _verify_evidence_warroom(msg_id, project_id="PRJ-GEN-WORKPLACE"):
    with get_connection() as conn:
        m = conn.execute("SELECT id, project_id, author, tag FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        if not m:
            return False, f"warroom:{msg_id} không tồn tại"
        if project_id and (m["project_id"] or "") != project_id:
            return False, f"warroom:{msg_id} thuộc dự án khác ({m['project_id']})"
        author = (m["author"] or "").strip()
        workers = {r["id"] for r in conn.execute("SELECT id FROM tmux_sessions").fetchall()}
        d = conn.execute("SELECT id, status, exit_code, finished_at FROM dispatch_log WHERE reply_msg_id = ? ORDER BY id DESC LIMIT 1",
                         (msg_id,)).fetchone()
    is_agent = author in workers or author in WARROOM_ROLE_SESSIONS.values() or author == "Orchestrator (agy)" or d is not None
    if not is_agent or (m["tag"] or "") == "Error" or author.endswith("(lỗi)"):
        return False, f"warroom:{msg_id} (tác giả '{author}') không phải tin trả lời của agent — không nhận làm bằng chứng"
    if d is not None and _row_status(dict(d)) not in DISPATCH_OK_STATUSES:
        return False, f"warroom:{msg_id} là trả lời của dispatch:{d['id']} có status '{_row_status(dict(d))}' — không nhận làm bằng chứng"
    return True, f"warroom:{msg_id} là tin trả lời của {author}"

def verify_evidence_ref(evidence_ref, task_id="", project_id="PRJ-GEN-WORKPLACE", legacy=False):
    """
    Kiểm bằng chứng nghiệm thu. Trả (ok, verified_by, message). Nhận:
    - commit SHA 7–40 hex có trong repo → git:commit
    - dispatch:<id>: dòng dispatch_log status done/ok (có task_id thì phải khớp task) → dispatch
    - warroom:<id>: tin chat_messages là trả lời của agent (dispatch của nó, nếu có, phải done) → warroom
    - https://github.com/<owner>/<repo>/pull/<n>: PR có thật theo GitHub API (không gọi được mạng → từ chối) → github:pr
    - file không rỗng trong ~/gw-reports/, repo hoặc worktree → file
    legacy=True: chỉ dùng cho rà soát task done cũ lúc khởi động — không gọi mạng, URL PR chỉ kiểm định dạng, file chỉ cần tồn tại.
    """
    ev = (evidence_ref or "").strip()
    if not ev:
        return False, "", f"Thiếu evidence_ref ({EVIDENCE_FORMATS_HINT})"
    project_id = normalize_project_id(project_id) if project_id else project_id
    m = EVIDENCE_DISPATCH_RE.match(ev)
    if m:
        ok, msg = _verify_evidence_dispatch(int(m.group(1)), "" if legacy else (task_id or ""))
        return ok, ("dispatch" if ok else ""), msg
    m = EVIDENCE_WARROOM_RE.match(ev)
    if m:
        ok, msg = _verify_evidence_warroom(int(m.group(1)), "" if legacy else project_id)
        return ok, ("warroom" if ok else ""), msg
    m = EVIDENCE_PR_RE.match(ev)
    if m:
        if legacy:
            return True, "github:pr", "URL PR GitHub (định dạng hợp lệ, không kiểm mạng khi rà soát dữ liệu cũ)"
        ok, msg = _verify_github_pr(m.group(1), m.group(2), m.group(3))
        return ok, ("github:pr" if ok else ""), msg
    if EVIDENCE_SHA_RE.match(ev.lower()):
        # Repo app + repo dispatch (worktree của vai / của task dùng chung kho object với repo gốc nên commit trên nhánh
        # wt/TSK-n cũng thấy ở đây, kể cả sau khi đã dọn worktree vì nhánh local được giữ) (#45)
        repos = list(dict.fromkeys(os.path.realpath(r) for r in (str(BASE_DIR), os.environ.get("GW_DISPATCH_REPO") or "") if r))
        for repo in repos:
            try:
                res = subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{ev}^{{commit}}"], capture_output=True, timeout=5.0)
                if res.returncode == 0:
                    return True, "git:commit", f"Commit {ev} tồn tại trong repo" + ("" if repo == os.path.realpath(str(BASE_DIR)) else f" {repo}")
            except Exception:
                pass
        return False, "", f"Commit {ev} không tồn tại trong repo {', '.join(repos)}"
    ok, msg = _verify_evidence_file(ev, legacy=legacy)
    if ok:
        return True, "file", msg
    if msg:
        return False, "", msg
    return False, "", f"Bằng chứng không kiểm được: '{ev}' không phải {EVIDENCE_FORMATS_HINT}"

def task_lock_timeout_sec():
    """Ngưỡng coi khóa task là quá hạn (giây) — cùng ngưỡng thread [reclaim]: GW_RECLAIM_TIMEOUT_SEC, mặc định = GW_RECLAIM_INTERVAL_SEC (300)."""
    try:
        interval = max(5, int(os.environ.get("GW_RECLAIM_INTERVAL_SEC", "300")))
    except ValueError:
        interval = 300
    try:
        return int(os.environ.get("GW_RECLAIM_TIMEOUT_SEC", str(interval)))
    except ValueError:
        return interval

# Khóa còn hiệu lực = task in_progress, có người giữ khác người gọi, locked_at chưa quá ngưỡng.
# Task in_progress không có locked_at (dữ liệu cũ / sửa tay) coi như khóa quá hạn — giống reclaim_stalled_tasks.
_LOCK_STALE_SQL = "(locked_at IS NULL OR locked_at = '' OR strftime('%s', 'now') - strftime('%s', locked_at) > ?)"

def _claim_refused(table, todo_id, project_id, session_id):
    """Đọc lại task sau khi UPDATE có điều kiện không trúng dòng nào để báo lý do + ai đang giữ."""
    with get_connection() as conn:
        if table == "todos":
            r = conn.execute("SELECT status, assigned_session_id AS holder, locked_at FROM todos WHERE id = ? AND project_id = ?",
                             (todo_id, project_id)).fetchone()
        else:
            r = conn.execute("""SELECT status, COALESCE(NULLIF(claimed_by, ''), assigned_agent, '') AS holder, locked_at
                                FROM gen_session_todos WHERE id = ? AND project_id = ?""", (todo_id, project_id)).fetchone()
    if not r:
        return {"error": "Task not found", "task_id": todo_id}
    if r["status"] == "done":
        return {"error": f"Task {todo_id} đã done — không claim lại được", "code": "already_done", "task_id": todo_id}
    if r["status"] != "in_progress" or not (r["holder"] or "").strip():
        return {"error": f"Task {todo_id} vừa đổi trạng thái trong lúc claim — thử lại", "code": "retry", "task_id": todo_id}
    return {"error": f"Task {todo_id} đang bị khóa bởi {r['holder']} (locked_at {r['locked_at'] or '?'} UTC); "
                     f"chỉ claim lại được khi người đó nhả khóa hoặc khóa quá {task_lock_timeout_sec()}s",
            "code": "locked", "task_id": todo_id, "held_by": r["holder"], "locked_at": r["locked_at"] or "", "session_id": session_id}

def claim_task(session_id, todo_id, project_id="PRJ-GEN-WORKPLACE", lock_timeout_sec=None):
    """
    Khóa độc quyền nhiệm vụ (Atomic Task Mutex) cho cả bảng roadmap 'todos' và Kanban phiên 'gen_session_todos':
    - Nguyên tử: một câu UPDATE có điều kiện trong WHERE (BEGIN IMMEDIATE), kiểm rowcount → hai worker không cùng thắng.
    - Từ chối khi task in_progress đang do người khác giữ và khóa chưa quá hạn (ngưỡng = reclaim_stalled_tasks);
      trả {"error", "code": "locked", "held_by", "locked_at"}. Người đang giữ gọi lại → làm mới locked_at.
    - Không claim task đã done (tránh mở lại rồi ghi đè bằng chứng).
    - 'todos': kiểm ràng buộc tiền đề (depends_on) trước khi khóa.
    """
    project_id = normalize_project_id(project_id)
    session_id = (session_id or "").strip()
    todo_id = (todo_id or "").strip()
    if not session_id or not todo_id:
        return {"error": "Thiếu session_id hoặc task_id"}
    timeout = task_lock_timeout_sec() if lock_timeout_sec is None else int(lock_timeout_sec)

    with get_connection() as conn:
        todo = conn.execute("SELECT id, depends_on FROM todos WHERE id = ? AND project_id = ?", (todo_id, project_id)).fetchone()
        table = "todos" if todo else None
        if not todo:
            if conn.execute("SELECT 1 FROM gen_session_todos WHERE id = ? AND project_id = ?", (todo_id, project_id)).fetchone():
                table = "gen_session_todos"
        if table is None:
            return {"error": "Task not found", "task_id": todo_id}
        if table == "todos" and todo["depends_on"]:
            dep = conn.execute("SELECT status FROM todos WHERE id = ? AND project_id = ?", (todo["depends_on"], project_id)).fetchone()
            if dep and dep["status"] != "done":
                return {"error": f"Prerequisite task {todo['depends_on']} is not done yet (status: {dep['status']})"}

    with get_connection() as conn:
        conn.isolation_level = None  # tự quản giao dịch: BEGIN IMMEDIATE giữ khóa ghi ngay từ đầu
        conn.execute("BEGIN IMMEDIATE")
        try:
            if table == "todos":
                cur = conn.execute(f"""
                UPDATE todos
                SET status = 'in_progress', assigned_session_id = ?, locked_at = CURRENT_TIMESTAMP
                WHERE id = ? AND project_id = ? AND status != 'done'
                  AND (status != 'in_progress' OR COALESCE(assigned_session_id, '') IN ('', ?) OR {_LOCK_STALE_SQL})
                """, (session_id, todo_id, project_id, session_id, timeout))
            else:
                cur = conn.execute(f"""
                UPDATE gen_session_todos
                SET status = 'in_progress', assigned_agent = ?, claimed_by = ?, locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND project_id = ? AND status != 'done'
                  AND (status != 'in_progress' OR COALESCE(NULLIF(claimed_by, ''), assigned_agent, '') IN ('', ?) OR {_LOCK_STALE_SQL})
                """, (session_id, session_id, todo_id, project_id, session_id, timeout))
            won = cur.rowcount == 1
            if won:
                conn.execute("UPDATE tmux_sessions SET current_task_id = ?, last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?",
                             (todo_id, session_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    if not won:
        return _claim_refused(table, todo_id, project_id, session_id)
    return {"status": "claimed", "task_id": todo_id, "session_id": session_id}

def _log_evidence_override(conn, table, todo_id, project_id, session_id, old_ev, old_by, new_ev, new_by, reason,
                           action="override_evidence", held_by=""):
    conn.execute("""
    INSERT INTO task_evidence_audit (task_id, table_name, project_id, session_id, old_evidence_ref, old_verified_by,
                                     new_evidence_ref, new_verified_by, reason, action, held_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (todo_id, table, project_id, session_id or "", old_ev or "", old_by or "", new_ev, new_by, reason or "", action, held_by or ""))
    if action == "holder_dispatch":
        print(f"[evidence] {session_id} đóng task {todo_id} ({table}) thay {held_by} bằng dispatch của chính {held_by}: '{new_ev}'")
    elif action == "force_close":
        print(f"[evidence] force đóng task {todo_id} ({table}) đang do {held_by} giữ, người đóng {session_id}: '{new_ev}'"
              + (f" — lý do: {reason}" if reason else ""))
    else:
        print(f"[evidence] force ghi đè bằng chứng task {todo_id} ({table}) bởi {session_id}: '{old_ev}' → '{new_ev}'"
              + (f" — lý do: {reason}" if reason else ""))

def get_task_evidence_audit(task_id="", limit=50):
    """Nhật ký ghi đè bằng chứng (complete_task force=True), mới nhất trước."""
    with get_connection() as conn:
        if task_id:
            rows = conn.execute("SELECT * FROM task_evidence_audit WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM task_evidence_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

# Người đang giữ task (claim_task): todos → assigned_session_id, Kanban phiên → claimed_by (assigned_agent chỉ là nhãn giao việc)
_TASK_HOLDER_COL = {"todos": "assigned_session_id", "gen_session_todos": "claimed_by"}

def _is_holder_dispatch_evidence(evidence_ref, todo_id, holder):
    """evidence_ref = dispatch:<id> của chính holder, gắn đúng task todo_id, đã done; hoặc commit SHA mà lần build (chế độ Làm)
    đã done của holder cho task này tạo ra (#45)."""
    ev = (evidence_ref or "").strip().lower() if isinstance(evidence_ref, str) else ""
    if holder and EVIDENCE_SHA_RE.match(ev):
        try:
            with get_connection() as conn:
                rows = conn.execute("SELECT build_commit FROM dispatch_log WHERE kind = 'build' AND session_id = ? AND task_id = ? "
                                    "AND status IN ('done', 'ok') AND build_commit != ''", (holder, todo_id)).fetchall()
            return any((r["build_commit"] or "").lower().startswith(ev) for r in rows)
        except Exception:
            return False
    m = EVIDENCE_DISPATCH_RE.match((evidence_ref or "").strip()) if isinstance(evidence_ref, str) else None
    if not m or not holder:
        return False
    try:
        row = _get_dispatch_row(int(m.group(1)))
    except Exception:
        return False
    return bool(row) and row.get("session_id") == holder and (row.get("task_id") or "") == todo_id \
        and _row_status(row) in DISPATCH_OK_STATUSES

def complete_task(session_id, todo_id, evidence_ref, verified_by="Lead Architect", project_id="PRJ-GEN-WORKPLACE", force=False, reason=""):
    """
    HÀM CHUNG duy nhất chuyển task sang 'done' (Evidence-Backed Completion, #4, #16, #18). Mọi đường đổi trạng thái
    (API /api/task/complete, /api/todo/update, /api/gen/session/todos/status|save, MCP complete_task, kéo thả Kanban,
    chỉ thị [KANBAN_UPDATE]/[TASK_DONE] của chat Gen) đều gọi qua đây:
    - evidence_ref phải qua verify_evidence_ref (commit SHA / file không rỗng trong ~/gw-reports·repo·worktree /
      dispatch:<id> done khớp task / warroom:<id> trả lời của agent / URL PR GitHub có thật).
    - Không đạt → trả {"error": ...} và KHÔNG đổi trạng thái.
    - Chỉ người đang giữ task (claimed_by / assigned_session_id) được đóng; task chưa ai claim thì ai cũng đóng được.
      Người khác → {"error", "code": "not_holder", "held_by"}; muốn đóng thay phải force=True và lần đóng đó được ghi
      vào task_evidence_audit (action='force_close', held_by).
    - Task đã done → {"error", "code": "already_done"} và KHÔNG ghi đè; chỉ ghi đè khi force=True,
      mỗi lần ghi đè được lưu vào task_evidence_audit (bằng chứng cũ → mới, ai, lý do) và in log.
    - verified_by được tính: 'git:commit' / 'file' / 'github:pr' / 'dispatch' / 'warroom' (tham số verified_by chỉ giữ để tương thích API).
    - Ngoại lệ không cần force: bằng chứng là dispatch:<id> ĐÃ XONG của chính người đang giữ task, gắn đúng task này (kết quả
      là của người giữ) → đóng thay được, trả closed_for_holder và ghi task_evidence_audit action='holder_dispatch' (#24).
    - Tự động nhả khóa session để sẵn sàng nhận nhiệm vụ tiếp theo.
    """
    project_id = normalize_project_id(project_id)
    todo_id = (todo_id or "").strip()
    session_id = (session_id or "").strip()
    evidence_ref = evidence_ref if isinstance(evidence_ref, str) else ("" if evidence_ref is None else str(evidence_ref))
    with get_connection() as conn:
        table, row = None, None
        for tbl, by_col in (("todos", "verified_by"), ("gen_session_todos", "'' AS verified_by")):
            row = conn.execute(f"SELECT status, evidence_ref, {by_col}, COALESCE({_TASK_HOLDER_COL[tbl]}, '') AS holder "
                               f"FROM {tbl} WHERE id = ? AND project_id = ?", (todo_id, project_id)).fetchone()
            if row:
                table = tbl
                break
    if table is None:
        return {"error": "Task not found", "task_id": todo_id}
    was_done = row["status"] == "done"
    if was_done and not force:
        return {"error": f"Task {todo_id} đã done với bằng chứng '{row['evidence_ref'] or ''}' — không ghi đè. "
                         "Cần sửa bằng chứng thì gọi lại với force=true (sẽ được ghi log).",
                "code": "already_done", "task_id": todo_id, "evidence_ref": row["evidence_ref"] or "", "verified_by": row["verified_by"] or ""}
    holder = (row["holder"] or "").strip()
    acting_for = ""
    if not was_done and holder and holder != session_id and not force and _is_holder_dispatch_evidence(evidence_ref, todo_id, holder):
        # Bằng chứng là lần giao việc (dispatch) ĐÃ XONG của chính người giữ task cho đúng task này → đóng thay người giữ được,
        # không cần force (kết quả là của người giữ); ghi task_evidence_audit action='holder_dispatch'.
        acting_for = holder
    if not was_done and holder and holder != session_id and not force and not acting_for:
        return {"error": f"Task {todo_id} đang do {holder} giữ — chỉ người giữ task mới được đóng. "
                         "Muốn đóng thay thì gọi lại với force=true (sẽ được ghi nhật ký task_evidence_audit).",
                "code": "not_holder", "task_id": todo_id, "held_by": holder, "session_id": session_id}

    ok, verified_by, msg = verify_evidence_ref(evidence_ref, task_id=todo_id, project_id=project_id)
    if not ok:
        return {"error": msg, "code": "invalid_evidence", "task_id": todo_id}
    evidence_ref = evidence_ref.strip()
    holder_col = _TASK_HOLDER_COL[table]

    with get_connection() as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            by_col = "verified_by" if table == "todos" else "'' AS verified_by"
            old = conn.execute(f"SELECT status, evidence_ref, {by_col}, COALESCE({holder_col}, '') AS holder FROM {table} "
                               "WHERE id = ? AND project_id = ?", (todo_id, project_id)).fetchone()
            guard, guard_params = "", []
            if not force:
                # Nguyên tử: chưa done và (chưa ai giữ hoặc chính người gọi đang giữ) ngay tại lúc ghi
                guard = f" AND status != 'done' AND COALESCE({holder_col}, '') IN ('', ?)"
                guard_params = [acting_for or session_id]
            if table == "todos":
                cur = conn.execute(f"""
                UPDATE todos
                SET status = 'done', evidence_ref = ?, verified_by = ?, assigned_session_id = ''
                WHERE id = ? AND project_id = ?{guard}
                """, [evidence_ref, verified_by, todo_id, project_id] + guard_params)
            else:
                cur = conn.execute(f"""
                UPDATE gen_session_todos
                SET status = 'done', evidence_ref = ?, claimed_by = '', locked_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND project_id = ?{guard}
                """, [evidence_ref, todo_id, project_id] + guard_params)
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                if old and old["status"] == "done":
                    return {"error": f"Task {todo_id} vừa được nghiệm thu bởi lượt gọi khác — không ghi đè", "code": "already_done", "task_id": todo_id}
                return {"error": f"Task {todo_id} vừa được {old['holder'] if old else '?'} claim trong lúc đóng — chỉ người giữ task mới được đóng",
                        "code": "not_holder", "task_id": todo_id, "held_by": old["holder"] if old else ""}
            overridden = bool(old and old["status"] == "done")
            old_holder = (old["holder"] or "").strip() if old else ""
            force_closed = bool(old and not overridden and old_holder and old_holder != session_id and not acting_for)
            if acting_for:
                _log_evidence_override(conn, table, todo_id, project_id, session_id, old["evidence_ref"] if old else "",
                                       old["verified_by"] if old else "", evidence_ref, verified_by, reason or "bằng chứng là dispatch của người giữ task",
                                       action="holder_dispatch", held_by=acting_for)
                conn.execute("UPDATE tmux_sessions SET current_task_id = '' WHERE id = ? AND current_task_id = ?", (acting_for, todo_id))
            if overridden:
                _log_evidence_override(conn, table, todo_id, project_id, session_id, old["evidence_ref"], old["verified_by"],
                                       evidence_ref, verified_by, reason)
            elif force_closed:
                _log_evidence_override(conn, table, todo_id, project_id, session_id, old["evidence_ref"], old["verified_by"],
                                       evidence_ref, verified_by, reason, action="force_close", held_by=old_holder)
            conn.execute("UPDATE tmux_sessions SET current_task_id = '', last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
            if force_closed:
                conn.execute("UPDATE tmux_sessions SET current_task_id = '' WHERE id = ? AND current_task_id = ?", (old_holder, todo_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    cleanup = cleanup_task_worktree(todo_id)   # chế độ Làm (#45): task done → gỡ worktree ../gw-worktrees/TSK-n, giữ nhánh
    viec_ref = get_task_viec_ref(todo_id, project_id)
    webhook_sent = send_event_webhook("task_completed", project_id=project_id, viec_ref=viec_ref, task_id=todo_id,
                                      session_id=session_id, exit_code=None, report_path="", evidence_ref=evidence_ref, verified_by=verified_by)
    res = {"status": "completed", "task_id": todo_id, "viec_ref": viec_ref, "evidence_ref": evidence_ref, "verified_by": verified_by,
           "verify_message": msg, "webhook_sent": webhook_sent}
    if overridden:
        res["overridden"] = {"old_evidence_ref": old["evidence_ref"] or "", "old_verified_by": old["verified_by"] or "", "reason": reason or ""}
    if force_closed:
        res["force_closed"] = {"held_by": old_holder, "reason": reason or ""}
    if acting_for:
        res["closed_for_holder"] = acting_for
    if cleanup.get("removed"):
        res["worktree_removed"] = cleanup["dir"]
    return res


def cleanup_task_worktree(task_id):
    """Gỡ worktree chế độ Làm của task (agy_build.cleanup_task_worktree). Không có / lỗi → {"removed": False, ...}, không ném."""
    try:
        try:
            from backend import agy_build as _build
        except ImportError:
            import agy_build as _build
        return _build.cleanup_task_worktree(task_id)
    except Exception as e:
        print(f"[build] Không dọn được worktree của {task_id}: {e}")
        return {"removed": False, "error": str(e)}

TASK_STATUS_ERROR_HTTP = {"not_found": 404, "already_done": 409, "not_holder": 409, "wrong_conversation": 404}

def task_error_http_status(res):
    """Mã HTTP cho kết quả lỗi của complete_task / set_task_status: 404 không có task, 409 đã done / người khác giữ, 400 còn lại."""
    if not isinstance(res, dict) or "error" not in res:
        return 200
    if res.get("error") == "Task not found":
        return 404
    return TASK_STATUS_ERROR_HTTP.get(res.get("code"), 400)

def set_task_status(todo_id, new_status, project_id="PRJ-GEN-WORKPLACE", conv_id=None, session_id="", evidence_ref="",
                    force=False, reason=""):
    """
    Đổi trạng thái 1 task (todos roadmap hoặc Kanban phiên nếu truyền conv_id). Mọi đường đổi trạng thái đều đi qua đây:
    - new_status == 'done' → complete_task (kiểm evidence + chỉ người giữ task, force thì ghi audit). Không có evidence
      hợp lệ → {"error", "code"} và trạng thái KHÔNG đổi.
    - Trạng thái khác → cập nhật thẳng (Kanban phiên: ngoài todo/in_progress/review/done → 'todo').
    Trả dict: {"status": "updated"|"completed", ...} hoặc {"error", "code"?, ...}.
    """
    project_id = normalize_project_id(project_id)
    todo_id = (todo_id or "").strip()
    new_status = (new_status or "").strip().lower()
    if conv_id:
        if new_status not in GEN_SESSION_TODO_STATUSES:
            new_status = "todo"
        with get_connection() as conn:
            row = conn.execute("SELECT conversation_id FROM gen_session_todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            return {"error": "Task not found", "id": todo_id, "task_id": todo_id}
        if row["conversation_id"] != conv_id:
            return {"error": f"Task {todo_id} không thuộc phiên {conv_id}", "code": "wrong_conversation", "id": todo_id, "task_id": todo_id}
    if new_status == "done":
        res = complete_task(session_id, todo_id, evidence_ref or "", project_id=project_id, force=force, reason=reason)
        res.setdefault("id", todo_id)
        if "error" not in res:
            res["new_status"] = "done"
        return res
    with get_connection() as conn:
        if conv_id:
            params = [new_status]
            extra = ""
            if evidence_ref:
                # Không đè bằng chứng của task đã done qua đường đổi trạng thái thường
                extra = ", evidence_ref = CASE WHEN status = 'done' THEN evidence_ref ELSE ? END"
                params.append(evidence_ref)
            cur = conn.execute(f"UPDATE gen_session_todos SET status = ?, updated_at = CURRENT_TIMESTAMP{extra} WHERE id = ? AND conversation_id = ?",
                               params + [todo_id, conv_id])
        else:
            cur = conn.execute("UPDATE todos SET status = ? WHERE id = ? AND project_id = ?", (new_status, todo_id, project_id))
        updated = cur.rowcount > 0
        conn.commit()
    if not updated:
        return {"error": "Task not found", "id": todo_id, "task_id": todo_id}
    if not conv_id:
        _refresh_roadmap_status(todo_id, project_id)
    return {"status": "updated", "id": todo_id, "new_status": new_status}

def reclaim_stalled_tasks(timeout_seconds=None, project_id="PRJ-GEN-WORKPLACE"):
    """
    Thu hồi nhiệm vụ bị treo từ Agent bóng ma / crash (Anti-Zombie Reclamation):
    - Quét các task 'in_progress' bị giữ quá timeout mà session không gửi heartbeat
      (kể cả task seed/không có locked_at — không ai thật sự đang giữ).
    - Nhả task về lại trạng thái 'queued' để worker khác nhận việc.
    - timeout_seconds=None → task_lock_timeout_sec() (cùng ngưỡng claim_task dùng để cho claim lại).
    """
    project_id = normalize_project_id(project_id)
    if timeout_seconds is None:
        timeout_seconds = task_lock_timeout_sec()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
        UPDATE todos
        SET status = 'queued', assigned_session_id = '', locked_at = NULL
        WHERE project_id = ? AND status = 'in_progress'
          AND {_LOCK_STALE_SQL}
        """, (project_id, timeout_seconds))
        reclaimed = cursor.rowcount
        conn.commit()
        return {"reclaimed_count": reclaimed}

def get_warroom_messages(channel_id="war_room", project_id="PRJ-GEN-WORKPLACE", limit=60):
    """Lấy danh sách tin nhắn phòng giao ban theo kênh."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        # N tin MỚI NHẤT (ORDER BY id DESC LIMIT) rồi đảo lại để hiển thị cũ → mới
        cursor.execute("""
        SELECT * FROM (
            SELECT id, project_id, runtime_id, author, created_time, tag, body, react_json, reply_to, created_at
            FROM chat_messages
            WHERE project_id = ? AND runtime_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) ORDER BY id ASC
        """, (project_id, channel_id, limit))
        rows = cursor.fetchall()


    results = []
    for r in rows:
        reacts = []
        if r["react_json"]:
            try:
                reacts = json.loads(r["react_json"])
            except Exception:
                pass
        results.append({
            "id": r["id"],
            "project_id": r["project_id"],
            "channel_id": r["runtime_id"],
            "author": r["author"],
            "created_time": r["created_time"],
            "tag": r["tag"],
            "body": r["body"],
            "reacts": reacts,
            "reply_to": r["reply_to"],
            "created_at": r["created_at"] or ""
        })
    return results

# ---------------------------------------------------------------------------
# Việc gắn VIEC-n (Kho Ryan) + webhook sự kiện (Issue #12)
# ---------------------------------------------------------------------------
VIEC_REF_RE = re.compile(r"^VIEC-[0-9]+$")
VIEC_REF_ERROR = "Thiếu viec_ref (mã việc trong Kho Ryan, vd VIEC-12)"
EVENT_WEBHOOK_TIMEOUT_SEC = 5

def validate_viec_ref(viec_ref):
    """(ok, giá trị đã chuẩn hóa). Hợp lệ khi khớp ^VIEC-[0-9]+$."""
    v = (viec_ref or "").strip().upper()
    return (bool(VIEC_REF_RE.match(v)), v)

def get_task_viec_ref(task_id, project_id="PRJ-GEN-WORKPLACE"):
    """viec_ref của task trong todos hoặc gen_session_todos ('' nếu không có)."""
    if not task_id:
        return ""
    try:
        with get_connection() as conn:
            for tbl in ("todos", "gen_session_todos"):
                r = conn.execute(f"SELECT viec_ref FROM {tbl} WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
                if r:
                    return r["viec_ref"] or ""
    except Exception:
        pass
    return ""

def send_event_webhook(event, project_id="PRJ-GEN-WORKPLACE", viec_ref="", task_id="", session_id="", exit_code=None,
                       report_path="", evidence_ref="", verified_by="", status=""):
    """
    POST JSON tới GW_EVENT_WEBHOOK_URL (env; rỗng = tắt) khi task hoàn tất / dispatch kết thúc.
    Payload: {event, project_id, viec_ref, task_id, session_id, exit_code, status, report_path, evidence_ref, verified_by, at}.
    status (dispatch_finished): done | failed — exit_code 0 vẫn có thể failed khi agy bị auto-denied (#9).
    Timeout 5s; lỗi chỉ log, không ném. Trả True khi gửi được (HTTP 2xx).
    """
    url = (os.environ.get("GW_EVENT_WEBHOOK_URL") or "").strip()
    if not url:
        return False
    payload = {
        "event": event,
        "project_id": normalize_project_id(project_id),
        "viec_ref": viec_ref or "",
        "task_id": task_id or "",
        "session_id": session_id or "",
        "exit_code": exit_code,
        "status": status or "",
        "report_path": report_path or "",
        "evidence_ref": evidence_ref or "",
        "verified_by": verified_by or "",
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "User-Agent": "gen-workplace-webhook"}, method="POST")
        with urllib.request.urlopen(req, timeout=EVENT_WEBHOOK_TIMEOUT_SEC) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                print(f"[webhook] {event} → HTTP {resp.status}")
            return ok
    except Exception as e:
        print(f"[webhook] Không gửi được {event} tới {url}: {e}")
        return False

def get_dispatch_log(limit=50, task_id="", session_id=""):
    """Nhật ký dispatch_log (mới nhất trước) cho GET /api/dispatch/log; lọc theo task_id và/hoặc session_id (khớp đúng)."""
    try:
        limit = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        limit = 50
    where, params = [], []
    if (task_id or "").strip():
        where.append("task_id = ?")
        params.append(task_id.strip())
    if (session_id or "").strip():
        where.append("session_id = ?")
        params.append(session_id.strip())
    sql = "SELECT * FROM dispatch_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    with get_connection() as conn:
        rows = conn.execute(sql, params + [limit]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["status"] = _row_status(d)
        d["fallback"] = bool(d.get("profile_used")) and (d.get("profile_used") or "") != (d.get("profile_initial") or "")
        out.append(d)
    return out

# ---------------------------------------------------------------------------
# Chờ kết quả worker phía server (Issue #9): wait_worker_result
# ---------------------------------------------------------------------------
# Chỗ ghi kết quả dispatch (dispatch_warroom_to_agent, watcher tmux) gọi _notify_dispatch_change();
# wait_worker_result chờ trên Condition này, kèm poll DB mỗi giây (tiến trình MCP stdio riêng không nhận được notify).
_DISPATCH_COND = threading.Condition()
# Worker ngoài đăng ký hàm làm mới 1 dòng dispatch_log đang running theo kind (vd 'jules' → jules_worker.refresh_for_wait).
# wait_worker_result gọi hàm này thay vì đánh dấu quá hạn kiểu war-room. db không import module worker (tránh vòng import).
DISPATCH_POLLERS = {}
WAIT_WORKER_DEFAULT_SEC = 60
WAIT_WORKER_MAX_SEC = 120          # không vượt timeout HTTP của MCP
WAIT_WORKER_POLL_SEC = 1.0
DISPATCH_SUMMARY_MAX = 2000
XONG_RE = re.compile(r"=== XONG exit=(\d+) ===")
# agy -p thoát 0 nhưng tool bị từ chối quyền / không in kết quả (vd "jetski: no output produced — a tool required
# the "command" permission … auto-denied") → coi là failed. Chỉ xét output ngắn để câu trả lời thật (dài) có nhắc
# tới các cụm này không bị đánh nhầm là lỗi.
AGY_DENIED_RE = re.compile(r"no output produced|auto-denied", re.IGNORECASE)
AGY_DENIED_MAX_LEN = 2000

def agy_output_denied(output):
    """Cụm khớp nếu output agy (ngắn) cho thấy bị từ chối quyền / không có kết quả; rỗng output cũng tính; ngược lại ''."""
    out = (output or "").strip()
    if not out:
        return "empty output"
    if len(out) > AGY_DENIED_MAX_LEN:
        return ""
    m = AGY_DENIED_RE.search(out)
    return m.group(0) if m else ""

def _notify_dispatch_change():
    with _DISPATCH_COND:
        _DISPATCH_COND.notify_all()

def _current_task_of(session_id):
    """current_task_id của worker ('' nếu không có)."""
    try:
        with get_connection() as conn:
            r = conn.execute("SELECT current_task_id FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone()
            return (r["current_task_id"] or "") if r else ""
    except Exception:
        return ""

def _shorten_output(text, limit=DISPATCH_SUMMARY_MAX):
    """Rút gọn output: giữ nguyên nếu ngắn, không thì nửa đầu + nửa cuối."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n…(lược {len(text) - 2 * half} ký tự)…\n{text[-half:]}"

def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def start_dispatch_log(session_id, kind="warroom", channel_id="", task_id="", command="", report_path="", viec_ref="", request_msg_id=None):
    """Ghi 1 dòng dispatch_log status=running lúc bắt đầu giao việc; trả id (= dispatch_id cho wait_worker_result)."""
    with get_connection() as conn:
        cur = conn.execute("""
        INSERT INTO dispatch_log (session_id, command, exit_code, report_path, started_at, finished_at, task_id, viec_ref, channel_id, webhook_sent, status, kind, summary, request_msg_id)
        VALUES (?, ?, NULL, ?, ?, '', ?, ?, ?, 0, 'running', ?, '', ?)
        """, (session_id, command, report_path, time.strftime("%Y-%m-%d %H:%M:%S"), task_id or "", viec_ref or "", channel_id, kind, request_msg_id))
        conn.commit()
        return cur.lastrowid

def _get_dispatch_row(dispatch_id):
    with get_connection() as conn:
        r = conn.execute("SELECT * FROM dispatch_log WHERE id = ?", (dispatch_id,)).fetchone()
        return dict(r) if r else None

def _row_status(row):
    """status của dòng dispatch_log; dòng cũ (trước #9) không có status → suy từ finished_at/exit_code."""
    st = (row.get("status") or "").strip()
    if st:
        return st
    if row.get("finished_at"):
        return "done" if row.get("exit_code") == 0 else "failed"
    return "running"

def _read_report_tail(report_path):
    try:
        path = os.path.expanduser(report_path or "")
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception:
        pass
    return ""

def _finish_tmux_dispatch(row, exit_code, output, project_id="PRJ-GEN-WORKPLACE", fail_reason=""):
    """Chốt dòng tmux đang running → done/failed (chỉ 1 lần, UPDATE ... WHERE status='running'), rồi bắn webhook dispatch_finished."""
    status = "done" if exit_code == 0 and not fail_reason else "failed"
    summary = _shorten_output(f"[{fail_reason}]\n{output}" if fail_reason else output)
    if row.get("fallback_reason") and not fail_reason and (row.get("profile_used") or "") != (row.get("profile_initial") or ""):
        summary = f"[Tài khoản] {row['fallback_reason']}.\n{summary}"
    with get_connection() as conn:
        cur = conn.execute("""
        UPDATE dispatch_log SET status = ?, exit_code = ?, finished_at = ?, summary = ?
        WHERE id = ? AND status = 'running'
        """, (status, exit_code, time.strftime("%Y-%m-%d %H:%M:%S"), summary, row["id"]))
        conn.commit()
        claimed = cur.rowcount == 1
    if claimed:
        viec_ref = row.get("viec_ref") or get_task_viec_ref(row.get("task_id"), project_id)
        sent = send_event_webhook("dispatch_finished", project_id=project_id, viec_ref=viec_ref, task_id=row.get("task_id") or "",
                                  session_id=row["session_id"], exit_code=exit_code, report_path=row.get("report_path") or "", status=status)
        if sent:
            with get_connection() as conn:
                conn.execute("UPDATE dispatch_log SET webhook_sent = 1 WHERE id = ?", (row["id"],))
                conn.commit()
        report_dispatch_to_task(row["id"], project_id)
        _notify_dispatch_change()
    return claimed

def poll_tmux_dispatch(dispatch_id):
    """
    Kiểm lệnh giao việc qua tmux (kind=tmux) đã xong chưa: tìm tên file báo cáo (duy nhất theo thời điểm) trong
    toàn bộ lịch sử pane, rồi dòng `=== XONG exit=N ===` phía sau nó. Thấy → chốt done/failed. Trả status hiện tại.
    """
    row = _get_dispatch_row(dispatch_id)
    if not row or row.get("kind") != "tmux" or _row_status(row) != "running":
        return _row_status(row) if row else "not_found"
    anchor = os.path.basename(os.path.expanduser(row.get("report_path") or ""))
    if not anchor:
        return "running"
    try:
        res = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-S", "-", "-t", row["session_id"]],
                             capture_output=True, text=True, timeout=3.0)
    except Exception:
        return "running"
    if res.returncode != 0:
        return "running"
    pane = res.stdout or ""
    idx = pane.rfind(anchor)
    if idx < 0:
        return "running"
    m = XONG_RE.search(pane, idx)
    if not m:
        return "running"
    exit_code = int(m.group(1))
    output = _read_report_tail(row.get("report_path")) or pane[idx:m.start()]
    if row.get("profile_used"):
        q_status, q_reset = record_quota_probe_output(row["profile_used"], "default", exit_code, output)
        if q_status == "rate_limited":
            nxt = _tmux_quota_fallback(row, output, q_reset)
            if nxt == "running":
                return "running"
            if nxt:
                _finish_tmux_dispatch(row, exit_code if exit_code else 1, output, fail_reason=nxt)
                return "failed"
    denied = agy_output_denied(output) if exit_code == 0 else ""
    _finish_tmux_dispatch(row, exit_code, output, fail_reason=f"agy bị từ chối quyền / không ra kết quả ({denied})" if denied else "")
    return "done" if exit_code == 0 and not denied else "failed"

def _tmux_quota_fallback(row, output, reset_raw=""):
    """
    Lệnh giao task qua tmux bị hết quota (#22): chọn hồ sơ khác khả dụng, gõ lại đúng lệnh với --gemini_dir mới vào phiên tmux
    của vai (qua directive_guard), cập nhật dòng dispatch_log (vẫn running). Trả 'running' khi đã gõ lại; chuỗi lý do failed khi
    hết hồ sơ khả dụng / không gõ lại được.
    """
    sid = row["session_id"]
    used = row.get("profile_used") or ""
    try:
        tried = json.loads(row.get("profiles_tried") or "[]")
        tried = [x for x in tried if isinstance(x, str)]
    except Exception:
        tried = []
    if used and used not in tried:
        tried.append(used)
    account, primary_dir, title = row.get("profile_initial") or used, "", ""
    try:
        with get_connection() as conn:
            s_row = conn.execute("SELECT account_type, profile_dir FROM tmux_sessions WHERE id = ?", (sid,)).fetchone()
            if s_row:
                account = row.get("profile_initial") or s_row["account_type"] or "owner_default"
                primary_dir = os.path.expanduser(s_row["profile_dir"]) if s_row["profile_dir"] else _profile_dir(account)
            for tbl in ("todos", "gen_session_todos"):
                t = conn.execute(f"SELECT title FROM {tbl} WHERE id = ?", (row.get("task_id") or "",)).fetchone()
                if t:
                    title = t["title"]
                    break
    except Exception:
        pass
    primary_dir = primary_dir or _profile_dir(account)
    sel = select_agy_profile_for_run(account, primary_dir, tried=tried)
    if not sel["profile_id"]:
        return sel["note"]
    if sel["profile_id"] != account:
        copy_agy_allow_rules(primary_dir, sel["p_dir"])
    cmd, report_path = build_task_directive(sid, row.get("task_id") or "", title, sel["p_dir"])
    try:
        from backend import directive_guard as _guard
    except ImportError:
        import directive_guard as _guard
    allowed, reason = _guard.guard(sid, cmd)
    log_directive_audit(sid, "quota_fallback", cmd, allowed, reason)
    if not allowed:
        return f"hết quota ở {used}; không gõ lại được lệnh với {sel['profile_id']} (directive_guard: {reason})"
    try:
        ok = subprocess.run(["tmux", "send-keys", "-t", sid, cmd, "Enter"], capture_output=True, text=True, timeout=2.0).returncode == 0
    except Exception:
        ok = False
    if not ok:
        return f"hết quota ở {used}; tmux không nhận lệnh chạy lại với {sel['profile_id']}"
    note = sel["note"]
    with get_connection() as conn:
        conn.execute("""
        UPDATE dispatch_log SET command = ?, report_path = ?, profile_used = ?, profiles_tried = ?, fallback_reason = ?
        WHERE id = ? AND status = 'running'
        """, (cmd, os.path.expanduser(report_path), sel["profile_id"], json.dumps(tried), note, row["id"]))
        conn.commit()
    try:
        append_tmux_output(sid, cmd, f"[Tài khoản] {note}")
    except Exception:
        pass
    _notify_dispatch_change()
    return "running"

TMUX_WATCH_MAX_SEC = int(os.environ.get("GW_TMUX_WATCH_MAX_SEC", str(2 * 3600)))
TMUX_WATCH_INTERVAL_SEC = 2.0

def start_tmux_dispatch_watcher(dispatch_id, max_sec=None):
    """Thread nền theo dõi lệnh tmux tới khi có dòng XONG (→ chốt + webhook, không cần ai poll) hoặc hết max_sec."""
    max_sec = TMUX_WATCH_MAX_SEC if max_sec is None else max_sec

    def _loop():
        deadline = time.time() + max_sec
        while time.time() < deadline:
            try:
                if poll_tmux_dispatch(dispatch_id) != "running":
                    return
            except Exception as e:
                print(f"[dispatch-watch] #{dispatch_id}: {e}")
            time.sleep(TMUX_WATCH_INTERVAL_SEC)

    t = threading.Thread(target=_loop, daemon=True, name=f"tmux-dispatch-watch-{dispatch_id}")
    t.start()
    return t

def _mark_stale_warroom(row):
    """Dòng warroom running quá timeout agy + 2 phút → thread đã mất (app khởi động lại) → failed."""
    try:
        started = time.mktime(time.strptime(row.get("started_at") or "", "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return False
    limit = AGY_BUILD_TIMEOUT_SEC + AGY_BUILD_POST_SEC if row.get("kind") == "build" else WARROOM_DISPATCH_TIMEOUT_SEC
    if time.time() - started < limit + 120:
        return False
    with get_connection() as conn:
        cur = conn.execute("""
        UPDATE dispatch_log SET status = 'failed', finished_at = ?, summary = ?
        WHERE id = ? AND status = 'running'
        """, (time.strftime("%Y-%m-%d %H:%M:%S"), "Quá hạn mà không có kết quả (app có thể đã khởi động lại khi agy đang chạy).", row["id"]))
        conn.commit()
        return cur.rowcount == 1

def _resolve_dispatch_id(dispatch_id=None, task_id="", session_id=""):
    """dispatch_id trực tiếp, hoặc lần giao việc mới nhất của task_id / session_id."""
    if dispatch_id not in (None, ""):
        try:
            return int(dispatch_id)
        except (TypeError, ValueError):
            return None
    with get_connection() as conn:
        if task_id:
            r = conn.execute("SELECT id FROM dispatch_log WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        elif session_id:
            r = conn.execute("SELECT id FROM dispatch_log WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        else:
            return None
        return r["id"] if r else None

def _task_info(task_id, project_id="PRJ-GEN-WORKPLACE"):
    if not task_id:
        return {}
    try:
        with get_connection() as conn:
            for tbl in ("todos", "gen_session_todos"):
                r = conn.execute(f"SELECT status, evidence_ref, viec_ref FROM {tbl} WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
                if r:
                    return {"task_status": r["status"] or "", "evidence": r["evidence_ref"] or "", "viec_ref": r["viec_ref"] or ""}
    except Exception:
        pass
    return {}

def wait_worker_result(dispatch_id=None, task_id="", session_id="", timeout_sec=WAIT_WORKER_DEFAULT_SEC, project_id="PRJ-GEN-WORKPLACE"):
    """
    Chờ phía server tới khi lần giao việc kết thúc hoặc hết timeout_sec (mặc định 60, tối đa 120) (#9).
    Chọn lần giao việc theo dispatch_id, hoặc lần mới nhất của task_id / session_id.
    Trả {status: done|failed|running|not_found, dispatch_id, session_id, kind, exit_code, summary, report_path,
         task_id, viec_ref, task_status, evidence, started_at, finished_at, webhook_sent, waited_sec, timeout_sec}.
    running = hết giờ mà chưa xong → gọi lại với cùng dispatch_id.
    """
    project_id = normalize_project_id(project_id)
    try:
        timeout_sec = float(timeout_sec if timeout_sec not in (None, "") else WAIT_WORKER_DEFAULT_SEC)
    except (TypeError, ValueError):
        timeout_sec = WAIT_WORKER_DEFAULT_SEC
    timeout_sec = max(0.0, min(timeout_sec, float(WAIT_WORKER_MAX_SEC)))
    if timeout_sec == int(timeout_sec):
        timeout_sec = int(timeout_sec)
    task_id = (task_id or "").strip()
    session_id = (session_id or "").strip()
    if dispatch_id in (None, "") and not task_id and not session_id:
        return {"status": "error", "error": "Cần dispatch_id, task_id hoặc session_id"}

    did = _resolve_dispatch_id(dispatch_id, task_id, session_id)
    row = _get_dispatch_row(did) if did is not None else None
    if not row:
        info = _task_info(task_id, project_id)
        if info.get("task_status") == "done":
            # Task đã nghiệm thu (complete_task) mà không qua dispatch: coi là xong, trả bằng chứng
            return {"status": "done", "dispatch_id": None, "kind": "task", "task_id": task_id, "session_id": session_id,
                    "exit_code": None, "summary": "", "report_path": "", **info, "waited_sec": 0.0, "timeout_sec": timeout_sec}
        res = {"status": "not_found", "error": "Không tìm thấy lần giao việc (dispatch_log) khớp tham số",
               "dispatch_id": dispatch_id, "task_id": task_id, "session_id": session_id}
        res.update(info)
        return res

    t0 = time.time()
    deadline = t0 + timeout_sec
    while True:
        status = _row_status(row)
        if status == "running":
            if row.get("kind") == "tmux":
                status = poll_tmux_dispatch(did)
            elif row.get("kind") in DISPATCH_POLLERS:
                try:
                    status = DISPATCH_POLLERS[row["kind"]](did) or "running"
                except Exception as e:
                    print(f"[dispatch] làm mới dispatch:{did} ({row.get('kind')}) lỗi: {type(e).__name__}")
                    status = "running"
            elif _mark_stale_warroom(row):
                status = "failed"
            if status != "running":
                row = _get_dispatch_row(did) or row
        if status != "running":
            break
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        with _DISPATCH_COND:
            _DISPATCH_COND.wait(timeout=min(WAIT_WORKER_POLL_SEC, remaining))
        row = _get_dispatch_row(did) or row

    status = _row_status(row)
    res = {
        "status": status,
        "dispatch_id": row["id"],
        "session_id": row["session_id"],
        "kind": row.get("kind") or "warroom",
        "exit_code": row.get("exit_code") if status != "running" else None,
        "summary": row.get("summary") or "",
        "report_path": row.get("report_path") or "",
        "task_id": row.get("task_id") or "",
        "viec_ref": row.get("viec_ref") or "",
        "started_at": row.get("started_at") or "",
        "finished_at": row.get("finished_at") or "",
        "webhook_sent": bool(row.get("webhook_sent")),
        "request_msg_id": row.get("request_msg_id"),
        "reply_msg_id": row.get("reply_msg_id"),
        # Tự chuyển tài khoản khi hết quota (#22): hồ sơ gán cho vai, hồ sơ chạy thật, lý do ("đã chuyển từ … sang …")
        "profile_initial": row.get("profile_initial") or "",
        "profile_used": row.get("profile_used") or "",
        "fallback": bool(row.get("profile_used")) and (row.get("profile_used") or "") != (row.get("profile_initial") or ""),
        "fallback_reason": row.get("fallback_reason") or "",
        # worker ngoài (#43): engine ('jules'), id phiên bên ngoài, trạng thái bên ngoài, URL PR đã mở
        "engine": row.get("engine") or "",
        "ext_session_id": row.get("ext_session_id") or "",
        "ext_state": row.get("ext_state") or "",
        "ext_url": row.get("ext_url") or "",
        "pr_url": row.get("pr_url") or "",
        # chế độ Làm (#45): nhánh wt/TSK-n, commit SHA, kết quả py_compile + test, push, link compare
        **build_dispatch_fields(row),
        "waited_sec": round(time.time() - t0, 1),
        "timeout_sec": timeout_sec,
    }
    info = _task_info(res["task_id"], project_id)
    res["task_status"] = info.get("task_status", "")
    res["evidence"] = info.get("evidence", "")
    if not res["viec_ref"]:
        res["viec_ref"] = info.get("viec_ref", "")
    if status == "running":
        res["hint"] = f"Chưa xong sau {timeout_sec:g}s; gọi lại wait_worker_result(dispatch_id={row['id']})."
    return res

WARROOM_ROLE_SESSIONS = {
    "backend": "gw-backend-agy",
    "devops": "gw-devops-agy",
    "qa": "gw-qa-agy",
    "lead": "gw-lead-agy",
}
WARROOM_MENTION_RE = re.compile(r"@(backend|devops|qa|lead)\b", re.IGNORECASE)
# @Gen / @Toàn Đội / @all: không giao việc (war-room chỉ giao cho vai cụ thể; Gen trả lời ở Bàn làm việc Gen)
WARROOM_BROADCAST_RE = re.compile(r"@(gen|all|toàn\s*đội)(?![\w-])", re.IGNORECASE)
TASK_REF_RE = re.compile(r"(?<![\w-])(TSK-\d+)(?![\w-])")
WARROOM_DISPATCH_TIMEOUT_SEC = 15 * 60
# Chế độ Làm (#45): 1 lần agy sửa code + app chạy test/push; quá hạn này (cộng 2 phút) mà dòng vẫn running → coi là mất thread
AGY_BUILD_TIMEOUT_SEC = int(os.environ.get("GW_AGY_BUILD_TIMEOUT_SEC", str(30 * 60)) or 30 * 60)
AGY_BUILD_POST_SEC = 20 * 60     # ngân sách thời gian app chạy py_compile + toàn bộ test + push sau khi agy xong

def _worktree_root():
    """Thư mục chứa worktree riêng của từng vai (GW_WORKTREE_ROOT, mặc định <BASE_DIR>/../gw-worktrees)."""
    return os.environ.get("GW_WORKTREE_ROOT") or str(BASE_DIR.parent / "gw-worktrees")

def ensure_role_worktree(session_id):
    """Đảm bảo git worktree riêng của vai tại <root>/<session_id> trên nhánh wt/<session_id>; lỗi git → trả về thư mục repo."""
    repo = os.environ.get("GW_DISPATCH_REPO") or str(BASE_DIR)
    wt_dir = os.path.join(_worktree_root(), session_id)
    if os.path.exists(os.path.join(wt_dir, ".git")):
        return wt_dir
    branch = f"wt/{session_id}"
    try:
        os.makedirs(_worktree_root(), exist_ok=True)
        has_branch = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                                    capture_output=True, timeout=10).returncode == 0
        args = ["git", "-C", repo, "worktree", "add"] + ([wt_dir, branch] if has_branch else ["-b", branch, wt_dir])
        res = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            return wt_dir
        print(f"[dispatch] git worktree add thất bại cho {session_id}: {(res.stderr or '').strip()[:200]}")
    except Exception as e:
        print(f"[dispatch] Không tạo được worktree cho {session_id}: {e}")
    return repo

# ---------------------------------------------------------------------------
# Quyền của agy (Issue #7 + lỗi auto-denied của war-room)
# ---------------------------------------------------------------------------
AGY_SKIP_PERMISSIONS_FLAG = "--dangerously-skip-permissions"
# Lệnh chỉ đọc mà agy --mode plan được chạy không cần hỏi (permissions.allow của agy, khớp theo TIỀN TỐ TOKEN: command(grep)
# cho cả "grep -n x f", "grep -rn x dir | head"; &&, |, ; vẫn khớp từng lệnh). Chỉ lệnh không ghi/xóa, không mạng, không chạy mã tùy ý.
# KHÔNG có: python3 -c / node -e / bash -c / xargs / awk (chạy mã tùy ý, không giới hạn được về chỉ đọc), sed/find/git branch dạng tự do.
AGY_PLAN_READONLY_COMMANDS = ["ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "tree", "cd",
                              "find", "stat", "file",
                              "git status", "git log", "git diff", "git show", "git blame", "git ls-files", "git grep", "git rev-parse"]
# Dạng regex (mỗi token là 1 regex neo ^(?:...)$): chỉ mở đúng dạng chỉ đọc của lệnh có thể ghi
AGY_PLAN_READONLY_REGEX = [
    # sed -n 'N,Mp' / '$p' / 'N,+Kp' (in theo dòng) — không mở sed tự do (sed -i, lệnh w/e trong script)
    r"""sed -n ['"]?([0-9]+|\$)(,\+?([0-9]+|\$))?p['"]?""",
    # git branch chỉ ở dạng liệt kê (git branch -D / -m / tên-nhánh-mới là lệnh ghi)
    r"git branch (--show-current|-a|--all|-r|--remotes|-v|-vv|--list|-l|--contains|--merged|--no-merged)",
]
# Cờ ghi / chạy lệnh con của các lệnh ở trên → permissions.deny (Deny > Allow), đặt ở mọi vị trí token sau tên lệnh.
AGY_PLAN_DENY_FLAGS = [
    ("find", r"-(delete|exec|execdir|ok|okdir|fprint|fprint0|fprintf|fls)"),
    ("sed -n", r"-[a-zA-Z]*[ief].*|--(in-place|expression|file).*"),
    ("rg", r"--pre(=.*)?"),
    ("tree", r"-[a-zA-Z]*o[a-zA-Z]*|--output.*"),
    ("file", r"-[a-zA-Z]*C[a-zA-Z]*|--compile"),
    ("git (diff|log|show)", r"--output(=.*)?"),
    ("git grep", r"-[a-zA-Z]*O.*|--open-files-in-pager.*"),
    ("git branch", r"-[a-zA-Z]*[dDmMcCfu].*|--(delete|move|copy|force|set-upstream-to|unset-upstream|edit-description|track|no-track|create-reflog|recurse-submodules).*"),
]
AGY_PLAN_DENY_MAX_POS = 10
# Quy tắc cũ app từng ghi nhưng quá rộng → gỡ khỏi permissions.allow (command(git branch) cho cả git branch -D)
AGY_PLAN_RETIRED_RULES = ["command(git branch)"]


def agy_plan_allow_rules():
    """Các rule command(...) chỉ đọc app ghi vào permissions.allow (không gồm read_file)."""
    return [f"command({c})" for c in AGY_PLAN_READONLY_COMMANDS] + [f"command(regex:{r})" for r in AGY_PLAN_READONLY_REGEX]


def agy_plan_deny_rules():
    """Rule deny cho cờ ghi/chạy lệnh con: command(regex:<lệnh> [.* ...] <cờ>) với 0..AGY_PLAN_DENY_MAX_POS token ở giữa."""
    rules = []
    for cmd, flag in AGY_PLAN_DENY_FLAGS:
        for k in range(AGY_PLAN_DENY_MAX_POS + 1):
            rules.append(f"command(regex:{' '.join([cmd] + ['.*'] * k + ['(' + flag + ')'])})")
    return rules


def agy_plan_allowed_summary():
    """Danh sách lệnh được phép dạng dễ đọc (cho prompt agy)."""
    return ", ".join(AGY_PLAN_READONLY_COMMANDS[:13] + ["sed -n 'N,Mp'", "git branch --show-current|-a|-r|-v"]
                     + AGY_PLAN_READONLY_COMMANDS[13:])


_AGY_SETTINGS_LOCK = threading.Lock()

def _env_on(name, default=""):
    return (os.environ.get(name, default) or "").strip().lower() in ("1", "true", "yes", "on")

def agy_write_roles():
    """Tập vai được bật quyền ghi không hỏi cho agy-run (GW_AGY_WRITE_ROLES="backend,gw-devops-agy"; rỗng = không vai nào)."""
    raw = os.environ.get("GW_AGY_WRITE_ROLES", "")
    return {x.strip().lower() for x in re.split(r"[,\s]+", raw) if x.strip()}

def role_may_skip_permissions(session_id):
    """Vai có trong GW_AGY_WRITE_ROLES (theo session_id 'gw-backend-agy' hoặc tên ngắn 'backend')."""
    sid = (session_id or "").strip().lower()
    roles = agy_write_roles()
    short = sid[3:-4] if sid.startswith("gw-") and sid.endswith("-agy") else sid
    return bool(sid) and (sid in roles or short in roles)

def is_role_worktree(session_id, cwd):
    """cwd đúng là worktree riêng <GW_WORKTREE_ROOT>/<session_id> (có .git), không phải repo đang chạy app."""
    if not cwd or not session_id:
        return False
    wt = os.path.realpath(os.path.join(_worktree_root(), session_id))
    repo = os.path.realpath(os.environ.get("GW_DISPATCH_REPO") or str(BASE_DIR))
    real = os.path.realpath(cwd)
    return real == wt and real != repo and os.path.exists(os.path.join(wt, ".git"))

def ensure_agy_plan_permissions(p_dir, cwd, extra_dirs=()):
    """
    Thêm quy tắc chỉ đọc vào permissions.allow của hồ sơ agy (<p_dir>/antigravity-cli/settings.json) để agy -p
    đọc được file và chạy lệnh chỉ đọc trong thư mục làm việc (worktree của vai / thư mục chat Gen) thay vì bị auto-denied:
      allow: read_file(<cwd>) [+ read_file(<extra_dirs>...)] + agy_plan_allow_rules() (ls|cat|grep|find|sed -n 'N,Mp'|git log|...)
      deny:  agy_plan_deny_rules() — cờ ghi / chạy lệnh con của các lệnh trên (find -delete/-exec, sed -i, rg --pre, git --output...)
    Gỡ quy tắc cũ quá rộng (AGY_PLAN_RETIRED_RULES). Giữ nguyên mọi khóa khác; file hỏng / permissions sai kiểu → không đụng.
    Tắt bằng GW_AGY_PLAN_ALLOW=0. Trả danh sách quy tắc vừa thêm (allow + deny); không đổi gì → [].
    """
    if (os.environ.get("GW_AGY_PLAN_ALLOW", "1") or "").strip().lower() in ("0", "false", "no", "off"):
        return []
    cli_dir = os.path.join(p_dir or "", "antigravity-cli")
    # Chỉ ghi vào hồ sơ agy đã có thư mục antigravity-cli: tạo mới sẽ làm _agy_env đổi ANTIGRAVITY_APP_DATA_DIR của hồ sơ
    if not p_dir or not os.path.isdir(cli_dir) or not cwd:
        return []
    dirs = []
    for d in [cwd] + [x for x in (extra_dirs or ()) if x]:
        real = os.path.realpath(str(d))
        if real not in dirs:
            dirs.append(real)
    rules = [f"read_file({d})" for d in dirs] + agy_plan_allow_rules()
    deny_rules = agy_plan_deny_rules()
    path = os.path.join(cli_dir, "settings.json")
    with _AGY_SETTINGS_LOCK:
        try:
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, dict):
                return []
            perms = data.setdefault("permissions", {})
            if not isinstance(perms, dict):
                return []
            allow = perms.setdefault("allow", [])
            deny = perms.setdefault("deny", [])
            if not isinstance(allow, list) or not isinstance(deny, list):
                return []
            added = [r for r in rules if r not in allow]
            added_deny = [r for r in deny_rules if r not in deny]
            retired = [r for r in AGY_PLAN_RETIRED_RULES if r in allow]
            # Rule chế độ Làm còn sót (app khởi động lại giữa lần build) mà hồ sơ không còn lần build nào chạy → gỡ (#45)
            if not any(v > 0 for v in _AGY_BUILD_ACTIVE.get(path, {}).values()):
                retired += [r for r in allow if is_agy_build_rule(r) and r not in retired]
            if not added and not added_deny and not retired:
                return []
            allow[:] = [r for r in allow if r not in retired] + added
            deny.extend(added_deny)
            tmp = f"{path}.gw-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            return added + added_deny
        except Exception as e:
            print(f"[agy-perm] Không cập nhật được {path}: {e}")
            return []

# ---------------------------------------------------------------------------
# Quyền agy cho chế độ "Làm" (build, Issue #45): agy -p ở chế độ mặc định (không --mode plan) trong worktree riêng của task
# ---------------------------------------------------------------------------
# Lệnh thêm cho chế độ Làm (ngoài lệnh chỉ đọc): biên dịch thử, chạy test của repo, git trong worktree (cwd của agy).
# echo: agy hay nối "&& echo OK" sau lệnh kiểm (dispatch:13 bị chặn vì thiếu echo, #47)
AGY_BUILD_EXTRA_COMMANDS = ["python3 -m py_compile", "git add", "git commit", "echo"]
AGY_BUILD_EXTRA_REGEX = [r"python3 scripts/test_[A-Za-z0-9_]+\.py"]
# Lệnh cấm hẳn (khớp tiền tố token, Deny > Allow): đẩy / đổi remote, mạng, quyền root, cài gói
AGY_BUILD_DENY_COMMANDS = [
    "git push", "git remote", "git fetch", "git pull", "git clone", "git ls-remote", "git submodule", "git worktree",
    "git update-ref", "git symbolic-ref", "git config", "git switch", "git -C", "git -c", "git --git-dir", "git --work-tree",
    "git --exec-path", "git clean", "git filter-branch", "git gc", "git reflog",
    "curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet", "ftp", "socat", "gh",
    "sudo", "su", "doas", "pkexec",
    "pip", "pip3", "python3 -m pip", "python -m pip", "python3 -m venv", "python3 -m http.server",
    "npm", "npx", "yarn", "pnpm", "apt", "apt-get", "dpkg", "snap", "brew", "gem", "cargo", "go install", "go get",
]
# Cờ / đích nguy hiểm ở mọi vị trí token (0..AGY_PLAN_DENY_MAX_POS token ở giữa): (lệnh, regex token)
AGY_BUILD_DENY_FLAGS = [
    ("git checkout", r"(main|master|origin/.*|refs/.*|-b|-B|--orphan|--detach|-f|--force)"),
    ("git reset", r"(--hard|--merge|--keep)"),
    ("git commit", r"(-[a-zA-Z]*n[a-zA-Z]*|--no-verify|--amend)"),
    ("git", r"(-C|-c|--git-dir(=.*)?|--work-tree(=.*)?|--exec-path(=.*)?|--namespace(=.*)?)"),
    ("rm", r"(-[a-zA-Z]*[rRf][a-zA-Z]*|--recursive|--force|/.*|~.*|\$.*|.*\.\..*)"),
]
# Không chặn `cd`: agy hay cd tới đường dẫn tuyệt đối của chính worktree (#47); commit / push sai chỗ đã có hook pre-commit /
# pre-push + kiểm sau khi chạy (detect_violations), ghi file ngoài worktree bị write_file deny.
# Rule chỉ chế độ Làm ghi (để nhận ra và gỡ khi không còn lần build nào dùng hồ sơ đó)
_AGY_BUILD_ACTIVE = {}      # settings.json → {rule: số lần build đang dùng}
_AGY_BUILD_OWNED = {}       # settings.json → {rule} do chế độ Làm thêm vào (không phải rule người dùng có sẵn)


def agy_build_command_allow_rules():
    """command(...) chế độ Làm: lệnh chỉ đọc + py_compile + scripts/test_*.py + git add/commit (không gồm read/write_file)."""
    return (agy_plan_allow_rules() + [f"command({c})" for c in AGY_BUILD_EXTRA_COMMANDS]
            + [f"command(regex:{r})" for r in AGY_BUILD_EXTRA_REGEX])


def agy_build_test_path_rules(worktree):
    """python3 <đường dẫn tuyệt đối của worktree>/scripts/test_*.py (agy hay gọi test bằng đường dẫn tuyệt đối, dispatch:15, #49).
    Chỉ đúng worktree của task (không mở test của repo khác); đường dẫn có khoảng trắng → bỏ (rule tách token theo khoảng trắng)."""
    out = []
    for p in dict.fromkeys([os.path.realpath(str(worktree)), os.path.abspath(str(worktree))]):
        if p and not re.search(r"\s", p):
            out.append(f"command(regex:python3 {re.escape(p)}/scripts/test_[A-Za-z0-9_]+\\.py)")
    return out


def agy_build_allow_rules(worktree):
    """Allow-rule chế độ Làm cho worktree của task: đọc + GHI file trong worktree, lệnh chỉ đọc, test, git add/commit."""
    wt = os.path.realpath(str(worktree))
    return [f"read_file({wt})", f"write_file({wt})"] + agy_build_command_allow_rules() + agy_build_test_path_rules(worktree)


def _agy_build_protected_dirs(worktree, repo="", p_dir=""):
    """Thư mục cấm ghi (write_file deny): repo app, hồ sơ agy, cấu hình git / ssh / hệ thống; bỏ thư mục chứa worktree."""
    wt = os.path.realpath(str(worktree))
    cands = [repo or os.environ.get("GW_DISPATCH_REPO") or str(BASE_DIR), str(BASE_DIR), p_dir,
             os.path.join(HOME_DIR, ".gemini"), os.path.join(HOME_DIR, ".agy-profiles"), os.path.join(HOME_DIR, ".ssh"),
             os.path.join(HOME_DIR, ".config"), os.path.join(HOME_DIR, ".gitconfig"), os.path.join(HOME_DIR, ".git-credentials"),
             os.path.join(HOME_DIR, ".bashrc"), os.path.join(HOME_DIR, ".profile"), os.path.join(HOME_DIR, ".local", "bin"),
             os.path.join(HOME_DIR, "gw-reports"), str(DATA_DIR), "/etc", "/usr", "/bin", "/sbin", "/lib", "/opt", "/var"]
    out = []
    for d in cands:
        if not d:
            continue
        real = os.path.realpath(os.path.expanduser(str(d)))
        if wt == real or wt.startswith(real + os.sep):
            continue   # worktree nằm trong thư mục này → deny sẽ chặn luôn worktree (Deny > Allow)
        if real not in out:
            out.append(real)
    return out


def agy_build_deny_rules(worktree, repo="", p_dir=""):
    """Deny-rule chế độ Làm: cờ ghi của lệnh chỉ đọc + lệnh cấm (push, remote, mạng, sudo, cài gói) + cờ/đích nguy hiểm
    (checkout main, reset --hard, commit --no-verify, rm -rf / xóa ngoài worktree) + ghi file ngoài worktree."""
    rules = agy_plan_deny_rules() + [f"command({c})" for c in AGY_BUILD_DENY_COMMANDS]
    for cmd, flag in AGY_BUILD_DENY_FLAGS:
        for k in range(AGY_PLAN_DENY_MAX_POS + 1):
            rules.append(f"command(regex:{' '.join([cmd] + ['.*'] * k + ['(' + flag + ')'])})")
    rules += [f"write_file({d})" for d in _agy_build_protected_dirs(worktree, repo, p_dir)]
    return list(dict.fromkeys(rules))


def is_agy_build_rule(rule):
    """Rule chỉ chế độ Làm cấp (không chép sang hồ sơ khác khi fallback quota, gỡ khi không còn lần build nào dùng)."""
    if not isinstance(rule, str):
        return False
    if rule.startswith("write_file(") and rule.endswith(")"):
        root = os.path.realpath(_worktree_root())
        target = os.path.realpath(rule[len("write_file("):-1] or "/")
        return target == root or target.startswith(root + os.sep)
    extra = {f"command({c})" for c in AGY_BUILD_EXTRA_COMMANDS} | {f"command(regex:{r})" for r in AGY_BUILD_EXTRA_REGEX}
    if rule.startswith("command(regex:python3 /") and rule.endswith("/scripts/test_[A-Za-z0-9_]+\\.py)"):
        return True   # agy_build_test_path_rules
    return rule in extra


def _agy_settings_path(p_dir):
    cli_dir = os.path.join(p_dir or "", "antigravity-cli")
    return os.path.join(cli_dir, "settings.json") if p_dir and os.path.isdir(cli_dir) else ""


def _edit_agy_settings(path, fn):
    """Đọc settings.json của hồ sơ agy, gọi fn(allow, deny) (sửa tại chỗ, trả True nếu có đổi), ghi nguyên tử. Lỗi → False."""
    try:
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            return False
        perms = data.setdefault("permissions", {})
        if not isinstance(perms, dict):
            return False
        allow = perms.setdefault("allow", [])
        deny = perms.setdefault("deny", [])
        if not isinstance(allow, list) or not isinstance(deny, list):
            return False
        if not fn(allow, deny):
            return False
        tmp = f"{path}.gw-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[agy-perm] Không cập nhật được {path}: {e}")
        return False


def ensure_agy_build_permissions(p_dir, worktree, repo=""):
    """
    Ghi allow + deny của chế độ Làm vào <p_dir>/antigravity-cli/settings.json trước 1 lần build (Issue #45) và đếm số lần build
    đang dùng từng rule. Trả token {"path", "allow", "deny"} (các rule đã tính cho lần này) cho release_agy_build_permissions,
    hoặc None nếu hồ sơ chưa có antigravity-cli/ / file hỏng (không đụng). Tắt ghi: GW_AGY_PLAN_ALLOW=0.
    """
    if (os.environ.get("GW_AGY_PLAN_ALLOW", "1") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    path = _agy_settings_path(p_dir)
    if not path or not worktree:
        return None
    allow_rules = agy_build_allow_rules(worktree)
    deny_rules = agy_build_deny_rules(worktree, repo, p_dir)
    token = {"path": path, "allow": allow_rules, "deny": deny_rules, "added": []}
    with _AGY_SETTINGS_LOCK:
        owned = _AGY_BUILD_OWNED.setdefault(path, set())
        active = _AGY_BUILD_ACTIVE.setdefault(path, {})

        def _fn(allow, deny):
            changed = False
            for lst, rules in ((allow, allow_rules), (deny, deny_rules)):
                for r in rules:
                    if r not in lst:
                        lst.append(r)
                        owned.add(r)
                        token["added"].append(r)
                        changed = True
            return changed

        if not _edit_agy_settings(path, _fn) and token["added"]:
            owned.difference_update(token["added"])   # ghi hỏng → không nhận là rule của mình
            return None
        # rule đã có sẵn (lần build khác đang dùng / người dùng tự thêm) vẫn đếm để không bị gỡ giữa chừng
        for r in allow_rules + deny_rules:
            active[r] = active.get(r, 0) + 1
    return token


def release_agy_build_permissions(token):
    """Sau lần build: giảm đếm; rule do chế độ Làm thêm mà không còn lần build nào dùng → gỡ khỏi settings (rule có sẵn giữ nguyên)."""
    if not token or not token.get("path"):
        return []
    path = token["path"]
    removed = []
    with _AGY_SETTINGS_LOCK:
        active = _AGY_BUILD_ACTIVE.setdefault(path, {})
        owned = _AGY_BUILD_OWNED.setdefault(path, set())
        drop = set()
        for r in token["allow"] + token["deny"]:
            n = active.get(r, 0) - 1
            if n > 0:
                active[r] = n
                continue
            active.pop(r, None)
            if r in owned:
                drop.add(r)

        def _fn(allow, deny):
            before = len(allow) + len(deny)
            allow[:] = [r for r in allow if r not in drop]
            deny[:] = [r for r in deny if r not in drop]
            return len(allow) + len(deny) != before

        if drop and _edit_agy_settings(path, _fn):
            removed = sorted(drop)
        owned.difference_update(drop)
    return removed


def agy_build_active(p_dir):
    """Hồ sơ đang có lần build chạy (còn rule chế độ Làm đang được đếm)."""
    path = _agy_settings_path(p_dir)
    return bool(path) and any(v > 0 for v in _AGY_BUILD_ACTIVE.get(path, {}).values())


def copy_agy_allow_rules(src_dir, dst_dir):
    """
    Chép permissions.allow của hồ sơ agy src_dir sang dst_dir (thêm quy tắc còn thiếu, giữ nguyên mọi khóa khác) để lần chạy
    tự chuyển tài khoản (#22) giữ đúng quyền của vai. Chỉ ghi khi dst_dir đã có antigravity-cli/. Trả danh sách quy tắc vừa thêm.
    """
    if not src_dir or not dst_dir or os.path.realpath(src_dir) == os.path.realpath(dst_dir):
        return []
    src = os.path.join(src_dir, "antigravity-cli", "settings.json")
    dst_cli = os.path.join(dst_dir, "antigravity-cli")
    if not os.path.isfile(src) or not os.path.isdir(dst_cli):
        return []
    try:
        with open(src, "r", encoding="utf-8") as f:
            src_allow = ((json.load(f) or {}).get("permissions") or {}).get("allow") or []
    except Exception:
        return []
    if not isinstance(src_allow, list) or not src_allow:
        return []
    path = os.path.join(dst_cli, "settings.json")
    with _AGY_SETTINGS_LOCK:
        try:
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, dict):
                return []
            perms = data.setdefault("permissions", {})
            if not isinstance(perms, dict):
                return []
            allow = perms.setdefault("allow", [])
            if not isinstance(allow, list):
                return []
            # Không chép rule chế độ Làm (write_file, git commit…): chỉ cấp cho đúng hồ sơ + worktree của lần build (#45)
            added = [r for r in src_allow if isinstance(r, str) and r not in allow and r not in AGY_PLAN_RETIRED_RULES
                     and not is_agy_build_rule(r)]
            if not added:
                return []
            allow.extend(added)
            tmp = f"{path}.gw-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            return added
        except Exception as e:
            print(f"[agy-perm] Không chép được allow-rule sang {path}: {e}")
            return []

# ---------------------------------------------------------------------------
# Prompt chỉ đọc + đọc output stream-json của agy để biết lệnh nào bị chặn (lỗi dispatch:9)
# ---------------------------------------------------------------------------
AGY_STREAM_ARGS = ["--output-format", "stream-json"]
AGY_NEED_PERM_RE = re.compile(r"^\s*[-*>]*\s*\**\s*CẦN QUYỀN\s*\**\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_AGY_TOOL_DENY_RE = re.compile(r"permission|denied|not allowed|requires? approval|cannot prompt|auto-den", re.IGNORECASE)
_AGY_RULE_IN_TEXT_RE = re.compile(r"\b(command|read_file|write_file|read_url|execute_url|mcp)\(([^()\n]{1,300})\)")
_AGY_FLAG_UNSUPPORTED_RE = re.compile(r"(unknown|unrecognized|invalid|undefined)\s+(flag|option|argument)[^\n]*output-format|"
                                      r"output-format[^\n]*(unknown|not defined|not supported|unrecognized)", re.IGNORECASE)
_AGY_CMD_PARAM_KEYS = ("CommandLine", "commandLine", "command_line", "Command", "command", "cmd")


def build_agy_readonly_prompt(message, task_block, session_id):
    """
    Prompt agy --mode plan cho war-room và POST /api/task/assign (lỗi dispatch:9): nói rõ CHỈ ĐỌC, ưu tiên công cụ đọc file
    của agy, chỉ dùng lệnh shell trong danh sách allow-rule, không lệnh ghi; bị chặn thì ghi "CẦN QUYỀN: ..." thay vì dừng im lặng.
    """
    rules = (
        "[CHẾ ĐỘ CHỈ ĐỌC — agy --mode plan, không ai duyệt quyền giữa chừng]\n"
        "- Việc này CHỈ ĐỌC: không tạo/sửa/xóa file, không chạy lệnh ghi (không >, >>, tee, sed -i, rm, mv, cp, mkdir, "
        "git commit/push/checkout/reset...), không lệnh mạng (curl, wget, pip, npm...).\n"
        "- Ưu tiên công cụ đọc có sẵn của agy (đọc file, liệt kê thư mục, tìm file theo tên, tìm chuỗi trong code): "
        "chạy được ngay, không cần xin quyền.\n"
        f"- Nếu cần lệnh shell thì CHỈ dùng lệnh trong danh sách cho phép: {agy_plan_allowed_summary()}. "
        "Viết lệnh đơn giản; không dùng $(...), dấu `...`, {a,b}; không python/node/bash -c/xargs/awk; "
        "không find -exec/-delete, không git --output.\n"
        "- Lệnh ngoài danh sách sẽ bị hệ thống TỪ CHỐI (không ai bấm duyệt được). Nếu cần thao tác bị chặn: KHÔNG dừng im lặng — "
        "làm tiếp phần còn lại bằng công cụ đọc file, rồi ghi rõ một dòng \"CẦN QUYỀN: <lệnh hoặc thao tác> — <lý do>\" "
        "trong báo cáo."
    )
    tail = (f"(Bạn là {session_id}, đang ở worktree của repo gen-workplace. "
            "Hãy tự đọc file cần thiết rồi trả lời ĐẦY ĐỦ ngay trong một lượt bằng tiếng Việt; "
            "không hỏi lại, không chỉ nêu kế hoạch.)")
    return f"{message}\n\n" + (f"{task_block}\n\n" if task_block else "") + f"{rules}\n\n{tail}"


def _agy_tool_command(params):
    """Chuỗi lệnh shell trong tham số của 1 tool step (run_command: CommandLine); không phải lệnh shell → ''."""
    if not isinstance(params, dict):
        return ""
    for k in _AGY_CMD_PARAM_KEYS:
        v = params.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def parse_agy_stream(stdout):
    """
    Đọc stdout của agy --output-format stream-json (NDJSON: init / step_update / result).
    Trả {"is_stream", "response", "error", "status", "tools": [{"name", "command", "output", "error", "denied"}]}.
    stdout không phải stream-json (agy cũ / script giả) → is_stream=False, response = cả stdout.
    """
    info = {"is_stream": False, "response": "", "error": "", "status": "", "tools": []}
    raw = stdout or ""
    events = []
    other = []
    for line in raw.splitlines():
        t = line.strip()
        if not t:
            continue
        ev = None
        if t.startswith("{"):
            try:
                ev = json.loads(t)
            except Exception:
                ev = None
        if isinstance(ev, dict) and ev.get("event"):
            events.append(ev)
        else:
            other.append(line)
    if not events:
        info["response"] = raw.strip()
        return info
    info["is_stream"] = True
    deltas = []
    steps = {}
    order = []
    for ev in events:
        kind = ev.get("event")
        if kind == "step_update":
            su = ev.get("step_update") or {}
            if not isinstance(su, dict):
                continue
            if su.get("text_delta") and (su.get("step_type") in (None, "", "agent_response")):
                deltas.append(str(su["text_delta"]))
            ti = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else None
            if su.get("step_type") == "tool" or ti:
                key = su.get("step_index", len(order))
                if key not in steps:
                    steps[key] = {"name": "", "command": "", "output": "", "error": "", "denied": False}
                    order.append(key)
                st = steps[key]
                st["name"] = (ti or {}).get("name") or su.get("tool_name") or st["name"]
                st["command"] = _agy_tool_command((ti or {}).get("parameters")) or st["command"]
                if ti and ti.get("output"):
                    st["output"] = str(ti["output"])
                err = (ti or {}).get("error")
                if err:
                    st["error"] = (" ".join(str(err.get(k) or "") for k in ("type", "message")).strip()
                                   if isinstance(err, dict) else str(err))
                st["denied"] = bool(_AGY_TOOL_DENY_RE.search(st["error"])) or st["denied"]
        elif kind == "result":
            r = ev.get("result") or {}
            if isinstance(r, dict):
                info["response"] = (r.get("response") or "").strip()
                info["error"] = str(r.get("error") or "").strip()
                info["status"] = str(r.get("status") or "")
    if not info["response"]:
        info["response"] = "".join(deltas).strip()
    if other:   # dòng không phải JSON trên stdout (hiếm) — giữ lại để không mất thông tin
        info["response"] = (info["response"] + "\n" + "\n".join(other)).strip()
    info["tools"] = [steps[k] for k in order]
    return info


def normalize_agy_stream_result(res):
    """CompletedProcess của agy stream-json → (CompletedProcess với stdout = câu trả lời, stderr = stderr + lỗi result, info)."""
    info = parse_agy_stream(res.stdout)
    if not info["is_stream"]:
        return res, info
    stderr = (res.stderr or "")
    if info["error"] and info["error"] not in stderr:
        stderr = (stderr + "\n" + info["error"]).strip()
    return subprocess.CompletedProcess(res.args, res.returncode, info["response"], stderr), info


def agy_flag_unsupported(res):
    """agy cũ không nhận --output-format → True (chạy lại không cờ)."""
    return res is not None and res.returncode != 0 and bool(_AGY_FLAG_UNSUPPORTED_RE.search((res.stderr or "") + "\n" + (res.stdout or "")))


def agy_blocked_commands(info, output=""):
    """
    Lệnh / thao tác agy bị chặn quyền, trích từ output: (1) tool step có lỗi quyền (stream-json), (2) rule action(target) cụ thể
    trong thông báo của agy (bỏ mẫu "<target>"). Trả list chuỗi (không trùng), rỗng nếu không trích được.
    """
    found = []
    for t in (info or {}).get("tools") or []:
        if t.get("denied"):
            found.append(t.get("command") or f"{t.get('name') or 'tool'}")
    for action, target in _AGY_RULE_IN_TEXT_RE.findall(output or ""):
        if "<" in target or ">" in target:
            continue
        found.append(target.strip() if action == "command" else f"{action}({target.strip()})")
    return list(dict.fromkeys(x for x in found if x))


def agy_shell_commands(info):
    """Mọi lệnh shell agy đã gọi trong lần chạy (stream-json), theo thứ tự."""
    return [t for t in (info or {}).get("tools") or [] if t.get("command")]


def describe_agy_blocked(info, output=""):
    """Dòng mô tả lệnh bị chặn cho dispatch_log / war-room / phiên. Không trích được lệnh cụ thể → nêu lệnh shell cuối agy gọi."""
    blocked = agy_blocked_commands(info, output)
    if blocked:
        return "Lệnh bị chặn: " + " ; ".join(f"`{b[:300]}`" for b in blocked[:5]) + " → cần thêm allow-rule tương ứng (nếu là lệnh chỉ đọc)"
    shell = agy_shell_commands(info)
    if shell:
        last = shell[-1]
        return (f"Lệnh bị chặn (suy ra: lệnh shell cuối agy gọi, output không nêu tên): `{last['command'][:300]}`"
                + (f" — lỗi: {last['error'][:200]}" if last.get("error") else ""))
    if (info or {}).get("is_stream"):
        return "Lệnh bị chặn: không trích được — agy không gọi lệnh shell nào trong stream, chỉ báo thiếu quyền"
    return "Lệnh bị chặn: không trích được — output của agy không nêu lệnh"


def agy_need_permission_lines(text):
    """Các dòng "CẦN QUYỀN: ..." agy tự ghi trong báo cáo."""
    return [m.strip() for m in AGY_NEED_PERM_RE.findall(text or "")][:10]


def build_agy_aliases(session_id, p_dir, conv_id, cwd):
    """
    Alias agy/agy-run cho phiên tmux của vai (#7): mặc định KHÔNG có --dangerously-skip-permissions.
    Chỉ agy-run của vai trong GW_AGY_WRITE_ROLES, và chỉ khi phiên mở trong worktree riêng của vai, mới có cờ này.
    Trả (danh sách dòng alias, có_bật_quyền_ghi).
    """
    write = role_may_skip_permissions(session_id) and is_role_worktree(session_id, cwd)
    skip = f" {AGY_SKIP_PERMISSIONS_FLAG}" if write else ""
    return [f"alias agy=\"agy --gemini_dir='{p_dir}'\"",
            f"alias agy-run=\"agy --gemini_dir='{p_dir}'{skip} --conversation '{conv_id}'\""], write

def tmux_role_cwd(session_id):
    """Thư mục mở phiên tmux của vai: worktree riêng (như dispatch war-room); chỉ tạo khi có tmux thật (tmux -V chạy được)."""
    try:
        if subprocess.run(["tmux", "-V"], capture_output=True, timeout=2.0).returncode != 0:
            return os.environ.get("GW_DISPATCH_REPO") or str(BASE_DIR)
    except Exception:
        return os.environ.get("GW_DISPATCH_REPO") or str(BASE_DIR)
    return ensure_role_worktree(session_id)

def tmux_role_env_block(session_id, role_name, conv_id, p_dir, role_spec_file, email, cwd):
    """Phần export/alias chung của script khởi tạo tmux (ensure_real_tmux_sessions + wake_tmux_session)."""
    aliases, write = build_agy_aliases(session_id, p_dir, conv_id, cwd)
    perm_note = "BẬT cho agy-run (GW_AGY_WRITE_ROLES)" if write else "tắt (agy hỏi quyền trước khi ghi/chạy lệnh)"
    lines = [
        f"export PS1='[\\033[38;5;39m{session_id}\\033[0m:\\033[38;5;48m\\w\\033[0m]$ '",
        f"export GEN_ROLE='{role_name}'",
        f"export GEN_CONV_ID='{conv_id}'",
        f"export GEMINI_DIR='{p_dir}'",
        f"export GEN_ROLE_SPEC='{role_spec_file}'",
        *aliases,
        f"alias gw-role=\"cat '{role_spec_file}'\"",
        f"_gw_where() {{ echo \"CWD: $PWD\"; echo \"Branch: $(git branch --show-current 2>/dev/null)\"; echo 'Quyền ghi không hỏi: {perm_note}'; }}",
        f"alias gw-status=\"echo '=== SWARM ROLE: {role_name} ===' && echo 'Session: {session_id}' && echo 'Account: {email}' && echo 'ConvID: {conv_id}' && echo 'Role Spec: {role_spec_file}' && echo 'SSOT: docs/SSOT_ORIGINAL_SPEC.md' && _gw_where\"",
    ]
    return "\n".join(lines) + "\n"

def dispatch_warroom_to_agent(project_id, channel_id, session_id, message, timeout=WARROOM_DISPATCH_TIMEOUT_SEC, dispatch_id=None, reply_to=None):
    """
    Chạy agy thật (--mode plan -p <tin>) với profile của worker session_id trong worktree riêng; ghi trả lời thật vào chat_messages và dispatch_log (#3).
    dispatch_id: dòng dispatch_log (status=running) đã tạo sẵn bởi post_warroom_message; None → tự tạo. Khi xong cập nhật
    status done/failed + exit_code + summary rồi báo hiệu cho wait_worker_result (#9).
    """
    project_id = normalize_project_id(project_id)
    if dispatch_id is None:
        dispatch_id = start_dispatch_log(session_id, kind="warroom", channel_id=channel_id, task_id=_current_task_of(session_id), request_msg_id=reply_to)
    # Task của lần giao việc: đã chốt lúc tạo dòng dispatch_log (mã TSK trong tin > current_task_id của worker)
    task_id = ((_get_dispatch_row(dispatch_id) or {}).get("task_id") or "").strip()
    task_block = build_task_prompt_block(task_id, project_id) if task_id else ""
    account_type, profile_dir = "owner_default", ""
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT account_type, profile_dir FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                account_type = row["account_type"] or "owner_default"
                profile_dir = row["profile_dir"] or ""
    except Exception:
        pass
    p_dir = os.path.expanduser(profile_dir) if profile_dir else _profile_dir(account_type)
    cwd = ensure_role_worktree(session_id)
    # --mode plan: agy chỉ đọc, không sửa file. Không dùng --sandbox vì sandbox chặn cả việc đọc repo
    # (agy chỉ trả "Để tôi khám phá..." rồi dừng). Dặn trả lời trọn trong một lượt vì -p không tương tác.
    # Prompt nói rõ CHỈ ĐỌC + công cụ/lệnh được dùng + ghi "CẦN QUYỀN" khi bị chặn (lỗi dispatch:9: agy tự chạy lệnh shell bị chặn)
    prompt = build_agy_readonly_prompt(message, task_block, session_id)
    # agy -p không tương tác: tool cần quyền bị auto-denied. Cấp quy tắc chỉ đọc (read_file(worktree), command(ls|grep|git log|...))
    # trong settings của hồ sơ, KHÔNG bật skip-permissions cho mọi vai. Lối thoát cuối (opt-in GW_WARROOM_SKIP_PERMISSIONS=1):
    # thêm --dangerously-skip-permissions nhưng chỉ khi cwd đúng là worktree riêng của vai (vẫn --mode plan).
    in_worktree = is_role_worktree(session_id, cwd)

    # --output-format stream-json: tool step có lệnh shell (run_command.CommandLine) → biết đúng lệnh nào bị chặn quyền.
    # agy cũ không nhận cờ → chạy lại không cờ (stream_state["ok"] = False cho các lần sau). Tắt hẳn: GW_AGY_NO_STREAM=1.
    stream_state = {"ok": not _env_on("GW_AGY_NO_STREAM")}

    def _build_cmd(pdir):
        c = [_agy_bin(), f"--gemini_dir={pdir}"]
        if in_worktree and _env_on("GW_WARROOM_SKIP_PERMISSIONS"):
            c.append(AGY_SKIP_PERMISSIONS_FLAG)
        return c + ["--mode", "plan", "-p", prompt] + (AGY_STREAM_ARGS if stream_state["ok"] else [])

    cmd = _build_cmd(p_dir)
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_connection() as conn:
            conn.execute("UPDATE dispatch_log SET command = ?, profile_initial = ? WHERE id = ?", (" ".join(cmd), account_type, dispatch_id))
            conn.commit()
    except Exception:
        pass
    t0 = time.time()

    def _attempt(pid, pdir):
        """1 lần chạy agy với hồ sơ pid (#22): ghi allow-rule vào settings của hồ sơ đó trước, cùng cwd/worktree và cùng lệnh."""
        if in_worktree:
            ensure_agy_plan_permissions(pdir, cwd)
        c = _build_cmd(pdir)
        try:
            with get_connection() as conn:
                conn.execute("UPDATE dispatch_log SET command = ?, profile_used = ? WHERE id = ?", (" ".join(c), pid, dispatch_id))
                conn.commit()
        except Exception:
            pass
        try:
            res = subprocess.run(c, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=_agy_env(pdir))
            if stream_state["ok"] and agy_flag_unsupported(res):
                print(f"[dispatch] agy không nhận {' '.join(AGY_STREAM_ARGS)} → chạy lại không cờ")
                stream_state["ok"] = False
                c = _build_cmd(pdir)
                res = subprocess.run(c, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=_agy_env(pdir))
            res, sinfo = normalize_agy_stream_result(res)
            st, reset = record_quota_probe_from_result(pid, "default", res)
            return st, reset, {"cmd": c, "res": res, "stream": sinfo}
        except subprocess.TimeoutExpired:
            msg = f"Lỗi: agy không phản hồi sau {timeout // 60} phút, đã hủy."
            record_quota_probe(pid, "default", "timeout", "", msg)
            return "timeout", "", {"cmd": c, "error_output": msg}
        except Exception as e:
            return "error", "", {"cmd": c, "error_output": f"Lỗi khi chạy agy ({_agy_bin()}): {e}"}

    fb = run_agy_with_quota_fallback(account_type, p_dir, _attempt)
    payload = fb["payload"] or {}
    cmd = payload.get("cmd") or cmd
    exit_code = -1
    output = ""
    fail_reason = ""
    res = payload.get("res")
    sinfo = payload.get("stream") or {}
    blocked_line = ""
    if res is not None:
        exit_code = res.returncode
        output = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()
        denied = agy_output_denied(output) if exit_code == 0 else ""
        if exit_code == 0 and not denied and sinfo.get("is_stream") and not (res.stdout or "").strip():
            # stream-json xong mà không có câu trả lời (stderr dài lấn thông báo) → vẫn là không ra kết quả
            m = AGY_DENIED_RE.search(output)
            denied = m.group(0) if m else "no output produced"
        if denied or agy_blocked_commands(sinfo):
            # Ghi rõ lệnh bị chặn (trích từ stream-json / thông báo agy) để biết cần mở thêm allow-rule nào
            blocked_line = describe_agy_blocked(sinfo, output)
        if fb["status"] == "rate_limited":
            reset_raw = classify_agy_result(exit_code, output)[1]
            body = f"Lỗi 429 / hết quota khi gọi agy (hồi {reset_raw or 'chưa rõ'}):\n{output[-1500:]}"
        elif denied:
            fail_reason = f"agy bị từ chối quyền / không ra kết quả ({denied}). {blocked_line}"
            body = f"{fail_reason}:\n{output[-3000:]}"
        elif exit_code != 0:
            body = f"agy thoát lỗi:\n{output[-3000:] or '(không có output)'}"
        else:
            body = output[:4000] if output else "(agy không trả output)"
            if blocked_line:   # agy vẫn trả lời nhưng có lệnh bị chặn giữa chừng
                body = f"{body}\n⚠ {blocked_line}"
    elif payload.get("error_output"):
        output = body = payload["error_output"]
    else:
        # Mọi hồ sơ đều exhausted từ trước → không gọi agy lần nào
        output = body = f"Không gọi agy: {fb['note']}."
    if fb["all_exhausted"]:
        fail_reason = fail_reason or fb["note"]
        if res is not None:
            body = f"{body}\n⚠ {fb['note']}."
    elif fb["fallback"]:
        body = f"[Tài khoản] {fb['note']}.\n{body}"
    denied_fail = bool(fail_reason) and not fb["all_exhausted"]
    body = f"{body}\nexit={exit_code}" + (" (failed: agy bị từ chối quyền, không có kết quả)" if denied_fail else "")
    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

    report_path = ""
    try:
        report_dir = os.path.join(HOME_DIR, "gw-reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"warroom-{session_id}-{time.strftime('%Y%m%d-%H%M%S')}-{dispatch_id}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# {session_id} · {started_at} → {finished_at}\n\n")
            f.write(f"- Kênh: {channel_id}\n- cwd: {cwd}\n- Lệnh: {' '.join(cmd)}\n- exit: {exit_code}\n"
                    f"- Hồ sơ gán: {account_type} · hồ sơ chạy: {fb['profile_used'] or '(không)'}"
                    + (f"\n- Tài khoản: {fb['note']}" if fb["note"] else "") + (f"\n- {blocked_line}" if blocked_line else "")
                    + f"\n\n## Tin nhắn\n\n{message}\n\n")
            shell = agy_shell_commands(sinfo)
            if shell:
                f.write("## Lệnh shell agy đã gọi\n\n" + "\n".join(
                    f"- `{t['command'][:500]}`" + (" — BỊ CHẶN" if t.get("denied") else "")
                    + (f" — lỗi: {t['error'][:300]}" if t.get("error") else "") for t in shell) + "\n\n")
            f.write(f"## Output\n\n{output}\n")
    except Exception as e:
        print(f"[dispatch] Không ghi được báo cáo: {e}")
        report_path = ""

    if not task_id:
        task_id = _current_task_of(session_id)
    # exit=0 nhưng agy báo auto-denied / no output produced → vẫn là failed
    status = "done" if exit_code == 0 and not fail_reason else "failed"
    viec_ref = get_task_viec_ref(task_id, project_id)
    webhook_sent = send_event_webhook("dispatch_finished", project_id=project_id, viec_ref=viec_ref, task_id=task_id,
                                      session_id=session_id, exit_code=exit_code, report_path=report_path, status=status)
    summary = _shorten_output(f"[{fail_reason}]\n{output}" if fail_reason else (f"[{blocked_line}]\n{output}" if blocked_line else output))
    need = agy_need_permission_lines(output)
    if need and not fail_reason:
        summary = _shorten_output("[agy báo cần quyền] " + " | ".join(need) + "\n" + summary)
    if fb["fallback"] and not fb["all_exhausted"]:
        summary = f"[Tài khoản] {fb['note']}.\n{summary}"

    reply_msg_id = None
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json, reply_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (project_id, channel_id, session_id, time.strftime("%H:%M:%S"), "Report", body, json.dumps(["🤖 agy thật"], ensure_ascii=False),
                  reply_to, _now_iso()))
            reply_msg_id = cursor.lastrowid
            cursor.execute("""
            UPDATE dispatch_log SET command = ?, exit_code = ?, report_path = ?, finished_at = ?, task_id = ?, viec_ref = ?,
                   channel_id = ?, webhook_sent = ?, status = ?, summary = ?, reply_msg_id = ?,
                   profile_initial = ?, profile_used = ?, fallback_reason = ?, profiles_tried = ?
            WHERE id = ?
            """, (" ".join(cmd), exit_code, report_path, finished_at, task_id, viec_ref, channel_id, 1 if webhook_sent else 0,
                  status, summary, reply_msg_id, account_type, fb["profile_used"], fb["note"],
                  json.dumps(fb["attempts"], ensure_ascii=False), dispatch_id))
            conn.commit()
        # Kết quả tự ghi về phiên (conversation) của task TRƯỚC khi báo xong → ai chờ wait_worker_result thấy luôn tin kết quả
        report_dispatch_to_task(dispatch_id, project_id)
    finally:
        _notify_dispatch_change()
    return {"dispatch_id": dispatch_id, "session_id": session_id, "status": status, "exit_code": exit_code, "report_path": report_path, "cwd": cwd,
            "elapsed_sec": round(time.time() - t0, 1), "task_id": task_id, "viec_ref": viec_ref, "webhook_sent": webhook_sent,
            "reply_msg_id": reply_msg_id, "error": fail_reason, "profile_initial": account_type,
            "profile_used": fb["profile_used"], "fallback": fb["fallback"], "fallback_reason": fb["note"], "attempts": fb["attempts"]}

# ---------------------------------------------------------------------------
# Việc là trung tâm: task ↔ war-room ↔ worker (Issue #24)
# ---------------------------------------------------------------------------
TASK_PROMPT_CHECKLIST_MAX = 30
TASK_REPORT_SUMMARY_MAX = 3000
KANBAN_CHECK_RE = re.compile(r"\[KANBAN_UPDATE:\s*(TSK-\d+)\s*\|\s*CHECK:\s*([A-Za-z0-9_.:-]{1,64})\s*\]", re.IGNORECASE)


def _task_detail(task_id, project_id="PRJ-GEN-WORKPLACE"):
    """Thông tin 1 task (Kanban phiên gen_session_todos, hoặc roadmap todos): id, table, title, description, status, viec_ref,
    checklist, conversation_id, holder. Không có → None."""
    task_id = (task_id or "").strip()
    if not task_id:
        return None
    project_id = normalize_project_id(project_id)
    try:
        with get_connection() as conn:
            r = conn.execute("SELECT * FROM gen_session_todos WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
            if r:
                try:
                    chk = json.loads(r["checklist_json"] or "[]")
                except Exception:
                    chk = []
                return {"id": r["id"], "table": "gen_session_todos", "title": r["title"] or "", "description": r["description"] or "",
                        "status": r["status"] or "", "viec_ref": r["viec_ref"] or "", "checklist": chk if isinstance(chk, list) else [],
                        "conversation_id": r["conversation_id"] or "", "holder": r["claimed_by"] or ""}
            r = conn.execute("SELECT * FROM todos WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
            if r:
                return {"id": r["id"], "table": "todos", "title": r["title"] or "", "description": "", "status": r["status"] or "",
                        "viec_ref": r["viec_ref"] or "", "checklist": [], "conversation_id": "", "holder": r["assigned_session_id"] or ""}
    except Exception:
        pass
    return None


def find_task_ref_in_message(message, project_id="PRJ-GEN-WORKPLACE"):
    """Mã TSK-n đầu tiên trong nội dung tin mà có task thật trong DB; không có → ''."""
    for tid in dict.fromkeys(m.upper() for m in TASK_REF_RE.findall(message or "")):
        if _task_detail(tid, project_id):
            return tid
    return ""


def build_task_prompt_block(task_id, project_id="PRJ-GEN-WORKPLACE"):
    """Khối thông tin task nhồi vào prompt agy: tiêu đề, mã việc Kho, mô tả, checklist (kèm id mục) và cách báo tick checklist."""
    d = _task_detail(task_id, project_id)
    if not d:
        return ""
    lines = [f"[THÔNG TIN VIỆC {d['id']}]", f"Tiêu đề: {d['title']}"]
    if d["viec_ref"]:
        lines.append(f"Mã việc Kho Ryan (viec_ref): {d['viec_ref']}")
    if d["description"]:
        lines.append(f"Mô tả: {d['description'][:1500]}")
    chk = d["checklist"][:TASK_PROMPT_CHECKLIST_MAX]
    if chk:
        done = sum(1 for c in d["checklist"] if isinstance(c, dict) and c.get("done"))
        lines.append(f"Checklist ({done}/{len(d['checklist'])} xong):")
        for c in chk:
            if isinstance(c, dict):
                lines.append(f"- [{'x' if c.get('done') else ' '}] ({c.get('id', '')}) {c.get('text', '')}")
        lines.append(f"Xong mục nào thì ghi đúng một dòng [KANBAN_UPDATE: {d['id']} | CHECK: <id mục>] trong câu trả lời để hệ thống tick checklist.")
    return "\n".join(lines)


def _apply_dispatch_checklist_marks(task, text):
    """Tick các mục checklist mà worker báo bằng [KANBAN_UPDATE: TSK-n | CHECK: <id>] cho ĐÚNG task của lần giao việc. Trả id đã tick."""
    if not task or task.get("table") != "gen_session_todos" or not text:
        return []
    ids = {str(c.get("id")) for c in task["checklist"] if isinstance(c, dict) and c.get("id")}
    ticked = []
    for tid, item_id in KANBAN_CHECK_RE.findall(text):
        if tid.upper() != task["id"].upper() or item_id not in ids or item_id in ticked:
            continue
        res = toggle_gen_session_todo_checklist_item(task["conversation_id"], task["id"], item_id, True)
        if "error" not in res:
            ticked.append(item_id)
    return ticked


def report_dispatch_to_task(dispatch_id, project_id="PRJ-GEN-WORKPLACE"):
    """
    Lần giao việc gắn task đã kết thúc → ghi 1 tin kết quả vào phiên (conversation) của task qua log_gen_message:
    trạng thái, tóm tắt, report_path, link dispatch:<id> (bằng chứng nghiệm thu khi done), tin war-room trả lời.
    Worker báo [KANBAN_UPDATE: TSK-n | CHECK: id] → tick checklist. Chỉ ghi 1 lần (dispatch_log.task_msg_id). Lỗi chỉ log, không ném.
    Trả message_id đã ghi hoặc None.
    """
    try:
        row = _get_dispatch_row(dispatch_id)
        if not row or not (row.get("task_id") or "").strip() or row.get("task_msg_id") is not None:
            return None
        status = _row_status(row)
        if status == "running":
            return None
        task = _task_detail(row["task_id"], project_id)
        if not task or not task.get("conversation_id"):
            return None
        with get_connection() as conn:   # giữ chỗ trước (-1) để 2 luồng không cùng ghi
            cur = conn.execute("UPDATE dispatch_log SET task_msg_id = -1 WHERE id = ? AND task_msg_id IS NULL", (row["id"],))
            conn.commit()
            if cur.rowcount != 1:
                return None
        full_output = _read_report_tail(row.get("report_path")) or row.get("summary") or ""
        if "\n## Output\n" in full_output:   # báo cáo war-room: chỉ xét phần output của agy, không xét tin người giao
            full_output = full_output.split("\n## Output\n", 1)[1]
        ticked = _apply_dispatch_checklist_marks(task, full_output) if status in DISPATCH_OK_STATUSES else []
        fallback = bool(row.get("profile_used")) and (row.get("profile_used") or "") != (row.get("profile_initial") or "")
        label = {"done": "XONG", "ok": "XONG", "failed": "LỖI"}.get(status, status.upper())
        summary = (row.get("summary") or "").strip()
        if len(summary) > TASK_REPORT_SUMMARY_MAX:
            summary = summary[:TASK_REPORT_SUMMARY_MAX] + "\n…(đã cắt, xem báo cáo)"
        lines = [f"Kết quả giao việc dispatch:{row['id']} · {task['id']} · {row['session_id']} · {label}"
                 + (f" (exit={row.get('exit_code')})" if row.get("exit_code") is not None else "")]
        if fallback:
            lines.append(f"Tài khoản: đã chuyển hồ sơ {row.get('profile_initial') or '?'} → {row.get('profile_used')}"
                         + (f" ({row.get('fallback_reason')})" if row.get("fallback_reason") else ""))
        if ticked:
            lines.append(f"Checklist đã tick theo báo cáo worker: {', '.join(ticked)}")
        bf = build_dispatch_fields(row) if row.get("kind") == "build" else None
        if bf:
            lines.extend(format_build_lines(bf))
        lines.append("Tóm tắt:\n" + (summary or "(không có output)"))
        if row.get("report_path"):
            lines.append(f"Báo cáo: {row['report_path']}")
        if row.get("reply_msg_id"):
            lines.append(f"Tin war-room: #{row['reply_msg_id']}")
        lines.append(f"Link: dispatch:{row['id']}")
        if status in DISPATCH_OK_STATUSES:
            if bf and bf["build_commit"]:
                lines.append(f"Bằng chứng nghiệm thu gợi ý: {bf['build_commit']} (commit trên {bf['build_branch']}; "
                             f"sau khi merge PR thì dùng SHA merge) hoặc dispatch:{row['id']}")
            else:
                lines.append(f"Bằng chứng nghiệm thu gợi ý: dispatch:{row['id']}")
        res = log_gen_message(task["conversation_id"], "\n".join(lines), "assistant", f"{row['session_id']} (agy)")
        msg_id = res.get("message_id")
        with get_connection() as conn:
            conn.execute("UPDATE dispatch_log SET task_msg_id = ? WHERE id = ?", (msg_id if msg_id else None, row["id"]))
            conn.commit()
        if not msg_id:
            print(f"[dispatch] Không ghi được kết quả dispatch:{row['id']} về phiên {task['conversation_id']}: {res.get('error')}")
        return msg_id
    except Exception as e:
        print(f"[dispatch] Lỗi ghi kết quả dispatch:{dispatch_id} về task: {e}")
        return None


def resolve_role_session(role_or_sid):
    """'qa' / '@qa' / 'QA' / 'gw-qa-agy' → 'gw-qa-agy'; worker lạ → ''."""
    v = (role_or_sid or "").strip().lstrip("@").strip().lower() if isinstance(role_or_sid, str) else ""
    if v in WARROOM_ROLE_SESSIONS:
        return WARROOM_ROLE_SESSIONS[v]
    if v in WARROOM_ROLE_SESSIONS.values():
        return v
    return ""


ASSIGN_MODES = ("build", "review")
ASSIGN_MODE_LABELS = {"build": "Làm", "review": "Rà soát"}


def normalize_assign_mode(mode, default="build"):
    """'build' | 'review' (nhận cả 'lam'/'làm', 'plan'/'ra-soat'/'rà soát'); rỗng → default; giá trị lạ → ''."""
    v = str(mode or "").strip().lower()
    if not v:
        return default
    aliases = {"build": "build", "lam": "build", "làm": "build", "code": "build",
               "review": "review", "plan": "review", "ra-soat": "review", "ra soat": "review", "rà soát": "review", "rasoat": "review"}
    return aliases.get(v, "")


def save_warroom_record(project_id, channel_id, author, body, tag="Assignment"):
    """Lưu 1 tin vào kênh war-room mà KHÔNG giao việc (không quét @vai). Trả id tin."""
    with get_connection() as conn:
        cur = conn.execute("""
        INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (normalize_project_id(project_id), channel_id, author, time.strftime("%H:%M:%S"), tag, body,
              json.dumps(["✅ đã ghi nhận"], ensure_ascii=False), _now_iso()))
        conn.commit()
        return cur.lastrowid


def assign_task_to_role(todo_id, session_id, project_id="PRJ-GEN-WORKPLACE", author="Ryan (Owner)", channel_id="war_room", wait=False,
                        mode="build"):
    """
    Giao 1 task cho 1 vai (POST /api/task/assign): claim task cho worker rồi chạy agy, trả dispatch_id để theo dõi / chờ
    (wait_worker_result). mode (#45):
    - "build" (mặc định, "Làm"): agy -p chế độ mặc định sửa code trong worktree riêng của task (../gw-worktrees/TSK-n,
      nhánh wt/TSK-n từ origin/main); app kiểm commit, chạy py_compile + test, push nhánh, ghi link compare (agy_build.py).
    - "review" ("Rà soát"): như war-room — tin "@vai Thực hiện TSK-n …", agy --mode plan chỉ đọc trong worktree của vai.
    Lỗi: {"error", "code"}: bad_request (thiếu / sai vai / sai mode), not_found, already_done, locked (người khác đang giữ).
    """
    project_id = normalize_project_id(project_id)
    todo_id = (todo_id or "").strip() if isinstance(todo_id, str) else ""
    sid = resolve_role_session(session_id)
    if not todo_id:
        return {"error": "Thiếu todo_id (vd TSK-12)", "code": "bad_request"}
    mode_norm = normalize_assign_mode(mode)
    if not mode_norm:
        return {"error": f"mode không hợp lệ '{mode}' (build = Làm | review = Rà soát)", "code": "bad_request"}
    mode = mode_norm
    retired = next((k for k, v in RETIRED_ROLES.items()
                    if str(session_id or "").strip().lstrip("@").lower() in (k, v["session_id"])), "")
    if retired:
        return {"error": retired_role_error(retired), "code": "retired_role"}
    if not sid:
        return {"error": f"Vai không hợp lệ '{session_id}' ({', '.join(WARROOM_ROLE_SESSIONS)} hoặc gw-<vai>-agy)",
                "code": "bad_request"}
    task = _task_detail(todo_id, project_id)
    if not task:
        return {"error": "Task not found", "code": "not_found", "task_id": todo_id}
    if task["status"] == "done":
        return {"error": f"Task {todo_id} đã done — không giao lại", "code": "already_done", "task_id": todo_id}
    claim = claim_task(sid, todo_id, project_id)
    if "error" in claim:
        claim.setdefault("code", "locked")
        return claim
    role = next(k for k, v in WARROOM_ROLE_SESSIONS.items() if v == sid)
    head = f"@{role} Thực hiện {todo_id}" + (f" ({task['viec_ref']})" if task["viec_ref"] else "") + f": {task['title']}"
    chk = [c for c in task["checklist"] if isinstance(c, dict)]
    body = head + ("\nChecklist:\n" + "\n".join(f"- [{'x' if c.get('done') else ' '}] {c.get('text', '')}" for c in chk[:TASK_PROMPT_CHECKLIST_MAX])
                   if chk else "")
    if mode == "build":
        try:
            from backend import agy_build as _build
        except ImportError:
            import agy_build as _build
        return _build.assign_build(todo_id, sid, role, task, body, claim, project_id=project_id, author=author,
                                   channel_id=channel_id, wait=wait)
    res = post_warroom_message(project_id, channel_id, author, body, "Assignment", wait=wait, task_id=todo_id)
    if "error" in res:
        return res
    d = (res.get("dispatches") or [{}])[0]
    return {"status": "assigned", "mode": "review", "task_id": todo_id, "session_id": sid, "role": role, "viec_ref": task["viec_ref"],
            "dispatch_id": d.get("dispatch_id"), "request_msg_id": (res.get("user_message") or {}).get("id"),
            "channel_id": channel_id, "claim": claim, "message": body}


def _dispatch_brief(r):
    d = dict(r)
    return {"id": d["id"], "session_id": d.get("session_id") or "", "status": _row_status(d), "kind": d.get("kind") or "",
            "exit_code": d.get("exit_code"), "started_at": d.get("started_at") or "", "finished_at": d.get("finished_at") or "",
            "request_msg_id": d.get("request_msg_id"), "reply_msg_id": d.get("reply_msg_id"), "report_path": d.get("report_path") or "",
            "profile_initial": d.get("profile_initial") or "", "profile_used": d.get("profile_used") or "",
            "fallback": bool(d.get("profile_used")) and (d.get("profile_used") or "") != (d.get("profile_initial") or ""),
            "fallback_reason": d.get("fallback_reason") or "", "channel_id": d.get("channel_id") or "",
            "task_msg_id": d.get("task_msg_id"),
            "engine": d.get("engine") or "", "ext_session_id": d.get("ext_session_id") or "", "ext_state": d.get("ext_state") or "",
            "ext_url": d.get("ext_url") or "", "pr_url": d.get("pr_url") or "", **build_dispatch_fields(d)}


def build_dispatch_fields(d):
    """Trường chế độ Làm (#45) của 1 dòng dispatch_log: nhánh, commit, test, push, compare (dòng không phải build → rỗng)."""
    def _j(v):
        try:
            x = json.loads(v or "null")
            return x if isinstance(x, dict) else None
        except Exception:
            return None
    return {"mode": "build" if d.get("kind") == "build" else ("review" if d.get("kind") in ("warroom", "tmux") else ""),
            "worktree_dir": d.get("worktree_dir") or "", "build_branch": d.get("build_branch") or "",
            "build_commit": d.get("build_commit") or "", "build_tests": _j(d.get("build_tests")),
            "build_push": _j(d.get("build_push")), "compare_url": d.get("compare_url") or ""}


def format_build_lines(bf):
    """Các dòng mô tả kết quả chế độ Làm cho tin trong phiên / war-room: nhánh, commit, test, push, compare, PR nháp."""
    out = []
    if bf.get("build_branch"):
        out.append(f"Nhánh: {bf['build_branch']}" + (f" (worktree {bf['worktree_dir']})" if bf.get("worktree_dir") else ""))
    out.append(f"Commit: {bf.get('build_commit') or '(không có commit mới)'}")
    t = bf.get("build_tests") or {}
    if t:
        failed = [x.get("name") for x in t.get("tests") or [] if not x.get("ok")]
        pc = t.get("py_compile") or {}
        out.append(f"Test: {'PASS' if t.get('ok') else 'FAIL'} — py_compile {'ok' if pc.get('ok') else 'LỖI'}, "
                   f"{t.get('passed', 0)}/{t.get('total', 0)} file test pass"
                   + (f"; lỗi: {', '.join(failed[:10])}" if failed else "") + (f"; {t['note']}" if t.get("note") else ""))
    p = bf.get("build_push") or {}
    if p:
        out.append("Push: " + (f"đã đẩy {p.get('ref') or bf.get('build_branch')} lên origin" if p.get("ok")
                               else f"LỖI — {p.get('error') or 'không rõ'}"))
    if bf.get("compare_url"):
        out.append(f"So sánh: {bf['compare_url']}")
    if p.get("pr_url"):
        out.append(f"PR nháp: {p['pr_url']}")
    elif p.get("pr_error"):
        out.append(f"PR nháp: không tạo được ({p['pr_error']})")
    return out


def attach_task_links(todos):
    """Gắn vào mỗi task: last_dispatch (lần giao gần nhất), dispatch_count, holder (claimed_by), conversation_title."""
    if not todos:
        return todos
    ids = [t["id"] for t in todos]
    marks = ",".join("?" for _ in ids)
    with get_connection() as conn:
        rows = conn.execute(f"SELECT * FROM dispatch_log WHERE task_id IN ({marks}) ORDER BY id DESC", ids).fetchall()
        convs = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM gen_conversations").fetchall()}
    last, counts = {}, {}
    for r in rows:
        counts[r["task_id"]] = counts.get(r["task_id"], 0) + 1
        if r["task_id"] not in last:
            last[r["task_id"]] = _dispatch_brief(r)
    for t in todos:
        t["last_dispatch"] = last.get(t["id"])
        t["dispatch_count"] = counts.get(t["id"], 0)
        t["holder"] = t.get("claimed_by") or ""
        t["conversation_title"] = convs.get(t.get("conversation_id"), "")
    return todos


def get_all_session_todos(project_id="PRJ-GEN-WORKPLACE"):
    """Mọi task Kanban phiên (bảng gen_session_todos — nơi chứa task thật của MCP create_kanban_task / claim) của dự án, kèm
    checklist đã parse, tiến độ x/y và thông tin giao việc (attach_task_links). Dùng cho /api/state → màn Việc & tiến độ."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM gen_session_todos WHERE project_id = ? ORDER BY updated_at DESC, id ASC", (project_id,)).fetchall()
    todos = []
    for r in rows:
        d = dict(r)
        try:
            d["checklist"] = json.loads(d.get("checklist_json") or "[]")
        except Exception:
            d["checklist"] = []
        d.pop("checklist_json", None)
        d["total_items"] = len(d["checklist"])
        d["done_items"] = sum(1 for it in d["checklist"] if isinstance(it, dict) and it.get("done"))
        todos.append(d)
    return attach_task_links(todos)


def post_warroom_message(project_id="PRJ-GEN-WORKPLACE", channel_id="war_room", author="Ryan (Owner)", message="", tag="Directive", wait=False, task_id=""):
    """
    Lưu tin nhắn; tin có @backend|@devops|@qa|@lead → chạy agy thật của vai đó ở thread nền (wait=True chạy
    đồng bộ, dùng cho test). Không có @vai → chỉ lưu (#3). @Gen / @Toàn Đội / @all KHÔNG giao việc (chỉ lưu, có ghi chú).
    Task của lần giao việc: task_id truyền vào > mã TSK-n đầu tiên có thật trong nội dung tin > current_task_id của worker.
    Prompt gửi agy kèm tiêu đề, mô tả, viec_ref và checklist của task đó.
    """
    project_id = normalize_project_id(project_id)
    if not message or not message.strip():
        return {"error": "Message is empty"}
    # Gọi vai đã bỏ (vd @security) → lỗi rõ ràng, KHÔNG lưu tin, không giao việc cho ai
    retired = [m.lower() for m in RETIRED_MENTION_RE.findall(message)]
    if retired:
        return {"error": retired_role_error(retired[0]), "code": "retired_role", "retired_roles": sorted(set(retired))}

    now_time = time.strftime("%H:%M:%S")
    now_iso = _now_iso()
    clean_msg = message.strip()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (project_id, channel_id, author, now_time, tag, clean_msg, json.dumps(["✅ đã ghi nhận"], ensure_ascii=False), now_iso))
        user_msg_id = cursor.lastrowid
        conn.commit()

    dispatched = []
    dispatches = []
    ref_task = (task_id or "").strip() or find_task_ref_in_message(clean_msg, project_id)
    for role in dict.fromkeys(m.lower() for m in WARROOM_MENTION_RE.findall(clean_msg)):
        sid = WARROOM_ROLE_SESSIONS[role]
        d_task = ref_task or _current_task_of(sid)
        # Tạo dòng dispatch_log (running) TRƯỚC khi chạy để bên gọi có dispatch_id truyền cho wait_worker_result (#9)
        did = start_dispatch_log(sid, kind="warroom", channel_id=channel_id, task_id=d_task,
                                 viec_ref=get_task_viec_ref(d_task, project_id), request_msg_id=user_msg_id)
        if wait:
            dispatch_warroom_to_agent(project_id, channel_id, sid, clean_msg, dispatch_id=did, reply_to=user_msg_id)
        else:
            threading.Thread(target=dispatch_warroom_to_agent, args=(project_id, channel_id, sid, clean_msg),
                             kwargs={"dispatch_id": did, "reply_to": user_msg_id}, daemon=True, name=f"warroom-dispatch-{sid}").start()
        dispatched.append(sid)
        dispatches.append({"session_id": sid, "dispatch_id": did, "task_id": d_task})

    broadcast = bool(WARROOM_BROADCAST_RE.search(clean_msg))
    if dispatched:
        note = (f"Đã chuyển tới {', '.join(dispatched)}; trả lời thật của agy sẽ xuất hiện trong kênh khi chạy xong (tối đa 15 phút). "
                f"Chờ kết quả: wait_worker_result(dispatch_id=...) với dispatch_id trong 'dispatches'.")
    elif broadcast:
        note = "@Gen / @Toàn Đội không giao việc cho worker nào; chỉ lưu tin. Gọi đúng vai: @backend, @devops, @qa, @lead."
    else:
        note = "Không có @vai nên chỉ lưu tin, không trả lời."
    return {
        "status": "sent",
        "channel_id": channel_id,
        "user_message": {"id": user_msg_id, "author": author, "body": clean_msg, "created_time": now_time, "created_at": now_iso, "tag": tag},
        "agent_reply": None,
        "dispatched": dispatched,
        "dispatches": dispatches,
        "task_id": ref_task,
        "note": note,
    }

# =========================================================================
# REAL DATA ACCESSORS & SYSTEM INTEGRATIONS (100% REAL REPO & SQLITE DATA)
# =========================================================================

def find_repo_path():
    for p in ["/app/repo", "/workspace", str(BASE_DIR)]:
        if os.path.exists(os.path.join(p, ".git")):
            return p
    return str(BASE_DIR)

def get_git_log(limit=15):
    repo = find_repo_path()
    try:
        cmd = ["git", "-C", repo, "log", f"-n{limit}", "--pretty=format:%h|%an|%ar|%s"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            commits = []
            for line in res.stdout.strip().splitlines():
                parts = line.split("|", 3)
                if len(parts) == 4:
                    commits.append({
                        "hash": parts[0],
                        "author": parts[1],
                        "time": parts[2],
                        "message": parts[3]
                    })
            return commits
    except Exception as e:
        print("[Git Log] Error:", e)
    return []

def get_git_status():
    repo = find_repo_path()
    try:
        b_res = subprocess.run(["git", "-C", repo, "branch", "--show-current"], capture_output=True, text=True, timeout=2)
        branch = b_res.stdout.strip() or "main"

        h_res = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2)
        head = h_res.stdout.strip() or "HEAD"

        c_res = subprocess.run(["git", "-C", repo, "rev-list", "--count", "HEAD"], capture_output=True, text=True, timeout=2)
        total_commits = int(c_res.stdout.strip() or "0")

        s_res = subprocess.run(["git", "-C", repo, "status", "-s"], capture_output=True, text=True, timeout=2)
        status_lines = [s for s in s_res.stdout.strip().splitlines() if s.strip()]
        is_clean = len(status_lines) == 0

        return {
            "branch": branch,
            "head": head,
            "total_commits": total_commits,
            "clean": is_clean,
            "changed_files": status_lines,
            "repo_path": repo
        }
    except Exception as e:
        return {
            "branch": "main",
            "head": "HEAD",
            "total_commits": 0,
            "clean": True,
            "changed_files": [],
            "repo_path": repo,
            "error": str(e)
        }

def get_db_tables(project_id="PRJ-GEN-WORKPLACE"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name ASC")
        rows = cursor.fetchall()
        tables = []
        for r in rows:
            tname = r["name"]
            ttype = r["type"]
            count = 0
            if ttype == "table" and not tname.endswith("_docsize") and not tname.endswith("_config"):
                try:
                    cursor.execute(f'SELECT count(*) FROM "{tname}"')
                    count = cursor.fetchone()[0]
                except Exception:
                    count = 0
            tables.append({
                "name": tname,
                "type": ttype,
                "count": count,
                "sql": r["sql"] or ""
            })
        return tables

def get_workspace_files():
    repo = find_repo_path()
    file_list = []
    idx = 1
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in [".git", "__pycache__", "node_modules", ".local", ".system_generated", "storage", ".gemini"]]
        rel_root = os.path.relpath(root, repo)
        for f in files:
            if f.endswith((".pyc", ".log", ".tmp", ".db-wal", ".db-shm")):
                continue
            rel_path = f if rel_root == "." else os.path.join(rel_root, f)
            full_path = os.path.join(root, f)
            try:
                st = os.stat(full_path)
                size_bytes = st.st_size
                if size_bytes < 1024:
                    size_str = f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    size_str = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                
                ext = os.path.splitext(f)[1].lower()
                if "spec" in f.lower() or ext == ".md":
                    cat = "spec / doc"
                    src = "SSOT / Owner"
                elif ext in [".py", ".sh"]:
                    cat = "script / backend"
                    src = "Backend / DevOps"
                elif ext in [".html", ".css", ".js", ".svg"]:
                    cat = "frontend / asset"
                    src = "Frontend"
                elif ext in [".yml", ".yaml", "dockerfile"]:
                    cat = "docker / config"
                    src = "DevOps"
                elif ext in [".db", ".sqlite"]:
                    cat = "database"
                    src = "Backend"
                else:
                    cat = "file"
                    src = "System"

                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
                file_list.append({
                    "id": f"FILE-{idx:02d}",
                    "path": rel_path,
                    "size": size_str,
                    "type": cat,
                    "source": src,
                    "modified": mtime
                })
                idx += 1
            except Exception:
                pass
    return sorted(file_list, key=lambda x: x["path"])

def _sop_line_field(content, labels):
    """Lấy giá trị dòng '- **<nhãn>**: ...' đầu tiên khớp 1 trong labels trong ROLE.md; không có → '' (không bịa)."""
    import re
    for label in labels:
        m = re.search(r"-\s*\*\*" + label + r"\*\*\s*:\s*([^\n]+)", content, re.I)
        if m:
            return m.group(1).strip()
    return ""

def get_roles_sop():
    import re
    roles_dir = None
    for p in [os.environ.get("GW_ROLES_DIR", ""), "/workspace/roles", "/app/repo/workspace/roles", "/app/repo/roles", "workspace/roles", "roles"]:
        if p and os.path.isdir(p):
            roles_dir = p
            break
    
    sop_data = {}
    if not roles_dir:
        return sop_data

    for fname in sorted(os.listdir(roles_dir)):
        if fname.endswith("_ROLE.md"):
            sid = fname.replace("_ROLE.md", "")
            if sid in RETIRED_SESSION_IDS:   # vai đã bỏ (#34)
                continue
            fpath = os.path.join(roles_dir, fname)
            try:
                content = Path(fpath).read_text(encoding="utf-8")
                
                title_match = re.search(r"#\s*Genesis\s*Swarm\s*Role\s*Specification:\s*([^\n]+)", content, re.I)
                title = title_match.group(1).strip() if title_match else sid
                
                mission_match = re.search(r"-\s*\*\*Active\s*Mission\*\*:\s*([^\n]+)", content, re.I)
                mission = mission_match.group(1).strip() if mission_match else "Chấp hành đặc tả SSOT"

                scope_match = re.search(r"-\s*\*\*Assigned\s*Scope\*\*:\s*`?([^`\n]+)`?", content, re.I)
                scope = scope_match.group(1).strip() if scope_match else ""

                # Luồng bàn giao: chỉ lấy từ ROLE.md ("Nhận từ"/"Input from", "Bàn giao cho"/"Output to"), không có → ""
                input_from = _sop_line_field(content, [r"Nhận\s*từ", r"Input\s*from"])
                output_to = _sop_line_field(content, [r"Bàn\s*giao\s*cho", r"Output\s*to"])

                allowed = []
                m_allow = re.search(r"\*\*Được\s*phép\s*chỉnh\s*sửa[^\n]*\*\*:\s*\n(.*?)(?=\n- \*\*|\n###|\Z)", content, re.I | re.DOTALL)
                if m_allow:
                    for line in m_allow.group(1).splitlines():
                        c_line = line.strip().lstrip("- `*").rstrip("`*").replace("`", "").strip()
                        if c_line and not c_line.startswith("**"):
                            allowed.append(c_line)

                blocked = []
                m_block = re.search(r"\*\*CẤM\s*TUYỆT\s*ĐỐI[^\n]*\*\*:\s*\n(.*?)(?=\n- \*\*|\n###|\Z)", content, re.I | re.DOTALL)
                if m_block:
                    for line in m_block.group(1).splitlines():
                        c_line = line.strip().lstrip("- `*").rstrip("`*").replace("`", "").strip()
                        if c_line and not c_line.startswith("**") and "không có hạn chế" not in c_line.lower() and "none" not in c_line.lower():
                            blocked.append(c_line)

                checklist = [
                    {"text": f"Khởi tạo và duy trì ranh giới ({', '.join(allowed[:2]) if allowed else 'Toàn quyền'})", "done": True},
                    {"text": "Đồng bộ đặc tả SSOT (docs/SSOT_ORIGINAL_SPEC.md)", "done": True},
                    {"text": "Bàn giao kết quả kèm Git commit hash hoặc test log", "done": False}
                ]

                sop_data[sid] = {
                    "id": sid,
                    "title": title,
                    "mission": mission,
                    "scope": scope,
                    "inputFrom": input_from,
                    "outputTo": output_to,
                    "allowed": allowed if allowed else ["* (Toàn quyền)"],
                    "blocked": blocked if blocked else ["None"],
                    "checklist": checklist
                }
            except Exception as e:
                print(f"[Roles SOP] Error reading {fpath}:", e)
    return sop_data

def get_vault_list():
    """Cấu hình/bí mật THẬT đang có hiệu lực: biến môi trường và trạng thái đăng nhập từng OAuth profile (không lộ giá trị)."""
    items = [
        {"id": "ENV-PORT", "name": "PORT", "owner": "Môi trường", "scope": f"Cổng web = {os.environ.get('PORT', 8888)}", "status": "đang dùng"},
        {"id": "ENV-DATA_DIR", "name": "DATA_DIR", "owner": "Môi trường", "scope": f"Thư mục DB = {DATA_DIR}", "status": "đang dùng"},
        {"id": "ENV-GW_AGY_BIN", "name": "GW_AGY_BIN", "owner": "Môi trường", "scope": f"Lệnh agy = {_agy_bin()}", "status": "đã đặt" if os.environ.get("GW_AGY_BIN") else "mặc định (agy trong PATH)"},
        {"id": "ENV-GW_EVENT_WEBHOOK_URL", "name": "GW_EVENT_WEBHOOK_URL", "owner": "Môi trường", "scope": "Webhook sự kiện task_completed / dispatch_finished", "status": "bật" if os.environ.get("GW_EVENT_WEBHOOK_URL") else "tắt"},
        {"id": "ENV-GW_AGY_WRITE_ROLES", "name": "GW_AGY_WRITE_ROLES", "owner": "Môi trường", "scope": "Vai được bật --dangerously-skip-permissions cho agy-run (chỉ trong worktree riêng)", "status": ", ".join(sorted(agy_write_roles())) or "không vai nào"},
        {"id": "ENV-GW_WARROOM_SKIP_PERMISSIONS", "name": "GW_WARROOM_SKIP_PERMISSIONS", "owner": "Môi trường", "scope": "Lối thoát cuối: agy war-room (--mode plan) bỏ hỏi quyền, chỉ trong worktree của vai", "status": "bật" if _env_on("GW_WARROOM_SKIP_PERMISSIONS") else "tắt"},
        {"id": "ENV-GW_GEN_CHAT_SKIP_PERMISSIONS", "name": "GW_GEN_CHAT_SKIP_PERMISSIONS", "owner": "Môi trường", "scope": "Lối thoát cuối: chat Gen / Orchestrator (agy --print) bỏ hỏi quyền; mặc định chỉ đọc theo permissions.allow", "status": "bật" if _env_on("GW_GEN_CHAT_SKIP_PERMISSIONS") else "tắt"},
        {"id": "ENV-GW_AUTO_UPDATE", "name": "GW_AUTO_UPDATE", "owner": "Môi trường", "scope": f"Tự cập nhật từ origin/{os.environ.get('GW_AUTO_UPDATE_BRANCH') or 'main'} mỗi {os.environ.get('GW_AUTO_UPDATE_SEC') or 120}s", "status": "tắt" if os.environ.get("GW_AUTO_UPDATE", "1").strip().lower() in ("0", "false", "no", "off") else "bật"},
        {"id": "ENV-GOOGLE_OAUTH", "name": "GOOGLE_OAUTH_CLIENT_ID/SECRET", "owner": "Môi trường (.env)", "scope": "Đổi code OAuth lấy token Google", "status": "đã cấu hình" if (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET) else "chưa cấu hình"},
    ]
    try:
        for prof in get_oauth_profiles():
            email = prof.get("email") or ""
            items.append({
                "id": f"OAUTH-{prof.get('id')}",
                "name": prof.get("name") or prof.get("id"),
                "owner": "OAuth profile",
                "scope": prof.get("path") or "",
                "status": (f"đã đăng nhập ({email})" if email else "đã đăng nhập") if prof.get("is_auth") else "chưa đăng nhập"
            })
    except Exception as e:
        items.append({"id": "OAUTH-ERR", "name": "OAuth profiles", "owner": "OAuth profile", "scope": str(e), "status": "lỗi đọc"})
    return items

def get_ssot_events(project_id="PRJ-GEN-WORKPLACE"):
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ssot_events WHERE project_id = ? ORDER BY id ASC", (project_id,))
        rows = cursor.fetchall()
        events = []
        for r in rows:
            events.append({
                "id": r["id"],
                "runtime": r["runtime_id"] or (r["role_name"] + " / " + (r["runtime_id"] or "")),
                "role": r["role_name"],
                "request": r["request"],
                "evidence": r["evidence"],
                "status": r["status"],
                "time": r["verified_time"]
            })
        return events

def verify_ssot_event(event_id, new_status="ssot", project_id="PRJ-GEN-WORKPLACE"):
    project_id = normalize_project_id(project_id)
    now_str = time.strftime("%H:%M")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE ssot_events SET status = ?, verified_time = ? WHERE id = ? AND project_id = ?",
                       (new_status, now_str, event_id, project_id))
        conn.commit()
        return True

def _refresh_roadmap_status(todo_id, project_id):
    """Tính lại trạng thái roadmap chứa task sau khi task đổi trạng thái."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT roadmap_id FROM todos WHERE id = ? AND project_id = ?", (todo_id, project_id))
        r_row = cursor.fetchone()
        if r_row:
            rm_id = r_row["roadmap_id"]
            cursor.execute("SELECT count(*) as total, sum(case when status='done' then 1 else 0 end) as dones FROM todos WHERE roadmap_id = ?", (rm_id,))
            stat = cursor.fetchone()
            total = stat["total"] or 0
            dones = stat["dones"] or 0
            rm_status = "done" if total > 0 and dones == total else ("live" if dones > 0 else "queued")
            cursor.execute("UPDATE roadmaps SET status = ?, todos_count = ? WHERE id = ?", (rm_status, total, rm_id))
        conn.commit()

def update_todo_status(todo_id, new_status, project_id="PRJ-GEN-WORKPLACE", session_id="", evidence_ref="", force=False, reason=""):
    """Đổi trạng thái task roadmap (bảng todos). Sang 'done' → qua complete_task (set_task_status). Trả dict như set_task_status."""
    project_id = normalize_project_id(project_id)
    res = set_task_status(todo_id, new_status, project_id, session_id=session_id, evidence_ref=evidence_ref, force=force, reason=reason)
    if res.get("status") == "completed":
        _refresh_roadmap_status(todo_id, project_id)
    return res

def create_new_project(name, repo_path, plan_text=""):
    name = (name or "").strip()
    if not name:
        return {"error": "Missing name"}
    pid = "PRJ-" + name.upper().replace(" ", "-").replace("_", "-")
    repo = repo_path or str(BASE_DIR.parent / name)
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR REPLACE INTO projects (id, name, repo_path, branch, plan_file, source_text, meta)
        VALUES (?, ?, ?, 'main', 'docs/SSOT_ORIGINAL_SPEC.md', ?, ?)
        """, (pid, name, repo, plan_text, "1 role · Newly created"))
        conn.commit()
    
    return {"status": "created", "id": pid, "name": name, "repo": repo}

def get_role_thinking_trace(session_id, project_id="PRJ-GEN-WORKPLACE"):
    buffer_len = 0
    t0 = time.time()
    try:
        res = subprocess.run(["tmux", "capture-pane", "-t", session_id, "-p", "-S", "-100"],
                             capture_output=True, text=True, timeout=1.5)
        if res.returncode == 0:
            buffer_len = len(res.stdout)
    except Exception:
        pass
    
    est_tokens = buffer_len // 4  # ước lượng ~4 ký tự/token từ buffer tmux thật; 0 khi không có phiên
    latency_ms = int((time.time() - t0) * 1000)

    return {
        "session_id": session_id,
        "latency_ms": f"{latency_ms}ms",
        "tokens": f"{est_tokens:,}",
        "buffer_chars": buffer_len,
        "note": "latency = thời gian tmux capture-pane thật; tokens = ước lượng buffer/4"
    }

def get_system_skills():
    skills_paths = [
        os.path.join(HOME_DIR, ".agents", "skills"),
        os.path.join(HOME_DIR, ".gemini", "config", "skills"),
        os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "builtin", "skills")
    ]
    skills = []
    seen = set()
    for sp in skills_paths:
        if os.path.isdir(sp):
            for item in sorted(os.listdir(sp)):
                if item in seen:
                    continue
                skill_md = os.path.join(sp, item, "SKILL.md")
                if os.path.exists(skill_md):
                    seen.add(item)
                    desc = ""
                    try:
                        content = Path(skill_md).read_text(encoding="utf-8")
                        for l in content.splitlines():
                            if l.startswith("description:"):
                                desc = l.replace("description:", "").strip()
                                break
                    except Exception:
                        pass
                    skills.append({
                        "name": item,
                        "tag": "Installed",
                        "desc": desc or f"Kỹ năng {item} tích hợp hệ thống"
                    })
    return skills  # không có skill cài trên máy → danh sách rỗng, không bịa

def get_system_mcps():
    mcp_path = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "mcp")
    mcps = []
    if os.path.isdir(mcp_path):
        for item in sorted(os.listdir(mcp_path)):
            dir_path = os.path.join(mcp_path, item)
            if os.path.isdir(dir_path):
                tools_count = len([f for f in os.listdir(dir_path) if f.endswith(".json") and f != "instructions.md"])
                mcps.append({
                    "name": item,
                    "desc": f"MCP Server {item} tích hợp Antigravity",
                    "tools": f"{tools_count} tools",
                    "status": "active"
                })
    return mcps  # không có MCP cài trên máy → danh sách rỗng, không bịa

# =========================================================================
# GEN WORKPLACE IDE: OWNER ↔ GEN CORE ENGINE (3-COLUMN STUDIO)
# =========================================================================

def get_owner_profile(owner_id="owner-ryan"):
    """Truy xuất hồ sơ Owner Ryan cùng số liệu thống kê toàn bộ tài sản dữ liệu thuộc quyền sở hữu."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM owner_profiles WHERE id = ?", (owner_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("SELECT * FROM owner_profiles WHERE is_primary = 1 LIMIT 1")
            row = cursor.fetchone()
        
        if not row:
            return {
                "id": "owner-ryan",
                "username": "ryan",
                "display_name": "Ryan (Owner)",
                "role": "Chủ Sở Hữu & Kiến Trúc Sư Trưởng Tối Cao (System Owner & Sovereign)",
                "email": "owner@genesis.local",
                "avatar": "👑",
                "bio": "Single Source of Truth tối cao và chủ sở hữu độc quyền toàn bộ dữ liệu hệ thống Gen Workplace & Genesis Swarm.",
                "storage_path": HOME_DIR,
                "workspace_root": str(BASE_DIR / "workspace"),
                "settings": {"theme": "dark", "data_ownership": "exclusive_ryan", "isolation_level": "strict"},
                "stats": {
                    "total_projects": 1,
                    "total_conversations": 0,
                    "total_messages": 0,
                    "total_notes": 0,
                    "total_session_files": 0,
                    "total_tmux_sessions": 6
                },
                "ownership_guarantee": "Toàn bộ dữ liệu thuộc quyền sở hữu độc quyền của Owner Ryan."
            }

        profile = dict(row)
        try:
            profile["settings"] = json.loads(profile.get("settings_json") or "{}")
        except Exception:
            profile["settings"] = {}

        # Thống kê khối lượng tài sản dữ liệu thuộc sở hữu của Ryan
        stats = {
            "total_projects": 1,
            "total_conversations": 0,
            "total_messages": 0,
            "total_notes": 0,
            "total_session_files": 0,
            "total_tmux_sessions": 6
        }
        try:
            cursor.execute("SELECT count(*) FROM projects WHERE owner_id = ? OR owner_id IS NULL", (owner_id,))
            stats["total_projects"] = cursor.fetchone()[0]

            cursor.execute("SELECT count(*) FROM gen_conversations WHERE owner_id = ? OR owner_id IS NULL", (owner_id,))
            stats["total_conversations"] = cursor.fetchone()[0]

            cursor.execute("SELECT count(*) FROM gen_messages WHERE owner_id = ? OR owner_id IS NULL", (owner_id,))
            stats["total_messages"] = cursor.fetchone()[0]

            cursor.execute("SELECT count(*) FROM gen_scratchpad_notes WHERE owner_id = ? OR owner_id IS NULL", (owner_id,))
            stats["total_notes"] = cursor.fetchone()[0]

            cursor.execute("SELECT count(*) FROM gen_session_files WHERE owner_id = ? OR owner_id IS NULL", (owner_id,))
            stats["total_session_files"] = cursor.fetchone()[0]

            cursor.execute("SELECT count(*) FROM tmux_sessions WHERE owner_id = ? OR owner_id IS NULL", (owner_id,))
            stats["total_tmux_sessions"] = cursor.fetchone()[0]
        except Exception:
            pass

        profile["stats"] = stats
        profile["ownership_guarantee"] = "100% Dữ liệu (Dự án, Phiên hội thoại, Tập tin Workspace, Sổ tay Scratchpad, Token và Tmux Runtimes) thuộc quyền sở hữu riêng biệt và độc quyền của Owner Ryan. Hệ thống cô lập triệt để, ngăn chặn rò rỉ hoặc chia sẻ ngoài ý muốn."
        return profile

def update_owner_profile(owner_id="owner-ryan", display_name=None, email=None, bio=None, settings=None):
    """Cập nhật thông tin hồ sơ và tùy chọn bảo mật của Owner Ryan."""
    with get_connection() as conn:
        cursor = conn.cursor()
        updates = []
        params = []
        if display_name:
            updates.append("display_name = ?")
            params.append(display_name)
        if email:
            updates.append("email = ?")
            params.append(email)
        if bio:
            updates.append("bio = ?")
            params.append(bio)
        if settings is not None:
            updates.append("settings_json = ?")
            params.append(json.dumps(settings, ensure_ascii=False) if isinstance(settings, dict) else str(settings))
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(owner_id)
            cursor.execute(f"UPDATE owner_profiles SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
    return {"status": "ok", "message": "Đã cập nhật hồ sơ Owner Ryan thành công"}

def default_conv_id(project_id="PRJ-GEN-WORKPLACE"):
    """ID phiên chat gần nhất (dùng khi caller không truyền conv_id); rỗng nếu chưa có phiên nào."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM gen_conversations WHERE project_id = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (normalize_project_id(project_id),)).fetchone()
        return row["id"] if row else ""

def get_gen_conversations(project_id="PRJ-GEN-WORKPLACE", owner_id="owner-ryan"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT c.*, 
               (SELECT count(*) FROM gen_messages m WHERE m.conversation_id = c.id) as msg_count,
               (SELECT content FROM gen_messages m WHERE m.conversation_id = c.id ORDER BY id DESC LIMIT 1) as last_msg,
               (SELECT created_at FROM gen_messages m WHERE m.conversation_id = c.id ORDER BY id DESC LIMIT 1) as last_time
        FROM gen_conversations c
        WHERE c.project_id = ? AND (c.owner_id = ? OR c.owner_id IS NULL)
        ORDER BY c.is_pinned DESC, c.updated_at DESC
        """, (project_id, owner_id))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["open_tabs"] = json.loads(d.get("open_tabs_json") or '["backend/main.py"]')
            except Exception:
                d["open_tabs"] = ["backend/main.py"]
            result.append(d)
        return result

def gen_conversation_exists(conv_id):
    """True nếu phiên chat conv_id có trong gen_conversations (kiểm trước khi INSERT bảng con có FOREIGN KEY)."""
    if not conv_id:
        return False
    with get_connection() as conn:
        return conn.execute("SELECT 1 FROM gen_conversations WHERE id = ?", (conv_id,)).fetchone() is not None

def create_gen_conversation(project_id="PRJ-GEN-WORKPLACE", title="Cuộc trò chuyện mới", model="Gemini 3.1 Pro (High)", account="owner_default", owner_id="owner-ryan"):
    conv_id = f"conv-{uuid.uuid4().hex[:8]}"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO gen_conversations (id, project_id, title, model, account_profile, is_pinned, active_tab, active_file, open_tabs_json, active_evidence_id, owner_id)
        VALUES (?, ?, ?, ?, ?, 0, 'files_repo', 'backend/main.py', '["backend/main.py"]', '', ?)
        """, (conv_id, project_id, title, model, account, owner_id))
        conn.commit()
    return {"id": conv_id, "title": title, "model": model, "account": account, "active_tab": "files_repo", "active_file": "backend/main.py", "open_tabs": ["backend/main.py"], "active_evidence_id": "NOTE-01", "owner_id": owner_id}

def find_gen_conversation_by_title(project_id, title, owner_id="owner-ryan"):
    """Phiên mới nhất có đúng tiêu đề title (dùng lại phiên 'VIEC-<n>: ...' thay vì tạo trùng); không có → None."""
    with get_connection() as conn:
        row = conn.execute("""
        SELECT id, title, model, account_profile, owner_id FROM gen_conversations
        WHERE project_id = ? AND title = ? AND (owner_id = ? OR owner_id IS NULL)
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """, (normalize_project_id(project_id), title, owner_id)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "title": row["title"], "model": row["model"], "account": row["account_profile"], "owner_id": row["owner_id"] or owner_id}

LOG_MESSAGE_ROLES = ("assistant", "user")
LOG_MESSAGE_MAX_LEN = 8000

def log_gen_message(conv_id, content, role="assistant", author="AI Agent"):
    """Ghi 1 tin tiến độ vào phiên (#19): CHỈ INSERT gen_messages + đẩy phiên lên đầu danh sách, không gọi agy/AI."""
    conv_id = (conv_id or "").strip()
    content = (content or "").strip()
    role = (role or "assistant").strip().lower()
    author = (author or "AI Agent").strip()[:120] or "AI Agent"
    if not conv_id:
        return {"error": "Thiếu conv_id (tạo phiên bằng create_conversation)"}
    if not content:
        return {"error": "Thiếu content"}
    if len(content) > LOG_MESSAGE_MAX_LEN:
        return {"error": f"content quá dài ({len(content)} > {LOG_MESSAGE_MAX_LEN} ký tự); ghi tóm tắt kèm link"}
    if role not in LOG_MESSAGE_ROLES:
        return {"error": f"role không hợp lệ '{role}' (chỉ nhận: {', '.join(LOG_MESSAGE_ROLES)})"}
    with get_connection() as conn:
        if not conn.execute("SELECT 1 FROM gen_conversations WHERE id = ?", (conv_id,)).fetchone():
            return {"error": f"Không tìm thấy phiên '{conv_id}'"}
        cur = conn.execute("""
        INSERT INTO gen_messages (conversation_id, author, role, content, model, note_ids_json, owner_id)
        VALUES (?, ?, ?, ?, '', '[]', 'owner-ryan')
        """, (conv_id, author, role, content))
        msg_id = cur.lastrowid
        conn.execute("UPDATE gen_conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conv_id,))
        conn.commit()
        created_at = conn.execute("SELECT created_at FROM gen_messages WHERE id = ?", (msg_id,)).fetchone()["created_at"]
    return {"status": "logged", "message_id": msg_id, "conv_id": conv_id, "role": role, "author": author, "created_at": created_at}

def update_gen_conversation_context(conv_id, active_tab=None, active_file=None, open_tabs=None, active_evidence_id=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        updates = []
        params = []
        if active_tab is not None:
            updates.append("active_tab = ?")
            params.append(active_tab)
        if active_file is not None:
            updates.append("active_file = ?")
            params.append(active_file)
        if open_tabs is not None:
            updates.append("open_tabs_json = ?")
            params.append(json.dumps(open_tabs) if isinstance(open_tabs, list) else str(open_tabs))
        if active_evidence_id is not None:
            updates.append("active_evidence_id = ?")
            params.append(active_evidence_id)
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(conv_id)
            cursor.execute(f"UPDATE gen_conversations SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
    return {"status": "ok", "conv_id": conv_id}

def update_gen_conversation(conv_id, title=None, is_pinned=None, model=None, account=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        if title is not None:
            cursor.execute("UPDATE gen_conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (title, conv_id))
        if is_pinned is not None:
            cursor.execute("UPDATE gen_conversations SET is_pinned = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (1 if is_pinned else 0, conv_id))
        if model is not None:
            cursor.execute("UPDATE gen_conversations SET model = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (model, conv_id))
        if account is not None:
            cursor.execute("UPDATE gen_conversations SET account_profile = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (account, conv_id))
        conn.commit()
    return {"status": "ok", "conv_id": conv_id}

def delete_gen_conversation(conv_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM gen_messages WHERE conversation_id = ?", (conv_id,))
        cursor.execute("DELETE FROM gen_compact_snapshots WHERE conversation_id = ?", (conv_id,))
        cursor.execute("DELETE FROM gen_scratchpad_notes WHERE conversation_id = ?", (conv_id,))
        cursor.execute("DELETE FROM gen_conversations WHERE id = ?", (conv_id,))
        conn.commit()
    return {"status": "deleted", "conv_id": conv_id}

def get_gen_messages(conv_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM gen_messages 
        WHERE conversation_id = ? 
        ORDER BY id ASC
        """, (conv_id,))
        rows = cursor.fetchall()
        msgs = []
        for r in rows:
            msgs.append({
                "id": r["id"],
                "conversation_id": r["conversation_id"],
                "author": r["author"],
                "role": r["role"],
                "content": r["content"],
                "model": r["model"],
                "note_ids": json.loads(r["note_ids_json"] or "[]"),
                "is_compacted": bool(r["is_compacted"]),
                "compact_id": r["compact_id"],
                "created_at": r["created_at"]
            })
        return msgs

AGY_MODEL_MAPPING = {
    "gemini-3.8-flash-high": "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low": "gemini-3.8-flash-low",
    "gemini-3.7-flash-high": "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium": "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low": "gemini-3.7-flash-low",
    "gemini-3.6-flash-high": "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium": "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low": "gemini-3.6-flash-low",
    "gemini-3.1-pro-high": "gemini-3.1-pro-high",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium": "gpt-oss-120b-medium",
    "gemini 3.8 flash (high)": "gemini-3.8-flash-high",
    "gemini 3.8 flash (medium)": "gemini-3.8-flash-medium",
    "gemini 3.8 flash (low)": "gemini-3.8-flash-low",
    "gemini 3.7 flash (high)": "gemini-3.7-flash-high",
    "gemini 3.7 flash (medium)": "gemini-3.7-flash-medium",
    "gemini 3.7 flash (low)": "gemini-3.7-flash-low",
    "gemini 3.6 flash (high)": "gemini-3.6-flash-high",
    "gemini 3.1 pro (high)": "gemini-3.1-pro-high",
    "gemini 3.1 pro (low)": "gemini-3.1-pro-low",
    "claude sonnet 4.6 (thinking)": "claude-sonnet-4-6",
    "claude opus 4.6 (thinking)": "claude-opus-4-6-thinking",
    "gpt-oss 120b (medium)": "gpt-oss-120b-medium"
}

def resolve_agy_model_slug(model_name):
    if not model_name or not str(model_name).strip():
        return "gemini-3.8-flash-high"
    cleaned = str(model_name).strip().lower()
    if cleaned in AGY_MODEL_MAPPING:
        return AGY_MODEL_MAPPING[cleaned]
    for k, v in AGY_MODEL_MAPPING.items():
        if k in cleaned or cleaned in k:
            return v
    if "flash" in cleaned:
        return "gemini-3.8-flash-high"
    if "sonnet" in cleaned or "claude" in cleaned:
        return "claude-sonnet-4-6"
    if "pro" in cleaned:
        return "gemini-3.1-pro-high"
    return "gemini-3.8-flash-high"

AGY_CHAT_TIMEOUT_SEC = int(os.environ.get("GW_AGY_CHAT_TIMEOUT_SEC", "180") or 180)

def _parse_agy_json_turn(stdout):
    """Đọc JSON agy --output-format json → (reply, conversation_id, usage). stdout không phải JSON → coi cả stdout là reply."""
    raw = (stdout or "").strip()
    if not raw:
        return "", None, {}
    try:
        data = json.loads(raw)
    except Exception:
        return raw, None, {}
    if not isinstance(data, dict):
        return raw, None, {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return (data.get("response") or "").strip(), data.get("conversation_id"), usage

def describe_agy_error(usage):
    """Diễn giải mã lỗi trong usage (do call_agy_cli_turn trả) thành lý do tiếng Việt, kèm output thật nếu có."""
    usage = usage if isinstance(usage, dict) else {}
    code = str(usage.get("error") or "UNKNOWN")
    detail = (usage.get("detail") or "").strip()
    if code == "RESOURCE_EXHAUSTED":
        reset = usage.get("reset_at") or ""
        reason = "RESOURCE_EXHAUSTED / 429 (hết quota" + (f", hồi {reset}" if reset else "") + ")"
    elif code == "TIMEOUT":
        reason = f"timeout sau {usage.get('timeout_sec', AGY_CHAT_TIMEOUT_SEC)}s"
    elif code == "AGY_NOT_FOUND":
        reason = f"không tìm thấy lệnh agy ({usage.get('bin') or _agy_bin()})"
    elif code == "EMPTY_RESPONSE":
        reason = "agy thoát 0 nhưng không có nội dung trả lời"
    elif code == "PERMISSION_DENIED":
        dirs = ", ".join(usage.get("read_dirs") or []) or "thư mục làm việc"
        reason = (f"agy bị từ chối quyền / không ra kết quả ({usage.get('match') or 'auto-denied'}). Chat Gen chạy KHÔNG có "
                  f"--dangerously-skip-permissions: chỉ được đọc trong {dirs} và chạy lệnh chỉ đọc (ls, cat, grep, git log...). "
                  "Việc cần ghi/sửa file hãy giao qua war-room / worker; lối thoát cuối (không khuyến nghị): GW_GEN_CHAT_SKIP_PERMISSIONS=1")
    elif code.startswith("EXIT_"):
        reason = f"agy thoát mã {code[5:]}"
    else:
        reason = code
    if usage.get("all_exhausted") and usage.get("fallback_note"):
        reason += f"; {usage['fallback_note']}"
    if detail:
        reason += f" — {detail[-600:]}"
    return reason

def call_agy_cli_turn(conv_id, user_message, model=None, account="owner_default"):
    """
    Gọi agy CLI thật cho 1 lượt chat:
    - Kế thừa ngữ cảnh phiên (agy_conv_id), dùng profile OAuth của account, ghi quota_probe.
    - Trả (reply, conversation_id, usage). KHÔNG bao giờ bịa reply: lỗi → reply rỗng và usage["error"]
      là mã lỗi thật (RESOURCE_EXHAUSTED / TIMEOUT / AGY_NOT_FOUND / EXIT_<rc> / EMPTY_RESPONSE / PERMISSION_DENIED / EXCEPTION),
      usage["detail"] là đuôi output thật của agy.
    """
    agy_conv_id = None
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT agy_conv_id FROM gen_conversations WHERE id = ?", (conv_id,))
            row = c.fetchone()
            if row and row["agy_conv_id"]:
                agy_conv_id = row["agy_conv_id"].strip()
    except Exception:
        pass

    p_dir = _profile_dir(account)
    env = _agy_env(p_dir)

    # Kanban & checklist của phiên (nếu có) để định hướng agent
    sess_todos = get_gen_session_todos(conv_id)
    if sess_todos:
        kanban_block = format_session_kanban_for_agent(conv_id, sess_todos)
        prompt_payload = f"{kanban_block}\n\n[TIN NHẮN TRỰC TIẾP TỪ SẾP RYAN]:\n{user_message}"
    else:
        prompt_payload = user_message

    model_slug = resolve_agy_model_slug(model)

    # Thư mục làm việc: thư mục riêng của phiên nếu có, không thì thư mục repo
    work_dir = "/workspace" if os.path.isdir("/workspace") else str(BASE_DIR)
    sess_dir = Path("/workspace/sessions") / conv_id
    if not sess_dir.exists():
        sess_dir = BASE_DIR / "workspace" / "sessions" / conv_id
    has_sess_dir = sess_dir.exists()
    if has_sess_dir:
        work_dir = str(sess_dir)

    # Quyền (Issue #7): mặc định KHÔNG --dangerously-skip-permissions. agy --print không tương tác nên tool cần quyền
    # bị auto-denied → cấp quy tắc chỉ đọc (read_file(thư mục làm việc + repo) + command(ls|cat|grep|git log|...)) trong
    # permissions.allow của hồ sơ agy, giống war-room (PR #15). Lối thoát cuối, opt-in: GW_GEN_CHAT_SKIP_PERMISSIONS=1.
    skip_permissions = _env_on("GW_GEN_CHAT_SKIP_PERMISSIONS")
    read_dirs = []
    for d in (work_dir, str(BASE_DIR)):
        real = os.path.realpath(d)
        if real not in read_dirs:
            read_dirs.append(real)
    cmd = [_agy_bin(), "--output-format", "json", "--print", prompt_payload]
    if skip_permissions:
        cmd.append(AGY_SKIP_PERMISSIONS_FLAG)
    cmd.extend(["--model", model_slug])

    def _cmd_for(with_conv):
        c = list(cmd)
        if with_conv and agy_conv_id:
            c.extend(["--conversation", agy_conv_id])
        if has_sess_dir:
            c.extend(["--add-dir", str(sess_dir)])
        return c

    def _save_conv(ret_id, tokens, save_id=True):
        if not ret_id:
            return
        try:
            with get_connection() as conn:
                if save_id:
                    conn.execute("UPDATE gen_conversations SET agy_conv_id = ?, total_tokens = coalesce(total_tokens, 0) + ? WHERE id = ?",
                                 (ret_id, int(tokens or 0), conv_id))
                else:
                    conn.execute("UPDATE gen_conversations SET total_tokens = coalesce(total_tokens, 0) + ? WHERE id = ?",
                                 (int(tokens or 0), conv_id))
                conn.commit()
        except Exception:
            pass

    def _run(argv, pid, pdir):
        own = pid == account
        try:
            res = subprocess.run(argv, capture_output=True, text=True, env=_agy_env(pdir), cwd=work_dir, timeout=AGY_CHAT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            record_quota_probe(pid, model_slug, "timeout", "", f"agy không phản hồi sau {AGY_CHAT_TIMEOUT_SEC}s")
            return "", agy_conv_id, {"error": "TIMEOUT", "timeout_sec": AGY_CHAT_TIMEOUT_SEC}
        except FileNotFoundError as e:
            return "", agy_conv_id, {"error": "AGY_NOT_FOUND", "bin": _agy_bin(), "detail": str(e)}
        except Exception as e:
            return "", agy_conv_id, {"error": "EXCEPTION", "detail": str(e)}
        status, reset_at = record_quota_probe_from_result(pid, model_slug, res)
        err_output = ((res.stderr or "") + "\n" + (res.stdout or "")).strip()
        if status == "rate_limited":
            return "", agy_conv_id, {"error": "RESOURCE_EXHAUSTED", "reset_at": reset_at, "detail": err_output[-800:]}
        if res.returncode != 0:
            print(f"[AGY Runner] returncode={res.returncode}, err: {err_output[:300]}")
            return "", agy_conv_id, {"error": f"EXIT_{res.returncode}", "detail": err_output[-800:]}
        reply, ret_id, usage = _parse_agy_json_turn(res.stdout)
        # Lượt chạy bằng hồ sơ khác (tự chuyển khi hết quota) không ghi đè agy_conv_id: phiên agy thuộc tài khoản khác
        _save_conv(ret_id, usage.get("total_tokens", 0) if isinstance(usage, dict) else 0, save_id=own)
        # Thoát 0 nhưng bị từ chối quyền ("no output produced", "auto-denied") → lỗi rõ, không coi là trả lời
        if reply:
            denied = agy_output_denied(reply)
        else:
            m = AGY_DENIED_RE.search(err_output)
            denied = m.group(0) if m else ""
        if denied:
            print(f"[AGY Runner] chat Gen bị từ chối quyền ({denied}): {err_output[-300:]}")
            return "", ret_id or agy_conv_id, {"error": "PERMISSION_DENIED", "match": denied, "read_dirs": read_dirs,
                                               "skip_permissions": skip_permissions, "detail": (err_output or reply)[-800:]}
        if not reply:
            return "", ret_id or agy_conv_id, {"error": "EMPTY_RESPONSE", "detail": err_output[-800:]}
        return reply, (ret_id or agy_conv_id) if own else agy_conv_id, dict(usage)

    def _attempt(pid, pdir):
        """1 lượt chat với hồ sơ pid; hồ sơ khác hồ sơ của phiên (fallback #22) chạy lượt mới, không --conversation."""
        nonlocal agy_conv_id
        if not skip_permissions:
            ensure_agy_plan_permissions(pdir, read_dirs[0], extra_dirs=read_dirs[1:])
        own = pid == account
        reply, ret_id, usage = _run(_cmd_for(own), pid, pdir)
        # Phiên agy cũ hỏng/hết hạn (không phải lỗi quota) → xóa agy_conv_id, thử lại 1 lần với lượt mới
        if own and not reply and agy_conv_id and usage.get("error", "").startswith("EXIT_"):
            print(f"[AGY Runner] agy_conv_id '{agy_conv_id}' lỗi, thử lại lượt mới không --conversation")
            try:
                with get_connection() as conn:
                    conn.execute("UPDATE gen_conversations SET agy_conv_id = '' WHERE id = ?", (conv_id,))
                    conn.commit()
            except Exception:
                pass
            agy_conv_id = None
            reply, ret_id, usage = _run(_cmd_for(False), pid, pdir)
        err = usage.get("error", "") if isinstance(usage, dict) else ""
        status = "rate_limited" if err == "RESOURCE_EXHAUSTED" else ("ok" if reply else ("timeout" if err == "TIMEOUT" else "error"))
        return status, usage.get("reset_at", "") if isinstance(usage, dict) else "", (reply, ret_id, usage)

    # Hết quota → tự chạy lại lượt chat với hồ sơ khác còn quota (#22); không đổi tài khoản của phiên chat
    fb = run_agy_with_quota_fallback(account, p_dir, _attempt, family=quota_family(model_slug))
    if fb["payload"]:
        reply, ret_id, usage = fb["payload"]
    else:
        reply, ret_id, usage = "", agy_conv_id, {"error": "RESOURCE_EXHAUSTED", "reset_at": "", "detail": ""}
    usage = dict(usage) if isinstance(usage, dict) else {}
    usage.update({"profile_initial": fb["profile_initial"], "profile_used": fb["profile_used"], "fallback": fb["fallback"],
                  "fallback_note": fb["note"], "all_exhausted": fb["all_exhausted"], "earliest_reset": fb["earliest_reset"]})
    return reply, ret_id, usage

def send_gen_chat(conv_id, author, message, model, account="owner_default"):
    """Lưu tin người dùng, gọi agy thật, lưu trả lời. agy lỗi → lưu tin 'Gen (lỗi)' với lý do thật, không tự sinh phản hồi."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM gen_conversations WHERE id = ?", (conv_id,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO gen_conversations (id, project_id, title, model, account_profile, is_pinned, active_tab, active_file, open_tabs_json, active_evidence_id, owner_id)
            VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, 0, 'files_repo', 'backend/main.py', '["backend/main.py"]', '', 'owner-ryan')
            """, (conv_id, f"Phiên {conv_id}", model, account))
            conn.commit()

        cursor.execute("""
        INSERT INTO gen_messages (conversation_id, author, role, content, model, note_ids_json, owner_id)
        VALUES (?, ?, 'user', ?, ?, '[]', 'owner-ryan')
        """, (conv_id, author, message, model))
        user_msg_id = cursor.lastrowid
        cursor.execute("UPDATE gen_conversations SET model = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (model, conv_id))
        conn.commit()

    actual_reply, ret_conv_id, usage = call_agy_cli_turn(conv_id, message, model, account)
    usage = usage if isinstance(usage, dict) else {}
    if actual_reply:
        reply_content = actual_reply
        engine_used = "agy-cli"
        author_name = "Gen Core (agy CLI)"
        cited_notes = sorted(set(re.findall(r'#(?:NOTE|EVT|SEC|TOOL|FILE)-\d+', reply_content)))
        is_error = False
        applied_kanban = parse_and_apply_agent_kanban_updates(conv_id, reply_content)
        refused = [u for u in applied_kanban if "BỊ TỪ CHỐI" in u]
        if refused:
            # Ghi rõ vào tin trả lời để người dùng thấy task KHÔNG được chuyển (không im lặng coi như xong)
            reply_content += "\n\n[Kanban] " + "\n[Kanban] ".join(refused)
        if usage.get("fallback") and usage.get("fallback_note"):
            reply_content += f"\n\n[Tài khoản] {usage['fallback_note']}."
    else:
        reply_content = f"agy không trả lời: {describe_agy_error(usage)}. Không có phản hồi tự sinh."
        engine_used = "error"
        author_name = "Gen (lỗi)"
        cited_notes = []
        is_error = True
        applied_kanban = []

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO gen_messages (conversation_id, author, role, content, model, note_ids_json, owner_id)
        VALUES (?, ?, 'assistant', ?, ?, ?, 'owner-ryan')
        """, (conv_id, author_name, reply_content, model, json.dumps(cited_notes, ensure_ascii=False)))
        reply_id = cursor.lastrowid
        conn.commit()

    updated_title = conv_id
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT title FROM gen_conversations WHERE id = ?", (conv_id,)).fetchone()
            if row and row["title"]:
                updated_title = row["title"]
    except Exception:
        pass

    return {
        "user_msg_id": user_msg_id,
        "reply_id": reply_id,
        "reply": reply_content,
        "author": author_name,
        "cited_notes": cited_notes,
        "model": model,
        "conv_title": updated_title,
        "engine": engine_used,
        "error": is_error,
        "error_code": usage.get("error", "") if is_error else "",
        "usage": usage,
        "account_used": usage.get("profile_used") or account,
        "fallback_note": usage.get("fallback_note", ""),
        "kanban_updates": applied_kanban
    }

def _shorten_line(text, limit=160):
    """Rút gọn 1 tin nhắn về 1 dòng (gộp khoảng trắng), cắt ở limit ký tự kèm '…' — nguyên văn, không thêm chữ."""
    one_line = " ".join((text or "").split())
    return one_line if len(one_line) <= limit else one_line[:limit].rstrip() + "…"


def compact_gen_conversation(conv_id, model_from="", model_to="", manual=False):
    """Nén (compact) các tin chưa nén của phiên thành 1 tóm tắt CHỈ gồm dữ liệu thật; < 2 tin → skipped, không ghi gì."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if model_to and model_to.strip():
            cursor.execute("UPDATE gen_conversations SET model = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (model_to.strip(), conv_id))
            conn.commit()

        cursor.execute("""
        SELECT * FROM gen_messages
        WHERE conversation_id = ? AND is_compacted = 0 AND role != 'compact'
        ORDER BY id ASC
        """, (conv_id,))
        uncompacted = cursor.fetchall()
        # manual hay không cũng vậy: không có gì để nén thì không ghi snapshot / tin tóm tắt nào
        if len(uncompacted) < 2:
            return {"status": "skipped", "reason": "Chưa đủ tin để nén", "message_count": len(uncompacted)}

        count = len(uncompacted)
        all_notes = set()
        user_msgs = []
        assistant_msgs = []
        import re
        for m in uncompacted:
            content = m["content"] or ""
            for f in re.findall(r'#NOTE-\d+|#EVT-\d+', content):
                all_notes.add(f)
            if m["role"] == "user":
                user_msgs.append(content)
            elif m["role"] == "assistant":
                assistant_msgs.append(content)

        cpt_id = f"CPT-{uuid.uuid4().hex[:6].upper()}"
        notes_list = sorted(list(all_notes))
        last_users = [t for t in (_shorten_line(x) for x in user_msgs[-3:]) if t]
        last_assistants = [t for t in (_shorten_line(x) for x in assistant_msgs[-3:]) if t]

        # Tóm tắt chỉ gồm: số tin thật, trích nguyên văn (rút gọn) 3 câu hỏi / 3 trả lời gần nhất, danh sách #NOTE/#EVT thật
        lines = [f"📦 **Nén ngữ cảnh ({cpt_id})**: đã nén {count} tin nhắn trước đó"
                 + (f", chuyển từ `{model_from}` sang `{model_to}`" if (model_from or model_to) else "") + "."]
        if last_users:
            lines.append(f"- **Câu hỏi người dùng gần nhất ({len(last_users)}/{len(user_msgs)}):**")
            lines.extend(f"  {i}. {t}" for i, t in enumerate(last_users, 1))
        if last_assistants:
            lines.append(f"- **Trả lời agy gần nhất ({len(last_assistants)}/{len(assistant_msgs)}):**")
            lines.extend(f"  {i}. {t}" for i, t in enumerate(last_assistants, 1))
        lines.append("- **Note/Event được nhắc tới:** " + (", ".join(notes_list) if notes_list else "không có"))
        summary = "\n".join(lines)

        cursor.execute("""
        INSERT INTO gen_compact_snapshots (id, conversation_id, model_from, model_to, summary, note_ids_json, message_count, owner_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'owner-ryan')
        """, (cpt_id, conv_id, model_from, model_to, summary, json.dumps(notes_list, ensure_ascii=False), count))

        for m in uncompacted:
            cursor.execute("UPDATE gen_messages SET is_compacted = 1, compact_id = ? WHERE id = ?", (cpt_id, m["id"]))

        cursor.execute("""
        INSERT INTO gen_messages (conversation_id, author, role, content, model, note_ids_json, compact_id, owner_id)
        VALUES (?, 'Hệ Thống', 'compact', ?, ?, ?, ?, 'owner-ryan')
        """, (conv_id, summary, model_to, json.dumps(notes_list, ensure_ascii=False), cpt_id))

        conn.commit()
    return {"status": "compacted", "compact_id": cpt_id, "message_count": count, "note_ids": notes_list, "summary": summary}

def get_gen_notes(project_id="PRJ-GEN-WORKPLACE", conv_id=None, owner_id="owner-ryan"):
    with get_connection() as conn:
        cursor = conn.cursor()
        if conv_id:
            cursor.execute("SELECT * FROM gen_scratchpad_notes WHERE project_id = ? AND (conversation_id = ? OR conversation_id = '') AND (owner_id = ? OR owner_id IS NULL) ORDER BY id ASC", (project_id, conv_id, owner_id))
        else:
            cursor.execute("SELECT * FROM gen_scratchpad_notes WHERE project_id = ? AND (owner_id = ? OR owner_id IS NULL) ORDER BY id ASC", (project_id, owner_id))
        rows = cursor.fetchall()
        notes = []
        for r in rows:
            notes.append({
                "id": r["id"],
                "project_id": r["project_id"],
                "conversation_id": r["conversation_id"],
                "title": r["title"],
                "content": r["content"],
                "tags": json.loads(r["tags_json"] or "[]"),
                "evidence_ref": r["evidence_ref"],
                "author": r["author"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"]
            })
        return notes

def save_gen_note(project_id="PRJ-GEN-WORKPLACE", note_id=None, title="Ghi chú mới", content="", tags=None, evidence_ref="", author="Ryan (Owner)", conv_id="", owner_id="owner-ryan"):
    tags = tags or []
    with get_connection() as conn:
        cursor = conn.cursor()
        if not note_id:
            cursor.execute("SELECT count(*) FROM gen_scratchpad_notes WHERE project_id = ?", (project_id,))
            num = cursor.fetchone()[0] + 1
            note_id = f"NOTE-{num:02d}"
        
        cursor.execute("""
        INSERT INTO gen_scratchpad_notes (id, project_id, conversation_id, title, content, tags_json, evidence_ref, author, owner_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            content = excluded.content,
            tags_json = excluded.tags_json,
            evidence_ref = excluded.evidence_ref,
            author = excluded.author,
            owner_id = excluded.owner_id,
            updated_at = CURRENT_TIMESTAMP
        """, (note_id, project_id, conv_id, title, content, json.dumps(tags, ensure_ascii=False), evidence_ref, author, owner_id))
        conn.commit()
    return {"status": "saved", "id": note_id, "title": title}

def delete_gen_note(note_id, project_id="PRJ-GEN-WORKPLACE"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM gen_scratchpad_notes WHERE id = ? AND project_id = ?", (note_id, project_id))
        conn.commit()
    return {"status": "deleted", "id": note_id}

def get_session_workspace_dir(conv_id):
    base_ws = "/workspace" if os.path.exists("/workspace") else str(BASE_DIR / "workspace")
    sess_dir = os.path.join(base_ws, "sessions", conv_id)
    os.makedirs(sess_dir, exist_ok=True)
    return sess_dir

def get_gen_session_files(conv_id):
    sess_dir = get_session_workspace_dir(conv_id)
    
    # Khởi tạo tệp mẫu ban đầu cho phiên nếu trống
    init_marker = os.path.join(sess_dir, ".init")
    if not os.path.exists(init_marker):
        try:
            with open(init_marker, "w") as f:
                f.write("initialized")
            docs_dir = os.path.join(sess_dir, "docs")
            src_dir = os.path.join(sess_dir, "src")
            os.makedirs(docs_dir, exist_ok=True)
            os.makedirs(src_dir, exist_ok=True)
            with open(os.path.join(sess_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write(f"# Session Workspace: {conv_id}\n\nThư mục làm việc chuyên biệt dành riêng cho phiên làm việc giữa Owner và Gen Core.\n- Mọi tệp và thư mục tại đây được cô lập theo phiên.\n")
            with open(os.path.join(docs_dir, "session_brief.md"), "w", encoding="utf-8") as f:
                f.write(f"# Hồ Sơ Nhiệm Vụ Phiên: {conv_id}\n- Trạng thái: Đang hoạt động\n- Người thực thi: Gen Core Agent\n- Giám sát: Owner Ryan\n")
        except Exception:
            pass

    # Quét tệp và thư mục trên đĩa của phiên
    files = []
    idx = 1
    for root, dirs, fnames in os.walk(sess_dir):
        rel_root = os.path.relpath(root, sess_dir)
        for d in sorted(dirs):
            dir_rel = d if rel_root == "." else os.path.join(rel_root, d)
            files.append({
                "id": f"SDIR-{idx:02d}",
                "name": d,
                "path": dir_rel,
                "is_dir": True,
                "size": "Folder",
                "type": "folder",
                "source": "session"
            })
            idx += 1
        for fn in sorted(fnames):
            if fn == ".init":
                continue
            file_rel = fn if rel_root == "." else os.path.join(rel_root, fn)
            full_path = os.path.join(root, fn)
            try:
                st = os.stat(full_path)
                sz = st.st_size
                sz_str = f"{sz} B" if sz < 1024 else (f"{sz/1024:.1f} KB" if sz < 1024*1024 else f"{sz/(1024*1024):.1f} MB")
                ext = os.path.splitext(fn)[1].lower()
                cat = "doc / spec" if ext == ".md" else ("script" if ext in [".py", ".sh"] else "file")
                files.append({
                    "id": f"SFIL-{idx:02d}",
                    "name": fn,
                    "path": file_rel,
                    "is_dir": False,
                    "size": sz_str,
                    "type": cat,
                    "source": "session",
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
                })
                idx += 1
            except Exception:
                pass

    # Lấy danh sách Repo Reference files từ DB
    repo_refs = []
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM gen_session_files WHERE conversation_id = ? AND file_type = 'repo_ref'", (conv_id,))
        for r in cursor.fetchall():
            repo_refs.append({
                "id": r["id"],
                "name": r["name"],
                "path": r["path"],
                "is_dir": False,
                "size": "Repo ref",
                "type": "repo_ref",
                "source": "repo_ref"
            })

    return {
        "conv_id": conv_id,
        "workspace_dir": f"/workspace/sessions/{conv_id}",
        "files": files,
        "repo_refs": repo_refs
    }

def create_gen_session_file(conv_id, rel_path, is_dir=False, content=""):
    sess_dir = get_session_workspace_dir(conv_id)
    clean_p = rel_path.lstrip("/").replace("\\", "/")
    if ".." in clean_p:
        return {"error": "Invalid path"}
    target = os.path.join(sess_dir, clean_p)
    try:
        if is_dir:
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
        
        file_id = f"SFIL-{uuid.uuid4().hex[:6]}"
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO gen_session_files (id, conversation_id, name, path, file_type, size_bytes, source)
            VALUES (?, ?, ?, ?, ?, ?, 'session')
            """, (file_id, conv_id, os.path.basename(clean_p), clean_p, "folder" if is_dir else "file", len(content.encode("utf-8"))))
            conn.commit()
        return {"status": "ok", "path": clean_p, "is_dir": is_dir}
    except Exception as e:
        return {"error": str(e)}

def delete_gen_session_file(conv_id, rel_path):
    sess_dir = get_session_workspace_dir(conv_id)
    clean_p = rel_path.lstrip("/").replace("\\", "/")
    if ".." in clean_p:
        return {"error": "Invalid path"}
    target = os.path.join(sess_dir, clean_p)
    try:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.exists(target):
            os.remove(target)
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM gen_session_files WHERE conversation_id = ? AND path = ?", (conv_id, clean_p))
            conn.commit()
        return {"status": "deleted", "path": clean_p}
    except Exception as e:
        return {"error": str(e)}

def attach_repo_file_to_session(conv_id, repo_path):
    clean_p = repo_path.lstrip("/").replace("\\", "/")
    file_id = f"SREF-{uuid.uuid4().hex[:6]}"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR REPLACE INTO gen_session_files (id, conversation_id, name, path, file_type, size_bytes, source)
        VALUES (?, ?, ?, ?, 'repo_ref', 0, 'repo_ref')
        """, (file_id, conv_id, os.path.basename(clean_p), clean_p))
        conn.commit()
    return {"status": "attached", "path": clean_p}

def get_file_content_safely(file_path):
    if not file_path or not str(file_path).strip():
        return {"error": "Thiếu file_path (đường dẫn file trong repo hoặc session:<conv_id>/<đường dẫn>)"}
    file_path = str(file_path).strip()
    # Hỗ trợ đọc file thuộc Session Workspace (prefix session:<conv_id>/<rel_path>)
    if file_path.startswith("session:"):
        parts = file_path[len("session:"):].split("/", 1)
        if len(parts) == 2:
            conv_id, rel_p = parts[0], parts[1]
            sess_dir = get_session_workspace_dir(conv_id)
            clean_p = rel_p.lstrip("/").replace("\\", "/")
            if ".." in clean_p:
                return {"error": "Invalid path"}
            target = os.path.abspath(os.path.join(sess_dir, clean_p))
            if not target.startswith(sess_dir) or not os.path.exists(target):
                return {"error": f"Session file không tồn tại: {clean_p}"}
            try:
                sz = os.path.getsize(target)
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                return {"path": file_path, "content": content, "size": sz, "lines": len(content.splitlines()), "is_session_file": True}
            except Exception as e:
                return {"error": str(e)}

    repo_base = find_repo_path()
    clean_p = file_path.lstrip("/").replace("\\", "/")
    if ".." in clean_p:
        return {"error": "Invalid path"}
    target = os.path.abspath(os.path.join(repo_base, clean_p))
    if not target.startswith(repo_base):
        return {"error": "Access denied (outside workspace sandbox)"}
    
    if not os.path.exists(target):
        host_target = os.path.abspath(os.path.join(str(BASE_DIR), clean_p))
        if os.path.exists(host_target):
            target = host_target
        else:
            return {"error": f"File không tồn tại: {clean_p}"}

    try:
        size = os.path.getsize(target)
        if size > 1024 * 1024 * 2:
            return {"error": "File quá lớn (> 2MB)", "size": size}
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": clean_p, "content": content, "size": size, "lines": len(content.splitlines())}
    except Exception as e:
        return {"error": str(e)}

# =========================================================================
# SESSION KANBAN & INTERACTIVE CHECKLIST ENGINE (PER-SESSION DAG)
# =========================================================================

def get_gen_session_todos(conv_id, project_id="PRJ-GEN-WORKPLACE"):
    """Lấy danh sách Kanban Todo & Checklist chuyên dụng của phiên cụ thể."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM gen_session_todos 
        WHERE conversation_id = ?
        ORDER BY order_idx ASC, id ASC
        """, (conv_id,))
        rows = cursor.fetchall()
        todos = []
        for r in rows:
            d = dict(r)
            try:
                d["checklist"] = json.loads(d.get("checklist_json") or "[]")
            except Exception:
                d["checklist"] = []
            
            total_items = len(d["checklist"])
            done_items = sum(1 for it in d["checklist"] if it.get("done"))
            d["total_items"] = total_items
            d["done_items"] = done_items
            d["progress_percent"] = int((done_items / total_items) * 100) if total_items > 0 else (100 if d["status"] == "done" else 0)
            todos.append(d)
    return attach_task_links(todos)

def save_gen_session_todo(conv_id, todo_id=None, title="Nhiệm vụ mới", description="", status="todo", priority="high", assigned_agent="Gen Core", checklist=None, evidence_ref="", order_idx=0, owner_id="owner-ryan", viec_ref="", session_id="", force=False, reason=""):
    """
    Tạo mới / cập nhật task Kanban của phiên. TẠO MỚI bắt buộc viec_ref khớp ^VIEC-[0-9]+$ (mã việc Kho Ryan),
    thiếu/sai → {"error": "Thiếu viec_ref (mã việc trong Kho Ryan, vd VIEC-12)"}. Cập nhật: viec_ref rỗng → giữ giá trị cũ.
    status='done' (task chưa done) KHÔNG ghi thẳng: các trường khác được lưu với trạng thái cũ, rồi chuyển sang done qua
    complete_task (evidence_ref, session_id, force, reason) — bị từ chối → {"error", "code", "saved": True}, trạng thái giữ nguyên.
    Task đã done: không đổi evidence_ref qua đường này (sửa bằng chứng phải qua complete_task force=true).
    """
    checklist = checklist or []
    viec_ok, viec_ref = validate_viec_ref(viec_ref)
    status = (status or "todo").strip().lower()
    if status not in GEN_SESSION_TODO_STATUSES:
        status = "todo"
    evidence_ref = (evidence_ref or "").strip()
    complete_after = False
    with get_connection() as conn:
        cursor = conn.cursor()
        is_new = True
        if todo_id:
            cursor.execute("SELECT viec_ref, conversation_id, status, evidence_ref FROM gen_session_todos WHERE id = ?", (todo_id,))
            existing = cursor.fetchone()
            if existing and existing["conversation_id"] != conv_id:
                return {"error": f"Task {todo_id} thuộc phiên khác ({existing['conversation_id']})", "code": "wrong_conversation", "id": todo_id}
            if existing:
                is_new = False
                if not viec_ref:
                    viec_ref = existing["viec_ref"] or ""
                    viec_ok = True
                if existing["status"] == "done":
                    if status == "done" and evidence_ref and evidence_ref != (existing["evidence_ref"] or ""):
                        return {"error": f"Task {todo_id} đã done với bằng chứng '{existing['evidence_ref'] or ''}' — sửa bằng chứng "
                                         "phải qua complete_task force=true (có ghi nhật ký).", "code": "already_done", "id": todo_id}
                    evidence_ref = existing["evidence_ref"] or ""
                elif status == "done":
                    complete_after = True
                    status = existing["status"]
        if is_new and status == "done":
            complete_after = True
            status = "todo"
        done_evidence = evidence_ref if complete_after else ""
        if complete_after and not is_new:
            evidence_ref = existing["evidence_ref"] or ""
        elif complete_after:
            evidence_ref = ""
        if is_new and not viec_ok:
            return {"error": VIEC_REF_ERROR, "viec_ref": viec_ref, "id": todo_id}
        if not is_new and viec_ref and not viec_ok:
            return {"error": f"viec_ref '{viec_ref}' sai định dạng (vd VIEC-12)", "viec_ref": viec_ref, "id": todo_id}
        if not todo_id:
            cursor.execute("SELECT count(*) FROM gen_session_todos WHERE conversation_id = ?", (conv_id,))
            num = cursor.fetchone()[0] + 1
            todo_id = f"TSK-{num:02d}"
            while cursor.execute("SELECT 1 FROM gen_session_todos WHERE id = ?", (todo_id,)).fetchone():
                num += 1
                todo_id = f"TSK-{num:02d}"

        normalized_chk = []
        for idx, item in enumerate(checklist, 1):
            if isinstance(item, str):
                normalized_chk.append({"id": f"chk-{idx}", "text": item, "done": False})
            elif isinstance(item, dict):
                normalized_chk.append({
                    "id": item.get("id") or f"chk-{idx}",
                    "text": item.get("text", f"Hạng mục {idx}"),
                    "done": bool(item.get("done", False))
                })

        cursor.execute("""
        INSERT INTO gen_session_todos (id, conversation_id, project_id, title, description, status, priority, assigned_agent, checklist_json, evidence_ref, order_idx, owner_id, viec_ref)
        VALUES (?, ?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            status = excluded.status,
            priority = excluded.priority,
            assigned_agent = excluded.assigned_agent,
            checklist_json = excluded.checklist_json,
            evidence_ref = excluded.evidence_ref,
            order_idx = excluded.order_idx,
            owner_id = excluded.owner_id,
            viec_ref = CASE WHEN excluded.viec_ref != '' THEN excluded.viec_ref ELSE gen_session_todos.viec_ref END,
            updated_at = CURRENT_TIMESTAMP
        """, (todo_id, conv_id, title, description, status, priority, assigned_agent, json.dumps(normalized_chk, ensure_ascii=False), evidence_ref, order_idx, owner_id, viec_ref))
        conn.commit()
    res = {"status": "saved", "id": todo_id, "title": title, "viec_ref": viec_ref, "created": is_new, "task_status": status}
    if complete_after:
        done = set_task_status(todo_id, "done", conv_id=conv_id, session_id=session_id, evidence_ref=done_evidence, force=force, reason=reason)
        if "error" in done:
            done.update({"saved": True, "id": todo_id, "title": title, "viec_ref": viec_ref, "created": is_new, "task_status": status,
                         "error": f"Đã lưu các trường khác nhưng KHÔNG chuyển sang done: {done['error']}"})
            return done
        res.update({"task_status": "done", "completed": done})
    return res

def toggle_gen_session_todo_checklist_item(conv_id, todo_id, item_id, done_status=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        if conv_id:
            cursor.execute("SELECT conversation_id, checklist_json, status FROM gen_session_todos WHERE id = ? AND conversation_id = ?", (todo_id, conv_id))
        else:
            cursor.execute("SELECT conversation_id, checklist_json, status FROM gen_session_todos WHERE id = ?", (todo_id,))
        row = cursor.fetchone()
        if not row:
            # Fallback tìm kiếm theo ID nếu conv_id không khớp
            cursor.execute("SELECT conversation_id, checklist_json, status FROM gen_session_todos WHERE id = ?", (todo_id,))
            row = cursor.fetchone()
            if not row:
                return {"error": "Todo not found"}
        
        target_conv_id = row["conversation_id"]
        try:
            chk = json.loads(row["checklist_json"] or "[]")
        except Exception:
            chk = []

        found = False
        all_done = True
        for item in chk:
            if item.get("id") == item_id or item.get("text") == item_id:
                if done_status is not None:
                    item["done"] = bool(done_status)
                else:
                    item["done"] = not item.get("done", False)
                found = True
            if not item.get("done"):
                all_done = False

        if not found:
            # Hỗ trợ alias dạng chk-01, chk-1, 0, 1
            if item_id in ("chk-01", "chk-1", "0", 0) and len(chk) > 0:
                chk[0]["done"] = bool(done_status) if done_status is not None else not chk[0].get("done", False)
                found = True
            elif item_id in ("chk-02", "chk-2", "1", 1) and len(chk) > 1:
                chk[1]["done"] = bool(done_status) if done_status is not None else not chk[1].get("done", False)
                found = True
            else:
                return {"error": f"Checklist item '{item_id}' not found"}

        new_status = row["status"]
        if all_done and len(chk) > 0 and new_status in ("todo", "in_progress"):
            new_status = "review"

        cursor.execute("""
        UPDATE gen_session_todos 
        SET checklist_json = ?, status = ?, updated_at = CURRENT_TIMESTAMP 
        WHERE id = ? AND conversation_id = ?
        """, (json.dumps(chk, ensure_ascii=False), new_status, todo_id, target_conv_id))
        conn.commit()
    return {"status": "updated", "id": todo_id, "item_id": item_id, "all_done": all_done, "new_status": new_status}

GEN_SESSION_TODO_STATUSES = ("todo", "in_progress", "review", "done")

def update_gen_session_todo_status(conv_id, todo_id, new_status, evidence_ref=None, session_id="", force=False, reason=""):
    """Đổi trạng thái task Kanban phiên. Sang 'done' → complete_task (qua set_task_status): thiếu/sai evidence hoặc
    người gọi không giữ task → {"error", "code"}, trạng thái không đổi."""
    return set_task_status(todo_id, new_status, conv_id=conv_id, session_id=session_id, evidence_ref=evidence_ref or "",
                           force=force, reason=reason)

def delete_gen_session_todo(conv_id, todo_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM gen_session_todos WHERE id = ? AND conversation_id = ?", (todo_id, conv_id))
        conn.commit()
        deleted = cursor.rowcount > 0
    res = {"status": "deleted", "id": todo_id}
    if deleted:   # task bị xóa (hủy) → gỡ worktree chế độ Làm, giữ nhánh (#45)
        c = cleanup_task_worktree(todo_id)
        if c.get("removed"):
            res["worktree_removed"] = c["dir"]
    return res

def format_session_kanban_for_agent(conv_id, todos):
    """Định dạng bản tóm tắt Kanban & Checklist của phiên để nhồi vào prompt bắt buộc của Agent."""
    if not todos:
        return ""
    lines = [
        "==================================================",
        f"🚨 [QUY CHẾ BẮT BUỘC: KANBAN & CHECKLIST ĐIỀU HÀNH PHIÊN ({conv_id})]",
        "Là Agent của phiên làm việc này, bạn BẮT BUỘC phải tuân thủ quy trình Kanban và đối soát checklist dưới đây:",
    ]
    for t in todos:
        status_icon = "📋" if t["status"] == "todo" else ("⚡" if t["status"] == "in_progress" else ("🔍" if t["status"] == "review" else "✅"))
        lines.append(f"\n{status_icon} [{t['id']}] ({t['status'].upper()}) - {t['title']} (Ưu tiên: {t['priority']})")
        if t.get("evidence_ref"):
            lines.append(f"   Bằng chứng: {t['evidence_ref']}")
        for item in t.get("checklist", []):
            chk_mark = "[x]" if item.get("done") else "[ ]"
            lines.append(f"   {chk_mark} ({item['id']}) {item['text']}")
    
    lines.append("\n🎯 NGUYÊN TẮC HÀNH XỬ CHO AGENT TRONG MỖI LƯỢT:")
    lines.append("1. TRỌNG TÂM TRỰC TIẾP: Luôn ưu tiên trả lời trực tiếp, chính xác, tự nhiên và đầy đủ câu hỏi hoặc yêu cầu của Sếp Ryan trong phần [TIN NHẮN TRỰC TIẾP TỪ SẾP RYAN].")
    lines.append("2. PHÂN ĐỊNH HÀNH ĐỘNG:")
    lines.append(f"   - Nếu Sếp chỉ hỏi thăm, thảo luận kiến trúc, rà soát tiến độ hoặc xin ý kiến: Hãy trả lời trực tiếp ngay bằng văn bản điều hành. Nếu đính kèm bảng đối soát Kanban, hãy dùng đúng khối chuẩn: ```text\\n[KANBAN_MONITOR: {conv_id}]\\n• [TSK-xx] Tiêu đề : [STATUS] (commit ...)\\n``` (Giao diện chat sẽ tự động thu gọn khối này, Sếp Ryan có thể bấm mở rộng khi cần); TUYỆT ĐỐI KHÔNG tự ý chạy các công cụ bash/tool không liên quan.")
    lines.append("   - Nếu Sếp giao việc thực thi cụ thể (viết mã, sửa file, kiểm thử, tạo file): Mới gọi các công cụ tương ứng để hoàn thành nhiệm vụ.")
    lines.append("3. ĐỐI SOÁT & CẬP NHẬT KANBAN:")
    lines.append("   - Nêu rõ task nào đang liên quan hoặc được giải quyết.")
    lines.append("   - Khi hoàn thành hạng mục checklist hoặc đổi trạng thái task, hãy ghi chú cú pháp chuẩn:")
    lines.append("     [KANBAN_UPDATE: <ID_TASK> | STATUS: <in_progress/review/done> | CHECK: <item_id> | EVIDENCE: <bằng_chứng>]")
    lines.append("   - STATUS: done CHỈ được nhận khi EVIDENCE kiểm được (commit SHA có trong repo, URL PR GitHub có thật, dispatch:<id>, warroom:<id>, file trong ~/gw-reports/)")
    lines.append("     và task không do worker khác đang giữ (claim). Thiếu/sai bằng chứng → hệ thống từ chối, task giữ nguyên trạng thái.")
    lines.append("==================================================")
    return "\n".join(lines)

def gen_chat_session_id(conv_id):
    """Danh tính của agent chat Gen khi đổi trạng thái task (dùng cho kiểm người giữ task trong complete_task)."""
    return f"gen-chat:{conv_id}"

def _describe_kanban_apply(tid, status, res):
    if isinstance(res, dict) and "error" in res:
        return f"Task {tid} -> {status} BỊ TỪ CHỐI: {res['error']}"
    if status == "done":
        return f"Task {tid} -> Hoàn thành (Done), bằng chứng {res.get('evidence_ref', '')} ({res.get('verified_by', '')})"
    return f"Task {tid} -> {status}"

def parse_and_apply_agent_kanban_updates(conv_id, agent_text):
    """Phân tích các chỉ thị cập nhật Kanban từ câu trả lời của Agent và tự động ghi vào SQLite."""
    if not agent_text:
        return []
    updates_made = []
    # Mẫu 1: [KANBAN_UPDATE: TSK-01 | STATUS: in_progress | CHECK: chk-1 | EVIDENCE: ...]
    pattern = r'\[KANBAN_UPDATE:\s*([^\|\]]+)(?:\s*\|\s*STATUS:\s*([^\|\]]+))?(?:\s*\|\s*CHECK:\s*([^\|\]]+))?(?:\s*\|\s*EVIDENCE:\s*([^\]]+))?\]'
    matches = re.findall(pattern, agent_text, re.IGNORECASE)
    for m in matches:
        tid = m[0].strip()
        status = m[1].strip().lower() if m[1] else None
        item_id = m[2].strip() if m[2] else None
        evidence = m[3].strip() if m[3] else None
        
        if item_id:
            toggle_gen_session_todo_checklist_item(conv_id, tid, item_id, done_status=True)
            updates_made.append(f"Checklist {item_id} của {tid} -> Hoàn thành")
        if status:
            res = update_gen_session_todo_status(conv_id, tid, status, evidence_ref=evidence, session_id=gen_chat_session_id(conv_id))
            updates_made.append(_describe_kanban_apply(tid, status, res))

    # Mẫu 2: [TASK_DONE: TSK-01] — không kèm bằng chứng nên complete_task sẽ từ chối (task giữ nguyên trạng thái)
    done_matches = re.findall(r'\[TASK_DONE:\s*([A-Za-z0-9_-]+)\]', agent_text, re.IGNORECASE)
    for tid in done_matches:
        tid = tid.strip()
        res = update_gen_session_todo_status(conv_id, tid, "done", session_id=gen_chat_session_id(conv_id))
        updates_made.append(_describe_kanban_apply(tid, "done", res))

    return updates_made

# Scope token của từng tool MCP: mcp_core điền từ TOOL_META lúc import (db không import mcp_core để tránh vòng import).
MCP_TOOL_SCOPES = {}

def public_origin(origin=None):
    """Gốc URL (scheme://host[:port]) để in link MCP: origin tính từ Host header của request > GW_PUBLIC_ORIGIN >
    http://localhost:<PORT>. Không ghi cứng localhost:8888 (app thường chạy sau IP / domain khác)."""
    o = (origin or os.environ.get("GW_PUBLIC_ORIGIN") or "").strip().rstrip("/")
    if o:
        return o
    return f"http://localhost:{os.environ.get('PORT', '8888')}"

_TOKEN_PREFIX_RE = re.compile(r"^[A-Za-z0-9]{1,8}_(?:[A-Za-z0-9]{1,8}_)?")

def mask_mcp_token(tok):
    """Bản che token để liệt kê (#41): tiền tố (vd gw_live_) + •••• + 4 ký tự cuối. Token ngắn / lạ chỉ còn ••••."""
    tok = str(tok or "")
    if len(tok) < 16:
        return "••••"
    m = _TOKEN_PREFIX_RE.match(tok)
    prefix = m.group(0) if m and len(tok) - len(m.group(0)) >= 12 else ""
    return f"{prefix}••••{tok[-4:]}"

def get_mcp_auth_status(origin=None):
    """Lấy trạng thái cấu hình xác thực MCP."""
    base = public_origin(origin)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM mcp_auth_settings WHERE key = 'require_auth'")
        row = cursor.fetchone()
        req_auth = (row["value"] == "1") if row else False
        cursor.execute("SELECT count(*) FROM mcp_agent_tokens WHERE status = 'active'")
        active_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM mcp_agent_tokens")
        total_count = cursor.fetchone()[0]
        since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("SELECT count(*) FROM mcp_agent_tokens WHERE status = 'active' AND last_used_at >= ?", (since,))
        used_24h = cursor.fetchone()[0]
        return {
            "require_auth": req_auth,
            "active_tokens": active_count,
            "total_tokens": total_count,
            "used_24h": used_24h,
            "origin": base,
            "endpoint": f"{base}/mcp"
        }

def set_mcp_strict_auth(enabled: bool, origin=None):
    """Bật / tắt chế độ bắt buộc xác thực MCP."""
    val = "1" if enabled else "0"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO mcp_auth_settings (key, value, updated_at) VALUES ('require_auth', ?, CURRENT_TIMESTAMP)", (val,))
        conn.commit()
    return get_mcp_auth_status(origin)

def get_mcp_agent_tokens(owner_id="owner-ryan", origin=None):
    """Lấy danh sách các Agent Token đã cấp."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, name, token, client, role, permissions_json, status, expires_at, last_used_at, calls_count, created_at
        FROM mcp_agent_tokens
        ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        tokens = []
        for r in rows:
            masked = mask_mcp_token(r["token"])
            try:
                perms = json.loads(r["permissions_json"])
            except Exception:
                perms = ["all"]
            status = r["status"]
            if status == "active" and r["expires_at"] and str(r["expires_at"]) < datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
                status = "expired"  # quá hạn nhưng chưa ai gọi (verify mới ghi 'expired' vào DB)
            tokens.append({
                "id": r["id"],
                "name": r["name"],
                "token_masked": masked,  # không trả token thô (#41): token đầy đủ chỉ có trong response lúc tạo
                "client": r["client"],
                "role": r["role"],
                "permissions": perms,
                "status": status,
                "expires_at": r["expires_at"] or "Vĩnh viễn",
                "last_used_at": r["last_used_at"] or "Chưa sử dụng",
                "calls_count": r["calls_count"] or 0,
                "created_at": str(r["created_at"])[:16]
            })
        return {
            "tokens": tokens,
            "auth_status": get_mcp_auth_status(origin)
        }

def create_mcp_agent_token(name, permissions=None, expires_days=90, client="Manual Token", role="agent", owner_id="owner-ryan", origin=None):
    """Tạo mới một Agent Token xác thực MCP chuẩn như Gen-hub."""
    if not name or not str(name).strip():
        return {"error": "Tên Agent không được để trống"}

    token_id = f"mcp-tok-{secrets.token_hex(4)}"
    token_val = f"gw_live_{secrets.token_hex(20)}"
    token_hash = hashlib.sha256(token_val.encode('utf-8')).hexdigest()

    perms = permissions if isinstance(permissions, list) and permissions else ["all"]
    perms_json = json.dumps(perms, ensure_ascii=False)

    expires_at = None
    if expires_days and int(expires_days) > 0:
        exp_dt = datetime.now() + timedelta(days=int(expires_days))
        expires_at = exp_dt.strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO mcp_agent_tokens (id, name, token, token_hash, client, role, permissions_json, status, expires_at, owner_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """, (token_id, name.strip(), token_val, token_hash, client, role, perms_json, expires_at, owner_id))
        conn.commit()

    endpoint = f"{public_origin(origin)}/mcp"
    auth_url = f"{endpoint}?token={token_val}"
    curl_snippet = (
        f'curl -X POST {endpoint} \\\n'
        f'  -H "Authorization: Bearer {token_val}" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f'  -d \'{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}\''
    )

    return {
        "ok": True,
        "id": token_id,
        "name": name.strip(),
        "token": token_val,
        "token_masked": mask_mcp_token(token_val),
        "shown_once": True,  # token đầy đủ chỉ trả 1 lần ở đây; danh sách chỉ có bản che
        "endpoint": endpoint,
        "auth_url": auth_url,
        "curl_snippet": curl_snippet,
        "permissions": perms,
        "expires_at": expires_at or "Vĩnh viễn",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def revoke_mcp_agent_token(token_id):
    """Thu hồi quyền / vô hiệu hóa Agent Token."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE mcp_agent_tokens SET status = 'revoked' WHERE id = ?", (token_id,))
        conn.commit()
    return {"ok": True, "status": "revoked", "id": token_id}

def verify_mcp_admin_token(headers=None):
    """Kiểm Bearer token cho thao tác quản trị (tắt bắt buộc token, #41). Chỉ nhận header Authorization: Bearer,
    không nhận ?token= (tránh lộ token qua URL / log). Token phải đang hoạt động, chưa hết hạn, và có role admin
    hoặc quyền đầy đủ ("all" / "*").
    Trả (ok, agent_info | None, http_status, lỗi): 401 = thiếu / sai / hết hạn / đã thu hồi; 403 = token hợp lệ nhưng không đủ quyền."""
    headers = headers or {}
    auth_header = headers.get("Authorization", "") or headers.get("authorization", "")
    token_str = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    if not token_str:
        return (False, None, 401, "Thiếu token: gửi header Authorization: Bearer <token> có quyền admin hoặc toàn quyền")
    token_hash = hashlib.sha256(token_str.encode("utf-8")).hexdigest()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, name, role, permissions_json, status, expires_at FROM mcp_agent_tokens
        WHERE token = ? OR token_hash = ?
        """, (token_str, token_hash))
        row = cursor.fetchone()
        if not row or row["status"] != "active":
            return (False, None, 401, "Token không hợp lệ hoặc đã bị thu hồi")
        if row["expires_at"] and str(row["expires_at"]) < datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
            return (False, None, 401, "Token đã hết hạn")
        try:
            perms = json.loads(row["permissions_json"])
        except Exception:
            perms = []
        info = {"id": row["id"], "name": row["name"], "role": row["role"], "permissions": perms}
        if row["role"] != "admin" and "all" not in perms and "*" not in perms:
            return (False, info, 403, f"Token '{row['name']}' không có quyền admin hoặc toàn quyền")
        cursor.execute("UPDATE mcp_agent_tokens SET last_used_at = ?, calls_count = calls_count + 1 WHERE id = ?",
                       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
        conn.commit()
    return (True, info, 200, None)

def log_mcp_auth_audit(action, allowed, token_id="", token_name="", client_ip="", reason=""):
    """Ghi 1 dòng nhật ký đổi chế độ bắt buộc token MCP (cả lần bị từ chối)."""
    try:
        with get_connection() as conn:
            conn.execute("""
            INSERT INTO mcp_auth_audit (action, allowed, token_id, token_name, client_ip, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (str(action or ""), 1 if allowed else 0, str(token_id or ""), str(token_name or ""), str(client_ip or ""), str(reason or "")[:500]))
            conn.commit()
    except Exception as e:
        print(f"[mcp_auth_audit] Không ghi được nhật ký: {e}")

def get_mcp_auth_audit(limit=50):
    """Đọc nhật ký bật / tắt bắt buộc token MCP, mới nhất trước."""
    limit = max(1, min(int(limit or 50), 500))
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM mcp_auth_audit ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

def verify_mcp_request_auth(headers=None, query=None, tool_name=None):
    """
    Xác thực yêu cầu tới MCP Server chuẩn như Gen-hub.
    Trả về (is_authorized, agent_info, error_message).
    Hỗ trợ Bearer Header và query param ?token=... hoặc ?key=...
    """
    headers = headers or {}
    query = query or {}
    
    auth_header = headers.get("Authorization", "") or headers.get("authorization", "")
    token_str = ""
    if auth_header.lower().startswith("bearer "):
        token_str = auth_header[7:].strip()
    elif "token" in query:
        token_str = query["token"][0] if isinstance(query["token"], list) else str(query["token"])
    elif "key" in query:
        token_str = query["key"][0] if isinstance(query["key"], list) else str(query["key"])

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM mcp_auth_settings WHERE key = 'require_auth'")
        row = cursor.fetchone()
        require_auth = (row["value"] == "1") if row else False

        if not token_str:
            if not require_auth:
                return (True, {"id": "anon", "name": "Local Loopback / Direct CLI", "role": "local", "permissions": ["all"]}, None)
            return (False, None, "Agent chưa xác thực hoặc quyền đã bị thu hồi")

        token_hash = hashlib.sha256(token_str.encode('utf-8')).hexdigest()
        cursor.execute("""
        SELECT id, name, role, permissions_json, status, expires_at, calls_count
        FROM mcp_agent_tokens
        WHERE token = ? OR token_hash = ?
        """, (token_str, token_hash))
        agent_row = cursor.fetchone()

        if not agent_row:
            return (False, None, "Agent chưa xác thực hoặc quyền đã bị thu hồi")

        if agent_row["status"] != "active":
            return (False, None, "Agent chưa xác thực hoặc quyền đã bị thu hồi")

        if agent_row["expires_at"]:
            try:
                exp_dt = datetime.strptime(agent_row["expires_at"], "%Y-%m-%d %H:%M:%S")
                if exp_dt < datetime.now():
                    cursor.execute("UPDATE mcp_agent_tokens SET status = 'expired' WHERE id = ?", (agent_row["id"],))
                    conn.commit()
                    return (False, None, "Agent chưa xác thực hoặc quyền đã bị thu hồi")
            except Exception:
                pass

        try:
            perms = json.loads(agent_row["permissions_json"])
        except Exception:
            perms = ["all"]

        if tool_name and "all" not in perms and "*" not in perms and tool_name not in perms:
            # scope của tool lấy từ mcp_core.TOOL_META (nguồn duy nhất, #28); tool không có scope → chỉ token toàn quyền
            scope = MCP_TOOL_SCOPES.get(tool_name)
            domain_allowed = bool(scope) and scope in perms
            if not domain_allowed:
                return (False, None, f"Agent không có quyền thực thi công cụ '{tool_name}'")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE mcp_agent_tokens SET last_used_at = ?, calls_count = calls_count + 1 WHERE id = ?", (now_str, agent_row["id"]))
        conn.commit()

        agent_data = {
            "id": agent_row["id"],
            "name": agent_row["name"],
            "role": agent_row["role"],
            "permissions": perms
        }
        return (True, agent_data, None)

# Khởi tạo tự động khi import
init_db()
seed_real_project()
seed_tmux_sessions()
retire_roles()



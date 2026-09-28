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
            ("last_heartbeat", "TEXT DEFAULT ''")
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
                              ("request_msg_id", "INTEGER"), ("reply_msg_id", "INTEGER")]:
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
    """Chỉ tạo bản ghi project PRJ-GEN-WORKPLACE và cấu hình 6 vai (agent_roles). Không còn seed roadmap/todo/chat/catalog/sự kiện giả."""
    with get_connection() as conn:
        cursor = conn.cursor()
        spec_path = BASE_DIR / "docs" / "SSOT_ORIGINAL_SPEC.md"
        source_text = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
        cursor.execute("""
        INSERT OR IGNORE INTO projects (id, name, repo_path, branch, plan_file, source_text, meta, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ('PRJ-GEN-WORKPLACE', 'gen-workplace', str(BASE_DIR), 'main', 'docs/SSOT_ORIGINAL_SPEC.md', source_text, '6 vai · SQLite WAL', 'active'))

        roles = [
            ('ROLE-01', 'L', 'Lead Architect', 'Gemini CLI (agy --effort high)', 'Quản trị SSOT, điều phối toàn bộ tiến trình gen-workplace', 'Chịu trách nhiệm bảo toàn SSOT đặc tả gốc, thẩm định evidence từ các role và điều phối live workflow.'),
            ('ROLE-02', 'B', 'Backend & DB Specialist', 'Claude Code CLI', 'Python daemon, SQLite WAL, FTS5 catalog và runner', 'Thực thi API control plane, tối ưu truy vấn FTS5 catalog sub-ms và stream log terminal.'),
            ('ROLE-03', 'F', 'Frontend Specialist', 'Cursor CLI', 'Web console UI, CSS Gen-workplace v1.1, real-time sync', 'Duy trì phong cách thiết kế tối kỹ thuật v1.1, bind dữ liệu thật từ backend và tối ưu UX.'),
            ('ROLE-04', 'D', 'DevOps & Packaging', 'Gemini CLI (agy --agent devops)', 'Systemd service, tmux, cài đặt và cập nhật app trên host', 'Đảm bảo app chạy ổn định trên host Linux (systemd + tmux), script cài đặt/cập nhật 1 lệnh.'),
            ('ROLE-05', 'Q', 'QA Tester', 'Gemini CLI (agy)', 'Kiểm thử cross-platform, test API /api/status, xác thực installer', 'Chạy regression tests, nghiệm thu thanh loading % của installer và báo cáo phản hồi.'),
            ('ROLE-06', 'S', 'Security Auditor', 'Codex Security CLI', 'Phân quyền thư mục, audit file permission, kiểm soát Vault', 'Kiểm tra an toàn phân quyền, cô lập biến môi trường và thẩm định secret boundary.')
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

        # Roadmaps
        cursor.execute("SELECT * FROM roadmaps WHERE project_id = ? ORDER BY order_idx ASC", (project_id,))
        roadmaps = []
        rm_ids = []
        for r in cursor.fetchall():
            roadmaps.append({
                "id": r["id"],
                "title": r["title"],
                "desc": r["description"],
                "todos": r["todos_count"],
                "status": r["status"]
            })
            rm_ids.append(r["id"])

        # Todos grouped by roadmap
        todos = []
        for rm_id in rm_ids:
            cursor.execute("SELECT * FROM todos WHERE roadmap_id = ? ORDER BY id ASC", (rm_id,))
            rm_todos = []
            for t in cursor.fetchall():
                rm_todos.append({
                    "id": t["id"],
                    "title": t["title"],
                    "role": t["assigned_role"],
                    "status": t["status"],
                    "viec_ref": t["viec_ref"] or "",
                    "evidence_ref": t["evidence_ref"] or ""
                })
            todos.append(rm_todos)

        # Roles
        cursor.execute("SELECT * FROM agent_roles WHERE project_id = ? ORDER BY id ASC", (project_id,))
        roles = []
        for rl in cursor.fetchall():
            m_name = rl["model_name"] if "model_name" in rl.keys() and rl["model_name"] else get_default_model_for_role(rl["name"])
            roles.append({
                "id": rl["id"],
                "key": rl["role_key"],
                "name": rl["name"],
                "cli": rl["cli_tool"],
                "model": m_name,
                "scope": rl["scope"],
                "instruction": rl["instruction"]
            })

        # Workflow Nodes
        cursor.execute("SELECT * FROM workflow_nodes WHERE project_id = ? ORDER BY step_index ASC", (project_id,))
        nodes = []
        for n in cursor.fetchall():
            nodes.append({
                "id": n["id"],
                "title": n["title"],
                "role": n["role_name"],
                "status": n["status"],
                "x": n["coord_x"],
                "y": n["coord_y"],
                "input": n["input_desc"],
                "output": n["output_desc"],
                "check": n["check_desc"],
                "handoff": n["handoff_desc"],
                "checklist": json.loads(n["checklist_json"] or "[]")
            })

        # Runtimes
        cursor.execute("SELECT * FROM agent_runtimes WHERE project_id = ? ORDER BY id ASC", (project_id,))
        runtimes = []
        for rt in cursor.fetchall():
            # Get messages for this runtime
            cursor.execute("SELECT * FROM chat_messages WHERE runtime_id = ? ORDER BY id ASC", (rt["id"],))
            msgs = []
            for m in cursor.fetchall():
                msgs.append({
                    "author": m["author"],
                    "time": m["created_time"],
                    "tag": m["tag"],
                    "body": m["body"],
                    "react": json.loads(m["react_json"] or "[]")
                })

            runtimes.append({
                "id": rt["id"],
                "role": rt["role_name"],
                "cli": rt["cli_tool"],
                "task": rt["task_ref"],
                "branch": rt["branch"],
                "status": rt["status"],
                "path": rt["path"],
                "messages": msgs,
                "trace": json.loads(rt["trace_json"] or "[]"),
                "io": json.loads(rt["io_json"] or "{}")
            })

        # Master SSOT
        cursor.execute("SELECT * FROM master_ssot WHERE project_id = ? ORDER BY id ASC", (project_id,))
        ssot = []
        for s in cursor.fetchall():
            ssot.append({
                "id": s["id"],
                "title": s["title"],
                "body": s["body"],
                "source": s["source_ref"],
                "verified": s["verified_time"]
            })

        # Role Memories
        cursor.execute("SELECT * FROM role_memories WHERE project_id = ? ORDER BY id ASC", (project_id,))
        role_memory = []
        for rm in cursor.fetchall():
            role_name = rm["role_name"]
            summary = rm["body"][:85] + ("..." if len(rm["body"]) > 85 else "")
            role_memory.append({
                "id": f"MEM-0{rm['id']}",
                "role": role_name,
                "runtime": f"runtime-{rm['id']}",
                "summary": summary,
                "detail": rm["body"],
                "body": rm["body"],
                "tags": json.loads(rm["tags_json"] or "[]"),
                "synced": bool(rm["synced_to_ssot"]),
                "time": rm["created_time"]
            })

        # Catalog References
        cursor.execute("SELECT * FROM catalog_references WHERE project_id = ? ORDER BY id ASC", (project_id,))
        catalog = []
        for c in cursor.fetchall():
            catalog.append({
                "id": c["id"],
                "type": c["category"],
                "title": c["title"],
                "desc": c["description"],
                "ref": c["ref_path"]
            })

        # Refs array for quick table rendering [id, type, title, desc]
        refs = [[c["id"], c["type"], c["title"], c["desc"]] for c in catalog]

        # Events list (verified events from SQLite ssot_events)
        cursor.execute("SELECT * FROM ssot_events WHERE project_id = ? ORDER BY id ASC", (project_id,))
        event_rows = cursor.fetchall()
        events = []
        for ev in event_rows:
            events.append({
                "id": ev["id"],
                "runtime": ev["runtime_id"] or (ev["role_name"] + " / " + (ev["runtime_id"] or "")),
                "role": ev["role_name"],
                "request": ev["request"],
                "evidence": ev["evidence"],
                "status": ev["status"],
                "time": ev["verified_time"]
            })

        # Kanban (Derived from todos)
        cursor.execute("SELECT * FROM todos WHERE project_id = ?", (project_id,))
        all_todos = cursor.fetchall()
        kanban = {
            "backlog": [],
            "ready": [],
            "progress": [],
            "done": []
        }
        for t in all_todos:
            item = [t["id"], t["title"], (t["assigned_role"] or "").split()[0] if (t["assigned_role"] or "").strip() else "", t["viec_ref"] or ""]
            st = t["status"]
            if st == "done":
                kanban["done"].append(item)
            elif st in ("live", "in_progress", "running"):
                kanban["progress"].append(item)
            elif st in ("ready", "wait"):
                kanban["ready"].append(item)
            else:
                kanban["backlog"].append(item)

        return {
            "project": project,
            "sourceText": p_row["source_text"],
            "projects": [
                {
                    "name": p_row["name"],
                    "meta": p_row["meta"],
                    "repo": p_row["repo_path"]
                }
            ],
            "roadmap": roadmaps,
            "todos": todos,
            "roles": roles,
            "nodes": nodes,
            "runtimes": runtimes,
            "kanban": kanban,
            "ssot": ssot,
            "roleMemory": role_memory,
            "catalog": catalog,
            "refs": refs,
            "events": events
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

def classify_agy_result(returncode, output):
    """Phân loại kết quả 1 lần gọi agy: ('ok', ''), ('rate_limited', reset) khi 429/RESOURCE_EXHAUSTED, ('error', '') còn lại."""
    out = output or ""
    if returncode == 0:
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
    status, reset_at = classify_agy_result(res.returncode, output)
    record_quota_probe(profile_id, model, status, reset_at, output)
    return status, reset_at

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
# REAL TMUX SWARM ENGINE (6 INTERACTIVE PROCESSES & SHARED CONTEXT)
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
        "allowed_paths": ["backend/**", "data/**", "migrations/**"],
        "blocked_paths": ["frontend/**", "Dockerfile", "docker-compose.yml"],
        "current_task_id": "",
        "scope": "Python daemon, SQLite WAL, FTS5 catalog và runner",
        "mission": "Thực thi API control plane, tối ưu truy vấn FTS5 catalog sub-ms, quản trị SQLite WAL và lock task."
    },
    {
        "id": "gw-frontend-agy",
        "role_name": "Frontend Specialist",
        "cli_tool": "Gemini CLI (agy)",
        "account_type": "profile2",
        "conv_id": "conv-frontend-ui",
        "gemini_model": "Gemini 3.7 Flash (High)",
        "anthropic_model": "Claude Sonnet 4.6 (Thinking)",
        "allowed_paths": ["frontend/**", "assets/**"],
        "blocked_paths": ["backend/**", "data/**", "Dockerfile"],
        "current_task_id": "",
        "scope": "Web console UI, CSS Gen-workplace v1.1, real-time sync",
        "mission": "Duy trì giao diện console (frontend/index.html), bố cục 2 cột List+Detail & Tabs, bind dữ liệu thật từ backend."
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
    },
    {
        "id": "gw-security-agy",
        "role_name": "Security Auditor",
        "cli_tool": "Codex Security CLI / agy",
        "account_type": "owner_default",
        "conv_id": "conv-security-audit",
        "gemini_model": "Gemini 3.1 Pro (High)",
        "anthropic_model": "Claude Opus 4.6 (Thinking)",
        "allowed_paths": ["vault/**", "security_audits/**", ".env.example"],
        "blocked_paths": ["backend/**", "frontend/**"],
        "current_task_id": "",
        "scope": "Phân quyền volume Docker, audit file permission, kiểm soát Vault",
        "mission": "Kiểm tra an toàn SELinux, cô lập quyền hạn biến môi trường và thẩm định secret boundary RFC 7636 PKCE."
    }
]

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
- **Execution Policy**: Autonomous Execution & Full Bypass Policy (auto-accept, không dừng bước trung gian)

---

### 🛡️ Ranh Giới Thư Mục Cứng (Zero-Conflict Directory Boundary)
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

def ensure_real_tmux_sessions(project_id="PRJ-GEN-WORKPLACE"):
    """
    Đảm bảo 6 phiên tmux thật sự đang chạy nền trên host (app chạy bằng python3 backend/main.py, không dùng Docker).
    Mỗi phiên là 1 tiến trình bash tương tác độc lập, được inject sẵn SSOT context,
    conversation ID continuity, profile xác thực và alias gọi agy CLI trực tiếp.
    """
    project_id = normalize_project_id(project_id)
    live_sessions = set()
    try:
        res = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0:
            live_sessions = {line.strip() for line in res.stdout.strip().splitlines() if line.strip()}
    except Exception:
        pass

    oauth_map = {p["id"]: p for p in get_oauth_profiles()}

    # Đảm bảo symlink docs trong /workspace để agent luôn đọc được SSOT spec
    try:
        if os.path.exists("/workspace") and not os.path.exists("/workspace/docs") and os.path.exists("/app/docs"):
            os.symlink("/app/docs", "/workspace/docs")
    except Exception:
        pass

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tmux_sessions WHERE project_id = ? ORDER BY id ASC", (project_id,))
        db_sessions = cursor.fetchall()

        for s in db_sessions:
            sid = s["id"]
            sess_status = s["status"]
            # Nếu phiên đang ngủ đông (hibernated) để giải phóng RAM & CPU, không tự động bật lại
            if sess_status == "hibernated":
                continue

            role_name = s["role_name"]
            acc_type = s["account_type"]
            profile_dir = s["profile_dir"]
            conv_id = s["conversation_id"] if "conversation_id" in s.keys() and s["conversation_id"] else f"conv-{sid}"

            p_info = oauth_map.get(acc_type, {})
            email = p_info.get("email") or "Chưa đăng nhập"
            is_auth = bool(p_info.get("is_auth"))
            if not profile_dir and p_info.get("path"):
                profile_dir = p_info["path"]

            quota_g, quota_a = get_quota_telemetry(acc_type, email)

            # Tạo role bootstrap spec
            role_spec_file = generate_role_spec_file(sid, role_name, conv_id=conv_id)

            # Nếu phiên tmux chưa chạy thật sự -> Khởi tạo phiên tmux bash thật!
            if sid not in live_sessions:
                init_script_path = f"/tmp/tmux_init_{sid}.sh"
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

            # Đọc Live Pane Output & Live PID
            live_out = ""
            pane_pid = 0
            try:
                subprocess.run(["tmux", "resize-window", "-t", sid, "-x", "200", "-y", "40"], capture_output=True, timeout=1.0)
                c_res = subprocess.run(["tmux", "capture-pane", "-t", sid, "-p", "-S", "-100"], capture_output=True, text=True, timeout=1.5)
                if c_res.returncode == 0 and c_res.stdout.strip():
                    raw_lines = c_res.stdout.splitlines()
                    while raw_lines and not raw_lines[-1].strip():
                        raw_lines.pop()
                    if raw_lines:
                        live_out = "\n".join(raw_lines)
                p_res = subprocess.run(["tmux", "list-panes", "-t", sid, "-F", "#{pane_pid}"], capture_output=True, text=True, timeout=1.0)
                if p_res.returncode == 0 and p_res.stdout.strip().isdigit():
                    pane_pid = int(p_res.stdout.strip().splitlines()[0])
            except Exception:
                pass

            # Cập nhật trạng thái vào SQLite
            cursor.execute("""
            UPDATE tmux_sessions
            SET pid = CASE WHEN ? > 0 THEN ? ELSE pid END,
                terminal_output = CASE WHEN ? != '' THEN ? ELSE terminal_output END,
                conversation_id = ?,
                quota_gemini_json = ?,
                quota_anthropic_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (pane_pid, pane_pid, live_out, live_out, conv_id, json.dumps(quota_g, ensure_ascii=False), json.dumps(quota_a, ensure_ascii=False), sid))

        conn.commit()

def seed_tmux_sessions(project_id="PRJ-GEN-WORKPLACE"):
    """Điền và đồng bộ cấu hình 6 phiên Swarm Runtimes trong SQLite Core DB."""
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, '/workspace', '', ?, ?, ?, ?)
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

    # Kích hoạt tạo phiên thật sự
    ensure_real_tmux_sessions(project_id)

def get_tmux_sessions(project_id="PRJ-GEN-WORKPLACE"):
    """Lấy danh sách các phiên Tmux với đầy đủ thông tin Account, Quota và Output thời gian thực."""
    project_id = normalize_project_id(project_id)
    ensure_real_tmux_sessions(project_id)
    oauth_map = {p["id"]: p for p in get_oauth_profiles()}

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tmux_sessions WHERE project_id = ? ORDER BY id ASC", (project_id,))
        rows = cursor.fetchall()
        results = []

        for r in rows:
            acc_type = r["account_type"]
            p_info = oauth_map.get(acc_type, {})
            email = p_info.get("email") or None
            is_auth = bool(p_info.get("is_auth"))
            
            # Luôn tính toán Quota thực tế thời gian thực
            quota_g, quota_a = get_quota_telemetry(acc_type, email or "")

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
            if task_id:
                cursor.execute("SELECT title, status, roadmap_id, evidence_ref, viec_ref FROM todos WHERE id = ? LIMIT 1", (task_id,))
                t_row = cursor.fetchone()
                if not t_row:
                    cursor.execute("SELECT title, status, '' AS roadmap_id, evidence_ref, viec_ref FROM gen_session_todos WHERE id = ? LIMIT 1", (task_id,))
                    t_row = cursor.fetchone()
                if t_row:
                    task_title = t_row["title"]
                    task_status = t_row["status"]
                    task_roadmap = t_row["roadmap_id"] or ""
                    evidence_ref = t_row["evidence_ref"] or ""
                    task_viec_ref = t_row["viec_ref"] or ""
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
                "pid": r["pid"],
                "cwd": r["cwd"],
                "terminal_output": r["terminal_output"],
                "conversation_id": r["conversation_id"] if "conversation_id" in r.keys() else f"conv-{r['id']}",
                "email": email,
                "is_auth": is_auth,
                "account_name": p_info.get("name"),
                "quota_gemini": quota_g,
                "quota_anthropic": quota_a,
                "allowed_paths": allowed_p,
                "blocked_paths": blocked_p,
                "current_task_id": task_id,
                "task_title": task_title,
                "task_status": task_status,
                "task_roadmap": task_roadmap,
                "task_viec_ref": task_viec_ref,
                "evidence_ref": evidence_ref,
                "attach_cmd": f"tmux attach -t {r['id']}",
                "updated_at": r["updated_at"]
            })

        return results

def update_tmux_account(session_id, account_type, account_label="", profile_dir=""):
    """Đổi tài khoản OAuth cho phiên Tmux và cập nhật môi trường runtime ngay trong tmux. Nhãn để trống sẽ được tính từ email thật; trả về nhãn đã dùng."""
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
    pid = 0
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pid FROM tmux_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        pid = row["pid"] if row else 0

    if pid <= 0:
        try:
            res = subprocess.run(["tmux", "list-panes", "-t", session_id, "-F", "#{pane_pid}"], capture_output=True, text=True, timeout=1.0)
            if res.returncode == 0 and res.stdout.strip().isdigit():
                pid = int(res.stdout.strip().splitlines()[0])
        except Exception:
            pass

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
    pid = 0
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pid FROM tmux_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        pid = row["pid"] if row else 0

    if pid <= 0:
        try:
            res = subprocess.run(["tmux", "list-panes", "-t", session_id, "-F", "#{pane_pid}"], capture_output=True, text=True, timeout=1.0)
            if res.returncode == 0 and res.stdout.strip().isdigit():
                pid = int(res.stdout.strip().splitlines()[0])
        except Exception:
            pass

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
        acc_type = s["account_type"]
        profile_dir = s["profile_dir"]
        conv_id = s["conversation_id"] or f"conv-{session_id}"
        prev_output = s["terminal_output"] or ""

        p_info = oauth_map.get(acc_type, {})
        email = p_info.get("email") or "Chưa đăng nhập"
        p_dir_clean = profile_dir or p_info.get("path") or os.path.join(HOME_DIR, ".gemini")
        role_spec_file = generate_role_spec_file(session_id, role_name, conv_id=conv_id)

        init_script_path = f"/tmp/tmux_init_{session_id}.sh"
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
        SET status = 'active', pid = ?, terminal_output = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (pane_pid, live_out or prev_output, session_id))
        conn.commit()

    return {"status": "active", "session_id": session_id, "pid": pane_pid, "conversation_id": conv_id}

def manage_tmux_swarm_lifecycle(action, target_id="all", project_id="PRJ-GEN-WORKPLACE"):
    """Quản trị vòng đời hàng loạt cho các phiên Swarm."""
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM tmux_sessions WHERE project_id = ?", (project_id,))
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

SWARM_SESSION_IDS = ["gw-lead-agy", "gw-backend-agy", "gw-frontend-agy", "gw-devops-agy", "gw-qa-agy", "gw-security-agy"]
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
    "Chạy Task này" (session_id) / "Chạy Toàn Bộ Swarm" (session_id=None → 6 worker):
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
            p_dir = os.path.expanduser(row["profile_dir"]) if row["profile_dir"] else _profile_dir(row["account_type"] or "owner_default")
            cmd, report_path = build_task_directive(sid, task_id, t["title"], p_dir)
            results[sid] = {"status": "pending", "task_id": task_id, "task_title": t["title"], "viec_ref": t["viec_ref"] or "", "command": cmd, "report_path": report_path}

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
                r["dispatch_id"] = did
                start_tmux_dispatch_watcher(did)
            except Exception as e:
                print(f"[dispatch] Không ghi được dispatch_log cho {sid}: {e}")
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
        try:
            res = subprocess.run(["git", "-C", str(BASE_DIR), "cat-file", "-e", f"{ev}^{{commit}}"], capture_output=True, timeout=5.0)
            if res.returncode == 0:
                return True, "git:commit", f"Commit {ev} tồn tại trong repo"
        except Exception:
            pass
        return False, "", f"Commit {ev} không tồn tại trong repo {BASE_DIR}"
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

def _log_evidence_override(conn, table, todo_id, project_id, session_id, old_ev, old_by, new_ev, new_by, reason):
    conn.execute("""
    INSERT INTO task_evidence_audit (task_id, table_name, project_id, session_id, old_evidence_ref, old_verified_by,
                                     new_evidence_ref, new_verified_by, reason)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (todo_id, table, project_id, session_id or "", old_ev or "", old_by or "", new_ev, new_by, reason or ""))
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

def complete_task(session_id, todo_id, evidence_ref, verified_by="Lead Architect", project_id="PRJ-GEN-WORKPLACE", force=False, reason=""):
    """
    Nghiệm thu hoàn tất có bằng chứng kiểm được (Evidence-Backed Completion, #4, #16):
    - evidence_ref phải qua verify_evidence_ref (commit SHA / file không rỗng trong ~/gw-reports·repo·worktree /
      dispatch:<id> done khớp task / warroom:<id> trả lời của agent / URL PR GitHub có thật).
    - Không đạt → trả {"error": ...} và KHÔNG đổi trạng thái.
    - Task đã done → {"error", "code": "already_done"} và KHÔNG ghi đè; chỉ ghi đè khi force=True,
      mỗi lần ghi đè được lưu vào task_evidence_audit (bằng chứng cũ → mới, ai, lý do) và in log.
    - verified_by được tính: 'git:commit' / 'file' / 'github:pr' / 'dispatch' / 'warroom' (tham số verified_by chỉ giữ để tương thích API).
    - Tự động nhả khóa session để sẵn sàng nhận nhiệm vụ tiếp theo.
    """
    project_id = normalize_project_id(project_id)
    todo_id = (todo_id or "").strip()
    with get_connection() as conn:
        table, row = None, None
        for tbl, by_col in (("todos", "verified_by"), ("gen_session_todos", "'' AS verified_by")):
            row = conn.execute(f"SELECT status, evidence_ref, {by_col} FROM {tbl} WHERE id = ? AND project_id = ?",
                               (todo_id, project_id)).fetchone()
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

    ok, verified_by, msg = verify_evidence_ref(evidence_ref, task_id=todo_id, project_id=project_id)
    if not ok:
        return {"error": msg, "task_id": todo_id}
    evidence_ref = evidence_ref.strip()

    with get_connection() as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            done_guard = "" if force else " AND status != 'done'"
            by_col = "verified_by" if table == "todos" else "'' AS verified_by"
            old = conn.execute(f"SELECT status, evidence_ref, {by_col} FROM {table} WHERE id = ? AND project_id = ?",
                               (todo_id, project_id)).fetchone()
            if table == "todos":
                cur = conn.execute(f"""
                UPDATE todos
                SET status = 'done', evidence_ref = ?, verified_by = ?, assigned_session_id = ''
                WHERE id = ? AND project_id = ?{done_guard}
                """, (evidence_ref, verified_by, todo_id, project_id))
            else:
                cur = conn.execute(f"""
                UPDATE gen_session_todos
                SET status = 'done', evidence_ref = ?, claimed_by = '', locked_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND project_id = ?{done_guard}
                """, (evidence_ref, todo_id, project_id))
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return {"error": f"Task {todo_id} vừa được nghiệm thu bởi lượt gọi khác — không ghi đè", "code": "already_done", "task_id": todo_id}
            overridden = bool(old and old["status"] == "done")
            if overridden:
                _log_evidence_override(conn, table, todo_id, project_id, session_id, old["evidence_ref"], old["verified_by"],
                                       evidence_ref, verified_by, reason)
            conn.execute("UPDATE tmux_sessions SET current_task_id = '', last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    viec_ref = get_task_viec_ref(todo_id, project_id)
    webhook_sent = send_event_webhook("task_completed", project_id=project_id, viec_ref=viec_ref, task_id=todo_id,
                                      session_id=session_id, exit_code=None, report_path="", evidence_ref=evidence_ref, verified_by=verified_by)
    res = {"status": "completed", "task_id": todo_id, "viec_ref": viec_ref, "evidence_ref": evidence_ref, "verified_by": verified_by,
           "verify_message": msg, "webhook_sent": webhook_sent}
    if overridden:
        res["overridden"] = {"old_evidence_ref": old["evidence_ref"] or "", "old_verified_by": old["verified_by"] or "", "reason": reason or ""}
    return res

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

def get_dispatch_log(limit=50):
    """Nhật ký dispatch_log (mới nhất trước) cho GET /api/dispatch/log."""
    limit = max(1, min(int(limit or 50), 500))
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM dispatch_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Chờ kết quả worker phía server (Issue #9): wait_worker_result
# ---------------------------------------------------------------------------
# Chỗ ghi kết quả dispatch (dispatch_warroom_to_agent, watcher tmux) gọi _notify_dispatch_change();
# wait_worker_result chờ trên Condition này, kèm poll DB mỗi giây (tiến trình MCP stdio riêng không nhận được notify).
_DISPATCH_COND = threading.Condition()
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
    denied = agy_output_denied(output) if exit_code == 0 else ""
    _finish_tmux_dispatch(row, exit_code, output, fail_reason=f"agy bị từ chối quyền / không ra kết quả ({denied})" if denied else "")
    return "done" if exit_code == 0 and not denied else "failed"

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
    if time.time() - started < WARROOM_DISPATCH_TIMEOUT_SEC + 120:
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
    "frontend": "gw-frontend-agy",
    "devops": "gw-devops-agy",
    "qa": "gw-qa-agy",
    "security": "gw-security-agy",
    "lead": "gw-lead-agy",
}
WARROOM_MENTION_RE = re.compile(r"@(backend|frontend|devops|qa|security|lead)\b", re.IGNORECASE)
WARROOM_DISPATCH_TIMEOUT_SEC = 15 * 60

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
# Lệnh chỉ đọc mà agy --mode plan được chạy không cần hỏi (permissions.allow của agy, khớp theo tiền tố lệnh)
AGY_PLAN_READONLY_COMMANDS = ["ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "tree",
                              "git status", "git log", "git diff", "git show", "git branch", "git ls-files", "git grep", "git rev-parse"]
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

def ensure_agy_plan_permissions(p_dir, cwd):
    """
    Thêm quy tắc chỉ đọc vào permissions.allow của hồ sơ agy (<p_dir>/antigravity-cli/settings.json) để agy -p --mode plan
    đọc được file và chạy lệnh chỉ đọc trong worktree của vai thay vì bị auto-denied:
      read_file(<cwd>) + command(ls|cat|grep|git status|git log|git diff|...).
    Giữ nguyên mọi khóa khác; file hỏng / permissions sai kiểu → không đụng. Tắt bằng GW_AGY_PLAN_ALLOW=0. Trả danh sách quy tắc vừa thêm.
    """
    if (os.environ.get("GW_AGY_PLAN_ALLOW", "1") or "").strip().lower() in ("0", "false", "no", "off"):
        return []
    cli_dir = os.path.join(p_dir or "", "antigravity-cli")
    # Chỉ ghi vào hồ sơ agy đã có thư mục antigravity-cli: tạo mới sẽ làm _agy_env đổi ANTIGRAVITY_APP_DATA_DIR của hồ sơ
    if not p_dir or not os.path.isdir(cli_dir) or not cwd:
        return []
    rules = [f"read_file({os.path.realpath(cwd)})"] + [f"command({c})" for c in AGY_PLAN_READONLY_COMMANDS]
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
            if not isinstance(allow, list):
                return []
            added = [r for r in rules if r not in allow]
            if not added:
                return []
            allow.extend(added)
            tmp = f"{path}.gw-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            return added
        except Exception as e:
            print(f"[agy-perm] Không cập nhật được {path}: {e}")
            return []

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
    prompt = (f"{message}\n\n(Bạn là {session_id}, đang ở worktree của repo gen-workplace. "
              "Hãy tự đọc file cần thiết rồi trả lời ĐẦY ĐỦ ngay trong một lượt bằng tiếng Việt; "
              "không hỏi lại, không chỉ nêu kế hoạch.)")
    # agy -p không tương tác: tool cần quyền bị auto-denied. Cấp quy tắc chỉ đọc (read_file(worktree), command(ls|grep|git log|...))
    # trong settings của hồ sơ, KHÔNG bật skip-permissions cho mọi vai. Lối thoát cuối (opt-in GW_WARROOM_SKIP_PERMISSIONS=1):
    # thêm --dangerously-skip-permissions nhưng chỉ khi cwd đúng là worktree riêng của vai (vẫn --mode plan).
    in_worktree = is_role_worktree(session_id, cwd)
    if in_worktree:
        ensure_agy_plan_permissions(p_dir, cwd)
    cmd = [_agy_bin(), f"--gemini_dir={p_dir}"]
    if in_worktree and _env_on("GW_WARROOM_SKIP_PERMISSIONS"):
        cmd.append(AGY_SKIP_PERMISSIONS_FLAG)
    cmd += ["--mode", "plan", "-p", prompt]
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_connection() as conn:
            conn.execute("UPDATE dispatch_log SET command = ? WHERE id = ?", (" ".join(cmd), dispatch_id))
            conn.commit()
    except Exception:
        pass
    t0 = time.time()
    exit_code = -1
    output = ""
    fail_reason = ""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=_agy_env(p_dir))
        exit_code = res.returncode
        output = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()
        status, reset_at = record_quota_probe_from_result(account_type, "default", res)
        denied = agy_output_denied(output) if exit_code == 0 else ""
        if status == "rate_limited":
            body = f"Lỗi 429 / hết quota khi gọi agy (hồi {reset_at or 'chưa rõ'}):\n{output[-1500:]}"
        elif denied:
            fail_reason = f"agy bị từ chối quyền / không ra kết quả ({denied})"
            body = f"{fail_reason}:\n{output[-3000:]}"
        elif exit_code != 0:
            body = f"agy thoát lỗi:\n{output[-3000:] or '(không có output)'}"
        else:
            body = output[:4000] if output else "(agy không trả output)"
    except subprocess.TimeoutExpired:
        output = body = f"Lỗi: agy không phản hồi sau {timeout // 60} phút, đã hủy."
        record_quota_probe(account_type, "default", "timeout", "", output)
    except Exception as e:
        output = body = f"Lỗi khi chạy agy ({_agy_bin()}): {e}"
    body = f"{body}\nexit={exit_code}" + (" (failed: agy bị từ chối quyền, không có kết quả)" if fail_reason else "")
    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

    report_path = ""
    try:
        report_dir = os.path.join(HOME_DIR, "gw-reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"warroom-{session_id}-{time.strftime('%Y%m%d-%H%M%S')}-{dispatch_id}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# {session_id} · {started_at} → {finished_at}\n\n")
            f.write(f"- Kênh: {channel_id}\n- cwd: {cwd}\n- Lệnh: {' '.join(cmd)}\n- exit: {exit_code}\n\n## Tin nhắn\n\n{message}\n\n## Output\n\n{output}\n")
    except Exception as e:
        print(f"[dispatch] Không ghi được báo cáo: {e}")
        report_path = ""

    task_id = ""
    try:
        with get_connection() as conn:
            r = conn.execute("SELECT current_task_id FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone()
            task_id = (r["current_task_id"] or "") if r else ""
    except Exception:
        pass
    # exit=0 nhưng agy báo auto-denied / no output produced → vẫn là failed
    status = "done" if exit_code == 0 and not fail_reason else "failed"
    viec_ref = get_task_viec_ref(task_id, project_id)
    webhook_sent = send_event_webhook("dispatch_finished", project_id=project_id, viec_ref=viec_ref, task_id=task_id,
                                      session_id=session_id, exit_code=exit_code, report_path=report_path, status=status)
    summary = _shorten_output(f"[{fail_reason}]\n{output}" if fail_reason else output)

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
                   channel_id = ?, webhook_sent = ?, status = ?, summary = ?, reply_msg_id = ?
            WHERE id = ?
            """, (" ".join(cmd), exit_code, report_path, finished_at, task_id, viec_ref, channel_id, 1 if webhook_sent else 0,
                  status, summary, reply_msg_id, dispatch_id))
            conn.commit()
    finally:
        _notify_dispatch_change()
    return {"dispatch_id": dispatch_id, "session_id": session_id, "status": status, "exit_code": exit_code, "report_path": report_path, "cwd": cwd,
            "elapsed_sec": round(time.time() - t0, 1), "task_id": task_id, "viec_ref": viec_ref, "webhook_sent": webhook_sent,
            "reply_msg_id": reply_msg_id, "error": fail_reason}

def post_warroom_message(project_id="PRJ-GEN-WORKPLACE", channel_id="war_room", author="Ryan (Owner)", message="", tag="Directive", wait=False):
    """Lưu tin nhắn; tin có @backend|@frontend|@devops|@qa|@security|@lead → chạy agy thật của vai đó ở thread nền (wait=True chạy đồng bộ, dùng cho test). Không có @vai → chỉ lưu (#3)."""
    project_id = normalize_project_id(project_id)
    if not message or not message.strip():
        return {"error": "Message is empty"}

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
    for role in dict.fromkeys(m.lower() for m in WARROOM_MENTION_RE.findall(clean_msg)):
        sid = WARROOM_ROLE_SESSIONS[role]
        # Tạo dòng dispatch_log (running) TRƯỚC khi chạy để bên gọi có dispatch_id truyền cho wait_worker_result (#9)
        did = start_dispatch_log(sid, kind="warroom", channel_id=channel_id, task_id=_current_task_of(sid), request_msg_id=user_msg_id)
        if wait:
            dispatch_warroom_to_agent(project_id, channel_id, sid, clean_msg, dispatch_id=did, reply_to=user_msg_id)
        else:
            threading.Thread(target=dispatch_warroom_to_agent, args=(project_id, channel_id, sid, clean_msg),
                             kwargs={"dispatch_id": did, "reply_to": user_msg_id}, daemon=True, name=f"warroom-dispatch-{sid}").start()
        dispatched.append(sid)
        dispatches.append({"session_id": sid, "dispatch_id": did})

    return {
        "status": "sent",
        "channel_id": channel_id,
        "user_message": {"id": user_msg_id, "author": author, "body": clean_msg, "created_time": now_time, "created_at": now_iso, "tag": tag},
        "agent_reply": None,
        "dispatched": dispatched,
        "dispatches": dispatches,
        "note": (f"Đã chuyển tới {', '.join(dispatched)}; trả lời thật của agy sẽ xuất hiện trong kênh khi chạy xong (tối đa 15 phút). "
                 f"Chờ kết quả: wait_worker_result(dispatch_id=...) với dispatch_id trong 'dispatches'."
                 if dispatched else "Không có @vai nên chỉ lưu tin, không trả lời.")
    }

def generate_structure_from_ssot(content, project_id="PRJ-GEN-WORKPLACE"):
    """
    Lưu đặc tả từ Input chat tổng / File Plan làm nguồn cho các vai (chỉ lưu, không phân rã):
    - Ghi trích đoạn đặc tả vào instruction của mọi role trong agent_roles của dự án.
    - Cập nhật master_ssot (SSOT-ACTIVE-PLAN).
    KHÔNG sinh roadmap/todo, KHÔNG ghi tin War Room; response nói rõ generated = 0.
    """
    project_id = normalize_project_id(project_id)
    content = (content or "").strip()
    if not content:
        return {"error": "Thiếu nội dung đặc tả", "generated": {"roadmaps": 0, "todos": 0}}
    spec_summary = " ".join(content[:250].split())
    now_stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    role_instruction = f"Nguồn đặc tả SSOT (cập nhật {now_stamp}): {spec_summary}"

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. Ghi trích đoạn đặc tả vào instruction của các role thật trong dự án (không câu chữ mẫu)
        cursor.execute("UPDATE agent_roles SET instruction = ? WHERE project_id = ?", (role_instruction, project_id))
        roles_updated = cursor.rowcount

        # 2. Cập nhật Master SSOT
        now_time = time.strftime("%H:%M")
        cursor.execute("""
        INSERT OR REPLACE INTO master_ssot (id, project_id, title, body, source_ref, verified_time)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "SSOT-ACTIVE-PLAN", 
            project_id, 
            "Bản Kế Hoạch Đang Chấp Hành (Active SSOT)", 
            spec_summary, 
            "Input Chat Tổng & File Plan",
            now_time
        ))
        conn.commit()

    return {
        "status": "saved",
        "generated": {"roadmaps": 0, "todos": 0},
        "note": "Chỉ lưu đặc tả làm nguồn cho các vai; roadmap/todo chưa được sinh tự động",
        "roles_updated": roles_updated,
        "timestamp": now_stamp,
        "ssot_summary": spec_summary
    }

def get_default_model_for_role(role_name):
    rn = (role_name or "").lower()
    if "lead" in rn or "architect" in rn:
        return "Gemini 3.1 Pro (High)"
    if "backend" in rn or "db" in rn:
        return "Gemini 3.1 Pro (High)"
    if "frontend" in rn:
        return "Gemini 3.8 Flash (High)"
    if "devops" in rn or "docker" in rn:
        return "Gemini 3.8 Flash (Medium)"
    if "qa" in rn or "test" in rn:
        return "Gemini 3.7 Flash (High)"
    if "security" in rn or "audit" in rn:
        return "Claude Sonnet 4.6 (Thinking)"
    return "Gemini 3.1 Pro (High)"

def update_role_model(role_id, model_name, project_id="PRJ-GEN-WORKPLACE"):
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE agent_roles
        SET model_name = ?
        WHERE (id = ? OR name = ? OR role_key = ?) AND project_id = ?
        """, (model_name, role_id, role_id, role_id, project_id))
        conn.commit()
        return cursor.rowcount > 0

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

def update_todo_status(todo_id, new_status, project_id="PRJ-GEN-WORKPLACE"):
    project_id = normalize_project_id(project_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE todos SET status = ? WHERE id = ? AND project_id = ?", (new_status, todo_id, project_id))
        updated = cursor.rowcount > 0  # id không tồn tại → False (trước đây luôn True)

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
        return updated

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
    elif code.startswith("EXIT_"):
        reason = f"agy thoát mã {code[5:]}"
    else:
        reason = code
    if detail:
        reason += f" — {detail[-600:]}"
    return reason

def call_agy_cli_turn(conv_id, user_message, model=None, account="owner_default"):
    """
    Gọi agy CLI thật cho 1 lượt chat:
    - Kế thừa ngữ cảnh phiên (agy_conv_id), dùng profile OAuth của account, ghi quota_probe.
    - Trả (reply, conversation_id, usage). KHÔNG bao giờ bịa reply: lỗi → reply rỗng và usage["error"]
      là mã lỗi thật (RESOURCE_EXHAUSTED / TIMEOUT / AGY_NOT_FOUND / EXIT_<rc> / EMPTY_RESPONSE / EXCEPTION),
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
    cmd = [_agy_bin(), "--output-format", "json", "--print", prompt_payload, "--dangerously-skip-permissions", "--model", model_slug]
    if agy_conv_id:
        cmd.extend(["--conversation", agy_conv_id])

    # Thư mục làm việc: thư mục riêng của phiên nếu có, không thì thư mục repo
    work_dir = "/workspace" if os.path.isdir("/workspace") else str(BASE_DIR)
    sess_dir = Path("/workspace/sessions") / conv_id
    if not sess_dir.exists():
        sess_dir = BASE_DIR / "workspace" / "sessions" / conv_id
    if sess_dir.exists():
        work_dir = str(sess_dir)
        cmd.extend(["--add-dir", str(sess_dir)])

    def _save_conv(ret_id, tokens):
        if not ret_id:
            return
        try:
            with get_connection() as conn:
                conn.execute("UPDATE gen_conversations SET agy_conv_id = ?, total_tokens = coalesce(total_tokens, 0) + ? WHERE id = ?",
                             (ret_id, int(tokens or 0), conv_id))
                conn.commit()
        except Exception:
            pass

    def _run(argv):
        try:
            res = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=work_dir, timeout=AGY_CHAT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            record_quota_probe(account, model_slug, "timeout", "", f"agy không phản hồi sau {AGY_CHAT_TIMEOUT_SEC}s")
            return "", agy_conv_id, {"error": "TIMEOUT", "timeout_sec": AGY_CHAT_TIMEOUT_SEC}
        except FileNotFoundError as e:
            return "", agy_conv_id, {"error": "AGY_NOT_FOUND", "bin": _agy_bin(), "detail": str(e)}
        except Exception as e:
            return "", agy_conv_id, {"error": "EXCEPTION", "detail": str(e)}
        status, reset_at = record_quota_probe_from_result(account, model_slug, res)
        err_output = ((res.stderr or "") + "\n" + (res.stdout or "")).strip()
        if status == "rate_limited":
            return "", agy_conv_id, {"error": "RESOURCE_EXHAUSTED", "reset_at": reset_at, "detail": err_output[-800:]}
        if res.returncode != 0:
            print(f"[AGY Runner] returncode={res.returncode}, err: {err_output[:300]}")
            return "", agy_conv_id, {"error": f"EXIT_{res.returncode}", "detail": err_output[-800:]}
        reply, ret_id, usage = _parse_agy_json_turn(res.stdout)
        _save_conv(ret_id, usage.get("total_tokens", 0) if isinstance(usage, dict) else 0)
        if not reply:
            return "", ret_id or agy_conv_id, {"error": "EMPTY_RESPONSE", "detail": err_output[-800:]}
        return reply, ret_id or agy_conv_id, dict(usage)

    reply, ret_id, usage = _run(cmd)
    # Phiên agy cũ hỏng/hết hạn (không phải lỗi quota) → xóa agy_conv_id, thử lại 1 lần với lượt mới
    if not reply and agy_conv_id and usage.get("error", "").startswith("EXIT_"):
        print(f"[AGY Runner] agy_conv_id '{agy_conv_id}' lỗi, thử lại lượt mới không --conversation")
        try:
            with get_connection() as conn:
                conn.execute("UPDATE gen_conversations SET agy_conv_id = '' WHERE id = ?", (conv_id,))
                conn.commit()
        except Exception:
            pass
        fresh_cmd = [t for i, t in enumerate(cmd) if t != "--conversation" and not (i > 0 and cmd[i - 1] == "--conversation")]
        agy_conv_id = None
        reply, ret_id, usage = _run(fresh_cmd)
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
        return todos

def save_gen_session_todo(conv_id, todo_id=None, title="Nhiệm vụ mới", description="", status="todo", priority="high", assigned_agent="Gen Core", checklist=None, evidence_ref="", order_idx=0, owner_id="owner-ryan", viec_ref=""):
    """
    Tạo mới / cập nhật task Kanban của phiên. TẠO MỚI bắt buộc viec_ref khớp ^VIEC-[0-9]+$ (mã việc Kho Ryan),
    thiếu/sai → {"error": "Thiếu viec_ref (mã việc trong Kho Ryan, vd VIEC-12)"}. Cập nhật: viec_ref rỗng → giữ giá trị cũ.
    """
    checklist = checklist or []
    viec_ok, viec_ref = validate_viec_ref(viec_ref)
    with get_connection() as conn:
        cursor = conn.cursor()
        is_new = True
        if todo_id:
            cursor.execute("SELECT viec_ref FROM gen_session_todos WHERE id = ? AND conversation_id = ?", (todo_id, conv_id))
            existing = cursor.fetchone()
            if existing:
                is_new = False
                if not viec_ref:
                    viec_ref = existing["viec_ref"] or ""
                    viec_ok = True
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
    return {"status": "saved", "id": todo_id, "title": title, "viec_ref": viec_ref, "created": is_new}

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

def update_gen_session_todo_status(conv_id, todo_id, new_status, evidence_ref=None):
    valid_statuses = ["todo", "in_progress", "review", "done"]
    if new_status not in valid_statuses:
        new_status = "todo"
    with get_connection() as conn:
        cursor = conn.cursor()
        updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [new_status]
        if evidence_ref:
            updates.append("evidence_ref = ?")
            params.append(evidence_ref)
        params.extend([todo_id, conv_id])
        cursor.execute(f"UPDATE gen_session_todos SET {', '.join(updates)} WHERE id = ? AND conversation_id = ?", params)
        conn.commit()
    return {"status": "updated", "id": todo_id, "new_status": new_status}

def delete_gen_session_todo(conv_id, todo_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM gen_session_todos WHERE id = ? AND conversation_id = ?", (todo_id, conv_id))
        conn.commit()
    return {"status": "deleted", "id": todo_id}

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
    lines.append("     Hoặc ngắn gọn: [TASK_DONE: <ID_TASK>]")
    lines.append("==================================================")
    return "\n".join(lines)

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
            update_gen_session_todo_status(conv_id, tid, status, evidence_ref=evidence)
            updates_made.append(f"Task {tid} -> {status}")

    # Mẫu 2: [TASK_DONE: TSK-01]
    done_matches = re.findall(r'\[TASK_DONE:\s*([A-Za-z0-9_-]+)\]', agent_text, re.IGNORECASE)
    for tid in done_matches:
        tid = tid.strip()
        update_gen_session_todo_status(conv_id, tid, "done")
        updates_made.append(f"Task {tid} -> Hoàn thành (Done)")

    return updates_made

def get_mcp_auth_status():
    """Lấy trạng thái cấu hình xác thực MCP."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM mcp_auth_settings WHERE key = 'require_auth'")
        row = cursor.fetchone()
        req_auth = (row["value"] == "1") if row else False
        cursor.execute("SELECT count(*) FROM mcp_agent_tokens WHERE status = 'active'")
        active_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM mcp_agent_tokens")
        total_count = cursor.fetchone()[0]
        return {
            "require_auth": req_auth,
            "active_tokens": active_count,
            "total_tokens": total_count,
            "origin": "http://localhost:8888",
            "endpoint": "http://localhost:8888/mcp"
        }

def set_mcp_strict_auth(enabled: bool):
    """Bật / tắt chế độ bắt buộc xác thực MCP."""
    val = "1" if enabled else "0"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO mcp_auth_settings (key, value, updated_at) VALUES ('require_auth', ?, CURRENT_TIMESTAMP)", (val,))
        conn.commit()
    return get_mcp_auth_status()

def get_mcp_agent_tokens(owner_id="owner-ryan"):
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
            raw_tok = r["token"]
            masked = f"{raw_tok[:10]}...{raw_tok[-6:]}" if len(raw_tok) > 16 else raw_tok
            try:
                perms = json.loads(r["permissions_json"])
            except Exception:
                perms = ["all"]
            tokens.append({
                "id": r["id"],
                "name": r["name"],
                "token_masked": masked,
                "token_raw": raw_tok,
                "client": r["client"],
                "role": r["role"],
                "permissions": perms,
                "status": r["status"],
                "expires_at": r["expires_at"] or "Vĩnh viễn",
                "last_used_at": r["last_used_at"] or "Chưa sử dụng",
                "calls_count": r["calls_count"] or 0,
                "created_at": str(r["created_at"])[:16]
            })
        return {
            "tokens": tokens,
            "auth_status": get_mcp_auth_status()
        }

def create_mcp_agent_token(name, permissions=None, expires_days=90, client="Manual Token", role="agent", owner_id="owner-ryan"):
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

    endpoint = "http://localhost:8888/mcp"
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
        "token_masked": f"{token_val[:10]}...{token_val[-6:]}",
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
            domain_allowed = False
            domain_map = {
                "quota": ["get_live_quota", "list_google_accounts", "switch_google_account", "get_oauth_login_url"],
                "swarm": ["list_swarm_workers", "send_worker_directive", "manage_worker_lifecycle", "get_worker_terminal_output", "post_warroom_message", "get_warroom_messages", "wait_worker_result"],
                "kanban": ["list_kanban_tasks", "create_kanban_task", "claim_task", "complete_task", "update_task_checklist"],
                "chat": ["gen_chat", "list_conversations", "create_conversation", "log_session_message", "get_conversation_messages", "compact_conversation"],
                "files": ["list_notes", "save_note", "delete_note", "read_workspace_file", "create_workspace_file", "list_workspace_files", "get_system_status"]
            }
            for p in perms:
                if p in domain_map and tool_name in domain_map[p]:
                    domain_allowed = True
                    break
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



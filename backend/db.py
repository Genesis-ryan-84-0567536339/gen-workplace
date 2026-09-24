#!/usr/bin/env python3
"""
GENESIS Multi-Agent Swarm Orchestrator - SQLite WAL Database Engine
Quản trị lưu trữ bền vững (Persistent Storage), Full-Text Search (FTS5) Catalog,
và Single Source of Truth (SSOT) cho toàn bộ Swarm Runtimes.
"""

import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "gen-workplace.db"

def get_connection():
    """Tạo kết nối SQLite tối ưu với WAL mode và Foreign Keys."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        conn.commit()


def seed_real_project():
    """Điền dữ liệu thực tế 100% của repository gen-workplace vào SQLite Core DB."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM projects WHERE id = 'PRJ-GEN-WORKPLACE'")
        if cursor.fetchone()[0] > 0:
            return  # Đã có dữ liệu thật

        now_str = datetime.now().strftime("%H:%M:%S")

        # 1. Project thật
        cursor.execute("""
        INSERT INTO projects (id, name, repo_path, branch, plan_file, source_text, meta, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'PRJ-GEN-WORKPLACE',
            'gen-workplace',
            '/workspace/LinuxDataA/gen-workplace',
            'main',
            'docs/SSOT_ORIGINAL_SPEC.md',
            """# ĐẶC TẢ DỰ ÁN GEN-WORKPLACE (MULTI-AGENT SWARM ORCHESTRATOR)
1. Bảng Console điều phối đa Agent Swarm quản lý nhiều dự án.
2. Khu Implementation: Hàng 01 chia 3 cột (Input+Plan -> Roadmap -> Todo). Hàng 02 chọn CLI Engine cho từng Role (Gemini CLI agy, Claude Code CLI, Cursor CLI, Codex Security CLI) với Instruction khóa theo Input tổng. Hàng 03 Sơ đồ live workflow từng node có checklist In-Output.
3. Khu Data Center: Git Repo (tuân thủ quy tắc Genesis Brain: Branch riêng + PR), Database, Vault, File Manager.
4. Khu Môi Trường Agent: Runtimes tự ghi tư duy (Thinking log), Chatroom thread @mention bàn giao task kèm file, phản ứng emoji thực tế (📥, ⚠️, 👎, ✅, ❓, 🔥), Live Kanban cả team do Trưởng nhóm phụ trách cập nhật live times.
5. Khu Nơi Làm Việc (Workplace): 2 cột Memory tổng Master SSOT vs Memory từng Role sync realtime + Catalog tra nhanh ID (#EVT, #SEC, #MCP, #FILE, #TOOL) để agent lấy dữ kiện tức thì không cần audit toàn cục mỗi phiên.
6. Đóng gói phân phối All-In-One: Cài đặt 1 lệnh trên giao diện TUI có thanh loading %, tự động kiểm tra môi trường (OS, Git, Docker, Compose), chạy 100% trong Docker container đa nền tảng, tự tạo Desktop Icon để nhấp đúp gọi WebApp ngay lập tức.""",
            '6 role · Docker live mount · Active',
            'active'
        ))

        # 2. Roadmaps thật
        roadmaps = [
            ('RM-01', 'Core Architecture & SSOT Spec', 'Thiết lập repo local, lưu nguyên văn đặc tả gốc của Owner vào docs/SSOT_ORIGINAL_SPEC.md.', 3, 'done', 1),
            ('RM-02', 'Docker Engine & Live Mount', 'Container hóa toàn bộ hệ thống với Dockerfile, docker-compose.yml và live bind mount :z.', 2, 'done', 2),
            ('RM-03', 'One-Command TUI Installer', 'Kịch bản install.sh + installer_tui.py với progress bar %, kiểm tra Docker/OS/Git, tự tạo Desktop Icon.', 3, 'done', 3),
            ('RM-04', 'SQLite Database & CLI Connectors', 'Tích hợp SQLite Core DB, FTS5 catalog lookup, process runner kết nối agy & claude CLI thực tế.', 3, 'live', 4),
            ('RM-05', 'GitHub Publication & Release', 'Gắn tag v1.0, publish repo lên GitHub cho cộng đồng, hỗ trợ 1-command curl install.', 2, 'queued', 5)
        ]
        for r in roadmaps:
            cursor.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?)", r)

        # 3. Todos thật
        todos = [
            ('TODO-01', 'RM-01', 'Khởi tạo Git repo /workspace/LinuxDataA/gen-workplace', 'Lead Architect', 'done'),
            ('TODO-02', 'RM-01', 'Lưu đặc tả gốc vào docs/SSOT_ORIGINAL_SPEC.md', 'Lead Architect', 'done'),
            ('TODO-03', 'RM-01', 'Chuyển đổi giao diện sang phong cách Gen-workplace v1.1', 'Frontend Specialist', 'done'),
            ('TODO-04', 'RM-02', 'Viết Dockerfile container hóa Python backend + WebApp', 'DevOps Engineer', 'done'),
            ('TODO-05', 'RM-02', 'Cấu hình docker-compose live mount với cờ SELinux :z', 'DevOps Engineer', 'done'),
            ('TODO-06', 'RM-03', 'Xây dựng installer_tui.py với thanh loading % đồ họa', 'Backend Specialist', 'done'),
            ('TODO-07', 'RM-03', 'Kịch bản install.sh tự động kiểm tra Git & Docker daemon', 'DevOps Engineer', 'done'),
            ('TODO-08', 'RM-03', 'Tạo shortcut Desktop Gen-workplace.desktop tự động', 'DevOps Engineer', 'done'),
            ('TODO-09', 'RM-04', 'Khởi tạo SQLite WAL DB & FTS5 virtual table', 'Backend Specialist', 'done'),
            ('TODO-10', 'RM-04', 'Kết nối API /api/state và /api/catalog với SQLite', 'Backend Specialist', 'live'),
            ('TODO-11', 'RM-04', 'Tích hợp Process Runner gọi agy CLI thời gian thực', 'Lead Architect', 'queued'),
            ('TODO-12', 'RM-05', 'Kiểm thử cross-platform trên macOS và Windows WSL2', 'QA Tester', 'queued'),
            ('TODO-13', 'RM-05', 'Publish repository lên GitHub và gắn release v1.0', 'Lead Architect', 'queued')
        ]
        for t in todos:
            cursor.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES (?, ?, 'PRJ-GEN-WORKPLACE', ?, ?, ?)", t)

        # 4. Roles thật
        roles = [
            ('ROLE-01', 'L', 'Lead Architect', 'Gemini CLI (agy --effort high)', 'Quản trị SSOT, điều phối toàn bộ tiến trình gen-workplace', 'Chịu trách nhiệm bảo toàn SSOT đặc tả gốc, thẩm định evidence từ các role và điều phối live workflow.'),
            ('ROLE-02', 'B', 'Backend & DB Specialist', 'Claude Code CLI', 'Python daemon, SQLite WAL, FTS5 catalog và runner', 'Thực thi API control plane, tối ưu truy vấn FTS5 catalog sub-ms và stream log terminal.'),
            ('ROLE-03', 'F', 'Frontend Specialist', 'Cursor CLI', 'Web console UI, CSS Gen-workplace v1.1, real-time sync', 'Duy trì phong cách thiết kế tối kỹ thuật v1.1, bind dữ liệu thật từ backend và tối ưu UX.'),
            ('ROLE-04', 'D', 'DevOps & Packaging', 'Gemini CLI (agy --agent devops)', 'Docker, SELinux bind mounts, TUI installer, desktop shortcut', 'Đảm bảo môi trường container chạy ổn định trên Linux, macOS và Windows, script 1-command installer hoàn hảo.'),
            ('ROLE-05', 'Q', 'QA Tester', 'Gemini CLI (agy)', 'Kiểm thử cross-platform, test API /api/status, xác thực installer', 'Chạy regression tests, nghiệm thu thanh loading % của installer và báo cáo phản hồi.'),
            ('ROLE-06', 'S', 'Security Auditor', 'Codex Security CLI', 'Phân quyền volume Docker, audit file permission, kiểm soát Vault', 'Kiểm tra an toàn SELinux, cô lập quyền hạn biến môi trường và thẩm định secret boundary.')
        ]
        for rl in roles:
            cursor.execute("INSERT INTO agent_roles (id, project_id, role_key, name, cli_tool, scope, instruction) VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?)", rl)

        # 5. Workflow Nodes thật
        nodes = [
            ('NODE-01', 0, 'Spec Ingestion', 'Lead Architect', 'done', 20, 42, 'Đặc tả gốc của Owner', 'docs/SSOT_ORIGINAL_SPEC.md', 'SSOT locked', '→ Docker Architecture', json.dumps(['Lập repo gen-workplace', 'Ghi nguyên văn SSOT', 'Cam kết zero-drift'], ensure_ascii=False)),
            ('NODE-02', 1, 'Container & Live Mount', 'DevOps Engineer', 'done', 260, 42, 'Dockerfile + docker-compose', 'gen-workplace-app (Live)', 'live bind mount verified', '→ TUI Installer', json.dumps(['Docker build 3.11-slim', 'Live mount cờ :z SELinux', 'Port 8888 200 OK'], ensure_ascii=False)),
            ('NODE-03', 2, 'TUI Installer & Icon', 'DevOps + Backend', 'done', 500, 42, 'install.sh + installer_tui.py', 'Gen-workplace.desktop', '1-command launch verified', '→ Database & Catalog', json.dumps(['Thanh loading % trực quan', 'Pre-flight checks OS/Docker', 'Tạo Desktop Icon WebApp'], ensure_ascii=False)),
            ('NODE-04', 3, 'Database & Catalog Engine', 'Backend & DB', 'live', 740, 42, 'SQLite FTS5 schema', 'Fast ID Catalog API', 'sub-ms query test', '→ Cross-Platform QA', json.dumps(['SQLite WAL mode', 'Bảng FTS5 Catalog', 'Zero-audit token saving'], ensure_ascii=False)),
            ('NODE-05', 4, 'Cross-Platform QA', 'QA Tester', 'pending', 980, 42, 'Installer test matrix', 'QA Sign-off report', 'zero install issue', '→ GitHub Release', json.dumps(['Test Linux / Fedora / Ubuntu', 'Test macOS Docker Desktop', 'Test Windows WSL2'], ensure_ascii=False)),
            ('NODE-06', 5, 'GitHub Release & Freeze', 'Lead Architect', 'pending', 500, 185, 'Clean git commits', 'GitHub Release v1.0', 'all issues resolved', '→ Community distribution', json.dumps(['Gắn tag v1.0.0', 'Push to GitHub main', '1-command curl ready'], ensure_ascii=False))
        ]
        for n in nodes:
            cursor.execute("""
            INSERT INTO workflow_nodes (id, project_id, step_index, title, role_name, status, coord_x, coord_y, input_desc, output_desc, check_desc, handoff_desc, checklist_json)
            VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, n)

        # 6. Runtimes thật
        runtimes = [
            ('runtime-01', 'Lead Architect', 'Gemini CLI (agy)', 'RM-04', 'main', 'running', '/workspace/LinuxDataA/gen-workplace',
             json.dumps({'input': 'Owner Request', 'output': 'gen-workplace git repo', 'request': 'init real project', 'conclusion': 'live & running'}, ensure_ascii=False),
             json.dumps(['20:34 repo created', '20:35 SSOT spec written', '20:47 live mount verified'], ensure_ascii=False)),
            ('runtime-02', 'Backend & DB Specialist', 'Claude Code CLI', 'TODO-09', 'main', 'running', '/workspace/LinuxDataA/gen-workplace',
             json.dumps({'input': 'Database spec', 'output': 'SQLite FTS5 schema', 'request': 'build zero-audit catalog', 'conclusion': 'in progress'}, ensure_ascii=False),
             json.dumps(['20:35 installer_tui.py implemented', '20:56 designing SQLite FTS5 catalog'], ensure_ascii=False)),
            ('runtime-03', 'Frontend Specialist', 'Cursor CLI', 'TODO-03', 'main', 'running', '/workspace/LinuxDataA/gen-workplace',
             json.dumps({'input': 'Gen-workplace-v1.1-dynamic.html', 'output': 'frontend/index.html', 'request': 'apply technical dark theme', 'conclusion': 'done'}, ensure_ascii=False),
             json.dumps(['20:10 applied v1.1 palette', '20:57 wiped mock data, replaced with real repo data'], ensure_ascii=False)),
            ('runtime-04', 'DevOps & Packaging', 'Gemini CLI (agy)', 'TODO-05', 'main', 'done', '/workspace/LinuxDataA/gen-workplace',
             json.dumps({'input': 'docker-compose.yml', 'output': 'container gen-workplace-app', 'request': 'containerize app', 'conclusion': 'done'}, ensure_ascii=False),
             json.dumps(['20:35 Dockerfile created', '20:36 image built', '20:47 container live-mounted'], ensure_ascii=False)),
            ('runtime-05', 'QA Tester', 'Gemini CLI', 'TODO-08', 'main', 'done', '/workspace/LinuxDataA/gen-workplace',
             json.dumps({'input': 'Gen-workplace.desktop', 'output': 'QA pass', 'request': 'verify desktop icon', 'conclusion': 'done'}, ensure_ascii=False),
             json.dumps(['20:37 checked desktop entry', '20:37 curl status 200 OK'], ensure_ascii=False)),
            ('runtime-06', 'Security Auditor', 'Codex Security CLI', 'TODO-05', 'main', 'done', '/workspace/LinuxDataA/gen-workplace',
             json.dumps({'input': 'SELinux policy', 'output': 'secure volume mount', 'request': 'enforce security', 'conclusion': 'done'}, ensure_ascii=False),
             json.dumps(['20:46 audit getenforce', '20:47 confirmed zero permission leak'], ensure_ascii=False))
        ]
        for rt in runtimes:
            cursor.execute("""
            INSERT INTO agent_runtimes (id, project_id, role_name, cli_tool, task_ref, branch, status, path, io_json, trace_json)
            VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?, ?, ?, ?)
            """, rt)

        # 7. Chat messages thật
        messages = [
            ('runtime-01', 'Lead Architect', '20:34:26', 'Repo Init', 'Đã khởi tạo repo <code>/workspace/LinuxDataA/gen-workplace</code> và lưu đặc tả gốc vào <code>docs/SSOT_ORIGINAL_SPEC.md</code>.', json.dumps(['📥 đã nhận', '✅ done'], ensure_ascii=False)),
            ('runtime-01', 'Lead Architect', '20:47:08', 'Live Mount', 'Đã cấu hình Live Mount với cờ SELinux <code>:z</code>. Mọi chỉnh sửa trên host sẽ tự động phản ánh tức thì vào container!', json.dumps(['🚀 live reload', '✅ xác nhận'], ensure_ascii=False)),
            ('runtime-02', 'Backend & DB Specialist', '20:35:45', 'Installer', 'Đã xây dựng xong bộ cài đặt <code>installer_tui.py</code> có thanh loading progress bar % và kiểm tra môi trường.', json.dumps(['✅ done', '🔥 mượt mà'], ensure_ascii=False)),
            ('runtime-02', 'Backend & DB Specialist', '20:56:00', 'Database', 'Đang triển khai SQLite WAL Core Database và FTS5 Virtual Table cho Catalog tra nhanh ID.', json.dumps(['⏳ đang chạy'], ensure_ascii=False)),
            ('runtime-03', 'Frontend Specialist', '20:10:23', 'Style v1.1', 'Đã chuyển đổi toàn bộ layout sang phong cách Gen-workplace v1.1 tinh tế, không rối mắt.', json.dumps(['✅ done'], ensure_ascii=False)),
            ('runtime-04', 'DevOps & Packaging', '20:36:58', 'Docker Up', 'Container <code>gen-workplace-app</code> đã khởi động thành công trên cổng 8888.', json.dumps(['✅ done', '🚀 online'], ensure_ascii=False)),
            ('runtime-05', 'QA Tester', '20:37:05', 'Shortcut', 'Đã xác thực shortcut <code>/workspace/Desktop/Gen-workplace.desktop</code> tồn tại và mở được WebApp.', json.dumps(['✅ verified'], ensure_ascii=False)),
            ('runtime-06', 'Security Auditor', '20:46:47', 'SELinux', 'Đã kiểm tra SELinux Enforcing trên host và gán nhãn <code>:z</code> an toàn cho Docker mounts.', json.dumps(['✅ secure'], ensure_ascii=False))
        ]
        for m in messages:
            cursor.execute("""
            INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json)
            VALUES ('PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?, ?)
            """, m)

        # 8. Master SSOT thật
        ssots = [
            ('SSOT-SPEC-01', 'Đặc Tả Gốc Bất Biến Của Owner', 'Bản đặc tả 5 phân khu (Dashboard, Implementation 3 hàng, Data Center, Runtimes, Workplace) và tiêu chuẩn cài đặt 1 lệnh TUI Docker là Single Source of Truth bất biến của toàn dự án.', 'EVT-03 · docs/SSOT_ORIGINAL_SPEC.md', '20:35'),
            ('SSOT-INSTALL-02', 'Tiêu Chuẩn Cài Đặt TUI & Desktop Icon', 'Kịch bản cài đặt phải có thanh đo tiến độ loading %, tự kiểm tra môi trường OS/Docker/Git và tự sinh shortcut desktop Gen-workplace.desktop khi kết thúc.', 'EVT-02 · installer_tui.py', '20:37'),
            ('SSOT-DOCKER-03', 'Môi Trường Container Hóa Đa Nền Tảng', 'Toàn bộ backend, frontend và data runtimes phải đóng gói khép kín trong Docker container để chạy đồng nhất trên Linux, macOS và Windows.', 'EVT-01 · docker-compose.yml', '20:47'),
            ('SSOT-LIVE-MOUNT-04', 'Cơ Chế Live Mount Hot-Reload', 'Thư mục frontend/ và backend/ được mount trực tiếp vào container với cờ SELinux :z, đảm bảo mọi thay đổi code trên repo được cập nhật tức thì trên WebApp khi F5.', 'COMMIT ae4e196', '20:47')
        ]
        for s in ssots:
            cursor.execute("""
            INSERT INTO master_ssot (id, project_id, title, body, source_ref, verified_time)
            VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?)
            """, s)

        # 9. Role Memories thật
        memories = [
            ('Lead Architect', 'Lập quy ước phân nhánh Git: Không commit trực tiếp lên main, mọi tính năng đều qua branch riêng và đối chiếu SSOT.', json.dumps(['git-policy', 'ssot'], ensure_ascii=False), 1, '20:34'),
            ('Backend & DB Specialist', 'Cấu hình SQLite với chế độ WAL (Write-Ahead Logging) và FTS5 để tối ưu hóa truy vấn catalog dưới 1ms cho agent.', json.dumps(['sqlite-wal', 'fts5'], ensure_ascii=False), 1, '20:56'),
            ('Frontend Specialist', 'Áp dụng bảng màu Nocturne Slate (--bg: #0d1014, --panel: #13171d, --line: #252c35) giúp dịu mắt và chuyên nghiệp.', json.dumps(['ui-v1.1', 'slate-palette'], ensure_ascii=False), 1, '20:10'),
            ('DevOps & Packaging', 'Ghi chú SELinux: Docker volume trên Fedora/RHEL bắt buộc có hậu tố :z để tự động gán nhãn container_file_t.', json.dumps(['docker', 'selinux'], ensure_ascii=False), 1, '20:46')
        ]
        for rm in memories:
            cursor.execute("""
            INSERT INTO role_memories (project_id, role_name, body, tags_json, synced_to_ssot, created_time)
            VALUES ('PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?)
            """, rm)

        # 10. Fast ID Lookup Catalog thật (Zero-audit tokens!)
        catalog = [
            ('#FILE-01', 'FILE', 'docs/SSOT_ORIGINAL_SPEC.md', 'Tài liệu SSOT đặc tả gốc bất biến của Owner toàn dự án', 'docs/SSOT_ORIGINAL_SPEC.md', json.dumps({'type': 'markdown', 'lines': 140})),
            ('#FILE-02', 'FILE', 'frontend/index.html', 'Giao diện Console hoàn chỉnh 100% phong cách Gen-workplace v1.1', 'frontend/index.html', json.dumps({'type': 'html', 'lines': 2755})),
            ('#FILE-03', 'FILE', 'backend/main.py', 'Control Plane Server & API endpoint (/api/status, /api/state)', 'backend/main.py', json.dumps({'type': 'python', 'port': 8888})),
            ('#FILE-04', 'FILE', 'installer_tui.py', 'Bộ cài đặt giao diện dòng lệnh TUI với progress bar % và dependency check', 'installer_tui.py', json.dumps({'type': 'tui'})),
            ('#FILE-05', 'FILE', 'docker-compose.yml', 'Cấu hình docker-compose live mount với cờ SELinux :z', 'docker-compose.yml', json.dumps({'services': ['gen-workplace']})),
            ('#FILE-06', 'FILE', '/workspace/Desktop/Gen-workplace.desktop', 'Shortcut Desktop mở WebApp bằng trình duyệt mặc định', '/workspace/Desktop/Gen-workplace.desktop', json.dumps({'type': 'desktop-entry'})),
            ('#SEC-01', 'SEC', 'Docker Container Sandbox Boundary', 'Cách ly toàn bộ mã thực thi của các CLI Agent trong Docker container', '/etc/docker/daemon.json', json.dumps({'isolation': 'docker'})),
            ('#SEC-02', 'SEC', 'Master Prompt Instruction Lock', 'Chỉ có thể chỉnh sửa hướng dẫn Agent Role thông qua ô Chat Input Tổng', 'frontend/index.html', json.dumps({'locked': True})),
            ('#MCP-01', 'MCP', 'google-drive MCP Server', 'Tích hợp đọc tài liệu, trang tính từ Google Drive', 'antigravity-cli/mcp/google-drive', json.dumps({'tools': 42})),
            ('#MCP-02', 'MCP', 'git-ops CLI Integration', 'Tự động kiểm soát commit, branch và pull request cho các subagent', 'builtin/git-ops', json.dumps({'cli': 'git'})),
            ('#TOOL-01', 'TOOL', 'Gemini CLI (agy)', 'Antigravity CLI v1.2.8 hỗ trợ chế độ autonomous và high-effort', '/workspace/.local/bin/agy', json.dumps({'version': '1.2.8'})),
            ('#TOOL-02', 'TOOL', 'Claude Code CLI', 'Engine tư duy và kiểm chứng mã nguồn độc lập', 'claude-code', json.dumps({'model': 'sonnet'})),
            ('#TOOL-03', 'TOOL', 'Codex Security CLI', 'Thẩm định an toàn, quyền truy cập tệp và phân tách bí mật', 'codex-cli', json.dumps({'sandbox': 'read-only'})),
            ('#DB-01', 'DB', 'SQLite 3 WAL Database', 'Cơ sở dữ liệu cốt lõi lưu trữ toàn bộ thực thể và FTS5 Catalog tra cứu', '/app/data/gen-workplace.db', json.dumps({'journal': 'WAL', 'fts': 'fts5'})),
            ('#EVT-01', 'EVT', 'Khởi tạo repo và commit đầu tiên 5f91e1e', 'Commit khởi tạo repo, Dockerfile và kịch bản TUI installer', 'git commit 5f91e1e', json.dumps({'author': 'Lead', 'time': '20:34'})),
            ('#EVT-02', 'EVT', 'Cấu hình Live Mount Hot-Reload commit ae4e196', 'Thêm cờ :z cho SELinux trên Fedora giúp phản ánh code tức thì', 'git commit ae4e196', json.dumps({'author': 'DevOps', 'time': '20:47'})),
            ('#EVT-03', 'EVT', 'Xóa sạch mock data, bind dữ liệu thật commit a4f63be', 'Thay thế toàn bộ mock data bằng dữ liệu thật của repository gen-workplace', 'git commit a4f63be', json.dumps({'author': 'Lead', 'time': '21:02'}))
        ]
        for c in catalog:
            cursor.execute("""
            INSERT INTO catalog_references (id, project_id, category, title, description, ref_path, metadata_json)
            VALUES (?, 'PRJ-GEN-WORKPLACE', ?, ?, ?, ?, ?)
            """, c)

        conn.commit()

def get_full_state(project_id="PRJ-GEN-WORKPLACE"):
    """Lấy toàn bộ trạng thái hệ thống theo đúng định dạng state của WebApp."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Project
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        p_row = cursor.fetchone()
        if not p_row:
            return None

        project = {
            "id": p_row["id"],
            "name": p_row["name"],
            "repo": p_row["repo_path"],
            "branch": p_row["branch"],
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
                    "status": t["status"]
                })
            todos.append(rm_todos)

        # Roles
        cursor.execute("SELECT * FROM agent_roles WHERE project_id = ? ORDER BY id ASC", (project_id,))
        roles = []
        for rl in cursor.fetchall():
            roles.append({
                "id": rl["id"],
                "key": rl["role_key"],
                "name": rl["name"],
                "cli": rl["cli_tool"],
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
            role_memory.append({
                "id": f"MEM-0{rm['id']}",
                "role": rm["role_name"],
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
            item = [t["id"], t["title"], t["assigned_role"].split()[0]]
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
            "catalog": catalog
        }

def search_catalog_fts(query_str, project_id="PRJ-GEN-WORKPLACE"):
    """Tra cứu siêu tốc ID trong Catalog bằng FTS5 (Full-Text Search)."""
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

def seed_tmux_sessions(project_id="PRJ-GEN-WORKPLACE"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tmux_sessions WHERE project_id = ?", (project_id,))
        if cursor.fetchone()[0] > 0:
            return

        sessions = [
            (
                'gw-lead-agy',
                project_id,
                'Lead Architect',
                'Gemini CLI (agy --effort high)',
                'owner_default',
                'Mặc định (Owner Gmail)',
                '~/.gemini',
                'active',
                30129,
                '/workspace/LinuxDataA/gen-workplace',
                """[tmux: gw-lead-agy - window 0 (agy-cli)]
💎 Antigravity CLI v1.2.8 (Gemini 3.8 Flash / Thinking High)
Logged in as: ryan@genesis.corp (Owner Default Gmail)
Workspace: /workspace/LinuxDataA/gen-workplace
Current Goal: Multi-Agent Swarm Orchestrator & SSOT Governance
[14:20:11] Lead: Thẩm định SSOT đặc tả gốc tại docs/SSOT_ORIGINAL_SPEC.md.
[14:20:15] Lead: Đã kiểm tra tiến trình 6 roles. Tất cả đang hoạt động trên branch main.
[14:20:20] Lead: Chờ lệnh phân công nhiệm vụ từ Master Console...
$ """
            ),
            (
                'gw-backend-agy',
                project_id,
                'Backend & DB Specialist',
                'Gemini CLI (agy --mode accept-edits)',
                'profile1',
                'Profile #1 (claude.bot@genesis.local)',
                '/workspace/.agy-profiles/profile1',
                'active',
                30142,
                '/workspace/LinuxDataA/gen-workplace',
                """[tmux: gw-backend-agy - window 0 (agy-cli)]
💎 Antigravity CLI v1.2.8 (--gemini_dir=/workspace/.agy-profiles/profile1)
Logged in as: claude.bot@genesis.local (Profile 1)
Workspace: /workspace/LinuxDataA/gen-workplace
Task: TODO-09 & TODO-10 (SQLite WAL DB & FTS5 Catalog)
[14:21:05] Backend: Khởi tạo schema 12 bảng SQLite tại /app/data/gen-workplace.db.
[14:21:08] Backend: Kích hoạt PRAGMA journal_mode = WAL thành công.
[14:21:12] Backend: Bảng ảo catalog_fts (FTS5) sẵn sàng phục vụ tra cứu tức thì < 1ms.
$ """
            ),
            (
                'gw-frontend-agy',
                project_id,
                'Frontend Specialist',
                'Gemini CLI (agy)',
                'owner_default',
                'Mặc định (Owner Gmail)',
                '~/.gemini',
                'active',
                30155,
                '/workspace/LinuxDataA/gen-workplace',
                """[tmux: gw-frontend-agy - window 0 (agy-cli)]
💎 Antigravity CLI v1.2.8
Logged in as: ryan@genesis.corp (Owner Default Gmail)
Workspace: /workspace/LinuxDataA/gen-workplace
Task: Cửa sổ phiên nền Tmux Runtimes & Tùy chọn Profile
[14:22:30] Frontend: Đang cập nhật giao diện tab Runtimes với interactive terminal & profile switcher.
[14:22:45] Frontend: Đồng bộ màu Slate Industrial Dark v1.1.
$ """
            ),
            (
                'gw-devops-agy',
                project_id,
                'DevOps & Packaging',
                'Gemini CLI (agy --agent devops)',
                'profile3',
                'Profile #3 (profile3)',
                '/workspace/.agy-profiles/profile3',
                'active',
                30168,
                '/workspace/LinuxDataA/gen-workplace',
                """[tmux: gw-devops-agy - window 0 (agy-cli)]
💎 Antigravity CLI v1.2.8 (--gemini_dir=/workspace/.agy-profiles/profile3)
Logged in as: profile3 (Subagent Workstream)
Workspace: /workspace/LinuxDataA/gen-workplace
Task: Docker Compose Live Mount & Installer TUI
[14:23:01] DevOps: Container gen-workplace-app đang chạy healthy trên cổng 8888.
[14:23:10] DevOps: Đã xác thực cờ SELinux :z hot-reload host ↔ container.
$ """
            ),
            (
                'gw-qa-agy',
                project_id,
                'QA Tester',
                'Gemini CLI (agy)',
                'profile4',
                'Profile #4 (profile4)',
                '/workspace/.agy-profiles/profile4',
                'active',
                30180,
                '/workspace/LinuxDataA/gen-workplace',
                """[tmux: gw-qa-agy - window 0 (agy-cli)]
💎 Antigravity CLI v1.2.8 (--gemini_dir=/workspace/.agy-profiles/profile4)
Logged in as: profile4
Workspace: /workspace/LinuxDataA/gen-workplace
Task: Cross-platform tests & Desktop Shortcut verify
[14:24:12] QA: Đã kiểm tra /api/status HTTP 200 OK.
[14:24:15] QA: Đã xác thực shortcut /workspace/Desktop/Gen-workplace.desktop.
$ """
            ),
            (
                'gw-security-agy',
                project_id,
                'Security Auditor',
                'Codex Security CLI / agy',
                'owner_default',
                'Mặc định (Owner Gmail)',
                '~/.gemini',
                'idle',
                30195,
                '/workspace/LinuxDataA/gen-workplace',
                """[tmux: gw-security-agy - window 0 (security-cli)]
🛡️ Codex Security Sandbox (Read-Only Workspace Write Isolation)
Logged in as: ryan@genesis.corp (Owner Default Gmail)
Workspace: /workspace/LinuxDataA/gen-workplace
[14:25:00] Security: Kiểm tra quyền volume mounts. Zero permission leak detected.
[14:25:05] Security: Sẵn sàng audit các lệnh nguy hiểm (force, rm -rf, drop table).
$ """
            )
        ]

        for s in sessions:
            cursor.execute("""
            INSERT INTO tmux_sessions (id, project_id, role_name, cli_tool, account_type, account_label, profile_dir, status, pid, cwd, terminal_output)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, s)

        conn.commit()

def get_tmux_sessions(project_id="PRJ-GEN-WORKPLACE"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tmux_sessions WHERE project_id = ? ORDER BY id ASC", (project_id,))
        rows = cursor.fetchall()
        return [
            {
                "id": r["id"],
                "role_name": r["role_name"],
                "cli_tool": r["cli_tool"],
                "account_type": r["account_type"],
                "account_label": r["account_label"],
                "profile_dir": r["profile_dir"],
                "status": r["status"],
                "pid": r["pid"],
                "cwd": r["cwd"],
                "terminal_output": r["terminal_output"],
                "updated_at": r["updated_at"]
            }
            for r in rows
        ]

def update_tmux_account(session_id, account_type, account_label, profile_dir=""):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE tmux_sessions 
        SET account_type = ?, account_label = ?, profile_dir = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (account_type, account_label, profile_dir, session_id))
        conn.commit()

def append_tmux_output(session_id, command, output=""):
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

# Khởi tạo tự động khi import
init_db()
seed_real_project()
seed_tmux_sessions()


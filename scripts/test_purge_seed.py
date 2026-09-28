#!/usr/bin/env python3
"""
Test Issue #12 (xóa seed giả + migration purge_seed_data). Chạy không cần server/agy/tmux:
DB tạm (DATA_DIR) → sau import không còn seed; chèn vài bản ghi seed cũ + 1 bản ghi thật → init_db()
→ seed mất, bản ghi thật còn; chạy lần 2 không xóa thêm (idempotent); API state/conversations/warroom trả rỗng.
"""
import json
import os
import sys
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-purge-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db  # noqa: E402

db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: None

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


def count(table, where="1=1", params=()):
    with db.get_connection() as conn:
        return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


print("[1] DB mới: không còn seed giả, chỉ giữ project + 6 vai + tmux_sessions + owner + mcp")
for tbl in ("roadmaps", "todos", "workflow_nodes", "agent_runtimes", "master_ssot", "catalog_references", "ssot_events",
            "role_memories", "chat_messages", "gen_conversations", "gen_messages", "gen_scratchpad_notes", "gen_session_todos"):
    check(f"{tbl} trống", count(tbl) == 0, f"{tbl}={count(tbl)}")
check("projects có PRJ-GEN-WORKPLACE", count("projects", "id='PRJ-GEN-WORKPLACE'") == 1)
check("agent_roles có 6 vai", count("agent_roles") == 6, str(count("agent_roles")))
check("tmux_sessions có 6 phiên, không gán task seed", count("tmux_sessions") == 6 and count("tmux_sessions", "current_task_id != ''") == 0)
check("owner_profiles có owner-ryan", count("owner_profiles", "id='owner-ryan'") == 1)
check("mcp token master + setting", count("mcp_agent_tokens") == 1 and count("mcp_auth_settings") == 1)
check("không còn hàm seed cũ", not any(hasattr(db, n) for n in ("seed_ssot_events", "seed_gen_workplace", "seed_session_default_todos")))

print("[2] API khi bảng trống: trả mảng rỗng, không lỗi")
st = db.get_full_state()
check("get_full_state không None", st is not None)
check("roadmap/todos/nodes/runtimes/ssot/roleMemory/catalog/refs/events rỗng",
      all(st[k] == [] for k in ("roadmap", "todos", "nodes", "runtimes", "ssot", "roleMemory", "catalog", "refs", "events")),
      str({k: len(st[k]) for k in ("roadmap", "todos", "nodes", "runtimes", "ssot", "roleMemory", "catalog", "refs", "events")}))
check("kanban 4 cột rỗng", all(v == [] for v in st["kanban"].values()))
check("roles 6", len(st["roles"]) == 6)
check("get_gen_conversations rỗng", db.get_gen_conversations() == [])
check("get_warroom_messages rỗng (không tự seed)", db.get_warroom_messages("war_room") == [] and db.get_warroom_messages("standup") == [] and count("chat_messages") == 0)
check("get_orch_chat_messages rỗng", db.get_orch_chat_messages() == [])
check("get_gen_session_todos rỗng (không tự seed TSK)", db.get_gen_session_todos("conv-bat-ky") == [] and count("gen_session_todos") == 0)
check("get_ssot_events rỗng", db.get_ssot_events() == [])
check("get_gen_messages rỗng", db.get_gen_messages("conv-gen-core-01") == [])
check("skills/mcps không bịa khi máy không có", isinstance(db.get_system_skills(), list) and isinstance(db.get_system_mcps(), list) and not any(s.get("name") == "chief-of-staff" for s in db.get_system_skills()))
check("vault không còn 'GitHub Token sẵn sàng gắn'", not any(v.get("name") == "GitHub Token" for v in db.get_vault_list()))
conv = db.create_gen_conversation(title="Phiên thật")
check("conversation mới không có lời chào giả", db.get_gen_messages(conv["id"]) == [])
check("json hóa được toàn bộ state", json.dumps(st, ensure_ascii=False) and True)

print("[3] chèn bản ghi seed cũ + bản ghi thật → init_db() → seed mất, thật còn")
with db.get_connection() as conn:
    c = conn.cursor()
    c.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-01', 'PRJ-GEN-WORKPLACE', 'Core Architecture & SSOT Spec', 'seed', 3, 'done', 1)")
    c.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-05', 'PRJ-GEN-WORKPLACE', 'GitHub Publication & Release', 'seed', 2, 'queued', 5)")
    c.execute("INSERT INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-REAL', 'PRJ-GEN-WORKPLACE', 'Roadmap thật của Ryan', '', 1, 'live', 9)")
    c.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES ('TODO-01', 'RM-01', 'PRJ-GEN-WORKPLACE', 'Khởi tạo Git repo và lưu cấu trúc dự án', 'Lead Architect', 'done')")
    c.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES ('TODO-12', 'RM-05', 'PRJ-GEN-WORKPLACE', 'Kiểm thử cross-platform trên macOS và Windows WSL2', 'QA Tester', 'queued')")
    # cùng ID seed nhưng tiêu đề khác (do người dùng tạo) → phải GIỮ, và roadmap RM-05 còn todo → giữ roadmap
    c.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES ('TODO-13', 'RM-05', 'PRJ-GEN-WORKPLACE', 'Việc thật do Ryan tự đặt tên', 'Lead Architect', 'queued')")
    c.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES ('TODO-REAL', 'RM-REAL', 'PRJ-GEN-WORKPLACE', 'Task thật', 'Backend & DB Specialist', 'queued')")
    c.execute("UPDATE tmux_sessions SET current_task_id = 'TODO-01' WHERE id = 'gw-lead-agy'")
    c.execute("UPDATE tmux_sessions SET current_task_id = 'TODO-REAL' WHERE id = 'gw-backend-agy'")
    c.execute("INSERT INTO ssot_events (id, project_id, runtime_id, role_name, request, evidence, status, verified_time) VALUES ('EVT-01', 'PRJ-GEN-WORKPLACE', 'runtime-04', 'DevOps & Packaging', 'Khởi động container gen-workplace-app với live bind mount :z', 'docker-compose.yml · port 8888', 'ssot', '20:47')")
    c.execute("INSERT INTO ssot_events (id, project_id, runtime_id, role_name, request, evidence, status, verified_time) VALUES ('EVT-05', 'PRJ-GEN-WORKPLACE', 'runtime-05', 'QA Tester', 'Kiểm thử chu kỳ Auto-Wake 68ms và API Regression Suite', '100% test pass · latency 68ms', 'ssot', '21:05')")
    c.execute("INSERT INTO ssot_events (id, project_id, runtime_id, role_name, request, evidence, status, verified_time) VALUES ('EVT-99', 'PRJ-GEN-WORKPLACE', 'gw-qa-agy', 'QA Tester', 'Sự kiện thật', 'abc1234', 'ssot', '10:00')")
    c.execute("INSERT INTO catalog_references (id, project_id, category, title, description, ref_path) VALUES ('#DB-01', 'PRJ-GEN-WORKPLACE', 'DB', 'SQLite 3 WAL Database', 'seed', '/app/data/gen-workplace.db')")
    c.execute("INSERT INTO catalog_references (id, project_id, category, title, description, ref_path) VALUES ('#EVT-01', 'PRJ-GEN-WORKPLACE', 'EVT', 'Khởi tạo repo và commit đầu tiên 5f91e1e', 'seed', 'git commit 5f91e1e')")
    c.execute("INSERT INTO catalog_references (id, project_id, category, title, description, ref_path) VALUES ('#EVT-03', 'PRJ-GEN-WORKPLACE', 'EVT', 'Xóa sạch mock data, bind dữ liệu thật commit a4f63be', 'seed', 'git commit a4f63be')")
    c.execute("INSERT INTO catalog_references (id, project_id, category, title, description, ref_path) VALUES ('#FILE-99', 'PRJ-GEN-WORKPLACE', 'FILE', 'Tệp thật', 'thật', 'README.md')")
    c.execute("INSERT INTO workflow_nodes (id, project_id, step_index, title, role_name, status) VALUES ('NODE-01', 'PRJ-GEN-WORKPLACE', 0, 'Spec Ingestion', 'Lead Architect', 'done')")
    c.execute("INSERT INTO workflow_nodes (id, project_id, step_index, title, role_name, status) VALUES ('NODE-06', 'PRJ-GEN-WORKPLACE', 5, 'GitHub Release & Freeze', 'Lead Architect', 'pending')")
    c.execute("INSERT INTO workflow_nodes (id, project_id, step_index, title, role_name, status) VALUES ('NODE-99', 'PRJ-GEN-WORKPLACE', 9, 'Node thật', 'QA Tester', 'pending')")
    c.execute("INSERT INTO agent_runtimes (id, project_id, role_name, cli_tool, status) VALUES ('runtime-01', 'PRJ-GEN-WORKPLACE', 'Lead Architect', 'Gemini CLI (agy)', 'running')")
    c.execute("INSERT INTO master_ssot (id, project_id, title, body, source_ref, verified_time) VALUES ('SSOT-DOCKER-03', 'PRJ-GEN-WORKPLACE', 'Môi Trường Container Hóa Khép Kín Đa Nền Tảng', 'seed', 'EVT-01', '20:47')")
    c.execute("INSERT INTO master_ssot (id, project_id, title, body, source_ref, verified_time) VALUES ('SSOT-MASTER-INPUT', 'PRJ-GEN-WORKPLACE', 'Chỉ Thị Tổng Thể & PRD Nguồn', 'nội dung thật do Ryan nhập', 'Input Chat Tổng', '10:00')")
    c.execute("INSERT INTO role_memories (project_id, role_name, body, tags_json, synced_to_ssot, created_time) VALUES ('PRJ-GEN-WORKPLACE', 'QA Tester', 'Thiết lập test suite tự động cho chu kỳ Auto-Wake 68ms, kiểm tra toàn bộ REST API endpoint và chứng thực bằng chứng commit hash trước khi bàn giao.', '[]', 1, '20:37')")
    c.execute("INSERT INTO role_memories (project_id, role_name, body, tags_json, synced_to_ssot, created_time) VALUES ('PRJ-GEN-WORKPLACE', 'QA Tester', 'Ghi nhớ thật: test chạy bằng scripts/test_*.py', '[]', 0, '10:00')")
    seed_chats = [
        ('runtime-04', 'DevOps & Packaging', 'Docker Up', 'Container <code>gen-workplace-app</code> đã khởi động thành công trên cổng 8888.'),
        ('war_room', 'Genesis Orchestrator', 'Reply', 'Rõ mệnh lệnh của Ryan! Tôi (Gen - Core Orchestrator) đã phổ biến chỉ thị tới toàn thể 6 chuyên gia. Hệ thống đang chạy ở chế độ kỷ luật thép: 1 Profile = 1 Identity, Task Mutex độc quyền và nghiệm thu 100% bằng chứng vật lý.'),
        ('war_room', 'Lead Architect', 'Report', 'Báo cáo Ryan và Gen: Đặc tả SSOT đã khóa bất biến tại <code>docs/SSOT_ORIGINAL_SPEC.md</code>. Tất cả 6 chuyên gia đã được cấp phát nhiệm vụ cụ thể trên Live Workbench.'),
        ('war_room', 'Backend Specialist', 'Reply', 'Đã rõ chỉ thị của Ryan (Owner)! Tôi (Backend Specialist) đang kiểm soát SQLite WAL và các endpoint API.'),
        ('war_room', 'Genesis Orchestrator', 'Reply', 'Chỉ huy tối cao ghi nhận mệnh lệnh: <em>"làm đi"</em>. Tôi (Gen) đang truyền đạt trực tiếp xuống Ban Chỉ Huy Kỹ Thuật.'),
        ('orch', 'Genesis Orchestrator', 'Reply', 'Chỉ huy tối cao đã ghi nhận chỉ thị: <em>"x"</em>. Tôi đang điều phối yêu cầu này tới Swarm.'),
        ('orch', 'Genesis Orchestrator', 'Reply', 'Đã chấp hành mệnh lệnh tối cao từ Ryan! Orchestrator đã truyền lệnh đồng loạt tới toàn bộ 6 vị trí Swarm.'),
        ('orch', 'Genesis Orchestrator', 'Reply', 'Xin chào Ryan! Tôi là Genesis Orchestrator, sẵn sàng nhận lệnh.'),
        ('standup', 'Backend Specialist', 'Report', 'Đã nhận việc từ Leader! Tôi đang triển khai TODO-14 trong <code>backend/db.py</code>. Cam kết response time < 5ms và nộp commit hash trước 10h.'),
        ('war_room', 'Lead Architect', 'Reply', 'Báo cáo Gen: Tôi (Lead Architect) đang giám sát chặt chẽ chuỗi Todo DAG và đối soát bằng chứng với SSOT gốc.'),
    ]
    for rt, author, tag, body in seed_chats:
        c.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json) VALUES ('PRJ-GEN-WORKPLACE', ?, ?, '08:00:00', ?, ?, '[]')", (rt, author, tag, body))
    c.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json) VALUES ('PRJ-GEN-WORKPLACE', 'war_room', 'Ryan (Owner)', '09:00:00', 'Directive', '@backend kiểm tra API thật', '[]')")
    c.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json) VALUES ('PRJ-GEN-WORKPLACE', 'war_room', 'gw-backend-agy', '09:01:00', 'Report', ?, '[]')", ("Trả lời thật từ agy\nexit=0",))
    # trả lời agy thật (tác giả gw-*-agy) dù bắt đầu bằng câu giống mẫu cũ cũng phải GIỮ
    c.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json) VALUES ('PRJ-GEN-WORKPLACE', 'war_room', 'gw-lead-agy', '09:03:00', 'Report', 'Đã rõ chỉ thị của Ryan, đây là trả lời thật của agy\nexit=0', '[]')")
    c.execute("INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json) VALUES ('PRJ-GEN-WORKPLACE', 'orch', 'Owner (Ryan)', '09:02:00', 'Instruction', 'Báo cáo Ryan và Gen có gì mới không', '[]')")
    for cid, title in (('conv-gen-core-01', 'Kiến Trúc & Điều Phối Swarm Tối Cao'), ('conv-gen-builder', 'Gen_workplace Builder'), ('conv-real-1', 'Phiên thật của Ryan')):
        c.execute("INSERT INTO gen_conversations (id, project_id, title, model, account_profile, owner_id) VALUES (?, 'PRJ-GEN-WORKPLACE', ?, 'Gemini 3.8 Flash (High)', 'owner_default', 'owner-ryan')", (cid, title))
    c.execute("INSERT INTO gen_messages (conversation_id, author, role, content, model, owner_id) VALUES ('conv-gen-core-01', 'Gen Core', 'assistant', 'Chào Ryan! Tôi (Gen - Core Orchestrator) đã sẵn sàng.', '', 'owner-ryan')")
    c.execute("INSERT INTO gen_messages (conversation_id, author, role, content, model, owner_id) VALUES ('conv-real-1', 'Ryan (Owner)', 'user', 'tin thật', '', 'owner-ryan')")
    c.execute("INSERT INTO gen_messages (conversation_id, author, role, content, model, owner_id) VALUES ('conv-real-1', 'Gen Core', 'assistant', 'Sẵn sàng phục vụ Owner Ryan! Bạn muốn giao nhiệm vụ hoặc thảo luận kiến trúc nào hôm nay?', '', 'owner-ryan')")
    c.execute("INSERT INTO gen_scratchpad_notes (id, project_id, conversation_id, title, content, author) VALUES ('NOTE-01', 'PRJ-GEN-WORKPLACE', 'conv-gen-core-01', 'Nguyên Tắc SSOT Tuyệt Đối', 'seed', 'Ryan')")
    c.execute("INSERT INTO gen_scratchpad_notes (id, project_id, conversation_id, title, content, author) VALUES ('NOTE-77', 'PRJ-GEN-WORKPLACE', 'conv-real-1', 'Ghi chú thật', 'thật', 'Ryan (Owner)')")
    c.execute("INSERT INTO gen_session_todos (id, conversation_id, project_id, title, status) VALUES ('TSK-01', 'conv-gen-builder', 'PRJ-GEN-WORKPLACE', 'Thiết lập hạ tầng Swarm & Cách ly Workspace phiên', 'review')")
    c.execute("INSERT INTO gen_session_todos (id, conversation_id, project_id, title, status) VALUES ('TSK-02', 'conv-real-1', 'PRJ-GEN-WORKPLACE', 'Thực thi tác vụ & Cập nhật tiến độ theo từng checklist', 'todo')")
    c.execute("INSERT INTO gen_session_todos (id, conversation_id, project_id, title, status) VALUES ('TSK-03', 'conv-real-1', 'PRJ-GEN-WORKPLACE', 'Việc thật trong phiên thật', 'todo')")
    conn.commit()

db.init_db()  # migration: purge_seed_data() chạy cuối init_db()

check("roadmaps: RM-01 mất, RM-05 giữ (còn todo thật), RM-REAL giữ", count("roadmaps", "id='RM-01'") == 0 and count("roadmaps", "id='RM-05'") == 1 and count("roadmaps", "id='RM-REAL'") == 1)
check("todos: TODO-01/TODO-12 mất; TODO-13 (tiêu đề khác) và TODO-REAL giữ", count("todos", "id IN ('TODO-01','TODO-12')") == 0 and count("todos", "id IN ('TODO-13','TODO-REAL')") == 2)
with db.get_connection() as conn:
    lead = conn.execute("SELECT current_task_id FROM tmux_sessions WHERE id='gw-lead-agy'").fetchone()[0]
    be = conn.execute("SELECT current_task_id FROM tmux_sessions WHERE id='gw-backend-agy'").fetchone()[0]
check("tmux_sessions: gw-lead bỏ gán TODO-01, gw-backend giữ TODO-REAL", lead == "" and be == "TODO-REAL", f"{lead!r}/{be!r}")
check("ssot_events: EVT-01/05 mất, EVT-99 giữ", count("ssot_events", "id IN ('EVT-01','EVT-05')") == 0 and count("ssot_events", "id='EVT-99'") == 1)
check("catalog: #DB-01/#EVT-01/#EVT-03 mất, #FILE-99 giữ", count("catalog_references", "id IN ('#DB-01','#EVT-01','#EVT-03')") == 0 and count("catalog_references", "id='#FILE-99'") == 1)
check("catalog_fts đồng bộ (không còn seed)", db.search_catalog_fts("SQLite") == [] and len(db.search_catalog_fts("Tệp")) == 1, str(db.search_catalog_fts("SQLite")))
check("workflow_nodes: NODE-01/06 mất, NODE-99 giữ", count("workflow_nodes", "id IN ('NODE-01','NODE-06')") == 0 and count("workflow_nodes", "id='NODE-99'") == 1)
check("agent_runtimes: runtime-01 mất", count("agent_runtimes") == 0)
check("master_ssot: SSOT-DOCKER-03 mất, SSOT-MASTER-INPUT giữ", count("master_ssot", "id='SSOT-DOCKER-03'") == 0 and count("master_ssot", "id='SSOT-MASTER-INPUT'") == 1)
check("role_memories: seed mất, thật giữ", count("role_memories") == 1 and count("role_memories", "body LIKE 'Ghi nhớ thật%'") == 1)
with db.get_connection() as conn:
    bodies = [r[0] for r in conn.execute("SELECT body FROM chat_messages ORDER BY id").fetchall()]
check("chat_messages: 10 tin seed/câu mẫu mất, 4 tin thật giữ (kể cả tin người dùng/agy trùng đầu câu mẫu)",
      sorted(bodies) == sorted(['@backend kiểm tra API thật', 'Trả lời thật từ agy\nexit=0', 'Báo cáo Ryan và Gen có gì mới không', 'Đã rõ chỉ thị của Ryan, đây là trả lời thật của agy\nexit=0']), str(bodies))
check("gen_conversations: 2 phiên seed mất, conv-real-1 giữ", count("gen_conversations", "id IN ('conv-gen-core-01','conv-gen-builder')") == 0 and count("gen_conversations", "id='conv-real-1'") == 1)
check("gen_messages: tin của phiên seed + lời chào giả mất, tin thật giữ", count("gen_messages") == 1 and count("gen_messages", "content='tin thật'") == 1, str(count("gen_messages")))
check("gen_scratchpad_notes: NOTE-01 (phiên seed) mất, NOTE-77 giữ", count("gen_scratchpad_notes", "id='NOTE-01'") == 0 and count("gen_scratchpad_notes", "id='NOTE-77'") == 1)
check("gen_session_todos: TSK seed mất (kể cả trong phiên thật), TSK-03 thật giữ", count("gen_session_todos") == 1 and count("gen_session_todos", "id='TSK-03'") == 1, str(count("gen_session_todos")))

print("[4] idempotent: chạy lại purge không xóa gì thêm")
res2 = db.purge_seed_data()
check("total = 0", res2["total"] == 0, str(res2))
check("bản ghi thật vẫn nguyên", count("todos") == 2 and count("chat_messages") == 4 and count("gen_conversations") == 2,
      f"todos={count('todos')} chat={count('chat_messages')} conv={count('gen_conversations')}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)

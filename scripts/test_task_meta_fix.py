#!/usr/bin/env python3
"""
Test bộ sửa đổi meta task (TSK-32 / Issue #68 mục 6, 11, 12):
1. allowed_paths đúng thư mục thật trong repo, migration idempotent, frontend hiển thị 'Phạm vi gợi ý (không chặn)', bỏ 'Toàn quyền trong workspace'.
2. list_kanban_tasks bỏ default conv-gen-core-01; conv_id trống = phiên gần nhất có thẻ.
3. get_system_status trả running_dispatches (kèm elapsed_sec) + auto_update.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="gw-test-meta-fix-")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["HOME"], exist_ok=True)

sys.path.insert(0, str(ROOT))

from backend import db, mcp_core, auto_update  # noqa: E402

total = 0
failed = 0


def check(label, cond, extra=""):
    global total, failed
    total += 1
    if cond:
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {extra}")


def main():
    print("[1] Kiểm tra cấu hình allowed_paths trong SWARM_DEFAULT_CONFIG và DB")
    cfg_map = {c["id"]: c["allowed_paths"] for c in db.SWARM_DEFAULT_CONFIG}

    check("lead allowed_paths đúng",
          cfg_map.get("gw-lead-agy") == ["docs/**", "roles/**", "README.md", "AGENTS.md"],
          cfg_map.get("gw-lead-agy"))

    check("backend allowed_paths đúng",
          cfg_map.get("gw-backend-agy") == ["backend/**", "frontend/**", "scripts/**", "docs/**", "roles/**", "README.md"],
          cfg_map.get("gw-backend-agy"))

    check("devops allowed_paths đúng",
          cfg_map.get("gw-devops-agy") == ["scripts/**", "docs/**", "roles/**", "*.service", "README.md"],
          cfg_map.get("gw-devops-agy"))

    check("qa allowed_paths đúng",
          cfg_map.get("gw-qa-agy") == ["scripts/test_*.py", "docs/**"],
          cfg_map.get("gw-qa-agy"))

    # Kiểm tra DB sau khi migrate_allowed_paths
    with db.get_connection() as conn:
        rows = conn.execute("SELECT id, allowed_paths_json FROM tmux_sessions").fetchall()
    db_paths = {r["id"]: json.loads(r["allowed_paths_json"] or "[]") for r in rows}
    check("DB tmux_sessions allowed_paths_json đã migrate đúng cho backend",
          db_paths.get("gw-backend-agy") == ["backend/**", "frontend/**", "scripts/**", "docs/**", "roles/**", "README.md"],
          db_paths.get("gw-backend-agy"))
    check("DB tmux_sessions allowed_paths_json đã migrate đúng cho qa",
          db_paths.get("gw-qa-agy") == ["scripts/test_*.py", "docs/**"],
          db_paths.get("gw-qa-agy"))

    # Idempotent: chạy lại migrate_allowed_paths không gây lỗi
    db.migrate_allowed_paths()
    with db.get_connection() as conn:
        rows2 = conn.execute("SELECT id, allowed_paths_json FROM tmux_sessions").fetchall()
    db_paths2 = {r["id"]: json.loads(r["allowed_paths_json"] or "[]") for r in rows2}
    check("migrate_allowed_paths idempotent", db_paths == db_paths2)

    # Kiểm tra frontend/index.html
    html_content = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    check("Frontend có nhãn 'Phạm vi gợi ý (không chặn)'",
          "Phạm vi gợi ý (không chặn)" in html_content)
    check("Frontend đã bỏ 'Toàn quyền trong workspace'",
          "Toàn quyền trong workspace" not in html_content)

    print("\n[2] Kiểm tra list_kanban_tasks (schema & fallback phiên gần nhất có thẻ)")
    tool_meta = next((t for t in mcp_core.TOOLS if t["name"] == "list_kanban_tasks"), None)
    check("list_kanban_tasks tool tồn tại trong schema", tool_meta is not None)
    conv_id_prop = tool_meta.get("inputSchema", {}).get("properties", {}).get("conv_id", {})
    check("list_kanban_tasks schema không có default 'conv-gen-core-01'",
          "default" not in conv_id_prop or conv_id_prop.get("default") != "conv-gen-core-01")

    # Tạo 2 phiên: conv_a tạo trước có task; conv_b tạo sau KHÔNG có task
    res_a = db.create_gen_conversation("PRJ-GEN-WORKPLACE", "Phiên A có task")
    conv_a = res_a.get("conversation", {}).get("id") or res_a.get("id") or "conv-a"
    db.save_gen_session_todo(conv_a, None, "Task 1 của phiên A", "Mô tả", "todo", "high", "Gen Core", [], "", 0, "owner-ryan", "VIEC-20")

    res_b = db.create_gen_conversation("PRJ-GEN-WORKPLACE", "Phiên B rỗng")
    conv_b = res_b.get("conversation", {}).get("id") or res_b.get("id") or "conv-b"

    with db.get_connection() as conn:
        conn.execute("UPDATE gen_conversations SET updated_at = '2026-10-01 12:00:00' WHERE id = ?", (conv_a,))
        conn.execute("UPDATE gen_conversations SET updated_at = '2026-10-01 13:00:00' WHERE id = ?", (conv_b,))
        conn.commit()

    # default_conv_id() trả về phiên mới nhất (conv_b)
    check("default_conv_id là phiên mới nhất (conv_b)", db.default_conv_id() == conv_b)
    # default_conv_id_with_tasks() trả về phiên có task (conv_a)
    check("default_conv_id_with_tasks trả về conv_a", db.default_conv_id_with_tasks() == conv_a)

    # Gọi tool list_kanban_tasks không truyền conv_id
    call_res = mcp_core.execute_tool("list_kanban_tasks", {})
    check("list_kanban_tasks không truyền conv_id -> không lỗi", not call_res.get("isError"))
    parsed = json.loads(call_res["content"][0]["text"])
    check("list_kanban_tasks tự chọn phiên conv_a có thẻ",
          parsed.get("conversation_id") == conv_a and parsed.get("count", 0) >= 1, parsed)

    # Gọi với conv_b rỗng
    call_res_b = mcp_core.execute_tool("list_kanban_tasks", {"conv_id": conv_b})
    parsed_b = json.loads(call_res_b["content"][0]["text"])
    check("list_kanban_tasks với conv_b trả 0 task", parsed_b.get("count") == 0)

    print("\n[3] Kiểm tra get_system_status trả running_dispatches + auto_update")
    call_status = mcp_core.execute_tool("get_system_status", {})
    check("get_system_status thực thi thành công", not call_status.get("isError"))
    st_data = json.loads(call_status["content"][0]["text"])
    check("get_system_status có key running_dispatches", "running_dispatches" in st_data)
    check("get_system_status có key auto_update", "auto_update" in st_data)
    check("auto_update là boolean", isinstance(st_data["auto_update"], bool))
    check("running_dispatches ban đầu là danh sách rỗng", isinstance(st_data["running_dispatches"], list) and len(st_data["running_dispatches"]) == 0)

    # Tạo dispatch running
    d_id = db.start_dispatch_log("gw-backend-agy", kind="build", task_id="TSK-32")
    call_status2 = mcp_core.execute_tool("get_system_status", {})
    st_data2 = json.loads(call_status2["content"][0]["text"])
    running_list = st_data2.get("running_dispatches", [])
    check("running_dispatches nhận diện được dispatch đang chạy", len(running_list) >= 1)
    target_d = next((d for d in running_list if d.get("id") == d_id), None)
    check("dispatch có đầy đủ id, kind, session_id, task_id, started_at, elapsed_sec",
          target_d is not None and all(k in target_d for k in ("id", "kind", "session_id", "task_id", "started_at", "elapsed_sec")),
          target_d)
    check("elapsed_sec là số >= 0",
          target_d is not None and isinstance(target_d.get("elapsed_sec"), (int, float)) and target_d["elapsed_sec"] >= 0,
          target_d)

    print(f"\n{total - failed}/{total} test pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
GENESIS Multi-Agent Swarm Workplace - Model Context Protocol (MCP) Core Engine
Đặc tả và điều phối giao thức MCP JSON-RPC 2.0 chuẩn cho AI Agent / Subagents / LLM Clients.
Cung cấp đầy đủ tính năng của WebApp giao diện cho AI Agent thông qua 25+ MCP Tools,
Resources, và Prompts chuyên dụng.
"""

import sys
import json
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from backend import db
    from backend import directive_guard
except ImportError:
    import db
    import directive_guard

MCP_SERVER_INFO = {
    "name": "gen-workplace",
    "version": "1.0.0"
}

MCP_PROTOCOL_VERSION = "2024-11-05"

# ==========================================
# 1. MCP TOOLS REGISTRY
# ==========================================

TOOLS = [
    # ---------------- Quota & Accounts ----------------
    {
        "name": "get_live_quota",
        "description": "Lấy thông số Quota thời gian thực từ Google Cloud Code API của tài khoản đang dùng: tỷ lệ hạn mức 5 giờ (rolling reset) và tỷ lệ hạn mức tuần (weekly reset), chi tiết theo Gemini Pro/Flash và Claude Sonnet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {
                    "type": "string",
                    "description": "ID tài khoản / profile OAuth (mặc định 'owner_default').",
                    "default": "owner_default"
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": "Bắt buộc làm mới token và gọi trực tiếp API Google (mặc định true).",
                    "default": True
                }
            }
        }
    },
    {
        "name": "list_google_accounts",
        "description": "Liệt kê toàn bộ các tài khoản Google / OAuth profiles có trong hệ thống và trạng thái đăng nhập/hiệu lực của token.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "switch_google_account",
        "description": "Chuyển đổi tài khoản Google đang sử dụng cho một phiên runtime Tmux hoặc gán cho worker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "ID phiên hoặc runtime cần chuyển (vd: 'gw-lead-agy', 'gw-fullstack-dev').",
                    "default": "gw-lead-agy"
                },
                "account_id": {
                    "type": "string",
                    "description": "ID profile tài khoản (vd: 'owner_default', 'profile1'). Nhãn hiển thị được tính tự động từ email thật của profile."
                }
            },
            "required": ["account_id"]
        }
    },
    {
        "name": "get_oauth_login_url",
        "description": "Khởi động luồng đăng nhập OAuth Google mới để thêm tài khoản vào Workplace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {
                    "type": "string",
                    "description": "ID profile đăng ký (vd: 'profile1', 'profile2').",
                    "default": "profile1"
                },
                "custom_path": {
                    "type": "string",
                    "description": "Đường dẫn thư mục lưu credentials riêng biệt (tùy chọn)."
                }
            }
        }
    },

    # ---------------- Swarm Workers & War Room ----------------
    {
        "name": "list_swarm_workers",
        "description": "Liệt kê 6 Agent Swarm chuyên gia (Lead Architect, Product Manager, Fullstack Dev, DevOps, QA, Research/Legal) cùng trạng thái hoạt động, mô hình AI đang gán, tài khoản và thông tin runtime.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "ID dự án (mặc định 'PRJ-GEN-WORKPLACE').",
                    "default": "PRJ-GEN-WORKPLACE"
                }
            }
        }
    },
    {
        "name": "send_worker_directive",
        "description": "Gửi chỉ thị, câu lệnh terminal hoặc chuỗi phím điều khiển trực tiếp vào runtime/tmux của một Agent Swarm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "ID phiên runtime chuyên gia (vd: 'gw-lead-agy', 'gw-fullstack-dev', 'gw-devops')."
                },
                "command": {
                    "type": "string",
                    "description": "Nội dung câu lệnh terminal hoặc chỉ thị gửi tới worker."
                },
                "key": {
                    "type": "string",
                    "description": "Chuỗi phím điều khiển terminal tmux (vd: 'C-c', 'Enter', 'q')."
                }
            },
            "required": ["session_id"]
        }
    },
    {
        "name": "manage_worker_lifecycle",
        "description": "Điều khiển vòng đời hoạt động của worker: pause (tạm dừng), resume (tiếp tục), hibernate (ngủ đông tiết kiệm tài nguyên), wake (đánh thức), sleep_all (ngủ đông tất cả), wake_all (đánh thức tất cả).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Hành động điều khiển: 'pause' | 'resume' | 'hibernate' | 'wake' | 'sleep_all' | 'wake_all'",
                    "enum": ["pause", "resume", "hibernate", "wake", "sleep_all", "wake_all"]
                },
                "session_id": {
                    "type": "string",
                    "description": "ID phiên runtime hoặc 'all' (mặc định 'all').",
                    "default": "all"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "get_worker_terminal_output",
        "description": "Đọc kết quả màn hình terminal thực tế (tmux capture-pane) gần nhất của một runtime worker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "ID phiên runtime chuyên gia (vd: 'gw-lead-agy', 'gw-fullstack-dev')."
                },
                "lines": {
                    "type": "integer",
                    "description": "Số dòng lịch sử cần lấy (mặc định 60 dòng).",
                    "default": 60
                }
            },
            "required": ["session_id"]
        }
    },
    {
        "name": "post_warroom_message",
        "description": "Đăng tin nhắn hoặc chỉ thị vào phòng họp chung War Room Swarm All-Hands để toàn bộ các agent phối hợp.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Nội dung tin nhắn hoặc chỉ thị."
                },
                "author": {
                    "type": "string",
                    "description": "Tên người hoặc agent phát biểu (mặc định 'AI Agent').",
                    "default": "AI Agent"
                },
                "tag": {
                    "type": "string",
                    "description": "Thẻ phân loại: 'Directive', 'Report', 'Clarification', 'Emergency'.",
                    "default": "Directive"
                },
                "channel_id": {
                    "type": "string",
                    "description": "ID kênh (mặc định 'war_room').",
                    "default": "war_room"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "get_warroom_messages",
        "description": "Lấy lịch sử tin nhắn phòng họp chung War Room Swarm All-Hands.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "ID kênh (mặc định 'war_room').",
                    "default": "war_room"
                },
                "limit": {
                    "type": "integer",
                    "description": "Số lượng tin nhắn gần nhất cần lấy (mặc định 30).",
                    "default": 30
                }
            }
        }
    },

    # ---------------- Kanban & Task Governance ----------------
    {
        "name": "list_kanban_tasks",
        "description": "Liệt kê danh sách nhiệm vụ Kanban trong phiên làm việc, kèm trạng thái (todo, in_progress, review, done), độ ưu tiên, danh sách checklist con, agent được giao và bằng chứng nghiệm thu.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID cuộc trò chuyện / phiên làm việc (mặc định 'conv-gen-core-01').",
                    "default": "conv-gen-core-01"
                }
            }
        }
    },
    {
        "name": "create_kanban_task",
        "description": "Tạo nhiệm vụ mới trên bảng Kanban của phiên với checklist con và độ ưu tiên.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID phiên làm việc (mặc định 'conv-gen-core-01').",
                    "default": "conv-gen-core-01"
                },
                "title": {
                    "type": "string",
                    "description": "Tiêu đề nhiệm vụ."
                },
                "description": {
                    "type": "string",
                    "description": "Mô tả chi tiết yêu cầu nhiệm vụ."
                },
                "priority": {
                    "type": "string",
                    "description": "Mức độ ưu tiên ('critical', 'high', 'medium', 'low').",
                    "default": "high"
                },
                "assigned_agent": {
                    "type": "string",
                    "description": "Tên Agent được giao phụ trách (vd: 'Lead Architect', 'Fullstack Dev').",
                    "default": "Gen Core"
                },
                "checklist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách các đầu việc con (checklist items) dạng text."
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "claim_task",
        "description": "Khóa độc quyền (claim) nhiệm vụ cho một worker/agent cụ thể theo cơ chế Mutex chống xung đột tranh chấp nhiệm vụ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "ID worker nhận việc (vd: 'gw-lead-agy', 'gw-fullstack-dev')."
                },
                "task_id": {
                    "type": "string",
                    "description": "ID nhiệm vụ cần khóa nhận việc."
                },
                "project_id": {
                    "type": "string",
                    "description": "ID dự án (mặc định 'PRJ-GEN-WORKPLACE').",
                    "default": "PRJ-GEN-WORKPLACE"
                }
            },
            "required": ["session_id", "task_id"]
        }
    },
    {
        "name": "complete_task",
        "description": "Nghiệm thu hoàn tất nhiệm vụ với bằng chứng bắt buộc (evidence_ref như file log, commit hash, file path).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "ID worker hoàn thành."
                },
                "task_id": {
                    "type": "string",
                    "description": "ID nhiệm vụ hoàn tất."
                },
                "evidence_ref": {
                    "type": "string",
                    "description": "Bằng chứng nghiệm thu (đường dẫn file kết quả, commit hash, hoặc log tóm tắt)."
                },
                "verified_by": {
                    "type": "string",
                    "description": "Người hoặc vai trò thẩm định nghiệm thu.",
                    "default": "Lead Architect"
                }
            },
            "required": ["session_id", "task_id", "evidence_ref"]
        }
    },
    {
        "name": "update_task_checklist",
        "description": "Đánh dấu hoàn thành hoặc chưa hoàn thành cho một mục checklist con trong nhiệm vụ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID phiên làm việc (mặc định 'conv-gen-core-01').",
                    "default": "conv-gen-core-01"
                },
                "task_id": {
                    "type": "string",
                    "description": "ID nhiệm vụ chứa checklist."
                },
                "item_id": {
                    "type": "string",
                    "description": "ID hoặc nội dung của mục checklist."
                },
                "done": {
                    "type": "boolean",
                    "description": "Trạng thái mới: true nếu xong, false nếu chưa."
                }
            },
            "required": ["task_id", "item_id", "done"]
        }
    },

    # ---------------- Chat & Conversations ----------------
    {
        "name": "gen_chat",
        "description": "Gửi tin nhắn trao đổi trong phiên hội thoại Gen Workplace, kích hoạt phản hồi thông minh và đồng bộ hóa ngữ cảnh phiên.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Nội dung tin nhắn gửi vào phiên."
                },
                "conv_id": {
                    "type": "string",
                    "description": "ID phiên làm việc (mặc định 'conv-gen-core-01').",
                    "default": "conv-gen-core-01"
                },
                "model": {
                    "type": "string",
                    "description": "Mô hình AI xử lý tin nhắn (vd: 'Gemini 3.1 Pro (High)', 'Claude Sonnet 4.6 (Thinking)').",
                    "default": "Gemini 3.1 Pro (High)"
                },
                "author": {
                    "type": "string",
                    "description": "Tác giả tin nhắn (mặc định 'AI Agent').",
                    "default": "AI Agent"
                },
                "account": {
                    "type": "string",
                    "description": "Tài khoản định tuyến (mặc định 'owner_default').",
                    "default": "owner_default"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "list_conversations",
        "description": "Liệt kê tất cả các cuộc trò chuyện / phiên làm việc đang có trên hệ thống Gen Workplace kèm thông tin ghim, mô hình, và số lượng tin nhắn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "ID dự án (mặc định 'PRJ-GEN-WORKPLACE').",
                    "default": "PRJ-GEN-WORKPLACE"
                }
            }
        }
    },
    {
        "name": "create_conversation",
        "description": "Tạo một cuộc trò chuyện / phiên làm việc mới trong Gen Workplace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Tiêu đề cuộc trò chuyện mới."
                },
                "model": {
                    "type": "string",
                    "description": "Mô hình AI ban đầu.",
                    "default": "Gemini 3.1 Pro (High)"
                },
                "account": {
                    "type": "string",
                    "description": "Tài khoản sử dụng.",
                    "default": "owner_default"
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "get_conversation_messages",
        "description": "Lấy toàn bộ lịch sử tin nhắn trong một cuộc trò chuyện.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID cuộc trò chuyện cần lấy lịch sử."
                }
            },
            "required": ["conv_id"]
        }
    },
    {
        "name": "compact_conversation",
        "description": "Nén lũy tiến ngữ cảnh cuộc trò chuyện (progressive compaction) để tối ưu token và lưu lại bản tóm tắt các quyết định quan trọng.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID cuộc trò chuyện cần nén ngữ cảnh."
                },
                "manual": {
                    "type": "boolean",
                    "description": "Chế độ nén chủ động do người/agent kích hoạt.",
                    "default": True
                }
            },
            "required": ["conv_id"]
        }
    },

    # ---------------- Notes, Vault & Workspace Files ----------------
    {
        "name": "list_notes",
        "description": "Lấy danh sách các ghi chú nhanh (Scratchpad Notes) trong dự án hoặc theo phiên.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID phiên lọc ghi chú (để trống nếu muốn lấy toàn bộ)."
                }
            }
        }
    },
    {
        "name": "save_note",
        "description": "Tạo mới hoặc cập nhật một ghi chú nhanh với thẻ tag và liên kết bằng chứng.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Tiêu đề ghi chú."
                },
                "content": {
                    "type": "string",
                    "description": "Nội dung ghi chú dạng Markdown."
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách thẻ phân loại (vd: ['Architecture', 'MCP', 'Decision'])."
                },
                "evidence_ref": {
                    "type": "string",
                    "description": "Liên kết bằng chứng hoặc đường dẫn file liên quan."
                },
                "conv_id": {
                    "type": "string",
                    "description": "Gắn vào ID phiên (tùy chọn)."
                },
                "note_id": {
                    "type": "string",
                    "description": "ID ghi chú nếu là cập nhật (để trống nếu tạo mới)."
                }
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "delete_note",
        "description": "Xóa một ghi chú nhanh theo ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "ID ghi chú cần xóa."
                }
            },
            "required": ["note_id"]
        }
    },
    {
        "name": "read_workspace_file",
        "description": "Đọc an toàn nội dung tệp tin trong thư mục dự án workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Đường dẫn tương đối hoặc tuyệt đối của tệp tin cần đọc."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "create_workspace_file",
        "description": "Tạo mới hoặc chỉnh sửa tệp tin trong không gian làm việc của phiên.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID phiên làm việc (mặc định 'conv-gen-core-01').",
                    "default": "conv-gen-core-01"
                },
                "path": {
                    "type": "string",
                    "description": "Đường dẫn tệp tin tương đối trong thư mục phiên."
                },
                "content": {
                    "type": "string",
                    "description": "Nội dung tệp tin cần ghi."
                },
                "is_dir": {
                    "type": "boolean",
                    "description": "Đặt true nếu muốn tạo thư mục.",
                    "default": False
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "list_workspace_files",
        "description": "Duyệt danh sách cây thư mục và tệp tin mã nguồn trong workspace dự án.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_system_status",
        "description": "Lấy tổng thể trạng thái hệ thống: container runtime, kết nối SQLite, trạng thái SSOT, số lượng agent và thông tin dự án.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

# Map tool names to fast lookup
TOOL_LOOKUP = {t["name"]: t for t in TOOLS}

# ==========================================
# 2. MCP RESOURCES REGISTRY
# ==========================================

RESOURCES = [
    {
        "uri": "gen-workplace://quota",
        "name": "Live Quota & Account Status",
        "mimeType": "application/json",
        "description": "Trạng thái hạn mức 2 tầng (5h rolling & tuần) từ Google Cloud Code API và danh sách profiles."
    },
    {
        "uri": "gen-workplace://system/status",
        "name": "System Health & Architecture Status",
        "mimeType": "application/json",
        "description": "Thông tin trạng thái máy chủ, cơ sở dữ liệu SQLite WAL, FTS5 và dự án đang hoạt động."
    },
    {
        "uri": "gen-workplace://swarm/workers",
        "name": "Swarm Specialists Roster",
        "mimeType": "application/json",
        "description": "Danh sách 6 runtime chuyên gia Agent Swarm, vai trò, mô hình AI và trạng thái."
    },
    {
        "uri": "gen-workplace://kanban/tasks",
        "name": "Active Kanban Tasks",
        "mimeType": "application/json",
        "description": "Danh sách nhiệm vụ Kanban và tiến độ checklist theo phiên."
    }
]

# ==========================================
# 3. MCP PROMPTS REGISTRY
# ==========================================

PROMPTS = [
    {
        "name": "anti_chaos_task_execution",
        "description": "Quy trình chuẩn Anti-Chaos Task Execution: Claim nhiệm vụ trước khi làm, kiểm tra quota, thực thi với bằng chứng rõ ràng và cập nhật checklist.",
        "arguments": [
            {
                "name": "task_id",
                "description": "ID nhiệm vụ trên Kanban cần thực thi.",
                "required": False
            }
        ]
    },
    {
        "name": "quota_optimization_sop",
        "description": "Quy chuẩn tối ưu hạn mức 5h/tuần: Phân phối model Gemini Flash cho tác vụ tra cứu, Gemini Pro cho tổng hợp, và Claude Sonnet cho kiến trúc phức tạp.",
        "arguments": []
    }
]

# ==========================================
# 4. TOOL EXECUTION HANDLER
# ==========================================

def execute_tool(name: str, args: dict) -> dict:
    """Thực thi tool nghiệp vụ và trả về kết quả chuẩn MCP content block."""
    if name not in TOOL_LOOKUP:
        return {
            "content": [{"type": "text", "text": f"Lỗi: Không tìm thấy tool '{name}'"}],
            "isError": True
        }

    try:
        # 1. get_live_quota
        if name == "get_live_quota":
            profile_id = args.get("profile_id", "owner_default")
            force = bool(args.get("force_refresh", True))
            quota_res = db.fetch_live_google_quota(profile_id, force=force)
            if quota_res:
                g_q, a_q = quota_res
                data = {
                    "ok": True,
                    "source": "cloudcode_api_live",
                    "gemini": g_q,
                    "claude": a_q
                }
            else:
                g_q, a_q = db.get_quota_telemetry(profile_id)
                data = {
                    "ok": True,
                    "source": "log_telemetry_fallback",
                    "gemini": g_q,
                    "claude": a_q
                }
            return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}], "isError": False}

        # 2. list_google_accounts
        if name == "list_google_accounts":
            profiles = db.get_oauth_profiles()
            return {"content": [{"type": "text", "text": json.dumps({"profiles": profiles}, ensure_ascii=False, indent=2)}], "isError": False}

        # 3. switch_google_account
        if name == "switch_google_account":
            account_id = args.get("account_id")
            session_id = args.get("session_id", "gw-lead-agy")
            account_label = db.update_tmux_account(session_id, account_id)
            db.append_tmux_output(session_id, f"auth switch --account='{account_label}'", f"Đã chuyển cấu hình phiên sang: {account_label}")
            return {"content": [{"type": "text", "text": json.dumps({"status": "account_updated", "session_id": session_id, "account_label": account_label}, ensure_ascii=False, indent=2)}], "isError": False}

        # 4. get_oauth_login_url
        if name == "get_oauth_login_url":
            profile_id = args.get("profile_id", "profile1")
            custom_path = args.get("custom_path", "")
            res = db.start_oauth_login(profile_id, custom_path)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 5. list_swarm_workers
        if name == "list_swarm_workers":
            project_id = args.get("project_id", "PRJ-GEN-WORKPLACE")
            sessions = db.get_tmux_sessions(project_id)
            return {"content": [{"type": "text", "text": json.dumps({"workers": sessions, "count": len(sessions)}, ensure_ascii=False, indent=2)}], "isError": False}

        # 6. send_worker_directive
        if name == "send_worker_directive":
            session_id = args.get("session_id")
            command = (args.get("command") or "").strip()
            key = (args.get("key") or "").strip()
            if not session_id or not (command or key):
                return {"content": [{"type": "text", "text": "Thiếu session_id hoặc (command/key)"}], "isError": True}

            allowed, reason = directive_guard.guard(session_id, command, key)
            db.log_directive_audit(session_id, "mcp:send_worker_directive", key or command, allowed, reason)
            if not allowed:
                return {"content": [{"type": "text", "text": json.dumps({"status": "rejected", "session_id": session_id, "reason": reason}, ensure_ascii=False, indent=2)}], "isError": True}

            payload = key if key else command
            tmux_success = False
            try:
                cmd_args = ["tmux", "send-keys", "-t", session_id, payload]
                if not key:
                    cmd_args.append("Enter")
                res = subprocess.run(cmd_args, capture_output=True, text=True, timeout=2.0)
                tmux_success = (res.returncode == 0)
            except Exception:
                pass

            log_cmd = f"^[KEY: {key}]" if key else command
            out_msg = f"Đã gửi trực tiếp vào tmux qua send-keys ({log_cmd})." if tmux_success else f"Đã ghi nhận tín hiệu '{log_cmd}' vào runtime."
            db.append_tmux_output(session_id, log_cmd, out_msg)
            return {"content": [{"type": "text", "text": json.dumps({"status": "dispatched", "session_id": session_id, "command": log_cmd, "tmux_real": tmux_success}, ensure_ascii=False, indent=2)}], "isError": False}

        # 7. manage_worker_lifecycle
        if name == "manage_worker_lifecycle":
            action = args.get("action")
            session_id = args.get("session_id", "all")
            results = db.manage_tmux_swarm_lifecycle(action, session_id, "PRJ-GEN-WORKPLACE")
            return {"content": [{"type": "text", "text": json.dumps({"status": "ok", "action": action, "target": session_id, "results": results}, ensure_ascii=False, indent=2)}], "isError": False}

        # 8. get_worker_terminal_output
        if name == "get_worker_terminal_output":
            session_id = args.get("session_id")
            lines = int(args.get("lines", 60))
            try:
                res = subprocess.run(["tmux", "capture-pane", "-t", session_id, "-p", "-S", f"-{lines}"],
                                     capture_output=True, text=True, timeout=2.0)
                if res.returncode == 0:
                    raw_lines = res.stdout.splitlines()
                    while raw_lines and not raw_lines[-1].strip():
                        raw_lines.pop()
                    terminal_text = "\n".join(raw_lines)
                else:
                    terminal_text = f"Không kết nối được tmux pane '{session_id}': {res.stderr.strip()}"
            except Exception as e:
                terminal_text = f"Lỗi capture-pane: {str(e)}"

            return {"content": [{"type": "text", "text": terminal_text}], "isError": False}

        # 9. post_warroom_message
        if name == "post_warroom_message":
            msg = args.get("message", "")
            author = args.get("author", "AI Agent")
            tag = args.get("tag", "Directive")
            channel = args.get("channel_id", "war_room")
            res = db.post_warroom_message("PRJ-GEN-WORKPLACE", channel, author, msg, tag)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 10. get_warroom_messages
        if name == "get_warroom_messages":
            channel = args.get("channel_id", "war_room")
            limit = int(args.get("limit", 30))
            messages = db.get_warroom_messages(channel, "PRJ-GEN-WORKPLACE", limit)
            return {"content": [{"type": "text", "text": json.dumps({"channel_id": channel, "count": len(messages), "messages": messages}, ensure_ascii=False, indent=2)}], "isError": False}

        # 11. list_kanban_tasks
        if name == "list_kanban_tasks":
            conv_id = args.get("conv_id", "conv-gen-core-01")
            todos = db.get_gen_session_todos(conv_id)
            return {"content": [{"type": "text", "text": json.dumps({"conversation_id": conv_id, "count": len(todos), "tasks": todos}, ensure_ascii=False, indent=2)}], "isError": False}

        # 12. create_kanban_task
        if name == "create_kanban_task":
            conv_id = args.get("conv_id", "conv-gen-core-01")
            title = args.get("title", "")
            desc = args.get("description", "")
            priority = args.get("priority", "high")
            assigned = args.get("assigned_agent", "Gen Core")
            raw_checklist = args.get("checklist") or []
            # Chuyển checklist strings thành dạng object nếu cần
            checklist_items = []
            for idx, c in enumerate(raw_checklist):
                checklist_items.append({"id": f"chk-{int(time.time()*1000)}-{idx}", "text": str(c), "done": False})
            res = db.save_gen_session_todo(conv_id, None, title, desc, "todo", priority, assigned, checklist_items, "", 0, "owner-ryan")
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 13. claim_task
        if name == "claim_task":
            session_id = args.get("session_id")
            task_id = args.get("task_id")
            project_id = args.get("project_id", "PRJ-GEN-WORKPLACE")
            res = db.claim_task(session_id, task_id, project_id)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 14. complete_task
        if name == "complete_task":
            session_id = args.get("session_id")
            task_id = args.get("task_id")
            evidence = args.get("evidence_ref")
            verified_by = args.get("verified_by", "Lead Architect")
            res = db.complete_task(session_id, task_id, evidence, verified_by, "PRJ-GEN-WORKPLACE")
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 15. update_task_checklist
        if name == "update_task_checklist":
            conv_id = args.get("conv_id", "conv-gen-core-01")
            task_id = args.get("task_id")
            item_id = args.get("item_id")
            done = bool(args.get("done"))
            res = db.toggle_gen_session_todo_checklist_item(conv_id, task_id, item_id, done)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 16. gen_chat
        if name == "gen_chat":
            msg = args.get("message", "")
            conv_id = args.get("conv_id", "conv-gen-core-01")
            model = args.get("model", "Gemini 3.1 Pro (High)")
            author = args.get("author", "AI Agent")
            account = args.get("account", "owner_default")
            res = db.send_gen_chat(conv_id, author, msg, model, account)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 17. list_conversations
        if name == "list_conversations":
            project_id = args.get("project_id", "PRJ-GEN-WORKPLACE")
            convs = db.get_gen_conversations(project_id)
            return {"content": [{"type": "text", "text": json.dumps({"conversations": convs}, ensure_ascii=False, indent=2)}], "isError": False}

        # 18. create_conversation
        if name == "create_conversation":
            title = args.get("title", "Cuộc trò chuyện mới")
            model = args.get("model", "Gemini 3.1 Pro (High)")
            account = args.get("account", "owner_default")
            res = db.create_gen_conversation("PRJ-GEN-WORKPLACE", title, model, account, "owner-ryan")
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 19. get_conversation_messages
        if name == "get_conversation_messages":
            conv_id = args.get("conv_id")
            msgs = db.get_gen_messages(conv_id)
            return {"content": [{"type": "text", "text": json.dumps({"messages": msgs}, ensure_ascii=False, indent=2)}], "isError": False}

        # 20. compact_conversation
        if name == "compact_conversation":
            conv_id = args.get("conv_id")
            manual = bool(args.get("manual", True))
            res = db.compact_gen_conversation(conv_id, manual=manual)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 21. list_notes
        if name == "list_notes":
            conv_id = args.get("conv_id")
            notes = db.get_gen_notes("PRJ-GEN-WORKPLACE", conv_id)
            return {"content": [{"type": "text", "text": json.dumps({"notes": notes}, ensure_ascii=False, indent=2)}], "isError": False}

        # 22. save_note
        if name == "save_note":
            title = args.get("title", "Ghi chú mới")
            content = args.get("content", "")
            tags = args.get("tags") or []
            evidence = args.get("evidence_ref", "")
            conv_id = args.get("conv_id", "")
            note_id = args.get("note_id")
            res = db.save_gen_note("PRJ-GEN-WORKPLACE", note_id, title, content, tags, evidence, "AI Agent", conv_id)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 23. delete_note
        if name == "delete_note":
            note_id = args.get("note_id")
            res = db.delete_gen_note(note_id, "PRJ-GEN-WORKPLACE")
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 24. read_workspace_file
        if name == "read_workspace_file":
            path_str = args.get("file_path", "")
            res = db.get_file_content_safely(path_str)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 25. create_workspace_file
        if name == "create_workspace_file":
            conv_id = args.get("conv_id", "conv-gen-core-01")
            rel_path = args.get("path", "")
            is_dir = bool(args.get("is_dir", False))
            content = args.get("content", "")
            res = db.create_gen_session_file(conv_id, rel_path, is_dir, content)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 26. list_workspace_files
        if name == "list_workspace_files":
            files = db.get_workspace_files()
            return {"content": [{"type": "text", "text": json.dumps({"files": files, "count": len(files)}, ensure_ascii=False, indent=2)}], "isError": False}

        # 27. get_system_status
        if name == "get_system_status":
            state = db.get_full_state() or {}
            status = {
                "status": "online",
                "version": "1.2-sqlite-wal",
                "timestamp": int(time.time()),
                "mcp_server": "gen-workplace v1.0.0",
                "mcp_tools_count": len(TOOLS),
                "active_agents": len(state.get("roles", [])),
                "runtimes_count": len(state.get("runtimes", [])),
                "active_project": state.get("project", {}).get("name", "gen-workplace")
            }
            return {"content": [{"type": "text", "text": json.dumps(status, ensure_ascii=False, indent=2)}], "isError": False}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Lỗi nội bộ khi thực thi tool '{name}': {str(e)}"}], "isError": True}

    return {"content": [{"type": "text", "text": f"Tool '{name}' chưa được hiện thực"}], "isError": True}

# ==========================================
# 5. MCP JSON-RPC 2.0 PROTOCOL DISPATCHER
# ==========================================

def handle_jsonrpc_request(req: dict) -> dict:
    """Xử lý một payload JSON-RPC đơn và trả về kết quả theo chuẩn Model Context Protocol."""
    if not isinstance(req, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: Body must be a JSON object"}
        }

    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    # Notification không cần trả về response nếu method là initialized
    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "initialize":
        client_version = params.get("protocolVersion", MCP_PROTOCOL_VERSION)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False}
                },
                "serverInfo": MCP_SERVER_INFO
            }
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS
            }
        }

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        if not tool_name:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Missing required parameter 'name'"}
            }
        call_result = execute_tool(tool_name, tool_args)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": call_result
        }

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resources": RESOURCES
            }
        }

    if method == "resources/read":
        uri = params.get("uri", "")
        content_text = ""
        if uri == "gen-workplace://quota":
            q_res = db.fetch_live_google_quota("owner_default", force=False)
            content_text = json.dumps(q_res or {}, ensure_ascii=False)
        elif uri == "gen-workplace://system/status":
            state = db.get_full_state() or {}
            content_text = json.dumps(state, ensure_ascii=False)
        elif uri == "gen-workplace://swarm/workers":
            sessions = db.get_tmux_sessions("PRJ-GEN-WORKPLACE")
            content_text = json.dumps(sessions, ensure_ascii=False)
        elif uri == "gen-workplace://kanban/tasks":
            todos = db.get_gen_session_todos("conv-gen-core-01")
            content_text = json.dumps(todos, ensure_ascii=False)
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Resource URI not found: {uri}"}
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": content_text
                    }
                ]
            }
        }

    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "prompts": PROMPTS
            }
        }

    if method == "prompts/get":
        prompt_name = params.get("name")
        if prompt_name == "anti_chaos_task_execution":
            text = (
                "BẠN ĐANG THỰC THI NHIỆM VỤ THEO QUY CHUẨN ANTI-CHAOS CỦA GENESIS SWARM WORKPLACE:\n"
                "1. Luôn claim_task trước khi thực hiện để giữ khóa Mutex, tránh xung đột giữa các agent.\n"
                "2. Kiểm tra hạn mức get_live_quota để chọn mô hình AI thích hợp (ưu tiên Flash cho tác vụ thường, Pro cho tổng hợp, Sonnet cho logic chuyên sâu).\n"
                "3. Khi hoàn thành, gọi complete_task và cung cấp evidence_ref bắt buộc (đường dẫn file, commit, log).\n"
                "4. Đánh dấu checklist tương ứng qua update_task_checklist."
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "description": "Quy trình thực thi nhiệm vụ chuẩn Anti-Chaos",
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": text}
                        }
                    ]
                }
            }
        elif prompt_name == "quota_optimization_sop":
            text = (
                "QUY CHUẨN TỐI ƯU HẠN MỨC QUOTA 5 GIỜ VÀ TUẦN:\n"
                "1. Cửa sổ 5h có cơ chế rolling reset. Nếu quota 5h dưới 20%, chủ động giảm nhịp gửi prompt và chuyển sang Gemini Flash.\n"
                "2. Hạn ngạch tuần dành cho các cột mốc quan trọng. Sử dụng compact_conversation khi lịch sử phiên quá 30 tin nhắn.\n"
                "3. Đổi profile tài khoản qua switch_google_account nếu tài khoản chính chạm trần hạn mức."
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "description": "Quy chuẩn tối ưu hạn ngạch 2 tầng",
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": text}
                        }
                    ]
                }
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Prompt '{prompt_name}' not found"}
            }

    # Phương thức không được hỗ trợ
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: '{method}'"}
    }

def handle_jsonrpc(raw_payload) -> (dict | list | None):
    """Điểm nhận diện tổng quát, hỗ trợ cả Single Request và Batch Requests."""
    if isinstance(raw_payload, list):
        results = []
        for item in raw_payload:
            res = handle_jsonrpc_request(item)
            if res is not None:
                results.append(res)
        return results
    elif isinstance(raw_payload, dict):
        return handle_jsonrpc_request(raw_payload)
    else:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error: invalid JSON-RPC payload"}
        }

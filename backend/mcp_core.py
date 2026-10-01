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
    from backend import auto_update
except ImportError:
    import db
    import directive_guard
    import auto_update

MCP_SERVER_INFO = {
    "name": "gen-workplace",
    "version": "1.0.0"
}

MCP_PROTOCOL_VERSION = "2024-11-05"

# Hướng dẫn "bootstrap" trả trong initialize.instructions (#19): sửa ở backend/mcp_instructions.md,
# đọc 1 lần lúc khởi động; thiếu file / file rỗng → dùng DEFAULT_MCP_INSTRUCTIONS.
MCP_INSTRUCTIONS_FILE = Path(__file__).resolve().parent / "mcp_instructions.md"

DEFAULT_MCP_INSTRUCTIONS = (
    "gen-workplace là nơi các agent ngoài phát lệnh chỉ huy vào Phòng giao ban để đội agy CLI làm việc.\n"
    "BẮT BUỘC cho mọi agent dùng gen-workplace: ghi công việc lên app để Boss theo dõi được trong chatroom và Kanban.\n"
    "1. Mỗi việc = 1 phiên: list_conversations, đã có phiên \"VIEC-<n>: <tên việc>\" thì dùng lại; chưa có thì create_conversation(title=\"VIEC-<n>: <tên việc>\", reuse_existing=true).\n"
    "2. Chia bước thành task Kanban: create_kanban_task(conv_id, viec_ref=\"VIEC-<n>\" bắt buộc); claim_task trước khi làm, update_task_checklist khi tiến triển.\n"
    "3. Ghi tiến độ vào chatroom của phiên: log_session_message(conv_id, content, author) ở mỗi mốc (bắt đầu, giao việc, kết quả, bị chặn, xong), tin ngắn kèm link Issue/PR/commit. Chỉ lưu tin, không gọi AI (đừng dùng gen_chat để ghi log).\n"
    "4. Giao việc cho agy: post_warroom_message với @<vai> mặc định THỰC THI (Làm), rồi wait_worker_result(dispatch_id) để lấy kết quả; thêm [đọc] ngay sau @vai hoặc mode=\"review\" cho việc chỉ đọc; assign_task khi giao đúng một việc trong Kanban (sửa code trong worktree TSK-n, app test + push nhánh wt/TSK-n).\n"
    "5. Đóng việc: complete_task với evidence thật (commit SHA, URL PR có thật, dispatch:<id>, file trong ~/gw-reports/).\n"
    "6. Quy trình đầy đủ: repo Genesis-ryan-84-0567536339/Brain → skills/work-style/subskills/gen-workplace-dispatch/SKILL.md.\n"
    "7. Xác thực: gọi HTTP /mcp phải kèm Authorization: Bearer <token> (thiếu token → 401). Agent điều phối dùng connector Gen-hub mcp-06594, hoặc REST /api/* không cần token (POST /api/gen/conversations/log, /api/task/assign, /api/dispatch/wait)."
)


def load_mcp_instructions(path=None) -> str:
    """Đọc nội dung instructions từ file; lỗi đọc / rỗng → DEFAULT_MCP_INSTRUCTIONS (không bao giờ trả chuỗi rỗng)."""
    try:
        text = Path(path or MCP_INSTRUCTIONS_FILE).read_text(encoding="utf-8").strip()
    except Exception:
        text = ""
    return text or DEFAULT_MCP_INSTRUCTIONS


MCP_INSTRUCTIONS = load_mcp_instructions()

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
        "name": "probe_quota",
        "description": "Chạy 1 lệnh agy tối thiểu (--mode plan -p 'ping') với profile chỉ định để cập nhật quota từ kết quả gọi THẬT (ok / 429 rate_limited + giờ hồi). Kết quả được ghi vào bảng quota_probe và dùng cho get_live_quota.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {
                    "type": "string",
                    "description": "ID tài khoản / profile OAuth (mặc định 'owner_default').",
                    "default": "owner_default"
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
        "description": "Điều khiển vòng đời phiên tmux của worker: open (mở nếu đang ngủ, không khởi động lại phiên đang chạy), pause (tạm dừng), resume (tiếp tục), hibernate (ngủ đông), wake (khởi động lại phiên), sleep_all, wake_all. Phiên tự ngủ sau GW_TMUX_IDLE_MIN phút rảnh.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Hành động điều khiển: 'open' | 'pause' | 'resume' | 'hibernate' | 'wake' | 'sleep_all' | 'wake_all'",
                    "enum": ["open", "pause", "resume", "hibernate", "wake", "sleep_all", "wake_all"]
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
        "description": "Đăng tin nhắn / chỉ thị vào Phòng giao ban (War Room). Tin có @backend, @devops, @qa hoặc @lead mặc định chạy agy chế độ MẶC ĐỊNH (Làm, sửa code): nếu tin nêu TSK-n hoặc VIEC-n của task có thật thì giao task đó (assign_task_to_role mode=build); nếu không có task thì agy chạy trong worktree riêng của vai (../gw-worktrees/gw-<vai>-agy). Thêm [đọc] ngay sau @vai (vd '@qa [đọc] xem file X') hoặc truyền mode='review' để giữ chế độ Rà soát cũ (agy --mode plan, chỉ đọc). @security và @frontend đã bỏ (29/09): tin nhắc vai đã bỏ bị từ chối với lỗi rõ ràng (code retired_role), không lưu. @Gen / @Toàn Đội không giao việc. Response có 'dispatches': [{session_id, dispatch_id, task_id, mode}] — truyền dispatch_id cho wait_worker_result để chờ kết quả.",
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
                },
                "task_id": {
                    "type": "string",
                    "description": "Gắn lần giao việc với task này (bỏ trống = mã TSK-n/VIEC-n đầu tiên trong tin, rồi task đang làm của worker)."
                },
                "mode": {
                    "type": "string",
                    "enum": ["build", "review"],
                    "description": "'build' (mặc định) = Làm: agy sửa code trong worktree (hoặc giao assign_task_to_role nếu có task); 'review' = Đọc: agy --mode plan chỉ đọc. [đọc] sau @vai cũng chọn review.",
                    "default": "build"
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

    {
        "name": "wait_worker_result",
        "description": "Chờ phía server tới khi worker làm xong một lần giao việc (tin War Room @vai, assign_task hoặc /api/swarm/dispatch) rồi trả kết quả ngay: status done/failed, exit_code, summary (kết quả rút gọn), report_path, task_id/viec_ref, evidence. Hết timeout_sec mà chưa xong → status 'running', gọi lại với cùng dispatch_id. Không cần poll get_worker_terminal_output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dispatch_id": {
                    "type": "integer",
                    "description": "ID lần giao việc: 'dispatches[].dispatch_id' do post_warroom_message trả về, 'dispatch_id' của assign_task, hoặc 'results.<sid>.dispatch_id' của POST /api/swarm/dispatch (= dispatch_log.id)."
                },
                "task_id": {
                    "type": "string",
                    "description": "Thay cho dispatch_id: chờ lần giao việc mới nhất của task này."
                },
                "session_id": {
                    "type": "string",
                    "description": "Thay cho dispatch_id: chờ lần giao việc mới nhất của worker (vd 'gw-qa-agy')."
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Số giây tối đa chờ (mặc định 60, tối đa 120).",
                    "default": 60
                }
            }
        }
    },

    # ---------------- Kanban & Task Governance ----------------
    {
        "name": "list_kanban_tasks",
        "description": "Liệt kê danh sách nhiệm vụ Kanban trong phiên làm việc, kèm trạng thái (todo, in_progress, review, done), độ ưu tiên, danh sách checklist con, agent được giao, viec_ref (mã việc Kho Ryan) và bằng chứng nghiệm thu. Mỗi thẻ có thêm: live = {dispatch_id, kind, session_id, started_at, elapsed_sec} khi có dispatch đang chạy thật cho task (null nếu không), stale = true khi in_progress mà không có dispatch nào chạy (thẻ treo — thread nền tự thu hồi về todo khi khóa quá hạn), checklist_done / checklist_total.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID cuộc trò chuyện / phiên làm việc (bỏ trống = phiên gần nhất)."
                }
            }
        }
    },
    {
        "name": "create_kanban_task",
        "description": "Tạo nhiệm vụ mới trên bảng Kanban của phiên với checklist con và độ ưu tiên. BẮT BUỘC viec_ref = mã việc trong Kho Ryan (dạng VIEC-<số>, vd VIEC-12).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID phiên làm việc (bỏ trống = phiên gần nhất)."
                },
                "title": {
                    "type": "string",
                    "description": "Tiêu đề nhiệm vụ."
                },
                "viec_ref": {
                    "type": "string",
                    "description": "Mã việc trong Kho Ryan, bắt buộc, khớp ^VIEC-[0-9]+$ (vd 'VIEC-12')."
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
                    "description": "Agent / worker được giao phụ trách (vd: 'gw-qa-agy', 'claude-dieu-phoi'). Nhận cả tên cũ assigned_to.",
                    "default": "Gen Core"
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Bí danh của assigned_agent (giữ tương thích)."
                },
                "checklist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách các đầu việc con (checklist items) dạng text."
                }
            },
            "required": ["title", "viec_ref"]
        }
    },
    {
        "name": "claim_task",
        "description": "Khóa độc quyền (claim) nhiệm vụ cho một worker (todos roadmap hoặc task Kanban phiên). Nguyên tử: task in_progress đang do worker khác giữ (khóa chưa quá hạn GW_RECLAIM_TIMEOUT_SEC) → lỗi code 'locked' kèm held_by; người đang giữ gọi lại → làm mới khóa; task đã done → lỗi code 'already_done'.",
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
        "description": "Nghiệm thu hoàn tất nhiệm vụ với bằng chứng KIỂM ĐƯỢC: commit SHA có trong repo, file không rỗng trong ~/gw-reports/ hoặc repo/worktree, dispatch:<id> (lần giao việc done, khớp task), warroom:<id> (tin trả lời của agent), hoặc URL PR GitHub có thật (kiểm qua GitHub API; không gọi được mạng → từ chối). Bằng chứng không kiểm được → lỗi, trạng thái không đổi. Chỉ người đang giữ task (claim_task) được đóng; task chưa ai claim thì ai cũng đóng được; người khác → lỗi code 'not_holder' kèm held_by, đóng thay phải force=true (ghi nhật ký task_evidence_audit). Task đã done → lỗi code 'already_done', không ghi đè trừ khi force=true (được ghi log). Đây là đường DUY NHẤT chuyển task sang done.",
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
                    "description": "Commit SHA (7-40 hex, có trong repo); file không rỗng trong ~/gw-reports/, repo hoặc worktree (tuyệt đối / tương đối repo / ~/gw-reports/...); dispatch:<id>; warroom:<id>; hoặc https://github.com/<owner>/<repo>/pull/<n> (PR có thật)."
                },
                "verified_by": {
                    "type": "string",
                    "description": "Không còn dùng: hệ thống tự đặt 'git:commit' / 'file' / 'github:pr' / 'dispatch' / 'warroom' theo loại bằng chứng.",
                    "default": ""
                },
                "force": {
                    "type": "boolean",
                    "description": "Chỉ dùng khi (a) cần sửa bằng chứng của task ĐÃ done, hoặc (b) đóng thay task đang do worker khác giữ. Cả hai đều lưu nhật ký task_evidence_audit (action override_evidence / force_close).",
                    "default": False
                },
                "reason": {
                    "type": "string",
                    "description": "Lý do ghi đè (lưu vào nhật ký khi force=true).",
                    "default": ""
                },
                "project_id": {
                    "type": "string",
                    "description": "ID dự án (mặc định 'PRJ-GEN-WORKPLACE').",
                    "default": "PRJ-GEN-WORKPLACE"
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
                    "description": "ID phiên làm việc (bỏ trống = phiên gần nhất)."
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
    {
        "name": "assign_task",
        "description": "Giao 1 task Kanban cho 1 vai agy (cùng hàm với REST POST /api/task/assign): claim task cho worker của vai rồi chạy agy nền, trả ngay dispatch_id để chờ bằng wait_worker_result. mode=\"build\" (mặc định, \"Làm\"): agy SỬA CODE với toàn quyền trên máy Fedora trong worktree riêng ../gw-worktrees/TSK-n (nhánh wt/TSK-n từ origin/main); app tự chạy py_compile + test, push nhánh wt/TSK-n, ghi nhánh / commit / kết quả test / link compare vào dispatch và phiên của task; app KHÔNG merge, điều phối tạo PR, review rồi merge. mode=\"review\" (\"Rà soát\"): như tin @vai trong war-room, agy --mode plan chỉ đọc. Lỗi (isError, kèm code + http_status): 400 thiếu / sai tham số hoặc vai đã bỏ, 404 không có task, 409 task đã done / worker khác đang giữ / đang có lần Làm chạy (code busy).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Mã task Kanban (vd 'TSK-26')."
                },
                "session_id": {
                    "type": "string",
                    "description": "Vai nhận việc: 'backend' | 'devops' | 'qa' | 'lead' hoặc session worker 'gw-<vai>-agy'."
                },
                "mode": {
                    "type": "string",
                    "enum": ["build", "review"],
                    "description": "'build' (mặc định) = Làm: agy sửa code toàn quyền trong worktree của task; 'review' = Rà soát, chỉ đọc.",
                    "default": "build"
                },
                "author": {
                    "type": "string",
                    "description": "Người giao (ghi vào tin giao việc trong war-room).",
                    "default": "AI Agent"
                }
            },
            "required": ["task_id", "session_id"]
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
                    "description": "ID phiên làm việc (bỏ trống = phiên gần nhất)."
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
        "description": "Tạo một phiên làm việc (conversation) mới trong Gen Workplace; phiên hiện ngay ở danh sách phiên trên UI. Quy ước: 1 việc = 1 phiên, tiêu đề 'VIEC-<n>: <tên việc>'. reuse_existing=true → nếu đã có phiên cùng tiêu đề thì trả phiên đó (reused=true) thay vì tạo trùng.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Tiêu đề phiên, vd 'VIEC-12: Sửa lỗi đăng nhập'."
                },
                "reuse_existing": {
                    "type": "boolean",
                    "description": "true → dùng lại phiên đã có đúng tiêu đề này (mặc định false = luôn tạo mới).",
                    "default": False
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
        "name": "log_session_message",
        "description": "Ghi 1 tin tiến độ vào chatroom của phiên (bắt đầu, giao việc, kết quả, bị chặn, xong; kèm link Issue/PR/commit). CHỈ LƯU TIN, không gọi agy/AI trả lời (khác gen_chat). Phiên phải tồn tại (tạo bằng create_conversation).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {
                    "type": "string",
                    "description": "ID phiên (conversation) cần ghi tin."
                },
                "content": {
                    "type": "string",
                    "description": "Nội dung tin, ngắn gọn, kèm link Issue/PR/commit nếu có."
                },
                "role": {
                    "type": "string",
                    "description": "'assistant' (mặc định, tin của agent) hoặc 'user'.",
                    "default": "assistant"
                },
                "author": {
                    "type": "string",
                    "description": "Tên agent ghi tin (mặc định 'AI Agent').",
                    "default": "AI Agent"
                }
            },
            "required": ["conv_id", "content"]
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
                    "description": "ID phiên làm việc (bỏ trống = phiên gần nhất)."
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
        "description": "Lấy tổng thể trạng thái hệ thống: tiến trình control plane trên host, kết nối SQLite, số lượng vai/runtime và thông tin dự án.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

# ==========================================
# 1b. METADATA TOOL — NGUỒN DUY NHẤT (#28)
# ==========================================
# Mỗi tool khai báo đúng 1 lần: nhóm chức năng (màn MCP & Kết nối), scope token được gọi, chỉ đọc hay có tác dụng phụ,
# mô tả tiếng Việt ngắn. READ_ONLY_TOOLS, annotations.readOnlyHint, quyền token theo scope (db.verify_mcp_request_auth)
# và UI đều suy ra từ bảng này — không lặp danh sách ở nơi khác.
# Tool CHỈ ĐỌC: không đổi dữ liệu, không chạy agy, không gửi gì vào tmux → UI chạy thử ngay.
# Tool CÓ TÁC DỤNG PHỤ → UI bắt nhập tham số + xác nhận trước khi gọi.

TOOL_GROUPS = [
    {"id": "kanban", "label": "Việc & Kanban"},
    {"id": "warroom", "label": "Giao ban & Worker"},
    {"id": "session", "label": "Phiên & ghi chú"},
    {"id": "files", "label": "File"},
    {"id": "account", "label": "Tài khoản & quota"},
    {"id": "system", "label": "Hệ thống"},
]

# Scope của token MCP (permissions_json). "all" = mọi tool. Tool có scope None chỉ token toàn quyền gọi được.
TOKEN_SCOPES = [
    {"id": "all", "label": "Toàn quyền", "hint": "Gọi được mọi tool"},
    {"id": "kanban", "label": "Việc & Kanban", "hint": "Tạo, nhận, cập nhật và nghiệm thu task"},
    {"id": "swarm", "label": "Giao ban & Worker", "hint": "Giao việc cho agy, đọc terminal, điều khiển worker"},
    {"id": "chat", "label": "Phiên chat", "hint": "Tạo phiên, ghi tiến độ, đọc tin, chat với Gen"},
    {"id": "files", "label": "Ghi chú, file & trạng thái", "hint": "Ghi chú, đọc/ghi file workspace, trạng thái hệ thống"},
    {"id": "quota", "label": "Tài khoản & quota", "hint": "Xem quota, đổi tài khoản Google, đăng nhập OAuth"},
]


def _m(group, scope, read_only, summary):
    return {"group": group, "scope": scope, "read_only": read_only, "summary": summary}


TOOL_META = {
    # Việc & Kanban
    "list_kanban_tasks": _m("kanban", "kanban", True, "Xem các task Kanban của một phiên, kèm trạng thái và checklist."),
    "create_kanban_task": _m("kanban", "kanban", False, "Tạo task Kanban mới gắn mã VIEC, có checklist và độ ưu tiên."),
    "claim_task": _m("kanban", "kanban", False, "Nhận (khóa) một task cho worker trước khi làm."),
    "complete_task": _m("kanban", "kanban", False, "Nghiệm thu task xong, bắt buộc có bằng chứng kiểm được."),
    "update_task_checklist": _m("kanban", "kanban", False, "Tick hoặc bỏ tick một mục checklist của task."),
    "assign_task": _m("kanban", "kanban", False, "Giao task cho agy; mode=build cho agy sửa code với toàn quyền trên máy Fedora."),
    # Giao ban & Worker
    "list_swarm_workers": _m("warroom", "swarm", True, "Xem danh sách worker agy, vai trò và trạng thái."),
    "get_worker_terminal_output": _m("warroom", "swarm", True, "Đọc màn hình terminal gần nhất của một worker."),
    "get_warroom_messages": _m("warroom", "swarm", True, "Đọc tin mới nhất trong Phòng giao ban."),
    "wait_worker_result": _m("warroom", "swarm", True, "Chờ worker làm xong một lần giao việc rồi trả kết quả."),
    "post_warroom_message": _m("warroom", "swarm", False, "Đăng tin vào Phòng giao ban; có @vai mặc định Làm, [đọc] để chỉ đọc."),
    "send_worker_directive": _m("warroom", "swarm", False, "Gửi lệnh hoặc phím thẳng vào terminal của worker."),
    "manage_worker_lifecycle": _m("warroom", "swarm", False, "Tạm dừng, tiếp tục, ngủ đông hoặc đánh thức worker."),
    # Phiên & ghi chú
    "list_conversations": _m("session", "chat", True, "Xem danh sách phiên làm việc."),
    "get_conversation_messages": _m("session", "chat", True, "Đọc toàn bộ tin nhắn của một phiên."),
    "list_notes": _m("session", "files", True, "Xem ghi chú nhanh của dự án hoặc một phiên."),
    "create_conversation": _m("session", "chat", False, "Tạo phiên mới (hoặc dùng lại phiên cùng tên)."),
    "log_session_message": _m("session", "chat", False, "Ghi 1 tin tiến độ vào phiên, không gọi AI."),
    "gen_chat": _m("session", "chat", False, "Gửi tin cho Gen trong phiên và chờ AI trả lời."),
    "compact_conversation": _m("session", "chat", False, "Nén ngữ cảnh phiên thành bản tóm tắt."),
    "save_note": _m("session", "files", False, "Tạo mới hoặc sửa một ghi chú nhanh."),
    "delete_note": _m("session", "files", False, "Xóa một ghi chú nhanh."),
    # File
    "list_workspace_files": _m("files", "files", True, "Xem cây thư mục và file trong workspace."),
    "read_workspace_file": _m("files", "files", True, "Đọc nội dung một file trong workspace."),
    "create_workspace_file": _m("files", "files", False, "Tạo hoặc ghi đè file / thư mục trong workspace của phiên."),
    # Tài khoản & quota
    "get_live_quota": _m("account", "quota", True, "Xem quota 5 giờ và quota tuần của tài khoản đang dùng."),
    "list_google_accounts": _m("account", "quota", True, "Xem các tài khoản Google / hồ sơ agy và trạng thái đăng nhập."),
    "probe_quota": _m("account", None, False, "Chạy 1 lệnh agy nhỏ để đo quota thật (tốn 1 lượt gọi)."),
    "switch_google_account": _m("account", "quota", False, "Đổi tài khoản Google cho một worker."),
    "get_oauth_login_url": _m("account", "quota", False, "Tạo link đăng nhập Google để thêm tài khoản."),
    # Hệ thống
    "get_system_status": _m("system", "files", True, "Xem trạng thái máy chủ, cơ sở dữ liệu và dự án."),
}

_missing_meta = sorted({t["name"] for t in TOOLS} ^ set(TOOL_META))
if _missing_meta:
    raise RuntimeError(f"TOOL_META lệch với TOOLS: {_missing_meta}")

READ_ONLY_TOOLS = {n for n, m in TOOL_META.items() if m["read_only"]}
TOOL_SCOPES = {n: m["scope"] for n, m in TOOL_META.items()}
for _t in TOOLS:
    # MCP tool annotations (spec 2025-03-26): client biết tool nào chỉ đọc
    _t["annotations"] = {"readOnlyHint": _t["name"] in READ_ONLY_TOOLS, "destructiveHint": False}

# db.verify_mcp_request_auth đọc scope của tool từ đây (db không import mcp_core để tránh vòng import)
db.MCP_TOOL_SCOPES.clear()
db.MCP_TOOL_SCOPES.update(TOOL_SCOPES)


def tool_catalog():
    """Danh sách tool cho màn MCP & Kết nối: tool gốc (name, description, inputSchema, annotations) + group, scope,
    read_only, summary từ TOOL_META, theo thứ tự nhóm trong TOOL_GROUPS."""
    order = {g["id"]: i for i, g in enumerate(TOOL_GROUPS)}
    items = [dict(t, **TOOL_META[t["name"]]) for t in TOOLS]
    return sorted(items, key=lambda t: (order.get(t["group"], 99), not t["read_only"]))


def valid_token_permissions(perms):
    """Scope hợp lệ cho token: id trong TOKEN_SCOPES, '*' hoặc tên tool có thật. Trả (ok, danh sách lạ)."""
    known = {s["id"] for s in TOKEN_SCOPES} | {"*"} | set(TOOL_META)
    bad = [p for p in (perms or []) if not isinstance(p, str) or p not in known]
    return (not bad, bad)


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
            profile_id = args.get("profile_id") or "owner_default"
            force = bool(args.get("force_refresh", True))
            data = db.live_quota_response(profile_id, force=force)
            return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}], "isError": False}

        # 1b. probe_quota
        if name == "probe_quota":
            profile_id = args.get("profile_id", "owner_default")
            res = db.probe_quota(profile_id)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 2. list_google_accounts
        if name == "list_google_accounts":
            profiles = db.get_oauth_profiles()
            return {"content": [{"type": "text", "text": json.dumps({"profiles": profiles}, ensure_ascii=False, indent=2)}], "isError": False}

        # 3. switch_google_account
        if name == "switch_google_account":
            account_id = args.get("account_id")
            session_id = args.get("session_id", "gw-lead-agy")
            # Kiểm tham số: gửi {} trước đây ghi account_type = NULL → /api/tmux/sessions 500
            err = db.validate_account_switch(session_id, account_id)
            if err:
                return {"content": [{"type": "text", "text": json.dumps({"error": err}, ensure_ascii=False)}], "isError": True}
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

            db.ensure_tmux_session_live(session_id)   # phiên đang ngủ → mở theo nhu cầu (#32)
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
            session_id = (args.get("session_id") or "").strip()
            if not session_id:
                return {"content": [{"type": "text", "text": json.dumps({"error": "Thiếu session_id (vd gw-qa-agy)"}, ensure_ascii=False)}], "isError": True}
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
            mode = str(args.get("mode") or "build").strip() or "build"
            res = db.post_warroom_message("PRJ-GEN-WORKPLACE", channel, author, msg, tag,
                                          task_id=str(args.get("task_id") or "").strip(), mode=mode)
            # TSK-24 (A): isError=True khi mọi vai đều lỗi (không dispatch được, chỉ có errors)
            all_failed = bool(res.get("errors")) and not res.get("dispatched")
            is_err = "error" in res or all_failed
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": is_err}


        # 10. get_warroom_messages
        if name == "get_warroom_messages":
            channel = args.get("channel_id", "war_room")
            limit = int(args.get("limit", 30))
            messages = db.get_warroom_messages(channel, "PRJ-GEN-WORKPLACE", limit)
            return {"content": [{"type": "text", "text": json.dumps({"channel_id": channel, "count": len(messages), "messages": messages}, ensure_ascii=False, indent=2)}], "isError": False}

        # 10b. wait_worker_result (#9)
        if name == "wait_worker_result":
            res = db.wait_worker_result(args.get("dispatch_id"), args.get("task_id", ""), args.get("session_id", ""),
                                        args.get("timeout_sec", db.WAIT_WORKER_DEFAULT_SEC))
            is_err = res.get("status") in ("error", "not_found")
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": is_err}

        # 11. list_kanban_tasks
        if name == "list_kanban_tasks":
            conv_id = (args.get("conv_id") or "").strip()
            if not conv_id:
                conv_id = db.default_conv_id_with_tasks() or db.default_conv_id()
            if not conv_id:
                return {"content": [{"type": "text", "text": json.dumps({"error": "Chưa có phiên chat nào; truyền conv_id hoặc tạo phiên bằng create_conversation"}, ensure_ascii=False)}], "isError": True}
            todos = db.get_gen_session_todos(conv_id)
            return {"content": [{"type": "text", "text": json.dumps({"conversation_id": conv_id, "count": len(todos), "tasks": todos}, ensure_ascii=False, indent=2)}], "isError": False}

        # 12. create_kanban_task
        if name == "create_kanban_task":
            conv_id = args.get("conv_id") or db.default_conv_id()
            if not conv_id:
                return {"content": [{"type": "text", "text": json.dumps({"error": "Chưa có phiên chat nào; truyền conv_id hoặc tạo phiên bằng create_conversation"}, ensure_ascii=False)}], "isError": True}
            title = args.get("title", "")
            desc = args.get("description", "")
            priority = args.get("priority", "high")
            # assigned_to là bí danh cũ — trước đây bị bỏ qua nên task luôn gán "Gen Core"
            assigned = (str(args.get("assigned_agent") or args.get("assigned_to") or "").strip()) or "Gen Core"
            raw_checklist = args.get("checklist") or []
            viec_ref = (args.get("viec_ref") or "").strip()
            # Chuyển checklist strings thành dạng object nếu cần
            checklist_items = []
            for idx, c in enumerate(raw_checklist):
                checklist_items.append({"id": f"chk-{int(time.time()*1000)}-{idx}", "text": str(c), "done": False})
            res = db.save_gen_session_todo(conv_id, None, title, desc, "todo", priority, assigned, checklist_items, "", 0, "owner-ryan", viec_ref=viec_ref)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

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
            force = args.get("force") in (True, 1, "1", "true", "True")
            res = db.complete_task(session_id, task_id, evidence, "", args.get("project_id") or "PRJ-GEN-WORKPLACE",
                                   force=force, reason=str(args.get("reason") or ""))
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 15. update_task_checklist
        if name == "update_task_checklist":
            conv_id = args.get("conv_id") or db.default_conv_id()
            if not conv_id:
                return {"content": [{"type": "text", "text": json.dumps({"error": "Chưa có phiên chat nào; truyền conv_id hoặc tạo phiên bằng create_conversation"}, ensure_ascii=False)}], "isError": True}
            task_id = args.get("task_id")
            item_id = args.get("item_id")
            done = bool(args.get("done"))
            res = db.toggle_gen_session_todo_checklist_item(conv_id, task_id, item_id, done)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 15b. assign_task (#61): cùng hàm với REST POST /api/task/assign (db.assign_task_to_role), không chờ agy chạy xong
        if name == "assign_task":
            if str(args.get("engine") or "").strip():
                res = {"error": "assign_task không nhận engine: chỉ giao cho agy (mode build | review)", "code": "bad_request"}
            else:
                res = db.assign_task_to_role(str(args.get("task_id") or args.get("todo_id") or ""),
                                             str(args.get("session_id") or args.get("role") or "").strip(),
                                             args.get("project_id") or "PRJ-GEN-WORKPLACE",
                                             author=str(args.get("author") or "AI Agent"), channel_id="war_room",
                                             mode=str(args.get("mode") or "build"))
            res = dict(res, http_status=db.assign_task_http_status(res))
            if "error" not in res and res.get("dispatch_id"):
                res["next"] = f"wait_worker_result(dispatch_id={res['dispatch_id']}) để chờ kết quả"
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

        # 16. gen_chat
        if name == "gen_chat":
            msg = args.get("message", "")
            conv_id = args.get("conv_id") or db.default_conv_id()
            if not conv_id:
                return {"content": [{"type": "text", "text": json.dumps({"error": "Chưa có phiên chat nào; truyền conv_id hoặc tạo phiên bằng create_conversation"}, ensure_ascii=False)}], "isError": True}
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
            model = args.get("model", "Gemini 3.1 Pro (High)")
            account = args.get("account", "owner_default")
            title = (args.get("title") or "").strip() or "Cuộc trò chuyện mới"
            reuse = args.get("reuse_existing") in (True, 1, "1", "true", "True")
            existing = db.find_gen_conversation_by_title("PRJ-GEN-WORKPLACE", title) if reuse else None
            if existing:
                res = dict(existing, reused=True)
            else:
                res = dict(db.create_gen_conversation("PRJ-GEN-WORKPLACE", title, model, account, "owner-ryan"), reused=False)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": False}

        # 18b. log_session_message (#19): chỉ lưu tin vào phiên, không gọi agy
        if name == "log_session_message":
            res = db.log_gen_message(args.get("conv_id", ""), args.get("content", ""),
                                     args.get("role") or "assistant", args.get("author") or "AI Agent")
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}], "isError": "error" in res}

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
            conv_id = args.get("conv_id") or db.default_conv_id()
            if not conv_id:
                return {"content": [{"type": "text", "text": json.dumps({"error": "Chưa có phiên chat nào; truyền conv_id hoặc tạo phiên bằng create_conversation"}, ensure_ascii=False)}], "isError": True}
            rel_path = (args.get("path") or "").strip()
            if not rel_path:
                return {"content": [{"type": "text", "text": json.dumps({"error": "Thiếu path (đường dẫn tương đối trong phiên, vd docs/ghi-chu.md)"}, ensure_ascii=False)}], "isError": True}
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
                "runtimes_count": db.count_active_tmux_sessions(),
                "active_project": state.get("project", {}).get("name", "gen-workplace"),
                "running_dispatches": db.running_dispatches_for_update(),
                "auto_update": auto_update.enabled()
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
                "serverInfo": MCP_SERVER_INFO,
                "instructions": MCP_INSTRUCTIONS
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
            todos = db.get_gen_session_todos(db.default_conv_id())
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
                "3. Khi hoàn thành, gọi complete_task và cung cấp evidence_ref kiểm được (commit SHA, file báo cáo trong ~/gw-reports/, dispatch:<id>, warroom:<id> hoặc URL PR GitHub có thật).\n"
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

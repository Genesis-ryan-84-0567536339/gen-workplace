# 🏛️ KIẾN TRÚC KỸ THUẬT & LUỒNG DỮ LIỆU (ARCHITECTURE & STATE FLOW)
> **Dự án:** `GEN-WORKPLACE`  
> **Tài liệu tham chiếu:** [`PROJECT_HANDOFF_SSOT.md`](PROJECT_HANDOFF_SSOT.md) · hợp đồng API Issue #12: [`OPERATIONAL_GUIDE.md`](OPERATIONAL_GUIDE.md) mục 3.8

---

## 1. SƠ ĐỒ LUỒNG ĐIỀU PHỐI (DATA & CONTROL FLOW)

```
[Người Dùng: Sếp Ryan]
       │
       ▼
 [Giao Diện SPA: frontend/index.html]
       │  (1) REST API Request (fetch /api/gen/chat)
       ▼
 [Control Plane: backend/main.py]
       │  (2) Xác thực & Tra cứu thông tin phiên
       ▼
 [Database Engine: backend/db.py]
       ├───► [SQLite 3 WAL: $DATA_DIR/gen-workplace.db, mặc định <repo>/data/] (Ghi nhận tin nhắn user, status)
       │
       │  (3) Kích hoạt Core Agent CLI Process
       ▼
 [call_agy_cli_turn()]
       │  (4) subprocess.run(["agy", "--output-format", "json", "--print", ...])
       │      với ENV: HOME=${HOME:-/root}, token OAuth từ ~/.gemini hoặc ~/.agy-profiles
       ▼
 [Google Antigravity Engine: agy v1.2.11]
       │  (5) Kết nối Google Cloud Code Assist / Gemini Foundation Models
       ▼
 [Phản hồi AI Thông Minh + Token Usage]
       │  (6) Trả về JSON: { "response": "...", "usage": { ... }, "conversation_id": "..." }
       ▼
 [backend/db.py]
       ├───► Cập nhật agy_conv_id & total_tokens vào gen_conversations
       ├───► Ghi nhận tin nhắn phản hồi của Gen Core vào gen_messages
       │
       │  (7) Trả JSON về trình duyệt WebApp
       ▼
 [Giao Diện Cột 3 & Cột 2]
       - Hiển thị tin nhắn sắc nét chuẩn trợ lý điều hành.
       - Cập nhật số token trên thanh Telemetry Quota Bar.
       - Trích xuất Note ID để liên kết chứng cứ nghiệm thu ở Cột 2.
```

---

## 2. QUẢN LÝ KHÔNG GIAN BỘ NHỚ THEO PHIÊN (PER-SESSION WORKSPACE ISOLATION)

Mỗi phiên chat ở Cột 1 sở hữu một không gian làm việc độc lập tại `/workspace/sessions/<conv_id>/`:

```
<repo>/workspace/sessions/        (hoặc /workspace/sessions nếu thư mục đó tồn tại)
├── conv-orchestrator/         <-- Phiên agy của Orchestrator chat (/api/orch/chat), tự tạo khi cần
└── conv-2bec3f7f/             <-- Các phiên làm việc do người dùng tạo (id ngẫu nhiên conv-<8 hex>)
```
> Từ Issue #12 không còn phiên mẫu `conv-gen-core-01` / `conv-gen-builder`, không còn tin nhắn, ghi chú hay TSK mẫu; DB mới chỉ có project, 6 vai, cấu hình tmux, hồ sơ owner và token MCP.

Khi Sếp chọn một phiên ở Cột 1:
- Cột 2 tự động tải danh sách tệp thuộc thư mục của phiên đó.
- Khung editor tự động mở tệp tin cuối cùng mà phiên đó đang thao tác (`active_file`).
- Trạng thái tab đang mở (`open_tabs`) và mã bằng chứng đang xem (`active_evidence_id`) được khôi phục 100%.

---

## 3. CƠ CHẾ NÉN LŨY TIẾN (PROGRESSIVE COMPACTION)
Khi phiên trò chuyện kéo dài hoặc khi chuyển đổi sang mô hình có cửa sổ ngữ cảnh khác nhau (ví dụ từ Pro sang Flash):
1. Hệ thống tự động gom các cặp tin nhắn cũ chưa được nén (`is_compacted = 0`).
2. Tóm tắt thành một khối `Progressive Compact Context` (`CPT-XXXX`).
3. **Bảo tồn toàn bộ Note ID (`#NOTE-xx`, `#EVT-xx`)**: Không bị mất bất kỳ mã chứng thực nào.
4. Giao diện Cột 3 cho phép bấm nút `▼ Chi tiết đã nén` để xem lại nguyên văn khi cần kiểm chứng.

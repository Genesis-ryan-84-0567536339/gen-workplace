# 📘 HỒ SƠ BÀN GIAO TOÀN DIỆN HIỆN TRẠNG DỰ ÁN (PROJECT HANDOFF SSOT)
> **Dự án:** `GEN-WORKPLACE` — Multi-Agent Swarm Orchestrator & Autonomous Console  
> **Chủ sở hữu & Nguồn Sự Thật Duy Nhất (Owner & Sole SSOT):** Sếp Ryan  
> **Phiên làm việc:** `Gen_workplace Builder` (`conv-gen-builder`)  
> **Ngày lập hồ sơ:** 26/09/2026  
> **Trạng thái hệ thống:** Sẵn sàng vận hành (Live Container, SQLite WAL Ready, Live `agy` CLI Connected)

---

## 1. NGUYÊN VĂN ĐẶC TẢ GỐC CỦA SẾP RYAN (VERBATIM SPEC)

> *"tôi muốn 1 cái công cụ dùng để phân bố công việc đa agent bạn brainstorm giúp tôi , tôi mô tả ý nghĩ của tôi như sau :*  
> *1 bảng console: Quản lý nhiều dự án - Dashboard : theo Dõi và tạo mới dự án*  
> *--trong mỗi dự án có:*  
> *- khu Implementation gồm:*  
> *  + input +file plan --> AI sinh ra --> Roadmap --> Todo list theo từng mục roadmap.*  
> *  + Hàng 01: 1 hàng 3 cột*  
> *  + sau đó sinh ra list các agent Role để owner lựa chọn loại agent cli phù hợp cho từng Role*  
> *  + có kèm instrution cho từng role (Chỉ có thể edit dự trên input chat tổng để mọi thứ thống nhất )*  
> *  + sinh ra sơ đồ live workflow từng node cho tiến trình của các agent role để các role string step by step đúng qui trình (có mô tả từng node list cho từng role và check list công việc in-out put).*  
> *- Khu Data center : repo, database, vault, file manager*  
> *- Agent runtimes : nơi các tự ghi nhật ký và tư duy - nội dung in-out put request và kết quả kết luận - hành vi trong chính chatroom của mình . và @mention trong group nhóm theo thread để nhận và báo task cho trưởng nhóm hoặc các role tiếp theo trong luồng công việc kèm file nếu có. các role khác comment theo thread phản hồi khi cần check lại hay làm rõ - các role phải thả emoji sau khi đã nhận và thay emoji tương ứng với tình trạng thực tế như cảnh báo, đánh giá tệ, xác nhận done, ? cần làm rõ, và các emoji tương quan khác.*  
> *  + trong khu có hàng kanban để theo dõi công việc của cả team - trưởng nhóm phụ trách update kanban theo live times công việc.*  
> *- khu workplace : Memory tổng ghi note trí nhớ ssot toàn dự án từ memory của từng role - được thể hiện 2 cột danh sách Memory tổng và memory từng role được sync từ memory của từng role runtimes đang chạy. memory tổng sẽ được Role trưởng nhóm tổng hợp lại update theo sự kiện đã xác thực làm ssot tham chiếu bắt buộc của mỗi runtime trong tiến trình công việc + kèm list ID sự kiện, vault, mcp, tool, crendial, file được ghi nhận và mô tả ngắn trong database như 1 nơi để các agent tra nhanh id cần tìm để lấy nhanh dữ kiện trong database hay mã nguồn mà ko cần audit toàn cục mỗi phiên chấp hành.*  
>  
> *tôi muốn dự án này khi hoản thành sẽ up lên github và cho owner có thể cài đặt trọn bộ all in one về chạy ngay bằng 1 lệnh trên giao diện hỗ trợ cài đặt TUI nhanh chóng mọi thành phần có thanh loading % quá trình cài - khi cài xong thì nó phải tự tạo icon để gọi ra webapp . tất cả repo phải cài hết vào môi trường docker để cài được trên linux , macos, window, lệnh cài đặt phải kiểm tra môi trường để đảm bảo owner đã có docker và các thứ cần thiết khác để khởi tạo , thiếu cái nào thì tải thêm cái đó về cho đủ mới cài"*

---

## 2. TỔNG THỂ KIẾN TRÚC HỆ THỐNG HIỆN HÀNH

Hệ thống được thiết kế theo mô hình **All-In-One Containerized SPA + Python Control Plane + SQLite 3 WAL + Real CLI Runtimes**.

```
+-----------------------------------------------------------------------------------------+
|                                    SẾP RYAN (OWNER)                                     |
|                            Trình duyệt WebApp: http://localhost:8888                    |
+-----------------------------------------------------------------------------------------+
                                             |
                                    [HTTP REST / JSON]
                                             v
+-----------------------------------------------------------------------------------------+
|                       CONTAINER: gen-workplace-app (Port 8888)                          |
|                                                                                         |
|  +-----------------------------------------------------------------------------------+  |
|  |                  PYTHON HTTP CONTROL PLANE DAEMON (backend/main.py)               |  |
|  |   - ThreadingHTTPServer phục vụ Single Page App frontend/index.html               |  |
|  |   - 50+ REST Endpoints: /api/gen/*, /api/tmux/*, /api/state, /api/ssot/*,...       |  |
|  +-----------------------------------------------------------------------------------+  |
|         |                                    |                                    |     |
|         v                                    v                                    v     |
|  +---------------+                 +--------------------+              +--------------+ |
|  | CƠ SỞ DỮ LIỆU |                 | CORE AGENT RUNNER  |              | SWARM TMUX   | |
|  | SQLite 3 WAL  |                 | call_agy_cli_turn  |              | 6 CHUYÊN GIA | |
|  | + FTS5 Engine |                 | /usr/local/bin/agy |              | (lead, be,   | |
|  | /app/data/*.db|                 | Google OAuth Live  |              | fe, devops,  | |
|  +---------------+                 +--------------------+              | qa, sec)     | |
|                                                                        +--------------+ |
+-----------------------------------------------------------------------------------------+
```

---

## 3. GIAO DIỆN MISSION CONTROL IDE 3 CỘT (CỘT 1 - CỘT 2 - CỘT 3)

Không gian làm việc chính của Owner và Gen Core (`#gen_workplace`) được cấu trúc thành 3 cột cố định 100% chiều cao màn hình (`height: 100vh; overflow: hidden`):

### 3.1. Cột 1: Danh Sách Phiên Chat (Sessions Sidebar)
- **Độ rộng:** Tự động điều chỉnh linh hoạt (`260px` mặc định, hỗ trợ kéo resize bằng chuột hoặc nút thu gọn `◀`).
- **Phân nhóm phiên:**
  - 📌 **Được ghim (Pinned Sessions):** Lưu các phiên chiến lược quan trọng, ví dụ `Điều Phối Tối Cao Swarm Genesis`, `Gen_workplace Builder`.
  - 🕒 **Gần đây (Recent Sessions):** Danh sách phiên sắp xếp giảm dần theo thời gian tương tác.
- **Tính năng trên mỗi thẻ phiên:**
  - Tiêu đề phiên có nút ✏️ và hỗ trợ **nhấp đúp chuột để đổi tên nhanh**.
  - Badge mô hình AI đang dùng (`Gemini 3.8`, `Gemini 3.1`, `Claude Sonnet`,...).
  - Đoạn trích tin nhắn cuối cùng + Thời gian gửi + Đếm tổng số tin nhắn.
  - Nút ghim/bỏ ghim (📌/📍) và nút xóa phiên (🗑️).
  - Thanh tìm kiếm phiên tức thời (`filterGenSessions`).

### 3.2. Cột 2: Nội Dung Phiên, Tệp Tin & Bằng Chứng Nghiệm Thu (Workspace Detail)
Đi kèm độc lập và đồng bộ 100% với phiên chat được chọn ở Cột 1:
- **Thanh chuyển đổi phạm vi (Scope Switcher):**
  - `📁 Tệp Của Phiên Này (/workspace/sessions/<conv_id>)`: Không gian riêng biệt của phiên chat hiện tại. Gồm **Cột Danh sách** (bên trái) và **Cột Chi tiết xem trước** (bên phải với Line Numbers, dung lượng, nút mở editor).
  - `🌐 Toàn Bộ Repo (/app/repo)`: Xem cây thư mục toàn diện của dự án, trạng thái Git Working Tree, Diff và Branches.
  - `📜 Đặc Tả SSOT Gốc (docs/SSOT_ORIGINAL_SPEC.md)`: Nơi nạp chỉ thị duy nhất dùng để chỉ đạo và đồng bộ toàn bộ Role.
  - `📋 Bằng Chứng & Sổ Tay (Scratchpad Notes)`: Xem chi tiết các Note ID (`#NOTE-xx`, `#EVT-xx`) được các agent trích dẫn làm bằng chứng hoàn tất công việc.

### 3.3. Cột 3: Phòng Chat Trực Tiếp Với Core Agent (Chatroom Gen Core)
- **Header:**
  - Nút thu gọn / mở rộng cột (`▶` / `◀`).
  - Badge tên phiên đang chọn (bấm vào để đổi tên) + Nút **"✏️ Đổi tên"**.
  - Dropdown chọn mô hình AI (Gemini 3.1 Pro, Gemini 3.8 Flash, Claude Sonnet 4.6,...).
  - Dropdown chọn tài khoản agy CLI (`owner_default`, `profile1`, `profile2`,...).
- **Thanh đo Quota & Token Telemetry:**
  - Hiển thị % khả dụng, RPM và TPM thời gian thực từ API.
- **Khung tin nhắn:**
  - Phân định rõ ràng giữa tin nhắn của **Ryan (Owner)** và **⚡ Gen Core (agy CLI)**.
  - Tự động nhận diện `#NOTE-xx`, `#EVT-xx` thành các badge có thể click để mở ngay chi tiết ở Cột 2.
  - Cơ chế **Progressive Compaction**: Tự động tóm tắt tin nhắn cũ khi đổi model để tiết kiệm token nhưng vẫn bảo tồn toàn bộ Note ID làm bằng chứng.

---

## 4. HẠ TẦNG CONTAINER, PROCESS RUNNER & BẢO MẬT OAUTH

### 4.1. Cấu Hình Docker Compose (`docker-compose.yml`)
- **Dịch vụ:** `gen-workplace-app` chạy trên port `8888:8888`.
- **Mounts quan trọng (tuân thủ cờ SELinux `:z`):**
  - `gen-workplace-data:/app/data:z`: Lưu trữ bền vững cơ sở dữ liệu SQLite.
  - `./workspace:/workspace:z`: Thư mục chứa các phiên làm việc và spec của các role.
  - `./backend:/app/backend:z`: Backend Python (hỗ trợ live reload khi sửa mã).
  - `./frontend:/app/frontend:ro,z`: Single Page Application HTML/JS/CSS.
  - `/workspace/.gemini:/workspace/.gemini:z`: Lưu trữ cấu hình và token xác thực Google OAuth mặc định.
  - `/workspace/.agy-profiles:/workspace/.agy-profiles:z`: Chứa các profile đăng nhập bổ sung (`profile1`, `profile2`,...).
  - `/workspace/.local/bin/agy:/usr/local/bin/agy:ro,z`: Mount trực tiếp binary Antigravity CLI v1.2.11 vào container.
  - `HOME=/workspace`: Đảm bảo `agy` CLI luôn tìm đúng thư mục chứa token xác thực.

### 4.2. Cơ Chế Gọi agy CLI Thời Gian Thực (`call_agy_cli_turn`)
Trong file `backend/db.py`:
```python
def call_agy_cli_turn(conv_id, user_message, model=None, account="owner_default"):
    # 1. Tra cứu agy_conv_id đã liên kết với phiên (duy trì mạch tư duy)
    # 2. Cấu hình môi trường (HOME=/workspace, ANTIGRAVITY_APP_DATA_DIR)
    # 3. Chạy lệnh: agy --output-format json --print <msg> --dangerously-skip-permissions --model <model> [--conversation <agy_conv_id>]
    # 4. Parse JSON trả về: response, conversation_id mới, token usage (input, output, thinking)
    # 5. Lưu conversation_id và cộng dồn token vào gen_conversations
```
- Khi `call_agy_cli_turn` thành công: Tin nhắn trả về từ AI thật 100%, có đính kèm số token thực tế.
- Nếu CLI bị timeout (>50s) hoặc bận: Tự động kích hoạt cơ chế Smart Fallback để bảo đảm trải nghiệm không bị gián đoạn.

---

## 5. CƠ SỞ DỮ LIỆU SQLITE 3 WAL & BẢNG THAM CHIẾU (SCHEMA SSOT)

Cơ sở dữ liệu được đặt tại `/app/data/gen-workplace.db` (chế độ `PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;`).

### Các Bảng Cốt Lõi:
1. **`projects`**: Quản lý đa dự án (`id`, `name`, `repo_path`, `branch`, `plan_file`, `status`).
2. **`roadmaps`**: Phân rã mục tiêu thành các giai đoạn (`id`, `project_id`, `title`, `todos_count`, `status`).
3. **`todos`**: Nhiệm vụ chi tiết (`id`, `roadmap_id`, `project_id`, `title`, `assigned_role`, `status`, `assigned_to`, `locked_at`, `evidence_ref`). Hỗ trợ Mutex Lock chống xung đột giữa các agent.
4. **`tmux_sessions`**: Quản lý 6 phiên chuyên gia Swarm (`id`, `role`, `project_id`, `status`, `pid`, `account_label`, `profile_dir`, `task_status`, `progress_percent`).
5. **`gen_conversations`**: Các phiên chat với Core Agent (`id`, `project_id`, `title`, `model`, `account_profile`, `is_pinned`, `active_tab`, `active_file`, `open_tabs_json`, `active_evidence_id`, `agy_conv_id`, `total_tokens`).
6. **`gen_messages`**: Lịch sử tin nhắn (`id`, `conversation_id`, `author`, `role`, `content`, `model`, `note_ids_json`, `is_compacted`, `compact_id`).
7. **`gen_scratchpad_notes`**: Sổ tay bằng chứng (`id`, `project_id`, `conversation_id`, `title`, `body`, `status`).
8. **`quick_catalog`**: Virtual table FTS5 hỗ trợ tra cứu nhanh ID dữ kiện toàn cục (`id`, `cat_type`, `title`, `description`, `source_ref`, `data_json`).

---

## 6. HỆ THỐNG 6 CHUYÊN GIA SWARM & PHÂN CÔNG TRÁCH NHIỆM

Mỗi vai trò vận hành trong một phiên Tmux riêng biệt bên trong container:

| Mã Phiên Tmux | Vai Trò (Role) | CLI Engine Tiêu Chuẩn | Trách Nhiệm Cốt Lõi & Ranh Giới Whitelist |
|---|---|---|---|
| `gw-lead-agy` | **Lead Architect** | `Gemini CLI (agy --effort high)` | Bảo tồn SSOT, phân rã DAG Roadmap/Todos, thẩm định bằng chứng, ký duyệt nghiệm thu. |
| `gw-backend-agy` | **Backend & DB Specialist** | `Gemini CLI (agy --mode accept-edits)` | Quản trị SQLite WAL, REST API Control Plane, Task Mutex Lock, Process Runner kết nối CLI. |
| `gw-frontend-agy` | **Frontend Specialist** | `Gemini CLI (agy)` | Giao diện Mission Control SPA (Nocturne Slate), quản trị trạng thái 3 cột, stream terminal. |
| `gw-devops-agy` | **DevOps & Packaging** | `Gemini CLI (agy --agent devops)` | Docker Compose cờ `:z`, TUI installer, bash scripts, desktop icon launcher. |
| `gw-qa-agy` | **QA Tester** | `Gemini CLI (agy)` | Tự động hóa kiểm thử cross-platform, test API /api/status, stress test, bảo đảm 0-error. |
| `gw-security-agy` | **Security Auditor** | `Codex Security CLI / agy` | Quét mã nguồn, bảo vệ Vault, cô lập token OAuth PKCE, kiểm toán lỗ hổng bảo mật. |

---

## 7. CÁC ĐIỂM KỸ THUẬT QUAN TRỌNG ĐÃ ĐƯỢC XỬ LÝ & TỐI ƯU

1. **Khắc phục lỗi mất danh sách phiên khi F5 (Page Reload):**
   - Đã cô lập hàm khởi tạo `initGenWorkplace()` trong khối `try-catch` ưu tiên số 1.
   - Thêm bộ kiểm tra an toàn chống lỗi `TypeError: Cannot set properties of null (setting 'innerHTML')` đối với tất cả các phần tử DOM trên trang.
2. **Khắc phục phản hồi robot / máy móc:**
   - Thay thế toàn bộ logic sinh chuỗi tĩnh bằng bộ điều phối gọi trực tiếp `agy` CLI thời gian thực.
   - Thiết lập chuẩn mực giao tiếp: Xưng *"Em"* — Gọi *"Sếp Ryan"*, tự nhiên, quyết đoán, đi thẳng vào giải pháp kỹ thuật.
3. **Tính năng đổi tên tiêu đề phiên:**
   - Hỗ trợ đổi tên bằng 3 cách: Bấm nút ✏️ trên thẻ Cột 1, nút ✏️ Đổi tên trên header Cột 3, hoặc gõ trực tiếp trong chat *"đổi tên phiên này thành X"*.
4. **Bảo toàn ràng buộc khóa ngoại (Foreign Key Integrity):**
   - `send_gen_chat` tự động kiểm tra và khởi tạo bản ghi trong `gen_conversations` trước khi ghi nhận tin nhắn, ngăn chặn hoàn toàn lỗi `sqlite3.IntegrityError`.

---

## 8. DANH MỤC REST API ENDPOINTS CHÍNH

- `GET /api/status`: Kiểm tra trạng thái sức khỏe của hệ thống và CSDL.
- `GET /api/state`: Lấy toàn bộ trạng thái sống của dự án (kanban, todos, roadmap, ssot, roles).
- `GET /api/gen/conversations`: Lấy danh sách toàn bộ phiên chat và thông tin context.
- `POST /api/gen/conversations/create`: Tạo phiên chat mới.
- `POST /api/gen/conversations/update`: Đổi tiêu đề hoặc cấu hình của phiên.
- `POST /api/gen/conversations/context`: Lưu trạng thái tệp đang mở, tab đang chọn của phiên.
- `POST /api/gen/conversations/delete`: Xóa phiên chat.
- `GET /api/gen/messages?conv_id=...`: Lấy lịch sử tin nhắn của phiên.
- `POST /api/gen/chat`: Gửi tin nhắn và kích hoạt Core Agent `agy` CLI.
- `GET /api/gen/session/files?conv_id=...`: Lấy cây thư mục tệp chuyên biệt của phiên.
- `POST /api/gen/session/file/create`: Tạo tệp mới trong thư mục phiên.
- `POST /api/gen/session/file/delete`: Xóa tệp trong phiên.
- `GET /api/file/content?path=...`: Đọc nội dung tệp tin với bộ đếm dòng và định dạng code.
- `POST /api/tmux/send`: Gửi lệnh hoặc phím điều khiển vào phiên chuyên gia Tmux.
- `GET /api/tmux/sessions`: Lấy trạng thái và dòng output mới nhất của 6 chuyên gia Swarm.
- `POST /api/task/claim` & `POST /api/task/complete`: Nhận và nghiệm thu nhiệm vụ kèm bằng chứng (Evidence Hash).

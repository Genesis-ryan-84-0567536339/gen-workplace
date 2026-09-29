# SSOT — ĐẶC TẢ GỐC DỰ ÁN GEN-WORKPLACE
> **Tài liệu nguồn sự thật duy nhất (Single Source of Truth - SSOT)**  
> **Phiên bản:** 1.0-ORIGINAL  
> **Chủ sở hữu (Owner):** Ryan  
> **Ngày khởi tạo:** 24/09/2026  
> **Mã dự án:** `GEN-WORKPLACE`

---

## 1. NGUYÊN VĂN MÔ TẢ ĐẶC TẢ GỐC CỦA OWNER (VERBATIM SPEC)

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

## 2. PHÂN TÁCH KIẾN TRÚC VÀ CÁC THÀNH PHẦN CỐT LÕI (DECONSTRUCTED SPEC)

### 2.1. Phân Vùng 1: Dashboard Quản Lý Đa Dự Án (Multi-Project Management)
- Quản trị nhiều dự án độc lập trong cùng một workspace.
- Theo dõi chỉ số toàn hệ thống: Trạng thái cluster, số lượng Agent Online, tiến độ Roadmap %, số lượng SSOT Fact Keys.
- Chức năng tạo mới dự án: Thiết lập tên, repo path, lead orchestrator, ngân sách token và plan ban đầu.
- Project Switcher: Chuyển đổi ngữ cảnh workspace tức thì giữa các dự án.

### 2.2. Phân Vùng 2: Khu Implementation (3 Hàng Nghiêm Ngặt)
- **Hàng 01 (1 hàng 3 cột)**:
  - *Cột 1*: **Input + File Plan & Master Steering Chat**: Điểm nạp chỉ thị duy nhất dùng để chỉ đạo và đồng bộ toàn bộ Role (đảm bảo tính nhất quán SSOT). Hỗ trợ upload file plan `.md`, PRD.
  - *Cột 2*: **AI Sinh Ra &rarr; Roadmap**: Phân rã mục tiêu thành các giai đoạn (Milestones/Phases) có điều kiện tiên quyết và trạng thái (*Done, In Progress, Queued*).
  - *Cột 3*: **Todo List theo Roadmap**: Danh sách task chi tiết tương ứng với Phase đang chọn, gán Role phụ trách, phân định CLI và trạng thái.
- **Hàng 02**: **Bảng Agent Roles & CLI Engine Selector**:
  - Danh sách các Role chuyên trách: *Lead Architect / Orchestrator*, *Backend Specialist*, *Frontend Specialist*, *DevOps & Infra Engineer*, *QA Tester*, *Security Auditor* (vai Security Auditor (#34) và Frontend Specialist (#39) đã bỏ từ 29/09 theo quyết định của Boss — VIEC-12; việc giao diện giao cho Backend; phần đặc tả gốc giữ nguyên để tra cứu).
  - Owner trực tiếp lựa chọn loại CLI Engine phù hợp cho từng Role:
    - `Gemini CLI (agy)`
    - `Claude Code CLI`
    - `Cursor CLI`
    - `OpenAI Codex CLI`
    - `Grok CLI`
  - **Quy tắc Khóa Instruction**: Không sửa trực tiếp tại bảng Role. Mọi chỉnh sửa chỉ được thực hiện thông qua **Input Chat Tổng** để toàn dự án đồng bộ theo SSOT.
- **Hàng 03**: **Sơ Đồ Live Workflow Từng Node**:
  - Canvas đồ thị trực quan xâu chuỗi step-by-step giữa các node (từ nạp spec đến đóng gói release).
  - Chi tiết từng Node:
    - Role đảm nhiệm.
    - **Checklist INPUT bắt buộc**.
    - **Checklist OUTPUT tiêu chuẩn nghiệm thu**.
    - Checkpoint & Handoff tiếp theo.

### 2.3. Phân Vùng 3: Khu Data Center
- **Repository Hub**: Theo dõi git branches, commits, worktrees, tuân thủ nghiêm ngặt quy tắc Genesis Brain (*Issue &rarr; Branch riêng &rarr; PR &rarr; Label 'Đã xác nhận' &rarr; Merge*).
- **Database Explorer**: Bảng tham chiếu schema PostgreSQL, Redis Streams, Vector store (1536-dim embeddings), tra cứu nhanh bằng mã định danh ID.
- **Vault & Secrets**: Quản lý bí mật bảo mật an toàn theo nguyên tắc: Runtime chỉ nhận *ID + Scope + Quyền*, giá trị thực chỉ inject tại execution boundary.
- **File Manager & Artifacts**: Cây thư mục tệp tin, spec, PRD và artifact nghiệm thu.

### 2.4. Phân Vùng 4: Agent Runtimes & Điều Hành Swarm
- **Chatroom Đa Chế Độ Cho Từng Role**:
  - *Nhật ký tư duy (Thinking Process)*: Ghi lại từng bước phân tích, Self-Reflection, đánh giá rủi ro và kết luận hành động.
  - *Vào / Ra (In-Out)*: Hiển thị Request, Input Contract, Output Artifact và Kết luận.
  - *Live Terminal CLI Stream*: Stream dòng lệnh shell thời gian thực từ tiến trình CLI thực tế.
- **Swarm Group Chat theo Thread & @Mention**:
  - Nhận và bàn giao task qua @mention, đính kèm file schema/diff.
  - Bình luận phản hồi dạng Thread khi cần check lại hoặc yêu cầu làm rõ.
  - **Hệ thống Emoji Status Thời Gian Thực**:
    - 📥 `Đã nhận task`
    - ⚠️ `Cảnh báo nguy cơ`
    - 👎 / ❌ `Đánh giá tệ / Fail test`
    - ✅ `Xác nhận Hoàn thành (Done)`
    - ❓ `Cần làm rõ (kèm thread câu hỏi cho Trưởng nhóm)`
    - 👀 `Đang xem xét`, ⏳ `Đang xử lý`, 🔥 `Khẩn cấp / Blocker`, 🚀 `Đã release`
- **Hàng Live Kanban Toàn Đội**:
  - 4-5 cột: *Backlog, In Queue, Agent Running ⚙️, Review & Clarify ❓, Done & Verified ✅*.
  - **Trưởng nhóm (Lead Orchestrator) trực tiếp phụ trách cập nhật Kanban theo live times công việc**.

### 2.5. Phân Vùng 5: Khu Workplace (Memory & SSOT Toàn Dự Án)
- **Bố Cục 2 Cột Memory Song Song**:
  - *Cột 1*: **Memory Tổng (Master SSOT Toàn Dự Án)**: Do Trưởng nhóm tổng hợp lại từ các sự kiện đã xác thực, đóng băng làm nguồn tham chiếu bắt buộc cho mọi runtime trước khi chấp hành, đảm bảo 0 conflict.
  - *Cột 2*: **Memory Từng Role**: Đồng bộ live từ memory của từng CLI runtime đang hoạt động, có nút gộp thẩm định sang SSOT.
- **Catalog Tra Cứu Nhanh Dữ Kiện Toàn Cục (Quick ID Lookup Database)**:
  - Cho phép các Agent tra nhanh mã ID để lấy ngay dữ kiện trong database hay mã nguồn mà **không cần audit toàn cục mỗi phiên chấp hành**, tiết kiệm token tối đa.
  - Phân loại ID:
    - 🏷️ `EVT-XXX`: ID Sự kiện đã xác thực
    - 🔐 `SEC-XXX` / `VLT-XXX`: ID Vault & Secrets
    - 🛠️ `MCP-XXX` / `TOOL-XXX`: ID MCP Tools & CLI commands
    - 📁 `FILE-XXX`: ID File & Contracts
    - 🗄️ `TBL-XXX` / `DB-XXX`: ID Database Schemas
    - 🔑 `CRD-XXX`: ID Credentials
- **Event Register**: Bảng thẩm định sự kiện của Trưởng nhóm: xác thực nâng thành SSOT hoặc yêu cầu Role làm rõ.

---

## 3. TIÊU CHUẨN ĐÓNG GÓI, CÀI ĐẶT & TRIỂN KHAI (DISTRIBUTION & INSTALL SPEC)

1. **Chuẩn Bị Lên GitHub**:
   - Kho mã nguồn chuẩn Git, branch `main`.
   - Có cấu trúc modular: `frontend/`, `backend/`, `docs/`, `docker/`, `assets/`.
2. **Cài Đặt All-In-One 1 Lệnh (One-Command Installer)**:
   - Chạy qua curl/bash: `curl -fsSL https://.../install.sh | bash` hoặc `./install.sh`.
   - Giao diện cài đặt **TUI (Terminal User Interface)** trực quan:
     - Giao diện đồ họa terminal (ANSI colors / Text UI).
     - Thanh đo phần trăm tiến độ cài đặt (Loading Progress Bar %).
     - Mô tả từng bước rõ ràng.
3. **Tự Động Kiểm Tra Môi Trường & Bổ Sung Phần Mềm**:
   - Kiểm tra hệ điều hành (Linux, macOS, Windows WSL).
   - Kiểm tra Docker Engine & Docker Compose daemon.
   - Kiểm tra Git, Curl, Python.
   - Nếu phát hiện thiếu thành phần nào: Tự động tải/cài đặt (qua package manager `apt`, `dnf`, `brew`, `winget` hoặc binary standalone) cho đến khi đủ điều kiện mới tiến hành cài đặt.
4. **Môi Trường Container Hóa Toàn Diện (Docker Multi-Platform)**:
   - Mọi dịch vụ chạy khép kín trong Docker container (Frontend, Backend, Redis/DB cache, Agent CLI connectors).
   - Tương thích 100% trên Linux, macOS và Windows (Docker Desktop / WSL2).
5. **Tự Động Tạo Desktop Icon Gọi WebApp**:
   - Khi hoàn tất cài đặt, script tự động sinh file launcher:
     - Linux: Tạo file `.desktop` trong `~/.local/share/applications/` và `~/Desktop/Gen-Workplace.desktop` kèm icon.
     - macOS: Tạo app shortcut trong `/Applications` hoặc Desktop.
     - Windows: Tạo shortcut `.lnk` hoặc batch launcher trên Desktop.
   - Người dùng chỉ cần click đúp vào icon là tự động khởi động dịch vụ và mở ngay trình duyệt vào webapp.

# GEN-WORKPLACE · Multi-Agent Swarm Orchestrator & Autonomous Console

> **Bảng điều khiển và bộ công cụ phân bổ công việc đa tác nhân (Multi-Agent Swarm Workbench) chuyên biệt cho quy trình phát triển phần mềm tự động hóa cao cấp.**

---

## 🌟 TỔNG QUAN HỆ THỐNG
**Gen-workplace** cung cấp môi trường tích hợp (Console Workbench) giúp Owner quản trị nhiều dự án, phân rã mục tiêu tự động từ Plan & Chat tổng, phân công Role và chỉ định CLI Engine chuyên biệt (`Gemini CLI - agy`, `Claude Code CLI`, `Cursor CLI`, `Codex Security CLI`), theo dõi tiến trình qua Live Workflow Graph từng node, điều hành qua Swarm Chatroom với @mention/reaction emojis, và quản trị bộ nhớ Single Source of Truth (SSOT).

Tài liệu đặc tả nguồn gốc chính thức: [`docs/SSOT_ORIGINAL_SPEC.md`](docs/SSOT_ORIGINAL_SPEC.md).

---

## 🚀 CÀI ĐẶT 1 LỆNH (ONE-COMMAND ALL-IN-ONE INSTALLER)

Hỗ trợ tự động trên **Linux**, **macOS** và **Windows (WSL2 / Docker Desktop)**.

### Cách 1: Chạy từ mã nguồn repo
```bash
cd /workspace/LinuxDataA/gen-workplace
./install.sh
```

### Cách 2: Chạy trực tiếp từ GitHub (Khi đã push lên repo)
```bash
curl -fsSL https://raw.githubusercontent.com/<OWNER>/gen-workplace/main/install.sh | bash
```

### ⚡ Các tính năng nổi bật của bộ cài đặt TUI:
- **Giao diện Terminal User Interface (TUI)** chuyên nghiệp, có **thanh loading % tiến độ cài đặt**.
- **Tự động Pre-flight Check môi trường**: Kiểm tra OS, Git, Curl, Docker Engine và Docker Compose. Nếu thiếu thành phần nào, kịch bản tự động tải và cấu hình bổ sung.
- **Môi trường Docker hóa 100%**: Mọi thành phần (Backend, Frontend, Agent Runtimes) chạy an toàn và độc lập trong container Docker.
- **Tự động tạo Desktop Icon Launcher**: Tự sinh shortcut trên màn hình Desktop (`Gen-workplace.desktop` trên Linux, `.command` trên macOS, `.bat` trên Windows) để Owner nhấp đúp là gọi ra WebApp ngay lập tức.

---

## 🏗️ CẤU TRÚC DỰ ÁN
```
gen-workplace/
├── assets/
│   └── icon.svg                 # Biểu tượng nhận diện ứng dụng
├── backend/
│   ├── db.py                    # SQLite 3 WAL Core DB & FTS5 Fast Catalog
│   └── main.py                  # Control Plane API & CLI Runner
├── frontend/
│   └── index.html               # Giao diện WebApp chuẩn phong cách v1.1
├── docs/
│   └── SSOT_ORIGINAL_SPEC.md    # Nguồn sự thật duy nhất (Mô tả gốc của Owner)
├── docker-compose.yml           # Cấu hình container đa nền tảng
├── Dockerfile                   # Đóng gói image độc lập
├── install.sh                   # Script khởi động 1 lệnh
├── installer_tui.py             # Bộ cài đặt giao diện TUI có thanh % loading
└── README.md                    # Hướng dẫn dự án
```

---

## 📱 TRUY CẬP ỨNG DỤNG SAU KHI CÀI ĐẶT
- **Trình duyệt máy chủ**: [http://localhost:8888](http://localhost:8888)
- **Truy cập mạng nội bộ**: `http://<IP_MAY_CHU>:8888`
- **Icon Desktop**: Nhấp đúp vào biểu tượng **"Gen-workplace Console"** trên Desktop.

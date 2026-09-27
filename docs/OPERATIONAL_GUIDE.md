# 🛠️ CẨM NANG VẬN HÀNH & KIỂM THỬ (OPERATIONAL GUIDE)
> **Dự án:** `GEN-WORKPLACE`  
> **Dành cho:** Developer & Builder Agent tại phiên `Gen_workplace Builder`

---

## 1. CÁC LỆNH VẬN HÀNH CỐT LÕI (RUNBOOK)

### 1.1. Khởi Động & Khởi Động Lại Hệ Thống
```bash
# Khởi động dịch vụ nền qua Docker Compose
docker compose up -d

# Khởi động lại container khi cập nhật backend hoặc cấu hình
docker restart gen-workplace-app

# Kiểm tra log runtime của Control Plane
docker logs -f --tail 50 gen-workplace-app
```

### 1.2. Kiểm Tra Sức Khỏe API & Kết Nối CSDL
```bash
# Kiểm tra API status
curl -s http://localhost:8888/api/status | python3 -m json.tool

# Kiểm tra danh sách phiên chat
curl -s http://localhost:8888/api/gen/conversations | python3 -m json.tool

# Kiểm tra danh sách 6 chuyên gia Tmux
curl -s http://localhost:8888/api/tmux/sessions | python3 -m json.tool
```

### 1.3. Kiểm Thử Cú Pháp Trước Khi Bàn Giao
```bash
# Kiểm thử cú pháp Python backend
python3 -m py_compile backend/db.py
python3 -m py_compile backend/main.py

# Kiểm thử cú pháp JavaScript frontend
node -e '
const fs = require("fs");
const html = fs.readFileSync("frontend/index.html", "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/g);
if (match) {
  let combined = match.map(s => s.replace(/<\/?script>/g, "")).join("\n;\n");
  fs.writeFileSync("/tmp/check_syntax.js", combined);
  require("child_process").execSync("node --check /tmp/check_syntax.js");
  console.log("JavaScript syntax is VALID!");
}
'
```

### 1.4. Kiểm Thử Trình Duyệt Bằng Headless Chrome
```bash
# Kiểm tra xem có bất kỳ lỗi Uncaught Exception nào xảy ra khi tải trang
google-chrome --headless=new --virtual-time-budget=3000 --dump-dom http://localhost:8888 > /tmp/rendered.html
```

---

## 2. QUY TRÌNH PHÁT TRIỂN & COMMIT THEO QUY CHUẨN GENESIS

1. **Rà soát mã nguồn:**
   - Đảm bảo giữ vững phong cách xưng *"Em"* — gọi *"Sếp Ryan"*.
   - Đảm bảo mọi thay đổi đối với `frontend/index.html` đều có kiểm tra an toàn chống lỗi `null` DOM element.
2. **Kiểm thử cục bộ:**
   - Biên dịch Python và Node.js syntax check.
   - Thử nghiệm curl API thực tế.
3. **Commit Git có cấu trúc:**
   - Format: `<type>(<scope>): <mô tả ngắn bằng tiếng Anh>`
   - Ví dụ: `fix(chat): eliminate robotic canned replies with live executive persona`
   - Nhánh phát triển hiện hành: `feat/mission-control-ui`.

---

## 3. VẬN HÀNH BACKEND SAU CÁC SỬA ĐỔI (OAuth · #3 · #4 · #5 · #6)

### 3.1. Biến môi trường mới
| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | (rỗng) | Bắt buộc để đăng nhập Google. Không còn client ID hard-code; thiếu → API trả lỗi "Chưa cấu hình GOOGLE_OAUTH_CLIENT_ID trong .env". |
| `GW_AGY_BIN` | `agy` | Đường dẫn CLI agy. Test trỏ tới script giả để chạy không cần agy thật. |
| `GW_RECLAIM_INTERVAL_SEC` | `300` | Chu kỳ thread nền `[reclaim]` thu hồi task `in_progress` treo (`GW_RECLAIM_TIMEOUT_SEC` = ngưỡng treo, mặc định bằng chu kỳ). |
| `GW_WORKTREE_ROOT` | `<repo>/../gw-worktrees` | Nơi tạo worktree riêng `wt/<session_id>` cho từng vai khi chatroom gọi agy. |
| `GW_DISPATCH_REPO` | thư mục repo | Repo nguồn để `git worktree add` (test dùng repo git tạm). |

### 3.2. OAuth callback
- Server callback cổng 8085 chỉ bind `127.0.0.1`; route dự phòng `/oauth2callback` trên cổng chính dùng chung `handle_oauth_callback()`.
- Tham số `state` (= profile đích) phải khớp `^(owner_default|profile[0-9]{1,2})$`, sai → HTTP 400, không gọi Google.
- `mcp_config.json` seed cho profile mới trỏ `~/.local/bin/genos-gdrive-mcp` và `~/.local/bin/gen-workplace-mcp` (theo `$HOME`).

### 3.3. Nhãn tài khoản (#5)
`account_label` trong `GET /api/tmux/sessions` là giá trị TÍNH: `profileN (email)` hoặc `profileN (chưa đăng nhập)`; `owner_default` → `Mặc định (...)`. Không còn email giả `owner@genesis.local`. MCP `switch_google_account` chỉ nhận `account_id`.

### 3.4. Bằng chứng nghiệm thu (#4)
`POST /api/task/complete` và MCP `complete_task` chỉ nhận `evidence_ref` kiểm được:
- commit SHA 7–40 hex có trong repo → `verified_by = git:commit`;
- file tồn tại (tuyệt đối, tương đối repo, hoặc `~/gw-reports/...`) → `file`;
- `https://github.com/<owner>/<repo>/pull/<n>` → `github:pr`.
Không đạt → `{"error": ...}` và trạng thái giữ nguyên. Lúc `init_db`, task `done` có evidence không kiểm được được đặt lại về `review`.

### 3.5. Quota từ kết quả gọi thật (#6)
- Mỗi lần runner agy chạy (chat, probe, dispatch) ghi 1 dòng `quota_probe(profile_id, model, status, reset_at, checked_at, raw)`.
- `get_quota_telemetry` ưu tiên dòng < 6 giờ: `rate_limited` → `status_label = "429 · hồi <reset>"`; chưa có dòng nào → `status = unknown`, `percent = null`.
- Cập nhật thủ công: `POST /api/quota/probe {"profile_id": "owner_default"}` hoặc MCP `probe_quota` (chạy `agy --gemini_dir=<dir> --mode plan -p 'ping'`, timeout 60s).

### 3.6. Chatroom gọi agy thật (#3)
Tin trong War Room có `@backend|@frontend|@devops|@qa|@security|@lead` → thread nền chạy
`agy --gemini_dir=<profile của worker> --mode plan --sandbox -p "<tin>"` (timeout 15 phút) trong worktree riêng của vai; trả lời thật
(tác giả = `gw-<vai>-agy`, body = output cắt 4000 ký tự + `exit=<code>`) được ghi vào `chat_messages`, kèm `dispatch_log` và báo cáo `~/gw-reports/warroom-<sid>-<ts>.md`. Tin không có `@vai` chỉ được lưu.

### 3.7. Kiểm thử không cần server / agy / tmux
```bash
python3 -m py_compile backend/*.py
python3 scripts/test_directive_guard.py
python3 scripts/test_task_evidence.py      # #4
python3 scripts/test_quota_probe.py        # #6
python3 scripts/test_warroom_dispatch.py   # #3
```

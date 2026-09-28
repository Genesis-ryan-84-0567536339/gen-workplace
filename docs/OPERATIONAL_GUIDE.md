# 🛠️ CẨM NANG VẬN HÀNH & KIỂM THỬ (OPERATIONAL GUIDE)
> **Dự án:** `GEN-WORKPLACE`  
> **Dành cho:** Developer & Builder Agent tại phiên `Gen_workplace Builder`

---

## 1. CÁC LỆNH VẬN HÀNH CỐT LÕI (RUNBOOK)

### 1.1. Khởi Động & Khởi Động Lại Hệ Thống
App chạy trực tiếp trên host bằng `python3 backend/main.py` (http.server thuần, DB SQLite tại `DATA_DIR`, mặc định `<repo>/data/`). Không dùng Docker.
```bash
# Chạy tay (thư mục repo)
python3 backend/main.py

# Chạy nền bằng systemd --user (unit mẫu: scripts/gen-workplace.service)
mkdir -p ~/.config/systemd/user && cp scripts/gen-workplace.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now gen-workplace
systemctl --user restart gen-workplace          # khởi động lại sau khi cập nhật backend
journalctl --user -u gen-workplace -f -n 50     # log runtime của Control Plane

# TỰ CẬP NHẬT (#13, backend/auto_update.py): app tự kiểm origin/main mỗi 120s; có commit mới
# thì chạy thử trên cổng phụ với bản sao DB, OK mới tự restart (os.execv), hỏng thì giữ bản cũ.
# Chỉ chạy khi máy đang ở nhánh main. Xem trạng thái: curl localhost:8888/api/auto-update
# Biến: GW_AUTO_UPDATE=0 (tắt) · GW_AUTO_UPDATE_SEC=120 · GW_AUTO_UPDATE_BRANCH=main
# Nhật ký: data/auto_update.log

# Cập nhật tay (thường không cần nữa) (scripts/gw-update.sh)
gw-update            # origin/main — cũng là lệnh đưa máy về main để tự cập nhật chạy lại
gw-update <nhánh>    # nhánh PR đang xem thử (tự cập nhật tạm dừng tới khi gw-update về main)

# Vào phiên tmux của một vai (attach_cmd trong GET /api/tmux/sessions)
tmux attach -t gw-backend-agy
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
| `GW_EVENT_WEBHOOK_URL` | (rỗng = tắt) | URL nhận POST JSON sự kiện `task_completed` / `dispatch_finished` (Issue #12, xem 3.8). |
| `GW_ORCH_MODEL` | (rỗng → model mặc định của agy) | Model dùng cho Orchestrator chat (`/api/orch/chat`). |
| `GW_AGY_CHAT_TIMEOUT_SEC` | `180` | Timeout một lượt agy cho chat Gen / Orchestrator. |

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
`agy --gemini_dir=<profile của worker> --mode plan -p "<tin>"` (không `--sandbox`; timeout 15 phút) trong worktree riêng của vai; trả lời thật
(tác giả = `gw-<vai>-agy`, body = output cắt 4000 ký tự + `exit=<code>`) được ghi vào `chat_messages`, kèm `dispatch_log` và báo cáo `~/gw-reports/warroom-<sid>-<ts>.md`. Tin không có `@vai` chỉ được lưu.

### 3.7. Kiểm thử không cần server / agy / tmux
```bash
python3 -m py_compile backend/*.py
python3 scripts/test_directive_guard.py
python3 scripts/test_task_evidence.py      # #4
python3 scripts/test_quota_probe.py        # #6
python3 scripts/test_warroom_dispatch.py   # #3
python3 scripts/test_no_fake_reply.py      # #12: không bịa câu trả lời, dispatch task thật, attach_cmd
python3 scripts/test_purge_seed.py         # #12: bỏ seed + migration purge_seed_data()
python3 scripts/test_viec_ref.py           # #12: viec_ref bắt buộc + webhook + /api/dispatch/log
```

### 3.8. Issue #12 — gỡ toàn bộ dữ liệu / phản hồi giả (hợp đồng API cho frontend)

**Không còn phản hồi tự sinh.** Mọi câu trả lời hiển thị đều là output thật của `agy`; agy lỗi thì hiển thị lỗi thật.

| API | Thay đổi |
|---|---|
| `POST /api/gen/chat` | Giữ nguyên các key cũ (`user_msg_id`, `reply_id`, `reply`, `cited_notes`, `model`, `conv_title`, `engine`, `usage`, `kanban_updates`). Thêm `author`, `error` (bool), `error_code`. `engine` = `agy-cli` (thật) hoặc `error`. Khi lỗi: tin lưu với tác giả **`Gen (lỗi)`**, `reply` = `agy không trả lời: <lý do>. Không có phản hồi tự sinh.`; `error_code` ∈ `RESOURCE_EXHAUSTED`, `TIMEOUT`, `AGY_NOT_FOUND`, `EXIT_<rc>`, `EMPTY_RESPONSE`, `EXCEPTION`; `usage.detail` = đuôi output thật. Không còn `smart-fallback`. |
| `POST /api/orch/chat` | Giữ `reply`, `action_taken` (luôn `null`), `user_time`, `agent_time`. Thêm `author` (`Orchestrator (agy)` / `Orchestrator (lỗi)`), `engine`, `error`, `error_code`, `usage`. Gọi agy thật qua conversation `conv-orchestrator` (giữ `agy_conv_id`). Body lưu trong `/api/orch/messages` đã HTML-escape, xuống dòng → `<br>`. |
| `POST /api/swarm/dispatch` | Body `{project_id, session_id?}`. Không `session_id` → 6 worker ("Chạy Toàn Bộ Swarm"); có `session_id` → 1 worker ("Chạy Task này"). Mỗi worker chạy lệnh thật cho task đang gán (`tmux_sessions.current_task_id`): `agy --gemini_dir='<hồ sơ>' --mode plan -p 'Thực hiện task <id>: <tiêu đề>' 2>&1 \| tee ~/gw-reports/task-<id>-<ts>.md; echo "=== XONG exit=${PIPESTATUS[0]} ==="` (qua `directive_guard`, ghi `directive_audit`). Response: `{status: dispatched\|error, dispatched_count, error_count, results: {sid: {status: dispatched\|error, reason?, task_id?, task_title?, viec_ref?, command?, report_path?, tmux_real?}}}`. Worker không có task → `results[sid] = {status: "error", reason: "Worker ... không có task đang gán ..."}`, không echo giả. |
| `GET /api/tmux/sessions` | `attach_cmd` = `tmux attach -t <sid>`; thêm `task_viec_ref`. |
| `GET /api/state` | Bảng trống → `roadmap`, `todos`, `nodes`, `runtimes`, `ssot`, `roleMemory`, `catalog`, `refs`, `events` là `[]` (không còn EVT giả). `todos[i][j]` thêm `viec_ref`, `evidence_ref`; mỗi item `kanban.*` là `[id, title, role, viec_ref]`. |
| `GET /api/gen/conversations`, `/api/warroom/messages`, `/api/orch/messages`, `/api/gen/session/todos` | Không tự seed nữa; trống → `[]`. Phiên `conv-gen-core-01` / `conv-gen-builder` và TSK mẫu không còn (frontend không nên mặc định `conv-gen-core-01` tồn tại). Tạo phiên mới không còn lời chào giả. |
| `POST /api/gen/session/todos/save` | Tạo mới (không `id` hoặc `id` chưa có) **bắt buộc `viec_ref`** khớp `^VIEC-[0-9]+$` (mã việc trong Kho Ryan). Thiếu/sai → **400** `{"error": "Thiếu viec_ref (mã việc trong Kho Ryan, vd VIEC-12)"}`. Sửa task: không gửi `viec_ref` thì giữ mã cũ. Nhận thêm `order_idx`. Response `{status, id, title, viec_ref, created}`. |
| MCP `create_kanban_task` | Thêm tham số bắt buộc `viec_ref`; thiếu → `isError: true` cùng câu báo trên. `list_kanban_tasks` / `GET /api/gen/session/todos` trả `viec_ref` trong mỗi todo. |
| `POST /api/task/complete` / MCP `complete_task` | Response thêm `viec_ref`, `webhook_sent`. |
| `GET /api/dispatch/log?limit=50` | `{items: [{id, session_id, command, exit_code, report_path, started_at, finished_at, task_id, viec_ref, channel_id, webhook_sent}], count, webhook_enabled}` (mới nhất trước). |
| `GET /api/skills`, `/api/mcps`, `/api/vault/list` | Không còn danh sách giả; `/api/vault/list` liệt kê biến môi trường đang có hiệu lực + trạng thái đăng nhập từng OAuth profile (không lộ giá trị). |

**Webhook sự kiện** (`GW_EVENT_WEBHOOK_URL`): POST JSON, timeout 5s, lỗi chỉ ghi log.
```json
{"event": "task_completed | dispatch_finished", "project_id": "PRJ-GEN-WORKPLACE", "viec_ref": "VIEC-12",
 "task_id": "TSK-03", "session_id": "gw-backend-agy", "exit_code": 0, "report_path": "~/gw-reports/warroom-....md",
 "evidence_ref": "<sha|file|PR url>", "verified_by": "git:commit", "at": "2026-09-28T09:00:00+07:00"}
```
`task_completed` bắn khi `complete_task` thành công (`exit_code` = `null`); `dispatch_finished` bắn khi agy của chatroom chạy xong (`task_id`/`viec_ref` = task đang gán cho worker, có thể rỗng).

**Migration dữ liệu seed cũ:** `purge_seed_data()` chạy cuối `init_db()` (mỗi lần app khởi động), xóa đúng các bản ghi seed liệt kê trong `backend/seed_purge_list.json` (RM-01…05, TODO-01…13, NODE-01…06, runtime-01…06, SSOT-*-01…08, 17 mã catalog, EVT-01…05, 6 role memory, tin chat seed và các câu mẫu cũ của tác giả bot, phiên `conv-gen-core-01`/`conv-gen-builder` và dữ liệu con, TSK-* tiêu đề mẫu). Idempotent; log `[purge] xóa N bản ghi seed (bảng: ...)`. Bản ghi do người dùng tạo (kể cả trùng ID nhưng khác tiêu đề, hoặc tin người dùng trùng đầu câu mẫu) không bị xóa. Task `TODO-14…17` (nếu có trên máy chủ) không có trong code seed nào nên không nằm trong danh sách — xem `/api/state` sau khi cập nhật và xóa tay nếu là dữ liệu giả.

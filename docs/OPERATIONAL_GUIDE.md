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
| `GW_RECLAIM_INTERVAL_SEC` | `300` | Chu kỳ thread nền `[reclaim]` thu hồi task `in_progress` treo (`GW_RECLAIM_TIMEOUT_SEC` = ngưỡng treo, mặc định bằng chu kỳ). Cùng ngưỡng này quyết định khi nào khóa `claim_task` quá hạn và worker khác được claim lại (#16). |
| `GITHUB_TOKEN` | (rỗng) | Có thì gửi `Authorization: Bearer` khi kiểm URL PR làm bằng chứng (repo private, tránh rate limit 60 lượt/giờ). |
| `GW_GITHUB_API_TIMEOUT_SEC` | `5` | Timeout gọi `https://api.github.com/repos/<owner>/<repo>/pulls/<n>` khi kiểm bằng chứng PR. |
| `GW_WORKTREE_ROOT` | `<repo>/../gw-worktrees` | Nơi tạo worktree riêng `wt/<session_id>` cho từng vai khi chatroom gọi agy. |
| `GW_DISPATCH_REPO` | thư mục repo | Repo nguồn để `git worktree add` (test dùng repo git tạm). |
| `GW_EVENT_WEBHOOK_URL` | (rỗng = tắt) | URL nhận POST JSON sự kiện `task_completed` / `dispatch_finished` (Issue #12, xem 3.8). |
| `GW_ORCH_MODEL` | (rỗng → model mặc định của agy) | Model dùng cho Orchestrator chat (`/api/orch/chat`). |
| `GW_AGY_CHAT_TIMEOUT_SEC` | `180` | Timeout một lượt agy cho chat Gen / Orchestrator. |
| `GW_AGY_WRITE_ROLES` | (rỗng = không vai nào) | Vai được bật `--dangerously-skip-permissions` cho **alias `agy-run`** trong tmux, vd `backend,gw-devops-agy`. Chỉ có hiệu lực khi phiên tmux mở trong worktree riêng của vai; alias `agy` thường không bao giờ có cờ (Issue #7). |
| `GW_AGY_PLAN_ALLOW` | `1` | War-room tự thêm quy tắc chỉ đọc vào `permissions.allow` của hồ sơ agy (xem 3.6). `0` = không đụng settings. |
| `GW_WARROOM_SKIP_PERMISSIONS` | `0` | Lối thoát cuối nếu agy thật vẫn auto-denied dù đã có quy tắc: thêm `--dangerously-skip-permissions` cho lệnh war-room (`--mode plan`), **chỉ khi cwd là worktree riêng của vai**. Đánh đổi: lệnh shell agy chạy không bị hỏi. |
| `GW_TMUX_WATCH_MAX_SEC` | `7200` | Thời gian tối đa thread nền theo dõi 1 lệnh giao qua tmux chờ dòng `=== XONG exit=N ===`. |

### 3.2. OAuth callback
- Server callback cổng 8085 chỉ bind `127.0.0.1`; route dự phòng `/oauth2callback` trên cổng chính dùng chung `handle_oauth_callback()`.
- Tham số `state` (= profile đích) phải khớp `^(owner_default|profile[0-9]{1,2})$`, sai → HTTP 400, không gọi Google.
- `mcp_config.json` seed cho profile mới trỏ `~/.local/bin/genos-gdrive-mcp` và `~/.local/bin/gen-workplace-mcp` (theo `$HOME`).

### 3.3. Nhãn tài khoản (#5)
`account_label` trong `GET /api/tmux/sessions` là giá trị TÍNH: `profileN (email)` hoặc `profileN (chưa đăng nhập)`; `owner_default` → `Mặc định (...)`. Không còn email giả `owner@genesis.local`. MCP `switch_google_account` chỉ nhận `account_id`.

### 3.4. Bằng chứng nghiệm thu (#4, #16)
`POST /api/task/complete` và MCP `complete_task` chỉ nhận `evidence_ref` kiểm được (chi tiết hợp đồng API ở 3.8):
- commit SHA 7–40 hex có trong repo → `verified_by = git:commit`;
- file **không rỗng** nằm trong `~/gw-reports/`, repo app (`GW_DISPATCH_REPO`) hoặc `GW_WORKTREE_ROOT` (đường dẫn tuyệt đối, tương đối repo hoặc `~/...`; symlink/`../` được giải thật, file trong `.git` không nhận) → `file`;
- `dispatch:<id>` — dòng `dispatch_log` có status `done` (dòng cũ không có status: đã xong và exit 0); dispatch có `task_id` thì phải trùng task đang nghiệm thu → `dispatch`;
- `warroom:<id>` — tin `chat_messages` cùng dự án, tác giả là worker (`gw-*-agy` / phiên tmux) hoặc `Orchestrator (agy)`, không phải tin lỗi; nếu là trả lời của một dispatch thì dispatch đó phải `done` → `warroom`;
- `https://github.com/<owner>/<repo>/pull/<n>` — gọi GitHub API công khai kiểm PR có thật (200) → `github:pr`. 404 / 401 / 403 / mất mạng / timeout → **từ chối**, kèm lý do.
Không đạt → `{"error": ...}` và trạng thái giữ nguyên. Lúc `init_db`, task `done` có evidence không kiểm được được đặt lại về `review` — bước rà soát này
dùng luật cũ (URL PR chỉ kiểm định dạng, file chỉ cần tồn tại, không gọi mạng) nên task đã done trước #16 không bị hạ cấp.

### 3.5. Quota từ kết quả gọi thật (#6)
- Mỗi lần runner agy chạy (chat, probe, dispatch) ghi 1 dòng `quota_probe(profile_id, model, status, reset_at, checked_at, raw)`.
- `get_quota_telemetry` ưu tiên dòng < 6 giờ: `rate_limited` → `status_label = "429 · hồi <reset>"`; chưa có dòng nào → `status = unknown`, `percent = null`.
- Cập nhật thủ công: `POST /api/quota/probe {"profile_id": "owner_default"}` hoặc MCP `probe_quota` (chạy `agy --gemini_dir=<dir> --mode plan -p 'ping'`, timeout 60s).

### 3.6. Chatroom gọi agy thật (#3)
Tin trong War Room có `@backend|@frontend|@devops|@qa|@security|@lead` → thread nền chạy
`agy --gemini_dir=<profile của worker> --mode plan -p "<tin>"` (không `--sandbox`; timeout 15 phút) trong worktree riêng của vai; trả lời thật
(tác giả = `gw-<vai>-agy`, body = output cắt 4000 ký tự + `exit=<code>`) được ghi vào `chat_messages`, kèm `dispatch_log` và báo cáo `~/gw-reports/warroom-<sid>-<ts>-<dispatch_id>.md`. Tin không có `@vai` chỉ được lưu.

- Tin trả lời có `reply_to` = id tin yêu cầu và `created_at` (ISO, đủ ngày giờ). `dispatch_log` có `request_msg_id` / `reply_msg_id`.
- **Quyền agy trong `-p`** (không tương tác → tool cần quyền bị auto-denied): trước khi chạy, app thêm vào
  `<hồ sơ>/antigravity-cli/settings.json` → `permissions.allow` các quy tắc chỉ đọc: `read_file(<worktree của vai>)` và
  `command(ls|cat|head|tail|wc|grep|rg|pwd|tree|git status|git log|git diff|git show|git branch|git ls-files|git grep|git rev-parse)`.
  Giữ nguyên các khóa khác; file hỏng thì không đụng; chạy lại không nhân đôi. Không cấp lệnh ghi/xóa. Nếu agy thật vẫn
  auto-denied → bật `GW_WARROOM_SKIP_PERMISSIONS=1` (chỉ áp dụng trong worktree của vai).
- agy thoát 0 nhưng output ngắn có `no output produced` / `auto-denied` (hoặc rỗng) → **failed** (tin trả lời ghi
  `exit=0 (failed: agy bị từ chối quyền, không có kết quả)`).

### 3.6b. Phiên tmux của vai (Issue #7)
- `ensure_real_tmux_sessions` / `wake_tmux_session` mở phiên `gw-<vai>-agy` trong worktree riêng `<GW_WORKTREE_ROOT>/<session_id>`
  (nhánh `wt/<session_id>`, cùng hàm `ensure_role_worktree` với war-room), không mở trong repo app. Chỉ tạo worktree khi `tmux -V` chạy được.
- Alias `agy` = `agy --gemini_dir='<hồ sơ>'` (không skip-permissions). `agy-run` chỉ thêm cờ khi vai có trong `GW_AGY_WRITE_ROLES`
  **và** cwd là worktree của vai. `update_tmux_account` dựng lại alias theo cùng luật.
- `gw-status` in thêm `CWD`, `Branch` và trạng thái quyền ghi.
- `directive_guard` từ chối mọi lệnh agy gửi qua directive có `--dangerously-skip-permissions`.
- Phiên tmux đang chạy từ trước **không tự chuyển**: cần hibernate → wake (hoặc kill phiên để app mở lại) để vào worktree + alias mới.
- Chat Gen / Orchestrator (`call_agy_cli_turn`, `agy --print`) vẫn dùng `--dangerously-skip-permissions` như cũ (không phải tmux, chạy trong thư mục phiên chat).

### 3.7. Kiểm thử không cần server / agy / tmux
```bash
python3 -m py_compile backend/*.py
python3 scripts/test_directive_guard.py
python3 scripts/test_task_evidence.py      # #4, #16: bằng chứng (GitHub API giả), chống ghi đè task done, claim nguyên tử 2 thread
python3 scripts/test_quota_probe.py        # #6
python3 scripts/test_warroom_dispatch.py   # #3
python3 scripts/test_no_fake_reply.py      # #12: không bịa câu trả lời, dispatch task thật, attach_cmd
python3 scripts/test_purge_seed.py         # #12: bỏ seed + migration purge_seed_data()
python3 scripts/test_viec_ref.py           # #12: viec_ref bắt buộc + webhook + /api/dispatch/log
python3 scripts/test_wait_worker_result.py # #9: wait_worker_result, auto-denied → failed, reply_to, N tin mới nhất, tmux thật + webhook
python3 scripts/test_agy_permissions.py    # #7: alias không skip-permissions, worktree cho tmux, quy tắc chỉ đọc
python3 scripts/test_mcp_instructions.py   # #19: initialize.instructions (HTTP + stdio), log_session_message chỉ lưu, create_conversation reuse
python3 scripts/test_task_hub.py           # #24: task ↔ war-room ↔ worker, kết quả ghi về phiên, kiểm tham số switch_google_account
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
| `POST /api/task/complete` / MCP `complete_task` (#16) | `evidence_ref` nhận thêm `dispatch:<id>` và `warroom:<id>`; URL PR được kiểm qua GitHub API; file phải không rỗng và nằm trong `~/gw-reports/`/repo/worktree (xem 3.4). `verified_by` ∈ `git:commit\|file\|github:pr\|dispatch\|warroom`. **Task đã `done`** → HTTP **409** `{"error", "code": "already_done", "evidence_ref", "verified_by"}` (bằng chứng hiện có), không ghi đè. Ghi đè có chủ đích: thêm `"force": true` (+ `"reason"`) — bằng chứng mới vẫn phải qua kiểm; response thêm `overridden: {old_evidence_ref, old_verified_by, reason}`; mỗi lần ghi đè lưu vào bảng `task_evidence_audit` và log `[evidence]`. Bằng chứng sai → 400; task không có → 404. Hoàn tất task Kanban nhả `claimed_by`/`locked_at`. |
| `GET /api/task/evidence-audit?task_id=&limit=50` (#16) | `{items: [{id, created_at, task_id, table_name, project_id, session_id, old_evidence_ref, old_verified_by, new_evidence_ref, new_verified_by, reason}], count}`, mới nhất trước. |
| `POST /api/task/claim` / MCP `claim_task` (#16) | Áp dụng cho cả `todos` và task Kanban phiên (`gen_session_todos`, thêm cột `claimed_by`, `locked_at`). Khóa **nguyên tử**: một câu `UPDATE ... WHERE` có điều kiện trong `BEGIN IMMEDIATE`, kiểm `rowcount` — nhiều worker gọi cùng lúc thì đúng 1 người thắng. Task `in_progress` đang do người khác giữ (`assigned_session_id`, hoặc `claimed_by`/`assigned_agent` với Kanban) và khóa chưa quá `GW_RECLAIM_TIMEOUT_SEC` → HTTP **409** `{"error", "code": "locked", "held_by", "locked_at" (UTC)}`. Người đang giữ gọi lại → 200, làm mới `locked_at`. Khóa quá hạn hoặc không có `locked_at` (dữ liệu cũ, task sửa tay trên UI) → worker khác claim được. Task `done` → 409 `code: already_done` (không mở lại task). Không có task → 404. |
| `POST /api/task/reclaim` | `timeout_seconds` bỏ trống → dùng cùng ngưỡng với khóa claim (`GW_RECLAIM_TIMEOUT_SEC`). |
| `GET /api/dispatch/log?limit=50` | `{items: [{id, session_id, command, exit_code, report_path, started_at, finished_at, task_id, viec_ref, channel_id, webhook_sent, status, kind, summary, request_msg_id, reply_msg_id}], count, webhook_enabled}` (mới nhất trước). `id` = `dispatch_id`. `status` ∈ `running\|done\|failed` (dòng cũ trước #9: `''`), `kind` ∈ `warroom\|tmux`. Dòng được ghi `running` ngay lúc giao việc. |
| MCP `post_warroom_message` / `POST /api/warroom/send` | Response thêm `dispatches: [{session_id, dispatch_id}]` (mỗi `@vai` một lần giao) và `user_message.created_at`. |
| `POST /api/swarm/dispatch` (bổ sung #9) | Mỗi `results[sid]` thêm `dispatch_id` (int khi `tmux_real`, `null` khi tmux không nhận lệnh). Thread nền đọc pane tmux tới dòng `=== XONG exit=N ===` sau tên file báo cáo → chốt `done/failed` và bắn webhook `dispatch_finished` (không cần ai poll). |
| MCP `wait_worker_result` | Tham số: `dispatch_id` (int) **hoặc** `task_id` **hoặc** `session_id` (lần giao mới nhất), `timeout_sec` (mặc định 60, tối đa 120). Chờ phía server (`threading.Condition` do chỗ ghi kết quả báo hiệu + đọc DB mỗi giây). Trả JSON: `{status: done\|failed\|running\|not_found\|error, dispatch_id, session_id, kind, exit_code, summary (≤2000 ký tự), report_path, task_id, viec_ref, task_status, evidence, started_at, finished_at, webhook_sent, request_msg_id, reply_msg_id, waited_sec, timeout_sec, hint?}`. Hết giờ → `running` + `hint` (gọi lại cùng `dispatch_id`). `not_found`/`error` → `isError: true`. Task đã `done` (qua `complete_task`) mà không có dispatch → `status: done, kind: task`. |
| `GET /api/dispatch/wait?dispatch_id=12&timeout_sec=60` / `POST /api/dispatch/wait {dispatch_id\|task_id\|session_id, timeout_sec}` | Như MCP `wait_worker_result`. HTTP 200 (done/failed/running), 404 (`not_found`), 400 (thiếu tham số). |
| `GET /api/warroom/messages` / MCP `get_warroom_messages` | Trả **N tin mới nhất** (trước đây là N tin cũ nhất), vẫn theo thứ tự tăng dần. Mỗi tin thêm `reply_to`, `created_at`. |
| `GET /api/skills`, `/api/mcps`, `/api/vault/list` | Không còn danh sách giả; `/api/vault/list` liệt kê biến môi trường đang có hiệu lực + trạng thái đăng nhập từng OAuth profile (không lộ giá trị). |

**Webhook sự kiện** (`GW_EVENT_WEBHOOK_URL`): POST JSON, timeout 5s, lỗi chỉ ghi log.
```json
{"event": "task_completed | dispatch_finished", "project_id": "PRJ-GEN-WORKPLACE", "viec_ref": "VIEC-12",
 "task_id": "TSK-03", "session_id": "gw-backend-agy", "exit_code": 0, "status": "done | failed", "report_path": "~/gw-reports/warroom-....md",
 "evidence_ref": "<sha|file|PR url|dispatch:<id>|warroom:<id>>", "verified_by": "git:commit", "at": "2026-09-28T09:00:00+07:00"}
```
`task_completed` bắn khi `complete_task` thành công (`exit_code` = `null`); `dispatch_finished` bắn khi agy của chatroom chạy xong (`task_id`/`viec_ref` = task đang gán cho worker, có thể rỗng), và khi lệnh giao qua `/api/swarm/dispatch` in dòng `=== XONG exit=N ===` (thread nền theo dõi pane, #9). `status` = `failed` cả khi `exit_code` = 0 nhưng agy bị auto-denied / không ra kết quả; `status` rỗng với `task_completed`.

**Migration dữ liệu seed cũ:** `purge_seed_data()` chạy cuối `init_db()` (mỗi lần app khởi động), xóa đúng các bản ghi seed liệt kê trong `backend/seed_purge_list.json` (RM-01…05, TODO-01…13, NODE-01…06, runtime-01…06, SSOT-*-01…08, 17 mã catalog, EVT-01…05, 6 role memory, tin chat seed và các câu mẫu cũ của tác giả bot, phiên `conv-gen-core-01`/`conv-gen-builder` và dữ liệu con, TSK-* tiêu đề mẫu). Idempotent; log `[purge] xóa N bản ghi seed (bảng: ...)`. Bản ghi do người dùng tạo (kể cả trùng ID nhưng khác tiêu đề, hoặc tin người dùng trùng đầu câu mẫu) không bị xóa. Task `TODO-14…17` (nếu có trên máy chủ) không có trong code seed nào nên không nằm trong danh sách — xem `/api/state` sau khi cập nhật và xóa tay nếu là dữ liệu giả.

### 3.9. Issue #19: bootstrap cho agent qua MCP `initialize.instructions`

Agent kết nối MCP gen-workplace (HTTP `POST /mcp` hoặc stdio `backend/mcp_server.py`) nhận quy trình bắt buộc trong `result.instructions` của `initialize`. Nhờ vậy việc agent làm hiện trong chatroom và Kanban cho Boss xem.

- **Sửa nội dung:** file `backend/mcp_instructions.md`. File được đọc 1 lần lúc app hoặc stdio khởi động, nên sửa xong phải restart (auto-update sẽ tự restart khi merge vào main). Nếu thiếu file hoặc file rỗng thì dùng `mcp_core.DEFAULT_MCP_INSTRUCTIONS`. Chuỗi hiện hành xem ở `GET /api/mcp/status` → `instructions`.
- **Nội dung (tóm tắt):** (1) mỗi việc là 1 phiên `VIEC-<n>: <tên việc>`, phiên đã có thì dùng lại; (2) chia bước thành `create_kanban_task` có `viec_ref` và `conv_id`, rồi `claim_task` và `update_task_checklist`; (3) ghi tiến độ bằng `log_session_message` ở mỗi mốc, kèm link; (4) giao việc bằng `post_warroom_message @<vai>` rồi `wait_worker_result`; (5) đóng việc bằng `complete_task` có evidence thật; (6) skill đầy đủ ở `Genesis-ryan-84-0567536339/Brain` → `skills/work-style/subskills/gen-workplace-dispatch/SKILL.md`.

| API / tool | Hợp đồng |
|---|---|
| MCP `log_session_message(conv_id, content, role="assistant", author="AI Agent")` | Chỉ INSERT 1 dòng vào `gen_messages` (`model = ''`) và cập nhật `updated_at` của phiên để phiên nổi lên đầu danh sách. **Không gọi agy/AI** (khác `gen_chat`). `role` ∈ `assistant\|user`; `content` ≤ 8000 ký tự. Trả `{status: "logged", message_id, conv_id, role, author, created_at}`. Phiên không có, content rỗng, role lạ hoặc content quá dài → `isError: true` kèm `error`. Token MCP có quyền domain `chat` được gọi tool này. |
| `POST /api/gen/conversations/log {conv_id, content, role?, author?}` | Như trên, dành cho client không dùng MCP. Trả 200, 400 (tham số sai) hoặc 404 (phiên không có). |
| MCP `create_conversation(title, reuse_existing=false, model?, account?)` | `title` được trim; rỗng thì thành "Cuộc trò chuyện mới". `reuse_existing=true` thì trả phiên mới nhất có đúng tiêu đề (`reused: true`) thay vì tạo trùng. Response luôn có `reused`. Phiên tạo qua MCP có `owner_id = owner-ryan`, nên hiện trong `list_conversations` và `GET /api/gen/conversations` (danh sách phiên trên UI). |
| `GET /api/mcp/status` | Thêm trường `instructions`. |

**UI:** màn Gen Workplace poll `/api/gen/conversations` mỗi 5 giây (khi đang mở màn đó và không có tin đang gửi). Phiên mới do agent tạo hiện ngay trong danh sách. Nếu phiên đang mở có thêm tin (`msg_count` đổi) thì UI tải lại tin và Kanban của phiên. Tin có `model` rỗng (tin log) hiển thị tên `author` thay cho "Gen Core (model)".

Kiểm thử thật trên cổng phụ (không đụng app chính):
```bash
PORT=18899 DATA_DIR=$(mktemp -d) GW_AUTO_UPDATE=0 python3 backend/main.py &   # tắt: kill $!  (KHÔNG dùng pkill -f)
curl -s localhost:18899/mcp -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
curl -s localhost:18899/mcp -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_conversation","arguments":{"title":"VIEC-1: Thử","reuse_existing":true}}}'
curl -s localhost:18899/mcp -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"log_session_message","arguments":{"conv_id":"<id>","content":"Bắt đầu — Issue #1","author":"Claude Code"}}}'
```

### 3.10. Issue #24: Việc là trung tâm (Kanban ↔ Phòng giao ban ↔ Worker) + màn MCP

Task thật nằm ở bảng `gen_session_todos` (MCP `create_kanban_task`, `claim_task`, Kanban phiên). Màn "Việc & tiến độ", Kanban của từng worker và thẻ task đều đọc bảng này (bảng `todos` cũ chỉ còn cho roadmap).

| API / tool | Hợp đồng |
|---|---|
| `GET /api/tasks?project=` | `{tasks: [...], count}`: mọi task của dự án, checklist đã parse, `total_items`/`done_items`, `holder` (= `claimed_by`), `conversation_title`, `dispatch_count`, `last_dispatch: {id, session_id, status, request_msg_id, reply_msg_id, report_path, profile_initial, profile_used, fallback, fallback_reason, channel_id, task_msg_id, ...}` (lần giao gần nhất). `/api/state` có cùng danh sách ở khóa `gen_session_todos`; `GET /api/gen/session/todos` cũng kèm `last_dispatch`. |
| `POST /api/task/assign {todo_id, session_id, author?, channel_id?}` | `session_id` nhận `qa`, `@qa` hoặc `gw-qa-agy`. Claim task cho worker (như `claim_task`), gửi war-room `@qa Thực hiện TSK-n (VIEC-m): <tiêu đề>` + checklist; prompt agy kèm khối `[THÔNG TIN VIỆC TSK-n]` (tiêu đề, `viec_ref`, mô tả, checklist có id mục). Trả `{status: "assigned", task_id, session_id, role, viec_ref, dispatch_id, request_msg_id, channel_id, claim, message}`. 400 thiếu/sai vai · 404 không có task · 409 task đã done (`already_done`) hoặc người khác đang giữ (`locked`, kèm `held_by`). |
| `GET /api/dispatch/log?limit=&task_id=&session_id=` | Lọc đúng giá trị (bỏ trống = không lọc, 2 tham số cùng lúc = AND). `status` luôn đã chuẩn hóa (`running\|done\|failed`), thêm `fallback` (bool). `limit` sai → 50. |
| `post_warroom_message` / `POST /api/warroom/send {..., task_id?}` | Task của lần giao việc: `task_id` truyền vào > mã `TSK-n` đầu tiên **có thật** trong nội dung tin > `current_task_id` của worker. `dispatches[]` thêm `task_id`; response thêm `task_id`. `@Gen`, `@Toàn Đội`, `@all` không giao việc (chỉ lưu, `note` nói rõ). |
| Kết quả giao việc ghi về task | Dispatch gắn task kết thúc (war-room hoặc tmux) → 1 tin `log_gen_message` trong phiên (`conversation_id`) của task, tác giả `<worker> (agy)`: trạng thái XONG/LỖI + exit, "đã chuyển hồ sơ" nếu fallback, tóm tắt, `Báo cáo: <report_path>`, `Tin war-room: #<id>`, `Link: dispatch:<id>`, và `Bằng chứng nghiệm thu gợi ý: dispatch:<id>` khi done. Ghi đúng 1 lần (`dispatch_log.task_msg_id`), trước khi `wait_worker_result` trả về. Worker ghi `[KANBAN_UPDATE: TSK-n \| CHECK: <id mục>]` trong output → mục checklist đó được tick (chỉ task của lần giao đó). |
| `complete_task` | Thêm ngoại lệ: bằng chứng là `dispatch:<id>` **đã done của chính người đang giữ task, cho đúng task đó** → người khác (vd Boss trên UI, `owner-ui`) đóng được mà không cần force; trả `closed_for_holder`, ghi `task_evidence_audit` action `holder_dispatch`. |
| MCP `switch_google_account` / `POST /api/tmux/account` | Kiểm tham số: thiếu `session_id`/`account_id`, worker không có, hồ sơ không có → lỗi (`isError` / HTTP 400), không ghi gì. `account_type` NULL cũ trong DB được `init_db` sửa thành `owner_default`; `fetch_live_google_quota(None)` coi là `owner_default`. |
| `GET /api/quota/live` / MCP `get_live_quota` | Cả nhánh Cloud Code (`source: cloudcode_api_live`) cũng có `gemini.exhausted`, `reset_at`, `reset_at_label` lấy từ `profile_quota_state`. |
| MCP `create_kanban_task` | Nhận `assigned_to` (bí danh của `assigned_agent`). |
| MCP `tools/list` | Mỗi tool có `annotations.readOnlyHint`; `GET /api/mcp/tools` thêm `read_only_tools`. Link MCP (`auth.endpoint`, `curl_snippet` token mới) lấy từ Host header (`X-Forwarded-Host/Proto` nếu có), rồi `GW_PUBLIC_ORIGIN`, cuối cùng `http://localhost:$PORT`. |

**Biến môi trường mới:** `GW_TMUX_INIT_DIR` (thư mục script khởi tạo tmux của vai, mặc định `$DATA_DIR/tmux-init`; trước đây ghi chung `/tmp/tmux_init_*.sh` nên các tiến trình ghi đè nhau), `GW_PUBLIC_ORIGIN` (gốc URL in trong link MCP khi request không có Host).

**UI:** thẻ task (màn Việc & tiến độ, Kanban phiên, Kanban của worker) có người giữ, lần giao gần nhất (đang chạy / xong / lỗi / đã chuyển hồ sơ), chip VIEC, checklist x/y và các nút "Giao cho @vai", "Tin #n", "Terminal", "Phiên", "Nghiệm thu" (hộp bằng chứng gợi ý `dispatch:<id>` của lần giao đã xong). War-room: `TSK-n`/`VIEC-n` là chip mở thẻ task, tin trả lời nối với tin hỏi (`reply_to`), trạng thái dispatch dưới tin, `dispatches[]` hiện ngay sau khi gửi. Worker & Terminal: task đang làm (chip TSK/VIEC, checklist x/y), quota thật hoặc lý do chưa có số, cờ hết quota, hồ sơ đang dùng, 5 lần giao gần nhất, nút Claim chọn task từ danh sách. Màn MCP: modal dùng class `show`; Test chỉ gọi ngay tool chỉ đọc, tool có tác dụng phụ phải nhập tham số + xác nhận; token bị che (`••••`) với nút Hiện/Copy.

```bash
python3 scripts/test_task_hub.py   # #24: /api/task/assign, lọc dispatch log, TSK trong tin, ghi kết quả về phiên, switch_google_account, quota None
```

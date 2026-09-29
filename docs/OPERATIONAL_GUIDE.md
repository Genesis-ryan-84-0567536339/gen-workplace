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
# Đang có việc chạy trên máy (agy build / review / tmux; Jules không tính) -> action=deferred, thử lại chu kỳ sau;
# hoãn quá GW_AUTO_UPDATE_MAX_DEFER_MIN phút (60) vẫn cập nhật + ghi cảnh báo (defer_expired).
# /api/auto-update có max_defer_min, deferred (since, waited_min, busy), busy_now.
# Biến: GW_AUTO_UPDATE=0 (tắt) · GW_AUTO_UPDATE_SEC=120 · GW_AUTO_UPDATE_BRANCH=main · GW_AUTO_UPDATE_MAX_DEFER_MIN=60
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

# Kiểm tra danh sách chuyên gia Tmux (4 vai)
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
| `GW_AGY_BUILD_TIMEOUT_SEC` | `1800` | Chế độ Làm (3.6c): thời gian tối đa 1 lần agy sửa code. Dòng `dispatch_log` build còn `running` quá mức này + 20 phút (ngân sách test/push) thì bị coi là mất thread. |
| `GW_BUILD_BASE` | `main` | Chế độ Làm: nhánh gốc, worktree tạo từ `origin/<GW_BUILD_BASE>`, link compare `compare/<GW_BUILD_BASE>...wt/TSK-n`. |
| `GW_BUILD_TEST_EXCLUDE` | `test_mcp_suite.py` | Chế độ Làm: file `scripts/test_*.py` app KHÔNG chạy sau khi agy xong (phân cách bằng dấu phẩy). |
| `GW_BUILD_TEST_TIMEOUT_SEC` / `GW_BUILD_PUSH_TIMEOUT_SEC` | `300` / `120` | Chế độ Làm: giới hạn 1 file test / lệnh `git push` của app. |
| `GW_BUILD_GITHUB_REPO` | (suy từ `git remote get-url origin`) | Chế độ Làm: `owner/repo` cho link compare / PR nháp khi remote không phải GitHub. |
| `GITHUB_TOKEN` | (rỗng) | Chế độ Làm: có token thì app tạo **PR nháp** `wt/TSK-n → main` sau khi push (không bao giờ merge); không có thì chỉ ghi link compare. |

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
Tin trong War Room có `@backend|@devops|@qa|@lead` → thread nền chạy (`@security` và `@frontend` đã bỏ từ 29/09: trả lỗi `retired_role`, không lưu tin; việc giao diện giao cho `@backend`)
`agy --gemini_dir=<profile của worker> --mode plan -p "<tin>"` (không `--sandbox`; timeout 15 phút) trong worktree riêng của vai; trả lời thật
(tác giả = `gw-<vai>-agy`, body = output cắt 4000 ký tự + `exit=<code>`) được ghi vào `chat_messages`, kèm `dispatch_log` và báo cáo `~/gw-reports/warroom-<sid>-<ts>-<dispatch_id>.md`. Tin không có `@vai` chỉ được lưu.

- Tin trả lời có `reply_to` = id tin yêu cầu và `created_at` (ISO, đủ ngày giờ). `dispatch_log` có `request_msg_id` / `reply_msg_id`.
- **Quyền agy trong `-p`** (không tương tác → tool cần quyền bị auto-denied): trước khi chạy, app ghi vào
  `<hồ sơ>/antigravity-cli/settings.json` (hàm `ensure_agy_plan_permissions`, Issue #26 sau lỗi dispatch:9):
  - `permissions.allow`: `read_file(<worktree của vai>)`, `command(ls|cat|head|tail|wc|grep|rg|pwd|tree|cd|find|stat|file|git status|git log|git diff|git show|git blame|git ls-files|git grep|git rev-parse)`
    và 2 rule regex: `sed -n 'N,Mp'` (chỉ in theo dòng) và `git branch --show-current|-a|-r|-v|--list|...` (chỉ liệt kê).
    Rule cũ `command(git branch)` (cho cả `git branch -D`) bị gỡ.
  - `permissions.deny` (Deny > Allow): cờ ghi / chạy lệnh con của các lệnh trên ở mọi vị trí token (0..10 token sau tên lệnh):
    `find -delete/-exec/-execdir/-ok/-fprint…`, `sed -n … -i/-e/-f/--in-place`, `rg --pre`, `tree -o`, `file -C`,
    `git diff|log|show --output`, `git grep -O/--open-files-in-pager`, `git branch -d/-D/-m/-c/-f/-u/--set-upstream-to…`.
  - Cố ý KHÔNG mở: `python3 -c`, `node -e`, `bash -c`, `xargs`, `awk`, `sed` tự do, lệnh mạng, lệnh ghi/xóa.
    Giới hạn đã biết của agy: chuyển hướng ghi file đơn giản (`cat a > b`) vẫn khớp tiền tố — prompt cấm dùng.
  Giữ nguyên các khóa khác; file hỏng / `allow`/`deny` sai kiểu thì không đụng; chạy lại không nhân đôi. Tắt: `GW_AGY_PLAN_ALLOW=0`.
  Lối thoát cuối (không khuyến nghị): `GW_WARROOM_SKIP_PERMISSIONS=1` (chỉ áp dụng trong worktree của vai).
- **Prompt chế độ chỉ đọc** (`build_agy_readonly_prompt`, dùng chung cho war-room và `POST /api/task/assign`): chỉ đọc, không lệnh ghi/mạng;
  ưu tiên công cụ đọc file có sẵn của agy; lệnh shell chỉ trong danh sách trên; bị chặn thì ghi dòng `CẦN QUYỀN: <lệnh> — <lý do>`
  trong báo cáo thay vì dừng im lặng (dòng này được đưa lên đầu `summary` của dispatch).
- **Ghi rõ lệnh bị chặn**: agy chạy thêm `--output-format stream-json`; app đọc tool step `run_command` (`CommandLine`, `error`) để biết
  lệnh nào bị từ chối → `summary` / tin war-room / tin kết quả trong phiên / báo cáo có dòng ``Lệnh bị chặn: `<lệnh>` ``
  (không trích được thì nêu lệnh shell cuối agy gọi). Báo cáo có mục `## Lệnh shell agy đã gọi`. agy cũ không nhận cờ → tự chạy lại
  không cờ; tắt hẳn stream-json: `GW_AGY_NO_STREAM=1`.
- agy thoát 0 nhưng output ngắn có `no output produced` / `auto-denied` (hoặc rỗng / stream-json không có câu trả lời) → **failed**
  (tin trả lời ghi `exit=0 (failed: agy bị từ chối quyền, không có kết quả)`).

### 3.6c. Chế độ "Làm" (build) — agy sửa code thật (Issue #45)
Boss chốt 29/09 (VIEC-12): agy được code thật, nhưng chỉ trong worktree riêng của task; app (không phải agy) test, push, ghi kết quả.
- **Giao**: `POST /api/task/assign {todo_id, session_id, mode}` — `mode` ∈ `build` ("Làm", **mặc định**) | `review` ("Rà soát").
  Thẻ task có ô chọn chế độ (mặc định "Làm") cạnh ô chọn vai. **Tin war-room `@vai` luôn là Rà soát** (`--mode plan`, 3.6).
  Response build thêm `mode: "build"`, `branch`, `worktree_dir`; task đang có lần Làm chạy → 409 `code: busy`.
- **Worktree**: `<GW_WORKTREE_ROOT>/TSK-n` (mặc định `../gw-worktrees/TSK-n`), nhánh `wt/TSK-n`, tạo bằng
  `git fetch origin main` + `git worktree add --no-track -b wt/TSK-n <dir> origin/main`. Giao lại cùng task → dùng lại worktree / nhánh.
- **Lệnh**: `agy --gemini_dir=<hồ sơ> -p <prompt> --output-format stream-json`, cwd = worktree: chế độ mặc định của agy
  (KHÔNG `--mode plan`), KHÔNG `--model` (agy dùng model mặc định), không có cờ bỏ hỏi quyền. Hồ sơ: `select_agy_profile_for_run`
  (hồ sơ của vai, hết quota thì hồ sơ khác còn quota; mỗi lần 429 thử hồ sơ tiếp theo). Tác giả commit = `gw-<vai>-agy (agy)`.
- **Quyền** (`ensure_agy_build_permissions`, ghi vào `<hồ sơ>/antigravity-cli/settings.json` trước khi chạy, **gỡ sau khi chạy**
  — đếm theo lần build nên 2 lần build song song trên cùng hồ sơ không gỡ của nhau; rule người dùng có sẵn giữ nguyên;
  không chép sang hồ sơ khác khi fallback quota; chế độ Rà soát tự gỡ rule Làm còn sót nếu app khởi động lại giữa chừng):
  - allow: `read_file(<worktree>)`, `write_file(<worktree>)`, mọi lệnh chỉ đọc của 3.6, `python3 -m py_compile`,
    `python3 scripts/test_<tên>.py` và `python3 <worktree tuyệt đối>/scripts/test_<tên>.py` (regex, chỉ đúng worktree của task — dispatch:15, #49),
    `git add`, `git commit`, `echo` (git status/diff/log đã có). Prompt dặn chạy từng lệnh riêng:
    một lệnh bị từ chối là agy `-p` dừng luôn (dispatch:13 dừng trước khi commit vì `&& echo`, #47).
  - deny: cờ ghi của 3.6 + `git push|remote|fetch|pull|clone|ls-remote|submodule|worktree|update-ref|symbolic-ref|config|switch|clean|reflog|gc`,
    `git -C|-c|--git-dir|--work-tree` (mọi vị trí), `git checkout main|master|origin/*|-b|-B|--detach|-f`, `git reset --hard|--merge|--keep`,
    `git commit -n|--no-verify|--amend`, `rm -r/-f` và `rm` đường dẫn tuyệt đối / `..` / `~` (không chặn `cd`: commit/push sai chỗ đã có hook + kiểm sau khi chạy),
    `curl|wget|ssh|scp|sftp|rsync|nc|telnet|ftp|socat|gh`, `sudo|su|doas`, `pip|pip3|python3 -m pip|npm|npx|yarn|pnpm|apt|apt-get|dpkg|brew|gem|cargo`,
    `write_file(...)` cho repo app, hồ sơ agy, `~/.ssh`, `~/.config`, `~/.gitconfig`, `~/.git-credentials`, `~/gw-reports`, `DATA_DIR`, `/etc`, `/usr`...
  - Lớp chặn thứ hai (chỉ cho tiến trình agy, qua `GIT_CONFIG_*`): `core.hooksPath` → hook `pre-commit` chỉ cho commit trên `wt/TSK-n`
    trong đúng worktree, `pre-push` / `pre-rebase` chặn; `remote.origin.pushurl` hỏng. Hook nằm ở `DATA_DIR/agy-build-hooks/TSK-n`.
- **Prompt**: giao lại khi worktree còn thay đổi chưa commit → prompt liệt kê file và nói rõ đó là việc dang dở của chính agy (#49). SOP `roles/build.md` (hiểu yêu cầu → sửa code → chạy test liên quan → commit message rõ ràng → báo cáo ngắn: đã đổi gì,
  test nào pass, rủi ro) + khối `[THÔNG TIN VIỆC TSK-n]` (tiêu đề, `viec_ref`, mô tả, checklist) + luật quyền (worktree, lệnh được / cấm, bắt buộc commit, không push).
- **Sau khi agy xong, app làm tiếp**: (1) kiểm commit mới trên `wt/TSK-n`; main local / HEAD repo app đổi sang commit không có trên
  `origin/main` hoặc worktree rời nhánh → **VI PHẠM**, failed, không push; (2) `py_compile` mọi file `.py` + chạy từng `scripts/test_*.py`
  (trừ `GW_BUILD_TEST_EXCLUDE`) trong worktree, HOME tạm, không ghi `__pycache__`; (3) `git push origin HEAD:refs/heads/wt/TSK-n` bằng
  credential git sẵn có (không hỏi mật khẩu; lỗi thì ghi rõ, không crash); (4) ghi vào `dispatch_log` (`worktree_dir`, `build_branch`,
  `build_commit`, `build_tests` JSON, `build_push` JSON, `compare_url`, `pr_url`), tin war-room của vai, báo cáo `~/gw-reports/build-TSK-n-<ts>-<id>.md`
  và tin kết quả trong phiên của task (nhánh, commit, test, push, `https://github.com/<owner>/<repo>/compare/main...wt/TSK-n`).
  Có `GITHUB_TOKEN` → PR **nháp**; không thì chỉ link compare. **App không bao giờ merge**; Claude điều phối tạo PR, review, merge.
- **Trạng thái**: `done` khi agy có ít nhất 1 commit mới và không vi phạm (test FAIL / push lỗi vẫn `done` nhưng ghi rõ trong summary,
  `build_tests.ok=false` / `build_push.ok=false`); `failed` khi không có commit, agy lỗi / bị chặn quyền không ra kết quả, hết quota, vi phạm.
- **Nghiệm thu**: bằng chứng gợi ý là commit SHA trên `wt/TSK-n` (`verify_evidence_ref` tìm trong repo app và `GW_DISPATCH_REPO`; worktree
  dùng chung kho object nên commit trong worktree được nhận, kể cả sau khi dọn). SHA của lần Làm đã done của người giữ task đóng thay được
  như `dispatch:<id>` (audit `holder_dispatch`). Sau khi merge PR thì đóng bằng SHA merge.
- **Dọn**: task done (`complete_task`) hoặc bị xóa → `git worktree remove --force` + `git worktree prune` + xóa hook; nhánh local và remote
  giữ nguyên. Task đang có lần Làm chạy thì không gỡ.
- Rủi ro còn lại: `python3 scripts/test_*.py` chạy mã trong worktree (agy có thể viết file test rồi chạy nó); chuyển hướng ghi
  (`cat a > /x`) vẫn khớp tiền tố lệnh đọc — prompt cấm, hook + kiểm sau chạy bắt commit/push sai chỗ nhưng không bắt được ghi file ngoài worktree bằng shell.

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
python3 scripts/test_agy_readonly_dispatch.py  # #26: allow/deny-rule chỉ đọc, prompt chỉ đọc, ghi lệnh bị chặn (stream-json)
python3 scripts/test_mcp_instructions.py   # #19: initialize.instructions (HTTP + stdio), log_session_message chỉ lưu, create_conversation reuse
python3 scripts/test_task_hub.py           # #24: task ↔ war-room ↔ worker, kết quả ghi về phiên, kiểm tham số switch_google_account
python3 scripts/test_agy_build.py          # #45: chế độ Làm — worktree, allow/deny, commit, test, push remote giả, chặn push/main, dọn worktree
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

Kiểm thử thật trên cổng phụ (không đụng app chính). DB mới có `require_auth = 0` nên gọi được không token; khi bắt buộc token (mặc định trên app thật, xem 3.12) thì mỗi lệnh `curl /mcp` phải thêm `-H "Authorization: Bearer $TOKEN"`:
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
| `POST /api/task/assign {todo_id, session_id, mode?, author?, channel_id?}` | `mode` = `build` (mặc định, chế độ Làm — xem 3.6c) hoặc `review`; mô tả dưới đây là `review`. `session_id` nhận `qa`, `@qa` hoặc `gw-qa-agy`. Claim task cho worker (như `claim_task`), gửi war-room `@qa Thực hiện TSK-n (VIEC-m): <tiêu đề>` + checklist; prompt agy kèm khối `[THÔNG TIN VIỆC TSK-n]` (tiêu đề, `viec_ref`, mô tả, checklist có id mục). Trả `{status: "assigned", task_id, session_id, role, viec_ref, dispatch_id, request_msg_id, channel_id, claim, message}`. 400 thiếu/sai vai · 404 không có task · 409 task đã done (`already_done`) hoặc người khác đang giữ (`locked`, kèm `held_by`). |
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

### 3.11. Issue #28: màn MCP & Kết nối

Modal MCP cũ được thay bằng màn riêng **MCP & Kết nối** (thanh bên, mục "Tri thức & dữ liệu"; nút `MCP` trên thanh trên cũng mở màn này; `openMcpModal()` cũ chuyển thẳng tới màn mới). Màn có header trạng thái (ping `/mcp` thật + thời gian phản hồi, URL kết nối, số tool, số token hoạt động và số agent dùng trong 24 giờ) và 4 tab: **Kết nối** (cấu hình copy được cho agy CLI, Claude Code, Claude.ai / Gen-hub, JSON chung; token vừa tạo điền sẵn, token cũ in `<TOKEN>` để tự thay — xem 3.12), **Tool** (nhóm theo chức năng, tìm không dấu, lọc Chỉ đọc / Có tác dụng phụ, ngăn chạy thử sinh form từ `inputSchema`), **Token** (tạo theo scope, token đầy đủ hiện 1 lần ngay sau khi tạo, danh sách chỉ có bản che, thu hồi có xác nhận, bật/tắt bắt buộc token có xác nhận; tắt cần token admin), **Bootstrap** (nội dung `initialize.instructions`, chỉ đọc). Esc đóng hộp xác nhận → ngăn chạy thử → màn (quay về màn trước đó).

**Metadata tool — nguồn duy nhất:** `mcp_core.TOOL_META[name] = {group, scope, read_only, summary}`. Từ bảng này suy ra `READ_ONLY_TOOLS`, `annotations.readOnlyHint`, scope kiểm quyền token (`db.MCP_TOOL_SCOPES`, do `mcp_core` điền lúc import — thay cho `domain_map` ghi cứng trong `db.verify_mcp_request_auth`) và dữ liệu cho UI. Thêm tool mới mà thiếu dòng trong `TOOL_META` → app báo lỗi ngay khi khởi động. Nhóm: `TOOL_GROUPS`; scope token: `TOKEN_SCOPES` (`all`, `kanban`, `swarm`, `chat`, `files`, `quota`). Phân quyền giữ nguyên như trước; `probe_quota` (chạy agy) chỉ token toàn quyền gọi được.

| API | Hợp đồng |
|---|---|
| `GET /api/mcp/status` (cũng `/api/mcp/tools`, `/mcp/tools`) | `tools[]` thêm `group`, `scope`, `read_only`, `summary` (xếp theo nhóm); thêm `groups`, `scopes`, `stdio: {wrapper, wrapper_exists, python, script, data_dir}` (lệnh chạy MCP qua stdio trên máy chủ); `auth.used_24h` = số token hoạt động được dùng trong 24 giờ. MCP `tools/list` không đổi. |
| `GET /api/mcp/tokens` | Token quá hạn mà chưa ai gọi hiện `status: "expired"`; `auth_status.used_24h`. |
| `POST /api/mcp/tokens/create` | `permissions` chỉ nhận id trong `TOKEN_SCOPES`, `*` hoặc tên tool có thật; giá trị lạ → **400** `{"error": "Scope không hợp lệ: ..."}`. |

Màn này không gọi `/mcp` trực tiếp mà gọi `POST /api/mcp/ui/rpc` cùng origin, không cần token (xem 3.12), nên không đọc token của agent và không cộng lượt gọi giả vào token. Chỗ nào cú pháp client chưa kiểm chứng được trong repo (Claude Code qua HTTP, Claude.ai cần URL HTTPS công khai, tên khóa `url`/`headers` của từng client) có nhãn **Kiểm tra lại** trên màn.

```bash
python3 scripts/test_mcp_ui_api.py   # #28: TOOL_META đủ/hợp lệ, /api/mcp/status, quyền theo scope, scope lạ 400, expired, used_24h
```

### 3.12. Issue #41: siết bảo mật MCP, bắt buộc token

**Gọi `/mcp` phải kèm Bearer.** Trên app thật, `require_auth` đang bật (bảng `mcp_auth_settings`). Khi đó `/mcp`, `/sse` và `/api/mcp` không có token hợp lệ đều trả **401**. Cách gửi token: header `Authorization: Bearer <token>`. Riêng connector Claude.ai không gửi được header nên dùng `?token=`. REST `/api/*` không bị ảnh hưởng.

```bash
curl -s http://<host>:8888/mcp -H "Authorization: Bearer $GW_TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

**Agent điều phối (sub agent chạy `workplace_exec` + `curl`) KHÔNG gọi `/mcp` không token.** Có hai cách thay:
- Dùng tool MCP qua connector Gen-hub `mcp-06594`. Connector này đã gửi token `claude-orchestrator`.
- Hoặc gọi REST `/api/*`, không cần token:

| Việc | REST |
|---|---|
| Ghi tiến độ vào phiên | `POST /api/gen/conversations/log {conv_id, content, author}` |
| Giao task cho vai | `POST /api/task/assign {todo_id, session_id: "backend", mode: "build"}` (sửa code, 3.6c) · `mode: "review"` hoặc `POST /api/warroom/send {message: "@qa ...", task_id}` (chỉ đọc) |
| Chờ kết quả | `POST /api/dispatch/wait {dispatch_id, timeout_sec}` (tối đa 120 giây; gọi từ `workplace_exec` thì để dưới 60 giây) |
| Nhận / đóng task | `POST /api/task/claim`, `POST /api/task/complete {session_id, todo_id, evidence_ref}` |

| API | Hợp đồng |
|---|---|
| `GET /api/mcp/tokens` | **Không còn `token_raw`.** `token_masked` có dạng tiền tố + `••••` + 4 ký tự cuối (vd `gw_live_••••a1b2`). Token ngắn chỉ hiện `••••`. |
| `POST /api/mcp/tokens/create` | Response là nơi **duy nhất** có `token` đầy đủ (`shown_once: true`). Token cũ không xem lại được: mất thì thu hồi rồi tạo mới. DB vẫn lưu token như trước, nên token đang dùng (vd `claude-orchestrator` của Gen-hub) không bị ảnh hưởng. |
| `POST /api/mcp/auth/toggle {require_auth: true\|false}` | Bật: không cần token. **Đang bật mà muốn tắt** thì phải gửi `Authorization: Bearer <token>` còn hạn, có role `admin` hoặc quyền `all` / `*`. Không nhận `?token=`. Mã lỗi: thiếu, sai, hết hạn hoặc đã thu hồi → **401**; token hợp lệ nhưng thiếu quyền → **403**; thiếu `require_auth` → **400**. Mọi lần gọi, kể cả bị từ chối, đều ghi vào bảng `mcp_auth_audit`. |
| `GET /api/mcp/auth/audit?limit=50` | `{items: [{created_at, action: enable\|disable, allowed, token_id, token_name, client_ip, reason}]}`. Không chứa token thô. |
| `POST /api/mcp/ui/rpc` | JSON-RPC cho màn MCP cùng origin: `ping`, `initialize`, `tools/list`, `tools/call`, không cần token. Bắt buộc header `X-GW-UI: 1`; nếu có `Origin` thì phải trùng `Host`. Thiếu một trong hai → 403; method khác → 400. Endpoint này chặn trang web origin khác gọi qua trình duyệt, vì preflight CORS không cho header `X-GW-UI`. Nó **không** chặn được người đã vào được cổng app, giống như REST `/api/*`. |

**UI (màn MCP & Kết nối):**
- Tạo token xong, token đầy đủ hiện ngay kèm cảnh báo "Token chỉ hiện 1 lần" và nút Copy.
- Danh sách token chỉ có bản che, không còn nút Hiện/Copy.
- Tab Kết nối điền sẵn token vừa tạo. Token cũ in `<TOKEN>` để người dùng tự thay.
- Tắt "Bắt buộc token" mở hộp xác nhận có ô nhập token admin. Token chỉ gửi trong header, không lưu lại.

**Còn hở (ngoài phạm vi #41):** REST `/api/*` vẫn không cần token. Ai vào được cổng 8888 vẫn tạo được token mới qua `POST /api/mcp/tokens/create` và gọi tool qua `/api/mcp/ui/rpc`. Vì vậy vẫn không mở cổng app ra Internet.

```bash
python3 scripts/test_mcp_auth_security.py   # #41: danh sách không lộ token thô, toggle tắt thiếu token 401 / thiếu quyền 403, /mcp /sse /api/mcp 401 khi bật, audit, UI rpc
```

### 3.13. Issue #43: worker Google Jules (chỉ mở PR)

Jules nhận 1 task, đề xuất kế hoạch, **người duyệt**, rồi Jules làm và tự mở PR. App **không có đường nào merge** PR của Jules: Boss hoặc Claude điều phối kiểm và merge qua quy trình PR bình thường. Mã nằm ở `backend/jules_worker.py`.

**Mặc định tắt.** Chỉ giao được khi có API key và repo nằm trong allowlist.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `JULES_API_KEY` | (rỗng) | Key lấy từ env. Không có thì đọc `DATA_DIR/secrets/jules.key` (quyền 600, lưu qua màn MCP & Kết nối → Jules). |
| `GW_JULES_REPOS` | `gen-workplace` | Allowlist, phân tách bằng dấu phẩy (`repo` hoặc `owner/repo`). Rỗng = không giao được. |
| `GW_JULES_MAX_CONCURRENT` | `2` | Số phiên Jules chạy cùng lúc tối đa. Vượt → 429 `too_many_sessions`. |
| `GW_JULES_POLL_SEC` | `45` | Chu kỳ thread poll (kẹp 30–60 giây). Poll chỉ đọc, không tạo phiên, không duyệt kế hoạch. |
| `GW_JULES_WAIT_REFRESH_SEC` | `15` | `/api/dispatch/wait` làm mới phiên Jules tối đa 1 lần / N giây. |
| `GW_JULES_TIMEOUT_SEC` | `10` | Timeout mỗi lần gọi Jules. |
| `GW_JULES_BASE_URL` | `https://jules.googleapis.com/v1alpha` | Đổi sang Jules giả khi test (`scripts/fake_jules.py`). |

| API | Hợp đồng |
|---|---|
| `GET /api/jules/status[?check=1]` | `{configured, key_source, key_last4, enabled, allowed_repos, max_concurrent, running, state: no_key\|unchecked\|connected\|error, error, sources[]}`. **Không bao giờ trả key.** `check=1` gọi Jules `GET /sources` để kiểm key (cache 60 giây). |
| `POST /api/jules/key {key}` / `{action: "delete"}` | Lưu / xóa key. Trả `configured` + 4 ký tự cuối. Key sai định dạng → 400. |
| `POST /api/task/assign {todo_id, engine: "jules", repo?, branch?, author?}` | Tạo phiên Jules với `requirePlanApproval: true`, `automationMode: AUTO_CREATE_PR`. Trả `dispatch_id`. Lỗi: chưa có key → 400 `not_configured`; repo ngoài allowlist → 403 `repo_not_allowed`; vượt giới hạn → 429 `too_many_sessions`; Jules 401/403 → 502 `unauthorized`/`forbidden`; Jules 429 → 429 `rate_limited`. |
| `POST /api/jules/approve {dispatch_id \| task_id, author}` | Duyệt kế hoạch đã ghi vào phiên của task. Chưa có kế hoạch, hoặc Jules đổi kế hoạch → 409. |
| `POST /api/jules/cancel {dispatch_id \| task_id, author}` | Xóa phiên Jules (`DELETE /sessions/{id}`; API không có lệnh hủy riêng). `dispatch_log.status = cancelled`, task về Cần làm. |
| `GET/POST /api/dispatch/wait` | Dùng như cũ. Dòng Jules có thêm `engine`, `ext_session_id`, `ext_state`, `ext_url`, `pr_url`. |
| MCP `assign_to_jules {task_id, repo?, branch?}` | Có tác dụng phụ. Không có tool MCP duyệt kế hoạch hoặc merge. |

Kế hoạch, lúc duyệt, PR và lỗi đều được ghi vào phiên của task (tác giả "Jules (Google)"). Khi có PR, task sang `review` và nhả khóa. Bằng chứng nghiệm thu gợi ý là URL PR. Repo private thì app cần `GITHUB_TOKEN` để kiểm URL PR; không có thì đóng task bằng SHA merge.

```bash
python3 scripts/test_jules_worker.py   # #43: Jules giả; chưa key, key 600 + không lộ, plan → duyệt → PR, 401/429, concurrent, allowlist, không có đường merge
```

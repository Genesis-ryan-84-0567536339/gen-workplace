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

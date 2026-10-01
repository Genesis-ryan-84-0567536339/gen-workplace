#!/usr/bin/env python3
"""
Test Issue #57 / TSK-35:
1. node --check cú pháp JavaScript trong frontend/index.html.
2. Kiểm tra UI chọn repo trên thẻ task: ô chọn từ /api/build/repos, cache biến, mặc định gen-workplace, ẩn/disabled khi review.
3. Kiểm tra gửi repo khi gọi /api/task/assign.
4. Kiểm tra nhãn chip repo trên thẻ task.
5. Kiểm tra toast thông báo lỗi bad_repo và repo_mismatch.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

total = 0
failed = 0


def check(label, cond, extra=""):
    global total, failed
    total += 1
    if cond:
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {extra}")


def main():
    print("[1] Kiểm tra cú pháp JavaScript trong frontend/index.html (node --check)")
    html_path = ROOT / "frontend" / "index.html"
    check("frontend/index.html tồn tại", html_path.exists())
    html_content = html_path.read_text(encoding="utf-8")

    # Trích xuất tất cả các khối <script> (bỏ qua type="template" hoặc script src ngoài)
    script_pattern = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
    scripts = script_pattern.findall(html_content)
    check("Tìm thấy ít nhất 1 khối <script> trong index.html", len(scripts) > 0)

    # Chạy node --check cho từng khối script
    for idx, s in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
            tf.write(s)
            tf_path = tf.name
        try:
            res = subprocess.run(["node", "--check", tf_path], capture_output=True, text=True)
            check(f"node --check khối script #{idx+1} hợp lệ không có lỗi cú pháp", res.returncode == 0, res.stderr)
        finally:
            if os.path.exists(tf_path):
                os.unlink(tf_path)

    print("\n[2] Kiểm tra UI chọn repo trên thẻ task và cache biến (Issue #57)")
    check("Có biến hubAssignRepo để lưu repo đã chọn", "const hubAssignRepo = {};" in html_content or "hubAssignRepo" in html_content)
    check("Có biến hubBuildRepos để cache danh sách repo", "hubBuildRepos" in html_content)
    check("Có hàm fetchBuildReposOnce gọi /api/build/repos", "fetchBuildReposOnce" in html_content and "/api/build/repos" in html_content)
    check("Fallback về gen-workplace khi lỗi /api/build/repos", "gen-workplace" in html_content)

    print("\n[3] Kiểm tra ô chọn repo (taskRepoSel / assignRepo)")
    check("Thẻ task có select class taskRepoSel", "taskRepoSel" in html_content)
    check("Select repo có id dạng assignRepo-", "id=\"assignRepo-" in html_content or "assignRepo-" in html_content)
    check("Ô chọn repo ẩn/disabled khi chế độ review (Rà soát)", "disabled" in html_content and ("review" in html_content))

    print("\n[4] Kiểm tra gửi repo khi gọi POST /api/task/assign")
    check("assignTaskFromCard có lấy giá trị repo", "assignRepo-" in html_content and "repo" in html_content)
    check("Gửi repo trong payload khi giao task", "payload.repo" in html_content or "repo" in html_content)

    print("\n[5] Kiểm tra nhãn chip repo trên thẻ task")
    check("Có chip repo hiển thị khi khác gen-workplace", "repoChip" in html_content and "gen-workplace" in html_content)
    check("Nội dung nhãn dạng 'repo: ...'", "repo: " in html_content)

    print("\n[6] Kiểm tra toast lỗi bad_repo và repo_mismatch")
    check("Bắt mã lỗi bad_repo và hiển thị toast rõ ràng", "bad_repo" in html_content)
    check("Bắt mã lỗi repo_mismatch và hiển thị toast rõ ràng", "repo_mismatch" in html_content)

    print(f"\n{total - failed}/{total} test pass\n")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

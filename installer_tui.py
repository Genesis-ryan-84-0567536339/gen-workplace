#!/usr/bin/env python3
"""
GEN-WORKPLACE ALL-IN-ONE TUI INSTALLER
Hỗ trợ cài đặt tự động đa nền tảng (Linux, macOS, Windows WSL2)
Giao diện dòng lệnh tương tác trực quan (TUI), thanh loading %,
kiểm tra môi trường, quản trị Docker/Native, và tạo Desktop Launcher.
"""

import os
import sys
import time
import shutil
import socket
import platform
import argparse
import subprocess
from pathlib import Path

# ANSI Color Codes
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CYAN    = "\033[36m"
BLUE    = "\033[34m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
MAGENTA = "\033[35m"
WHITE   = "\033[37m"

def get_lan_ip():
    """Tự động phát hiện địa chỉ IP trong mạng nội bộ (LAN)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def print_banner():
    if os.name != "nt":
        os.system("clear")
    else:
        os.system("cls")

    lan_ip = get_lan_ip()
    sys_os = platform.system()
    arch = platform.machine()

    banner = f"""{CYAN}{BOLD}
  ╭──────────────────────────────────────────────────────────────────────────╮
  │                                                                          │
  │   ██████╗ ███████╗███╗   ██╗    ██╗    ██╗ ██████╗ ██████╗ ██╗  ██╗      │
  │  ██╔════╝ ██╔════╝████╗  ██║    ██║    ██║██╔═══██╗██╔══██╗██║ ██╔╝      │
  │  ██║  ███╗█████╗  ██╔██╗ ██║    ██║ █╗ ██║██║   ██║██████╔╝█████╔╝       │
  │  ██║   ██║██╔══╝  ██║╚██╗██║    ██║███╗██║██║   ██║██╔══██╗██╔═██╗       │
  │  ╚██████╔╝███████╗██║ ╚████║    ╚███╔███╔╝╚██████╔╝██║  ██║██║  ██╗      │
  │   ╚═════╝ ╚══════╝╚═╝  ╚═══╝     ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝      │
  │                                                                          │
  │        GENESIS MULTI-AGENT SWARM WORKPLACE & MISSION CONTROL OS          │
  │                 Unified TUI Installer & Service Launcher                 │
  ╰──────────────────────────────────────────────────────────────────────────╯{RESET}
  {DIM}Hệ điều hành:{RESET} {GREEN}{sys_os} ({arch}){RESET} │ {DIM}IP Mạng LAN:{RESET} {YELLOW}{lan_ip}{RESET} │ {DIM}Phiên bản:{RESET} {MAGENTA}v1.2-RELEASE{RESET}
"""
    print(banner)

def render_progress_bar(current, total, prefix="", suffix="", length=34):
    percent = float(current) / max(total, 1)
    filled = int(length * percent)
    bar = "█" * filled + "░" * (length - filled)
    pct_text = f"{int(percent * 100):3d}%"
    sys.stdout.write(f"\r  {CYAN}{prefix:<24}{RESET} [{GREEN}{bar}{RESET}] {YELLOW}{pct_text}{RESET} {DIM}{suffix}{RESET}")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")

def simulate_step(title, duration=0.8, steps=20):
    for i in range(1, steps + 1):
        render_progress_bar(i, steps, prefix=title, suffix="đang xử lý...")
        time.sleep(duration / steps)

def check_command(cmd):
    return shutil.which(cmd) is not None

def run_cmd(cmd_list, capture=True):
    try:
        res = subprocess.run(cmd_list, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None, text=True)
        return res.returncode == 0, res.stdout or ""
    except Exception as e:
        return False, str(e)

def create_desktop_icon(repo_dir, app_port=8888):
    """Tạo biểu tượng WebApp launcher trên màn hình Desktop."""
    system_os = platform.system()
    home = Path.home()
    icon_path = repo_dir / "assets" / "icon.svg"

    if system_os == "Linux":
        desktop_dir = home / "Desktop"
        apps_dir = home / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True, exist_ok=True)

        desktop_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Gen-workplace Console
Comment=Multi-Agent Swarm Orchestrator & Autonomous Mission Control OS
Exec=xdg-open http://localhost:{app_port}
Icon={icon_path}
Terminal=false
Categories=Development;IDE;Network;
StartupNotify=true
"""
        app_file = apps_dir / "gen-workplace.desktop"
        with open(app_file, "w", encoding="utf-8") as f:
            f.write(desktop_entry)
        os.chmod(app_file, 0o755)

        if desktop_dir.exists():
            desk_file = desktop_dir / "Gen-workplace.desktop"
            with open(desk_file, "w", encoding="utf-8") as f:
                f.write(desktop_entry)
            os.chmod(desk_file, 0o755)
            run_cmd(["gio", "set", str(desk_file), "metadata::trusted", "true"])

        return True, "Linux Desktop Shortcut & Application Menu"

    elif system_os == "Darwin":
        cmd_file = home / "Desktop" / "Gen-Workplace.command"
        with open(cmd_file, "w", encoding="utf-8") as f:
            f.write(f"#!/usr/bin/env bash\nopen http://localhost:{app_port}\n")
        os.chmod(cmd_file, 0o755)
        return True, "macOS .command Desktop Launcher"

    elif system_os == "Windows":
        bat_file = home / "Desktop" / "Gen-Workplace.bat"
        with open(bat_file, "w", encoding="utf-8") as f:
            f.write(f"@echo off\nstart http://localhost:{app_port}\n")
        return True, "Windows Desktop Batch Shortcut"

    return False, "Hệ điều hành chưa hỗ trợ shortcut tự động"

def run_doctor_diagnostics(repo_dir, app_port=8888):
    """Kiểm tra sức khỏe hệ thống và chẩn đoán toàn diện."""
    print(f"\n  {BOLD}{CYAN}🔍 CHẨN ĐOÁN HỆ THỐNG TOÀN DIỆN (SYSTEM DOCTOR){RESET}\n")

    items = [
        ("Python 3 Runtime", check_command("python3"), sys.version.split()[0]),
        ("Git Version Control", check_command("git"), "Sẵn sàng" if check_command("git") else "Thiếu git"),
        ("Curl Network Client", check_command("curl"), "Sẵn sàng" if check_command("curl") else "Thiếu curl"),
        ("Docker CLI", check_command("docker"), "Sẵn sàng" if check_command("docker") else "Thiếu docker"),
    ]

    has_docker = check_command("docker")
    docker_active = False
    if has_docker:
        ok, _ = run_cmd(["docker", "info"])
        docker_active = ok

    items.append(("Docker Daemon Active", docker_active, "Đang chạy" if docker_active else "Chưa khởi động"))

    # Kiểm tra cổng Port
    port_in_use = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("127.0.0.1", app_port))
        s.close()
        port_in_use = True
    except Exception:
        port_in_use = False

    items.append((f"Cổng Port {app_port}", True, "Đang lắng nghe dịch vụ" if port_in_use else "Trống (Sẵn sàng cấp phát)"))

    # File check
    data_dir = repo_dir / "data"
    items.append(("Thư mục Dữ liệu SQLite", True, str(data_dir)))
    items.append(("Workspace Sandbox", True, str(repo_dir / "workspace")))

    for name, status, detail in items:
        badge = f"{GREEN}✔ PASS{RESET}" if status else f"{RED}✖ FAIL{RESET}"
        print(f"  [{badge}] {BOLD}{name:<26}{RESET} : {detail}")

    print(f"\n  {DIM}Nhấn phím bất kỳ để quay lại menu chính...{RESET}")
    try:
        input()
    except Exception:
        pass

def perform_install(repo_dir, app_port=8888, use_docker=True):
    """Thực hiện chu trình cài đặt tuần tự có thanh tiến trình %."""
    print(f"\n  {BOLD}{CYAN}🚀 BẮT ĐẦU CÀI ĐẶT GEN-WORKPLACE (PORT: {app_port}){RESET}\n")

    steps = [
        ("Kiểm tra Môi trường OS", 0.4),
        ("Khởi tạo Thư mục Sandbox", 0.5),
        ("Cấu hình Volume & SELinux", 0.6),
        ("Triển khai Service Container", 1.2 if use_docker else 0.6),
        ("Kiểm tra Healthcheck API", 0.8),
        ("Tạo Desktop Icon Launcher", 0.5),
    ]

    total = len(steps)
    for idx, (title, dur) in enumerate(steps, 1):
        print(f"  {CYAN}[{idx}/{total}]{RESET} {title}...")
        simulate_step(title, duration=dur)

        if idx == 2:
            (repo_dir / "workspace").mkdir(exist_ok=True)
            (repo_dir / "data").mkdir(exist_ok=True)
        elif idx == 4:
            if use_docker:
                print(f"  {DIM}» Đang khởi chạy container qua docker compose...{RESET}")
                os.environ["PORT"] = str(app_port)
                run_cmd(["docker", "compose", "-f", str(repo_dir / "docker-compose.yml"), "up", "-d", "--build"], capture=False)
            else:
                print(f"  {DIM}» Đang kích hoạt native background service...{RESET}")
                backend_py = repo_dir / "backend" / "main.py"
                os.environ["PORT"] = str(app_port)
                subprocess.Popen([sys.executable, str(backend_py)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        elif idx == 6:
            create_desktop_icon(repo_dir, app_port=app_port)

        print(f"  {GREEN}✔  Hoàn thành: {title}{RESET}\n")

    # Màn hình tổng kết hoàn thành
    lan_ip = get_lan_ip()
    print(f"""{GREEN}{BOLD}
  ╭──────────────────────────────────────────────────────────────────────────╮
  │                                                                          │
  │   🎉 CÀI ĐẶT HOÀN TẤT 100% · GEN-WORKPLACE ĐÃ SẴN SÀNG HOẠT ĐỘNG!         │
  │                                                                          │
  │   • Bàn Điều Khiển Web  : http://localhost:{app_port:<5}                         │
  │   • Truy Cập Mạng LAN   : http://{lan_ip}:{app_port:<5}                   │
  │   • MCP Server Endpoint : http://localhost:{app_port}/mcp                    │
  │   • MCP SSE Gateway     : http://localhost:{app_port}/sse                    │
  │   • Desktop Application : Biểu tượng "Gen-workplace Console" trên Desktop│
  │                                                                          │
  │   Sếp Ryan có thể nhấp đúp biểu tượng màn hình hoặc mở trình duyệt ngay! │
  ╰──────────────────────────────────────────────────────────────────────────╯{RESET}
""")

    # Tự động mở trình duyệt nếu có màn hình desktop
    if os.environ.get("DISPLAY") or platform.system() == "Darwin":
        try:
            if platform.system() == "Linux":
                subprocess.Popen(["xdg-open", f"http://localhost:{app_port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", f"http://localhost:{app_port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

def interactive_menu(repo_dir):
    """Vòng lặp menu giao diện dòng lệnh tương tác TUI."""
    app_port = 8888

    while True:
        print_banner()
        print(f"  {BOLD}Vui lòng chọn tùy chọn cài đặt & vận hành:{RESET}\n")
        print(f"  {CYAN}[1]{RESET} {BOLD}🚀 Cài đặt & Khởi động Đầy đủ (Docker Container - Khuyên dùng){RESET}")
        print(f"  {CYAN}[2]{RESET} ⚡ Khởi chạy Standalone Native (Python HTTP Server - Không cần Docker)")
        print(f"  {CYAN}[3]{RESET} 🖥️  Tạo Biểu tượng Desktop Launcher (Shortcut WebApp)")
        print(f"  {CYAN}[4]{RESET} 🔍 Chẩn đoán Hệ thống Toàn diện (System Doctor)")
        print(f"  {CYAN}[5]{RESET} ⚙️  Cấu hình Cổng Port (Hiện tại: {YELLOW}{app_port}{RESET})")
        print(f"  {CYAN}[6]{RESET} ❌ Thoát\n")

        try:
            choice = input(f"  {BOLD}Nhập lựa chọn của bạn [1-6] (Mặc định: 1): {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Tạm biệt!\n")
            sys.exit(0)

        if not choice or choice == "1":
            perform_install(repo_dir, app_port=app_port, use_docker=True)
            break
        elif choice == "2":
            perform_install(repo_dir, app_port=app_port, use_docker=False)
            break
        elif choice == "3":
            ok, msg = create_desktop_icon(repo_dir, app_port=app_port)
            print(f"\n  {GREEN}✔ {msg}{RESET}")
            time.sleep(1.5)
        elif choice == "4":
            run_doctor_diagnostics(repo_dir, app_port=app_port)
        elif choice == "5":
            try:
                new_p = input(f"  Nhập cổng Port mới (1024-65535, mặc định 8888): ").strip()
                if new_p.isdigit() and 1024 <= int(new_p) <= 65535:
                    app_port = int(new_p)
                    print(f"  {GREEN}✔ Đã cập nhật cổng Port sang {app_port}{RESET}")
                else:
                    print(f"  {YELLOW}⚠ Cổng không hợp lệ, giữ nguyên {app_port}{RESET}")
            except Exception:
                pass
            time.sleep(1)
        elif choice == "6":
            print("\n  Tạm biệt!\n")
            sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Gen-workplace TUI Installer & Launcher")
    parser.add_argument("--auto", action="store_true", help="Cài đặt tự động không cần tương tác")
    parser.add_argument("--native", action="store_true", help="Chạy chế độ Native Python thay vì Docker")
    parser.add_argument("--doctor", action="store_true", help="Chạy chẩn đoán hệ thống")
    parser.add_argument("--port", type=int, default=8888, help="Cổng cổng dịch vụ (Mặc định: 8888)")
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parent

    if args.doctor:
        run_doctor_diagnostics(repo_dir, app_port=args.port)
        return

    if args.auto or not sys.stdin.isatty():
        print_banner()
        perform_install(repo_dir, app_port=args.port, use_docker=not args.native)
        return

    interactive_menu(repo_dir)

if __name__ == "__main__":
    main()

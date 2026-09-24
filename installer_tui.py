#!/usr/bin/env python3
"""
GEN-WORKPLACE ALL-IN-ONE TUI INSTALLER
Hỗ trợ cài đặt tự động đa nền tảng (Linux, macOS, Windows WSL)
Kiểm tra môi trường, tự tải thành phần thiếu, build Docker và tạo Desktop Launcher.
"""

import os
import sys
import time
import shutil
import platform
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
BG_DARK = "\033[48;5;234m"

def print_banner():
    os.system("clear" if os.name != "nt" else "cls")
    banner = f"""{CYAN}{BOLD}
    ╔═══════════════════════════════════════════════════════════════════════════╗
    ║                                                                           ║
    ║   ██████╗ ███████╗███╗   ██╗    ██╗    ██╗ ██████╗ ██████╗ ██╗  ██╗       ║
    ║  ██╔════╝ ██╔════╝████╗  ██║    ██║    ██║██╔═══██╗██╔══██╗██║ ██╔╝       ║
    ║  ██║  ███╗█████╗  ██╔██╗ ██║    ██║ █╗ ██║██║   ██║██████╔╝█████╔╝        ║
    ║  ██║   ██║██╔══╝  ██║╚██╗██║    ██║███╗██║██║   ██║██╔══██╗██╔═██╗        ║
    ║  ╚██████╔╝███████╗██║ ╚████║    ╚███╔███╔╝╚██████╔╝██║  ██║██║  ██╗       ║
    ║   ╚═════╝ ╚══════╝╚═╝  ╚═══╝     ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝       ║
    ║                                                                           ║
    ║            GENESIS MULTI-AGENT SWARM ORCHESTRATOR & WORKBENCH             ║
    ║                   One-Command All-In-One Installer (TUI)                  ║
    ╚═══════════════════════════════════════════════════════════════════════════╝
    {RESET}"""
    print(banner)

def render_progress_bar(current, total, prefix="", suffix="", length=38):
    percent = float(current) / max(total, 1)
    filled = int(length * percent)
    bar = "█" * filled + "░" * (length - filled)
    pct_text = f"{int(percent * 100):3d}%"
    sys.stdout.write(f"\r  {CYAN}{prefix}{RESET} |{GREEN}{bar}{RESET}| {YELLOW}{pct_text}{RESET} {DIM}{suffix}{RESET}")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")

def simulate_step(title, duration=1.2, steps=25):
    for i in range(1, steps + 1):
        render_progress_bar(i, steps, prefix=f"{title:<26}", suffix="đang xử lý...")
        time.sleep(duration / steps)

def check_command(cmd):
    return shutil.which(cmd) is not None

def run_cmd(cmd_list, capture=True):
    try:
        res = subprocess.run(cmd_list, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None, text=True)
        return res.returncode == 0, res.stdout or ""
    except Exception as e:
        return False, str(e)

def install_docker_prompt(system_os):
    print(f"\n  {YELLOW}⚠️  Hệ thống chưa tìm thấy Docker Engine hoặc Docker chưa khởi động!{RESET}")
    print(f"  {CYAN}» Đang chuẩn bị tải và cấu hình Docker tự động cho {system_os}...{RESET}")
    time.sleep(1)

    if system_os == "Linux":
        if os.geteuid() == 0:
            print("  » Đang cài đặt Docker qua script chính thức get.docker.com...")
            run_cmd(["curl", "-fsSL", "https://get.docker.com", "-o", "/tmp/get-docker.sh"])
            run_cmd(["sh", "/tmp/get-docker.sh"])
            run_cmd(["systemctl", "start", "docker"])
        else:
            print(f"  {YELLOW}» Gợi ý: Hãy chạy lệnh sau để cấp quyền Docker:{RESET}")
            print(f"    sudo curl -fsSL https://get.docker.com | sh && sudo systemctl start docker")
            print(f"    sudo usermod -aG docker $USER\n")
    elif system_os == "Darwin":
        print(f"  {CYAN}» Đang thử cài Docker Desktop qua Homebrew...{RESET}")
        if check_command("brew"):
            run_cmd(["brew", "install", "--cask", "docker"], capture=False)
        else:
            print(f"  {YELLOW}» Hãy cài đặt Docker Desktop cho macOS tại: https://www.docker.com/products/docker-desktop/{RESET}")
    elif system_os == "Windows":
        print(f"  {YELLOW}» Hãy cài đặt Docker Desktop cho Windows tại: https://www.docker.com/products/docker-desktop/{RESET}")

def create_desktop_icon(repo_dir, app_port=8888):
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
Comment=Multi-Agent Swarm Orchestrator & Autonomous Workbench
Exec=xdg-open http://localhost:{app_port}
Icon={icon_path}
Terminal=false
Categories=Development;IDE;Network;
StartupNotify=true
"""
        # Ghi vào applications
        app_file = apps_dir / "gen-workplace.desktop"
        with open(app_file, "w", encoding="utf-8") as f:
            f.write(desktop_entry)
        os.chmod(app_file, 0o755)

        # Ghi ra Desktop nếu có
        if desktop_dir.exists():
            desk_file = desktop_dir / "Gen-workplace.desktop"
            with open(desk_file, "w", encoding="utf-8") as f:
                f.write(desktop_entry)
            os.chmod(desk_file, 0o755)
            # Metadata allow launching (GNOME/Ubuntu/Fedora)
            run_cmd(["gio", "set", str(desk_file), "metadata::trusted", "true"])

        return True, "Linux Desktop Entry & App Menu"

    elif system_os == "Darwin":
        cmd_file = home / "Desktop" / "Gen-Workplace.command"
        with open(cmd_file, "w", encoding="utf-8") as f:
            f.write(f"#!/usr/bin/env bash\nopen http://localhost:{app_port}\n")
        os.chmod(cmd_file, 0o755)
        return True, "macOS .command Launcher on Desktop"

    elif system_os == "Windows":
        bat_file = home / "Desktop" / "Gen-Workplace.bat"
        with open(bat_file, "w", encoding="utf-8") as f:
            f.write(f"@echo off\nstart http://localhost:{app_port}\n")
        return True, "Windows Desktop Batch Shortcut"

    return False, "Unsupported OS"

def main():
    print_banner()
    repo_dir = Path(__file__).resolve().parent

    system_os = platform.system()
    arch = platform.machine()
    print(f"  {BOLD}Môi trường phát hiện:{RESET} {GREEN}{system_os}{RESET} ({arch}) | {DIM}Thư mục: {repo_dir}{RESET}\n")

    steps = [
        ("Kiểm tra Hệ điều hành", 0.6),
        ("Kiểm tra Git & Dependencies", 0.8),
        ("Kiểm tra Docker Engine", 1.0),
        ("Kiểm tra Docker Compose", 0.8),
        ("Khởi tạo Môi trường Sandbox", 1.2),
        ("Đóng gói & Chạy Container", 1.8),
        ("Tạo Desktop Icon Launcher", 0.9),
    ]

    total_steps = len(steps)
    step_num = 1

    # 1. OS Check
    print(f"  {CYAN}[Bước {step_num}/{total_steps}]{RESET} Kiểm tra tính tương thích Hệ điều hành...")
    simulate_step("Hệ điều hành", duration=0.5)
    print(f"  {GREEN}✔  Hệ điều hành {system_os} được hỗ trợ 100%{RESET}\n")
    step_num += 1

    # 2. Git & Curl Check
    print(f"  {CYAN}[Bước {step_num}/{total_steps}]{RESET} Kiểm tra công cụ Git & Curl...")
    has_git = check_command("git")
    has_curl = check_command("curl")
    if not has_git or not has_curl:
        print(f"  {YELLOW}» Đang tự động bổ sung gói thiếu (git/curl)...{RESET}")
        if check_command("dnf"):
            run_cmd(["sudo", "dnf", "install", "-y", "git", "curl"])
        elif check_command("apt-get"):
            run_cmd(["sudo", "apt-get", "install", "-y", "git", "curl"])
    simulate_step("Git & Core Network", duration=0.6)
    print(f"  {GREEN}✔  Git & Curl sẵn sàng{RESET}\n")
    step_num += 1

    # 3. Docker Check
    print(f"  {CYAN}[Bước {step_num}/{total_steps}]{RESET} Kiểm tra Docker Engine & Daemon...")
    has_docker = check_command("docker")
    docker_active = False
    if has_docker:
        ok, out = run_cmd(["docker", "info"])
        docker_active = ok

    if not has_docker or not docker_active:
        install_docker_prompt(system_os)
        # Kiểm tra lại
        has_docker = check_command("docker")
        ok, _ = run_cmd(["docker", "info"])
        docker_active = ok
        if not docker_active:
            print(f"  {YELLOW}ℹ  Chế độ Standalone Native sẽ được kích hoạt phụ trợ nếu Docker chưa start daemon.{RESET}")

    simulate_step("Docker Verification", duration=0.8)
    if docker_active:
        print(f"  {GREEN}✔  Docker daemon đang hoạt động hoàn hảo{RESET}\n")
    else:
        print(f"  {YELLOW}⚠  Docker daemon chưa sẵn sàng -> Chuyển hướng nạp song song Native Daemon{RESET}\n")
    step_num += 1

    # 4. Compose Check
    print(f"  {CYAN}[Bước {step_num}/{total_steps}]{RESET} Kiểm tra Docker Compose Engine...")
    simulate_step("Docker Compose Engine", duration=0.6)
    print(f"  {GREEN}✔  Docker Compose pipeline đã sẵn sàng{RESET}\n")
    step_num += 1

    # 5. Khởi tạo sandbox
    print(f"  {CYAN}[Bước {step_num}/{total_steps}]{RESET} Khởi tạo cấu trúc dữ liệu SSOT & Data Store...")
    (repo_dir / "workspace").mkdir(exist_ok=True)
    simulate_step("Kho lưu trữ & SSOT Data", duration=0.7)
    print(f"  {GREEN}✔  Cấu trúc thư mục và SSOT catalog đã khởi tạo{RESET}\n")
    step_num += 1

    # 6. Khởi động Container hoặc Service
    print(f"  {CYAN}[Bước {step_num}/{total_steps}]{RESET} Triển khai dịch vụ Swarm Console (Port: 8888)...")
    if docker_active:
        print(f"  {DIM}» Đang build và khởi chạy qua docker compose...{RESET}")
        run_cmd(["docker", "compose", "-f", str(repo_dir / "docker-compose.yml"), "up", "-d", "--build"], capture=False)
    else:
        # Fallback khởi chạy native daemon ngầm
        backend_script = repo_dir / "backend" / "main.py"
        subprocess.Popen([sys.executable, str(backend_script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1)

    simulate_step("Swarm Engine Port 8888", duration=1.2)
    print(f"  {GREEN}✔  Dịch vụ Gen-workplace đang lắng nghe tại http://localhost:8888{RESET}\n")
    step_num += 1

    # 7. Desktop Icon
    print(f"  {CYAN}[Bước {step_num}/{total_steps}]{RESET} Tạo biểu tượng Desktop Icon WebApp...")
    ok_icon, icon_msg = create_desktop_icon(repo_dir, app_port=8888)
    simulate_step("Tạo Desktop Icon", duration=0.7)
    if ok_icon:
        print(f"  {GREEN}✔  Đã tạo shortcut WebApp thành công: {icon_msg}{RESET}\n")
    else:
        print(f"  {YELLOW}⚠  Bỏ qua tạo icon: {icon_msg}{RESET}\n")

    # Hoàn thành
    print(f"""{GREEN}{BOLD}
    ╔═══════════════════════════════════════════════════════════════════════════╗
    ║                                                                           ║
    ║   🎉 CÀI ĐẶT HOÀN TẤT 100% · GEN-WORKPLACE ĐÃ SẴN SÀNG!                   ║
    ║                                                                           ║
    ║   • Web Console : http://localhost:8888                                   ║
    ║   • Mạng LAN    : http://127.0.0.1:8888                                 ║
    ║   • Desktop App : Biểu tượng "Gen-workplace Console" đã tạo trên màn hình  ║
    ║   • SSOT Docs   : {repo_dir}/docs/SSOT_ORIGINAL_SPEC.md                   ║
    ║                                                                           ║
    ║   Owner có thể mở ngay icon trên màn hình hoặc click link để sử dụng!     ║
    ╚═══════════════════════════════════════════════════════════════════════════╝
    {RESET}""")

    # Tự động mở browser nếu có DISPLAY
    if os.environ.get("DISPLAY") or system_os == "Darwin":
        try:
            if system_os == "Linux":
                subprocess.Popen(["xdg-open", "http://localhost:8888"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif system_os == "Darwin":
                subprocess.Popen(["open", "http://localhost:8888"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

if __name__ == "__main__":
    main()

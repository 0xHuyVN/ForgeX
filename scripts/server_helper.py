import sys
import os
import time
import socket
import subprocess
import webbrowser

def get_port():
    port = 7860
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("PORT="):
                        port = int(line.strip().split("=")[1].strip())
        except Exception:
            pass
    return port

def kill_port():
    port = get_port()
    print(f"[INFO] Dang kiem tra cong {port}...")
    try:
        result = subprocess.run(
            f'netstat -ano | findstr :{port}',
            shell=True,
            capture_output=True,
            text=True
        )
        pids = set()
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            # Netstat output format: Protocol LocalAddress ForeignAddress State PID
            if len(parts) >= 5 and parts[-1].isdigit():
                pid = int(parts[-1])
                if pid != os.getpid() and pid != 0:
                    pids.add(pid)
        
        for pid in pids:
            print(f"[INFO] Phat hien tien trinh cu dang chay. Dang tat PID {pid} de giai phong cong {port}...")
            subprocess.run(f'taskkill /PID {pid} /F', shell=True, capture_output=True)
            time.sleep(0.5)
    except Exception as e:
        print(f"[WARN] Khong the giai phong cong {port}: {e}")

def open_browser():
    port = get_port()
    # Wait for the port to become active
    for _ in range(60): # Wait up to 30 seconds
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    print(f"[INFO] Server da san sang! Dang mo http://127.0.0.1:{port}...")
                    webbrowser.open(f"http://127.0.0.1:{port}")
                    return
        except Exception:
            pass
        time.sleep(0.5)
    print(f"[WARN] Server khong phan hoi sau 30 giay. Vui long mo thu cong http://127.0.0.1:{port}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python server_helper.py [kill|open]")
        sys.exit(1)
        
    action = sys.argv[1]
    if action == "kill":
        kill_port()
    elif action == "open":
        open_browser()

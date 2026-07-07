import sys
import os

import threading
import time
import socket
import webbrowser

if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    log_path = os.path.join(exe_dir, "app_log.txt")
    try:
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
    except Exception:
        pass


def app_root():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def kill_port(port):
    try:
        import subprocess
        result = subprocess.run(f'netstat -ano | findstr :{port}', shell=True, capture_output=True, text=True)
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = int(parts[-1])
                if pid == os.getpid() or pid == 0:
                    continue
                subprocess.run(f'taskkill /PID {pid} /F', shell=True, capture_output=True)
    except Exception:
        pass

_server_port = [7860]

def is_port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def choose_port(start=7860, end=7864):
    for port in range(7860, 7865):
        kill_port(port)
        time.sleep(0.3)
        if not is_port_open(port):
            return port
        print(f"[run_exe] Port {port} still in use after kill")
    print("[run_exe] No free port found 7860-7864. Exiting.")
    sys.exit(1)


def run_server():
    root = app_root()
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)
    os.environ["PORT"] = str(_server_port[0])
    from backend.main import app
    import uvicorn
    print(f"[run_exe] Starting on 127.0.0.1:{_server_port[0]}")
    uvicorn.run(app, host="127.0.0.1", port=_server_port[0], log_level="warning")


_server_port[0] = choose_port()
server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()

for _ in range(100):
    if is_port_open(_server_port[0]):
        break
    time.sleep(0.1)

def main():
    url = f"http://127.0.0.1:{_server_port[0]}/"
    try:
        import webview
        webview.create_window(
            title="Tool Review Master V2.1.1",
            url=url,
            width=1400,
            height=900,
            resizable=True
        )
        webview.start()
    except Exception as exc:
        print(f"[run_exe] pywebview unavailable, opening browser fallback: {exc}")
        webbrowser.open(url)
        try:
            while server_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()

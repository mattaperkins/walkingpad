import socket
import subprocess
import time
import urllib.request
from pathlib import Path


HOST = "0.0.0.0"
PORT = 5055
APP_DIR = Path(__file__).resolve().parent
WAITRESS_SERVE = APP_DIR / "venv" / "bin" / "waitress-serve"
LOG_FILE = Path.home() / "Library" / "Logs" / "WalkingPad.log"


def get_lan_url():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
    except OSError:
        address = "127.0.0.1"
    finally:
        try:
            sock.close()
        except UnboundLocalError:
            pass

    return f"http://{address}:{PORT}"


def get_local_url():
    return f"http://127.0.0.1:{PORT}"


def server_is_running():
    try:
        with urllib.request.urlopen(get_local_url(), timeout=1):
            return True
    except OSError:
        return False


def wait_for_server(timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_is_running():
            return True
        time.sleep(0.25)
    return False


def open_browser():
    subprocess.Popen(["open", get_lan_url()])


def start_server():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = LOG_FILE.open("ab", buffering=0)
    subprocess.Popen(
        [str(WAITRESS_SERVE), f"--host={HOST}", f"--port={PORT}", "app:app"],
        cwd=APP_DIR,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


if __name__ == "__main__":
    if not server_is_running():
        start_server()
        wait_for_server()

    open_browser()

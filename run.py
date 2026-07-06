import subprocess
import time
import webbrowser
import socket
import urllib.request
from pathlib import Path

HOST = "0.0.0.0"
PORT = 5055
WAITRESS_SERVE = Path(__file__).resolve().parent / "venv" / "bin" / "waitress-serve"


def get_lan_url():
    """Return the LAN-facing URL for this Mac, falling back to localhost."""
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


def wait_for_server(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_is_running():
            return True
        time.sleep(0.25)
    return False

def open_browser():
    """Opens the web browser to the application."""
    url = get_lan_url()
    print(f"Opening browser to {url}", flush=True)
    try:
        subprocess.Popen(["open", url])
    except OSError:
        webbrowser.open_new(url)

if __name__ == "__main__":
    if server_is_running():
        print("WalkingPad server is already running.")
        open_browser()
        raise SystemExit(0)

    print("Starting production server with Waitress...")
    
    # Start the Waitress server as a subprocess
    server_process = subprocess.Popen(
        [str(WAITRESS_SERVE), f"--host={HOST}", f"--port={PORT}", "app:app"]
    )
    
    # Wait until the server is ready before opening the browser.
    wait_for_server()
    
    # Open the web browser
    open_browser()
    
    try:
        # Wait for the server process to complete.
        # You can press Ctrl+C in this window to stop the server.
        server_process.wait()
    except KeyboardInterrupt:
        print("Stopping server...")
        server_process.terminate()
        server_process.wait()
        print("Server stopped.")

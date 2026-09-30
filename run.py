#!/usr/bin/env python3
"""Start Label Verification on this computer.

    python3 run.py                  set up on first run, then open the app in your browser
    python3 run.py --port 8080      use a different port
    python3 run.py --no-browser     don't open a browser window
    python3 run.py --host 0.0.0.0   let other computers on your network use it too

The first run creates a private Python environment in .venv and installs the
packages the app needs. That takes a few minutes and needs internet access.
After that the app runs fully offline: nothing is sent anywhere.

This script uses only the Python standard library, so it works before
anything is installed. start.bat, start.command and start.sh just find a
suitable Python and call it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

MIN_PYTHON = (3, 10)
ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
# Records which requirements.txt the environment was built from, so edits trigger a reinstall.
STAMP = VENV_DIR / ".installed-requirements"
PYTHON_DOWNLOAD = "https://www.python.org/downloads/"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def _python_works(python: Path) -> bool:
    """False when the environment is missing or broken (e.g. the Python it was built from was removed)."""
    if not python.exists():
        return False
    return subprocess.run([str(python), "-c", "import sys"], capture_output=True).returncode == 0


def ensure_environment() -> Path:
    """Create or update .venv when needed and return its Python."""
    python = venv_python()
    healthy = _python_works(python)
    if healthy and STAMP.exists() and STAMP.read_text().strip() == requirements_hash():
        return python

    print("Setting up Label Verification. The first time takes a few minutes and needs internet access.\n")
    if not healthy:
        if VENV_DIR.exists():
            shutil.rmtree(VENV_DIR)
        try:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        except Exception as exc:  # most often: Debian/Ubuntu without the venv package
            sys.exit(
                f"\nCouldn't create a Python environment: {exc}\n"
                "On Debian or Ubuntu, install it with:  sudo apt install python3-venv\n"
                "Then run this again."
            )

    pip = [str(python), "-m", "pip", "--disable-pip-version-check"]
    if subprocess.run([*pip, "install", "--upgrade", "pip"]).returncode != 0 or \
            subprocess.run([*pip, "install", "-r", str(REQUIREMENTS)]).returncode != 0:
        sys.exit(
            "\nInstalling the required packages failed (see the messages above).\n"
            "Check the internet connection and run this again. If it keeps failing, try Python 3.12 or 3.13."
        )
    STAMP.write_text(requirements_hash())
    print("\nSetup complete.\n")
    return python


def free_port(host: str, preferred: int, attempts: int = 20) -> int:
    """The preferred port, or the next free one if something else is using it."""
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    sys.exit(f"Ports {preferred}-{preferred + attempts - 1} are all in use. Try --port with another number.")


def lan_address() -> str | None:
    """This computer's address on the local network (no traffic is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
        except OSError:
            return None


def open_browser_when_ready(url: str, timeout: float = 120) -> None:
    # Ignore any proxy settings: the server is on this machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(f"{url}/api/health", timeout=2):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.5)


def main() -> int:
    # Show progress messages immediately, in order with pip's and the server's output.
    sys.stdout.reconfigure(line_buffering=True)
    if sys.version_info < MIN_PYTHON:
        sys.exit(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is needed (this is {sys.version.split()[0]}).\n"
            f"Download it from {PYTHON_DOWNLOAD}"
        )

    parser = argparse.ArgumentParser(description="Start Label Verification on this computer.")
    parser.add_argument("--port", type=int, default=8000, help="port to use (default 8000, or the next free one)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on; 0.0.0.0 lets other computers on your network connect")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = parser.parse_args()

    python = ensure_environment()
    port = free_port(args.host, args.port)
    url = f"http://127.0.0.1:{port}" if args.host in ("0.0.0.0", "::") else f"http://{args.host}:{port}"

    print(f"Label Verification is starting at {url}")
    if args.host in ("0.0.0.0", "::") and (address := lan_address()):
        print(f"Other computers on your network can use http://{address}:{port}")
    print("Keep this window open while you use it. Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    server = subprocess.Popen(
        [str(python), "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(port), "--no-access-log"],
        cwd=ROOT,
    )
    try:
        return server.wait()
    except KeyboardInterrupt:
        # The server received Ctrl+C too; give it a moment to shut down cleanly.
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.terminate()
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

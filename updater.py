import os
import re
import sys
import time
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request

# ---------------- CONFIG: edit these 3 lines ----------------
GITHUB_USER = "Akshay-SIFY"
GITHUB_REPO = "Content_verification_Tool"
GITHUB_BRANCH = "main"
# ------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(APP_DIR, "version.txt")
REPO_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"
RAW_VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/version.txt"
ZIP_URL = f"{REPO_URL}/archive/refs/heads/{GITHUB_BRANCH}.zip"
SKIP_DIRS = {"env", "venv", ".git", ".github", "__pycache__"}


def get_local_version() -> str:
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except Exception:
        return "0.0.0"


def _ver_tuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


def check_for_update(timeout: int = 3):
    """Returns the newer version string if GitHub has one, else None. Silent on any error (offline etc.)."""
    try:
        req = urllib.request.Request(RAW_VERSION_URL, headers={"User-Agent": "content-verification-tool"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            remote = resp.read().decode("utf-8").strip()
        if remote and _ver_tuple(remote) > _ver_tuple(get_local_version()):
            return remote
    except Exception:
        pass
    return None


def apply_update():
    """Downloads the branch zip, overwrites the app files, installs requirements. version.txt is written last."""
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "content-verification-tool"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    tmp = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(tmp, "update.zip")
        with open(zip_path, "wb") as f:
            f.write(data)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)

        # GitHub zips contain a single top folder: <repo>-<branch>/
        roots = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
        src_root = os.path.join(tmp, roots[0])

        for folder, dirs, files in os.walk(src_root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            rel = os.path.relpath(folder, src_root)
            dest_dir = APP_DIR if rel == "." else os.path.join(APP_DIR, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for name in files:
                if rel == "." and name == "version.txt":
                    continue                      # copied last, after pip succeeds
                shutil.copy2(os.path.join(folder, name), os.path.join(dest_dir, name))

        req_file = os.path.join(APP_DIR, "requirements.txt")
        if os.path.exists(req_file):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], check=True)

        new_ver = os.path.join(src_root, "version.txt")
        if os.path.exists(new_ver):
            shutil.copy2(new_ver, VERSION_FILE)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart_app():
    """Starts Run_Dashboard.bat in a new console after a short delay, then stops the current server."""
    time.sleep(1.5)                               # let the UI flush the success message
    if os.name == "nt":
        subprocess.Popen(
            ["cmd", "/c", "timeout /t 3 /nobreak >nul && call Run_Dashboard.bat"],
            cwd=APP_DIR,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app_ui.py"], cwd=APP_DIR)
    os._exit(0)
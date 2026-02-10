"""
Milk Button Player: receives announce requests and plays audio on this machine.

Runs on the device that has the speaker (e.g. a Raspberry Pi). Exposes /announce
(GET/POST with ?file=...&file=...) to trigger playback, and a web UI at / for
managing audio files and config (login via PAM). Playback uses mpg123.

Run with working directory set to the player/ subfolder so that config.json
and the audio/ directory are found alongside this file.
"""

from __future__ import annotations

from flask import Flask, request, abort, jsonify, session, redirect, render_template
from flask.typing import ResponseValue
from functools import wraps
from typing import Any, Callable, List

import subprocess
import os
import time
import json
import re
import logging

# -----------------------------------------------------------------------------
# Logging and optional PAM
# -----------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import pam
except ImportError as e:
    pam = None
    logging.getLogger(__name__).warning("python-pam not available: %s", e)

# -----------------------------------------------------------------------------
# App and paths (relative to player/ subfolder when run as python player.py)
# -----------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET_KEY", os.urandom(24).hex())

BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR: str = os.path.join(BASE_DIR, "audio")
CONFIG_PATH: str = os.path.join(BASE_DIR, "config.json")

# Throttle: time of last playback start (unix timestamp)
LAST_PLAYED: float = 0.0

# Default playback config (overridden by config.json)
DEFAULT_REPEATS: int = 2
DEFAULT_DELAY: int = 10
DEFAULT_VOLUME: int = 32768

# Allowed ranges for config (used by UI and PATCH API)
REPEATS_MIN, REPEATS_MAX = 1, 10
DELAY_MIN, DELAY_MAX = 0, 60
VOLUME_MIN, VOLUME_MAX = 1, 500000


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    """Load config from config.json if it exists; otherwise return defaults."""
    config: dict[str, Any] = {
        "repeats": DEFAULT_REPEATS,
        "delay": DEFAULT_DELAY,
        "volume": DEFAULT_VOLUME,
    }
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data: Any = json.load(f)
            if "repeats" in data:
                config["repeats"] = int(data["repeats"])
            if "delay" in data:
                config["delay"] = int(data["delay"])
            if "volume" in data:
                config["volume"] = int(data["volume"])
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict[str, Any]) -> None:
    """Write config dict to config.json."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


# -----------------------------------------------------------------------------
# Auth (PAM + session)
# -----------------------------------------------------------------------------

def require_auth(f: Callable[..., ResponseValue]) -> Callable[..., ResponseValue]:
    """Decorator: require session login; return 401 for API, redirect to / for browser."""
    @wraps(f)
    def wrapped(*args: Any, **kwargs: Any) -> ResponseValue:
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                abort(401)
            return redirect("/")
        return f(*args, **kwargs)
    return wrapped


def authenticate(username: str, password: str) -> bool:
    """Authenticate user via PAM (system login). Returns True if successful."""
    if not pam:
        logger.warning("Login attempt for %r rejected: python-pam not loaded", username)
        return False
    try:
        p = pam.pam()
        ok: bool = p.authenticate(username, password, service="login")
        if ok:
            logger.info("PAM authentication succeeded for user %r", username)
        else:
            logger.warning("PAM authentication failed for user %r (wrong password or user?)", username)
        return ok
    except Exception as e:
        logger.exception("PAM authentication error for user %r: %s", username, e)
        return False


# -----------------------------------------------------------------------------
# Playback
# -----------------------------------------------------------------------------

def play_audio(
    file_paths: List[str],
    repeats: int = 2,
    delay: int = 10,
    volume: int = 1000,
) -> bool:
    """
    Play audio files using mpg123. Respects a delay (seconds) since last play.
    Returns True on success (or if skipped due to throttle).
    """
    global LAST_PLAYED

    now: float = time.time()
    if now - LAST_PLAYED < delay:
        return True

    params: List[str] = [
        "mpg123",
        "-f",
        str(volume),
        "-q",
    ]
    params.extend(file_paths * repeats)

    subprocess.Popen(params)
    LAST_PLAYED = now
    return True


def _safe_filename(name: str) -> bool:
    """Return True if name is safe (no path traversal, allowed characters only)."""
    if not name or ".." in name or "/" in name or "\\" in name:
        return False
    return bool(re.match(r"^[a-zA-Z0-9._ -]+$", name))


# -----------------------------------------------------------------------------
# Routes: login / logout
# -----------------------------------------------------------------------------

@app.route("/login", methods=["POST"])
def login() -> ResponseValue:
    """POST JSON { username, password }; set session and return { ok: true } or 401."""
    data: Any = request.get_json(force=True, silent=True) or {}
    username: str = (data.get("username") or "").strip()
    password: str = data.get("password") or ""

    logger.info("Login attempt for username %r (password present: %s)", username, bool(password))
    if not username:
        logger.warning("Login rejected: empty username")
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401
    if not pam:
        logger.warning("Login rejected: PAM module not available")
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401
    if not authenticate(username, password):
        logger.warning("Login rejected for %r: authentication failed", username)
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401

    session["logged_in"] = True
    session["username"] = username
    logger.info("Login successful for %r", username)
    return jsonify({"ok": True})


@app.route("/logout", methods=["POST"])
def logout() -> ResponseValue:
    """Clear session and return { ok: true }."""
    session.clear()
    return jsonify({"ok": True})


# -----------------------------------------------------------------------------
# Routes: API (require auth)
# -----------------------------------------------------------------------------

@app.route("/api/config", methods=["GET"])
@require_auth
def api_get_config() -> ResponseValue:
    """Return current config (repeats, delay, volume)."""
    return jsonify(load_config())


@app.route("/api/config", methods=["PATCH"])
@require_auth
def api_patch_config() -> ResponseValue:
    """Accept partial JSON { repeats?, delay?, volume? }; clamp to allowed ranges and save."""
    config: dict[str, Any] = load_config()
    data: Any = request.get_json(force=True, silent=True) or {}

    if "repeats" in data:
        v: int = int(data["repeats"])
        config["repeats"] = max(REPEATS_MIN, min(REPEATS_MAX, v))
    if "delay" in data:
        v = int(data["delay"])
        config["delay"] = max(DELAY_MIN, min(DELAY_MAX, v))
    if "volume" in data:
        v = int(data["volume"])
        config["volume"] = max(VOLUME_MIN, min(VOLUME_MAX, v))

    save_config(config)
    return jsonify(config)


@app.route("/api/files", methods=["GET"])
@require_auth
def api_list_files() -> ResponseValue:
    """Return JSON list of filenames in the audio directory."""
    if not os.path.isdir(AUDIO_DIR):
        return jsonify([])
    files: List[str] = [
        name for name in os.listdir(AUDIO_DIR)
        if os.path.isfile(os.path.join(AUDIO_DIR, name))
    ]
    return jsonify(sorted(files))


@app.route("/api/files", methods=["POST"])
@require_auth
def api_upload_file() -> ResponseValue:
    """Accept multipart file upload; save to audio dir. Return { ok, name? } or 4xx/5xx."""
    if "file" not in request.files:
        logger.warning("Upload rejected: no 'file' part in request (keys: %s)", list(request.files.keys()))
        return jsonify({"ok": False, "error": "No file part"}), 400

    f: Any = request.files["file"]
    if not f.filename:
        logger.warning("Upload rejected: empty filename")
        return jsonify({"ok": False, "error": "No file selected"}), 400

    name: str = os.path.basename(f.filename)
    if not _safe_filename(name):
        logger.warning("Upload rejected: invalid filename %r", name)
        return jsonify({"ok": False, "error": "Invalid filename (use only letters, numbers, spaces, . _ -)"}), 400

    os.makedirs(AUDIO_DIR, exist_ok=True)
    path: str = os.path.join(AUDIO_DIR, name)
    try:
        f.save(path)
    except OSError as e:
        logger.exception("Upload failed to save %r: %s", name, e)
        return jsonify({"ok": False, "error": "Failed to save file"}), 500

    logger.info("Uploaded file %r", name)
    return jsonify({"ok": True, "name": name})


@app.route("/api/files/<path:name>", methods=["DELETE"])
@require_auth
def api_delete_file(name: str) -> ResponseValue:
    """Delete the named file from the audio directory. Returns { ok: true } or 4xx."""
    if not _safe_filename(name):
        abort(400, "Invalid filename")
    path: str = os.path.join(AUDIO_DIR, name)
    if not os.path.isfile(path):
        abort(404)
    os.remove(path)
    return jsonify({"ok": True})


# -----------------------------------------------------------------------------
# Routes: UI and public API
# -----------------------------------------------------------------------------

@app.route("/")
def index() -> ResponseValue:
    """Serve the config UI (login + dashboard for files and playback settings)."""
    return render_template(
        "player-config.html",
        repeats_min=REPEATS_MIN,
        repeats_max=REPEATS_MAX,
        delay_min=DELAY_MIN,
        delay_max=DELAY_MAX,
        volume_min=VOLUME_MIN,
        volume_max=VOLUME_MAX,
    )


@app.route("/files", methods=["GET"])
def list_files() -> ResponseValue:
    """Public: return JSON list of audio filenames (no auth). Used by senders to discover files."""
    if not os.path.isdir(AUDIO_DIR):
        return jsonify([])
    files = [
        name for name in os.listdir(AUDIO_DIR)
        if os.path.isfile(os.path.join(AUDIO_DIR, name))
    ]
    return jsonify(sorted(files))


@app.route("/announce", methods=["GET", "POST"])
def announce() -> ResponseValue:
    """
    Public: trigger playback. Query params: file=...&file=... (one or more filenames).
    Uses config for repeats, delay, volume. Returns "OK\\n" or 4xx/5xx.
    """
    config: dict[str, Any] = load_config()
    files: List[str] = request.args.getlist("file")

    if not files:
        abort(400, "No file provided to play")

    file_paths: List[str] = []
    for file in files:
        file_path = os.path.join(AUDIO_DIR, file)
        if not os.path.exists(file_path):
            abort(400, "Unknown file: %s" % file)
        file_paths.append(file_path)

    ok: bool = play_audio(
        file_paths,
        repeats=config["repeats"],
        delay=config["delay"],
        volume=config["volume"],
    )
    if not ok:
        abort(500, "Failed to play audio")

    return "OK\n"


@app.route("/health")
def health() -> ResponseValue:
    """Simple health check for monitoring."""
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

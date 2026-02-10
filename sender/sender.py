"""
Milk Button Sender: web app and /send endpoint that forwards announce requests to the player.

Runs on a device that has a button/keyboard (e.g. a Raspberry Pi). At startup, discovers
the player via Zeroconf (_milkpi._tcp.local.) if not in config. Exposes /send
(GET or POST) to trigger the player's /announce with configurable audio_files_to_send.
Also serves a config UI at / (PAM login) to set player URL and which files to send.

Run with working directory set to the sender/ subfolder so that config.json
is found alongside this file.
"""

from __future__ import annotations

from flask import Flask, request, abort, jsonify, session, redirect, render_template
from flask.typing import ResponseValue
from functools import wraps
from typing import Any, Callable

import os
import json
import logging
import time
import urllib.request
import urllib.error
import urllib.parse

# -----------------------------------------------------------------------------
# Logging and optional dependencies (PAM, Zeroconf)
# -----------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import pam
except ImportError as e:
    pam = None
    logging.getLogger(__name__).warning("python-pam not available: %s", e)

try:
    from zeroconf import Zeroconf, ServiceBrowser
except ImportError as e:
    Zeroconf = None  # type: ignore[misc, assignment]
    ServiceBrowser = None  # type: ignore[misc, assignment]
    logging.getLogger(__name__).warning("zeroconf not available: %s", e)

# -----------------------------------------------------------------------------
# App and paths (relative to sender/ subfolder when run as python sender.py)
# -----------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET_KEY", os.urandom(24).hex())

BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH: str = os.path.join(BASE_DIR, "config.json")


# -----------------------------------------------------------------------------
# Zeroconf: discover player on the network
# -----------------------------------------------------------------------------

class Listener:
    """Zeroconf service listener that stores the first discovered player URL."""

    def __init__(self) -> None:
        self.server: str | None = None  # player base URL (e.g. http://192.168.1.2:8000)

    def add_service(self, zc: Any, type: Any, name: str) -> None:
        """Called when a _milkpi._tcp.local. service is found; set self.server to the player URL."""
        info = zc.get_service_info(type, name)
        if info:
            ip: str = info.parsed_addresses()[0]
            port: int = info.port
            self.server = "http://%s:%d" % (ip, port)
            logger.info("Found server: %s", self.server)


def find_server(timeout: int = 5) -> str | None:
    """
    Discover the Milk Button player via Zeroconf (mDNS). Returns player base URL (e.g. http://192.168.1.2:8000)
    or None if not found or Zeroconf unavailable.
    """
    if not Zeroconf or not ServiceBrowser:
        return None
    zc = Zeroconf()
    listener = Listener()
    ServiceBrowser(zc, "_milkpi._tcp.local.", listener)
    time.sleep(timeout)
    zc.close()
    return listener.server


def fetch_remote_files(server_url: str) -> list[str] | None:
    """GET server_url/files and return list of filenames, or None on failure. server_url is the player base URL."""
    if not server_url:
        return None
    url: str = server_url.rstrip("/") + "/files"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return None
            data: Any = json.loads(resp.read().decode())
            if isinstance(data, list):
                return data
            return None
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to fetch files from %s: %s", url, e)
        return None


# -----------------------------------------------------------------------------
# Config (sender's config.json: server = player URL, available_audio_files, audio_files_to_send)
# -----------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    """Load sender config from config.json. Returns empty dict if missing or invalid."""
    config: dict[str, Any] = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict[str, Any]) -> None:
    """Write config dict to config.json."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def run_startup() -> None:
    """
    Run once at startup: if player URL not in config, discover via Zeroconf; then fetch
    file list from player and set default audio_files_to_send if empty.
    """
    config: dict[str, Any] = load_config()
    zeroconf_done: bool = False

    if not config.get("server"):
        server: str | None = find_server()
        zeroconf_done = True
        if server:
            config["server"] = server
            save_config(config)

    if config.get("server"):
        files: list[str] | None = fetch_remote_files(config["server"])
        if files is not None:
            config["available_audio_files"] = files
            save_config(config)
            if not config.get("audio_files_to_send") and files:
                config["audio_files_to_send"] = [files[0]]
                save_config(config)
        else:
            if not zeroconf_done:
                server = find_server()
                zeroconf_done = True
                if server:
                    config["server"] = server
                    save_config(config)
                    files = fetch_remote_files(config["server"])
                    if files is not None:
                        config["available_audio_files"] = files
                        save_config(config)
                        if not config.get("audio_files_to_send") and files:
                            config["audio_files_to_send"] = [files[0]]
                            save_config(config)

    logger.info("Startup complete. Player: %s", config.get("server"))


# -----------------------------------------------------------------------------
# Auth (PAM + session)
# -----------------------------------------------------------------------------

def require_auth(f: Callable[..., ResponseValue]) -> Callable[..., ResponseValue]:
    """Decorator: require session login; 401 for API, redirect to / for browser."""
    @wraps(f)
    def wrapped(*args: Any, **kwargs: Any) -> ResponseValue:
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                abort(401)
            return redirect("/")
        return f(*args, **kwargs)
    return wrapped


def authenticate(username: str, password: str) -> bool:
    """Authenticate via PAM (system login). Returns True if successful."""
    if not pam:
        return False
    try:
        p = pam.pam()
        return p.authenticate(username, password, service="login")
    except Exception as e:
        logger.exception("PAM auth error for %r: %s", username, e)
        return False


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.route("/send", methods=["GET", "POST"])
def send() -> ResponseValue:
    """
    Trigger announce on the configured player. Reads config for server (player URL) and audio_files_to_send,
    GETs player/announce?file=...&file=..., returns { ok: true } or 4xx/502 with { ok: false, error }.
    """
    config = load_config()
    server: str = (config.get("server") or "").strip().rstrip("/")
    files_raw: Any = config.get("audio_files_to_send")
    files: list[str] = [f for f in (files_raw if isinstance(files_raw, list) else []) if f]

    if not server:
        logger.warning("Send failed: no player URL in config")
        return jsonify({"ok": False, "error": "No player configured"}), 400
    if not files:
        logger.warning("Send failed: no audio_files_to_send in config")
        return jsonify({"ok": False, "error": "No audio files to send"}), 400

    url: str = server + "/announce?" + urllib.parse.urlencode([("file", f) for f in files])
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                body: str = resp.read().decode(errors="replace")
                logger.warning("Send failed: server returned %s: %s", resp.status, body)
                return jsonify({"ok": False, "error": "Server returned %s" % resp.status}), 502
            return jsonify({"ok": True})
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if e.fp else ""
        logger.warning("Send failed: HTTP %s: %s", e.code, body)
        return jsonify({"ok": False, "error": "Server returned %s" % e.code}), 502
    except (urllib.error.URLError, OSError) as e:
        logger.warning("Send failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/login", methods=["POST"])
def login() -> ResponseValue:
    """POST JSON { username, password }; set session and return { ok: true } or 401."""
    data = request.get_json(force=True, silent=True) or {}
    username: str = (data.get("username") or "").strip()
    password: str = data.get("password") or ""
    if not username or not pam:
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401
    if not authenticate(username, password):
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401
    session["logged_in"] = True
    session["username"] = username
    return jsonify({"ok": True})


@app.route("/logout", methods=["POST"])
def logout() -> ResponseValue:
    """Clear session and return { ok: true }."""
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/config", methods=["GET"])
@require_auth
def api_get_config() -> ResponseValue:
    """Return full sender config (server, available_audio_files, audio_files_to_send)."""
    return jsonify(load_config())


@app.route("/api/config", methods=["PATCH"])
@require_auth
def api_patch_config() -> ResponseValue:
    """
    Accept partial JSON: server (string, refetch files), or audio_files_to_send (list).
    Save and return updated config.
    """
    config = load_config()
    data = request.get_json(force=True, silent=True) or {}

    if "server" in data:
        new_server = (data.get("server") or "").strip().rstrip("/")
        if not new_server:
            config["server"] = None
            config["available_audio_files"] = []
            config["audio_files_to_send"] = []
            save_config(config)
            return jsonify(config)
        # Validate: fetch /files from candidate URL (server-side to avoid CORS)
        files = fetch_remote_files(new_server)
        if files is None:
            return jsonify({"ok": False, "error": "Could not reach player or invalid response from /files"}), 400
        if not isinstance(files, list) or len(files) == 0:
            return jsonify({"ok": False, "error": "No audio files found on player"}), 400
        config["server"] = new_server
        config["available_audio_files"] = files
        current = config.get("audio_files_to_send") or []
        config["audio_files_to_send"] = [f for f in current if f in files]
        if not config["audio_files_to_send"] and files:
            config["audio_files_to_send"] = [files[0]]
        save_config(config)
        return jsonify(config)

    if "audio_files_to_send" in data:
        available: set[str] = set(config.get("available_audio_files") or [])
        requested: Any = data["audio_files_to_send"]
        if not isinstance(requested, list):
            requested = []
        config["audio_files_to_send"] = [f for f in requested if f in available]
        save_config(config)
        return jsonify(config)

    return jsonify(config)


@app.route("/")
def index() -> ResponseValue:
    """Serve the sender config UI (login + dashboard for player URL and file selection)."""
    return render_template("sender-config.html")


@app.route("/health")
def health() -> ResponseValue:
    """Simple health check for monitoring."""
    return "OK"


if __name__ == "__main__":
    run_startup()
    app.run(host="0.0.0.0", port=8000)

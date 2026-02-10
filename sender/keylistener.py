#!/usr/bin/env python3
"""
Milk Button Key Listener: watches for key/button events and triggers the sender.

Runs headless (no GUI) using evdev to read from /dev/input/event*. On any key-down
event, POSTs to the sender's /send endpoint so the sender forwards the announce
request to the player. Run alongside sender.py on the same device (from the sender/ directory).

Permissions: on Linux, reading input devices usually requires the user to be in the
'input' group (e.g. sudo usermod -aG input $USER) or to run as root.

  On Raspberry Pi: sudo apt install python3-evdev
  Or: pip install evdev (may need libevdev dev headers)
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Where to POST to trigger a send (override with SENDER_URL env)
SENDER_URL: str = os.environ.get("SENDER_URL", "http://127.0.0.1:8000/send")
# Ignore events for this many seconds after each trigger (debounce)
DEBOUNCE: float = 1.0


def find_input_device() -> Any:
    """
    Find the first evdev input device that supports key events (keyboard or button).
    Returns the device object or None. Skips devices that cannot be opened (e.g. permission).
    """
    from evdev import InputDevice, ecodes, list_devices

    for path in list_devices():
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError):
            continue
        if ecodes.EV_KEY in dev.capabilities():
            logger.info("Using input device: %s (%s)", path, dev.name)
            return dev
    return None


def send_trigger() -> None:
    """POST to SENDER_URL to trigger the sender's /send endpoint. Log success or failure."""
    try:
        req = urllib.request.Request(
            SENDER_URL,
            data=b"",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                logger.info("Trigger sent")
            else:
                logger.warning("Sender returned %s", resp.status)
    except urllib.error.HTTPError as e:
        logger.warning("Trigger failed: HTTP %s", e.code)
    except (urllib.error.URLError, OSError) as e:
        logger.warning("Trigger failed: %s", e)


def main() -> None:
    """Discover an input device, then run the read loop and call send_trigger on key down (debounced)."""
    try:
        from evdev import InputDevice, ecodes, list_devices
    except ImportError as e:
        logger.error(
            "evdev not available. On Raspberry Pi try: sudo apt install python3-evdev (or pip install evdev)"
        )
        raise SystemExit(1) from e

    dev: Any = find_input_device()
    if not dev:
        logger.error(
            "No input device with key events found. Check permissions (e.g. add user to 'input' group)."
        )
        raise SystemExit(1)

    last: float = 0.0
    logger.info("Listening for button/key presses (debounce %.1fs)...", DEBOUNCE)

    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY:
            continue
        # EV_KEY value: 1 = key down, 0 = release, 2 = repeat
        if event.value != 1:
            continue
        now: float = time.monotonic()
        if now - last < DEBOUNCE:
            continue
        last = now
        send_trigger()


if __name__ == "__main__":
    main()

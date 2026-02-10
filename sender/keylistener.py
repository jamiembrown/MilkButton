#!/usr/bin/env python3
"""
Milk Button Key Listener: watches for key/button events and triggers the sender.

Runs headless (no GUI) using evdev to read from /dev/input/event*. Listens to
all input devices that support key events (EV_KEY), so multiple keyboards/buttons
are supported. On any key-down event, POSTs to the sender's /send endpoint.
Run alongside sender.py on the same device (from the sender/ directory).

Permissions: on Linux, reading input devices usually requires the user to be in the
'input' group (e.g. sudo usermod -aG input $USER) or to run as root.

  On Raspberry Pi: sudo apt install python3-evdev
  Or: pip install evdev (may need libevdev dev headers)
"""

from __future__ import annotations

import fcntl
import logging
import os
import select
import time
import urllib.error
import urllib.request
from typing import Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Where to POST to trigger a send (override with SENDER_URL env)
SENDER_URL: str = os.environ.get("SENDER_URL", "http://127.0.0.1:8000/send")
# Ignore events for this many seconds after each trigger (debounce)
DEBOUNCE: float = 1.0


def find_all_input_devices() -> List[Any]:
    """
    Find all evdev input devices that support key events (keyboard or button).
    Returns a list of device objects. Skips devices that cannot be opened (e.g. permission).
    """
    from evdev import InputDevice, ecodes, list_devices

    devices: List[Any] = []
    for path in sorted(list_devices()):
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError):
            continue
        if ecodes.EV_KEY in dev.capabilities():
            logger.info("Listening on: %s (%s)", path, dev.name)
            devices.append(dev)
    return devices


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
    """Discover all EV_KEY input devices and listen on them; on key down (debounced) call send_trigger."""
    try:
        from evdev import InputDevice, ecodes, list_devices
    except ImportError as e:
        logger.error(
            "evdev not available. On Raspberry Pi try: sudo apt install python3-evdev (or pip install evdev)"
        )
        raise SystemExit(1) from e

    devices: List[Any] = find_all_input_devices()
    if not devices:
        logger.error(
            "No input device with key events found. Check permissions (e.g. add user to 'input' group)."
        )
        raise SystemExit(1)

    # Non-blocking so select() and read_one() work together
    for dev in devices:
        try:
            fd = dev.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        except Exception as e:
            logger.warning("Could not set non-blocking on %s: %s", dev.name, e)

    fd_to_dev: dict[int, Any] = {dev.fileno(): dev for dev in devices}
    last: float = 0.0
    logger.info("Listening for button/key presses on %d device(s) (debounce %.1fs)...", len(devices), DEBOUNCE)

    while True:
        r, _, _ = select.select(list(fd_to_dev.keys()), [], [])
        for fd in r:
            dev = fd_to_dev[fd]
            while True:
                try:
                    event = dev.read_one()
                except (BlockingIOError, OSError):
                    break
                if event is None:
                    break
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

# Milk Button

A small system for playing audio on a Raspberry Pi (or similar) when a button is pressed. Typical use: a “milk run” button that announces a message over a speaker.

## Architecture

- **Player** (`player/`) – Runs on the device with the **speaker**. Receives HTTP requests to play audio files (via mpg123), and serves a web UI to manage files and playback settings. Optionally advertises itself on the network with Avahi (Zeroconf).
- **Sender** (`sender/`) – Runs on the device with the **button** (can be the same Pi or another). Exposes a `/send` endpoint that, when called, asks the **player** to play a configured list of audio files. Also serves a config UI to set the player URL and which files to send.
- **Key listener** (`sender/keylistener.py`) – Runs on the same machine as the sender. Listens for key/button events (via evdev, no GUI needed). On each key press, POSTs to the sender’s `/send` so the player plays the chosen audio.

```
[Button] → keylistener.py → sender (/send) → player (/announce) → mpg123 → speaker
```

## Project layout

- **player/** – Player app. Run from this directory: `python player.py`. Uses `config.json` and `audio/` in the same directory.
- **sender/** – Sender app and key listener. Run from this directory: `python sender.py` and `python keylistener.py`. Uses `config.json` in the same directory.

## Requirements

- **Player machine**: Python 3, mpg123, optional Avahi for discovery.
- **Sender machine**: Python 3, optional Zeroconf for discovery, optional PAM for web UI login.
- **Key listener**: Python 3, evdev (for `/dev/input` access). On a headless Pi, the user usually needs to be in the `input` group.

## Quick start

### On the device with the speaker (player)

1. Go to the player directory and install system dependencies (Debian/Ubuntu/Raspberry Pi OS):

   ```bash
   cd player
   sudo apt install -y $(cat apt-requirements.txt)
   ```

2. Create a virtualenv and install Python deps:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. (Optional) Copy the Avahi service file so the player is discoverable as `_milkpi._tcp.local.`:

   ```bash
   sudo cp avahi.milkpi.service /etc/avahi/services/
   sudo systemctl reload avahi-daemon
   ```

4. Create an `audio` directory and add some MP3s:

   ```bash
   mkdir -p audio
   # copy your .mp3 files into audio/
   ```

5. Run the player (working directory must be `player/`):

   ```bash
   python player.py
   ```

   By default it listens on `0.0.0.0:8000`. Open `http://<player-ip>:8000/` to manage files and config (login with a system user if PAM is available). To trigger playback from another machine:

   ```text
   GET or POST http://<player-ip>:8000/announce?file=chime.mp3&file=alarm.mp3
   ```

### On the device with the button (sender + key listener)

1. Go to the sender directory and install system deps (including `python3-evdev` for the key listener):

   ```bash
   cd sender
   sudo apt install -y $(cat apt-requirements.txt)
   ```

2. If the sender runs on a different host than the player, ensure the player is reachable. If the player uses Avahi, the sender will try to discover it at startup via Zeroconf.

3. Create a venv and install deps:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Run the sender (working directory must be `sender/`). If this host also runs the player, use a different port for the sender (e.g. 8001) or run the player on another machine:

   ```bash
   export SESSION_SECRET_KEY="your-secret-key"  # recommended for login sessions
   python sender.py
   ```

   By default the sender listens on port 8000. To avoid clashing with the player on the same host, set the port when running (e.g. via Flask or your process manager).

5. In another terminal (or as a service), from the **sender** directory run the key listener so that button/key presses trigger a send:

   ```bash
   cd sender
   source .venv/bin/activate
   python keylistener.py
   ```

   If the sender is on the same host but a different port (e.g. 8001):

   ```bash
   SENDER_URL=http://127.0.0.1:8001/send python keylistener.py
   ```

   If you get permission errors reading input devices:

   ```bash
   sudo usermod -aG input $USER
   # then log out and back in
   ```

## Configuration

- **Player** (`player/config.json`, optional)  
  - `repeats` (1–10), `delay` (seconds between plays), `volume` (mpg123 -f value). The web UI at `/` can edit these (after PAM login).
  - `SESSION_SECRET_KEY` env var for Flask session signing (recommended in production).

- **Sender** (`sender/config.json`)  
  - `server`: base URL of the Milk Button **player** (e.g. `http://192.168.1.2:8000`). The UI at `/` can set this (after PAM login). At startup, if `server` is missing, the sender tries Zeroconf discovery.
  - `audio_files_to_send`: list of filenames to pass as `file=` to the player’s `/announce`. The UI lets you choose and order them.

- **Key listener**  
  - `SENDER_URL`: URL of the sender’s `/send` endpoint (default `http://127.0.0.1:8000/send`).  
  - Debounce is 1 second (hardcoded) to avoid duplicate triggers.

## API summary

| Endpoint                     | App    | Auth   | Description |
|-----------------------------|--------|--------|-------------|
| `GET/POST /announce?file=...` | Player | No     | Play one or more audio files (query params). |
| `GET /files`                | Player | No     | List available audio filenames. |
| `GET /`                    | Player | No (UI) | Config UI (login via PAM for management). |
| `GET/POST /send`           | Sender | No     | Trigger player’s `/announce` with configured `audio_files_to_send`. |
| `GET /`                    | Sender | No (UI) | Sender config UI (login via PAM). |
| `GET /health`               | Both   | No     | Health check. |

## License

Use and modify as you like. If you open-source your fork, a note and link back is appreciated.

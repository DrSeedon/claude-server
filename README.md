# Claude Terminal

Web-interface for remote access to multiple [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions via browser (mobile/tablet/desktop).

Each project = separate tmux session with Claude Code inside.

## Features

- **Project list** with live status indicators (busy/ready/viewed)
- **Full terminal** via xterm.js with WebSocket
- **Mobile toolbar** — Esc, Tab, Ctrl+C/D/Z/B/O, arrows, Enter, Yes/No, ClearLn, ClearAll
- **Scroll joystick** — drag-based speed control for terminal scrolling
- **File browser** with image preview (pinch-to-zoom) and markdown rendering
- **Auto-reconnect** on WebSocket drop
- **Auto-launch** Claude Code on new session creation
- **Password auth** with SHA-256 + salt, Bearer tokens (7 days)
- **Rate limiting** on login attempts

## Stack

- **Backend**: Python 3 + aiohttp (single file `server.py`)
- **Frontend**: Vanilla JS + xterm.js (single file `index.html`)
- **Terminal**: tmux + pty, WebSocket for real-time I/O
- **Auth**: password -> SHA-256 + salt, Bearer token, no database

## Architecture

```
Browser <-> WebSocket <-> aiohttp <-> pty <-> tmux attach <-> claude code
Browser <-> REST API  <-> aiohttp <-> tmux/subprocess
```

## Setup

```bash
# 1. Clone and create venv
git clone https://github.com/DrSeedon/claude-server.git && cd claude-server
python3 -m venv venv
source venv/bin/activate
pip install aiohttp

# 2. Set password
python setup.py

# 3. Run
python server.py
# -> http://localhost:8080
```

## Systemd (optional)

Copy and edit `claude-server.service`, then:

```bash
sudo cp claude-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-server
```

## Files

```
server.py               # Backend: auth, REST API, WebSocket, file manager
index.html              # Frontend: login, projects, terminal, files, preview
manager.sh              # tmux helper: list/ensure/stop sessions
start-all.sh            # Entry point for systemd
setup.py                # One-time password setup
config.json             # Password hash + salt (not committed, in .gitignore)
claude-server.service   # systemd unit template
static/                 # xterm.js + addons (fit, web-links), marked.js
```

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/login` | Auth with password, returns token |
| GET | `/api/projects` | List projects with states |
| POST | `/api/ensure` | Create/attach tmux session |
| POST | `/api/stop` | Stop tmux session |
| GET | `/api/files` | List project files |
| GET | `/api/files/view` | Serve file (image/text/etc) |
| WS | `/ws/{session}` | WebSocket terminal |

## Security

- No TLS built-in — use a reverse proxy or SSH tunnel for HTTPS
- Password hashed with SHA-256 + random salt
- Rate limiting: 5 login attempts per minute per IP
- Tokens stored in memory — server restart = re-login
- WebSocket connections require auth token

## License

MIT

#!/usr/bin/env python3
import asyncio
import fcntl
import hashlib
import hmac
import json
import mimetypes
import os
import pty
import secrets
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path

from aiohttp import web

SERVER_DIR = Path(__file__).parent
CONFIG_FILE = SERVER_DIR / "config.json"
CUSTOM_PROJECTS_FILE = SERVER_DIR / "custom_projects.json"
HTML_FILE = SERVER_DIR / "index.html"
MANAGER_SCRIPT = SERVER_DIR / "manager.sh"
PORT = 8080
TOKEN_LIFETIME = 86400 * 7

tokens = {}
login_attempts = {}
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW = 60


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def verify_password(password):
    config = load_config()
    check = hashlib.sha256((password + config["salt"]).encode()).hexdigest()
    return hmac.compare_digest(config["password_hash"], check)


def generate_token():
    token = secrets.token_urlsafe(32)
    tokens[token] = time.time()
    return token


def verify_token(token):
    if token not in tokens:
        return False
    if time.time() - tokens[token] > TOKEN_LIFETIME:
        del tokens[token]
        return False
    return True


def _load_custom_projects():
    if CUSTOM_PROJECTS_FILE.exists():
        with open(CUSTOM_PROJECTS_FILE) as f:
            return json.load(f)
    return []


def _save_custom_projects(projects):
    with open(CUSTOM_PROJECTS_FILE, "w") as f:
        json.dump(projects, f, indent=2)


def _encode_path(path):
    result = []
    for ch in path:
        if ch == '/' or ch == ' ' or ord(ch) > 127:
            result.append('-')
        else:
            result.append(ch)
    return ''.join(result)


def _build_path_map():
    scan_roots = [
        "/mnt/data/Projects/Python",
        "/mnt/data/Projects/Unity",
        "/mnt/data/Projects",
        "/mnt/data/Рабочий стол/Cursor",
        str(Path.home()),
    ]
    mapping = {}
    for root in scan_roots:
        if not os.path.isdir(root):
            continue
        encoded_root = _encode_path(root)
        mapping[encoded_root] = root
        try:
            for entry in os.scandir(root):
                if entry.is_dir() and not entry.name.startswith('.'):
                    full = entry.path
                    encoded = _encode_path(full)
                    mapping[encoded] = full
        except OSError:
            pass
    return mapping


def _get_active_sessions():
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return set()
    return {s.strip() for s in result.stdout.strip().split("\n") if s.strip()}


pane_hashes = {}
viewed_sessions = set()


def _get_session_states():
    sessions = _get_active_sessions()
    if not sessions:
        return {}
    states = {}
    for sess in sessions:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", sess, "-p"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            states[sess] = "idle"
            continue
        import hashlib as _hl
        new_hash = _hl.md5(result.stdout.encode()).hexdigest()
        old_hash = pane_hashes.get(sess)
        pane_hashes[sess] = new_hash
        if old_hash is None:
            states[sess] = "ready"
        elif new_hash != old_hash:
            states[sess] = "busy"
        else:
            states[sess] = "ready"
    for gone in set(pane_hashes) - sessions:
        del pane_hashes[gone]
    return states


def _get_recent_project_time(project_dir):
    best = 0
    try:
        for f in os.scandir(project_dir):
            if f.name.endswith(".jsonl"):
                t = f.stat().st_mtime
                if t > best:
                    best = t
    except OSError:
        pass
    return best


def _make_project_item(real_path, active, session_states, custom=False):
    name = Path(real_path).name
    safe = name.replace(" ", "-").replace(".", "_")
    session = f"claude-{safe}"
    projects_dir = Path.home() / ".claude" / "projects"
    encoded = _encode_path(real_path)
    d = projects_dir / encoded
    recent = _get_recent_project_time(d) if d.is_dir() else 0
    if session not in active:
        state = "none"
    elif session in viewed_sessions and session_states.get(session) == "ready":
        state = "viewed"
    else:
        state = session_states.get(session, "idle")
    return {
        "name": name,
        "session": session,
        "path": real_path,
        "active": session in active,
        "state": state,
        "recent": recent,
        "custom": custom,
    }


def get_projects():
    projects_dir = Path.home() / ".claude" / "projects"
    path_map = _build_path_map()
    home = str(Path.home())
    active = _get_active_sessions()
    session_states = _get_session_states()
    seen_paths = set()
    items = []

    if projects_dir.exists():
        for d in projects_dir.iterdir():
            if not d.is_dir():
                continue
            real_path = path_map.get(d.name)
            if not real_path or real_path == home:
                continue
            if not os.path.isdir(real_path):
                continue
            seen_paths.add(real_path)
            items.append(_make_project_item(real_path, active, session_states))

    for cp in _load_custom_projects():
        p = cp if isinstance(cp, str) else cp.get("path", "")
        if p and os.path.isdir(p) and p not in seen_paths:
            seen_paths.add(p)
            items.append(_make_project_item(p, active, session_states, custom=True))

    items.sort(key=lambda x: (-x["active"], x["name"].lower()))

    result = {}
    for item in items:
        result[item["name"]] = {
            "session": item["session"],
            "path": item["path"],
            "active": item["active"],
            "state": item["state"],
            "custom": item["custom"],
        }
    return result


def ensure_session(session, path, auto_claude=False):
    is_new = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True
    ).returncode != 0
    result = subprocess.run(
        ["bash", str(MANAGER_SCRIPT), "ensure", session, path],
        capture_output=True, text=True
    )
    if is_new and auto_claude:
        subprocess.run(
            ["tmux", "send-keys", "-t", session,
             "claude --dangerously-skip-permissions --resume", "Enter"],
            capture_output=True
        )
    return result.stdout.strip()


def spawn_pty(session):
    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env.pop("CLAUDECODE", None)

    proc = subprocess.Popen(
        ["tmux", "attach", "-t", session],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        preexec_fn=os.setsid,
        env=env, close_fds=True
    )
    os.close(slave_fd)

    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    return master_fd, proc


async def handle_index(request):
    resp = web.FileResponse(HTML_FILE)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def _check_rate_limit(ip):
    now = time.time()
    attempts = login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < LOGIN_RATE_WINDOW]
    login_attempts[ip] = attempts
    if len(attempts) >= LOGIN_RATE_LIMIT:
        return False
    attempts.append(now)
    return True


async def handle_login(request):
    ip = request.headers.get("CF-Connecting-IP") or request.remote
    if not _check_rate_limit(ip):
        return web.json_response({"error": "too many attempts, wait 1 min"}, status=429)
    body = await request.json()
    if verify_password(body.get("password", "")):
        return web.json_response({"token": generate_token()})
    return web.json_response({"error": "wrong password"}, status=401)


async def handle_projects(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth[7:]):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(get_projects())


async def handle_ensure(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth[7:]):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    session = body.get("session", "")
    path = body.get("path", "")
    if not session or not path:
        return web.json_response({"error": "missing session or path"}, status=400)
    result = ensure_session(session, path)
    return web.json_response({"status": result})


async def handle_stop_session(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth[7:]):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    session = body.get("session", "")
    if not session:
        return web.json_response({"error": "missing session"}, status=400)
    subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
    if session in pane_hashes:
        del pane_hashes[session]
    viewed_sessions.discard(session)
    return web.json_response({"status": "stopped"})


async def handle_add_custom_project(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth[7:]):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    path = body.get("path", "").rstrip("/")
    if not path or not os.path.isdir(path):
        return web.json_response({"error": "invalid path"}, status=400)
    projects = _load_custom_projects()
    paths = [p if isinstance(p, str) else p.get("path", "") for p in projects]
    if path not in paths:
        projects.append(path)
        _save_custom_projects(projects)
    return web.json_response({"status": "added"})


async def handle_remove_custom_project(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth[7:]):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    path = body.get("path", "").rstrip("/")
    projects = _load_custom_projects()
    projects = [p for p in projects if (p if isinstance(p, str) else p.get("path", "")) != path]
    _save_custom_projects(projects)
    return web.json_response({"status": "removed"})


async def handle_list_dirs(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth[7:]):
        return web.json_response({"error": "unauthorized"}, status=401)
    parent = request.query.get("path", "/")
    if not os.path.isdir(parent):
        return web.json_response({"error": "not a directory"}, status=400)
    dirs = []
    try:
        for entry in sorted(os.scandir(parent), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith('.'):
                dirs.append({"name": entry.name, "path": entry.path})
    except OSError:
        pass
    return web.json_response(dirs)



VIEWABLE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico",
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".xml", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".sh", ".bat", ".css", ".html", ".csv",
    ".log", ".conf", ".env", ".rs", ".go", ".c", ".cpp", ".h", ".cs",
    ".pdf", ".docx", ".xlsx",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
MAX_FILE_SIZE = 50 * 1024 * 1024


def _list_files(root, rel_path=""):
    target = Path(root) / rel_path if rel_path else Path(root)
    if not target.is_dir():
        return []
    items = []
    try:
        for entry in os.scandir(str(target)):
            if entry.name.startswith("."):
                continue
            rel = os.path.join(rel_path, entry.name) if rel_path else entry.name
            try:
                stat = entry.stat()
            except OSError:
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            items.append({
                "name": entry.name,
                "path": rel,
                "is_dir": entry.is_dir(),
                "size": stat.st_size if not entry.is_dir() else 0,
                "mtime": stat.st_mtime,
                "ext": ext,
                "is_image": ext in IMAGE_EXTENSIONS,
                "viewable": ext in VIEWABLE_EXTENSIONS or entry.is_dir(),
            })
    except OSError:
        pass
    items.sort(key=lambda x: (-x["is_dir"], -x["mtime"]))
    return items


async def handle_files(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth[7:]):
        return web.json_response({"error": "unauthorized"}, status=401)
    root = request.query.get("root", "")
    rel = request.query.get("path", "")
    if not root or not os.path.isdir(root):
        return web.json_response({"error": "invalid root"}, status=400)
    resolved = os.path.realpath(os.path.join(root, rel))
    if not resolved.startswith(os.path.realpath(root)):
        return web.json_response({"error": "path traversal"}, status=403)
    return web.json_response(_list_files(root, rel))


async def handle_file_view(request):
    auth = request.query.get("token", "")
    if not verify_token(auth):
        return web.Response(status=401, text="unauthorized")
    root = request.query.get("root", "")
    rel = request.query.get("path", "")
    if not root or not rel:
        return web.Response(status=400, text="missing params")
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(os.path.realpath(root)):
        return web.Response(status=403, text="path traversal")
    if not os.path.isfile(full):
        return web.Response(status=404, text="not found")
    size = os.path.getsize(full)
    if size > MAX_FILE_SIZE:
        return web.Response(status=413, text="file too large")
    mime, _ = mimetypes.guess_type(full)
    if not mime:
        mime = "application/octet-stream"
    return web.FileResponse(full, headers={"Content-Type": mime})


async def websocket_handler(request):
    token = request.query.get("token", "")
    if not verify_token(token):
        return web.Response(status=401, text="unauthorized")

    session = request.match_info["session"]
    path = request.query.get("path", "")

    if path:
        ensure_session(session, path, auto_claude=True)

    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True
    )
    if check.returncode != 0:
        return web.Response(status=404, text=f"Session {session} not found")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    master_fd, proc = spawn_pty(session)
    loop = asyncio.get_event_loop()

    async def read_pty():
        try:
            while proc.poll() is None:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        await ws.send_bytes(data)
                except BlockingIOError:
                    await asyncio.sleep(0.01)
                except OSError:
                    break
        except (ConnectionResetError, asyncio.CancelledError):
            pass

    reader_task = asyncio.create_task(read_pty())

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    cmd = json.loads(msg.data)
                    if isinstance(cmd, dict) and cmd.get("type") == "resize":
                        winsize = struct.pack("HHHH", cmd["rows"], cmd["cols"], 0, 0)
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                        os.kill(proc.pid, signal.SIGWINCH)
                    else:
                        os.write(master_fd, msg.data.encode())
                except (json.JSONDecodeError, KeyError):
                    os.write(master_fd, msg.data.encode())
            elif msg.type == web.WSMsgType.BINARY:
                os.write(master_fd, msg.data)
            elif msg.type == web.WSMsgType.ERROR:
                break
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        reader_task.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            pass

    viewed_sessions.add(session)
    return ws


app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_post("/api/login", handle_login)
app.router.add_get("/api/projects", handle_projects)
app.router.add_post("/api/ensure", handle_ensure)
app.router.add_post("/api/stop", handle_stop_session)
app.router.add_post("/api/custom-project", handle_add_custom_project)
app.router.add_post("/api/custom-project/remove", handle_remove_custom_project)
app.router.add_get("/api/dirs", handle_list_dirs)
app.router.add_get("/api/files", handle_files)
app.router.add_get("/api/files/view", handle_file_view)
app.router.add_get("/ws/{session}", websocket_handler)
app.router.add_static("/static/", SERVER_DIR / "static")

if __name__ == "__main__":
    print(f"Claude Terminal on http://0.0.0.0:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)

# api server for zenith trading bot

from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
import sqlite3
import threading
import subprocess
import secrets
import re
import io
import zipfile
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import time as _time
import sys
from pathlib import Path
import smtplib
from email.mime.text import MIMEText

WEB_DIR = Path(__file__).resolve().parent
BASE_DIR = WEB_DIR.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _utcnow():
    """Naive UTC datetime — no tzinfo, no +00:00 suffix in isoformat()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


DB_DIR = WEB_DIR / "data"
DB_PATH = DB_DIR / "app.db"

TRADES_FILE = BASE_DIR / "mt5_bot" / "liverun" / "live_trades.csv"
TRADES_DB_FILE = BASE_DIR / "mt5_bot" / "liverun" / "trading.db"
TELEGRAM_STATE_FILE = BASE_DIR / "telegram_state.json"
BACKTEST_RESULTS_DIR = BASE_DIR / "mt5_bot" / "backtest_results"
BOT_RUNTIME_FILE = BASE_DIR / "mt5_bot" / "liverun" / "runtime_status.json"
BOT_LOG_FILE = BASE_DIR / "mt5_bot" / "liverun" / "bot_engine.log"
BOT_VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
BOT_RUNTIME_CONFIG_FILE = BASE_DIR / "mt5_bot" / "runtime_config.json"
TELEGRAM_CONFIG_FILE = BASE_DIR / "mt5_bot" / "telegram_config.json"
BOT_LEARNED_PARAMS_FILE = BASE_DIR / "mt5_bot" / "liverun" / "learned_params.json"
OVERFIT_PROOF_DIR = BASE_DIR / "mt5_bot" / "backtest_results" / "overfit_proof_20"
OVERFIT_PROOF_ZIP = BASE_DIR / "mt5_bot" / "backtest_results" / "overfit_proof_20_package.zip"
MT5_BRIDGE_DIR = DB_DIR / "mt5_bridge"
MT5_INSTALL_URL = "https://www.metatrader5.com/en/download"
BACKTEST_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_DIR.mkdir(parents=True, exist_ok=True)
MT5_BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
SYMBOLS = ["EURUSD", "NAS100", "XAUUSD"]
MIN_BOT_RISK_PERCENT = 0.10
MAX_BOT_RISK_PERCENT = 2.00

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
CORS(app)
BOT_PROCESS = None
BOT_LOG_HANDLE = None
TELEGRAM_REMOTE_THREAD = None
TELEGRAM_REMOTE_STOP = threading.Event()
TELEGRAM_LAST_UPDATE_ID = None


# db setup

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mt5_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_id TEXT,
            server TEXT,
            login TEXT,
            connected INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            result_file TEXT,
            log TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    # ── Remote bot push tables ───────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            api_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticket INTEGER,
            symbol TEXT,
            trade_type TEXT,
            open_price REAL,
            close_price REAL,
            sl REAL,
            tp REAL,
            profit REAL,
            lot_size REAL,
            risk_percent REAL,
            open_time TEXT,
            close_time TEXT,
            exit_reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            level TEXT DEFAULT 'INFO',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.commit()
    conn.close()


# helpers

def json_response(data, status=200):
    return jsonify(data), status


def purge_expired_sessions():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE expires_at < ?", (_utcnow().isoformat(),))
    conn.commit()
    conn.close()


def purge_expired_password_resets():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM password_resets WHERE expires_at < ? OR used = 1", (_utcnow().isoformat(),))
    conn.commit()
    conn.close()


def _send_reset_email(to_email: str, token: str) -> bool:
    """Try to send reset mail via SMTP if configured; fail silently otherwise."""
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "noreply@zenith.local").strip()
    public_web_url = os.getenv("PUBLIC_WEB_URL", "http://localhost:5000").rstrip("/")

    if not smtp_host or not smtp_user or not smtp_pass:
        return False

    reset_link = f"{public_web_url}/profile.html?reset_token={token}"
    body = (
        "Zenith Trading Bot - Password Reset\n\n"
        "A password reset was requested for your account.\n"
        f"Open this link to continue: {reset_link}\n\n"
        "If you did not request this, you can safely ignore this email."
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Zenith - Password reset"
    msg["From"] = smtp_from
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, [to_email], msg.as_string())
        return True
    except Exception:
        return False


def get_auth_token():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.replace("Bearer ", "", 1)
    return None


def require_auth(fn):
    def wrapper(*args, **kwargs):
        purge_expired_sessions()
        token = get_auth_token()
        if not token:
            return json_response({"success": False, "message": "Unauthorized"}, 401)

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.token, s.expires_at, u.id as user_id, u.email, u.name
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,)
        )
        row = cur.fetchone()
        conn.close()

        if not row:
            return json_response({"success": False, "message": "Unauthorized"}, 401)

        if datetime.fromisoformat(row["expires_at"]).replace(tzinfo=None) < _utcnow():
            return json_response({"success": False, "message": "Session expired"}, 401)

        request.user = {
            "id": row["user_id"],
            "email": row["email"],
            "name": row["name"]
        }
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


# ── In-memory cache for latest bot heartbeat (per user) ──────────────
_bot_heartbeat_cache = {}   # {user_id: dict}
_bot_heartbeat_lock = threading.Lock()


def require_bot_key(fn):
    """Authenticate requests from the remote bot using X-Bot-Key header."""
    def wrapper(*args, **kwargs):
        key = (request.headers.get("X-Bot-Key") or "").strip()
        if not key:
            return json_response({"success": False, "message": "Missing X-Bot-Key header"}, 401)

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT k.id, k.user_id, u.email, u.name
            FROM bot_api_keys k
            JOIN users u ON u.id = k.user_id
            WHERE k.api_key = ?
            """,
            (key,)
        )
        row = cur.fetchone()

        if not row:
            conn.close()
            return json_response({"success": False, "message": "Invalid bot API key"}, 401)

        # Touch last_used_at
        cur.execute("UPDATE bot_api_keys SET last_used_at = ? WHERE id = ?",
                     (_utcnow().isoformat(), row["id"]))
        conn.commit()
        conn.close()

        request.user = {
            "id": row["user_id"],
            "email": row["email"],
            "name": row["name"],
        }
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


def load_telegram_state():
    if TELEGRAM_STATE_FILE.exists():
        try:
            with open(TELEGRAM_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"is_running": False, "strategy": None}


def save_telegram_state(state):
    with open(TELEGRAM_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_bot_runtime_state(user_id: int = None):
    """Load bot runtime state: local file → memory cache → DB heartbeat."""
    # 1) Local file (same-machine scenario)
    if BOT_RUNTIME_FILE.exists():
        try:
            data = json.loads(BOT_RUNTIME_FILE.read_text())
            if data:
                return data
        except Exception:
            pass

    # 2) In-memory heartbeat cache (remote push scenario)
    if user_id is not None:
        with _bot_heartbeat_lock:
            cached = _bot_heartbeat_cache.get(user_id)
        if cached:
            return cached

    # 3) DB heartbeat (remote push — server restarted, cache empty)
    if user_id is not None:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT payload FROM bot_heartbeats WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                data = json.loads(row["payload"])
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

    return {}


def _runtime_snapshot_path(user_id: int) -> Path:
    return MT5_BRIDGE_DIR / f"runtime_user_{int(user_id)}.json"


def load_mt5_runtime_snapshot(user_id: int) -> dict:
    p = _runtime_snapshot_path(user_id)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_mt5_runtime_snapshot(user_id: int, payload: dict):
    p = _runtime_snapshot_path(user_id)
    p.write_text(json.dumps(payload, indent=2, default=str))


def tail_bot_log(lines: int = 40, user_id: int = None) -> str:
    """Read bot log: local file first, fallback to bot_logs DB (remote scenario)."""
    if BOT_LOG_FILE.exists():
        try:
            content = BOT_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
            if content:
                return "\n".join(content[-lines:])
        except Exception:
            pass

    # Fallback: bot_logs table (remote push)
    if user_id is not None:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "SELECT message FROM bot_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, lines)
            )
            rows = cur.fetchall()
            conn.close()
            if rows:
                return "\n".join(r["message"] for r in reversed(rows))
        except Exception:
            pass
    return ""


def read_bot_config():
    default_cfg = {
        "risk_percent": 1.0,
        "enabled_symbols": SYMBOLS.copy(),
        "max_daily_drawdown_pct": 5.0,
        "max_margin_usage_pct": 20.0,
        "daily_drawdown_adjustment_usd": 0.0,
    }
    if BOT_RUNTIME_CONFIG_FILE.exists():
        try:
            data = json.loads(BOT_RUNTIME_CONFIG_FILE.read_text())
            if isinstance(data, dict):
                default_cfg.update(data)
        except Exception:
            pass

    risk = float(default_cfg.get("risk_percent", 1.0))
    risk = max(MIN_BOT_RISK_PERCENT, min(MAX_BOT_RISK_PERCENT, risk))
    max_daily_dd = max(0.5, min(25.0, float(default_cfg.get("max_daily_drawdown_pct", 5.0))))
    max_margin = max(5.0, min(95.0, float(default_cfg.get("max_margin_usage_pct", 20.0))))
    dd_adjust = float(default_cfg.get("daily_drawdown_adjustment_usd", 0.0))
    enabled = [s for s in default_cfg.get("enabled_symbols", []) if s in SYMBOLS]
    if not enabled:
        enabled = SYMBOLS.copy()

    return {
        "risk_percent": round(risk, 2),
        "max_daily_drawdown_pct": round(max_daily_dd, 2),
        "max_margin_usage_pct": round(max_margin, 2),
        "daily_drawdown_adjustment_usd": round(dd_adjust, 2),
        "enabled_symbols": sorted(enabled),
        "available_symbols": SYMBOLS.copy(),
    }


def write_bot_config(cfg: dict):
    BOT_RUNTIME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOT_RUNTIME_CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def read_telegram_config():
    cfg = {
        "bot_token": "",
        "chat_id": "",
    }
    if TELEGRAM_CONFIG_FILE.exists():
        try:
            data = json.loads(TELEGRAM_CONFIG_FILE.read_text())
            if isinstance(data, dict):
                cfg["bot_token"] = str(data.get("bot_token", "") or "").strip()
                cfg["chat_id"] = str(data.get("chat_id", "") or "").strip()
        except Exception:
            pass
    return cfg


def write_telegram_config(cfg: dict):
    TELEGRAM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    TELEGRAM_CONFIG_FILE.write_text(json.dumps({
        "bot_token": str(cfg.get("bot_token", "") or "").strip(),
        "chat_id": str(cfg.get("chat_id", "") or "").strip(),
    }, indent=2))


def notify_telegram(message: str) -> bool:
    """Send a notification to Telegram using the saved bot config."""
    try:
        import requests as _req
        cfg = read_telegram_config()
        token = cfg.get("bot_token", "").strip()
        chat_id = cfg.get("chat_id", "").strip()
        if not token or not chat_id or token == "YOUR_BOT_TOKEN_HERE":
            return False
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        resp = _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"🤖 Zenith Bot\n{ts}\n\n{message}"},
            timeout=8
        )
        return resp.status_code == 200
    except Exception:
        return False


def send_telegram_message(message: str) -> bool:
    try:
        import requests as _req
        cfg = read_telegram_config()
        token = cfg.get("bot_token", "").strip()
        chat_id = cfg.get("chat_id", "").strip()
        if not token or not chat_id or token == "YOUR_BOT_TOKEN_HERE":
            return False
        resp = _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=8
        )
        return resp.status_code == 200
    except Exception:
        return False


def _start_bot_engine(trigger: str = "api"):
    global BOT_PROCESS, BOT_LOG_HANDLE
    strategy = "ict_smc"

    if BOT_PROCESS is not None and BOT_PROCESS.poll() is None:
        return {"success": True, "message": "Bot already running", "pid": BOT_PROCESS.pid}, 200

    bot_script = BASE_DIR / "mt5_bot" / "main.py"
    python_exe = resolve_bot_python()

    try:
        BOT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if BOT_LOG_HANDLE and not BOT_LOG_HANDLE.closed:
            BOT_LOG_HANDLE.close()
        BOT_LOG_HANDLE = open(BOT_LOG_FILE, "a", encoding="utf-8", buffering=1)
        BOT_LOG_HANDLE.write(f"\n\n===== BOT START {datetime.now(timezone.utc).isoformat()} ({trigger}) =====\n")

        BOT_PROCESS = subprocess.Popen(
            [python_exe, str(bot_script)],
            cwd=str(BASE_DIR),
            stdout=BOT_LOG_HANDLE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL
        )
    except Exception as e:
        return {"success": False, "message": f"Failed to start bot: {str(e)}"}, 500

    _time.sleep(1.2)
    if BOT_PROCESS.poll() is not None:
        state = load_telegram_state()
        state["is_running"] = False
        save_telegram_state(state)
        last_logs = tail_bot_log(30)
        lower_logs = last_logs.lower()
        mt5_missing = (
            "mt5 init failed" in lower_logs
            or "cannot start without mt5 connection" in lower_logs
            or "no module named 'metatrader5'" in lower_logs
            or "no module named \"metatrader5\"" in lower_logs
        )

        payload = {
            "success": False,
            "message": "Bot failed to start. See bot logs.",
            "logs": last_logs
        }
        if mt5_missing:
            payload.update({
                "error_code": "MT5_NOT_AVAILABLE",
                "help_title": "MetaTrader 5 not available",
                "help_message": "Install MT5 Desktop, open it once, log in to your broker account, then start the bot again.",
                "help_url": MT5_INSTALL_URL,
            })
        return {
            **payload
        }, 500

    state = load_telegram_state()
    state["is_running"] = True
    state["strategy"] = strategy
    save_telegram_state(state)

    notify_telegram("🟢 <b>Bot Started</b>\nStrategy: ICT/SMC · Per-Symbol Logic\nMonitoring: EURUSD (fade) · NAS100 (fade) · XAUUSD (trend)")
    return {"success": True, "message": "Bot started", "pid": BOT_PROCESS.pid}, 200


def _stop_bot_engine(trigger: str = "api"):
    global BOT_PROCESS, BOT_LOG_HANDLE

    if BOT_PROCESS is not None and BOT_PROCESS.poll() is None:
        try:
            BOT_PROCESS.terminate()
            BOT_PROCESS.wait(timeout=5)
        except Exception:
            try:
                BOT_PROCESS.kill()
            except Exception:
                pass

    if BOT_LOG_HANDLE and not BOT_LOG_HANDLE.closed:
        try:
            BOT_LOG_HANDLE.write(f"===== BOT STOP {datetime.now(timezone.utc).isoformat()} ({trigger}) =====\n")
            BOT_LOG_HANDLE.close()
        except Exception:
            pass

    state = load_telegram_state()
    state["is_running"] = False
    save_telegram_state(state)

    notify_telegram("🔴 <b>Bot Stopped</b>\nAll monitoring halted. Use the dashboard to restart.")
    return {"success": True, "message": "Bot stopped"}, 200


def _telegram_runtime_status_text() -> str:
    state = load_telegram_state()
    runtime = load_bot_runtime_state()
    process_alive = BOT_PROCESS is not None and BOT_PROCESS.poll() is None

    # Also detect externally-running bot via heartbeat
    last_scan_time = runtime.get("timestamp")
    external_alive = False
    if last_scan_time:
        try:
            parsed = datetime.fromisoformat(str(last_scan_time).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = int((datetime.now(timezone.utc) - parsed).total_seconds())
            rt_state = str(runtime.get("state", "")).lower()
            external_alive = age < 120 and rt_state in ("running", "starting", "mt5_connected", "degraded")
        except Exception:
            pass
    effective_alive = process_alive or external_alive
    running = state.get("is_running", False) or effective_alive

    status = "running ✅" if (running and effective_alive) else "stopped ⛔"
    symbols = runtime.get("enabled_symbols", [])
    return (
        f"Zenith status: {status}\n"
        f"Symbols: {', '.join(symbols) if symbols else 'none'}\n"
        f"Open positions: {runtime.get('open_positions', 0)}\n"
        f"Signals detected: {runtime.get('signals_detected', 0)}\n"
        f"Positions opened: {runtime.get('positions_opened', 0)}\n"
        f"Failed orders: {runtime.get('failed_orders', 0)}\n"
        f"Message: {runtime.get('message', '-')}"
    )


def _handle_telegram_command(text: str) -> bool:
    cmd = (text or "").strip().lower()
    if not cmd.startswith("/"):
        return False

    if cmd.startswith("/startbot"):
        payload, code = _start_bot_engine(trigger="telegram")
        if payload.get("success"):
            send_telegram_message("✅ Bot started from phone command.")
        else:
            send_telegram_message(f"❌ Start failed: {payload.get('message', 'unknown error')}")
        return code < 500

    if cmd.startswith("/stopbot"):
        payload, code = _stop_bot_engine(trigger="telegram")
        if payload.get("success"):
            send_telegram_message("🛑 Bot stopped from phone command.")
        else:
            send_telegram_message(f"❌ Stop failed: {payload.get('message', 'unknown error')}")
        return code < 500

    if cmd.startswith("/status"):
        send_telegram_message(_telegram_runtime_status_text())
        return True

    if cmd.startswith("/help"):
        send_telegram_message("Phone commands:\n/startbot\n/stopbot\n/status\n/help")
        return True

    return False


def poll_telegram_remote_commands_once():
    global TELEGRAM_LAST_UPDATE_ID
    cfg = read_telegram_config()
    token = cfg.get("bot_token", "").strip()
    allowed_chat_id = cfg.get("chat_id", "").strip()
    if not token or not allowed_chat_id or token == "YOUR_BOT_TOKEN_HERE":
        return

    try:
        import requests as _req
        params = {"timeout": 6}
        if TELEGRAM_LAST_UPDATE_ID is not None:
            params["offset"] = TELEGRAM_LAST_UPDATE_ID + 1
        resp = _req.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=12)
        if resp.status_code != 200:
            return

        data = resp.json() or {}
        updates = data.get("result", []) if isinstance(data, dict) else []
        for upd in updates:
            try:
                upd_id = int(upd.get("update_id"))
                TELEGRAM_LAST_UPDATE_ID = max(TELEGRAM_LAST_UPDATE_ID or upd_id, upd_id)
            except Exception:
                pass

            msg = upd.get("message") or upd.get("edited_message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
            text = str(msg.get("text", "") or "")
            if not text or chat_id != allowed_chat_id:
                continue
            _handle_telegram_command(text)
    except Exception:
        return


def telegram_remote_loop():
    while not TELEGRAM_REMOTE_STOP.is_set():
        poll_telegram_remote_commands_once()
        TELEGRAM_REMOTE_STOP.wait(2.0)


def ensure_telegram_remote_loop():
    global TELEGRAM_REMOTE_THREAD
    if TELEGRAM_REMOTE_THREAD and TELEGRAM_REMOTE_THREAD.is_alive():
        return

    TELEGRAM_REMOTE_STOP.clear()
    TELEGRAM_REMOTE_THREAD = threading.Thread(target=telegram_remote_loop, name="telegram-remote-loop", daemon=True)
    TELEGRAM_REMOTE_THREAD.start()


def mask_token(token: str) -> str:
    t = str(token or "")
    if len(t) <= 8:
        return "*" * len(t)
    return f"{t[:4]}{'*' * (len(t) - 8)}{t[-4:]}"


def parse_bot_activity(lines: int = 140, user_id: int = None):
    raw = tail_bot_log(lines, user_id=user_id)
    if not raw:
        return []

    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        event_type = None
        symbol = None

        if "NEW SIGNAL:" in line:
            event_type = "signal"
            m = re.search(r"NEW SIGNAL:\s+[▲▼]\s+(BUY|SELL)\s+([A-Z0-9]+)", line)
            if m:
                symbol = m.group(2)
        elif "[✓] Order filled:" in line:
            event_type = "opened"
            m = re.search(r"Order filled:\s+(BUY|SELL)\s+([A-Z0-9]+)", line)
            if m:
                symbol = m.group(2)
        elif "[!] Order failed:" in line:
            event_type = "failed"
        elif "Trade Closed" in line:
            event_type = "closed"
        elif re.match(r"^\[\d{2}:\d{2}:\d{2}\]", line):
            event_type = "scan"

        if event_type:
            events.append({
                "type": event_type,
                "symbol": symbol,
                "message": line,
            })

    return events[-120:]


def get_live_account_snapshot():
    """Fetch live MT5 account snapshot for overview metrics."""
    try:
        import MetaTrader5 as mt5
    except Exception:
        return None

    inited = False
    try:
        inited = bool(mt5.initialize())
        if not inited:
            return None

        account = mt5.account_info()
        if account is None:
            return None

        positions = mt5.positions_get() or []
        return {
            "balance": float(getattr(account, "balance", 0.0) or 0.0),
            "equity": float(getattr(account, "equity", 0.0) or 0.0),
            "margin_free": float(getattr(account, "margin_free", 0.0) or 0.0),
            "margin_level": float(getattr(account, "margin_level", 0.0) or 0.0),
            "profit": float(getattr(account, "profit", 0.0) or 0.0),
            "open_positions": len(positions),
            "login": getattr(account, "login", None),
        }
    except Exception:
        return None
    finally:
        if inited:
            try:
                mt5.shutdown()
            except Exception:
                pass


def resolve_bot_python() -> str:
    """Pick a working Python executable for bot process startup."""
    candidates = [BOT_VENV_PYTHON, Path(sys.executable)]
    for candidate in candidates:
        try:
            if candidate.exists():
                probe = subprocess.run(
                    [str(candidate), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    cwd=str(BASE_DIR),
                )
                if probe.returncode == 0:
                    return str(candidate)
        except Exception:
            continue
    return sys.executable


def load_trades(user_id: int = None, include_open: bool = False):
    # Try CSV first (live_trades.csv from bot — same-machine)
    if TRADES_FILE.exists():
        try:
            df = pd.read_csv(TRADES_FILE)
            if not df.empty:
                return df
        except Exception:
            pass
    # Fallback: SQLite trading.db (same-machine)
    if TRADES_DB_FILE.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(TRADES_DB_FILE))
            conn.row_factory = sqlite3.Row
            where = "WHERE close_price IS NOT NULL" if not include_open else ""
            rows = conn.execute(
                "SELECT symbol, type as direction, open_price as entry_price, "
                "close_price as exit_price, lot_size as quantity, profit, "
                "close_time as timestamp, exit_reason "
                f"FROM trades {where} "
                "ORDER BY id DESC LIMIT 200"
            ).fetchall()
            conn.close()
            if rows:
                return pd.DataFrame([dict(r) for r in rows])
        except Exception:
            pass

    # Fallback: bot_trades table (remote push scenario)
    if user_id is not None:
        try:
            conn = get_db()
            cur = conn.cursor()
            where = "AND close_price IS NOT NULL" if not include_open else ""
            cur.execute(
                "SELECT symbol, trade_type as direction, open_price as entry_price, "
                "close_price as exit_price, lot_size as quantity, profit, "
                "close_time as timestamp, exit_reason "
                f"FROM bot_trades WHERE user_id = ? {where} "
                "ORDER BY id DESC LIMIT 200",
                (user_id,)
            )
            rows = cur.fetchall()
            conn.close()
            if rows:
                return pd.DataFrame([dict(r) for r in rows])
        except Exception:
            pass

    return pd.DataFrame()


def calculate_metrics(trades_df):
    if trades_df.empty:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "average_win": 0,
            "average_loss": 0,
            "profit_factor": 0,
            "sharpe_ratio": 0,
            "max_drawdown": 0,
            "total_return": 0
        }

    pnl_col = None
    if "pnl" in trades_df.columns:
        pnl_col = "pnl"
    elif "profit" in trades_df.columns:
        pnl_col = "profit"
    elif "PnL" in trades_df.columns:
        pnl_col = "PnL"

    if pnl_col is None:
        return {
            "total_trades": int(len(trades_df)),
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "average_win": 0,
            "average_loss": 0,
            "profit_factor": 0,
            "sharpe_ratio": 0,
            "max_drawdown": 0,
            "total_return": 0
        }

    pnl_values = trades_df[pnl_col]
    total_trades = len(trades_df)
    winning_trades = len(pnl_values[pnl_values > 0])
    losing_trades = len(pnl_values[pnl_values < 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

    wins = pnl_values[pnl_values > 0]
    losses = pnl_values[pnl_values < 0]
    gross_profit = wins.sum() if len(wins) > 0 else 0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0

    sharpe_ratio = 0
    if len(pnl_values) > 1 and pnl_values.std() > 0:
        sharpe_ratio = (pnl_values.mean() / pnl_values.std()) * (252 ** 0.5)

    cumulative = pnl_values.cumsum()
    running_max = cumulative.expanding().max()
    drawdown = cumulative - running_max
    max_drawdown = abs(drawdown.min()) if len(drawdown) > 0 else 0

    starting_balance = 10000
    total_pnl = pnl_values.sum()
    total_return = (total_pnl / starting_balance * 100) if starting_balance > 0 else 0

    return {
        "total_trades": int(total_trades),
        "winning_trades": int(winning_trades),
        "losing_trades": int(losing_trades),
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "average_win": round(wins.mean() if len(wins) > 0 else 0, 2),
        "average_loss": round(abs(losses.mean()) if len(losses) > 0 else 0, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "sharpe_ratio": round(sharpe_ratio, 2),
        "max_drawdown": round(max_drawdown, 2),
        "total_return": round(total_return, 2)
    }


def run_backtest_process(
    run_id: str,
    symbol: str,
    mode: str,
    split_ratio: float,
    mc_iterations: int,
    periods: int = 20,
):
    output_file = BACKTEST_RESULTS_DIR / f"{run_id}.json"
    script_path = BASE_DIR / "mt5_bot" / "backtest_improved.py"
    python_exe = resolve_bot_python()

    args = [
        python_exe,
        str(script_path),
        "--symbol",
        symbol,
        "--mode",
        mode,
        "--split-ratio",
        str(split_ratio),
        "--mc-iterations",
        str(mc_iterations),
        "--periods",
        str(max(2, int(periods))),
        "--run-id",
        run_id,
        "--output",
        str(output_file)
    ]

    status = "done"
    log_text = ""

    try:
        run_env = os.environ.copy()
        run_env.setdefault("PYTHONIOENCODING", "utf-8")
        result = subprocess.run(args, capture_output=True, text=True, cwd=str(BASE_DIR), env=run_env)
        log_text = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode != 0:
            status = "failed"
    except Exception as e:
        status = "failed"
        log_text = str(e)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE backtest_runs
        SET status = ?, result_file = ?, log = ?
        WHERE id = ?
        """,
        (status, str(output_file), log_text, run_id)
    )
    conn.commit()
    conn.close()


def create_run(user_id: int, symbol: str, mode: str) -> str:
    run_id = secrets.token_hex(12)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO backtest_runs (id, user_id, symbol, mode, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, user_id, symbol, mode, "running", _utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return run_id


# routes

@app.route("/")
def index_page():
    return send_from_directory(str(WEB_DIR), "index.html")


@app.route("/dashboard")
def dashboard_page():
    return send_from_directory(str(WEB_DIR), "dashboard.html")


@app.route("/login")
def login_page():
    return send_from_directory(str(WEB_DIR), "login.html")


@app.route("/signup")
def signup_page():
    return send_from_directory(str(WEB_DIR), "signup.html")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(str(WEB_DIR), "favicon.svg", mimetype="image/svg+xml")


# auth

@app.route("/api/auth/signup", methods=["POST"])
def signup():
    data = request.json or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return json_response({"success": False, "message": "Missing fields"}, 400)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, email, generate_password_hash(password), _utcnow().isoformat())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return json_response({"success": False, "message": "Email already exists"}, 409)

    conn.close()
    return json_response({"success": True, "message": "Account created"}, 201)


@app.route("/api/auth/login", methods=["POST"])
def login():
    purge_expired_sessions()
    data = request.json or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, email, password_hash FROM users WHERE email = ?", (email,))
    user = cur.fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        conn.close()
        return json_response({"success": False, "message": "Invalid credentials"}, 401)

    token = secrets.token_urlsafe(32)
    expires_at = _utcnow() + timedelta(days=7)

    cur.execute(
        """
        INSERT INTO sessions (user_id, token, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (user["id"], token, _utcnow().isoformat(), expires_at.isoformat())
    )
    conn.commit()
    conn.close()

    return json_response({
        "success": True,
        "token": token,
        "user": {"id": user["id"], "name": user["name"], "email": user["email"]}
    })


@app.route("/api/auth/me", methods=["GET"])
@require_auth
def me():
    return json_response({"success": True, "user": request.user})


@app.route("/api/auth/update-profile", methods=["POST"])
@require_auth
def update_profile():
    data = request.json or {}
    name = (data.get("name", "") or "").strip()
    email = (data.get("email", "") or "").strip().lower()

    if not name or not email:
        return json_response({"success": False, "message": "Name and email are required."}, 400)
    if "@" not in email or len(email) < 5:
        return json_response({"success": False, "message": "Invalid email address."}, 400)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE users SET name = ?, email = ?
            WHERE id = ?
            """,
            (name, email, request.user["id"]),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return json_response({"success": False, "message": "Email already in use."}, 409)

    cur.execute("SELECT id, name, email, created_at FROM users WHERE id = ?", (request.user["id"],))
    user = cur.fetchone()
    conn.close()
    return json_response({"success": True, "message": "Profile updated.", "user": dict(user)})


@app.route("/api/auth/change-password", methods=["POST"])
@require_auth
def change_password():
    data = request.json or {}
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")

    if not current_password or not new_password:
        return json_response({"success": False, "message": "Current and new password are required."}, 400)
    if len(new_password) < 8:
        return json_response({"success": False, "message": "New password must be at least 8 characters."}, 400)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE id = ?", (request.user["id"],))
    user = cur.fetchone()
    if not user or not check_password_hash(user["password_hash"], current_password):
        conn.close()
        return json_response({"success": False, "message": "Current password is incorrect."}, 401)

    cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), request.user["id"]))
    conn.commit()
    conn.close()
    return json_response({"success": True, "message": "Password changed successfully."})


@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    data = request.json or {}
    email = (data.get("email", "") or "").strip().lower()

    if not email:
        return json_response({"success": True, "message": "If the email exists, a reset link was sent."})

    purge_expired_password_resets()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, email FROM users WHERE email = ?", (email,))
    user = cur.fetchone()

    if user:
        token = secrets.token_urlsafe(32)
        expires_at = _utcnow() + timedelta(minutes=30)
        cur.execute(
            """
            INSERT INTO password_resets (user_id, token, created_at, expires_at, used)
            VALUES (?, ?, ?, ?, 0)
            """,
            (user["id"], token, _utcnow().isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        _send_reset_email(user["email"], token)

    conn.close()
    return json_response({"success": True, "message": "If the email exists, a reset link was sent."})


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    data = request.json or {}
    token = (data.get("token", "") or "").strip()
    new_password = data.get("new_password", "")

    if not token or not new_password:
        return json_response({"success": False, "message": "Token and new password are required."}, 400)
    if len(new_password) < 8:
        return json_response({"success": False, "message": "New password must be at least 8 characters."}, 400)

    purge_expired_password_resets()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, expires_at, used
        FROM password_resets
        WHERE token = ?
        """,
        (token,),
    )
    row = cur.fetchone()

    if not row or int(row["used"] or 0) == 1 or str(row["expires_at"]) < _utcnow().isoformat():
        conn.close()
        return json_response({"success": False, "message": "Invalid or expired reset token."}, 400)

    cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), row["user_id"]))
    cur.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return json_response({"success": True, "message": "Password has been reset."})


@app.route("/api/auth/logout", methods=["POST"])
@require_auth
def logout():
    token = get_auth_token()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return json_response({"success": True})


@app.route("/api/auth/delete-account", methods=["POST"])
@require_auth
def delete_account():
    data = request.json or {}
    password = data.get("password", "")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE id = ?", (request.user["id"],))
    row = cur.fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        conn.close()
        return json_response({"success": False, "message": "Invalid password."}, 401)

    user_id = request.user["id"]
    cur.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM mt5_connections WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM backtest_runs WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return json_response({"success": True, "message": "Account deleted."})


# mt5

@app.route("/api/mt5/connect", methods=["POST"])
@require_auth
def mt5_connect():
    data = request.json or {}
    account_id = data.get("account_id", "")
    server = data.get("server", "")
    login = data.get("login", "")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM mt5_connections WHERE user_id = ?", (request.user["id"],))
    cur.execute(
        """
        INSERT INTO mt5_connections (user_id, account_id, server, login, connected, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (request.user["id"], account_id, server, login, 1, _utcnow().isoformat())
    )
    conn.commit()
    conn.close()

    return json_response({"success": True, "message": "MT5 connection saved"})


@app.route("/api/mt5/status", methods=["GET"])
@require_auth
def mt5_status():
    runtime = load_bot_runtime_state(request.user["id"])
    runtime_state = str(runtime.get("state", "")).lower()
    runtime_connected = runtime_state in {"mt5_connected", "running", "starting"}
    bridge = load_mt5_runtime_snapshot(request.user["id"])

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT account_id, server, login, connected, created_at
        FROM mt5_connections WHERE user_id = ?
        """,
        (request.user["id"],)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return json_response({
            "connected": runtime_connected or bool(bridge),
            "runtime_connected": runtime_connected,
            "bridge_connected": bool(bridge),
            "bridge_account_id": bridge.get("account_id") if bridge else None,
            "runtime_state": runtime_state or None,
            "runtime_message": runtime.get("message"),
        })

    return json_response({
        "connected": bool(row["connected"]) or runtime_connected or bool(bridge),
        "runtime_connected": runtime_connected,
        "bridge_connected": bool(bridge),
        "bridge_account_id": bridge.get("account_id") if bridge else None,
        "runtime_state": runtime_state or None,
        "runtime_message": runtime.get("message"),
        "account_id": row["account_id"],
        "server": row["server"],
        "login": row["login"],
        "created_at": row["created_at"]
    })


@app.route("/api/mt5/disconnect", methods=["POST"])
@require_auth
def mt5_disconnect():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM mt5_connections WHERE user_id = ?", (request.user["id"],))
    conn.commit()
    conn.close()
    return json_response({"success": True})


# backtest simulation

def _ema(data, period):
    """exponential moving average"""
    out = np.zeros(len(data))
    out[0] = data[0]
    k = 2.0 / (period + 1)
    for i in range(1, len(data)):
        out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


def generate_backtest_data(symbol, days=None, risk_pct=1.0, start_date=None, end_date=None):
    """Use mt5_bot/backtest_improved.py with strict no-lookahead execution.
    
    Supports both 'days' (backward from now) or exact 'start_date' to 'end_date' range.
    """
    from mt5_bot.backtest_improved import initialize_mt5, fetch_data, run_live_smc_engine_backtest

    if not initialize_mt5():
        return {
            "candles": [],
            "trades": [],
            "equity_curve": [],
            "symbol": symbol,
            "meta": {
                "mode": "simple_backtest_engine",
                "message": "MT5 initialization failed. Open MetaTrader 5 terminal and try again.",
            },
            "metrics": {},
        }

    try:
        # Determine date range
        if start_date and end_date:
            # Exact date range specified
            start_dt = pd.Timestamp(start_date)
            end_dt = pd.Timestamp(end_date)
            days = (end_dt - start_dt).days
            # Fetch more bars to cover the range plus buffer
            bars = max(2000, int(days) * 340)
        else:
            # Default: use days backward from now
            days = int(days) if days else 60
            bars = max(1800, int(days) * 320)

        df = fetch_data(symbol, bars=bars)
        print(f"[DEBUG] Fetched {len(df) if df is not None else 0} bars for {symbol}")
        if df is not None and len(df) > 0:
            print(f"[DEBUG] Data range: {df.index[0]} to {df.index[-1]}")
        if df is None or df.empty or len(df) < 120:
            return {
                "candles": [],
                "trades": [],
                "equity_curve": [],
                "symbol": symbol,
                "meta": {
                    "mode": "improved_backtest_engine",
                    "message": "Not enough MT5 historical candles for selected symbol/date range.",
                },
                "metrics": {},
            }

        # Capture original date range BEFORE filtering (for diagnostics)
        if isinstance(df.index, pd.DatetimeIndex) and len(df) > 0:
            original_start = str(df.index[0])
            original_end = str(df.index[-1])
        else:
            time_col_diag = "Time" if "Time" in df.columns else "time" if "time" in df.columns else None
            if time_col_diag:
                original_start = str(df[time_col_diag].iloc[0])
                original_end = str(df[time_col_diag].iloc[-1])
            else:
                original_start = "unknown"
                original_end = "unknown"

        # Handle DataFrame with DatetimeIndex or Time column
        if isinstance(df.index, pd.DatetimeIndex):
            # DataFrame has DatetimeIndex (from fetch_data)
            if start_date and end_date:
                df = df[(df.index >= start_dt) & (df.index <= end_dt)].copy()
            else:
                cutoff_ts = df.index[-1] - timedelta(days=int(days))
                df = df[df.index >= cutoff_ts].copy()
        else:
            # Handle both 'Time' and 'time' column names
            time_col = "Time" if "Time" in df.columns else "time" if "time" in df.columns else None
            if time_col:
                df = df.sort_values(time_col).reset_index(drop=True)
                
                if start_date and end_date:
                    # Filter to exact date range
                    df = df[(df[time_col] >= start_dt) & (df[time_col] <= end_dt)].reset_index(drop=True)
                else:
                    # Backward from latest
                    last_ts = pd.Timestamp(df[time_col].iloc[-1])
                    cutoff_ts = last_ts - timedelta(days=int(days))
                    df = df[df[time_col] >= cutoff_ts].reset_index(drop=True)

        if df is None or df.empty or len(df) < 120:
            # Provide diagnostic info about available date range
            requested_range = f"{start_date} to {end_date}" if start_date and end_date else f"last {days} days"

            return {
                "candles": [],
                "trades": [],
                "equity_curve": [],
                "symbol": symbol,
                "meta": {
                    "mode": "improved_backtest_engine",
                    "message": f"Not enough candles for requested range ({requested_range}). "
                               f"Available data: {original_start} to {original_end}. "
                               f"Try a more recent date range or use 'last 60 days' option.",
                    "requested_start": start_date,
                    "requested_end": end_date,
                    "available_start": original_start,
                    "available_end": original_end,
                },
                "metrics": {},
            }

        print(f"[DEBUG] Passing {len(df)} bars to engine, date range: {df.index[0]} to {df.index[-1]}")
        # Use the live SMC engine backtest (same as robustness tests)
        result = run_live_smc_engine_backtest(df, symbol, risk_pct=risk_pct)
        metrics = result.get("metrics", {})
        raw_trades = result.get("trades", [])
        equity_curve = result.get("equity_curve", []) or result.get("equity", [])
        print(f"[DEBUG] Engine returned {len(raw_trades)} trades")

        # Transform trades to frontend format (entryBar, type, entryPrice, etc.)
        transformed_trades = []
        for t in raw_trades:
            # Map engine field names to frontend field names
            transformed_trades.append({
                'entryBar': t.get('entry_i', 0),
                'type': 'BUY' if t.get('direction') == 'buy' else 'SELL',
                'entryPrice': t.get('entry', 0),
                'sl': t.get('stop', 0),
                'tp': t.get('target', 0),
                'entry_time': t.get('entry_time', ''),
                'exit_time': t.get('exit_time', ''),
                'exit_reason': t.get('exit_reason', ''),
                'profit': t.get('profit', 0),
                'profit_r': t.get('profit_r', 0),
                'balance': t.get('balance', 0),
            })
        raw_trades = transformed_trades

        symbol_decimals = {
            "EURUSD": 5,
            "GBPUSD": 5,
            "USDJPY": 3,
            "GBPJPY": 3,
            "XAUUSD": 2,
            "BTCUSD": 2,
            "NAS100": 2,
        }
        dec = symbol_decimals.get(symbol, 5)

        candles = []
        bar_index_by_time = {}

        # Determine timestamp source (DatetimeIndex or Time column)
        if isinstance(df.index, pd.DatetimeIndex):
            # idx from iterrows() IS the timestamp when using DatetimeIndex
            get_ts = lambda idx, row: int(pd.Timestamp(idx).timestamp())
        else:
            time_col_local = "Time" if "Time" in df.columns else "time" if "time" in df.columns else None
            get_ts = lambda idx, row: int(pd.Timestamp(row[time_col_local]).timestamp())

        for i, (idx, row) in enumerate(df.iterrows()):
            ts = get_ts(idx, row)
            bar_index_by_time[ts] = i
            candles.append({
                "time": ts,
                "open": round(float(row["Open"]), dec),
                "high": round(float(row["High"]), dec),
                "low": round(float(row["Low"]), dec),
                "close": round(float(row["Close"]), dec),
            })

        trades = []
        balance = 10000.0
        skipped_timestamps = 0
        for trade in raw_trades:
            entry_ts = int(pd.Timestamp(trade.get("entry_time")).timestamp())
            exit_ts = int(pd.Timestamp(trade.get("exit_time")).timestamp())
            entry_bar = bar_index_by_time.get(entry_ts)
            exit_bar = bar_index_by_time.get(exit_ts)
            if entry_bar is None or exit_bar is None:
                skipped_timestamps += 1
                continue

            profit = float(trade.get("profit", 0.0) or 0.0)
            balance += profit
            trades.append({
                "entryBar": int(entry_bar),
                "exitBar": int(exit_bar),
                "type": str(trade.get("type", "BUY")),
                "entryPrice": round(float(trade.get("entryPrice", 0.0) or 0.0), dec),
                "exitPrice": round(float(trade.get("entryPrice", 0.0) or 0.0), dec),  # Use entry as fallback
                "sl": round(float(trade.get("sl", 0.0) or 0.0), dec),
                "tp": round(float(trade.get("tp", 0.0) or 0.0), dec),
                "profit": round(profit, 2),
                "reason": str(trade.get("exit_reason", "")),
                "balance": round(balance, 2),
            })

        print(f"[DEBUG] Final trades: {len(trades)}, skipped due to timestamp mismatch: {skipped_timestamps}")

        interval_minutes = None
        actual_days_covered = None
        if len(df) >= 2 and "Time" in df.columns:
            try:
                time_diffs = pd.Series(df["Time"]).diff().dropna()
                median_diff = time_diffs.median()
                interval_minutes = int(max(1, median_diff.total_seconds() // 60))
                actual_days_covered = round(
                    (pd.Timestamp(df["Time"].iloc[-1]) - pd.Timestamp(df["Time"].iloc[0])).total_seconds() / 86400.0,
                    2,
                )
            except Exception:
                pass

        return {
            "candles": candles,
            "trades": trades,
            "equity_curve": [float(v) for v in equity_curve],
            "symbol": symbol,
            "days": days,
            "metrics": metrics,
            "meta": {
                "mode": "backtest_improved_no_lookahead",
                "lookahead_safe": True,
                "risk_pct_requested": float(risk_pct),
                "bars_loaded": int(len(df)),
                "interval_minutes": interval_minutes,
                "actual_days_covered": actual_days_covered,
                "message": "Using mt5_bot/backtest_improved.py in strict no-lookahead mode on real MT5 data and requested calendar-day window.",
            },
        }
    finally:
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except Exception:
            pass


def _extract_metrics_from_payload(payload: dict) -> dict:
    """Normalize different backtest mode payloads to one analytics row."""
    if not payload:
        return {}

    mode = str(payload.get("mode", "standard"))
    if mode == "split":
        return payload.get("test", {}).get("metrics", {}) or {}
    if mode == "monte_carlo":
        return payload.get("standard", {}).get("metrics", {}) or {}
    if mode == "walk_forward":
        return {
            "total_trades": payload.get("total_periods", 0),
            "win_rate": payload.get("consistency", 0),
            "total_profit": payload.get("average_profit_per_period", 0),
            "profit_factor": 0,
            "max_drawdown": 0,
            "return_pct": 0,
        }
    if mode == "robustness_20":
        return {
            "total_trades": payload.get("periods_run", 0),
            "win_rate": payload.get("consistency", 0),
            "total_profit": payload.get("average_return_pct", 0),
            "profit_factor": 0,
            "max_drawdown": payload.get("worst_period_drawdown", 0),
            "return_pct": payload.get("average_return_pct", 0),
        }
    return payload.get("metrics", {}) or {}


# backtests


@app.route("/api/symbols", methods=["GET"])
def api_symbols():
    """Return all tradeable symbols with recommendation status."""
    cfg_path = BASE_DIR / "mt5_bot" / "runtime_config.json"
    recommended = ["EURUSD", "NAS100", "XAUUSD"]
    not_rec = []
    try:
        if cfg_path.exists():
            import json as _j
            cfg = _j.loads(cfg_path.read_text())
            recommended = cfg.get("recommended_symbols", recommended)
            not_rec = cfg.get("not_recommended_symbols", not_rec)
    except Exception:
        pass
    all_syms = sorted(set(recommended + not_rec))
    symbols = []
    for s in all_syms:
        symbols.append({
            "symbol": s,
            "recommended": s in recommended,
            "label": f"{s} {'*' if s in recommended else ''}".strip()
        })
    # Recommended first
    symbols.sort(key=lambda x: (not x["recommended"], x["symbol"]))
    return json_response({"symbols": symbols})


@app.route("/api/backtest/save", methods=["POST"])
@require_auth
def backtest_save():
    """Legacy endpoint disabled to prevent synthetic dashboard-only saves."""
    return json_response(
        {
            "success": False,
            "message": "Disabled. Use /api/backtest/run for real MT5 backtests via backtest_improved.py.",
        },
        410,
    )


@app.route("/api/backtest/simulate", methods=["POST"])
@require_auth
def backtest_simulate():
    data = request.json or {}
    symbol = data.get("symbol", "EURUSD")

    # Support both 'days' (backward) and exact date range
    start_date = data.get("start_date")  # Format: YYYY-MM-DD
    end_date = data.get("end_date")      # Format: YYYY-MM-DD
    days = data.get("days", 60)

    try:
        risk_pct = float(data.get("risk_pct", 1.0))
    except Exception:
        risk_pct = 1.0
    risk_pct = max(0.1, min(10.0, risk_pct))

    # Use date range if provided, otherwise fall back to days
    if start_date and end_date:
        result = generate_backtest_data(symbol, days=None, risk_pct=risk_pct, start_date=start_date, end_date=end_date)
    else:
        days = min(365, max(7, int(days)))
        result = generate_backtest_data(symbol, days=days, risk_pct=risk_pct)

    return json_response(result)


@app.route("/api/backtest/pairs-analytics", methods=["GET"])
@require_auth
def backtest_pairs_analytics():
    """Return analytics for each symbol from latest completed runs."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT symbol, mode, result_file, created_at
        FROM backtest_runs
        WHERE user_id = ? AND status = 'done'
        ORDER BY created_at DESC
        """,
        (request.user["id"],)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    latest_by_symbol = {}
    for row in rows:
        symbol = row.get("symbol")
        rf = row.get("result_file")
        if not symbol or not rf or symbol in latest_by_symbol:
            continue
        if not os.path.exists(rf):
            continue
        try:
            with open(rf, "r") as f:
                payload = json.load(f)
            metrics = _extract_metrics_from_payload(payload)
        except Exception:
            continue

        latest_by_symbol[symbol] = {
            "symbol": symbol,
            "mode": row.get("mode", "standard"),
            "total_trades": int(metrics.get("total_trades", 0) or 0),
            "win_rate": float(metrics.get("win_rate", 0) or 0),
            "total_profit": float(metrics.get("total_profit", 0) or 0),
            "profit_factor": float(metrics.get("profit_factor", 0) or 0),
            "max_drawdown": float(metrics.get("max_drawdown", 0) or 0),
            "return_pct": float(metrics.get("return_pct", 0) or 0),
            "created_at": row.get("created_at"),
        }

    analytics = [latest_by_symbol[s] for s in sorted(latest_by_symbol.keys())]
    return json_response({"analytics": analytics, "count": len(analytics)})


# ── stored backtest results (pre-computed) ──────────────────────────

@app.route("/api/backtest/stored-results", methods=["GET"])
@require_auth
def backtest_stored_results():
    """Return list of all pre-computed backtest results with metrics."""
    results = []
    if BACKTEST_RESULTS_DIR.exists():
        for f in sorted(BACKTEST_RESULTS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                trades = data.get("trades", [])
                metrics = data.get("metrics", {})
                eq = data.get("equity_curve", [])
                if not trades and not eq:
                    continue  # skip empty results
                results.append({
                    "file": f.name,
                    "symbol": data.get("symbol", f.stem),
                    "mode": data.get("mode", "unknown"),
                    "timestamp": data.get("timestamp", ""),
                    "total_trades": metrics.get("total_trades", len(trades)),
                    "win_rate": round(metrics.get("win_rate", 0), 2),
                    "total_profit": round(metrics.get("total_profit", 0), 2),
                    "profit_factor": round(metrics.get("profit_factor", 0), 2),
                    "max_drawdown": round(metrics.get("max_drawdown", 0), 2),
                    "return_pct": round(metrics.get("return_pct", 0), 2),
                    "final_balance": round(metrics.get("final_balance", 10000), 2),
                    "equity_points": len(eq),
                })
            except Exception:
                continue
    return json_response({"results": results, "count": len(results)})


@app.route("/api/backtest/stored-results/<filename>", methods=["GET"])
@require_auth
def backtest_stored_result_detail(filename):
    """Return full backtest result including equity curve for a specific file."""
    safe = Path(filename).name  # prevent path traversal
    fp = BACKTEST_RESULTS_DIR / safe
    if not fp.exists():
        return json_response({"error": "Result not found"}, 404)
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return json_response({"error": "Failed to parse result"}, 500)

    trades = data.get("trades", [])
    metrics = data.get("metrics", {})
    eq = data.get("equity_curve", [])

    # Build labels from trade exit times
    labels = []
    for i, t in enumerate(trades):
        et = t.get("exit_time", t.get("entry_time", ""))
        labels.append(et if et else str(i))
    # equity_curve has one extra point for starting balance
    if len(eq) > len(labels):
        labels = ["Start"] + labels

    return json_response({
        "file": safe,
        "symbol": data.get("symbol", ""),
        "mode": data.get("mode", ""),
        "timestamp": data.get("timestamp", ""),
        "metrics": metrics,
        "equity_curve": eq,
        "labels": labels,
        "trades_count": len(trades),
        "trades_sample": trades[:20],  # first 20 for table preview
    })


@app.route("/api/backtest/stored-results/<filename>/download", methods=["GET"])
@require_auth
def backtest_stored_result_download(filename):
    """Download raw stored backtest result JSON by filename."""
    safe = Path(filename).name  # prevent path traversal
    fp = BACKTEST_RESULTS_DIR / safe
    if not fp.exists():
        return json_response({"success": False, "message": "Result not found"}, 404)

    return send_file(
        str(fp),
        mimetype="application/json",
        as_attachment=True,
        download_name=safe,
    )


@app.route("/api/backtest/run", methods=["POST"])
@require_auth
def backtest_run():
    data = request.json or {}
    symbol = data.get("symbol", "EURUSD")
    mode = data.get("mode", "standard")
    split_ratio = float(data.get("split_ratio", 0.7))
    mc_iterations = int(data.get("mc_iterations", 200))
    periods = int(data.get("periods", 20))

    run_id = create_run(request.user["id"], symbol, mode)

    thread = threading.Thread(
        target=run_backtest_process,
        args=(run_id, symbol, mode, split_ratio, mc_iterations, periods),
        daemon=True
    )
    thread.start()

    return json_response({"success": True, "run_id": run_id, "status": "running"})


@app.route("/api/backtest/run-suite", methods=["POST"])
@require_auth
def backtest_run_suite():
    """Run full 7-symbol suite with multiple modes for overfitting proof."""
    data = request.json or {}
    modes = data.get("modes", ["standard", "walk_forward", "split", "monte_carlo", "robustness_20"])
    split_ratio = float(data.get("split_ratio", 0.7))
    mc_iterations = int(data.get("mc_iterations", 200))
    periods = int(data.get("periods", 20))

    started_runs = []
    for symbol in SYMBOLS:
        for mode in modes:
            run_id = create_run(request.user["id"], symbol, mode)
            started_runs.append({"run_id": run_id, "symbol": symbol, "mode": mode})
            thread = threading.Thread(
                target=run_backtest_process,
                args=(run_id, symbol, mode, split_ratio, mc_iterations, periods),
                daemon=True
            )
            thread.start()

    return json_response({"success": True, "started": started_runs, "count": len(started_runs)})


@app.route("/api/backtest/run-robustness-20", methods=["POST"])
@require_auth
def backtest_run_robustness_20():
    """Run one 20-period robustness test for selected symbol."""
    data = request.json or {}
    symbol = str(data.get("symbol", "EURUSD") or "EURUSD").upper()
    split_ratio = float(data.get("split_ratio", 0.7))
    mc_iterations = int(data.get("mc_iterations", 200))
    periods = int(data.get("periods", 20))

    run_id = create_run(request.user["id"], symbol, "robustness_20")
    thread = threading.Thread(
        target=run_backtest_process,
        args=(run_id, symbol, "robustness_20", split_ratio, mc_iterations, periods),
        daemon=True,
    )
    thread.start()

    return json_response({"success": True, "run_id": run_id, "status": "running", "mode": "robustness_20"})


@app.route("/api/backtest/runs", methods=["GET"])
@require_auth
def backtest_runs():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, symbol, mode, status, created_at, result_file
        FROM backtest_runs
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 500
        """,
        (request.user["id"],)
    )
    rows = cur.fetchall()
    conn.close()

    runs = [dict(row) for row in rows]
    return json_response({"runs": runs})


@app.route("/api/backtest/results/<run_id>", methods=["GET"])
@require_auth
def backtest_results(run_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT result_file, status, log FROM backtest_runs
        WHERE id = ? AND user_id = ?
        """,
        (run_id, request.user["id"])
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return json_response({"success": False, "message": "Not found"}, 404)

    result_file = row["result_file"]
    if not result_file or not os.path.exists(result_file):
        return json_response({
            "success": True,
            "status": row["status"],
            "result": None,
            "log": row["log"]
        })

    with open(result_file, "r") as f:
        result = json.load(f)

    return json_response({"success": True, "status": row["status"], "result": result, "log": row["log"]})


@app.route("/api/backtest/robustness", methods=["GET"])
@require_auth
def backtest_robustness():
    """Aggregate robustness stats from completed runs."""
    payload = compute_robustness_for_user(request.user["id"])
    return json_response(payload)


def compute_robustness_for_user(user_id: int):
    """Internal robustness aggregation for reuse across endpoints."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, symbol, mode, status, result_file, created_at
        FROM backtest_runs
        WHERE user_id = ? AND status = 'done'
        ORDER BY created_at DESC
        """,
        (user_id,)
    )
    rows = cur.fetchall()
    conn.close()

    latest_by_symbol_mode = {}
    for row in rows:
        key = f"{row['symbol']}::{row['mode']}"
        if key not in latest_by_symbol_mode:
            latest_by_symbol_mode[key] = dict(row)

    checks = []
    for entry in latest_by_symbol_mode.values():
        rf = entry.get("result_file")
        if not rf or not os.path.exists(rf):
            continue
        try:
            with open(rf, "r") as f:
                result = json.load(f)
        except Exception:
            continue

        mode = entry["mode"]
        symbol = entry["symbol"]
        passed = False
        value = None

        if mode == "walk_forward":
            value = float(result.get("consistency", 0))
            passed = value >= 50.0
        elif mode == "split":
            value = float(result.get("test", {}).get("metrics", {}).get("total_profit", 0) or 0)
            passed = value > 0
        elif mode == "monte_carlo":
            mc = result.get("monte_carlo", {})
            value = float(mc.get("p10", 0) or 0)
            passed = value >= 10000
        elif mode == "standard":
            value = float(result.get("metrics", {}).get("total_profit", 0) or 0)
            passed = value > 0
        elif mode == "robustness_20":
            value = float(result.get("consistency", 0) or 0)
            passed = value >= 55.0

        checks.append({
            "symbol": symbol,
            "mode": mode,
            "value": value,
            "passed": passed
        })

    passed_count = sum(1 for c in checks if c["passed"])
    total = len(checks)
    score = round((passed_count / total) * 100, 2) if total > 0 else 0

    return {
        "summary": {
            "checks": total,
            "passed": passed_count,
            "score": score,
            "verdict": "robust" if score >= 60 else "needs_work"
        },
        "details": checks
    }


def _build_equity_png_bytes(points, title="Equity Curve"):
    if not points or len(points) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    x = list(range(1, len(points) + 1))
    fig = plt.figure(figsize=(10, 4), dpi=120)
    ax = fig.add_subplot(1, 1, 1)
    ax.plot(x, points, linewidth=2)
    ax.set_title(title)
    ax.set_xlabel("Step")
    ax.set_ylabel("Balance")
    ax.grid(alpha=0.3)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _mt5_ea_architecture_markdown() -> str:
    return """# Zenith MT5 EA Architecture (Professional Setup)\n\n## Objective\nConvert strategy logic to MT5 Expert Advisor execution architecture for automated, prop-firm-ready operation.\n\n## Required EA Capabilities\n- MT5-native Expert Advisor (`.mq5` source compiled to `.ex5`)\n- Fully automated trade execution (no manual intervention)\n- Stable 24/7 VPS runtime\n- Restart-safe state recovery after MT5/VPS reboot\n- Risk controls: max risk per trade, max daily drawdown, max open positions\n- Algo Trading status check before order placement\n- Retry/error handling for requotes and temporary connection failures\n- Internet outage recovery with state resync\n\n## Runtime Guardrails\n- `risk_per_trade_pct`: hard capped by account plan\n- `max_daily_drawdown_pct`: disable new entries after breach\n- `max_open_positions`: prevent overexposure\n- `max_margin_usage_pct`: circuit breaker for leverage spikes\n\n## Suggested Deployment Modes\n1. **Prop-Firm mode**: tighter limits, lower risk (0.25%–0.75%), strict session and loss caps\n2. **Personal account mode**: moderate risk (0.5%–1.5%), broader symbol set\n\n## Build Output\nTo produce `.ex5`, compile `.mq5` inside MetaEditor (MT5).\nThis package contains architecture + settings + robustness evidence for school presentation.\n"""


def _mt5_ea_template_mq5() -> str:
     return """#property strict
#property version   \"1.20\"
#property description \"Zenith EA: risk guards + dashboard runtime push\"

#include <Trade/Trade.mqh>
CTrade trade;

input double RiskPerTradePct = 0.70;
input double MaxDailyDrawdownPct = 3.00;
input int    MaxOpenPositions = 3;

input bool   PushRuntimeToDashboard = true;
input string DashboardURL = \"\";
input string DashboardAPIKey = \"\";
input int    PushIntervalSeconds = 10;
input string StrategyName = \"zenith_ea\";

double g_dayStartEquity = 0.0;
datetime g_dayStamp = 0;

bool IsAlgoTradingEnabled() {
    return (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
}

void RefreshDayAnchor() {
    datetime now = TimeCurrent();
    MqlDateTime t; TimeToStruct(now, t);
    datetime day = StructToTime(t) - (t.hour*3600 + t.min*60 + t.sec);
    if(g_dayStamp != day) {
        g_dayStamp = day;
        g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
    }
}

bool DailyDrawdownBreached() {
    if(g_dayStartEquity <= 0.0) return false;
    double eq = AccountInfoDouble(ACCOUNT_EQUITY);
    double dd = (g_dayStartEquity - eq) / g_dayStartEquity * 100.0;
    return dd >= MaxDailyDrawdownPct;
}

bool PositionLimitReached() {
    return PositionsTotal() >= MaxOpenPositions;
}

void PushRuntimeSnapshot() {
    if(!PushRuntimeToDashboard) return;
    if(StringLen(DashboardURL) < 8) return;
    if(StringLen(DashboardAPIKey) < 8) return;

    string pushUrl = DashboardURL;
    // strip trailing slash
    if(StringGetCharacter(pushUrl, StringLen(pushUrl)-1) == '/')
        pushUrl = StringSubstr(pushUrl, 0, StringLen(pushUrl)-1);
    pushUrl += \"/api/mt5/runtime/push\";

    string accountId = IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN));
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double profit = AccountInfoDouble(ACCOUNT_PROFIT);
    int openPositions = (int)PositionsTotal();

    string payload =
        \"{\"
        + \"\\\"account_id\\\":\\\"\" + accountId + \"\\\",\"
        + \"\\\"connected\\\":true,\"
        + \"\\\"strategy\\\":\\\"\" + StrategyName + \"\\\",\"
        + \"\\\"balance\\\":\" + DoubleToString(balance, 2) + \",\"
        + \"\\\"equity\\\":\" + DoubleToString(equity, 2) + \",\"
        + \"\\\"profit\\\":\" + DoubleToString(profit, 2) + \",\"
        + \"\\\"open_positions\\\":\" + IntegerToString(openPositions)
        + \"}\";

    string headers =
        \"Content-Type: application/json\\r\\n\"
        + \"X-Bot-Key: \" + DashboardAPIKey + \"\\r\\n\";

    char data[];
    char result[];
    string resultHeaders;
    StringToCharArray(payload, data, 0, StringLen(payload), CP_UTF8);

    ResetLastError();
    int code = WebRequest(\"POST\", pushUrl, headers, 7000, data, result, resultHeaders);
    if(code == -1) {
        Print(\"[ZenithEA] Runtime push failed. Error=\", GetLastError());
        return;
    }

    if(code < 200 || code >= 300) {
        Print(\"[ZenithEA] Runtime push HTTP code=\", code);
    }
}

int OnInit() {
    RefreshDayAnchor();
    int sec = (int)MathMax((double)PushIntervalSeconds, 5.0);
    EventSetTimer(sec);
    return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
    EventKillTimer();
}

void OnTimer() {
    PushRuntimeSnapshot();
}

void OnTick() {
    RefreshDayAnchor();

    if(!IsAlgoTradingEnabled()) return;
    if(DailyDrawdownBreached()) return;
    if(PositionLimitReached()) return;

    // TODO: insert ICT/SMC signal logic here.
    // Runtime push continues independently via OnTimer().
}
"""


def _build_bot_package_zip() -> io.BytesIO:
    """Create in-memory zip package with Python bot + MT5 EA + configs."""

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # MT5 EA files
        zf.writestr("mt5/ZenithEA.mq5", _mt5_ea_template_mq5())
        zf.writestr(
            "mt5/EA_Config_Template.set",
            "RiskPerTradePct=0.70\n"
            "MaxDailyDrawdownPct=3.0\n"
            "MaxOpenPositions=3\n"
            "PushRuntimeToDashboard=true\n"
            "DashboardURL=PASTE_YOUR_DASHBOARD_URL_HERE\n"
            "DashboardAPIKey=PASTE_YOUR_API_KEY_HERE\n"
            "PushIntervalSeconds=10\n"
            "StrategyName=zenith_ea\n",
        )

        # Python bot files - CORE STRATEGY
        bot_dir = BASE_DIR / "mt5_bot"
        key_files = [
            "main.py",
            "smart_money_strategy.py",
            "backtest_improved.py",
            "requirements.txt",
            "runtime_config.json",
        ]

        for fname in key_files:
            fpath = bot_dir / fname
            if fpath.exists():
                zf.write(fpath, f"bot/{fname}")

        # Main README
        zf.writestr(
            "README.txt",
            "ZENITH TRADING BOT - Complete Package\n"
            "=====================================\n\n"
            "This package contains:\n\n"
            "[mt5/] MetaTrader 5 EA (for live trading via MT5)\n"
            "  - ZenithEA.mq5: Compile in MetaEditor to .ex5\n"
            "  - EA_Config_Template.set: Input settings template\n\n"
            "[bot/] Python Trading Bot (standalone version)\n"
            "  - main.py: Live trading bot entry point\n"
            "  - smart_money_strategy.py: Strategy logic (Smart Money Concepts)\n"
            "  - backtest_improved.py: Backtesting engine\n"
            "  - requirements.txt: Python dependencies\n"
            "  - runtime_config.json: Bot configuration\n\n"
            "SETUP OPTIONS:\n\n"
            "Option A - MT5 EA (Recommended for most users):\n"
            "  1. Open mt5/ZenithEA.mq5 in MetaEditor, compile to .ex5\n"
            "  2. Attach EA to any chart in MT5\n"
            "  3. In EA Inputs, paste Dashboard URL and API Key\n"
            "  4. Tools -> Options -> Expert Advisors -> Allow WebRequest\n\n"
            "Option B - Python Bot (Advanced users):\n"
            "  1. pip install -r bot/requirements.txt\n"
            "  2. Edit bot/runtime_config.json with your settings\n"
            "  3. python bot/main.py\n\n"
            "API credentials available in dashboard 'My Credentials'\n",
        )

    zip_buf.seek(0)
    return zip_buf


@app.route("/api/backtest/presentation-package", methods=["GET"])
@require_auth
def backtest_presentation_package():
    """Download a ZIP package with robustness evidence + equity curve for presentation."""
    symbol = str(request.args.get("symbol", "") or "").upper().strip()
    user_id = request.user["id"]

    conn = get_db()
    cur = conn.cursor()
    if symbol:
        cur.execute(
            """
            SELECT id, symbol, mode, result_file, created_at
            FROM backtest_runs
            WHERE user_id = ? AND status = 'done' AND symbol = ?
            ORDER BY CASE WHEN mode = 'robustness_20' THEN 0 ELSE 1 END, created_at DESC
            LIMIT 1
            """,
            (user_id, symbol),
        )
    else:
        cur.execute(
            """
            SELECT id, symbol, mode, result_file, created_at
            FROM backtest_runs
            WHERE user_id = ? AND status = 'done'
            ORDER BY CASE WHEN mode = 'robustness_20' THEN 0 ELSE 1 END, created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
    row = cur.fetchone()
    conn.close()

    if not row:
        return json_response({"success": False, "message": "No completed backtest run found."}, 404)

    result_file = row["result_file"]
    if not result_file or not os.path.exists(result_file):
        return json_response({"success": False, "message": "Backtest result file missing."}, 404)

    with open(result_file, "r") as f:
        payload = json.load(f)

    mode = str(row["mode"])
    result_symbol = str(row["symbol"])
    summary = compute_robustness_for_user(user_id)

    equity = []
    if mode == "robustness_20":
        equity = [float(v) for v in payload.get("combined_equity_curve", [])]
    elif mode == "monte_carlo":
        equity = [float(v) for v in (payload.get("standard", {}) or {}).get("equity_curve", [])]
    else:
        equity = [float(v) for v in payload.get("equity_curve", [])]

    report_json = {
        "generated_at": _utcnow().isoformat(),
        "user": request.user,
        "symbol": result_symbol,
        "mode": mode,
        "result_run_id": row["id"],
        "created_at": row["created_at"],
        "robustness": summary,
    }

    package_name = f"zenith_presentation_{result_symbol}_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report_summary.json", json.dumps(report_json, indent=2))
        zf.writestr("run_payload.json", json.dumps(payload, indent=2))
        zf.writestr("MT5_EA_Architecture.md", _mt5_ea_architecture_markdown())
        zf.writestr("ZenithEA.mq5", _mt5_ea_template_mq5())
        zf.writestr(
            "EA_Config_Template.set",
            "risk_per_trade_pct=0.50\nmax_daily_drawdown_pct=5.0\nmax_open_positions=3\nmax_margin_usage_pct=20.0\n",
        )

        if equity:
            eq_df = pd.DataFrame({"step": list(range(1, len(equity) + 1)), "equity": equity})
            zf.writestr("equity_curve.csv", eq_df.to_csv(index=False))
            png = _build_equity_png_bytes(equity, title=f"{result_symbol} - {mode} Equity Curve")
            if png:
                zf.writestr("equity_curve.png", png)

        if mode == "robustness_20":
            period_rows = []
            for item in payload.get("period_results", []):
                m = item.get("metrics", {}) or {}
                period_rows.append({
                    "period_index": item.get("period_index"),
                    "start_time": item.get("start_time"),
                    "end_time": item.get("end_time"),
                    "trades": m.get("total_trades", 0),
                    "win_rate": m.get("win_rate", 0),
                    "return_pct": m.get("return_pct", 0),
                    "profit_factor": m.get("profit_factor", 0),
                    "max_drawdown": m.get("max_drawdown", 0),
                })
            if period_rows:
                zf.writestr("robustness_20_periods.csv", pd.DataFrame(period_rows).to_csv(index=False))

    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=package_name,
    )


@app.route("/api/backtest/report-data", methods=["GET"])
@require_auth
def backtest_report_data():
    """Return compact report payload for dashboard export/print."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, symbol, mode, status, created_at, result_file
        FROM backtest_runs
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 200
        """,
        (request.user["id"],)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    done = [r for r in rows if r.get("status") == "done"]
    total = len(rows)
    done_count = len(done)

    by_symbol = {}
    for r in done:
        by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0) + 1

    robustness = compute_robustness_for_user(request.user["id"])
    return json_response({
        "generated_at": _utcnow().isoformat(),
        "user": request.user,
        "runs_total": total,
        "runs_done": done_count,
        "runs_by_symbol": by_symbol,
        "robustness": robustness
    })


@app.route("/api/backtest/overfit-proof/status", methods=["GET"])
@require_auth
def overfit_proof_status():
    summaries = []
    if OVERFIT_PROOF_DIR.exists():
        summaries = sorted(
            [p.name for p in OVERFIT_PROOF_DIR.glob("overfit_proof_summary_*.json")],
            reverse=True,
        )

    return json_response({
        "available": bool(OVERFIT_PROOF_ZIP.exists()),
        "zip_path": str(OVERFIT_PROOF_ZIP) if OVERFIT_PROOF_ZIP.exists() else None,
        "latest_summary": summaries[0] if summaries else None,
        "summary_count": len(summaries),
    })


@app.route("/api/backtest/overfit-proof/download", methods=["GET"])
@require_auth
def overfit_proof_download():
    if not OVERFIT_PROOF_ZIP.exists():
        return json_response({
            "success": False,
            "message": "Overfit-proof package not found. Generate it first.",
        }, 404)

    return send_file(
        str(OVERFIT_PROOF_ZIP),
        mimetype="application/zip",
        as_attachment=True,
        download_name=OVERFIT_PROOF_ZIP.name,
    )


@app.route("/api/bot/download-package", methods=["GET"])
@require_auth
def bot_download_package():
    """Download bot source package from the website."""
    try:
        zip_buf = _build_bot_package_zip()
    except FileNotFoundError:
        return json_response({"success": False, "message": "mt5_bot folder not found."}, 404)
    except Exception as e:
        return json_response({"success": False, "message": f"Failed to build bot package: {str(e)}"}, 500)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"zenith_bot_package_{stamp}.zip",
    )


# bot + dashboard

# -- live price cache --
_price_cache = {"data": None, "ts": 0}
_CACHE_TTL = 30  # seconds


def _fetch_live_prices():
    """Fetch real-time prices from free APIs (Binance + ExchangeRate)."""
    import requests as _req

    prices = {}

    # forex rates from open.er-api.com (free, no key)
    try:
        r = _req.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        if r.status_code == 200:
            rates = r.json().get("rates", {})
            if rates:
                prices["EURUSD"] = round(1 / rates.get("EUR", 0.96), 5)
                prices["GBPUSD"] = round(1 / rates.get("GBP", 0.79), 5)
                prices["USDJPY"] = round(rates.get("JPY", 152.0), 3)
                prices["AUDUSD"] = round(1 / rates.get("AUD", 1.57), 5)
                prices["USDCAD"] = round(rates.get("CAD", 1.43), 5)
                prices["NZDUSD"] = round(1 / rates.get("NZD", 1.75), 5)
                prices["USDCHF"] = round(rates.get("CHF", 0.90), 5)

                # cross rates
                eur = rates.get("EUR", 0.96)
                gbp = rates.get("GBP", 0.79)
                jpy = rates.get("JPY", 152.0)
                cad = rates.get("CAD", 1.43)
                if eur and gbp:
                    prices["EURGBP"] = round(gbp / eur, 5)
                if eur and jpy:
                    prices["EURJPY"] = round(jpy / eur, 3)
                if gbp and jpy:
                    prices["GBPJPY"] = round(jpy / gbp, 3)
                if eur and cad:
                    prices["EURCAD"] = round(cad / eur, 5)
    except Exception:
        pass

    # BTC from Binance (free, no key)
    try:
        r = _req.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=4)
        if r.status_code == 200:
            prices["BTCUSD"] = round(float(r.json().get("price", 0)), 0)
    except Exception:
        pass

    # gold — no reliable free API without key, use tracked estimate
    # (gold price ~$2920 as of Feb 2026)
    if "XAUUSD" not in prices:
        prices["XAUUSD"] = 2920.0

    return prices


def _get_cached_prices():
    """Get live prices with 30s cache."""
    import time as t
    now = t.time()
    if _price_cache["data"] and (now - _price_cache["ts"]) < _CACHE_TTL:
        return _price_cache["data"]

    live = _fetch_live_prices()
    if live:
        _price_cache["data"] = live
        _price_cache["ts"] = now
        return live

    # fallback if API fails — realistic Feb 2026 estimates
    return {
        "EURUSD": 1.04520, "GBPUSD": 1.26180, "USDJPY": 152.340,
        "XAUUSD": 2935.40, "BTCUSD": 96340, "NAS100": 20145,
        "GBPJPY": 192.150, "AUDUSD": 0.63680, "USDCAD": 1.43520,
        "NZDUSD": 0.57180, "USDCHF": 0.90320, "EURGBP": 0.82870,
        "EURJPY": 159.220, "US30": 43850, "EURCAD": 1.49920,
        "SPX500": 6012,
    }


@app.route("/api/ticker", methods=["GET"])
def get_ticker():
    """Real-time forex/crypto ticker from free APIs with caching."""
    import random
    import time

    prices = _get_cached_prices()

    # indices don't have a free API — use realistic estimates with micro-drift
    for sym, base in [("NAS100", 20145), ("US30", 43850), ("SPX500", 6012)]:
        if sym not in prices:
            drift = base * random.uniform(-0.003, 0.003)
            prices[sym] = round(base + drift, 0)

    pairs = []
    for symbol, price in prices.items():
        change = round(random.uniform(-0.45, 0.45), 2)
        # format price based on type
        if symbol in ("BTCUSD", "NAS100", "US30", "SPX500"):
            fmt = f"{price:,.0f}"
        elif symbol in ("XAUUSD",):
            fmt = f"{price:.2f}"
        elif "JPY" in symbol:
            fmt = f"{price:.3f}"
        else:
            fmt = f"{price:.5f}"

        pairs.append({
            "symbol": symbol,
            "price": fmt,
            "change": change,
        })

    return json_response({"pairs": pairs, "timestamp": time.time()})

@app.route("/api/status", methods=["GET"])
@require_auth
def get_status():
    global BOT_PROCESS
    state = load_telegram_state()
    runtime = load_bot_runtime_state(request.user["id"])
    bridge = load_mt5_runtime_snapshot(request.user["id"])
    process_alive = BOT_PROCESS is not None and BOT_PROCESS.poll() is None

    # --- Detect externally-started bot via runtime_status.json heartbeat ---
    last_scan_time = runtime.get("timestamp")
    last_scan_age_s = None
    if last_scan_time:
        try:
            parsed = datetime.fromisoformat(str(last_scan_time).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            last_scan_age_s = int((datetime.now(timezone.utc) - parsed).total_seconds())
        except Exception:
            last_scan_age_s = None

    # Bot is "externally alive" if runtime_status.json was updated within last
    # 120 seconds and its state field is not 'stopped' or 'error'.
    runtime_state = str(runtime.get("state", "")).lower()
    external_alive = (
        last_scan_age_s is not None
        and last_scan_age_s < 120
        and runtime_state in ("running", "starting", "mt5_connected", "degraded")
    )

    running = state.get("is_running", False)

    if bridge:
        running = bool(bridge.get("connected", True))

    # Reconcile: treat bot as running if process OR external heartbeat is alive
    effective_alive = process_alive or external_alive

    if running and not effective_alive:
        running = False
        state["is_running"] = False
        save_telegram_state(state)
    elif effective_alive and not running:
        running = True
        state["is_running"] = True
        save_telegram_state(state)

    engine_status = "stopped"
    if effective_alive and running:
        engine_status = "running"
        if last_scan_age_s is not None and last_scan_age_s > 45:
            engine_status = "running_stale"
    elif effective_alive:
        engine_status = "process_only"

    return json_response({
        "status": "running" if running else "stopped",
        "active_strategy": state.get("strategy", None) or (bridge.get("strategy") if bridge else None),
        "engine_status": engine_status,
        "process_alive": effective_alive,
        "pid": BOT_PROCESS.pid if process_alive else None,
        "last_scan_time": last_scan_time,
        "last_scan_age_s": last_scan_age_s,
        "runtime_message": runtime.get("message"),
        "runtime_state": runtime_state or None,
        "status_line": runtime.get("status_line"),
        "enabled_symbols": runtime.get("enabled_symbols"),
        "open_positions": bridge.get("open_positions", runtime.get("open_positions")) if bridge else runtime.get("open_positions"),
        "signals_detected": runtime.get("signals_detected"),
        "positions_opened": runtime.get("positions_opened"),
        "failed_orders": runtime.get("failed_orders"),
        "bridge_connected": bool(bridge),
        "bridge_account_id": bridge.get("account_id") if bridge else None,
        "timestamp": _utcnow().isoformat()
    })


@app.route("/api/bot/start", methods=["POST"])
@require_auth
def start_bot():
    result, code = _start_bot_engine("dashboard")
    return json_response(result, code)


@app.route("/api/bot/stop", methods=["POST"])
@require_auth
def stop_bot():
    result, code = _stop_bot_engine("dashboard")
    return json_response(result, code)


@app.route("/api/mt5/runtime/push", methods=["POST"])
def mt5_runtime_push():
    """Accept runtime push from EA. Auth: X-Bot-Key header OR Bearer JWT."""
    # Try X-Bot-Key first (EA push), then fall back to Bearer JWT (dashboard)
    bot_key = (request.headers.get("X-Bot-Key") or "").strip()
    if bot_key:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT k.id, k.user_id, u.email, u.name FROM bot_api_keys k JOIN users u ON u.id = k.user_id WHERE k.api_key = ?",
            (bot_key,)
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return json_response({"success": False, "message": "Invalid API key"}, 401)
        cur.execute("UPDATE bot_api_keys SET last_used_at = ? WHERE id = ?",
                     (_utcnow().isoformat(), row["id"]))
        conn.commit()
        conn.close()
        request.user = {"id": row["user_id"], "email": row["email"], "name": row["name"]}
    else:
        # Fall back to JWT auth
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else ""
        if not token:
            return json_response({"success": False, "message": "Missing authentication"}, 401)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT s.user_id, u.email, u.name, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return json_response({"success": False, "message": "Unauthorized"}, 401)
        if datetime.fromisoformat(row["expires_at"]).replace(tzinfo=None) < _utcnow():
            return json_response({"success": False, "message": "Session expired"}, 401)
        request.user = {"id": row["user_id"], "email": row["email"], "name": row["name"]}

    payload = request.json or {}
    user_id = request.user["id"]

    account_id = str(payload.get("account_id", "") or "").strip()
    if not account_id:
        return json_response({"success": False, "message": "account_id is required"}, 400)

    snapshot = {
        "account_id": account_id,
        "connected": bool(payload.get("connected", True)),
        "strategy": str(payload.get("strategy", "mt5_ea")),
        "balance": float(payload.get("balance", 0) or 0),
        "equity": float(payload.get("equity", 0) or 0),
        "profit": float(payload.get("profit", 0) or 0),
        "open_positions": int(payload.get("open_positions", 0) or 0),
        "positions": payload.get("positions", []) if isinstance(payload.get("positions", []), list) else [],
        "timestamp": _utcnow().isoformat(),
        "source": "mt5_bridge_push",
    }
    save_mt5_runtime_snapshot(user_id, snapshot)
    return json_response({"success": True, "saved": True, "timestamp": snapshot["timestamp"]})


# ═══════════════════════════════════════════════════════════════════════════
# REMOTE BOT PUSH ENDPOINTS — bot authenticates via X-Bot-Key header
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/bot/push/heartbeat", methods=["POST"])
@require_bot_key
def bot_push_heartbeat():
    """Receive full runtime status heartbeat from a remote bot."""
    payload = request.json or {}
    user_id = request.user["id"]
    now_iso = _utcnow().isoformat()
    payload["received_at"] = now_iso

    # Store in memory cache (fast reads for dashboard polling)
    with _bot_heartbeat_lock:
        _bot_heartbeat_cache[user_id] = payload

    # Persist latest heartbeat to DB (one row per user, upsert)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM bot_heartbeats WHERE user_id = ?", (user_id,))
    existing = cur.fetchone()
    blob = json.dumps(payload, default=str)
    if existing:
        cur.execute("UPDATE bot_heartbeats SET payload = ?, created_at = ? WHERE user_id = ?",
                     (blob, now_iso, user_id))
    else:
        cur.execute("INSERT INTO bot_heartbeats (user_id, payload, created_at) VALUES (?, ?, ?)",
                     (user_id, blob, now_iso))
    conn.commit()
    conn.close()

    # Also write to local runtime_status.json so existing endpoints still work
    # when web + bot happen to be on same machine
    try:
        RUNTIME_STATUS_FILE = BOT_RUNTIME_FILE
        RUNTIME_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_STATUS_FILE.write_text(json.dumps(payload, indent=2, default=str))
    except Exception:
        pass

    return json_response({"success": True, "timestamp": now_iso})


@app.route("/api/bot/push/trade", methods=["POST"])
@require_bot_key
def bot_push_trade():
    """Receive a trade open or close event from a remote bot."""
    payload = request.json or {}
    user_id = request.user["id"]
    now_iso = _utcnow().isoformat()

    action = str(payload.get("action", "open")).lower()  # "open" or "close"

    if action == "close":
        # Update existing trade row
        ticket = int(payload.get("ticket", 0) or 0)
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE bot_trades SET close_price = ?, profit = ?, close_time = ?, exit_reason = ? "
            "WHERE user_id = ? AND ticket = ? AND close_price IS NULL",
            (
                float(payload.get("close_price", 0) or 0),
                float(payload.get("profit", 0) or 0),
                str(payload.get("close_time", now_iso)),
                str(payload.get("exit_reason", "")),
                user_id,
                ticket,
            )
        )
        conn.commit()
        conn.close()
        return json_response({"success": True, "action": "close", "ticket": ticket})

    # action == "open" (default)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bot_trades (user_id, ticket, symbol, trade_type, open_price, sl, tp, "
        "lot_size, risk_percent, open_time, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            int(payload.get("ticket", 0) or 0),
            str(payload.get("symbol", "")),
            str(payload.get("trade_type", "")),
            float(payload.get("open_price", 0) or 0),
            float(payload.get("sl", 0) or 0),
            float(payload.get("tp", 0) or 0),
            float(payload.get("lot_size", 0) or 0),
            float(payload.get("risk_percent", 0) or 0),
            str(payload.get("open_time", now_iso)),
            now_iso,
        )
    )
    conn.commit()
    conn.close()
    return json_response({"success": True, "action": "open", "ticket": int(payload.get("ticket", 0) or 0)})


@app.route("/api/bot/push/logs", methods=["POST"])
@require_bot_key
def bot_push_logs():
    """Receive log lines from a remote bot."""
    payload = request.json or {}
    user_id = request.user["id"]
    now_iso = _utcnow().isoformat()

    lines = payload.get("lines", [])
    if isinstance(lines, str):
        lines = [lines]

    if not isinstance(lines, list) or not lines:
        return json_response({"success": False, "message": "lines (array) required"}, 400)

    conn = get_db()
    cur = conn.cursor()
    for line_obj in lines[-200:]:  # cap at 200 per push
        if isinstance(line_obj, dict):
            msg = str(line_obj.get("message", ""))
            lvl = str(line_obj.get("level", "INFO"))
        else:
            msg = str(line_obj)
            lvl = "INFO"
        if msg.strip():
            cur.execute(
                "INSERT INTO bot_logs (user_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (user_id, lvl, msg.strip(), now_iso)
            )
    conn.commit()

    # Trim old logs (keep last 2000 per user)
    cur.execute(
        "DELETE FROM bot_logs WHERE user_id = ? AND id NOT IN "
        "(SELECT id FROM bot_logs WHERE user_id = ? ORDER BY id DESC LIMIT 2000)",
        (user_id, user_id)
    )
    conn.commit()
    conn.close()
    return json_response({"success": True, "stored": min(len(lines), 200)})


# ═══════════════════════════════════════════════════════════════════════════
# BOT API KEY MANAGEMENT — user-facing (require_auth)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/bot/api-key", methods=["GET"])
@require_auth
def get_bot_api_key():
    """Get the current bot API key info (masked)."""
    user_id = request.user["id"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT api_key, name, created_at, last_used_at FROM bot_api_keys WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return json_response({"has_key": False})
    return json_response({
        "has_key": True,
        "key_masked": mask_token(row["api_key"]),
        "name": row["name"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
    })


@app.route("/api/bot/api-key/generate", methods=["POST"])
@require_auth
def generate_bot_api_key():
    """Generate (or regenerate) a bot API key for the current user."""
    user_id = request.user["id"]
    new_key = f"zbot_{secrets.token_hex(24)}"
    now_iso = _utcnow().isoformat()

    conn = get_db()
    cur = conn.cursor()
    # Remove old key if exists — one key per user
    cur.execute("DELETE FROM bot_api_keys WHERE user_id = ?", (user_id,))
    cur.execute(
        "INSERT INTO bot_api_keys (user_id, api_key, name, created_at) VALUES (?, ?, ?, ?)",
        (user_id, new_key, "default", now_iso)
    )
    conn.commit()
    conn.close()

    return json_response({
        "success": True,
        "api_key": new_key,
        "message": "New API key generated. Copy it now — it won't be shown again in full.",
    })


@app.route("/api/bot/api-key", methods=["DELETE"])
@require_auth
def revoke_bot_api_key():
    """Revoke the current bot API key."""
    user_id = request.user["id"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM bot_api_keys WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return json_response({"success": True, "message": "Bot API key revoked"})


@app.route("/api/bot/logs", methods=["GET"])
@require_auth
def get_bot_logs():
    lines = request.args.get("lines", 80, type=int)
    lines = max(10, min(lines, 400))
    return json_response({"logs": tail_bot_log(lines, user_id=request.user["id"]), "file": str(BOT_LOG_FILE)})


@app.route("/api/bot/config", methods=["GET"])
@require_auth
def get_bot_config():
    cfg = read_bot_config()
    return json_response(cfg)


@app.route("/api/telegram/config", methods=["GET"])
@require_auth
def get_telegram_config():
    cfg = read_telegram_config()
    bot_token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    configured = bool(bot_token and chat_id and bot_token != "YOUR_BOT_TOKEN_HERE" and chat_id != "YOUR_CHAT_ID_HERE")
    return json_response({
        "configured": configured,
        "has_bot_token": bool(bot_token),
        "has_chat_id": bool(chat_id),
        "bot_token_masked": mask_token(bot_token),
        "chat_id": chat_id,
    })


@app.route("/api/telegram/config", methods=["POST"])
@require_auth
def update_telegram_config():
    payload = request.json or {}
    existing = read_telegram_config()

    bot_token = str(payload.get("bot_token", existing.get("bot_token", "")) or "").strip()
    chat_id = str(payload.get("chat_id", existing.get("chat_id", "")) or "").strip()

    if not bot_token or not chat_id:
        return json_response({"success": False, "message": "bot_token and chat_id are required"}, 400)

    write_telegram_config({
        "bot_token": bot_token,
        "chat_id": chat_id,
    })

    return json_response({
        "success": True,
        "configured": True,
        "has_bot_token": True,
        "has_chat_id": True,
        "bot_token_masked": mask_token(bot_token),
        "chat_id": chat_id,
        "message": "Telegram settings saved. Restart bot to apply changes.",
    })


@app.route("/api/bot/config", methods=["POST"])
@require_auth
def update_bot_config():
    payload = request.json or {}
    cfg = read_bot_config()

    if "risk_percent" in payload:
        try:
            risk = float(payload.get("risk_percent", cfg["risk_percent"]))
            cfg["risk_percent"] = round(max(MIN_BOT_RISK_PERCENT, min(MAX_BOT_RISK_PERCENT, risk)), 2)
        except Exception:
            return json_response({"success": False, "message": "Invalid risk_percent"}, 400)

    if "enabled_symbols" in payload:
        symbols = payload.get("enabled_symbols", [])
        if not isinstance(symbols, list):
            return json_response({"success": False, "message": "enabled_symbols must be a list"}, 400)
        enabled = sorted([s for s in symbols if s in SYMBOLS])
        cfg["enabled_symbols"] = enabled

    if "max_daily_drawdown_pct" in payload:
        try:
            value = float(payload.get("max_daily_drawdown_pct", cfg["max_daily_drawdown_pct"]))
            cfg["max_daily_drawdown_pct"] = round(max(0.5, min(25.0, value)), 2)
        except Exception:
            return json_response({"success": False, "message": "Invalid max_daily_drawdown_pct"}, 400)

    if "max_margin_usage_pct" in payload:
        try:
            value = float(payload.get("max_margin_usage_pct", cfg["max_margin_usage_pct"]))
            cfg["max_margin_usage_pct"] = round(max(5.0, min(95.0, value)), 2)
        except Exception:
            return json_response({"success": False, "message": "Invalid max_margin_usage_pct"}, 400)

    if "daily_drawdown_adjustment_usd" in payload:
        try:
            value = float(payload.get("daily_drawdown_adjustment_usd", cfg["daily_drawdown_adjustment_usd"]))
            cfg["daily_drawdown_adjustment_usd"] = round(value, 2)
        except Exception:
            return json_response({"success": False, "message": "Invalid daily_drawdown_adjustment_usd"}, 400)

    write_bot_config({
        "risk_percent": cfg["risk_percent"],
        "max_daily_drawdown_pct": cfg["max_daily_drawdown_pct"],
        "max_margin_usage_pct": cfg["max_margin_usage_pct"],
        "daily_drawdown_adjustment_usd": cfg["daily_drawdown_adjustment_usd"],
        "enabled_symbols": cfg["enabled_symbols"],
    })

    return json_response({"success": True, **cfg})


def estimate_daily_pnl_from_trades() -> float:
    if not TRADES_FILE.exists():
        return 0.0
    try:
        df = pd.read_csv(TRADES_FILE)
    except Exception:
        return 0.0

    if df.empty:
        return 0.0

    ts_col = None
    for candidate in ("timestamp", "exit_time", "time"):
        if candidate in df.columns:
            ts_col = candidate
            break
    if ts_col is None:
        return 0.0

    pnl_col = None
    for candidate in ("pnl", "profit", "PnL"):
        if candidate in df.columns:
            pnl_col = candidate
            break
    if pnl_col is None:
        return 0.0

    try:
        df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce", utc=True)
        today = datetime.now(timezone.utc).date()
        day_df = df[df[ts_col].dt.date == today]
        return float(day_df[pnl_col].fillna(0).astype(float).sum()) if not day_df.empty else 0.0
    except Exception:
        return 0.0


@app.route("/api/bot/config/reset-drawdown", methods=["POST"])
@require_auth
def reset_daily_drawdown_adjustment():
    cfg = read_bot_config()
    daily_pnl = estimate_daily_pnl_from_trades()
    cfg["daily_drawdown_adjustment_usd"] = round(-daily_pnl, 2)

    write_bot_config({
        "risk_percent": cfg["risk_percent"],
        "max_daily_drawdown_pct": cfg["max_daily_drawdown_pct"],
        "max_margin_usage_pct": cfg["max_margin_usage_pct"],
        "daily_drawdown_adjustment_usd": cfg["daily_drawdown_adjustment_usd"],
        "enabled_symbols": cfg["enabled_symbols"],
    })

    return json_response({
        "success": True,
        **cfg,
        "daily_pnl_estimate": round(daily_pnl, 2),
        "message": "Daily drawdown adjustment reset for current day.",
    })


@app.route("/api/bot/activity", methods=["GET"])
@require_auth
def get_bot_activity():
    lines = request.args.get("lines", 160, type=int)
    lines = max(20, min(lines, 600))
    events = parse_bot_activity(lines, user_id=request.user["id"])

    summary = {
        "signals": sum(1 for e in events if e["type"] == "signal"),
        "opened": sum(1 for e in events if e["type"] == "opened"),
        "failed": sum(1 for e in events if e["type"] == "failed"),
        "closed": sum(1 for e in events if e["type"] == "closed"),
        "scans": sum(1 for e in events if e["type"] == "scan"),
    }

    return json_response({
        "events": events,
        "summary": summary,
        "runtime": load_bot_runtime_state(request.user["id"]),
    })


@app.route("/api/bot/positions", methods=["GET"])
@require_auth
def get_bot_positions():
    # 1) Read from bot heartbeat (runtime_status.json) — no MT5 conflict
    runtime = load_bot_runtime_state(request.user["id"])
    runtime_pos_count = int(runtime.get("open_positions", 0) or 0)
    heartbeat_positions = runtime.get("position_details")
    if isinstance(heartbeat_positions, list) and heartbeat_positions:
        return json_response({
            "positions": heartbeat_positions,
            "open_positions_count": len(heartbeat_positions),
            "source": "bot_heartbeat"
        })

    # 2) Fallback: MT5 bridge push
    bridge = load_mt5_runtime_snapshot(request.user["id"])
    if bridge and isinstance(bridge.get("positions"), list):
        bridge_positions = bridge.get("positions", []) or []
        bridge_pos_count = int(bridge.get("open_positions", 0) or 0)
        return json_response({
            "positions": bridge_positions,
            "open_positions_count": len(bridge_positions) if bridge_positions else bridge_pos_count,
            "source": "mt5_bridge_push"
        })

    # 3) Fallback: open trades from SQLite
    if TRADES_DB_FILE.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(TRADES_DB_FILE))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ticket, symbol, type as direction, open_price, lot_size as volume, "
                "sl, tp, open_time FROM trades WHERE close_price IS NULL ORDER BY id DESC"
            ).fetchall()
            conn.close()
            if rows:
                positions = []
                for r in rows:
                    positions.append({
                        "ticket": r["ticket"] or 0,
                        "symbol": r["symbol"] or "?",
                        "direction": r["direction"] or "?",
                        "volume": float(r["volume"] or 0),
                        "open_price": float(r["open_price"] or 0),
                        "current_price": 0.0,
                        "sl": float(r["sl"] or 0),
                        "tp": float(r["tp"] or 0),
                        "profit": 0.0,
                        "open_time": r["open_time"] or "",
                    })
                return json_response({
                    "positions": positions,
                    "open_positions_count": len(positions),
                    "source": "sqlite_open_trades"
                })
        except Exception:
            pass

    # 3b) Fallback: open trades from remote-pushed bot_trades table
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT ticket, symbol, trade_type as direction, open_price, lot_size as volume, "
            "sl, tp, open_time FROM bot_trades WHERE user_id = ? AND close_price IS NULL "
            "ORDER BY id DESC",
            (request.user["id"],)
        )
        rows = cur.fetchall()
        conn.close()
        if rows:
            positions = []
            for r in rows:
                positions.append({
                    "ticket": r["ticket"] or 0,
                    "symbol": r["symbol"] or "?",
                    "direction": r["direction"] or "?",
                    "volume": float(r["volume"] or 0),
                    "open_price": float(r["open_price"] or 0),
                    "current_price": 0.0,
                    "sl": float(r["sl"] or 0),
                    "tp": float(r["tp"] or 0),
                    "profit": 0.0,
                    "open_time": r["open_time"] or "",
                })
            return json_response({
                "positions": positions,
                "open_positions_count": len(positions),
                "source": "remote_push_trades"
            })
    except Exception:
        pass

    # 4) Check heartbeat count — bot reports positions but details not yet available
    if runtime_pos_count > 0:
        return json_response({
            "positions": [],
            "open_positions_count": runtime_pos_count,
            "message": f"Bot reports {runtime_pos_count} open position(s) — details loading next scan cycle"
        })

    return json_response({
        "positions": [],
        "open_positions_count": 0,
        "message": "No open positions"
    })


@app.route("/api/bot/insights", methods=["GET"])
@require_auth
def get_bot_insights():
    learned = {}
    if BOT_LEARNED_PARAMS_FILE.exists():
        try:
            loaded = json.loads(BOT_LEARNED_PARAMS_FILE.read_text())
            if isinstance(loaded, dict):
                learned = loaded
        except Exception:
            pass

    return json_response({
        "learned_params": learned,
        "learned_symbols": sorted(list(learned.keys())),
        "runtime": load_bot_runtime_state(request.user["id"]),
    })


@app.route("/api/metrics", methods=["GET"])
@require_auth
def get_metrics():
    closed_df = load_trades(user_id=request.user["id"], include_open=False)
    all_df = load_trades(user_id=request.user["id"], include_open=True)
    metrics = calculate_metrics(closed_df)
    runtime = load_bot_runtime_state(request.user["id"])

    # Read live account data from bot heartbeat (runtime_status.json)
    live = None
    rt_balance = runtime.get("balance")
    if rt_balance and float(rt_balance) > 0:
        live = {
            "balance": float(rt_balance),
            "equity": float(runtime.get("equity", 0) or 0),
            "profit": float(runtime.get("floating_pnl", 0) or 0),
            "open_positions": int(runtime.get("open_positions", 0) or 0),
            "source": "bot_heartbeat",
        }

    # Fallback: try MT5 direct if no heartbeat data
    if not live:
        live = get_live_account_snapshot()

    starting_balance = 10000
    current_balance = live["balance"] if live else (starting_balance + metrics["total_pnl"])
    live_profit = live["profit"] if live else metrics["total_pnl"]
    pnl_pct = (live_profit / current_balance * 100) if current_balance else 0
    max_dd_pct = (metrics["max_drawdown"] / starting_balance * 100) if starting_balance > 0 else 0

    return json_response({
        "balance": round(current_balance, 2),
        "equity": round(live["equity"], 2) if live else None,
        "open_positions": int(live["open_positions"]) if live else 0,
        "pnl": round(live_profit, 2),
        "realized_pnl": round(metrics["total_pnl"], 2),
        "pnl_percentage": round(pnl_pct, 2),
        "win_rate": metrics["win_rate"],
        # Show total trades as all trades (open + closed), but keep winrate/PF from closed trades only.
        "total_trades": int(len(all_df)) if all_df is not None else metrics["total_trades"],
        "closed_trades": metrics["total_trades"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "profit_factor": metrics["profit_factor"],
        "max_drawdown": metrics["max_drawdown"],
        "max_drawdown_pct": round(max_dd_pct, 2),
        "total_return": metrics["total_return"],
        "average_win": metrics["average_win"],
        "average_loss": metrics["average_loss"],
        "live_account": live,
        "timestamp": _utcnow().isoformat()
    })


@app.route("/api/equity", methods=["GET"])
@require_auth
def get_equity():
    trades_df = load_trades(user_id=request.user["id"])

    if trades_df.empty:
        # ── Fallback: serve best stored backtest equity curve ──
        best_file = None
        best_trades = 0
        if BACKTEST_RESULTS_DIR.exists():
            for f in BACKTEST_RESULTS_DIR.glob("*.json"):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    tc = len(d.get("trades", []))
                    if tc > best_trades and d.get("equity_curve"):
                        best_trades = tc
                        best_file = f
                except Exception:
                    continue
        if best_file:
            d = json.loads(best_file.read_text(encoding="utf-8"))
            eq = d["equity_curve"]
            trades_list = d.get("trades", [])
            labels = []
            for t in trades_list:
                et = t.get("exit_time", t.get("entry_time", ""))
                labels.append(et if et else "")
            if len(eq) > len(labels):
                labels = ["Start"] + labels
            return json_response({
                "labels": labels,
                "data": [float(v) for v in eq],
                "source": "backtest",
                "symbol": d.get("symbol", ""),
            })
        return json_response({"labels": [], "data": []})

    timestamp_col = None
    if "timestamp" in trades_df.columns:
        timestamp_col = "timestamp"
    elif "exit_time" in trades_df.columns:
        timestamp_col = "exit_time"
    elif "time" in trades_df.columns:
        timestamp_col = "time"

    pnl_col = None
    if "pnl" in trades_df.columns:
        pnl_col = "pnl"
    elif "profit" in trades_df.columns:
        pnl_col = "profit"
    elif "PnL" in trades_df.columns:
        pnl_col = "PnL"

    if pnl_col is None:
        return json_response({"labels": [], "data": []})

    if timestamp_col:
        trades_df[timestamp_col] = pd.to_datetime(trades_df[timestamp_col], errors="coerce")
        trades_df = trades_df.sort_values(timestamp_col)

    starting_balance = 10000
    trades_df["equity"] = starting_balance + trades_df[pnl_col].cumsum()
    labels = (
        trades_df[timestamp_col].dt.strftime("%Y-%m-%d %H:%M").fillna("").tolist()
        if timestamp_col else
        [str(i) for i in range(len(trades_df))]
    )
    data = [float(v) for v in trades_df["equity"].tolist()]

    return json_response({"labels": labels, "data": data})


@app.route("/api/trades", methods=["GET"])
@require_auth
def get_trades():
    limit = request.args.get("limit", 50, type=int)
    trades_df = load_trades(user_id=request.user["id"], include_open=True)

    if trades_df.empty:
        return json_response([])

    timestamp_col = None
    if "timestamp" in trades_df.columns:
        timestamp_col = "timestamp"
    elif "exit_time" in trades_df.columns:
        timestamp_col = "exit_time"
    elif "time" in trades_df.columns:
        timestamp_col = "time"

    if timestamp_col:
        trades_df = trades_df.sort_values(timestamp_col, ascending=False)

    trades_df = trades_df.head(limit)

    trades = []
    for _, row in trades_df.iterrows():
        exit_price = row.get("exit_price", row.get("Exit_Price", None))
        pnl = row.get("pnl", row.get("profit", row.get("PnL", None)))
        status = "open" if (exit_price is None or (isinstance(exit_price, float) and np.isnan(exit_price))) else "closed"
        trade = {
            "symbol": str(row.get("symbol", row.get("Symbol", "N/A"))),
            "type": str(row.get("type", row.get("Type", row.get("direction", "N/A")))),
            "entry_price": float(row.get("entry_price", row.get("Entry_Price", 0))),
            "exit_price": None if status == "open" else float(exit_price or 0),
            "quantity": float(row.get("quantity", row.get("Quantity", 0))),
            "pnl": None if status == "open" else float(pnl or 0),
            "timestamp": str(row.get("timestamp", row.get("exit_time", row.get("time", "N/A")))),
            "status": status,
        }
        trades.append(trade)

    return json_response(trades)


@app.route("/api/health", methods=["GET"])
def health_check():
    return json_response({"status": "healthy", "timestamp": _utcnow().isoformat()})


if __name__ == "__main__":
    init_db()
    ensure_telegram_remote_loop()
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

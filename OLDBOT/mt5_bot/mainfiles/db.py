# SQLite database layer
# Documentation: Vnútorná SQLite databáza s WAL režimom
# Tables: trades, order_blocks, logs

import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone

DB_DIR = Path(__file__).parent.parent / "liverun"
DB_PATH = DB_DIR / "trading.db"

_local = threading.local()


def _utcnow() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_db() -> sqlite3.Connection:
    """Get a thread-local database connection with WAL mode enabled."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL mode — concurrent reads, single writer, better crash resilience
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init_trading_db():
    """Create tables if they do not exist yet.

    Table 1 — trades
        ticket, symbol, type (BUY/SELL), open_price, close_price, sl, tp,
        profit, open_time, close_time, lot_size, risk_percent, exit_reason

    Table 2 — order_blocks
        symbol, price_high, price_low, direction (BULL/BEAR),
        is_mitigated flag, created_at, mitigated_at

    Table 3 — logs
        timestamp, level (INFO/WARN/ERROR), message
    """
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket        INTEGER,
            symbol        TEXT NOT NULL,
            strategy_name TEXT NOT NULL DEFAULT 'smart_money_v1',
            type          TEXT NOT NULL,
            open_price    REAL,
            close_price   REAL,
            sl            REAL,
            tp            REAL,
            profit        REAL,
            open_time     TEXT,
            close_time    TEXT,
            lot_size      REAL,
            risk_percent  REAL,
            exit_reason   TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Lightweight migration for existing databases.
    columns = [row[1] for row in cur.execute("PRAGMA table_info(trades)").fetchall()]
    if 'strategy_name' not in columns:
        cur.execute("ALTER TABLE trades ADD COLUMN strategy_name TEXT NOT NULL DEFAULT 'smart_money_v1'")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy_close_time ON trades(strategy_name, close_time)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_blocks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT NOT NULL,
            price_high    REAL NOT NULL,
            price_low     REAL NOT NULL,
            direction     TEXT NOT NULL,
            is_mitigated  INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            mitigated_at  TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            level      TEXT NOT NULL DEFAULT 'INFO',
            message    TEXT NOT NULL
        )
    """)

    conn.commit()


# ---------------------------------------------------------------------------
# Trade helpers
# ---------------------------------------------------------------------------

def insert_trade(*, ticket: int = 0, symbol: str, trade_type: str,
                 strategy_name: str = 'smart_money_v1',
                 open_price: float, sl: float, tp: float,
                 lot_size: float = 0.0, risk_percent: float = 0.0,
                 open_time: str | None = None) -> int:
    """Insert a new trade row (position opened). Returns row id."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO trades
              (ticket, symbol, strategy_name, type, open_price, sl, tp, lot_size, risk_percent, open_time, created_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
          (ticket, symbol, strategy_name, trade_type, open_price, sl, tp,
         lot_size, risk_percent, open_time or _utcnow(), _utcnow()),
    )
    conn.commit()
    return cur.lastrowid


def close_trade(ticket: int, *, close_price: float, profit: float,
                exit_reason: str = "", close_time: str | None = None):
    """Update a trade row when the position is closed."""
    conn = get_db()
    conn.execute(
        """UPDATE trades SET close_price=?, profit=?, exit_reason=?, close_time=?
           WHERE ticket=? AND close_price IS NULL""",
        (close_price, profit, exit_reason, close_time or _utcnow(), ticket),
    )
    conn.commit()


def get_trades(symbol: str | None = None, limit: int = 100) -> list[dict]:
    """Return recent closed trades, optionally filtered by symbol."""
    conn = get_db()
    if symbol:
        rows = conn.execute(
            "SELECT * FROM trades WHERE symbol=? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_pnl() -> float:
    """Sum profit of all trades closed today (UTC)."""
    conn = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COALESCE(SUM(profit), 0) as pnl FROM trades WHERE close_time LIKE ?",
        (f"{today}%",),
    ).fetchone()
    return float(row["pnl"]) if row else 0.0


# ---------------------------------------------------------------------------
# Order Block helpers
# ---------------------------------------------------------------------------

def insert_order_block(*, symbol: str, price_high: float, price_low: float,
                       direction: str) -> int:
    """Store a detected Order Block zone."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO order_blocks (symbol, price_high, price_low, direction, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (symbol, price_high, price_low, direction, _utcnow()),
    )
    conn.commit()
    return cur.lastrowid


def mitigate_order_block(ob_id: int):
    """Mark an Order Block as mitigated (touched / used)."""
    conn = get_db()
    conn.execute(
        "UPDATE order_blocks SET is_mitigated=1, mitigated_at=? WHERE id=?",
        (_utcnow(), ob_id),
    )
    conn.commit()


def get_active_order_blocks(symbol: str) -> list[dict]:
    """Return non-mitigated Order Blocks for a symbol."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM order_blocks WHERE symbol=? AND is_mitigated=0 ORDER BY id DESC",
        (symbol,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# System log helpers
# ---------------------------------------------------------------------------

def log_event(message: str, level: str = "INFO"):
    """Write a system event to the logs table."""
    conn = get_db()
    conn.execute(
        "INSERT INTO logs (timestamp, level, message) VALUES (?, ?, ?)",
        (_utcnow(), level, message),
    )
    conn.commit()


def get_logs(limit: int = 200, level: str | None = None) -> list[dict]:
    """Return recent logs, optionally filtered by level."""
    conn = get_db()
    if level:
        rows = conn.execute(
            "SELECT * FROM logs WHERE level=? ORDER BY id DESC LIMIT ?",
            (level, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

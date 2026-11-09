"""JSON state for carts/products/locations + SQLite index for orders and tickets.

Orders and support tickets live in orders.sqlite (indexed by id, email, phone,
conversation). Cart/product/location state stays in state.json. Writes are
atomic; order lookups never scan the JSON file.
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_PLUGIN_NAME = "shopmate"
_PATH = None
_DB_PATH = None


def _data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore
        d = Path(plugin_data_dir(_PLUGIN_NAME))
    except ImportError:
        d = Path(os.environ.get("SHOPMATE_TEST_DATA") or tempfile.gettempdir()) / _PLUGIN_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    global _PATH
    if _PATH is None:
        _PATH = _data_dir() / "state.json"
    return _PATH


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = _data_dir() / "orders.sqlite"
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            payment_status TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email);
        CREATE INDEX IF NOT EXISTS idx_orders_phone ON orders(phone);
        CREATE INDEX IF NOT EXISTS idx_orders_conv ON orders(conversation_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL DEFAULT '',
            conversation_id TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_order ON tickets(order_id);
        CREATE INDEX IF NOT EXISTS idx_tickets_conv ON tickets(conversation_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tickets_email ON tickets(email);
        CREATE TABLE IF NOT EXISTS cards (
            email TEXT NOT NULL,
            signature TEXT NOT NULL,
            authorization_code TEXT NOT NULL,
            last4 TEXT,
            brand TEXT,
            bank TEXT,
            bin TEXT,
            exp_month TEXT,
            exp_year TEXT,
            card_type TEXT,
            reusable INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (email, signature)
        );
        CREATE INDEX IF NOT EXISTS idx_cards_email ON cards(email);
        CREATE TABLE IF NOT EXISTS transfers (
            reference TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'NGN',
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_transfers_order ON transfers(order_id);
        """
    )
    return conn


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _norm_phone(s: str) -> str:
    digits = "".join(c for c in (s or "") if c.isdigit())
    # keep last 10 digits for matching local/intl variants
    return digits[-10:] if len(digits) >= 10 else digits


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"carts": {}, "orders": {}, "products": {}, "locations": {}}
    try:
        data = json.loads(p.read_text("utf-8"))
    except Exception:
        data = {}
    for k in ("carts", "orders", "products", "locations"):
        data.setdefault(k, {})
    return data


def _save(data: dict) -> None:
    p = _path()
    # Orders live in sqlite; keep JSON free of the heavy orders map.
    out = {k: v for k, v in data.items() if k != "orders"}
    out.setdefault("carts", {})
    out.setdefault("products", {})
    out.setdefault("locations", {})
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".state-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, p)


def _migrate_orders_from_json(data: dict) -> None:
    orders = data.get("orders") or {}
    if not orders:
        return
    conn = _connect()
    try:
        for oid, order in orders.items():
            cust = order.get("customer") or {}
            conn.execute(
                "INSERT OR REPLACE INTO orders(order_id,conversation_id,email,phone,created_at,"
                "payment_status,delivery_status,payload) VALUES(?,?,?,?,?,?,?,?)",
                (
                    oid,
                    str(order.get("conversation_id") or ""),
                    _norm_email(cust.get("email") or order.get("email") or ""),
                    _norm_phone(cust.get("phone") or order.get("phone") or ""),
                    int(order.get("created_at") or time.time()),
                    order.get("payment_status") or "pending",
                    order.get("delivery_status") or "awaiting_payment",
                    json.dumps(order, ensure_ascii=False),
                ),
            )
        data["orders"] = {}
    finally:
        conn.close()


def transact(fn):
    """Run fn(data) under the lock, persist carts/products/locations, return result."""
    with _LOCK:
        data = _load()
        _migrate_orders_from_json(data)
        result = fn(data)
        prods = data["products"]
        if len(prods) > 2000:
            for k in list(prods)[: len(prods) - 2000]:
                prods.pop(k, None)
        _save(data)
        return result


def read(fn):
    with _LOCK:
        data = _load()
        _migrate_orders_from_json(data)
        return fn(data)


# ---------- orders (sqlite) ----------

def save_order(order: dict) -> None:
    oid = order["order_id"]
    cust = order.get("customer") or {}
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR REPLACE INTO orders(order_id,conversation_id,email,phone,created_at,"
                "payment_status,delivery_status,payload) VALUES(?,?,?,?,?,?,?,?)",
                (
                    oid,
                    str(order.get("conversation_id") or ""),
                    _norm_email(cust.get("email") or ""),
                    _norm_phone(cust.get("phone") or ""),
                    int(order.get("created_at") or time.time()),
                    order.get("payment_status") or "pending",
                    order.get("delivery_status") or "awaiting_payment",
                    json.dumps(order, ensure_ascii=False),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def update_order(order_id: str, mutator) -> dict | None:
    """Load order, run mutator(order)->order, save. Returns updated order or None."""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            order = json.loads(row["payload"])
            order = mutator(order) or order
            cust = order.get("customer") or {}
            conn.execute(
                "UPDATE orders SET email=?, phone=?, payment_status=?, delivery_status=?, payload=? WHERE order_id=?",
                (
                    _norm_email(cust.get("email") or ""),
                    _norm_phone(cust.get("phone") or ""),
                    order.get("payment_status") or "pending",
                    order.get("delivery_status") or "",
                    json.dumps(order, ensure_ascii=False),
                    order_id,
                ),
            )
            conn.execute("COMMIT")
            return order
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def get_order(order_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT payload FROM orders WHERE order_id=?", (order_id,)).fetchone()
            return json.loads(row["payload"]) if row else None
        finally:
            conn.close()


def find_orders(*, order_id: str = None, email: str = None, phone: str = None,
                conversation_id: str = None, limit: int = 20) -> list:
    """Indexed lookup. Prefer order_id, then email/phone, then conversation."""
    limit = max(1, min(int(limit or 20), 50))
    with _LOCK:
        conn = _connect()
        try:
            if order_id:
                row = conn.execute("SELECT payload FROM orders WHERE order_id=?", (order_id.strip(),)).fetchone()
                return [json.loads(row["payload"])] if row else []
            if email:
                rows = conn.execute(
                    "SELECT payload FROM orders WHERE email=? ORDER BY created_at DESC LIMIT ?",
                    (_norm_email(email), limit),
                ).fetchall()
                return [json.loads(r["payload"]) for r in rows]
            if phone:
                rows = conn.execute(
                    "SELECT payload FROM orders WHERE phone=? ORDER BY created_at DESC LIMIT ?",
                    (_norm_phone(phone), limit),
                ).fetchall()
                return [json.loads(r["payload"]) for r in rows]
            if conversation_id is not None:
                rows = conn.execute(
                    "SELECT payload FROM orders WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                    (str(conversation_id), limit),
                ).fetchall()
                return [json.loads(r["payload"]) for r in rows]
            return []
        finally:
            conn.close()


def order_count() -> int:
    with _LOCK:
        conn = _connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        finally:
            conn.close()


# ---------- support tickets ----------

def save_ticket(ticket: dict) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR REPLACE INTO tickets(ticket_id,order_id,conversation_id,email,phone,status,created_at,payload)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    ticket["ticket_id"],
                    ticket.get("order_id") or "",
                    str(ticket.get("conversation_id") or ""),
                    _norm_email(ticket.get("email") or ""),
                    _norm_phone(ticket.get("phone") or ""),
                    ticket.get("status") or "open",
                    int(ticket.get("created_at") or time.time()),
                    json.dumps(ticket, ensure_ascii=False),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def get_ticket(ticket_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT payload FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            return json.loads(row["payload"]) if row else None
        finally:
            conn.close()


def find_tickets(*, ticket_id: str = None, order_id: str = None, conversation_id: str = None,
                 email: str = None, limit: int = 20) -> list:
    limit = max(1, min(int(limit or 20), 50))
    with _LOCK:
        conn = _connect()
        try:
            if ticket_id:
                row = conn.execute("SELECT payload FROM tickets WHERE ticket_id=?", (ticket_id.strip(),)).fetchone()
                return [json.loads(row["payload"])] if row else []
            if order_id:
                rows = conn.execute(
                    "SELECT payload FROM tickets WHERE order_id=? ORDER BY created_at DESC LIMIT ?",
                    (order_id, limit),
                ).fetchall()
                return [json.loads(r["payload"]) for r in rows]
            if email:
                rows = conn.execute(
                    "SELECT payload FROM tickets WHERE email=? ORDER BY created_at DESC LIMIT ?",
                    (_norm_email(email), limit),
                ).fetchall()
                return [json.loads(r["payload"]) for r in rows]
            if conversation_id is not None:
                rows = conn.execute(
                    "SELECT payload FROM tickets WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                    (str(conversation_id), limit),
                ).fetchall()
                return [json.loads(r["payload"]) for r in rows]
            return []
        finally:
            conn.close()



# ---------- saved cards (Paystack reusable authorizations) ----------

def save_card(email: str, auth: dict) -> dict | None:
    """Upsert a reusable authorization keyed by email+signature (or auth code)."""
    email_n = _norm_email(email)
    if not email_n or not auth or not auth.get("authorization_code"):
        return None
    if auth.get("reusable") is False:
        return None
    sig = auth.get("signature") or auth.get("authorization_code")
    payload = dict(auth)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR REPLACE INTO cards(email,signature,authorization_code,last4,brand,bank,bin,"
                "exp_month,exp_year,card_type,reusable,created_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    email_n, sig, auth["authorization_code"],
                    auth.get("last4"), auth.get("brand"), auth.get("bank"), auth.get("bin"),
                    auth.get("exp_month"), auth.get("exp_year"), auth.get("card_type"),
                    1 if auth.get("reusable", True) else 0,
                    int(time.time()), json.dumps(payload, ensure_ascii=False),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    return public_card(payload)


def public_card(auth: dict) -> dict:
    """Customer-facing card view — never includes authorization_code."""
    return {
        "card_id": auth.get("signature") or auth.get("authorization_code"),
        "last4": auth.get("last4"),
        "brand": auth.get("brand"),
        "bank": auth.get("bank"),
        "bin": auth.get("bin"),
        "exp_month": auth.get("exp_month"),
        "exp_year": auth.get("exp_year"),
        "card_type": auth.get("card_type"),
        "label": "card ending %s" % (auth.get("last4") or "????"),
    }


def list_cards(email: str) -> list:
    email_n = _norm_email(email)
    if not email_n:
        return []
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT payload FROM cards WHERE email=? AND reusable=1 ORDER BY created_at DESC",
                (email_n,),
            ).fetchall()
            return [public_card(json.loads(r["payload"])) for r in rows]
        finally:
            conn.close()


def get_card(email: str, card_id: str) -> dict | None:
    """Return full auth payload (incl. authorization_code) for charging."""
    email_n = _norm_email(email)
    if not email_n or not card_id:
        return None
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload FROM cards WHERE email=? AND (signature=? OR authorization_code=?)",
                (email_n, card_id, card_id),
            ).fetchone()
            return json.loads(row["payload"]) if row else None
        finally:
            conn.close()


def remove_card(email: str, card_id: str) -> bool:
    email_n = _norm_email(email)
    if not email_n or not card_id:
        return False
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "DELETE FROM cards WHERE email=? AND (signature=? OR authorization_code=?)",
                (email_n, card_id, card_id),
            )
            conn.execute("COMMIT")
            return cur.rowcount > 0
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


# ---------- transfers (idempotent by reference) ----------

def get_transfer(reference: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT payload FROM transfers WHERE reference=?", (reference,)).fetchone()
            return json.loads(row["payload"]) if row else None
        finally:
            conn.close()


def save_transfer(reference: str, order_id: str, amount: float, currency: str, status: str, payload: dict) -> dict:
    """Insert-or-get: if reference exists, return existing (idempotency)."""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM transfers WHERE reference=?", (reference,)).fetchone()
            if row:
                conn.execute("COMMIT")
                return json.loads(row["payload"])
            body = dict(payload or {}, reference=reference, order_id=order_id,
                        amount=amount, currency=currency, status=status,
                        created_at=int(time.time()))
            conn.execute(
                "INSERT INTO transfers(reference,order_id,amount,currency,status,created_at,payload) VALUES(?,?,?,?,?,?,?)",
                (reference, order_id, float(amount), currency, status, body["created_at"],
                 json.dumps(body, ensure_ascii=False)),
            )
            conn.execute("COMMIT")
            return body
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def update_transfer(reference: str, status: str, payload_patch: dict = None) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM transfers WHERE reference=?", (reference,)).fetchone()
            if not row:
                # allow creating from webhook
                body = dict(payload_patch or {}, reference=reference, status=status, created_at=int(time.time()))
                conn.execute(
                    "INSERT INTO transfers(reference,order_id,amount,currency,status,created_at,payload) VALUES(?,?,?,?,?,?,?)",
                    (reference, body.get("order_id") or "", float(body.get("amount") or 0),
                     body.get("currency") or "NGN", status, body["created_at"],
                     json.dumps(body, ensure_ascii=False)),
                )
                conn.execute("COMMIT")
                return body
            body = json.loads(row["payload"])
            body["status"] = status
            if payload_patch:
                body.update(payload_patch)
            conn.execute(
                "UPDATE transfers SET status=?, payload=? WHERE reference=?",
                (status, json.dumps(body, ensure_ascii=False), reference),
            )
            conn.execute("COMMIT")
            return body
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


# test helper: reset cached paths when SHOPMATE_TEST_DATA changes
def _reset_paths_for_tests():
    global _PATH, _DB_PATH
    _PATH = None
    _DB_PATH = None

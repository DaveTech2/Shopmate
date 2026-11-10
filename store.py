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
            card_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL DEFAULT '',
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
            UNIQUE (conversation_id, email, signature)
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
    _migrate_cards_table(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_conv ON cards(conversation_id, email)")
    return conn


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _norm_phone(s: str) -> str:
    digits = "".join(c for c in (s or "") if c.isdigit())
    # keep last 10 digits for matching local/intl variants
    return digits[-10:] if len(digits) >= 10 else digits



def _new_card_id() -> str:
    """Local opaque id. Never the Paystack authorization_code or signature."""
    import secrets
    return "card_" + secrets.token_hex(16)


def _infer_card_conversation(conn: sqlite3.Connection, email: str, signature: str, authorization_code: str) -> str:
    """Best-effort backfill: the conversation of an order that saved this authorization."""
    rows = conn.execute(
        "SELECT conversation_id, payload FROM orders WHERE email=? ORDER BY created_at DESC",
        (email,),
    ).fetchall()
    for r in rows:
        try:
            order = json.loads(r["payload"])
        except Exception:
            continue
        auth = order.get("authorization") or {}
        if signature and auth.get("signature") == signature:
            return str(order.get("conversation_id") or r["conversation_id"] or "")
        if authorization_code and auth.get("authorization_code") == authorization_code:
            return str(order.get("conversation_id") or r["conversation_id"] or "")
    return ""


def _migrate_cards_table(conn: sqlite3.Connection) -> None:
    """Upgrade pre-0.5.1 cards rows: opaque card_id + conversation_id, no auth code as id."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cards)").fetchall()}
    if not cols:
        return
    if "card_id" in cols and "conversation_id" in cols:
        return
    conn.execute("ALTER TABLE cards RENAME TO cards_legacy")
    conn.execute(
        """
        CREATE TABLE cards (
            card_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL DEFAULT '',
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
            UNIQUE (conversation_id, email, signature)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_email ON cards(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_conv ON cards(conversation_id, email)")
    legacy_cols = {r[1] for r in conn.execute("PRAGMA table_info(cards_legacy)").fetchall()}
    rows = conn.execute("SELECT * FROM cards_legacy").fetchall()
    for row in rows:
        payload = {}
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except Exception:
            payload = {}
        email = row["email"]
        signature = row["signature"]
        auth_code = row["authorization_code"]
        cid = ""
        if "conversation_id" in legacy_cols:
            cid = str(row["conversation_id"] or "")
        cid = cid or str(payload.get("conversation_id") or "") or _infer_card_conversation(
            conn, email, signature, auth_code
        )
        card_id = payload.get("card_id") if isinstance(payload.get("card_id"), str) else None
        if not card_id or card_id in (auth_code, signature) or card_id.startswith("AUTH_"):
            card_id = _new_card_id()
        payload = dict(payload)
        payload["card_id"] = card_id
        payload["conversation_id"] = cid
        # Keep authorization_code in the stored payload only — never as card_id.
        conn.execute(
            "INSERT OR REPLACE INTO cards(card_id,conversation_id,email,signature,authorization_code,last4,brand,bank,bin,"
            "exp_month,exp_year,card_type,reusable,created_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                card_id, cid, email, signature, auth_code,
                row["last4"] if "last4" in legacy_cols else payload.get("last4"),
                row["brand"] if "brand" in legacy_cols else payload.get("brand"),
                row["bank"] if "bank" in legacy_cols else payload.get("bank"),
                row["bin"] if "bin" in legacy_cols else payload.get("bin"),
                row["exp_month"] if "exp_month" in legacy_cols else payload.get("exp_month"),
                row["exp_year"] if "exp_year" in legacy_cols else payload.get("exp_year"),
                row["card_type"] if "card_type" in legacy_cols else payload.get("card_type"),
                int(row["reusable"]) if "reusable" in legacy_cols else 1,
                int(row["created_at"]) if "created_at" in legacy_cols else int(time.time()),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    conn.execute("DROP TABLE cards_legacy")


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
                if conversation_id is not None:
                    rows = conn.execute(
                        "SELECT payload FROM orders WHERE email=? AND conversation_id=? "
                        "ORDER BY created_at DESC LIMIT ?",
                        (_norm_email(email), str(conversation_id), limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT payload FROM orders WHERE email=? ORDER BY created_at DESC LIMIT ?",
                        (_norm_email(email), limit),
                    ).fetchall()
                return [json.loads(r["payload"]) for r in rows]
            if phone:
                if conversation_id is not None:
                    rows = conn.execute(
                        "SELECT payload FROM orders WHERE phone=? AND conversation_id=? "
                        "ORDER BY created_at DESC LIMIT ?",
                        (_norm_phone(phone), str(conversation_id), limit),
                    ).fetchall()
                else:
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

def save_card(email: str, auth: dict, conversation_id: str = None) -> dict | None:
    """Upsert a reusable authorization bound to the conversation that paid.

    card_id is a local opaque id. The Paystack authorization_code is stored
    in orders.sqlite but is never returned as card_id.
    """
    email_n = _norm_email(email)
    cid = str(conversation_id or "").strip()
    if not email_n or not cid or not auth or not auth.get("authorization_code"):
        return None
    if auth.get("reusable") is False:
        return None
    sig = auth.get("signature") or auth.get("authorization_code")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT card_id, payload FROM cards WHERE conversation_id=? AND email=? AND signature=?",
                (cid, email_n, sig),
            ).fetchone()
            if existing:
                card_id = existing["card_id"]
                try:
                    prev = json.loads(existing["payload"]) if existing["payload"] else {}
                except Exception:
                    prev = {}
            else:
                card_id = _new_card_id()
                prev = {}
            payload = dict(prev)
            payload.update(auth)
            payload["card_id"] = card_id
            payload["conversation_id"] = cid
            conn.execute(
                "INSERT OR REPLACE INTO cards(card_id,conversation_id,email,signature,authorization_code,last4,brand,bank,bin,"
                "exp_month,exp_year,card_type,reusable,created_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    card_id, cid, email_n, sig, auth["authorization_code"],
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
    """Customer-facing card view — never includes authorization_code or signature."""
    auth = auth or {}
    card_id = auth.get("card_id")
    # Never fall back to the Paystack authorization_code (or signature) as card_id.
    if not isinstance(card_id, str) or not card_id or card_id == auth.get("authorization_code") or card_id == auth.get("signature"):
        card_id = None
    return {
        "card_id": card_id,
        "last4": auth.get("last4"),
        "brand": auth.get("brand"),
        "bank": auth.get("bank"),
        "bin": auth.get("bin"),
        "exp_month": auth.get("exp_month"),
        "exp_year": auth.get("exp_year"),
        "card_type": auth.get("card_type"),
        "label": "card ending %s" % (auth.get("last4") or "????"),
    }


def list_cards(email: str, conversation_id: str = None) -> list:
    """List reusable cards for this email that belong to the calling conversation."""
    email_n = _norm_email(email)
    cid = str(conversation_id or "").strip()
    if not email_n or not cid:
        return []
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT card_id, conversation_id, payload FROM cards "
                "WHERE email=? AND conversation_id=? AND reusable=1 ORDER BY created_at DESC",
                (email_n, cid),
            ).fetchall()
            out = []
            for r in rows:
                payload = json.loads(r["payload"])
                payload["card_id"] = r["card_id"]
                payload["conversation_id"] = r["conversation_id"]
                out.append(public_card(payload))
            return out
        finally:
            conn.close()


def get_card(email: str, card_id: str, conversation_id: str = None) -> dict | None:
    """Return full auth payload (incl. authorization_code) for charging, if it belongs to this conversation.

    Lookup is by the local opaque card_id only — never by authorization_code.
    """
    email_n = _norm_email(email)
    cid = str(conversation_id or "").strip()
    if not email_n or not cid or not card_id:
        return None
    # Refuse lookups that use a Paystack authorization_code as card_id.
    if str(card_id).startswith("AUTH_"):
        return None
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload FROM cards WHERE card_id=? AND email=? AND conversation_id=?",
                (card_id, email_n, cid),
            ).fetchone()
            return json.loads(row["payload"]) if row else None
        finally:
            conn.close()


def remove_card(email: str, card_id: str, conversation_id: str = None) -> bool:
    email_n = _norm_email(email)
    cid = str(conversation_id or "").strip()
    if not email_n or not cid or not card_id:
        return False
    if str(card_id).startswith("AUTH_"):
        return False
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "DELETE FROM cards WHERE card_id=? AND email=? AND conversation_id=?",
                (card_id, email_n, cid),
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

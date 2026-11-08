"""Small JSON store for carts, orders and cached live products.

State lives in the plugin's data directory, never in the plugin tree.
"""

import json
import os
import tempfile
import threading
from pathlib import Path

_LOCK = threading.Lock()
_PLUGIN_NAME = "shopmate"


def _data_dir() -> Path:
    try:  # Hermes' profile-aware data dir; survives plugin updates
        from plugins.plugin_storage import plugin_data_dir  # type: ignore
        d = Path(plugin_data_dir(_PLUGIN_NAME))
    except ImportError:  # outside Hermes (offline tests)
        d = Path(os.environ.get("SHOPMATE_TEST_DATA") or tempfile.gettempdir()) / _PLUGIN_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


_PATH = None


def _path() -> Path:
    global _PATH
    if _PATH is None:
        _PATH = _data_dir() / "state.json"
    return _PATH


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
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".state-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, p)


def transact(fn):
    """Run fn(data) under the lock, persist, and return its result."""
    with _LOCK:
        data = _load()
        result = fn(data)
        # Keep the live-product cache bounded.
        prods = data["products"]
        if len(prods) > 2000:
            for k in list(prods)[: len(prods) - 2000]:
                prods.pop(k, None)
        _save(data)
        return result


def read(fn):
    with _LOCK:
        return fn(_load())

"""
Pytest-Setup: jeder Test-Run laeuft gegen eine frische temp-DB. Wir
ueberschreiben JARNEX_DB_PATH BEVOR router.py importiert wird, damit
ensure_schema() beim Import gegen die Test-DB greift.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Parent-Dir (Modul-Wurzel) auf sys.path setzen, damit `import router` klappt
_MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODULE_ROOT))

# Pro Test-Session eigene temp-DB-Datei
_TMP_DB = Path(tempfile.gettempdir()) / "jarnex_admin_test.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["JARNEX_DB_PATH"] = str(_TMP_DB)


@pytest.fixture(autouse=True)
def _reset_db_between_tests():
    """Vor jedem Test alle Tabellen droppen + neu anlegen.

    Wir loeschen NICHT das File (Windows haelt unter Umstaenden noch Handles),
    sondern reset'en das Schema-Level. So bleiben die Auto-Increment-IDs jung
    und vorhergehende Tests beeinflussen die naechsten nicht.
    """
    import jarnex_database as db  # type: ignore
    conn = db.connect()
    try:
        for tbl in ("events", "capabilities", "credentials", "cameras", "settings"):
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
    finally:
        conn.close()
    db.ensure_schema()
    # Backend-Cache des routers leeren (zwischen Tests sonst sticky)
    try:
        import router  # type: ignore
        router._backend_cache.clear()
    except Exception:  # noqa: BLE001
        pass
    yield

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from shopagent.settings import DATA


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(name):
    from shopagent.core.environment import ensure_directory
    ensure_directory(DATA)
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / f"{name}.db", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def encode(value):
    return json.dumps(value, ensure_ascii=False)

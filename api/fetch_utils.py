from pathlib import Path
import sqlite3 as sql

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "main.db"
EVALS_DB_PATH = BASE_DIR / "data" / "evals.db"
INSTRUCTOR_EVALS_DB_PATH = BASE_DIR / "data" / "instructor_evals.db"
SYLLABI_DIR = BASE_DIR / "data" / "syllabi"


def get_db():
    conn = sql.connect(
        f"file:{DB_PATH}?mode=ro", timeout=15.0, uri=True, check_same_thread=False
    )
    conn.row_factory = sql.Row
    try:
        yield conn
    finally:
        conn.close()


def get_evals_db():
    if not EVALS_DB_PATH.exists():
        conn = sql.connect(":memory:", check_same_thread=False)
        conn.row_factory = sql.Row
        try:
            yield conn
        finally:
            conn.close()
        return

    conn = sql.connect(
        f"file:{EVALS_DB_PATH}?mode=ro", timeout=15.0, uri=True, check_same_thread=False
    )
    conn.row_factory = sql.Row
    try:
        yield conn
    finally:
        conn.close()


def get_instructor_evals_db():
    target_path = INSTRUCTOR_EVALS_DB_PATH if INSTRUCTOR_EVALS_DB_PATH.exists() else EVALS_DB_PATH
    if not target_path.exists():
        conn = sql.connect(":memory:", check_same_thread=False)
        conn.row_factory = sql.Row
        try:
            yield conn
        finally:
            conn.close()
        return

    conn = sql.connect(
        f"file:{target_path}?mode=ro", timeout=15.0, uri=True, check_same_thread=False
    )
    conn.row_factory = sql.Row
    try:
        yield conn
    finally:
        conn.close()

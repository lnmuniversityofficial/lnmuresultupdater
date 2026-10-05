# -*- coding: utf-8 -*-
"""
SQLite Ultra-Fast Lookup — final.db (rollno → dob & student metadata)
"""

import os
import sqlite3
import threading
import logging
from config import Config

logger = logging.getLogger('local_db')
_conn_local = threading.local()


def get_conn():
    """Get thread-local SQLite connection with WAL mode and memory cache"""
    if not hasattr(_conn_local, "conn") or _conn_local.conn is None:
        db_path = Config.LOCAL_DB_PATH
        if not os.path.isabs(db_path):
            db_path = os.path.normpath(os.path.join(Config.BASE_DIR, db_path))
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = 10000")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 30000000")
        _conn_local.conn = conn
    return _conn_local.conn


def init_indexes():
    """Initialize database indexes on students table"""
    conn = get_conn()
    if not conn:
        logger.warning(f"[DB] {Config.LOCAL_DB_PATH} not found")
        return False

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='students'")
        if not cur.fetchone():
            logger.warning("[DB] 'students' table not found in SQLite DB")
            return False

        cur.execute("PRAGMA table_info(students)")
        cols = [row[1].lower() for row in cur.fetchall()]

        if 'rollno' in cols:
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rollno ON students(rollno)")
                conn.commit()
                logger.info("[DB] Index (rollno) ready")
            except Exception as e:
                logger.warning(f"[DB] Index warning: {e}")

        cur.execute("SELECT COUNT(*) FROM students")
        count = cur.fetchone()[0]
        logger.info(f"[DB] Total students indexed: {count}")
        return True
    except Exception as e:
        logger.error(f"[DB ERROR] {e}")
        return False


def get_dob_by_rollno(rollno):
    """
    Ultra-fast: rollno → dob
    Returns: dict {found: bool, dob: str, name: str, ...}
    """
    conn = get_conn()
    if not conn:
        return {"found": False, "error": f"{Config.LOCAL_DB_PATH} not found"}

    try:
        cur = conn.cursor()
        rollno_str = str(rollno).strip()

        cur.execute("SELECT * FROM students WHERE rollno = ? LIMIT 1", (rollno_str,))
        row = cur.fetchone()

        if not row:
            return {"found": False, "error": f"Roll {rollno_str} DB mein nahi mila"}

        result = {k.lower(): v for k, v in dict(row).items()}
        dob = str(result.get('dob', '')).strip()
        if not dob:
            return {"found": False, "error": "DOB DB mein khali hai"}

        return {
            "found": True,
            "rollno": rollno_str,
            "dob": dob,
            "name": str(result.get('name', '') or result.get('sname', '') or '').strip(),
            "fatherName": str(result.get('fname', '') or result.get('father_name', '') or '').strip(),
            "college": str(result.get('college', '') or result.get('cname', '') or '').strip(),
            "semester": str(result.get('sem', '') or result.get('semester', '2')).strip(),
            "all": result,
        }

    except Exception as e:
        logger.error(f"[DB LOOKUP ERROR] {e}")
        return {"found": False, "error": f"DB error: {str(e)}"}


def check_db():
    """Health check for SQLite database"""
    if not os.path.exists(Config.LOCAL_DB_PATH):
        return {"ok": False, "error": f"{Config.LOCAL_DB_PATH} not found"}
    if not init_indexes():
        return {"ok": False, "error": "Indexes initialization failed"}
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM students")
        count = cur.fetchone()[0]
        return {"ok": True, "count": count}
    except Exception as e:
        return {"ok": False, "error": str(e)}

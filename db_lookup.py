#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite Ultra-Fast Lookup — final.db se rollno → dob
"""

import os
import sqlite3
import threading

DB_FILE = "final.db"

_conn_local = threading.local()


def get_conn():
    if not hasattr(_conn_local, "conn") or _conn_local.conn is None:
        if not os.path.exists(DB_FILE):
            return None
        conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = 10000")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 30000000")
        _conn_local.conn = conn
    return _conn_local.conn


def init_indexes():
    conn = get_conn()
    if not conn:
        print(f"[DB] {DB_FILE} nahi mila")
        return False

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='students'")
        if not cur.fetchone():
            print("[DB] 'students' table nahi mila")
            return False

        cur.execute("PRAGMA table_info(students)")
        cols = [row[1].lower() for row in cur.fetchall()]
        print(f"[DB] Columns: {cols}")

        if 'rollno' in cols:
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rollno ON students(rollno)")
                conn.commit()
                print("[DB] Index (rollno) ready")
            except Exception as e:
                print(f"[DB] Index warning: {e}")

        cur.execute("SELECT COUNT(*) FROM students")
        count = cur.fetchone()[0]
        print(f"[DB] Total students: {count}")

        return True
    except Exception as e:
        print(f"[DB ERROR] {e}")
        return False


def get_dob_by_rollno(rollno):
    """
    Ultra-fast: rollno → dob
    Returns: dict {found: bool, dob: str, name: str, ...}
    """
    conn = get_conn()
    if not conn:
        return {"found": False, "error": f"{DB_FILE} nahi mila"}

    try:
        cur = conn.cursor()
        rollno = str(rollno).strip()

        cur.execute("SELECT * FROM students WHERE rollno = ? LIMIT 1", (rollno,))
        row = cur.fetchone()

        if not row:
            return {"found": False, "error": f"Roll {rollno} DB mein nahi mila"}

        result = {k.lower(): v for k, v in dict(row).items()}

        dob = str(result.get('dob', '')).strip()
        if not dob:
            return {"found": False, "error": "DOB DB mein khali hai"}

        return {
            "found": True,
            "rollno": rollno,
            "dob": dob,
            "name": str(result.get('name', '') or result.get('sname', '') or '').strip(),
            "all": result,
        }

    except Exception as e:
        return {"found": False, "error": f"DB error: {str(e)}"}


def check_db():
    if not os.path.exists(DB_FILE):
        return {"ok": False, "error": f"{DB_FILE} nahi mila"}
    if not init_indexes():
        return {"ok": False, "error": "Indexes nahi bane"}
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM students")
        count = cur.fetchone()[0]
        return {"ok": True, "count": count}
    except Exception as e:
        return {"ok": False, "error": str(e)}

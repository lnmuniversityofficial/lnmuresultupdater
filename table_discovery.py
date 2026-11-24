"""
Phase 1: table discovery (LNMU DBs se res/result tables, 24 ghante cache)
Phase 2: parallel search — Option B (har DB ka ek UNION ALL query), fail par Option A (8 threads)
Filter: table naam mein 'res' ya 'result'; columns mein rollno, semester, sno, cop aur res-type column.
"""
import json
import os
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

warnings.filterwarnings("ignore")

SQL_BASE = "https://lnmuniversity.com/ugentrance23/photo/Manager.aspx"
HEADERS = {
    "content-type": "application/json; charset=utf-8",
    "origin": "https://lnmuniversity.com",
    "referer": "https://lnmuniversity.com/ugentrance23/photo/",
    "user-agent": "Mozilla/5.0",
}
DB_LIST = ["DBlnmu", "DBlnmu_25", "Lnmu_ResultMaster", "TempDbLnmu"]
CACHE_FILE = os.environ.get("LNMU_TABLE_CACHE", "table_cache.json")
CACHE_TTL = 24 * 3600
MAX_WORKERS = 8
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

LAST_TIMING = {}


def _post(endpoint, payload, timeout=60):
    err = ""
    for _ in range(2):
        try:
            r = requests.post(f"{SQL_BASE}/{endpoint}", json=payload, headers=HEADERS,
                              timeout=timeout, verify=False)
            return r.json().get("d", {}) or {}
        except Exception as e:  # noqa: BLE001
            err = str(e)
            time.sleep(0.5)
    return {"success": False, "message": f"network: {err}"}


def _query(db, sql):
    d = _post("ExecuteCustomQuery", {"dbName": db, "sqlQuery": sql}, timeout=300)
    if not d.get("success"):
        raise RuntimeError(d.get("message", "query fail"))
    return d.get("rows") or d.get("data") or []


def _is_res(col):
    c = col.lower()
    return c in ("res", "result", "resremark", "rs1") or bool(re.match(r"^res_\d+$", c))


def _is_cop(col):
    c = col.lower()
    return c == "cop" or bool(re.match(r"^cop_\d+$", c))


def _safe(name):
    if not NAME_RE.match(name):
        raise ValueError(f"naam galat: {name}")
    return name


# ---------- Phase 1: discovery + cache ----------

def _discover_db(db):
    sql = ("SELECT s.name AS sch, t.name AS tbl, c.name AS col "
           "FROM sys.tables t "
           "JOIN sys.schemas s ON s.schema_id = t.schema_id "
           "JOIN sys.columns c ON c.object_id = t.object_id "
           "WHERE LOWER(t.name) LIKE '%res%' OR LOWER(t.name) LIKE '%result%'")
    by_table = {}
    for r in _query(db, sql):
        by_table.setdefault((r["sch"], r["tbl"]), []).append(r["col"])
    out = []
    for (sch, tbl), cols in by_table.items():
        has = {c.lower() for c in cols}
        if {"rollno", "semester", "sno"} <= has and any(_is_cop(c) for c in cols) \
                and any(_is_res(c) for c in cols):
            out.append({"db": db, "schema": sch, "table": tbl, "columns": cols})
    return out


def discover_all():
    tables = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(DB_LIST))) as ex:
        futs = {ex.submit(_discover_db, db): db for db in DB_LIST}
        for f in as_completed(futs):
            db = futs[f]
            try:
                found = f.result()
                print(f"[DISCOVERY] {db}: {len(found)} matching tables")
                tables += found
            except Exception as e:  # noqa: BLE001
                print(f"[DISCOVERY] {db}: fail {e}")
    return tables


def get_tables(force=False):
    if not force and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as fh:
                cache = json.load(fh)
            if time.time() - cache.get("created", 0) < CACHE_TTL and cache.get("tables"):
                return cache["tables"]
        except (ValueError, OSError):
            pass
    t0 = time.time()
    tables = discover_all()
    with open(CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"created": time.time(), "tables": tables}, fh, ensure_ascii=False)
    LAST_TIMING["discovery"] = round(time.time() - t0, 2)
    return tables


# ---------- Phase 2: search ----------

def _search_union(db, tables, rollno):
    """Option B: ek DB ka ek hi query (UNION ALL)."""
    parts = []
    for t in tables:
        full = f"{t['db']}.{t['schema']}.{t['table']}"
        parts.append(
            f"SELECT '{full}' AS src, CAST(sno AS NVARCHAR(50)) AS sno, "
            f"CAST(rollno AS NVARCHAR(50)) AS rollno, CAST(semester AS NVARCHAR(20)) AS semester "
            f"FROM [{_safe(t['schema'])}].[{_safe(t['table'])}] WHERE rollno = '{rollno.replace(chr(39), chr(39)*2)}'"
        )
    return _query(db, " UNION ALL ".join(parts))


def _search_threaded(t, rollno):
    """Option A: ek table = ek GetTableData call."""
    d = _post("GetTableData", {"dbName": t["db"], "schemaName": t["schema"], "tableName": t["table"],
                               "page": 1, "pageSize": 100, "sortColumn": "", "sortOrder": "ASC",
                               "searchKeyword": rollno})
    if not d.get("success"):
        return []
    out = []
    for r in d.get("rows") or []:
        out.append({"src": f"{t['db']}.{t['schema']}.{t['table']}", "sno": r.get("sno"),
                    "rollno": r.get("rollno"), "semester": r.get("semester")})
    return out


def find_targets(rollno, semester):
    """Returns target dicts (purane format jaise): db, schema, table, sno, columns, row, target_key."""
    from sql_runner import normalize_semester
    rollno = str(rollno).strip()
    want = normalize_semester(semester)

    t0 = time.time()
    tables = get_tables()
    LAST_TIMING.setdefault("discovery", round(time.time() - t0, 2))
    by_db = {}
    for t in tables:
        by_db.setdefault(t["db"], []).append(t)

    t1 = time.time()
    hits = []                    # (src, sno, rollno, semester)
    by_key = {f"{t['db']}.{t['schema']}.{t['table']}": t for t in tables}
    fallback_tables = []

    def _b(db):
        return db, _search_union(db, by_db[db], rollno)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_b, db): db for db in by_db}
        for f in as_completed(futs):
            db = futs[f]
            try:
                _, rows = f.result()
                hits += [(r.get("src"), r.get("sno"), r.get("rollno"), r.get("semester")) for r in rows]
            except Exception as e:  # noqa: BLE001
                print(f"[SEARCH] Option B fail {db}: {e} -> Option A")
                fallback_tables += by_db[db]

    if fallback_tables:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for rows in ex.map(lambda t: _search_threaded(t, rollno), fallback_tables):
                hits += [(r["src"], r["sno"], r["rollno"], r["semester"]) for r in rows]
    LAST_TIMING["search"] = round(time.time() - t1, 2)

    targets, seen = [], set()
    for src, sno, r_roll, r_sem in hits:
        if str(r_roll or "").strip() != rollno:
            continue
        if normalize_semester(r_sem) != want or src in seen:
            continue
        t = by_key.get(src)
        if not t:
            continue
        seen.add(src)
        targets.append({
            "db": t["db"], "schema": t["schema"], "table": t["table"],
            "sno": str(sno or "").strip(), "columns": t["columns"], "row": {},
            "target_key": src,
        })
        print(f"  ✅ Verified Target: {src} | sno: {sno} | sem: {want}")
    LAST_TIMING["total"] = round(time.time() - t0, 2)
    print(f"[TIMING] discovery={LAST_TIMING.get('discovery')}s search={LAST_TIMING.get('search')}s "
          f"total={LAST_TIMING['total']}s targets={len(targets)}")
    return targets

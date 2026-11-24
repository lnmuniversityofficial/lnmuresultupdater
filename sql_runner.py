#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL Runner — Manager.aspx ke through SQL execute + UpdateCell
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import json
import warnings
import requests

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

SQL_BASE = "https://lnmuniversity.com/ugentrance23/photo/Manager.aspx"
SQL_HEADERS = {
    "content-type": "application/json; charset=utf-8",
    "origin": "https://lnmuniversity.com",
    "referer": "https://lnmuniversity.com/ugentrance23/photo/",
    "user-agent": "Mozilla/5.0",
}


def run_sql(sql, db="master", timeout=300):
    """SQL command run karo (SELECT only)"""
    for attempt in range(2):
        try:
            r = requests.post(
                f"{SQL_BASE}/ExecuteCustomQuery",
                json={"dbName": db, "sqlQuery": sql},
                headers=SQL_HEADERS,
                timeout=timeout,
                verify=False
            )
            try:
                return r.json().get("d", {})
            except Exception:
                return {"success": False, "message": f"Invalid JSON: {r.text[:200]}"}
        except Exception as e:
            print(f"[SQL-RUN ERROR] Attempt {attempt + 1}: {e}")
            time.sleep(2)
    return {"success": False, "message": "Network error"}


def get_table_columns_v2(db, schema, table):
    """GetTableData API se columns fetch karo (correct columns)"""
    url = f"{SQL_BASE}/GetTableData"
    payload = {
        "dbName": db,
        "schemaName": schema,
        "tableName": table,
        "page": 1,
        "pageSize": 1,
        "sortColumn": "",
        "sortOrder": "ASC",
        "searchKeyword": ""
    }
    for attempt in range(2):
        try:
            r = requests.post(url, json=payload, headers=SQL_HEADERS, timeout=30, verify=False)
            try:
                result = r.json().get("d", {})
                if result.get("success"):
                    cols = result.get("columns", [])
                    return [c.get("name") for c in cols if c.get("name")]
                return []
            except Exception:
                return []
        except Exception as e:
            print(f"[GET-COLUMNS ERROR] Attempt {attempt + 1}: {e}")
            time.sleep(2)
    return []


def update_cell(db, schema, table, primary_keys, column, new_value, is_null=False, timeout=30):
    """UpdateCell API — 1 cell update karta hai"""
    payload = {
        "dbName": db,
        "schemaName": schema,
        "tableName": table,
        "primaryKeys": primary_keys,
        "columnName": column,
        "newValue": str(new_value) if new_value is not None else "",
        "isNull": is_null,
    }

    for attempt in range(2):
        try:
            r = requests.post(
                f"{SQL_BASE}/UpdateCell",
                json=payload,
                headers=SQL_HEADERS,
                timeout=timeout,
                verify=False
            )
            try:
                return r.json().get("d", {})
            except Exception:
                return {"success": False, "message": f"Invalid JSON: {r.text[:200]}"}
        except Exception as e:
            print(f"[UPDATE-CELL ERROR] Attempt {attempt + 1}: {e}")
            time.sleep(2)
    return {"success": False, "message": "Network error"}


# ============================================================
# MASTER CATALOG OF TABLES BY BATCH (2327, 2428, 2529) — FUTUREPROOF UP TO SEM 8
# ============================================================
BATCH_TABLE_CATALOG = {
    '23': [
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULTCBCS"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "lnmu_resultcbcs_nc"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULTCBCS_old"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULTCBCS_bkp_2508"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "result-data-4th-sem-23-27"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULTCBCS"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULTCBCS_4th"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULTCBCS_NC"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULTCBCS_bak"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_V_SEM_2327"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "lnmu_result_V_Sem"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VI_SEM_2327"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VII_SEM_2327"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VIII_SEM_2327"},
        {"db": "DBlnmu", "schema": "dbo", "table": "LNMU_RESULTCBCS"},
        {"db": "DBlnmu", "schema": "dbo", "table": "lnmu_resultcbcs_nc"},
    ],
    '24': [
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2428"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2428_NC"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2428_old"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "Lnmu_combined23272428"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "RESULT-2SEM-2024-2028"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2428"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2428_NC"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2428_bak"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2428_NEW"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LiveRegResult_II_2428"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "Result_Sem3_2428"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_IV_2428"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_V_2428"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VI_2428"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VII_2428"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VIII_2428"},
        {"db": "DBlnmu", "schema": "dbo", "table": "LNMU_RESULT2428"},
    ],
    '25': [
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2529"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2529_NC"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2529_old"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_II_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_II_2529_NC"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_II_2529_old"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_III_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_IV_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_V_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VI_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VII_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_VIII_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "lnmu_result2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2529_NC"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "lnmuresult_cbcs_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "lnmuresult_cbcs_2529_new"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "lnmuresult_cbcs_2529_old"},
        {"db": "DBlnmu", "schema": "dbo", "table": "LNMU_RESULT2529"},
    ]
}

# Teen DB/table targets
TARGETS = [
    {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_II_2529"},
    {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2529"},
    {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2529"},
]

def normalize_semester(s):
    """Roman, string aur number semesters ko single digit '1'-'8' me normalize karta hai"""
    if s is None:
        return ""
    st = str(s).strip().upper()
    roman_map = {
        "I": "1", "II": "2", "III": "3", "IV": "4",
        "V": "5", "VI": "6", "VII": "7", "VIII": "8",
        "1ST": "1", "2ND": "2", "3RD": "3", "4TH": "4",
        "5TH": "5", "6TH": "6", "7TH": "7", "8TH": "8"
    }
    if st in roman_map:
        return roman_map[st]
    m = re.search(r'([1-8])', st)
    if m:
        return m.group(1)
    return st


def get_table_data(db, schema, table, search_keyword="", page=1, page_size=100, timeout=30):
    """GetTableData API — search by rollno with all columns and rows in one call"""
    url = f"{SQL_BASE}/GetTableData"
    payload = {
        "dbName": db,
        "schemaName": schema,
        "tableName": table,
        "page": page,
        "pageSize": page_size,
        "sortColumn": "",
        "sortOrder": "ASC",
        "searchKeyword": str(search_keyword).strip()
    }
    for attempt in range(2):
        try:
            r = requests.post(url, json=payload, headers=SQL_HEADERS, timeout=timeout, verify=False)
            try:
                res = r.json().get("d", {})
                if res.get("success"):
                    return res
                return {"success": False, "message": res.get("message", "fail"), "rows": [], "columns": []}
            except Exception:
                return {"success": False, "message": "Invalid JSON", "rows": [], "columns": []}
        except Exception as e:
            print(f"[GET-TABLE-DATA ERROR] {db}.{table} Attempt {attempt + 1}: {e}")
            time.sleep(1)
    return {"success": False, "message": "Network error", "rows": [], "columns": []}


def get_table_columns(db, schema, table):
    """GetTableData API se columns fetch karo"""
    data = get_table_data(db, schema, table, search_keyword="", page=1, page_size=1)
    if data.get("success"):
        cols = data.get("columns", [])
        return [c.get("name") for c in cols if c.get("name")]
    return []


def get_table_columns_v2(db, schema, table):
    return get_table_columns(db, schema, table)


def find_matching_targets(rollno, semester, batch_year=None):
    """
    Phase 1+2: LNMU DBs ki res/result tables (cache 24h), parallel search (UNION ALL, fail par threads).
    Sem 6 (batch 23) ka default path waisa hi. Discovery fail ho to purana catalog scan.
    """
    rollno = str(rollno).strip()
    target_sem_norm = normalize_semester(semester)
    if not batch_year:
        batch_year = rollno[:2] if len(rollno) >= 2 else "25"
    if target_sem_norm == "6" and str(batch_year) == "23":
        return _sem6_default_targets(rollno)
    try:
        from table_discovery import find_targets
        return find_targets(rollno, target_sem_norm)
    except Exception as ex:  # noqa: BLE001
        print(f"[DISCOVERY] fail, catalog fallback: {ex}")
        return _find_targets_catalog(rollno, semester, batch_year)


def _find_targets_catalog(rollno, semester, batch_year=None):
    """
    Search all category tables for given batch.
    VERIFIES that the row exists AND belongs to the requested semester.
    Returns list of verified target dictionaries with sno and available columns.
    """
    rollno = str(rollno).strip()
    target_sem_norm = normalize_semester(semester)
    if not batch_year:
        batch_year = rollno[:2] if len(rollno) >= 2 else "25"

    candidate_tables = BATCH_TABLE_CATALOG.get(batch_year, BATCH_TABLE_CATALOG.get("25", []))
    verified_targets = []

    print(f"[TARGET SCAN] Scanning {len(candidate_tables)} tables for Batch {batch_year}, Roll: {rollno}, Sem: {target_sem_norm}")

    for item in candidate_tables:
        db = item["db"]
        schema = item.get("schema", "dbo")
        table = item["table"]

        data = get_table_data(db, schema, table, search_keyword=rollno, page=1, page_size=100)
        if not data.get("success"):
            continue

        rows = data.get("rows") or []
        columns = [c.get("name") for c in data.get("columns", []) if c.get("name")]

        for row in rows:
            r_roll = str(row.get("rollno") or row.get("RollNo") or row.get("Roll_No") or "").strip()
            if r_roll != rollno:
                continue

            r_sem = row.get("semester") or row.get("Semester") or row.get("sem")
            r_sem_norm = normalize_semester(r_sem) if r_sem else None

            sem_matched = False
            if r_sem_norm:
                if r_sem_norm == target_sem_norm:
                    sem_matched = True
            else:
                t_lower = table.lower()
                sem_patterns = {
                    "1": ["_i_", "1sem", "sem1", "1st", "sem_1", "_1_"],
                    "2": ["_ii_", "2sem", "sem2", "2nd", "sem_2", "_2_"],
                    "3": ["_iii_", "3sem", "sem3", "3rd", "sem_3", "_3_"],
                    "4": ["_iv_", "4sem", "sem4", "4th", "sem_4", "_4_"],
                    "5": ["_v_", "5sem", "sem5", "5th", "sem_5", "_5_"],
                    "6": ["_vi_", "6sem", "sem6", "6th", "sem_6", "_6_"],
                    "7": ["_vii_", "7sem", "sem7", "7th", "sem_7", "_7_"],
                    "8": ["_viii_", "8sem", "sem8", "8th", "sem_8", "_8_"],
                }
                patterns = sem_patterns.get(target_sem_norm, [])
                if any(p in t_lower for p in patterns):
                    sem_matched = True
                elif f"sem{target_sem_norm}" in t_lower or f"sem_{target_sem_norm}" in t_lower or f"_{target_sem_norm}_" in t_lower:
                    sem_matched = True

            if sem_matched:
                sno_val = str(row.get("sno") or row.get("SNO") or row.get("id") or "").strip()
                t_key = f"{db}.{schema}.{table}"
                verified_targets.append({
                    "db": db,
                    "schema": schema,
                    "table": table,
                    "sno": sno_val,
                    "columns": columns,
                    "row": row,
                    "target_key": t_key
                })
                print(f"  ✅ Verified Target: {t_key} | sno: {sno_val} | sem: {r_sem_norm or target_sem_norm}")
                break

    return verified_targets


def get_subject_credit(db, schema, table, semester, subject_code, kind="theory", timeout=20):
    """
    Credit 0/missing ho to usi SUBJECT CODE (a1id_<sem>) ke kisi bhi row se
    uska sahi credit nikalo — random/next-rollno se nahi, exact subject match se.
    kind: 'theory' -> a1cr_<sem> column, 'practical' -> ap1cr_<sem> column.
    Returns int credit ya None agar kuch na mile.
    """
    if not subject_code:
        return None
    sem = str(semester).strip()
    id_col = f"a1id_{sem}"
    cr_col = f"a1cr_{sem}" if kind == "theory" else f"ap1cr_{sem}"
    safe_code = str(subject_code).replace("'", "''")

    sql = (
        f"SELECT TOP 1 {cr_col} AS credit FROM {schema}.{table} "
        f"WHERE {id_col} = '{safe_code}' AND {cr_col} IS NOT NULL AND {cr_col} > 0"
    )
    try:
        result = run_sql(sql, db=db, timeout=timeout)
        if result.get("success"):
            rows = result.get("rows") or result.get("data") or []
            if rows:
                val = rows[0].get("credit")
                try:
                    iv = int(float(val))
                    return iv if iv > 0 else None
                except (TypeError, ValueError):
                    return None
    except Exception as e:
        print(f"[CREDIT-LOOKUP ERROR] {db}.{table} {subject_code} {kind}: {e}")
    return None


def get_subject_codes_by_roll(db, schema, table, rollno, semester, timeout=8):
    """
    Student ki apni DB row se slot-wise subject code nikalo (a1id_<slot>).
    Returns {slot_number: code} ya {} agar row na mile.
    """
    safe_roll = str(rollno).replace("'", "''")
    sql = f"SELECT TOP 1 * FROM {schema}.{table} WHERE rollno = '{safe_roll}'"
    try:
        result = run_sql(sql, db=db, timeout=timeout)
        if not result.get("success"):
            return {}
        rows = result.get("rows") or result.get("data") or []
        if not rows:
            return {}
        row = rows[0]
        r_sem = row.get("semester") or row.get("Semester") or row.get("sem")
        if r_sem and normalize_semester(r_sem) != normalize_semester(semester):
            return {}
        codes = {}
        for k, v in row.items():
            m = re.match(r"^a1id_(\d+)$", str(k), re.I)
            if m and v is not None and str(v).strip():
                codes[int(m.group(1))] = str(v).strip()
        return codes
    except Exception as e:
        print(f"[CODE-LOOKUP ERROR] {db}.{table} {rollno}: {e}")
        return {}



def get_student_row(db, schema, table, rollno, semester, timeout=8):
    """Student ki apni poori DB row (agar is table mein ho aur semester match kare), warna None."""
    safe_roll = str(rollno).replace("'", "''")
    sql = f"SELECT TOP 1 * FROM {schema}.{table} WHERE rollno = '{safe_roll}'"
    try:
        result = run_sql(sql, db=db, timeout=timeout)
        if not result.get("success"):
            return None
        rows = result.get("rows") or result.get("data") or []
        if not rows:
            return None
        row = rows[0]
        r_sem = row.get("semester") or row.get("Semester") or row.get("sem")
        if r_sem and normalize_semester(r_sem) != normalize_semester(semester):
            return None
        return row
    except Exception as e:
        print(f"[ROW-LOOKUP ERROR] {db}.{table} {rollno}: {e}")
        return None



def check_row_exists(db, schema, table, primary_keys):
    """Check if row exists and get current values (Legacy helper)"""
    where_clause = " AND ".join([f"{col} = '{v}'" for col, v in primary_keys.items()])
    sql = f"SELECT * FROM {schema}.{table} WHERE {where_clause}"
    result = run_sql(sql, db=db, timeout=30)
    if result.get("success"):
        rows = result.get("rows") or result.get("data") or []
        return rows[0] if rows else None
    return None


def preview_updates(updates, rollno=None, semester="2", batch_year=None, primary_keys=None, verified_targets=None):
    """
    Preview: Konsa column kis verified table me update hoga, konsa skip hoga
    """
    if primary_keys and not rollno:
        rollno = primary_keys.get("rollno")
        semester = primary_keys.get("semester", semester)

    if verified_targets is None and rollno:
        verified_targets = find_matching_targets(rollno, semester, batch_year)

    preview = {
        "rollno": rollno,
        "semester": semester,
        "batch": batch_year or (rollno[:2] if rollno and len(rollno) >= 2 else "25"),
        "total_updates": len(updates),
        "verified_targets_count": len(verified_targets or []),
        "targets": {},
    }

    if not verified_targets:
        preview["summary"] = {
            "tables_accessible": 0,
            "total_will_update": 0,
            "total_will_skip": len(updates),
        }
        return preview

    for target in verified_targets:
        t_key = target["target_key"]
        columns = set(target.get("columns", []))
        row_data = target.get("row", {})
        sno = target.get("sno", "")

        target_preview = {
            "status": "ok",
            "db": target["db"],
            "table": target["table"],
            "sno": sno,
            "columns_available": len(columns),
            "will_update": [],
            "will_skip": [],
        }

        for u in updates:
            col = u.get("column")
            val = u.get("value")

            if col not in columns:
                target_preview["will_skip"].append({
                    "column": col,
                    "value": val,
                    "reason": "column_not_exists"
                })
            else:
                target_preview["will_update"].append({
                    "column": col,
                    "old_value": row_data.get(col),
                    "new_value": val
                })

        preview["targets"][t_key] = target_preview

    total_will_update = sum(
        len(t.get("will_update", []))
        for t in preview["targets"].values()
        if t.get("status") == "ok"
    )
    total_will_skip = sum(
        len(t.get("will_skip", []))
        for t in preview["targets"].values()
        if t.get("status") == "ok"
    )
    preview["summary"] = {
        "tables_accessible": len(verified_targets),
        "total_will_update": total_will_update,
        "total_will_skip": total_will_skip,
    }

    return preview


def execute_batch_updates(updates, rollno=None, semester="2", batch_year=None, primary_keys=None, verified_targets=None, db=None, schema=None, table=None):
    """
    Multiple cells update karo — sirf un tables pe jisme is rollno aur is semester ka data verify hua ho
    """
    if primary_keys and not rollno:
        rollno = primary_keys.get("rollno")
        semester = primary_keys.get("semester", semester)

    # Legacy single table call
    if db and schema and table:
        verified_targets = [{
            "db": db,
            "schema": schema,
            "table": table,
            "sno": (primary_keys or {}).get("sno", ""),
            "columns": get_table_columns(db, schema, table),
            "target_key": f"{db}.{schema}.{table}"
        }]
    elif verified_targets is None and rollno:
        verified_targets = find_matching_targets(rollno, semester, batch_year)

    overall = {
        "success": True,
        "rollno": rollno,
        "semester": semester,
        "batch": batch_year or (rollno[:2] if rollno and len(rollno) >= 2 else "25"),
        "total": len(updates),
        "verified_targets_count": len(verified_targets or []),
        "targets": {},
        "success_count": 0,
        "failed": [],
    }

    if not verified_targets:
        overall["success"] = False
        overall["message"] = f"Koi matching table nahi mili jisme rollno={rollno} aur semester={semester} ho."
        return overall

    def _run_one(target):
        t_db = target["db"]
        t_schema = target["schema"]
        t_table = target["table"]
        t_sno = target.get("sno", "")
        t_key = target["target_key"]
        columns = set(target.get("columns", []))
        pk = {"sno": t_sno} if t_sno else (primary_keys or {"rollno": rollno, "semester": semester})
        results = {"success": True, "sno": t_sno, "success_count": 0, "failed": [], "details": []}
        applied = []

        print(f"[UPDATE] Target: {t_key} | PK: {pk}")

        from edit_rules import load_row, plan_for_row
        cur_row, cur_err = load_row(rollno, t_sno, t_db, t_schema, t_table)
        plan_apply = []
        if cur_row is None:
            results["success"] = False
            results["failed"].append({"column": "-", "value": "-", "error": f"current row nahi mili: {cur_err}"})
        else:
            plan = plan_for_row(updates, cur_row)
            results["unchanged"] = plan["unchanged"]
            results["blocked"] = plan["blocked"]
            results["fee"] = plan["fee"]
            print(f"[PLAN] {t_key}: apply={len(plan['apply'])} unchanged={len(plan['unchanged'])} "
                  f"blocked={plan['blocked']} fee={plan['fee']}")
            plan_apply = plan["apply"]

        for u in plan_apply:
            col = u.get("column")
            val = u.get("value")
            is_null = u.get("is_null", False)
            if columns and col not in columns:
                continue
            result = update_cell(t_db, t_schema, t_table, pk, col, val, is_null)
            if result.get("success"):
                results["success_count"] += 1
                applied.append({"db": t_db, "schema": t_schema, "table": t_table, "pk": pk,
                                "col": col, "old": cur_row.get(col) if cur_row else None})
                results["details"].append({"column": col, "value": val, "ok": True})
            else:
                results["success"] = False
                results["failed"].append({"column": col, "value": val, "error": result.get("message", "unknown")})
                results["details"].append({"column": col, "value": val, "ok": False, "error": result.get("message")})
            time.sleep(0.05)
        return t_key, results, applied

    applied_all = []
    t_upd = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_run_one, t) for t in verified_targets]
        for f in as_completed(futs):
            t_key, res, applied = f.result()
            overall["targets"][t_key] = res
            applied_all += applied
            if not res["success"]:
                overall["success"] = False

    if not overall["success"] and applied_all:
        # Koi table fail: jo likha gaya use sab jagah wapas karo
        rb = []
        for a_ in reversed(applied_all):
            old = a_["old"]
            r = update_cell(a_["db"], a_["schema"], a_["table"], a_["pk"], a_["col"],
                            "" if old is None else old, old is None)
            if not r.get("success"):
                rb.append(f"{a_['table']}.{a_['col']}: {r.get('message')}")
        overall["rolled_back"] = True
        overall["rollback_errors"] = rb
        print(f"[ROLLBACK] {len(applied_all)} cells wapas. errors={len(rb)}")
    overall["timing_update"] = round(time.time() - t_upd, 2)

    total_success = sum(r["success_count"] for r in overall["targets"].values())
    overall["success_count"] = 0 if overall.get("rolled_back") else total_success

    for t_key, r in overall["targets"].items():
        for f in r.get("failed", []):
            overall["failed"].append({**f, "target": t_key})

    return overall
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL Runner — Manager.aspx ke through SQL execute + UpdateCell
"""

import time
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


# Teen DB/table targets
TARGETS = [
    {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_II_2529"},
    {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2529"},
    {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2529"},
]


def get_table_columns(db, schema, table):
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


def check_row_exists(db, schema, table, primary_keys):
    """Check if row exists and get current values"""
    where_clause = " AND ".join([f"{col} = '{v}'" for col, v in primary_keys.items()])
    sql = f"SELECT * FROM {schema}.{table} WHERE {where_clause}"
    result = run_sql(sql, db=db, timeout=30)
    if result.get("success"):
        rows = result.get("rows") or result.get("data") or []
        return rows[0] if rows else None
    return None


def preview_updates(updates, primary_keys):
    """
    Preview: konsa column kis table me update hoga, konsa skip hoga
    Returns: dict with planned updates per table
    """
    preview = {
        "total_updates": len(updates),
        "targets": {},
    }

    for target in TARGETS:
        t_db = target["db"]
        t_schema = target["schema"]
        t_table = target["table"]
        t_key = f"{t_db}.{t_schema}.{t_table}"

        # Get columns
        columns = get_table_columns(t_db, t_schema, t_table)
        if not columns:
            preview["targets"][t_key] = {
                "status": "error",
                "message": "Table not accessible or no columns found"
            }
            continue

        # Check row exists
        row_data = check_row_exists(t_db, t_schema, t_table, primary_keys)

        target_preview = {
            "status": "ok",
            "columns_available": columns,
            "row_exists": row_data is not None,
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
            elif row_data is None:
                target_preview["will_skip"].append({
                    "column": col,
                    "value": val,
                    "reason": "row_not_exists"
                })
            elif row_data.get(col) is None or str(row_data.get(col)).strip() == "":
                target_preview["will_skip"].append({
                    "column": col,
                    "value": val,
                    "reason": "current_value_null"
                })
            else:
                target_preview["will_update"].append({
                    "column": col,
                    "old_value": row_data.get(col),
                    "new_value": val
                })

        preview["targets"][t_key] = target_preview

    # Summary
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
        "tables_accessible": sum(1 for t in preview["targets"].values() if t.get("status") == "ok"),
        "total_will_update": total_will_update,
        "total_will_skip": total_will_skip,
    }

    return preview


def execute_batch_updates(updates, db=None, schema=None, table=None, primary_keys=None):
    """Multiple cells update karo — dono DBs pe"""
    # Legacy call ke liye backward compatibility
    if db and schema and table:
        targets = [{"db": db, "schema": schema, "table": table}]
    else:
        targets = TARGETS

    overall = {
        "success": True,
        "total": len(updates),
        "targets": {},
    }

    for target in targets:
        t_db = target["db"]
        t_schema = target["schema"]
        t_table = target["table"]
        t_key = f"{t_db}.{t_schema}.{t_table}"

        results = {
            "success": True,
            "success_count": 0,
            "failed": [],
            "details": [],
        }

        print(f"[UPDATE] Target: {t_key}")

        for u in updates:
            col = u.get("column")
            val = u.get("value")
            is_null = u.get("is_null", False)

            result = update_cell(t_db, t_schema, t_table, primary_keys, col, val, is_null)

            if result.get("success"):
                results["success_count"] += 1
                results["details"].append({"column": col, "value": val, "ok": True})
            else:
                results["success"] = False
                results["failed"].append({
                    "column": col,
                    "value": val,
                    "error": result.get("message", "unknown")
                })
                results["details"].append({"column": col, "value": val, "ok": False, "error": result.get("message")})

            time.sleep(0.1)

        overall["targets"][t_key] = results
        if not results["success"]:
            overall["success"] = False

    # Overall summary
    total_success = sum(r["success_count"] for r in overall["targets"].values())
    overall["success_count"] = total_success
    overall["failed"] = []
    for t_key, r in overall["targets"].items():
        for f in r.get("failed", []):
            overall["failed"].append({**f, "target": t_key})

    return overall

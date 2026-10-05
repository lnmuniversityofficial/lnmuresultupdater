# -*- coding: utf-8 -*-
"""
SQL Runner module — Manager.aspx API integration
Executes queries, cell updates, previews, snapshot extraction, and rollback/revert operations.
"""

import time
import warnings
import logging
import requests
from config import Config

logger = logging.getLogger('sql_runner')
warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

SQL_BASE = Config.SQL_BASE_URL
SQL_HEADERS = {
    "content-type": "application/json; charset=utf-8",
    "origin": "https://lnmuniversity.com",
    "referer": "https://lnmuniversity.com/ugentrance23/photo/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
}

TARGETS = Config.SQL_TARGETS


def run_sql(sql, db="master", timeout=300):
    """Execute SQL query (SELECT / read)"""
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
            logger.error(f"[SQL-RUN ERROR] Attempt {attempt + 1}: {e}")
            time.sleep(2)
    return {"success": False, "message": "Network error connecting to SQL Runner API"}


def get_table_columns(db, schema, table):
    """Fetch columns from table via GetTableData API"""
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
            logger.error(f"[GET-COLUMNS ERROR] Attempt {attempt + 1}: {e}")
            time.sleep(1)
    return []


def check_row_exists(db, schema, table, primary_keys):
    """Check if row exists in target table and return its column values"""
    where_clause = " AND ".join([f"{col} = '{v}'" for col, v in primary_keys.items()])
    sql = f"SELECT * FROM {schema}.{table} WHERE {where_clause}"
    result = run_sql(sql, db=db, timeout=30)
    if result.get("success"):
        rows = result.get("rows") or result.get("data") or []
        return rows[0] if rows else None
    return None


def update_cell(db, schema, table, primary_keys, column, new_value, is_null=False, timeout=30):
    """Update single cell in target table via UpdateCell API"""
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
            logger.error(f"[UPDATE-CELL ERROR] Attempt {attempt + 1}: {e}")
            time.sleep(1)
    return {"success": False, "message": "Network error executing cell update"}


def fetch_row_snapshot(primary_keys, columns_to_watch=None):
    """
    Capture a complete snapshot of row values across all target databases before an update.
    Used for audit trails and REVERT capability.
    """
    snapshot = {
        "primaryKeys": primary_keys,
        "capturedAt": time.time(),
        "targets": {}
    }

    for target in TARGETS:
        t_db = target["db"]
        t_schema = target["schema"]
        t_table = target["table"]
        t_key = f"{t_db}.{t_schema}.{t_table}"

        row_data = check_row_exists(t_db, t_schema, t_table, primary_keys)
        if row_data:
            if columns_to_watch:
                filtered_row = {k: v for k, v in row_data.items() if k in columns_to_watch or k in primary_keys}
                snapshot["targets"][t_key] = filtered_row
            else:
                snapshot["targets"][t_key] = row_data
        else:
            snapshot["targets"][t_key] = None

    return snapshot


def preview_updates(updates, primary_keys):
    """
    Simulate what will be updated across all target databases.
    Returns details on matching columns, existing values, and planned updates.
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

        columns = get_table_columns(t_db, t_schema, t_table)
        if not columns:
            preview["targets"][t_key] = {
                "status": "error",
                "message": "Table not accessible or no columns found"
            }
            continue

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
            val = u.get("value") if "value" in u else u.get("newValue")

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
    """
    Execute batch updates across configured database targets.
    """
    if db and schema and table:
        targets = [{"db": db, "schema": schema, "table": table}]
    else:
        targets = TARGETS

    overall = {
        "success": True,
        "total": len(updates),
        "targets": {},
        "executedTargets": []
    }

    for target in targets:
        t_db = target["db"]
        t_schema = target["schema"]
        t_table = target["table"]
        t_key = f"{t_db}.{t_schema}.{t_table}"

        results = {
            "database": t_db,
            "table": t_table,
            "schema": t_schema,
            "success": True,
            "successCount": 0,
            "failureCount": 0,
            "failed": [],
            "details": [],
        }

        logger.info(f"[UPDATE] Target: {t_key}")

        for u in updates:
            col = u.get("column")
            val = u.get("value") if "value" in u else u.get("newValue")
            is_null = bool(u.get("is_null", False)) or (val is None) or (col == "cop" and (not val or str(val).strip() in ("", "None", "null")))
            if is_null and col == "cop":
                val = ""

            result = update_cell(t_db, t_schema, t_table, primary_keys, col, val, is_null)

            if result.get("success"):
                results["successCount"] += 1
                results["details"].append({"column": col, "value": val, "ok": True})
            else:
                results["success"] = False
                results["failureCount"] += 1
                results["failed"].append({
                    "column": col,
                    "value": val,
                    "error": result.get("message", "unknown")
                })
                results["details"].append({"column": col, "value": val, "ok": False, "error": result.get("message")})

            time.sleep(0.05)

        overall["targets"][t_key] = results
        overall["executedTargets"].append(results)
        if not results["success"]:
            overall["success"] = False

    total_success = sum(r["successCount"] for r in overall["executedTargets"])
    total_failures = sum(r["failureCount"] for r in overall["executedTargets"])
    overall["cellsUpdated"] = total_success
    overall["success_count"] = total_success
    overall["failure_count"] = total_failures
    overall["status"] = "SUCCESS" if overall["success"] else ("PARTIAL" if total_success > 0 else "FAILED")

    return overall


def execute_revert_updates(snapshot_before, primary_keys):
    """
    ⭐ MAIN REVERT ENGINE
    Restores original cell values from snapshotBeforeUpdate on all target tables.
    Supports both full-row target DB snapshots and submittedOldValues / changes diffs.
    Returns structured results of revert execution.
    """
    if not snapshot_before:
        return {
            "success": False,
            "message": "No snapshotBeforeUpdate found to restore from",
            "cellsReverted": 0
        }

    targets_data = snapshot_before.get("targets", {})
    submitted_old_values = snapshot_before.get("submittedOldValues", {})
    if not submitted_old_values and "changes" in snapshot_before:
        submitted_old_values = {
            c.get("column"): (c.get("before") if c.get("before") is not None else c.get("oldValue"))
            for c in snapshot_before.get("changes", [])
            if c.get("column")
        }

    revert_summary = {
        "success": True,
        "cellsReverted": 0,
        "revertedValues": {},
        "executedTargets": []
    }

    # Check if we have live target DB snapshots
    has_live_target_snapshots = any(
        isinstance(targets_data.get(f"{t['db']}.{t['schema']}.{t['table']}"), dict) and len(targets_data.get(f"{t['db']}.{t['schema']}.{t['table']}")) > 0
        for t in TARGETS
    )

    for target in TARGETS:
        t_db = target["db"]
        t_schema = target["schema"]
        t_table = target["table"]
        t_key = f"{t_db}.{t_schema}.{t_table}"

        target_snapshot = targets_data.get(t_key)
        if not target_snapshot or not isinstance(target_snapshot, dict):
            # Fallback to submitted_old_values for this target if target_snapshot is None
            if submitted_old_values:
                target_snapshot = submitted_old_values
            else:
                logger.warning(f"Target snapshot missing for {t_key}")
                continue

        target_result = {
            "database": t_db,
            "table": t_table,
            "schema": t_schema,
            "success": True,
            "successCount": 0,
            "failureCount": 0,
            "details": []
        }

        # For every column in the saved snapshot (excluding primary keys), restore old value
        for col, original_val in target_snapshot.items():
            if col in primary_keys or not col:
                continue

            is_null = original_val is None or str(original_val).strip() in ("", "None", "null") or (col == "cop" and (not original_val or str(original_val).strip() in ("", "None", "null")))
            val_to_set = "" if (is_null and col == "cop") else original_val

            res = update_cell(
                t_db, t_schema, t_table,
                primary_keys,
                col,
                val_to_set,
                is_null=is_null
            )

            if res.get("success"):
                target_result["successCount"] += 1
                revert_summary["revertedValues"][col] = f"Restored to '{val_to_set}'"
                target_result["details"].append({"column": col, "restoredValue": val_to_set, "ok": True})
            else:
                target_result["success"] = False
                target_result["failureCount"] += 1
                target_result["details"].append({"column": col, "restoredValue": val_to_set, "ok": False, "error": res.get("message")})

            time.sleep(0.05)

        revert_summary["executedTargets"].append(target_result)
        if not target_result["success"]:
            revert_summary["success"] = False

    revert_summary["cellsReverted"] = sum(t["successCount"] for t in revert_summary["executedTargets"])
    return revert_summary

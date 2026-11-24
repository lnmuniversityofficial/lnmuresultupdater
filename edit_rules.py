"""
Edit rules: fee, credit block, unchanged skip.

Rules:
  1. Fee per changed cell: a1te (theory) = 1000, a1pe (practical) = 500,
     a1ts (CIA) = 500, a1ps (practical sessional) = 500. Summary/credit cells = 0.
  2. Credit (a1cr_n / ap1cr_n) sirf tab jayega jab usi subject (n) ka koi marks cell change ho.
     Bina marks change ke credit change = BLOCK (SQL nahi, fee 0).
  3. Har cell ki old value (DB row) aur new value compare karo. Same hai to SQL nahi.
     Change hai to SQL + fee. Fee aur SQL ek hi list se, isliye kabhi alag nahi honge.
  4. sgpa / res / result / resremark / rs1 / cop (aur sem-specific sgpa_N, res_N, cop_N) HAMESHA
     likhe jayenge, chahe value badli ho ya na ho (sirf table mein column ho to).
     Column naam case-insensitive match hota hai.
"""
import re

FEE = {"a1te": 1000, "a1pe": 500, "a1ts": 500, "a1ps": 500}
MARK_BASES = set(FEE)
CREDIT_BASES = {"a1cr", "ap1cr"}
# Ye columns HAR request mein likhe jayenge (value badli ho ya na badli ho), agar table mein hon
ALWAYS_BASES = {"sgpa", "res", "result", "resremark", "rs1", "cop"}
_COL = re.compile(r"^([A-Za-z0-9]+?)_(\d+)$")


def split_col(col):
    """'a1te_3' -> ('a1te', 3); 'sgpa' -> ('sgpa', None)."""
    m = _COL.match(str(col or ""))
    return (m.group(1), int(m.group(2))) if m else (str(col or ""), None)


def norm(v):
    """'50' == '50.0' == 50; blank == None; 'ab' == 'AB'."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    try:
        return f"{float(s):g}"
    except ValueError:
        return s.upper()


def plan_for_row(edits, row):
    """
    edits: [{'column', 'value', 'is_null'}]
    row:   current DB row (dict, table ke saare columns)
    Returns: apply (SQL jayega, list of dict), blocked (credit block), unchanged,
             missing (table mein column nahi), fee (int), fee_cells (fee wale cells).
    """
    lower = {str(k).lower(): k for k in row}
    cands, unchanged, missing = [], [], []
    for e in edits or []:
        raw = str(e.get("column") or "").strip()
        if not raw:
            continue
        col = lower.get(raw.lower())          # table ka asli column naam
        if col is None:
            missing.append(raw)
            continue
        new = None if e.get("is_null") else e.get("value")
        base, _ = split_col(col)
        forced = base.lower() in {b.lower() for b in ALWAYS_BASES} or col.lower() in {b.lower() for b in ALWAYS_BASES}
        if norm(row.get(col)) == norm(new) and not forced:
            unchanged.append(col)
            continue
        cands.append({"column": col, "value": new, "is_null": bool(e.get("is_null")),
                      "old": row.get(col), "forced": forced})

    changed_subjects = set()
    for c in cands:
        base, n = split_col(c["column"])
        if base in MARK_BASES and n is not None:
            changed_subjects.add(n)

    apply_, blocked = [], []
    for c in cands:
        base, n = split_col(c["column"])
        if base in CREDIT_BASES and n not in changed_subjects:
            blocked.append(c["column"])
        else:
            apply_.append(c)

    fee, fee_cells = 0, []
    for c in apply_:
        base, _ = split_col(c["column"])
        if base in FEE:
            fee += FEE[base]
            fee_cells.append(c["column"])

    return {"apply": apply_, "blocked": blocked, "unchanged": unchanged,
            "missing": missing, "fee": fee, "fee_cells": fee_cells}


def load_row(rollno, sno, db, schema, table):
    """Table se is roll ki row (sno se match) — current DB value ke liye."""
    from sql_runner import get_table_data
    data = get_table_data(db, schema, table, search_keyword=rollno, page=1, page_size=100)
    if not data.get("success"):
        return None, data.get("message", "fetch fail")
    for r in data.get("rows") or []:
        if str(r.get("sno") or "").strip() == str(sno).strip():
            return r, None
    return None, "row nahi mili"


_TARGET_CACHE = {}
_TARGET_TTL = 600  # seconds


def _targets_cached(rollno, semester):
    import time
    from sql_runner import find_matching_targets
    key = f"{rollno}|{semester}"
    hit = _TARGET_CACHE.get(key)
    if hit and time.time() - hit[0] < _TARGET_TTL:
        return hit[1]
    targets = find_matching_targets(rollno, semester) or []
    if targets:
        _TARGET_CACHE[key] = (time.time(), targets)
    return targets


def plan_for_request(rollno, semester, edits):
    """Pehle target table jiski row mile, uske against plan. Nahi mila to None."""
    for t in _targets_cached(rollno, semester):
        row, _ = load_row(rollno, t.get("sno"), t["db"], t["schema"], t["table"])
        if row is not None:
            p = plan_for_row(edits, row)
            p["target"] = t["target_key"]
            return p
    return None

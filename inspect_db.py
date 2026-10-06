"""Read-only SQLite inspection for HW4. Uses only Python's standard library."""

import argparse
import json
from pathlib import Path
import re
import sqlite3


DEFAULT_DB = Path(__file__).resolve().parent / "data" / "campus_customs.db"
SECRET_NAME = re.compile(
    r"pass|secret|token|salt|credential|api.?key|private.?key|session|"
    r"recovery|otp|mfa|two.?factor|auth", re.IGNORECASE
)
# Unknown users columns are redacted too, in case a secret has an unusual name.
SAFE_USER_FIELDS = {
    "id", "user_id", "name", "first_name", "last_name", "email", "username",
    "created_at", "updated_at", "role", "status", "is_active",
}
CATEGORY_NAME = re.compile(
    r"(^|_)(category|garment_type|size|colou?rs?|role|status|brand|material|fit|style)($|_)",
    re.IGNORECASE,
)
FREE_TEXT_NAME = re.compile(
    r"(^|_)(id|name|email|description|content|text|tags|json|path|file|url|date|time)($|_)|_at$",
    re.IGNORECASE,
)


def quote(name):
    """Quote a database identifier, including embedded double quotes."""
    return '"' + name.replace('"', '""') + '"'


def sensitive(table, column):
    return bool(SECRET_NAME.search(column)) or (
        table.lower() == "users" and column.lower() not in SAFE_USER_FIELDS
    )


def printable(value):
    if isinstance(value, bytes):
        return f"<binary: {len(value)} bytes>"
    return value


def emit(value):
    print(json.dumps(value, ensure_ascii=True, default=printable))


def columns(con, table):
    return list(con.execute(f"PRAGMA table_info({quote(table)})"))


def show_table(con, table):
    print(f"\n=== TABLE {json.dumps(table)} ===")
    fields = columns(con, table)
    print("Columns (pk_position = 0 means not part of the primary key):")
    for field in fields:
        emit({
            "name": field["name"], "type": field["type"],
            "not_null": bool(field["notnull"]),
            "default": "<redacted>" if sensitive(table, field["name"])
            and field["dflt_value"] is not None else field["dflt_value"],
            "pk_position": field["pk"],
        })

    print("Foreign keys:")
    foreign_keys = [dict(row) for row in con.execute(
        f"PRAGMA foreign_key_list({quote(table)})"
    )]
    emit(foreign_keys)
    print("Indexes (origin: pk = primary key, u = UNIQUE, c = CREATE INDEX):")
    indexes = list(con.execute(f"PRAGMA index_list({quote(table)})"))
    if not indexes:
        emit([])
    for index in indexes:
        parts = list(con.execute(f"PRAGMA index_info({quote(index['name'])})"))
        emit({"name": index["name"], "unique": bool(index["unique"]),
              "origin": index["origin"], "partial": bool(index["partial"]),
              "columns": [part["name"] if part["name"] is not None
                          else "<expression>" for part in parts]})
    print("Note: an INTEGER PRIMARY KEY can use the rowid without a separate index.")
    count = con.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()[0]
    print(f"Row count: {count}")

    print("Sample rows (up to 3; secret fields redacted):")
    # Redact in SQL: secret values are never selected into Python sample rows.
    selection = ", ".join(
        f"'<redacted>' AS {quote(f['name'])}" if sensitive(table, f["name"])
        else quote(f["name"]) for f in fields
    )
    samples = con.execute(f"SELECT {selection} FROM {quote(table)} LIMIT 3")
    for sample in samples:
        emit({key: printable(value) for key, value in dict(sample).items()})
    if not count:
        print("(empty table)")

    print("NULL counts (counts only, including nullable fields):")
    nulls = {}
    for field in fields:
        name = field["name"]
        nulls[name] = con.execute(
            f"SELECT COUNT(*) FROM {quote(table)} WHERE {quote(name)} IS NULL"
        ).fetchone()[0]
    emit(nulls)

    print("Categorical text values and counts (JSON strings kept as stored):")
    found = False
    for field in fields:
        name, dtype = field["name"], field["type"].upper()
        if sensitive(table, name) or not any(t in dtype for t in ("TEXT", "CHAR", "CLOB")):
            continue
        distinct, populated = con.execute(
            f"SELECT COUNT(DISTINCT {quote(name)}), COUNT({quote(name)}) FROM {quote(table)}"
        ).fetchone()
        named_category = bool(CATEGORY_NAME.search(name))
        low_cardinality = (not FREE_TEXT_NAME.search(name)
                           and 0 < distinct <= 20 and populated >= max(10, 2 * distinct))
        if not (named_category or low_cardinality):
            continue
        found = True
        print(f"  {json.dumps(name)}:")
        for value, value_count in con.execute(
            f"SELECT {quote(name)}, COUNT(*) FROM {quote(table)} "
            f"GROUP BY {quote(name)} ORDER BY COUNT(*) DESC, {quote(name)}"
        ):
            emit({"value": value, "count": value_count})
        if not count:
            print("  (empty table)")
    if not found:
        print("(none detected; names, free text, paths, and secrets are excluded)")


def image_path(raw, database):
    """Resolve supported stored paths, requiring a file inside data/products/."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty or non-text path"
    normalized = raw.replace("\\", "/")
    path = Path(normalized)
    data_dir = database.parent
    products = (data_dir / "products").resolve()
    if path.is_absolute():
        candidate = path.resolve()
    elif path.parts and path.parts[0] == "data":
        candidate = (data_dir.parent / path).resolve()
    elif path.parts and path.parts[0] == "products":
        candidate = (data_dir / path).resolve()
    else:
        candidate = (products / path).resolve()
    if not candidate.is_relative_to(products):
        return None, "path resolves outside data/products"
    return candidate, None if candidate.is_file() else "file missing"


def show_images(con, database, tables):
    print("\n=== CATALOGUE IMAGE FILES ===")
    if "catalogue" not in tables:
        print("No catalogue table found.")
        return
    names = {field["name"] for field in columns(con, "catalogue")}
    image_columns = [name for name in sorted(names)
                     if "image" in name.lower() and any(x in name.lower() for x in ("path", "file"))]
    if not image_columns:
        print("No image path/file column detected in catalogue.")
        return
    key = quote("product_id") if "product_id" in names else "NULL"
    for name in image_columns:
        exists = missing = 0
        failures = []
        for product_id, stored in con.execute(f"SELECT {key}, {quote(name)} FROM catalogue"):
            _, issue = image_path(stored, database)
            if issue:
                missing += 1
                failures.append({"product_id": product_id, "stored_path": stored, "issue": issue})
            else:
                exists += 1
        emit({"column": name, "catalogue_rows_checked": exists + missing,
              "files_exist": exists, "missing_or_invalid": missing})
        for failure in failures:
            emit(failure)


def show_inventory_links(con, tables):
    print("\n=== INVENTORY / CATALOGUE LINKS ===")
    if not {"inventory", "catalogue"}.issubset(tables):
        print("The inventory or catalogue table is missing.")
        return
    cat_names = {f["name"] for f in columns(con, "catalogue")}
    inv_names = {f["name"] for f in columns(con, "inventory")}
    if "product_id" not in cat_names or "product_id" not in inv_names:
        print("No shared product_id field. Use the foreign-key metadata above to determine the link.")
        return
    fks = list(con.execute('PRAGMA foreign_key_list("inventory")'))
    declared = any(f["table"] == "catalogue" and f["from"] == "product_id"
                   and f["to"] == "product_id" for f in fks)
    print("inventory.product_id joins catalogue.product_id.")
    print("Link basis:", "declared foreign key" if declared else "inferred from shared column name; no matching declared FK")
    if "size" in inv_names:
        unique_variant = False
        for idx in con.execute('PRAGMA index_list("inventory")').fetchall():
            keys = [r["name"] for r in con.execute(f"PRAGMA index_info({quote(idx['name'])})")]
            if idx["unique"] and not idx["partial"] and set(keys) == {"product_id", "size"}:
                unique_variant = True
        print("UNIQUE constraint/index on (product_id, size):", unique_variant)
        duplicates = con.execute(
            "SELECT product_id, size, COUNT(*) AS n FROM inventory "
            "GROUP BY product_id, size HAVING COUNT(*) > 1"
        ).fetchall()
        print("Duplicate product_id/size groups:", len(duplicates))
        for row in duplicates:
            emit(dict(row))
    no_inventory = con.execute(
        "SELECT c.product_id FROM catalogue c WHERE NOT EXISTS "
        "(SELECT 1 FROM inventory i WHERE i.product_id = c.product_id)"
    ).fetchall()
    orphan_inventory = con.execute(
        "SELECT i.product_id, COUNT(*) AS inventory_rows FROM inventory i WHERE NOT EXISTS "
        "(SELECT 1 FROM catalogue c WHERE c.product_id = i.product_id) GROUP BY i.product_id"
    ).fetchall()
    print("Catalogue items with no inventory rows:", len(no_inventory))
    for row in no_inventory:
        emit(dict(row))
    print("Inventory rows without a matching catalogue item:", sum(r["inventory_rows"] for r in orphan_inventory))
    for row in orphan_inventory:
        emit(dict(row))
    print("No inventory rows is different from having inventory rows with zero stock.")


def inspect(database):
    database = database.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")
    con = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only = ON")
        con.execute("BEGIN")  # One consistent read snapshot; no writes.
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
        )]
        print("Database:", database)
        print("Read-only inspection. Categorical detection uses names or low distinct-value counts.")
        print("All tables (including SQLite internal tables):")
        emit(tables)
        for table in tables:
            show_table(con, table)
        show_images(con, database, tables)
        show_inventory_links(con, set(tables))
        print("\n=== DATABASE-WIDE FOREIGN KEY CHECK ===")
        violations = list(con.execute("PRAGMA foreign_key_check"))
        print("Foreign-key violations:", len(violations))
        for violation in violations:
            emit(dict(violation))
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="SQLite database file (default: data/campus_customs.db beside this script)")
    args = parser.parse_args()
    try:
        inspect(args.db)
    except (OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Inspection failed: {exc}\n")


if __name__ == "__main__":
    main()

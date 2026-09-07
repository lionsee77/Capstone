"""Inspect the local Senseigigs MySQL database and validate its inferred links."""

from __future__ import annotations

import os
import sys
from typing import Any

import mysql.connector
from mysql.connector import Error


TABLES = ("services", "categories", "specialities", "tags", "speciality_tags")


def setting(name: str, default: str) -> str:
    return os.getenv(name, default)


def print_rows(cursor: Any, query: str, params: tuple[Any, ...] = ()) -> None:
    cursor.execute(query, params)
    headers = list(cursor.column_names)
    rows = cursor.fetchall()
    widths = [len(str(header)) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))

    print("  " + " | ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
    print("  " + "-+-".join("-" * width for width in widths))
    for row in rows:
        print("  " + " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)))


def main() -> int:
    config = {
        "host": setting("MYSQL_HOST", "127.0.0.1"),
        "port": int(setting("MYSQL_PORT", "3307")),
        "database": setting("MYSQL_DATABASE", "greygigz_beta"),
        "user": setting("MYSQL_USER", "senseigigs"),
        "password": setting("MYSQL_PASSWORD", "localpassword"),
    }

    try:
        connection = mysql.connector.connect(**config)
    except Error as exc:
        print(f"Could not connect to MySQL: {exc}", file=sys.stderr)
        print("Start it with: docker compose up -d --build", file=sys.stderr)
        return 1

    cursor = connection.cursor()
    print(f"Connected to {config['database']} at {config['host']}:{config['port']}\n")

    print("TABLE COUNTS")
    for table in TABLES:
        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
        print(f"  {table:<18} {cursor.fetchone()[0]:>6}")

    print("\nCOLUMNS AND KEYS")
    print_rows(
        cursor,
        """
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
    )

    print("\nINFERRED RELATIONSHIPS (not declared as foreign keys in the dumps)")
    print("  services.id           -> categories.service_id")
    print("  categories.id         -> specialities.category_id")
    print("  specialities.id       -> speciality_tags.speciality_id")
    print("  tags.id                -> speciality_tags.tag_id")

    checks = {
        "categories with missing service": """
            SELECT COUNT(*) FROM categories c
            LEFT JOIN services s ON s.id = c.service_id
            WHERE s.id IS NULL
        """,
        "specialities with missing category": """
            SELECT COUNT(*) FROM specialities sp
            LEFT JOIN categories c ON c.id = sp.category_id
            WHERE c.id IS NULL
        """,
        "links with missing speciality": """
            SELECT COUNT(*) FROM speciality_tags st
            LEFT JOIN specialities sp ON sp.id = st.speciality_id
            WHERE sp.id IS NULL
        """,
        "links with missing tag": """
            SELECT COUNT(*) FROM speciality_tags st
            LEFT JOIN tags t ON t.id = st.tag_id
            WHERE t.id IS NULL
        """,
    }

    print("\nREFERENTIAL CHECKS")
    for label, query in checks.items():
        cursor.execute(query)
        print(f"  {label:<38} {cursor.fetchone()[0]}")

    print("\nEXAMPLE: Banking specialities and tags")
    print_rows(
        cursor,
        """
        SELECT sp.id AS speciality_id,
               sp.name AS speciality,
               GROUP_CONCAT(t.name ORDER BY t.name SEPARATOR ', ') AS tags
        FROM services s
        JOIN categories c ON c.service_id = s.id
        JOIN specialities sp ON sp.category_id = c.id
        LEFT JOIN speciality_tags st ON st.speciality_id = sp.id
        LEFT JOIN tags t ON t.id = st.tag_id
        WHERE s.name = 'Industry' AND c.name = 'Banking'
        GROUP BY sp.id, sp.name
        ORDER BY sp.id
        """,
    )

    cursor.close()
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_events.py — importa eventi da CSV nel DB SQLite.

Ora:
- Supporta il campo squawk.
- Crea automaticamente la tabella 'events' se non esiste.
"""

import argparse
import csv
import sqlite3
import os

DB_FILE = "/home/pi/flight_anom_gr/events.db"
TABLE = "events"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen_utc TEXT NOT NULL,
    hex TEXT,
    callsign TEXT,
    reg TEXT,
    lat REAL,
    lon REAL,
    alt_ft INTEGER,
    gs_kt REAL,
    squawk TEXT,
    event_type TEXT NOT NULL,
    note TEXT
);
"""

def connect_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.executescript(SCHEMA)
    return conn

def safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None

def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

def import_csv(csv_file, conn, event_type=None):
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [row for row in reader]

    if event_type:
        rows = [r for r in rows if r["event_type"] == event_type]

    with conn:
        for row in rows:
            conn.execute(f"""
                INSERT INTO {TABLE}
                (first_seen_utc, hex, callsign, reg, lat, lon, alt_ft, gs_kt, squawk, event_type, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["first_seen_utc"], row["hex"], row["callsign"], row["reg"],
                safe_float(row["lat"]),
                safe_float(row["lon"]),
                safe_int(row["alt_ft"]),
                safe_float(row["gs_kt"]),
                row.get("squawk", None),
                row["event_type"], row["note"]
            ))
    print(f"[INFO] Importati {len(rows)} eventi da {csv_file}")

def main():
    ap = argparse.ArgumentParser(description="Importa eventi CSV in SQLite")
    ap.add_argument("import_csv", help="File CSV da importare")
    ap.add_argument("--event-type", choices=["PATTERN", "PROX", "ANOMALY"], help="Filtra per tipo evento")
    args = ap.parse_args()

    conn = connect_db()
    if args.event_type:
        print(f"[INFO] Importando solo eventi tipo {args.event_type}")
    import_csv(args.import_csv, conn, args.event_type)
    conn.close()

if __name__ == "__main__":
    main()

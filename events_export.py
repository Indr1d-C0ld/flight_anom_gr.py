#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
events_export.py — esporta eventi dal DB in CSV.

Ora:
- Supporta anche il campo squawk.
"""

import argparse
import sqlite3
import csv
import os

DB_FILE = "/home/pi/flight_anom_gr/events.db"
TABLE = "events"

def connect_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def export_csv(conn, out_file, event_type=None):
    q = f"SELECT * FROM {TABLE}"
    if event_type:
        q += " WHERE event_type = ?"
        rows = conn.execute(q, (event_type,)).fetchall()
    else:
        rows = conn.execute(q).fetchall()

    if not rows:
        print("[INFO] Nessun evento trovato.")
        return

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))

    print(f"[INFO] Esportati {len(rows)} eventi in {out_file}")

def main():
    ap = argparse.ArgumentParser(description="Esporta eventi da SQLite a CSV")
    ap.add_argument("out_csv", help="File CSV di output")
    ap.add_argument("--event-type", choices=["PATTERN", "PROX", "ANOMALY"], help="Filtra per tipo evento")
    args = ap.parse_args()

    conn = connect_db()
    export_csv(conn, args.out_csv, args.event_type)
    conn.close()

if __name__ == "__main__":
    main()

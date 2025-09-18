#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
events_cli.py — frontend interattivo per esplorare events.db

Ora:
- Supporta la visualizzazione del campo squawk.
- Comando "list ANOMALY" incluso.
"""

import sqlite3
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter

DB_FILE = "events.db"
TABLE = "events"

def connect_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def query_db(conn, where="", params=()):
    q = f"SELECT * FROM {TABLE} WHERE 1=1 {where} ORDER BY datetime(first_seen_utc) DESC LIMIT 50"
    cur = conn.execute(q, params)
    rows = cur.fetchall()
    for r in rows:
        print(
            f"[{r['first_seen_utc']}] {r['event_type']} "
            f"HEX={r['hex']} FLT={r['callsign']} REG={r['reg']} "
            f"ALT={r['alt_ft']} GS={r['gs_kt']} "
            f"SQ={r['squawk'] if 'squawk' in r.keys() else ''} "
            f"NOTE={r['note']}"
        )
    print(f"--- {len(rows)} risultati ---")

def show_menu():
    conn = connect_db()
    commands = [
        "list all",
        "list PATTERN",
        "list PROX",
        "list ANOMALY",   # aggiunto
        "filter hex",
        "filter callsign",
        "filter date",
        "quit"
    ]
    completer = WordCompleter(commands, ignore_case=True)

    while True:
        cmd = prompt("events> ", completer=completer).strip().lower()

        if cmd in ("quit", "exit"):
            break

        elif cmd == "list all":
            query_db(conn)

        elif cmd == "list pattern":
            query_db(conn, "AND event_type=?", ("PATTERN",))

        elif cmd == "list prox":
            query_db(conn, "AND event_type=?", ("PROX",))

        elif cmd == "list anomaly":
            query_db(conn, "AND event_type=?", ("ANOMALY",))

        elif cmd == "filter hex":
            hx = prompt("HEX (parziale): ").strip().lower()
            query_db(conn, "AND hex LIKE ?", (f"%{hx}%",))

        elif cmd == "filter callsign":
            cs = prompt("Callsign (parziale): ").strip().upper()
            query_db(conn, "AND callsign LIKE ?", (f"%{cs}%",))

        elif cmd == "filter date":
            since = prompt("Da data (YYYY-MM-DD): ").strip()
            until = prompt("A data (YYYY-MM-DD): ").strip()
            try:
                s_dt = f"{since} 00:00:00"
                u_dt = f"{until} 23:59:59"
                query_db(conn,
                         "AND datetime(first_seen_utc) BETWEEN datetime(?) AND datetime(?)",
                         (s_dt, u_dt))
            except Exception as e:
                print("[ERR] Formato data non valido", e)

        else:
            print("Comando non riconosciuto. Usa tab-completion!")

    conn.close()

if __name__ == "__main__":
    show_menu()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_adsb_report.py — genera post Hugo con tabella eventi ADS-B

Funzioni principali:
- Allinea CSV → SQLite (crea DB se non esiste).
- Query per periodo (giornaliero, settimanale, mensile).
- Genera post Hugo con tabella Markdown.
- Compila blog e invia link su Telegram.
"""

import sqlite3
import os
from datetime import datetime, timedelta, date
import argparse
import subprocess

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# ---------------------------
# Config path
# ---------------------------
CSV_FILE = "/home/pi/flight_anom_gr/events.csv"
DB_FILE = "/home/pi/flight_anom_gr/events.db"
TABLE = "events"
BLOG_PATH = "/home/pi/blog"
POSTS_DIR = os.path.join(BLOG_PATH, "content/posts")
BASE_URL = "https://timrouter.dns.army/blog/posts"

# ---------------------------
# Config Telegram (fallback se non in env)
# ---------------------------
TELEGRAM_BOT_TOKEN = "7572618623:AAHsb5JT_IBQ6lpHxdAGjuax76xGyM6EpC4"
TELEGRAM_CHAT_ID = "-1002363443306"

# ---------------------------
# DB helpers
# ---------------------------
def connect_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):
    q = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        first_seen_utc TEXT,
        hex TEXT,
        callsign TEXT,
        reg TEXT,
        model_t TEXT,
        lat REAL,
        lon REAL,
        alt_ft INTEGER,
        gs_kt REAL,
        squawk TEXT,
        ground TEXT,
        event_type TEXT,
        note TEXT
    )
    """
    conn.execute(q)
    conn.commit()

def import_csv_to_db(conn, csv_path: str):
    if not os.path.isfile(csv_path):
        print(f"[INFO] CSV non trovato: {csv_path}")
        return

    import csv
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    inserted = 0
    for r in rows:
        # controlla duplicati minimi
        q_check = f"""
            SELECT 1 FROM {TABLE}
            WHERE first_seen_utc=? AND hex=? AND event_type=? LIMIT 1
        """
        cur = conn.execute(q_check, (r["first_seen_utc"], r["hex"], r["event_type"]))
        if cur.fetchone():
            continue

        conn.execute(
            f"""INSERT INTO {TABLE}
            (first_seen_utc, hex, callsign, reg, model_t,
             lat, lon, alt_ft, gs_kt, squawk, ground,
             event_type, note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("first_seen_utc"), r.get("hex"), r.get("callsign"), r.get("reg"),
                r.get("model_t"), r.get("lat"), r.get("lon"), r.get("alt_ft"),
                r.get("gs_kt"), r.get("squawk"), r.get("ground"),
                r.get("event_type"), r.get("note")
            )
        )
        inserted += 1
    conn.commit()
    print(f"[INFO] Importati {inserted} nuovi eventi dal CSV")

def query_events_by_day_range(conn, start_day: str, end_day: str):
    q = f"""
        SELECT * FROM {TABLE}
        WHERE substr(first_seen_utc,1,10) BETWEEN ? AND ?
        ORDER BY datetime(first_seen_utc) ASC
    """
    return conn.execute(q, (start_day, end_day)).fetchall()

# ---------------------------
# Export helpers
# ---------------------------
def to_markdown(rows):
    if not rows:
        return "_Nessun evento registrato in questo periodo._"
    headers = rows[0].keys()
    out = "| " + " | ".join(headers) + " |\n"
    out += "| " + " | ".join(["---"] * len(headers)) + " |\n"
    for r in rows:
        out += "| " + " | ".join(str(r[h]) if r[h] is not None else "" for h in headers) + " |\n"
    return out

def format_front_matter(title: str, pub_dt_local: datetime, tags=None):
    if tags is None:
        tags = ["ads-b", "report", "monitoraggio"]
    if pub_dt_local.tzinfo is not None:
        iso_ts = pub_dt_local.isoformat(timespec="seconds")
    else:
        iso_ts = pub_dt_local.strftime("%Y-%m-%dT%H:%M:%S")
    tags_yaml = "[" + ",".join(f"\"{t}\"" for t in tags) + "]"
    return f"""---
title: "{title}"
date: {iso_ts}
tags: {tags_yaml}
---
"""

def write_post(pub_date_str: str, slug: str, title: str, body_md: str):
    year = pub_date_str[:4]
    post_dir = os.path.join(POSTS_DIR, year)
    os.makedirs(post_dir, exist_ok=True)
    filename = f"{pub_date_str}-{slug}.md"
    filepath = os.path.join(post_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(body_md)
    return filepath, filename

# ---------------------------
# Telegram notify
# ---------------------------
def send_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    if not token or not chat_id:
        print("[WARN] Telegram non configurato")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        import requests
        r = requests.post(url, json={"chat_id": chat_id, "text": msg,
                                     "disable_web_page_preview": False}, timeout=10)
        if r.status_code != 200:
            print(f"[WARN] Telegram HTTP {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[WARN] Telegram errore: {e}")

# ---------------------------
# Period helpers
# ---------------------------
def today_local_eu_rome():
    if ZoneInfo:
        return datetime.now(ZoneInfo("Europe/Rome"))
    return datetime.now()

def get_period_bounds(period: str, now_local: datetime):
    today = now_local.date()
    if period == "daily":
        start_day = end_day = today
        label = today.strftime("%Y-%m-%d")
    elif period == "weekly":
        start_day = today - timedelta(days=today.weekday())
        end_day = start_day + timedelta(days=6)
        label = f"{start_day.strftime('%Y-%m-%d')} → {end_day.strftime('%Y-%m-%d')}"
    elif period == "monthly":
        start_day = today.replace(day=1)
        if start_day.month == 12:
            next_month = date(start_day.year + 1, 1, 1)
        else:
            next_month = date(start_day.year, start_day.month + 1, 1)
        end_day = next_month - timedelta(days=1)
        label = start_day.strftime("%Y-%m")
    else:
        start_day = end_day = today
        label = today.strftime("%Y-%m-%d")
    return start_day.strftime("%Y-%m-%d"), end_day.strftime("%Y-%m-%d"), label

# ---------------------------
# Main
# ---------------------------
def main():
    ap = argparse.ArgumentParser(description="Pubblica report ADS-B come post Hugo")
    ap.add_argument("--period", choices=["daily", "weekly", "monthly"], default="daily")
    ap.add_argument("--slug", default="monitor-adsbfi-report")
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()

    now_local = today_local_eu_rome()
    pub_date_str = now_local.strftime("%Y-%m-%d")

    # --- Step 1: crea DB + importa CSV ---
    conn = connect_db()
    init_db(conn)
    import_csv_to_db(conn, CSV_FILE)

    # --- Step 2: query sul periodo ---
    start_day_str, end_day_str, label = get_period_bounds(args.period, now_local)
    rows = query_events_by_day_range(conn, start_day_str, end_day_str)
    conn.close()

    if not rows:
        print(f"[INFO] Nessun evento nel periodo {label}, nessun post generato.")
        return

    if args.limit and len(rows) > args.limit:
        rows = rows[-args.limit:]

    md_table = to_markdown(rows)

    # --- Titoli personalizzati ---
    if args.period == "daily":
        date_label = now_local.strftime("%d.%m.%y")
        title = f"Report voli con eventi di interesse su Grosseto e bassa Toscana {date_label}"
        period_intro = f"## Report giornaliero ({date_label})\n"
    elif args.period == "weekly":
        start = datetime.strptime(start_day_str, "%Y-%m-%d")
        end = datetime.strptime(end_day_str, "%Y-%m-%d")
        label_fmt = f"{start.strftime('%d.%m.%y')} → {end.strftime('%d.%m.%y')}"
        title = f"Report voli con eventi di interesse su Grosseto e bassa Toscana {label_fmt}"
        period_intro = f"## Report settimanale ({label_fmt})\n"
    else:  # monthly
        start = datetime.strptime(start_day_str, "%Y-%m-%d")
        label_fmt = start.strftime("%m.%Y")
        title = f"Report voli con eventi di interesse su Grosseto e bassa Toscana {label_fmt}"
        period_intro = f"## Report mensile ({label_fmt})\n"

    front_matter = format_front_matter(title, now_local)
    body_md = f"""{front_matter}
{period_intro}

{md_table}
"""

    filepath, filename = write_post(pub_date_str, args.slug, title, body_md)
    print(f"[INFO] Creato post {filepath}")

    subprocess.run(["hugo"], cwd=BLOG_PATH, check=False)

    year = pub_date_str[:4]
    post_url = f"{BASE_URL}/{year}/{filename.replace('.md','/')}"
    send_telegram(post_url)
    print(f"[INFO] Pubblicato link: {post_url}")

if __name__ == "__main__":
    main()

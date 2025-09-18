#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_fake_events.py — genera CSV di eventi fittizi per test.

Ora:
- PATTERN: LOOP/CERCHIO, TAGLIAERBA, MESH/RETICOLATO
- PROX: CLUSTER, INSEGUIMENTO
- ANOMALY: squawk 7500/7600/7700, GS alta/bassa, ALT alta/bassa,
           VS anomala, ΔGS anomalo
- I valori numerici (alt_ft, gs_kt, squawk) sono coerenti con l’anomalia
"""

import argparse
import csv
import random
import datetime as dt

PATTERNS = ["LOOP/CERCHIO", "TAGLIAERBA", "MESH/RETICOLATO"]
PROXES = ["CLUSTER", "INSEGUIMENTO"]

def rand_ts():
    return (dt.datetime.utcnow() - dt.timedelta(minutes=random.randint(0, 1440))).strftime("%Y-%m-%d %H:%M:%S UTC")

def make_event(ts, ev_type, note, alt_ft=None, gs_kt=None, squawk=None):
    return {
        "first_seen_utc": ts,
        "hex": f"{random.randint(0, 0xFFFFFF):06x}",
        "callsign": f"FLT{random.randint(100,999)}",
        "reg": f"REG{random.randint(100,999)}",
        "lat": round(random.uniform(35, 60), 6),
        "lon": round(random.uniform(5, 20), 6),
        "alt_ft": alt_ft if alt_ft is not None else random.randint(1000, 40000),
        "gs_kt": gs_kt if gs_kt is not None else random.randint(100, 500),
        "event_type": ev_type,
        "note": note,
        "squawk": squawk if squawk else ""
    }

def make_anomaly_event(ts):
    kind = random.choice(["squawk", "gs_high", "gs_low", "alt_high", "alt_low", "vs", "dgs"])
    if kind == "squawk":
        sq = random.choice(["7500", "7600", "7700"])
        return make_event(ts, "ANOMALY", f"SQUAWK {sq}",
                          alt_ft=random.randint(1000, 30000),
                          gs_kt=random.randint(200, 500),
                          squawk=sq)
    elif kind == "gs_high":
        return make_event(ts, "ANOMALY", "GS alta 720 kt", gs_kt=720)
    elif kind == "gs_low":
        return make_event(ts, "ANOMALY", "GS bassa 25 kt", gs_kt=25)
    elif kind == "alt_high":
        return make_event(ts, "ANOMALY", "ALT alta 65000 ft", alt_ft=65000)
    elif kind == "alt_low":
        return make_event(ts, "ANOMALY", "ALT bassa 150 ft", alt_ft=150)
    elif kind == "vs":
        return make_event(ts, "ANOMALY", "VS anomala 12000 fpm",
                          alt_ft=random.randint(1000, 20000),
                          gs_kt=random.randint(200, 400))
    elif kind == "dgs":
        return make_event(ts, "ANOMALY", "ΔGS anomalo +300 kt",
                          alt_ft=random.randint(10000, 25000),
                          gs_kt=random.randint(100, 700))

def generate_events(n=50):
    rows = []
    for _ in range(n):
        ts = rand_ts()
        choice = random.random()
        if choice < 0.4:  # 40% PATTERN
            note = random.choice(PATTERNS)
            rows.append(make_event(ts, "PATTERN", note))
        elif choice < 0.7:  # 30% PROX
            note = random.choice(PROXES)
            rows.append(make_event(ts, "PROX", note))
        else:  # 30% ANOMALY
            rows.append(make_anomaly_event(ts))
    return rows

def save_csv(rows, out_file):
    fieldnames = ["first_seen_utc", "hex", "callsign", "reg",
                  "lat", "lon", "alt_ft", "gs_kt", "event_type", "note", "squawk"]
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(rows)

def main():
    ap = argparse.ArgumentParser(description="Genera eventi fittizi ADS-B")
    ap.add_argument("out_csv", help="File CSV di output")
    ap.add_argument("--n", type=int, default=50, help="Numero di eventi da generare")
    args = ap.parse_args()

    rows = generate_events(args.n)
    save_csv(rows, args.out_csv)
    print(f"[INFO] Generati {len(rows)} eventi fittizi in {args.out_csv}")

if __name__ == "__main__":
    main()

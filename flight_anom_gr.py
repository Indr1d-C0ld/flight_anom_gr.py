#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor aerei con poligoni + rilevamento pattern, prossimità e anomalie operative.

Funzionalità:
 - Pattern: LOOP/CERCHIO, TAGLIAERBA, MESH/RETICOLATO (criteri stringenti).
 - Prossimità: CLUSTER (formazione), INSEGUIMENTO (fila indiana).
 - Anomalie operative:
      * SQUAWK emergenza: 7500 / 7600 / 7700
      * Velocità anomala (solo se non a terra)
      * Quota anomala (solo se non a terra)
      * Vertical speed anomala
      * ΔGS improvviso
 - Identificazione voli militari (dbFlags: military) → nota "mil".
 - Notifiche con riga MODEL (es. "AGUSTA AW-139") se disponibile.
 - Debounce sugli alert.
 - Registrazione su CSV + notifiche Telegram opzionali con link diretti.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import fcntl
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests

# ---------------------------
# Tiles Italia (fallback se non c’è un polygons-file)
# ---------------------------
TILES = [
    (45.5, 9.2, 250),   # Nord Italia, area Milano
    (44.5, 11.3, 200),  # Centro-Nord, area Bologna
    (43.8, 11.3, 200),  # Centro, area Firenze
    (41.9, 12.5, 200),  # Roma e Lazio
    (40.8, 14.3, 200),  # Sud, area Napoli
    (39.2, 9.1, 250),   # Sardegna
    (38.1, 15.6, 250),  # Sicilia, area Messina
    (37.5, 13.4, 200),  # Sicilia, area Palermo
]

API_TEMPLATE = "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{rng}"
API_MIL = "https://opendata.adsb.fi/api/v2/mil"

HTTP_TIMEOUT = 15
HTTP_RETRIES = 2
HTTP_BACKOFF = 2.0

# Soglie anomalie default
DEF_MAX_GS_KT = 650
DEF_MIN_GS_KT = 35
DEF_MIN_ALT_FT = 500
DEF_MAX_ALT_FT = 60000
DEF_MAX_VS_FPM = 8000
DEF_MAX_DGS_KTS = 250

# ---------------------------
# Dataclasses
# ---------------------------
@dataclass
class Aircraft:
    hex: str
    flight: str
    lat: Optional[float]
    lon: Optional[float]
    alt_baro: Optional[int]
    gs: Optional[float]
    ts: Optional[float]
    reg: Optional[str] = None
    squawk: Optional[str] = None
    ground: Optional[bool] = None
    model_desc: Optional[str] = None   # esteso (desc)
    model_t: Optional[str] = None      # sigla breve (t)
    is_mil: bool = False

# ---------------------------
# Funzioni utili
# ---------------------------
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

def safe_bool(val) -> Optional[bool]:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None

def now_utc_str() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def haversine_km(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    R = 6371.0
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def heading(p1: Tuple[float, float], p2: Tuple[float, float]) -> Optional[float]:
    dy = p2[0] - p1[0]
    dx = p2[1] - p1[1]
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360

def angle_diff_deg(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d

def model_line(ac: Aircraft) -> Optional[str]:
    if ac.model_desc:
        return f"MODEL: {ac.model_desc}"
    if ac.model_t:
        return f"MODEL: {ac.model_t}"
    return None

# ---------------------------
# Fetch dei voli militari
# ---------------------------
def fetch_military() -> List[dict]:
    api_rate_guard()
    last_exc = None
    for attempt in range(HTTP_RETRIES + 1):
        try:
            r = requests.get(API_MIL, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            raw = r.json() or {}
            # Debug non bloccante
            print("[DEBUG] Risposta /v2/mil:", r.text[:500], file=sys.stderr)

            if isinstance(raw, dict) and "ac" in raw:
                data = raw["ac"]
            elif isinstance(raw, dict) and "aircraft" in raw:
                data = raw["aircraft"]
            elif isinstance(raw, list):
                data = raw
            else:
                return []

            # Forza flag militare
            for ac in data:
                if isinstance(ac, dict):
                    ac["force_mil"] = True
            return data
        except Exception as e:
            last_exc = e
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_BACKOFF * (attempt + 1))
    print(f"[WARN] Fetch militare fallito {API_MIL} — {last_exc}", file=sys.stderr)
    return []

# ---------------------------
# GeoJSON loader & poligoni
# ---------------------------
def load_polygons_from_geojson(path: str) -> List[List[List[Tuple[float, float]]]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    polys = []
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        for feat in data.get("features", []):
            geom = feat.get("geometry", {})
            gtype = geom.get("type")
            coords = geom.get("coordinates", [])
            if gtype == "Polygon":
                polys.append([[(float(pt[1]), float(pt[0])) for pt in ring] for ring in coords])
            elif gtype == "MultiPolygon":
                for polycoords in coords:
                    polys.append([[(float(pt[1]), float(pt[0])) for pt in ring] for ring in polycoords])
    elif isinstance(data, dict) and "polygons" in data:
        for poly in data["polygons"]:
            polys.append([[(float(pt[0]), float(pt[1])) for pt in ring] for ring in poly])
    return polys

def point_in_ring(point: Tuple[float, float], ring: List[Tuple[float, float]]) -> bool:
    x, y = point[1], point[0]
    inside = False
    n = len(ring)
    for i in range(n):
        yi, xi = ring[i][0], ring[i][1]
        yj, xj = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
    return inside

def point_in_polygon(point: Tuple[float, float], polygon: List[List[Tuple[float, float]]]) -> bool:
    if not polygon:
        return False
    if not point_in_ring(point, polygon[0]):
        return False
    for hole in polygon[1:]:
        if point_in_ring(point, hole):
            return False
    return True

def in_any_polygon(lat: Optional[float], lon: Optional[float],
                   polygons: Iterable[List[List[Tuple[float, float]]]]) -> bool:
    if lat is None or lon is None:
        return False
    pt = (lat, lon)
    return any(point_in_polygon(pt, poly) for poly in polygons)

# ---------------------------
# Link generator
# ---------------------------
def make_links(ac: Aircraft) -> List[str]:
    links = []
    if ac.hex:
        links.append(f"[ADSB.fi](https://globe.adsb.fi/?icao={ac.hex})")
        links.append(f"[ADSB Exchange](https://globe.adsbexchange.com/?icao={ac.hex})")
        links.append(f"[Planespotters](https://www.planespotters.net/hex/{ac.hex})")
    if ac.flight:
        links.append(f"[FlightAware](https://www.flightaware.com/it-IT/flight/{ac.flight})")
    if ac.reg:
        links.append(f"[AirHistory](https://www.airhistory.net/marks-all/{ac.reg})")
        links.append(f"[JetPhotos](https://www.jetphotos.com/registration/{ac.reg})")
    return links

# ---------------------------
# Rate limiting (lockfile locale, 1 req/s)
# ---------------------------
def api_rate_guard():
    lockfile = "/tmp/adsbfi_api.lock"
    with open(lockfile, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            last = float(f.read().strip())
        except Exception:
            last = 0.0
        now = time.time()
        delta = now - last
        if delta < 1.05:
            time.sleep(1.05 - delta)
        f.seek(0)
        f.truncate()
        f.write(str(time.time()))
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)

# ---------------------------
# Fetch dati
# ---------------------------
def fetch_tile(lat: float, lon: float, rng_nm: int) -> List[dict]:
    api_rate_guard()
    url = API_TEMPLATE.format(lat=lat, lon=lon, rng=rng_nm)
    last_exc = None
    for attempt in range(HTTP_RETRIES + 1):
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json().get("aircraft", []) or []
        except Exception as e:
            last_exc = e
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_BACKOFF * (attempt + 1))
    print(f"[WARN] Fetch fallito {url} — {last_exc}", file=sys.stderr)
    return []

# ---------------------------
# Pattern detection migliorata
# ---------------------------
def detect_loop_or_racetrack(track: List[Tuple[float, float]],
                             loop_close_km: float = 3.0,
                             min_points: int = 30,
                             min_span_km: float = 10.0,
                             min_laps: int = 2) -> Optional[str]:
    if len(track) < min_points:
        return None

    dist_start_end = haversine_km(track[0], track[-1])
    if dist_start_end > loop_close_km:
        return None

    lats = [p[0] for p in track]
    lons = [p[1] for p in track]
    span_lat = haversine_km((min(lats), min(lons)), (max(lats), min(lons)))
    span_lon = haversine_km((min(lats), min(lons)), (min(lats), max(lons)))
    major = max(span_lat, span_lon)
    minor = min(span_lat, span_lon)

    if major < min_span_km or minor < 2:
        return None

    aspect_ratio = major / (minor + 1e-6)
    shape = "LOOP/CERCHIO" if aspect_ratio < 1.5 else "RACETRACK"

    crossings = 0
    mid_lat = (max(lats) + min(lats)) / 2
    for i in range(len(track) - 1):
        if (track[i][0] - mid_lat) * (track[i+1][0] - mid_lat) < 0:
            crossings += 1
    if crossings < min_laps * 2:
        return None

    return shape

def detect_lawnmower(track: List[Tuple[float, float]],
                     min_points: int = 14,
                     heading_tolerance: float = 15.0,
                     required_passes: int = 4,
                     min_span_km: float = 15.0) -> bool:
    if len(track) < min_points:
        return False

    lats = [p[0] for p in track]
    lons = [p[1] for p in track]
    span = haversine_km((min(lats), min(lons)), (max(lats), max(lons)))
    if span < min_span_km:
        return False

    heads = []
    for i in range(len(track) - 1):
        h = heading(track[i], track[i+1])
        if h is None:
            continue
        heads.append(h % 180)
    if not heads:
        return False

    clusters = [[], []]
    base = min(heads, key=lambda x: sum(angle_diff_deg(x, y) for y in heads))
    for h in heads:
        if angle_diff_deg(h, base) < heading_tolerance:
            clusters[0].append(h)
        elif angle_diff_deg((h+180) % 180, base) < heading_tolerance:
            clusters[1].append(h)

    if len(clusters[0]) < required_passes or len(clusters[1]) < required_passes:
        return False

    sequence = []
    for h in heads:
        if angle_diff_deg(h, base) < heading_tolerance:
            sequence.append("A")
        elif angle_diff_deg((h+180) % 180, base) < heading_tolerance:
            sequence.append("B")
    alternations = sum(1 for i in range(1, len(sequence)) if sequence[i] != sequence[i-1])

    return alternations >= (required_passes - 1)

def detect_mesh(track: List[Tuple[float, float]],
                min_points: int = 40,
                perpendicular_tolerance: float = 10.0,
                min_crossings: int = 6,
                min_family_ratio: float = 0.25) -> bool:
    if len(track) < min_points:
        return False

    heads = [heading(track[i], track[i+1]) for i in range(len(track)-1)]
    heads = [int(round((h or 0)/10.0)*10) % 180 for h in heads if h is not None]
    if not heads:
        return False

    uniq = sorted(set(heads))
    pairs = [(a, b) for a in uniq for b in uniq
             if abs(((a-b)+180)%180 - 90) <= perpendicular_tolerance]
    if not pairs:
        return False

    def family(h, a, b, tol):
        if abs(((h-a)+180)%180) <= tol:
            return "A"
        if abs(((h-b)+180)%180) <= tol:
            return "B"
        return None

    a, b = pairs[0]
    fam_counts = {"A": 0, "B": 0}
    crossings = 0
    last = None

    for h in heads:
        f = family(h, a, b, perpendicular_tolerance)
        if f:
            fam_counts[f] += 1
            if f != last:
                crossings += 1
                last = f

    total = fam_counts["A"] + fam_counts["B"]
    if total == 0:
        return False
    if fam_counts["A"]/total < min_family_ratio or fam_counts["B"]/total < min_family_ratio:
        return False

    return crossings >= min_crossings

# ---------------------------
# Anomaly detection (fix GS/ALT a terra)
# ---------------------------
def detect_anomalies(ac: Aircraft, prev: Optional[Aircraft], dt_sec: Optional[float],
                     min_alt_ft: int, max_alt_ft: int,
                     min_gs_kt: float, max_gs_kt: float,
                     max_vs_fpm: float, max_dgs_kts: float) -> List[str]:
    seen = set()

    is_ground = False
    if ac.ground is True:
        is_ground = True
    elif ac.alt_baro is not None and ac.alt_baro <= 100 and (ac.gs is None or ac.gs < 60):
        is_ground = True
    elif ac.alt_baro is not None and ac.alt_baro <= 0:
        is_ground = True

    if ac.squawk and str(ac.squawk).strip() in {"7500", "7600", "7700"}:
        seen.add(f"SQUAWK: #{ac.squawk}")

    if ac.gs is not None:
        if ac.gs > max_gs_kt:
            seen.add(f"GS alta: {ac.gs:.0f} kt")
        elif ac.gs < min_gs_kt and not is_ground:
            seen.add(f"GS bassa: {ac.gs:.0f} kt")

    if ac.alt_baro is not None:
        if ac.alt_baro > max_alt_ft:
            seen.add(f"ALT alta: {ac.alt_baro} ft")
        elif ac.alt_baro < min_alt_ft and not is_ground and ac.alt_baro > 0:
            seen.add(f"ALT bassa: {ac.alt_baro} ft")

    if prev and dt_sec and dt_sec > 0:
        if ac.gs is not None and prev.gs is not None:
            dgs = ac.gs - prev.gs
            if abs(dgs) > max_dgs_kts:
                seen.add(f"ΔGS anomalo: {dgs:+.0f} kt")
        if ac.alt_baro is not None and prev.alt_baro is not None:
            vs_fpm = ((ac.alt_baro - prev.alt_baro) / dt_sec) * 60.0
            if abs(vs_fpm) > max_vs_fpm:
                seen.add(f"VS anomala: {vs_fpm:.0f} fpm")

    return sorted(seen)

# ---------------------------
# CSV & Telegram
# ---------------------------
def append_seen_csv(csv_path: str, rows: List[dict]) -> None:
    must_write_header = not os.path.isfile(csv_path)
    fieldnames = [
        "first_seen_utc", "hex", "callsign", "reg",
        "model_t",
        "lat", "lon", "alt_ft", "gs_kt", "squawk", "ground",
        "event_type", "note"
    ]
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=fieldnames)
            if must_write_header:
                wr.writeheader()
            wr.writerows(rows)
    except Exception as e:
        print(f"[WARN] Scrittura CSV fallita: {e}", file=sys.stderr)

def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": chat_id, "text": text,
                  "disable_web_page_preview": False,
                  "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception:
        pass

# ---------------------------
# Helper prossimità / formazione
# ---------------------------
def same_direction(h1: Optional[float], h2: Optional[float], tol_deg: float) -> bool:
    if h1 is None or h2 is None:
        return False
    return angle_diff_deg(h1, h2) <= tol_deg

def approx_following(p_lead: Tuple[float, float], h_lead: Optional[float],
                     p_trail: Tuple[float, float], h_trail: Optional[float],
                     tol_deg: float) -> bool:
    if h_lead is None or h_trail is None:
        return False
    if angle_diff_deg(h_lead, h_trail) > tol_deg:
        return False
    bt = heading(p_lead, p_trail)
    if bt is None:
        return False
    return angle_diff_deg((h_lead + 180.0) % 360.0, bt) <= tol_deg

# ---------------------------
# Main
# ---------------------------
def main():
    ap = argparse.ArgumentParser(description="Monitor ADS-B con pattern/prox/anomalie")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--csv", type=str, default="/home/pi/flight_anom_gr/events.csv")
    ap.add_argument("--notify-telegram", action="store_true")
    ap.add_argument("--polygons-file", type=str)

    # soglie anomalie operative
    ap.add_argument("--min-alt-ft", type=int, default=DEF_MIN_ALT_FT)
    ap.add_argument("--max-alt-ft", type=int, default=DEF_MAX_ALT_FT)
    ap.add_argument("--min-gs-kt", type=float, default=DEF_MIN_GS_KT)
    ap.add_argument("--max-gs-kt", type=float, default=DEF_MAX_GS_KT)
    ap.add_argument("--max-vs-fpm", type=float, default=DEF_MAX_VS_FPM)
    ap.add_argument("--max-dgs-kts", type=float, default=DEF_MAX_DGS_KTS)

    # prossimità
    ap.add_argument("--proximity-km", type=float, default=3.0)
    ap.add_argument("--prox_angle_deg", type=float, default=20.0)
    ap.add_argument("--prox_alt_diff_ft", type=float, default=500.0)
    ap.add_argument("--prox_gs_diff_kt", type=float, default=40.0)

    # cooldown alert
    ap.add_argument("--anomaly-cooldown", type=int, default=300)
    ap.add_argument("--pattern-cooldown", type=int, default=900)
    ap.add_argument("--prox-cooldown", type=int, default=600)
    ap.add_argument("--mil-cooldown", type=int, default=1800)

    # pattern params
    ap.add_argument("--loop-min-points", type=int, default=30)
    ap.add_argument("--loop-close-km", type=float, default=3.0)
    ap.add_argument("--loop-min-span-km", type=float, default=10.0)
    ap.add_argument("--loop-min-laps", type=int, default=2)

    ap.add_argument("--lawn-min-points", type=int, default=14)
    ap.add_argument("--lawn-heading-tol", type=float, default=15.0)
    ap.add_argument("--lawn-required-passes", type=int, default=4)
    ap.add_argument("--lawn-min-span-km", type=float, default=15.0)

    ap.add_argument("--mesh-min-points", type=int, default=25)
    ap.add_argument("--mesh-perp-tol", type=float, default=15.0)
    ap.add_argument("--mesh-min-crossings", type=int, default=3)

    args = ap.parse_args()

    polygons = load_polygons_from_geojson(args.polygons_file) if args.polygons_file else []

    # storici e cooldown
    track_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=120))
    prev_state: Dict[str, Aircraft] = {}
    last_anom_alert: Dict[str, float] = {}
    last_pattern_alert: Dict[Tuple[str, str], float] = {}
    last_prox_alert: Dict[Tuple[str, str, str], float] = {}
    last_mil_alert: Dict[str, float] = {}

    print(f"Monitor aerei — start {now_utc_str()}")
    while True:
        t0 = time.time()
        merged: List[dict] = []
        for (lat, lon, rng) in TILES:
            merged += fetch_tile(lat, lon, rng)

        merged += fetch_military()

        aircraft: List[Aircraft] = []
        for ac in merged:
            try:
                aircraft.append(
                    Aircraft(
                        (ac.get("hex") or "").lower(),
                        (ac.get("flight") or "").strip(),
                        safe_float(ac.get("lat")),
                        safe_float(ac.get("lon")),
                        safe_int(ac.get("alt_baro")),
                        safe_float(ac.get("gs")),
                        safe_float(ac.get("seen_pos_timestamp") or ac.get("seen_timestamp")),
                        (ac.get("r") or ac.get("reg") or "").strip() or None,
                        str(ac.get("squawk")).strip() if ac.get("squawk") else None,
                        safe_bool(ac.get("ground")),
                        (ac.get("desc") or None),
                        (ac.get("t") or None),
                        bool(
                            ac.get("force_mil") or
                            ac.get("military") or
                            ac.get("isMil") or
                            ac.get("mil") or
                            ("military" in str(ac.get("dbFlags") or "").lower())
                        )
                    )
                )
            except Exception:
                continue

        if polygons:
            aircraft = [ac for ac in aircraft if in_any_polygon(ac.lat, ac.lon, polygons)]

        now_str = now_utc_str()
        event_rows: List[dict] = []

        # ---------------- TRACK & PATTERN ----------------
        for ac in aircraft:
            if ac.lat is None or ac.lon is None:
                continue
            track_history[ac.hex].append((ac.lat, ac.lon))
            track = list(track_history[ac.hex])

            pattern = None
            loop_type = detect_loop_or_racetrack(
                track,
                min_points=args.loop_min_points,
                loop_close_km=args.loop_close_km,
                min_span_km=args.loop_min_span_km,
                min_laps=args.loop_min_laps
            )
            if loop_type:
                pattern = loop_type
            elif detect_lawnmower(track,
                                  min_points=args.lawn_min_points,
                                  heading_tolerance=args.lawn_heading_tol,
                                  required_passes=args.lawn_required_passes,
                                  min_span_km=args.lawn_min_span_km):
                pattern = "TAGLIAERBA"
            elif detect_mesh(track,
                             min_points=args.mesh_min_points,
                             perpendicular_tolerance=args.mesh_perp_tol,
                             min_crossings=args.mesh_min_crossings):
                pattern = "MESH/RETICOLATO"

            if pattern:
                key = (ac.hex, pattern)
                now_ts = time.time()
                if now_ts - last_pattern_alert.get(key, 0) >= args.pattern_cooldown:
                    row = {
                        "first_seen_utc": now_str, "hex": ac.hex,
                        "callsign": ac.flight, "reg": ac.reg or "",
                        "model_t": ac.model_t or "",
                        "lat": ac.lat, "lon": ac.lon,
                        "alt_ft": ac.alt_baro or "", "gs_kt": ac.gs or "",
                        "squawk": ac.squawk or "", "ground": ac.ground,
                        "event_type": "PATTERN", "note": pattern
                    }
                    event_rows.append(row)

                    msg_lines = [
                        "PATTERN",
                        pattern,
                        f"HEX: #{ac.hex}",
                        f"FLT: #{ac.flight or '-'}"
                    ]
                    if ac.reg:
                        msg_lines.append(f"REG: #{ac.reg}")
                    ml = model_line(ac)
                    if ml:
                        msg_lines.append(ml)
                    links = make_links(ac)
                    if links:
                        msg_lines.append("")
                        msg_lines.extend(links)
                    msg = "\n".join(msg_lines)

                    print(msg)
                    if args.notify_telegram:
                        send_telegram(msg)
                    last_pattern_alert[key] = now_ts

        # ---------------- PROX (formazione / inseguimento) ----------------
        cur_head: Dict[str, Optional[float]] = {}
        for ac in aircraft:
            th = track_history[ac.hex]
            cur_head[ac.hex] = heading(th[-2], th[-1]) if len(th) >= 2 else None

        for i, ac1 in enumerate(aircraft):
            if not (ac1.lat and ac1.lon):
                continue
            p1 = (ac1.lat, ac1.lon)
            h1 = cur_head.get(ac1.hex)

            for j in range(i+1, len(aircraft)):
                ac2 = aircraft[j]
                if not (ac2.lat and ac2.lon):
                    continue
                if ac1.hex == ac2.hex:
                    continue

                p2 = (ac2.lat, ac2.lon)
                h2 = cur_head.get(ac2.hex)
                dist = haversine_km(p1, p2)
                if dist >= args.proximity_km:
                    continue

                alt_ok = (ac1.alt_baro is not None and ac2.alt_baro is not None and
                          abs(ac1.alt_baro - ac2.alt_baro) <= args.prox_alt_diff_ft)
                gs_ok = (ac1.gs is not None and ac2.gs is not None and
                         abs(ac1.gs - ac2.gs) <= args.prox_gs_diff_kt)
                dir_ok = same_direction(h1, h2, args.prox_angle_deg)

                if not (alt_ok and gs_ok and dir_ok):
                    continue

                label = "CLUSTER"
                if approx_following(p_lead=p1, h_lead=h1, p_trail=p2, h_trail=h2, tol_deg=args.prox_angle_deg) \
                   or approx_following(p_lead=p2, h_lead=h2, p_trail=p1, h_trail=h1, tol_deg=args.prox_angle_deg):
                    label = "INSEGUIMENTO"

                key = tuple(sorted([ac1.hex, ac2.hex]) + [label])
                now_ts = time.time()
                if now_ts - last_prox_alert.get(key, 0) < args.prox_cooldown:
                    continue

                row1 = {"first_seen_utc": now_str, "hex": ac1.hex, "callsign": ac1.flight,
                        "reg": ac1.reg or "", "model_t": ac1.model_t or "",
                        "lat": ac1.lat, "lon": ac1.lon,
                        "alt_ft": ac1.alt_baro or "", "gs_kt": ac1.gs or "",
                        "squawk": ac1.squawk or "", "ground": ac1.ground,
                        "event_type": "PROX", "note": f"{label}; peer={ac2.hex}; dist={dist:.1f} km"}
                row2 = {"first_seen_utc": now_str, "hex": ac2.hex, "callsign": ac2.flight,
                        "reg": ac2.reg or "", "model_t": ac2.model_t or "",
                        "lat": ac2.lat, "lon": ac2.lon,
                        "alt_ft": ac2.alt_baro or "", "gs_kt": ac2.gs or "",
                        "squawk": ac2.squawk or "", "ground": ac2.ground,
                        "event_type": "PROX", "note": f"{label}; peer={ac1.hex}; dist={dist:.1f} km"}
                event_rows += [row1, row2]

                msg_lines = [
                    "PROX",
                    label,
                    f"HEX: #{ac1.hex}",
                    f"FLT: #{ac1.flight or '-'}"
                ]
                if ac1.reg:
                    msg_lines.append(f"REG: #{ac1.reg}")
                ml1 = model_line(ac1)
                if ml1:
                    msg_lines.append(ml1)

                if label == "CLUSTER":
                    msg_lines.append(f"Vicino a: #{ac2.hex} ({dist:.1f} km)")
                else:
                    msg_lines.append(f"Inseguendo: #{ac2.hex} ({dist:.1f} km)")

                links = make_links(ac1)
                if links:
                    msg_lines.append("")
                    msg_lines.extend(links)
                msg = "\n".join(msg_lines)

                print(msg)
                if args.notify_telegram:
                    send_telegram(msg)
                last_prox_alert[key] = now_ts

        # ---------------- ANOMALY ----------------
        for ac in aircraft:
            prev = prev_state.get(ac.hex)
            dt_sec = None
            if prev and ac.ts and prev.ts:
                try:
                    dt_sec = max(0.0, float(ac.ts) - float(prev.ts))
                except Exception:
                    dt_sec = None

            anomalies = detect_anomalies(
                ac, prev, dt_sec,
                args.min_alt_ft, args.max_alt_ft,
                args.min_gs_kt, args.max_gs_kt,
                args.max_vs_fpm, args.max_dgs_kts
            )
            if anomalies:
                now_ts = time.time()
                if now_ts - last_anom_alert.get(ac.hex, 0) >= args.anomaly_cooldown:
                    msg_lines = [
                        "ANOMALY",
                        f"HEX: #{ac.hex}",
                        f"FLT: #{ac.flight or '-'}"
                    ]
                    if ac.reg:
                        msg_lines.append(f"REG: #{ac.reg}")
                    ml = model_line(ac)
                    if ml:
                        msg_lines.append(ml)
                    for an in anomalies:
                        msg_lines.append(an)
                    links = make_links(ac)
                    if links:
                        msg_lines.append("")
                        msg_lines.extend(links)
                    msg = "\n".join(msg_lines)

                    row = {
                        "first_seen_utc": now_str, "hex": ac.hex,
                        "callsign": ac.flight, "reg": ac.reg or "",
                        "model_t": ac.model_t or "",
                        "lat": ac.lat or "", "lon": ac.lon or "",
                        "alt_ft": ac.alt_baro or "", "gs_kt": ac.gs or "",
                        "squawk": ac.squawk or "", "ground": ac.ground,
                        "event_type": "ANOMALY", "note": "; ".join(anomalies)
                    }
                    event_rows.append(row)

                    print(msg)
                    if args.notify_telegram:
                        send_telegram(msg)
                    last_anom_alert[ac.hex] = now_ts

            prev_state[ac.hex] = ac

        # ---------------- MIL (endpoint dedicato) ----------------
        for ac in aircraft:
            if not ac.is_mil:
                continue

            now_ts = time.time()
            if now_ts - last_mil_alert.get(ac.hex, 0) < args.mil_cooldown:
                continue

            row = {
                "first_seen_utc": now_str, "hex": ac.hex,
                "callsign": ac.flight, "reg": ac.reg or "",
                "model_t": ac.model_t or "",
                "lat": ac.lat or "", "lon": ac.lon or "",
                "alt_ft": ac.alt_baro or "", "gs_kt": ac.gs or "",
                "squawk": ac.squawk or "", "ground": ac.ground,
                "event_type": "MIL", "note": "MIL"
            }
            event_rows.append(row)

            msg_lines = [
                "MIL",
                f"HEX: #{ac.hex}",
                f"FLT: #{ac.flight or '-'}"
            ]
            if ac.reg:
                msg_lines.append(f"REG: #{ac.reg}")
            ml = model_line(ac)
            if ml:
                msg_lines.append(ml)
            msg_lines.append("Flag: military")

            links = make_links(ac)
            if links:
                msg_lines.append("")
                msg_lines.extend(links)
            msg = "\n".join(msg_lines)

            print(msg)
            if args.notify_telegram:
                send_telegram(msg)
            last_mil_alert[ac.hex] = now_ts

        # ---------------- Persistenza CSV ----------------
        if event_rows:
            append_seen_csv(args.csv, event_rows)

        # ciclo
        time.sleep(max(1, int(round(args.interval - (time.time() - t0)))))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[INFO] Interrotto dall'utente.")

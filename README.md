✈️ Flight Anomalies & Pattern Monitor

Un sistema completo per il monitoraggio del traffico aereo ADS-B, con particolare attenzione a:

rilevamento pattern di volo insoliti (loop, cerchi, “tagliaerba”, mesh/reticolati);

individuazione di prossimità anomale (cluster e inseguimenti);

segnalazione di anomalie operative (squawk d’emergenza, velocità/altitudine fuori soglia, variazioni improvvise);

identificazione di voli militari tramite endpoint dedicato.

Gli eventi rilevati vengono salvati in CSV/SQLite, possono essere esplorati via CLI, esportati, pubblicati in report Markdown (con integrazione Hugo + Telegram) e testati con generatori di dati fittizi.

---

🔍 Scopo del progetto

Fornire uno strumento OSINT leggero e personalizzabile per appassionati e ricercatori.

Automatizzare la raccolta di eventi “anomali” su aree geografiche definite da poligoni (es. zone di interesse o sorvolo sensibile).

Creare una base dati storica (SQLite) interrogabile e riutilizzabile.

Integrare i report con workflow già esistenti: blog Hugo, notifiche Telegram, esportazioni CSV.

---

📂 Componenti principali

- flight_anom_gr.py: Core monitor: si connette alle API adsb.fi, applica filtri su poligoni, rileva pattern, prossimità, anomalie operative e voli militari.
    → Output su CSV, con notifiche Telegram opzionali.

- publish_adsb_report.py: Importa gli eventi in SQLite, genera report giornalieri/settimanali/mensili in Markdown, pubblica automaticamente su blog Hugo e invia link su Telegram.

- events_cli.py: Interfaccia a riga di comando interattiva per esplorare gli eventi nel DB (con filtri per tipo, HEX, callsign, date).

- import_events.py: Importa CSV esterni in SQLite, mantenendo schema e compatibilità.

- generate_fake_events.py: Generatore di dataset fittizi (PATTERN, PROX, ANOMALY) utile per test e demo.

- polygons.json: File GeoJSON di esempio per delimitare un’area di monitoraggio.

---

⚙️ Utilizzo rapido

Avviare il monitoraggio:
./flight_anom_gr.py --interval 60 --csv ./events.csv --notify-telegram --polygons-file ./polygons.json

Esplorare gli eventi da CLI:
./events_cli.py

Esportare in CSV filtrato:
./events_export.py out.csv --event-type ANOMALY

Importare in DB:
./import_events.py events.csv

Generare report per blog:
./publish_adsb_report.py --period weekly

Creare dati fittizi (per test):
./generate_fake_events.py test.csv --n 100

---

📊 Eventi supportati

PATTERN: LOOP/CERCHIO, TAGLIAERBA, MESH/RETICOLATO

PROX: CLUSTER, INSEGUIMENTO

ANOMALY: squawk 7500/7600/7700, GS/ALT anomale, vertical speed, variazioni improvvise

MIL: flag su traffico militare

---

🚀 Requisiti

Python 3.8+

Moduli: requests, sqlite3, prompt_toolkit

Facoltativo: token e chat ID Telegram per notifiche automatiche

Hugo (per generazione blog post)

---

📌 Note

Il progetto è pensato per uso personale, educativo e di ricerca OSINT.

Non sostituisce sistemi di monitoraggio ufficiali né fornisce garanzie su completezza e accuratezza dei dati.

Le API utilizzate (adsb.fi) hanno rate-limit: lo script include meccanismi di lockfile locale per rispettarli.

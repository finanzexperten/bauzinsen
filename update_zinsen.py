#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ruft die aktuelle EZB-Renditekurve (AAA-Staatsanleihen Euroraum, 10 Jahre) ab
und rechnet daraus Richtwerte fuer Bauzinsen je Zinsbindung + eine taegliche
Zins-Historie (~12 Monate) fuer den Verlaufs-Chart. Ergebnis: bauzinsen.json
Nur Python-Standardbibliothek. Wird von GitHub Actions taeglich ausgefuehrt.

MODELL: Anker ist die 10-Jahres-Rendite. Effektiver Bauzins 10J = 10J-Rendite +
BASE_SPREAD. Andere Bindungen bekommen einen festen Zu-/Abschlag (TERM). Die
Historie enthaelt denselben 10J-Bauzins Tag fuer Tag (gleiche Basis wie die Kacheln).

>>> STELLSCHRAUBEN FUER LAIEN <<<
- BASE_SPREAD  : verschiebt ALLE Zinsen nach oben/unten (Euer Top-Zins-Niveau).
- TERM         : Kurvenform, also wie viel mehr laengere Bindungen kosten.
- HISTORY_N    : Anzahl Boersentage im Verlaufs-Chart (260 ~ 1 Jahr).
"""
import json, urllib.request, datetime, sys, csv, io

BASE_SPREAD = 0.59
TERM = {5: -0.02, 10: 0.00, 15: 0.24, 20: 0.37}
SOLL_ABSCHLAG = 0.07
HISTORY_N = 260

KEY_10Y = "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
BASE_URL = "https://data-api.ecb.europa.eu/service/data/YC/{key}?lastNObservations={n}&format=csvdata"
MONATE = ["Januar","Februar","Maerz","April","Mai","Juni","Juli",
          "August","September","Oktober","November","Dezember"]

def fetch_series(n):
    url = BASE_URL.format(key=KEY_10Y, n=n)
    req = urllib.request.Request(url, headers={"User-Agent": "finanzexperten-bauzins/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        text = r.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for row in rows:
        try:
            out.append((row["TIME_PERIOD"], float(row["OBS_VALUE"])))
        except Exception:
            pass
    if not out:
        raise ValueError("keine Datenpunkte")
    out.sort(key=lambda x: x[0])   # chronologisch
    return out

def stand_deutsch(iso):
    try:
        d = datetime.date.fromisoformat(iso)
    except Exception:
        d = datetime.date.today()
    return "%d. %s %d" % (d.day, MONATE[d.month-1], d.year)

def main():
    series = fetch_series(HISTORY_N)
    last_date, y10 = series[-1]
    eff10 = y10 + BASE_SPREAD

    base = {}
    for years, prem in TERM.items():
        eff = round(eff10 + prem, 2)
        soll = round(eff - SOLL_ABSCHLAG, 2)
        base[str(years)] = {"soll": soll, "eff": eff}

    history = [{"d": d, "v": round(v + BASE_SPREAD + TERM[10], 2)} for (d, v) in series]

    out = {"stand": stand_deutsch(last_date), "live": True, "base": base, "history": history}
    with open("bauzinsen.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("OK (10J-Rendite %.2f%%, %d Verlaufs-Punkte):" % (y10, len(history)), base)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fehler beim Abruf:", e, file=sys.stderr)
        sys.exit(1)

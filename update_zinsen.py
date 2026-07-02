#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ruft die aktuelle EZB-Renditekurve (AAA-Staatsanleihen Euroraum) ab und
rechnet daraus Richtwerte fuer Bauzinsen je Zinsbindung. Ergebnis: bauzinsen.json
Nur Python-Standardbibliothek. Wird von GitHub Actions taeglich ausgefuehrt.

>>> EINZIGE STELLSCHRAUBE FUER LAIEN: SPREAD unten. <<<
SPREAD = Aufschlag in Prozentpunkten von der Staatsanleihen-Rendite auf den
effektiven Bauzins (bei 60 % Beleihung, sehr gute Bonitaet).
"""
import json, urllib.request, datetime, sys, csv, io

SPREAD = {5: 0.95, 10: 1.00, 15: 1.15, 20: 1.25}
SOLL_ABSCHLAG = 0.07

SERIES = {
    5:  "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_5Y",
    10: "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y",
    15: "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_15Y",
    20: "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_20Y",
}
BASE = "https://data-api.ecb.europa.eu/service/data/YC/{key}?lastNObservations=1&format=csvdata"
MONATE = ["Januar","Februar","Maerz","April","Mai","Juni","Juli",
          "August","September","Oktober","November","Dezember"]

def parse_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("leere Antwort")
    last = rows[-1]
    return float(last["OBS_VALUE"]), last.get("TIME_PERIOD", "")

def fetch_yield(key):
    url = BASE.format(key=key)
    req = urllib.request.Request(url, headers={"User-Agent": "finanzexperten-bauzins/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8")
    return parse_csv(text)

def stand_deutsch(iso):
    try:
        d = datetime.date.fromisoformat(iso)
    except Exception:
        d = datetime.date.today()
    return "%d. %s %d" % (d.day, MONATE[d.month-1], d.year)

def main():
    base, datum_iso = {}, ""
    for years, key in SERIES.items():
        y, iso = fetch_yield(key)
        if years == 10:
            datum_iso = iso
        eff = round(y + SPREAD[years], 2)
        soll = round(eff - SOLL_ABSCHLAG, 2)
        base[str(years)] = {"soll": soll, "eff": eff}
    out = {"stand": stand_deutsch(datum_iso), "live": True, "base": base}
    with open("bauzinsen.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("OK:", json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fehler beim Abruf:", e, file=sys.stderr)
        sys.exit(1)

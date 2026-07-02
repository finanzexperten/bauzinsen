#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ruft die aktuelle EZB-Renditekurve (AAA-Staatsanleihen Euroraum, 10 Jahre) ab
und rechnet daraus Richtwerte fuer Bauzinsen je Zinsbindung. Ergebnis: bauzinsen.json
Nur Python-Standardbibliothek. Wird von GitHub Actions taeglich ausgefuehrt.

MODELL: Anker ist die 10-Jahres-Rendite. Der effektive Bauzins fuer 10 Jahre =
10J-Rendite + BASE_SPREAD. Andere Bindungen bekommen einen festen Zu-/Abschlag
(TERM) obendrauf. So bleibt die Zinskurve realistisch, egal wie steil die
Staatsanleihen-Kurve gerade ist.

>>> STELLSCHRAUBEN FUER LAIEN <<<
- BASE_SPREAD  : verschiebt ALLE Zinsen nach oben/unten (Euer Top-Zins-Niveau).
- TERM         : Kurvenform, also wie viel mehr laengere Bindungen kosten.
"""
import json, urllib.request, datetime, sys, csv, io

BASE_SPREAD = 0.59                                 # Aufschlag 10J-Rendite -> 10J-Bauzins (effektiv, 60 % Beleihung)
TERM = {5: -0.02, 10: 0.00, 15: 0.24, 20: 0.37}    # Zu-/Abschlag je Bindung ggue. 10 Jahren
SOLL_ABSCHLAG = 0.07                               # Sollzins = Effektivzins minus dieser Wert

# ECB Data Portal: Spot-Rendite der AAA-Euroraum-Zinskurve, 10 Jahre
KEY_10Y = "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
BASE_URL = "https://data-api.ecb.europa.eu/service/data/YC/{key}?lastNObservations=1&format=csvdata"
MONATE = ["Januar","Februar","Maerz","April","Mai","Juni","Juli",
          "August","September","Oktober","November","Dezember"]

def parse_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("leere Antwort")
    last = rows[-1]
    return float(last["OBS_VALUE"]), last.get("TIME_PERIOD", "")

def fetch_yield_10y():
    url = BASE_URL.format(key=KEY_10Y)
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
    y10, iso = fetch_yield_10y()
    eff10 = y10 + BASE_SPREAD
    base = {}
    for years, prem in TERM.items():
        eff = round(eff10 + prem, 2)
        soll = round(eff - SOLL_ABSCHLAG, 2)
        base[str(years)] = {"soll": soll, "eff": eff}
    out = {"stand": stand_deutsch(iso), "live": True, "base": base}
    with open("bauzinsen.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("OK (10J-Rendite %.2f%%):" % y10, json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fehler beim Abruf:", e, file=sys.stderr)
        sys.exit(1)

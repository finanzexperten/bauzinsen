#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ruft die 10-Jahres-Rendite aus MEHREREN Quellen ab (Robustheit) und nutzt
automatisch die jeweils FRISCHESTE. Daraus werden Richtwerte fuer Bauzinsen
je Zinsbindung + eine taegliche Historie (~10 Jahre) berechnet -> bauzinsen.json
Nur Python-Standardbibliothek. Laeuft taeglich per GitHub Actions.

QUELLEN (in dieser Reihenfolge geprueft, es gewinnt die mit dem neuesten Datum):
  1) Deutsche Bundesbank - Zinsstruktur boersennotierter Bundeswertpapiere,
     Restlaufzeit 10 Jahre, taeglich (Svensson).
     Serie: BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A
  2) EZB - AAA-Renditekurve Euroraum, 10-Jahres-Spot (Svensson) als Fallback.
     Serie: YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y

Beide Quellen liefern nahezu dasselbe Zinsniveau (gleiche Methode), ein Wechsel
verursacht daher keinen sichtbaren Sprung.

>>> STELLSCHRAUBEN FUER LAIEN <<<
- BASE_SPREAD  : verschiebt ALLE Zinsen nach oben/unten (Euer Top-Zins-Niveau).
- TERM         : Kurvenform (Zu-/Abschlag je Zinsbindung).
- HISTORY_YEARS: Laenge der Historie im Chart.
"""
import json, urllib.request, datetime, sys, csv, io

BASE_SPREAD   = 0.59
TERM          = {5: -0.02, 10: 0.00, 15: 0.24, 20: 0.37}
SOLL_ABSCHLAG = 0.07
HISTORY_YEARS = 10
WARN_STALE_DAYS = 8   # informativ: Warnung ins Log, wenn Quelle aelter ist

MONATE = ["Januar","Februar","Maerz","April","Mai","Juni","Juli",
          "August","September","Oktober","November","Dezember"]

def is_date(s):
    return (len(s) == 10 and s[4] == "-" and s[7] == "-"
            and s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit())

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "finanzexperten-bauzins/3.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")

def fetch_bundesbank(n):
    # WICHTIG: die REST-API (api.statistiken...) ist tagesaktuell.
    # Der alte CSV-Download (StatisticDownload) lieferte teils GECACHTE, veraltete Daten.
    url = ("https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
           "D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
           "?format=json&lastNObservations=" + str(n))
    d = json.loads(_get(url))
    dates = [v["id"] for v in d["data"]["structure"]["dimensions"]["observation"][0]["values"]]
    ser = d["data"]["dataSets"][0]["series"]
    obs = ser[next(iter(ser))]["observations"]
    out = []
    for i, dt in enumerate(dates):
        cell = obs.get(str(i))
        if not cell:
            continue
        val = cell[0]
        if val in (None, ""):
            continue
        try: out.append((dt, float(val)))
        except (ValueError, TypeError): pass
    if not out:
        raise ValueError("Bundesbank REST: keine Datenpunkte")
    out.sort(key=lambda x: x[0])
    return out

def fetch_ecb(n):
    url = ("https://data-api.ecb.europa.eu/service/data/YC/"
           "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?format=csvdata&lastNObservations=" + str(n))
    rows = list(csv.DictReader(io.StringIO(_get(url))))
    out = []
    for row in rows:
        d, v = row.get("TIME_PERIOD", ""), row.get("OBS_VALUE", "")
        if is_date(d) and v not in ("", "."):
            try: out.append((d, float(v)))
            except (ValueError, TypeError): pass
    if not out:
        raise ValueError("EZB: keine Datenpunkte")
    out.sort(key=lambda x: x[0])
    return out

def try_source(name, fn, *args):
    try:
        s = fn(*args)
        print("OK  %-28s letzter Wert: %s (%.2f %%)" % (name, s[-1][0], s[-1][1]))
        return (name, s)
    except Exception as e:
        print("FEHLER %-25s %s" % (name, e), file=sys.stderr)
        return None

def stand_deutsch(iso):
    try: dt = datetime.date.fromisoformat(iso)
    except ValueError: dt = datetime.date.today()
    return "%d. %s %d" % (dt.day, MONATE[dt.month - 1], dt.year)

def main():
    candidates = []
    for c in (try_source("Deutsche Bundesbank", fetch_bundesbank, HISTORY_YEARS * 260),
              try_source("EZB AAA-Renditekurve", fetch_ecb, HISTORY_YEARS * 260)):
        if c: candidates.append(c)
    if not candidates:
        raise SystemExit("Keine Quelle erreichbar - bauzinsen.json bleibt unveraendert.")

    # Frischeste Quelle gewinnt (neuestes Datum des letzten Werts)
    name, series = max(candidates, key=lambda c: c[1][-1][0])
    last_date, y10 = series[-1]

    age = (datetime.date.today() - datetime.date.fromisoformat(last_date)).days
    if age > WARN_STALE_DAYS:
        print("WARNUNG: Frischeste Quelle (%s) ist %d Tage alt (Stand %s)."
              % (name, age, last_date), file=sys.stderr)

    eff10 = y10 + BASE_SPREAD
    base = {}
    for years, prem in TERM.items():
        eff = round(eff10 + prem, 2)
        base[str(years)] = {"soll": round(eff - SOLL_ABSCHLAG, 2), "eff": eff}
    history = [{"d": d, "v": round(v + BASE_SPREAD + TERM[10], 2)} for (d, v) in series]

    out = {"stand": stand_deutsch(last_date), "live": True, "quelle": name,
           "quelleStand": last_date, "base": base, "history": history}
    with open("bauzinsen.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("GESCHRIEBEN: Quelle=%s, Stand=%s, 10J-Rendite=%.2f %%, %d Verlaufspunkte"
          % (name, last_date, y10, len(history)))

if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e, file=sys.stderr); sys.exit(1)
    except Exception as e:
        print("Unerwarteter Fehler:", e, file=sys.stderr); sys.exit(1)

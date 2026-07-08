#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ruft die taegliche Renditekurve der DEUTSCHEN BUNDESBANK ab (Bund 10 Jahre,
Svensson-Methode) und rechnet daraus Richtwerte fuer Bauzinsen je Zinsbindung
+ eine taegliche Zins-Historie (~10 Jahre) fuer den Verlaufs-Chart.
Ergebnis: bauzinsen.json. Nur Python-Standardbibliothek.
Wird von GitHub Actions taeglich ausgefuehrt.

QUELLE: Bundesbank-Zeitreihe BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A
(Zinsstruktur / boersennotierte Bundeswertpapiere / Restlaufzeit 10 Jahre / taeglich).
Frischer und zuverlaessiger als die EZB-Kurve (i.d.R. Vortageswert).

MODELL: Anker ist die 10-Jahres-Rendite der Bundesbank. Effektiver Bauzins 10J =
10J-Rendite + BASE_SPREAD. Andere Bindungen bekommen einen festen Zu-/Abschlag
(TERM). Die Historie enthaelt denselben 10J-Bauzins Tag fuer Tag.

>>> STELLSCHRAUBEN FUER LAIEN <<<
- BASE_SPREAD  : verschiebt ALLE Zinsen nach oben/unten (Euer Top-Zins-Niveau).
- TERM         : Kurvenform, also wie viel mehr laengere Bindungen kosten.
- HISTORY_YEARS: Laenge der Zins-Historie im Chart (Jahre).
"""
import json, urllib.request, datetime, sys

BASE_SPREAD = 0.59
TERM = {5: -0.02, 10: 0.00, 15: 0.24, 20: 0.37}
SOLL_ABSCHLAG = 0.07
HISTORY_YEARS = 10

TS_ID = "BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
BASE_URL = ("https://www.bundesbank.de/statistic-rmi/StatisticDownload"
            "?tsId={ts}&its_csvFormat=en&mode=its&its_fileFormat=csv&its_from={frm}")
MONATE = ["Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]


def is_date(s):
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return False
    return s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()


def fetch_series(years):
    frm = (datetime.date.today() - datetime.timedelta(days=365 * years + 10)).isoformat()
    url = BASE_URL.format(ts=TS_ID, frm=frm)
    req = urllib.request.Request(url, headers={"User-Agent": "finanzexperten-bauzins/2.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    out = []
    for line in text.splitlines():
        cells = line.split(",")
        if len(cells) < 2:
            continue
        d, v = cells[0].strip().strip('"'), cells[1].strip()
        if not is_date(d):
            continue          # ueberspringt Kopf-/Metazeilen
        if v in ("", "."):
            continue          # Wochenenden/Feiertage ohne Wert
        try:
            out.append((d, float(v)))
        except ValueError:
            pass
    if not out:
        raise ValueError("keine Datenpunkte von der Bundesbank erhalten")
    out.sort(key=lambda x: x[0])   # chronologisch
    return out


def stand_deutsch(iso):
    try:
        dt = datetime.date.fromisoformat(iso)
    except ValueError:
        dt = datetime.date.today()
    return "%d. %s %d" % (dt.day, MONATE[dt.month - 1], dt.year)


def main():
    series = fetch_series(HISTORY_YEARS)
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
    print("OK (Bundesbank 10J-Rendite %.2f %% am %s, %d Verlaufs-Punkte):"
          % (y10, last_date, len(history)), base)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fehler beim Abruf:", e, file=sys.stderr)
        sys.exit(1)

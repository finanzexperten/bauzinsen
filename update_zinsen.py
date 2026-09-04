#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Erzeugt bauzinsen.json fuer den Bauzinsen-Rechner von finanzexperten.de.
Nur Python-Standardbibliothek. Laeuft taeglich per GitHub Actions.

=========================================================================
 ZWEI QUELLEN, ZWEI AUFGABEN
=========================================================================
 1) HEUTIGES ZINSNIVEAU  ->  Bundesbank Zinsstrukturkurve, Bund 10 Jahre
    (boersentaeglich, Svensson). Fallback: EZB AAA-Renditekurve.
    Daraus entstehen die Kachelwerte je Zinsbindung.

 2) HISTORISCHER VERLAUF ->  Bundesbank MFI-Zinsstatistik: Wohnungsbau-
    kredite an private Haushalte, Neugeschaeft, Effektivzinssatz,
    anfaengliche Zinsbindung ueber 10 Jahre (monatlich, Reihe SUD119).

 WARUM ZWEI QUELLEN?
 Der Abstand zwischen Bundrendite und tatsaechlichem Bauzins ist NICHT
 konstant. Im Maerz 2020 lag die Bundrendite bei rund -0,55 %, der reale
 Bauzins laut MFI-Statistik bei 1,18 % - ein Abstand von rund 1,7 Prozent-
 punkten. Heute liegt derselbe Abstand bei rund 0,6 Prozentpunkten.
 Die alte Version rechnete einen festen Aufschlag von BASE_SPREAD auf jede
 historische Bundrendite. Ergebnis waren Bauzinsen um 0 % und an 66 Tagen
 sogar negative Werte - die hat es nie gegeben.

 Jetzt wird der Aufschlag Monat fuer Monat aus der MFI-Statistik GEMESSEN
 und anschliessend so verschoben, dass er im juengsten gemeinsamen Monat
 exakt BASE_SPREAD entspricht. Damit gilt beides:
   - Das Kurvenende passt weiterhin exakt zum Kachelwert (10 J / 60 %).
   - Der historische Verlauf bekommt die tatsaechliche Form.

=========================================================================
 STELLSCHRAUBEN
=========================================================================
 BASE_SPREAD  : verschiebt ALLE Zinsen nach oben/unten (Euer Zinsniveau).
 TERM         : Aufschlag je Zinsbindung, relativ zu 10 Jahren.
 SOLL_ABSCHLAG: Abstand Effektivzins -> Sollzins.
 HISTORY_YEARS: Laenge der Historie in Jahren.
"""

import json, urllib.request, datetime, sys, statistics

# Marktabgleich 04.09.2026 (Rendite Bund 10 J = 3,40 %):
#   MFI-Durchschnitt aller Neuabschluesse (Juli 2026)      3,92 %
#   Vergleichsportale, Schlagzeilenzins 10 Jahre           3,69 - 3,74 %
#   frueher hier: 0.59 -> 3,99 % und damit UEBER dem Marktdurchschnitt,
#   obwohl 60 % Beleihung die gute Kondition sein soll.
# 0.35 ergibt 3,75 % bei 60 % Beleihung: unter dem Durchschnitt, auf
# Hoehe der Vergleichsportale, aber kein unrealistischer Lockzins.
BASE_SPREAD     = 0.35
TERM            = {5: -0.02, 10: 0.00, 15: 0.24, 20: 0.37}
SOLL_ABSCHLAG   = 0.07
HISTORY_YEARS   = 10
WARN_STALE_DAYS = 8       # aeltere Quelle -> Abbruch mit Fehler (Workflow wird rot)

MONATE = ["Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]

# --- Quellen -------------------------------------------------------------
API = "https://api.statistiken.bundesbank.de/rest/data/"
SERIE_BUND = "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
SERIE_EZB  = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
# MFI-Zinsstatistik, Wohnungsbaukredite priv. HH, Neugeschaeft,
# anfaengliche Zinsbindung ueber 10 Jahre, Effektivzins (= SUD119)
SERIE_BAUFI = "BBIM1/M.DE.B.A2C.P.R.A.2250.EUR.N"


# =========================================================================
# Abruf-Helfer
# =========================================================================
def _get(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "finanzexperten-bauzins/4.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def _parse_sdmx(raw):
    """SDMX-JSON -> Liste [(periode, wert)], aufsteigend, ohne Luecken-Nullen."""
    d = json.loads(raw)
    data = d.get("data", d)
    obs_dims = data["structure"]["dimensions"].get("observation") or []
    if not obs_dims:
        return []
    perioden = [v["id"] for v in obs_dims[0]["values"]]
    serien = data["dataSets"][0].get("series") or {}
    if not serien:
        return []
    beob = serien[next(iter(serien))].get("observations") or {}
    out = []
    for i, p in enumerate(perioden):
        zelle = beob.get(str(i))
        if not zelle:
            continue
        wert = zelle[0]
        if wert in (None, ""):
            continue
        try:
            out.append((p, float(wert)))
        except (ValueError, TypeError):
            pass
    out.sort(key=lambda x: x[0])
    return out


def fetch_bundesbank(start_iso):
    """Taegliche Bund-10J-Rendite ab start_iso.

    WICHTIG: frueher wurde lastNObservations=HISTORY_YEARS*260 benutzt.
    Der Parameter zaehlt aber KALENDERTAGE, nicht Handelstage - aus 10
    angeforderten Jahren wurden dadurch nur rund 7,1 Jahre Historie.
    startPeriod ist eindeutig und liefert genau den gewuenschten Zeitraum.
    """
    url = API + SERIE_BUND + "?format=json&startPeriod=" + start_iso
    s = _parse_sdmx(_get(url))
    if not s:
        raise ValueError("Bundesbank-Renditekurve lieferte keine Werte")
    return s


def fetch_ecb(start_iso):
    """Fallback: EZB AAA-Renditekurve, 10-Jahres-Spot."""
    url = SERIE_EZB + "?format=jsondata&startPeriod=" + start_iso
    s = _parse_sdmx(_get(url))
    if not s:
        raise ValueError("EZB-Renditekurve lieferte keine Werte")
    return s


def fetch_baufi(start_iso):
    """Monatliche MFI-Effektivzinssaetze fuer Wohnungsbaukredite (>10 J)."""
    url = API + SERIE_BAUFI + "?format=json&startPeriod=" + start_iso[:7]
    s = _parse_sdmx(_get(url))
    if not s:
        raise ValueError("MFI-Zinsstatistik lieferte keine Werte")
    return s


def try_source(name, fn, *args):
    try:
        s = fn(*args)
        print("OK     %-26s %d Werte, letzter: %s (%.2f %%)"
              % (name, len(s), s[-1][0], s[-1][1]))
        return (name, s)
    except Exception as e:
        print("FEHLER %-26s %s" % (name, e), file=sys.stderr)
        return None


# =========================================================================
# Aufschlagskurve aus der MFI-Statistik
# =========================================================================
def monatsmittel(taeglich):
    """[(YYYY-MM-DD, wert)] -> {YYYY-MM: mittelwert}"""
    eimer = {}
    for d, v in taeglich:
        eimer.setdefault(d[:7], []).append(v)
    return {m: statistics.fmean(vs) for m, vs in eimer.items()}


def _monatsmitte(monat):
    j, m = int(monat[:4]), int(monat[5:7])
    return datetime.date(j, m, 15).toordinal()


def spread_kurve(taeglich, baufi_monatlich):
    """Misst den Abstand Bauzins minus Bundrendite je Monat.

    Rueckgabe: (stuetzstellen, referenz) - stuetzstellen als
    [(ordinal_der_monatsmitte, spread)], referenz = Spread des juengsten
    gemeinsamen Monats. Dieser Referenzwert wird spaeter abgezogen, damit
    heute genau BASE_SPREAD herauskommt.
    """
    bund_m = monatsmittel(taeglich)
    stuetz = []
    for monat, baufi in baufi_monatlich:
        if monat in bund_m:
            stuetz.append((_monatsmitte(monat), round(baufi - bund_m[monat], 4), monat))
    if not stuetz:
        raise ValueError("Keine gemeinsamen Monate von Bund- und MFI-Reihe")
    stuetz.sort()
    referenz = stuetz[-1][1]
    print("Aufschlag gemessen: %d Monate, aeltester %s = %.2f pp, "
          "juengster %s = %.2f pp (Referenz)"
          % (len(stuetz), stuetz[0][2], stuetz[0][1], stuetz[-1][2], referenz))
    return [(o, s) for o, s, _ in stuetz], referenz


def spread_am_tag(stuetz, tag_ordinal):
    """Linear zwischen den Monatsmitten interpolieren, an den Raendern halten."""
    if tag_ordinal <= stuetz[0][0]:
        return stuetz[0][1]
    if tag_ordinal >= stuetz[-1][0]:
        return stuetz[-1][1]
    lo, hi = 0, len(stuetz) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if stuetz[mid][0] <= tag_ordinal:
            lo = mid
        else:
            hi = mid
    x0, y0 = stuetz[lo]
    x1, y1 = stuetz[hi]
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (tag_ordinal - x0) / (x1 - x0)


def baue_historie(taeglich, stuetz, referenz):
    """history-Werte: Bundrendite + BASE_SPREAD + gemessene Aufschlagsdifferenz."""
    out = []
    for d, y in taeglich:
        o = datetime.date.fromisoformat(d).toordinal()
        delta = spread_am_tag(stuetz, o) - referenz
        out.append({"d": d, "v": round(y + BASE_SPREAD + TERM[10] + delta, 2)})
    return out


def baue_historie_konstant(taeglich):
    """Rueckfallebene, wenn die MFI-Statistik nicht erreichbar ist."""
    return [{"d": d, "v": round(y + BASE_SPREAD + TERM[10], 2)} for d, y in taeglich]


# =========================================================================
def stand_deutsch(iso):
    try:
        dt = datetime.date.fromisoformat(iso)
    except ValueError:
        dt = datetime.date.today()
    return "%d. %s %d" % (dt.day, MONATE[dt.month - 1], dt.year)


def main():
    heute = datetime.date.today()
    start = (heute - datetime.timedelta(days=int(HISTORY_YEARS * 365.25))).isoformat()

    # --- 1) Tagesreihe fuer das Zinsniveau ------------------------------
    kandidaten = [c for c in (
        try_source("Bundesbank Bund 10J", fetch_bundesbank, start),
        try_source("EZB AAA-Renditekurve", fetch_ecb, start),
    ) if c]
    if not kandidaten:
        raise SystemExit("Keine Renditequelle erreichbar - bauzinsen.json bleibt unveraendert.")

    name, taeglich = max(kandidaten, key=lambda c: c[1][-1][0])
    letzter_tag, y10 = taeglich[-1]

    alter = (heute - datetime.date.fromisoformat(letzter_tag)).days
    if alter > WARN_STALE_DAYS:
        # Bewusst harter Abbruch: eine eingeschlafene Quelle darf nicht mit
        # frischem "stand"-Datum ausgeliefert werden.
        raise SystemExit(
            "ABBRUCH: Frischeste Quelle (%s) ist %d Tage alt (Stand %s), erlaubt sind %d."
            % (name, alter, letzter_tag, WARN_STALE_DAYS))

    # --- 2) Kachelwerte -------------------------------------------------
    eff10 = y10 + BASE_SPREAD
    base = {}
    for jahre, prem in TERM.items():
        eff = round(eff10 + prem, 2)
        base[str(jahre)] = {"soll": round(eff - SOLL_ABSCHLAG, 2), "eff": eff}

    # --- 3) Historie mit gemessenem Aufschlag ---------------------------
    baufi = try_source("MFI-Zinsstatistik (SUD119)", fetch_baufi, start)
    if baufi:
        stuetz, referenz = spread_kurve(taeglich, baufi[1])
        history = baue_historie(taeglich, stuetz, referenz)
        modell = ("Aufschlag je Monat aus der MFI-Zinsstatistik gemessen "
                  "(Wohnungsbaukredite private Haushalte, Neugeschaeft, "
                  "Zinsbindung ueber 10 Jahre) und auf das heutige Niveau kalibriert")
        quelle_hist = "Deutsche Bundesbank: Renditekurve + MFI-Zinsstatistik"
        hist_stand = baufi[1][-1][0]
    else:
        print("WARNUNG: MFI-Statistik nicht erreichbar - Historie mit konstantem "
              "Aufschlag (historisch zu niedrig).", file=sys.stderr)
        history = baue_historie_konstant(taeglich)
        modell = "Rueckfallebene: konstanter Aufschlag auf die Renditekurve"
        quelle_hist = name
        hist_stand = None

    # --- 4) Plausibilitaet ----------------------------------------------
    negativ = [p for p in history if p["v"] < 0]
    if negativ:
        print("WARNUNG: %d Historienwerte unter 0 %% (Minimum %.2f %% am %s). "
              "Bauzinsen waren nie negativ - Aufschlagsmodell pruefen."
              % (len(negativ), min(p["v"] for p in negativ),
                 min(negativ, key=lambda p: p["v"])["d"]), file=sys.stderr)
    if abs(history[-1]["v"] - base["10"]["eff"]) > 0.01:
        print("WARNUNG: Kurvenende (%.2f) weicht vom Kachelwert 10 J (%.2f) ab."
              % (history[-1]["v"], base["10"]["eff"]), file=sys.stderr)

    print("Historie: %d Tage, %s bis %s, Spanne %.2f bis %.2f %%"
          % (len(history), history[0]["d"], history[-1]["d"],
             min(p["v"] for p in history), max(p["v"] for p in history)))

    # --- 5) Schreiben ----------------------------------------------------
    out = {
        "stand": stand_deutsch(letzter_tag),
        "live": True,
        "quelle": name,
        "quelleStand": letzter_tag,
        "quelleHistorie": quelle_hist,
        "historieModell": modell,
        "historieStand": hist_stand,
        "base": base,
        "history": history,
    }
    with open("bauzinsen.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("bauzinsen.json geschrieben.")


if __name__ == "__main__":
    main()

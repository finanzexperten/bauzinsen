#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Erzeugt bauzinsen.json fuer den Bauzinsen-Rechner von finanzexperten.de.
Nur Python-Standardbibliothek. Laeuft taeglich per GitHub Actions.

=========================================================================
 SO ENTSTEHEN DIE ZINSEN
=========================================================================
 Zwei Quellen der Deutschen Bundesbank:

 1) TAGESBEWEGUNG  ->  Zinsstrukturkurve, Bund 10 Jahre (boersentaeglich,
    Svensson). Fallback: EZB AAA-Renditekurve.
    Liefert, wie sich das Zinsniveau von Tag zu Tag bewegt.

 2) MARKTNIVEAU    ->  MFI-Zinsstatistik: Wohnungsbaukredite an private
    Haushalte, Neugeschaeft, Effektivzinssatz, anfaengliche Zinsbindung
    ueber 10 Jahre (monatlich, Reihe SUD119, rund 2 Monate Meldeverzug).
    Liefert, wie weit der echte Bauzins ueber der Bundrendite liegt.

 Aus 2) wird Monat fuer Monat der Abstand gemessen:
     Aufschlag(Monat) = MFI-Bauzins(Monat) - Bundrendite(Monatsmittel)
 Dieser Abstand ist NICHT konstant: Maerz 2020 rund 1,7 Prozentpunkte,
 heute rund 0,6. Wer ihn einfriert, zeigt fuer 2019-2021 Bauzinsen um
 0 % oder darunter - die hat es nie gegeben.

 Unser eigener Zins ergibt sich daraus als:
     unser Zins = Bundrendite + Aufschlag(Monat) + MARKT_VORSPRUNG

 MARKT_VORSPRUNG ist die EINZIGE Zahl, die von Hand gepflegt wird, und
 sie hat eine fachliche Bedeutung: Um wie viele Prozentpunkte liegen wir
 besser als der Marktdurchschnitt aller Neuabschluesse?
 Bewegt sich die Marge der Banken, wandert der gemessene Aufschlag mit -
 die Anzeige folgt automatisch nach, mit rund zwei Monaten Verzug.
 Frueher stand hier ein fester Aufschlag auf die Bundrendite. Der lief
 unbemerkt vom Markt weg, sobald sich die Margen aenderten.

=========================================================================
 STELLSCHRAUBEN
=========================================================================
 MARKT_VORSPRUNG : Abstand zum Marktdurchschnitt (negativ = wir sind
                   guenstiger). Quartalsweise gegen die echte Partner-
                   kondition pruefen.
 TERM            : Aufschlag je Zinsbindung, relativ zu 10 Jahren.
 SOLL_ABSCHLAG   : Abstand Effektivzins -> Sollzins.
 HISTORY_YEARS   : Laenge der Historie in Jahren.
 NOTFALL_SPREAD  : greift nur, wenn die MFI-Reihe nicht erreichbar ist.
 MAX_SPRUNG      : Sicherung gegen Datenmuell, siehe unten.
"""

import json, os, urllib.request, datetime, sys, statistics

# ---------------------------------------------------------------------
# KALIBRIERUNG - Stand 04.09.2026
#
#   Referenz ist die Interhyp-Gruppe (unser Vertriebspartner):
#     10 Jahre Zinsbindung, 60 % Beleihung  ->  3,85 % effektiv
#
#   Gegengerechnet am echten Lauf vom 07.09.2026:
#     Bundrendite 10 J                        3,41 %
#     gemessener Aufschlag (MFI, Juli 2026)   0,77 pp
#     Summe = rechnerischer Marktzins heute    4,18 %
#     Interhyp                                 3,85 %
#     -> MARKT_VORSPRUNG = 3,85 - 4,18 = -0,33
#
#   Wir liegen also rund 0,33 Prozentpunkte besser als der Durchschnitt
#   aller Neuabschluesse. Das ist der typische Abstand zwischen einem
#   Top-Angebot und dem Marktmittel ueber alle Beleihungen und Bonitaeten.
#
#   Quartalsweise gegen die echte Interhyp-Kondition pruefen:
#   MARKT_VORSPRUNG = Interhyp-Zins - (Bundrendite + Aufschlag)
#   Beide Groessen stehen im Log jedes Laufs.
#   Letzte Pruefung: 07.09.2026
# ---------------------------------------------------------------------
MARKT_VORSPRUNG = -0.33

TERM            = {5: -0.02, 10: 0.00, 15: 0.24, 20: 0.37}
SOLL_ABSCHLAG   = 0.07
HISTORY_YEARS   = 10
WARN_STALE_DAYS = 8      # aeltere Renditequelle -> Abbruch (Workflow wird rot)
NOTFALL_SPREAD  = 0.45   # nur wenn die MFI-Reihe ausfaellt (Stand 04.09.2026)
MAX_SPRUNG      = 0.30   # groesserer Tagessprung -> Abbruch, siehe pruefe_sprung()

MONATE = ["Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]

# --- Quellen -------------------------------------------------------------
API = "https://api.statistiken.bundesbank.de/rest/data/"
SERIE_BUND = "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
SERIE_EZB  = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
SERIE_BAUFI = "BBIM1/M.DE.B.A2C.P.R.A.2250.EUR.N"   # = SUD119

AUSGABE = "bauzinsen.json"


# =========================================================================
# Abruf-Helfer
# =========================================================================
def _get(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "finanzexperten-bauzins/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def _parse_sdmx(raw):
    """SDMX-JSON -> Liste [(periode, wert)], aufsteigend, ohne Luecken."""
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
    startPeriod ist eindeutig.
    """
    s = _parse_sdmx(_get(API + SERIE_BUND + "?format=json&startPeriod=" + start_iso))
    if not s:
        raise ValueError("Bundesbank-Renditekurve lieferte keine Werte")
    return s


def fetch_ecb(start_iso):
    """Fallback: EZB AAA-Renditekurve, 10-Jahres-Spot."""
    s = _parse_sdmx(_get(SERIE_EZB + "?format=jsondata&startPeriod=" + start_iso))
    if not s:
        raise ValueError("EZB-Renditekurve lieferte keine Werte")
    return s


def fetch_baufi(start_iso):
    """Monatliche MFI-Effektivzinssaetze fuer Wohnungsbaukredite (>10 J)."""
    s = _parse_sdmx(_get(API + SERIE_BAUFI + "?format=json&startPeriod=" + start_iso[:7]))
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
    """Misst je Monat: MFI-Bauzins minus Bundrendite (Monatsmittel).

    Rueckgabe: (stuetzstellen, aktuell)
      stuetzstellen = [(ordinal der Monatsmitte, aufschlag)]
      aktuell       = Aufschlag des juengsten gemeinsamen Monats
    """
    bund_m = monatsmittel(taeglich)
    stuetz = []
    for monat, baufi in baufi_monatlich:
        if monat in bund_m:
            stuetz.append((_monatsmitte(monat), round(baufi - bund_m[monat], 4), monat))
    if not stuetz:
        raise ValueError("Keine gemeinsamen Monate von Bund- und MFI-Reihe")
    stuetz.sort()
    aktuell = stuetz[-1][1]
    print("Aufschlag gemessen: %d Monate | aeltester %s = %.2f pp | "
          "juengster %s = %.2f pp"
          % (len(stuetz), stuetz[0][2], stuetz[0][1], stuetz[-1][2], aktuell))
    return [(o, s) for o, s, _ in stuetz], aktuell


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


def baue_historie(taeglich, stuetz):
    """unser Zins = Bundrendite + gemessener Aufschlag + MARKT_VORSPRUNG."""
    out = []
    for d, y in taeglich:
        o = datetime.date.fromisoformat(d).toordinal()
        auf = spread_am_tag(stuetz, o)
        out.append({"d": d, "v": round(y + auf + MARKT_VORSPRUNG + TERM[10], 2)})
    return out


def baue_historie_notfall(taeglich):
    """Rueckfallebene: konstanter Aufschlag. Historisch zu niedrig!"""
    return [{"d": d, "v": round(y + NOTFALL_SPREAD + TERM[10], 2)} for d, y in taeglich]


# =========================================================================
# Sicherungen
# =========================================================================
def pruefe_sprung(neu_eff10):
    """Vergleicht mit der zuletzt veroeffentlichten Datei.

    Ein Tagessprung ueber MAX_SPRUNG deutet auf Datenmuell hin (falsche
    Reihe, Komma verrutscht, Quelle umgestellt). Dann lieber abbrechen als
    Unsinn ausliefern. Mit '--force' bewusst uebergehen, z. B. wenn
    MARKT_VORSPRUNG absichtlich stark geaendert wurde.
    """
    if not os.path.exists(AUSGABE):
        return
    try:
        alt = json.load(open(AUSGABE, encoding="utf-8"))
        alt_eff10 = float(alt["base"]["10"]["eff"])
    except Exception as e:
        print("Hinweis: alte %s nicht lesbar (%s) - Sprungpruefung entfaellt."
              % (AUSGABE, e), file=sys.stderr)
        return
    diff = abs(neu_eff10 - alt_eff10)
    if diff > MAX_SPRUNG and "--force" not in sys.argv:
        raise SystemExit(
            "ABBRUCH: Effektivzins 10 J springt von %.2f %% auf %.2f %% "
            "(%.2f pp, erlaubt sind %.2f). Daten pruefen. Wenn gewollt: "
            "Workflow mit '--force' starten."
            % (alt_eff10, neu_eff10, diff, MAX_SPRUNG))
    print("Sprungpruefung: %.2f %% -> %.2f %% (%.2f pp)" % (alt_eff10, neu_eff10, diff))


def pruefe_historie(history, base):
    negativ = [p for p in history if p["v"] < 0]
    if negativ:
        tief = min(negativ, key=lambda p: p["v"])
        print("WARNUNG: %d Historienwerte unter 0 %% (Minimum %.2f %% am %s). "
              "Bauzinsen waren nie negativ - Aufschlagsmodell pruefen."
              % (len(negativ), tief["v"], tief["d"]), file=sys.stderr)
    if abs(history[-1]["v"] - base["10"]["eff"]) > 0.01:
        print("WARNUNG: Kurvenende (%.2f) weicht vom Kachelwert 10 J (%.2f) ab."
              % (history[-1]["v"], base["10"]["eff"]), file=sys.stderr)


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

    # --- 1) Tagesreihe --------------------------------------------------
    kandidaten = [c for c in (
        try_source("Bundesbank Bund 10J", fetch_bundesbank, start),
        try_source("EZB AAA-Renditekurve", fetch_ecb, start),
    ) if c]
    if not kandidaten:
        raise SystemExit("Keine Renditequelle erreichbar - %s bleibt unveraendert." % AUSGABE)

    name, taeglich = max(kandidaten, key=lambda c: c[1][-1][0])
    letzter_tag, y10 = taeglich[-1]

    alter = (heute - datetime.date.fromisoformat(letzter_tag)).days
    if alter > WARN_STALE_DAYS:
        raise SystemExit(
            "ABBRUCH: Frischeste Quelle (%s) ist %d Tage alt (Stand %s), erlaubt sind %d."
            % (name, alter, letzter_tag, WARN_STALE_DAYS))

    # --- 2) Aufschlag messen --------------------------------------------
    baufi = try_source("MFI-Zinsstatistik (SUD119)", fetch_baufi, start)
    if baufi:
        stuetz, aufschlag = spread_kurve(taeglich, baufi[1])
        spread_heute = round(aufschlag + MARKT_VORSPRUNG, 4)
        history      = baue_historie(taeglich, stuetz)
        modell = ("Aufschlag monatlich aus der MFI-Zinsstatistik gemessen "
                  "(Wohnungsbaukredite private Haushalte, Neugeschaeft, "
                  "Zinsbindung ueber 10 Jahre), zzgl. unseres Vorsprungs "
                  "von %.2f Prozentpunkten gegenueber dem Marktdurchschnitt"
                  % MARKT_VORSPRUNG)
        quelle_hist = "Deutsche Bundesbank: Renditekurve + MFI-Zinsstatistik"
        hist_stand  = baufi[1][-1][0]
        print("Marktdurchschnitt %s: Aufschlag %.2f pp | unser Vorsprung %.2f pp "
              "| wirksamer Aufschlag %.2f pp"
              % (hist_stand, aufschlag, MARKT_VORSPRUNG, spread_heute))
    else:
        print("WARNUNG: MFI-Statistik nicht erreichbar - Notfallwert %.2f pp, "
              "Historie historisch zu niedrig." % NOTFALL_SPREAD, file=sys.stderr)
        spread_heute = NOTFALL_SPREAD
        history      = baue_historie_notfall(taeglich)
        modell       = "Rueckfallebene: konstanter Aufschlag %.2f pp" % NOTFALL_SPREAD
        quelle_hist  = name
        hist_stand   = None

    # --- 3) Kachelwerte -------------------------------------------------
    eff10 = y10 + spread_heute
    base = {}
    for jahre, prem in TERM.items():
        eff = round(eff10 + prem, 2)
        base[str(jahre)] = {"soll": round(eff - SOLL_ABSCHLAG, 2), "eff": eff}

    # --- 4) Sicherungen --------------------------------------------------
    pruefe_sprung(base["10"]["eff"])
    pruefe_historie(history, base)
    print("Historie: %d Tage, %s bis %s, Spanne %.2f bis %.2f %%"
          % (len(history), history[0]["d"], history[-1]["d"],
             min(p["v"] for p in history), max(p["v"] for p in history)))
    print("Kacheln 60 %% Beleihung: " + " | ".join(
        "%s J %.2f %%" % (j, base[j]["eff"]) for j in ("5", "10", "15", "20")))
    print("ABGLEICH: Weicht der Wert fuer 10 Jahre von Eurer Partnerkondition ab, "
          "MARKT_VORSPRUNG (aktuell %.2f) um genau die Differenz verschieben."
          % MARKT_VORSPRUNG)

    # --- 5) Schreiben ----------------------------------------------------
    out = {
        "stand": stand_deutsch(letzter_tag),
        "live": True,
        "quelle": name,
        "quelleStand": letzter_tag,
        "quelleHistorie": quelle_hist,
        "historieModell": modell,
        "historieStand": hist_stand,
        "marktVorsprung": MARKT_VORSPRUNG,
        "aufschlagGemessen": round(spread_heute, 2),
        "base": base,
        "history": history,
    }
    with open(AUSGABE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("%s geschrieben." % AUSGABE)


if __name__ == "__main__":
    main()

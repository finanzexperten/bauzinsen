#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Test fuer update_zinsen.py: kein Netzzugriff, synthetische SDMX-Antworten.

Prueft:
 1. SDMX-Parser gegen die echte Antwortstruktur der Bundesbank-API
 2. Aufschlag wird je Monat korrekt gemessen
 3. Selbstkorrektur: steigt die Bankmarge, steigt der angezeigte Zins mit
 4. Kurvenende == Kachelwert 10 Jahre, keine negativen Werte
 5. Sprungsicherung schlaegt bei Datenmuell an und laesst sich mit --force uebergehen
 6. Ausfall der MFI-Reihe
 7. Meldeverzug: MFI folgt der Bundrendite einen Monat spaeter -> Aufschlag bleibt stabil
"""
import json, datetime, os, sys, tempfile
import update_zinsen as uz

FEHLER = []
def pruefe(ok, text, detail=""):
    print(("  OK   " if ok else "  FEHL ") + text + ("  " + detail if detail else ""))
    if not ok:
        FEHLER.append(text)

# ---------------------------------------------------------------- Testdaten
def sdmx(perioden, werte):
    return json.dumps({"data": {
        "structure": {"dimensions": {"observation": [
            {"id": "TIME_PERIOD", "values": [{"id": p} for p in perioden]}]}},
        "dataSets": [{"series": {"0:0:0": {"observations": {
            str(i): ([None] if w is None else [w]) for i, w in enumerate(werte)}}}}]
    }})

START, ENDE = datetime.date(2020, 1, 1), datetime.date(2026, 9, 3)

def bund_reihe():
    """Rendite steigt linear von -0,55 auf 3,42; Wochenenden sind Luecken."""
    p, w, d = [], [], START
    spanne = (ENDE - START).days
    while d <= ENDE:
        p.append(d.isoformat())
        w.append(None if d.weekday() >= 5
                 else round(-0.55 + (d - START).days / spanne * (3.42 + 0.55), 2))
        d += datetime.timedelta(days=1)
    return p, w

def baufi_reihe(margenschub=0.0):
    """MFI-Bauzins 1,18 -> 3,92; margenschub hebt die letzten 6 Monate an."""
    p, w = [], []
    n = 0
    monate = [(j, m) for j in range(2020, 2027) for m in range(1, 13)
              if not (j == 2026 and m > 7)]
    for i, (j, m) in enumerate(monate):
        p.append("%04d-%02d" % (j, m))
        v = 1.18 + (i / (len(monate) - 1.0)) * (3.92 - 1.18)
        if i >= len(monate) - 6:
            v += margenschub
        w.append(round(v, 2))
    return p, w

BP, BW = bund_reihe()

def mach_fake_get(margenschub=0.0, mfi_kaputt=False):
    MP, MW = baufi_reihe(margenschub)
    def fake_get(url):
        if "BBIM1" in url:
            if mfi_kaputt:
                raise RuntimeError("Simulierter Ausfall der MFI-Reihe")
            return sdmx(MP, MW)
        if "BBSIS" in url:
            return sdmx(BP, BW)
        raise RuntimeError("EZB im Test nicht noetig")
    return fake_get

uz._get = mach_fake_get()

# ---------------------------------------------------------------- 1) Parser
print("\n1) SDMX-Parser")
bund = uz.fetch_bundesbank("2020-01-01")
pruefe(len(bund) == sum(1 for x in BW if x is not None),
       "Nullwerte (Wochenenden) werden uebersprungen", "%d Werte" % len(bund))
pruefe(bund[-1] == ("2026-09-03", 3.42), "letzter Renditewert korrekt", str(bund[-1]))
mfi = uz.fetch_baufi("2020-01-01")
pruefe(mfi[-1][0] == "2026-07", "MFI endet im Juli 2026 (Meldeverzug)", mfi[-1][0])

# ------------------------------------------------------ 2) Aufschlagskurve
print("\n2) Aufschlag messen")
stuetz, aufschlag = uz.spread_kurve(bund, mfi)
# Erster MFI-Monat (2020-01) hat keinen Bund-Vormonat in den Testdaten
pruefe(len(stuetz) == len(mfi) - uz.MFI_VERZUG,
       "je MFI-Monat mit Bund-Vormonat eine Stuetzstelle", "%d" % len(stuetz))
pruefe(stuetz[0][1] > aufschlag + 1.0,
       "Aufschlag war 2020 deutlich groesser als heute",
       "2020: %.2f pp  heute: %.2f pp" % (stuetz[0][1], aufschlag))

# ------------------------------------------------- 3) Selbstkorrektur
print("\n3) Selbstkorrektur bei steigender Bankmarge")
uz._get = mach_fake_get(margenschub=0.25)
mfi_hoch = uz.fetch_baufi("2020-01-01")
_, aufschlag_hoch = uz.spread_kurve(bund, mfi_hoch)
zins_normal = round(bund[-1][1] + aufschlag + uz.MARKT_VORSPRUNG, 2)
zins_hoch   = round(bund[-1][1] + aufschlag_hoch + uz.MARKT_VORSPRUNG, 2)
pruefe(abs((zins_hoch - zins_normal) - 0.25) < 0.02,
       "0,25 pp mehr Marge -> 0,25 pp hoeherer Anzeigezins",
       "%.2f %% -> %.2f %%" % (zins_normal, zins_hoch))
uz._get = mach_fake_get()

# --------------------------------------------------------- 4) Historie
print("\n4) Historie")
hist = uz.baue_historie(bund, stuetz)
eff10 = round(bund[-1][1] + aufschlag + uz.MARKT_VORSPRUNG + uz.TERM[10], 2)
pruefe(abs(hist[-1]["v"] - eff10) <= 0.01, "Kurvenende == Kachelwert 10 Jahre",
       "Kurve %.2f / Kachel %.2f" % (hist[-1]["v"], eff10))
negativ = [p for p in hist if p["v"] < 0]
pruefe(not negativ, "keine negativen Bauzinsen", "%d negative Werte" % len(negativ))
pruefe(0.7 <= hist[0]["v"] <= 1.4, "Anfang 2020 auf MFI-Niveau minus Vorsprung",
       "%.2f %%" % hist[0]["v"])
notfall = uz.baue_historie_notfall(bund)
abstand = hist[0]["v"] - notfall[0]["v"]
pruefe(abstand > 0.5,
       "Gegenprobe: konstanter Aufschlag laege Anfang 2020 deutlich zu niedrig",
       "konstant %.2f %% / gemessen %.2f %% / Abstand %.2f pp"
       % (notfall[0]["v"], hist[0]["v"], abstand))

# ------------------------------------------------------ 5) Sprungsicherung
print("\n5) Sprungsicherung")
tmp = tempfile.mkdtemp(); alt = os.getcwd(); os.chdir(tmp)
try:
    uz.AUSGABE = "bauzinsen.json"
    frei_orig = uz.SPRUNG_FREI_BIS
    uz.SPRUNG_FREI_BIS = "2000-01-01"   # Freigabefenster fuer den Test schliessen
    json.dump({"base": {"10": {"eff": 3.85, "soll": 3.78}}},
              open("bauzinsen.json", "w", encoding="utf-8"))
    try:
        uz.pruefe_sprung(3.95); ok_klein = True
    except SystemExit:
        ok_klein = False
    pruefe(ok_klein, "kleiner Sprung (0,10 pp) wird durchgelassen")

    try:
        uz.pruefe_sprung(5.20); ok_gross = False
    except SystemExit as e:
        ok_gross = "ABBRUCH" in str(e)
    pruefe(ok_gross, "grosser Sprung (1,35 pp) bricht ab")

    sys.argv.append("--force")
    try:
        uz.pruefe_sprung(5.20); ok_force = True
    except SystemExit:
        ok_force = False
    sys.argv.remove("--force")
    pruefe(ok_force, "--force uebergeht die Sperre bewusst")

    os.remove("bauzinsen.json")
    try:
        uz.pruefe_sprung(9.99); ok_erst = True
    except SystemExit:
        ok_erst = False
    pruefe(ok_erst, "ohne Vordatei (Erstlauf) keine Sperre")

    json.dump({"base": {"10": {"eff": 3.85, "soll": 3.78}}},
              open("bauzinsen.json", "w", encoding="utf-8"))
    uz.SPRUNG_FREI_BIS = "2999-12-31"
    try:
        uz.pruefe_sprung(5.20); ok_frei = True
    except SystemExit:
        ok_frei = False
    pruefe(ok_frei, "Freigabefenster SPRUNG_FREI_BIS laesst Sprung durch")
    uz.SPRUNG_FREI_BIS = frei_orig
finally:
    os.chdir(alt)

# ---------------------------------------------------- 6) Ausfall der MFI
print("\n6) Ausfall der MFI-Reihe")
uz._get = mach_fake_get(mfi_kaputt=True)
pruefe(uz.try_source("MFI", uz.fetch_baufi, "2020-01-01") is None,
       "Ausfall wird abgefangen und liefert None")
pruefe(abs(notfall[-1]["v"] - round(bund[-1][1] + uz.NOTFALL_SPREAD, 2)) <= 0.01,
       "Notfallmodell rechnet mit NOTFALL_SPREAD", "%.2f %%" % notfall[-1]["v"])

# ------------------------------------------- 7) Meldeverzug der MFI-Reihe
print("\n7) Meldeverzug: MFI laeuft einen Monat hinterher")
# Bund: 3,00 % bis Juni, ab Juli sprunghaft 3,50 %. Echte Marge 0,90 pp.
# MFI bildet den Sprung erst im August ab (Zinszusage im Vormonat).
tb = []
d = datetime.date(2026, 1, 1)
while d <= datetime.date(2026, 9, 30):
    if d.weekday() < 5:
        tb.append((d.isoformat(), 3.00 if d.month <= 6 else 3.50))
    d += datetime.timedelta(days=1)
mfi_lag = [("2026-%02d" % m, 3.90 if m <= 7 else 4.40) for m in range(2, 10)]
_, auf_jetzt = uz.spread_kurve(tb, mfi_lag)
st_alt, auf_alt0 = uz.spread_kurve(tb, mfi_lag, verzug=0)
werte_alt = [s for _, s in st_alt]
pruefe(abs(auf_jetzt - 0.90) < 1e-9, "Vormonats-Messung trifft echte Marge 0,90 pp",
       "%.2f pp" % auf_jetzt)
pruefe(min(werte_alt) < 0.5,
       "Gegenprobe: gleichzeitige Messung haette im Sprungmonat 0,40 pp gezeigt",
       "Minimum %.2f pp" % min(werte_alt))

print("\n" + ("ALLE TESTS BESTANDEN" if not FEHLER
              else "%d TEST(S) FEHLGESCHLAGEN: %s" % (len(FEHLER), FEHLER)))
sys.exit(1 if FEHLER else 0)

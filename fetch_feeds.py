#!/usr/bin/env python3
"""
fetch_feeds.py
Liest RSS-Feeds aus sources.json, erkennt neue Artikel,
aktualisiert seen.json und generiert index.html.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from html import escape

try:
    import feedparser
except ImportError:
    print("FEHLER: Das Modul 'feedparser' ist nicht installiert.")
    print("Bitte ausfuehren: pip install feedparser")
    sys.exit(1)

# Dateipfade relativ zum Skript-Verzeichnis
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(SCRIPT_DIR, "sources.json")
SEEN_FILE = os.path.join(SCRIPT_DIR, "seen.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "index.html")

MAX_SEEN_DAYS = 30


def load_json(filepath, fallback):
    """Laedt eine JSON-Datei oder gibt fallback zurueck bei Fehler."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"HINWEIS: {os.path.basename(filepath)} nicht gefunden, erstelle neu.")
        return fallback
    except json.JSONDecodeError as e:
        print(f"FEHLER: {os.path.basename(filepath)} enthaelt ungueltiges JSON: {e}")
        return fallback


def save_json(filepath, data):
    """Speichert Daten als formatierte JSON-Datei."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cleanup_seen(seen_data):
    """Entfernt Eintraege aus seen.json die aelter als MAX_SEEN_DAYS Tage sind."""
    heute_str = datetime.now().strftime("%Y-%m-%d")
    heute = datetime.strptime(heute_str, "%Y-%m-%d")
    schwelle = heute - timedelta(days=MAX_SEEN_DAYS)
    bereinigt = {}

    for url, datum_str in seen_data.items():
        try:
            eintrags_datum = datetime.strptime(datum_str, "%Y-%m-%d")
            if eintrags_datum >= schwelle:
                bereinigt[url] = datum_str
        except ValueError:
            print(f"WARNUNG: Ungueltiges Datum '{datum_str}' fuer URL, wird behalten.")
            bereinigt[url] = datum_str

    entfernt = len(seen_data) - len(bereinigt)
    if entfernt > 0:
        print(f"Bereinigung: {entfernt} veraltete Eintraege aus seen.json entfernt.")

    return bereinigt


def fetch_feed(quelle):
    """Ruft einen RSS-Feed ab und gibt die Entries zurueck. Gibt None bei Fehler."""
    name = quelle.get("name", "Unbekannt")
    url = quelle.get("url", "")

    if not url:
        print(f"WARNUNG: Quelle '{name}' hat keine URL, wird uebersprungen.")
        return []

    try:
        print(f"Rufe Feed ab: {name} ({url})")
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            print(f"FEHLER: Feed '{name}' konnte nicht geparst werden: {feed.bozo_exception}")
            return []

        if not feed.entries:
            print(f"WARNUNG: Feed '{name}' enthaelt keine Eintraege.")
            return []

        return feed.entries

    except Exception as e:
        print(f"FEHLER beim Abrufen von '{name}': {e}")
        return []


def extract_article(entry, quelle):
    """Extrahiert die relevanten Daten aus einem Feed-Entry."""
    titel = entry.get("title", "Ohne Titel")
    link = entry.get("link", "")

    if not link:
        return None

    # Datum aus dem Feed extrahieren
    datum_raw = entry.get("published_parsed") or entry.get("updated_parsed")
    if datum_raw:
        try:
            datum = datetime(*datum_raw[:6]).strftime("%Y-%m-%d")
        except Exception:
            datum = datetime.now().strftime("%Y-%m-%d")
    else:
        datum = datetime.now().strftime("%Y-%m-%d")

    zusammenfassung = entry.get("summary", "") or entry.get("description", "")

    return {
        "titel": titel,
        "link": link,
        "datum": datum,
        "kategorie": quelle.get("category", "Allgemein"),
        "quelle": quelle.get("name", "Unbekannt"),
        "zusammenfassung": zusammenfassung,
    }


def generate_html(artikel_liste):
    """Generiert die komplette index.html aus der Artikelliste."""
    jetzt = datetime.now().strftime("%d.%m.%Y um %H:%M Uhr")

    # Artikel nach Kategorie gruppieren
    kategorien = {}
    for artikel in artikel_liste:
        kat = artikel["kategorie"]
        if kat not in kategorien:
            kategorien[kat] = []
        kategorien[kat].append(artikel)

    # Innerhalb jeder Kategorie nach Datum absteigend sortieren
    for kat in kategorien:
        kategorien[kat].sort(key=lambda x: x["datum"], reverse=True)

    # Kategorien alphabetisch sortieren
    sortierte_kategorien = sorted(kategorien.items())

    # Artikel-Karten generieren
    artikel_html = ""

    if not artikel_liste:
        artikel_html = """
            <div class="empty-state">
                <div class="empty-icon">&#128240;</div>
                <h2>Keine neuen Artikel</h2>
                <p>Es wurden heute keine neuen Artikel gefunden. Schau morgen wieder vorbei!</p>
            </div>"""
    else:
        for kategorie, artikel in sortierte_kategorien:
            artikel_html += f"""
            <section class="category">
                <h2 class="category-title">{escape(kategorie)}</h2>
                <div class="articles">"""
            for a in artikel:
                zusammenfassung_kurz = ""
                if a["zusammenfassung"]:
                    zusammenfassung_kurz = escape(a["zusammenfassung"][:150])
                    if len(a["zusammenfassung"]) > 150:
                        zusammenfassung_kurz += "..."

                artikel_html += f"""
                    <a href="{escape(a['link'])}" class="article-card" target="_blank" rel="noopener noreferrer">
                        <div class="article-meta">
                            <span class="article-source">{escape(a['quelle'])}</span>
                            <span class="article-date">{escape(a['datum'])}</span>
                        </div>
                        <h3 class="article-title">{escape(a['titel'])}</h3>
                        {f'<p class="article-summary">{zusammenfassung_kurz}</p>' if zusammenfassung_kurz else ''}
                    </a>"""

            artikel_html += """
                </div>
            </section>"""

    # Vollstaendiges HTML zusammenbauen
    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DailyBriefing</title>
    <style>
        *, *::before, *::after {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f5f5f7;
            color: #1d1d1f;
            line-height: 1.6;
        }}

        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }}

        header {{
            text-align: center;
            padding: 40px 20px 30px;
            border-bottom: 1px solid #e0e0e0;
            margin-bottom: 30px;
        }}

        header h1 {{
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: -0.5px;
            color: #1d1d1f;
        }}

        header .subtitle {{
            color: #86868b;
            font-size: 0.9rem;
            margin-top: 8px;
        }}

        .category {{
            margin-bottom: 40px;
        }}

        .category-title {{
            font-size: 1.3rem;
            font-weight: 600;
            color: #1d1d1f;
            padding-bottom: 10px;
            border-bottom: 2px solid #1d1d1f;
            margin-bottom: 16px;
        }}

        .articles {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}

        .article-card {{
            display: block;
            background: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 12px;
            padding: 16px 20px;
            text-decoration: none;
            color: inherit;
            transition: border-color 0.2s, box-shadow 0.2s;
        }}

        .article-card:hover {{
            border-color: #0071e3;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
        }}

        .article-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }}

        .article-source {{
            font-size: 0.75rem;
            font-weight: 600;
            color: #0071e3;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .article-date {{
            font-size: 0.75rem;
            color: #86868b;
        }}

        .article-title {{
            font-size: 1.05rem;
            font-weight: 600;
            line-height: 1.4;
            color: #1d1d1f;
        }}

        .article-summary {{
            font-size: 0.85rem;
            color: #86868b;
            margin-top: 6px;
            line-height: 1.4;
        }}

        .empty-state {{
            text-align: center;
            padding: 60px 20px;
        }}

        .empty-icon {{
            font-size: 3rem;
            margin-bottom: 16px;
        }}

        .empty-state h2 {{
            font-size: 1.4rem;
            font-weight: 600;
            margin-bottom: 8px;
        }}

        .empty-state p {{
            color: #86868b;
            font-size: 0.95rem;
        }}

        footer {{
            text-align: center;
            padding: 30px 20px;
            border-top: 1px solid #e0e0e0;
            margin-top: 40px;
            color: #86868b;
            font-size: 0.8rem;
        }}

        @media (max-width: 600px) {{
            .container {{
                padding: 12px;
            }}
            header h1 {{
                font-size: 1.6rem;
            }}
            .article-card {{
                padding: 12px 16px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>DailyBriefing</h1>
            <p class="subtitle">Deine taegliche Nachrichtenuebersicht &mdash; zuletzt aktualisiert am {jetzt}</p>
        </header>

        <main>
            {artikel_html}
        </main>

        <footer>
            <p>DailyBriefing &mdash; Automatisch generiert mit GitHub Actions</p>
        </footer>
    </div>
</body>
</html>"""

    return html


def main():
    """Hauptfunktion: Feeds abrufen, neue Artikel finden, HTML generieren."""
    print("=" * 50)
    print("DailyBriefing - Feed-Abruf gestartet")
    print(f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print("=" * 50)

    # Quellen laden
    quellen = load_json(SOURCES_FILE, [])
    if not quellen:
        print("FEHLER: Keine Quellen in sources.json gefunden.")
        sys.exit(1)

    print(f"{len(quellen)} Quellen geladen.\n")

    # Bisher gesehene Artikel laden und bereinigen
    seen_data = load_json(SEEN_FILE, {})
    seen_data = cleanup_seen(seen_data)

    # Alle Feeds abrufen und neue Artikel sammeln
    neue_artikel = []
    gesamt_gefunden = 0

    for quelle in quellen:
        entries = fetch_feed(quelle)
        gesamt_gefunden += len(entries)

        for entry in entries:
            artikel = extract_article(entry, quelle)
            if artikel is None:
                continue

            url = artikel["link"]

            # Pruefen ob der Artikel bereits bekannt ist
            if url not in seen_data:
                neue_artikel.append(artikel)
                seen_data[url] = datetime.now().strftime("%Y-%m-%d")

    # Ergebnis ausgeben
    print(f"\n{'=' * 50}")
    print(f"Gesamt: {gesamt_gefunden} Artikel in Feeds gefunden")
    print(f"Davon neu: {len(neue_artikel)} Artikel")
    print(f"{'=' * 50}\n")

    # seen.json aktualisieren
    save_json(SEEN_FILE, seen_data)
    print(f"seen.json aktualisiert ({len(seen_data)} Eintraege).")

    # index.html generieren
    html_content = generate_html(neue_artikel)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"index.html generiert: {OUTPUT_FILE}")

    if neue_artikel:
        for artikel in neue_artikel:
            print(f"  - [{artikel['kategorie']}] {artikel['titel'][:80]}")

    print("\nFertig!")


if __name__ == "__main__":
    main()

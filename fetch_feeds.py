#!/usr/bin/env python3
"""
fetch_feeds.py
Liest RSS-Feeds und gescrapte Seiten aus sources.json, erkennt neue Artikel,
aktualisiert seen.json und generiert index.html.

Quellen-Typen in sources.json:
  "type": "rss"    -> mit feedparser verarbeiten
  "type": "scrape" -> mit requests + BeautifulSoup verarbeiten

seen.json Struktur (pro Eintrag):
{
  "URL": {
    "date": "YYYY-MM-DD",
    "title": "Titel des Artikels",
    "source": "Quellenname",
    "category": "Kategorie"
  }
}
"""

import json
import os
import sys
from datetime import datetime, timedelta
from html import escape
from urllib.parse import urljoin

try:
    import feedparser
except ImportError:
    print("FEHLER: Das Modul 'feedparser' ist nicht installiert.")
    print("Bitte ausfuehren: pip install feedparser")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("FEHLER: Das Modul 'requests' ist nicht installiert.")
    print("Bitte ausfuehren: pip install requests")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("FEHLER: Das Modul 'beautifulsoup4' ist nicht installiert.")
    print("Bitte ausfuehren: pip install beautifulsoup4")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(SCRIPT_DIR, "sources.json")
SEEN_FILE = os.path.join(SCRIPT_DIR, "seen.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "index.html")

MAX_SEEN_DAYS = 30

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}


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


def migrate_seen_format(seen_data):
    """
    Migriert das alte seen.json Format (string) zum neuen Format (objekt).
    Altes Format: {"url": "2026-04-16"}
    Neues Format: {"url": {"date": "2026-04-16", "title": "", "source": "", "category": ""}}
    """
    migriert = {}
    anzahl_alt = 0

    for url, wert in seen_data.items():
        if isinstance(wert, str):
            anzahl_alt += 1
            migriert[url] = {
                "date": wert,
                "title": "",
                "source": "",
                "category": "",
            }
        elif isinstance(wert, dict):
            migriert[url] = wert
        else:
            print(f"WARNUNG: Unbekanntes Format fuer URL {url}, wird uebersprungen.")

    if anzahl_alt > 0:
        print(f"Migration: {anzahl_alt} Eintraege vom alten ins neue Format konvertiert.")

    return migriert


def cleanup_seen(seen_data):
    """Entfernt Eintraege aus seen.json die aelter als MAX_SEEN_DAYS Tage sind."""
    heute = datetime.now()
    schwelle = heute - timedelta(days=MAX_SEEN_DAYS)
    bereinigt = {}

    for url, eintrag in seen_data.items():
        datum_str = eintrag.get("date", "") if isinstance(eintrag, dict) else eintrag
        try:
            eintrags_datum = datetime.strptime(datum_str, "%Y-%m-%d")
            if eintrags_datum >= schwelle:
                bereinigt[url] = eintrag
        except ValueError:
            print(f"WARNUNG: Ungueltiges Datum '{datum_str}' fuer URL, wird behalten.")
            bereinigt[url] = eintrag

    entfernt = len(seen_data) - len(bereinigt)
    if entfernt > 0:
        print(f"Bereinigung: {entfernt} veraltete Eintraege aus seen.json entfernt.")

    return bereinigt


# ---------------------------------------------------------------------------
# RSS-Feed Verarbeitung (wie bisher)
# ---------------------------------------------------------------------------

def fetch_feed(quelle):
    """Ruft einen RSS-Feed ab und gibt die Entries zurueck."""
    name = quelle.get("name", "Unbekannt")
    url = quelle.get("url", "")

    if not url:
        print(f"WARNUNG: Quelle '{name}' hat keine URL, wird uebersprungen.")
        return []

    try:
        print(f"Rufe RSS-Feed ab: {name} ({url})")
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


# ---------------------------------------------------------------------------
# Scraping-Verarbeitung
# ---------------------------------------------------------------------------

BLOCKED_TITLES = {"startseite", "zum archiv", "weiterlesen", "nach oben", "zurück"}

DEBUG_DIR = SCRIPT_DIR


def _debug_dump(name, html_text):
    """Speichert HTML temporaer zum Debuggen."""
    debug_path = os.path.join(DEBUG_DIR, f"debug_{name}.html")
    try:
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html_text)
        print(f"  Debug-HTML gespeichert: {debug_path}")
    except Exception as e:
        print(f"  WARNUNG: Debug-Datei konnte nicht gespeichert werden: {e}")


def _clean_debug_files():
    """Loescht alle debug_*.html Dateien nach dem Lauf."""
    import glob
    for f in glob.glob(os.path.join(DEBUG_DIR, "debug_*.html")):
        try:
            os.remove(f)
        except Exception:
            pass


def _filter_title(titel):
    """Prueft ob ein Titel gueltig ist (nicht blockiert, nicht leer)."""
    if not titel or not titel.strip():
        return False
    return titel.strip().lower() not in BLOCKED_TITLES


def _fetch_page(url, name, use_raw_bytes=False):
    """Laedt eine Seite mit Logging und gibt (resp, soup) oder (None, None) zurueck.
    use_raw_bytes: True fuer Seiten mit problematischer Zeichenkodierung (z.B. Amprion).
    """
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=20)
        if use_raw_bytes:
            soup = BeautifulSoup(resp.content, "html.parser", from_encoding="utf-8")
        else:
            soup = BeautifulSoup(resp.text, "html.parser")
        print(f"  HTTP {resp.status_code}, {len(resp.content)} Bytes empfangen")
        if resp.status_code != 200:
            print(f"  FEHLER: HTTP {resp.status_code} fuer '{name}'")
            _debug_dump(name.replace(" ", "_").lower(), resp.text)
            return None, None
        return resp, soup
    except Exception as e:
        print(f"  FEHLER beim Laden von '{name}': {e}")
        return None, None


def scrape_amprion(url):
    """Extrahiert Pressemitteilungen von amprion.net.
    Struktur: h3.mol--press-release__headline hat den Titel,
    Parent div.mol--press-release__content hat den "weiterlesen"-Link.
    """
    artikel = []
    name = "Amprion"
    print(f"Scrape: {name}")
    resp, soup = _fetch_page(url, name, use_raw_bytes=True)
    if not soup:
        return artikel

    for h3 in soup.select("h3.mol--press-release__headline"):
        titel = h3.get_text(strip=True)
        if not _filter_title(titel):
            if not titel:
                print(f"  WARNUNG: Leerer h3-Titel bei Amprion")
            continue
        # Link ist im Parent-Div
        parent = h3.parent
        a = parent.find("a", href=True) if parent else None
        if not a:
            continue
        href = str(a.get("href", ""))
        full_url = urljoin(url, href)
        artikel.append({"title": titel, "link": full_url})

    # Duplikate entfernen
    seen = set()
    unique = []
    for a in artikel:
        if a["link"] not in seen:
            seen.add(a["link"])
            unique.append(a)
    artikel = unique

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel


GENERIC_TITLES = {"festlegung", "hinweise", "hinweises", "entscheidung", "einleitung", "erhebungsbogen", "mitteilung", "veröffentlichung", "feststellung", "anlage 1"}

def scrape_bk8(url):
    """Extrahiert Eintraege von Bundesnetzagentur BK8 Aktuell-Seite.
    Struktur: Tabelle mit Datum und Titel-Spalte.
    Wenn der Link-Text generisch ist (z.B. 'Festlegung'), wird der
    vollstaendige Zellentext als Titel verwendet.
    """
    artikel = []
    name = "BK8"
    print(f"Scrape: {name}")
    resp, soup = _fetch_page(url, name)
    if not soup:
        return artikel

    base_url = "https://www.bundesnetzagentur.de/"

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            last_cell = cells[-1]
            a = last_cell.find("a", href=True)
            if not a:
                continue
            titel = a.get_text(strip=True)
            href = str(a.get("href", ""))
            if not href:
                continue
            if not _filter_title(titel) or not titel:
                if not titel and a:
                    print(f"  WARNUNG: Leerer Titel fuer Link {href[:60]}")
                continue
            if titel.lower() in GENERIC_TITLES or len(titel) < 15:
                cell_text = last_cell.get_text(strip=True)
                if cell_text and len(cell_text) > len(titel):
                    titel = cell_text[:160].rstrip()
            full_url = urljoin(base_url, href)
            artikel.append({"title": titel, "link": full_url})

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel


def scrape_bmwe(url):
    """Extrahiert Pressemitteilungen von bundeswirtschaftsministerium.de.
    Struktur: .card-title hat den Titel, Parent enthaelt den Link.
    """
    artikel = []
    name = "BMWE"
    print(f"Scrape: {name}")
    resp, soup = _fetch_page(url, name)
    if not soup:
        return artikel

    for card_title in soup.select(".card-title"):
        titel = card_title.get_text(strip=True)
        # Link im Parent suchen
        a = None
        el = card_title
        for _ in range(5):
            el = el.parent
            if not el:
                break
            a = el.find("a", href=True)
            if a:
                break
        if not a:
            continue
        href = str(a.get("href", ""))
        if not _filter_title(titel) or not href:
            if not titel:
                print(f"  WARNUNG: Leerer Titel fuer Link {href[:60]}")
            continue
        # "Pressemitteilung:" Praefix entfernen
        if titel.lower().startswith("pressemitteilung:"):
            titel = titel[len("pressemitteilung:"):].strip()
        artikel.append({"title": titel, "link": href})

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel


SCRAPE_FUNCTIONS = {
    "amprion": scrape_amprion,
    "bundesnetzagentur": scrape_bk8,
    "bundeswirtschaftsministerium": scrape_bmwe,
}


def fetch_scrape(quelle):
    """Ruft eine Scraping-Quelle auf und gibt eine Liste von {title, link} zurueck."""
    name = quelle.get("name", "Unbekannt")
    url = quelle.get("url", "")

    if not url:
        print(f"WARNUNG: Quelle '{name}' hat keine URL, wird uebersprungen.")
        return []

    # Anhand der URL die passende Funktion waehlen
    funktion = None
    for domain, fn in SCRAPE_FUNCTIONS.items():
        if domain in url.lower():
            funktion = fn
            break

    if funktion is None:
        print(f"WARNUNG: Keine Scraping-Funktion fuer '{name}' ({url}) definiert.")
        return []

    print(f"Scrape Seite: {name} ({url})")
    return funktion(url)


def extract_scraped_article(scraped_entry, quelle):
    """Wandelt einen gescrapten Eintrag {title, link} in das einheitliche Artikel-Format um."""
    titel = scraped_entry.get("title", "Ohne Titel")
    link = scraped_entry.get("link", "")

    if not link:
        return None

    if not titel or not titel.strip() or titel == "Ohne Titel":
        quelle_name = quelle.get("name", "Unbekannt")
        print(f"  WARNUNG: Leerer Titel bei Quelle '{quelle_name}' fuer Link {link[:60]}")

    return {
        "titel": titel,
        "link": link,
        "datum": datetime.now().strftime("%Y-%m-%d"),
        "kategorie": quelle.get("category", "Allgemein"),
        "quelle": quelle.get("name", "Unbekannt"),
        "zusammenfassung": "",
    }


# ---------------------------------------------------------------------------
# Gemeinsame Logik
# ---------------------------------------------------------------------------

def seen_entry_from_artikel(artikel):
    """Erstellt einen seen.json-Eintrag aus einem Artikel-Objekt."""
    return {
        "date": artikel["datum"],
        "title": artikel["titel"],
        "source": artikel["quelle"],
        "category": artikel["kategorie"],
    }


def render_article_cards(artikel_liste):
    """Generiert HTML-Karten fuer eine Liste von Artikeln."""
    html = ""

    for a in artikel_liste:
        zusammenfassung_kurz = ""
        if a.get("zusammenfassung"):
            zusammenfassung_kurz = escape(a["zusammenfassung"][:150])
            if len(a["zusammenfassung"]) > 150:
                zusammenfassung_kurz += "..."

        html += f"""
                    <a href="{escape(a['link'])}" class="article-card" target="_blank" rel="noopener noreferrer">
                        <div class="article-meta">
                            <span class="article-source">{escape(a['quelle'])}</span>
                            <span class="article-date">{escape(a['datum'])}</span>
                        </div>
                        <h3 class="article-title">{escape(a['titel'])}</h3>
                        {f'<p class="article-summary">{zusammenfassung_kurz}</p>' if zusammenfassung_kurz else ''}
                    </a>"""

    return html


def render_categories(artikel_liste):
    """Gruppiert Artikel nach Kategorie und generiert HTML-Sektionen."""
    kategorien = {}
    for artikel in artikel_liste:
        kat = artikel["kategorie"]
        if kat not in kategorien:
            kategorien[kat] = []
        kategorien[kat].append(artikel)

    for kat in kategorien:
        kategorien[kat].sort(key=lambda x: x["datum"], reverse=True)

    sortierte_kategorien = sorted(kategorien.items())

    html = ""
    for kategorie, artikel in sortierte_kategorien:
        html += f"""
            <section class="category">
                <h2 class="category-title">{escape(kategorie)}</h2>
                <div class="articles">"""
        html += render_article_cards(artikel)
        html += """
                </div>
            </section>"""

    return html


def generate_html(heute_artikel):
    """Generiert die komplette index.html."""
    jetzt = datetime.now().strftime("%d.%m.%Y um %H:%M Uhr")

    if not heute_artikel:
        bereich_heute = """
            <div class="empty-state">
                <div class="empty-icon">&#128240;</div>
                <h2>Keine neuen Artikel</h2>
                <p>Es wurden heute keine neuen Artikel gefunden. Schau morgen wieder vorbei!</p>
            </div>"""
    else:
        bereich_heute = render_categories(heute_artikel)

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="data:,">
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
            {bereich_heute}
        </main>

        <footer>
            <p>DailyBriefing &mdash; Automatisch generiert mit GitHub Actions</p>
        </footer>
    </div>
</body>
</html>"""

    return html


def process_source(quelle, seen_data, heute_artikel, neue_artikel):
    """Verarbeitet eine einzelne Quelle (RSS oder Scrape) und aktualisiert die Listen."""
    queltyp = quelle.get("type", "rss")
    name = quelle.get("name", "Unbekannt")

    if queltyp == "rss":
        entries = fetch_feed(quelle)
        for entry in entries:
            artikel = extract_article(entry, quelle)
            if artikel is None:
                continue
            heute_artikel.append(artikel)
            url = artikel["link"]
            if url not in seen_data:
                neue_artikel.append(artikel)
                seen_data[url] = seen_entry_from_artikel(artikel)
            else:
                bestehend = seen_data[url]
                if isinstance(bestehend, dict) and not bestehend.get("title"):
                    seen_data[url] = seen_entry_from_artikel(artikel)

    elif queltyp == "scrape":
        entries = fetch_scrape(quelle)
        for entry in entries:
            artikel = extract_scraped_article(entry, quelle)
            if artikel is None:
                continue
            heute_artikel.append(artikel)
            url = artikel["link"]
            if url not in seen_data:
                neue_artikel.append(artikel)
                seen_data[url] = seen_entry_from_artikel(artikel)
            else:
                bestehend = seen_data[url]
                if isinstance(bestehend, dict) and not bestehend.get("title"):
                    seen_data[url] = seen_entry_from_artikel(artikel)

    else:
        print(f"WARNUNG: Unbekannter Quell-Typ '{queltyp}' bei '{name}', wird uebersprungen.")


def main():
    """Hauptfunktion: Quellen abrufen, neue Artikel finden, HTML generieren."""
    print("=" * 50)
    print("DailyBriefing - Abruf gestartet")
    print(f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print("=" * 50)

    # Quellen laden
    quellen = load_json(SOURCES_FILE, [])
    if not quellen:
        print("FEHLER: Keine Quellen in sources.json gefunden.")
        sys.exit(1)

    rss_count = sum(1 for q in quellen if q.get("type", "rss") == "rss")
    scrape_count = sum(1 for q in quellen if q.get("type") == "scrape")
    print(f"{len(quellen)} Quellen geladen ({rss_count} RSS, {scrape_count} Scrape).\n")

    # Bisher gesehene Artikel laden, migrieren und bereinigen
    seen_data = load_json(SEEN_FILE, {})
    seen_data = migrate_seen_format(seen_data)
    seen_data = cleanup_seen(seen_data)

    # Alle Quellen verarbeiten
    heute_artikel = []
    neue_artikel = []

    for quelle in quellen:
        try:
            process_source(quelle, seen_data, heute_artikel, neue_artikel)
        except Exception as e:
            print(f"FEHLER bei Quelle '{quelle.get('name', '?')}': {e}")

    # Ergebnis ausgeben
    print(f"\n{'=' * 50}")
    print(f"Angezeigt: {len(heute_artikel)} Artikel")
    print(f"Neu in seen.json: {len(neue_artikel)} Artikel")
    print(f"{'=' * 50}\n")

    # seen.json aktualisieren
    save_json(SEEN_FILE, seen_data)
    print(f"seen.json aktualisiert ({len(seen_data)} Eintraege).")

    # index.html generieren
    html_content = generate_html(heute_artikel)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"index.html generiert: {OUTPUT_FILE}")

    if neue_artikel:
        print(f"\nNeu in seen.json:")
        for artikel in neue_artikel:
            print(f"  - [{artikel['kategorie']}] {artikel['titel'][:80]}")

    print("\nFertig!")

    # Debug-Dateien aufraeumen
    _clean_debug_files()
    print("Debug-Dateien geloescht.")


if __name__ == "__main__":
    main()

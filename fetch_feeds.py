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
import re
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

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("WARNUNG: 'playwright' nicht installiert. BMWE-Scraper wird nicht funktionieren.")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(SCRIPT_DIR, "sources.json")
SEEN_FILE = os.path.join(SCRIPT_DIR, "seen.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "index.html")

MAX_SEEN_DAYS = 365
MAX_DISPLAY_DAYS = 2

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}

FULL_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
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


def _is_within_display_window(datum_str):
    """Prueft ob ein Artikel-Datum innerhalb des Anzeigezeitraums liegt (MAX_DISPLAY_DAYS)."""
    if not datum_str:
        return True
    try:
        artikel_datum = datetime.strptime(datum_str, "%Y-%m-%d")
        schwelle = datetime.now() - timedelta(days=MAX_DISPLAY_DAYS)
        return artikel_datum >= schwelle
    except ValueError:
        return True


SOURCE_SHORT = {
    "Amprion – Pressemitteilungen": "Amprion",
    "Bundesnetzagentur – Beschlusskammer 8": "BK8",
    "Bundesnetzagentur – Beschlusskammer 6": "BK6",
    "BMWE – Pressemitteilungen": "BMWE",
    "TenneT – News": "TenneT",
    "Netztransparenz – Aktuelles": "Netztransparenz",
}


def extract_seen_in_range(seen_data, von_datum, bis_datum):
    """Extrahiert Eintraege aus seen.json deren Datum im angegebenen Bereich liegt.
    Gibt Liste von dicts zurueck: {url, date, title, source, category}.
    """
    artikel = []
    for url, eintrag in seen_data.items():
        if not isinstance(eintrag, dict):
            continue
        datum_str = eintrag.get("date", "")
        if not datum_str:
            continue
        try:
            eintrags_datum = datetime.strptime(datum_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if von_datum <= eintrags_datum <= bis_datum:
            artikel.append({
                "url": url,
                "date": datum_str,
                "title": eintrag.get("title", ""),
                "source": eintrag.get("source", ""),
                "category": eintrag.get("category", ""),
            })
    return artikel


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

    if "rss.app" in link or quelle.get("name") == "TenneT – News":
        titel = _clean_tennet_title(titel)

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


def _clean_text(text):
    """Bereinigt Text: fuehrende/folgende Leerzeichen entfernen, mehrfache Leerzeichen zusammenfassen."""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _clean_tennet_title(titel):
    """Bereinigt TenneT-Titel vom rss.app Feed.
    Entfernt Datum-Prafix, Lesezeit-Angaben und ueberfluessige Pipes.
    """
    titel = re.sub(r'^\d{1,2}\.\s+\w+\s+\d{4}[\s|]*', '', titel)
    titel = re.sub(r'Lesezeit\s+\d+\s+Min\w*', '', titel)
    titel = titel.replace('|', '').strip()
    titel = re.sub(r'\s+', ' ', titel)
    return titel


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


def extract_date_generic(element):
    """Generische Datumsextraktion als Fallback fuer neue/ unbekannte Quellen.
    Versucht (in Reihenfolge):
      1. <time datetime="YYYY-MM-DD">
      2. DD.MM.YYYY im Text
      3. YYYY-MM-DD im Text
    Gibt das Datum als String "YYYY-MM-DD" zurueck oder "".
    """
    if not element:
        return ""
    time_tag = element.find("time")
    if time_tag and time_tag.get("datetime"):
        try:
            return datetime.strptime(time_tag["datetime"][:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    text = element.get_text()
    match = re.search(r'\d{2}\.\d{2}\.\d{4}', text)
    if match:
        try:
            return datetime.strptime(match.group(), "%d.%m.%Y").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    match = re.search(r'\d{4}-\d{2}-\d{2}', text)
    if match:
        try:
            return datetime.strptime(match.group(), "%Y-%m-%d").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    return ""


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
        titel = _clean_text(h3.get_text())
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
        published = ""
        date_div = parent.find("div", class_="mol--press-release__date") if parent else None
        if date_div:
            time_el = date_div.find("time")
            if time_el and time_el.get("datetime"):
                published = time_el["datetime"]
        if not published and parent:
            published = extract_date_generic(parent)
        artikel.append({"title": titel, "link": full_url, "published": published})

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


def scrape_bk8(url):
    """Extrahiert Eintraege von Bundesnetzagentur BK8 Aktuell-Seite.
    Struktur: Tabelle mit Datum und Titel-Spalte.
    Der Teasertext (vollstaendiger Zellentext) wird immer an den Titel angehaengt.
    Format: "[Titel] – [Teasertext]"
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
            titel = _clean_text(a.get_text())
            href = str(a.get("href", ""))
            if not href:
                continue
            if not _filter_title(titel) or not titel:
                if not titel and a:
                    print(f"  WARNUNG: Leerer Titel fuer Link {href[:60]}")
                continue
            cell_text = _clean_text(last_cell.get_text())
            if cell_text and len(cell_text) > len(titel):
                teaser = cell_text[len(titel):].strip()
                if teaser:
                    titel = f"{titel} – {teaser[:160].rstrip()}"
            titel = _clean_text(titel)
            full_url = urljoin(base_url, href)
            if "/BK08/" not in full_url and "/BK8" not in full_url:
                continue
            published = ""
            datum_text = _clean_text(cells[0].get_text())
            try:
                published = datetime.strptime(datum_text, "%d.%m.%Y").strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
            if not published:
                published = extract_date_generic(row)
            artikel.append({"title": titel, "link": full_url, "published": published})

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel


def scrape_bk6(url):
    """Extrahiert Eintraege von Bundesnetzagentur BK6 Aktuell-Seite.
    Struktur: Tabelle, erste Zelle hat Titel/Link, zweite Zelle hat Datum.
    """
    artikel = []
    name = "BK6"
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
            first_cell = cells[0]
            a = first_cell.find("a", href=True)
            if not a:
                continue
            titel = _clean_text(a.get_text())
            href = str(a.get("href", ""))
            if not href:
                continue
            if not _filter_title(titel) or not titel:
                continue
            cell_text = _clean_text(first_cell.get_text())
            if cell_text and len(cell_text) > len(titel):
                teaser = cell_text[len(titel):].strip()
                if teaser:
                    titel = f"{titel} – {teaser[:160].rstrip()}"
            titel = _clean_text(titel)
            full_url = urljoin(base_url, href)
            if "/BK06/" not in full_url and "/BK6" not in full_url:
                continue
            artikel.append({"title": titel, "link": full_url})

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel


def scrape_bmwe(url):
    """Extrahiert Pressemitteilungen von bundeswirtschaftsministerium.de
    via Playwright (headless Chromium), um Bot-Schutz zu umgehen.
    Struktur: li.media-space-list-item > p.card-topline > span.date,
              p.card-title > strong.card-title-label, a.card-link-overlay.
    """
    artikel = []
    name = "BMWE"
    print(f"Scrape: {name} (Playwright)")

    if not HAS_PLAYWRIGHT:
        print(f"  FEHLER: playwright nicht installiert. Quelle wird uebersprungen.")
        return artikel

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            page.wait_for_selector("li.media-space-list-item", timeout=20000)

            html = page.content()
            browser.close()
    except Exception as e:
        print(f"  FEHLER: Playwright fehlgeschlagen: {e}. Quelle wird uebersprungen.")
        return artikel

    soup = BeautifulSoup(html, "html.parser")
    base_url = "https://www.bundeswirtschaftsministerium.de"

    for item in soup.select("li.media-space-list-item"):
        titel_tag = item.select_one("strong.card-title-label")
        link_tag = item.select_one("a.card-link-overlay")
        datum_tag = item.select_one("span.date")

        if not titel_tag or not link_tag:
            continue

        titel = titel_tag.get_text(strip=True)
        link = link_tag.get("href", "")
        datum_text = datum_tag.get_text(strip=True) if datum_tag else ""

        if link.startswith("/"):
            link = base_url + link

        published = ""
        if datum_text:
            try:
                published = datetime.strptime(datum_text, "%d.%m.%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass

        artikel.append({"title": titel, "link": link, "published": published})

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel[:10]


def scrape_tennet(url):
    """Extrahiert News-Artikel von tennet.eu/de/news-de.
    Nutzt generische Extraktion mit extract_date_generic fuer Datum.
    Nutzt FULL_BROWSER_HEADERS gegen Bot-Schutz.
    """
    artikel = []
    name = "TenneT"
    print(f"Scrape: {name}")
    try:
        resp = requests.get(url, headers=FULL_BROWSER_HEADERS, timeout=20)
        print(f"  HTTP {resp.status_code}, {len(resp.content)} Bytes empfangen")
        print(f"  Response-Preview: {resp.text[:500]}")
        if resp.status_code in (403, 503):
            print(f"  FEHLER: Server blockiert Anfrage (HTTP {resp.status_code}) fuer '{name}'. Quelle wird uebersprungen.")
            return artikel
        if resp.status_code != 200:
            print(f"  FEHLER: Unerwarteter HTTP {resp.status_code} fuer '{name}'. Quelle wird uebersprungen.")
            return artikel
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  FEHLER beim Laden von '{name}': {e}. Quelle wird uebersprungen.")
        return artikel

    for a in soup.find_all("a", href=True):
        href = str(a.get("href", ""))
        if not href or href.startswith("#") or "javascript:" in href.lower():
            continue
        titel = _clean_text(a.get_text())
        if not _filter_title(titel) or len(titel) < 15:
            continue
        full_url = urljoin(url, href)
        if full_url == url:
            continue
        parent = a.parent
        published = extract_date_generic(parent) if parent else ""
        artikel.append({"title": titel, "link": full_url, "published": published})

    seen = set()
    unique = []
    for a in artikel:
        if a["link"] not in seen:
            seen.add(a["link"])
            unique.append(a)
    artikel = unique[:10]

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel


def fetch_netztransparenz_api(quelle):
    """Ruft die Netztransparenz-News-API ab und gibt Artikel zurueck.

    Die Seite ist eine SPA - Artikel werden per JSON-API geladen.
    Workflow:
      1. Login via auth/Login (anonymous, erfordert loginDto aus der Seite)
      2. POST newsItems/Get mit Filter-Payload
      3. Extrahiere Titel, Link, Datum aus der JSON-Antwort
    """
    artikel = []
    name = "Netztransparenz"
    base_url = quelle.get("url", "https://www.netztransparenz.de/de-de/Über-uns/Aktuelles/")
    hub_api = "https://www.netztransparenz.de/xspproxy/api/"
    login_dto = {
        "encryptedData": "paoIp2xm7WJ/9+z8/GVC4K4z9zoV6KORUBjFcVA6OgD3gninLAV7IBjtfXUWjK7V3EcEB+ivjOlqclOLGMcLTp+h4i8YWBZIxNGLgu8m7nb7thmyhvjRbcscFFQnx69ErS2CUK3phZv/NuRDEjfSUuoogyEkYxRP2bpKsZRXLkC8RNzFN9bck9MhyaW7yEOWkl4LiL3KMKkvrvfLb1znVFCQswd2cvWkPBZLix4PykE=",
        "iv": "NU9mYasHXthKNGXBTHOYDQ==",
    }
    client_id = "776e7a05-4990-4141-8bcb-df621dafc98d"

    print(f"API-Abruf: {name}")

    session = requests.Session()
    session.headers.update({
        "User-Agent": SCRAPE_HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    })

    try:
        lr = session.post(hub_api + "auth/Login", json=login_dto, timeout=20)
        print(f"  Login: HTTP {lr.status_code}")
        if lr.status_code != 200:
            print(f"  FEHLER: Login fehlgeschlagen (HTTP {lr.status_code}). Quelle wird uebersprungen.")
            return artikel
        login_data = lr.json()
        if not login_data.get("data", {}).get("success"):
            print(f"  FEHLER: Login nicht erfolgreich. Quelle wird uebersprungen.")
            return artikel
    except Exception as e:
        print(f"  FEHLER: Login-Anfrage fehlgeschlagen: {e}. Quelle wird uebersprungen.")
        return artikel

    payload = {
        "contains": "",
        "page": 1,
        "pageSize": 10,
        "descending": True,
        "type": 0,
        "clientId": client_id,
        "includeDefaultCategory": True,
        "languageTag": "de-DE",
        "showPublished": True,
        "showUnpublished": False,
    }

    try:
        r = session.post(hub_api + "newsItems/Get", json=payload, timeout=20)
        print(f"  newsItems/Get: HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  FEHLER: API-Fehler (HTTP {r.status_code}): {r.text[:300]}. Quelle wird uebersprungen.")
            return artikel
        data = r.json()
    except Exception as e:
        print(f"  FEHLER: API-Anfrage fehlgeschlagen: {e}. Quelle wird uebersprungen.")
        return artikel

    items = data.get("data", {}).get("items", [])
    if not items:
        print(f"  0 Artikel von API erhalten.")
        return artikel

    for item in items[:10]:
        title = ""
        slug = ""
        for content_entry in item.get("newsContentList", []):
            if content_entry.get("language") == "de-DE" or content_entry.get("baseLanguage"):
                title = content_entry.get("title", "")
                slug = content_entry.get("slugTitle", "")
                break
        if not title:
            continue

        article_id = item.get("id", "")
        detail_url = f"{base_url}Details/{article_id}/{slug}" if slug else f"{base_url}Details/{article_id}"

        date_str = ""
        publish_date = item.get("publishStartDate") or item.get("dateCreated")
        if publish_date:
            try:
                date_str = datetime.fromisoformat(publish_date.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        artikel.append({"title": title, "link": detail_url, "published": date_str})

    print(f"  {len(artikel)} Artikel extrahiert")
    return artikel


SCRAPE_FUNCTIONS = {
    "amprion": scrape_amprion,
    "bk8": scrape_bk8,
    "bk6": scrape_bk6,
    "bundesnetzagentur": scrape_bk8,
    "bundeswirtschaftsministerium": scrape_bmwe,
    "netztransparenz": None,
    "tennet": scrape_tennet,
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
    artikel = funktion(url)
    return artikel[:10]


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
        "published": scraped_entry.get("published", ""),
    }


# ---------------------------------------------------------------------------
# Gemeinsame Logik
# ---------------------------------------------------------------------------

def seen_entry_from_artikel(artikel):
    """Erstellt einen seen.json-Eintrag aus einem Artikel-Objekt."""
    entry = {
        "date": artikel["datum"],
        "title": artikel["titel"],
        "source": artikel["quelle"],
        "category": artikel["kategorie"],
    }
    if artikel.get("published"):
        entry["published"] = artikel["published"]
    return entry


def render_article_cards(artikel_liste):
    """Generiert HTML-Karten fuer eine Liste von Artikeln."""
    html = ""

    for a in artikel_liste:
        zusammenfassung_kurz = ""
        if a.get("zusammenfassung"):
            zusammenfassung_kurz = escape(a["zusammenfassung"][:150])
            if len(a["zusammenfassung"]) > 150:
                zusammenfassung_kurz += "..."

        anzeige_datum = a.get("published") or a["datum"]
        html += f"""
                    <a href="{escape(a['link'])}" class="article-card" target="_blank" rel="noopener noreferrer">
                        <div class="article-meta">
                            <span class="article-source">{escape(a['quelle'])}</span>
                            <span class="article-date">{escape(anzeige_datum)}</span>
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


def generate_html(heute_artikel, letzte7_artikel):
    """Generiert die komplette index.html mit zwei Bereichen: Heute und Letzte 7 Tage."""
    jetzt = datetime.now().strftime("%d.%m.%Y um %H:%M Uhr")
    heute_str = datetime.now().strftime("%Y-%m-%d")

    if not heute_artikel:
        bereich_heute = """
            <div class="empty-state">
                <div class="empty-icon">&#128240;</div>
                <h2>Keine neuen Artikel</h2>
                <p>Es wurden heute keine neuen Artikel gefunden. Schau morgen wieder vorbei!</p>
            </div>"""
    else:
        bereich_heute = render_categories(heute_artikel)

    bereich_letzte7 = ""
    if letzte7_artikel:
        letzte7_sortiert = sorted(letzte7_artikel, key=lambda x: x["date"], reverse=True)
        letzte7_html = ""
        for a in letzte7_sortiert:
            titel = escape(a.get("title", "") or "Ohne Titel")
            source_raw = a.get("source", "")
            source_short = SOURCE_SHORT.get(source_raw, source_raw)
            datum = a.get("published") or a.get("date", "")
            datum_str = f' <span class="recent-date">{escape(datum)}</span>' if datum else ""
            letzte7_html += f"""
                        <li>
                            <a href="{escape(a['url'])}" target="_blank" rel="noopener noreferrer">{titel}{datum_str}</a>
                            <span class="recent-source">{escape(source_short)}</span>
                        </li>"""
        bereich_letzte7 = f"""
        <details class="recent-section">
            <summary class="recent-summary">Letzte 7 Tage ({len(letzte7_artikel)} Artikel)</summary>
            <ul class="recent-list">{letzte7_html}
            </ul>
        </details>"""

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

        .recent-section {{
            margin-top: 48px;
            border: 1px solid #e0e0e0;
            border-radius: 12px;
            background: #ffffff;
        }}

        .recent-summary {{
            padding: 16px 20px;
            font-size: 1.1rem;
            font-weight: 600;
            color: #1d1d1f;
            cursor: pointer;
            list-style: none;
        }}

        .recent-summary::-webkit-details-marker {{
            display: none;
        }}

        .recent-summary::before {{
            content: "▸ ";
            color: #86868b;
        }}

        .recent-section[open] .recent-summary::before {{
            content: "▾ ";
        }}

        .recent-list {{
            list-style: none;
            padding: 0 20px 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}

        .recent-list li {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 12px;
            padding: 6px 0;
            border-bottom: 1px solid #f0f0f0;
        }}

        .recent-list li:last-child {{
            border-bottom: none;
        }}

        .recent-list a {{
            text-decoration: none;
            color: #1d1d1f;
            font-size: 0.9rem;
            line-height: 1.4;
        }}

        .recent-list a:hover {{
            color: #0071e3;
        }}

        .recent-source {{
            font-size: 0.75rem;
            color: #86868b;
            white-space: nowrap;
        }}

        .recent-date {{
            font-size: 0.7rem;
            color: #86868b;
            margin-left: 4px;
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
            .recent-list li {{
                flex-direction: column;
                gap: 2px;
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
            {bereich_letzte7}
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
            url = artikel["link"]
            is_new = url not in seen_data
            if is_new:
                seen_data[url] = seen_entry_from_artikel(artikel)
                if not _is_within_display_window(artikel["datum"]):
                    print(f"  Uebersprungen (aelter als {MAX_DISPLAY_DAYS} Tage): {artikel['titel'][:70]}")
                    continue
                neue_artikel.append(artikel)
            else:
                bestehend = seen_data[url]
                if isinstance(bestehend, dict) and not bestehend.get("title"):
                    seen_data[url] = seen_entry_from_artikel(artikel)
                continue
            heute_artikel.append(artikel)

    elif queltyp == "scrape":
        entries = fetch_scrape(quelle)
        for entry in entries:
            artikel = extract_scraped_article(entry, quelle)
            if artikel is None:
                continue
            url = artikel["link"]
            is_new = url not in seen_data
            if is_new:
                seen_data[url] = seen_entry_from_artikel(artikel)
                if not _is_within_display_window(artikel["datum"]):
                    print(f"  Uebersprungen (aelter als {MAX_DISPLAY_DAYS} Tage): {artikel['titel'][:70]}")
                    continue
                neue_artikel.append(artikel)
            else:
                bestehend = seen_data[url]
                if isinstance(bestehend, dict) and not bestehend.get("title"):
                    seen_data[url] = seen_entry_from_artikel(artikel)
                continue
            heute_artikel.append(artikel)

    elif queltyp == "api":
        entries = fetch_netztransparenz_api(quelle)
        for entry in entries:
            artikel = extract_scraped_article(entry, quelle)
            if artikel is None:
                continue
            url = artikel["link"]
            is_new = url not in seen_data
            if is_new:
                seen_data[url] = seen_entry_from_artikel(artikel)
                if not _is_within_display_window(artikel["datum"]):
                    print(f"  Uebersprungen (aelter als {MAX_DISPLAY_DAYS} Tage): {artikel['titel'][:70]}")
                    continue
                neue_artikel.append(artikel)
            else:
                bestehend = seen_data[url]
                if isinstance(bestehend, dict) and not bestehend.get("title"):
                    seen_data[url] = seen_entry_from_artikel(artikel)
                continue
            heute_artikel.append(artikel)

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
    api_count = sum(1 for q in quellen if q.get("type") == "api")
    print(f"{len(quellen)} Quellen geladen ({rss_count} RSS, {scrape_count} Scrape, {api_count} API).\n")

    # Bisher gesehene Artikel laden, migrieren und bereinigen
    seen_data = load_json(SEEN_FILE, {})
    seen_data = migrate_seen_format(seen_data)
    seen_data = cleanup_seen(seen_data)

    # Alle Quellen verarbeiten
    heute_artikel = []
    neue_artikel = []

    for quelle in quellen:
        if quelle.get("disabled"):
            print(f"UEBERSPRUNGEN: Quelle '{quelle.get('name', '?')}' ist deaktiviert.")
            continue
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

    # Letzte-7-Tage-Artikel aus seen.json extrahieren
    heute_date = datetime.now().date()
    von_datum = heute_date - timedelta(days=7)
    bis_datum = heute_date - timedelta(days=1)
    letzte7_artikel = extract_seen_in_range(seen_data, von_datum, bis_datum)
    print(f"Letzte 7 Tage: {len(letzte7_artikel)} Artikel aus seen.json.")

    # index.html generieren
    html_content = generate_html(heute_artikel, letzte7_artikel)

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

import requests
from bs4 import BeautifulSoup
import time
import json
import os
from datetime import datetime
from pathlib import Path
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─────────────────────────────────────────────
#  Konfiguracja ze zmiennych środowiskowych
# ─────────────────────────────────────────────
BASE_URL       = "https://www.autazeszwajcarii.pl/aukcje/?type=&brand=&run_from=&run_to=&production_date_from=&production_date_to=&phrase="
EMAIL_SENDER   = "firmowypprolki@gmail.com"
SMTP_SERVER    = "smtp.gmail.com"
SMTP_PORT      = 587

# Zmienne środowiskowe w Railway Dashboard:
#   EMAIL_PASSWORD   → hasło aplikacji Gmail
#   EMAIL_RECIPIENT  → adres email odbiorcy
#   BRANDS           → np. "BMW,Audi,Toyota"
#   INTERVAL_MINUTES → co ile minut (domyślnie 5)
#
# Volume: zamontuj w Railway pod /data
# Plik z historią zapisze się do /data/ogloszenia.json

EMAIL_PASSWORD   = os.environ.get("EMAIL_PASSWORD", "")
RECIPIENT        = os.environ.get("EMAIL_RECIPIENT", "")
BRANDS_RAW       = os.environ.get("BRANDS", "")
INTERVAL_MINUTES = int(os.environ.get("INTERVAL_MINUTES", "5"))
INTERVAL         = INTERVAL_MINUTES * 60

DATA_FILE = Path("/data/ogloszenia.json")


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def validate_config():
    errors = []
    if not RECIPIENT or "@" not in RECIPIENT:
        errors.append("Brak lub nieprawidlowy EMAIL_RECIPIENT")
    if not EMAIL_PASSWORD:
        errors.append("Brak EMAIL_PASSWORD")
    if not BRANDS_RAW.strip():
        errors.append("Brak zmiennej BRANDS (np. BMW,Audi)")
    if not DATA_FILE.parent.exists():
        errors.append(
            f"Katalog {DATA_FILE.parent} nie istnieje — "
            "dodaj Volume w Railway i zamontuj pod /data"
        )
    if errors:
        for e in errors:
            log(f"BLAD KONFIGURACJI: {e}")
        raise SystemExit(1)


def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_data(known):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(known, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"BLAD zapisu danych: {e}")


def fetch(session, url):
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log(f"Blad pobierania {url}: {e}")
        return None


def get_main_image(session, listing_url):
    try:
        html = fetch(session, listing_url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not src:
                continue
            if any(x in src.lower() for x in ["flag", "logo", "icon", "favicon"]):
                continue
            if ".jpg" in src or ".jpeg" in src or ".png" in src:
                if not src.startswith("http"):
                    src = f"https://autazeszwajcarii.pl{src}"
                return src
    except Exception:
        pass
    return None


def parse(session, html, source_name):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    links = soup.find_all("a", href=lambda x: x and "/licytacja/" in x)
    for link in links:
        try:
            href = link.get("href")
            full_url = href if href.startswith("http") else f"https://autazeszwajcarii.pl{href}"
            parent = link.find_parent(["div", "article"])
            title_tag = parent.find("h4") if parent else None
            title = title_tag.get_text(strip=True) if title_tag else link.get_text(strip=True)
            if not title or "Wyroznionie" in title or len(title) < 3:
                continue
            listing_id = hashlib.md5(full_url.encode()).hexdigest()
            image_url = get_main_image(session, full_url)
            listings.append({
                "id":     listing_id,
                "title":  title,
                "link":   full_url,
                "source": source_name,
                "image":  image_url,
            })
        except Exception:
            continue
    return listings


def detect_new(known, listings):
    new = []
    for item in listings:
        if item["id"] not in known:
            known[item["id"]] = {
                "title":    item["title"],
                "link":     item["link"],
                "image":    item["image"],
                "source":   item["source"],
                "found_at": datetime.now().isoformat(),
            }
            new.append(item)
    return new


def send_email(recipient, new_listings):
    if not new_listings:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{len(new_listings)} nowe ogloszenie(a) — Auto Monitor"
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = recipient

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; }}
                .container {{ max-width: 1400px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.2); }}
                .header {{ background: linear-gradient(135deg, #3e5dff 0%, #2a3bcc 100%); padding: 40px 20px; text-align: center; color: white; }}
                .header h1 {{ margin: 0; font-size: 32px; font-weight: bold; }}
                .header p {{ margin: 10px 0 0 0; font-size: 16px; opacity: 0.9; }}
                .content {{ padding: 30px; }}
                .listings {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }}
                @media (max-width: 1200px) {{ .listings {{ grid-template-columns: repeat(3, 1fr); }} }}
                @media (max-width: 768px)  {{ .listings {{ grid-template-columns: repeat(2, 1fr); }} }}
                @media (max-width: 480px)  {{ .listings {{ grid-template-columns: 1fr; }} }}
                .listing-card {{ background: #f9f9f9; border-radius: 10px; overflow: hidden; border-left: 4px solid #3e5dff; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
                .listing-image {{ width: 100%; height: 160px; background: #e0e0e0; object-fit: cover; display: block; }}
                .listing-content {{ padding: 15px; }}
                .listing-title {{ font-size: 14px; font-weight: bold; color: #333; margin: 0 0 8px 0; line-height: 1.3; }}
                .listing-brand {{ font-size: 12px; color: #3e5dff; font-weight: 600; margin: 6px 0; }}
                .listing-link {{ display: inline-block; background: linear-gradient(135deg, #3e5dff, #2a3bcc); color: white; padding: 8px 12px; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 12px; margin-top: 8px; }}
                .footer {{ text-align: center; padding: 25px 20px; background: #f5f5f5; border-top: 1px solid #ddd; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Nowe ogłoszenia samochodów</h1>
                    <p>Znaleziono {len(new_listings)} nowych ogłoszeń</p>
                    <p>{datetime.now().strftime('%d.%m.%Y o %H:%M')}</p>
                </div>
                <div class="content">
                    <div class="listings">
        """

        for item in new_listings:
            img_html = (
                f'<img src="{item["image"]}" class="listing-image">'
                if item.get("image")
                else '<div class="listing-image"></div>'
            )
            html += f"""
                        <div class="listing-card">
                            {img_html}
                            <div class="listing-content">
                                <h3 class="listing-title">{item["title"]}</h3>
                                <div class="listing-brand">Marka: {item["source"]}</div>
                                <a href="{item["link"]}" class="listing-link">Otwórz</a>
                            </div>
                        </div>
            """

        html += """
                    </div>
                </div>
                <div class="footer">
                    <p>Auto Monitor — Telarek ©</p>
                    <p>Email wysłany automatycznie</p>
                </div>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        log(f"Email wyslany do {recipient} ({len(new_listings)} ogloszen)")
    except Exception as e:
        log(f"Blad emaila: {e}")


def run_monitor(recipient, brands, interval):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    sites = {brand.strip(): f"{BASE_URL}{brand.strip()}" for brand in brands}

    log("=" * 55)
    log("AUTO MONITOR uruchomiony — backend: Railway Volume")
    log(f"Plik danych    : {DATA_FILE}")
    log(f"Email odbiorcy : {recipient}")
    log(f"Marki          : {', '.join(sites.keys())}")
    log(f"Interwal       : co {interval // 60} minut")
    log("=" * 55)

    while True:
        known = load_data()
        log("Rozpoczynam sprawdzanie ogloszen...")
        all_new = []

        for name, url in sites.items():
            log(f"  Sprawdzam: {name}")
            html = fetch(session, url)
            if not html:
                continue
            listings = parse(session, html, name)
            log(f"  Znaleziono {len(listings)} ogloszen dla {name}")
            new = detect_new(known, listings)
            if new:
                log(f"  >>> {len(new)} NOWYCH dla {name}!")
                all_new.extend(new)
            else:
                log(f"  Brak nowych dla {name}")

        if all_new:
            log(f"RAZEM: {len(all_new)} nowych! Zapisuje i wysylam email...")
            save_data(known)
            send_email(recipient, all_new)
        else:
            log("Brak nowych ogloszen w tej rundzie.")

        log(f"Nastepne sprawdzenie za {interval // 60} min. Czekam...")
        time.sleep(interval)


if __name__ == "__main__":
    validate_config()
    brands = [b.strip() for b in BRANDS_RAW.split(",") if b.strip()]
    run_monitor(RECIPIENT, brands, INTERVAL)

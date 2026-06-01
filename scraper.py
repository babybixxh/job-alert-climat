import smtplib
import os
import json
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup

KEYWORDS = [
    "consultant climat", "stratégie climat", "bilan carbone",
    "transition écologique", "RSE climat", "politiques climat",
    "chargé de mission climat", "décarbonation"
]

LOCATIONS = ["Marseille", "télétravail", "remote"]

SOURCES = [
    {
        "name": "Welcome to the Jungle",
        "url": "https://www.welcometothejungle.com/fr/jobs?query={keyword}&aroundQuery={location}",
    },
    {
        "name": "Indeed",
        "url": "https://fr.indeed.com/jobs?q={keyword}&l={location}",
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def search_indeed(keyword, location):
    jobs = []
    url = f"https://fr.indeed.com/jobs?q={keyword}&l={location}&fromage=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.find_all("div", class_="job_seen_beacon")
        for card in cards[:5]:
            title_el = card.find("h2", class_="jobTitle")
            company_el = card.find("span", {"data-testid": "company-name"})
            location_el = card.find("div", {"data-testid": "text-location"})
            link_el = card.find("a", href=True)
            if title_el:
                jobs.append({
                    "title": title_el.get_text(strip=True),
                    "company": company_el.get_text(strip=True) if company_el else "N/A",
                    "location": location_el.get_text(strip=True) if location_el else location,
                    "url": "https://fr.indeed.com" + link_el["href"] if link_el else url,
                    "source": "Indeed"
                })
    except Exception as e:
        print(f"Erreur Indeed ({keyword}, {location}): {e}")
    return jobs

def search_wttj(keyword, location):
    jobs = []
    url = f"https://www.welcometothejungle.com/fr/jobs?query={keyword}&aroundQuery={location}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.find_all("li", {"data-testid": "search-results-list-item-wrapper"})
        for card in cards[:5]:
            title_el = card.find("h3")
            company_el = card.find("strong")
            link_el = card.find("a", href=True)
            if title_el:
                jobs.append({
                    "title": title_el.get_text(strip=True),
                    "company": company_el.get_text(strip=True) if company_el else "N/A",
                    "location": location,
                    "url": "https://www.welcometothejungle.com" + link_el["href"] if link_el else url,
                    "source": "Welcome to the Jungle"
                })
    except Exception as e:
        print(f"Erreur WTTJ ({keyword}, {location}): {e}")
    return jobs

def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = (job["title"].lower(), job["company"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique

def build_email(jobs):
    today = datetime.now().strftime("%d/%m/%Y")
    if not jobs:
        body = f"<h2>🌱 Alerte emploi climat — {today}</h2><p>Aucune nouvelle offre trouvée aujourd'hui.</p>"
        return body

    body = f"<h2>🌱 Alerte emploi climat — {today}</h2>"
    body += f"<p><strong>{len(jobs)} offre(s) trouvée(s)</strong></p><hr>"

    for job in jobs:
        body += f"""
        <div style="margin-bottom:20px; padding:15px; border-left:4px solid #2d6a4f; background:#f9f9f9;">
            <h3 style="margin:0"><a href="{job['url']}" style="color:#2d6a4f">{job['title']}</a></h3>
            <p style="margin:5px 0">🏢 {job['company']} &nbsp;|&nbsp; 📍 {job['location']} &nbsp;|&nbsp; 🔍 {job['source']}</p>
        </div>
        """
    return body

def send_email(html_body, job_count):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_PASSWORD"]
    gmail_to = os.environ["GMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌱 {job_count} offre(s) climat aujourd'hui — {datetime.now().strftime('%d/%m/%Y')}"
    msg["From"] = gmail_user
    msg["To"] = gmail_to
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, gmail_to, msg.as_string())
    print("Email envoyé !")

if __name__ == "__main__":
    all_jobs = []
    for keyword in KEYWORDS:
        for location in LOCATIONS:
            all_jobs += search_indeed(keyword, location)
            all_jobs += search_wttj(keyword, location)

    jobs = deduplicate(all_jobs)
    print(f"{len(jobs)} offres uniques trouvées")
    html = build_email(jobs)
    send_email(html, len(jobs))

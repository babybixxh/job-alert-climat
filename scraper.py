import smtplib
import os
import json
import requests
from bs4 import BeautifulSoup
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

KEYWORDS = [
    "consultant climat",
    "bilan carbone",
    "transition écologique",
    "chargé mission climat",
    "décarbonation",
    "RSE climat",
    "politiques climatiques",
]

LOCATIONS = ["Paris", "Marseille", "Aix-en-Provence", "Toulon", "Nice"]

EXCLUSIONS = [
    "stage", "alternance", "alternant", "alternan", "apprentissage", "apprenti",
    "intern", "junior", "en alternance", "en stage", "contrat pro",
    "contrat d'apprentissage", "bac+2", "bac+3", "débutant accepté",
    "travaux", "moe", "chantier", "réhabilitation", "urbaniste",
    "collecte", "nettoiement", "assainissement", "exploitation eau",
    "inspection itv", "ouvrages d'art", "frigoriste", "polissage",
    "électromécanicien", "electro mec",
    "technicien", "technicienne", "technician",
    "opérateur", "opératrice", "agent de",
    "conducteur d'engins", "chauffeur",
    "acheteur", "achats", "procurement", "comptabilité", "comptable",
    "amoa finance", "avant-vente", "présales",
    "recrutement", "chargé de recrutement", "ressources humaines",
    "nucléaire", "nucl", "hydraulique moe", "calcul mécanique",
    "aéronautique", "aeronautics", "vessel", "optique",
    "regulatory affairs", "r&d procédés",
    "régisseur", "régisseuse", "vidéo", "delivery manager",
    "responsable bureau d'études",
    "préparateur coordinateur", "chef de projet urbanisme",
    "ingénieur travaux", "ingénieur calcul", "ingénieur hydraulique",
    "ingénieur mécanique", "projeteur",
    "paysagiste", "paysager", "espaces verts",
]

SEEN_FILE = "seen_jobs.json"
TODAY_FILE = "today_jobs.json"
REJECTED_FILE = "rejected_keywords.json"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_exclusions():
    base = list(EXCLUSIONS)
    rejected = load_json(REJECTED_FILE, [])
    return base + rejected


def search_adzuna(keyword, location):
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    exclusions = get_exclusions()

    url = (
        f"https://api.adzuna.com/v1/api/jobs/fr/search/1"
        f"?app_id={app_id}&app_key={app_key}"
        f"&results_per_page=10"
        f"&what={requests.utils.quote(keyword)}"
        f"&where={requests.utils.quote(location)}"
        f"&max_days_old=7"
        f"&content-type=application/json"
    )

    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if "exception" in data:
            print(f"  ERREUR API: {data['exception']}")
            return []
        results = data.get("results", [])
        print(f"  Adzuna '{keyword}' / '{location}' → {len(results)} brutes")
        jobs = []
        for job in results:
            title_lower = job.get("title", "").lower()
            if any(excl in title_lower for excl in exclusions):
                print(f"  Exclu: {job.get('title')}")
                continue
            jobs.append({
                "id": str(job.get("id", "")),
                "title": job.get("title", "N/A"),
                "company": job.get("company", {}).get("display_name", "N/A"),
                "location": job.get("location", {}).get("display_name", location),
                "url": job.get("redirect_url", ""),
                "description": job.get("description", "")[:150] + "...",
                "source": "Adzuna",
            })
        return jobs
    except Exception as e:
        print(f"  EXCEPTION Adzuna: {e}")
        return []


def search_wttj(keyword, location):
    url = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries"
    headers = {
        "X-Algolia-Application-Id": "csekhvms53",
        "X-Algolia-API-Key": "9ba3b23b73aef34bf37f79c17e8a4e4f",
        "Content-Type": "application/json",
    }
    payload = {
        "requests": [{
            "indexName": "wttj_jobs_production_fr",
            "params": f"query={requests.utils.quote(keyword)}&aroundQuery={requests.utils.quote(location)}&aroundRadius=50000&hitsPerPage=10&filters=contract_type%3AFULL_TIME%20OR%20contract_type%3APART_TIME"
        }]
    }
    exclusions = get_exclusions()
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        data = r.json()
        hits = data.get("results", [{}])[0].get("hits", [])
        print(f"  WTTJ '{keyword}' / '{location}' → {len(hits)} brutes")
        jobs = []
        for hit in hits:
            title = hit.get("name", "N/A")
            if any(excl in title.lower() for excl in exclusions):
                print(f"  Exclu WTTJ: {title}")
                continue
            company = hit.get("organization", {}).get("name", "N/A")
            city = hit.get("office", {}).get("city", location)
            slug_company = hit.get("organization", {}).get("slug", "")
            slug_job = hit.get("slug", "")
            jobs.append({
                "id": hit.get("objectID", slug_job),
                "title": title,
                "company": company,
                "location": city,
                "url": f"https://www.welcometothejungle.com/fr/companies/{slug_company}/jobs/{slug_job}",
                "description": hit.get("description", "")[:150] + "...",
                "source": "Welcome to the Jungle",
            })
        return jobs
    except Exception as e:
        print(f"  EXCEPTION WTTJ: {e}")
        return []


def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = (job["title"].lower(), job["company"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def mark_seen(jobs, seen_ids):
    for job in jobs:
        key = f"{job['title'].lower()}|{job['company'].lower()}"
        job["is_new"] = key not in seen_ids
    return jobs


def categorize(jobs):
    marseille, paca, paris = [], [], []
    for job in jobs:
        loc = job["location"].lower()
        if "marseille" in loc:
            marseille.append(job)
        elif any(city in loc for city in ["aix", "toulon", "nice", "provence", "paca", "var", "alpes"]):
            paca.append(job)
        else:
            paris.append(job)
    return marseille, paca, paris


def section_html(title, emoji, jobs, color):
    if not jobs:
        return ""
    new_count = sum(1 for j in jobs if j.get("is_new"))
    html = f"""
    <div style="margin:2rem 0 1rem">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:1rem">
            <span style="font-size:20px">{emoji}</span>
            <h2 style="margin:0;font-size:17px;font-weight:500;color:{color}">{title}</h2>
            <span style="font-size:13px;color:#888;background:#f0f0f0;padding:2px 10px;border-radius:20px">{len(jobs)} offre(s)</span>
            {f'<span style="font-size:13px;color:#fff;background:#e05c2a;padding:2px 10px;border-radius:20px">🆕 {new_count} nouvelle(s)</span>' if new_count else ''}
        </div>
    """
    for job in jobs:
        is_new = job.get("is_new", True)
        badge_new = '<span style="font-size:11px;color:#fff;background:#e05c2a;padding:1px 8px;border-radius:10px;margin-left:8px">NOUVEAU</span>' if is_new else '<span style="font-size:11px;color:#888;background:#f0f0f0;padding:1px 8px;border-radius:10px;margin-left:8px">Déjà vu</span>'
        source_color = "#4a90a4" if job.get("source") == "Adzuna" else "#7b5ea7"
        badge_source = f'<span style="font-size:11px;color:#fff;background:{source_color};padding:1px 8px;border-radius:10px;margin-left:6px">{job.get("source","")}</span>'
        html += f"""
        <div style="margin-bottom:16px;padding:14px;border-left:4px solid {color};background:{'#fff8f5' if is_new else '#f9f9f9'};border-radius:4px">
            <h3 style="margin:0 0 6px 0">
                <a href="{job['url']}" style="color:{color};text-decoration:none">{job['title']}</a>
                {badge_new}{badge_source}
            </h3>
            <p style="margin:0 0 5px 0;color:#555;font-size:14px">
                🏢 <strong>{job['company']}</strong> &nbsp;|&nbsp; 📍 {job['location']}
            </p>
            <p style="margin:0;font-size:13px;color:#777">{job['description']}</p>
        </div>
        """
    html += "</div>"
    return html


def build_email(jobs, feedback_url):
    today = datetime.now().strftime("%d/%m/%Y")
    marseille, paca, paris = categorize(jobs)
    total = len(jobs)
    new_total = sum(1 for j in jobs if j.get("is_new"))

    if not total:
        return f"""
        <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:20px">
        <h2 style="color:#2d6a4f">🌱 Alerte emploi climat — {today}</h2>
        <p>Aucune nouvelle offre trouvée aujourd'hui.</p>
        </body></html>
        """

    body = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:20px">
    <h2 style="color:#2d6a4f">🌱 Alerte emploi climat — {today}</h2>
    <p style="color:#555">{total} offre(s) dont <strong style="color:#e05c2a">{new_total} nouvelle(s)</strong> — Marseille ({len(marseille)}) · PACA ({len(paca)}) · Paris ({len(paris)})</p>
    <a href="{feedback_url}" style="display:inline-block;margin:8px 0 16px;padding:10px 20px;background:#2d6a4f;color:#fff;border-radius:6px;text-decoration:none;font-size:14px">
        👎 Signaler des offres non pertinentes
    </a>
    <hr style="border:1px solid #e0e0e0">
    """

    body += section_html("Marseille", "🔵", marseille, "#0f6e56")
    if marseille and paca:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    body += section_html("Région PACA hors Marseille", "🟢", paca, "#3b6d11")
    if (marseille or paca) and paris:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    body += section_html("Paris", "🔴", paris, "#993c1d")
    body += "</body></html>"
    return body


def send_email(html_body, job_count):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_PASSWORD"]
    gmail_to = os.environ["GMAIL_TO"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌱 {job_count} offre(s) climat — {datetime.now().strftime('%d/%m/%Y')}"
    msg["From"] = gmail_user
    msg["To"] = gmail_to
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, gmail_to, msg.as_string())
    print(f"Email envoyé avec {job_count} offres !")


if __name__ == "__main__":
    seen_ids = set(load_json(SEEN_FILE, []))
    print(f"{len(seen_ids)} offres déjà vues en mémoire")

    all_jobs = []
    for keyword in KEYWORDS:
        for location in LOCATIONS:
            all_jobs += search_adzuna(keyword, location)
            all_jobs += search_wttj(keyword, location)

    jobs = deduplicate(all_jobs)
    jobs = mark_seen(jobs, seen_ids)

    new_seen = seen_ids | {f"{j['title'].lower()}|{j['company'].lower()}" for j in jobs}
    save_json(SEEN_FILE, list(new_seen))
    save_json(TODAY_FILE, jobs)

    repo = os.environ.get("GITHUB_REPOSITORY", "babybixxh/job-alert-climat")
    feedback_url = f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/feedback.html"

    print(f"\nTotal : {len(jobs)} offres uniques")
    html = build_email(jobs, feedback_url)
    send_email(html, len(jobs))

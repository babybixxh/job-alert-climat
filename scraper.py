import smtplib
import os
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

KEYWORDS = [
    "consultant climat",
    "stratégie climat",
    "bilan carbone",
    "transition écologique",
    "chargé mission climat",
    "décarbonation",
    "RSE climat",
    "politiques climatiques",
]

LOCATIONS = ["Marseille", "teleravail", "remote"]

def search_adzuna(keyword, location):
    app_id = os.environ["ADZUNA_APP_ID"]
    app_key = os.environ["ADZUNA_APP_KEY"]
    jobs = []
    url = (
        f"https://api.adzuna.com/v1/api/jobs/fr/search/1"
        f"?app_id={app_id}&app_key={app_key}"
        f"&results_per_page=5"
        f"&what={requests.utils.quote(keyword)}"
        f"&where={requests.utils.quote(location)}"
        f"&max_days_old=1"
        f"&content-type=application/json"
    )
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        for job in data.get("results", []):
            jobs.append({
                "title": job.get("title", "N/A"),
                "company": job.get("company", {}).get("display_name", "N/A"),
                "location": job.get("location", {}).get("display_name", location),
                "url": job.get("redirect_url", ""),
                "description": job.get("description", "")[:150] + "...",
                "source": "Adzuna"
            })
    except Exception as e:
        print(f"Erreur Adzuna ({keyword}, {location}): {e}")
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
        body = f"""
        <h2>🌱 Alerte emploi climat — {today}</h2>
        <p>Aucune nouvelle offre trouvée aujourd'hui. Réessaie demain !</p>
        """
        return body

    body = f"<h2>🌱 Alerte emploi climat — {today}</h2>"
    body += f"<p><strong>{len(jobs)} offre(s) trouvée(s)</strong></p><hr>"

    for job in jobs:
        body += f"""
        <div style="margin-bottom:20px; padding:15px; border-left:4px solid #2d6a4f; background:#f9f9f9;">
            <h3 style="margin:0">
                <a href="{job['url']}" style="color:#2d6a4f; text-decoration:none">{job['title']}</a>
            </h3>
            <p style="margin:5px 0; color:#555">
                🏢 {job['company']} &nbsp;|&nbsp; 📍 {job['location']}
            </p>
            <p style="margin:5px 0; font-size:13px; color:#777">{job['description']}</p>
        </div>
        """
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
    all_jobs = []
    for keyword in KEYWORDS:
        for location in LOCATIONS:
            found = search_adzuna(keyword, location)
            print(f"  '{keyword}' / '{location}' → {len(found)} offres")
            all_jobs += found

    jobs = deduplicate(all_jobs)
    print(f"\nTotal : {len(jobs)} offres uniques")
    html = build_email(jobs)
    send_email(html, len(jobs))

import smtplib
import os
import requests
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

EXCLUSIONS = ["stage", "alternance", "apprentissage", "intern", "junior"]


def search_adzuna(keyword, location):
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")

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
            print(f"  ERREUR API: {data['exception']} - {data.get('display', '')}")
            return []

        results = data.get("results", [])
        print(f"  '{keyword}' / '{location}' → {len(results)} offres brutes")

        jobs = []
        for job in results:
            title_lower = job.get("title", "").lower()
            if any(excl in title_lower for excl in EXCLUSIONS):
                print(f"  Exclu: {job.get('title')}")
                continue
            jobs.append({
                "title": job.get("title", "N/A"),
                "company": job.get("company", {}).get("display_name", "N/A"),
                "location": job.get("location", {}).get("display_name", location),
                "url": job.get("redirect_url", ""),
                "description": job.get("description", "")[:150] + "...",
            })
        return jobs

    except Exception as e:
        print(f"  EXCEPTION: {e}")
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


def build_email(jobs):
    today = datetime.now().strftime("%d/%m/%Y")
    if not jobs:
        return f"""
        <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:20px">
        <h2 style="color:#2d6a4f">🌱 Alerte emploi climat — {today}</h2>
        <p>Aucune nouvelle offre trouvée aujourd'hui à Paris ou en PACA. Réessaie demain !</p>
        </body></html>
        """

    body = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:20px">
    <h2 style="color:#2d6a4f">🌱 Alerte emploi climat — {today}</h2>
    <p style="color:#555"><strong>{len(jobs)} offre(s)</strong> trouvée(s) à Paris et en PACA</p>
    <hr style="border:1px solid #e0e0e0">
    """

    for job in jobs:
        body += f"""
        <div style="margin-bottom:20px;padding:15px;border-left:4px solid #2d6a4f;background:#f9f9f9;border-radius:4px">
            <h3 style="margin:0 0 8px 0">
                <a href="{job['url']}" style="color:#2d6a4f;text-decoration:none">{job['title']}</a>
            </h3>
            <p style="margin:0 0 6px 0;color:#555;font-size:14px">
                🏢 <strong>{job['company']}</strong> &nbsp;|&nbsp; 📍 {job['location']}
            </p>
            <p style="margin:0;font-size:13px;color:#777">{job['description']}</p>
        </div>
        """

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
    all_jobs = []
    for keyword in KEYWORDS:
        for location in LOCATIONS:
            found = search_adzuna(keyword, location)
            all_jobs += found

    jobs = deduplicate(all_jobs)
    print(f"\nTotal : {len(jobs)} offres uniques")
    html = build_email(jobs)
    send_email(html, len(jobs))

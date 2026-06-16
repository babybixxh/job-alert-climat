import smtplib
import os
import json
import requests
import re
from html import unescape
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
REJECTED_REASONS_FILE = "rejected_reasons.json"

FT_COMMUNES = {
    "Paris": "75056",
    "Marseille": "13055",
    "Aix-en-Provence": "13001",
    "Toulon": "83137",
    "Nice": "06088",
}

PROFILE = """
Arnaud est ingénieur ECAM LaSalle (génie mécanique/thermodynamique) + MSc ESSEC Strategy.
Il a 3 ans de conseil en transformation (Bartle) et 3 ans de conseil stratégie climat chez ekodev
(bilans carbone GHG Protocol, stratégies bas-carbone, formateur ADEME ACT Pas à Pas).
Il cherche un poste de consultant climat senior, chargé de mission climat, ou expert politiques
publiques climatiques à Marseille, en PACA ou full télétravail (ou Paris).
Il veut travailler dans un cabinet conseil renommé, une agence publique (ADEME, Région, Métropole),
ou une ONG/think tank influent. Il ne veut PAS de postes terrain, techniciens, nucléaire,
achats, RH, finance, stages ou alternances.
"""


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = unescape(value)
    return " ".join(value.split())


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


def matches_location(value):
    text = (value or "").lower()
    allowed_terms = [
        "paris", "marseille", "aix", "aix-en-provence", "toulon", "nice",
        "remote", "télétravail", "teletravail", "france", "paca", "provence",
    ]
    return any(term in text for term in allowed_terms)


def get_ft_token():
    client_id = os.environ.get("FT_CLIENT_ID", "")
    client_secret = os.environ.get("FT_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("  FT: identifiants absents")
        return ""
    try:
        r = requests.post(
            "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "api_offresdemploiv2 o2dsoffre",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("access_token", "")
    except Exception as e:
        print(f"  EXCEPTION token FT: {e}")
        return ""


def search_adzuna(keyword, location):
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        print("  Adzuna: identifiants absents")
        return []
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
        r.raise_for_status()
        data = r.json()
        if "exception" in data:
            print(f"  ERREUR Adzuna: {data['exception']}")
            return []
        results = data.get("results", [])
        print(f"  Adzuna '{keyword}' / '{location}' → {len(results)} brutes")
        jobs = []
        for job in results:
            title = job.get("title", "N/A")
            description = job.get("description", "")
            if any(excl in f"{title} {description}".lower() for excl in exclusions):
                print(f"  Exclu Adzuna: {title}")
                continue
            jobs.append({
                "id": str(job.get("id", "")),
                "title": title,
                "company": job.get("company", {}).get("display_name", "N/A"),
                "location": job.get("location", {}).get("display_name", location),
                "url": job.get("redirect_url", ""),
                "description": description[:150] + "..." if description else "",
                "source": "Adzuna",
            })
        return jobs
    except Exception as e:
        print(f"  EXCEPTION Adzuna: {e}")
        return []


def search_france_travail(keyword, location):
    exclusions = get_exclusions()
    try:
        token = get_ft_token()
        if not token:
            return []
        commune = FT_COMMUNES.get(location, "")
        url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
        params = {
            "motsCles": keyword,
            "commune": commune,
            "distance": 30,
            "typeContrat": "CDI,CDD",
            "range": "0-9",
        }
        r = requests.get(url, params=params, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("resultats", [])
        print(f"  FT '{keyword}' / '{location}' → {len(results)} brutes")
        jobs = []
        for job in results:
            title = job.get("intitule", "N/A")
            description = job.get("description", "")
            if any(excl in f"{title} {description}".lower() for excl in exclusions):
                print(f"  Exclu FT: {title}")
                continue
            jobs.append({
                "id": job.get("id", ""),
                "title": title,
                "company": job.get("entreprise", {}).get("nom", "N/A"),
                "location": job.get("lieuTravail", {}).get("libelle", location),
                "url": job.get("origineOffre", {}).get("urlOrigine",
                    f"https://www.francetravail.fr/offres/recherche/detail/{job.get('id', '')}"),
                "description": description[:150] + "..." if description else "",
                "source": "France Travail",
            })
        return jobs
    except Exception as e:
        print(f"  EXCEPTION FT: {e}")
        return []


def search_hellowork(keyword, location):
    exclusions = get_exclusions()
    try:
        from bs4 import BeautifulSoup
        url = (
            f"https://www.hellowork.com/fr-fr/emploi/recherche.html"
            f"?k={requests.utils.quote(keyword)}"
            f"&l={requests.utils.quote(location)}"
            f"&c=CDI"
        )
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "fr-FR",
        }, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.find_all(["article", "li"], attrs={"data-id": True})
        if not cards:
            cards = soup.find_all("div", class_=lambda c: c and "job" in str(c).lower())
        print(f"  Hellowork '{keyword}' / '{location}' → {len(cards)} cartes")
        jobs = []
        for card in cards[:10]:
            title_el = card.find(["h2", "h3", "a"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if any(excl in title.lower() for excl in exclusions):
                continue
            link_el = card.find("a", href=True)
            href = link_el["href"] if link_el else ""
            full_url = href if href.startswith("http") else "https://www.hellowork.com" + href
            company_el = card.find(["span", "p"], class_=lambda c: c and any(
                w in str(c).lower() for w in ["company", "entreprise"]))
            jobs.append({
                "id": full_url,
                "title": title,
                "company": company_el.get_text(strip=True) if company_el else "N/A",
                "location": location,
                "url": full_url,
                "description": "",
                "source": "Hellowork",
            })
        print(f"  Hellowork '{keyword}' / '{location}' → {len(jobs)} après filtre")
        return jobs
    except Exception as e:
        print(f"  EXCEPTION Hellowork: {e}")
        return []


def search_jooble(keyword, location):
    api_key = os.environ.get("JOOBLE_API_KEY", "")
    if not api_key:
        print("  Jooble: clé API absente")
        return []
    exclusions = get_exclusions()
    try:
        url = f"https://jooble.org/api/{api_key}"
        payload = {"keywords": keyword, "location": location, "page": 1}
        r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("jobs", [])
        print(f"  Jooble '{keyword}' / '{location}' → {len(results)} brutes")
        jobs = []
        for job in results[:10]:
            title = clean_text(job.get("title", "N/A"))
            description = clean_text(job.get("snippet", ""))
            if any(excl in f"{title} {description}".lower() for excl in exclusions):
                print(f"  Exclu Jooble: {title}")
                continue
            jobs.append({
                "id": str(job.get("id") or job.get("link", "")),
                "title": title,
                "company": clean_text(job.get("company", "N/A")),
                "location": clean_text(job.get("location", location)),
                "url": job.get("link", ""),
                "description": description[:150] + "..." if len(description) > 150 else description,
                "source": "Jooble",
            })
        print(f"  Jooble '{keyword}' / '{location}' → {len(jobs)} après filtre")
        return jobs
    except Exception as e:
        print(f"  EXCEPTION Jooble: {e}")
        return []


def search_greenjob(keyword):
    exclusions = get_exclusions()
    try:
        from bs4 import BeautifulSoup
        url = f"https://www.greenjob.fr/offres-emploi/?s={requests.utils.quote(keyword)}"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "fr-FR",
        }, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.find_all(["article", "div"], class_=lambda c: c and any(
            w in str(c).lower() for w in ["job", "offre", "annonce"]))
        print(f"  Greenjob.fr '{keyword}' → {len(cards)} cartes")
        jobs = []
        for card in cards[:10]:
            title_el = card.find(["h2", "h3", "h4"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            if any(excl in title.lower() for excl in exclusions):
                continue
            link_el = card.find("a", href=True)
            href = link_el["href"] if link_el else ""
            full_url = href if href.startswith("http") else "https://www.greenjob.fr" + href
            location_el = card.find(["span", "p", "div"], class_=lambda c: c and any(
                w in str(c).lower() for w in ["loc", "lieu", "ville", "city"]))
            location = location_el.get_text(strip=True) if location_el else "France"
            if not matches_location(location):
                continue
            jobs.append({
                "id": full_url,
                "title": title,
                "company": "N/A",
                "location": location,
                "url": full_url,
                "description": "",
                "source": "Greenjob.fr",
            })
        print(f"  Greenjob.fr '{keyword}' → {len(jobs)} après filtre")
        return jobs
    except Exception as e:
        print(f"  EXCEPTION Greenjob.fr: {e}")
        return []


def filter_jobs_with_ai(jobs):
    mistral_key = os.environ.get("MISTRAL_API_KEY", "")
    if not mistral_key:
        print("  Mistral: clé API absente, pas de filtrage IA")
        return jobs
    if not jobs:
        return jobs

    rejected_reasons = load_json(REJECTED_REASONS_FILE, [])
    reasons_text = "\n".join([
        f"- \"{r['title']}\" chez {r['company']} → Raison : {r['reason']}"
        for r in rejected_reasons[-30:]
    ]) if rejected_reasons else "Aucun rejet enregistré."

    jobs_text = "\n".join([
        f"{i}. {job['title']} | {job['company']} | {job['location']}"
        for i, job in enumerate(jobs)
    ])

    prompt = f"""Tu es un assistant de recherche d'emploi. Voici le profil du candidat :
{PROFILE}

Offres récemment rejetées et raisons :
{reasons_text}

Offres du jour :
{jobs_text}

Réponds UNIQUEMENT avec un JSON (sans texte avant ou après, sans backticks) :
[{{"index": 0, "keep": true, "reason": "correspond au profil"}}, ...]

Garde si : conseil climat, politiques publiques, RSE stratégique, chargé de mission climat.
Rejette si : ressemble aux offres rejetées, terrain, technique, RH, finance, nucléaire, achats."""

    try:
        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {mistral_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "mistral-small-latest",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=30,
        )
        text = r.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        decisions = json.loads(text)

        kept = []
        for decision in decisions:
            idx = decision.get("index")
            if decision.get("keep") and idx is not None and idx < len(jobs):
                kept.append(jobs[idx])
            elif idx is not None and idx < len(jobs):
                print(f"  IA exclu: {jobs[idx]['title']} → {decision.get('reason', '')}")

        print(f"  Mistral: {len(kept)}/{len(jobs)} offres conservées")
        return kept
    except Exception as e:
        print(f"  EXCEPTION Mistral: {e}")
        return jobs


def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = (job["title"].lower().strip(), job["company"].lower().strip())
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
    source_colors = {
        "Adzuna": "#4a90a4",
        "France Travail": "#003189",
        "Greenjob.fr": "#2d8a4e",
        "Hellowork": "#d95f02",
        "Jooble": "#b56900",
    }
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
        source = job.get("source", "")
        sc = source_colors.get(source, "#888")
        badge_new = '<span style="font-size:11px;color:#fff;background:#e05c2a;padding:1px 8px;border-radius:10px;margin-left:8px">NOUVEAU</span>' if is_new else '<span style="font-size:11px;color:#888;background:#f0f0f0;padding:1px 8px;border-radius:10px;margin-left:8px">Déjà vu</span>'
        badge_source = f'<span style="font-size:11px;color:#fff;background:{sc};padding:1px 8px;border-radius:10px;margin-left:6px">{source}</span>'
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

# Sauvegarde le token dans today_jobs.json pour la page feedback
    meta = {"gh_pat": os.environ.get("GH_PAT", "")}
    save_json("meta.json", meta)
    
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
            all_jobs += search_france_travail(keyword, location)
            all_jobs += search_hellowork(keyword, location)
            all_jobs += search_jooble(keyword, location)
        all_jobs += search_greenjob(keyword)

    print(f"\n{len(all_jobs)} offres brutes avant filtrage IA")
    all_jobs = filter_jobs_with_ai(all_jobs)

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

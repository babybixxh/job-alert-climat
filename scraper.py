import hashlib
import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape
from urllib.parse import quote, urljoin

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

KEYWORDS = [
    "consultant climat",
    "consultant carbone",
    "consultant senior climat",
    "manager climat",
    "bilan carbone",
    "strategie climat",
    "stratégie climat",
    "transition écologique",
    "transition ecologique",
    "chargé mission climat",
    "chargé de mission climat",
    "décarbonation",
    "decarbonation",
    "RSE climat",
    "ESG climat",
    "politiques climatiques",
    "carbon accounting",
    "carbon strategy",
    "climate strategy",
    "sustainability consultant",
]

LOCATIONS = [
    "Marseille",
    "Aix-en-Provence",
    "Toulon",
    "Nice",
    "Paris",
    "France",
    "Remote",
]

ALLOWED_LOCATION_TERMS = [
    "paris",
    "marseille",
    "aix",
    "aix-en-provence",
    "toulon",
    "nice",
    "remote",
    "télétravail",
    "teletravail",
    "hybride",
    "france",
    "paca",
    "provence",
    "bouches-du-rhône",
    "bouches du rhone",
    "var",
    "alpes-maritimes",
]

EXCLUSIONS = [
    # contrats / séniorité
    "stage",
    "stagiaire",
    "alternance",
    "alternant",
    "alternan",
    "apprentissage",
    "apprenti",
    "intern",
    "internship",
    "junior",
    "graduate program",
    "en alternance",
    "en stage",
    "contrat pro",
    "contrat d'apprentissage",
    "bac+2",
    "bac+3",
    "débutant accepté",
    "debutant accepte",

    # terrain / technique / exploitation
    "travaux",
    "moe",
    "chantier",
    "réhabilitation",
    "rehabilitation",
    "urbaniste",
    "collecte",
    "nettoiement",
    "assainissement",
    "exploitation eau",
    "inspection itv",
    "ouvrages d'art",
    "frigoriste",
    "polissage",
    "électromécanicien",
    "electromecanicien",
    "electro mec",
    "technicien",
    "technicienne",
    "technician",
    "opérateur",
    "opératrice",
    "operateur",
    "operatrice",
    "agent de",
    "conducteur d'engins",
    "chauffeur",
    "responsable bureau d'études",
    "responsable bureau d'etudes",
    "préparateur coordinateur",
    "preparateur coordinateur",
    "chef de projet urbanisme",
    "ingénieur travaux",
    "ingenieur travaux",
    "ingénieur calcul",
    "ingenieur calcul",
    "ingénieur hydraulique",
    "ingenieur hydraulique",
    "ingénieur mécanique",
    "ingenieur mecanique",
    "projeteur",
    "paysagiste",
    "paysager",
    "espaces verts",

    # fonctions hors cible
    "acheteur",
    "achats",
    "procurement",
    "comptabilité",
    "comptable",
    "amoa finance",
    "finance manager",
    "avant-vente",
    "presales",
    "pré-sales",
    "recrutement",
    "chargé de recrutement",
    "charge de recrutement",
    "ressources humaines",
    "human resources",
    "talent acquisition",
    "sdr",
    "bdr",
    "business developer",
    "business development representative",
    "account executive",
    "sales development",
    "revenue operations",
    "growth manager",
    "brand manager",
    "content manager",
    "social media",
    "legal internship",

    # secteurs / sujets hors cible
    "nucléaire",
    "nucleaire",
    "nucl",
    "hydraulique moe",
    "calcul mécanique",
    "calcul mecanique",
    "aéronautique",
    "aeronautique",
    "aeronautics",
    "vessel",
    "optique",
    "regulatory affairs",
    "r&d procédés",
    "r&d procedes",
    "régisseur",
    "regisseur",
    "régisseuse",
    "regisseuse",
    "vidéo",
    "video",
    "delivery manager",
]

POSITIVE_TERMS = [
    "climat",
    "climate",
    "carbone",
    "carbon",
    "bilan carbone",
    "ghg",
    "décarbonation",
    "decarbonation",
    "decarbonization",
    "transition écologique",
    "transition ecologique",
    "rse",
    "esg",
    "csrd",
    "durabilité",
    "durabilite",
    "sustainability",
    "bas-carbone",
    "low carbon",
    "scope 1",
    "scope 2",
    "scope 3",
    "strategie climat",
    "stratégie climat",
    "politique climatique",
    "adaptation",
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
Arnaud est ingénieur ECAM LaSalle en génie mécanique et thermodynamique, avec un MSc ESSEC Strategy.
Il a 3 ans de conseil en transformation chez Bartle et 3 ans de conseil stratégie climat chez ekodev.
Compétences : bilans carbone, GHG Protocol, stratégies bas-carbone, trajectoires de décarbonation,
formation ADEME ACT Pas à Pas, conseil climat, politiques publiques climatiques et RSE stratégique.
Il cherche un poste de consultant climat senior, manager climat, chargé de mission climat, expert carbone,
ou expert politiques publiques climatiques à Marseille, en PACA, en full remote, ou à Paris si le poste est très pertinent.
Il veut éviter les postes terrain, techniciens, nucléaire, achats, RH, finance, stages et alternances.
"""

COMPANY_SOURCES = [
    {
        "name": "Carbone 4",
        "url": "https://www.carbone4.com/jobs",
        "location": "Paris / France",
    },
    {
        "name": "Utopies",
        "url": "https://utopies.com/recrutement/",
        "location": "Paris / France",
    },
    {
        "name": "BL évolution",
        "url": "https://www.bl-evolution.com/contact/nous-rejoindre/",
        "location": "France",
    },
    {
        "name": "I Care by BearingPoint",
        "url": "https://www.i-care-consult.com/fr/recrutement/",
        "location": "Paris / Lyon / France",
    },
    {
        "name": "Colombus Consulting",
        "url": "https://career.colombus-consulting.com/en/",
        "location": "Paris",
    },
    {
        "name": "Ministère de la Transition écologique",
        "url": "https://recrutement.ecologie.gouv.fr/offres-demploi",
        "location": "France",
    },
    {
        "name": "Tennaxia",
        "url": "https://jobs.tennaxia.com/jobs",
        "location": "France",
    },
    {
        "name": "Sami",
        "url": "https://www.welcometothejungle.com/fr/companies/sami/jobs",
        "location": "Paris / remote possible",
    },
    {
        "name": "Greenly",
        "url": "https://careers.greenly.earth/jobs",
        "location": "Paris / remote possible",
    },
    {
        "name": "Traace",
        "url": "https://jobs.stationf.co/companies/traace",
        "location": "Paris / remote possible",
    },
    {
        "name": "Sweep",
        "url": "https://sweep.teamtailor.com/jobs",
        "location": "France / remote possible",
    },
    {
        "name": "Aktio",
        "url": "https://www.welcometothejungle.com/fr/companies/aktio/jobs",
        "location": "Paris / remote possible",
    },
    {
        "name": "Carbon Cutter",
        "url": "https://www.welcometothejungle.com/fr/companies/carbon-cutter/jobs",
        "location": "Paris / remote possible",
    },
    {
        "name": "Jobs that make sense",
        "url": "https://jobs.makesense.org/fr/jobs?search=climat",
        "location": "France / remote possible",
    },
    {
        "name": "Emploi Environnement",
        "url": "https://www.emploi-environnement.com/",
        "location": "France",
    },
]

SOURCE_COLORS = {
    "Adzuna": "#4a90a4",
    "France Travail": "#003189",
    "Jooble": "#b56900",
    "ADEME": "#c04a00",
    "Carbone 4": "#222222",
    "Utopies": "#2d6a4f",
    "BL évolution": "#2d6a4f",
    "I Care by BearingPoint": "#2d6a4f",
    "Colombus Consulting": "#2d6a4f",
    "Ministère de la Transition écologique": "#003189",
    "Tennaxia": "#4a90a4",
    "Sami": "#2d8a4e",
    "Greenly": "#2d8a4e",
    "Traace": "#2d8a4e",
    "Sweep": "#2d8a4e",
    "Aktio": "#2d8a4e",
    "Carbon Cutter": "#2d8a4e",
    "Jobs that make sense": "#2d8a4e",
    "Emploi Environnement": "#2d8a4e",
}

EXCLUDED_LOG = []


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = unescape(value)
    return " ".join(value.split())


def normalize_text(value):
    return clean_text(value).lower()


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  Impossible de lire {path}: {e}")
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def stable_id(*parts):
    text = "|".join(clean_text(str(part)).lower() for part in parts if part is not None)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def get_exclusions():
    rejected = load_json(REJECTED_FILE, [])
    if not isinstance(rejected, list):
        rejected = []
    return list(dict.fromkeys(EXCLUSIONS + rejected))


def log_excluded(title, company, location, source, reason):
    EXCLUDED_LOG.append({
        "title": clean_text(title) or "N/A",
        "company": clean_text(company) or "N/A",
        "location": clean_text(location) or "",
        "source": clean_text(source) or "N/A",
        "reason": clean_text(reason) or "Non précisé",
    })


def text_has_exclusion(text):
    lowered = normalize_text(text)
    return any(excl.lower() in lowered for excl in get_exclusions())


def text_has_positive_signal(text):
    lowered = normalize_text(text)
    return any(term.lower() in lowered for term in POSITIVE_TERMS)


def matches_location(value):
    text = normalize_text(value)
    if not text:
        return True
    return any(term in text for term in ALLOWED_LOCATION_TERMS)


def build_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }


def safe_get(url, timeout=15, verify=True):
    try:
        return requests.get(url, headers=build_headers(), timeout=timeout, verify=verify)
    except requests.exceptions.SSLError:
        print(f"  SSL: retry sans vérification certificat pour {url}")
        return requests.get(url, headers=build_headers(), timeout=timeout, verify=False)


# -----------------------------------------------------------------------------
# API sources
# -----------------------------------------------------------------------------

def get_ft_token():
    client_id = os.environ.get("FT_CLIENT_ID", "")
    client_secret = os.environ.get("FT_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("  France Travail: identifiants absents")
        return ""

    try:
        r = requests.post(
            "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": f"api_offresdemploiv2 o2dsoffre application_{client_id}",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )

        if r.status_code != 200:
            print(f"  France Travail token erreur {r.status_code}: {r.text[:300]}")
            return ""

        return r.json().get("access_token", "")

    except Exception as e:
        print(f"  EXCEPTION token France Travail: {e}")
        return ""


def search_adzuna(keyword, location):
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")

    if not app_id or not app_key:
        print("  Adzuna: identifiants absents")
        return []

    url = (
        "https://api.adzuna.com/v1/api/jobs/fr/search/1"
        f"?app_id={app_id}&app_key={app_key}"
        "&results_per_page=10"
        f"&what={quote(keyword)}"
        f"&where={quote(location)}"
        "&max_days_old=14"
        "&content-type=application/json"
    )

    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        if "exception" in data:
            print(f"  ERREUR Adzuna: {data['exception']}")
            return []

        results = data.get("results", [])
        print(f"  Adzuna '{keyword}' / '{location}' -> {len(results)} brutes")

        jobs = []
        for job in results:
            title = clean_text(job.get("title", "N/A"))
            company = clean_text(job.get("company", {}).get("display_name", "N/A"))
            job_location = clean_text(job.get("location", {}).get("display_name", location))
            description = clean_text(job.get("description", ""))
            combined = f"{title} {company} {job_location} {description}"

            if text_has_exclusion(combined):
                log_excluded(title, company, job_location, "Adzuna", "mot-clé exclu")
                continue

            jobs.append({
                "id": str(job.get("id") or stable_id(title, company, job_location)),
                "title": title,
                "company": company,
                "location": job_location,
                "url": job.get("redirect_url", ""),
                "description": description[:180] + "..." if len(description) > 180 else description,
                "source": "Adzuna",
            })

        return jobs

    except Exception as e:
        print(f"  EXCEPTION Adzuna: {e}")
        return []


def search_france_travail(keyword, location):
    token = get_ft_token()
    if not token:
        return []

    commune = FT_COMMUNES.get(location, "")
    params = {
        "motsCles": keyword,
        "distance": 30,
        "typeContrat": "CDI,CDD",
        "range": "0-9",
    }

    if commune:
        params["commune"] = commune

    try:
        r = requests.get(
            "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("resultats", [])

        print(f"  France Travail '{keyword}' / '{location}' -> {len(results)} brutes")

        jobs = []
        for job in results:
            title = clean_text(job.get("intitule", "N/A"))
            company = clean_text(job.get("entreprise", {}).get("nom", "N/A"))
            job_location = clean_text(job.get("lieuTravail", {}).get("libelle", location))
            description = clean_text(job.get("description", ""))
            combined = f"{title} {company} {job_location} {description}"

            if text_has_exclusion(combined):
                log_excluded(title, company, job_location, "France Travail", "mot-clé exclu")
                continue

            jobs.append({
                "id": str(job.get("id") or stable_id(title, company, job_location)),
                "title": title,
                "company": company,
                "location": job_location,
                "url": job.get("origineOffre", {}).get(
                    "urlOrigine",
                    f"https://www.francetravail.fr/offres/recherche/detail/{job.get('id', '')}",
                ),
                "description": description[:180] + "..." if len(description) > 180 else description,
                "source": "France Travail",
            })

        return jobs

    except Exception as e:
        print(f"  EXCEPTION France Travail: {e}")
        return []


def search_jooble(keyword, location):
    api_key = os.environ.get("JOOBLE_API_KEY", "")

    if not api_key:
        print("  Jooble: clé API absente")
        return []

    try:
        url = f"https://jooble.org/api/{api_key}"
        payload = {
            "keywords": keyword,
            "location": location,
            "page": 1,
            "country": "fr",
        }

        r = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("jobs", [])

        print(f"  Jooble '{keyword}' / '{location}' -> {len(results)} brutes")

        jobs = []
        for job in results[:10]:
            title = clean_text(job.get("title", "N/A"))
            company = clean_text(job.get("company", "N/A"))
            job_location = clean_text(job.get("location", location))
            description = clean_text(job.get("snippet", ""))
            job_url = job.get("link", "")
            combined = f"{title} {company} {job_location} {description}"

            foreign_markers = [
                ", tx", ", ny", ", ca", ", fl", ", oh",
                " usa", "united states", "united kingdom", " uk,",
            ]
            if any(marker in job_location.lower() for marker in foreign_markers):
                log_excluded(title, company, job_location, "Jooble", "localisation hors cible")
                continue

            if text_has_exclusion(combined):
                log_excluded(title, company, job_location, "Jooble", "mot-clé exclu")
                continue

            jobs.append({
                "id": str(job.get("id") or stable_id(title, company, job_url)),
                "title": title,
                "company": company,
                "location": job_location,
                "url": job_url,
                "description": description[:180] + "..." if len(description) > 180 else description,
                "source": "Jooble",
            })

        print(f"  Jooble '{keyword}' / '{location}' -> {len(jobs)} après filtre")
        return jobs

    except Exception as e:
        print(f"  EXCEPTION Jooble: {e}")
        return []


# -----------------------------------------------------------------------------
# HTML sources
# -----------------------------------------------------------------------------

def search_ademe():
    return search_company_page({
        "name": "ADEME",
        "url": "https://recrutement.ademe.fr/offre-de-emploi/liste-offres.aspx",
        "location": "France",
    }, require_positive_signal=False)


def search_company_page(source, require_positive_signal=True):
    if BeautifulSoup is None:
        print(f"  {source.get('name', 'Source')}: BeautifulSoup absent, source ignorée")
        return []

    name = source.get("name", "Source")
    url = source.get("url", "")
    default_location = source.get("location", "France")

    if not url:
        return []

    try:
        r = safe_get(url, timeout=20)
        if r.status_code >= 400:
            print(f"  {name}: HTTP {r.status_code}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []

        # First pass: structured blocks that often represent job cards.
        card_selectors = [
            "article",
            "li",
            "div[class*='job']",
            "div[class*='offer']",
            "div[class*='offre']",
            "a[href*='jobs']",
            "a[href*='job']",
            "a[href*='offre']",
            "a[href*='career']",
            "a[href*='recrut']",
        ]

        for selector in card_selectors:
            for element in soup.select(selector):
                text = clean_text(element.get_text(" ", strip=True))
                link = element if element.name == "a" else element.find("a", href=True)
                href = link.get("href", "") if link else ""
                if not href:
                    continue
                full_url = urljoin(url, href)
                candidates.append((text, full_url))

        # Second pass: all links, useful for WTTJ, Teamtailor, Station F, etc.
        for link in soup.find_all("a", href=True):
            text = clean_text(link.get_text(" ", strip=True))
            href = link.get("href", "")
            full_url = urljoin(url, href)
            candidates.append((text, full_url))

        jobs = []
        seen_urls = set()

        for text, full_url in candidates:
            if full_url in seen_urls:
                continue

            title = clean_text(text)
            if not title or len(title) < 4:
                continue

            combined = f"{title} {full_url} {name} {default_location}"
            lowered_url = full_url.lower()

            job_like_url = any(token in lowered_url for token in [
                "job",
                "jobs",
                "offre",
                "offres",
                "career",
                "careers",
                "recrut",
                "poste",
                "positions",
            ])

            if not job_like_url and not text_has_positive_signal(combined):
                continue

            if require_positive_signal and not text_has_positive_signal(combined):
                continue

            if text_has_exclusion(combined):
                log_excluded(title, name, default_location, name, "mot-clé exclu")
                continue

            if not matches_location(default_location):
                continue

            seen_urls.add(full_url)
            jobs.append({
                "id": stable_id(title, name, full_url),
                "title": title[:160],
                "company": name,
                "location": default_location,
                "url": full_url,
                "description": "Offre détectée depuis la page carrière de l'entreprise.",
                "source": name,
            })

        print(f"  {name} -> {len(jobs)} offre(s) détectée(s)")
        return jobs[:20]

    except Exception as e:
        print(f"  EXCEPTION {name}: {e}")
        return []


# -----------------------------------------------------------------------------
# AI filtering
# -----------------------------------------------------------------------------

def filter_jobs_with_ai(jobs):
    mistral_key = os.environ.get("MISTRAL_API_KEY", "")

    if not mistral_key:
        print("  Mistral: clé API absente, pas de filtrage IA")
        return jobs

    if not jobs:
        return jobs

    rejected_reasons = load_json(REJECTED_REASONS_FILE, [])
    if not isinstance(rejected_reasons, list):
        rejected_reasons = []

    reasons_text = "\n".join([
        f"- \"{r.get('title', '')}\" chez {r.get('company', '')} -> Raison : {r.get('reason', '')}"
        for r in rejected_reasons[-30:]
    ]) or "Aucun rejet enregistré."

    jobs_text = "\n".join([
        (
            f"{i}. {job.get('title', '')} | {job.get('company', '')} | "
            f"{job.get('location', '')} | {job.get('source', '')} | "
            f"{job.get('description', '')[:240]}"
        )
        for i, job in enumerate(jobs)
    ])

    prompt = f"""Tu es un assistant de recherche d'emploi très sélectif.

Profil du candidat :
{PROFILE}

Offres récemment rejetées et raisons :
{reasons_text}

Offres du jour :
{jobs_text}

Réponds uniquement avec un JSON valide, sans texte avant ou après, sans markdown, au format :
[
  {{"index": 0, "keep": true, "reason": "raison courte"}}
]

Garde seulement les offres cohérentes avec : conseil climat, stratégie climat, bilan carbone, GHG Protocol,
décarbonation, CSRD/ESG stratégique, politiques publiques climatiques, chargé de mission climat senior,
manager climat, expert carbone, climate strategy, carbon accounting.

Rejette les offres qui semblent être : stage, alternance, junior, terrain, technicien, exploitation,
nucléaire, achats, RH, finance, commercial pur, SDR, BDR, account executive, marketing pur, IT pur.
"""

    try:
        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {mistral_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "mistral-small-latest",
                "max_tokens": 2500,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=40,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        decisions = json.loads(text)

        kept = []
        for decision in decisions:
            idx = decision.get("index")
            if idx is None or not isinstance(idx, int) or idx >= len(jobs):
                continue

            job = jobs[idx]
            reason = decision.get("reason", "")

            if decision.get("keep"):
                job["ai_reason"] = reason
                kept.append(job)
            else:
                print(f"  IA exclu: {job.get('title', '')} -> {reason}")
                log_excluded(
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("source", ""),
                    f"IA: {reason}",
                )

        print(f"  Mistral: {len(kept)}/{len(jobs)} offres conservées")
        return kept

    except Exception as e:
        print(f"  EXCEPTION Mistral: {e}")
        return jobs


# -----------------------------------------------------------------------------
# Post-processing
# -----------------------------------------------------------------------------

def deduplicate(jobs):
    seen = set()
    unique = []

    for job in jobs:
        title = normalize_text(job.get("title", ""))
        company = normalize_text(job.get("company", ""))
        url = normalize_text(job.get("url", ""))

        key = (title, company)
        if not title:
            continue

        # If title/company are weak, use URL as fallback.
        if company in ["", "n/a", "non précisé", "non precise"]:
            key = (title, url)

        if key in seen:
            continue

        seen.add(key)
        unique.append(job)

    return unique


def mark_seen(jobs, seen_ids):
    for job in jobs:
        key = f"{normalize_text(job.get('title', ''))}|{normalize_text(job.get('company', ''))}"
        job["is_new"] = key not in seen_ids
    return jobs


def categorize(jobs):
    marseille = []
    paca = []
    paris_remote = []

    for job in jobs:
        loc = normalize_text(job.get("location", ""))
        text = normalize_text(f"{job.get('title', '')} {job.get('description', '')} {loc}")

        if "marseille" in text:
            marseille.append(job)
        elif any(city in text for city in [
            "aix",
            "toulon",
            "nice",
            "provence",
            "paca",
            "var",
            "alpes-maritimes",
            "bouches-du-rhône",
            "bouches du rhone",
        ]):
            paca.append(job)
        else:
            paris_remote.append(job)

    return marseille, paca, paris_remote


# -----------------------------------------------------------------------------
# Email rendering
# -----------------------------------------------------------------------------

def html_escape(value):
    return (
        clean_text(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def section_html(title, jobs, color):
    if not jobs:
        return ""

    new_count = sum(1 for job in jobs if job.get("is_new"))

    html = f"""
    <div style="margin:2rem 0 1rem">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:1rem">
            <h2 style="margin:0;font-size:17px;font-weight:600;color:{color}">{html_escape(title)}</h2>
            <span style="font-size:13px;color:#555;background:#f0f0f0;padding:2px 10px;border-radius:20px">{len(jobs)} offre(s)</span>
            {f'<span style="font-size:13px;color:#fff;background:#e05c2a;padding:2px 10px;border-radius:20px">{new_count} nouvelle(s)</span>' if new_count else ''}
        </div>
    """

    for job in jobs:
        is_new = job.get("is_new", True)
        source = job.get("source", "")
        source_color = SOURCE_COLORS.get(source, "#777")
        badge_new = (
            '<span style="font-size:11px;color:#fff;background:#e05c2a;padding:1px 8px;border-radius:10px;margin-left:8px">NOUVEAU</span>'
            if is_new
            else '<span style="font-size:11px;color:#777;background:#f0f0f0;padding:1px 8px;border-radius:10px;margin-left:8px">Déjà vu</span>'
        )
        badge_source = (
            f'<span style="font-size:11px;color:#fff;background:{source_color};padding:1px 8px;border-radius:10px;margin-left:6px">{html_escape(source)}</span>'
        )
        ai_reason = job.get("ai_reason", "")
        ai_html = f'<p style="margin:6px 0 0;font-size:12px;color:#666"><strong>Pourquoi c’est pertinent :</strong> {html_escape(ai_reason)}</p>' if ai_reason else ""

        html += f"""
        <div style="margin-bottom:16px;padding:14px;border-left:4px solid {color};background:{'#fff8f5' if is_new else '#f9f9f9'};border-radius:4px">
            <h3 style="margin:0 0 6px 0;font-size:16px">
                <a href="{html_escape(job.get('url', ''))}" style="color:{color};text-decoration:none">{html_escape(job.get('title', 'N/A'))}</a>
                {badge_new}{badge_source}
            </h3>
            <p style="margin:0 0 5px 0;color:#555;font-size:14px">
                <strong>{html_escape(job.get('company', 'N/A'))}</strong> | {html_escape(job.get('location', ''))}
            </p>
            <p style="margin:0;font-size:13px;color:#777">{html_escape(job.get('description', ''))}</p>
            {ai_html}
        </div>
        """

    html += "</div>"
    return html


def excluded_section_html(excluded_log):
    if not excluded_log:
        return ""

    rows = ""
    for item in excluded_log[:100]:
        rows += f"""
        <tr>
            <td style="padding:6px 10px;font-size:12px;color:#555;border-bottom:1px solid #eee">{html_escape(item.get('title', ''))}</td>
            <td style="padding:6px 10px;font-size:12px;color:#777;border-bottom:1px solid #eee">{html_escape(item.get('company', ''))}</td>
            <td style="padding:6px 10px;font-size:12px;color:#777;border-bottom:1px solid #eee">{html_escape(item.get('location', ''))}</td>
            <td style="padding:6px 10px;font-size:12px;color:#777;border-bottom:1px solid #eee">{html_escape(item.get('source', ''))}</td>
            <td style="padding:6px 10px;font-size:12px;color:#9a5b00;border-bottom:1px solid #eee">{html_escape(item.get('reason', ''))}</td>
        </tr>
        """

    return f"""
    <details style="margin-top:2.5rem;padding:14px;background:#fafafa;border-radius:8px;border:0.5px solid #e0e0e0">
        <summary style="cursor:pointer;font-size:14px;color:#555;font-weight:600">
            Voir les {len(excluded_log)} offre(s) écartée(s) aujourd'hui
        </summary>
        <table style="width:100%;border-collapse:collapse;margin-top:10px">
            <tr style="text-align:left">
                <th style="padding:6px 10px;font-size:11px;color:#999;text-transform:uppercase">Titre</th>
                <th style="padding:6px 10px;font-size:11px;color:#999;text-transform:uppercase">Entreprise</th>
                <th style="padding:6px 10px;font-size:11px;color:#999;text-transform:uppercase">Lieu</th>
                <th style="padding:6px 10px;font-size:11px;color:#999;text-transform:uppercase">Source</th>
                <th style="padding:6px 10px;font-size:11px;color:#999;text-transform:uppercase">Raison</th>
            </tr>
            {rows}
        </table>
    </details>
    """


def build_email(jobs, feedback_url, excluded_log=None):
    today = datetime.now().strftime("%d/%m/%Y")
    marseille, paca, paris_remote = categorize(jobs)
    total = len(jobs)
    new_total = sum(1 for job in jobs if job.get("is_new"))

    if not total:
        return f"""
        <html><body style="font-family:Arial,sans-serif;max-width:760px;margin:auto;padding:20px">
        <h2 style="color:#2d6a4f">Alerte emploi climat - {today}</h2>
        <p>Aucune nouvelle offre pertinente trouvée aujourd'hui.</p>
        {excluded_section_html(excluded_log or [])}
        </body></html>
        """

    body = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:760px;margin:auto;padding:20px">
    <h2 style="color:#2d6a4f">Alerte emploi climat - {today}</h2>
    <p style="color:#555">
        {total} offre(s), dont <strong style="color:#e05c2a">{new_total} nouvelle(s)</strong><br>
        Marseille : {len(marseille)} | PACA hors Marseille : {len(paca)} | Paris / remote : {len(paris_remote)}
    </p>
    <a href="{html_escape(feedback_url)}" style="display:inline-block;margin:8px 0 16px;padding:10px 20px;background:#2d6a4f;color:#fff;border-radius:6px;text-decoration:none;font-size:14px">
        Signaler des offres non pertinentes
    </a>
    <hr style="border:1px solid #e0e0e0">
    """

    body += section_html("Marseille", marseille, "#0f6e56")
    if marseille and paca:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    body += section_html("Région PACA hors Marseille", paca, "#3b6d11")
    if (marseille or paca) and paris_remote:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    body += section_html("Paris / full remote / France", paris_remote, "#993c1d")
    body += excluded_section_html(excluded_log or [])
    body += "</body></html>"

    return body


def send_email(html_body, job_count):
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_password = os.environ.get("GMAIL_PASSWORD", "")
    gmail_to = os.environ.get("GMAIL_TO", "")

    if not gmail_user or not gmail_password or not gmail_to:
        print("Email non envoyé: variables GMAIL_USER, GMAIL_PASSWORD ou GMAIL_TO absentes")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{job_count} offre(s) climat - {datetime.now().strftime('%d/%m/%Y')}"
    msg["From"] = gmail_user
    msg["To"] = gmail_to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, gmail_to, msg.as_string())

    print(f"Email envoyé avec {job_count} offres")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def collect_jobs():
    all_jobs = []

    for keyword in KEYWORDS:
        for location in LOCATIONS:
            all_jobs += search_adzuna(keyword, location)
            all_jobs += search_france_travail(keyword, location)
            all_jobs += search_jooble(keyword, location)

    all_jobs += search_ademe()

    for source in COMPANY_SOURCES:
        all_jobs += search_company_page(source)

    return all_jobs


def main():
    seen_ids = set(load_json(SEEN_FILE, []))
    print(f"{len(seen_ids)} offres déjà vues en mémoire")

    all_jobs = collect_jobs()
    print(f"\n{len(all_jobs)} offres brutes avant filtrage IA")

    all_jobs = deduplicate(all_jobs)
    print(f"{len(all_jobs)} offres après déduplication avant IA")

    all_jobs = filter_jobs_with_ai(all_jobs)

    jobs = deduplicate(all_jobs)
    jobs = mark_seen(jobs, seen_ids)

    new_seen = seen_ids | {
        f"{normalize_text(job.get('title', ''))}|{normalize_text(job.get('company', ''))}"
        for job in jobs
    }

    save_json(SEEN_FILE, sorted(new_seen))
    save_json(TODAY_FILE, jobs)

    repo = os.environ.get("GITHUB_REPOSITORY", "babybixxh/job-alert-climat")
    if "/" in repo:
        owner, repo_name = repo.split("/", 1)
        feedback_url = f"https://{owner}.github.io/{repo_name}/feedback.html"
    else:
        feedback_url = "#"

    print(f"\nTotal final : {len(jobs)} offres uniques")
    print(f"Total écarté : {len(EXCLUDED_LOG)} offres")

    sources_count = {}
    for job in jobs:
        source = job.get("source", "?")
        sources_count[source] = sources_count.get(source, 0) + 1

    print(f"Répartition par source : {sources_count}")

    html = build_email(jobs, feedback_url, EXCLUDED_LOG)
    send_email(html, len(jobs))


if __name__ == "__main__":
    main()

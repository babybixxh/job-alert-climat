import smtplib
import os
import json
import requests
import re
import urllib3
from html import unescape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# Communes trop excentrées à écarter même quand elles ressortent comme
# "Toulon"/"Nice" dans les résultats bruts (ex: La Garde est une commune
# distincte de l'agglomération toulonnaise, jugée hors zone par Arnaud).
LOCATION_EXCLUSIONS = ["la garde"]

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

# Entreprises suivies en direct via l'API publique Welcome to the Jungle.
# Clé = slug WTTJ (segment d'URL welcometothejungle.com/fr/companies/<slug>),
# valeur = libellé affiché. Les slugs sont des paris raisonnables : les logs CI
# (« WTTJ <label> → N brutes ») révèlent ceux à corriger si une entreprise renvoie 0.
WTTJ_COMPANIES = {
    # Conseil climat / RSE
    "carbone-4": "Carbone 4",
    "utopies": "Utopies",
    "bl-evolution": "BL évolution",
    "i-care": "I Care",
    "carbon-cutter": "Carbon Cutter",
    "adaptation-s": "adaptation/s",
    "cci-france": "CCI France",
    # Logiciels de comptabilité carbone
    "sami": "Sami",
    "greenly": "Greenly",
    "aktio": "Aktio",
    "tennaxia": "Tennaxia",
    "traace": "Traace",
    "sweep": "Sweep",
}

# Types de contrat WTTJ à écarter (on veut CDI/CDD, pas stage/alternance/VIE).
WTTJ_CONTRACT_EXCLUDE = ("intern", "apprentice", "apprentiss", "stage", "vie", "vix")

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


EXCLUDED_LOG = []


def log_excluded(title, company, location, source, reason):
    EXCLUDED_LOG.append({
        "title": title,
        "company": company,
        "location": location,
        "source": source,
        "reason": reason,
    })


def get_exclusions():
    base = list(EXCLUSIONS)
    rejected = load_json(REJECTED_FILE, [])
    return base + rejected


def matches_location(value):
    text = (value or "").lower()
    allowed_terms = ["paris", "marseille", "aix", "aix-en-provence", "toulon", "nice", "remote", "télétravail", "teletravail", "france", "paca", "provence"]
    return any(term in text for term in allowed_terms)


def is_location_excluded(value):
    text = (value or "").lower()
    return any(term in text for term in LOCATION_EXCLUSIONS)


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
                "scope": f"api_offresdemploiv2 o2dsoffre application_{client_id}",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"  FT token erreur {r.status_code}: {r.text[:300]}")
            return ""
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
            if any(excl in title.lower() for excl in exclusions):
                print(f"  Exclu Adzuna: {title}")
                log_excluded(title, job.get("company", {}).get("display_name", "N/A"),
                             job.get("location", {}).get("display_name", location),
                             "Adzuna", "mot-clé exclu")
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


def search_adzuna_companies():
    """Recherche par nom d'entreprise sur Adzuna pour les entreprises ciblées
    (WTTJ_COMPANIES) : le scraping WTTJ direct est peu fiable, donc on
    réutilise le seul canal d'API qui fonctionne de façon fiable. Ne
    remonte que les offres effectivement publiées sur Adzuna par ces
    entreprises (peut être vide pour les structures qui n'y publient pas)."""
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        return []
    exclusions = get_exclusions()
    jobs = []
    for slug, label in WTTJ_COMPANIES.items():
        url = (
            f"https://api.adzuna.com/v1/api/jobs/fr/search/1"
            f"?app_id={app_id}&app_key={app_key}"
            f"&results_per_page=10"
            f"&what={requests.utils.quote(label)}"
            f"&max_days_old=30"
            f"&content-type=application/json"
        )
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            if "exception" in data:
                print(f"  ERREUR Adzuna entreprise '{label}': {data['exception']}")
                continue
            results = data.get("results", [])
            print(f"  Adzuna entreprise '{label}' → {len(results)} brutes")
            # Match par mot entier sur le NOM D'ENTREPRISE uniquement (pas le
            # titre) : évite que « Sami » matche « Samir recherche nounou » ou
            # qu'un cabinet de recrutement citant l'entreprise passe.
            name_re = re.compile(r"\b" + re.escape(label.lower()) + r"\b")
            for job in results:
                title = job.get("title", "N/A")
                company = job.get("company", {}).get("display_name", "N/A")
                if not name_re.search(company.lower()):
                    continue
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, company, job.get("location", {}).get("display_name", ""),
                                 "Adzuna", "mot-clé exclu")
                    continue
                description = job.get("description", "")
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": title,
                    "company": company,
                    "location": job.get("location", {}).get("display_name", "France"),
                    "url": job.get("redirect_url", ""),
                    "description": description[:150] + "..." if description else "",
                    "source": "Adzuna",
                    "company_watch": True,
                })
        except Exception as e:
            print(f"  EXCEPTION Adzuna entreprise '{label}': {e}")
    print(f"  Adzuna entreprises ciblées → {len(jobs)} après filtre")
    return jobs


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
            if any(excl in title.lower() for excl in exclusions):
                print(f"  Exclu FT: {title}")
                log_excluded(title, job.get("entreprise", {}).get("nom", "N/A"),
                             job.get("lieuTravail", {}).get("libelle", location),
                             "France Travail", "mot-clé exclu")
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
        print(f"  Hellowork status={r.status_code} len={len(r.text)}")
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
            job_location = clean_text(job.get("location", location))

            # Filtre anti-offres US/internationales : Jooble ne propose pas de paramètre pays strict
            loc_lower = job_location.lower()
            us_markers = ["usa", "united states", ", tx", ", ny", ", ca", ", fl", ", oh", ", il", ", uk", "united kingdom"]
            if any(marker in loc_lower for marker in us_markers):
                continue
            if not matches_location(job_location):
                continue

            if any(excl in title.lower() for excl in exclusions):
                print(f"  Exclu Jooble: {title}")
                continue
            jobs.append({
                "id": str(job.get("id") or job.get("link", "")),
                "title": title,
                "company": clean_text(job.get("company", "N/A")),
                "location": job_location,
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
        print(f"  Greenjob.fr status={r.status_code} len={len(r.text)}")
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


def search_ademe():
    exclusions = get_exclusions()
    try:
        from bs4 import BeautifulSoup
        url = "https://recrutement.ademe.fr/offre-de-emploi/liste-offres.aspx"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "fr-FR",
        }
        try:
            r = requests.get(url, headers=headers, timeout=15)
        except requests.exceptions.SSLError:
            print("  ADEME: erreur SSL, retry sans vérification du certificat")
            r = requests.get(url, headers=headers, timeout=15, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")

        jobs = []
        cards = soup.find_all("a", class_=lambda c: c and "offer" in str(c).lower())
        if not cards:
            cards = soup.find_all(["li", "div", "article"], class_=lambda c: c and any(
                w in str(c).lower() for w in ["offer", "offre", "job", "poste"]))

        print(f"  ADEME → {len(cards)} cartes trouvées")

        for card in cards:
            title_el = card.find(["h2", "h3", "h4", "span", "p"])
            title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)

            if not title or len(title) < 5:
                continue
            if any(excl in title.lower() for excl in exclusions):
                print(f"  Exclu ADEME: {title}")
                continue

            href = card.get("href", "")
            if not href:
                link_el = card.find("a", href=True)
                href = link_el["href"] if link_el else ""
            full_url = href if href.startswith("http") else "https://recrutement.ademe.fr" + href

            location_el = card.find(["span", "p", "div"], class_=lambda c: c and any(
                w in str(c).lower() for w in ["loc", "lieu", "ville", "city", "region"]))
            location = location_el.get_text(strip=True) if location_el else "France"

            if not matches_location(location):
                continue

            jobs.append({
                "id": full_url,
                "title": title,
                "company": "ADEME",
                "location": location,
                "url": full_url,
                "description": "",
                "source": "ADEME",
            })

        print(f"  ADEME → {len(jobs)} après filtre")
        return jobs

    except Exception as e:
        print(f"  EXCEPTION ADEME: {e}")
        return []


JTMS_LOCATIONS = ["France", "Paris--France", "Marseille--France"]

# jobs.makesense.org agrège des milliers d'offres tous secteurs (solidarité,
# santé, culture...) : pas de paramètre de recherche fiable connu côté URL,
# donc on filtre nous-mêmes les titres sur ces racines climat/RSE après scraping.
JTMS_CLIMATE_TERMS = (
    "climat", "carbone", "carbon", "rse", "durab", "écolog", "ecolog",
    "transition écolog", "transition energ", "décarbon", "decarbon",
    "bas-carbone", "environnement", "biodiversité", "biodiversite",
)


def search_jtms():
    """Scrape jobs.makesense.org (« Jobs that make sense »), plateforme
    d'offres à impact recommandée pour élargir au-delà des entreprises déjà
    suivies. Pas d'API publique documentée : on scrape les pages de
    résultats par localisation et on filtre nous-mêmes sur des mots-clés
    climat/RSE, le site n'ayant pas de paramètre de recherche fiable connu."""
    exclusions = get_exclusions()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "fr-FR",
    }
    jobs = []
    seen_urls = set()
    for loc in JTMS_LOCATIONS:
        try:
            from bs4 import BeautifulSoup
            url = f"https://jobs.makesense.org/fr/s/jobs/{loc}/all/cdi"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  JTMS '{loc}' → HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.find_all("a", href=lambda h: h and "/fr/jobs/" in h and "/fiches-metiers/" not in h)
            print(f"  JTMS '{loc}' → {len(links)} liens d'offres trouvés")
            for i, link in enumerate(links[:3]):
                print(f"  JTMS DEBUG HTML [{loc}][{i}]: {str(link)[:600]}")
            for link in links:
                href = link.get("href", "")
                job_url = href if href.startswith("http") else "https://jobs.makesense.org" + href
                if job_url in seen_urls:
                    continue
                title = link.get_text(strip=True) or link.get("aria-label", "")
                if not title or len(title) < 5:
                    continue
                if not any(term in title.lower() for term in JTMS_CLIMATE_TERMS):
                    continue
                seen_urls.add(job_url)
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, "N/A", loc, "JTMS", "mot-clé exclu")
                    continue
                jobs.append({
                    "id": job_url,
                    "title": clean_text(title),
                    "company": "N/A",
                    "location": "France" if loc == "France" else loc.split("--")[0],
                    "url": job_url,
                    "description": "",
                    "source": "JTMS",
                })
        except Exception as e:
            print(f"  EXCEPTION JTMS '{loc}': {e}")
    print(f"  JTMS total → {len(jobs)} offres après filtre")
    return jobs


def _wttj_text(value):
    """Normalise un champ WTTJ qui peut être une chaîne, un dict localisé
    ({'fr': '...', 'en': '...'}) ou un dict {'name': '...'} en chaîne simple."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("fr", "fr-fr", "en", "name", "label", "value"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


def _wttj_find_job_lists(node, found):
    """Parcourt récursivement le JSON __NEXT_DATA__ d'une page WTTJ pour
    repérer les listes d'offres (l'API publique JSON n'existe plus, les
    offres ne sont disponibles que via ce JSON embarqué côté serveur)."""
    if isinstance(node, dict):
        keys = set(node.keys())
        if {"name", "slug"}.issubset(keys) or {"title", "slug"}.issubset(keys):
            if any(k in keys for k in ("contractType", "contract_type", "officeIds", "offices", "publishedAt")):
                found.append(node)
        for v in node.values():
            _wttj_find_job_lists(v, found)
    elif isinstance(node, list):
        for item in node:
            _wttj_find_job_lists(item, found)


def search_wttj():
    """Récupère les offres directement chez les entreprises suivies en
    scrapant la page carrière publique de Welcome to the Jungle (l'API JSON
    publique n'existe plus, cf. HTTP 404 sur tous les slugs). Les offres
    sont extraites du JSON __NEXT_DATA__ embarqué dans la page ; à défaut,
    repli sur un scraping HTML générique des liens d'offres."""
    exclusions = get_exclusions()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "fr-FR",
    }
    jobs = []
    for slug, label in WTTJ_COMPANIES.items():
        try:
            url = f"https://www.welcometothejungle.com/fr/companies/{slug}/jobs"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  WTTJ {label} ({slug}) → HTTP {r.status_code} (slug à vérifier ?)")
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            raw = []
            script = soup.find("script", id="__NEXT_DATA__")
            if script and script.string:
                try:
                    next_data = json.loads(script.string)
                    _wttj_find_job_lists(next_data, raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            if raw:
                print(f"  WTTJ {label} → {len(raw)} brutes (JSON embarqué)")
            else:
                # Repli : scraping HTML générique des liens vers des offres
                links = soup.find_all("a", href=lambda h: h and "/jobs/" in h)
                print(f"  WTTJ {label} → 0 via JSON, {len(links)} liens en repli HTML")
                for link in links:
                    title = link.get_text(strip=True)
                    if not title or len(title) < 5:
                        continue
                    if any(excl in title.lower() for excl in exclusions):
                        log_excluded(title, label, "", "WTTJ", "mot-clé exclu")
                        continue
                    href = link.get("href", "")
                    job_url = href if href.startswith("http") else "https://www.welcometothejungle.com" + href
                    jobs.append({
                        "id": job_url,
                        "title": clean_text(title),
                        "company": label,
                        "location": "France",
                        "url": job_url,
                        "description": "",
                        "source": "WTTJ",
                        "company_watch": True,
                    })
                continue

            for job in raw:
                title = clean_text(_wttj_text(job.get("name") or job.get("title")))
                if not title:
                    continue

                contract = _wttj_text(job.get("contract_type") or job.get("contract")).lower()
                if any(bad in contract for bad in WTTJ_CONTRACT_EXCLUDE):
                    log_excluded(title, label, "", "WTTJ", f"contrat {contract}")
                    continue
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, label, "", "WTTJ", "mot-clé exclu")
                    continue

                offices = job.get("offices") or job.get("office") or []
                if isinstance(offices, dict):
                    offices = [offices]
                office = offices[0] if offices else {}
                city = _wttj_text(office.get("city") or office.get("name"))
                country_raw = office.get("country_code") or office.get("country") or ""
                if isinstance(country_raw, dict):
                    country_raw = country_raw.get("code") or country_raw.get("name") or ""
                country = str(country_raw).upper()
                remote = _wttj_text(job.get("remote")).lower()
                # On garde France + offres en télétravail ; on écarte un bureau
                # explicitement étranger (sauf si remote).
                if country and country not in ("FR", "FRANCE") and "remote" not in remote and not remote.startswith("full"):
                    continue
                location = city or ("Télétravail" if remote else "France")

                job_slug = job.get("slug") or job.get("reference") or ""
                job_url = (f"https://www.welcometothejungle.com/fr/companies/{slug}/jobs/{job_slug}"
                           if job_slug else f"https://www.welcometothejungle.com/fr/companies/{slug}")

                desc = clean_text(_wttj_text(job.get("profile") or job.get("description")))
                if not desc:
                    desc = " · ".join(p for p in [contract, city] if p)

                jobs.append({
                    "id": f"wttj-{slug}-{job_slug}",
                    "title": title,
                    "company": label,
                    "location": location,
                    "url": job_url,
                    "description": desc[:150] + "..." if len(desc) > 150 else desc,
                    "source": "WTTJ",
                    "company_watch": True,
                })
        except Exception as e:
            print(f"  EXCEPTION WTTJ {label}: {e}")
    print(f"  WTTJ total → {len(jobs)} offres après filtre")
    return jobs


MISTRAL_BATCH_SIZE = 25


def _filter_jobs_batch(jobs, reasons_text):
    """Filtre un lot d'offres via Mistral. Lève en cas d'échec (API ou JSON
    invalide) : à l'appelant de décider du repli, plutôt que de renvoyer le
    lot non filtré (ce qui spammerait l'utilisateur avec tout le bruit que
    le filtre est censé éliminer)."""
    mistral_key = os.environ["MISTRAL_API_KEY"]
    jobs_text = "\n".join([
        f"{i}. TITRE: {job['title']} | ENTREPRISE: {job['company']} | LIEU: {job['location']}"
        + (f" | ENTREPRISE CIBLÉE: oui" if job.get("company_watch") else "")
        + (f"\n   Description: {job.get('description', '')[:200]}" if job.get('description') else "")
        for i, job in enumerate(jobs)
    ])

    prompt = f"""Tu es un assistant de recherche d'emploi très sélectif. Voici le profil du candidat :
{PROFILE}

Offres récemment rejetées par le candidat et raisons (apprends-en) :
{reasons_text}

Offres du jour à évaluer :
{jobs_text}

RÈGLES DE DÉCISION (applique-les strictement) :

GARDE UNIQUEMENT si le poste porte VRAIMENT sur le climat / la durabilité / la RSE
au niveau stratégie ou conseil, ET correspond à la séniorité du candidat (confirmé,
pas junior). Exemples à garder : consultant·e climat/carbone/RSE, chargé·e de mission
climat ou transition, expert·e politiques publiques climat, manager décarbonation,
responsable RSE stratégique, chef·fe de projet bilan carbone / stratégie bas-carbone.

REJETTE (keep=false) dans TOUS ces cas, MÊME si « ENTREPRISE CIBLÉE: oui » :
- métiers tech/produit/data (developer, engineer, fullstack, software, data scientist, devops, product manager)
- commercial / vente / sales / account executive / business developer / marketing
- RH / paie / recrutement / office manager / assistant·e
- finance / comptabilité / achats / appels d'offres
- pédagogie / formation hors climat, support, ops génériques
- postes terrain, techniciens, juniors, stages, alternances
- tout poste sans lien explicite et central avec le climat/la durabilité
- ressemble aux offres rejetées ci-dessus

IMPORTANT : « ENTREPRISE CIBLÉE: oui » signifie seulement que l'entreprise est
intéressante — le POSTE doit quand même passer les règles ci-dessus. Une offre
de développeur ou de commercial chez une entreprise ciblée doit être REJETÉE.

Dans le doute, REJETTE.

Réponds UNIQUEMENT avec un JSON (sans texte avant/après, sans backticks) :
[{{"index": 0, "keep": true, "reason": "consultant climat senior, correspond au profil"}}, ...]"""

    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {mistral_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "mistral-small-latest",
            "max_tokens": 4000,
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
        if idx is None or idx >= len(jobs):
            continue
        if decision.get("keep"):
            kept.append(jobs[idx])
        else:
            excl_job = jobs[idx]
            reason = decision.get('reason', '')
            print(f"  IA exclu: {excl_job['title']} → {reason}")
            log_excluded(excl_job['title'], excl_job['company'], excl_job.get('location', ''),
                         excl_job.get('source', ''), f"IA: {reason}")
    return kept


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

    # On découpe en lots : avec beaucoup de sources actives, le nombre
    # d'offres peut dépasser ce qu'un seul appel Mistral peut traiter sans
    # tronquer sa réponse JSON (cause vue en prod : "Unterminated string").
    kept = []
    for start in range(0, len(jobs), MISTRAL_BATCH_SIZE):
        batch = jobs[start:start + MISTRAL_BATCH_SIZE]
        try:
            kept += _filter_jobs_batch(batch, reasons_text)
        except Exception as e:
            print(f"  EXCEPTION Mistral (lot {start}-{start+len(batch)}): {e}")
            print("  → lot écarté par sécurité (pas de filtre fiable = pas d'envoi non filtré)")

    print(f"  Mistral: {len(kept)}/{len(jobs)} offres conservées")
    return kept


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
        "ADEME": "#c04a00",
        "WTTJ": "#7a6500",
        "JTMS": "#6a1b9a",
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


def excluded_section_html(excluded_log):
    if not excluded_log:
        return ""
    rows = ""
    for item in excluded_log[:100]:
        rows += f"""
        <tr>
            <td style="padding:6px 10px;font-size:12px;color:#555;border-bottom:1px solid #eee">{item['title']}</td>
            <td style="padding:6px 10px;font-size:12px;color:#888;border-bottom:1px solid #eee">{item['company']}</td>
            <td style="padding:6px 10px;font-size:12px;color:#888;border-bottom:1px solid #eee">{item.get('location', '')}</td>
            <td style="padding:6px 10px;font-size:12px;color:#888;border-bottom:1px solid #eee">{item['source']}</td>
            <td style="padding:6px 10px;font-size:12px;color:#b56900;border-bottom:1px solid #eee">{item['reason']}</td>
        </tr>
        """
    return f"""
    <details style="margin-top:2.5rem;padding:14px;background:#fafafa;border-radius:8px;border:0.5px solid #e0e0e0">
        <summary style="cursor:pointer;font-size:14px;color:#555;font-weight:500">
            🗂️ Voir les {len(excluded_log)} offre(s) écartée(s) aujourd'hui
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
    watchlist = [j for j in jobs if j.get("company_watch")]
    geo_jobs = [j for j in jobs if not j.get("company_watch")]
    marseille, paca, paris = categorize(geo_jobs)
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
    <p style="color:#555">{total} offre(s) dont <strong style="color:#e05c2a">{new_total} nouvelle(s)</strong> — Entreprises ciblées ({len(watchlist)}) · Marseille ({len(marseille)}) · PACA ({len(paca)}) · Paris ({len(paris)})</p>
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
    if (marseille or paca or paris) and watchlist:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    body += section_html("Entreprises ciblées", "🏢", watchlist, "#0a5c54")
    body += excluded_section_html(excluded_log or [])
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
            all_jobs += search_france_travail(keyword, location)
            all_jobs += search_hellowork(keyword, location)
            all_jobs += search_jooble(keyword, location)
        # Greenjob.fr abandonné : recherche par mot-clé non fonctionnelle côté site,
        # et contenu majoritairement stages/bénévolat hors profil.

    all_jobs += search_ademe()
    all_jobs += search_wttj()
    all_jobs += search_adzuna_companies()
    all_jobs += search_jtms()

    before_loc_filter = len(all_jobs)
    filtered_jobs = []
    for job in all_jobs:
        if is_location_excluded(job.get("location", "")):
            log_excluded(job["title"], job["company"], job.get("location", ""),
                         job.get("source", ""), "localisation exclue")
            continue
        filtered_jobs.append(job)
    all_jobs = filtered_jobs
    print(f"\nLocalisations exclues : {before_loc_filter - len(all_jobs)} offre(s)")

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
    print(f"Total écarté : {len(EXCLUDED_LOG)} offres")

    sources_count = {}
    for j in jobs:
        sources_count[j.get("source", "?")] = sources_count.get(j.get("source", "?"), 0) + 1
    print(f"Répartition par source (offres conservées) : {sources_count}")

    html = build_email(jobs, feedback_url, EXCLUDED_LOG)
    send_email(html, len(jobs))

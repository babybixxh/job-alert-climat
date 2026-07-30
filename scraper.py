import smtplib
import os
import json
import requests
import re
import time
import threading
import urllib3
from html import unescape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

KEYWORDS = [
    "consultant climat",
    "consultant stratégie climat",
    "consultant transition",
    "climate solutions consultant",
    "carbon analyst",
    "risque climatique",
    "adaptation climatique",
    "climate risk analyst",
    "adaptation specialist",
    "climate finance",
    "energy analyst",
    "bilan carbone",
    "transition écologique",
    "chargé mission climat",
    "décarbonation",
    "politiques climatiques",
    "responsable développement durable",
    "chargé de mission développement durable",
]

LOCATIONS = ["Paris", "Marseille", "Aix-en-Provence", "Toulon", "Nice"]

# Mots-clés FR + EN pour les offres 100% télétravail Europe (Remotive, Arbeitnow) :
# l'utilisateur lit l'anglais et le français, donc on couvre les deux.
REMOTE_KEYWORDS = [
    "climate", "sustainability", "carbon", "ESG", "decarbonization",
    "climat", "durable", "carbone",
    # Axe risque physique / adaptation / agri-climat (surtout offres remote EN)
    "climate risk", "physical risk", "adaptation", "resilience",
    "nature-based", "natural capital", "catastrophe", "cat model",
    "parametric", "agtech", "climate modelling", "climate modeling", "TCFD",
    # Organisations internationales / think tanks (intitulés fréquents)
    "climate change specialist", "climate finance", "programme officer",
    "policy analyst", "research analyst", "readiness", "mrv", "climate investment",
    # Modélisation énergie/climat & analyse quantitative
    "energy analyst", "energy model", "scenario analysis", "research associate",
    "modelling analyst", "climate economist", "quantitative analyst",
]

# Communes trop excentrées à écarter même quand elles ressortent comme
# "Toulon"/"Nice" dans les résultats bruts (ex: La Garde est une commune
# distincte de l'agglomération toulonnaise, jugée hors zone par Arnaud).
LOCATION_EXCLUSIONS = ["la garde"]

# Employeurs à écarter systématiquement, quelle que soit la source.
COMPANY_EXCLUSIONS = ["eqosphere"]

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
    # NB : « avant-vente »/« présales » ne sont plus exclus en dur — l'avant-vente
    # TECHNIQUE chez les climate-tech (Solutions/Sales Engineer) est recherchée ;
    # l'IA écarte l'avant-vente sans lien climat.
    "amoa finance",
    # Commercial / relation client purs et dev logiciel : jamais pertinents,
    # même chez une entreprise suivie (garde déterministe, l'IA laissait
    # parfois passer un SDR/BDR malgré les règles).
    "sdr", "bdr", "sales representative", "account executive",
    "business developer",
    "fullstack", "full stack", "full-stack", "frontend", "backend",
    "software engineer", "devops",
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
    # Recentrage sur climat-risque/adaptation/agri (demandé) : on écarte les
    # domaines connexes non ciblés. (« RSE » est traité à part via is_rse_title,
    # en mot entier, pour ne pas bloquer par erreur « diverse », « traverse »…)
    "qhse", "compliance", "conformité",
    "efficacité énergétique", "energy efficiency",
    "hydrogène", "hydrogen", "batterie", "battery",
    "solaire", "solar", "photovoltaïque", "photovoltaique",
]

SEEN_FILE = "seen_jobs.json"
TODAY_FILE = "today_jobs.json"
REJECTED_FILE = "rejected_keywords.json"
REJECTED_REASONS_FILE = "rejected_reasons.json"
# Cache des décisions du filtre IA (clé titre|entreprise → garder/rejeter).
# Évite de réinterroger Mistral chaque jour sur des offres déjà tranchées.
AI_VERDICTS_FILE = "ai_verdicts.json"
AI_VERDICTS_MAX = 3000
# Version du prompt/règles IA. À incrémenter dès qu'on modifie le PROFILE ou les
# règles de décision : les verdicts en cache d'une version antérieure sont alors
# ré-évalués (sinon d'anciennes décisions périmées seraient rejouées).
AI_PROMPT_VERSION = 10

# Suivi de la santé des sources : pour chaque source, nombre de jours
# consécutifs sans aucune offre brute (parseur potentiellement cassé).
SOURCE_HEALTH_FILE = "source_health.json"
SOURCE_HEALTH_ALERT = 3  # alerte à partir de 3 jours d'affilée à zéro

# Score IA en dessous duquel une offre n'est PAS poussée en notif temps réel.
PRIORITY_SCORE = 85
# À Paris (hors entreprises suivies), score IA minimal pour retenir une offre :
# ne garde que le conseil/stratégie senior bien noté, pas le RSE générique.
PARIS_MIN_SCORE = 70
# Score par défaut attribué à une offre gardée mais jugée avant l'ajout du
# scoring (cache hérité sans champ "score").
DEFAULT_KEPT_SCORE = 60

# Boards Greenhouse d'entreprises climat suivies en direct (offres à la
# source, avant les agrégateurs). Token = segment d'URL boards.greenhouse.io/
# <token>. Surchargé par la variable d'env GREENHOUSE_BOARDS (CSV) si fournie.
#
# Liste réduite aux seuls tokens confirmés VIVANTS par sondage CI : la plupart
# des boîtes climat FR (Greenly, Sami, Deepki, Carbone 4…) ne sont PAS sur des
# boards Greenhouse publics (404) et sont déjà couvertes via Adzuna-entreprises,
# WTTJ et l'APEC. Ces boards-ci sont surtout US ; le filtre géographique ne
# laisse passer que leurs rares postes FR/Europe/remote. Pour en ajouter,
# poser le token dans le secret GREENHOUSE_BOARDS (séparés par des virgules).
GREENHOUSE_BOARDS_DEFAULT = ["watershed", "patch", "carbonchain", "tomorrow"]

# Entreprises climat suivies via l'API publique Lever (api.lever.co/v0/
# postings/<company>). Vide par défaut : aucun board climat FR/EU pertinent
# trouvé sur Lever lors du sondage. À renseigner via le secret LEVER_COMPANIES.
LEVER_COMPANIES_DEFAULT = []

# Organisations utilisant SmartRecruiters comme ATS (API publique des offres :
# api.smartrecruiters.com/v1/companies/<id>/postings). id = segment d'URL
# jobs.smartrecruiters.com/<id>. Surchargé par le secret SMARTRECRUITERS_COMPANIES.
# Les logs CI (« SmartRecruiters <id> → N / HTTP 404 ») révèlent les id à corriger.
SMARTRECRUITERS_COMPANIES_DEFAULT = ["OECD", "IEA", "IRENA"]

# Marqueurs d'offres US à écarter sur les boards internationaux (Greenhouse/
# Lever/LinkedIn) : on ne garde que France / Europe / télétravail.
US_LOCATION_MARKERS = [
    "united states", "usa", "u.s.", ", tx", ", ny", ", ca", ", fl", ", oh",
    ", il", ", wa", ", ma", ", co", "remote - us", "remote, us", "remote (us",
]
EU_LOCATION_TERMS = [
    "paris", "marseille", "aix", "toulon", "nice", "provence", "paca",
    "france", "europe", "emea", "français",
]

# Entreprises suivies en direct via l'API publique Welcome to the Jungle.
# Clé = slug WTTJ (segment d'URL welcometothejungle.com/fr/companies/<slug>),
# valeur = libellé affiché. Les slugs sont des paris raisonnables : les logs CI
# (« WTTJ <label> → N brutes ») révèlent ceux à corriger si une entreprise renvoie 0.
WTTJ_COMPANIES = {
    # Conseil climat / RSE
    "carbone-4": "Carbone 4",
    "quantis": "Quantis",
    "utopies": "Utopies",
    "bl-evolution": "BL évolution",
    "i-care": "I Care",
    "carbon-cutter": "Carbon Cutter",
    "adaptation-s": "adaptation/s",
    "cci-france": "CCI France",
    # Plateformes de comptabilité carbone (SaaS)
    "sami": "Sami",
    "greenly": "Greenly",
    "aktio": "Aktio",
    "tennaxia": "Tennaxia",
    "traace": "Traace",
    "sweep": "Sweep",
    "carbometrix": "Carbometrix",
    "carbo": "Carbo",
    "carbonfact": "Carbonfact",
    "watershed": "Watershed",
    "plan-a": "Plan A",
    "normative": "Normative",
    "isometric": "Isometric",
    "nelson": "Nelson",
    "persefoni": "Persefoni",
    "sinai": "Sinai Technologies",
    "carbonchain": "CarbonChain",
    # Data ESG / CSRD
    "ecovadis": "EcoVadis",
    "deepki": "Deepki",
    "greenomy": "Greenomy",
    "position-green": "Position Green",
    "coolset": "Coolset",
    # Data climat « hard » : satellite, risque physique, adaptation
    "kayrros": "Kayrros",
    "axa-climate": "AXA Climate",
    "descartes-underwriting": "Descartes Underwriting",
    "callendar": "Callendar",
    "namr": "namR",
    "murmuration": "Murmuration",
    "finres": "Finres",
    "resallience": "Resallience",
    # Assurance / réassurance / risque physique (beaucoup d'anglophones UK/US :
    # remonteront surtout en remote ou pas du tout via nos sources FR)
    "howden": "Howden",
    "marsh-mclennan": "Marsh McLennan",
    "aon": "Aon",
    "wtw": "WTW",
    "guy-carpenter": "Guy Carpenter",
    "scor": "SCOR",
    "swiss-re": "Swiss Re",
    "munich-re": "Munich Re",
    "jupiter-intelligence": "Jupiter Intelligence",
    "cervest": "Cervest",
    "climate-x": "Climate X",
    "mitiga-solutions": "Mitiga Solutions",
    "xdi": "XDI",
    "sust-global": "Sust Global",
    "kettle": "Kettle",
    "iceye": "ICEYE",
    "moodys-rms": "Moody's RMS",
    "fathom": "Fathom",
    "riskthinking-ai": "riskthinking.AI",
    # Institutions / think tanks / standards (publient surtout sur leurs sites
    # ou des job boards ONU spécialisés → couverture partielle via nos sources)
    "eea": "European Environment Agency",
    "i4ce": "I4CE",
    "climate-bonds-initiative": "Climate Bonds Initiative",
    "wri": "WRI",
    "climate-analytics": "Climate Analytics",
    "iddri": "IDDRI",
    "carbon-tracker": "Carbon Tracker",
    "2dii": "2 Investing Initiative",
    "climateworks": "ClimateWorks",
    "cadmus": "Cadmus",
    "icf": "ICF",
    "wsp": "WSP",
    "ramboll": "Ramboll",
    "cdp": "CDP",
    "climate-policy-initiative": "Climate Policy Initiative",
    "bruegel": "Bruegel",
    "ember": "Ember",
    "rmi": "Rocky Mountain Institute",
    "e3g": "E3G",
    "agora-energiewende": "Agora Energiewende",
    "crea": "CREA",
    "odi": "ODI",
    "chatham-house": "Chatham House",
    "jrc": "Joint Research Centre",
    # Organisations internationales / intergouvernementales / bailleurs
    # (expatriation possible ; AFD, BEI, Proparco peuvent aussi publier en France)
    "green-climate-fund": "Green Climate Fund",
    "unep": "UNEP",
    "unep-fi": "UNEP FI",
    "undp": "UNDP",
    "world-bank": "World Bank",
    "eib": "European Investment Bank",
    "afd": "AFD",
    "giz": "GIZ",
    "adaptation-fund": "Adaptation Fund",
    "ndc-partnership": "NDC Partnership",
    "global-center-adaptation": "Global Center on Adaptation",
    "iea": "IEA",
    "oecd": "OECD",
    "irena": "IRENA",
    "ipcc": "IPCC",
    "unfccc": "UNFCCC",
    "imf": "IMF",
    "itf-oecd": "International Transport Forum",
    "fao": "FAO",
    "ebrd": "EBRD",
    "kfw": "KfW",
    "proparco": "Proparco",
    "gef": "Global Environment Facility",
    "cif": "Climate Investment Funds",
    # Agro × climat : agtech carbone, transition agricole, agri-data
    "soil-capital": "Soil Capital",
    "rize-ag": "Rize",
    "agreena": "Agreena",
    "myeasycarbon": "MyEasyCarbon",
    "carbon-maps": "Carbon Maps",
    "itk": "ITK",
    "sencrop": "Sencrop",
    "weenat": "Weenat",
    "carbonfarm": "CarbonFarm",
    "klim": "Klim",
    "boomitra": "Boomitra",
    "regrow": "Regrow",
    "cropin": "Cropin",
    "aqysta": "aQysta",
    "perennial": "Perennial",
    # Industriels agroalimentaires et coopératives (postes internes
    # décarbonation/climat ; le filtre IA ne garde que les rôles climat)
    "danone": "Danone",
    "bel": "Bel",
    "bonduelle": "Bonduelle",
    "roquette": "Roquette",
    "savencia": "Savencia",
    "invivo": "InVivo",
    "sodiaal": "Sodiaal",
    "terrena": "Terrena",
    # Énergie / industrie
    "metron": "Metron",
    "purecontrol": "Purecontrol",
}

# Types de contrat WTTJ à écarter (on veut CDI/CDD, pas stage/alternance/VIE).
WTTJ_CONTRACT_EXCLUDE = ("intern", "apprentice", "apprentiss", "stage", "vie", "vix")

FT_COMMUNES = {
    # Paris et Marseille sont découpés en arrondissements dans le référentiel
    # géographique de l'API FT : le code INSEE "ville entière" (75056/13055)
    # n'y est pas reconnu comme code commune valide (HTTP 400). On utilise le
    # code du 1er arrondissement, couvert ensuite par le rayon "distance".
    "Paris": "75101",
    "Marseille": "13201",
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
ou une ONG/think tank influent. À Marseille et en PACA (sa zone prioritaire), il est aussi ouvert à
des postes qualifiés de développement durable / transition écologique en entreprise, de chargé
de mission développement durable / transition en collectivité ou établissement public, ou de coordination
de projets environnement / économie circulaire en association ou dans l'ESS ; à Paris il vise en priorité
le conseil et la stratégie climat.
IMPORTANT : il ne veut PLUS de postes de RSE générique (responsable/chargé RSE, responsabilité
sociétale) — ces offres sont déjà écartées en amont ; concentre-toi sur climat/carbone,
risque physique/adaptation et agri-climat.
NIVEAU / SÉNIORITÉ (filtre décisif) : il vise la fourchette Associate → Manager / Responsable /
Chargé·e senior / Consultant·e / Officer / Specialist / Advisor / Analyst. REJETTE les postes
trop hauts (Head of, Director, Chief, Principal, Lead, VP, Partner) et ceux exigeant 10+ ans
d'expérience ou un doctorat/PhD requis — hors de portée. Ne rejette pas pour autant les postes
« confirmés » : c'est le haut du panier (direction) et l'exigence 10+ ans/PhD qu'on écarte.
Il est OUVERT à l'EXPATRIATION pour les grandes institutions climat internationales (ONU/UNEP/
PNUD, Green Climate Fund, Agence européenne pour l'environnement, Global Center on Adaptation,
Banque mondiale, BEI, AFD, GIZ, think tanks WRI/I4CE/IDDRI/Climate Analytics) : ne pénalise pas
ces offres sur le seul critère géographique.
Il est aussi TRÈS intéressé par les éditeurs de logiciels de comptabilité carbone / plateformes
data-climat & ESG (Sweep, Greenly, Sami, Traace, Carbometrix, Carbonfact, Watershed, Plan A,
Normative, EcoVadis, Deepki, Kayrros, AXA Climate, Descartes, Metron…), où son profil ingénieur +
conseil carbone se valorise sur des rôles hybrides produit/conseil/méthodo : Climate Solutions
Consultant, Climate Expert, Carbon Analyst, Implementation / Onboarding Consultant, Solutions
Engineer / Sales Engineer (avant-vente technique), Carbon Accounting Methodologist / Methodology
Expert, Climate Risk Analyst, et à moyen terme Product Manager. Il code sur son temps libre
(automatisations LLM), donc le côté « builder » technique est un atout, pas un frein.
Son SWEET SPOT (score le PLUS élevé) : l'intersection AGRO × CLIMAT × DATA et le
RISQUE CLIMATIQUE PHYSIQUE / ADAPTATION :
- carbon farming et MRV agricole (Soil Capital, Rize, Agreena, MyEasyCarbon, Klim,
  Boomitra, Regrow, Perennial), ACV alimentaire (Carbon Maps), agri-data (ITK, Sencrop, Weenat) ;
- risque climatique physique, adaptation, résilience, catastrophe / cat modelling,
  assurance paramétrique, nature-based solutions, natural capital (AXA Climate, Finres,
  Descartes, Resallience, Callendar, Cervest, Jupiter, Climate X, Mitiga, XDI, ICEYE,
  Moody's RMS, Fathom, réassureurs) ;
- postes internes décarbonation / scope 3 / FLAG chez les industriels agroalimentaires et
  coopératives (Danone, Bel, Bonduelle, Roquette, Savencia, InVivo, Sodiaal, Terrena) ;
- think tanks / standards climat (I4CE, IDDRI, WRI, Climate Analytics, Carbon Tracker,
  Climate Bonds Initiative).
Intitulés très recherchés : Climate Risk Analyst, Physical Climate Risk, Climate Adaptation /
Adaptation Specialist, Resilience Analyst, Nature-based Solutions, Natural Capital, Climate Data
Analyst, Climate Modelling, Catastrophe / Cat Modelling, Parametric Insurance, TCFD, Climate
Scenario Analysis, Agri Climate Specialist, Climate Product Manager.
Compétences qui font mouche (bonus de score si présentes) : GHG Protocol, SBTi, ACT, Bilan
Carbone, CSRD, GIS/SIG, Python, R, modélisation / modelling, analyse quantitative, scenario
analysis, energy model (TIMES, Vensim), économétrie / econometric, forecasting, scénarios
climatiques (RCP/IPCC), downscaling, hazard modelling, vulnerability assessment. Il vise aussi
les postes d'analyste/modélisateur énergie-climat (Energy Analyst, Energy Modeler, Scenario
Analyst, Quantitative Analyst, Research Associate, Climate Economist) dans les agences (IEA,
OECD, IRENA…) et think tanks. Donne un score élevé aux postes de cette zone.
Il ne veut PAS de postes terrain, techniciens (maintenance/chantier), nucléaire,
achats, RH, finance/comptabilité, stages ou alternances.
"""


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = unescape(value)
    return " ".join(value.split())


def format_job_date(raw):
    """Formate une date ISO (APEC, Climatebase, Adzuna…) en JJ/MM/AAAA.
    Tolère les variantes de timezone : on ne lit que la partie AAAA-MM-JJ."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(raw or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def format_salary_range(lo, hi):
    """Formate une fourchette de salaire numérique annuel en « 40 k€ – 55 k€ ».
    Renvoie '' si les valeurs ne sont pas exploitables (APEC fournit déjà un
    libellé texte, donc n'utilise pas cette fonction)."""
    def k(v):
        try:
            n = float(v)
            return f"{round(n / 1000)} k€" if n >= 1000 else ""
        except (TypeError, ValueError):
            return ""
    lo_s, hi_s = k(lo), k(hi)
    if lo_s and hi_s and lo_s != hi_s:
        return f"{lo_s} – {hi_s}"
    return lo_s or hi_s


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


def keep_international_location(value):
    """Pour les boards internationaux (Greenhouse/Lever/LinkedIn) : ne garde
    que France / Europe / télétravail, en écartant les offres clairement US.
    Évite d'inonder l'alerte d'offres américaines non pertinentes."""
    text = (value or "").lower()
    if any(m in text for m in US_LOCATION_MARKERS) and not any(e in text for e in EU_LOCATION_TERMS):
        return False
    if any(e in text for e in EU_LOCATION_TERMS):
        return True
    return any(r in text for r in ["remote", "anywhere", "télétravail", "teletravail"])


_TITLE_NOISE = re.compile(
    r"\b(h/f|f/h|m/f|m/w|w/m|h-f|f-h|cdi|cdd|temps plein|full[- ]?time|"
    r"freelance|alternance|stage)\b", re.IGNORECASE)


def normalize_title(title):
    """Normalise un intitulé pour la déduplication inter-sources : minuscules,
    sans accents, sans mentions parasites (H/F, CDI…), sans ponctuation, mots
    triés. « Consultant Climat (H/F) - CDI » et « climat consultant cdi »
    produisent ainsi la même clé."""
    import unicodedata
    t = unicodedata.normalize("NFD", (title or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = _TITLE_NOISE.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    tokens = [w for w in t.split() if len(w) > 1]
    return " ".join(sorted(tokens))


# Marqueurs Paris / Île-de-France. Sert à la restriction temporaire : à Paris,
# on ne retient une offre QUE si elle vient d'une entreprise suivie.
PARIS_TERMS = [
    "paris", "île-de-france", "ile-de-france", "idf", "la défense", "la defense",
    "hauts-de-seine", "seine-saint-denis", "val-de-marne", "nanterre",
    "boulogne-billancourt", "levallois", "issy-les-moulineaux", "montreuil",
    "saint-denis", "courbevoie", "puteaux",
]


def is_paris_location(value):
    text = (value or "").lower()
    return any(term in text for term in PARIS_TERMS)


def is_remote_location(value):
    text = (value or "").lower()
    return any(term in text for term in
              ["remote", "télétravail", "teletravail", "anywhere", "full remote"])


def _is_us_location(value):
    """Vrai si le libellé de lieu est aux États-Unis (ex. « New York, NY, US »,
    « United States », « Texas, US »). Sert à écarter les « remote » US-only."""
    t = (value or "").lower()
    return ", us" in t or "united states" in t or t.strip() in ("us", "usa")


# Détection RSE en MOT ENTIER (recentrage : Arnaud exclut désormais la RSE
# générique). Évite les faux positifs des sous-chaînes (« diverse », « traverse »).
_RSE_RE = re.compile(r"\brse\b|responsabilit[ée]\s+soci[ée]tale|responsabilit[ée]\s+sociale",
                     re.IGNORECASE)


def is_rse_title(title):
    return bool(_RSE_RE.search(title or ""))


# Plafond de séniorité : Arnaud vise la fourchette Associate → Manager/Responsable.
# On écarte au titre les postes trop hauts (Head/Director/Chief/Principal/Lead/VP).
# Mot entier pour ne pas attraper « directorate », « leadership », etc.
_OVERSENIOR_RE = re.compile(
    r"\b(head of|chief|principal|managing director|director|directeur|directrice|"
    r"vice[- ]?president|vice[- ]?président|vp|partner|associé principal|lead|"
    r"senior manager)\b",
    re.IGNORECASE)


def is_over_senior_title(title):
    return bool(_OVERSENIOR_RE.search(title or ""))


# Seuil « salaire élevé » (en k€) pour la sous-section dédiée de l'email.
HIGH_SALARY_K = 55


def salary_max_k(value):
    """Extrait le plus haut montant annuel d'un libellé de salaire, en k€.
    Gère « 40 k€ – 55 k€ » (suffixe k) et « 45 000 - 55 000 € » (montant plein).
    Renvoie None si rien d'exploitable."""
    if not value:
        return None
    t = str(value).lower().replace("\xa0", " ").replace(" ", " ")
    vals = [int(x) for x in re.findall(r"(\d{2,3})\s*k", t)]
    for x in re.findall(r"\d[\d ]{3,}\d", t):
        n = int(x.replace(" ", ""))
        if n >= 1000:
            vals.append(round(n / 1000))
    vals = [v for v in vals if 15 <= v <= 500]
    return max(vals) if vals else None


def is_location_excluded(value):
    text = (value or "").lower()
    return any(term in text for term in LOCATION_EXCLUSIONS)


def is_company_excluded(value):
    text = (value or "").lower()
    return any(term in text for term in COMPANY_EXCLUSIONS)


_FT_TOKEN_CACHE = {"token": "", "expires_at": 0}


def get_ft_token():
    # Le token FT est valide ~30 min : on le met en cache pour tout le run au lieu
    # d'en redemander un par appel (45 requêtes OAuth/run), ce qui déclenchait le
    # rate-limit FT (400/réponses vides intermittentes sur certaines communes).
    if _FT_TOKEN_CACHE["token"] and time.time() < _FT_TOKEN_CACHE["expires_at"]:
        return _FT_TOKEN_CACHE["token"]
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
        payload = r.json()
        token = payload.get("access_token", "")
        _FT_TOKEN_CACHE["token"] = token
        _FT_TOKEN_CACHE["expires_at"] = time.time() + payload.get("expires_in", 1500) - 60
        return token
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
                "salary": format_salary_range(job.get("salary_min"), job.get("salary_max")),
                "date": job.get("created", ""),
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
        if r.status_code not in (200, 206):
            print(f"  FT '{keyword}' / '{location}' → HTTP {r.status_code}: {r.text[:300]}")
            return []
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


JTMS_LOCATIONS = [
    "France", "Paris--France", "Marseille--France",
    "Aix-en-Provence--France", "Toulon--France", "Nice--France",
]

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
            for link in links:
                href = link.get("href", "")
                job_url = href if href.startswith("http") else "https://jobs.makesense.org" + href
                if job_url in seen_urls:
                    continue
                title_tag = link.find("h3", class_="job__title")
                company_tag = link.find("span", class_="job__company")
                title = title_tag.get_text(strip=True) if title_tag else ""
                company = company_tag.get_text(strip=True) if company_tag else "N/A"
                if not title or len(title) < 5:
                    continue
                if not any(term in title.lower() for term in JTMS_CLIMATE_TERMS):
                    continue
                seen_urls.add(job_url)
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, company, loc, "JTMS", "mot-clé exclu")
                    continue
                jobs.append({
                    "id": job_url,
                    "title": clean_text(title),
                    "company": clean_text(company),
                    "location": "France" if loc == "France" else loc.split("--")[0],
                    "url": job_url,
                    "description": "",
                    "source": "JTMS",
                })
        except Exception as e:
            print(f"  EXCEPTION JTMS '{loc}': {e}")
    print(f"  JTMS total → {len(jobs)} offres après filtre")
    return jobs


# Mots-clés (slugs d'URL) interrogés sur Choisir le service public.
SP_KEYWORDS = [
    "climat", "developpement-durable", "transition-ecologique",
    "environnement", "rse", "energie",
]

# Départements PACA → libellé. Les offres TERRITORIALES encodent le département
# dans leur référence d'URL (« ...-reference-O0<dd>... », ex. O013 = Bouches-du-
# Rhône) : filtre fiable, sans dépendre du texte de la carte.
SP_PACA_DEPTS = {
    "04": "Alpes-de-Haute-Provence", "05": "Hautes-Alpes",
    "06": "Alpes-Maritimes (Nice)", "13": "Bouches-du-Rhône (Marseille)",
    "83": "Var (Toulon)", "84": "Vaucluse (Avignon)",
}

# Pour les offres d'État (référence sans code département), on retombe sur une
# détection par libellé dans le texte de la carte.
SP_PACA_TEXT = [
    ("marseille", "Marseille"), ("aix-en-provence", "Aix-en-Provence"),
    ("toulon", "Toulon"), ("nice", "Nice"), ("avignon", "Avignon"),
    ("bouches-du-rhône", "Bouches-du-Rhône"), ("bouches-du-rhone", "Bouches-du-Rhône"),
    ("alpes-maritimes", "Alpes-Maritimes"), ("provence-alpes", "PACA"),
    ("paca", "PACA"), ("télétravail", "Télétravail"), ("teletravail", "Télétravail"),
]


def search_apec():
    """APEC (apec.fr) : LE portail des offres cadres en France, pile le profil
    stratégie/conseil climat. API JSON interne, POST sur /cms/webservices/
    rechercheOffre avec le DTO RechercheOffreCriteriaDto (pagination
    {startIndex, range}). Les offres reviennent déjà formatées : intitule,
    nomCommercial, lieuTexte, salaireTexte, texteOffre, datePublication."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    exclusions = get_exclusions()
    jobs = []
    seen_ids = set()
    url = "https://www.apec.fr/cms/webservices/rechercheOffre"
    for kw in KEYWORDS:
        body = {
            "motsCles": kw,
            "fonctions": [], "lieux": [], "typesContrat": [], "typesConvention": [],
            "niveauxExperience": [], "secteursActivite": [], "statutPoste": [],
            "typesTeletravail": [], "idsEtablissement": [], "sorts": [],
            "activeFiltre": False,
            "pagination": {"startIndex": 0, "range": 20},
        }
        try:
            r = requests.post(url, json=body, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  APEC '{kw}' → HTTP {r.status_code}")
                continue
            results = r.json().get("resultats", [])
            print(f"  APEC '{kw}' → {len(results)} offres")
            for job in results:
                job_id = str(job.get("numeroOffre") or job.get("id") or "")
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                title = job.get("intitule", "")
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, job.get("nomCommercial", "N/A"),
                                 job.get("lieuTexte", "France"), "APEC", "mot-clé exclu")
                    continue
                description = job.get("texteOffre", "")
                jobs.append({
                    "id": job_id,
                    "title": clean_text(title),
                    "company": job.get("nomCommercial", "N/A"),
                    "location": job.get("lieuTexte", "France"),
                    "url": f"https://www.apec.fr/candidat/recherche-emploi.html/detail-offre/{job_id}",
                    "description": (description[:200] + "...") if description else "",
                    "salary": job.get("salaireTexte", ""),
                    "date": job.get("datePublication", ""),
                    "source": "APEC",
                })
        except Exception as e:
            print(f"  EXCEPTION APEC '{kw}': {e}")
    print(f"  APEC total → {len(jobs)} offres")
    return jobs


def search_remotive():
    """Remotive (remotive.com/remote-jobs/api) : API JSON publique sans clé,
    offres 100% télétravail (souvent ouvertes Europe entière). Le paramètre
    ?search= ne filtre pas réellement côté serveur (mêmes résultats peu
    importe le mot-clé) : on récupère donc le flux complet une seule fois et
    on filtre nous-mêmes sur titre + description."""
    exclusions = get_exclusions()
    jobs = []
    seen_ids = set()
    keywords_lower = [kw.lower() for kw in REMOTE_KEYWORDS]
    try:
        r = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"limit": 200},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"  Remotive → HTTP {r.status_code}")
            return jobs
        results = r.json().get("jobs", [])
        print(f"  Remotive → {len(results)} offres récupérées")
        for job in results:
            job_id = str(job.get("id", ""))
            if not job_id or job_id in seen_ids:
                continue
            title = job.get("title", "")
            description = job.get("description", "")
            haystack = (title + " " + description).lower()
            if not any(kw in haystack for kw in keywords_lower):
                continue
            seen_ids.add(job_id)
            if any(excl in title.lower() for excl in exclusions):
                log_excluded(title, job.get("company_name", "N/A"),
                             job.get("candidate_required_location", "Remote"),
                             "Remote EU", "mot-clé exclu")
                continue
            jobs.append({
                "id": job_id,
                "title": clean_text(title),
                "company": job.get("company_name", "N/A"),
                "location": job.get("candidate_required_location", "Remote"),
                "url": job.get("url", ""),
                "description": description[:150] + "..." if description else "",
                "source": "Remote EU",
            })
    except Exception as e:
        print(f"  EXCEPTION Remotive: {e}")
    print(f"  Remotive total → {len(jobs)} offres")
    return jobs


def search_climatebase():
    """Climatebase (climatebase.org/jobs) : job board spécialisé climat, donc
    pas besoin de filtrer par mot-clé climat (tout le board l'est déjà). La
    page intègre les offres en SSR dans un <script id="__NEXT_DATA__"> JSON
    (pas besoin de navigateur headless). On ne garde que les offres avec
    remote_preferences contenant 'Remote', ET on écarte les « remote » dont le
    champ `locations` est 100 % américain (remote US-only, inutile pour Arnaud) :
    on garde le remote mondial (locations vide) ou avec au moins un lieu non-US."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    }
    exclusions = get_exclusions()
    jobs = []
    try:
        r = requests.get("https://climatebase.org/jobs", headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"  Climatebase → HTTP {r.status_code}")
            return jobs
        text = r.text
        idx = text.find('id="__NEXT_DATA__"')
        start = text.find(">", idx) + 1
        end = text.find("</script>", start)
        data = json.loads(text[start:end])
        raw_jobs = data.get("props", {}).get("pageProps", {}).get("jobs", [])
        print(f"  Climatebase → {len(raw_jobs)} offres récupérées")
        for job in raw_jobs:
            if "remote" not in " ".join(job.get("remote_preferences", [])).lower():
                continue
            job_id = str(job.get("id", ""))
            if not job_id:
                continue
            title = job.get("title", "")
            employer = job.get("name_of_employer", "N/A")
            locs = job.get("locations") or []
            # Écarte le remote US-only : lieux tous américains. On garde le
            # remote mondial (locs vide) ou avec au moins un lieu non-US.
            non_us = [l for l in locs if not _is_us_location(l)]
            if locs and not non_us:
                log_excluded(title, employer, ", ".join(locs)[:40],
                             "Remote EU", "remote US-only")
                continue
            if any(excl in title.lower() for excl in exclusions):
                log_excluded(title, employer, "Remote", "Remote EU", "mot-clé exclu")
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            jobs.append({
                "id": job_id,
                "title": clean_text(title),
                "company": employer,
                "location": non_us[0] if non_us else "Remote",
                "url": f"https://climatebase.org/job/{slug}-{job_id}",
                "description": job.get("employer_short_description", "")[:150],
                "salary": format_salary_range(job.get("salary_from"), job.get("salary_to")),
                "date": job.get("activation_date", ""),
                "source": "Remote EU",
            })
    except Exception as e:
        print(f"  EXCEPTION Climatebase: {e}")
    print(f"  Climatebase total → {len(jobs)} offres")
    return jobs


def search_arbeitnow():
    """Arbeitnow (arbeitnow.com/api/job-board-api) : API JSON publique sans
    clé, fort accent Europe. Pas de paramètre de recherche : on filtre les
    offres remote=true par mot-clé sur titre+tags+description, sur un plus
    grand nombre de pages pour augmenter la couverture."""
    exclusions = get_exclusions()
    jobs = []
    seen_slugs = set()
    keywords_lower = [kw.lower() for kw in REMOTE_KEYWORDS]
    try:
        for page in range(1, 11):
            r = requests.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={"page": page},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  Arbeitnow page {page} → HTTP {r.status_code}")
                break
            results = r.json().get("data", [])
            if not results:
                break
            for job in results:
                if not job.get("remote"):
                    continue
                slug = job.get("slug", "")
                if not slug or slug in seen_slugs:
                    continue
                title = job.get("title", "")
                tags_text = " ".join(job.get("tags", [])).lower()
                description = job.get("description", "")
                haystack = (title + " " + tags_text + " " + description).lower()
                if not any(kw in haystack for kw in keywords_lower):
                    continue
                seen_slugs.add(slug)
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, job.get("company_name", "N/A"),
                                 job.get("location", "Remote"), "Remote EU", "mot-clé exclu")
                    continue
                jobs.append({
                    "id": slug,
                    "title": clean_text(title),
                    "company": job.get("company_name", "N/A"),
                    "location": job.get("location", "Remote"),
                    "url": job.get("url", ""),
                    "description": description[:150] + "..." if description else "",
                    "source": "Remote EU",
                })
    except Exception as e:
        print(f"  EXCEPTION Arbeitnow: {e}")
    print(f"  Arbeitnow total → {len(jobs)} offres")
    return jobs


def _greenhouse_boards():
    env = os.environ.get("GREENHOUSE_BOARDS", "").strip()
    if env:
        return [b.strip() for b in env.split(",") if b.strip()]
    return GREENHOUSE_BOARDS_DEFAULT


def _smartrecruiters_companies():
    env = os.environ.get("SMARTRECRUITERS_COMPANIES", "").strip()
    if env:
        return [c.strip() for c in env.split(",") if c.strip()]
    return SMARTRECRUITERS_COMPANIES_DEFAULT


def search_smartrecruiters():
    """SmartRecruiters : API publique des offres (postings) des organisations
    qui l'utilisent comme ATS — notamment l'OCDE, et potentiellement d'autres
    agences (IEA, IRENA…). Sans clé. On ne garde que France / Europe / remote
    (les postes US-remote sont écartés) ; l'IA + le plafond de séniorité
    tranchent la pertinence. Un id invalide renvoie 404 (visible dans les logs)."""
    exclusions = get_exclusions()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    jobs = []
    for company in _smartrecruiters_companies():
        try:
            r = requests.get(
                f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                params={"limit": 100}, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  SmartRecruiters {company} → HTTP {r.status_code} (id à vérifier ?)")
                continue
            results = r.json().get("content", [])
            print(f"  SmartRecruiters {company} → {len(results)} brutes")
            for job in results:
                title = clean_text(job.get("name", ""))
                if not title:
                    continue
                loc = job.get("location") or {}
                remote = bool(loc.get("remote"))
                location = ", ".join([p for p in (loc.get("city"), loc.get("region"),
                                                  loc.get("country")) if p])
                if remote:
                    location = f"{location} (Remote)".strip() if location else "Remote"
                if not keep_international_location(location):
                    continue
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, company, location, "SmartRecruiters", "mot-clé exclu")
                    continue
                pid = job.get("id", "")
                jobs.append({
                    "id": f"sr-{company}-{pid}",
                    "title": title,
                    "company": company,
                    "location": location or "Remote",
                    "url": f"https://jobs.smartrecruiters.com/{company}/{pid}",
                    "description": "",
                    "date": (job.get("releasedDate") or "")[:10],
                    "source": "SmartRecruiters",
                    "company_watch": True,
                })
        except Exception as e:
            print(f"  EXCEPTION SmartRecruiters {company}: {e}")
    print(f"  SmartRecruiters total → {len(jobs)} offres après filtre")
    return jobs


def search_reliefweb():
    """ReliefWeb (api.reliefweb.int) : job board ONU / humanitaire / développement,
    riche en postes climat-adaptation d'organisations internationales (PNUD, UNEP,
    GCF, GCA…). API JSON publique, un simple `appname` requis. On filtre par
    mots-clés climat côté requête ; le filtre IA + le plafond de séniorité
    tranchent ensuite la pertinence. Offres mondiales (Arnaud est ouvert à
    l'expatriation pour ces institutions)."""
    # L'API v2 de ReliefWeb exige un `appname` APPROUVÉ (403 sinon : leur page
    # d'aide « anyone can use it » est périmée). Il se demande par email à
    # feedback@reliefweb.int. Sans le secret RELIEFWEB_APPNAME, on saute la
    # source (le RSS/site est bloqué Cloudflare 202, pas d'alternative).
    appname = os.environ.get("RELIEFWEB_APPNAME", "")
    if not appname:
        print("  ReliefWeb: RELIEFWEB_APPNAME absent → source désactivée")
        return []
    exclusions = get_exclusions()
    jobs = []
    try:
        r = requests.post(
            "https://api.reliefweb.int/v2/jobs",
            params={"appname": appname},
            json={
                "limit": 60,
                "query": {
                    "value": "climate adaptation carbon resilience mitigation decarbonisation \"climate change\" \"climate finance\"",
                    "operator": "OR",
                    "fields": ["title", "body"],
                },
                "fields": {"include": ["title", "url", "source.name", "country.name",
                                       "city.name", "date.created"]},
                "sort": ["date.created:desc"],
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  ReliefWeb → HTTP {r.status_code}")
            return jobs
        data = r.json().get("data", [])
        print(f"  ReliefWeb → {len(data)} offres récupérées")
        for item in data:
            f = item.get("fields", {})
            title = clean_text(f.get("title", ""))
            if not title:
                continue
            source = (f.get("source") or [{}])[0].get("name", "N/A")
            if any(excl in title.lower() for excl in exclusions):
                log_excluded(title, source, "", "ReliefWeb", "mot-clé exclu")
                continue
            countries = [c.get("name", "") for c in (f.get("country") or []) if c.get("name")]
            cities = [c.get("name", "") for c in (f.get("city") or []) if c.get("name")]
            loc_bits = ([cities[0]] if cities else []) + ([countries[0]] if countries else [])
            location = ", ".join(loc_bits) or "International"
            jobs.append({
                "id": f.get("url", str(item.get("id", ""))),
                "title": title,
                "company": clean_text(source),
                "location": location,
                "url": f.get("url", ""),
                "description": "",
                "date": (f.get("date") or {}).get("created", "")[:10],
                "source": "ReliefWeb",
            })
    except Exception as e:
        print(f"  EXCEPTION ReliefWeb: {e}")
    print(f"  ReliefWeb total → {len(jobs)} offres")
    return jobs


def search_greenhouse():
    """Boards Greenhouse des entreprises climat suivies : API JSON publique
    sans clé (boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true).
    Offres récupérées À LA SOURCE, avant qu'elles n'arrivent sur les
    agrégateurs. On ne garde que les postes France / Europe / télétravail ;
    le filtre IA tranche ensuite la pertinence métier. Un token invalide
    renvoie 404 (visible dans les logs CI, à corriger comme pour WTTJ)."""
    exclusions = get_exclusions()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    jobs = []
    for token in _greenhouse_boards():
        try:
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                params={"content": "true"}, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  Greenhouse {token} → HTTP {r.status_code} (token à vérifier ?)")
                continue
            results = r.json().get("jobs", [])
            print(f"  Greenhouse {token} → {len(results)} brutes")
            for job in results:
                title = clean_text(job.get("title", ""))
                if not title:
                    continue
                location = clean_text((job.get("location") or {}).get("name", ""))
                if not keep_international_location(location):
                    continue
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, token, location, "Greenhouse", "mot-clé exclu")
                    continue
                description = clean_text(job.get("content", ""))
                jobs.append({
                    "id": f"gh-{token}-{job.get('id', '')}",
                    "title": title,
                    "company": token.capitalize(),
                    "location": location or "Remote",
                    "url": job.get("absolute_url", ""),
                    "description": description[:150] + "..." if description else "",
                    "date": job.get("updated_at", ""),
                    "source": "Greenhouse",
                    "company_watch": True,
                })
        except Exception as e:
            print(f"  EXCEPTION Greenhouse {token}: {e}")
    print(f"  Greenhouse total → {len(jobs)} offres après filtre")
    return jobs


def _lever_companies():
    env = os.environ.get("LEVER_COMPANIES", "").strip()
    if env:
        return [c.strip() for c in env.split(",") if c.strip()]
    return LEVER_COMPANIES_DEFAULT


def search_lever():
    """Boards Lever des entreprises climat suivies : API JSON publique sans
    clé (api.lever.co/v0/postings/<company>?mode=json). Même logique que
    Greenhouse : offres à la source, filtrées France / Europe / télétravail."""
    exclusions = get_exclusions()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    jobs = []
    for company in _lever_companies():
        try:
            r = requests.get(
                f"https://api.lever.co/v0/postings/{company}",
                params={"mode": "json"}, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  Lever {company} → HTTP {r.status_code} (slug à vérifier ?)")
                continue
            results = r.json()
            print(f"  Lever {company} → {len(results)} brutes")
            for job in results:
                title = clean_text(job.get("text", ""))
                if not title:
                    continue
                cats = job.get("categories") or {}
                location = clean_text(cats.get("location", ""))
                if not keep_international_location(location):
                    continue
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, company, location, "Lever", "mot-clé exclu")
                    continue
                description = clean_text(job.get("descriptionPlain", ""))
                created = job.get("createdAt")
                date = ""
                if isinstance(created, (int, float)):
                    date = datetime.utcfromtimestamp(created / 1000).strftime("%Y-%m-%d")
                jobs.append({
                    "id": f"lever-{company}-{job.get('id', '')}",
                    "title": title,
                    "company": company.capitalize(),
                    "location": location or "Remote",
                    "url": job.get("hostedUrl", ""),
                    "description": description[:150] + "..." if description else "",
                    "date": date,
                    "source": "Lever",
                    "company_watch": True,
                })
        except Exception as e:
            print(f"  EXCEPTION Lever {company}: {e}")
    print(f"  Lever total → {len(jobs)} offres après filtre")
    return jobs


# Temporisation LinkedIn : l'endpoint « jobs-guest » anonyme renvoie des
# HTTP 429 quand on l'appelle trop vite en parallèle. On espace donc le DÉBUT
# de chaque requête LinkedIn d'au moins LINKEDIN_MIN_INTERVAL secondes (les
# autres sources continuent de tourner en parallèle pendant ce temps).
_LINKEDIN_LOCK = threading.Lock()
_LINKEDIN_LAST = [0.0]
LINKEDIN_MIN_INTERVAL = 1.3


def _linkedin_throttle():
    with _LINKEDIN_LOCK:
        wait = LINKEDIN_MIN_INTERVAL - (time.time() - _LINKEDIN_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LINKEDIN_LAST[0] = time.time()


def search_linkedin(keyword, location):
    """LinkedIn via l'API « jobs-guest » (sans authentification) utilisée par
    le widget public d'offres. Fragile et soumise à l'anti-bot LinkedIn :
    se désactive proprement (retour []) sur tout statut non-200. Conservée
    car, quand elle répond, c'est la plus grosse source d'offres cadres.
    Les requêtes sont espacées (voir _linkedin_throttle) pour limiter les 429."""
    _linkedin_throttle()
    exclusions = get_exclusions()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "fr-FR",
    }
    try:
        from bs4 import BeautifulSoup
        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        params = {
            "keywords": keyword, "location": location,
            "f_TPR": "r604800",  # postées dans les 7 derniers jours
            "start": 0,
        }
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"  LinkedIn '{keyword}' / '{location}' → HTTP {r.status_code} (anti-bot ?)")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.find_all("li")
        print(f"  LinkedIn '{keyword}' / '{location}' → {len(cards)} cartes")
        jobs = []
        for card in cards:
            title_el = card.find(class_=lambda c: c and "title" in str(c).lower())
            link_el = card.find("a", href=True)
            if not title_el or not link_el:
                continue
            title = clean_text(title_el.get_text(strip=True))
            if not title or any(excl in title.lower() for excl in exclusions):
                if title:
                    log_excluded(title, "LinkedIn", location, "LinkedIn", "mot-clé exclu")
                continue
            company_el = card.find(class_=lambda c: c and "subtitle" in str(c).lower())
            loc_el = card.find(class_=lambda c: c and "location" in str(c).lower())
            href = link_el["href"].split("?")[0]
            jobs.append({
                "id": href,
                "title": title,
                "company": clean_text(company_el.get_text(strip=True)) if company_el else "N/A",
                "location": clean_text(loc_el.get_text(strip=True)) if loc_el else location,
                "url": href,
                "description": "",
                "source": "LinkedIn",
            })
        print(f"  LinkedIn '{keyword}' / '{location}' → {len(jobs)} après filtre")
        return jobs
    except Exception as e:
        print(f"  EXCEPTION LinkedIn: {e}")
        return []


def _sp_paca_location(job_url, card_text):
    """Renvoie un libellé de lieu PACA si l'offre est en PACA, sinon None.
    1) offre territoriale : département lu dans la référence d'URL ;
    2) offre d'État : libellé repéré dans le texte de la carte."""
    m = re.search(r"reference-O0(\d{2})", job_url)
    if m:
        return SP_PACA_DEPTS.get(m.group(1))  # None si territoriale hors PACA
    for term, label in SP_PACA_TEXT:
        if term in card_text:
            return label
    return None


def search_ess():
    """Scrape emploi-ess.fr (portail national de l'économie sociale et
    solidaire / UDES) : recherche par mot-clé climat via le formulaire
    GET ?c=0&l=0&m=<mot-clé> (c=secteur, l=région, m=mot-clé). Pas d'API,
    structure HTML stable (div.bloc-offre fondoffre / offre-titre / offre-
    localisation). Couverture nationale, le filtre géographique global
    d'__main__ (is_location_excluded) retire les offres hors zone cible."""
    exclusions = get_exclusions()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "fr-FR",
    }
    jobs = []
    seen_urls = set()
    for kw in KEYWORDS:
        try:
            from bs4 import BeautifulSoup
            url = "https://www.emploi-ess.fr/offres-d-emploi"
            r = requests.get(url, params={"c": 0, "l": 0, "m": kw}, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  ESS '{kw}' → HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.find_all("div", class_="bloc-offre")
            print(f"  ESS '{kw}' → {len(cards)} offres")
            for card in cards:
                title_a = card.find("div", class_="offre-titre")
                title_a = title_a.find("a") if title_a else None
                if not title_a:
                    continue
                job_url = title_a.get("href", "")
                if not job_url or job_url in seen_urls:
                    continue
                seen_urls.add(job_url)
                title = title_a.get_text(strip=True)
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, "Employeur ESS", "France", "ESS", "mot-clé exclu")
                    continue
                loc_div = card.find("div", class_="offre-localisation")
                location = loc_div.get_text(strip=True).replace("localisation :", "").strip() if loc_div else "France"
                desc_div = card.find("div", class_="offre-descriptif")
                description = desc_div.get_text(" ", strip=True) if desc_div else ""
                jobs.append({
                    "id": job_url,
                    "title": clean_text(title),
                    "company": "Employeur ESS",
                    "location": location,
                    "url": job_url,
                    "description": description[:150] + "..." if description else "",
                    "source": "ESS",
                })
        except Exception as e:
            print(f"  EXCEPTION ESS '{kw}': {e}")
    print(f"  ESS total → {len(jobs)} offres")
    return jobs


def search_service_public():
    """Scrape choisirleservicepublic.gouv.fr (ex-Place de l'emploi public /
    BIEP) : offres des fonctions publiques d'État, territoriale et hospitalière.
    Cible idéale pour les postes développement durable / transition en
    collectivité (Région Sud, Métropole Aix-Marseille), agence ou établissement
    public. Pas d'API publique simple : on scrape les pages de résultats par
    mot-clé et on ne garde que les offres localisées en PACA."""
    exclusions = get_exclusions()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "fr-FR",
    }
    jobs = []
    seen_urls = set()
    for kw in SP_KEYWORDS:
        try:
            from bs4 import BeautifulSoup
            url = f"https://choisirleservicepublic.gouv.fr/nos-offres/filtres/mot-cles/{kw}/"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"  ServicePublic '{kw}' → HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            offer_links = soup.find_all("a", href=lambda h: h and "/offre-emploi/" in h)
            print(f"  ServicePublic '{kw}' → {len(offer_links)} offres")
            for a in offer_links:
                href = a.get("href", "")
                job_url = href if href.startswith("http") else "https://choisirleservicepublic.gouv.fr" + href
                if job_url in seen_urls:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 5:
                    continue
                cont = a.find_parent(["article", "li"]) or a.parent
                card_text = cont.get_text(" ", strip=True).lower() if cont else ""
                location = _sp_paca_location(job_url, card_text)
                if not location:
                    continue
                seen_urls.add(job_url)
                if any(excl in title.lower() for excl in exclusions):
                    log_excluded(title, "Fonction publique", location, "Service Public", "mot-clé exclu")
                    continue
                jobs.append({
                    "id": job_url,
                    "title": clean_text(title),
                    "company": "Fonction publique",
                    "location": location,
                    "url": job_url,
                    "description": "",
                    "source": "Service Public",
                })
        except Exception as e:
            print(f"  EXCEPTION ServicePublic '{kw}': {e}")
    print(f"  ServicePublic total → {len(jobs)} offres PACA après filtre")
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


def _filter_jobs_batch(jobs, reasons_text, verdicts=None):
    """Filtre un lot d'offres via Mistral. Lève en cas d'échec (API ou JSON
    invalide) : à l'appelant de décider du repli, plutôt que de renvoyer le
    lot non filtré (ce qui spammerait l'utilisateur avec tout le bruit que
    le filtre est censé éliminer). Si `verdicts` est fourni, on y enregistre
    la décision (garder/rejeter + raison) de chaque offre pour le cache."""
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

GARDE UNIQUEMENT si le poste porte VRAIMENT sur le climat / la durabilité au niveau
stratégie ou conseil, ET correspond à la séniorité du candidat (confirmé,
pas junior). Exemples à garder : consultant·e climat/carbone, chargé·e de mission
climat ou transition, expert·e politiques publiques climat, manager décarbonation,
chef·fe de projet bilan carbone / stratégie bas-carbone.
NB : la RSE générique est déjà écartée en amont — ne « repêche » pas un poste RSE.

ADAPTATION SELON LE LIEU (applique-la avant de trancher) :
- LIEU contenant Marseille, Aix, Toulon, Nice, PACA, Provence, Bouches-du-Rhône, ou en
  télétravail : ÉLARGIS les critères « garde ». En plus du conseil/stratégie, garde aussi les
  postes QUALIFIÉS (confirmé/senior, pas junior) de : développement durable EN ENTREPRISE ;
  chargé·e de mission développement durable / transition
  écologique / climat / énergie en COLLECTIVITÉ ou ÉTABLISSEMENT PUBLIC (Région, Métropole,
  ADEME, EPCI…) ; coordination ou chef·fe de projet environnement / économie circulaire /
  transition en ASSOCIATION ou ESS. Les rejets absolus ci-dessous s'appliquent quand même.
- LIEU à Paris / Île-de-France (hors télétravail) : reste STRICT — uniquement conseil /
  stratégie climat senior, OU les rôles climate-tech décrits juste en dessous.

CLIMATE-TECH / COMPTABILITÉ CARBONE (s'applique surtout aux ENTREPRISE CIBLÉE: oui,
éditeurs de logiciels carbone / plateformes data-climat & ESG) : chez ces boîtes, GARDE
(keep=true) UNIQUEMENT les rôles HYBRIDES conseil/produit/méthodo qui valorisent son profil
ingénieur + conseil carbone. Liste FERMÉE des intitulés à garder :
- Climate Solutions Consultant, Solutions Consultant, Climate Expert, Sustainability Consultant
- Carbon Analyst, Climate Analyst, Climate Risk Analyst, ESG/Carbon Data Analyst
- Implementation Consultant / Manager, Onboarding Consultant/Manager (déploiement de l'outil)
- Customer Success Manager (accompagnement client sur un produit climat/carbone)
- Solutions Engineer / Sales Engineer / Pre-Sales / Avant-vente (démos, réponses AO, TECHNIQUE)
- Carbon Accounting Methodologist / Methodology Expert / Emission Factors
- Product Manager climat/carbone
Ces postes-là NE sont PAS à rejeter comme « tech » ou « commercial ».

REJETTE (keep=false), Y COMPRIS chez une ENTREPRISE CIBLÉE :
- DEV LOGICIEL PUR : software / fullstack / backend / frontend / mobile engineer, data
  scientist / ML / data engineer, devops, SRE, QA, architecte technique. (« engineer » seul
  ≠ Solutions Engineer : si le poste consiste à écrire du code, on REJETTE.)
- COMMERCIAL PUR : SDR, BDR, Sales/Account Representative, Account Executive,
  Business Developer, Account Manager, marketing, growth. (Le Customer Success sur un
  produit climat/carbone est GARDÉ, cf. liste ci-dessus.)
- RH / paie / recrutement / office manager / assistant·e
- finance / comptabilité / contrôle de gestion / achats / appels d'offres. INCLUT les
  intitulés « data finance », « chef·fe de projet finance / data finance », « pilotage
  financier », « ingénierie financière » : ce sont des FONCTIONS SUPPORT financières, PAS
  du climat, MÊME quand le titre contient « data » ou « chef·fe de projet » et MÊME dans
  une agence publique cible (ADEME, Région…). La marque de l'employeur ne repêche jamais
  une fonction support. (La finance CLIMAT explicite — finance carbone, green bonds,
  finance climat — reste évaluable, mais « data finance » générique = REJET.)
- pédagogie / formation hors climat, support, ops génériques
- postes terrain, techniciens (maintenance/chantier), juniors, stages, alternances
- tout poste sans lien explicite et central avec le climat/la durabilité
- ressemble aux offres rejetées ci-dessus

IMPORTANT : « ENTREPRISE CIBLÉE: oui » = entreprise pile dans sa cible, MAIS le poste doit
être dans la liste FERMÉE climate-tech ci-dessus (ou conseil/stratégie climat). Un « Fullstack
Engineer », un « SDR/BDR » ou un « Sales Representative » chez une entreprise ciblée = REJETÉ.
En cas de doute sur un rôle climate-tech, garde-le mais borderline=true.

MÊME LOGIQUE POUR LES AGENCES PUBLIQUES CIBLES (ADEME, Région, Métropole, EPCI…) : l'employeur
prestigieux ne rescape PAS une fonction support (finance, data finance, contrôle de gestion,
achats, RH, DSI/informatique interne, juridique). Le poste n'est gardé QUE s'il porte
EXPLICITEMENT sur le climat / la transition / la stratégie bas-carbone. Sans contenu climat
explicite dans l'intitulé, un poste d'agence cible = REJET (keep=false, score=0), quel que soit
le niveau « chef·fe de projet » affiché.

CAS LIMITES (« borderline ») : pour les postes RSE / développement durable
génériques que tu hésiterais à rejeter (pertinents sur le fond mais sans
preuve nette d'un niveau stratégie/conseil senior), ne les rejette PAS
sèchement : garde-les (keep=true) MAIS marque "borderline": true. Réserve
borderline=false aux offres qui correspondent clairement et pleinement au
profil. Les rejets absolus listés plus haut restent rejetés (keep=false).

SCORE : pour chaque offre GARDÉE, donne aussi un "score" entier de 0 à 100
mesurant l'adéquation au profil (100 = match parfait conseil/stratégie climat
senior dans la zone cible ; 60-80 = bon match ; 40-60 = correct mais
générique / borderline). Pour une offre rejetée, score = 0.

Réponds UNIQUEMENT avec un JSON (sans texte avant/après, sans backticks).
Chaque objet : index, keep (bool), borderline (bool), score (int 0-100), reason.
[{{"index": 0, "keep": true, "borderline": false, "score": 90, "reason": "consultant climat senior, correspond au profil"}}, ...]"""

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
    # Mistral ajoute parfois du texte avant/après le tableau JSON (provoque
    # « Extra data »/« Unterminated string ») : on isole le tableau lui-même.
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    decisions = json.loads(text)

    kept = []
    for decision in decisions:
        idx = decision.get("index")
        if idx is None or idx >= len(jobs):
            continue
        job = jobs[idx]
        keep = bool(decision.get("keep"))
        borderline = bool(decision.get("borderline"))
        reason = decision.get('reason', '')
        try:
            score = int(decision.get("score", DEFAULT_KEPT_SCORE))
        except (TypeError, ValueError):
            score = DEFAULT_KEPT_SCORE
        score = max(0, min(100, score))
        if verdicts is not None:
            verdicts[ai_key(job)] = {"keep": keep, "reason": reason,
                                     "borderline": borderline, "score": score,
                                     "v": AI_PROMPT_VERSION}
        if keep:
            job["borderline"] = borderline
            job["score"] = score
            kept.append(job)
        else:
            print(f"  IA exclu: {job['title']} → {reason}")
            log_excluded(job['title'], job['company'], job.get('location', ''),
                         job.get('source', ''), f"IA: {reason}")
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

    # Cache des verdicts : on ne réinterroge Mistral que sur les offres jamais
    # jugées. Les déjà-tranchées sont rejouées depuis le cache (gratuit, et ça
    # évite de saturer les rate limits / re-tronquer les réponses JSON).
    verdicts = load_json(AI_VERDICTS_FILE, {})
    kept = []
    to_evaluate = []
    for job in jobs:
        cached = verdicts.get(ai_key(job))
        if cached is None or cached.get("v") != AI_PROMPT_VERSION:
            # Jamais jugée, ou jugée sous d'anciennes règles → (ré)évaluer.
            to_evaluate.append(job)
        elif cached.get("keep"):
            job["borderline"] = cached.get("borderline", False)
            job["score"] = cached.get("score", DEFAULT_KEPT_SCORE)
            kept.append(job)
        else:
            log_excluded(job['title'], job['company'], job.get('location', ''),
                         job.get('source', ''), f"IA (cache): {cached.get('reason', '')}")
    print(f"  Cache IA : {len(jobs) - len(to_evaluate)} offre(s) déjà jugée(s), "
          f"{len(to_evaluate)} à évaluer")

    # On découpe en lots : avec beaucoup de sources actives, le nombre
    # d'offres peut dépasser ce qu'un seul appel Mistral peut traiter sans
    # tronquer sa réponse JSON (cause vue en prod : "Unterminated string").
    for start in range(0, len(to_evaluate), MISTRAL_BATCH_SIZE):
        batch = to_evaluate[start:start + MISTRAL_BATCH_SIZE]
        try:
            kept += _filter_jobs_batch(batch, reasons_text, verdicts)
        except Exception as e:
            print(f"  EXCEPTION Mistral (lot {start}-{start+len(batch)}): {e}")
            print("  → lot écarté par sécurité (pas de filtre fiable = pas d'envoi non filtré)")

    # On borne le cache (dict ordonné par insertion : on garde les plus récents).
    if len(verdicts) > AI_VERDICTS_MAX:
        verdicts = dict(list(verdicts.items())[-AI_VERDICTS_MAX:])
    save_json(AI_VERDICTS_FILE, verdicts)

    print(f"  Mistral: {len(kept)}/{len(jobs)} offres conservées "
          f"({len(to_evaluate)} réellement évaluées par l'IA)")
    return kept


def ai_key(job):
    """Clé de cache d'une offre pour le filtre IA et le suivi des déjà-vues :
    titre + entreprise normalisés (même convention que mark_seen)."""
    return f"{job['title'].lower().strip()}|{job['company'].lower().strip()}"


def deduplicate(jobs):
    """Dédup inter-sources tolérante : on compare des intitulés normalisés
    (sans accents, sans H/F/CDI…, mots triés) et un nom d'entreprise réduit à
    ses lettres, pour attraper la même offre repostée avec un libellé un peu
    différent sur Adzuna / France Travail / APEC. « Premier vu gagne » (l'ordre
    de all_jobs est déterministe)."""
    seen = set()
    unique = []
    for job in jobs:
        company_key = re.sub(r"[^a-z0-9]+", "", job["company"].lower())
        key = (normalize_title(job["title"]), company_key)
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
    # Tri par score IA décroissant (les meilleures correspondances en haut),
    # puis nouveautés avant déjà-vues à score égal.
    jobs = sorted(jobs, key=lambda j: (j.get("score", DEFAULT_KEPT_SCORE), j.get("is_new", False)), reverse=True)
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
        "Service Public": "#000091",
        "ESS": "#5a8f3c",
        "Remote EU": "#1f7a99",
        "APEC": "#e2001a",
        "Greenhouse": "#1f8a5c",
        "Lever": "#5a4fcf",
        "LinkedIn": "#0a66c2",
        "ReliefWeb": "#c8102e",
        "SmartRecruiters": "#00b6b0",
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
        badge_borderline = ('<span style="font-size:11px;color:#fff;background:#e0a800;padding:1px 8px;border-radius:10px;margin-left:6px">⚠️ À VÉRIFIER</span>'
                            if job.get("borderline") else '')
        meta_bits = []
        if job.get("score") is not None:
            meta_bits.append(f"🎯 {job['score']}/100")
        if job.get("salary"):
            meta_bits.append(f"💰 {job['salary']}")
        date_str = format_job_date(job.get("date", ""))
        if date_str:
            meta_bits.append(f"🗓️ {date_str}")
        meta_line = (
            f'<p style="margin:0 0 5px 0;font-size:13px;color:#777">{" &nbsp;|&nbsp; ".join(meta_bits)}</p>'
            if meta_bits else ""
        )
        html += f"""
        <div style="margin-bottom:16px;padding:14px;border-left:4px solid {color};background:{'#fff8f5' if is_new else '#f9f9f9'};border-radius:4px">
            <h3 style="margin:0 0 6px 0">
                <a href="{job['url']}" style="color:{color};text-decoration:none">{job['title']}</a>
                {badge_new}{badge_source}{badge_borderline}
            </h3>
            <p style="margin:0 0 5px 0;color:#555;font-size:14px">
                🏢 <strong>{job['company']}</strong> &nbsp;|&nbsp; 📍 {job['location']}
            </p>
            {meta_line}
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


def disappeared_section_html(disappeared):
    """Offres présentes hier dans l'alerte mais absentes aujourd'hui (souvent
    pourvues : signal d'un marché tendu sur le profil)."""
    if not disappeared:
        return ""
    items = ""
    for d in disappeared[:15]:
        items += (f'<li style="margin-bottom:4px;font-size:13px;color:#777">'
                  f'{d.get("title", "")} — <span style="color:#999">{d.get("company", "")}</span>'
                  f'</li>')
    return f"""
    <details style="margin-top:1.5rem;padding:12px;background:#fbfbfb;border-radius:8px;border:0.5px solid #eee">
        <summary style="cursor:pointer;font-size:13px;color:#888">
            👋 {len(disappeared)} offre(s) d'hier ne sont plus en ligne aujourd'hui
        </summary>
        <ul style="margin:10px 0 0;padding-left:20px">{items}</ul>
    </details>
    """


def health_footer_html(health_alerts):
    """Avertit qu'une source ne remonte plus rien depuis plusieurs jours
    (parseur probablement cassé, comme Hellowork/WTTJ avant désactivation)."""
    if not health_alerts:
        return ""
    rows = "".join(f"<li style='font-size:13px;color:#a33'>⚠️ <strong>{s}</strong> : "
                   f"{n} jours sans aucune offre — parseur à vérifier</li>"
                   for s, n in health_alerts)
    return f"""
    <div style="margin-top:1.5rem;padding:12px;background:#fff6f6;border-radius:8px;border:0.5px solid #f0d0d0">
        <ul style="margin:0;padding-left:20px">{rows}</ul>
    </div>
    """


def build_email(jobs, feedback_url, excluded_log=None, disappeared=None, health_alerts=None):
    today = datetime.now().strftime("%d/%m/%Y")
    watchlist = [j for j in jobs if j.get("company_watch")]
    geo_jobs = [j for j in jobs if not j.get("company_watch")]
    marseille, paca, paris = categorize(geo_jobs)
    total = len(jobs)
    new_total = sum(1 for j in jobs if j.get("is_new"))
    top_score = max((j.get("score", DEFAULT_KEPT_SCORE) for j in jobs), default=0)

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
    <p style="color:#555">{total} offre(s) dont <strong style="color:#e05c2a">{new_total} nouvelle(s)</strong> · meilleur score <strong style="color:#2d6a4f">{top_score}/100</strong></p>
    <p style="color:#888;font-size:13px;margin-top:-4px">Entreprises ciblées ({len(watchlist)}) · Marseille ({len(marseille)}) · PACA ({len(paca)}) · Hors PACA &amp; télétravail ({len(paris)})</p>
    <div style="margin:10px 0;padding:10px 14px;background:#fff8e6;border:1px solid #f0d98a;border-radius:6px;font-size:13px;color:#7a5b00">
        ℹ️ <strong>Filtre Paris.</strong> À Paris, on ne retient que les postes conseil / stratégie climat senior bien notés (score ≥ {PARIS_MIN_SCORE}/100) — plus toutes les offres de tes entreprises suivies, quel que soit le poste. Marseille, la PACA et le télétravail ne sont pas filtrés.
    </div>
    <a href="{feedback_url}" style="display:inline-block;margin:8px 0 16px;padding:10px 20px;background:#2d6a4f;color:#fff;border-radius:6px;text-decoration:none;font-size:14px">
        👎 Signaler des offres non pertinentes
    </a>
    <hr style="border:1px solid #e0e0e0">
    """

    body += section_html("Marseille", "🔵", marseille, "#0f6e56")
    if marseille and paca:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    body += section_html("Région PACA hors Marseille", "🟢", paca, "#3b6d11")
    if (marseille or paca) and watchlist:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    body += section_html("Entreprises ciblées", "🏢", watchlist, "#0a5c54")
    if (marseille or paca or watchlist) and paris:
        body += '<hr style="border:0.5px solid #e0e0e0;margin:1rem 0">'
    # Section « Hors PACA » découpée en sous-catégories : télétravail, salaire
    # élevé, puis le reste (Paris & autres villes). Buckets exclusifs, dans cet
    # ordre de priorité (une offre remote bien payée va dans Télétravail).
    remote_jobs = [j for j in paris if is_remote_location(j.get("location", ""))]
    used = {id(j) for j in remote_jobs}
    high_sal = [j for j in paris if id(j) not in used
                and (salary_max_k(j.get("salary", "")) or 0) >= HIGH_SALARY_K]
    used |= {id(j) for j in high_sal}
    autres = [j for j in paris if id(j) not in used]
    body += section_html("Télétravail / Remote", "🏠", remote_jobs, "#1f7a99")
    body += section_html(f"Salaire élevé (≥ {HIGH_SALARY_K} k€)", "💰", high_sal, "#7a5b00")
    body += section_html("Autres — Paris &amp; France", "🔴", autres, "#993c1d")
    body += disappeared_section_html(disappeared or [])
    body += excluded_section_html(excluded_log or [])
    body += health_footer_html(health_alerts or [])
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


def update_source_health(raw_counts):
    """Met à jour le compteur de jours consécutifs sans offre par source et
    renvoie la liste des sources en alerte (≥ SOURCE_HEALTH_ALERT jours à
    zéro). `raw_counts` = nb d'offres BRUTES par source sur ce run."""
    health = load_json(SOURCE_HEALTH_FILE, {})
    alerts = []
    for source, count in raw_counts.items():
        streak = 0 if count > 0 else health.get(source, 0) + 1
        health[source] = streak
        if streak >= SOURCE_HEALTH_ALERT:
            alerts.append((source, streak))
    save_json(SOURCE_HEALTH_FILE, health)
    if alerts:
        print(f"  Santé sources : {len(alerts)} source(s) en alerte → {alerts}")
    return alerts


def send_priority_alert(jobs):
    """Pousse une notif temps réel (Telegram puis Slack en repli) pour les
    nouvelles offres à très haut score. No-op silencieux si aucun secret de
    notif n'est configuré."""
    priority = [j for j in jobs
                if j.get("is_new") and j.get("score", 0) >= PRIORITY_SCORE]
    if not priority:
        return
    priority.sort(key=lambda j: j.get("score", 0), reverse=True)
    lines = [f"🔥 {len(priority)} offre(s) climat à fort potentiel :"]
    for j in priority[:10]:
        lines.append(f"• [{j.get('score')}/100] {j['title']} — {j['company']} ({j['location']})\n{j['url']}")
    text = "\n\n".join(lines)

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    try:
        if tg_token and tg_chat:
            r = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat, "text": text, "disable_web_page_preview": True},
                timeout=15)
            print(f"  Notif Telegram → HTTP {r.status_code} ({len(priority)} offre(s))")
        elif slack_url:
            r = requests.post(slack_url, json={"text": text}, timeout=15)
            print(f"  Notif Slack → HTTP {r.status_code} ({len(priority)} offre(s))")
    except Exception as e:
        print(f"  EXCEPTION notif prioritaire: {e}")


if __name__ == "__main__":
    seen_ids = set(load_json(SEEN_FILE, []))
    print(f"{len(seen_ids)} offres déjà vues en mémoire")
    # Offres conservées hier (avant écrasement de TODAY_FILE) : sert à repérer
    # celles qui ont disparu aujourd'hui.
    previous_jobs = load_json(TODAY_FILE, [])

    # Toutes les recherches sont indépendantes (chacune renvoie une liste, le
    # seul état partagé est EXCLUDED_LOG via log_excluded() dont .append est
    # thread-safe sous CPython). On les lance donc en parallèle pour ne plus
    # payer ~140 requêtes HTTP en série.
    #
    # Hellowork désactivé : parseur renvoie « 0 cartes » à chaque appel (HTML
    # changé). Greenjob.fr abandonné (recherche mot-clé non fonctionnelle).
    # WTTJ désactivé : HTTP 202 anti-bot Cloudflare → 0 offre.
    # Jooble désactivé : pour nos villes (Paris/Toulon/Nice…) l'API renvoie les
    # villes US homonymes (Paris TX, Toulon IL, Nice CA…) → 0 offre en zone,
    # 45 requêtes/run gaspillées. Les fonctions sont conservées mais non
    # appelées.
    tasks = []
    for keyword in KEYWORDS:
        for location in LOCATIONS:
            tasks.append((search_adzuna, (keyword, location)))
            tasks.append((search_france_travail, (keyword, location)))
            tasks.append((search_linkedin, (keyword, location)))
    for fn in (search_ademe, search_adzuna_companies, search_jtms,
               search_service_public, search_ess, search_remotive,
               search_arbeitnow, search_climatebase, search_apec,
               search_greenhouse, search_lever, search_reliefweb,
               search_smartrecruiters):
        tasks.append((fn, ()))

    all_jobs = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(fn, *args) for fn, args in tasks]
        # On collecte dans l'ordre de soumission : l'ordre de all_jobs reste
        # déterministe (important pour le « premier gagne » de deduplicate()).
        for (fn, _), future in zip(tasks, futures):
            try:
                all_jobs += future.result()
            except Exception as e:
                print(f"  EXCEPTION {getattr(fn, '__name__', fn)}: {e}")

    # Comptage brut par source (avant tout filtre) pour le suivi de santé :
    # une source qui tombe à 0 plusieurs jours d'affilée a un parseur cassé.
    raw_counts = {}
    for j in all_jobs:
        raw_counts[j.get("source", "?")] = raw_counts.get(j.get("source", "?"), 0) + 1
    health_alerts = update_source_health(raw_counts)

    before_loc_filter = len(all_jobs)
    filtered_jobs = []
    for job in all_jobs:
        if is_location_excluded(job.get("location", "")):
            log_excluded(job["title"], job["company"], job.get("location", ""),
                         job.get("source", ""), "localisation exclue")
            continue
        if is_company_excluded(job.get("company", "")):
            log_excluded(job["title"], job["company"], job.get("location", ""),
                         job.get("source", ""), "employeur exclu")
            continue
        # Recentrage : on écarte la RSE générique (mot entier), partout, y
        # compris chez une entreprise suivie.
        if is_rse_title(job.get("title", "")):
            log_excluded(job["title"], job["company"], job.get("location", ""),
                         job.get("source", ""), "RSE exclu (recentrage risque/adaptation)")
            continue
        # Plafond de séniorité : postes trop hauts (Head/Director/Chief/Lead…)
        # hors de portée → écartés au titre.
        if is_over_senior_title(job.get("title", "")):
            log_excluded(job["title"], job["company"], job.get("location", ""),
                         job.get("source", ""), "trop senior (Head/Director/Chief/Lead)")
            continue
        filtered_jobs.append(job)
    all_jobs = filtered_jobs
    print(f"\nLocalisations exclues : {before_loc_filter - len(all_jobs)} offre(s)")

    # Dédup AVANT l'IA : une même offre remontée par plusieurs sources/combos
    # mot-clé×ville n'est ainsi évaluée qu'une fois par Mistral.
    all_jobs = deduplicate(all_jobs)
    print(f"\n{len(all_jobs)} offres uniques avant filtrage IA")
    all_jobs = filter_jobs_with_ai(all_jobs)

    # Filtre Paris (combiné) : une offre parisienne hors entreprise suivie n'est
    # gardée que si l'IA la juge conseil/stratégie senior à bon score
    # (>= PARIS_MIN_SCORE) — pour attraper les postes type consultant senior sans
    # laisser passer le bruit RSE générique. Entreprises suivies : tous postes.
    # Marseille / PACA / télétravail : aucun filtre supplémentaire.
    kept_after_paris = []
    for job in all_jobs:
        if (is_paris_location(job.get("location", "")) and not job.get("company_watch")
                and job.get("score", 0) < PARIS_MIN_SCORE):
            log_excluded(job["title"], job["company"], job.get("location", ""),
                         job.get("source", ""),
                         f"Paris hors entreprise suivie : score {job.get('score', 0)} < {PARIS_MIN_SCORE}")
            continue
        kept_after_paris.append(job)
    all_jobs = kept_after_paris

    jobs = mark_seen(all_jobs, seen_ids)

    # Offres d'hier disparues aujourd'hui (clé titre|entreprise normalisée).
    today_keys = {ai_key(j) for j in jobs}
    disappeared = [p for p in previous_jobs if ai_key(p) not in today_keys]
    print(f"\n{len(disappeared)} offre(s) d'hier disparue(s) aujourd'hui")

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

    html = build_email(jobs, feedback_url, EXCLUDED_LOG, disappeared, health_alerts)
    send_email(html, len(jobs))
    send_priority_alert(jobs)

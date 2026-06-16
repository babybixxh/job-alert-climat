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
    allowed_terms = ["paris", "marseille", "aix", "aix-en-provence", "toulon", "nice", "remote", "télétravail", "teletravail", "france", "paca", "provence"]
    return any(term in text for term in allowed_terms)

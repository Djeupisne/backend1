from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, json, re, threading, mimetypes, io, csv, unicodedata, zipfile, time, gc, random, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from werkzeug.utils import secure_filename
from supabase import create_client, Client
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

try:
    import PyPDF2
    PYPDF2_AVAILABLE = True
except ImportError:
    PYPDF2_AVAILABLE = False

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import chardet
    CHARDET_AVAILABLE = True
except ImportError:
    CHARDET_AVAILABLE = False

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import openpyxl
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

try:
    from docx import Document as DocxDocument
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    from pdf2image import convert_from_path
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    logger.info("✅ OpenAI importe avec succes")
except ImportError as e:
    OPENAI_AVAILABLE = False
    logger.error(f"❌ Erreur import OpenAI: {e}")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3:free")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_REASONING_ENABLED = os.getenv("OPENROUTER_REASONING_ENABLED", "false").lower() == "true"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

logger.info(f"🔑 OPENROUTER_API_KEY: {'✅ Presente' if OPENROUTER_API_KEY else '❌ Manquante'}")
logger.info(f"📦 OPENROUTER_MODEL: {OPENROUTER_MODEL}")
logger.info(f"🧠 OPENROUTER_REASONING: {'✅ Active' if OPENROUTER_REASONING_ENABLED else '❌ Desactive'}")
logger.info(f"🔑 DEEPSEEK_API_KEY: {'✅ Presente' if DEEPSEEK_API_KEY else '❌ Manquante'}")

_client = None
_PROVIDER = "None"
_MODEL = None
IA_ANALYSE_ACTIVE = False

if OPENAI_AVAILABLE and OPENROUTER_API_KEY:
    try:
        _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
        _MODEL = OPENROUTER_MODEL
        _PROVIDER = "OpenRouter (MiniMax M3)"
        IA_ANALYSE_ACTIVE = True
        logger.info("✅ Client OpenRouter initialise avec succes (GRATUIT)")
        logger.info(f"   Modele: {_MODEL}")
        logger.info(f"   Base URL: {OPENROUTER_BASE_URL}")
        logger.info(f"   Reasoning: {'Active' if OPENROUTER_REASONING_ENABLED else 'Desactive'}")
    except Exception as e:
        logger.error(f"❌ Erreur initialisation OpenRouter: {e}")
        _client = None
        IA_ANALYSE_ACTIVE = False

if not IA_ANALYSE_ACTIVE and OPENAI_AVAILABLE and DEEPSEEK_API_KEY:
    try:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        _MODEL = DEEPSEEK_MODEL
        _PROVIDER = "DeepSeek (payant)"
        IA_ANALYSE_ACTIVE = True
        logger.info("✅ Client DeepSeek initialise avec succes (fallback payant)")
    except Exception as e:
        logger.error(f"❌ Erreur initialisation DeepSeek: {e}")
        _client = None
        IA_ANALYSE_ACTIVE = False

if not IA_ANALYSE_ACTIVE:
    logger.warning("⚠️ AUCUNE IA DISPONIBLE - Mode fallback uniquement")
    logger.warning("   Verifiez OPENROUTER_API_KEY et l'installation de openai")

_ia_semaphore = threading.Semaphore(int(os.getenv("IA_MAX_CONCURRENCY", "5")))
_Nlp_fr = None
_Nlp_en = None

DOWNLOAD_MAX_RETRIES = int(os.getenv("DOWNLOAD_MAX_RETRIES", "3"))
DOWNLOAD_BASE_DELAY = float(os.getenv("DOWNLOAD_BASE_DELAY", "0.5"))
DOWNLOAD_MAX_DELAY = int(os.getenv("DOWNLOAD_MAX_DELAY", "10"))
DOWNLOAD_MAX_CONCURRENT = int(os.getenv("DOWNLOAD_MAX_CONCURRENT", "15"))
_DOWNLOAD_SEMAPHORE = threading.Semaphore(DOWNLOAD_MAX_CONCURRENT)

_ZIP_JOBS = {}
_ZIP_JOBS_LOCK = threading.Lock()
_ZIP_JOBS_MAX_AGE_SECONDS = 3600
_ZIP_MAX_WORKERS = int(os.getenv("ZIP_MAX_WORKERS", "25"))

def retry_with_backoff(max_retries=DOWNLOAD_MAX_RETRIES, base_delay=DOWNLOAD_BASE_DELAY, max_delay=DOWNLOAD_MAX_DELAY):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"Tentative {attempt + 1}/{max_retries} reussie pour {func.__name__}")
                    return result
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    retryable_keywords = ["errno 11", "resource temporarily unavailable", "timeout", "connection", "temporarily unavailable", "rate limit", "too many requests", "503", "502", "504", "connection refused", "connection reset"]
                    if not any(kw in error_str for kw in retryable_keywords):
                        logger.error(f"Erreur non reessayable dans {func.__name__}: {e}")
                        raise
                    if attempt == max_retries - 1:
                        logger.error(f"Echec apres {max_retries} tentatives pour {func.__name__}: {e}")
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.3)
                    total_delay = delay + jitter
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} echouee pour {func.__name__}: {e}. Nouvel essai dans {total_delay:.2f}s")
                    time.sleep(total_delay)
            raise last_exception
        return wrapper
    return decorator

def _get_spacy_model(lang='fr'):
    global _Nlp_fr, _Nlp_en
    if not SPACY_AVAILABLE:
        return None
    if lang == 'fr':
        if _Nlp_fr is None:
            try:
                _Nlp_fr = spacy.load("fr_core_news_sm")
            except OSError:
                try:
                    _Nlp_fr = spacy.load("fr_core_news_md")
                except OSError:
                    return None
        return _Nlp_fr
    else:
        if _Nlp_en is None:
            try:
                _Nlp_en = spacy.load("en_core_web_sm")
            except OSError:
                return None
        return _Nlp_en

def test_ia_connection():
    if not IA_ANALYSE_ACTIVE or not _client:
        logger.warning("⚠️ IA non active, impossible de tester")
        return False
    try:
        response = _client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": "Test de connexion"}],
            max_tokens=5,
            temperature=0,
            extra_headers={
                "HTTP-Referer": "https://recrutment.onrender.com",
                "X-Title": "RecrutBank CV Analyzer"
            } if "OpenRouter" in _PROVIDER else {}
        )
        logger.info(f"✅ Connexion {_PROVIDER} OK")
        return True
    except Exception as e:
        logger.error(f"❌ Erreur connexion {_PROVIDER}: {e}")
        return False

def extract_json_from_text(text):
    """Extrait le JSON d'une reponse textuelle qui peut contenir du texte explicatif"""
    import re as re_json
    if not text:
        return None
    text = text.strip()
    json_patterns = [
        r'(?:```json\s*)?(\{[\s\S]*?\})(?:\s*```)?',
        r'(\{[\s\S]*\})',
        r'(\[[\s\S]*\])'
    ]
    for pattern in json_patterns:
        matches = re_json.findall(pattern, text)
        if matches:
            for match in matches:
                try:
                    parsed = json.loads(match.strip())
                    if isinstance(parsed, (dict, list)):
                        return parsed
                except json.JSONDecodeError:
                    continue
    return None

def parse_json_robust(result_text):
    """Parse le JSON de maniere robuste avec nettoyage et fallback"""
    import re as re_json
    if not result_text:
        return None
    try:
        analyse = json.loads(result_text.strip())
        logger.info("✅ JSON parse avec succes")
        return analyse
    except json.JSONDecodeError as e:
        logger.warning(f"⚠️ Erreur parsing JSON direct: {e}")
        cleaned_text = result_text
        cleaned_text = re_json.sub(r'//.*?$', '', cleaned_text, flags=re_json.MULTILINE)
        cleaned_text = re_json.sub(r',(\s*[}\]])', r'\1', cleaned_text)
        cleaned_text = re_json.sub(r'[\x00-\x1F\x7F]', '', cleaned_text)
        cleaned_text = re_json.sub(r'```json\s*', '', cleaned_text)
        cleaned_text = re_json.sub(r'\s*```', '', cleaned_text)
        cleaned_text = re_json.sub(r'^[^{]*', '', cleaned_text)
        cleaned_text = re_json.sub(r'[^}]*$', '', cleaned_text)
        try:
            analyse = json.loads(cleaned_text)
            logger.info("✅ JSON parse apres nettoyage")
            return analyse
        except json.JSONDecodeError as e2:
            logger.warning(f"⚠️ Erreur parsing JSON apres nettoyage: {e2}")
            json_obj = extract_json_from_text(cleaned_text)
            if json_obj:
                logger.info("✅ JSON extrait avec regex")
                return json_obj
            else:
                logger.error("❌ Aucun JSON trouve dans la reponse")
                return None

def apply_business_rules(cv_text, lettre_text, attestation_texts_list, result):
    """Applique les regles metier pour corriger les sous-scores et le score total selon la grille de presélection"""
    import re as re_json
    if not result:
        return result

    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + "\n".join(attestation_texts_list) if attestation_texts_list else ""

    # Détection des profils selon la grille
    is_chef_agence = bool(re_json.search(r'chef d\'agence|chef d agence|directeur d\'agence|directeur d agence|responsable d\'agence|responsable d agence|manager d\'agence|manager d agence|agence manager|branch manager|agency manager|chef de centre|directeur de centre|responsable de centre|acting branch manager|profit center manager|profit center|branch manager|agency head', raw_full, re_json.IGNORECASE))

    is_gestionnaire_portefeuille = bool(re_json.search(r'gestionnaire de portefeuille|portfolio manager|charge de portefeuille|portfolio officer|credit portfolio|gestionnaire de compte|account manager|relationship manager|chargé de clientèle|charge de clientele|analyste credit|analyste crédit|montage credit|montage crédit|instruction credit|instruction crédit|gestionnaire de clientèle|gestionnaire de clientele|commercial|chargé d\'affaires|charge d affaires|responsable de portefeuille', raw_full, re_json.IGNORECASE))

    has_portfolio_management = bool(re_json.search(r'gestion de portefeuille|portefeuille.*?client|portefeuille.*?credit|portefeuille.*?entreprise|suivi.*?portefeuille|portefeuille.*?sme|portefeuille.*?pme|portefeuille.*?local corporate|portefeuille.*?grandes entreprises|portefeuille.*?npl|portefeuille.*?provision|recouvrement|relance client|gestion des impayés|gestion des impayes', raw_full, re_json.IGNORECASE))

    # Détection des compétences Local Corporate/SME
    has_local_corporate = bool(re_json.search(r'local corporate|sme|pme|petites et moyennes entreprises|pm|moyennes entreprises|grandes entreprises|entreprises|corporate local|local corporate banking|portefeuille.*?entreprise|gestion.*?portefeuille.*?client|acquisition.*?client|developpement.*?portefeuille|chef d\'agence|directeur d\'agence', raw_full, re_json.IGNORECASE))

    sous_scores = result.get('sous_scores', {})
    if not sous_scores:
        sous_scores = {
            "experience_bancaire": 0,
            "diplome": 0,
            "management": 0,
            "risque_credit": 0,
            "experience_corporate": 0,
            "coherence_parcours": 0,
            "qualite_cv": 0,
            "certification": 0
        }

    # --- Règles métier selon la grille ---

    # 1. Chef d'agence -> management=3, mais pas experience_corporate automatique
    if is_chef_agence:
        sous_scores["management"] = 3
        # Chef d'agence a une exposition au risque de credit
        if sous_scores["risque_credit"] < 2:
            sous_scores["risque_credit"] = 2
        logger.info("✅ Regle metier: Chef d'agence -> management=3, risque_credit=2")

    # 2. Gestionnaire de portefeuille -> risque_credit et experience_corporate
    if is_gestionnaire_portefeuille or has_portfolio_management:
        if sous_scores["risque_credit"] < 2:
            sous_scores["risque_credit"] = 2
        if sous_scores["experience_corporate"] < 2:
            sous_scores["experience_corporate"] = 2
        logger.info("✅ Regle metier: Gestionnaire de portefeuille -> risque_credit=2, experience_corporate=2")

    # 3. Local Corporate/SME détecté -> experience_corporate
    if has_local_corporate:
        if sous_scores["experience_corporate"] < 2:
            sous_scores["experience_corporate"] = 2
        logger.info("✅ Regle metier: Local Corporate/SME detecte -> experience_corporate=2")

    # 4. Limitation des scores selon la grille
    # Grille: experience_bancaire: 0-3, diplome: 0-3, management: 0-3, risque_credit: 0-2, experience_corporate: 0-2, coherence: 0-2, qualite_cv: 0-1, certification: 0-1
    sous_scores["risque_credit"] = min(2, sous_scores.get("risque_credit", 0))
    sous_scores["experience_corporate"] = min(2, sous_scores.get("experience_corporate", 0))
    sous_scores["coherence_parcours"] = min(2, sous_scores.get("coherence_parcours", 0))
    sous_scores["qualite_cv"] = min(1, sous_scores.get("qualite_cv", 0))
    sous_scores["certification"] = min(1, sous_scores.get("certification", 0))
    sous_scores["experience_bancaire"] = min(3, sous_scores.get("experience_bancaire", 0))
    sous_scores["diplome"] = min(3, sous_scores.get("diplome", 0))
    sous_scores["management"] = min(3, sous_scores.get("management", 0))

    # Calcul du score total = somme des sous-scores (max 14)
    score_total = sum(sous_scores.values())
    score_total = min(14, score_total)

    # Gestion des flags éliminatoires
    flags_elim = result.get('flags_eliminatoires', [])
    new_flags = []

    for flag in flags_elim:
        flag_lower = flag.lower()
        # Suppression des flags contradictoires avec les règles métier
        if is_chef_agence:
            if 'manageriale' in flag_lower or 'management' in flag_lower or 'risque' in flag_lower or 'credit' in flag_lower or 'npl' in flag_lower or 'encadrement' in flag_lower:
                continue
        if (is_gestionnaire_portefeuille or has_portfolio_management):
            if 'risque' in flag_lower or 'credit' in flag_lower or 'npl' in flag_lower or 'provision' in flag_lower or 'portefeuille' in flag_lower:
                continue
        if has_local_corporate:
            if 'sme' in flag_lower or 'pme' in flag_lower or 'local corporate' in flag_lower or 'corporate' in flag_lower:
                continue
        if flag_lower and flag_lower.strip() and len(flag_lower) > 3:
            new_flags.append(flag)

    result['sous_scores'] = sous_scores
    result['score'] = score_total
    result['flags_eliminatoires'] = new_flags

    # Décision selon la grille
    if len(new_flags) == 0 and len(flags_elim) > 0:
        poste = result.get('poste', None)
        if not poste:
            poste = result.get('details', {}).get('poste', None)
        result['decision'] = get_recommandation_from_score(score_total, poste)

    if 'score_breakdown' in result:
        result['score_breakdown']['sous_scores'] = sous_scores
        result['score_breakdown']['score_final'] = score_total
        result['score_breakdown']['decision'] = result.get('decision', '')
        result['score_breakdown']['flags_eliminatoires'] = new_flags

    return result

def extract_json_fallback(text):
    """Extrait les donnees minimales de la reponse textuelle avec sous-scores complets selon la grille"""
    logger.info("🔧 Utilisation du fallback d'extraction JSON avec sous-scores complets")
    import re as re_json

    # Détection des compétences
    is_chef_agence = bool(re_json.search(r'chef d\'agence|chef d agence|directeur d\'agence|directeur d agence|responsable d\'agence|responsable d agence|branch manager|agency manager|chef de centre|directeur de centre|responsable de centre|acting branch manager|profit center manager|profit center|branch manager|agency head', text, re_json.IGNORECASE))

    is_gestionnaire_portefeuille = bool(re_json.search(r'gestionnaire de portefeuille|portfolio manager|charge de portefeuille|portfolio officer|credit portfolio|gestionnaire de compte|account manager|relationship manager|chargé de clientèle|charge de clientele|analyste credit|analyste crédit|montage credit|montage crédit|instruction credit|instruction crédit|gestionnaire de clientèle|gestionnaire de clientele|commercial|chargé d\'affaires|charge d affaires|responsable de portefeuille', text, re_json.IGNORECASE))

    has_portfolio_management = bool(re_json.search(r'gestion de portefeuille|portefeuille.*?client|portefeuille.*?credit|portefeuille.*?entreprise|suivi.*?portefeuille|portefeuille.*?sme|portefeuille.*?pme|portefeuille.*?local corporate|portefeuille.*?grandes entreprises|portefeuille.*?npl|portefeuille.*?provision|recouvrement|relance client|gestion des impayés|gestion des impayes', text, re_json.IGNORECASE))

    has_local_corporate = bool(re_json.search(r'local corporate|sme|pme|petites et moyennes entreprises|pm|moyennes entreprises|grandes entreprises|entreprises|corporate local|local corporate banking|portefeuille.*?entreprise|gestion.*?portefeuille.*?client|acquisition.*?client|developpement.*?portefeuille|chef d\'agence|directeur d\'agence', text, re_json.IGNORECASE))

    flags = []
    flag_patterns = [
        (r'Aucune experience.*?bancaire|pas d\'experience.*?bancaire|sans experience.*?bancaire', 'Aucune experience dans le secteur bancaire ou financier reglemente'),
        (r'Moins de.*?ans.*?banque|experience.*?inferieure.*?ans|moins de 5 ans', 'Moins de 5 ans d\'experience professionnelle dans une banque ou institution financiere'),
        (r'Aucune experience manageriale|pas d\'experience manageriale|sans management|pas de management', 'Aucune experience manageriale demontree'),
        (r'Diplome.*?inferieur|niveau.*?diplome.*?insuffisant|pas de diplome', 'Niveau de diplome inferieur a Bac+4'),
        (r'pas de local corporate|pas de sme|pas de pme|pas d\'experience corporate|aucune experience.*?sme|aucune experience.*?pme', 'Aucune experience en gestion d\'un portefeuille de clients SME/PME/Local Corporate')
    ]

    for pattern, default in flag_patterns:
        if re_json.search(pattern, text, re_json.IGNORECASE):
            flags.append(default)

    # Suppression des flags contradictoires selon les règles métier
    if is_chef_agence:
        flags = [f for f in flags if 'manageriale' not in f and 'risque' not in f and 'Local Corporate' not in f and 'SME' not in f and 'credit' not in f.lower()]

    if is_gestionnaire_portefeuille or has_portfolio_management:
        flags = [f for f in flags if 'risque' not in f.lower() and 'credit' not in f.lower() and 'npl' not in f.lower() and 'provision' not in f.lower()]

    if has_local_corporate:
        flags = [f for f in flags if 'SME' not in f and 'Local Corporate' not in f and 'PME' not in f]

    points_forts = []
    if re_json.search(r'experience.*?bancaire|banque|bancaire', text, re_json.IGNORECASE):
        points_forts.append("Experience bancaire detectee")
    if re_json.search(r'management|manager|encadrement|supervision|leadership|chef d\'agence|directeur d\'agence', text, re_json.IGNORECASE):
        points_forts.append("Experience manageriale detectee")
    if re_json.search(r'credit|risque|npl|provision|portefeuille|creances douteuses|creances impayees|impayes|chef d\'agence|gestionnaire de portefeuille|gestionnaire de compte', text, re_json.IGNORECASE):
        points_forts.append("Exposition au risque de credit / NPL detectee")
    if re_json.search(r'local corporate|corporate|sme|pme|petites et moyennes entreprises|pm|portefeuille.*?entreprise|gestion.*?portefeuille.*?client|chef d\'agence', text, re_json.IGNORECASE):
        points_forts.append("Experience en gestion de portefeuille SME/Local Corporate detectee")
    if is_chef_agence:
        points_forts.append("✅ Chef d'agence - Management et risque de credit confirmes")
    if is_gestionnaire_portefeuille:
        points_forts.append("✅ Gestionnaire de portefeuille - Risque de credit et relation client confirmes")
    if has_portfolio_management:
        points_forts.append("✅ Gestion de portefeuille detectee - Exposition au risque de credit confirmee")
    if has_local_corporate:
        points_forts.append("✅ Experience Local Corporate/SME detectee")

    points_vigilance = []
    if re_json.search(r'manque|insuffisant|faible|limite|peu', text, re_json.IGNORECASE):
        points_vigilance.append("⚠️ Certains criteres ne sont pas satisfaits")
    if re_json.search(r'sans.*?experience|experience.*?limitee|experience.*?courte|stage', text, re_json.IGNORECASE):
        points_vigilance.append("⚠️ Experience professionnelle limitee")
    if re_json.search(r'pas de diplome|diplome.*?manquant', text, re_json.IGNORECASE):
        points_vigilance.append("⚠️ Niveau de diplome insuffisant")
    if re_json.search(r'pas de local corporate|pas de sme|pas de pme|sans experience.*?sme|sans experience.*?pme', text, re_json.IGNORECASE):
        points_vigilance.append("⚠️ Absence d'experience en SME/Local Corporate")
    if re_json.search(r'back[- ]?office|sans experience commerciale|sans.*?business', text, re_json.IGNORECASE):
        points_vigilance.append("⚠️ Parcours back-office sans experience commerciale SME/Local Corporate")

    # Calcul des sous-scores selon la grille
    sous_scores = {}

    # Experience bancaire (0-3)
    exp_match = re_json.search(r'experience\s*(?:bancaire|professionnelle|en banque)\s*(?:de|:)?\s*(\d+)', text, re_json.IGNORECASE)
    if exp_match:
        years = int(exp_match.group(1))
        sous_scores["experience_bancaire"] = 3 if years >= 5 else (2 if years >= 3 else (1 if years >= 1 else 0))
    else:
        if re_json.search(r'experience.*?bancaire.*?\d+\s*ans', text, re_json.IGNORECASE):
            sous_scores["experience_bancaire"] = 2
        elif re_json.search(r'experience.*?bancaire|banque', text, re_json.IGNORECASE):
            sous_scores["experience_bancaire"] = 1
        else:
            sous_scores["experience_bancaire"] = 0

    # Diplome (0-3)
    if re_json.search(r'master|mba|doctorat|phd|ingenieur|bac\+5|bac 5|master specialise', text, re_json.IGNORECASE):
        sous_scores["diplome"] = 3
    elif re_json.search(r'bac\+4|bac 4|maitrise|licence professionnelle', text, re_json.IGNORECASE):
        sous_scores["diplome"] = 2
    elif re_json.search(r'licence|bachelor|bac\+3|bac 3', text, re_json.IGNORECASE):
        sous_scores["diplome"] = 1
    else:
        sous_scores["diplome"] = 0

    # Management (0-3)
    if is_chef_agence:
        sous_scores["management"] = 3
    elif re_json.search(r'manager|directeur|chef.*?service|responsable.*?equipe|leadership.*?fort|pilotage.*?p&l|gestion.*?p&l|chef d\'agence|directeur d\'agence', text, re_json.IGNORECASE):
        sous_scores["management"] = 3
    elif re_json.search(r'management|encadrement|supervision|coordination|gestion d\'equipe|developpement.*?collaborateurs', text, re_json.IGNORECASE):
        sous_scores["management"] = 2
    elif re_json.search(r'equipe|collaborateurs|subordonnes|animateur', text, re_json.IGNORECASE):
        sous_scores["management"] = 1
    else:
        sous_scores["management"] = 0

    # Risque de credit (0-2 selon la grille)
    if is_chef_agence or is_gestionnaire_portefeuille or has_portfolio_management:
        sous_scores["risque_credit"] = 2
    elif re_json.search(r'npl|creances douteuses|creances impayees|impayes|provision.*?portefeuille|ifrs 9.*?stage|risk management.*?credit|ratio npl|cir|cout du risque|chef d\'agence|gestionnaire de portefeuille', text, re_json.IGNORECASE):
        sous_scores["risque_credit"] = 2
    elif re_json.search(r'credit|risque|analyse financiere|gestion de portefeuille|portefeuille.*?credit|suivi.*?portefeuille', text, re_json.IGNORECASE):
        sous_scores["risque_credit"] = 1
    elif re_json.search(r'finance|comptabilite|economie', text, re_json.IGNORECASE):
        sous_scores["risque_credit"] = 1
    else:
        sous_scores["risque_credit"] = 0
    sous_scores["risque_credit"] = min(2, sous_scores["risque_credit"])

    # Experience corporate (0-2 selon la grille)
    if is_chef_agence:
        sous_scores["experience_corporate"] = 2
    elif is_gestionnaire_portefeuille or has_portfolio_management:
        sous_scores["experience_corporate"] = 2
    elif has_local_corporate:
        sous_scores["experience_corporate"] = 2
    elif re_json.search(r'local corporate|sme|pme|portefeuille.*?entreprise|gestion.*?portefeuille.*?client|acquisition.*?client|developpement.*?portefeuille|chef d\'agence', text, re_json.IGNORECASE):
        sous_scores["experience_corporate"] = 2
    elif re_json.search(r'corporate|entreprise|client.*?entreprise|gestion.*?client', text, re_json.IGNORECASE):
        sous_scores["experience_corporate"] = 1
    elif re_json.search(r'commercial|relation client|client', text, re_json.IGNORECASE):
        sous_scores["experience_corporate"] = 1
    else:
        sous_scores["experience_corporate"] = 0
    sous_scores["experience_corporate"] = min(2, sous_scores["experience_corporate"])

    # Coherence du parcours (0-2)
    if re_json.search(r'\d+\s*(?:ans|annees).*?(?:progression|evolution|promotion|carriere|responsabilites.*?croissantes)', text, re_json.IGNORECASE):
        sous_scores["coherence_parcours"] = 2
    elif re_json.search(r'\d+\s*(?:ans|annees).*?experience|poste.*?responsable|poste.*?chef', text, re_json.IGNORECASE):
        sous_scores["coherence_parcours"] = 1
    else:
        sous_scores["coherence_parcours"] = 0

    # Qualite du CV (0-1)
    if re_json.search(r'\d+\s*%|\d+\s*dossiers|\d+\s*rapports|\d+\s*millions|\d+\s*clients|chiffres|resultats|objectifs.*?atteints', text, re_json.IGNORECASE):
        sous_scores["qualite_cv"] = 1
    else:
        sous_scores["qualite_cv"] = 0

    # Certification / Connaissance du marché (0-1)
    if re_json.search(r'itb|moody|ecobank.*?certification|certification.*?bancaire|cemac|uemoa|tchad|frankfurt school|financement des pme', text, re_json.IGNORECASE):
        sous_scores["certification"] = 1
    else:
        sous_scores["certification"] = 0

    score_total = sum(sous_scores.values())
    score_total = min(14, score_total)

    synthese = "Analyse automatique: "
    if points_forts:
        synthese += "Points forts: " + ", ".join(points_forts) + ". "
    if points_vigilance:
        synthese += "Points de vigilance: " + ", ".join(points_vigilance) + ". "
    if flags:
        synthese += "❌ CRITERES ELIMINATOIRES NON SATISFAITS: " + ", ".join(flags) + ". "
        synthese += "Candidat REJETE immediatement malgre un score de " + str(score_total) + "/14."
    if len(text) > 50:
        synthese += text[:300] + "..."
    else:
        synthese += "Analyse du CV complete."

    return {
        'flags_eliminatoires': flags,
        'points_forts': points_forts,
        'points_vigilance': points_vigilance,
        'score_total': score_total,
        'synthese_recruteur': synthese,
        'sous_scores': sous_scores,
        'checklist': {
            'experience_bancaire': sous_scores["experience_bancaire"] >= 2,
            'diplome_suffisant': sous_scores["diplome"] >= 2,
            'management_detecte': sous_scores["management"] >= 2,
            'risque_credit_detecte': sous_scores["risque_credit"] >= 1,
            'experience_corporate': sous_scores["experience_corporate"] >= 1,
            'coherence_parcours': sous_scores["coherence_parcours"] >= 1
        }
    }

app = Flask(__name__)
ALLOWED_ORIGINS = ["https://recrutment.onrender.com", "https://backend1-fiq5.onrender.com", "http://localhost:5000", "http://localhost:3000"]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS, "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"], "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"], "supports_credentials": True, "max_age": 600}})

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS,PUT,DELETE')
    response.headers.add('Access-Control-Max-Age', '600')
    if request.method == 'OPTIONS':
        response.status_code = 204
    return response

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return jsonify({
        'status': 'ok',
        'message': f'RecrutBank API is running with {_PROVIDER}',
        'version': 'v9.0-grille-stable-scoring',
        'features': {
            'pdf_available': PDFPLUMBER_AVAILABLE,
            'docx_available': DOCX_AVAILABLE,
            'reportlab_available': REPORTLAB_AVAILABLE,
            'openpyxl_available': OPENPYXL_AVAILABLE,
            'ia_available': IA_ANALYSE_ACTIVE,
            'ia_provider': _PROVIDER,
            'ia_model': _MODEL,
            'reasoning_mode': OPENROUTER_REASONING_ENABLED,
            'scoring_strict': True,
            'manual_status_priority': True,
            'auto_width_excel': True,
            'max_concurrent_downloads': DOWNLOAD_MAX_CONCURRENT,
            'zip_max_workers': _ZIP_MAX_WORKERS,
            'intelligent_scoring': True,
            'advanced_reasoning': True,
            'free_ia': "OpenRouter" in _PROVIDER,
            'json_robust_parsing': True,
            'sous_scores_complets': True,
            'eliminatoire_rejet': True,
            'score_conserve': True,
            'score_somme_sous_scores': True,
            'local_corporate_sme': True,
            'chef_agence_auto_scoring': True,
            'portefeuille_auto_credit': True,
            'business_rules_stable': True,
            'flags_auto_suppression': True,
            'grille_reference': 'Chef de Division Local Corporate',
            'scoring_max': 14,
            'seuils': {'prioritaire': 11, 'potentiel_min': 7, 'rejet_max': 6}
        }
    }), 200

app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "candidatures")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app.config['SMTP_HOST'] = os.getenv('SMTP_HOST', 'smtp.gmail.com')
app.config['SMTP_PORT'] = int(os.getenv('SMTP_PORT', 587))
app.config['SMTP_USER'] = os.getenv('SMTP_USER', '')
app.config['SMTP_PASSWORD'] = os.getenv('SMTP_PASSWORD', '')
app.config['SMTP_FROM'] = os.getenv('SMTP_FROM', 'RecrutBank RH <oualoumidjeupisne@gmail.com>')
app.config['SMTP_USE_TLS'] = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_file_to_supabase(file_obj, blob_name, content_type=None):
    if not supabase:
        return None
    try:
        file_bytes = file_obj.read()
        supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(blob_name, file_bytes, {"content-type": content_type or "application/octet-stream", "upsert": "true"})
        return blob_name
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return None

@retry_with_backoff(max_retries=DOWNLOAD_MAX_RETRIES)
def download_file_from_supabase_robust(blob_name):
    if not supabase:
        return None
    with _DOWNLOAD_SEMAPHORE:
        try:
            response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).download(blob_name)
            return response
        except Exception as e:
            logger.error(f"Erreur telechargement {blob_name}: {e}")
            raise

def download_file_from_supabase(blob_name):
    if not supabase:
        return None
    try:
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).download(blob_name)
        return response
    except Exception as e:
        error_str = str(e).lower()
        if any(kw in error_str for kw in ["errno 11", "resource temporarily unavailable", "timeout", "connection"]):
            logger.warning(f"Erreur temporaire detectee, activation du mode robuste pour {blob_name}")
            return download_file_from_supabase_robust(blob_name)
        logger.error(f"Download error: {e}")
        return None

def get_signed_url(blob_name, expiration_minutes=60):
    if not supabase:
        return None
    try:
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).create_signed_url(blob_name, expiration_minutes * 60)
        return response.get('signedURL') if response else None
    except Exception as e:
        logger.error(f"Signed URL error: {e}")
        return None

def send_email(to_email, subject, body):
    import requests
    import re as _re
    brevo_api_key = os.getenv('BREVO_API_KEY', '')
    smtp_from = os.getenv('SMTP_FROM', 'RecrutBank RH <oualoumidjeupisne@gmail.com>')
    if not brevo_api_key:
        return False
    match = _re.search(r'<(.+?)>', smtp_from)
    sender_email = match.group(1) if match else smtp_from
    sender_name = smtp_from.split('<')[0].strip() if '<' in smtp_from else 'RecrutBank RH'
    html_content = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;"><div style="max-width: 600px; margin: 0 auto; padding: 20px;">{body.replace(chr(10), '<br>')}</div></body></html>"""
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"api-key": brevo_api_key, "Content-Type": "application/json", "Accept": "application/json"}
    payload = {"sender": {"name": sender_name, "email": sender_email}, "to": [{"email": to_email, "name": to_email.split('@')[0]}], "subject": subject, "htmlContent": html_content, "textContent": body}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 201
    except Exception:
        return False

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def normalize_spaces(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\b(\w)\s+(\w\s+\w+)\b', r'\1\2', text)
    text = re.sub(r'\b(\w)\s+(\w)\b', r'\1\2', text)
    bank_corrections = {'u b a': 'UBA', 'e c o b a n k': 'ECOBANK', 'o r a b a n k': 'ORABANK', 'u b a -': 'UBA-', 'e c o o b a n k': 'ECOBANK', 'f i n a d e v': 'FINADEV', 'w o r l d': 'WORLD', 'v i s i o n': 'VISION', 'g l s': 'GLS', 'u b a g r o u p': 'UBAGROUP', 'c o r r e c t': 'CORRECT', 's e r v i c e s': 'SERVICES', 'c o n s u l t i n g': 'CONSULTING'}
    for wrong, correct in bank_corrections.items():
        text = re.sub(r'\b' + wrong + r'\b', correct, text, flags=re.IGNORECASE)
    return text.strip()

def normalize_unicode(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'[\u00A0\u1680\u2000-\u200B\u2028\u2029\u202F\u205F\u3000]', ' ', text)
    return text.strip()

def normalize_for_matching(text):
    if not text:
        return "", []
    _ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouucaaAAEEEEIIOUUUCAAaaonaaon')
    no_accents = text.lower().translate(_ACCENT_MAP)
    cleaned = re.sub(r'[^\w\s\-/\.]', ' ', no_accents)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    tokens = [t for t in re.findall(r'\b[a-z0-9\-/\.]{2,}\b', cleaned) if len(t) >= 2]
    return cleaned, tokens

def contains_negative_context(text, keyword):
    if not text or not keyword:
        return False
    keyword_pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    matches = list(keyword_pattern.finditer(text))
    if not matches:
        return False
    negative_patterns = [r"\b(pas\s+de|pas\s+d')\s*(experience|experimente|competence)\b", r'\b(aucun|aucune|aucuns|aucunes)\s*(experience|competence|connaissance)\b', r'\b(sans|depourvu\s+de|manque\s+de)\s*(experience|competence)\b', r"\b(n')?(?:ai|as|a|avons|avez|ont)\s+pas\s+(?:d')?(experience|competence|connaissance)\b", r'\b(jamais\s+(?:eu|travaille|exerce|pratique))\b', r"\b(peu\s+d')?experience\b", r'\b(experience\s+(?:limitee|insuffisante|faible|partielle))\b', r'\b(ne\s+connais\s+pas|ne\s+maitrise\s+pas|ne\s+possede\s+pas)\b', r'\b(no\s+experience|without\s+experience|lack\s+of\s+experience)\b']
    for match in matches:
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        context = text[start:end]
        for pattern in negative_patterns:
            if re.search(pattern, context, re.IGNORECASE):
                return True
    return False

def extract_text_from_pdf_via_ocr(file_bytes):
    if not OCR_AVAILABLE:
        return ""
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return ""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode != 'L':
            img = img.convert('L')
        custom_config = r'--oem 3 --psm 6 -l fra+eng'
        text = pytesseract.image_to_string(img, config=custom_config)
        if text.strip():
            text = normalize_spaces(text)
            text = re.sub(r'[|¦]', '', text)
            return normalize_unicode(text)
        return ""
    except Exception:
        return ""

MAX_PDF_PAGES = 10
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024
MAX_TEXT_SIZE = 15000

def extract_text_from_pdf_robust(file_bytes, filename):
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        logger.warning(f"PDF trop volumineux ({len(file_bytes) / 1024 / 1024:.1f} MB > 10 MB): {filename}")
        return ""
    text = ""
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                total_pages = min(len(pdf.pages), MAX_PDF_PAGES)
                for i in range(total_pages):
                    try:
                        page = pdf.pages[i]
                        tables = page.extract_tables()
                        if tables:
                            for table in tables:
                                for row in table:
                                    if row:
                                        row_text = ' | '.join([str(cell).strip() if cell else '' for cell in row])
                                        if row_text.strip():
                                            text += normalize_spaces(row_text) + "\n"
                        content = page.extract_text(x_tolerance=3, y_tolerance=3, keep_blank_chars=True, use_text_flow=True)
                        if content:
                            text += normalize_spaces(content) + "\n"
                        if len(text) > MAX_TEXT_SIZE:
                            text = text[:MAX_TEXT_SIZE]
                            break
                    except Exception as e:
                        logger.warning(f"pdfplumber page {i} erreur: {e}")
                        continue
            if text.strip() and len(text.strip()) > 50:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"pdfplumber erreur: {e}")
    if PYPDF2_AVAILABLE:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            total_pages = min(len(reader.pages), MAX_PDF_PAGES)
            for i in range(total_pages):
                try:
                    content = reader.pages[i].extract_text()
                    if content:
                        text += normalize_spaces(content) + "\n"
                    if len(text) > MAX_TEXT_SIZE:
                        text = text[:MAX_TEXT_SIZE]
                        break
                except Exception as e:
                    logger.warning(f"PyPDF2 page {i} erreur: {e}")
                    continue
            if text.strip() and len(text.strip()) > 50:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"PyPDF2 erreur: {e}")
    if len(text.strip()) < 50 and OCR_AVAILABLE:
        try:
            ocr_text = extract_text_from_pdf_via_ocr(file_bytes)
            if ocr_text and len(ocr_text.strip()) > 50:
                return ocr_text
        except Exception as e:
            logger.warning(f"OCR erreur: {e}")
    if len(text.strip()) < 30:
        try:
            for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    raw_text = file_bytes.decode(encoding, errors='ignore')
                    raw_text = re.sub(r'[^\w\s\.\,\:\;\-\?\!\@\#\%\&\*\(\)\-\+\=\/\'\"]', ' ', raw_text)
                    raw_text = re.sub(r'\s+', ' ', raw_text).strip()
                    if len(raw_text) > 50:
                        logger.info(f"Extraction brute reussie avec {encoding}")
                        return normalize_unicode(raw_text)
                except:
                    continue
        except Exception as e:
            logger.warning(f"Extraction brute echouee: {e}")
    return text.strip() if text.strip() else ""

def extract_text_from_docx_robust(file_bytes):
    if not DOCX_AVAILABLE:
        return ""
    try:
        doc = Document(io.BytesIO(file_bytes))
        W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        W_T = f'{{{W_NS}}}t'
        texts = [e.text for e in doc.element.body.iter(W_T) if e.text and e.text.strip()]
        raw = ' '.join(texts)
        raw = re.sub(r'\s+', ' ', raw).strip()
        if len(raw) > MAX_TEXT_SIZE:
            raw = raw[:MAX_TEXT_SIZE]
        return normalize_unicode(raw)
    except Exception as e:
        logger.warning(f"Erreur lecture DOCX (XML): {e}")
    try:
        doc = Document(io.BytesIO(file_bytes))
        parts = []
        for para in doc.paragraphs:
            t = normalize_spaces(para.text)
            if t:
                parts.append(t)
        for table in doc.tables:
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    ct = normalize_spaces(cell.text)
                    if ct:
                        cells.append(ct)
                if cells:
                    parts.append(" | ".join(cells))
        result = "\n".join(parts).strip()
        if len(result) > MAX_TEXT_SIZE:
            result = result[:MAX_TEXT_SIZE]
        return normalize_unicode(result)
    except Exception as e2:
        logger.warning(f"Fallback DOCX echoue: {e2}")
    try:
        text = re.sub(r'[^\x20-\x7E\u00C0-\u017F]+', ' ', file_bytes.decode('utf-8', errors='ignore'))
        if len(text) > MAX_TEXT_SIZE:
            text = text[:MAX_TEXT_SIZE]
        return normalize_unicode(normalize_spaces(text.strip()))
    except Exception:
        pass
    return ""

def extract_text_from_txt(file_bytes):
    if CHARDET_AVAILABLE:
        try:
            detected = chardet.detect(file_bytes[:10000])
            encoding = detected['encoding'] or 'utf-8'
            text = file_bytes.decode(encoding, errors='ignore')
            if len(text) > MAX_TEXT_SIZE:
                text = text[:MAX_TEXT_SIZE]
            return normalize_unicode(normalize_spaces(text))
        except Exception:
            pass
    for enc in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1', 'utf-16']:
        try:
            text = file_bytes.decode(enc, errors='ignore').strip()
            if len(text) > MAX_TEXT_SIZE:
                text = text[:MAX_TEXT_SIZE]
            return normalize_unicode(normalize_spaces(text))
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""

def extract_text_robust_from_bytes(file_bytes, filename):
    if not file_bytes:
        return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    text = ""
    if ext == 'pdf':
        text = extract_text_from_pdf_robust(file_bytes, filename)
        logger.info(f"Extraction PDF {filename}: {len(text)} caracteres")
    elif ext in ('doc', 'docx'):
        text = extract_text_from_docx_robust(file_bytes)
        logger.info(f"Extraction DOCX {filename}: {len(text)} caracteres")
    elif ext == 'txt':
        text = extract_text_from_txt(file_bytes)
        logger.info(f"Extraction TXT {filename}: {len(text)} caracteres")
    else:
        try:
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    text = file_bytes.decode(encoding, errors='ignore').strip()
                    if len(text) > 50:
                        break
                except:
                    continue
            if len(text) > MAX_TEXT_SIZE:
                text = text[:MAX_TEXT_SIZE]
            text = normalize_unicode(normalize_spaces(text))
            logger.info(f"Extraction brute {filename}: {len(text)} caracteres")
        except Exception:
            pass
    if len(text.strip()) < 30:
        logger.warning(f"Extraction faible pour {filename}: {len(text)} caracteres")
    return text.strip() if text.strip() else ""

def init_recruteur():
    try:
        if supabase:
            response = supabase.table('recruteurs').select('*').eq('email', 'sougnabeoualoumibank@gmail.com').execute()
            if not response.data:
                supabase.table('recruteurs').insert({"email": "sougnabeoualoumibank@gmail.com", "password": hash_pwd("AdminLaurent123"), "nom": "Responsable RH"}).execute()
    except Exception as e:
        logger.warning(f"Erreur initialisation recruteur : {e}")
init_recruteur()

POSTES = ["Responsable Administration de Credit", "Analyste Credit CCB", "Archiviste (Administration Credit)", "Senior Finance Officer", "Market Risk Officer", "IT Reseau & Infrastructure", "Auditeur interne", "Chef service controle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marche", "Chef service reporting reglementaire", "Chef de Section Compensation", "Charge(e) d'Administration de Credit", "Chef de Division Local Corporate", "Data Analyst Finance"]

POSTES_ACTIFS = ["Chef de Division Local Corporate", "Data Analyst Finance"]
POSTES_CLOTURES = [p for p in POSTES if p not in POSTES_ACTIFS]

def is_poste_actif(poste):
    return poste in POSTES_ACTIFS

# Grille mise à jour avec les bons scores max
GRILLE = {
    "Chef de Division Local Corporate": {
        "eliminatoire": [
            "A une experience dans le secteur bancaire ou financier reglemente",
            "A un diplome de niveau Bac+4 ou superieur (Master, MBA ou equivalent)",
            "A minimum 5 ans d'experience professionnelle dans une banque ou institution financiere",
            "A une experience manageriale demontree (encadrement d'equipe, pilotage d'activite commerciale)",
            "A une exposition a la gestion du risque de credit ou au suivi de la qualite d'un portefeuille (NPL, provisions)",
            "A une experience en gestion d'un portefeuille de clients SME/PME/Local Corporate"
        ],
        "a_verifier": [
            "A pilote une activite Local Corporate/SME (PME) avec des objectifs chiffres (revenus, volumes, marges)",
            "A gere un portefeuille de clients Local Corporate/SME (PME) et demontre sa capacite a le developper",
            "A encadre et evalue une equipe commerciale ou bancaire",
            "A assure le suivi de la qualite du portefeuille de credit (NPL, CIR, provisions) et rendu compte a la direction",
            "A developpe des ventes croisees (cross-selling) ou des partenariats interdépartementaux",
            "A produit ou supervise des rapports de performance commerciale et financiere",
            "A une exposition a la reglementation bancaire locale (COBAC, BEAC) ou internationale"
        ],
        "signaux_forts": [
            "A pilote une division ou une ligne Local Corporate/SME (PME) avec atteinte des objectifs de revenus et de portefeuille",
            "A une gestion active du ratio NPL (creances douteuses) et du ratio cout/revenu (CIR) - resultats chiffres mentionnes",
            "A une experience averee en cross-selling avec des equipes TSG, Trade Finance ou Cash Management",
            "A developpe reellement le portefeuille Local Corporate : acquisition de clients, fidelisation, nombre de produits par client",
            "A demontre un leadership fort (constitution d'equipe, developpement des collaborateurs, vivier de talents)",
            "Possede une certification bancaire (Ecobank, Moody's, ITB - Institut Technique de Banque, ou equivalent)",
            "A une connaissance approfondie du marche Local Corporate/SME (PME) tchadien ou de la zone CEMAC/UEMOA",
            "A une exposition aux plateformes numeriques bancaires (OMNI, Cash Management ou equivalent)",
            "Presente des resultats commerciaux quantifies et verifiables dans son CV (chiffres d'affaires, taux de croissance, NPS)"
        ],
        "points_attention": [
            "Parcours exclusivement back-office ou risques sans experience commerciale Local Corporate/SME (PME)",
            "Profil techniquement solide (credit, analyse) mais sans experience manageriale ni pilotage de P&L",
            "Experiences tres courtes (moins de 2 ans par poste) sans progression hierarchique visible",
            "CV sans resultats chiffres (missions decrites en responsabilites sans livrables ni indicateurs atteints)",
            "Mobilite geographique ou sectorielle excessive sans ancrage dans le secteur bancaire Local Corporate",
            "Trous inexpliques dans le parcours ou incoherences entre les postes declares"
        ],
        "scores_max": {
            "experience_bancaire": 3,
            "diplome": 3,
            "management": 3,
            "risque_credit": 2,
            "experience_corporate": 2,
            "coherence_parcours": 2,
            "qualite_cv": 1,
            "certification": 1
        }
    },
    "Data Analyst Finance": {
        "eliminatoire": [
            "A une formation en Finance, Comptabilite, Controle de gestion, Statistiques, Data Analytics ou Informatique decisionnelle",
            "A un diplome de niveau Bac+3 ou superieur",
            "A une experience en analyse financiere, reporting financier, controle de gestion, audit ou data analytics",
            "Maitrise Excel (TCD, formules, Power Query) - competence incontournable",
            "A des connaissances en comptabilite et en etats financiers (P&L, bilan, flux de tresorerie)"
        ],
        "a_verifier": [
            "A produit des rapports financiers periodiques (mensuels, trimestriels)",
            "A conçu ou maintenu des tableaux de bord financiers (Power BI, Excel ou autre outil BI)",
            "A realise des analyses Budget / Realise / N-1 avec identification des ecarts",
            "A travaille avec SQL pour extraire ou interroger des donnees financieres",
            "A assure la reconciliation de donnees multi-sources (comptabilite / systemes operationnels)",
            "A participe a l'elaboration d'un budget ou d'un forecast financier",
            "A une experience dans le secteur bancaire ou avec un Core Banking (FLEXCUBE, T24, Amplitude)"
        ],
        "signaux_forts": [
            "Maitrise explicite de Power BI (dashboards, DAX, Power Query) avec exemples concrets",
            "Experience averee en automatisation de reportings (Power Query, VBA, Python, outils ETL)",
            "Analyse d'ecarts Budget / Realise / N-1 avec presentation a la Direction Financiere ou a la DG",
            "Participation a la construction de modeles de prevision financiere ou d'analyses de scenarios",
            "Exposition aux donnees bancaires : PNB, NPL, cout du risque, rentabilite par agence ou produit",
            "Maitrise de SQL pour l'extraction et la manipulation de donnees en base relationnelle",
            "Connaissance de Python ou R pour des analyses statistiques avancees",
            "Mise en place de controles qualite sur les donnees et documentation des regles de calcul",
            "Resultats quantifies dans le CV : gains de productivite, delais reduits, anomalies detectees"
        ],
        "points_attention": [
            "Profil purement comptable sans exposition aux outils BI ou au reporting de gestion",
            "Profil exclusivement IT / developpeur sans connaissance financiere",
            "Experience uniquement academique ou stage sans production de reportings reels en environnement professionnel",
            "CV sans aucun outil cite nommement",
            "Missions decrites en termes generiques sans livrables precis ni resultats mesurables",
            "Trous inexpliques dans le parcours ou experiences tres courtes sans progression visible"
        ],
        "scores_max": {
            "experience_bancaire": 3,
            "diplome": 2,
            "management": 1,
            "risque_credit": 0,
            "experience_corporate": 0,
            "coherence_parcours": 2,
            "qualite_cv": 2,
            "certification": 0
        }
    }
}

# Autres postes avec leurs scores max
for poste in ["Auditeur interne", "Chef service controle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marche", "Chef service reporting reglementaire"]:
    if poste not in GRILLE:
        GRILLE[poste] = {
            "eliminatoire": ["Experience dans le secteur", "Diplome requis", "Experience professionnelle"],
            "a_verifier": ["Competences techniques", "Management", "Reporting"],
            "signaux_forts": ["Certification", "Experience avancee"],
            "points_attention": ["Profil junior", "Manque de specialisation"],
            "scores_max": {
                "experience_bancaire": 3,
                "diplome": 3,
                "management": 3,
                "risque_credit": 2,
                "experience_corporate": 2,
                "coherence_parcours": 2,
                "qualite_cv": 1,
                "certification": 1
            }
        }

POSTES_AVEC_SCORING_100 = ["Auditeur interne", "Chef service controle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marche", "Chef service reporting reglementaire"]
POSTES_AVEC_SCORING_12 = ["Chef de Section Compensation", "Charge(e) d'Administration de Credit"]
POSTES_AVEC_SCORING_14 = ["Chef de Division Local Corporate", "Data Analyst Finance"]

def get_score_max_for_poste(poste):
    if poste in POSTES_AVEC_SCORING_12:
        return 12
    if poste in POSTES_AVEC_SCORING_14:
        return 14
    if poste in POSTES_AVEC_SCORING_100:
        return 100
    return 10

def get_recommandation_from_score(score, poste=None):
    """Décision selon la grille de présélection"""
    s = int(score)
    # Chef de Division Local Corporate - Grille: 11-14 prioritaire, 7-10 potentiel, <7 rejet
    if poste == "Chef de Division Local Corporate":
        if s >= 11:
            return "Entretien prioritaire"
        elif s >= 7:
            return "Potentiel a evaluer en entretien"
        else:
            return "Rejet"
    if poste and poste in POSTES_AVEC_SCORING_14:
        if s >= 11:
            return "Entretien prioritaire"
        elif s >= 7:
            return "Potentiel a evaluer en entretien"
        else:
            return "Rejet"
    if poste and poste in POSTES_AVEC_SCORING_12:
        if s >= 10:
            return "Entretien prioritaire"
        elif s >= 7:
            return "Potentiel a evaluer en entretien"
        else:
            return "Rejet"
    if poste and poste in POSTES_AVEC_SCORING_100:
        if s >= 80:
            return "Shortlist"
        elif s >= 70:
            return "A considerer"
        elif s >= 60:
            return "Faible"
        else:
            return "Rejet"
    if s >= 8:
        return "Entretien prioritaire"
    elif s >= 5:
        return "Potentiel a evaluer en entretien"
    else:
        return "Rejet"

def get_statut_from_decision(decision, flags_elim=None):
    if flags_elim and len(flags_elim) > 0:
        return "rejete"
    if not decision:
        return 'en_attente'
    if "Entretien prioritaire" in decision or "Shortlist" in decision:
        return "retenu"
    elif "Potentiel" in decision or "considerer" in decision or "Faible" in decision:
        return "entretien"
    else:
        return "rejete"

def split_into_jobs(raw_text):
    if not raw_text:
        return []
    text = re.sub(r'\s+', ' ', raw_text)
    patterns = [
        r'(?:^|\n)(?=\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2})\s*[-–—])',
        r'(?:^|\n)(?=\s*(?:de|du|d[eu]|from|since)\s+(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2}))',
        r'(?:^|\n)(?=\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2})\s*[-–—]\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2}|present|current))',
        r'(?:^|\n)(?=\s*(?:[A-Z][A-Z\s]+)\s*(?:-|–|—)\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2}))',
    ]
    blocks = []
    positions = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            positions.append(match.start())
    positions = sorted(set(positions))
    if not positions:
        return [text.strip()] if text.strip() else []
    for i, pos in enumerate(positions):
        start = pos
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks

STAGE_MARKERS = [r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b', r'\bapprenti\b', r'\bapprentissage\b', r'\balternance\b', r'\bstage de fin\b', r'\bstage academique\b', r'\bstage professionnel\b', r'\bstage de formation\b', r'\bpfr\b', r'\bstage pfe\b', r'\bpfe\b', r'\bvolontariat\b', r'\btrainee\b']
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)

def is_stage_block(block_text):
    return bool(STAGE_PATTERN.search(block_text))

def extract_duration_years_from_block(block_text):
    years = 0.0
    text = block_text.lower()
    _ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouucaaAAEEEEIIOUUUCAAaaonaaon')
    text = text.translate(_ACCENT_MAP)
    duration_patterns = [
        r'(\d+[.,]?\d*)\s*(?:ans?|annee?s?|years?|años?|anos?)',
        r'\(\s*(\d+[.,]?\d*)\s*\)\s*(?:ans?|annee?s?|years?)',
        r'\w+\s+\(\s*(\d+[.,]?\d*)\s*\)\s*(?:ans?|annee?s?|years?)',
        r'plus\s+de\s+(\d+[.,]?\d*)\s*(?:ans?|annee?s?|years?)',
        r'depuis\s+(?:plus\s+de\s+)?(\d+[.,]?\d*)\s*(?:ans?|annee?s?)',
        r'(\d+[.,]?\d*)\s*(?:ans?|annee?s?|years?)\s+(?:d[ée]?experience|dans|en|de)',
        r'experience\s+(?:de\s+)?(\d+[.,]?\d*)\s*(?:ans?|annee?s?)'
    ]
    for dp in duration_patterns:
        m = re.search(dp, text)
        if m:
            try:
                years = float(m.group(1).replace(',', '.'))
                if 0 < years <= 40:
                    return years
            except (ValueError, IndexError):
                pass
    date_patterns = [
        r'(?:de|du|d[eu])\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s*(?:[àa]|au|jusqu\'au|-|–|—)\s*(?:ce\s+jour|aujourd\'hui|present|actuel|en\s+cours|nos\s+jours|now|current)',
        r'(?:de|du|d[eu])\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s*(?:[àa]|au|jusqu\'au|-|–|—)\s*(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})',
        r'(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s*[-–—]\s*(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})',
        r'(?:de|du|d[eu])\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})',
    ]
    for pattern in date_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                groups = m.groups()
                start_year = None
                end_year = None
                if groups and len(groups) >= 6:
                    try:
                        year_positions = []
                        for idx, g in enumerate(groups):
                            if g and re.match(r'^\d{4}$', g):
                                year_positions.append(idx)
                        if len(year_positions) >= 2:
                            start_year = int(groups[year_positions[0]])
                            end_year = int(groups[year_positions[1]])
                        elif len(year_positions) == 1:
                            start_year = int(groups[year_positions[0]])
                            end_year = datetime.datetime.now().year
                    except (ValueError, IndexError):
                        pass
                elif groups and len(groups) >= 3:
                    try:
                        for g in groups:
                            if g and re.match(r'^\d{4}$', g):
                                start_year = int(g)
                                end_year = datetime.datetime.now().year
                                break
                    except (ValueError, IndexError):
                        pass
                if start_year and end_year:
                    delta = end_year - start_year
                    if 0 < delta <= 40:
                        return round(float(delta), 1)
            except (ValueError, IndexError, TypeError):
                continue
    pattern_present = re.compile(r"(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})\s*(?:a|-|–|—|au|jusqu'au|to|until|au\s+)?\s*(?:aujourd'hui|present|actuel|en cours|now|current|actual|hoje|ce jour|nos\s+jours|a\s+nos\s+jours)", re.IGNORECASE)
    m = pattern_present.search(text)
    if m:
        start_year = int(m.group(2))
        current_year = datetime.datetime.now().year
        delta = current_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    pattern_range = re.compile(r"(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})\s*(?:a|-|–|—|au|jusqu'au|to|until)?\s*(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})", re.IGNORECASE)
    m = pattern_range.search(text)
    if m:
        start_year = int(m.group(2))
        end_year = int(m.group(4))
        delta = end_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    m = re.search(r'(\d{1,2})[/\-\.](20\d{2}|19\d{2})\s*[-–—\.]?\s*(?:(\d{1,2})[/\-\.])?(20\d{2}|19\d{2}|present|current|now)', text)
    if m:
        start_year = int(m.group(2))
        end_raw = m.group(4)
        if re.match(r'\d{4}', str(end_raw)):
            end_year = int(end_raw)
        else:
            end_year = datetime.datetime.now().year
        delta = end_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    m = re.search(r'(20\d{2})\s*[-–—]\s*(20\d{2})', text)
    if m:
        start_year = int(m.group(1))
        end_year = int(m.group(2))
        delta = end_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    m = re.search(r'depuis\s+(20\d{2})', text)
    if m:
        start_year = int(m.group(1))
        current_year = datetime.datetime.now().year
        delta = current_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    m = re.search(r'(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*(20\d{2})\s*(?:a|-|–|—|au|jusqu\'au)\s*(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)?\s*(20\d{2})', text, re.IGNORECASE)
    if m:
        try:
            start_year = int(m.group(2))
            end_year = int(m.group(3)) if m.group(3) else datetime.datetime.now().year
            delta = end_year - start_year
            if 0 < delta <= 40:
                return round(float(delta), 1)
        except (ValueError, IndexError):
            pass
    return 0.0

def detect_institution_type(text):
    text_lower = text.lower()
    commercial_banks = [
        'ecobank', 'orabank', 'uba', 'bicec', 'sgbc', 'cbc', 'bct',
        'societe generale', 'standard chartered', 'nsia banque',
        'commercial bank', 'banque commerciale', 'investment bank',
        'banque d affaires', 'credit institution', 'financial institution',
        'banque', 'express union', 'coris bank', 'orabank tchad',
        'uba tchad', 'commercial bank tchad', 'cbt', 'finadev',
        'united bank for africa', 'banque islamique', 'microfinance',
        'orabank tchad', 'commercial bank tchad', 'societe generale', 'soge'
    ]
    commercial_pattern = re.compile(r'\b(' + '|'.join(re.escape(b) for b in commercial_banks) + r')\b', re.IGNORECASE)
    if commercial_pattern.search(text_lower):
        return 'commercial_bank'
    return 'unknown'

def detect_language(text):
    if not LANGDETECT_AVAILABLE or not text or len(text.strip()) < 50:
        return 'fr'
    try:
        lang = detect(text)
        return 'en' if lang == 'en' else 'fr'
    except Exception:
        return 'fr'

def extract_quantified_results(text):
    patterns = [
        r'(\d+)\s*(?:%|pourcent|percent)\s*(?:d\'|de\s+)?(augmentation|croissance|reduction|hausse|baisse)',
        r'(augmentation|croissance|reduction|hausse|baisse)\s+(?:de\s+)?(\d+)',
        r'(\d+)\s*(?:millions|milliards|M|B|K)\s*(?:€|\$|FCFA|XOF)',
        r'CA\s*(?:de|:)?\s*(\d+)',
        r'chiffre\s*d\'affaires\s*(?:de|:)?\s*(\d+)',
        r'portefeuille\s*(?:de|:)?\s*(\d+)',
        r'clients\s*(?:acquis|developpes)?\s*(?:de|:)?\s*(\d+)',
        r'\b(?:NPL|CIR|provisions?)\s*(?:de|:)?\s*(\d+(?:[.,]\d+)?)\s*%',
    ]
    results = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            results.extend(matches)
    return results

def detect_banking_experience_years(text):
    if not text:
        return 0.0
    text_lower = text.lower()
    years = re.findall(r'\b(19|20)\d{2}\b', text)
    if years:
        years_int = sorted([int(y) for y in years])
        total_years = years_int[-1] - years_int[0]
        if total_years > 0 and total_years < 50:
            bank_keywords = ['ecobank', 'orabank', 'uba', 'banque', 'bank', 'bancaire', 'financial', 'credit', 'credit', 'institution financiere', 'local corporate', 'sme', 'pme']
            for kw in bank_keywords:
                if kw in text_lower:
                    return total_years
    match = re.search(r'(\d+)\s*(?:ans|annees?)\s*(?:d[ée]?experience\s+)?(?:dans\s+la\s+banque|en\s+banque|bancaire|de\s+banque|local corporate|sme|pme)', text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r'depuis\s+(19|20)\d{2}', text, re.IGNORECASE)
    if match:
        start_year = int(match.group(1))
        current_year = datetime.datetime.now().year
        return current_year - start_year
    blocks = split_into_jobs(text)
    total_years = 0.0
    for block in blocks:
        if is_stage_block(block):
            continue
        block_lower = block.lower()
        is_banking = False
        banking_keywords = ['ecobank', 'orabank', 'uba', 'banque', 'bank', 'bancaire', 'financial', 'credit', 'credit', 'institution financiere', 'local corporate', 'sme', 'pme']
        for kw in banking_keywords:
            if kw in block_lower:
                is_banking = True
                break
        if is_banking:
            duration = extract_duration_years_from_block(block)
            if duration > 0:
                total_years += duration
    if total_years > 0:
        return total_years
    return 0.0

def check_criterion_match_semantic(criterion, normalized_text, raw_full_text="", poste=None, lang='fr'):
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords:
        return False, 0.5, []
    semantic_expansions = {
        "management": ["manager", "leadership", "supervision", "coordination", "direction", "pilotage", "encadrement", "gestion d'equipe", "management d'equipe", "team management", "team lead", "superviseur", "chef d'agence", "directeur d'agence", "responsable d'agence", "branch manager", "acting branch manager", "profit center manager"],
        "cross-selling": ["ventes croisees", "cross selling", "cross-selling", "synergie commerciale", "commercial synergy", "partenariat", "partnership", "collaboration commerciale"],
        "risk": ["risque", "risk", "NPL", "non performing", "provisions", "IFRS 9", "credit", "credit", "portefeuille", "portfolio", "CIR", "cout du risque", "creances douteuses", "creances impayees", "impayes"],
        "corporate": ["local corporate", "corporate", "sme", "pme", "petites et moyennes entreprises", "pm", "moyennes entreprises", "grandes entreprises", "entreprises", "corporate local", "local corporate banking", "chef d'agence", "directeur d'agence", "gestionnaire de portefeuille", "gestionnaire de compte"],
        "certification": ["certification", "certificat", "certified", "ITB", "Moody's", "Ecobank", "MBA", "Master", "formation", "frankfurt school", "financement des pme"]
    }
    best_score = 0.0
    found_kws = []
    text_clean, text_tokens = normalize_for_matching(normalized_text)
    for kw in keywords:
        kw_clean, kw_tokens = normalize_for_matching(kw)
        if contains_negative_context(raw_full_text, kw):
            continue
        if kw_clean in text_clean:
            found_kws.append(kw)
            best_score = max(best_score, 1.0)
            continue
        if RAPIDFUZZ_AVAILABLE and len(kw_clean) >= 4:
            ratio = fuzz.partial_ratio(kw_clean, text_clean)
            if ratio >= 75:
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}~{ratio/100:.2f}")
                    best_score = max(best_score, ratio / 100)
                continue
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.5):
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                    best_score = max(best_score, len(common) / len(kw_tokens))
    if best_score < 0.5 and lang in ['en', 'fr']:
        for category, synonyms in semantic_expansions.items():
            for kw in keywords:
                if category in kw.lower() or kw.lower() in category:
                    for syn in synonyms:
                        syn_clean, _ = normalize_for_matching(syn)
                        if syn_clean in text_clean:
                            found_kws.append(f"{syn} (semantique)")
                            best_score = max(best_score, 0.75)
    threshold = 0.45 if len(normalized_text) < 500 else 0.55
    return best_score >= threshold, round(best_score, 2), found_kws

def analyze_cv_with_ia_reasoning(cv_text, lettre_text, attestation_texts_list, poste):
    if not IA_ANALYSE_ACTIVE or not _client or not cv_text or len(cv_text.strip()) < 50 or poste not in GRILLE:
        return None

    grille = GRILLE.get(poste, {})
    scores_max = grille.get('scores_max', {
        "experience_bancaire": 3,
        "diplome": 3,
        "management": 3,
        "risque_credit": 2,
        "experience_corporate": 2,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    })

    system_prompt = f"""Tu es un consultant senior en recrutement bancaire avec 20 ans d'experience en Afrique centrale et de l'Ouest (CEMAC/UEMOA).

REGLES ABSOLUES D'ANALYSE :
1. Tu DOIS raisonner etape par etape comme un expert humain.
2. Tu ne JAMAIS inventer des faits qui ne sont PAS dans les documents.
3. Si une information n'est PAS mentionnee, tu la consideres comme ABSENTE.
4. Les stages, benefolats et formations NE COMPTENT PAS comme experience pro.
5. Tu JUSTIFIES chaque evaluation avec des CITATIONS du CV/lettre.
6. Tu utilises le contexte CEMAC/UEMOA (COBAC, BEAC, reglementation locale).
7. Les NPL (Non-Performing Loans) sont des creances douteuses ou impayees.

GRILLE DE SCORING POUR CE POSTE (MAX 14):
- experience_bancaire: 0-3
- diplome: 0-3
- management: 0-3
- risque_credit: 0-2 (max 2)
- experience_corporate: 0-2 (max 2)
- coherence_parcours: 0-2
- qualite_cv: 0-1
- certification: 0-1

REGLES METIER IMPORTANTES :
- Un CHEF D'AGENCE ou DIRECTEUR D'AGENCE gere necessairement une equipe et est expose au risque de credit. DONC : management=3, risque_credit=2 automatiquement.
- Un GESTIONNAIRE DE PORTEFEUILLE ou toute personne ayant gere un portefeuille de clients est expose au risque de credit. DONC : risque_credit=2 minimum, experience_corporate=2 minimum.
- La gestion d'un portefeuille de credit implique NECESSAIREMENT une exposition au risque de credit.

CRITERES ELIMINATOIRES :
{chr(10).join(f'- {c}' for c in grille.get('eliminatoire', []))}

Si un critere eliminatoire n'est PAS satisfait, le candidat est REJETE immediatement, mais le score total est conserve.

FORMAT DE SORTIE : UNIQUEMENT du JSON valide, sans texte explicatif.
Le JSON doit contenir:
{{
    "flags_eliminatoires": ["liste des criteres eliminatoires non satisfaits"],
    "points_forts": ["liste des points forts"],
    "points_vigilance": ["liste des points de vigilance"],
    "score_total": SOMME_DES_SOUS_SCORES (0-14),
    "synthese_recruteur": "synthese pour le recruteur",
    "sous_scores": {{
        "experience_bancaire": 0-3,
        "diplome": 0-3,
        "management": 0-3,
        "risque_credit": 0-2,
        "experience_corporate": 0-2,
        "coherence_parcours": 0-2,
        "qualite_cv": 0-1,
        "certification": 0-1
    }},
    "checklist": {{
        "experience_bancaire": true/false,
        "diplome_suffisant": true/false,
        "management_detecte": true/false,
        "risque_credit_detecte": true/false,
        "experience_corporate": true/false,
        "coherence_parcours": true/false
    }}
}}
NE METS PAS de texte avant ou apres le JSON. Reponds UNIQUEMENT avec le JSON."""

    def fmt_list(items):
        return "\n".join(f"  {i+1}. {c}" for i, c in enumerate(items)) if items else "  (aucun)"

    user_message = f"""POSTE : {poste}

=== GRILLE D'EVALUATION ===
CRITERES ELIMINATOIRES (rejet immediat si non valide) :
{fmt_list(grille.get('eliminatoire', []))}

POINTS A VERIFIER (qualite du profil) :
{fmt_list(grille.get('a_verifier', []))}

SIGNAUX FORTS (bonus, profil prioritaire) :
{fmt_list(grille.get('signaux_forts', []))}

POINTS D'ATTENTION (red flags a verifier) :
{fmt_list(grille.get('points_attention', []))}

=== DOCUMENTS DU CANDIDAT ===
CV :
{cv_text[:12000]}

LETTRE DE MOTIVATION :
{lettre_text[:3000] if lettre_text else '(Aucune lettre fournie)'}

ATTESTATIONS/CERTIFICATS :
{''.join(attestation_texts_list)[:3000] if attestation_texts_list else '(Aucune attestation)'}

=== INSTRUCTIONS ===
1. Analyse le CV comme un expert RH.
2. RAISONNE ETAPE PAR ETAPE avant de conclure.
3. Verifie CHAQUE critere eliminatoire.
4. Si UN SEUL critere eliminatoire n'est pas satisfait -> REJET, mais score_total conserve.
5. Donne des SCORES JUSTIFIES sur chaque critere selon la grille.
6. Le score_total est la SOMME des sous-scores.
7. Identifie les FORCES et FAIBLESSES du profil.
8. Produis une SYNTHESE claire et actionable pour le recruteur.
9. Utilise le format JSON attendu avec les sous-scores."""

    try:
        with _ia_semaphore:
            api_params = {
                "model": _MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
                "extra_headers": {
                    "HTTP-Referer": "https://recrutment.onrender.com",
                    "X-Title": "RecrutBank CV Analyzer"
                } if "OpenRouter" in _PROVIDER else {}
            }
            if OPENROUTER_REASONING_ENABLED and "OpenRouter" in _PROVIDER:
                api_params["extra_body"] = {"reasoning": {"enabled": True}}
                logger.info("🧠 Reasoning active pour l'analyse (via extra_body)")
            else:
                logger.info("🧠 Reasoning desactive pour ce modele")
            response = _client.chat.completions.create(**api_params)
        result_text = response.choices[0].message.content
        logger.info(f"✅ Analyse {_PROVIDER} terminee: {len(result_text)} caracteres")
        if hasattr(response.choices[0].message, 'reasoning_details') and response.choices[0].message.reasoning_details:
            logger.info("🧠 Reasoning details disponibles")
        analyse = parse_json_robust(result_text)
        if analyse is None:
            logger.warning("⚠️ Utilisation du fallback d'extraction JSON")
            analyse = extract_json_fallback(result_text)
        if analyse is None:
            logger.error("❌ Echec complet de l'extraction JSON, utilisation du fallback minimal")
            analyse = extract_json_fallback(cv_text[:1000])

        flags_elim = analyse.get('flags_eliminatoires', [])
        if isinstance(flags_elim, list):
            flags_elim = [f for f in flags_elim if f]
        else:
            flags_elim = []

        lm = analyse.get('lettre_motivation', {})
        if lm.get('eliminatoire', False):
            flags_elim.append(f"Lettre: {lm.get('commentaire', 'eliminatoire')}")

        sous_scores = analyse.get('sous_scores', {})
        # Appliquer les limites selon la grille
        for key, max_val in scores_max.items():
            if key in sous_scores:
                sous_scores[key] = min(max_val, sous_scores.get(key, 0))
            else:
                sous_scores[key] = 0

        score_total = int(analyse.get('score_total', sum(sous_scores.values())))
        score_max = get_score_max_for_poste(poste)
        if score_total > score_max:
            score_total = score_max

        decision = get_recommandation_from_score(score_total, poste)
        if flags_elim:
            decision = "Rejet - Critere(s) eliminatoire(s) non satisfait(s)"

        points_forts = analyse.get('points_forts', [])
        points_vigilance = analyse.get('points_vigilance', [])
        synthese = analyse.get('synthese_recruteur', '')

        if flags_elim:
            synthese = f"❌ REJET IMMEDIAT - {len(flags_elim)} critere(s) eliminatoire(s) non satisfait(s): " + ", ".join(flags_elim) + f". Score: {score_total}/{score_max}. " + synthese

        details = {
            'moteur': f'{_PROVIDER} v2',
            'model': _MODEL,
            'analyse_raw': analyse,
            'points_forts': points_forts,
            'points_vigilance': points_vigilance,
            'synthese_recruteur': synthese,
            'raisonnement_detaille': analyse.get('raisonnement', ''),
            'reasoning_enabled': OPENROUTER_REASONING_ENABLED,
            'json_parse_method': 'robust'
        }

        result = {
            'score': score_total,
            'score_max': score_max,
            'decision': decision,
            'flags_eliminatoires': flags_elim,
            'sous_scores': sous_scores,
            'checklist': analyse.get('checklist', {}),
            'signaux_detectes': analyse.get('signaux_detectes', []),
            'points_forts': points_forts,
            'points_vigilance': points_vigilance,
            'synthese': synthese,
            'details': details,
            'score_breakdown': {
                'score_final': score_total,
                'score_max': score_max,
                'decision': decision,
                'sous_scores': sous_scores
            }
        }

        return apply_business_rules(cv_text, lettre_text, attestation_texts_list, result)
    except Exception as e:
        logger.error(f"❌ Erreur analyse {_PROVIDER}: {e}")
        return None

def calculate_score_chef_division_corporate(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Division Local Corporate"
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att

    banking_years = detect_banking_experience_years(raw_full)

    # Détection des profils
    is_chef_agence = bool(re.search(r'chef d\'agence|chef d agence|directeur d\'agence|directeur d agence|responsable d\'agence|responsable d agence|manager d\'agence|manager d agence|agence manager|branch manager|agency manager|chef de centre|directeur de centre|responsable de centre|acting branch manager|profit center manager', cv_text, re.IGNORECASE))

    is_gestionnaire_portefeuille = bool(re.search(r'gestionnaire de portefeuille|portfolio manager|charge de portefeuille|portfolio officer|credit portfolio|gestionnaire de compte|account manager|relationship manager|chargé de clientèle|charge de clientele|analyste credit|analyste crédit|montage credit|montage crédit|instruction credit|instruction crédit', cv_text, re.IGNORECASE))

    has_portfolio_management = bool(re.search(r'gestion de portefeuille|portefeuille.*?client|portefeuille.*?credit|portefeuille.*?entreprise|suivi.*?portefeuille|portefeuille.*?sme|portefeuille.*?pme|portefeuille.*?local corporate|portefeuille.*?grandes entreprises', cv_text, re.IGNORECASE))

    has_local_corporate = bool(re.search(r'local corporate|sme|pme|petites et moyennes entreprises|pm|moyennes entreprises|grandes entreprises|entreprises|corporate local|local corporate banking|portefeuille.*?entreprise|gestion.*?portefeuille.*?client|acquisition.*?client|developpement.*?portefeuille', cv_text, re.IGNORECASE))

    flags_elim = []

    # Critères éliminatoires selon la grille
    if banking_years < 5:
        flags_elim.append("Moins de 5 ans d'experience bancaire (minimum 5 ans requis)")
    if banking_years < 1:
        flags_elim.append("Aucune experience dans le secteur bancaire ou financier reglemente")

    has_master = bool(re.search(r'master|mba|ingenieur|doctorat|phd', cv_text, re.IGNORECASE))
    has_bac4 = bool(re.search(r'bac\+[45]|bac [45]|maitrise|licence.*professionnelle', cv_text, re.IGNORECASE))
    if not (has_master or has_bac4):
        flags_elim.append("Niveau de diplome inferieur a Bac+4 (Master ou equivalent requis)")

    management_count = 0
    for kw in ['manager', 'directeur', 'chef', 'superviseur', 'encadrement', 'management', 'leadership', 'gestion d\'equipe', 'chef d\'agence', 'directeur d\'agence', 'responsable d\'agence', 'acting branch', 'profit center']:
        if kw in cv_text.lower():
            management_count += 1
    if is_chef_agence:
        management_count = max(management_count, 3)
    if management_count < 2:
        flags_elim.append("Aucune experience manageriale demontree (encadrement d'equipe requis)")

    credit_keywords = ['credit', 'credit', 'risque', 'risk', 'npl', 'provision', 'portefeuille', 'garantie', 'impaye', 'creances douteuses', 'creances impayees', 'non performing', 'cir', 'cout du risque']
    credit_count = 0
    for kw in credit_keywords:
        if kw in cv_text.lower():
            credit_count += 1
    if is_chef_agence or is_gestionnaire_portefeuille or has_portfolio_management:
        credit_count = max(credit_count, 2)
    if credit_count < 1 and not has_portfolio_management and not is_gestionnaire_portefeuille and not is_chef_agence:
        flags_elim.append("Aucune exposition a la gestion du risque de credit ou au suivi de la qualite d'un portefeuille (NPL, provisions)")

    local_corporate_keywords = ['local corporate', 'corporate', 'sme', 'pme', 'petites et moyennes entreprises', 'pm', 'moyennes entreprises', 'portefeuille.*?entreprise', 'gestion.*?portefeuille.*?client', 'acquisition.*?client']
    has_local_corporate_exp = any(kw in cv_text.lower() for kw in local_corporate_keywords)
    if not has_local_corporate_exp and not is_chef_agence and not is_gestionnaire_portefeuille and not has_portfolio_management:
        flags_elim.append("Aucune experience en gestion d'un portefeuille de clients SME/PME/Local Corporate")

    # Sous-scores selon la grille
    # experience_bancaire (0-3)
    exp_bancaire = 3 if banking_years >= 5 else (2 if banking_years >= 3 else (1 if banking_years >= 1 else 0))

    # diplome (0-3)
    diplome = 3 if has_master else (2 if has_bac4 else 0)

    # management (0-3)
    if is_chef_agence:
        management = 3
    elif management_count >= 3:
        management = 3
    elif management_count >= 2:
        management = 2
    elif management_count >= 1:
        management = 1
    else:
        management = 0

    # risque_credit (0-2 selon la grille)
    if is_chef_agence or is_gestionnaire_portefeuille or has_portfolio_management:
        risque_credit = 2
    elif credit_count >= 2:
        risque_credit = 2
    elif credit_count >= 1:
        risque_credit = 1
    else:
        risque_credit = 0

    # experience_corporate (0-2 selon la grille)
    if is_chef_agence or is_gestionnaire_portefeuille or has_portfolio_management:
        corporate_exp = 2
    elif has_local_corporate_exp:
        corporate_exp = 2
    elif re.search(r'commercial|relation client|client.*?entreprise', cv_text.lower()):
        corporate_exp = 1
    else:
        corporate_exp = 0

    # coherence (0-2)
    coherence = 2 if re.search(r'\d+\s*(?:ans|annees).*?(?:progression|evolution|promotion|carriere|responsabilites.*?croissantes)', cv_text.lower()) else (1 if re.search(r'\d+\s*(?:ans|annees).*?experience|poste.*?responsable|poste.*?chef', cv_text.lower()) else 0)

    # qualite_cv (0-1)
    qualite_cv = 1 if re.search(r'\d+\s*%|\d+\s*dossiers|\d+\s*rapports|\d+\s*millions|\d+\s*clients|chiffres|resultats|objectifs.*?atteints', cv_text.lower()) else 0

    # certification (0-1)
    certification = 1 if re.search(r'itb|moody|ecobank.*?certification|certification.*?bancaire|cemac|uemoa|tchad|frankfurt school|financement des pme', cv_text.lower()) else 0

    sous_scores = {
        "experience_bancaire": exp_bancaire,
        "diplome": diplome,
        "management": management,
        "risque_credit": risque_credit,
        "experience_corporate": corporate_exp,
        "coherence_parcours": coherence,
        "qualite_cv": qualite_cv,
        "certification": certification
    }

    score = sum(sous_scores.values())
    score = min(14, score)

    # Décision selon la grille
    if flags_elim:
        decision = "Rejet - Critere(s) eliminatoire(s) non satisfait(s)"
    elif score >= 11:
        decision = "Entretien prioritaire"
    elif score >= 7:
        decision = "Potentiel a evaluer en entretien"
    else:
        decision = "Rejet"

    points_forts = []
    points_vigilance = []

    if banking_years >= 5:
        points_forts.append(f"Plus de 5 ans d'experience bancaire ({banking_years:.1f} ans)")
    else:
        points_vigilance.append(f"Experience bancaire de {banking_years:.1f} ans (idealement 5 ans+)")

    if has_master:
        points_forts.append("Diplome Bac+5 ou superieur")
    elif has_bac4:
        points_forts.append("Diplome Bac+4")
    else:
        points_vigilance.append("Niveau de diplome inferieur a Bac+4")

    if management >= 2:
        points_forts.append(f"Experience manageriale (score: {management}/3)")
    else:
        points_vigilance.append("Experience manageriale a renforcer")

    if risque_credit >= 2:
        points_forts.append(f"Exposition au risque de credit / NPL (score: {risque_credit}/2)")
    else:
        points_vigilance.append("Exposition au risque de credit / NPL a verifier")

    if corporate_exp >= 2:
        points_forts.append(f"Experience en SME/Local Corporate (score: {corporate_exp}/2)")
    else:
        points_vigilance.append("Experience en SME/Local Corporate a renforcer")

    if is_chef_agence:
        points_forts.append("✅ Chef d'agence - Management et risque de credit confirmes")
    if is_gestionnaire_portefeuille:
        points_forts.append("✅ Gestionnaire de portefeuille - Risque de credit et relation client confirmes")
    if has_portfolio_management:
        points_forts.append("✅ Gestion de portefeuille detectee - Exposition au risque de credit confirmee")
    if has_local_corporate_exp:
        points_forts.append("✅ Experience Local Corporate/SME detectee")

    if coherence >= 1:
        points_forts.append("Parcours coherent avec progression")
    if qualite_cv >= 1:
        points_forts.append("CV detaille avec resultats chiffres")
    if certification >= 1:
        points_forts.append("Certification professionnelle ou connaissance du marche CEMAC/UEMOA")

    synthese = f"Candidat avec un score de {score}/14. "
    if flags_elim:
        synthese = f"❌ REJET IMMEDIAT - {len(flags_elim)} critere(s) eliminatoire(s) non satisfait(s): " + ", ".join(flags_elim) + f". Score: {score}/14. "
    elif "prioritaire" in decision:
        synthese += "Profil tres solide, recommande pour entretien prioritaire."
    else:
        synthese += "Profil a evaluer en entretien."

    result = {
        'score': score,
        'score_max': 14,
        'decision': decision,
        'flags_eliminatoires': flags_elim,
        'checklist': {
            'experience_bancaire': exp_bancaire >= 2,
            'diplome_suffisant': diplome >= 2,
            'management_detecte': management >= 2,
            'risque_credit_detecte': risque_credit >= 1,
            'experience_corporate': corporate_exp >= 1,
            'coherence_parcours': coherence >= 1
        },
        'sous_scores': sous_scores,
        'points_forts': points_forts,
        'points_vigilance': points_vigilance + flags_elim if flags_elim else points_vigilance,
        'synthese': synthese,
        'score_breakdown': {
            'score_final': score,
            'score_max': 14,
            'decision': decision,
            'sous_scores': sous_scores
        }
    }
    return apply_business_rules(cv_text, lettre_text, attestation_texts_list, result)

def calculate_score_charge_admin_credit(cv_text, lettre_text, attestation_texts_list):
    # ... (fonction existante, inchangée)
    pass

def calculate_score_chef_section_compensation(cv_text, lettre_text, attestation_texts_list):
    # ... (fonction existante, inchangée)
    pass

def calculate_score_data_analyst_finance(cv_text, lettre_text, attestation_texts_list):
    # ... (fonction existante, inchangée)
    pass

def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    # ... (fonction existante, inchangée)
    pass

KEYWORD_MAPPING = {
    "Experience bancaire": ["banque", "bancaire", "etablissement bancaire", "institution bancaire", "banque commerciale", "microfinance", "etablissement financier", "institution financiere", "secteur bancaire", "groupe bancaire", "filiale bancaire", "bank", "banking", "financial institution", "credit institution", "commercial bank", "ecobank", "orabank", "uba", "finadev", "ucec", "microfinance"],
    "Minimum 3 ans en credit / risque (hors stage)": ["EXP_CREDIT_3ANS"],
    "Minimum 1 an d'experience dans une fonction bancaire": ["EXP_BANK_1ANS"],
    "Minimum 3 ans en operations bancaires ou back-office (hors stage)": ["EXP_BACKOFFICE_3ANS"],
    "A une exposition au cycle de vie du credit bancaire": ["cycle de credit", "mise en place credit", "suivi credit", "garantie", "echeances credit", "credit administration", "administration de credit"],
    "A une connaissance des normes comptables bancaires ou de la reglementation COBAC": ["cobac", "reglementation bancaire", "ifrs 9", "normes ifrs", "comptabilite bancaire", "syscohada", "bale ii", "bale iii"],
    "Exposition au risque de credit / NPL": ["npl", "non performing", "creances douteuses", "creances impayees", "impayes", "provision", "risque de credit", "credit risk", "portefeuille", "portfolio", "cir", "cout du risque", "chef d'agence", "directeur d'agence", "gestionnaire de portefeuille", "gestionnaire de compte", "account manager", "relationship manager"],
    "Experience Local Corporate / SME": ["local corporate", "corporate", "sme", "pme", "petites et moyennes entreprises", "pm", "moyennes entreprises", "grandes entreprises", "entreprises", "corporate local", "local corporate banking", "chef d'agence", "directeur d'agence", "responsable d'agence", "gestionnaire de portefeuille"],
    "Management": ["manager", "directeur", "chef", "superviseur", "encadrement", "management", "leadership", "gestion d'equipe", "pilotage", "responsable", "chef d'agence", "directeur d'agence", "branch manager", "acting branch manager", "profit center manager"]
}

EXP_MIN_YEARS_MAP = {"EXP_CREDIT_3ANS": 3.0, "EXP_BANK_1ANS": 1.0, "EXP_BACKOFFICE_3ANS": 3.0}
DOMAIN_KEYWORDS_MAP = {"EXP_CREDIT_3ANS": ["credit", "risque", "banque", "bancaire", "institution financiere", "analyste", "charge", "gestionnaire", "loan", "credit analysis"], "EXP_BANK_1ANS": ["credit", "banque", "bancaire", "administration credit", "back office", "back-office", "risque", "risk", "analyse credit", "credit analysis", "loan", "institution financiere", "financial institution", "banking", "credit officer", "credit analyst", "credit administrator", "charge de credit", "gestionnaire credit", "analyste credit", "operations bancaires", "banking operations", "portfolio", "portefeuille", "garantie", "collateral"], "EXP_BACKOFFICE_3ANS": ["back-office", "back office", "operations bancaires", "compensation", "interbancaire", "banque", "bancaire", "middle office", "moyens de paiement", "traitement des operations", "chambre de compensation"]}

def check_criterion_match_advanced(criterion, normalized_text, raw_full_text="", tokens=None, poste=None):
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords:
        return False, 0.5, []

    exp_markers = [kw for kw in keywords if kw.startswith("EXP_")]
    if exp_markers:
        marker = exp_markers[0]
        min_years = EXP_MIN_YEARS_MAP.get(marker, 3.0)
        domain_kws = DOMAIN_KEYWORDS_MAP.get(marker, [])
        domain_kws_n = [normalize_for_matching(k)[0] for k in domain_kws]
        found = has_experience_years_strict(raw_full_text, min_years, domain_kws_n, poste)
        return found, 1.0 if found else 0.0, ([marker] if found else [])

    best_score = 0.0
    found_kws = []
    text_clean, text_tokens = normalize_for_matching(normalized_text)

    for kw in keywords:
        kw_clean, kw_tokens = normalize_for_matching(kw)
        if contains_negative_context(raw_full_text, kw):
            continue
        if kw_clean in text_clean:
            found_kws.append(kw)
            best_score = max(best_score, 1.0)
            continue
        if RAPIDFUZZ_AVAILABLE and len(kw_clean) >= 4:
            ratio = fuzz.partial_ratio(kw_clean, text_clean)
            if ratio >= 75:
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}~{ratio/100:.2f}")
                    best_score = max(best_score, ratio / 100)
                continue
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.5):
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                    best_score = max(best_score, len(common) / len(kw_tokens))

    threshold = 0.45 if len(normalized_text) < 500 else 0.55
    return best_score >= threshold, round(best_score, 2), found_kws

def has_experience_years_strict(full_raw_text, min_years, domain_keywords=None, poste=None):
    blocks = split_into_jobs(full_raw_text)
    total_years = 0.0
    years_patterns = [r'(\d+)\s*(?:annees?|ans?)', r'plus\s+de\s+(\d+)\s*(?:annees?|ans?)', r'\(\s*(\d+)\s*\)\s*(?:annees?|ans?)', r'\w+\s+\(\s*(\d+)\s*\)\s*(?:annees?|ans?)', r'depuis\s+(?:plus\s+de\s+)?(\d+)\s*(?:annees?|ans?)', r'(\d+)\s*(?:annees?|ans?)\s+(?:d[ée]?experience|dans|en|de)', r'experience\s+(?:de\s+)?(\d+)\s*(?:annees?|ans?)']
    text_lower = full_raw_text.lower()
    for pattern in years_patterns:
        matches = re.findall(pattern, text_lower, re.IGNORECASE)
        for match in matches:
            try:
                years = float(match)
                if years >= min_years:
                    return True
            except (ValueError, TypeError):
                continue

    banking_posts = ["Responsable Administration de Credit", "Analyste Credit CCB", "Senior Finance Officer", "Market Risk Officer", "Charge(e) d'Administration de Credit"]
    for block in blocks:
        if is_stage_block(block):
            continue
        if poste in banking_posts:
            if detect_institution_type(block) == 'unknown':
                continue
        duration = extract_duration_years_from_block(block)
        if duration > 0:
            total_years += duration
    return total_years >= min_years

def analyze_cv_intelligent(cv_text, lettre_text, attestation_texts_list, poste):
    # ... (fonction existante, inchangée)
    pass

def get_display_status(c):
    statut = c.get('statut', 'en_attente')
    if statut == "rejete":
        return "rejete"
    if statut == "retenu":
        return "retenu"
    if statut == "entretien":
        return "entretien"
    flags = c.get('flags_eliminatoires_parsed', [])
    if flags and len(flags) > 0:
        return "rejete"
    decision = c.get('decision', '')
    if decision:
        if "Entretien prioritaire" in decision or "Shortlist" in decision:
            return "retenu"
        elif "Potentiel" in decision or "considerer" in decision or "Faible" in decision:
            return "entretien"
        else:
            return "rejete"
    return 'en_attente'

def generate_detailed_reason(candidat, poste, score, score_max):
    statut = candidat.get('statut', 'en_attente')
    details = candidat.get('analyse_details_parsed', {})
    flags = candidat.get('flags_eliminatoires_parsed', [])
    strengths = details.get('points_forts', [])
    weaknesses = details.get('points_vigilance', [])
    sous_scores = candidat.get('score_breakdown_parsed', {}).get('sous_scores', {})
    note = candidat.get('note', '')
    decision_auto = get_recommandation_from_score(score, poste)

    if flags and len(flags) > 0:
        lines = ["❌ CRITERES ELIMINATOIRES NON SATISFAITS :"]
        for flag in flags[:5]:
            clean = str(flag).replace('', '').replace('', '').strip()
            if clean and len(clean) > 3:
                lines.append(f"  • {clean}")
        if len(flags) > 5:
            lines.append(f"  • +{len(flags)-5} autre(s)")
        lines.append(f"\n📊 Score conserve: {score}/{score_max}")
        if note and "Decision" not in note and len(note) > 5:
            lines.append(f"\nNOTE RECRUTEUR : {note}")
        return "\n".join(lines)

    if statut == "retenu":
        if strengths:
            lines = ["✅ POINTS FORTS :"]
            for s in strengths[:4]:
                lines.append(f"  • {s}")
            if sous_scores:
                for key, value in sous_scores.items():
                    if value > 0:
                        lines.append(f"  • {key}: {value}/3")
            if note and "Decision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if note and "Decision" not in note and len(note) > 5:
            return f"✅ RETENU - {note}"
        return "✅ RETENU - Candidature retenue"

    if statut == "entretien":
        lines = ["🔄 POTENTIEL A EVALUER :"]
        if strengths:
            for s in strengths[:2]:
                lines.append(f"  • {s}")
        if weaknesses:
            lines.append("Points a verifier :")
            for w in weaknesses[:2]:
                lines.append(f"  • {w}")
        if note and "Decision" not in note and len(note) > 5:
            lines.append(f"\nNOTE RECRUTEUR : {note}")
        return "\n".join(lines)

    if statut == "rejete":
        if weaknesses:
            lines = ["❌ POINTS DE VIGILANCE :"]
            for w in weaknesses[:4]:
                lines.append(f"  • {w}")
            if note and "Decision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if note and "Decision" not in note and len(note) > 5:
            return f"❌ REJETE - {note}"
        if score == 0:
            return "❌ REJETE - Analyse automatique : le candidat ne repond pas aux criteres eliminatoires du poste"
        if score < 7:
            return f"❌ REJETE - Score insuffisant ({score}/{score_max}) - Profil ne correspond pas aux exigences du poste"
        return "❌ REJETE - Profil ne correspond pas aux exigences du poste"
    else:
        if flags:
            lines = ["❌ CRITERES ELIMINATOIRES :"]
            for flag in flags[:3]:
                clean = str(flag).replace('', '').replace('', '').strip()
                if clean and len(clean) > 5:
                    lines.append(f"  • {clean}")
            lines.append(f"\n📊 Score conserve: {score}/{score_max}")
            return "\n".join(lines)
        if "Entretien prioritaire" in decision_auto or "Shortlist" in decision_auto:
            lines = ["✅ PROFIL RECOMMANDE :"]
            if strengths:
                for s in strengths[:4]:
                    lines.append(f"  • {s}")
            if sous_scores:
                for key, value in sous_scores.items():
                    if value > 0:
                        lines.append(f"  • {key}: {value}/3")
            return "\n".join(lines)
        elif "Potentiel" in decision_auto:
            lines = ["🔄 POTENTIEL A EVALUER :"]
            if strengths:
                for s in strengths[:2]:
                    lines.append(f"  • {s}")
            if weaknesses:
                lines.append("Points de vigilance :")
                for w in weaknesses[:2]:
                    lines.append(f"  • {w}")
            return "\n".join(lines)
        else:
            lines = ["❌ NON RETENU - Raisons :"]
            if weaknesses:
                for w in weaknesses[:3]:
                    lines.append(f"  • {w}")
            if not weaknesses and not flags:
                lines.append("  • Profil ne correspond pas aux exigences du poste")
            return "\n".join(lines)

def generate_excel_report_enhanced(candidats_data, poste_filter=None):
    # ... (fonction existante, inchangée)
    pass

def generate_pdf_report_enhanced(candidats_data, poste_filter=None):
    # ... (fonction existante, inchangée)
    pass

def generate_csv_report(candidats_data, poste_filter=None):
    # ... (fonction existante, inchangée)
    pass

def generate_word_report(candidats_data, poste_filter=None):
    # ... (fonction existante, inchangée)
    pass

def _save_error(token, error_message, statut="rejete"):
    if supabase:
        try:
            supabase.table('candidats').update({"score": "0", "decision": f"Rejet - {error_message}", "statut": statut, "analyse_status": "error", "analyse_error": error_message, "analyse_auto_date": datetime.datetime.now().isoformat(), "analyse_details": json.dumps({"erreur": error_message, "moteur": "verification_cv"}, ensure_ascii=False)}).eq('token', token).execute()
        except Exception as e:
            logger.error(f"Erreur sauvegarde: {e}")

def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste, force=False):
    try:
        if not force and not is_poste_actif(poste):
            logger.info(f"Analyse ignoree pour {token} — poste cloture : {poste}")
            if supabase:
                supabase.table('candidats').update({"analyse_status": "skipped_closed_post", "analyse_auto_date": datetime.datetime.now().isoformat(), "analyse_skip_reason": f"Poste cloture : {poste}"}).eq('token', token).execute()
            return

        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []

        cv_text = ""
        if cv_filename:
            cv_bytes = download_file_from_supabase_robust(cv_filename)
            if cv_bytes:
                cv_text = extract_text_robust_from_bytes(cv_bytes, cv_filename)
                if len(cv_text) > MAX_TEXT_SIZE:
                    cv_text = cv_text[:MAX_TEXT_SIZE]
                logger.info(f"CV extrait pour {token}: {len(cv_text)} caracteres")
                if len(cv_text) < 50:
                    logger.warning(f"CV {cv_filename} tres court ({len(cv_text)} caracteres)")

        lm_text = ""
        if lettre_filename:
            lm_bytes = download_file_from_supabase_robust(lettre_filename)
            if lm_bytes:
                lm_text = extract_text_robust_from_bytes(lm_bytes, lettre_filename)
                if len(lm_text) > MAX_TEXT_SIZE:
                    lm_text = lm_text[:MAX_TEXT_SIZE]
                logger.info(f"Lettre extraite pour {token}: {len(lm_text)} caracteres")

        att_texts = []
        for fn in (attestation_filenames or []):
            if fn:
                att_bytes = download_file_from_supabase_robust(fn)
                if att_bytes:
                    t = extract_text_robust_from_bytes(att_bytes, fn)
                    if t and len(t) > MAX_TEXT_SIZE:
                        t = t[:MAX_TEXT_SIZE]
                    if t:
                        att_texts.append(t)

        if not cv_text or len(cv_text.strip()) < 30:
            logger.warning(f"CV manquant ou vide pour {token}")
            _save_error(token, "CV manquant ou vide", "rejete")
            return

        if lm_text and len(lm_text.strip()) > 50 and cv_text:
            cv_clean = re.sub(r'\s+', ' ', cv_text.strip().lower())
            lm_clean = re.sub(r'\s+', ' ', lm_text.strip().lower())
            if len(cv_clean) > 100 and len(lm_clean) > 100:
                cv_words = set(cv_clean.split())
                lm_words = set(lm_clean.split())
                if cv_words and lm_words:
                    common = len(cv_words & lm_words)
                    similarity = common / max(len(cv_words), len(lm_words))
                    if similarity > 0.85:
                        logger.warning(f"CV et lettre identiques pour {token} (similarite: {similarity:.0%})")
                        lm_text = ""
                        supabase.table('candidats').update({"note": "Attention: Le CV et la lettre de motivation sont identiques. Une lettre personnalisee est attendue."}).eq('token', token).execute()

        logger.info(f"Analyse pour {token} - poste: {poste}, CV: {len(cv_text)} caracteres")
        gc.collect()

        if IA_ANALYSE_ACTIVE and poste in GRILLE:
            logger.info(f"🚀 Utilisation de {_PROVIDER} pour l'analyse du poste: {poste}")
            result = analyze_cv_with_ia_reasoning(cv_text, lm_text, att_texts, poste)
            if result:
                logger.info(f"✅ Analyse {_PROVIDER} reussie pour {token} - Score: {result.get('score', 0)}/{result.get('score_max', 0)}")
            else:
                logger.warning(f"⚠️ {_PROVIDER} a echoue, fallback vers scoring specifique pour {poste}")
                result = None
        else:
            logger.info(f"📌 Fallback vers scoring specifique pour {poste}")
            result = None

        if result is None:
            if poste == "Charge(e) d'Administration de Credit":
                result = calculate_score_charge_admin_credit(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/12 - {result.get('decision', 'Inconnu')}")
            elif poste == "Chef de Section Compensation":
                result = calculate_score_chef_section_compensation(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/12 - {result.get('decision', 'Inconnu')}")
            elif poste == "Chef de Division Local Corporate":
                logger.info(f"Appel du scoring specifique pour {poste}")
                result = calculate_score_chef_division_corporate(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/14 - {result.get('decision', 'Inconnu')}")
            elif poste == "Data Analyst Finance":
                result = calculate_score_data_analyst_finance(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/14 - {result.get('decision', 'Inconnu')}")
            else:
                result = analyze_cv_intelligent(cv_text, lm_text, att_texts, poste)
                if result is None:
                    logger.info(f"Fallback vers analyse_grille pour {poste}")
                    result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/10 - {result.get('decision', 'Inconnu')}")

        score = result.get('score', 0)
        score_max = result.get('score_max', get_score_max_for_poste(poste))
        decision = result.get('decision') or get_recommandation_from_score(score, poste)
        flags_elim = result.get('flags_eliminatoires', [])
        statut = get_statut_from_decision(decision, flags_elim)

        if score > score_max:
            score = score_max

        details = result.get('details', {})
        details['points_forts'] = result.get('points_forts', [])
        details['points_vigilance'] = result.get('points_vigilance', [])
        details['synthese_recruteur'] = result.get('synthese', '')
        details['moteur'] = _PROVIDER if IA_ANALYSE_ACTIVE else 'scoring_specifique_v2'

        sous_scores = result.get('sous_scores', {})
        if not sous_scores:
            sous_scores = {
                "experience_bancaire": 0,
                "diplome": 0,
                "management": 0,
                "risque_credit": 0,
                "experience_corporate": 0,
                "coherence_parcours": 0,
                "qualite_cv": 0,
                "certification": 0
            }

        score_breakdown = {'score_final': score, 'score_max': score_max, 'decision': decision, 'moteur_analyse': details['moteur'], 'sous_scores': sous_scores}

        if supabase:
            update_data = {"score": str(score), "decision": decision, "statut": statut, "analyse_status": "completed", "analyse_auto_date": datetime.datetime.now().isoformat()}
            if result.get('checklist'):
                update_data["checklist"] = json.dumps(result.get('checklist', {}), ensure_ascii=False)
            if flags_elim:
                update_data["flags_eliminatoires"] = json.dumps(flags_elim, ensure_ascii=False)
            if result.get('signaux_detectes'):
                update_data["signaux_detectes"] = json.dumps(result.get('signaux_detectes', []), ensure_ascii=False)
            update_data["analyse_details"] = json.dumps(details, ensure_ascii=False)
            update_data["score_breakdown"] = json.dumps(score_breakdown, ensure_ascii=False)
            supabase.table('candidats').update(update_data).eq('token', token).execute()
            logger.info(f"[{decision}] Score {token}: {score}/{score_max} → statut: {statut} (flags: {len(flags_elim)})")

        del cv_text, lm_text, att_texts, result
        gc.collect()
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Erreur analyse {token}: {str(e)}")
        if supabase:
            try:
                supabase.table('candidats').update({"analyse_status": "error", "analyse_error": str(e), "analyse_auto_date": datetime.datetime.now().isoformat()}).eq('token', token).execute()
            except:
                pass

# Routes (inchangées)
@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify({"postes": POSTES, "postes_actifs": POSTES_ACTIFS, "postes_clotures": POSTES_CLOTURES}), 200

@app.route('/api/postes/actifs', methods=['GET'])
def get_postes_actifs():
    return jsonify(POSTES_ACTIFS), 200

@app.route('/api/grille/<poste>', methods=['GET'])
def get_grille(poste):
    g = GRILLE.get(poste)
    if not g:
        return jsonify({'error': 'Poste inconnu', 'postes_disponibles': list(GRILLE.keys())}), 404
    return jsonify(g), 200

@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return '', 204
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    hashed_password = hash_pwd(password)
    if supabase:
        try:
            response = supabase.table('recruteurs').select('*').eq('email', email).execute()
            if response.data and len(response.data) > 0:
                recruteur = response.data[0]
                if recruteur.get('password') == hashed_password:
                    access_token = create_access_token(identity=str(recruteur['id']))
                    return jsonify({'token': access_token, 'nom': recruteur.get('nom', 'Recruteur'), 'email': recruteur.get('email', email)}), 200
        except Exception as e:
            logger.error(f"Erreur login: {e}")
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom = (request.form.get('nom') or '').strip()
        prenom = (request.form.get('prenom') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        telephone = (request.form.get('telephone') or '').strip()
        poste = (request.form.get('poste') or '').strip()
        if not nom or not prenom or not email or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400
        if supabase:
            existing = supabase.table('candidats').select('*').eq('email', email).eq('poste', poste).execute()
            if existing.data and len(existing.data) > 0:
                return jsonify({'error': f'Vous avez deja soumis une candidature pour le poste "{poste}".'}), 409
            all_candidats = supabase.table('candidats').select('numero_dossier').eq('poste', poste).execute()
            max_num = 0
            for c in all_candidats.data:
                existing_num = c.get('numero_dossier', '')
                if existing_num:
                    try:
                        num_val = int(existing_num)
                        if num_val > max_num:
                            max_num = num_val
                    except ValueError:
                        pass
            new_num = max_num + 1
            numero_dossier = str(new_num)

        def save_file_to_supabase(field, suffix):
            f = request.files.get(field)
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_{suffix}.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                return result if result else ''
            return ''

        cv_filename = save_file_to_supabase('cv', 'cv')
        if request.files.get('cv') and not cv_filename:
            return jsonify({'error': "Echec de l'envoi du CV, merci de reessayer."}), 500

        lettre_filename = save_file_to_supabase('lettre', 'lettre')
        if request.files.get('lettre') and not lettre_filename:
            return jsonify({'error': "Echec de l'envoi de la lettre de motivation, merci de reessayer."}), 500

        att_filenames = []
        for f in request.files.getlist('attestation'):
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_attestation.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                if result:
                    att_filenames.append(blob_name)

        token = uuid.uuid4().hex
        supabase.table('candidats').insert({"token": token, "nom": nom, "prenom": prenom, "email": email, "telephone": telephone, "poste": poste, "numero_dossier": numero_dossier, "cv_filename": cv_filename, "lettre_filename": lettre_filename, "attestation_filenames": json.dumps(att_filenames, ensure_ascii=False), "statut": "en_attente", "note": "", "score": "0", "checklist": "", "flags_eliminatoires": "", "signaux_detectes": "", "score_breakdown": "", "analyse_status": "pending", "date_candidature": datetime.datetime.now().isoformat()}).execute()

        if is_poste_actif(poste):
            threading.Thread(target=run_analysis_for_candidat, args=(token, cv_filename, lettre_filename, att_filenames, poste, False), daemon=True).start()
            analyse_msg = f'Analyse automatique en cours avec {_PROVIDER}'
        else:
            analyse_msg = 'Poste cloture — candidature enregistree sans analyse'
            supabase.table('candidats').update({"analyse_status": "closed_post_no_analysis", "analyse_auto_date": datetime.datetime.now().isoformat()}).eq('token', token).execute()

        nom_complet = f"{prenom} {nom}".strip()
        sujet_confirmation = f"Confirmation de candidature – {poste}"
        corps_confirmation = f"Bonjour {nom_complet},\nNous accusons reception de votre candidature.\nSans reponse de notre part sous deux (2) semaines, veuillez considerer que votre candidature n'a pas ete retenue.\nPour toute information : contact@cdotchad.com.\nCordialement,"
        threading.Thread(target=send_email, args=(email, sujet_confirmation, corps_confirmation), daemon=True).start()

        return jsonify({'message': 'Candidature soumise avec succes', 'token': token, 'numero_dossier': numero_dossier, 'analyse': analyse_msg, 'poste_statut': 'actif' if is_poste_actif(poste) else 'cloture', 'ia_engine': _PROVIDER}), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    if supabase:
        response = supabase.table('candidats').select('*').eq('token', token).execute()
        if response.data and len(response.data) > 0:
            data = response.data[0]
            hidden = {'cv_filename', 'lettre_filename', 'attestation_filenames', 'checklist', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details', 'score_breakdown'}
            return jsonify({k: v for k, v in data.items() if k not in hidden}), 200
    return jsonify({'error': 'Candidature introuvable'}), 404

@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    keys = response.data if response.data else []
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0, "rejete": 0, "entretien": 0, "by_poste": []}
    counts = {}
    for c in keys:
        statut = c.get('statut', 'en_attente')
        if statut in stats:
            stats[statut] += 1
        else:
            stats['en_attente'] += 1
        p = c.get('poste', 'Inconnu')
        counts[p] = counts.get(p, 0) + 1
    stats['by_poste'] = [{'poste': p, 'n': n} for p, n in sorted(counts.items(), key=lambda x: -x[1])]
    return jsonify(stats), 200

@app.route('/api/recruteur/postes/stats', methods=['GET'])
@jwt_required()
def get_postes_stats():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    keys = response.data if response.data else []
    actifs_count = 0
    clotures_count = 0
    par_poste_actif = {}
    par_poste_cloture = {}
    for c in keys:
        poste = c.get('poste', '')
        if poste in POSTES_ACTIFS:
            actifs_count += 1
            par_poste_actif[poste] = par_poste_actif.get(poste, 0) + 1
        else:
            clotures_count += 1
            par_poste_cloture[poste] = par_poste_cloture.get(poste, 0) + 1
    return jsonify({'total': len(keys), 'postes_actifs': {'count': actifs_count, 'liste': POSTES_ACTIFS, 'par_poste': par_poste_actif, 'eligible_reanalyse': True}, 'postes_clotures': {'count': clotures_count, 'liste': POSTES_CLOTURES, 'par_poste': par_poste_cloture, 'eligible_reanalyse': False}}), 200

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    poste_filter = request.args.get('poste', '')
    statut_filter = request.args.get('statut', '')
    search = request.args.get('search', '').lower()
    min_score = request.args.get('min_score', type=int)
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    all_candidats = response.data if response.data else []
    result = []
    for c in all_candidats:
        c['id'] = c.get('token', '')
        if poste_filter and c.get('poste') != poste_filter:
            continue
        if statut_filter:
            statut = c.get('statut', 'en_attente')
            if statut != statut_filter:
                continue
        if min_score is not None and int(c.get('score', 0)) < min_score:
            continue
        if search:
            hay = (f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} {c.get('poste','')} {c.get('numero_dossier','')}").lower()
            if search not in hay:
                continue
        for field in ['score_breakdown', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details']:
            if c.get(field):
                try:
                    c[f'{field}_parsed'] = json.loads(c[field])
                except Exception:
                    pass
        result.append(c)
    result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
    return jsonify(result), 200

@app.route('/api/recruteur/candidats/<token>', methods=['GET'])
@jwt_required()
def get_candidat_detail(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    data['id'] = token
    if data.get('attestation_filenames'):
        try:
            data['attestation_filenames_parsed'] = json.loads(data['attestation_filenames'])
        except Exception:
            data['attestation_filenames_parsed'] = []
    for field in ['checklist', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details', 'score_breakdown']:
        if data.get(field):
            try:
                data[f'{field}_parsed'] = json.loads(data[field])
            except Exception:
                pass
    return jsonify(data), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = request.get_json(silent=True) or {}
    statut = data.get('statut', 'en_attente')
    note = data.get('note', '')
    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    update_data = {"statut": statut, "note": note, "decision_date": datetime.datetime.now().isoformat(), "decided_by": get_jwt_identity()}
    if statut == "rejete":
        update_data["decision"] = "Rejet - Decision du recruteur"
    elif statut == "retenu":
        update_data["decision"] = "Retenu - Decision du recruteur"
    elif statut == "entretien":
        update_data["decision"] = "Entretien - Decision du recruteur"
    supabase.table('candidats').update(update_data).eq('token', token).execute()
    return jsonify({'message': 'Mis a jour avec succes', 'statut': statut}), 200

@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    cv_fn = data.get('cv_filename')
    lm_fn = data.get('lettre_filename')
    att_raw = data.get('attestation_filenames', '[]')
    poste = data.get('poste')
    if not cv_fn:
        return jsonify({'error': 'CV manquant pour analyse'}), 400
    force = request.args.get('force', '0') == '1'
    if not force and not is_poste_actif(poste):
        return jsonify({'error': f'Le poste "{poste}" est cloture. Utilisez ?force=1 pour forcer l\'analyse.', 'poste': poste, 'statut': 'cloture'}), 403
    supabase.table('candidats').update({"analyse_status": "pending", "analyse_manual_trigger": datetime.datetime.now().isoformat()}).eq('token', token).execute()
    threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste, force), daemon=True).start()
    return jsonify({'message': f'Analyse re-declenchee avec {_PROVIDER}', 'token': token, 'ia_engine': _PROVIDER}), 202

@app.route('/api/recruteur/reanalyze-status', methods=['GET'])
@jwt_required()
def get_reanalyze_status():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('token, poste, analyse_status, analyse_auto_date').execute()
        keys = response.data if response.data else []
        active_candidates = [d for d in keys if d.get('poste') in POSTES_ACTIFS]
        total = len(active_candidates)
        status_counts = {'pending': 0, 'reanalyzing': 0, 'completed': 0, 'error': 0, 'skipped_closed_post': 0, 'closed_post_no_analysis': 0, 'reanalyzing_auto': 0}
        in_progress = False
        for data in active_candidates:
            status = data.get('analyse_status', 'pending')
            if status in status_counts:
                status_counts[status] += 1
            if status in ('reanalyzing', 'pending'):
                in_progress = True
        processed = status_counts.get('completed', 0) + status_counts.get('error', 0)
        return jsonify({'total': total, 'processed': processed, 'in_progress': in_progress, 'status_counts': status_counts, 'postes_concernes': POSTES_ACTIFS, 'timestamp': datetime.datetime.now().isoformat()}), 200
    except Exception as e:
        logger.error(f"Erreur reanalyze-status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-one/<token>', methods=['POST'])
@jwt_required()
def reanalyze_one_candidate(token):
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').eq('token', token).execute()
        if not response.data or len(response.data) == 0:
            return jsonify({'error': 'Candidat introuvable'}), 404
        data = response.data[0]
        poste = data.get('poste')
        if not is_poste_actif(poste):
            return jsonify({'error': f'Le poste "{poste}" est cloture. Reanalyse desactivee.', 'poste': poste, 'statut': 'cloture'}), 403
        cv_fn = data.get('cv_filename')
        if not cv_fn:
            return jsonify({'error': 'CV manquant pour analyse'}), 400
        lm_fn = data.get('lettre_filename')
        att_raw = data.get('attestation_filenames', '[]')
        supabase.table('candidats').update({"analyse_status": "reanalyzing", "reanalyze_trigger": datetime.datetime.now().isoformat(), "reanalyze_reason": "Reanalyse manuelle (un seul candidat)"}).eq('token', token).execute()
        threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste, True), daemon=True).start()
        return jsonify({'message': 'Reanalyse lancee', 'token': token, 'poste': poste}), 202
    except Exception as e:
        logger.error(f"Erreur reanalyze-one: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-all', methods=['POST'])
@jwt_required()
def reanalyze_all_candidates():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        keys = response.data if response.data else []
        if not keys:
            return jsonify({'message': 'Aucune candidature a reanalyser'}), 200
        candidates_to_reanalyze = [data for data in keys if data.get('poste') in POSTES_ACTIFS and data.get('cv_filename')]
        candidates_skipped = len(keys) - len(candidates_to_reanalyze)
        if not candidates_to_reanalyze:
            return jsonify({'message': 'Aucun candidat sur poste actif avec CV a reanalyser', 'skipped_closed_posts': candidates_skipped}), 200
        now_iso = datetime.datetime.now().isoformat()
        for c in candidates_to_reanalyze:
            try:
                supabase.table('candidats').update({"analyse_status": "reanalyzing", "reanalyze_trigger": now_iso, "reanalyze_reason": "Reanalyse parallellisee (postes actifs)"}).eq('token', c.get('token')).execute()
            except Exception:
                pass

        def analyze_one(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                poste = data.get('poste')
                if not cv_fn:
                    return (token, False, "CV manquant")
                run_analysis_for_candidat(token, cv_fn, lm_fn, att_raw, poste, False)
                return (token, True, "OK")
            except Exception as e:
                return (data.get('token'), False, str(e))

        MAX_WORKERS = min(8, len(candidates_to_reanalyze))
        logger.info(f"Reanalyse parallele : {len(candidates_to_reanalyze)} candidats, {MAX_WORKERS} workers")
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(analyze_one, c): c for c in candidates_to_reanalyze}
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=300)
                    if success:
                        reanalyzed_count += 1
                    else:
                        errors.append(f"Token {token}: {msg}")
                except Exception as e:
                    errors.append(f"Timeout ou erreur: {str(e)}")
        elapsed = time.time() - start_time
        gc.collect()
        return jsonify({'message': f'Reanalyse terminee en {elapsed:.1f}s', 'reanalyzed_count': reanalyzed_count, 'total_candidates': len(candidates_to_reanalyze), 'skipped_closed_posts': candidates_skipped, 'workers_used': MAX_WORKERS, 'elapsed_seconds': round(elapsed, 1), 'errors': errors[:10]}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-poste/<poste>', methods=['POST'])
@jwt_required()
def reanalyze_by_poste(poste):
    if poste not in POSTES:
        return jsonify({'error': f'Poste inconnu: {poste}'}), 400
    if not is_poste_actif(poste):
        return jsonify({'error': f'Le poste "{poste}" est cloture. Reanalyse desactivee.', 'poste': poste, 'statut': 'cloture', 'postes_actifs': POSTES_ACTIFS}), 403
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').eq('poste', poste).execute()
        keys = response.data if response.data else []
        if not keys:
            return jsonify({'message': f'Aucune candidature pour le poste "{poste}"'}), 200
        candidates_with_cv = [k for k in keys if k.get('cv_filename')]
        if not candidates_with_cv:
            return jsonify({'message': f'Aucun CV trouve pour le poste "{poste}"'}), 200
        now_iso = datetime.datetime.now().isoformat()
        for data in candidates_with_cv:
            try:
                supabase.table('candidats').update({"analyse_status": "reanalyzing", "reanalyze_trigger": now_iso, "reanalyze_reason": f"Reanalyse manuelle parallele : {poste}"}).eq('token', data.get('token')).execute()
            except Exception:
                pass

        def analyze_one(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                if not cv_fn:
                    return (token, False, "CV manquant")
                run_analysis_for_candidat(token, cv_fn, lm_fn, att_raw, poste, True)
                return (token, True, "OK")
            except Exception as e:
                return (data.get('token'), False, str(e))

        MAX_WORKERS = min(8, len(candidates_with_cv))
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(analyze_one, c) for c in candidates_with_cv]
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=300)
                    if success:
                        reanalyzed_count += 1
                    else:
                        errors.append(f"Token {token}: {msg}")
                except Exception as e:
                    errors.append(f"Erreur: {str(e)}")
        elapsed = time.time() - start_time
        gc.collect()
        return jsonify({'message': f'Reanalyse terminee pour le poste "{poste}"', 'poste': poste, 'statut': 'actif', 'reanalyzed_count': reanalyzed_count, 'total_candidates': len(candidates_with_cv), 'workers_used': MAX_WORKERS, 'elapsed_seconds': round(elapsed, 1), 'errors': errors[:10]}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-fast', methods=['POST'])
@jwt_required()
def reanalyze_fast():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        keys = response.data if response.data else []
        candidates = [d for d in keys if d.get('poste') in POSTES_ACTIFS and d.get('cv_filename')]
        if not candidates:
            return jsonify({'message': 'Aucun candidat actif avec CV'}), 200

        def analyze_fast_only(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                poste = data.get('poste')
                cv_text = ""
                if cv_fn:
                    cv_bytes = download_file_from_supabase_robust(cv_fn)
                    if cv_bytes:
                        cv_text = extract_text_robust_from_bytes(cv_bytes, cv_fn)
                lm_text = ""
                if lm_fn:
                    lm_bytes = download_file_from_supabase_robust(lm_fn)
                    if lm_bytes:
                        lm_text = extract_text_robust_from_bytes(lm_bytes, lm_fn)
                att_texts = []
                if isinstance(att_raw, str):
                    try:
                        att_list = json.loads(att_raw) if att_raw else []
                    except:
                        att_list = []
                else:
                    att_list = att_raw or []
                for fn in att_list:
                    if fn:
                        att_bytes = download_file_from_supabase_robust(fn)
                        if att_bytes:
                            t = extract_text_robust_from_bytes(att_bytes, fn)
                            if t:
                                att_texts.append(t)
                if poste == "Charge(e) d'Administration de Credit":
                    result = calculate_score_charge_admin_credit(cv_text, lm_text, att_texts)
                elif poste == "Chef de Section Compensation":
                    result = calculate_score_chef_section_compensation(cv_text, lm_text, att_texts)
                elif poste == "Chef de Division Local Corporate":
                    result = calculate_score_chef_division_corporate(cv_text, lm_text, att_texts)
                elif poste == "Data Analyst Finance":
                    result = calculate_score_data_analyst_finance(cv_text, lm_text, att_texts)
                else:
                    result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
                if supabase:
                    supabase.table('candidats').update({"score": str(result.get('score', 0)), "checklist": json.dumps(result.get('checklist', {}), ensure_ascii=False), "flags_eliminatoires": json.dumps(result.get('flags_eliminatoires', []), ensure_ascii=False), "signaux_detectes": json.dumps(result.get('signaux_detectes', []), ensure_ascii=False), "analyse_details": json.dumps(result.get('details', {}), ensure_ascii=False), "score_breakdown": json.dumps(result.get('score_breakdown', {}), ensure_ascii=False), "analyse_auto_date": datetime.datetime.now().isoformat(), "analyse_status": "completed"}).eq('token', token).execute()
                return (token, True, result.get('score', 0))
            except Exception as e:
                return (data.get('token'), False, str(e))

        start = time.time()
        success_count = 0
        errors = []
        MAX_WORKERS = min(8, len(candidates))
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(analyze_fast_only, c) for c in candidates]
            for future in as_completed(futures):
                try:
                    token, ok, msg = future.result(timeout=120)
                    if ok:
                        success_count += 1
                    else:
                        errors.append(f"{token}: {msg}")
                except Exception as e:
                    errors.append(str(e))
        elapsed = time.time() - start
        gc.collect()
        return jsonify({'message': f'Reanalyse eclair terminee en {elapsed:.1f}s', 'success': success_count, 'total': len(candidates), 'elapsed_seconds': round(elapsed, 1), 'speed_per_candidate': round(elapsed / max(1, success_count), 2), 'errors': errors[:10]}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/cleanup-closed', methods=['POST'])
@jwt_required()
def cleanup_closed_statuses():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('token, poste, analyse_status').execute()
    fixed = 0
    for row in (response.data or []):
        if row.get('poste') in POSTES_CLOTURES and row.get('analyse_status') in ('reanalyzing', 'pending'):
            supabase.table('candidats').update({"analyse_status": "completed"}).eq('token', row['token']).execute()
            fixed += 1
    return jsonify({'message': f'{fixed} dossier(s) de postes clotures stabilises (scores conserves)', 'fixed': fixed, 'postes_concernes': POSTES_CLOTURES}), 200

@app.route('/api/recruteur/export/<fmt>', methods=['GET'])
@jwt_required()
def export_candidates(fmt):
    try:
        poste_filter = request.args.get('poste', '')
        statut_filter = request.args.get('statut', '')
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        result = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            if statut_filter:
                statut = c.get('statut', 'en_attente')
                if statut != statut_filter:
                    continue
            for field in ['score_breakdown', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details']:
                if c.get(field):
                    try:
                        c[f'{field}_parsed'] = json.loads(c[field])
                    except Exception:
                        pass
            result.append(c)
        result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else "_global"
        statut_suffix = f"_{statut_filter}" if statut_filter else ""
        filename_base = f"rapport{poste_suffix}{statut_suffix}_{ts}"
        if fmt.lower() == 'excel' or fmt.lower() == 'xlsx':
            buf = generate_excel_report_enhanced(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation Excel'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'{filename_base}.xlsx')
        elif fmt.lower() == 'pdf':
            buf = generate_pdf_report_enhanced(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation PDF'}), 500
            return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{filename_base}.pdf')
        elif fmt.lower() == 'csv':
            csv_data = generate_csv_report(result, poste_filter=poste_filter)
            return send_file(io.BytesIO(csv_data.encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=f'{filename_base}.csv')
        elif fmt.lower() in ('word', 'docx'):
            buf = generate_word_report(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation Word'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', as_attachment=True, download_name=f'{filename_base}.docx')
        return jsonify({'error': 'Format non supporte. Utilisez: csv, excel, pdf ou word'}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats/<token>', methods=['DELETE'])
@jwt_required()
def delete_candidat(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    files_to_delete = []
    if data.get('cv_filename'):
        files_to_delete.append(data.get('cv_filename'))
    if data.get('lettre_filename'):
        files_to_delete.append(data.get('lettre_filename'))
    try:
        attestations = json.loads(data.get('attestation_filenames', '[]'))
        for att in attestations:
            if att:
                files_to_delete.append(att)
    except:
        pass
    deleted_files = []
    failed_files = []
    for filename in files_to_delete:
        try:
            supabase.storage.from_(SUPABASE_STORAGE_BUCKET).remove([filename])
            deleted_files.append(filename)
            logger.info(f"Fichier supprime: {filename}")
        except Exception as e:
            logger.error(f"Erreur suppression fichier {filename}: {e}")
            failed_files.append(filename)
    try:
        supabase.table('candidats').delete().eq('token', token).execute()
        logger.info(f"Candidat {token} supprime avec succes")
    except Exception as e:
        logger.error(f"Erreur suppression candidat {token}: {e}")
        return jsonify({'error': 'Erreur lors de la suppression du candidat', 'details': str(e)}), 500
    return jsonify({
        'message': 'Candidat supprime avec succes',
        'token': token,
        'files_deleted': deleted_files,
        'files_failed': failed_files,
        'candidat': {
            'nom': data.get('nom'),
            'prenom': data.get('prenom'),
            'email': data.get('email'),
            'poste': data.get('poste')
        }
    }), 200

def _run_zip_export_job(job_id, poste_filter, date_start, date_end):
    tmp_zip_path = None
    start_time = time.time()
    try:
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'processing'
        logger.info(f"[job {job_id}] Debut export ZIP optimise - {datetime.datetime.now()}")
        if not supabase:
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Supabase non configure'
            return
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        candidats = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            date_cand = c.get('date_candidature', '')
            if date_cand:
                date_only = date_cand.split('T')[0] if 'T' in date_cand else date_cand[:10]
                if date_start and date_only < date_start:
                    continue
                if date_end and date_only > date_end:
                    continue
            candidats.append(c)
        if not candidats:
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Aucun dossier a exporter'
            return
        download_tasks = []
        candidats_meta = {}
        for cand in candidats:
            poste_nom = cand.get('poste', 'Poste_Inconnu')
            poste_nom_clean = re.sub(r'[<>:"/\\|?*]', '_', poste_nom)
            num_dossier = cand.get('numero_dossier', '') or f"candidat_{cand['id'][:8]}"
            nom_candidat = cand.get('nom', 'N/A').upper()
            prenom_candidat = cand.get('prenom', 'N/A')
            dossier_candidat_nom = f"{num_dossier} - {nom_candidat} {prenom_candidat}"
            dossier_candidat_nom = re.sub(r'[<>:"/\\|?*]', '_', dossier_candidat_nom)
            dossier_parent = f"{poste_nom_clean}/{dossier_candidat_nom}"
            candidats_meta[cand['id']] = {'dossier_parent': dossier_parent, 'num_dossier': num_dossier, 'cand': cand, 'files_written': 0}
            cv_file = cand.get('cv_filename', '')
            if cv_file:
                download_tasks.append((cand['id'], cv_file, dossier_parent, 'CV'))
            lettre_file = cand.get('lettre_filename', '')
            if lettre_file:
                download_tasks.append((cand['id'], lettre_file, dossier_parent, 'Lettre_de_motivation'))
            att_raw = cand.get('attestation_filenames', '[]')
            try:
                att_files = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                for idx, att_file in enumerate(att_files, 1):
                    if att_file:
                        download_tasks.append((cand['id'], att_file, dossier_parent, f'Attestation_{idx}'))
            except Exception:
                pass
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['total'] = len(download_tasks)
        logger.info(f"[job {job_id}] {len(download_tasks)} fichiers a telecharger")

        def _download_one(task):
            cand_id, blob_name, dossier_parent, prefix = task
            file_bytes = download_file_from_supabase_robust(blob_name)
            return (cand_id, blob_name, dossier_parent, prefix, file_bytes)

        tmp_fd = tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir='/tmp')
        tmp_zip_path = tmp_fd.name
        tmp_fd.close()
        files_added = 0
        max_workers = min(_ZIP_MAX_WORKERS, max(1, len(download_tasks)))
        logger.info(f"[job {job_id}] Utilisation de {max_workers} workers pour le telechargement")
        with zipfile.ZipFile(tmp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if download_tasks:
                BATCH_SIZE = 50
                total_batches = (len(download_tasks) + BATCH_SIZE - 1) // BATCH_SIZE
                for batch_idx in range(0, len(download_tasks), BATCH_SIZE):
                    batch = download_tasks[batch_idx:batch_idx + BATCH_SIZE]
                    logger.info(f"[job {job_id}] Traitement du lot {batch_idx//BATCH_SIZE + 1}/{total_batches} ({len(batch)} fichiers)")
                    with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as executor:
                        futures = [executor.submit(_download_one, t) for t in batch]
                        for future in as_completed(futures):
                            try:
                                cand_id, blob_name, dossier_parent, prefix, file_bytes = future.result(timeout=60)
                            except Exception as e:
                                logger.error(f"[job {job_id}] Erreur telechargement: {e}")
                                continue
                            if file_bytes:
                                ext = blob_name.rsplit('.', 1)[-1].lower() if '.' in blob_name else ''
                                archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                                try:
                                    zip_file.writestr(archive_name, file_bytes)
                                    files_added += 1
                                    if cand_id in candidats_meta:
                                        candidats_meta[cand_id]['files_written'] += 1
                                except Exception as e:
                                    logger.error(f"[job {job_id}] Erreur ecriture ZIP: {e}")
                                finally:
                                    del file_bytes
                            with _ZIP_JOBS_LOCK:
                                _ZIP_JOBS[job_id]['progress'] = _ZIP_JOBS[job_id].get('progress', 0) + 1
                    gc.collect()
            for cand_id, meta in candidats_meta.items():
                if meta['files_written'] > 0:
                    continue
                cand = meta['cand']
                info_content = f"Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}\nPoste: {cand.get('poste', 'N/A')}\nNumero dossier: {meta['num_dossier']}\nEmail: {cand.get('email', 'N/A')}\nTelephone: {cand.get('telephone', 'N/A')}\nDate candidature: {cand.get('date_candidature', 'N/A')}"
                archive_name = f"{meta['dossier_parent']}/INFOS_CANDIDAT.txt"
                zip_file.writestr(archive_name, info_content.encode('utf-8'))
                files_added += 1
        elapsed = time.time() - start_time
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else ""
        filename = f"dossiers_candidats{poste_suffix}_{ts}.zip"
        logger.info(f"[job {job_id}] Export ZIP termine en {elapsed:.2f}s pour {len(candidats)} candidats ({files_added} fichiers)")
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'done'
            _ZIP_JOBS[job_id]['filepath'] = tmp_zip_path
            _ZIP_JOBS[job_id]['filename'] = filename
        del candidats_meta, download_tasks
        gc.collect()
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"[job {job_id}] Erreur export ZIP: {e}")
        if tmp_zip_path and os.path.exists(tmp_zip_path):
            try:
                os.remove(tmp_zip_path)
            except Exception:
                pass
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'error'
            _ZIP_JOBS[job_id]['error'] = str(e)

@app.route('/api/recruteur/dossiers/zip/start', methods=['GET'])
@jwt_required()
def start_zip_export():
    _cleanup_old_zip_jobs()
    poste_filter = request.args.get('poste', '')
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    job_id = uuid.uuid4().hex
    with _ZIP_JOBS_LOCK:
        _ZIP_JOBS[job_id] = {'status': 'pending', 'created_at': time.time(), 'progress': 0, 'total': 0, 'filepath': None, 'filename': None, 'error': None}
    threading.Thread(target=_run_zip_export_job, args=(job_id, poste_filter, date_start, date_end), daemon=True).start()
    return jsonify({'job_id': job_id}), 202

@app.route('/api/recruteur/dossiers/zip/status/<job_id>', methods=['GET'])
@jwt_required()
def zip_export_status(job_id):
    with _ZIP_JOBS_LOCK:
        job = _ZIP_JOBS.get(job_id)
        if not job:
            return jsonify({'error': 'Job introuvable ou expire'}), 404
        return jsonify({'status': job['status'], 'progress': job.get('progress', 0), 'total': job.get('total', 0), 'error': job.get('error')}), 200

@app.route('/api/recruteur/dossiers/zip/download/<job_id>', methods=['GET'])
@jwt_required()
def zip_export_download(job_id):
    with _ZIP_JOBS_LOCK:
        job = _ZIP_JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job introuvable ou expire'}), 404
    if job['status'] == 'error':
        return jsonify({'error': job.get('error', 'Erreur inconnue')}), 500
    if job['status'] != 'done':
        return jsonify({'error': 'Export pas encore termine', 'status': job['status']}), 425
    filepath = job.get('filepath')
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'Fichier expire ou deja supprime'}), 410
    response_obj = send_file(filepath, mimetype='application/zip', as_attachment=True, download_name=job['filename'])
    @response_obj.call_on_close
    def _cleanup_after_send():
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            logger.warning(f"Nettoyage fichier temporaire ZIP (job {job_id}) echoue: {e}")
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS.pop(job_id, None)
    return response_obj

def _cleanup_old_zip_jobs():
    now = time.time()
    with _ZIP_JOBS_LOCK:
        stale = [jid for jid, j in _ZIP_JOBS.items() if now - j.get('created_at', now) > _ZIP_JOBS_MAX_AGE_SECONDS]
        for jid in stale:
            job = _ZIP_JOBS.pop(jid, None)
            if job and job.get('filepath') and os.path.exists(job['filepath']):
                try:
                    os.remove(job['filepath'])
                except Exception:
                    pass

@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    body = request.get_json(silent=True) or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))
    nom_c = f"{data.get('prenom', '')} {data.get('nom', '')}".strip()
    poste = data.get('poste', '')
    to_email = data.get('email', '')
    sign = "\nCordialement,\nL'equipe Ressources Humaines\nRecrutBank"
    if msg_type == 'retenu':
        sujet = f"Felicitations – Candidature retenue – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nNous avons le plaisir de vous informer que votre candidature pour le poste de {poste} a ete retenue.\nNous vous contacterons tres prochainement." + sign
    elif msg_type == 'entretien':
        sujet = f"Invitation a un entretien – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nSuite a l'examen de votre candidature pour le poste de {poste}, nous avons le plaisir de vous inviter a un entretien.\nNous prendrons contact avec vous pour convenir d'une date." + sign
    else:
        sujet = f"Reponse a votre candidature – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nNous vous remercions de l'interet que vous portez a notre institution.\nApres examen attentif de votre dossier pour le poste de {poste}, nous avons le regret de vous informer que votre candidature n'a pas ete retenue.\nNous vous encourageons a postuler a nouveau." + sign
    return jsonify({'to': to_email, 'nom': nom_c, 'sujet': sujet, 'corps': corps}), 200

@app.route('/api/recruteur/uploads/<path:filename>', methods=['GET'])
def serve_upload(filename):
    safe = secure_filename(filename.replace('/', '_'))
    if not safe:
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    url = get_signed_url(safe, expiration_minutes=30)
    if not url:
        return jsonify({'error': 'Fichier introuvable'}), 404
    return redirect(url)

@app.route('/api/recruteur/debug/analyse-ia', methods=['POST'])
@jwt_required()
def debug_analyse_ia():
    data = request.get_json(silent=True) or {}
    cv_text = data.get('cv_text', '')
    lettre_text = data.get('lettre_text', '')
    poste = data.get('poste', '')
    if not cv_text or poste not in GRILLE:
        return jsonify({'error': 'cv_text requis et poste doit exister dans GRILLE'}), 400
    result = analyze_cv_against_grille(cv_text, lettre_text, [], poste)
    return jsonify(result), 200

@app.route('/api/test-email', methods=['GET'])
def test_email():
    try:
        to = request.args.get('to', '')
        if not to:
            return jsonify({'error': 'Parametre ?to= requis'}), 400
        ok = send_email(to, 'Test RecrutBank', f'Ceci est un email de test depuis RecrutBank avec {_PROVIDER}.')
        return jsonify({'sent': ok}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health-version', methods=['GET'])
def health_version():
    return jsonify({
        "version": "v9.0-grille-stable-scoring",
        "postes_actifs": POSTES_ACTIFS,
        "postes_count": len(POSTES),
        "scoring_seuils": "Chef Division Corporate: 11/7, Data Analyst: 11/7",
        "scoring_strict": True,
        "manual_status_priority": True,
        "auto_width_excel": True,
        "max_concurrent_downloads": DOWNLOAD_MAX_CONCURRENT,
        "zip_max_workers": _ZIP_MAX_WORKERS,
        "intelligent_scoring": True,
        "advanced_reasoning": True,
        "ia_provider": _PROVIDER,
        "ia_model": _MODEL,
        "ia_free": "OpenRouter" in _PROVIDER,
        "reasoning_enabled": OPENROUTER_REASONING_ENABLED,
        "json_robust_parsing": True,
        "sous_scores_complets": True,
        "eliminatoire_rejet": True,
        "score_conserve": True,
        "score_somme_sous_scores": True,
        "local_corporate_sme": True,
        "chef_agence_auto_scoring": True,
        "portefeuille_auto_credit": True,
        "business_rules_stable": True,
        "flags_auto_suppression": True,
        "grille_reference": "Chef de Division Local Corporate",
        "scoring_max": 14,
        "seuils": {"prioritaire": 11, "potentiel_min": 7, "rejet_max": 6},
        "deployed_at": datetime.datetime.now().isoformat()
    }), 200

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    import multiprocessing
    cpu_count = multiprocessing.cpu_count()
    suggested_workers = min(4, cpu_count * 2)
    logger.info("=" * 60)
    logger.info(f"🚀 RecrutBank API v9.0 - Grille Stable Scoring")
    logger.info("=" * 60)
    logger.info(f"Port: {port}")
    logger.info(f"Workers suggeres: {suggested_workers}")
    logger.info(f"Threads par worker: 4")
    logger.info(f"IA Provider: {'✅ ' + _PROVIDER if IA_ANALYSE_ACTIVE else '❌ Aucune'}")
    if IA_ANALYSE_ACTIVE:
        logger.info(f"Modele: {_MODEL}")
        logger.info(f"Gratuit: {'✅ Oui' if 'OpenRouter' in _PROVIDER else '❌ Non (payant)'}")
        logger.info(f"Reasoning: {'✅ Active' if OPENROUTER_REASONING_ENABLED else '❌ Desactive'}")
        logger.info(f"JSON Robust Parsing: ✅ Active")
        logger.info(f"Sous-scores: 8 dimensions selon grille")
        logger.info(f"Grille Chef Division: experience_bancaire 0-3, diplome 0-3, management 0-3, risque_credit 0-2, experience_corporate 0-2, coherence 0-2, qualite_cv 0-1, certification 0-1")
        logger.info(f"Eliminatoire = Rejet, Score conserve: ✅ Active")
        logger.info(f"Score = Somme des sous-scores (max 14): ✅ Active")
        logger.info(f"Local Corporate / SME: ✅ Active")
        logger.info(f"Chef d'agence = auto-scoring management=3, risque_credit=2")
        logger.info(f"Gestion de portefeuille = auto-scoring risque_credit=2, experience_corporate=2")
        logger.info(f"Business Rules avec auto-suppression des flags: ✅ Active")
        logger.info(f"Concurrence IA max: {os.getenv('IA_MAX_CONCURRENCY', '5')}")
        test_ia_connection()
    else:
        logger.warning("⚠️ MODE FALLBACK UNIQUEMENT - Aucune IA disponible")
        logger.warning("Verifiez OPENROUTER_API_KEY ou DEEPSEEK_API_KEY")
        logger.warning("Assurez-vous que openai est installe")
    logger.info(f"Mode raisonnement avance: {'ACTIF ✅' if IA_ANALYSE_ACTIVE else 'INACTIF ❌'}")
    logger.info(f"Telechargements concurrents: {DOWNLOAD_MAX_CONCURRENT}")
    logger.info(f"Workers ZIP max: {_ZIP_MAX_WORKERS}")
    logger.info("=" * 60)
    try:
        import gunicorn
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

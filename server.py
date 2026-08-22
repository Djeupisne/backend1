from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, json, re, threading, io, csv, unicodedata, zipfile, time, gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from supabase import create_client
import logging
from dotenv import load_dotenv
load_dotenv()

# === GEMINI ===
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# === EXTRACTION TEXT ===
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
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

# === REPORTLAB (PDF) ===
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# === OPENPYXL (EXCEL) ===
try:
    import openpyxl
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.styles.numbers import FORMAT_DATE
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

# === CONFIGURATION GEMINI ===
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

if GEMINI_AVAILABLE and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        models_to_try = [
            "gemini-1.5-pro",
            "gemini-1.0-pro",
            "gemini-pro",
            "models/gemini-1.5-pro",
            "models/gemini-1.0-pro"
        ]
        GEMINI_ACTIVE = False
        for model_name in models_to_try:
            try:
                gemini_model = genai.GenerativeModel(model_name)
                test_response = gemini_model.generate_content("Test")
                if test_response:
                    GEMINI_ACTIVE = True
                    GEMINI_MODEL = model_name
                    print(f"✅ Gemini activé avec succès: {model_name}")
                    break
            except Exception as e:
                print(f"⚠️ Échec avec {model_name}: {e}")
                continue
        if not GEMINI_ACTIVE:
            print("❌ Aucun modèle Gemini disponible")
    except Exception as e:
        GEMINI_ACTIVE = False
        print(f"⚠️ Gemini erreur: {e}")
else:
    GEMINI_ACTIVE = False
    print("⚠️ Gemini désactivé")

# === APP ===
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS,PUT,DELETE')
    response.headers.add('Access-Control-Max-Age', '600')
    if request.method == 'OPTIONS':
        response.status_code = 204
    return response

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return jsonify({'status': 'ok', 'message': 'RecrutBank API is running'}), 200

app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

# === SUPABASE ===
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "candidatures")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# === SUPABASE FUNCTIONS ===
def upload_file_to_supabase(file_obj, blob_name, content_type=None):
    if not supabase:
        return None
    try:
        file_bytes = file_obj.read()
        supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            blob_name, file_bytes,
            {"content-type": content_type or "application/octet-stream", "upsert": "true"}
        )
        return blob_name
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return None

def download_file_from_supabase(blob_name):
    if not supabase:
        return None
    try:
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).download(blob_name)
        return response
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None

def get_signed_url(blob_name, expiration_minutes=60):
    if not supabase:
        return None
    try:
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).create_signed_url(
            blob_name, expiration_minutes * 60
        )
        return response.get('signedURL') if response else None
    except Exception as e:
        logger.error(f"Signed URL error: {e}")
        return None

# === EMAIL ===
def send_email(to_email, subject, body):
    import requests
    import re as _re
    brevo_api_key = os.getenv('BREVO_API_KEY', '')
    smtp_from = os.getenv('SMTP_FROM', 'RecrutBank RH <recrutbank@email.com>')
    if not brevo_api_key:
        return False
    match = _re.search(r'<(.+?)>', smtp_from)
    sender_email = match.group(1) if match else smtp_from
    sender_name = smtp_from.split('<')[0].strip() if '<' in smtp_from else 'RecrutBank RH'
    html_content = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;"><div style="max-width: 600px; margin: 0 auto; padding: 20px;">{body.replace(chr(10), '<br>')}</div></body></html>"""
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"api-key": brevo_api_key, "Content-Type": "application/json", "Accept": "application/json"}
    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email, "name": to_email.split('@')[0]}],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": body
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 201
    except Exception:
        return False

# === POSTES ===
POSTES = [
    "Responsable Administration de Crédit",
    "Analyste Crédit CCB",
    "Archiviste (Administration Crédit)",
    "Senior Finance Officer",
    "Market Risk Officer",
    "IT Réseau & Infrastructure",
    "Auditeur interne",
    "Chef service contrôle des engagements",
    "Chef service IT (maintenance/support)",
    "Chef service finance",
    "Chef service risques de marché",
    "Chef service reporting réglementaire",
    "Chef de Section Compensation",
    "Chargé(e) d'Administration de Crédit",
    "Chef de Division Local Corporate"
]

POSTES_ACTIFS = ["Chargé(e) d'Administration de Crédit", "Chef de Division Local Corporate"]
POSTES_CLOTURES = [p for p in POSTES if p not in POSTES_ACTIFS]
POSTES_AVEC_SCORING_12 = ["Chef de Section Compensation", "Chargé(e) d'Administration de Crédit"]
POSTES_AVEC_SCORING_14 = ["Chef de Division Local Corporate"]
POSTES_AVEC_SCORING_100 = [
    "Auditeur interne", "Chef service contrôle des engagements", "Chef service IT (maintenance/support)",
    "Chef service finance", "Chef service risques de marché", "Chef service reporting réglementaire"
]

def is_poste_actif(poste):
    return poste in POSTES_ACTIFS

# === GRILLES ===
GRILLE = {
    "Chef de Division Local Corporate": {
        "eliminatoire": [
            "Aucune expérience dans le secteur bancaire ou financier réglementé",
            "Niveau de diplôme inférieur à Bac +4 (Master ou équivalent requis)",
            "Moins de 5 ans d'expérience professionnelle, dont une partie significative en banque",
            "Aucune expérience en gestion d'un portefeuille de clients Corporate ou d'entreprises",
            "Aucune expérience managériale : ni encadrement d'équipe, ni pilotage d'une activité commerciale",
            "Aucune exposition à la gestion du risque de crédit ou au suivi de la qualité d'un portefeuille (NPL, provisions)"
        ],
        "a_verifier": [
            "Pilotage d'une activité Corporate ou d'un segment entreprises avec des objectifs chiffrés",
            "Gestion d'un portefeuille de clients Corporate et capacité à le développer",
            "Encadrement et évaluation d'une équipe commerciale ou bancaire",
            "Suivi de la qualité du portefeuille de crédit (NPL, CIR, provisions)",
            "Développement de ventes croisées (cross-selling)",
            "Production ou supervision de rapports de performance commerciale",
            "Exposition à la réglementation bancaire locale (COBAC, BEAC)"
        ],
        "signaux_forts": [
            "Pilotage d'une division Corporate avec atteinte des objectifs",
            "Gestion active du ratio NPL et du ratio coût/revenu (CIR)",
            "Expérience avérée en cross-selling avec équipes TSG ou Cash Management",
            "Développement réel du portefeuille Corporate",
            "Leadership démontré",
            "Certification Ecobank, Moody's ou ITB",
            "Connaissance du marché CEMAC / UEMOA",
            "Exposition aux plateformes numériques bancaires",
            "Résultats commerciaux quantifiés"
        ],
        "points_attention": [
            "Parcours exclusivement back-office sans expérience commerciale",
            "Profil technique sans expérience managériale",
            "Expériences très courtes (< 2 ans)",
            "CV sans résultats chiffrés",
            "Mobilité excessive",
            "Trous inexpliqués"
        ]
    },
    "Chargé(e) d'Administration de Crédit": {
        "eliminatoire": [
            "Aucune expérience ou formation dans un domaine bancaire, financier ou comptable",
            "Niveau de diplôme inférieur à Bac +3",
            "Aucune notion du crédit bancaire"
        ],
        "a_verifier": [
            "Exposition au cycle de crédit",
            "Gestion ou participation au suivi des garanties",
            "Production de reportings ou tableaux de bord",
            "Expérience avec un système bancaire",
            "Détection d'anomalies ou impayés"
        ],
        "signaux_forts": [
            "Gestion administrative du cycle de crédit",
            "Exposition à la norme IFRS 9",
            "Suivi et sécurisation des garanties",
            "Production de reportings portefeuille",
            "Participation aux comités de risque",
            "Maîtrise des Produits de Portefeuille",
            "Expérience dans une banque de la zone CEMAC",
            "Audits ou contrôles internes réussis",
            "Rigueur documentaire"
        ],
        "points_attention": [
            "Parcours commercial pur",
            "Profil uniquement théorique",
            "Expériences courtes",
            "Missions peu détaillées"
        ]
    },
    "Chef de Section Compensation": {
        "eliminatoire": [
            "Expérience en banque ou établissement financier réglementé",
            "Minimum 3 ans en opérations bancaires ou back-office",
            "Exposition aux opérations de compensation interbancaire",
            "Connaissance des règles BEAC / GIMAC",
            "Gestion de suspens, rejets ou réclamations",
            "Expérience d'encadrement",
            "Profil bancaire avec exposition interbancaire"
        ],
        "a_verifier": [
            "Supervision quotidienne des opérations de compensation",
            "Dénouement de positions nettes",
            "Gestion de suspens et rejets",
            "Encadrement d'équipe",
            "Utilisation de SYSTAC, SYGMA, SWIFT",
            "Production de reportings",
            "Participation à des contrôles internes"
        ],
        "signaux_forts": [
            "BEAC / GIMAC / SYSTAC / SYGMA",
            "Règlement de positions nettes",
            "Contrôle de conformité",
            "Maîtrise de SYSCOHADA",
            "Gestion de fin de journée",
            "Rapports opérationnels",
            "Expérience en zone CEMAC",
            "Audits COBAC réussis",
            "Gestion d'équipe"
        ],
        "points_attention": [
            "Parcours purement comptable",
            "Rôle uniquement administratif",
            "Absence de rôle managérial",
            "CV générique",
            "Expériences courtes",
            "Outils non mentionnés"
        ]
    }
}

GRILLE.update({
    "Responsable Administration de Crédit": {
        "eliminatoire": ["Expérience bancaire", "Minimum 3 ans en crédit / risque", "Exposition aux garanties ou conformité"],
        "a_verifier": ["Validation de dossiers de crédit", "Gestion des garanties", "Participation à des audits"],
        "signaux_forts": ["IFRS 9", "COBAC / conformité", "Suivi portefeuille / impayés"],
        "points_attention": ["Parcours trop comptable", "Rôle administratif sans responsabilité", "CV flou"]
    },
    "Analyste Crédit CCB": {
        "eliminatoire": ["Expérience en analyse crédit", "Capacité à lire des états financiers", "Minimum 3 ans institution financière"],
        "a_verifier": ["Clients PME", "Clients particuliers", "Structuration de crédit", "Avis de crédit"],
        "signaux_forts": ["Cash-flow analysis", "Montage de crédit", "Comités de crédit"],
        "points_attention": ["CV trop relation client", "Aucune notion de risque", "Expériences courtes"]
    },
    "Archiviste (Administration Crédit)": {
        "eliminatoire": ["Expérience en gestion documentaire", "Rigueur démontrée"],
        "a_verifier": ["Archivage physique et électronique", "Gestion des dossiers sensibles"],
        "signaux_forts": ["Expérience en banque ou juridique", "Manipulation de garanties"],
        "points_attention": ["Profils généralistes", "CV désorganisé"]
    },
    "Senior Finance Officer": {
        "eliminatoire": ["Expérience en reporting financier", "Exposition aux états financiers", "Interaction avec auditeurs", "Minimum 3 ans finance"],
        "a_verifier": ["Production états financiers", "Reporting groupe", "Connaissance IFRS", "Contraintes réglementaires"],
        "signaux_forts": ["IFRS / consolidation", "Reporting groupe", "Outils SPECTRA / CERBER"],
        "points_attention": ["Profil comptable junior", "Pas de responsabilité réelle", "CV flou"]
    },
    "Market Risk Officer": {
        "eliminatoire": ["Base en risques de marché", "Exposition à FX / taux / liquidité", "Minimum 3 ans institution financière"],
        "a_verifier": ["Maîtrise VaR / stress testing", "Analyse des positions", "Excel avancé", "VBA ou Python"],
        "signaux_forts": ["Bâle II / III", "Gestion ALM", "Produits FICC", "Reporting risque"],
        "points_attention": ["CV trop théorique", "Aucune mention d'outils", "Incapacité à modéliser"]
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": ["Expérience en réseau / infrastructure", "Environnement critique", "Notion de sécurité IT", "Minimum 2 ans"],
        "a_verifier": ["Gestion réseaux LAN/WAN/VPN", "Gestion serveurs", "Cloud", "Gestion des incidents"],
        "signaux_forts": ["Cybersécurité", "Haute disponibilité", "Gestion ATM", "Certifications"],
        "points_attention": ["Profil helpdesk", "CV sans détail technique", "Sécurité non mentionnée"]
    },
    "Auditeur interne": {
        "eliminatoire": ["Expérience en audit", "Minimum 3 ans audit bancaire", "Connaissance normes d'audit"],
        "a_verifier": ["Missions d'audit", "Évaluation des risques", "Rédaction rapports", "Suivi recommandations"],
        "signaux_forts": ["Normes IIA", "COBAC", "Audit IT", "Certification CIA"],
        "points_attention": ["Profil comptable sans audit", "Pas d'expérience terrain", "CV flou"]
    },
    "Chef service contrôle des engagements": {
        "eliminatoire": ["Maîtrise risque crédit", "Expérience en octroi crédits", "Minimum 5 ans"],
        "a_verifier": ["Analyse financière", "Structuration crédits", "Animation comité crédit", "Management"],
        "signaux_forts": ["IFRS 9", "Grande entreprise", "Restructuration", "Risk management"],
        "points_attention": ["Profil commercial sans analyse", "Pas d'analyse financière"]
    },
    "Chef service IT (maintenance/support)": {
        "eliminatoire": ["Background IT solide", "Minimum 5 ans maintenance", "Environnement critique"],
        "a_verifier": ["Maintenance préventive", "Support N2/N3", "Gestion parc", "Supervision"],
        "signaux_forts": ["ITIL", "Virtualisation", "Core banking", "Certifications"],
        "points_attention": ["Profil helpdesk N1", "CV sans détail technique"]
    },
    "Chef service finance": {
        "eliminatoire": ["Expérience finance bancaire 7 ans", "Reporting financier", "Management équipe"],
        "a_verifier": ["Production états financiers", "Reporting réglementaire", "Relation auditeurs", "Pilotage performance"],
        "signaux_forts": ["IFRS", "Consolidation", "Outils SPECTRA", "Bac+5 + Certification"],
        "points_attention": ["Profil comptable junior", "Pas de management", "Expérience hors banque"]
    },
    "Chef service risques de marché": {
        "eliminatoire": ["Expérience risques de marché", "Exposition produits trésorerie", "Minimum 5 ans"],
        "a_verifier": ["Calcul VaR", "Stress testing", "Reporting risques", "Excel avancé"],
        "signaux_forts": ["Bâle II/III", "Gestion ALM", "Produits FICC", "Python/R"],
        "points_attention": ["Profil théorique", "Pas d'exposition marchés"]
    },
    "Chef service reporting réglementaire": {
        "eliminatoire": ["Comptabilité bancaire", "Reporting réglementaire", "Minimum 5 ans"],
        "a_verifier": ["Production rapports réglementaires", "Contrôle cohérence", "Veille réglementaire"],
        "signaux_forts": ["SPECTRA", "Normes COBAC", "Reporting prudentiel"],
        "points_attention": ["Profil généraliste", "Pas de spécialisation bancaire"]
    }
})

# === KEYWORD MAPPING ===
KEYWORD_MAPPING = {
    "Expérience en banque": ["banque", "bancaire", "institution financiere", "bank", "financial institution"],
    "Minimum 3 ans en crédit": ["credit", "risque", "analyse credit", "loan", "credit analysis"],
    "Exposition aux garanties": ["garantie", "collateral", "surete", "hypotheque", "guarantee"],
    "IFRS 9": ["ifrs 9", "ifrs9", "ecl", "provisionnement", "staging"],
    "COBAC / conformité": ["cobac", "conformite", "bceao", "regulation bancaire", "compliance"],
    "Suivi portefeuille": ["portefeuille", "impayes", "npl", "encours", "portfolio"],
    "Pilotage Corporate": ["corporate", "pilotage", "grandes entreprises", "sme", "local corporate"],
    "Management": ["management", "encadrement", "supervision", "equipe", "manager"],
    "Cross-selling": ["cross selling", "ventes croisees", "upselling", "cross-sell"],
    "NPL": ["npl", "non performing", "cir", "cost income"],
    "CEMAC": ["cemac", "uemoa", "bceao", "beac", "afrique centrale"],
    "Certification": ["ecobank", "moody's", "itb", "certification", "mba", "master"],
    "Reporting": ["reporting", "tableau de bord", "dashboard", "rapport"],
    "Système bancaire": ["finacle", "t24", "amplitude", "flexcube", "core banking"],
    "IFRS 9 staging": ["ifrs 9", "stage 1", "stage 2", "stage 3", "ecl"],
    "BEAC/GIMAC": ["beac", "gimac", "systac", "sygma", "swift"],
    "Compensation": ["compensation", "interbancaire", "clearing", "chambre de compensation"]
}

_ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouucaaAAEEEEIIOUUUCAAaaonaaon')

# === FONCTIONS D'EXTRACTION ===
def normalize_spaces(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def normalize_unicode(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    return text.strip()

def normalize_for_matching(text):
    if not text:
        return "", []
    no_accents = text.lower().translate(_ACCENT_MAP)
    cleaned = re.sub(r'[^\w\s\-/\.]', ' ', no_accents)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    tokens = [t for t in re.findall(r'\b[a-z0-9\-/\.]{2,}\b', cleaned) if len(t) >= 2]
    return cleaned, tokens

def extract_text_from_pdf_robust(file_bytes, filename):
    text = ""
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages[:10]:
                    content = page.extract_text()
                    if content:
                        text += normalize_spaces(content) + "\n"
            if text.strip():
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"pdfplumber error: {e}")
    if PYPDF2_AVAILABLE and not text.strip():
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            for page in reader.pages[:10]:
                content = page.extract_text()
                if content:
                    text += normalize_spaces(content) + "\n"
            if text.strip():
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"PyPDF2 error: {e}")
    try:
        raw = file_bytes.decode('utf-8', errors='ignore')
        raw = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', raw)
        if raw.strip():
            return normalize_unicode(normalize_spaces(raw))
    except:
        pass
    return normalize_unicode(text.strip()) if text.strip() else ""

def extract_text_from_docx_robust(file_bytes):
    if not DOCX_AVAILABLE:
        return ""
    try:
        doc = Document(io.BytesIO(file_bytes))
        parts = []
        for para in doc.paragraphs:
            t = normalize_spaces(para.text)
            if t:
                parts.append(t)
        result = "\n".join(parts).strip()
        if result:
            return normalize_unicode(result)
    except Exception as e:
        logger.warning(f"DOCX error: {e}")
    try:
        raw = file_bytes.decode('utf-8', errors='ignore')
        return normalize_unicode(normalize_spaces(raw))
    except:
        return ""

def extract_text_from_txt(file_bytes):
    for enc in ['utf-8', 'latin-1', 'cp1252']:
        try:
            return normalize_unicode(normalize_spaces(file_bytes.decode(enc, errors='ignore').strip()))
        except:
            continue
    return ""

def extract_text_robust_from_bytes(file_bytes, filename):
    if not file_bytes:
        return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    try:
        if ext == 'pdf':
            return extract_text_from_pdf_robust(file_bytes, filename)
        elif ext in ('doc', 'docx'):
            return extract_text_from_docx_robust(file_bytes)
        elif ext == 'txt':
            return extract_text_from_txt(file_bytes)
        else:
            return normalize_unicode(normalize_spaces(file_bytes.decode('utf-8', errors='ignore').strip()))
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        return ""

# === GEMINI SEMANTIC ANALYSIS ===
def check_criterion_with_gemini(criterion, cv_text, lettre_text, poste):
    if not GEMINI_ACTIVE:
        return None, 0.0, "", []
    cv_extrait = cv_text[:3000] if cv_text else ""
    lettre_extrait = (lettre_text or "")[:1000]
    prompt = f"""Tu es un expert en recrutement bancaire. Analyse si le candidat remplit ce critère.
POSTE: {poste}
CRITERE: "{criterion}"
--- CV ---
{cv_extrait}
--- LETTRE ---
{lettre_extrait if lettre_extrait else "(non fournie)"}
Réponds UNIQUEMENT avec ce JSON: {{"valide": true/false, "confiance": 0.0-1.0, "justification": "", "elements_trouves": []}}"""
    try:
        response = gemini_model.generate_content(prompt)
        import json
        import re
        json_match = re.search(r'\{[^{}]*\}', response.text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return result.get('valide', False), float(result.get('confiance', 0.5)), result.get('justification', ''), result.get('elements_trouves', [])
        return None, 0.0, "", []
    except Exception as e:
        logger.warning(f"Gemini error: {e}")
        return None, 0.0, "", []

def check_criterion_semantic(criterion, cv_text, lettre_text, poste, normalized_text=None, raw_full_text=None):
    if GEMINI_ACTIVE and poste in POSTES_AVEC_SCORING_12 + POSTES_AVEC_SCORING_14:
        try:
            valide, confiance, justification, elements = check_criterion_with_gemini(criterion, cv_text, lettre_text, poste)
            if valide is not None and confiance >= 0.5:
                logger.info(f"✅ Gemini: '{criterion}' → {valide} (conf: {confiance:.2f})")
                return valide, confiance, elements
            elif valide is not None and valide:
                logger.info(f"⚠️ Gemini conf faible: '{criterion}' → {valide} (conf: {confiance:.2f})")
                return True, confiance, elements
        except Exception as e:
            logger.warning(f"Gemini fallback: {e}")
    if raw_full_text:
        normalized, _ = normalize_for_matching(raw_full_text)
    else:
        normalized = normalized_text or ""
    keywords = KEYWORD_MAPPING.get(criterion, [criterion.lower()])
    text_clean, text_tokens = normalize_for_matching(normalized)
    for kw in keywords:
        kw_clean, kw_tokens = normalize_for_matching(kw)
        if kw_clean in text_clean:
            logger.info(f"✅ Mots-clés: '{criterion}' trouvé via '{kw}'")
            return True, 1.0, [kw]
        if RAPIDFUZZ_AVAILABLE and len(kw_clean) >= 4:
            ratio = fuzz.partial_ratio(kw_clean, text_clean)
            if ratio >= 80:
                logger.info(f"✅ Mots-clés fuzzy: '{criterion}' trouvé via '{kw}' (ratio: {ratio}%)")
                return True, ratio/100, [f"{kw}~{ratio/100:.2f}"]
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(1, len(kw_tokens) * 0.5):
                logger.info(f"✅ Mots-clés tokens: '{criterion}' trouvé via '{kw}' ({len(common)}/{len(kw_tokens)})")
                return True, len(common)/len(kw_tokens), [f"{kw}[{len(common)}/{len(kw_tokens)}]"]
    logger.info(f"❌ Critère non trouvé: '{criterion}'")
    return False, 0.0, []

# === FONCTIONS DE SCORING ===
def calculate_score_chef_division_corporate(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Division Local Corporate"
    grille = GRILLE[poste]
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized, _ = normalize_for_matching(raw_full)
    flags = []
    for crit in grille['eliminatoire']:
        ok, _, _ = check_criterion_semantic(crit, cv_text, lettre_text, poste, normalized, raw_full)
        if not ok:
            flags.append(crit)
    if flags:
        return {'score': 0, 'score_max': 14, 'decision': '❌ Rejet (éliminatoire)', 'flags_eliminatoires': flags, 'sous_scores': {}, 'checklist': {}, 'detail': f"ÉLIMINÉ: {len(flags)} critère(s)"}
    exp_criteria = ["Pilotage Corporate", "Gestion portefeuille Corporate", "Développement portefeuille"]
    n_exp = sum(1 for c in exp_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    adequation = min(3, n_exp)
    mgmt_criteria = ["Management", "Leadership"]
    n_mgmt = sum(1 for c in mgmt_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    management = min(3, n_mgmt)
    risk_criteria = ["NPL", "Suivi qualité portefeuille"]
    n_risk = sum(1 for c in risk_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    risque = min(2, n_risk)
    cross_criteria = ["Cross-selling", "Ventes croisées"]
    n_cross = sum(1 for c in cross_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    crossselling = min(2, n_cross)
    n_attention = sum(1 for c in grille['points_attention'] if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    coherence = 2 if n_attention == 0 else (1 if n_attention <= 2 else 0)
    word_count = len(cv_text.split())
    has_quantified = bool(re.search(r'\d+\s*(%|pourcent|portefeuille|millions|milliards|ca)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified) else 0
    lettre_clean = (lettre_text or '').strip()
    if lettre_clean:
        mentions_poste = any(kw in lettre_clean.lower() for kw in ['corporate', 'grandes entreprises', 'division', 'management'])
        lettre_score = 1 if (len(lettre_clean.split()) >= 80 and mentions_poste) else 0
    else:
        lettre_score = 0
    has_certif = check_criterion_semantic("Certification", cv_text, lettre_text, poste, normalized, raw_full)[0]
    has_market = check_criterion_semantic("CEMAC", cv_text, lettre_text, poste, normalized, raw_full)[0]
    certif_score = 1 if (has_certif or has_market) else 0
    qualite_globale = 1 if (qualite_cv == 1 and lettre_score == 1) else (0.5 if (qualite_cv == 1 or lettre_score == 1) else 0)
    qualite_globale = min(1, round(qualite_globale))
    sous_scores = {
        "Adéquation Corporate": adequation,
        "Capacité managériale": management,
        "Maîtrise du risque": risque,
        "Cross-selling": crossselling,
        "Cohérence du parcours": coherence,
        "Qualité CV et lettre": qualite_globale,
        "Certifications": certif_score
    }
    score_total = sum(sous_scores.values())
    if score_total >= 11:
        decision = "🥇 Entretien prioritaire"
    elif score_total >= 7:
        decision = "🥈 Potentiel à évaluer en entretien"
    else:
        decision = "❌ Rejet"
    return {'score': score_total, 'score_max': 14, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {score_total}/14"}

def calculate_score_charge_admin_credit(cv_text, lettre_text, attestation_texts_list):
    poste = "Chargé(e) d'Administration de Crédit"
    grille = GRILLE[poste]
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized, _ = normalize_for_matching(raw_full)
    flags = []
    for crit in grille['eliminatoire']:
        ok, _, _ = check_criterion_semantic(crit, cv_text, lettre_text, poste, normalized, raw_full)
        if not ok:
            flags.append(crit)
    if flags:
        return {'score': 0, 'score_max': 12, 'decision': '❌ Rejet (éliminatoire)', 'flags_eliminatoires': flags, 'sous_scores': {}, 'checklist': {}, 'detail': f"ÉLIMINÉ: {len(flags)} critère(s)"}
    exp_criteria = ["Cycle de crédit", "Garanties", "Reportings", "Système bancaire"]
    n_exp = sum(1 for c in exp_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    adequation = min(3, n_exp)
    ifrs_criteria = ["IFRS 9 staging", "Comités de risque"]
    n_ifrs = sum(1 for c in ifrs_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    exposition_ifrs = min(3, n_ifrs)
    outils_ok = check_criterion_semantic("Système bancaire", cv_text, lettre_text, poste, normalized, raw_full)[0]
    rigueur_ok = check_criterion_semantic("Rigueur documentaire", cv_text, lettre_text, poste, normalized, raw_full)[0]
    rigueur_outils = min(2, sum([outils_ok, rigueur_ok]))
    n_attention = sum(1 for c in grille['points_attention'] if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    coherence = 2 if n_attention == 0 else (1 if n_attention <= 2 else 0)
    word_count = len(cv_text.split())
    has_quantified = bool(re.search(r'\d+\s*(%|dossiers|credits|portefeuille)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified) else 0
    lettre_clean = (lettre_text or '').strip()
    if lettre_clean:
        mentions = any(kw in lettre_clean.lower() for kw in ['administration de credit', 'credit', 'back-office', 'ifrs'])
        lettre_score = 1 if (len(lettre_clean.split()) >= 80 and mentions) else 0
    else:
        lettre_score = 0
    sous_scores = {
        "Adéquation expérience": adequation,
        "Exposition IFRS 9": exposition_ifrs,
        "Rigueur et outils": rigueur_outils,
        "Cohérence parcours": coherence,
        "Qualité CV": qualite_cv,
        "Lettre motivation": lettre_score
    }
    score_total = sum(sous_scores.values())
    if score_total >= 10:
        decision = "🥇 Entretien prioritaire"
    elif score_total >= 7:
        decision = "🥈 Entretien si besoin (vivier de réserve)"
    else:
        decision = "❌ Rejet"
    return {'score': score_total, 'score_max': 12, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {score_total}/12"}

def calculate_score_chef_section_compensation(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Section Compensation"
    grille = GRILLE[poste]
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized, _ = normalize_for_matching(raw_full)
    flags = []
    for crit in grille['eliminatoire']:
        ok, _, _ = check_criterion_semantic(crit, cv_text, lettre_text, poste, normalized, raw_full)
        if not ok:
            flags.append(crit)
    if flags:
        return {'score': 0, 'score_max': 12, 'decision': '❌ Rejet (éliminatoire)', 'flags_eliminatoires': flags, 'sous_scores': {}, 'checklist': {}, 'detail': f"ÉLIMINÉ: {len(flags)} critère(s)"}
    exp_criteria = ["Supervision compensation", "Dénouement positions", "Gestion suspens"]
    n_exp = sum(1 for c in exp_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    adequation = min(3, n_exp)
    beac_criteria = ["BEAC/GIMAC", "CEMAC"]
    n_beac = sum(1 for c in beac_criteria if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    exposition_beac = min(3, n_beac)
    encadrement_ok = check_criterion_semantic("Management", cv_text, lettre_text, poste, normalized, raw_full)[0]
    encadrement = 2 if encadrement_ok else 0
    n_attention = sum(1 for c in grille['points_attention'] if check_criterion_semantic(c, cv_text, lettre_text, poste, normalized, raw_full)[0])
    coherence = 2 if n_attention == 0 else (1 if n_attention <= 2 else 0)
    word_count = len(cv_text.split())
    has_quantified = bool(re.search(r'\d+\s*(%|operations|clients|agences)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified) else 0
    lettre_clean = (lettre_text or '').strip()
    if lettre_clean:
        mentions = any(kw in lettre_clean.lower() for kw in ['compensation', 'beac', 'gimac', 'interbancaire'])
        lettre_score = 1 if (len(lettre_clean.split()) >= 80 and mentions) else 0
    else:
        lettre_score = 0
    sous_scores = {
        "Adéquation expérience": adequation,
        "Exposition BEAC/GIMAC": exposition_beac,
        "Capacité encadrement": encadrement,
        "Cohérence parcours": coherence,
        "Qualité CV": qualite_cv,
        "Lettre motivation": lettre_score
    }
    score_total = sum(sous_scores.values())
    if score_total >= 10:
        decision = "🥇 Entretien prioritaire"
    elif score_total >= 7:
        decision = "🥈 Entretien si besoin (vivier de réserve)"
    else:
        decision = "❌ Rejet"
    return {'score': score_total, 'score_max': 12, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {score_total}/12"}

def calculate_detailed_score_100(cv_text, lettre_text, attestation_texts_list, poste):
    score_cv = {'CV_Exp': 0, 'CV_Niveau': 0, 'CV_Secteur': 0, 'CV_Tech': 0, 'CV_Progression': 0, 'CV_Management': 0, 'CV_Stabilite': 0}
    score_lm = {'LM_Comprehension': 0, 'LM_Coherence': 0, 'LM_Motivation': 0, 'LM_Qualite': 0}
    score_diplomes = {'D_Niveau': 0, 'D_Specialisation': 0, 'D_Certif': 0}
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + "\n".join(attestation_texts_list or [])
    years_found = 0
    for pattern in [r'(\d+)\s*(?:années|ans|years)', r'plus\s+de\s+(\d+)\s*(?:années|ans)']:
        for m in re.findall(pattern, raw_full, re.IGNORECASE):
            try:
                years_found = max(years_found, int(m))
            except:
                pass
    if years_found >= 10:
        score_cv['CV_Niveau'] = 10
    elif years_found >= 7:
        score_cv['CV_Niveau'] = 8
    elif years_found >= 5:
        score_cv['CV_Niveau'] = 6
    elif years_found >= 3:
        score_cv['CV_Niveau'] = 4
    score_cv['CV_Exp'] = 20 if years_found >= 5 else (10 if years_found >= 3 else 5)
    has_bank = any(re.search(r'\b' + re.escape(b) + r'\b', raw_full, re.IGNORECASE) for b in ['banque', 'bank', 'finance', 'credit'])
    if has_bank:
        score_cv['CV_Secteur'] = 10
    tech_count = sum(1 for kw in ['excel', 'vba', 'python', 'sql', 'reporting', 'dashboard'] if kw in raw_full.lower())
    score_cv['CV_Tech'] = min(20, tech_count * 4)
    mgmt_count = sum(1 for kw in ['management', 'equipe', 'supervision', 'encadrement'] if kw in raw_full.lower())
    score_cv['CV_Management'] = min(5, mgmt_count * 2)
    score_cv['CV_Stabilite'] = 5
    total_cv = sum(score_cv.values())
    score_cv_total = round((total_cv / 70) * 70) if total_cv > 0 else 0
    lm_text = lettre_text or ""
    if lm_text and len(lm_text) > 100:
        score_lm['LM_Qualite'] = min(5, len(lm_text.split()) // 30)
        score_lm['LM_Coherence'] = 3 if any(kw in lm_text.lower() for kw in ['experience', 'formation', 'competence']) else 1
    score_lm_total = sum(score_lm.values())
    has_bac5 = any(re.search(p, raw_full, re.IGNORECASE) for p in [r'bac\+\s*5', r'master', r'mba'])
    has_bac3 = any(re.search(p, raw_full, re.IGNORECASE) for p in [r'bac\+\s*3', r'licence'])
    score_diplomes['D_Niveau'] = 4 if has_bac5 else (2 if has_bac3 else 1)
    score_diplomes['D_Certif'] = min(3, sum(1 for c in ['acca', 'cpa', 'cfa', 'frm', 'itil', 'cia'] if c in raw_full.lower()))
    score_total = min(100, score_cv_total + score_lm_total + sum(score_diplomes.values()))
    if score_total >= 80:
        decision = "Shortlist"
    elif score_total >= 70:
        decision = "À considérer"
    elif score_total >= 60:
        decision = "Faible"
    else:
        decision = "Rejet"
    return {'score': score_total, 'decision': decision, 'bloc_cv': {'total': score_cv_total, 'max': 70, 'details': score_cv}, 'bloc_lm': {'total': score_lm_total, 'max': 20, 'details': score_lm}, 'bloc_diplomes': {'total': sum(score_diplomes.values()), 'max': 10, 'details': score_diplomes}, 'note': f"Score: {score_total}/100"}

# === FONCTION D'ANALYSE PRINCIPALE ===
def analyze_cv_against_grille_semantic(cv_text, lettre_text, attestation_texts_list, poste):
    if not cv_text or len(cv_text.strip()) < 50:
        return {'score': 0, 'checklist': {}, 'flags_eliminatoires': ['CV non analysable'], 'signaux_detectes': [], 'details': {'error': 'CV vide'}, 'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0}}
    grille = GRILLE.get(poste)
    if not grille:
        return {'score': 0, 'checklist': {}, 'flags_eliminatoires': [f'Poste inconnu: {poste}'], 'signaux_detectes': [], 'details': {}, 'score_breakdown': {}}
    all_att_raw = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw
    normalized, _ = normalize_for_matching(raw_full)
    checklist = {}
    flags_elim = []
    signaux = []
    details = {'cv_words': len(cv_text.split()), 'lettre_words': len((lettre_text or "").split()), 'criteres_valides_bloc2': [], 'signaux_valides_bloc3': [], 'alertes_attention': []}
    eliminatoire_failed = False
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        is_present, _, _ = check_criterion_semantic(crit, cv_text, lettre_text, poste, normalized, raw_full)
        checklist[key] = is_present
        if not is_present:
            eliminatoire_failed = True
            flags_elim.append(f"❌ {crit}")
    if eliminatoire_failed:
        return {'score': 0, 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': [], 'details': details, 'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0}}
    points_bloc2 = 0
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, _, _ = check_criterion_semantic(crit, cv_text, lettre_text, poste, normalized, raw_full)
        checklist[key] = is_present
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(crit)
    points_bloc3 = 0
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, _, _ = check_criterion_semantic(crit, cv_text, lettre_text, poste, normalized, raw_full)
        checklist[key] = is_present
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(crit)
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, _, _ = check_criterion_semantic(crit, cv_text, lettre_text, poste, normalized, raw_full)
        checklist[key] = is_present
        if is_present:
            details['alertes_attention'].append(crit)
    if poste == "Chef de Section Compensation":
        result = calculate_score_chef_section_compensation(cv_text, lettre_text, attestation_texts_list)
        return {'score': result['score'], 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': signaux, 'details': details, 'score_breakdown': {'bloc1_eliminatoire': False, 'sous_scores': result.get('sous_scores', {}), 'score_final': result['score'], 'score_max': 12, 'decision': result['decision']}}
    elif poste == "Chargé(e) d'Administration de Crédit":
        result = calculate_score_charge_admin_credit(cv_text, lettre_text, attestation_texts_list)
        return {'score': result['score'], 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': signaux, 'details': details, 'score_breakdown': {'bloc1_eliminatoire': False, 'sous_scores': result.get('sous_scores', {}), 'score_final': result['score'], 'score_max': 12, 'decision': result['decision']}}
    elif poste == "Chef de Division Local Corporate":
        result = calculate_score_chef_division_corporate(cv_text, lettre_text, attestation_texts_list)
        return {'score': result['score'], 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': signaux, 'details': details, 'score_breakdown': {'bloc1_eliminatoire': False, 'sous_scores': result.get('sous_scores', {}), 'score_final': result['score'], 'score_max': 14, 'decision': result['decision'], 'points_forts': result.get('points_forts', []), 'points_vigilance': result.get('points_vigilance', []), 'synthese_recruteur': result.get('synthese_recruteur', '')}}
    elif poste in POSTES_AVEC_SCORING_100:
        result = calculate_detailed_score_100(cv_text, lettre_text, attestation_texts_list, poste)
        return {'score': result['score'], 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': signaux, 'details': details, 'score_breakdown': {'bloc1_eliminatoire': False, 'scoring_type': '100_points', 'bloc_cv': result['bloc_cv'], 'bloc_lm': result['bloc_lm'], 'bloc_diplomes': result['bloc_diplomes'], 'score_final': result['score'], 'decision': result['decision']}}
    adequation = min(3, len([k for k, v in checklist.items() if k.startswith('elim_') and v]))
    coherence = min(2, points_bloc2)
    risque_metier = min(3, len(signaux))
    qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre_motiv = 1 if lettre_text and len(lettre_text.strip()) > 50 else 0
    score_final = min(10, adequation + coherence + risque_metier + qualite_cv + lettre_motiv)
    return {'score': score_final, 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': signaux, 'details': details, 'score_breakdown': {'bloc1_eliminatoire': False, 'score_final': score_final}}

# === FONCTIONS D'EXPORT AVEC FILTRES ===
def get_filtered_candidats(candidats_data, poste_filter=None, date_start=None, date_end=None, statut_filter=None, min_score=None):
    """Filtre les candidats selon les critères"""
    filtered = candidats_data
    if poste_filter:
        filtered = [c for c in filtered if c.get('poste') == poste_filter]
    if statut_filter:
        filtered = [c for c in filtered if c.get('statut') == statut_filter]
    if date_start:
        filtered = [c for c in filtered if c.get('date_candidature', '') >= date_start]
    if date_end:
        filtered = [c for c in filtered if c.get('date_candidature', '') <= date_end + 'T23:59:59']
    if min_score is not None:
        filtered = [c for c in filtered if int(c.get('score', 0)) >= min_score]
    return filtered

def generate_excel_report(candidats_data, poste_filter=None, date_start=None, date_end=None, statut_filter=None, min_score=None):
    if not OPENPYXL_AVAILABLE:
        return None
    filtered = get_filtered_candidats(candidats_data, poste_filter, date_start, date_end, statut_filter, min_score)
    wb = Workbook()
    ws = wb.active
    ws.title = "Candidatures"
    headers = ['Numéro Dossier', 'Nom', 'Prénom', 'Email', 'Téléphone', 'Poste', 'Statut', 'Score', 'Date Candidature', 'Note']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1a3a5c", end_color="1a3a5c", fill_type="solid")
        cell.alignment = Alignment(horizontal="center")
    for row_idx, c in enumerate(filtered, 2):
        ws.cell(row=row_idx, column=1, value=c.get('numero_dossier', ''))
        ws.cell(row=row_idx, column=2, value=c.get('nom', ''))
        ws.cell(row=row_idx, column=3, value=c.get('prenom', ''))
        ws.cell(row=row_idx, column=4, value=c.get('email', ''))
        ws.cell(row=row_idx, column=5, value=c.get('telephone', ''))
        ws.cell(row=row_idx, column=6, value=c.get('poste', ''))
        ws.cell(row=row_idx, column=7, value=c.get('statut', ''))
        ws.cell(row=row_idx, column=8, value=c.get('score', 0))
        ws.cell(row=row_idx, column=9, value=c.get('date_candidature', '')[:10] if c.get('date_candidature') else '')
        ws.cell(row=row_idx, column=10, value=c.get('note', ''))
    for col in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 20
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def generate_csv_report(candidats_data, poste_filter=None, date_start=None, date_end=None, statut_filter=None, min_score=None):
    filtered = get_filtered_candidats(candidats_data, poste_filter, date_start, date_end, statut_filter, min_score)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Numéro Dossier', 'Nom', 'Prénom', 'Email', 'Téléphone', 'Poste', 'Statut', 'Score', 'Date Candidature', 'Note'])
    for c in filtered:
        writer.writerow([
            c.get('numero_dossier', ''), c.get('nom', ''), c.get('prenom', ''),
            c.get('email', ''), c.get('telephone', ''), c.get('poste', ''),
            c.get('statut', ''), c.get('score', 0),
            c.get('date_candidature', '')[:10] if c.get('date_candidature') else '',
            c.get('note', '')
        ])
    return io.BytesIO(output.getvalue().encode('utf-8-sig'))

def generate_pdf_report(candidats_data, poste_filter=None, date_start=None, date_end=None, statut_filter=None, min_score=None):
    if not REPORTLAB_AVAILABLE:
        return None
    filtered = get_filtered_candidats(candidats_data, poste_filter, date_start, date_end, statut_filter, min_score)
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontSize=16, alignment=TA_CENTER, spaceAfter=20)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Normal'], fontSize=10, alignment=TA_CENTER, spaceAfter=15, textColor=colors.grey)
    # Filtres info
    filters_info = []
    if poste_filter:
        filters_info.append(f"Poste: {poste_filter}")
    if date_start:
        filters_info.append(f"Du: {date_start}")
    if date_end:
        filters_info.append(f"Au: {date_end}")
    if statut_filter:
        filters_info.append(f"Statut: {statut_filter}")
    if min_score is not None:
        filters_info.append(f"Score ≥ {min_score}")
    filter_text = " | ".join(filters_info) if filters_info else "Tous les candidats"
    data = [['N° Dossier', 'Nom', 'Prénom', 'Poste', 'Statut', 'Score']]
    for c in filtered:
        data.append([
            c.get('numero_dossier', '')[:10],
            c.get('nom', '')[:15],
            c.get('prenom', '')[:15],
            c.get('poste', '')[:20],
            c.get('statut', '')[:15],
            str(c.get('score', 0))
        ])
    table = Table(data, colWidths=[2*cm, 3*cm, 3*cm, 4*cm, 3*cm, 2*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a5c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey])
    ]))
    elements = [
        Paragraph("Rapport des Candidatures", title_style),
        Paragraph(f"Filtres: {filter_text}", subtitle_style),
        Spacer(1, 0.5*cm),
        table,
        Spacer(1, 1*cm),
        Paragraph(f"Total: {len(filtered)} candidat(s) | Généré le {datetime.datetime.now().strftime('%d/%m/%Y à %H:%M')}", styles['Normal'])
    ]
    doc.build(elements)
    output.seek(0)
    return output

def generate_word_report(candidats_data, poste_filter=None, date_start=None, date_end=None, statut_filter=None, min_score=None):
    if not DOCX_AVAILABLE:
        return None
    filtered = get_filtered_candidats(candidats_data, poste_filter, date_start, date_end, statut_filter, min_score)
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document()
    title = doc.add_heading('Rapport des Candidatures', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # Filtres
    filters_info = []
    if poste_filter:
        filters_info.append(f"Poste: {poste_filter}")
    if date_start:
        filters_info.append(f"Du: {date_start}")
    if date_end:
        filters_info.append(f"Au: {date_end}")
    if statut_filter:
        filters_info.append(f"Statut: {statut_filter}")
    if min_score is not None:
        filters_info.append(f"Score ≥ {min_score}")
    filter_text = " | ".join(filters_info) if filters_info else "Tous les candidats"
    doc.add_paragraph(f"Filtres: {filter_text}")
    doc.add_paragraph(f"Total: {len(filtered)} candidat(s)")
    doc.add_paragraph("")
    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = 'N° Dossier'
    hdr_cells[1].text = 'Nom'
    hdr_cells[2].text = 'Prénom'
    hdr_cells[3].text = 'Poste'
    hdr_cells[4].text = 'Statut'
    hdr_cells[5].text = 'Score'
    for c in filtered:
        row_cells = table.add_row().cells
        row_cells[0].text = c.get('numero_dossier', '')[:10]
        row_cells[1].text = c.get('nom', '')[:20]
        row_cells[2].text = c.get('prenom', '')[:20]
        row_cells[3].text = c.get('poste', '')[:25]
        row_cells[4].text = c.get('statut', '')
        row_cells[5].text = str(c.get('score', 0))
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    return output

# === TÉLÉCHARGEMENT ZIP AVEC FILTRES ===
def download_dossiers_zip_filtered(candidats_data, poste_filter=None, date_start=None, date_end=None):
    filtered = get_filtered_candidats(candidats_data, poste_filter, date_start, date_end)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for c in filtered:
            cv_fn = c.get('cv_filename')
            if cv_fn:
                cv_bytes = download_file_from_supabase(cv_fn)
                if cv_bytes:
                    ext = cv_fn.rsplit('.', 1)[-1].lower() if '.' in cv_fn else 'pdf'
                    zip_file.writestr(f"{c.get('prenom')}_{c.get('nom')}_CV.{ext}", cv_bytes)
            lm_fn = c.get('lettre_filename')
            if lm_fn:
                lm_bytes = download_file_from_supabase(lm_fn)
                if lm_bytes:
                    ext = lm_fn.rsplit('.', 1)[-1].lower() if '.' in lm_fn else 'pdf'
                    zip_file.writestr(f"{c.get('prenom')}_{c.get('nom')}_Lettre.{ext}", lm_bytes)
            att_filenames = []
            try:
                att_filenames = json.loads(c.get('attestation_filenames', '[]'))
            except:
                pass
            for idx, att_fn in enumerate(att_filenames):
                if att_fn:
                    att_bytes = download_file_from_supabase(att_fn)
                    if att_bytes:
                        ext = att_fn.rsplit('.', 1)[-1].lower() if '.' in att_fn else 'pdf'
                        zip_file.writestr(f"{c.get('prenom')}_{c.get('nom')}_Certificat_{idx+1}.{ext}", att_bytes)
    zip_buffer.seek(0)
    return zip_buffer

# === RÉANALYSE ===
reanalyze_status = {"in_progress": False, "total": 0, "processed": 0, "success": 0, "errors": 0}

def run_analysis_for_candidat_semantic(token, cv_filename, lettre_filename, attestation_filenames, poste, force=False):
    try:
        if not force and not is_poste_actif(poste):
            if supabase:
                supabase.table('candidats').update({"analyse_status": "skipped_closed_post"}).eq('token', token).execute()
            return
        cv_text = ""
        if cv_filename:
            cv_bytes = download_file_from_supabase(cv_filename)
            if cv_bytes:
                cv_text = extract_text_robust_from_bytes(cv_bytes, cv_filename)
        lm_text = ""
        if lettre_filename:
            lm_bytes = download_file_from_supabase(lettre_filename)
            if lm_bytes:
                lm_text = extract_text_robust_from_bytes(lm_bytes, lettre_filename)
        att_texts = []
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        for fn in (attestation_filenames or []):
            if fn:
                att_bytes = download_file_from_supabase(fn)
                if att_bytes:
                    t = extract_text_robust_from_bytes(att_bytes, fn)
                    if t:
                        att_texts.append(t)
        if not cv_text:
            return
        result = analyze_cv_against_grille_semantic(cv_text, lm_text, att_texts, poste)
        if result and supabase:
            update_data = {
                "score": str(result['score']),
                "checklist": json.dumps(result.get('checklist', {}), ensure_ascii=False),
                "flags_eliminatoires": json.dumps(result['flags_eliminatoires'], ensure_ascii=False),
                "signaux_detectes": json.dumps(result['signaux_detectes'], ensure_ascii=False),
                "analyse_details": json.dumps(result['details'], ensure_ascii=False),
                "score_breakdown": json.dumps(result['score_breakdown'], ensure_ascii=False),
                "decision": result['score_breakdown'].get('decision', ''),
                "analyse_auto_date": datetime.datetime.now().isoformat(),
                "analyse_status": "completed"
            }
            if result['score_breakdown'].get('bloc1_eliminatoire'):
                update_data['statut'] = 'exclu'
            supabase.table('candidats').update(update_data).eq('token', token).execute()
    except Exception as e:
        logger.error(f"Analyse error: {e}")

def auto_reanalyze_active_postes():
    global reanalyze_status
    if reanalyze_status["in_progress"]:
        return {"status": "already_running"}
    if not supabase:
        return {"status": "error", "message": "Supabase non configuré"}
    try:
        response = supabase.table('candidats').select('*').in_('poste', POSTES_ACTIFS).execute()
        candidats = response.data if response.data else []
        if not candidats:
            return {"status": "no_candidates"}
        reanalyze_status = {"in_progress": True, "total": len(candidats), "processed": 0, "success": 0, "errors": 0}
        def run_reanalysis():
            global reanalyze_status
            for c in candidats:
                try:
                    token = c.get('token')
                    cv_fn = c.get('cv_filename')
                    lm_fn = c.get('lettre_filename')
                    att_raw = c.get('attestation_filenames', '[]')
                    poste = c.get('poste')
                    if cv_fn:
                        run_analysis_for_candidat_semantic(token, cv_fn, lm_fn, att_raw, poste, True)
                        reanalyze_status["success"] += 1
                    else:
                        reanalyze_status["errors"] += 1
                except:
                    reanalyze_status["errors"] += 1
                finally:
                    reanalyze_status["processed"] += 1
            reanalyze_status["in_progress"] = False
        threading.Thread(target=run_reanalysis, daemon=True).start()
        return {"status": "started", "total": len(candidats)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def init_recruteur():
    try:
        if supabase:
            response = supabase.table('recruteurs').select('*').eq('email', 'sougnabeoualoumibank@gmail.com').execute()
            if not response.data:
                supabase.table('recruteurs').insert({
                    "email": "sougnabeoualoumibank@gmail.com",
                    "password": hash_pwd("AdminLaurent123"),
                    "nom": "Responsable RH"
                }).execute()
    except Exception as e:
        logger.warning(f"Init error: {e}")

init_recruteur()

# === ROUTES ===
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
        return jsonify({'error': 'Poste inconnu'}), 404
    return jsonify(g), 200

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    pwd = hash_pwd(data.get('password', ''))
    if supabase:
        response = supabase.table('recruteurs').select('*').eq('email', email).execute()
        if response.data and len(response.data) > 0:
            r = response.data[0]
            if r.get("password") == pwd:
                token = create_access_token(identity=str(r["id"]))
                return jsonify({'token': token, 'nom': r["nom"], 'email': r["email"]}), 200
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
            return jsonify({'error': 'Champs obligatoires manquants'}), 400
        if supabase:
            existing = supabase.table('candidats').select('*').eq('email', email).eq('poste', poste).execute()
            if existing.data and len(existing.data) > 0:
                return jsonify({'error': f'Vous avez déjà postulé pour "{poste}"'}), 409
            all_candidats = supabase.table('candidats').select('numero_dossier').eq('poste', poste).execute()
            max_num = 0
            for c in all_candidats.data:
                try:
                    num_val = int(c.get('numero_dossier', 0))
                    if num_val > max_num:
                        max_num = num_val
                except:
                    pass
            numero_dossier = str(max_num + 1)
        def save_file(field, suffix):
            f = request.files.get(field)
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_{suffix}.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                return result if result else ''
            return ''
        cv_filename = save_file('cv', 'cv')
        if request.files.get('cv') and not cv_filename:
            return jsonify({'error': "Échec de l'envoi du CV"}), 500
        lettre_filename = save_file('lettre', 'lettre')
        att_filenames = []
        for f in request.files.getlist('attestation'):
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_attestation.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                if result:
                    att_filenames.append(blob_name)
        token = uuid.uuid4().hex
        supabase.table('candidats').insert({
            "token": token, "nom": nom, "prenom": prenom, "email": email,
            "telephone": telephone, "poste": poste, "numero_dossier": numero_dossier,
            "cv_filename": cv_filename, "lettre_filename": lettre_filename,
            "attestation_filenames": json.dumps(att_filenames, ensure_ascii=False),
            "statut": "en_attente", "note": "", "score": "0", "analyse_status": "pending",
            "date_candidature": datetime.datetime.now().isoformat()
        }).execute()
        if is_poste_actif(poste):
            threading.Thread(target=run_analysis_for_candidat_semantic, args=(token, cv_filename, lettre_filename, att_filenames, poste, False), daemon=True).start()
        nom_complet = f"{prenom} {nom}".strip()
        sujet_confirmation = f"Confirmation de candidature – {poste}"
        corps_confirmation = f"Bonjour {nom_complet},\nNous accusons réception de votre candidature pour le poste de {poste}.\nVotre numéro de suivi: {token}\nCordialement."
        threading.Thread(target=send_email, args=(email, sujet_confirmation, corps_confirmation), daemon=True).start()
        return jsonify({'message': 'Candidature soumise', 'token': token, 'numero_dossier': numero_dossier}), 201
    except Exception as e:
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
        return jsonify({'error': 'Supabase non configuré'}), 500
    response = supabase.table('candidats').select('*').execute()
    keys = response.data if response.data else []
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0, "rejete": 0, "entretien": 0, "by_poste": []}
    counts = {}
    for c in keys:
        s = c.get('statut', 'en_attente')
        if s in stats:
            stats[s] += 1
        p = c.get('poste', 'Inconnu')
        counts[p] = counts.get(p, 0) + 1
    stats['by_poste'] = [{'poste': p, 'n': n} for p, n in sorted(counts.items(), key=lambda x: -x[1])]
    return jsonify(stats), 200

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    poste_filter = request.args.get('poste', '')
    statut_filter = request.args.get('statut', '')
    search = request.args.get('search', '').lower()
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    min_score = request.args.get('min_score', type=int)
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    response = supabase.table('candidats').select('*').execute()
    all_candidats = response.data if response.data else []
    result = []
    for c in all_candidats:
        c['id'] = c.get('token', '')
        if poste_filter and c.get('poste') != poste_filter:
            continue
        if statut_filter and c.get('statut') != statut_filter:
            continue
        if min_score is not None and int(c.get('score', 0)) < min_score:
            continue
        if date_start and c.get('date_candidature', '') < date_start:
            continue
        if date_end and c.get('date_candidature', '') > date_end + 'T23:59:59':
            continue
        if search:
            hay = (f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} {c.get('poste','')} {c.get('numero_dossier','')}").lower()
            if search not in hay:
                continue
        if c.get('score_breakdown'):
            try:
                c['score_breakdown_parsed'] = json.loads(c['score_breakdown'])
            except:
                pass
        result.append(c)
    result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
    return jsonify(result), 200

@app.route('/api/recruteur/candidats/<token>', methods=['GET'])
@jwt_required()
def get_candidat_detail(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    data['id'] = token
    return jsonify(data), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = request.get_json(silent=True) or {}
    statut = data.get('statut', 'en_attente')
    note = data.get('note', '')
    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    supabase.table('candidats').update({"statut": statut, "note": note}).eq('token', token).execute()
    return jsonify({'message': 'Mis à jour', 'statut': statut}), 200

@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    cv_fn = data.get('cv_filename')
    lm_fn = data.get('lettre_filename')
    att_raw = data.get('attestation_filenames', '[]')
    poste = data.get('poste')
    if not cv_fn:
        return jsonify({'error': 'CV manquant'}), 400
    force = request.args.get('force', '0') == '1'
    if not force and not is_poste_actif(poste):
        return jsonify({'error': f'Poste "{poste}" clôturé'}), 403
    threading.Thread(target=run_analysis_for_candidat_semantic, args=(token, cv_fn, lm_fn, att_raw, poste, force), daemon=True).start()
    return jsonify({'message': 'Analyse déclenchée'}), 202

@app.route('/api/recruteur/auto-reanalyze', methods=['POST'])
@jwt_required()
def trigger_auto_reanalyze():
    result = auto_reanalyze_active_postes()
    return jsonify(result), 200

@app.route('/api/recruteur/reanalyze-status', methods=['GET'])
@jwt_required()
def get_reanalyze_status():
    global reanalyze_status
    progress = 0
    if reanalyze_status["total"] > 0:
        progress = round((reanalyze_status["processed"] / reanalyze_status["total"]) * 100)
    return jsonify({
        "in_progress": reanalyze_status["in_progress"],
        "total": reanalyze_status["total"],
        "processed": reanalyze_status["processed"],
        "success": reanalyze_status["success"],
        "errors": reanalyze_status["errors"],
        "progress": progress
    }), 200

@app.route('/api/recruteur/export/<format>', methods=['GET'])
@jwt_required()
def export_report(format):
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    # Récupérer tous les filtres
    poste_filter = request.args.get('poste', '')
    statut_filter = request.args.get('statut', '')
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    min_score = request.args.get('min_score', type=int)
    response = supabase.table('candidats').select('*').execute()
    candidats = response.data if response.data else []
    format = format.lower()
    if format == 'excel':
        output = generate_excel_report(candidats, poste_filter, date_start, date_end, statut_filter, min_score)
        if not output:
            return jsonify({'error': 'Openpyxl non disponible'}), 500
        filename = f"rapport_candidats_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=filename)
    elif format == 'csv':
        output = generate_csv_report(candidats, poste_filter, date_start, date_end, statut_filter, min_score)
        filename = f"rapport_candidats_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return send_file(output, mimetype='text/csv', as_attachment=True, download_name=filename)
    elif format == 'pdf':
        output = generate_pdf_report(candidats, poste_filter, date_start, date_end, statut_filter, min_score)
        if not output:
            return jsonify({'error': 'Reportlab non disponible'}), 500
        filename = f"rapport_candidats_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name=filename)
    elif format == 'word':
        output = generate_word_report(candidats, poste_filter, date_start, date_end, statut_filter, min_score)
        if not output:
            return jsonify({'error': 'python-docx non disponible'}), 500
        filename = f"rapport_candidats_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', as_attachment=True, download_name=filename)
    return jsonify({'error': 'Format non supporté'}), 400

@app.route('/api/recruteur/dossiers/zip', methods=['GET'])
@jwt_required()
def download_dossiers_zip():
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    poste_filter = request.args.get('poste', '')
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    response = supabase.table('candidats').select('*').execute()
    candidats = response.data if response.data else []
    output = download_dossiers_zip_filtered(candidats, poste_filter, date_start, date_end)
    filename = f"dossiers_candidats_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(output, mimetype='application/zip', as_attachment=True, download_name=filename)

@app.route('/api/recruteur/debug/test-gemini', methods=['POST'])
@jwt_required()
def test_gemini():
    if not GEMINI_ACTIVE:
        return jsonify({'error': 'Gemini non actif'}), 400
    data = request.get_json(silent=True) or {}
    criterion = data.get('criterion', "Expérience en Corporate Banking")
    text = data.get('text', "Responsable du développement des relations avec les grands comptes")
    valide, confiance, justification, elements = check_criterion_with_gemini(criterion, text, "", "Chef de Division Local Corporate")
    return jsonify({'criterion': criterion, 'valide': valide, 'confiance': confiance, 'justification': justification, 'elements_trouves': elements}), 200

@app.route('/api/health-version', methods=['GET'])
def health_version():
    return jsonify({
        "version": "v4.0-complete",
        "postes_actifs": POSTES_ACTIFS,
        "gemini_active": GEMINI_ACTIVE,
        "gemini_model": GEMINI_MODEL if GEMINI_ACTIVE else "inactif",
        "exports": {"excel": OPENPYXL_AVAILABLE, "pdf": REPORTLAB_AVAILABLE, "word": DOCX_AVAILABLE}
    }), 200

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    if GEMINI_ACTIVE:
        print(f"🧠 Gemini activé: {GEMINI_MODEL}")
    print(f"📊 Export Excel: {'✅' if OPENPYXL_AVAILABLE else '❌'}")
    print(f"📄 Export PDF: {'✅' if REPORTLAB_AVAILABLE else '❌'}")
    print(f"📝 Export Word: {'✅' if DOCX_AVAILABLE else '❌'}")
    app.run(host="0.0.0.0", port=port, debug=False)

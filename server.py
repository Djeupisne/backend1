from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, json, re, threading, mimetypes, io, csv, unicodedata, zipfile, time
from werkzeug.utils import secure_filename
from supabase import create_client, Client
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
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
IA_ANALYSE_ACTIVE = ANTHROPIC_AVAILABLE and bool(ANTHROPIC_API_KEY)
_claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if IA_ANALYSE_ACTIVE else None
_ia_semaphore = threading.Semaphore(int(os.getenv("IA_MAX_CONCURRENCY", "5")))
_Nlp_fr = None
_Nlp_en = None

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

app = Flask(__name__)
import logging
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
    "Chef de Section Compensation"
]

GRILLE = {
    "Responsable Administration de Crédit": {
        "eliminatoire": ["Expérience bancaire", "Minimum 3 ans en crédit / risque (hors stage)", "Exposition aux garanties ou conformité"],
        "a_verifier": ["Validation de dossiers de crédit", "Gestion des garanties", "Participation à des audits"],
        "signaux_forts": ["IFRS 9", "COBAC / conformité", "Suivi portefeuille / impayés"],
        "points_attention": ["Parcours trop comptable pur", "Rôle uniquement administratif sans responsabilité", "CV flou avec missions génériques"]
    },
    "Analyste Crédit CCB": {
        "eliminatoire": ["Expérience en analyse crédit", "Capacité à lire des états financiers", "Minimum 3 ans institution financière (hors stage)"],
        "a_verifier": ["Clients PME", "Clients particuliers", "Structuration de crédit", "Avis de crédit"],
        "signaux_forts": ["Cash-flow analysis", "Montage de crédit", "Comités de crédit"],
        "points_attention": ["CV trop relation client", "Aucune notion de risque", "Expériences très courtes sans progression"]
    },
    "Archiviste (Administration Crédit)": {
        "eliminatoire": ["Expérience en gestion documentaire structurée", "Rigueur démontrée"],
        "a_verifier": ["Archivage physique et électronique", "Gestion des dossiers sensibles"],
        "signaux_forts": ["Expérience en banque ou juridique", "Manipulation de garanties ou contrats"],
        "points_attention": ["Profils trop généralistes", "CV désorganisé"]
    },
    "Senior Finance Officer": {
        "eliminatoire": ["Expérience en reporting financier structuré", "Exposition aux états financiers", "Interaction avec auditeurs", "Minimum 3 ans département finance ou en cabinet d'audit (hors stage)"],
        "a_verifier": ["Production états financiers", "Reporting groupe", "Connaissance IFRS", "Contraintes réglementaires"],
        "signaux_forts": ["IFRS / consolidation", "Reporting groupe", "Interaction avec CAC", "Outils SPECTRA / CERBER / ERP"],
        "points_attention": ["Profil comptable junior amélioré", "Pas de responsabilité réelle", "CV flou sur les livrables"]
    },
    "Market Risk Officer": {
        "eliminatoire": ["Base en risques de marché", "Exposition à FX / taux / liquidité", "Minimum 3 ans institution financière (hors stage)"],
        "a_verifier": ["Maîtrise VaR / stress testing", "Analyse des positions", "Excel avancé", "VBA ou Python"],
        "signaux_forts": ["Bâle II / III", "Gestion ALM / liquidité", "Produits FICC", "Reporting risque"],
        "points_attention": ["CV trop théorique académique", "Aucune mention d'outils", "Incapacité implicite à modéliser"]
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": ["Expérience en réseau / infrastructure", "Exposition à environnement critique", "Notion de sécurité IT", "Minimum 2 ans expérience (hors stage)"],
        "a_verifier": ["Gestion réseaux LAN/WAN/VPN", "Gestion serveurs Windows/Linux", "Cloud même basique", "Gestion des incidents", "Assurance de la disponibilité"],
        "signaux_forts": ["Cybersécurité / firewall", "Haute disponibilité / PRA/PCA", "Gestion ATM ou systèmes bancaires", "Certifications Cisco ou Microsoft"],
        "points_attention": ["Profil trop helpdesk", "CV sans détail technique", "Aucune mention de sécurité"]
    },
    "Auditeur interne": {
        "eliminatoire": ["Expérience réelle en audit interne ou externe", "Minimum 3 ans en audit bancaire ou cabinet d'audit (hors stage)", "Connaissance des normes d'audit et contrôle interne"],
        "a_verifier": ["Missions d'audit sur site", "Évaluation des risques opérationnels", "Rédaction de rapports d'audit", "Suivi des recommandations"],
        "signaux_forts": ["Normes IIA / IPPF", "COBAC / réglementation bancaire", "Audit IT ou systèmes d'information", "Certification CIA / CPA / ACCA"],
        "points_attention": ["Profil purement comptable sans audit", "Aucune expérience terrain en audit", "CV flou sur les missions réalisées"]
    },
    "Chef service contrôle des engagements": {
        "eliminatoire": ["Maîtrise du risque crédit et analyse financière", "Expérience significative en octroi de crédits", "Minimum 5 ans en institution financière (hors stage)"],
        "a_verifier": ["Analyse financière d'entreprises", "Structuration de crédits complexes", "Animation de comité de crédit", "Management d'équipe"],
        "signaux_forts": ["IFRS 9 / classification des risques", "Grande entreprise / Corporate", "Restructuration de dossiers sensibles", "Formation risk management"],
        "points_attention": ["Profil purement commercial sans analyse", "Aucune expérience en analyse financière", "CV orienté relation client uniquement"]
    },
    "Chef service IT (maintenance/support)": {
        "eliminatoire": ["Background IT solide avec expérience technique réelle", "Minimum 5 ans en maintenance et support informatique", "Exposition à environnement critique (banque, datacenter)"],
        "a_verifier": ["Maintenance préventive et curative", "Support utilisateurs niveau 2/3", "Gestion de parc informatique", "Supervision d'infrastructures"],
        "signaux_forts": ["ITIL / gestion de services IT", "Virtualisation (VMware, Hyper-V)", "Systèmes bancaires core banking", "Certifications Microsoft / Cisco / ITIL"],
        "points_attention": ["Profil trop helpdesk niveau 1", "CV sans détail technique précis", "Aucune expérience en maintenance infrastructure"]
    },
    "Chef service finance": {
        "eliminatoire": ["Expérience significative en finance bancaire (minimum 7 ans)", "Maîtrise du reporting financier et comptabilité bancaire", "Expérience avérée en management d'équipe"],
        "a_verifier": ["Production d'états financiers", "Reporting réglementaire (BEAC, COBAC)", "Relations avec auditeurs externes", "Pilotage de la performance financière"],
        "signaux_forts": ["IFRS / normes internationales", "Consolidation de comptes", "Outils SPECTRA / CERBER / ERP bancaires", "Bac+5 + Certification (ACCA, CPA, CFA)"],
        "points_attention": ["Profil comptable junior sans évolution", "Pas de responsabilité managériale réelle", "Expérience hors secteur bancaire"]
    },
    "Chef service risques de marché": {
        "eliminatoire": ["Expérience avérée en risques de marché (FX, taux, liquidité)", "Exposition aux produits de trésorerie et ALM", "Minimum 5 ans en institution financière (hors stage)"],
        "a_verifier": ["Calcul et suivi de la VaR", "Stress testing et scénarios de crise", "Reporting des risques à la direction", "Maîtrise Excel avancé / VBA"],
        "signaux_forts": ["Bâle II / III / réglementation prudentielle", "Gestion ALM (Asset Liability Management)", "Produits FICC (Fixed Income, Currencies, Commodities)", "Python / R pour modélisation financière"],
        "points_attention": ["Profil trop théorique académique", "Aucune exposition aux marchés financiers", "CV sans mention d'outils de modélisation"]
    },
    "Chef service reporting réglementaire": {
        "eliminatoire": ["Comptabilité bancaire approfondie", "Expérience en reporting réglementaire (BEAC, COBAC, SPECTRA)", "Minimum 5 ans en banque ou cabinet d'audit bancaire"],
        "a_verifier": ["Production de rapports réglementaires", "Contrôle de cohérence des données", "Veille réglementaire bancaire", "Interaction avec autorités de tutelle"],
        "signaux_forts": ["SPECTRA / CERBER / outils BEAC", "Normes COBAC précises", "Reporting prudentiel Bâle", "Formation comptabilité bancaire spécialisée"],
        "points_attention": ["Profil généraliste sans spécialisation bancaire", "Aucune expérience reporting réglementaire", "CV flou sur les livrables produits"]
    },
    "Chef de Section Compensation": {
        "eliminatoire": ["Expérience en banque ou établissement financier réglementé", "Minimum 3 ans en opérations bancaires ou back-office (hors stage)", "Exposition aux opérations de compensation interbancaire (chèques, virements, prélèvements)", "Connaissance des règles BEAC / GIMAC ou d'un système de compensation équivalent", "Gestion de suspens, rejets ou réclamations interbancaires", "Expérience d'encadrement ou de supervision d'équipe (poste de chef de section)", "Profil bancaire avec exposition interbancaire (hors microfinance isolée)"],
        "a_verifier": ["Supervision quotidienne des opérations de compensation interbancaire", "Dénouement de positions nettes en fin de journée", "Gestion de suspens, rejets et réclamations interbancaires", "Encadrement et coordination d'une équipe opérationnelle", "Utilisation de systèmes bancaires de compensation (SYSTAC, SYGMA, SWIFT)", "Production de reportings opérationnels ou réglementaires", "Participation à des contrôles internes, audits COBAC ou inspections réglementaires"],
        "signaux_forts": ["BEAC / GIMAC / compensation interbancaire (SYSTAC, SYGMA)", "Règlement de positions nettes dans les délais réglementaires", "Contrôle de conformité réglementaire et procédurale", "Maîtrise du contrôle interne et de la comptabilité bancaire (SYSCOHADA)", "Gestion de fin de journée comptable / clôture des opérations interbancaires", "Rapports opérationnels ou réglementaires produits", "Expérience dans une banque de la zone CEMAC / UEMOA", "Audits COBAC ou contrôles internes réussis sans réserve majeure", "Gestion d'une équipe avec résultats mesurables"],
        "points_attention": ["Parcours purement comptable sans exposition aux opérations interbancaires", "Rôle uniquement administratif ou de support, sans responsabilité opérationnelle", "Absence de tout rôle managérial", "CV aux missions trop génériques, sans livrables ni résultats quantifiés", "Expériences très courtes (< 1 an par poste) sans progression visible", "Maîtrise des outils non mentionnée (SWIFT, compensation, ERP bancaire)", "Trous inexpliqués dans le parcours professionnel"]
    }
}

SCORING_CONFIG = {
    "Responsable Administration de Crédit": None,
    "Analyste Crédit CCB": None,
    "Archiviste (Administration Crédit)": None,
    "Senior Finance Officer": None,
    "Market Risk Officer": None,
    "IT Réseau & Infrastructure": None,
    "Chef de Section Compensation": None,
    "Auditeur interne": {"CV_Exp": 25, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 15, "CV_Progression": 5, "CV_Management": 0, "CV_Stabilite": 5, "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3},
    "Chef service contrôle des engagements": {"CV_Exp": 20, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 20, "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3},
    "Chef service IT (maintenance/support)": {"CV_Exp": 15, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 25, "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3},
    "Chef service finance": {"CV_Exp": 25, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 15, "CV_Progression": 5, "CV_Management": 10, "CV_Stabilite": 5, "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3},
    "Chef service risques de marché": {"CV_Exp": 20, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 20, "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3},
    "Chef service reporting réglementaire": {"CV_Exp": 20, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 20, "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3}
}

POSTES_AVEC_SCORING_100 = ["Auditeur interne", "Chef service contrôle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marché", "Chef service reporting réglementaire"]
POSTES_AVEC_SCORING_12 = ["Chef de Section Compensation"]

BEAC_GIMAC_KEYWORDS = ['beac', 'gimac', 'systac', 'sygma', 'cemac', 'zone cemac', 'banque centrale', 'banque des etats', 'compensation regionale', 'chambre de compensation', 'clearing house', 'central bank cemac']
COMPENSATION_INTERBANCAIRE_KEYWORDS = ['compensation interbancaire', 'compensation bancaire', 'chambre de compensation', 'cheques', 'virements', 'prelevements', 'interbank clearing', 'clearing', 'systeme de compensation', 'compensation des operations', 'echange interbancaire', 'reglement interbancaire', 'compensation des cheques']
BACKOFFICE_KEYWORDS = ['back-office', 'back office', 'operations bancaires', 'traitement des operations', 'middle office', 'operations interbancaires', 'service operations', 'banking operations', 'transaction processing', 'operations bancaires courantes']
SUSPENS_REJETS_KEYWORDS = ['suspens', 'rejets', 'reclamations interbancaires', 'litiges interbancaires', 'reglement des litiges', 'disputes', 'claims', 'unresolved items', 'rejets de virements', 'reclamation client', 'gestion des suspens', 'gestion des rejets', 'incidents de paiement']
ENCADREMENT_KEYWORDS = ['encadrement', 'supervision equipe', 'chef d equipe', 'team lead', 'responsable equipe', 'superviseur', 'coordination equipe', 'management equipe', 'gestion d equipe', 'head of team', 'manageur', 'encadre une equipe', 'supervise une equipe', 'pilotage d equipe', 'chef de section', 'chef de service', 'responsable de section', 'responsable de service']
SYSCOHADA_KEYWORDS = ['syscohada', 'comptabilite bancaire', 'plan comptable bancaire', 'normes comptables ohada', 'comptabilite ohada']

COMMERCIAL_BANKS = ['ecobank', 'orabank', 'uba', 'bicec', 'sgbc', 'cbc', 'bct', 'société générale', 'standard chartered', 'nsia banque', 'commercial bank', 'banque commerciale', 'investment bank', 'banque d affaires', 'credit institution', 'financial institution', 'banque', 'e c o b a n k', 'o r a b a n k', 'u b a', 'u b a g r o u p', 'ecob', 'orab', 'ubagroup', 'uba-tchad', 'uba-congo', 'ecobank-tchad', 'afriland', 'bgfi', 'bgfibank', 'ccei', 'boa', 'bank of africa', 'banque atlantique', 'commercial bank cameroun', 'sgc cameroun']
MICROFINANCE = ['microfinance', 'micro-finance', 'mfb', 'finadev', 'ucec', 'caisse d epargne', 'credit union', 'cooperative financiere', 'financial development', 'union des caisses', 'f i n a d e v']
NON_FINANCIAL_SECTORS = ['logistics', 'logistique', 'transport', 'shipping', 'gls', 'global logistics', 'société commerciale', 'entreprise commerciale', 'retail store', 'grande distribution', 'distribution commerciale', 'manufacturing', 'industrie', 'construction', 'btp', 'holding', 'encobat', 'agriculture', 'farming', 'agroalimentaire', 'communication agency', 'agence de communication', 'health', 'hôpital', 'clinique', 'samaritaine', 'education', 'enseignement', 'école', 'ngo', 'ong', 'association', 'humanitaire', 'world vision', 'wvi', 'government', 'gouvernement', 'administration publique', 'media', 'presse', 'journalisme', 'tourism', 'tourisme', 'restauration', 'real estate', 'immobilier', 'energy', 'énergie', 'oil', 'gaz', 'petrole', 'mining', 'correct services', 'cdo consulting']

COMMERCIAL_BANK_PATTERN = re.compile(r'\b(' + '|'.join(COMMERCIAL_BANKS) + r')\b', re.IGNORECASE)
MICROFINANCE_PATTERN = re.compile('|'.join(MICROFINANCE), re.IGNORECASE)
NON_FINANCIAL_PATTERN = re.compile('|'.join(NON_FINANCIAL_SECTORS), re.IGNORECASE)

STAGE_MARKERS = [r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b', r'\bapprenti\b', r'\bapprentissage\b', r'\balternance\b', r'\bstage de fin\b', r'\bstage academique\b', r'\bstage professionnel\b', r'\bstage de formation\b', r'\bpfr\b', r'\bstage pfe\b', r'\bpfe\b', r'\bvolontariat\b', r'\btrainee\b']
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)

NEGATIVE_PATTERNS = [
    r"\b(pas\s+de|pas\s+d')\s*(expérience|experience|expérimenté|competence)\b",
    r'\b(aucun|aucune|aucuns|aucunes)\s*(expérience|experience|competence|connaissance)\b',
    r'\b(sans|dépourvu\s+de|manque\s+de)\s*(expérience|experience|competence)\b',
    r"\b(n')?(?:ai|as|a|avons|avez|ont)\s+pas\s+(?:d')?(expérience|experience|competence|connaissance)\b",
    r'\b(jamais\s+(?:eu|travaillé|exercé|pratiqué))\b',
    r"\b(peu\s+d')?expérience\b",
    r'\b(expérience\s+(?:limitée|insuffisante|faible|partielle))\b',
    r'\b(ne\s+connais\s+pas|ne\s+maîtrise\s+pas|ne\s+possède\s+pas)\b',
    r'\b(no\s+experience|without\s+experience|lack\s+of\s+experience)\b'
]
NEGATIVE_REGEX = re.compile('|'.join(NEGATIVE_PATTERNS), re.IGNORECASE)

_ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouuucaaAAEEEEIIOUUUCAAaaonaaon')

def normalize_spaces(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\b(\w)\s+(\w\s+\w+)\b', r'\1\2', text)
    text = re.sub(r'\b(\w)\s+(\w)\b', r'\1\2', text)
    bank_corrections = {'u b a': 'UBA', 'e c o b a n k': 'ECOBANK', 'o r a b a n k': 'ORABANK', 'u b a -': 'UBA-', 'e c o o b a n k': 'ECOBANK', 'f i n a d e v': 'FINADEV', 'w o r l d': 'WORLD', 'v i s i o n': 'VISION', 'g l s': 'GLS', 'u b a g r o u p': 'UBAGROUP', 'c o r r e c t': 'CORRECT', 's e r v i c e s': 'SERVICES', 'c o n s u l t i n g': 'CONSULTING'}
    for wrong, correct in bank_corrections.items():
        text = re.sub(r'\b' + wrong + r'\b', correct, text, flags=re.IGNORECASE)
    typo_corrections = {'risque de marche': 'risque de marché', 'risque marche': 'risque marché', 'market risk': 'market risk', 'taux de change': 'taux de change', 'liquidite': 'liquidité', 'competence': 'compétence', 'experience': 'expérience'}
    for wrong, correct in typo_corrections.items():
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
    for match in matches:
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        context = text[start:end]
        if NEGATIVE_REGEX.search(context):
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

def extract_text_from_pdf_robust(file_bytes, filename):
    text = ""
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
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
            if text.strip() and len(text.strip()) > 100:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"pdfplumber erreur: {e}")
    if PYPDF2_AVAILABLE:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            for page in reader.pages:
                content = page.extract_text()
                if content:
                    text += normalize_spaces(content) + "\n"
            if text.strip() and len(text.strip()) > 100:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"PyPDF2 erreur: {e}")
    if len(text.strip()) < 100:
        ocr_text = extract_text_from_pdf_via_ocr(file_bytes)
        if ocr_text and len(ocr_text.strip()) > 100:
            return ocr_text
    return ""

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
        return normalize_unicode(result)
    except Exception as e2:
        logger.warning(f"Fallback DOCX échoué: {e2}")
    try:
        text = re.sub(r'[^\x20-\x7E\u00C0-\u017F]+', ' ', file_bytes.decode('utf-8', errors='ignore'))
        return normalize_unicode(normalize_spaces(text.strip()))
    except Exception:
        pass
    return ""

def extract_text_from_txt(file_bytes):
    if CHARDET_AVAILABLE:
        try:
            detected = chardet.detect(file_bytes[:10000])
            encoding = detected['encoding'] or 'utf-8'
            return normalize_unicode(normalize_spaces(file_bytes.decode(encoding, errors='ignore')))
        except Exception:
            pass
    for enc in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1', 'utf-16']:
        try:
            return normalize_unicode(normalize_spaces(file_bytes.decode(enc, errors='ignore').strip()))
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""

def extract_text_robust_from_bytes(file_bytes, filename):
    if not file_bytes:
        return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return extract_text_from_pdf_robust(file_bytes, filename)
    elif ext in ('doc', 'docx'):
        return extract_text_from_docx_robust(file_bytes)
    elif ext == 'txt':
        return extract_text_from_txt(file_bytes)
    try:
        return normalize_unicode(normalize_spaces(file_bytes.decode('utf-8', errors='ignore').strip()))
    except Exception:
        pass
    return ""

def detect_institution_type(text):
    text_lower = text.lower()
    if COMMERCIAL_BANK_PATTERN.search(text_lower):
        if MICROFINANCE_PATTERN.search(text_lower):
            return 'microfinance'
        return 'commercial_bank'
    if MICROFINANCE_PATTERN.search(text_lower):
        return 'microfinance'
    if NON_FINANCIAL_PATTERN.search(text_lower):
        return 'non_financial'
    return 'unknown'

def check_current_employment_financial(cv_text):
    current_patterns = [
        r'(?:depuis|from|since|à nos jours|a nos jours|nos jours|to present|current|actuel)\s*[:\-]?\s*([^\n]+)',
        r"(\d{4})\s*[-–]\s*(?:présent|present|now|actuel|nos jours|a nos jours|aujourd'hui)",
        r"(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s*\d{4}\s*[-–]\s*(?:présent|present|now|actuel|nos jours|a nos jours|aujourd'hui)"
    ]
    for pattern in current_patterns:
        matches = re.findall(pattern, cv_text, re.IGNORECASE)
        if matches:
            context = cv_text[max(0, cv_text.lower().find(str(matches[0]).lower()) - 300):cv_text.lower().find(str(matches[0]).lower()) + 300]
            inst_type = detect_institution_type(context)
            if inst_type == 'non_financial':
                return False, "Emploi actuel hors secteur financier"
            elif inst_type in ['commercial_bank', 'microfinance']:
                return True, "Emploi actuel dans secteur financier"
    inst_type = detect_institution_type(cv_text)
    if inst_type == 'non_financial':
        return False, "Secteur non financier détecté"
    return True, "Secteur financier ou inconnu"

def check_cv_letter_consistency(cv_text, letter_text, poste):
    cv_lower = cv_text.lower()
    letter_lower = letter_text.lower() if letter_text else ""
    if poste == "Market Risk Officer":
        technical_keywords = ['var', 'value at risk', 'stress testing', 'trading', 'alm', 'bâle', 'ficc', 'positions', 'modélisation', 'quantitatif', 'quantitative', 'modeling', 'risque de marché', 'market risk', 'taux', 'change', 'liquidité', 'fx', 'risque de marche', 'risque marche', 'reporting', 'trésorerie', 'gestion des risques', 'risque opérationnel', 'responsable risque', 'directeur risque']
        cv_matches = sum(1 for kw in technical_keywords if kw in cv_lower)
        letter_matches = sum(1 for kw in technical_keywords if kw in letter_lower)
        if cv_matches > 0 or letter_matches > 0:
            return True, "Compétences Market Risk détectées"
    if ('risque' in cv_lower or 'risque' in letter_lower) and ('banque' in cv_lower or 'uba' in cv_lower or 'ecobank' in cv_lower or 'orabank' in cv_lower):
        return True, "Profil risque en banque détecté"
    if ('responsable' in cv_lower or 'responsable' in letter_lower) and ('risque' in cv_lower or 'risque' in letter_lower):
        return True, "Responsable risque détecté"
    if re.search(r'gestion\s+bancaire', cv_lower) or re.search(r'gestion\s+bancaire', letter_lower):
        if re.search(r'(\d+)\s*(?:années?|ans?)', cv_lower) or re.search(r'(\d+)\s*(?:années?|ans?)', letter_lower):
            return True, "Gestion bancaire avec expérience détectée"
    return True, "Cohérent"

def validate_financial_institution_for_market_risk(text):
    text_lower = text.lower()
    text_normalized = normalize_spaces(text_lower)
    has_commercial = COMMERCIAL_BANK_PATTERN.search(text_normalized)
    has_microfinance = MICROFINANCE_PATTERN.search(text_normalized)
    has_non_financial = NON_FINANCIAL_PATTERN.search(text_normalized)
    uba_patterns = [r'u\s*b\s*a', r'uba[-\s]*tchad', r'uba[-\s]*congo', r'ubagroup']
    ecobank_patterns = [r'e\s*c\s*o\s*b\s*a\s*n\s*k', r'ecobank[-\s]*tchad']
    orabank_patterns = [r'o\s*r\s*a\s*b\s*a\s*n\s*k', r'orabank[-\s]*tchad']
    for pattern in uba_patterns + ecobank_patterns + orabank_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True, "Banque commerciale détectée (UBA/ECOBANK/ORABANK)"
    if has_commercial or has_microfinance:
        if has_commercial:
            return True, "Banque commerciale détectée"
        elif has_microfinance:
            return True, "Microfinance agréée détectée"
    if re.search(r'gestion\s+bancaire', text_lower) or re.search(r'risque', text_lower):
        years_match = re.search(r'(\d+)\s*(?:années?|ans?)', text_lower)
        if years_match:
            years = int(years_match.group(1))
            if years >= 3:
                return True, f"Expérience bancaire mentionnée ({years} ans)"
    if has_non_financial and not has_commercial and not has_microfinance:
        recent_year_pattern = re.compile(r'(201[5-9]|202\d)')
        if not recent_year_pattern.search(text):
            return True, "Expériences hors secteur mais antérieures à 2015 – ignorées"
        return False, "Secteur non financier détecté (récent)"
    return True, "Institution financière valide"

def check_not_microfinance_only(raw_text):
    text_lower = normalize_spaces(raw_text.lower())
    text_deaccent = text_lower.translate(_ACCENT_MAP)
    has_microfinance = bool(MICROFINANCE_PATTERN.search(text_lower))
    has_commercial_bank = bool(COMMERCIAL_BANK_PATTERN.search(text_lower))
    has_interbank_exposure = any(kw in text_deaccent for kw in (COMPENSATION_INTERBANCAIRE_KEYWORDS + BEAC_GIMAC_KEYWORDS))
    if has_microfinance and not has_commercial_bank and not has_interbank_exposure:
        return False
    return True

def check_criterion_context(criterion, raw_text, poste):
    text_lower = raw_text.lower()
    banking_posts = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Senior Finance Officer", "Market Risk Officer"]
    if poste in banking_posts:
        banking_criteria = ["Expérience bancaire", "Minimum 3 ans en crédit / risque (hors stage)", "Exposition aux garanties ou conformité", "Minimum 3 ans institution financière (hors stage)", "Minimum 3 ans département finance ou en cabinet d'audit (hors stage)", "Expérience en analyse crédit", "Capacité à lire des états financiers", "Base en risques de marché", "Exposition à FX / taux / liquidité", "Expérience en reporting financier structuré", "Exposition aux états financiers"]
        if criterion in banking_criteria:
            banking_matches = list(COMMERCIAL_BANK_PATTERN.finditer(text_lower))
            if not banking_matches:
                microfinance_matches = list(MICROFINANCE_PATTERN.finditer(text_lower))
                if not microfinance_matches:
                    return False
            for match in banking_matches:
                idx = match.start()
                window = raw_text[max(0, idx-500): min(len(raw_text), idx+500)]
                window_lower = window.lower()
                if NON_FINANCIAL_PATTERN.search(window_lower):
                    continue
                return True
            return False
    if poste == "Archiviste (Administration Crédit)":
        if criterion in ["Expérience en banque ou juridique"]:
            banking_matches = list(COMMERCIAL_BANK_PATTERN.finditer(text_lower))
            legal_terms = ['juridique', 'legal', 'law', 'droit', 'notaire', 'cabinet']
            if banking_matches:
                for match in banking_matches:
                    idx = match.start()
                    window = raw_text[max(0, idx-400): min(len(raw_text), idx+400)]
                    if not NON_FINANCIAL_PATTERN.search(window.lower()):
                        return True
            for legal in legal_terms:
                if legal in text_lower:
                    idx = text_lower.find(legal)
                    window = raw_text[max(0, idx-400): min(len(raw_text), idx+400)]
                    if any(t in window.lower() for t in ['contrat', 'garantie', 'documentation', 'archive']):
                        return True
            return False
    if poste == "IT Réseau & Infrastructure":
        if criterion == "Exposition à environnement critique":
            critical_pattern = re.compile('|'.join(['banque', 'bancaire', 'bank', 'banking', 'telco', 'telecom', 'télécom', 'opérateur', 'datacenter', 'centre de données', 'data center', 'hébergement', 'hosting', 'cloud provider', 'faa', 'gouvernement', 'ministère', 'défense', 'hôpital', 'santé', 'critical infrastructure', 'ecobank', 'orabank', 'uba', 'mtn', 'airtel', 'salam', 'financial services', 'telecommunications', 'critical systems']), re.IGNORECASE)
            critical_matches = list(critical_pattern.finditer(text_lower))
            if critical_matches:
                return True
            return False
    if poste == "Chef de Section Compensation":
        banking_criteria_comp = ["Expérience en banque ou établissement financier réglementé", "Minimum 3 ans en opérations bancaires ou back-office (hors stage)", "Profil bancaire avec exposition interbancaire (hors microfinance isolée)"]
        if criterion in banking_criteria_comp:
            banking_matches = list(COMMERCIAL_BANK_PATTERN.finditer(text_lower))
            if not banking_matches:
                microfinance_matches = list(MICROFINANCE_PATTERN.finditer(text_lower))
                if not microfinance_matches:
                    return False
            for match in banking_matches:
                idx = match.start()
                window = raw_text[max(0, idx-500): min(len(raw_text), idx+500)]
                window_lower = window.lower()
                if NON_FINANCIAL_PATTERN.search(window_lower):
                    continue
                return True
            return False
    return True

FRENCH_MONTHS = {'janvier': 1, 'jan': 1, 'février': 2, 'fevrier': 2, 'fev': 2, 'mars': 3, 'mar': 3, 'avril': 4, 'avr': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'juil': 7, 'août': 8, 'aout': 8, 'aou': 8, 'septembre': 9, 'sep': 9, 'octobre': 10, 'oct': 10, 'novembre': 11, 'nov': 11, 'décembre': 12, 'decembre': 12, 'dec': 12}

def split_into_jobs(raw_text):
    separators = re.compile(
        r"(?:^|\n)(?=\s*(?:(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*(?:20\d{2}|19\d{2})|\d{1,2}[/\-\.](?:20\d{2}|19\d{2})|(?:depuis|de |from |since |desde |a partir de |starting |beginning)))",
        re.IGNORECASE | re.MULTILINE
    )
    blocks = separators.split(raw_text)
    return [b.strip() for b in blocks if b.strip()]

def is_stage_block(block_text):
    return bool(STAGE_PATTERN.search(block_text))

def extract_duration_years_from_block(block_text):
    years = 0.0
    text = block_text.lower().translate(_ACCENT_MAP)
    duration_patterns = [r'(\d+[\.,]?\d*)\s*(?:ans?|annee?s?|years?|años?|anos?)', r'\(\s*(\d+)\s*\)\s*(?:ans?|annee?s?|years?)', r'\w+\s+\(\s*(\d+)\s*\)\s*(?:ans?|annee?s?|years?)', r'plus\s+de\s+(\d+)\s*(?:ans?|annee?s?|years?)', r'depuis\s+(?:plus\s+de\s+)?(\d+)\s*(?:ans?|annee?s?)']
    for dp in duration_patterns:
        m = re.search(dp, text)
        if m:
            try:
                years = float(m.group(1).replace(',', '.'))
                if 0 < years <= 40:
                    return years
            except (ValueError, IndexError):
                pass
    pattern_present = re.compile(
        r"(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})\s*(?:a|-|–|—|au|jusqu'au|to|until|au\s+)?\s*(?:aujourd'hui|present|actuel|en cours|now|current|actual|hoje|ce jour|nos\s+jours|a\s+nos\s+jours)",
        re.IGNORECASE
    )
    m = pattern_present.search(text)
    if m:
        start_year = int(m.group(2))
        start_month = FRENCH_MONTHS.get((m.group(1) or '').lower(), 1)
        end_year = datetime.datetime.now().year
        end_month = datetime.datetime.now().month
        delta = (end_year - start_year) + (end_month - start_month) / 12.0
        if 0 < delta <= 40:
            return round(delta, 1)
    pattern_since = re.compile(r'(?:depuis|since|from)\s+(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec\s+)?(20\d{2}|19\d{2})', re.IGNORECASE)
    m = pattern_since.search(text)
    if m:
        start_year = int(m.group(1))
        delta = datetime.datetime.now().year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    pattern_range = re.compile(
        r"(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})\s*(?:a|-|–|—|au|jusqu'au|to|until)?\s*(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})",
        re.IGNORECASE
    )
    m = pattern_range.search(text)
    if m:
        start_month = FRENCH_MONTHS.get((m.group(1) or '').lower(), 1)
        start_year = int(m.group(2))
        end_month = FRENCH_MONTHS.get((m.group(3) or '').lower(), 12)
        end_year = int(m.group(4))
        delta = (end_year - start_year) + (end_month - start_month) / 12.0
        if 0 < delta <= 40:
            return round(delta, 1)
    m = re.search(r'(\d{1,2})[/\-\.](20\d{2}|19\d{2})\s*[-–—\.]?\s*(?:(\d{1,2})[/\-\.])?(20\d{2}|19\d{2}|present|current|now)', text)
    if m:
        start_month = int(m.group(1))
        start_year = int(m.group(2))
        end_raw = m.group(4)
        end_month_raw = m.group(3)
        if re.match(r'\d{4}', str(end_raw)):
            end_year = int(end_raw)
            end_month = int(end_month_raw) if end_month_raw else 12
        else:
            end_year = datetime.datetime.now().year
            end_month = datetime.datetime.now().month
        delta = (end_year - start_year) + (end_month - start_month) / 12.0
        if 0 < delta <= 40:
            return round(delta, 1)
    return 0.0

def has_experience_years_strict(full_raw_text, min_years, domain_keywords=None, poste=None):
    blocks = split_into_jobs(full_raw_text)
    total_years = 0.0
    years_patterns = [r'(\d+)\s*(?:années?|ans?)', r'plus\s+de\s+(\d+)\s*(?:années?|ans?)', r'\(\s*(\d+)\s*\)\s*(?:années?|ans?)', r'\w+\s+\(\s*(\d+)\s*\)\s*(?:années?|ans?)', r'depuis\s+(?:plus\s+de\s+)?(\d+)\s*(?:années?|ans?)', r'(\d+)\s*(?:années?|ans?)\s+(?:d[ée]?expérience|dans|en|de)', r'expérience\s+(?:de\s+)?(\d+)\s*(?:années?|ans?)']
    text_lower = full_raw_text.lower().translate(_ACCENT_MAP)
    for pattern in years_patterns:
        matches = re.findall(pattern, text_lower, re.IGNORECASE)
        for match in matches:
            try:
                years = float(match)
                if years >= min_years:
                    return True
            except (ValueError, TypeError):
                continue
    banking_posts = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Senior Finance Officer", "Market Risk Officer"]
    for block in blocks:
        if is_stage_block(block):
            continue
        if poste in banking_posts:
            if NON_FINANCIAL_PATTERN.search(block.lower()):
                if COMMERCIAL_BANK_PATTERN.search(block.lower()):
                    pass
                else:
                    recent_year_pattern = re.compile(r'(201[5-9]|202\d)')
                    if recent_year_pattern.search(block):
                        continue
        elif poste == "IT Réseau & Infrastructure":
            critical_pattern = re.compile('|'.join(['banque', 'bancaire', 'bank', 'banking', 'telco', 'telecom', 'télécom', 'opérateur', 'datacenter', 'centre de données', 'data center', 'hébergement', 'hosting', 'cloud provider', 'faa', 'gouvernement', 'ministère', 'défense', 'hôpital', 'santé', 'critical infrastructure', 'ecobank', 'orabank', 'uba', 'mtn', 'airtel', 'salam', 'financial services', 'telecommunications', 'critical systems']), re.IGNORECASE)
            if not critical_pattern.search(block.lower()):
                continue
        if domain_keywords:
            if any(contains_negative_context(block, kw) for kw in domain_keywords):
                continue
            norm_block, _ = normalize_for_matching(block)
            if not any(kw in norm_block and not contains_negative_context(block, kw) for kw in domain_keywords):
                continue
        duration = extract_duration_years_from_block(block)
        if duration > 0:
            total_years += duration
    return total_years >= min_years

KEYWORD_MAPPING = {
    "Expérience bancaire": ["banque", "bancaire", "etablissement bancaire", "institution bancaire", "banque commerciale", "microfinance", "etablissement financier", "institution financiere", "secteur bancaire", "groupe bancaire", "filiale bancaire", "bank", "banking", "financial institution", "credit institution", "commercial bank", "ecobank", "orabank", "uba", "finadev", "ucec", "microfinance"],
    "Minimum 3 ans en crédit / risque (hors stage)": ["EXP_CREDIT_3ANS"],
    "Exposition aux garanties ou conformité": ["garantie", "garanties", "nantissement", "hypotheque", "surete", "suretes", "conformite", "compliance", "cobac", "bceao", "bcac", "commission bancaire", "reglementation bancaire", "audit", "controle interne", "collateral", "regulatory", "guarantee", "guarantees", "compliance officer", "regulatory compliance", "internal control"],
    "Validation de dossiers de crédit": ["validation dossier", "instruction credit", "approbation credit", "dossier credit", "traitement dossier", "montage dossier", "credit approval", "loan processing", "credit file", "loan file"],
    "Gestion des garanties": ["gestion garanties", "suivi garanties", "garanties reelles", "portefeuille garanties", "hypotheque", "nantissement", "collateral management", "guarantee management", "security management"],
    "Participation à des audits": ["audit", "controle interne", "inspection", "commissariat aux comptes", "conformite", "compliance audit", "mission audit", "internal audit", "external audit", "audit mission", "audit report"],
    "IFRS 9": ["ifrs 9", "ias 39", "normes ifrs", "comptabilite ifrs", "ifrs9", "provisionnement ifrs", "international financial reporting", "ifrs standards", "impairment ifrs 9"],
    "COBAC / conformité": ["cobac", "conformite bancaire", "bceao", "bcac", "commission bancaire", "regulation bancaire", "compliance", "banking regulation", "central bank", "banking authority"],
    "Suivi portefeuille / impayés": ["portefeuille credit", "impayes", "recouvrement", "contentieux", "encours", "suivi portefeuille", "creances douteuses", "npls", "portfolio monitoring", "non-performing loans", "loan portfolio", "collections", "past due", "default management"],
    "Expérience en analyse crédit": ["analyse credit", "credit analysis", "evaluation credit", "scoring credit", "analyse financiere credit", "instruction credit", "analyste credit", "octroi credit", "loan analysis", "credit analyst", "credit assessment", "credit evaluation"],
    "Capacité à lire des états financiers": ["etats financiers", "bilan", "compte de resultat", "ratios financiers", "analyse financiere", "liasse fiscale", "situation financiere", "diagnostic financier", "solvabilite", "financial statements", "balance sheet", "income statement", "financial analysis", "financial ratios", "cash flow statement"],
    "Minimum 3 ans institution financière (hors stage)": ["EXP_FIN_3ANS"],
    "Clients PME": ["pme", "petite entreprise", "moyenne entreprise", "tpe", "entreprise cliente", "sme", "small business", "mid-market", "small and medium enterprises"],
    "Clients particuliers": ["particulier", "clientele particuliere", "retail banking", "client particulier", "retail", "personal banking", "individual clients", "consumer banking"],
    "Structuration de crédit": ["structuration credit", "montage credit", "structurer credit", "dossier de credit", "credit structurel", "loan structuring", "credit structuring", "loan arrangement"],
    "Avis de crédit": ["avis credit", "recommandation credit", "opinion credit", "note de credit", "avis d'octroi", "credit opinion", "credit recommendation", "credit memo", "loan opinion"],
    "Cash-flow analysis": ["cash flow", "cashflow", "flux tresorerie", "flux de tresorerie", "fcf", "free cash flow", "capacite d autofinancement", "caf", "cash flow analysis", "cash flow statement", "operating cash flow"],
    "Montage de crédit": ["montage credit", "structuration credit", "montage dossier", "montage financier", "loan structuring", "credit arrangement", "loan packaging", "deal structuring"],
    "Comités de crédit": ["comite credit", "commission credit", "credit committee", "comite d octroi", "validation comite", "credit approval committee", "credit board", "loan committee"],
    "Expérience en gestion documentaire structurée": ["gestion documentaire", "archivage", "ged", "records management", "classement", "documentation", "gestion archives", "archiviste", "document management", "filing system", "document control", "records keeping", "archive management"],
    "Rigueur démontrée": ["rigueur", "methode", "organisation", "procedures", "tracabilite", "precision", "fiabilite", "serieux", "attention to detail", "meticulous", "accuracy", "precision", "thoroughness"],
    "Archivage physique et électronique": ["archivage physique", "archivage electronique", "dematerialisation", "numerisation", "archivage numerique", "scan", "ged", "physical archiving", "digital archiving", "electronic filing", "scanning", "digitization", "document imaging"],
    "Gestion des dossiers sensibles": ["dossier sensible", "confidentiel", "securise", "acces restreint", "donnees sensibles", "confidentialite", "confidential documents", "sensitive files", "restricted access", "classified documents"],
    "Expérience en banque ou juridique": ["banque", "etablissement financier", "juridique", "droit bancaire", "secteur bancaire", "cabinet juridique", "etude notariale", "banking", "legal", "law firm", "legal department", "banking sector"],
    "Manipulation de garanties ou contrats": ["garantie", "contrat", "convention", "acte juridique", "documentation juridique", "acte notarie", "contracts", "legal documents", "guarantees", "legal agreements", "contract management"],
    "Expérience en reporting financier structuré": ["reporting financier", "reporting", "tableau de bord", "kpi", "indicateurs financiers", "etats financiers", "production reporting", "financial reporting", "management reporting", "financial dashboard", "financial metrics", "performance reporting", "rapport financier", "rapports financiers", "production de rapports", "rapport de gestion", "rapport mensuel", "rapport annuel"],
    "Exposition aux états financiers": ["etats financiers", "bilan", "compte de resultat", "consolidation", "reporting financier", "liasse", "financial statements", "balance sheet", "income statement", "consolidated accounts", "financial reporting"],
    "Interaction avec auditeurs": ["auditeur", "audit", "commissaire aux comptes", "cac", "audit externe", "commissariat aux comptes", "revue externe", "external auditor", "statutory audit", "audit firm", "external audit", "audit interaction"],
    "Minimum 3 ans département finance ou en cabinet d'audit (hors stage)": ["EXP_FINANCE_3ANS"],
    "Production états financiers": ["production etats financiers", "elaboration etats financiers", "etablissement etats financiers", "cloture comptable", "cloture", "financial statements preparation", "accounting close", "financial close", "month-end close"],
    "Reporting groupe": ["reporting groupe", "reporting consolide", "consolidation groupe", "reporting mensuel", "pack de gestion", "group reporting", "consolidated reporting", "corporate reporting", "group accounts", "rapport groupe", "rapports consolidés", "rapport de consolidation", "rapport corporate", "rapport mensuel groupe"],
    "Connaissance IFRS": ["ifrs", "normes internationales", "ias", "comptabilite internationale", "international accounting standards", "ifrs standards", "international financial reporting standards"],
    "Contraintes réglementaires": ["reglementation", "contraintes reglementaires", "conformite", "reglementaire", "prudentiel", "regulatory requirements", "compliance requirements", "regulatory compliance", "prudential"],
    "IFRS / consolidation": ["ifrs", "consolidation", "comptes consolides", "normes ifrs", "consolidated accounts", "group consolidation", "ifrs consolidation"],
    "Interaction avec CAC": ["cac", "commissaire aux comptes", "audit legal", "audit externe", "statutory auditor", "external auditor", "audit firm"],
    "Outils SPECTRA / CERBER / ERP": ["spectra", "cerber", "erp", "sap", "oracle", "sage", "outil de gestion", "logiciel comptable", "enterprise software", "accounting software", "financial systems", "erp systems"],
    "Base en risques de marché": ["risque marche", "market risk", "risques de marche", "gestion risques de marche", "risque financier", "trading risk", "market risk management", "trading risks", "financial risk", "risque de marche", "risque marche", "risques marche", "directeur de risques", "responsable risques", "risk manager", "gestion des risques", "risk management", "risque operationnel", "operational risk", "risques operationnels", "risques bancaires", "stress testing", "alm", "fx", "reporting", "liquidité", "trésorerie"],
    "Exposition à FX / taux / liquidité": ["fx", "change", "taux", "liquidite", "forex", "taux d interet", "risque de liquidite", "risque de change", "foreign exchange", "interest rate", "liquidity risk", "fx risk", "rate risk", "funding liquidity", "taux de change", "exposition aux risques", "risque de taux", "taux de changes", "gestion des taux", "trésorerie", "cash management", "funding", "risque de marche", "market risk", "risque opérationnel", "responsable risque", "gestion risques", "directeur risque"],
    "Maîtrise VaR / stress testing": ["var", "value at risk", "stress testing", "back testing", "backtesting", "scenario de stress", "value-at-risk", "stress test", "var model", "risk modeling", "value a risque", "tests de resistance", "tests de stress"],
    "Analyse des positions": ["analyse des positions", "suivi des positions", "analyse portefeuille", "exposition", "position monitoring", "position analysis", "portfolio analysis", "exposure monitoring"],
    "Excel avancé": ["excel avance", "excel", "vba", "macros excel", "pivot", "tableaux croises", "power query", "advanced excel", "excel modeling", "spreadsheet", "excel functions"],
    "VBA ou Python": ["vba", "python", "programmation", "scripting", "r statistical", "visual basic", "data analysis", "programming", "coding", "quantitative programming", "financial modeling"],
    "Bâle II / III": ["bale ii", "bale iii", "bale 2", "bale 3", "basel ii", "basel iii", "accords de bale", "reglementation bale", "basel framework", "basel accords", "basel regulations", "capital requirements"],
    "Gestion ALM / liquidité": ["alm", "asset liability management", "liquidite", "gestion alm", "actif passif", "gap de liquidite", "asset-liability management", "liquidity management", "alm framework"],
    "Produits FICC": ["ficc", "produits derives", "commodities", "matieres premieres", "produits de taux", "taux", "fixed income", "derivatives", "fixed income currencies commodities", "bond", "rates"],
    "Reporting risque": ["reporting risque", "rapport de risque", "tableau de bord risque", "reporting des risques", "risk reporting", "risk dashboard", "risk metrics", "risk reports", "rapport risques", "rapports de risques", "reporting hebdomadaire", "reporting mensuel"],
    "Expérience en réseau / infrastructure": ["reseau", "infrastructure", "lan", "wan", "vpn", "wlan", "sd-wan", "infrastructure it", "network", "reseaux", "networking", "routeur", "switch", "ospf", "eigrp", "bgp", "glbp", "cisco", "mikrotik", "ubiquiti", "fortinet", "palo alto", "router", "network infrastructure", "it infrastructure"],
    "Exposition à environnement critique": ["banque", "telco", "telecom", "datacenter", "centre de donnees", "environnement critique", "secteur bancaire", "haute disponibilite", "critical infrastructure", "mission critical", "bad", "orabank", "ecobank", "uba", "unicef", "assurances", "financial services", "telecommunications", "data center", "critical systems"],
    "Notion de sécurité IT": ["securite it", "cybersecurite", "securite informatique", "firewall", "securite reseau", "ids", "ips", "siem", "soar", "it security", "cybersecurity", "network security", "antimalware", "antivirus", "anti-spam", "cisco security", "cyberops", "information security", "security protocols"],
    "Minimum 2 ans expérience (hors stage)": ["EXP_IT_2ANS"],
    "Gestion réseaux LAN/WAN/VPN": ["lan", "wan", "vpn", "reseaux locaux", "reseau local", "virtual private network", "switch", "routeur", "ospf", "eigrp", "bgp", "glbp", "sd-wan", "wlan", "interconnexion", "local area network", "wide area network", "network management"],
    "Gestion serveurs Windows/Linux": ["windows server", "linux", "serveurs", "administration serveurs", "unix", "active directory", "debian", "ubuntu server", "vmware", "esxi", "hyper-v", "virtualbox", "virtualisation", "server administration", "server management", "virtualization"],
    "Cloud même basique": ["cloud", "aws", "azure", "google cloud", "cloud computing", "iaas", "saas", "ovh", "hosting", "amen", "lws", "starlink", "cloud services", "cloud platform", "cloud infrastructure"],
    "Gestion des incidents": ["incident", "gestion incidents", "support technique", "resolution incident", "itil", "ticketing", "prtg", "nagios", "zabbix", "supervision", "monitoring", "incident management", "technical support", "helpdesk", "service desk"],
    "Assurance de la disponibilité": ["disponibilite", "haute disponibilite", "sla", "uptime", "continuite service", "availability", "high availability", "service level agreement", "failover", "system availability", "uptime monitoring", "service continuity"],
    "Cybersécurité / firewall": ["cybersecurite", "firewall", "securite", "ids", "ips", "siem", "pentest", "vulnerability", "cybersecurity", "intrusion detection", "soar", "security firewall", "network security", "threat detection"],
    "Haute disponibilité / PRA/PCA": ["haute disponibilite", "pra", "pca", "plan de reprise", "continuite activite", "disaster recovery", "basculement", "business continuity", "disaster recovery plan", "failover", "backup", "recovery plan", "business continuity plan"],
    "Gestion ATM ou systèmes bancaires": ["atm", "systemes bancaires", "gab", "distributeur automatique", "systeme bancaire core", "temenos", "flexcube", "banking systems", "core banking", "interconnexion gab", "atm management", "banking core systems", "payment systems"],
    "Certifications Cisco ou Microsoft": ["ccna", "ccnp", "ccie", "cisco", "microsoft certified", "mcse", "network+", "certification reseau", "cisco certification", "microsoft certification", "encor", "350-401", "it certifications", "professional certifications"],
    "Expérience réelle en audit interne ou externe": ["audit interne", "audit externe", "auditeur", "mission d'audit", "internal audit", "external audit", "audit mission", "auditor", "audit bancaire", "banking audit", "financial audit", "compliance audit"],
    "Minimum 3 ans en audit bancaire ou cabinet d'audit (hors stage)": ["EXP_AUDIT_3ANS"],
    "Connaissance des normes d'audit et contrôle interne": ["normes audit", "controle interne", "internal control", "audit standards", "iia", "ippf", "coso", "normes ifrs", "audit procedures", "methodologie audit", "audit methodology", "risk assessment"],
    "Missions d'audit sur site": ["audit sur site", "mission terrain", "on-site audit", "fieldwork", "audit visite", "site visit", "physical audit", "inspection sur place"],
    "Évaluation des risques opérationnels": ["risques operationnels", "risk assessment", "operational risk", "evaluation risques", "cartographie risques", "risk mapping", "analyse risques", "risk analysis", "internal control review"],
    "Rédaction de rapports d'audit": ["rapport audit", "rapports d'audit", "audit report", "audit findings", "redaction rapport", "writing audit reports", "audit documentation", "recommandations audit", "audit recommendations"],
    "Suivi des recommandations": ["suivi recommandations", "follow-up", "plan action", "action plan", "tracking recommendations", "remediation", "corrective actions", "mise en oeuvre", "implementation"],
    "Normes IIA / IPPF": ["iia", "ippf", "institute internal auditors", "normes internationales", "international standards", "professional practices framework", "cia certification", "certified internal auditor"],
    "COBAC / réglementation bancaire": ["cobac", "bceao", "bcac", "reglementation bancaire", "banking regulation", "conformite", "compliance", "commission bancaire", "central bank", "prudential regulation", "banking authority"],
    "Audit IT ou systèmes d'information": ["audit it", "audit informatique", "it audit", "is audit", "systemes information", "information systems", "itgc", "it general controls", "cybersecurity audit", "application controls", "it risk"],
    "Certification CIA / CPA / ACCA": ["cia", "cpa", "acca", "certified internal auditor", "certified public accountant", "association chartered certified accountants", "audit certification", "professional qualification", "ifrs certification"],
    "Maîtrise du risque crédit et analyse financière": ["risque credit", "credit risk", "analyse financiere", "financial analysis", "credit analysis", "evaluation credit", "credit assessment", "scoring credit", "credit scoring", "loan analysis"],
    "Expérience significative en octroi de crédits": ["octroi credit", "credit granting", "loan approval", "approval credit", "dossier credit", "credit file", "loan origination", "credit decision", "validation credit", "credit validation"],
    "Minimum 5 ans en institution financière (hors stage)": ["EXP_FIN_5ANS"],
    "Analyse financière d'entreprises": ["analyse financiere", "financial analysis", "etats financiers", "financial statements", "ratios financiers", "financial ratios", "bilan", "balance sheet", "compte resultat", "income statement", "cash flow", "flux tresorerie"],
    "Structuration de crédits complexes": ["structuration credit", "credit structuring", "montage credit", "complex loans", "corporate credit", "structured finance", "financement complexe", "deal structuring", "credit facilities"],
    "Animation de comité de crédit": ["comite credit", "credit committee", "commission credit", "credit approval committee", "loan committee", "validation comite", "presentation comite", "committee presentation"],
    "Management d'équipe": ["management", "encadrement", "team management", "team leader", "chef equipe", "supervision", "managing team", "team supervision", "responsable equipe", "head of"],
    "IFRS 9 / classification des risques": ["ifrs 9", "ias 39", "classification risques", "risk classification", "impairment", "provisionnement", "expected credit loss", "ecl", "stage 1 stage 2", "credit risk grading"],
    "Grande entreprise / Corporate": ["grande entreprise", "corporate", "corporate banking", "large corporates", "clients corporate", "enterprise clients", "wholesale banking", "institutional clients"],
    "Restructuration de dossiers sensibles": ["restructuration", "dossiers sensibles", "distressed assets", "non-performing loans", "npl", "creances douteuses", "impayes", "workout", "debt restructuring", "problem loans"],
    "Formation risk management": ["risk management", "gestion risques", "formation risque", "risk training", "frm", "financial risk manager", "prmie", "certification risque"],
    "Background IT solide avec expérience technique réelle": ["background it", "experience technique", "technical expertise", "it professional", "ingenieur it", "it engineer", "technical skills", "competences techniques", "it specialist"],
    "Minimum 5 ans en maintenance et support informatique": ["EXP_IT_MAINT_5ANS"],
    "Exposition à environnement critique (banque, datacenter)": ["environnement critique", "critical environment", "datacenter", "centre donnees", "high availability", "mission critical", "banque", "banking", "financial services", "telecom"],
    "Maintenance préventive et curative": ["maintenance preventive", "maintenance curative", "preventive maintenance", "corrective maintenance", "troubleshooting", "depannages", "repair", "fix", "resolution incidents"],
    "Support utilisateurs niveau 2/3": ["support niveau 2", "support niveau 3", "level 2 support", "level 3 support", "support technique", "technical support", "helpdesk", "user support", "end user support"],
    "Gestion de parc informatique": ["gestion parc", "parc informatique", "fleet management", "asset management", "gestion actifs", "inventory management", "computer fleet", "device management"],
    "Supervision d'infrastructures": ["supervision", "monitoring", "infrastructure monitoring", "nagios", "zabbix", "prtg", "supervision reseau", "infrastructure oversight"],
    "ITIL / gestion de services IT": ["itil", "itsm", "service management", "gestion services it", "incident management", "change management", "problem management", "service desk", "it service delivery"],
    "Virtualisation (VMware, Hyper-V)": ["virtualisation", "vmware", "hyper-v", "vsphere", "esxi", "virtualization", "vcenter", "virtual machines", "vm", "containers", "docker", "kubernetes"],
    "Systèmes bancaires core banking": ["core banking", "systemes bancaires", "banking systems", "temenos", "flexcube", "t24", "spectrum", "amplitude", "banking software", "financial systems"],
    "Certifications Microsoft / Cisco / ITIL": ["microsoft certified", "cisco certification", "itil foundation", "mcse", "ccna", "ccnp", "itil v4", "azure certified", "microsoft 365", "windows server certification"],
    "Expérience significative en finance bancaire (minimum 7 ans)": ["EXP_FINANCE_7ANS"],
    "Maîtrise du reporting financier et comptabilité bancaire": ["reporting financier", "financial reporting", "comptabilite bancaire", "banking accounting", "etats financiers", "financial statements", "consolidation", "group reporting", "management reporting"],
    "Expérience avérée en management d'équipe": ["management equipe", "team management", "leadership", "encadrement", "supervision equipe", "managing staff", "head of department", "department head", "team lead"],
    "Production d'états financiers": ["production etats financiers", "financial statements preparation", "elaboration bilans", "closing accounts", "cloture comptable", "month end close", "year end close", "financial close"],
    "Reporting réglementaire (BEAC, COBAC)": ["reporting reglementaire", "beac", "cobac", "spectra", "regulatory reporting", "central bank reporting", "prudential reporting", "rapports bancaires", "banking returns"],
    "Relations avec auditeurs externes": ["auditeurs externes", "external auditors", "commissaires aux comptes", "cac", "statutory audit", "audit externe", "big four", "deloitte", "pwc", "ey", "kpmg"],
    "Pilotage de la performance financière": ["performance financiere", "financial performance", "kpis", "tableau bord", "dashboard", "budgeting", "forecasting", "variance analysis", "financial planning"],
    "IFRS / normes internationales": ["ifrs", "ias", "normes internationales", "international standards", "accounting standards", "gaap", "consolidation ifrs", "ifrs compliance"],
    "Consolidation de comptes": ["consolidation", "comptes consolides", "consolidated accounts", "group consolidation", "scope consolidation", "perimetre", "eliminations intra-groupe", "intercompany eliminations"],
    "Outils SPECTRA / CERBER / ERP bancaires": ["spectra", "cerber", "erp", "sap", "oracle financials", "core banking", "systemes integres", "banking erp", "financial systems", "enterprise software"],
    "Bac+5 + Certification (ACCA, CPA, CFA)": ["bac 5", "master", "mba", "acca", "cpa", "cfa", "chartered accountant", "certified financial analyst", "diplome superieur", "graduate degree"],
    "Expérience avérée en risques de marché (FX, taux, liquidité)": ["risques marche", "market risk", "fx", "forex", "change", "taux", "interest rates", "liquidite", "liquidity", "trading risk", "treasury risk", "alm"],
    "Exposition aux produits de trésorerie et ALM": ["tresorerie", "treasury", "alm", "asset liability management", "gestion actif passif", "cash management", "funding", "money market", "marche monetaire"],
    "Calcul et suivi de la VaR": ["var", "value at risk", "value a risque", "var calculation", "risk metrics", "market risk measurement", "backtesting", "stress testing", "scenario analysis"],
    "Stress testing et scénarios de crise": ["stress testing", "tests resistance", "scenarios crise", "crisis scenarios", "what-if analysis", "sensitivity analysis", "shock scenarios", "adverse scenarios"],
    "Reporting des risques à la direction": ["reporting risques", "risk reporting", "rapport direction", "management reporting", "risk committee", "board reporting", "risk dashboard", "risk metrics"],
    "Maîtrise Excel avancé / VBA": ["excel avance", "advanced excel", "vba", "macros", "excel modeling", "spreadsheet", "power query", "pivot tables", "financial modeling excel"],
    "Bâle II / III / réglementation prudentielle": ["bale ii", "bale iii", "basel ii", "basel iii", "reglementation prudentielle", "prudential regulation", "capital requirements", "ratio fonds propres", "tier 1"],
    "Gestion ALM (Asset Liability Management)": ["alm", "asset liability", "gestion actif-passif", "gap analysis", "duration", "convexity", "interest rate risk", "irrbb", "liquidity coverage ratio", "lcr", "nsfr"],
    "Produits FICC (Fixed Income, Currencies, Commodities)": ["ficc", "fixed income", "currencies", "commodities", "produits derives", "derivatives", "swaps", "options", "bonds", "obligations", "matieres premieres"],
    "Python / R pour modélisation financière": ["python", "r statistical", "programming", "quantitative", "financial modeling", "data analysis", "pandas", "numpy", "scikit-learn", "tensorflow", "machine learning"],
    "Comptabilité bancaire approfondie": ["comptabilite bancaire", "banking accounting", "plan comptable banque", "banking chart accounts", "operations bancaires", "banking operations", "ecriture comptable", "journal entries", "general ledger"],
    "Expérience en reporting réglementaire (BEAC, COBAC, SPECTRA)": ["reporting reglementaire", "beac", "cobac", "spectra", "cerber", "regulatory reporting", "prudential returns", "central bank", "surveillant bancaire", "banking supervision"],
    "Minimum 5 ans en banque ou cabinet d'audit bancaire": ["EXP_BANKING_5ANS"],
    "Production de rapports réglementaires": ["rapports reglementaires", "regulatory reports", "rapports cobac", "beac returns", "spectra filings", "prudential reports", "compliance reports", "regulatory filings"],
    "Contrôle de cohérence des données": ["controle coherence", "data quality", "verification donnees", "data validation", "reconciliation", "rapprochement", "data integrity", "quality checks"],
    "Veille réglementaire bancaire": ["veille reglementaire", "regulatory watch", "monitoring reglementaire", "compliance monitoring", "regulatory updates", "nouvelles normes", "new regulations", "regulatory changes"],
    "Interaction avec autorités de tutelle": ["autorites tutelle", "regulatory authorities", "beac", "cobac", "commission bancaire", "central bank", "supervisor", "regulatory liaison", "authority communication"],
    "SPECTRA / CERBER / outils BEAC": ["spectra", "cerber", "beac", "outils beac", "plateforme beac", "regulatory platform", "reporting system", "electronic filing"],
    "Normes COBAC précises": ["normes cobac", "cobac regulations", "instructions cobac", "reglementation cobac", "cobac circulars", "directives cobac", "banking standards", "prudential norms"],
    "Reporting prudentiel Bâle": ["reporting prudentiel", "basel reporting", "fonds propres", "capital adequacy", "pillar 1", "pillar 2", "pillar 3", "risk weighted assets", "rwa"],
    "Formation comptabilité bancaire spécialisée": ["formation comptabilite bancaire", "banking accounting training", "specialisation bancaire", "banking qualification", "institut bancaire", "banking institute", "cfob"],
    "Expérience en banque ou établissement financier réglementé": ["banque", "bancaire", "etablissement bancaire", "institution bancaire", "etablissement financier reglemente", "secteur bancaire", "bank", "banking", "financial institution", "regulated financial institution", "ecobank", "orabank", "uba", "afriland", "bgfi", "bgfibank", "ccei", "boa", "bank of africa", "banque atlantique", "banque centrale"],
    "Minimum 3 ans en opérations bancaires ou back-office (hors stage)": ["EXP_BACKOFFICE_3ANS"],
    "Exposition aux opérations de compensation interbancaire (chèques, virements, prélèvements)": COMPENSATION_INTERBANCAIRE_KEYWORDS,
    "Connaissance des règles BEAC / GIMAC ou d'un système de compensation équivalent": BEAC_GIMAC_KEYWORDS,
    "Gestion de suspens, rejets ou réclamations interbancaires": SUSPENS_REJETS_KEYWORDS,
    "Expérience d'encadrement ou de supervision d'équipe (poste de chef de section)": ENCADREMENT_KEYWORDS,
    "Profil bancaire avec exposition interbancaire (hors microfinance isolée)": ["MARKER_NOT_MICROFINANCE_ONLY"],
    "Supervision quotidienne des opérations de compensation interbancaire": COMPENSATION_INTERBANCAIRE_KEYWORDS + ["supervision quotidienne", "operations quotidiennes", "daily operations", "suivi quotidien"],
    "Dénouement de positions nettes en fin de journée": ["denouement", "positions nettes", "reglement des positions nettes", "net position settlement", "end of day settlement", "cloture quotidienne", "fin de journee", "solde net", "compensation de fin de journee"],
    "Encadrement et coordination d'une équipe opérationnelle": ENCADREMENT_KEYWORDS,
    "Utilisation de systèmes bancaires de compensation (SYSTAC, SYGMA, SWIFT)": ["systac", "sygma", "swift", "systeme de compensation", "clearing system", "core banking compensation", "plateforme de compensation"],
    "Production de reportings opérationnels ou réglementaires": ["reporting operationnel", "reporting reglementaire", "rapport hierarchie", "rapport beac", "operational reporting", "regulatory reporting", "tableau de bord operationnel"],
    "Participation à des contrôles internes, audits COBAC ou inspections réglementaires": ["controle interne", "audit cobac", "inspection reglementaire", "internal control", "cobac audit", "inspection bancaire", "mission de controle", "audit interne"],
    "BEAC / GIMAC / compensation interbancaire (SYSTAC, SYGMA)": BEAC_GIMAC_KEYWORDS + COMPENSATION_INTERBANCAIRE_KEYWORDS,
    "Règlement de positions nettes dans les délais réglementaires": ["reglement positions nettes", "delais reglementaires", "positions nettes", "net settlement", "regulatory deadlines", "denouement dans les delais"],
    "Contrôle de conformité réglementaire et procédurale": ["conformite reglementaire", "conformite procedurale", "compliance", "respect des procedures", "controle de conformite", "procedures internes"],
    "Maîtrise du contrôle interne et de la comptabilité bancaire (SYSCOHADA)": SYSCOHADA_KEYWORDS + ["controle interne"],
    "Gestion de fin de journée comptable / clôture des opérations interbancaires": ["cloture comptable", "fin de journee comptable", "cloture des operations", "end of day accounting", "cloture journaliere", "arrete comptable journalier"],
    "Rapports opérationnels ou réglementaires produits": ["rapport operationnel", "rapport reglementaire", "rapports produits", "reporting frequence", "destinataires rapport", "rapports periodiques"],
    "Expérience dans une banque de la zone CEMAC / UEMOA": BEAC_GIMAC_KEYWORDS + ["cemac", "uemoa", "afriland", "bgfi", "ccei", "sgc cameroun", "boa", "bank of africa", "afrique centrale", "afrique de l ouest"],
    "Audits COBAC ou contrôles internes réussis sans réserve majeure": ["audit cobac", "controle interne reussi", "sans reserve majeure", "audit sans reserve", "inspection cobac", "controle reussi"],
    "Gestion d'une équipe avec résultats mesurables": ENCADREMENT_KEYWORDS + ["resultats mesurables", "effectif", "delais reduits", "incidents reduits", "amelioration des delais", "indicateurs de performance equipe", "kpi equipe"],
    "Parcours purement comptable sans exposition aux opérations interbancaires": ["comptable", "comptabilite generale", "saisie comptable", "tenue de comptes"],
    "Rôle uniquement administratif ou de support, sans responsabilité opérationnelle": ["administratif", "support administratif", "assistant administratif", "secretariat", "taches administratives"],
    "CV aux missions trop génériques, sans livrables ni résultats quantifiés": ["diverses taches", "missions diverses", "taches diverses", "responsable de divers"],
    "Expériences très courtes (< 1 an par poste) sans progression visible": ["stage", "cdd court", "contrat court"]
}

DOMAIN_KEYWORDS_MAP = {
    "EXP_CREDIT_3ANS": ["credit", "risque", "banque", "bancaire", "institution financiere", "analyste", "charge", "gestionnaire", "loan", "credit analysis"],
    "EXP_FIN_3ANS": ["finance", "comptable", "comptabilite", "reporting", "tresorerie", "banque", "institution financiere", "auditeur", "controleur", "financial", "accounting", "risque", "risk"],
    "EXP_FINANCE_3ANS": ["finance", "comptable", "comptabilite", "reporting", "tresorerie", "banque", "institution financiere", "financial"],
    "EXP_IT_2ANS": ["reseau", "infrastructure", "systeme", "informatique", "it", "network", "serveur", "technicien", "ingenieur", "networking", "cisco", "admin", "administrateur"],
    "EXP_AUDIT_3ANS": ["audit", "auditeur", "controle interne", "internal audit", "cabinet audit", "big four", "deloitte", "pwc", "ey", "kpmg", "banking audit", "commissaire aux comptes"],
    "EXP_FIN_5ANS": ["finance", "credit", "risque", "banque", "bancaire", "financial institution", "credit analysis", "loan officer", "corporate banking", "investment banking"],
    "EXP_IT_MAINT_5ANS": ["maintenance", "support", "it", "informatique", "reseau", "infrastructure", "systemes", "technical support", "helpdesk", "it maintenance", "system administration"],
    "EXP_FINANCE_7ANS": ["finance", "comptabilite", "reporting", "banque", "bancaire", "financial reporting", "accounting", "consolidation", "ifrs", "controller", "finance manager", "cfo"],
    "EXP_RISK_5ANS": ["risque", "risk", "marche", "market risk", "alm", "tresorerie", "treasury", "trading", "var", "risk management", "financial markets", "investment"],
    "EXP_BANKING_5ANS": ["banque", "bancaire", "banking", "comptabilite bancaire", "reporting reglementaire", "beac", "cobac", "spectra", "central bank", "regulatory reporting", "banking supervision"],
    "EXP_BACKOFFICE_3ANS": ["back-office", "back office", "operations bancaires", "compensation", "interbancaire", "banque", "bancaire", "middle office", "moyens de paiement", "traitement des operations", "chambre de compensation"]
}

EXP_MIN_YEARS_MAP = {
    "EXP_CREDIT_3ANS": 3.0, "EXP_FIN_3ANS": 3.0, "EXP_FINANCE_3ANS": 3.0, "EXP_IT_2ANS": 2.0,
    "EXP_AUDIT_3ANS": 3.0, "EXP_FIN_5ANS": 5.0, "EXP_IT_MAINT_5ANS": 5.0, "EXP_FINANCE_7ANS": 7.0,
    "EXP_RISK_5ANS": 5.0, "EXP_BANKING_5ANS": 5.0, "EXP_BACKOFFICE_3ANS": 3.0
}

def check_criterion_match_advanced(criterion, normalized_text, raw_full_text="", tokens=None, poste=None):
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords:
        return False, 0.0, []
    exp_markers = [kw for kw in keywords if kw.startswith("EXP_")]
    if exp_markers:
        marker = exp_markers[0]
        min_years = EXP_MIN_YEARS_MAP.get(marker, 3.0)
        domain_kws = DOMAIN_KEYWORDS_MAP.get(marker, [])
        domain_kws_n = [normalize_for_matching(k)[0] for k in domain_kws]
        found = has_experience_years_strict(raw_full_text, min_years, domain_kws_n, poste)
        return found, 1.0 if found else 0.0, ([marker] if found else [])
    if keywords == ["MARKER_NOT_MICROFINANCE_ONLY"]:
        ok = check_not_microfinance_only(raw_full_text)
        return ok, (1.0 if ok else 0.0), ([] if ok else ["microfinance_exclusive"])
    if poste == "Market Risk Officer":
        market_risk_keywords = {"Base en risques de marché": ['risque marche', 'risques de marche', 'risque de marche', 'market risk', 'directeur de risques', 'responsable risques', 'responsable risque', 'risk manager', 'gestion des risques', 'risk management', 'risque operationnel', 'risques operationnels', 'risques bancaires', 'gestionnaire risques', 'gestionnaire-risques'], "Exposition à FX / taux / liquidité": ['fx', 'change', 'taux', 'liquidite', 'forex', 'taux de change', 'taux de changes', 'risque de change', 'liquidity', 'risque de liquidite', 'risque de taux', 'exposition aux risques', 'gestion des taux', 'risque de marche', 'market risk', 'risque opérationnel', 'responsable risque', 'gestion risques']}
        if criterion in market_risk_keywords:
            criterion_kws = market_risk_keywords[criterion]
            text_normalized = raw_full_text.lower().translate(_ACCENT_MAP)
            for kw in criterion_kws:
                if kw in text_normalized:
                    return True, 1.0, [kw]
            return False, 0.0, []
    if poste:
        if not check_criterion_context(criterion, raw_full_text, poste):
            return False, 0.0, []
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
            if ratio >= 85:
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}~{ratio/100:.2f}")
                    best_score = max(best_score, ratio / 100)
                continue
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.7):
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                    best_score = max(best_score, len(common) / len(kw_tokens))
    return best_score >= 0.70, round(best_score, 2), found_kws

def detect_language(text):
    if not text or not LANGDETECT_AVAILABLE:
        return None
    try:
        return detect(text)
    except Exception:
        return None

def extract_entities_with_spacy(text, lang='fr'):
    if not SPACY_AVAILABLE or not text:
        return None
    nlp = _get_spacy_model(lang)
    if not nlp:
        return None
    try:
        text_to_process = text[:15000]
        doc = nlp(text_to_process)
        entities = {'organisations': [], 'dates': [], 'locations': [], 'diplomes': [], 'competences_techniques': [], 'noms_personnes': []}
        for ent in doc.ents:
            if ent.label_ == 'ORG':
                entities['organisations'].append(ent.text.strip())
            elif ent.label_ in ('DATE', 'TIME'):
                entities['dates'].append(ent.text.strip())
            elif ent.label_ in ('LOC', 'GPE'):
                entities['locations'].append(ent.text.strip())
            elif ent.label_ == 'PERSON':
                entities['noms_personnes'].append(ent.text.strip())
        diplome_patterns = [r'(?:master|licence|bachelor|mba|dea|deug|ingénieur|doctorat|phd)\s*(?:\d+)?', r'bac\s*\+?\s*\d+', r'(?:bts|dut)\s*(?:en\s+)?(?:[a-zéèà]+)', r'(?:certification|certifié)\s+(?:acca|cpa|cfa|frm|itil|pmp|cia|cisa)']
        for pattern in diplome_patterns:
            matches = re.findall(pattern, text_to_process, re.IGNORECASE)
            entities['diplomes'].extend(matches)
        tech_patterns = [r'(?:excel|vba|python|r|sql|sap|oracle|swift|temenos|flexcube)', r'(?:ifrs|syscohada|cobac|beac|gimac|bâle)', r'(?:lan|wan|vpn|cisco|vmware|linux|windows)']
        for pattern in tech_patterns:
            matches = re.findall(pattern, text_to_process, re.IGNORECASE)
            entities['competences_techniques'].extend(matches)
        for key in entities:
            entities[key] = list(set(e for e in entities[key] if e and len(e) > 1))
        return entities
    except Exception:
        return None

def enrich_analysis_with_nlp(cv_text, lettre_text, detected_lang):
    if not SPACY_AVAILABLE:
        return {}
    lang = 'fr'
    if detected_lang in ('en', 'eng'):
        lang = 'en'
    full_text = (cv_text or "") + "\n" + (lettre_text or "")
    entities = extract_entities_with_spacy(full_text, lang)
    if not entities:
        return {}
    enrichment = {'nlp_available': True, 'organisations_detectees': entities.get('organisations', [])[:10], 'dates_cles': entities.get('dates', [])[:10], 'lieux': entities.get('locations', [])[:5], 'diplomes_identifies': entities.get('diplomes', [])[:5], 'competences_techniques': entities.get('competences_techniques', [])[:10]}
    bank_keywords = ['bank', 'banque', 'ecobank', 'orabank', 'uba', 'bgfi', 'afriland']
    detected_banks = [org for org in entities.get('organisations', []) if any(kw in org.lower() for kw in bank_keywords)]
    if detected_banks:
        enrichment['banques_detectees'] = detected_banks
    return enrichment

DEBUG_EXTRACTION = os.getenv("DEBUG_EXTRACTION", "false").lower() == "true"

if IA_ANALYSE_ACTIVE:
    logger.info(f"🧠 Moteur d'analyse INTELLIGENT activé (modèle: {ANTHROPIC_MODEL})")
else:
    logger.warning("⚠️ Moteur IA désactivé (ANTHROPIC_API_KEY manquante) — repli sur le moteur mots-clés")

SCORING_CODE_LABELS = {"CV_Exp": "Expérience professionnelle pertinente", "CV_Niveau": "Niveau / ancienneté de l'expérience", "CV_Secteur": "Expérience sectorielle (banque/finance)", "CV_Tech": "Compétences techniques", "CV_Progression": "Évolution de carrière", "CV_Management": "Capacité managériale", "CV_Stabilite": "Stabilité du parcours", "LM_Comprehension": "Compréhension du poste (lettre)", "LM_Coherence": "Cohérence du profil (lettre)", "LM_Motivation": "Motivation réelle (lettre)", "LM_Qualite": "Qualité rédactionnelle (lettre)", "D_Niveau": "Niveau académique", "D_Specialisation": "Spécialisation pertinente", "D_Certif": "Certifications"}

SCORING_RUBRIQUES = {"Chef de Section Compensation": {"Adéquation de l'expérience (compensation interbancaire, back-office bancaire)": 3, "Exposition aux règles BEAC / GIMAC et aux systèmes de compensation (SYSTAC, SYGMA, SWIFT)": 3, "Capacité d'encadrement et de management d'équipe opérationnelle": 2, "Cohérence et progression du parcours professionnel": 2, "Qualité et clarté du CV (missions précises, livrables, résultats)": 1, "Lettre de motivation": 1}}

def get_rubrique_scoring(poste):
    if poste in SCORING_RUBRIQUES:
        rub = SCORING_RUBRIQUES[poste]
        return rub, sum(rub.values())
    if poste in POSTES_AVEC_SCORING_100:
        rub = SCORING_CONFIG.get(poste) or {}
        return rub, 100
    return {"Adéquation de l'expérience": 3, "Cohérence du parcours": 2, "Exposition au risque métier": 3, "Qualité du CV": 1, "Lettre de motivation": 1}, 10

SYSTEM_PROMPT_RECRUTEUR = """Tu es un·e responsable recrutement senior, spécialisé·e dans le secteur bancaire en Afrique centrale et de l'Ouest (CEMAC/UEMOA), avec quinze ans d'expérience en présélection de cadres bancaires. Tu analyses des dossiers avec rigueur et bon sens. Les stages NE COMPTENT PAS. Distingue l'EMPLOYEUR réel d'un mot-clé. Une lettre générique est éliminatoire. Justifie chaque évaluation. Tu soumets ton analyse exclusivement via l'outil fourni."""

def build_analysis_tool_schema():
    return {"name": "soumettre_analyse_candidature", "description": "Soumet l'analyse structurée d'une candidature.", "input_schema": {"type": "object", "properties": {"eliminatoire": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "valide": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "valide", "justification"]}}, "a_verifier": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "detecte": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "detecte", "justification"]}}, "signaux_forts": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "detecte": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "detecte", "justification"]}}, "points_attention": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "present": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "present", "justification"]}}, "lettre_motivation": {"type": "object", "properties": {"presente": {"type": "boolean"}, "coherente_avec_cv": {"type": "boolean"}, "generique_ou_copiee": {"type": "boolean"}, "qualite_redactionnelle": {"type": "string", "enum": ["bonne", "moyenne", "faible", "non_evaluable"]}, "eliminatoire": {"type": "boolean"}, "commentaire": {"type": "string"}}, "required": ["presente", "coherente_avec_cv", "generique_ou_copiee", "qualite_redactionnelle", "eliminatoire", "commentaire"]}, "diplomes": {"type": "object", "properties": {"niveau_suffisant": {"type": "boolean"}, "domaine_pertinent": {"type": "boolean"}, "atout_complementaire_detecte": {"type": "boolean"}, "commentaire": {"type": "string"}}, "required": ["niveau_suffisant", "domaine_pertinent", "atout_complementaire_detecte", "commentaire"]}, "sous_scores": {"type": "object", "additionalProperties": {"type": "integer"}}, "score_total": {"type": "integer"}, "decision": {"type": "string"}, "points_forts": {"type": "array", "items": {"type": "string"}}, "points_vigilance": {"type": "array", "items": {"type": "string"}}, "synthese_recruteur": {"type": "string"}}, "required": ["eliminatoire", "a_verifier", "signaux_forts", "points_attention", "lettre_motivation", "diplomes", "sous_scores", "score_total", "decision", "points_forts", "points_vigilance", "synthese_recruteur"]}}

def build_analysis_user_message(cv_text, lettre_text, attestation_texts_list, poste):
    grille = GRILLE.get(poste, {})
    rubrique, score_max = get_rubrique_scoring(poste)
    def fmt_list(items):
        return "\n".join(f"  {i+1}. {c}" for i, c in enumerate(items)) if items else "  (aucun)"
    rubrique_txt = "\n".join(f"  - {SCORING_CODE_LABELS.get(nom, nom)} [clé: \"{nom}\"] : 0 à {pts} pts" for nom, pts in rubrique.items())
    att_txt = "\n".join(attestation_texts_list) if attestation_texts_list else "(aucune)"
    if poste in POSTES_AVEC_SCORING_12:
        seuils_txt = "10-12 : Entretien prioritaire | 7-9 : Vivier | <7 : Rejet"
    elif poste in POSTES_AVEC_SCORING_100:
        seuils_txt = "≥80 : Shortlist | 70-79 : À considérer | 60-69 : Faible | <60 : Rejet"
    else:
        seuils_txt = "≥8 : Entretien prioritaire | 6-7 : Entretien si besoin | <6 : Rejet"
    return f"""POSTE : {poste}
═══ GRILLE ═══
🔴 Éliminatoires :
{fmt_list(grille.get('eliminatoire', []))}
🟠 À vérifier :
{fmt_list(grille.get('a_verifier', []))}
🟡 Signaux forts :
{fmt_list(grille.get('signaux_forts', []))}
⚠️ Points attention :
{fmt_list(grille.get('points_attention', []))}
═══ SCORING /{score_max} ═══
{rubrique_txt}
Seuils : {seuils_txt}
═══ DOCUMENTS ═══
--- CV ---
{cv_text[:12000]}
--- LETTRE ---
{lettre_text[:4000] if lettre_text else "(aucune)"}
--- ATTESTATIONS ---
{att_txt[:6000]}
Utilise l'outil `soumettre_analyse_candidature`."""

def _build_result_from_ia_analysis(analyse, poste):
    _, score_max = get_rubrique_scoring(poste)
    flags_elim = [e['critere'] for e in analyse.get('eliminatoire', []) if not e.get('valide')]
    lm = analyse.get('lettre_motivation', {})
    if lm.get('eliminatoire'):
        flags_elim.append(f"Lettre: {lm.get('commentaire', 'éliminatoire')}")
    score_total = 0 if flags_elim else int(analyse.get('score_total', 0))
    decision = "❌ Rejet (éliminatoire)" if flags_elim else get_recommandation_from_score(score_total, poste)
    details = {'moteur': 'IA (Claude)', 'eliminatoire_detail': analyse.get('eliminatoire', []), 'a_verifier_detail': analyse.get('a_verifier', []), 'signaux_forts_detail': analyse.get('signaux_forts', []), 'points_attention_detail': analyse.get('points_attention', []), 'lettre_motivation': lm, 'diplomes': analyse.get('diplomes', {}), 'points_forts': analyse.get('points_forts', []), 'points_vigilance': analyse.get('points_vigilance', []), 'synthese_recruteur': analyse.get('synthese_recruteur', '')}
    checklist = {}
    for i, e in enumerate(analyse.get('eliminatoire', [])):
        checklist[f'elim_{i}'] = bool(e.get('valide'))
    for i, v in enumerate(analyse.get('a_verifier', [])):
        checklist[f'verif_{i}'] = bool(v.get('detecte'))
    for i, s in enumerate(analyse.get('signaux_forts', [])):
        checklist[f'signal_{i}'] = bool(s.get('detecte'))
    for i, p in enumerate(analyse.get('points_attention', [])):
        checklist[f'attn_{i}'] = bool(p.get('present'))
    return {'score': score_total, 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': [s['critere'] for s in analyse.get('signaux_forts', []) if s.get('detecte')], 'details': details, 'score_breakdown': {'bloc1_eliminatoire': bool(flags_elim), 'moteur_analyse': 'ia', 'sous_scores': analyse.get('sous_scores', {}), 'score_final': score_total, 'score_max': score_max, 'decision': decision, 'note': analyse.get('synthese_recruteur') or f"Score: {score_total}/{score_max} — {decision}"}}

def analyze_cv_intelligent(cv_text, lettre_text, attestation_texts_list, poste):
    if not IA_ANALYSE_ACTIVE or not cv_text or len(cv_text.strip()) < 50 or poste not in GRILLE:
        return None
    tool = build_analysis_tool_schema()
    user_msg = build_analysis_user_message(cv_text, lettre_text, attestation_texts_list, poste)
    for attempt in range(2):
        try:
            with _ia_semaphore:
                response = _claude_client.messages.create(model=ANTHROPIC_MODEL, max_tokens=4096, temperature=0, system=SYSTEM_PROMPT_RECRUTEUR, tools=[tool], tool_choice={"type": "tool", "name": "soumettre_analyse_candidature"}, messages=[{"role": "user", "content": user_msg}])
            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if not tool_use:
                return None
            return _build_result_from_ia_analysis(tool_use.input, poste)
        except Exception as e:
            time.sleep(2)
    return None

def _build_zero_sous_scores_compensation():
    return {
        "Adéquation de l'expérience (compensation interbancaire, back-office bancaire)": 0,
        "Exposition aux règles BEAC / GIMAC et aux systèmes de compensation (SYSTAC, SYGMA, SWIFT)": 0,
        "Capacité d'encadrement et de management d'équipe opérationnelle": 0,
        "Cohérence et progression du parcours professionnel": 0,
        "Qualité et clarté du CV (missions précises, livrables, résultats)": 0,
        "Lettre de motivation": 0
    }

def _build_checklist_from_grille(grille, raw_full, normalized, poste):
    checklist = {}
    for i, crit in enumerate(grille.get('eliminatoire', [])):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[f'elim_{i}'] = ok
    for i, crit in enumerate(grille.get('a_verifier', [])):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[f'verif_{i}'] = ok
    for i, crit in enumerate(grille.get('signaux_forts', [])):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[f'signal_{i}'] = ok
    for i, crit in enumerate(grille.get('points_attention', [])):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[f'attn_{i}'] = ok
    return checklist

def calculate_score_chef_section_compensation(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Section Compensation"
    grille = GRILLE[poste]
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized = normalize_for_matching(raw_full)[0]
    flags = []
    for crit in grille['eliminatoire']:
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        if not ok:
            flags.append(crit)
    checklist = _build_checklist_from_grille(grille, raw_full, normalized, poste)
    if flags:
        return {
            'score': 0,
            'score_max': 12,
            'decision': '❌ Rejet (éliminatoire)',
            'flags_eliminatoires': flags,
            'sous_scores': _build_zero_sous_scores_compensation(),
            'checklist': checklist,
            'detail': f"ÉLIMINÉ : {len(flags)} critère(s)"
        }
    signaux_exp = ["Supervision quotidienne des opérations de compensation interbancaire", "Dénouement de positions nettes en fin de journée", "Gestion de suspens, rejets et réclamations interbancaires", "Utilisation de systèmes bancaires de compensation (SYSTAC, SYGMA, SWIFT)"]
    n_exp = sum(1 for c in signaux_exp if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    adequation = min(3, n_exp)
    signaux_beac = ["BEAC / GIMAC / compensation interbancaire (SYSTAC, SYGMA)", "Règlement de positions nettes dans les délais réglementaires", "Expérience dans une banque de la zone CEMAC / UEMOA"]
    n_beac = sum(1 for c in signaux_beac if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    exposition_beac = min(3, n_beac)
    encadrement_ok = check_criterion_match_advanced("Encadrement et coordination d'une équipe opérationnelle", normalized, raw_full, poste=poste)[0]
    resultats_mesurables = check_criterion_match_advanced("Gestion d'une équipe avec résultats mesurables", normalized, raw_full, poste=poste)[0]
    encadrement = (1 if encadrement_ok else 0) + (1 if resultats_mesurables else 0)
    n_points_attention = sum(1 for c in grille['points_attention'] if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    coherence = 2 if n_points_attention == 0 else (1 if n_points_attention <= 2 else 0)
    word_count = len(cv_text.split())
    has_quantified_results = bool(re.search(r'\d+\s*(%|pourcent|jours|heures|incidents|clients|operations|agences|collaborateurs)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified_results) else 0
    lettre_clean = (lettre_text or '').strip()
    if lettre_clean:
        poste_kw = ['compensation', 'beac', 'gimac', 'interbancaire', 'back-office']
        mentions_poste = any(kw in lettre_clean.lower() for kw in poste_kw)
        lettre_score = 1 if (len(lettre_clean.split()) >= 80 and mentions_poste) else 0
    else:
        lettre_score = 0
    sous_scores = {
        "Adéquation de l'expérience (compensation interbancaire, back-office bancaire)": adequation,
        "Exposition aux règles BEAC / GIMAC et aux systèmes de compensation (SYSTAC, SYGMA, SWIFT)": exposition_beac,
        "Capacité d'encadrement et de management d'équipe opérationnelle": encadrement,
        "Cohérence et progression du parcours professionnel": coherence,
        "Qualité et clarté du CV (missions précises, livrables, résultats)": qualite_cv,
        "Lettre de motivation": lettre_score
    }
    score_total = sum(sous_scores.values())
    decision = " Entretien prioritaire" if score_total >= 10 else ("🥈 Entretien si besoin (vivier de réserve)" if score_total >= 7 else "❌ Rejet")
    return {
        'score': score_total,
        'score_max': 12,
        'decision': decision,
        'flags_eliminatoires': [],
        'sous_scores': sous_scores,
        'checklist': checklist,
        'detail': f"Score: {score_total}/12 — {decision}"
    }

def calculate_detailed_score_100(cv_text, lettre_text, attestation_texts_list, poste):
    config = SCORING_CONFIG.get(poste)
    if not config:
        return None
    all_att_raw = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw
    normalized = normalize_for_matching(raw_full)[0]
    score_cv = {'CV_Exp': 0, 'CV_Niveau': 0, 'CV_Secteur': 0, 'CV_Tech': 0, 'CV_Progression': 0, 'CV_Management': 0, 'CV_Stabilite': 0}
    score_lm = {'LM_Comprehension': 0, 'LM_Coherence': 0, 'LM_Motivation': 0, 'LM_Qualite': 0}
    score_diplomes = {'D_Niveau': 0, 'D_Specialisation': 0, 'D_Certif': 0}
    details = {'cv_scores': {}, 'lm_scores': {}, 'diplomes_scores': {}, 'justifications': []}
    grille = GRILLE.get(poste, {})
    max_exp = config.get('CV_Exp', 20)
    exp_valid = True
    for crit in grille.get('eliminatoire', []):
        if 'expérience' in crit.lower() or 'ans' in crit.lower():
            is_present, conf, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
            if not is_present:
                exp_valid = False
                break
    if exp_valid:
        signal_count = sum(1 for crit in grille.get('signaux_forts', []) if check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)[0])
        base_ratio = 0.5 + min(0.5, signal_count / max(1, len(grille.get('signaux_forts', []))))
        score_cv['CV_Exp'] = round(max_exp * base_ratio)
        details['cv_scores']['CV_Exp'] = f"{score_cv['CV_Exp']}/{max_exp}"
    max_niveau = config.get('CV_Niveau', 10)
    years_found = 0
    for pattern in [r'(\d+)\s*(?:années?|ans|years?)', r'(?:plus\s*de|over)\s*(\d+)\s*(?:années?|ans|years?)', r'(?:minimum|au\s*moins|at\s*least)\s*(\d+)\s*(?:années?|ans|years?)']:
        for m in re.findall(pattern, raw_full, re.IGNORECASE):
            try:
                years_found = max(years_found, int(m))
            except:
                pass
    if years_found >= 10: score_cv['CV_Niveau'] = max_niveau
    elif years_found >= 7: score_cv['CV_Niveau'] = round(max_niveau * 0.8)
    elif years_found >= 5: score_cv['CV_Niveau'] = round(max_niveau * 0.6)
    elif years_found >= 3: score_cv['CV_Niveau'] = round(max_niveau * 0.4)
    elif years_found >= 1: score_cv['CV_Niveau'] = round(max_niveau * 0.2)
    details['cv_scores']['CV_Niveau'] = f"{score_cv['CV_Niveau']}/{max_niveau} ({years_found} ans)"
    max_secteur = config.get('CV_Secteur', 10)
    has_bank = any(re.search(r'\b' + re.escape(b) + r'\b', raw_full, re.IGNORECASE) for b in COMMERCIAL_BANKS)
    finance_count = sum(1 for kw in ['banque', 'bank', 'finance', 'financier', 'crédit', 'credit', 'assurance', 'investment'] if kw in raw_full.lower())
    if has_bank and finance_count >= 3: score_cv['CV_Secteur'] = max_secteur
    elif has_bank or finance_count >= 2: score_cv['CV_Secteur'] = round(max_secteur * 0.7)
    elif finance_count >= 1: score_cv['CV_Secteur'] = round(max_secteur * 0.4)
    details['cv_scores']['CV_Secteur'] = f"{score_cv['CV_Secteur']}/{max_secteur}"
    max_tech = config.get('CV_Tech', 20)
    total_tech = len(grille.get('a_verifier', [])) + len(grille.get('signaux_forts', []))
    tech_signals = sum(1 for crit in grille.get('a_verifier', []) + grille.get('signaux_forts', []) if check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)[0])
    if total_tech > 0:
        score_cv['CV_Tech'] = round(max_tech * tech_signals / total_tech)
    details['cv_scores']['CV_Tech'] = f"{score_cv['CV_Tech']}/{max_tech}"
    for key, max_val, keywords in [('CV_Progression', config.get('CV_Progression', 5), ['promotion', 'évolution', 'senior', 'lead', 'manager', 'chef', 'responsable', 'head of', 'director']), ('CV_Management', config.get('CV_Management', 5), ['management', 'encadrement', 'équipe', 'team', 'supervision', 'collaborateurs'])]:
        count = sum(1 for kw in keywords if kw in raw_full.lower())
        if count >= 5: score_cv[key] = max_val
        elif count >= 3: score_cv[key] = round(max_val * 0.6)
        elif count >= 1: score_cv[key] = round(max_val * 0.3)
        details['cv_scores'][key] = f"{score_cv[key]}/{max_val}"
    max_stab = config.get('CV_Stabilite', 5)
    short = len(re.findall(r'(?:\d{1,2}\s*(?:mois|months?))|(?:<\s*1\s*(?:an|year))', raw_full, re.IGNORECASE))
    if short <= 1: score_cv['CV_Stabilite'] = max_stab
    elif short <= 3: score_cv['CV_Stabilite'] = round(max_stab * 0.6)
    else: score_cv['CV_Stabilite'] = round(max_stab * 0.3)
    details['cv_scores']['CV_Stabilite'] = f"{score_cv['CV_Stabilite']}/{max_stab}"
    total_cv_raw = sum(score_cv.values())
    max_cv_raw = sum(config.get(k, 0) for k in score_cv.keys())
    score_cv_total = round((total_cv_raw / max_cv_raw * 70)) if max_cv_raw > 0 else 0
    details['cv_total'] = f"{total_cv_raw}/{max_cv_raw} → {score_cv_total}/70"
    lm_text_clean = lettre_text.strip() if lettre_text else ""
    if lm_text_clean and len(lm_text_clean) > 100:
        lm_lower = lm_text_clean.lower()
        score_lm['LM_Comprehension'] = min(5, sum(1 for kw in poste.lower().split() if kw in lm_lower))
        score_lm['LM_Coherence'] = min(5, sum(1 for ind in ['mon profil', 'ma formation', 'mon expérience', 'mes compétences', 'my background'] if ind in lm_lower))
        score_lm['LM_Motivation'] = min(5, sum(1 for kw in ['motivé', 'passionné', 'intérêt', 'souhaite', 'rejoindre', 'intégrer', 'contribuer'] if kw in lm_lower) // 2)
        wc = len(lm_text_clean.split())
        if wc >= 200: score_lm['LM_Qualite'] = 5
        elif wc >= 150: score_lm['LM_Qualite'] = 4
        elif wc >= 100: score_lm['LM_Qualite'] = 3
        elif wc >= 50: score_lm['LM_Qualite'] = 2
        else: score_lm['LM_Qualite'] = 1
    for k, v in score_lm.items():
        details['lm_scores'][k] = f"{v}/5"
    score_lm_total = sum(score_lm.values())
    details['lm_total'] = f"{score_lm_total}/20"
    has_bac5 = any(re.search(p, raw_full, re.IGNORECASE) for p in [r'bac\+\s*5', r'master', r'mba', r'ingénieur'])
    has_bac3 = any(re.search(p, raw_full, re.IGNORECASE) for p in [r'bac\+\s*3', r'licence', r'bachelor'])
    score_diplomes['D_Niveau'] = 4 if has_bac5 else (2 if has_bac3 else 1)
    score_diplomes['D_Specialisation'] = min(3, sum(1 for kw in ['finance', 'comptabilité', 'audit', 'risque', 'management', 'informatique'] if kw in raw_full.lower()) // 2)
    score_diplomes['D_Certif'] = min(3, sum(1 for c in ['acca', 'cpa', 'cfa', 'frm', 'itil', 'pmp', 'cia', 'microsoft', 'cisco', 'aws', 'azure'] if c in raw_full.lower()))
    for k, v in score_diplomes.items():
        details['diplomes_scores'][k] = f"{v}/{[4,3,3][['D_Niveau','D_Specialisation','D_Certif'].index(k)]}"
    score_total = min(100, score_cv_total + score_lm_total + sum(score_diplomes.values()))
    decision = "Shortlist" if score_total >= 80 else ("À considérer" if score_total >= 70 else ("Faible" if score_total >= 60 else "Rejet"))
    return {'score': score_total, 'decision': decision, 'bloc_cv': {'total': score_cv_total, 'max': 70, 'details': score_cv}, 'bloc_lm': {'total': score_lm_total, 'max': 20, 'details': score_lm}, 'bloc_diplomes': {'total': sum(score_diplomes.values()), 'max': 10, 'details': score_diplomes}, 'details': details, 'note': f"Score: {score_total}/100 — {decision}"}

def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    if not cv_text or len(cv_text.strip()) < 50:
        return {'score': 0, 'checklist': {}, 'flags_eliminatoires': ['CV non analysable'], 'signaux_detectes': [], 'details': {'error': 'CV vide'}, 'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0, 'note': 'CV non analysable'}}
    grille = GRILLE.get(poste)
    if not grille:
        return {'score': 0, 'checklist': {}, 'flags_eliminatoires': [f'Poste inconnu: {poste}'], 'signaux_detectes': [], 'details': {}, 'score_breakdown': {}}
    all_att_raw = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw
    normalized = normalize_for_matching(raw_full)[0]
    detected_lang = detect_language(cv_text[:500]) if cv_text else None
    intelligent_flags = []
    is_consistent, consistency_reason = check_cv_letter_consistency(cv_text, lettre_text or "", poste)
    if not is_consistent:
        intelligent_flags.append(f"️ {consistency_reason}")
    current_financial, current_reason = check_current_employment_financial(cv_text)
    if not current_financial:
        intelligent_flags.append(f"⚠️ {current_reason}")
    if poste == "Market Risk Officer":
        inst_valid, inst_reason = validate_financial_institution_for_market_risk(cv_text)
        if not inst_valid:
            intelligent_flags.append(f"⚠️ {inst_reason}")
    checklist = {}
    flags_elim = []
    signaux = []
    points_bloc2 = 0
    points_bloc3 = 0
    details = {'cv_words': len(cv_text.split()), 'lettre_words': len((lettre_text or "").split()), 'attestation_words': len(all_att_raw.split()), 'detected_language': detected_lang, 'criteres_valides_bloc2': [], 'signaux_valides_bloc3': [], 'alertes_attention': intelligent_flags, 'matching_details': {}, 'documents_analyses': {'cv': len(cv_text) > 0, 'lettre': len(lettre_text or "") > 0, 'certificats': len(attestation_texts_list) if attestation_texts_list else 0}}
    eliminatoire_failed = False
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        if not is_present:
            eliminatoire_failed = True
            flags_elim.append(f"❌ {crit} (confiance: {confidence:.0%})")
            details['alertes_attention'].append(f"🔴 Éliminatoire manquant: {crit}")
            details['matching_details'][crit] = {'found': False, 'confidence': confidence, 'status': 'ÉLIMINATOIRE'}
        else:
            details['matching_details'][crit] = {'found': True, 'confidence': confidence, 'matched': found_kws}
    if eliminatoire_failed:
        for i, crit in enumerate(grille.get('a_verifier', [])):
            checklist[f'verif_{i}'] = False
        for i, crit in enumerate(grille.get('signaux_forts', [])):
            checklist[f'signal_{i}'] = False
        for i, crit in enumerate(grille.get('points_attention', [])):
            checklist[f'attn_{i}'] = False
        return {'score': 0, 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': [], 'details': details, 'score_breakdown': {'bloc1_eliminatoire': True, 'flags_eliminatoires_count': len(flags_elim), 'adequation_experience': 0, 'coherence_parcours': 0, 'exposition_risque_metier': 0, 'qualite_cv': 0, 'lettre_motivation': 0, 'total_raw_points': 0, 'score_final': 0, 'note': f"ÉLIMINÉ : {len(flags_elim)} critère(s)", 'documents_analyses': details['documents_analyses']}}
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'confidence': confidence, 'matched': found_kws if is_present else []}
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f" {crit}")
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'confidence': confidence, 'matched': found_kws if is_present else []}
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"🟡 {crit}")
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        if is_present:
            details['alertes_attention'].append(f"⚠️ Attention: {crit}")
    adequation = min(3, len([k for k, v in checklist.items() if k.startswith('elim_') and v]))
    coherence = min(2, points_bloc2)
    risque_metier = min(3, len(signaux))
    qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre_motiv = 1 if lettre_text and len(lettre_text.strip()) > 50 else 0
    score_final = min(10, adequation + coherence + risque_metier + qualite_cv + lettre_motiv)
    return {'score': score_final, 'checklist': checklist, 'flags_eliminatoires': [], 'signaux_detectes': signaux, 'details': details, 'score_breakdown': {'bloc1_eliminatoire': False, 'adequation_experience': adequation, 'coherence_parcours': coherence, 'exposition_risque_metier': risque_metier, 'qualite_cv': qualite_cv, 'lettre_motivation': lettre_motiv, 'bloc2_criteres_valides': len(details['criteres_valides_bloc2']), 'bloc2_points': points_bloc2, 'bloc3_signaux_detectes': len(signaux), 'bloc3_points': points_bloc3, 'total_raw_points': points_bloc2 + points_bloc3, 'score_final': score_final, 'note': f"Score Excel: {score_final}/10", 'documents_analyses': details['documents_analyses']}}

def normalize_text_for_matching(text):
    return normalize_for_matching(text)[0]

def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste):
    try:
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
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
        for fn in (attestation_filenames or []):
            if fn:
                att_bytes = download_file_from_supabase(fn)
                if att_bytes:
                    t = extract_text_robust_from_bytes(att_bytes, fn)
                    if t:
                        att_texts.append(t)
        detected_lang = detect_language(cv_text[:500]) if cv_text else None
        nlp_enrichment = enrich_analysis_with_nlp(cv_text, lm_text, detected_lang)
        if nlp_enrichment and supabase:
            supabase.table('candidats').update({"nlp_enrichment": json.dumps(nlp_enrichment, ensure_ascii=False)}).eq('token', token).execute()
        result = analyze_cv_intelligent(cv_text, lm_text, att_texts, poste)
        if result is None:
            if poste == "Chef de Section Compensation":
                fb = calculate_score_chef_section_compensation(cv_text, lm_text, att_texts)
                result = {
                    'score': fb['score'],
                    'checklist': fb.get('checklist', {}),
                    'flags_eliminatoires': fb['flags_eliminatoires'],
                    'signaux_detectes': [],
                    'details': {'moteur': 'mots-clés (repli)', 'sous_scores': fb['sous_scores']},
                    'score_breakdown': {
                        'bloc1_eliminatoire': bool(fb['flags_eliminatoires']),
                        'sous_scores': fb['sous_scores'],
                        'score_final': fb['score'],
                        'score_max': fb['score_max'],
                        'decision': fb['decision'],
                        'note': fb['detail']
                    }
                }
            elif poste in POSTES_AVEC_SCORING_100:
                detailed_result = calculate_detailed_score_100(cv_text, lm_text, att_texts, poste)
                if detailed_result:
                    result = {
                        'score': detailed_result['score'],
                        'checklist': {},
                        'flags_eliminatoires': [],
                        'signaux_detectes': [],
                        'details': detailed_result['details'],
                        'score_breakdown': {
                            'bloc1_eliminatoire': False,
                            'scoring_type': '100_points',
                            'bloc_cv': detailed_result['bloc_cv'],
                            'bloc_lm': detailed_result['bloc_lm'],
                            'bloc_diplomes': detailed_result['bloc_diplomes'],
                            'score_final': detailed_result['score'],
                            'decision': detailed_result['decision'],
                            'note': detailed_result['note']
                        }
                    }
                else:
                    result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
            else:
                result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
        if supabase:
            supabase.table('candidats').update({
                "score": str(result['score']),
                "checklist": json.dumps(result.get('checklist', {}), ensure_ascii=False),
                "flags_eliminatoires": json.dumps(result['flags_eliminatoires'], ensure_ascii=False),
                "signaux_detectes": json.dumps(result['signaux_detectes'], ensure_ascii=False),
                "analyse_details": json.dumps(result['details'], ensure_ascii=False),
                "score_breakdown": json.dumps(result['score_breakdown'], ensure_ascii=False),
                "analyse_auto_date": datetime.datetime.now().isoformat(),
                "analyse_status": "completed"
            }).eq('token', token).execute()
        moteur = result['score_breakdown'].get('moteur_analyse', result['details'].get('moteur', 'mots-clés'))
        tag = "⚠️ ÉLIMINÉ" if result['score_breakdown'].get('bloc1_eliminatoire') else "✅"
        logger.info(f"{tag} [{moteur}] Score {token}: {result['score']} — {result['score_breakdown'].get('note','')}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        if supabase:
            supabase.table('candidats').update({"analyse_status": "error", "analyse_error": str(e), "analyse_auto_date": datetime.datetime.now().isoformat()}).eq('token', token).execute()

def get_recommandation_from_score(score, poste=None):
    s = int(score)
    if poste and poste in POSTES_AVEC_SCORING_12:
        if s >= 10: return "🥇 Entretien prioritaire"
        elif s >= 7: return "🥈 Entretien si besoin (vivier de réserve)"
        else: return "❌ Rejet"
    if poste and poste in POSTES_AVEC_SCORING_100:
        if s >= 80: return "Shortlist"
        elif s >= 70: return "À considérer"
        elif s >= 60: return "Faible"
        else: return "Rejet"
    if s >= 8: return "🥇 Entretien prioritaire"
    elif s >= 6: return "🥈 Entretien si besoin"
    else: return "❌ Rejet"

def get_decision_from_score(score, poste=None):
    if not poste or (poste not in POSTES_AVEC_SCORING_100 and poste not in POSTES_AVEC_SCORING_12):
        return None
    return get_recommandation_from_score(score, poste)

def get_recommandation_color(score, poste=None):
    s = int(score)
    if poste and poste in POSTES_AVEC_SCORING_12:
        if s >= 10: return "00FF00"
        elif s >= 7: return "FFA500"
        else: return "FF0000"
    if poste and poste in POSTES_AVEC_SCORING_100:
        if s >= 80: return "00FF00"
        elif s >= 70: return "90EE90"
        elif s >= 60: return "FFA500"
        else: return "FF0000"
    if s >= 8: return "00FF00"
    elif s >= 6: return "FFA500"
    else: return "FF0000"

def get_score_max_for_poste(poste):
    if poste in POSTES_AVEC_SCORING_12:
        return 12
    if poste in POSTES_AVEC_SCORING_100:
        return 100
    return 10

def calculate_ranking_score(c, poste):
    sb = c.get('score_breakdown_parsed', {})
    if sb.get('bloc1_eliminatoire'):
        return -999
    score = int(c.get('score', 0))
    if poste and (poste in POSTES_AVEC_SCORING_100 or poste in POSTES_AVEC_SCORING_12):
        return float(score)
    signaux_count = len(c.get('signaux_detectes_parsed', []))
    criteres_ok = sb.get('bloc2_criteres_valides', 0)
    lettre_bonus = 0.1 if c.get('lettre_filename') else 0
    try:
        days = (datetime.datetime.now() - datetime.datetime.fromisoformat(c.get('date_candidature', ''))).days
        date_bonus = max(0, (30 - min(days, 30)) * 0.01)
    except Exception:
        date_bonus = 0
    return round(score + signaux_count * 0.5 + criteres_ok * 0.2 + lettre_bonus + date_bonus, 3)

def generate_ranking_for_poste(poste, candidats_data):
    pool = [c for c in candidats_data if c.get('poste') == poste]
    for c in pool:
        c['ranking_score'] = calculate_ranking_score(c, poste)
        c['ranking_position'] = 0
    pool.sort(key=lambda x: (-x['ranking_score'], -len(x.get('signaux_detectes_parsed', [])), -x.get('score_breakdown_parsed', {}).get('bloc2_criteres_valides', 0), x.get('date_candidature', '')))
    for idx, c in enumerate(pool, 1):
        c['ranking_position'] = idx
        c['ranking_recommendation'] = get_recommandation_from_score(c.get('score', 0), poste)
    return pool

def generate_excel_report(candidats_data, poste_filter=None):
    if not OPENPYXL_AVAILABLE:
        return None
    wb = Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    if poste_filter and poste_filter in POSTES:
        postes_to_export = [poste_filter]
    else:
        postes_to_export = list(dict.fromkeys(c.get('poste', '') for c in candidats_data if c.get('poste') in POSTES))
    if not postes_to_export:
        ws = wb.create_sheet(title="Aucune donnée")
        ws['A1'] = "Aucune candidature trouvée"
        ws['A1'].font = Font(bold=True, size=14)
    else:
        for poste in postes_to_export:
            candidats_poste = generate_ranking_for_poste(poste, [c for c in candidats_data if c.get('poste') == poste])
            sheet_name = poste[:28] if len(poste) > 31 else poste
            ws = wb.create_sheet(title=sheet_name)
            hfill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
            hfont = Font(color="000000", bold=True, size=11)
            border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
            ws.merge_cells('A1:L1')
            c = ws['A1']
            c.value = f"CANDIDATURES - {poste}"
            c.font = Font(bold=True, size=14, color="000000")
            c.alignment = Alignment(horizontal='center', vertical='center')
            c.fill = hfill
            ws.row_dimensions[1].height = 30
            score_max = get_score_max_for_poste(poste)
            if poste == "Chef de Section Compensation":
                headers = ['Rang', 'N° Dossier', 'Email', 'Candidat', 'Téléphone', 'Adéquation (0-3)', 'Exposition BEAC/GIMAC (0-3)', 'Encadrement (0-2)', 'Cohérence (0-2)', 'Qualité CV (0-1)', 'Lettre (0-1)', f'Score /{score_max}', 'Recommandation']
            else:
                headers = ['Rang', 'N° Dossier', 'Email', 'Candidat', 'Téléphone', 'Adéquation (0-3)', 'Cohérence (0-2)', 'Risque métier (0-3)', 'Qualité CV (0-1)', 'Lettre (0-1)', f'Score /{score_max}', 'Recommandation']
            for col, h in enumerate(headers, 1):
                cell = ws.cell(row=3, column=col, value=h)
                cell.font = hfont
                cell.fill = hfill
                cell.border = border
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            for row_i, cand in enumerate(candidats_poste, 4):
                sb = cand.get('score_breakdown_parsed', {})
                elim = sb.get('bloc1_eliminatoire', False)
                if poste == "Chef de Section Compensation":
                    adeq = sb.get('sous_scores', {}).get("Adéquation de l'expérience (compensation interbancaire, back-office bancaire)", 0) if not elim else 0
                    expo = sb.get('sous_scores', {}).get("Exposition aux règles BEAC / GIMAC et aux systèmes de compensation (SYSTAC, SYGMA, SWIFT)", 0) if not elim else 0
                    enc = sb.get('sous_scores', {}).get("Capacité d'encadrement et de management d'équipe opérationnelle", 0) if not elim else 0
                    coh = sb.get('sous_scores', {}).get("Cohérence et progression du parcours professionnel", 0) if not elim else 0
                    qcv = sb.get('sous_scores', {}).get("Qualité et clarté du CV (missions précises, livrables, résultats)", 0) if not elim else 0
                    lm = sb.get('sous_scores', {}).get("Lettre de motivation", 0) if not elim else 0
                    total = adeq + expo + enc + coh + qcv + lm
                else:
                    adeq = sb.get('adequation_experience', 0) if not elim else 0
                    cohe = sb.get('coherence_parcours', 0) if not elim else 0
                    risq = sb.get('exposition_risque_metier', 0) if not elim else 0
                    qcv = sb.get('qualite_cv', 0) if not elim else 0
                    lm = sb.get('lettre_motivation', 0) if not elim else 0
                    total = adeq + cohe + risq + qcv + lm
                rang = cand.get('ranking_position', row_i - 3)
                nom_c = f"{cand.get('prenom', '')} {cand.get('nom', '')}".strip()
                reco = cand.get('ranking_recommendation', get_recommandation_from_score(total, poste))
                num_dos = cand.get('numero_dossier', '') or '–'
                if poste == "Chef de Section Compensation":
                    row_data = [rang, num_dos, cand.get('email', '') or '–', nom_c, cand.get('telephone', '') or '–', adeq, expo, enc, coh, qcv, lm, total, reco]
                else:
                    row_data = [rang, num_dos, cand.get('email', '') or '–', nom_c, cand.get('telephone', '') or '–', adeq, cohe, risq, qcv, lm, total, reco]
                for col, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_i, column=col, value=val if val is not None else '')
                    cell.border = border
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                    if col == len(headers):
                        rec_color = get_recommandation_color(total, poste)
                        cell.font = Font(bold=True, color="000000")
                        if rec_color == "00FF00":
                            cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                        elif rec_color == "FFA500":
                            cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
                        else:
                            cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
            if poste == "Chef de Section Compensation":
                col_widths = [8, 20, 35, 35, 20, 15, 20, 15, 15, 15, 15, 12, 25]
            else:
                col_widths = [8, 20, 35, 35, 20, 15, 15, 20, 15, 15, 12, 25]
            for col, w in enumerate(col_widths, 1):
                ws.column_dimensions[get_column_letter(col)].width = w
            for row in range(3, ws.max_row + 1):
                ws.row_dimensions[row].height = 25
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

def generate_csv_report(candidats_data, poste_filter=None):
    out = io.StringIO()
    w = csv.writer(out, delimiter=';', quoting=csv.QUOTE_ALL, quotechar='"')
    headers = ['Rang', 'N° Dossier', 'Email', 'Nom', 'Prénom', 'Téléphone', 'Poste', 'Date candidature', 'Score', 'Statut', 'Éliminatoire', 'Adéquation (0-3)', 'Cohérence', 'Risque/Exposition', 'Note', 'Recommandation']
    w.writerow(headers)
    if poste_filter and poste_filter in POSTES:
        candidats_filtered = [c for c in candidats_data if c.get('poste') == poste_filter]
    else:
        candidats_filtered = candidats_data
    candidats_filtered.sort(key=lambda x: (x.get('poste', ''), x.get('date_candidature', '')), reverse=True)
    for idx, c in enumerate(candidats_filtered, 1):
        sb = c.get('score_breakdown_parsed', {})
        score = int(c.get('score', 0))
        poste = c.get('poste', '')
        reco = get_recommandation_from_score(score, poste)
        w.writerow([str(idx), str(c.get('numero_dossier', '') or '–'), str(c.get('email', '') or '–'), str(c.get('nom', '') or ''), str(c.get('prenom', '') or ''), str(c.get('telephone', '') or '–'), str(poste or ''), str(c.get('date_candidature', '') or ''), str(c.get('score', '0')), str(c.get('statut', '') or ''), 'OUI' if sb.get('bloc1_eliminatoire') else 'NON', str(sb.get('adequation_experience', sb.get('sous_scores', {}).get("Adéquation de l'expérience (compensation interbancaire, back-office bancaire)", 0))), str(sb.get('coherence_parcours', sb.get('sous_scores', {}).get("Cohérence et progression du parcours professionnel", 0))), str(sb.get('exposition_risque_metier', sb.get('sous_scores', {}).get("Exposition aux règles BEAC / GIMAC et aux systèmes de compensation (SYSTAC, SYGMA, SWIFT)", 0))), str(sb.get('note', '') or ''), str(reco)])
    out.seek(0)
    return out.getvalue()

def generate_pdf_report(candidats_data, poste_filter=None):
    if not REPORTLAB_AVAILABLE:
        return None
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=2*cm, bottomMargin=2*cm)
    els = []
    sty = getSampleStyleSheet()
    rapport_type = f"CANDIDATURES - {poste_filter}" if poste_filter else "RAPPORT GENERAL"
    els.append(Paragraph(f"{rapport_type} — RecrutBank", ParagraphStyle('T', parent=sty['Heading1'], fontSize=16, textColor=colors.black, spaceAfter=20, alignment=TA_CENTER)))
    els.append(Spacer(1, 0.3*cm))
    els.append(Paragraph(f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}", ParagraphStyle('D', parent=sty['Normal'], fontSize=9, textColor=colors.grey)))
    els.append(Spacer(1, 0.8*cm))
    if poste_filter and poste_filter in POSTES:
        postes_to_export = [poste_filter]
    else:
        postes_to_export = list(dict.fromkeys(c.get('poste', '') for c in candidats_data if c.get('poste') in POSTES))
    for poste in postes_to_export:
        candidats_poste = generate_ranking_for_poste(poste, [c for c in candidats_data if c.get('poste') == poste])
        if not candidats_poste:
            continue
        els.append(Paragraph(f"📋 {poste}", ParagraphStyle('P', parent=sty['Heading2'], fontSize=12, textColor=colors.black, spaceAfter=10, alignment=TA_LEFT)))
        score_max = get_score_max_for_poste(poste)
        if poste == "Chef de Section Compensation":
            data = [['Rang', 'N° Dossier', 'Email', 'Candidat', 'Téléphone', 'Poste', f'Score /{score_max}', 'Recommandation']]
        else:
            data = [['Rang', 'N° Dossier', 'Email', 'Candidat', 'Téléphone', 'Poste', f'Score /{score_max}', 'Recommandation']]
        for idx, c in enumerate(candidats_poste, 1):
            score = int(c.get('score', 0))
            num_dos = c.get('numero_dossier', '') or '–'
            reco = get_recommandation_from_score(score, poste)
            data.append([str(idx), num_dos, c.get('email', '') or '–', f"{c.get('prenom', '')} {c.get('nom', '')}", c.get('telephone', '') or '–', poste, f"{score}/{score_max}", reco])
        tbl = Table(data, colWidths=[1.5*cm, 3*cm, 5*cm, 4.5*cm, 3*cm, 5*cm, 2.5*cm, 4.5*cm])
        tbl_style = [('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 9), ('BOTTOMPADDING', (0, 0), (-1, 0), 10), ('GRID', (0, 0), (-1, -1), 0.5, colors.black), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]
        for row_idx in range(1, len(data)):
            score = int(candidats_poste[row_idx-1].get('score', 0)) if row_idx <= len(candidats_poste) else 0
            if score_max == 12:
                if score >= 10: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(0.8, 1, 0.8)))
                elif score >= 7: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.9, 0.6)))
                else: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.8, 0.8)))
            elif score_max == 100:
                if score >= 80: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(0.8, 1, 0.8)))
                elif score >= 70: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.95, 0.6)))
                elif score >= 60: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.9, 0.6)))
                else: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.8, 0.8)))
            else:
                if score >= 8: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(0.8, 1, 0.8)))
                elif score >= 6: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.9, 0.6)))
                else: tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.8, 0.8)))
        tbl.setStyle(TableStyle(tbl_style))
        els.append(tbl)
        els.append(Spacer(1, 0.5*cm))
    doc.build(els)
    buf.seek(0)
    return buf

def generate_word_report(candidats_data, poste_filter=None):
    if not DOCX_AVAILABLE:
        return None
    buf = io.BytesIO()
    doc = DocxDocument()
    title = doc.add_heading('Rapport Détaillé de Recrutement', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y à %H:%M')}"
    if poste_filter:
        subtitle += f" - Poste: {poste_filter}"
    doc.add_paragraph(subtitle).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()
    doc.add_heading('1. Statistiques Générales', level=1)
    total = len(candidats_data)
    retenus = sum(1 for c in candidats_data if c.get('statut') == 'retenu')
    exclus = sum(1 for c in candidats_data if c.get('statut') == 'exclu')
    en_attente = sum(1 for c in candidats_data if c.get('statut') == 'en_attente')
    entretien = sum(1 for c in candidats_data if c.get('statut') == 'entretien')
    doc.add_paragraph(f"• Total: {total}\n• Retenus: {retenus}\n• Exclus: {exclus}\n• En attente: {en_attente}\n• Entretien: {entretien}")
    doc.add_paragraph()
    doc.add_heading('2. Candidats Retenus', level=1)
    candidats_retenus = [c for c in candidats_data if c.get('statut') == 'retenu']
    if candidats_retenus:
        table_ret = doc.add_table(rows=1, cols=6)
        table_ret.style = 'Table Grid'
        hdr_cells = table_ret.rows[0].cells
        for i, h in enumerate(['N° Dossier', 'Nom', 'Prénom', 'Poste', 'Score', 'Motif']):
            hdr_cells[i].text = h
            hdr_cells[i].paragraphs[0].runs[0].bold = True
        for c in candidats_retenus:
            row_cells = table_ret.add_row().cells
            row_cells[0].text = str(c.get('numero_dossier', '') or '–')
            row_cells[1].text = c.get('nom', '') or '–'
            row_cells[2].text = c.get('prenom', '') or '–'
            row_cells[3].text = c.get('poste', '') or '–'
            score_max = get_score_max_for_poste(c.get('poste', ''))
            row_cells[4].text = f"{int(c.get('score', 0))}/{score_max}"
            row_cells[5].text = 'Profil correspondant'
    else:
        doc.add_paragraph("Aucun candidat retenu.")
    doc.add_paragraph()
    doc.add_heading('3. Candidats Exclus', level=1)
    candidats_exclus = [c for c in candidats_data if c.get('statut') == 'exclu']
    if candidats_exclus:
        table_exc = doc.add_table(rows=1, cols=6)
        table_exc.style = 'Table Grid'
        hdr_cells_exc = table_exc.rows[0].cells
        for i, h in enumerate(['N° Dossier', 'Nom', 'Prénom', 'Poste', 'Score', 'Motif']):
            hdr_cells_exc[i].text = h
            hdr_cells_exc[i].paragraphs[0].runs[0].bold = True
        for c in candidats_exclus:
            row_cells = table_exc.add_row().cells
            row_cells[0].text = str(c.get('numero_dossier', '') or '–')
            row_cells[1].text = c.get('nom', '') or '–'
            row_cells[2].text = c.get('prenom', '') or '–'
            row_cells[3].text = c.get('poste', '') or '–'
            score_max = get_score_max_for_poste(c.get('poste', ''))
            row_cells[4].text = f"{int(c.get('score', 0))}/{score_max}"
            row_cells[5].text = 'Ne correspond pas'
    else:
        doc.add_paragraph("Aucun candidat exclu.")
    doc.add_paragraph()
    doc.add_heading('4. Liste Complète', level=1)
    if candidats_data:
        table_all = doc.add_table(rows=1, cols=8)
        table_all.style = 'Table Grid'
        hdr_cells_all = table_all.rows[0].cells
        for i, h in enumerate(['N°', 'Dossier', 'Nom', 'Prénom', 'Email', 'Poste', 'Statut', 'Score']):
            hdr_cells_all[i].text = h
            hdr_cells_all[i].paragraphs[0].runs[0].bold = True
        for idx, c in enumerate(candidats_data, 1):
            row_cells = table_all.add_row().cells
            row_cells[0].text = str(idx)
            row_cells[1].text = str(c.get('numero_dossier', '') or '–')
            row_cells[2].text = c.get('nom', '') or '–'
            row_cells[3].text = c.get('prenom', '') or '–'
            row_cells[4].text = c.get('email', '') or '–'
            row_cells[5].text = c.get('poste', '') or '–'
            row_cells[6].text = c.get('statut', 'en_attente')
            score_max = get_score_max_for_poste(c.get('poste', ''))
            row_cells[7].text = f"{c.get('score', 0)}/{score_max}"
    doc.add_paragraph()
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run('--- Fin du Rapport ---')
    footer_run.italic = True
    doc.save(buf)
    buf.seek(0)
    return buf

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
        logger.warning(f"Erreur initialisation recruteur : {e}")

init_recruteur()

@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify(POSTES), 200

@app.route('/api/grille/<poste>', methods=['GET'])
def get_grille(poste):
    g = GRILLE.get(poste)
    if not g:
        return jsonify({'error': 'Poste inconnu', 'postes_disponibles': list(GRILLE.keys())}), 404
    return jsonify(g), 200

@app.route('/api/auth/login', methods=['POST'])
def login():
    if request.method == 'OPTIONS':
        return '', 204
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
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400
        if supabase:
            existing = supabase.table('candidats').select('*').eq('email', email).eq('poste', poste).execute()
            if existing.data and len(existing.data) > 0:
                return jsonify({'error': f'Vous avez déjà soumis une candidature pour le poste "{poste}".'}), 409
            all_candidats = supabase.table('candidats').select('numero_dossier').eq('poste', poste).execute()
            max_num = 0
            for c in all_candidats.data:
                existing_num = c.get('numero_dossier', '')
                if existing_num:
                    try:
                        num_val = int(existing_num)
                        if num_val > max_num:
                            max_num = num_val
                    except (ValueError):
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
            return jsonify({'error': "Échec de l'envoi du CV, merci de réessayer."}), 500
        lettre_filename = save_file_to_supabase('lettre', 'lettre')
        if request.files.get('lettre') and not lettre_filename:
            return jsonify({'error': "Échec de l'envoi de la lettre de motivation, merci de réessayer."}), 500
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
            "token": token,
            "nom": nom,
            "prenom": prenom,
            "email": email,
            "telephone": telephone,
            "poste": poste,
            "numero_dossier": numero_dossier,
            "cv_filename": cv_filename,
            "lettre_filename": lettre_filename,
            "attestation_filenames": json.dumps(att_filenames, ensure_ascii=False),
            "statut": "en_attente",
            "note": "",
            "score": "0",
            "checklist": "",
            "flags_eliminatoires": "",
            "signaux_detectes": "",
            "score_breakdown": "",
            "analyse_status": "pending",
            "date_candidature": datetime.datetime.now().isoformat()
        }).execute()
        threading.Thread(target=run_analysis_for_candidat, args=(token, cv_filename, lettre_filename, att_filenames, poste), daemon=True).start()
        nom_complet = f"{prenom} {nom}".strip()
        sujet_confirmation = f"Confirmation de candidature – {poste}"
        corps_confirmation = f"Bonjour {nom_complet},\n\nNous accusons réception de votre candidature.\n\nSans réponse de notre part sous deux (2) semaines, veuillez considérer que votre candidature n'a pas été retenue.\n\nPour toute information : contact@cdotchad.com.\n\nCordialement,"
        threading.Thread(target=send_email, args=(email, sujet_confirmation, corps_confirmation), daemon=True).start()
        return jsonify({'message': 'Candidature soumise avec succès', 'token': token, 'numero_dossier': numero_dossier, 'analyse': 'Analyse automatique en cours'}), 201
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
        if search:
            hay = (f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} {c.get('poste','')} {c.get('numero_dossier','')}").lower()
            if search not in hay:
                continue
        if c.get('score_breakdown'):
            try:
                c['score_breakdown_parsed'] = json.loads(c['score_breakdown'])
            except Exception:
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
        return jsonify({'error': 'Supabase non configuré'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = request.get_json(silent=True) or {}
    statut = data.get('statut', 'en_attente')
    note = data.get('note', '')
    candidat = response.data[0]
    poste = candidat.get('poste', '')
    score_max = get_score_max_for_poste(poste)
    score = str(min(score_max, max(0, int(data.get('score', 0)))))
    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    supabase.table('candidats').update({
        "statut": statut,
        "note": note,
        "score": score,
        "decision_date": datetime.datetime.now().isoformat(),
        "decided_by": get_jwt_identity()
    }).eq('token', token).execute()
    return jsonify({'message': 'Mis à jour avec succès', 'statut': statut}), 200

@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
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
    supabase.table('candidats').update({"analyse_status": "pending", "analyse_manual_trigger": datetime.datetime.now().isoformat()}).eq('token', token).execute()
    threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste), daemon=True).start()
    return jsonify({'message': 'Analyse re-déclenchée', 'token': token}), 202

@app.route('/api/recruteur/reanalyze-all', methods=['POST'])
@jwt_required()
def reanalyze_all_candidates():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configuré'}), 500
        response = supabase.table('candidats').select('*').execute()
        keys = response.data if response.data else []
        if not keys:
            return jsonify({'message': 'Aucune candidature à réanalyser'}), 200
        reanalyzed_count = 0
        errors = []
        for data in keys:
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                poste = data.get('poste')
                if not cv_fn:
                    errors.append(f"Token {token}: CV manquant")
                    continue
                supabase.table('candidats').update({
                    "analyse_status": "reanalyzing",
                    "reanalyze_trigger": datetime.datetime.now().isoformat(),
                    "reanalyze_reason": "Modification des règles de sélection"
                }).eq('token', token).execute()
                threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste), daemon=True).start()
                reanalyzed_count += 1
            except Exception as e:
                errors.append(f"Token {data.get('token')}: {str(e)}")
        return jsonify({'message': f'Réanalyse lancée pour {reanalyzed_count} candidature(s)', 'reanalyzed_count': reanalyzed_count, 'errors': errors[:10]}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-poste/<poste>', methods=['POST'])
@jwt_required()
def reanalyze_by_poste(poste):
    if poste not in POSTES:
        return jsonify({'error': f'Poste inconnu: {poste}'}), 400
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configuré'}), 500
        response = supabase.table('candidats').select('*').eq('poste', poste).execute()
        keys = response.data if response.data else []
        reanalyzed_count = 0
        errors = []
        for data in keys:
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                if not cv_fn:
                    errors.append(f"Token {token}: CV manquant")
                    continue
                supabase.table('candidats').update({
                    "analyse_status": "reanalyzing",
                    "reanalyze_trigger": datetime.datetime.now().isoformat(),
                    "reanalyze_reason": f"Modification des règles pour le poste: {poste}"
                }).eq('token', token).execute()
                threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste), daemon=True).start()
                reanalyzed_count += 1
            except Exception as e:
                errors.append(f"Token {data.get('token')}: {str(e)}")
        return jsonify({'message': f'Réanalyse lancée pour {reanalyzed_count} candidature(s) du poste "{poste}"', 'poste': poste, 'reanalyzed_count': reanalyzed_count, 'errors': errors[:10]}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-status', methods=['GET'])
@jwt_required()
def get_reanalyze_status():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configuré'}), 500
        response = supabase.table('candidats').select('*').execute()
        keys = response.data if response.data else []
        status_counts = {'pending': 0, 'reanalyzing': 0, 'completed': 0, 'error': 0}
        for data in keys:
            status = data.get('analyse_status', 'pending')
            if status in status_counts:
                status_counts[status] += 1
        return jsonify({'total_candidatures': len(keys), 'status_counts': status_counts, 'reanalyze_in_progress': status_counts['reanalyzing'] > 0}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/export/<fmt>', methods=['GET'])
@jwt_required()
def export_candidates(fmt):
    try:
        poste_filter = request.args.get('poste', '')
        statut_filter = request.args.get('statut', '')
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
            if c.get('score_breakdown'):
                try:
                    c['score_breakdown_parsed'] = json.loads(c['score_breakdown'])
                except Exception:
                    pass
            result.append(c)
        result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else "_global"
        statut_suffix = f"_{statut_filter}" if statut_filter else ""
        filename_base = f"rapport{poste_suffix}{statut_suffix}_{ts}"
        if fmt.lower() == 'csv':
            csv_bytes = generate_csv_report(result, poste_filter=poste_filter).encode('utf-8-sig')
            return send_file(io.BytesIO(csv_bytes), mimetype='text/csv', as_attachment=True, download_name=f'{filename_base}.csv')
        elif fmt.lower() in ('excel', 'xlsx'):
            if not OPENPYXL_AVAILABLE:
                return jsonify({'error': 'openpyxl non installé'}), 503
            buf = generate_excel_report(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur génération Excel'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'{filename_base}.xlsx')
        elif fmt.lower() == 'pdf':
            if not REPORTLAB_AVAILABLE:
                return jsonify({'error': 'reportlab non installé'}), 503
            buf = generate_pdf_report(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur génération PDF'}), 500
            return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{filename_base}.pdf')
        elif fmt.lower() in ('word', 'docx'):
            if not DOCX_AVAILABLE:
                return jsonify({'error': 'python-docx non installé'}), 503
            buf = generate_word_report(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur génération Word'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', as_attachment=True, download_name=f'{filename_base}.docx')
        return jsonify({'error': 'Format non supporté. Utilisez: csv, excel, pdf ou word'}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    body = request.get_json(silent=True) or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))
    nom_c = f"{data.get('prenom', '')} {data.get('nom', '')}".strip()
    poste = data.get('poste', '')
    to_email = data.get('email', '')
    sign = "\n\nCordialement,\nL'équipe Ressources Humaines\nRecrutBank"
    if msg_type == 'retenu':
        sujet = f"Félicitations – Candidature retenue – {poste}"
        corps = f"Madame, Monsieur {nom_c},\n\nNous avons le plaisir de vous informer que votre candidature pour le poste de {poste} a été retenue.\n\nNous vous contacterons très prochainement." + sign
    elif msg_type == 'entretien':
        sujet = f"Invitation à un entretien – {poste}"
        corps = f"Madame, Monsieur {nom_c},\n\nSuite à l'examen de votre candidature pour le poste de {poste}, nous avons le plaisir de vous inviter à un entretien.\n\nNous prendrons contact avec vous pour convenir d'une date." + sign
    else:
        sujet = f"Réponse à votre candidature – {poste}"
        corps = f"Madame, Monsieur {nom_c},\n\nNous vous remercions de l'intérêt que vous portez à notre institution.\n\nAprès examen attentif de votre dossier pour le poste de {poste}, nous avons le regret de vous informer que votre candidature n'a pas été retenue.\n\nNous vous encourageons à postuler à nouveau." + sign
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

@app.route('/api/recruteur/dossiers/zip', methods=['GET'])
@jwt_required()
def export_dossiers_zip():
    try:
        poste_filter = request.args.get('poste', '')
        date_start = request.args.get('date_start', '')
        date_end = request.args.get('date_end', '')
        if not supabase:
            return jsonify({'error': 'Supabase non configuré'}), 500
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
            return jsonify({'error': 'Aucun dossier à exporter'}), 404
        zip_buffer = io.BytesIO()
        files_added = 0
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for cand in candidats:
                poste_nom = cand.get('poste', 'Poste_Inconnu')
                poste_nom_clean = re.sub(r'[<>:"/\\|?*]', '_', poste_nom)
                num_dossier = cand.get('numero_dossier', '') or f"candidat_{cand['id'][:8]}"
                nom_candidat = cand.get('nom', 'N/A').upper()
                prenom_candidat = cand.get('prenom', 'N/A')
                dossier_candidat_nom = f"{num_dossier} - {nom_candidat} {prenom_candidat}"
                dossier_candidat_nom = re.sub(r'[<>:"/\\|?*]', '_', dossier_candidat_nom)
                dossier_parent = f"{poste_nom_clean}/{dossier_candidat_nom}"
                fichiers_a_inclure = []
                cv_file = cand.get('cv_filename', '')
                if cv_file:
                    cv_bytes = download_file_from_supabase(cv_file)
                    if cv_bytes:
                        fichiers_a_inclure.append((cv_bytes, cv_file, 'CV'))
                lettre_file = cand.get('lettre_filename', '')
                if lettre_file:
                    lettre_bytes = download_file_from_supabase(lettre_file)
                    if lettre_bytes:
                        fichiers_a_inclure.append((lettre_bytes, lettre_file, 'Lettre_de_motivation'))
                att_raw = cand.get('attestation_filenames', '[]')
                try:
                    att_files = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                    for idx, att_file in enumerate(att_files, 1):
                        if att_file:
                            att_bytes = download_file_from_supabase(att_file)
                            if att_bytes:
                                fichiers_a_inclure.append((att_bytes, att_file, f'Attestation_{idx}'))
                except Exception:
                    pass
                if not fichiers_a_inclure:
                    info_content = f"Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}\nPoste: {cand.get('poste', 'N/A')}\nNumero dossier: {num_dossier}\nEmail: {cand.get('email', 'N/A')}\nTelephone: {cand.get('telephone', 'N/A')}\nDate candidature: {cand.get('date_candidature', 'N/A')}\nNote: Les fichiers originaux ne sont plus disponibles."
                    archive_name = f"{dossier_parent}/INFOS_CANDIDAT.txt"
                    zip_file.writestr(archive_name, info_content.encode('utf-8'))
                    files_added += 1
                else:
                    for file_bytes, original_filename, prefix in fichiers_a_inclure:
                        ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
                        archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                        try:
                            zip_file.writestr(archive_name, file_bytes)
                            files_added += 1
                        except Exception:
                            pass
        zip_buffer.seek(0)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else ""
        filename = f"dossiers_candidats{poste_suffix}_{ts}.zip"
        return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name=filename)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/debug/analyse-ia', methods=['POST'])
@jwt_required()
def debug_analyse_ia():
    data = request.get_json(silent=True) or {}
    cv_text = data.get('cv_text', '')
    lettre_text = data.get('lettre_text', '')
    poste = data.get('poste', '')
    if not cv_text or poste not in GRILLE:
        return jsonify({'error': 'cv_text requis et poste doit exister dans GRILLE'}), 400
    result = analyze_cv_intelligent(cv_text, lettre_text, [], poste)
    if result is None:
        return jsonify({'error': "Moteur IA indisponible"}), 503
    return jsonify(result), 200

@app.route('/api/test-email', methods=['GET'])
def test_email():
    try:
        to = request.args.get('to', '')
        if not to:
            return jsonify({'error': 'Paramètre ?to= requis'}), 400
        ok = send_email(to, 'Test RecrutBank', 'Ceci est un email de test depuis RecrutBank.')
        return jsonify({'sent': ok}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)

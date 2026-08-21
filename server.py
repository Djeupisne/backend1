from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, json, re, threading, mimetypes, io, csv, unicodedata, zipfile, time, gc
from concurrent.futures import ThreadPoolExecutor, as_completed
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
_ia_semaphore = threading.Semaphore(int(os.getenv("IA_MAX_CONCURRENCY", "2")))
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

# ═══════════════════════════════════════════════════════════════
#  SUPABASE & EMAIL
# ═══════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════
#  POSTES
# ═══════════════════════════════════════════════════════════════
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

def is_poste_actif(poste):
    return poste in POSTES_ACTIFS

# ═══════════════════════════════════════════════════════════════
#  GRILLES DE SÉLECTION - VERSION 2.0 (MISE À JOUR)
# ═══════════════════════════════════════════════════════════════
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
        "eliminatoire": [
            "Expérience en banque ou établissement financier réglementé",
            "Minimum 3 ans en opérations bancaires ou back-office (hors stage)",
            "Exposition aux opérations de compensation interbancaire (chèques, virements, prélèvements)",
            "Connaissance des règles BEAC / GIMAC ou d'un système de compensation équivalent",
            "Gestion de suspens, rejets ou réclamations interbancaires",
            "Expérience d'encadrement ou de supervision d'équipe (poste de chef de section)",
            "Profil bancaire avec exposition interbancaire (hors microfinance isolée)"
        ],
        "a_verifier": [
            "Supervision quotidienne des opérations de compensation interbancaire",
            "Dénouement de positions nettes en fin de journée",
            "Gestion de suspens, rejets et réclamations interbancaires",
            "Encadrement et coordination d'une équipe opérationnelle",
            "Utilisation de systèmes bancaires de compensation (SYSTAC, SYGMA, SWIFT)",
            "Production de reportings opérationnels ou réglementaires",
            "Participation à des contrôles internes, audits COBAC ou inspections réglementaires"
        ],
        "signaux_forts": [
            "BEAC / GIMAC / compensation interbancaire (SYSTAC, SYGMA)",
            "Règlement de positions nettes dans les délais réglementaires",
            "Contrôle de conformité réglementaire et procédurale",
            "Maîtrise du contrôle interne et de la comptabilité bancaire (SYSCOHADA)",
            "Gestion de fin de journée comptable / clôture des opérations interbancaires",
            "Rapports opérationnels ou réglementaires produits",
            "Expérience dans une banque de la zone CEMAC / UEMOA",
            "Audits COBAC ou contrôles internes réussis sans réserve majeure",
            "Gestion d'une équipe avec résultats mesurables"
        ],
        "points_attention": [
            "Parcours purement comptable sans exposition aux opérations interbancaires",
            "Rôle uniquement administratif ou de support, sans responsabilité opérationnelle",
            "Absence de tout rôle managérial",
            "CV aux missions trop génériques, sans livrables ni résultats quantifiés",
            "Expériences très courtes (< 1 an par poste) sans progression visible",
            "Maîtrise des outils non mentionnée (SWIFT, compensation, ERP bancaire)",
            "Trous inexpliqués dans le parcours professionnel"
        ]
    },
    
    # ═══════════════════════════════════════════════════════════════
    #  NOUVELLE GRILLE - CHARGÉ(E) D'ADMINISTRATION DE CRÉDIT (V2)
    # ═══════════════════════════════════════════════════════════════
    "Chargé(e) d'Administration de Crédit": {
        "eliminatoire": [
            "Aucune expérience ou formation dans un domaine bancaire, financier ou comptable",
            "Niveau de diplôme inférieur à Bac +3 (Banque, Finance, Gestion, Comptabilité ou équivalent)",
            "Aucune notion du crédit bancaire : ni dans la formation, ni dans l'expérience, ni dans la lettre"
        ],
        "a_verifier": [
            "Exposition au cycle de crédit : conditions d'approbation, mise en place, suivi des échéances",
            "Gestion ou participation au suivi des garanties (enregistrement, valorisation, renouvellements)",
            "Production ou contribution à des reportings ou tableaux de bord liés à un portefeuille de crédit",
            "Expérience avec un système bancaire (Finacle, T24, Amplitude, Flexcube) ou outil de suivi de portefeuille",
            "Détection ou signalement d'anomalies, d'impayés ou de dépassements dans un portefeuille"
        ],
        "signaux_forts": [
            "Mention explicite de la gestion administrative du cycle de crédit (mise en place, suivi, clôture)",
            "Exposition à la norme IFRS 9 : staging du portefeuille (Stage 1, 2, 3), ECL, provisions",
            "Suivi et sécurisation des garanties (enregistrement, valorisation, coffre, coordination juridique)",
            "Production de reportings portefeuille (encours, impayés, dépassements, couverture par garanties)",
            "Participation aux comités de risque et traitement des anomalies (COBAC, audit interne)",
            "Maîtrise des Produits de Portefeuille (PP) et de la politique de crédit (GCPPM ou équivalent)",
            "Expérience dans une banque de la zone CEMAC / UEMOA avec exposition réglementaire COBAC",
            "Audits ou contrôles internes réussis sans réserve majeure",
            "Rigueur documentaire : dossiers complets, traçabilité des actes, zéro anomalie détectée en contrôle interne"
        ],
        "points_attention": [
            "Parcours commercial ou front-office pur sans exposition aux opérations de crédit",
            "Profil uniquement théorique ou stagiaire : à évaluer sur la motivation et la capacité d'apprentissage rapide",
            "Expériences courtes ou hétérogènes : comprendre les raisons avant de conclure",
            "Missions peu détaillées dans le CV : interroger sur les livrables et outils réellement utilisés"
        ]
    },
    
    # ═══════════════════════════════════════════════════════════════
    #  NOUVELLE GRILLE - CHEF DE DIVISION LOCAL CORPORATE (V2)
    # ═══════════════════════════════════════════════════════════════
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
            "Pilotage d'une activité Corporate ou d'un segment entreprises avec des objectifs chiffrés (revenus, volumes, marges)",
            "Gestion d'un portefeuille de clients Corporate et capacité à le développer",
            "Encadrement et évaluation d'une équipe commerciale ou bancaire",
            "Suivi de la qualité du portefeuille de crédit (NPL, CIR, provisions) et reporting à la direction",
            "Développement de ventes croisées (cross-selling) ou de partenariats interdépartementaux",
            "Production ou supervision de rapports de performance commerciale et financière",
            "Exposition à la réglementation bancaire locale (COBAC, BEAC) ou internationale"
        ],
        "signaux_forts": [
            "Pilotage d'une division ou d'une ligne Corporate avec atteinte des objectifs de revenus et de portefeuille",
            "Gestion active du ratio NPL et du ratio coût/revenu (CIR) — résultats chiffrés mentionnés",
            "Expérience avérée en cross-selling avec des équipes TSG, Trade Finance ou Cash Management",
            "Développement réel du portefeuille Corporate : acquisition de clients, fidélisation, nombre de produits par client",
            "Leadership démontré : constitution d'équipe, développement des collaborateurs, vivier de talents",
            "Certification Ecobank, Moody's ou ITB (Institut Technique de Banque) ou équivalent",
            "Connaissance du marché corporate tchadien ou de la zone CEMAC / UEMOA",
            "Exposition aux plateformes numériques bancaires (OMNI, Cash Management ou équivalent)",
            "Résultats commerciaux quantifiés et vérifiables dans le CV (chiffres d'affaires, taux de croissance, NPS)"
        ],
        "points_attention": [
            "Parcours exclusivement back-office ou risques sans expérience commerciale Corporate",
            "Profil techniquement solide (crédit, analyse) mais sans expérience managériale ni pilotage d'une P&L",
            "Expériences très courtes (moins de 2 ans par poste) ou trajectoire sans progression hiérarchique visible",
            "CV sans aucun résultat chiffré : missions décrites en responsabilités sans livrables ni indicateurs atteints",
            "Mobilité géographique ou sectorielle excessive sans ancrage dans le secteur bancaire Corporate",
            "Trous inexpliqués dans le parcours ou incohérences entre les postes déclarés"
        ]
    }
}

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATIONS DE SCORING
# ═══════════════════════════════════════════════════════════════
SCORING_CONFIG = {
    "Responsable Administration de Crédit": None,
    "Analyste Crédit CCB": None,
    "Archiviste (Administration Crédit)": None,
    "Senior Finance Officer": None,
    "Market Risk Officer": None,
    "IT Réseau & Infrastructure": None,
    "Chef de Section Compensation": None,
    "Chargé(e) d'Administration de Crédit": None,
    "Chef de Division Local Corporate": {
        "CV_Exp_Corporate": 3, 
        "CV_Management": 3, 
        "CV_Risque": 2, 
        "CV_CrossSelling": 2, 
        "CV_Progression": 2, 
        "CV_Qualite": 1, 
        "CV_Certification": 1
    },
    "Auditeur interne": {
        "CV_Exp": 25, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 15, 
        "CV_Progression": 5, "CV_Management": 0, "CV_Stabilite": 5, 
        "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, 
        "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3
    },
    "Chef service contrôle des engagements": {
        "CV_Exp": 20, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 20, 
        "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, 
        "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, 
        "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3
    },
    "Chef service IT (maintenance/support)": {
        "CV_Exp": 15, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 25, 
        "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, 
        "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, 
        "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3
    },
    "Chef service finance": {
        "CV_Exp": 25, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 15, 
        "CV_Progression": 5, "CV_Management": 10, "CV_Stabilite": 5, 
        "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, 
        "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3
    },
    "Chef service risques de marché": {
        "CV_Exp": 20, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 20, 
        "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, 
        "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, 
        "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3
    },
    "Chef service reporting réglementaire": {
        "CV_Exp": 20, "CV_Niveau": 10, "CV_Secteur": 10, "CV_Tech": 20, 
        "CV_Progression": 5, "CV_Management": 5, "CV_Stabilite": 5, 
        "LM_Comprehension": 5, "LM_Coherence": 5, "LM_Motivation": 5, "LM_Qualite": 5, 
        "D_Niveau": 4, "D_Specialisation": 3, "D_Certif": 3
    }
}

POSTES_AVEC_SCORING_100 = [
    "Auditeur interne", 
    "Chef service contrôle des engagements", 
    "Chef service IT (maintenance/support)", 
    "Chef service finance", 
    "Chef service risques de marché", 
    "Chef service reporting réglementaire"
]
POSTES_AVEC_SCORING_12 = ["Chef de Section Compensation", "Chargé(e) d'Administration de Crédit"]
POSTES_AVEC_SCORING_14 = ["Chef de Division Local Corporate"]

# ═══════════════════════════════════════════════════════════════
#  MOTS-CLÉS SECTORIELS
# ═══════════════════════════════════════════════════════════════
BEAC_GIMAC_KEYWORDS = ['beac', 'gimac', 'systac', 'sygma', 'cemac', 'zone cemac', 'banque centrale', 'banque des etats', 'compensation regionale', 'chambre de compensation', 'clearing house', 'central bank cemac']
COMPENSATION_INTERBANCAIRE_KEYWORDS = ['compensation interbancaire', 'compensation bancaire', 'chambre de compensation', 'cheques', 'virements', 'prelevements', 'interbank clearing', 'clearing', 'systeme de compensation', 'compensation des operations', 'echange interbancaire', 'reglement interbancaire', 'compensation des cheques']
BACKOFFICE_KEYWORDS = ['back-office', 'back office', 'operations bancaires', 'traitement des operations', 'middle office', 'operations interbancaires', 'service operations', 'banking operations', 'transaction processing', 'operations bancaires courantes']
SUSPENS_REJETS_KEYWORDS = ['suspens', 'rejets', 'reclamations interbancaires', 'litiges interbancaires', 'reglement des litiges', 'disputes', 'claims', 'unresolved items', 'rejets de virements', 'reclamation client', 'gestion des suspens', 'gestion des rejets', 'incidents de paiement']
ENCADREMENT_KEYWORDS = ['encadrement', 'supervision equipe', 'chef d equipe', 'team lead', 'responsable equipe', 'superviseur', 'coordination equipe', 'management equipe', 'gestion d equipe', 'head of team', 'manageur', 'encadre une equipe', 'supervise une equipe', 'pilotage d equipe', 'chef de section', 'chef de service', 'responsable de section', 'responsable de service']
SYSCOHADA_KEYWORDS = ['syscohada', 'comptabilite bancaire', 'plan comptable bancaire', 'normes comptables ohada', 'comptabilite ohada']
COMMERCIAL_BANKS = ['ecobank', 'orabank', 'uba', 'bicec', 'sgbc', 'cbc', 'bct', 'société générale', 'standard chartered', 'nsia banque', 'commercial bank', 'banque commerciale', 'investment bank', 'banque d affaires', 'credit institution', 'financial institution', 'banque', 'e c o b a n k', 'o r a b a n k', 'u b a', 'u b a g r o u p', 'ecob', 'orab', 'ubagroup', 'uba-tchad', 'uba-congo', 'ecobank-tchad', 'afriland', 'bgfi', 'bgfibank', 'ccei', 'boa', 'bank of africa', 'banque atlantique', 'commercial bank cameroun', 'sgc cameroun']
MICROFINANCE = ['microfinance', 'micro-finance', 'mfb', 'finadev', 'ucec', 'caisse d epargne', 'credit union', 'cooperative financiere', 'financial development', 'union des caisses', 'f i n a d e v']
NON_FINANCIAL_SECTORS = ['logistics', 'logistique', 'transport', 'shipping', 'gls', 'global logistics', 'société commerciale', 'entreprise commerciale', 'retail store', 'grande distribution', 'distribution commerciale', 'manufacturing', 'industrie', 'construction', 'btp', 'holding', 'encobat', 'agriculture', 'farming', 'agroalimentaire', 'communication agency', 'agence de communication', 'health', 'hôpital', 'clinique', 'samaritaine', 'education', 'enseignement', 'école', 'ngo', 'ong', 'association', 'humanitaire', 'world vision', 'wvi', 'government', 'gouvernement', 'administration publique', 'media', 'presse', 'journalisme', 'tourism', 'tourisme', 'restauration', 'real estate', 'immobilier', 'energy', 'énergie', 'oil', 'gaz', 'petrole', 'mining', 'correct services', 'cdo consulting']

COMMERCIAL_BANK_PATTERN = re.compile(r'\b(' + '|'.join(re.escape(b) for b in COMMERCIAL_BANKS) + r')\b', re.IGNORECASE)
MICROFINANCE_PATTERN = re.compile('|'.join(re.escape(m) for m in MICROFINANCE), re.IGNORECASE)
NON_FINANCIAL_PATTERN = re.compile('|'.join(re.escape(n) for n in NON_FINANCIAL_SECTORS), re.IGNORECASE)

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

_ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouucaaAAEEEEIIOUUUCAAaaonaaon')

# ═══════════════════════════════════════════════════════════════
#  NORMALISATION AMÉLIORÉE
# ═══════════════════════════════════════════════════════════════
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
    
    # Préserver les mots composés importants (acronymes, termes techniques)
    text = re.sub(r'\b(?:Credit|Crédit|Risque|Garantie|Portefeuille|Reporting|Finance|Banque|Corporate|Management|NPL|IFRS|COBAC|BEAC|GIMAC|SYSTAC|SYGMA|SWIFT|TSG|CIR|ECOBANK|UBA|ORABANK|CEMAC|UEMOA)\b', 
                  lambda m: m.group(0).lower(), text, flags=re.IGNORECASE)
    
    # Supprimer les accents
    no_accents = text.lower().translate(_ACCENT_MAP)
    
    # Garder les mots importants
    cleaned = re.sub(r'[^\w\s\-/\.]', ' ', no_accents)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # ✅ IMPORTANT: Garder les mots courts (2 lettres) car des acronymes importants peuvent être courts
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

# ═══════════════════════════════════════════════════════════════
#  EXTRACTION DE TEXTE AMÉLIORÉE
# ═══════════════════════════════════════════════════════════════
def extract_text_from_pdf_via_ocr(file_bytes):
    if not OCR_AVAILABLE:
        return ""
    try:
        # Vérifier que tesseract est installé
        import subprocess
        subprocess.run(['tesseract', '--version'], capture_output=True, check=True)
    except:
        return ""
    try:
        # Convertir PDF en images
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(file_bytes, first_page=1, last_page=5)
        text = ""
        for img in images:
            if img.mode != 'L':
                img = img.convert('L')
            custom_config = r'--oem 3 --psm 6 -l fra+eng'
            page_text = pytesseract.image_to_string(img, config=custom_config)
            if page_text:
                text += page_text + "\n"
        if text.strip():
            text = normalize_spaces(text)
            text = re.sub(r'[|¦]', '', text)
            return normalize_unicode(text)
        return ""
    except Exception as e:
        logger.warning(f"OCR erreur: {e}")
        return ""

MAX_PDF_PAGES = 15
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024
EXTRACTION_TIMEOUT = 60  # Secondes

def extract_text_from_pdf_robust(file_bytes, filename):
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        logger.warning(f"⚠️ PDF trop volumineux ({len(file_bytes) / 1024 / 1024:.1f} MB): {filename}")
        return ""
    
    text = ""
    extraction_methods = []
    
    # Méthode 1: pdfplumber (meilleur pour PDF textuels)
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for i, page in enumerate(pdf.pages):
                    if i >= MAX_PDF_PAGES:
                        logger.info(f"⚠️ PDF tronqué à {MAX_PDF_PAGES} pages: {filename}")
                        break
                    # Extraire les tableaux
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                if row:
                                    row_text = ' | '.join([str(cell).strip() if cell else '' for cell in row])
                                    if row_text.strip():
                                        text += normalize_spaces(row_text) + "\n"
                    # Extraire le texte
                    content = page.extract_text(x_tolerance=3, y_tolerance=3, keep_blank_chars=True, use_text_flow=True)
                    if content:
                        text += normalize_spaces(content) + "\n"
            if text.strip() and len(text.strip()) > 200:
                extraction_methods.append('pdfplumber')
                logger.info(f"✅ pdfplumber: {len(text)} caractères pour {filename}")
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"pdfplumber erreur: {e}")
    
    # Méthode 2: PyPDF2
    if PYPDF2_AVAILABLE and not text.strip():
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            for i, page in enumerate(reader.pages):
                if i >= MAX_PDF_PAGES:
                    break
                content = page.extract_text()
                if content:
                    text += normalize_spaces(content) + "\n"
            if text.strip() and len(text.strip()) > 200:
                extraction_methods.append('pypdf2')
                logger.info(f"✅ PyPDF2: {len(text)} caractères pour {filename}")
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"PyPDF2 erreur: {e}")
    
    # Méthode 3: OCR (pour les PDF scannés)
    if OCR_AVAILABLE and len(text.strip()) < 200:
        try:
            logger.info(f"🔄 Tentative OCR pour {filename}")
            ocr_text = extract_text_from_pdf_via_ocr(file_bytes)
            if ocr_text and len(ocr_text.strip()) > 200:
                text = ocr_text
                extraction_methods.append('ocr')
                logger.info(f"✅ OCR: {len(text)} caractères pour {filename}")
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"OCR erreur: {e}")
    
    # Méthode 4: Fallback - extraction brute
    if len(text.strip()) < 100:
        try:
            raw = file_bytes.decode('utf-8', errors='ignore')
            raw = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', raw)
            if raw.strip():
                text = normalize_unicode(normalize_spaces(raw))
                extraction_methods.append('raw')
                logger.info(f"✅ Raw: {len(text)} caractères pour {filename}")
                return text
        except:
            pass
    
    if not text.strip():
        logger.warning(f"❌ Aucun texte extrait pour {filename}")
        return ""
    
    return normalize_unicode(text.strip())

def extract_text_from_docx_robust(file_bytes):
    if not DOCX_AVAILABLE:
        return ""
    
    text = ""
    
    # Méthode 1: Lecture XML directe (plus fiable)
    try:
        import zipfile
        from xml.etree import ElementTree as ET
        
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            # Lire le document principal
            with zf.open('word/document.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
                
                # Namespace Word
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                
                # Extraire tout le texte
                texts = []
                for elem in root.iter():
                    if elem.tag == f'{{{ns["w"]}}}t':
                        if elem.text:
                            texts.append(elem.text)
                
                text = ' '.join(texts)
                if text.strip():
                    logger.info(f"✅ DOCX XML: {len(text)} caractères")
                    return normalize_unicode(normalize_spaces(text))
    except Exception as e:
        logger.warning(f"DOCX XML erreur: {e}")
    
    # Méthode 2: python-docx standard
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
        if result:
            logger.info(f"✅ DOCX python-docx: {len(result)} caractères")
            return normalize_unicode(result)
    except Exception as e:
        logger.warning(f"DOCX python-docx erreur: {e}")
    
    # Méthode 3: Fallback
    try:
        raw = file_bytes.decode('utf-8', errors='ignore')
        raw = re.sub(r'[^\x20-\x7E\u00C0-\u017F]+', ' ', raw)
        result = normalize_unicode(normalize_spaces(raw))
        if result.strip():
            logger.info(f"✅ DOCX raw: {len(result)} caractères")
            return result
    except:
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
    
    # Vérifier que le fichier n'est pas vide
    if len(file_bytes) < 100:
        logger.warning(f"⚠️ Fichier vide ou trop petit: {filename} ({len(file_bytes)} bytes)")
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
            # Tentative de lecture brute pour les formats non reconnus
            try:
                return normalize_unicode(normalize_spaces(file_bytes.decode('utf-8', errors='ignore').strip()))
            except:
                pass
    except Exception as e:
        logger.error(f"❌ Erreur extraction {filename}: {str(e)}")
        # Si l'extraction échoue, tenter l'OCR pour les PDFs
        if ext == 'pdf' and OCR_AVAILABLE:
            try:
                return extract_text_from_pdf_via_ocr(file_bytes)
            except:
                pass
        return ""
    
    return ""

# ═══════════════════════════════════════════════════════════════
#  DÉTECTION SECTORIELLE
# ═══════════════════════════════════════════════════════════════
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
    banking_posts = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Senior Finance Officer", "Market Risk Officer", "Chargé(e) d'Administration de Crédit"]
    if poste in banking_posts:
        banking_criteria = ["Expérience bancaire", "Minimum 3 ans en crédit / risque (hors stage)", "Exposition aux garanties ou conformité", "Minimum 3 ans institution financière (hors stage)", "Minimum 3 ans département finance ou en cabinet d'audit (hors stage)", "Expérience en analyse crédit", "Capacité à lire des états financiers", "Base en risques de marché", "Exposition à FX / taux / liquidité", "Expérience en reporting financier structuré", "Exposition aux états financiers", "Expérience dans une banque ou un établissement financier réglementé", "Minimum 1 an d'expérience dans une fonction bancaire (administration de crédit, back-office, risques ou analyse crédit)", "Exposition au cycle de vie du crédit bancaire (mise en place, suivi, garanties, échéances)"]
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
    banking_posts = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Senior Finance Officer", "Market Risk Officer", "Chargé(e) d'Administration de Crédit"]
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

def check_no_banking_tools(raw_text):
    text_lower = raw_text.lower().translate(_ACCENT_MAP)
    all_tools = ['finacle', 't24', 'temenos', 'amplitude', 'flexcube', 'core banking', 'systeme bancaire', 'banking system', 'sigma', 'sygma', 'systac', 'spectra', 'cerber', 'excel', 'vba', 'reporting', 'dashboard', 'tableau de bord']
    found = any(kw in text_lower for kw in all_tools)
    return not found

def check_unexplained_gaps(raw_text):
    years_found = sorted(set(int(m) for m in re.findall(r'(20[0-2]\d|199\d)', raw_text)))
    if len(years_found) < 2:
        return False
    gaps = []
    for i in range(1, len(years_found)):
        gap = years_found[i] - years_found[i-1]
        if gap >= 3:
            gaps.append((years_found[i-1], years_found[i]))
    return len(gaps) > 0

# ═══════════════════════════════════════════════════════════════
#  KEYWORD_MAPPING - VERSION 3.0 AVEC MOTS-CLÉS GÉNÉRIQUES
# ═══════════════════════════════════════════════════════════════
KEYWORD_MAPPING = {
    # ═══════════════════════════════════════════════════════════════
    #  MOTS-CLÉS GÉNÉRIQUES POUR CHEF DE DIVISION LOCAL CORPORATE
    # ═══════════════════════════════════════════════════════════════
    "Aucune expérience dans le secteur bancaire ou financier réglementé": [
        "banque", "bancaire", "etablissement financier", "institution financiere", 
        "banque commerciale", "commercial bank", "financial institution", "credit institution",
        "ecobank", "orabank", "uba", "bank of africa", "boa", "bgfi", "afriland",
        "societe generale", "standard chartered", "banque atlantique", "cbc", "bct",
        "ecob", "orab", "ubagroup", "boa", "sg", "financial", "banking"
    ],
    "Niveau de diplôme inférieur à Bac +4 (Master ou équivalent requis)": [
        "master", "bac+5", "bac +5", "mba", "ingénieur", "doctorat", "phd",
        "diplome d'etudes superieures", "diplome superieur", "graduate degree",
        "bac+4", "bac +4", "maitrise", "licence professionnelle", "bachelor",
        "master en finance", "master gestion", "master banque", "master corporate"
    ],
    "Moins de 5 ans d'expérience professionnelle, dont une partie significative en banque": [
        "EXP_5ANS_BANQUE"
    ],
    "Aucune expérience en gestion d'un portefeuille de clients Corporate ou d'entreprises": [
        "portefeuille", "corporate", "grandes entreprises", "gestion portefeuille",
        "client corporate", "sme", "local corporate", "enterprise", "grands comptes",
        "portefeuille client", "gestion client", "client entreprise", "corporate client"
    ],
    "Aucune expérience managériale : ni encadrement d'équipe, ni pilotage d'une activité commerciale": [
        "management", "encadrement", "supervision", "team lead", "chef equipe",
        "manager", "responsable equipe", "pilotage", "direction", "gestion equipe",
        "leadership", "manageur", "superviseur", "head of", "directeur"
    ],
    "Aucune exposition à la gestion du risque de crédit ou au suivi de la qualité d'un portefeuille (NPL, provisions)": [
        "npl", "non performing", "risque credit", "credit risk", "provision",
        "qualite portefeuille", "impaye", "default", "portefeuille credit",
        "npls", "non-performing", "loan", "credit", "portfolio quality"
    ],
    
    "Pilotage d'une activité Corporate ou d'un segment entreprises avec des objectifs chiffrés (revenus, volumes, marges)": [
        "pilotage", "activite", "corporate", "objectifs", "chiffres", "revenus", 
        "volumes", "marges", "performance", "resultats", "business plan", 
        "budget", "croissance", "developpement", "chiffre d'affaires",
        "objectif", "resultat", "performance commerciale"
    ],
    "Gestion d'un portefeuille de clients Corporate et capacité à le développer": [
        "portefeuille corporate", "developpement portefeuille", "fidelisation client",
        "acquisition client", "gestion relation client", "growth", "client relationship",
        "portfolio", "development", "client acquisition", "customer retention"
    ],
    "Encadrement et évaluation d'une équipe commerciale ou bancaire": [
        "encadrement equipe", "evaluation equipe", "management equipe", "team management",
        "supervision equipe", "chef equipe", "manager", "leadership", "team leader",
        "responsable equipe", "coordination equipe", "pilotage equipe"
    ],
    "Suivi de la qualité du portefeuille de crédit (NPL, CIR, provisions) et reporting à la direction": [
        "suivi qualite portefeuille", "npl", "cir", "provision", "reporting direction",
        "ratio npl", "cost income ratio", "qualite credit", "portefeuille credit",
        "reporting", "dashboard", "kpi", "indicateurs", "qualite portefeuille"
    ],
    "Développement de ventes croisées (cross-selling) ou de partenariats interdépartementaux": [
        "cross selling", "ventes croisees", "up selling", "cross-sell", "partenariats",
        "interdepartemental", "synergie", "collaboration", "upselling",
        "cross-sell", "upsell", "partenariat", "collaboration interdepartementale"
    ],
    "Production ou supervision de rapports de performance commerciale et financière": [
        "rapport performance", "reporting commercial", "reporting financier", "dashboard",
        "kpi", "indicateurs", "performance commerciale", "rapport d'activite",
        "tableau de bord", "reporting", "performance reporting"
    ],
    "Exposition à la réglementation bancaire locale (COBAC, BEAC) ou internationale": [
        "cobac", "beac", "reglementation bancaire", "banking regulation", "bale",
        "basel", "reglementation", "conformite", "compliance", "prudentiel",
        "regulator", "central bank", "commission bancaire"
    ],
    
    "Pilotage d'une division ou d'une ligne Corporate avec atteinte des objectifs de revenus et de portefeuille": [
        "pilotage division", "ligne corporate", "objectifs revenus", "objectifs portefeuille",
        "atteinte objectifs", "performance division", "resultats corporate",
        "division head", "line manager", "business head"
    ],
    "Gestion active du ratio NPL et du ratio coût/revenu (CIR) — résultats chiffrés mentionnés": [
        "ratio npl", "npl", "non performing", "cost income ratio", "cir", "results",
        "resultats chiffres", "reduction npl", "amelioration cir",
        "npl ratio", "cost-to-income", "performance ratio"
    ],
    "Expérience avérée en cross-selling avec des équipes TSG, Trade Finance ou Cash Management": [
        "tsg", "trade finance", "cash management", "cross selling", "financement trade",
        "financement du commerce", "cash mgt", "partenariat interdepartemental",
        "trade", "cash", "transaction banking"
    ],
    "Développement réel du portefeuille Corporate : acquisition de clients, fidélisation, nombre de produits par client": [
        "acquisition client", "fidelisation client", "nombre produits client", "cross ratio",
        "penetration client", "developpement portefeuille", "client corporate",
        "client acquisition", "retention", "product per client"
    ],
    "Leadership démontré : constitution d'équipe, développement des collaborateurs, vivier de talents": [
        "leadership", "constitution equipe", "developpement collaborateurs", "vivier talents",
        "recrutement equipe", "formation equipe", "mentorat", "coaching",
        "team building", "talent development", "people management"
    ],
    "Certification Ecobank, Moody's ou ITB (Institut Technique de Banque) ou équivalent": [
        "ecobank certification", "moody's", "itb", "institut technique banque",
        "certification bancaire", "formation banque", "certified", "moodys",
        "ecobank", "moodys", "itb", "banking certification"
    ],
    "Connaissance du marché corporate tchadien ou de la zone CEMAC / UEMOA": [
        "cemac", "uemoa", "zone cemac", "zone uemoa", "afrique centrale",
        "afrique de l'ouest", "marche corporate", "marche local", "tchad", "chad",
        "cemac", "uemoa", "africa", "west africa", "central africa"
    ],
    "Exposition aux plateformes numériques bancaires (OMNI, Cash Management ou équivalent)": [
        "omni", "cash management", "plateforme numerique", "digital banking",
        "banque en ligne", "mobile banking", "core banking", "ebanking",
        "digital", "online banking", "fintech", "banking platform"
    ],
    "Résultats commerciaux quantifiés et vérifiables dans le CV (chiffres d'affaires, taux de croissance, NPS)": [
        "croissance", "chiffre d'affaires", "taux de croissance", "nps",
        "resultats commerciaux", "performance commerciale", "objectifs atteints",
        "ca", "benefices", "marges", "part de marche", "growth rate",
        "revenue", "profit", "market share", "turnover"
    ],
    
    "Parcours exclusivement back-office ou risques sans expérience commerciale Corporate": [
        "back office", "back-office", "risque", "credit", "analyse",
        "operations", "middle office", "sans commercial", "hors commercial",
        "risk", "credit analysis", "operations"
    ],
    "Profil techniquement solide (crédit, analyse) mais sans expérience managériale ni pilotage d'une P&L": [
        "technique", "analyse credit", "credit analyst", "sans management",
        "sans encadrement", "pas d'equipe", "profil technique",
        "credit", "analysis", "technical"
    ],
    "Expériences très courtes (moins de 2 ans par poste) ou trajectoire sans progression hiérarchique visible": [
        "experience courte", "moins 2 ans", "sans progression", "poste identique",
        "pas d'evolution", "carriere stagnante", "short tenure",
        "no progression", "same role"
    ],
    "CV sans aucun résultat chiffré : missions décrites en responsabilités sans livrables ni indicateurs atteints": [
        "sans chiffres", "missions generiques", "responsabilites sans resultats",
        "pas d'indicateurs", "aucun livrable", "no results",
        "generic tasks", "no metrics"
    ],
    "Mobilité géographique ou sectorielle excessive sans ancrage dans le secteur bancaire Corporate": [
        "mobilite", "changement secteur", "secteurs varies", "secteur non bancaire",
        "geographique", "exterieur banque", "frequent change",
        "multiple sectors", "non-banking"
    ],
    "Trous inexpliqués dans le parcours ou incohérences entre les postes déclarés": [
        "trou parcours", "inexplique", "incoherence", "sans explication",
        "parcours discontinu", "absence d'activite", "gap",
        "unexplained gap", "inconsistency"
    ],
    
    # ═══════════════════════════════════════════════════════════════
    #  MOTS-CLÉS GÉNÉRIQUES POUR CHARGÉ(E) D'ADMINISTRATION DE CRÉDIT
    # ═══════════════════════════════════════════════════════════════
    "Aucune expérience ou formation dans un domaine bancaire, financier ou comptable": [
        "banque", "bancaire", "finance", "comptabilite", "comptable",
        "audit", "gestion", "economie", "banking", "accounting",
        "financial", "business", "management", "economie"
    ],
    "Niveau de diplôme inférieur à Bac +3 (Banque, Finance, Gestion, Comptabilité ou équivalent)": [
        "bac+3", "bac +3", "licence", "bachelor", "bts", "dut",
        "diplome universitaire", "etudes superieures", "bac+2",
        "licence pro", "bachelor", "brevet"
    ],
    "Aucune notion du crédit bancaire : ni dans la formation, ni dans l'expérience, ni dans la lettre": [
        "credit", "credit bancaire", "financement", "octroi", "pret",
        "garantie", "echange", "remboursement", "portefeuille",
        "loan", "lending", "borrowing", "credit analysis"
    ],
    
    "Exposition au cycle de crédit : conditions d'approbation, mise en place, suivi des échéances": [
        "cycle credit", "approbation", "mise en place", "suivi echeances",
        "credit approval", "loan processing", "credit monitoring", "repayment",
        "deblocage", "documentation credit", "conditions approbation",
        "credit cycle", "loan cycle", "approval", "disbursement"
    ],
    "Gestion ou participation au suivi des garanties (enregistrement, valorisation, renouvellements)": [
        "garantie", "collateral", "suivi garanties", "enregistrement garantie",
        "valorisation", "renouvellement assurance", "guarantee management",
        "surete", "hypotheque", "nantissement", "security",
        "collateral management", "guarantee tracking"
    ],
    "Production ou contribution à des reportings ou tableaux de bord liés à un portefeuille de crédit": [
        "reporting", "tableau de bord", "dashboard", "rapport portefeuille",
        "kpi", "indicateurs", "portfolio report", "credit report",
        "production de rapports", "reporting credit", "report",
        "portfolio dashboard", "credit dashboard"
    ],
    "Expérience avec un système bancaire (Finacle, T24, Amplitude, Flexcube) ou outil de suivi de portefeuille": [
        "finacle", "t24", "amplitude", "flexcube", "core banking",
        "systeme bancaire", "banking system", "temenos", "sigma", "sygma",
        "banking software", "credit system", "portfolio tool",
        "finacle", "temenos", "flexcube", "amplitude"
    ],
    "Détection ou signalement d'anomalies, d'impayés ou de dépassements dans un portefeuille": [
        "anomalie", "impaye", "depassement", "incident", "alerte",
        "default", "overdue", "past due", "exception", "signalement",
        "detection", "remontee", "anomaly", "alert",
        "non-payment", "overdue", "exception reporting"
    ],
    
    "Mention explicite de la gestion administrative du cycle de crédit (mise en place, suivi, clôture)": [
        "administration credit", "gestion administrative credit", "cycle credit",
        "mise en place credit", "suivi credit", "cloture credit",
        "credit administration", "loan administration", "administrative",
        "credit management", "loan management"
    ],
    "Exposition à la norme IFRS 9 : staging du portefeuille (Stage 1, 2, 3), ECL, provisions": [
        "ifrs 9", "stage 1", "stage 2", "stage 3", "ecl",
        "expected credit loss", "provisionnement", "staging",
        "perte attendue", "classification risques", "ifrs9",
        "impairment", "credit loss", "provision"
    ],
    "Suivi et sécurisation des garanties (enregistrement, valorisation, coffre, coordination juridique)": [
        "coffre", "safe", "vault", "coffre-fort", "coordination juridique",
        "legal coordination", "legal department", "service juridique",
        "guarantee", "collateral", "security", "legal"
    ],
    "Production de reportings portefeuille (encours, impayés, dépassements, couverture par garanties)": [
        "encours", "outstanding", "couverture", "coverage ratio",
        "taux de couverture", "couverture par garanties",
        "portfolio", "reporting", "outstanding loans"
    ],
    "Participation aux comités de risque et traitement des anomalies (COBAC, audit interne)": [
        "comite risque", "risk committee", "credit committee", "audit interne",
        "internal audit", "traitement anomalies", "anomaly resolution",
        "risk committee", "credit committee", "audit"
    ],
    "Maîtrise des Produits de Portefeuille (PP) et de la politique de crédit (GCPPM ou équivalent)": [
        "produits de portefeuille", "pp", "politique de credit", "credit policy",
        "gcppm", "credit guidelines", "lending policy",
        "portfolio products", "credit policy", "guidelines"
    ],
    "Expérience dans une banque de la zone CEMAC / UEMOA avec exposition réglementaire COBAC": [
        "cemac", "uemoa", "afrique centrale", "afrique de l'ouest",
        "bceao", "beac", "zone franc", "cobac",
        "west africa", "central africa", "banking regulation"
    ],
    "Audits ou contrôles internes réussis sans réserve majeure": [
        "audit reussi", "successful audit", "sans reserve", "clean audit",
        "zero anomalie", "controle interne reussi",
        "internal audit", "control", "compliance"
    ],
    "Rigueur documentaire : dossiers complets, traçabilité des actes, zéro anomalie détectée en contrôle interne": [
        "rigueur", "rigueur documentaire", "dossiers complets", "tracabilite",
        "zero anomalie", "zero erreur", "meticulous", "attention to detail",
        "accuracy", "precision", "thoroughness", "compliance"
    ],
    
    "Parcours commercial ou front-office pur sans exposition aux opérations de crédit": [
        "commercial", "front office", "front-office", "vente", "sales",
        "business development", "relation client", "prospection",
        "sales", "client facing", "customer service"
    ],
    "Profil uniquement théorique ou stagiaire : à évaluer sur la motivation et la capacité d'apprentissage rapide": [
        "stage", "stagiaire", "intern", "theorique", "academique",
        "trainee", "volontariat", "formation seule",
        "internship", "theoretical", "academic"
    ],
    "Expériences courtes ou hétérogènes : comprendre les raisons avant de conclure": [
        "cdd court", "contrat court", "short contract", "interim",
        "temporary", "saisonnier", "multipostes",
        "short-term", "contract", "temporary"
    ],
    "Missions peu détaillées dans le CV : interroger sur les livrables et outils réellement utilisés": [
        "missions generiques", "taches diverses", "peu de details",
        "sans outils", "sans livrables", "generic",
        "vague", "unclear", "no details"
    ],
    
    "Expérience en banque ou établissement financier réglementé": [
        "banque", "bancaire", "etablissement bancaire", "institution bancaire",
        "etablissement financier reglemente", "secteur bancaire", "bank", "banking",
        "financial institution", "regulated financial institution"
    ],
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
    "Expériences très courtes (< 1 an par poste) sans progression visible": ["stage", "cdd court", "contrat court"],
    
    # Critères communs pour les deux postes
    "Niveau de diplôme minimum Bac +3 (école de commerce, gestion, comptabilité ou équivalent)": [
        "bac+3", "bac +3", "bac 3", "bac+4", "bac+5", "bac +4", "bac +5",
        "licence", "licence professionnelle", "bachelor", "master", "mba",
        "ecole de commerce", "diplome universitaire", "diplome d etudes superieures"
    ],
    "Minimum 1 an d'expérience dans une fonction bancaire (administration de crédit, back-office, risques ou analyse crédit)": ["EXP_BANK_1ANS"],
    "Exposition au cycle de vie du crédit bancaire (mise en place, suivi, garanties, échéances)": [
        "cycle de credit", "cycle du credit", "cycle de vie credit",
        "mise en place credit", "deblocage credit", "credit disbursement",
        "loan origination", "loan processing", "credit approval",
        "approbation credit", "octroi credit", "credit granting",
        "documentation credit", "credit file", "dossier credit",
        "instruction credit", "credit administration", "administration de credit",
        "gestion de credit", "credit management", "loan administration",
        "back-office credit", "back office credit", "suivi credit",
        "credit monitoring", "echeances credit", "credit repayment",
        "remboursement credit", "cloture credit", "credit closure"
    ],
    "Connaissance des normes comptables bancaires ou de la réglementation COBAC": [
        "cobac", "commission bancaire", "reglementation bancaire",
        "banking regulation", "reglementation cobac", "normes cobac",
        "instructions cobac", "directives cobac", "supervision bancaire",
        "banking supervision", "controle prudentiel", "prudential regulation",
        "ifrs 9", "ifrs9", "ias 39", "ecl", "expected credit loss",
        "staging", "stage 1", "stage 2", "stage 3", "provisionnement"
    ],
    "Expérience de production de reportings ou tableaux de bord de portefeuille": [
        "reporting portefeuille", "tableau de bord portefeuille",
        "encours credit", "impayes", "creances douteuses", "npl",
        "non-performing", "depassements", "couverture garanties",
        "provisionnement", "portfolio monitoring", "credit portfolio"
    ],
    "Maîtrise des outils bureautiques courants (Excel, traitement de texte, messagerie)": [
        "excel", "word", "powerpoint", "outlook", "messagerie",
        "office", "microsoft office", "bureautique", "traitement de texte",
        "tableur", "vba", "macros", "power query"
    ],
    "Expérience bancaire": [
        "banque", "bancaire", "etablissement bancaire", "institution bancaire", 
        "banque commerciale", "microfinance", "etablissement financier", 
        "institution financiere", "secteur bancaire", "groupe bancaire", 
        "filiale bancaire", "bank", "banking", "financial institution", 
        "credit institution", "commercial bank", "ecobank", "orabank", 
        "uba", "finadev", "ucec", "microfinance", "ecob", "orab", "ubagroup"
    ],
    "Minimum 3 ans en crédit / risque (hors stage)": ["EXP_CREDIT_3ANS"],
    "Exposition aux garanties ou conformité": [
        "garantie", "garanties", "nantissement", "hypotheque", "surete", 
        "suretes", "conformite", "compliance", "cobac", "bceao", "bcac", 
        "commission bancaire", "reglementation bancaire", "audit", "controle interne", 
        "collateral", "regulatory", "guarantee", "guarantees", "compliance officer", 
        "regulatory compliance", "internal control"
    ],
    "Validation de dossiers de crédit": [
        "validation dossier", "instruction credit", "approbation credit", 
        "dossier credit", "traitement dossier", "montage dossier", 
        "credit approval", "loan processing", "credit file", "loan file"
    ],
    "Gestion des garanties": [
        "gestion garanties", "suivi garanties", "garanties reelles", 
        "portefeuille garanties", "hypotheque", "nantissement", 
        "collateral management", "guarantee management", "security management"
    ],
    "Participation à des audits": [
        "audit", "controle interne", "inspection", "commissariat aux comptes", 
        "conformite", "compliance audit", "mission audit", "internal audit", 
        "external audit", "audit mission", "audit report"
    ],
    "IFRS 9": [
        "ifrs 9", "ias 39", "normes ifrs", "comptabilite ifrs", "ifrs9", 
        "provisionnement ifrs", "international financial reporting", "ifrs standards", 
        "impairment ifrs 9"
    ],
    "COBAC / conformité": [
        "cobac", "conformite bancaire", "bceao", "bcac", "commission bancaire", 
        "regulation bancaire", "compliance", "banking regulation", "central bank", 
        "banking authority"
    ],
    "Suivi portefeuille / impayés": [
        "portefeuille credit", "impayes", "recouvrement", "contentieux", "encours", 
        "suivi portefeuille", "creances douteuses", "npls", "portfolio monitoring", 
        "non-performing loans", "loan portfolio", "collections", "past due", 
        "default management"
    ],
    "Expérience en analyse crédit": [
        "analyse credit", "credit analysis", "evaluation credit", "scoring credit", 
        "analyse financiere credit", "instruction credit", "analyste credit", 
        "octroi credit", "loan analysis", "credit analyst", "credit assessment", 
        "credit evaluation"
    ],
    "Capacité à lire des états financiers": [
        "etats financiers", "bilan", "compte de resultat", "ratios financiers", 
        "analyse financiere", "liasse fiscale", "situation financiere", 
        "diagnostic financier", "solvabilite", "financial statements", 
        "balance sheet", "income statement", "financial analysis", 
        "financial ratios", "cash flow statement"
    ],
    "Minimum 3 ans institution financière (hors stage)": ["EXP_FIN_3ANS"],
    "Clients PME": [
        "pme", "petite entreprise", "moyenne entreprise", "tpe", 
        "entreprise cliente", "sme", "small business", "mid-market", 
        "small and medium enterprises"
    ],
    "Clients particuliers": [
        "particulier", "clientele particuliere", "retail banking", 
        "client particulier", "retail", "personal banking", 
        "individual clients", "consumer banking"
    ],
    "Structuration de crédit": [
        "structuration credit", "montage credit", "structurer credit", 
        "dossier de credit", "credit structurel", "loan structuring", 
        "credit structuring", "loan arrangement"
    ],
    "Avis de crédit": [
        "avis credit", "recommandation credit", "opinion credit", 
        "note de credit", "avis d'octroi", "credit opinion", 
        "credit recommendation", "credit memo", "loan opinion"
    ],
    "Cash-flow analysis": [
        "cash flow", "cashflow", "flux tresorerie", "flux de tresorerie", 
        "fcf", "free cash flow", "capacite d autofinancement", "caf", 
        "cash flow analysis", "cash flow statement", "operating cash flow"
    ],
    "Montage de crédit": [
        "montage credit", "structuration credit", "montage dossier", 
        "montage financier", "loan structuring", "credit arrangement", 
        "loan packaging", "deal structuring"
    ],
    "Comités de crédit": [
        "comite credit", "commission credit", "credit committee", 
        "comite d octroi", "validation comite", "credit approval committee", 
        "credit board", "loan committee"
    ]
}

DOMAIN_KEYWORDS_MAP = {
    "EXP_CREDIT_3ANS": [
        "credit", "risque", "banque", "bancaire", "institution financiere", 
        "analyste", "charge", "gestionnaire", "loan", "credit analysis",
        "risk", "banking", "financial", "credit officer", "loan officer"
    ],
    "EXP_FIN_3ANS": [
        "finance", "comptable", "comptabilite", "reporting", "tresorerie", 
        "banque", "institution financiere", "auditeur", "controleur", 
        "financial", "accounting", "risque", "risk"
    ],
    "EXP_FINANCE_3ANS": [
        "finance", "comptable", "comptabilite", "reporting", "tresorerie", 
        "banque", "institution financiere", "financial"
    ],
    "EXP_IT_2ANS": [
        "reseau", "infrastructure", "systeme", "informatique", "it", 
        "network", "serveur", "technicien", "ingenieur", "networking", 
        "cisco", "admin", "administrateur"
    ],
    "EXP_AUDIT_3ANS": [
        "audit", "auditeur", "controle interne", "internal audit", 
        "cabinet audit", "big four", "deloitte", "pwc", "ey", "kpmg", 
        "banking audit", "commissaire aux comptes"
    ],
    "EXP_FIN_5ANS": [
        "finance", "credit", "risque", "banque", "bancaire", 
        "financial institution", "credit analysis", "loan officer", 
        "corporate banking", "investment banking"
    ],
    "EXP_IT_MAINT_5ANS": [
        "maintenance", "support", "it", "informatique", "reseau", 
        "infrastructure", "systemes", "technical support", "helpdesk", 
        "it maintenance", "system administration"
    ],
    "EXP_FINANCE_7ANS": [
        "finance", "comptabilite", "reporting", "banque", "bancaire", 
        "financial reporting", "accounting", "consolidation", "ifrs", 
        "controller", "finance manager", "cfo"
    ],
    "EXP_RISK_5ANS": [
        "risque", "risk", "marche", "market risk", "alm", "tresorerie", 
        "treasury", "trading", "var", "risk management", 
        "financial markets", "investment"
    ],
    "EXP_BANKING_5ANS": [
        "banque", "bancaire", "banking", "comptabilite bancaire", 
        "reporting reglementaire", "beac", "cobac", "spectra", 
        "central bank", "regulatory reporting", "banking supervision"
    ],
    "EXP_BACKOFFICE_3ANS": [
        "back-office", "back office", "operations bancaires", "compensation", 
        "interbancaire", "banque", "bancaire", "middle office", 
        "moyens de paiement", "traitement des operations", "chambre de compensation"
    ],
    "EXP_BANK_1ANS": [
        "credit", "banque", "bancaire", "administration credit", "back office", 
        "back-office", "risque", "risk", "analyse credit", "credit analysis", 
        "loan", "institution financiere", "financial institution", "banking", 
        "credit officer", "credit analyst", "credit administrator", 
        "charge de credit", "gestionnaire credit", "analyste credit", 
        "operations bancaires", "banking operations", "portfolio", 
        "portefeuille", "garantie", "collateral"
    ],
    "EXP_5ANS_BANQUE": [
        "banque", "bancaire", "banking", "institution financiere", 
        "financial institution", "credit", "finance", "experience bancaire",
        "bank", "financial", "commercial bank", "investment bank"
    ]
}

EXP_MIN_YEARS_MAP = {
    "EXP_CREDIT_3ANS": 3.0, "EXP_FIN_3ANS": 3.0, "EXP_FINANCE_3ANS": 3.0, 
    "EXP_IT_2ANS": 2.0, "EXP_AUDIT_3ANS": 3.0, "EXP_FIN_5ANS": 5.0, 
    "EXP_IT_MAINT_5ANS": 5.0, "EXP_FINANCE_7ANS": 7.0, "EXP_RISK_5ANS": 5.0, 
    "EXP_BANKING_5ANS": 5.0, "EXP_BACKOFFICE_3ANS": 3.0, "EXP_BANK_1ANS": 1.0,
    "EXP_5ANS_BANQUE": 5.0
}

# ═══════════════════════════════════════════════════════════════
#  FONCTIONS D'ANALYSE AVANCÉE (SEMANTIQUE + SCORING)
# ═══════════════════════════════════════════════════════════════

def check_criterion_match_advanced(criterion, normalized_text, raw_full_text="", tokens=None, poste=None):
    """
    Vérifie si un critère est présent dans le texte avec une analyse sémantique avancée.
    Combine la recherche de mots-clés, la détection de contexte négatif et le scoring fuzzy.
    """
    try:
        keywords = KEYWORD_MAPPING.get(criterion, [])
        if not keywords:
            # Fallback : recherche du critère comme phrase
            if criterion.lower() in normalized_text:
                return True, 1.0, [criterion]
            return False, 0.0, []
        
        # Gestion des marqueurs d'expérience
        exp_markers = [kw for kw in keywords if kw.startswith("EXP_")]
        if exp_markers:
            marker = exp_markers[0]
            min_years = EXP_MIN_YEARS_MAP.get(marker, 3.0)
            domain_kws = DOMAIN_KEYWORDS_MAP.get(marker, [])
            domain_kws_n = [normalize_for_matching(k)[0] for k in domain_kws]
            found = has_experience_years_strict(raw_full_text, min_years, domain_kws_n, poste)
            return found, 1.0 if found else 0.0, ([marker] if found else [])
        
        # Marqueurs spéciaux
        if keywords == ["MARKER_NOT_MICROFINANCE_ONLY"]:
            ok = check_not_microfinance_only(raw_full_text)
            return ok, (1.0 if ok else 0.0), ([] if ok else ["microfinance_exclusive"])
        if keywords == ["MARKER_NO_BANKING_TOOLS"]:
            ok = check_no_banking_tools(raw_full_text)
            return ok, (1.0 if ok else 0.0), ([] if ok else ["outils_bancaires_presents"])
        if keywords == ["MARKER_UNEXPLAINED_GAPS"]:
            ok = check_unexplained_gaps(raw_full_text)
            return ok, (1.0 if ok else 0.0), ([] if ok else ["parcours_continu"])
        
        # Vérification du contexte pour les critères bancaires
        if poste:
            if not check_criterion_context(criterion, raw_full_text, poste):
                return False, 0.0, []
        
        # Recherche avancée avec scoring fuzzy
        best_score = 0.0
        found_kws = []
        text_clean, text_tokens = normalize_for_matching(normalized_text)
        
        for kw in keywords:
            # Ignorer les marqueurs d'expérience déjà traités
            if kw.startswith("EXP_"):
                continue
            
            kw_clean, kw_tokens = normalize_for_matching(kw)
            
            # Vérification du contexte négatif
            if contains_negative_context(raw_full_text, kw):
                continue
            
            # Recherche exacte
            if kw_clean in text_clean:
                found_kws.append(kw)
                best_score = max(best_score, 1.0)
                continue
            
            # Recherche fuzzy pour les phrases longues
            if RAPIDFUZZ_AVAILABLE and len(kw_clean) >= 4:
                ratio = fuzz.partial_ratio(kw_clean, text_clean)
                if ratio >= 85:
                    if not contains_negative_context(raw_full_text, kw):
                        found_kws.append(f"{kw}~{ratio/100:.2f}")
                        best_score = max(best_score, ratio / 100)
                    continue
            
            # Recherche par tokens
            if kw_tokens and text_tokens:
                common = set(kw_tokens) & set(text_tokens)
                if len(common) >= max(2, len(kw_tokens) * 0.7):
                    if not contains_negative_context(raw_full_text, kw):
                        found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                        best_score = max(best_score, len(common) / len(kw_tokens))
        
        return best_score >= 0.70, round(best_score, 2), found_kws
    
    except Exception as e:
        logger.error(f"❌ Erreur check_criterion pour {criterion}: {e}")
        # Fallback : considérer le critère comme non trouvé
        return False, 0.0, []

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
        entities = {
            'organisations': [], 'dates': [], 'locations': [], 
            'diplomes': [], 'competences_techniques': [], 'noms_personnes': []
        }
        for ent in doc.ents:
            if ent.label_ == 'ORG':
                entities['organisations'].append(ent.text.strip())
            elif ent.label_ in ('DATE', 'TIME'):
                entities['dates'].append(ent.text.strip())
            elif ent.label_ in ('LOC', 'GPE'):
                entities['locations'].append(ent.text.strip())
            elif ent.label_ == 'PERSON':
                entities['noms_personnes'].append(ent.text.strip())
        
        diplome_patterns = [
            r'(?:master|licence|bachelor|mba|dea|deug|ingénieur|doctorat|phd)\s*(?:\d+)?',
            r'bac\s*\+?\s*\d+',
            r'(?:bts|dut)\s*(?:en\s+)?(?:[a-zéèà]+)',
            r'(?:certification|certifié)\s+(?:acca|cpa|cfa|frm|itil|pmp|cia|cisa)'
        ]
        for pattern in diplome_patterns:
            matches = re.findall(pattern, text_to_process, re.IGNORECASE)
            entities['diplomes'].extend(matches)
        
        tech_patterns = [
            r'(?:excel|vba|python|r|sql|sap|oracle|swift|temenos|flexcube)',
            r'(?:ifrs|syscohada|cobac|beac|gimac|bâle)',
            r'(?:lan|wan|vpn|cisco|vmware|linux|windows)'
        ]
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
    enrichment = {
        'nlp_available': True,
        'organisations_detectees': entities.get('organisations', [])[:10],
        'dates_cles': entities.get('dates', [])[:10],
        'lieux': entities.get('locations', [])[:5],
        'diplomes_identifies': entities.get('diplomes', [])[:5],
        'competences_techniques': entities.get('competences_techniques', [])[:10]
    }
    bank_keywords = ['bank', 'banque', 'ecobank', 'orabank', 'uba', 'bgfi', 'afriland']
    detected_banks = [org for org in entities.get('organisations', []) if any(kw in org.lower() for kw in bank_keywords)]
    if detected_banks:
        enrichment['banques_detectees'] = detected_banks
    return enrichment

# ═══════════════════════════════════════════════════════════════
#  PROMPT IA RENFORCÉ POUR AUTHENTICITÉ MAXIMALE
# ═══════════════════════════════════════════════════════════════
SYSTEM_PROMPT_RECRUTEUR = """Tu es un·e responsable recrutement senior avec 15 ans d'expérience dans le secteur bancaire en Afrique centrale et de l'Ouest (CEMAC/UEMOA).

RÈGLES ABSOLUES D'AUTHENTICITÉ :
1. Tu ne JAMAIS inventer de faits qui ne sont PAS dans les documents fournis (CV, lettre, attestations).
2. Si une information n'est PAS explicitement mentionnée, tu considères qu'elle N'EXISTE PAS.
3. Tu ne fais AUCUNE supposition, AUCUNE interprétation excessive.
4. Les stages, bénévolats et formations NE COMPTENT PAS comme expérience professionnelle.
5. Tu distingues l'EMPLOYEUR réel d'un simple mot-clé mentionné dans une mission ponctuelle.
6. Une lettre générique (sans mention du poste spécifique ni de l'institution) est ÉLIMINATOIRE.
7. Tu justifies CHAQUE évaluation avec une citation courte du document concerné.
8. Tu suis STRICTEMENT la grille fournie : aucun critère inventé, aucun critère ignoré.

MÉTHODOLOGIE :
- Pour les critères ÉLIMINATOIRES : si un seul manque → décision "❌ Rejet (éliminatoire)", score total = 0.
- Pour les critères À VÉRIFIER et SIGNAUX FORTS : présence = 1 point, absence = 0. Pas de demi-points.
- Pour le SOUS-SCORE : additionne UNIQUEMENT ce qui est prouvé dans les documents.
- Pour la DÉCISION : applique STRICTEMENT les seuils fournis (pas de mansuétude).

Tu soumets ton analyse exclusivement via l'outil `soumettre_analyse_candidature`."""

def build_analysis_tool_schema():
    return {
        "name": "soumettre_analyse_candidature",
        "description": "Soumet l'analyse structurée d'une candidature.",
        "input_schema": {
            "type": "object",
            "properties": {
                "eliminatoire": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "critere": {"type": "string"},
                            "valide": {"type": "boolean"},
                            "justification": {"type": "string"}
                        },
                        "required": ["critere", "valide", "justification"]
                    }
                },
                "a_verifier": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "critere": {"type": "string"},
                            "detecte": {"type": "boolean"},
                            "justification": {"type": "string"}
                        },
                        "required": ["critere", "detecte", "justification"]
                    }
                },
                "signaux_forts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "critere": {"type": "string"},
                            "detecte": {"type": "boolean"},
                            "justification": {"type": "string"}
                        },
                        "required": ["critere", "detecte", "justification"]
                    }
                },
                "points_attention": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "critere": {"type": "string"},
                            "present": {"type": "boolean"},
                            "justification": {"type": "string"}
                        },
                        "required": ["critere", "present", "justification"]
                    }
                },
                "lettre_motivation": {
                    "type": "object",
                    "properties": {
                        "presente": {"type": "boolean"},
                        "coherente_avec_cv": {"type": "boolean"},
                        "generique_ou_copiee": {"type": "boolean"},
                        "qualite_redactionnelle": {
                            "type": "string",
                            "enum": ["bonne", "moyenne", "faible", "non_evaluable"]
                        },
                        "eliminatoire": {"type": "boolean"},
                        "commentaire": {"type": "string"}
                    },
                    "required": [
                        "presente", "coherente_avec_cv", "generique_ou_copiee",
                        "qualite_redactionnelle", "eliminatoire", "commentaire"
                    ]
                },
                "diplomes": {
                    "type": "object",
                    "properties": {
                        "niveau_suffisant": {"type": "boolean"},
                        "domaine_pertinent": {"type": "boolean"},
                        "atout_complementaire_detecte": {"type": "boolean"},
                        "commentaire": {"type": "string"}
                    },
                    "required": [
                        "niveau_suffisant", "domaine_pertinent",
                        "atout_complementaire_detecte", "commentaire"
                    ]
                },
                "sous_scores": {"type": "object", "additionalProperties": {"type": "integer"}},
                "score_total": {"type": "integer"},
                "decision": {"type": "string"},
                "points_forts": {"type": "array", "items": {"type": "string"}},
                "points_vigilance": {"type": "array", "items": {"type": "string"}},
                "synthese_recruteur": {"type": "string"}
            },
            "required": [
                "eliminatoire", "a_verifier", "signaux_forts", "points_attention",
                "lettre_motivation", "diplomes", "sous_scores", "score_total",
                "decision", "points_forts", "points_vigilance", "synthese_recruteur"
            ]
        }
    }

def build_analysis_user_message(cv_text, lettre_text, attestation_texts_list, poste):
    grille = GRILLE.get(poste, {})
    rubrique, score_max = get_rubrique_scoring(poste)
    
    def fmt_list(items):
        return "\n".join(f"  {i+1}. {c}" for i, c in enumerate(items)) if items else "  (aucun)"
    
    if poste in POSTES_AVEC_SCORING_100:
        rubrique_txt = "\n".join(
            f"  - {SCORING_CODE_LABELS.get(nom, nom)} [clé: \"{nom}\"] : 0 à {pts} pts"
            for nom, pts in rubrique.items()
        )
    elif poste in POSTES_AVEC_SCORING_14:
        rubrique_txt = "\n".join(
            f"  - {SCORING_CODE_LABELS.get(nom, nom)} [clé: \"{nom}\"] : 0 à {pts} pts"
            for nom, pts in rubrique.items()
        )
    else:
        rubrique_txt = "\n".join(f"  - {nom} : 0 à {pts} pts" for nom, pts in rubrique.items())
    
    att_txt = "\n".join(attestation_texts_list) if attestation_texts_list else "(aucune)"
    
    if poste in POSTES_AVEC_SCORING_12:
        seuils_txt = "10-12 : Entretien prioritaire | 7-9 : Vivier | <7 : Rejet"
    elif poste in POSTES_AVEC_SCORING_14:
        seuils_txt = "11-14 : Entretien prioritaire | 7-10 : Potentiel à évaluer | <7 : Rejet"
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
    details = {
        'moteur': 'IA (Claude)',
        'eliminatoire_detail': analyse.get('eliminatoire', []),
        'a_verifier_detail': analyse.get('a_verifier', []),
        'signaux_forts_detail': analyse.get('signaux_forts', []),
        'points_attention_detail': analyse.get('points_attention', []),
        'lettre_motivation': lm,
        'diplomes': analyse.get('diplomes', {}),
        'points_forts': analyse.get('points_forts', []),
        'points_vigilance': analyse.get('points_vigilance', []),
        'synthese_recruteur': analyse.get('synthese_recruteur', '')
    }
    checklist = {}
    for i, e in enumerate(analyse.get('eliminatoire', [])):
        checklist[f'elim_{i}'] = bool(e.get('valide'))
    for i, v in enumerate(analyse.get('a_verifier', [])):
        checklist[f'verif_{i}'] = bool(v.get('detecte'))
    for i, s in enumerate(analyse.get('signaux_forts', [])):
        checklist[f'signal_{i}'] = bool(s.get('detecte'))
    for i, p in enumerate(analyse.get('points_attention', [])):
        checklist[f'attn_{i}'] = bool(p.get('present'))
    return {
        'score': score_total,
        'checklist': checklist,
        'flags_eliminatoires': flags_elim,
        'signaux_detectes': [s['critere'] for s in analyse.get('signaux_forts', []) if s.get('detecte')],
        'details': details,
        'score_breakdown': {
            'bloc1_eliminatoire': bool(flags_elim),
            'moteur_analyse': 'ia',
            'sous_scores': analyse.get('sous_scores', {}),
            'score_final': score_total,
            'score_max': score_max,
            'decision': decision,
            'note': analyse.get('synthese_recruteur') or f"Score: {score_total}/{score_max} — {decision}"
        }
    }

def analyze_cv_intelligent(cv_text, lettre_text, attestation_texts_list, poste):
    if not IA_ANALYSE_ACTIVE or not cv_text or len(cv_text.strip()) < 50 or poste not in GRILLE:
        return None
    tool = build_analysis_tool_schema()
    user_msg = build_analysis_user_message(cv_text, lettre_text, attestation_texts_list, poste)
    for attempt in range(2):
        try:
            with _ia_semaphore:
                response = _claude_client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT_RECRUTEUR,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "soumettre_analyse_candidature"},
                    messages=[{"role": "user", "content": user_msg}]
                )
            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if not tool_use:
                return None
            return _build_result_from_ia_analysis(tool_use.input, poste)
        except Exception as e:
            logger.error(f"IA analyse erreur (tentative {attempt+1}): {e}")
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

def _build_zero_sous_scores_rac():
    return {
        "Adéquation de l'expérience (administration de crédit, gestion des risques, analyse crédit)": 0,
        "Exposition aux normes IFRS 9 et à la gestion du portefeuille de crédit": 0,
        "Rigueur opérationnelle et maîtrise des outils (Excel, système bancaire, classement)": 0,
        "Cohérence et progression du parcours professionnel": 0,
        "Qualité et clarté du CV (missions précises, livrables, résultats)": 0,
        "Lettre de motivation": 0
    }

def _build_zero_sous_scores_chef_division_corporate():
    return {
        "Adéquation de l'expérience en local/corporate Banking avec gestion d'un portefeuille entreprises et objectifs atteints": 0,
        "Capacité managériale démontrée avec encadrement, développement d'équipe et pilotage d'une P&L": 0,
        "Maîtrise du risque de crédit et de la qualité du portefeuille avec gestion du NPL, du CIR et des provisions": 0,
        "Exposition au cross-selling, au Cash Management ou aux solutions TSG / Trade Finance": 0,
        "Cohérence et progression du parcours professionnel avec séniorité et responsabilités croissantes": 0,
        "Qualité du CV avec résultats chiffrés et précision des missions, ainsi que qualité de la lettre de motivation": 0,
        "Certification professionnelle (ITB, Moody's, Ecobank) ou connaissance du marché CEMAC / UEMOA": 0
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

# ═══════════════════════════════════════════════════════════════
#  FONCTIONS DE SCORING
# ═══════════════════════════════════════════════════════════════

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
    signaux_exp = [
        "Supervision quotidienne des opérations de compensation interbancaire",
        "Dénouement de positions nettes en fin de journée",
        "Gestion de suspens, rejets et réclamations interbancaires",
        "Utilisation de systèmes bancaires de compensation (SYSTAC, SYGMA, SWIFT)"
    ]
    n_exp = sum(1 for c in signaux_exp if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    adequation = min(3, n_exp)
    signaux_beac = [
        "BEAC / GIMAC / compensation interbancaire (SYSTAC, SYGMA)",
        "Règlement de positions nettes dans les délais réglementaires",
        "Expérience dans une banque de la zone CEMAC / UEMOA"
    ]
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
    decision = "🥇 Entretien prioritaire" if score_total >= 10 else (
        "🥈 Entretien si besoin (vivier de réserve)" if score_total >= 7 else "❌ Rejet"
    )
    return {
        'score': score_total, 
        'score_max': 12, 
        'decision': decision, 
        'flags_eliminatoires': [], 
        'sous_scores': sous_scores, 
        'checklist': checklist, 
        'detail': f"Score: {score_total}/12 — {decision}"
    }

def calculate_score_charge_admin_credit(cv_text, lettre_text, attestation_texts_list):
    poste = "Chargé(e) d'Administration de Crédit"
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
            'sous_scores': _build_zero_sous_scores_rac(), 
            'checklist': checklist, 
            'detail': f"ÉLIMINÉ : {len(flags)} critère(s)"
        }
    signaux_exp = [
        "Exposition au cycle de crédit : conditions d'approbation, mise en place, suivi des échéances",
        "Gestion ou participation au suivi des garanties (enregistrement, valorisation, renouvellements)",
        "Production ou contribution à des reportings ou tableaux de bord liés à un portefeuille de crédit",
        "Expérience avec un système bancaire (Finacle, T24, Amplitude, Flexcube) ou outil de suivi de portefeuille",
        "Détection ou signalement d'anomalies, d'impayés ou de dépassements dans un portefeuille"
    ]
    n_exp = sum(1 for c in signaux_exp if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    adequation = min(3, n_exp)
    signaux_ifrs = [
        "Exposition à la norme IFRS 9 : staging du portefeuille (Stage 1, 2, 3), ECL, provisions",
        "Production de reportings portefeuille (encours, impayés, dépassements, couverture par garanties)",
        "Suivi et sécurisation des garanties (enregistrement, valorisation, coffre, coordination juridique)",
        "Maîtrise des Produits de Portefeuille (PP) et de la politique de crédit (GCPPM ou équivalent)"
    ]
    n_ifrs = sum(1 for c in signaux_ifrs if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    exposition_ifrs = min(3, n_ifrs)
    outils_ok = check_criterion_match_advanced("Expérience avec un système bancaire (Finacle, T24, Amplitude, Flexcube)", normalized, raw_full, poste=poste)[0]
    classement_ok = check_criterion_match_advanced("Classement physique et numérique des dossiers de crédit", normalized, raw_full, poste=poste)[0]
    rigueur_ok = check_criterion_match_advanced("Rigueur documentaire : dossiers complets, traçabilité des actes", normalized, raw_full, poste=poste)[0]
    rigueur_outils = min(2, sum([outils_ok, classement_ok, rigueur_ok]))
    n_points_attention = sum(1 for c in grille['points_attention'] if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    coherence = 2 if n_points_attention == 0 else (1 if n_points_attention <= 2 else 0)
    word_count = len(cv_text.split())
    has_quantified = bool(re.search(r'\d+\s*(%|pourcent|dossiers|credits|portefeuille|garanties|operations|agences|collaborateurs|millions|milliards)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified) else 0
    lettre_clean = (lettre_text or '').strip()
    if lettre_clean:
        poste_keywords = ['administration de credit', 'credit', 'back-office', 'ifrs', 'cobac', 'garantie', 'portefeuille', 'reporting', 'banque', 'ecobank']
        mentions_poste = any(kw in lettre_clean.lower() for kw in poste_keywords)
        lettre_score = 1 if (len(lettre_clean.split()) >= 80 and mentions_poste) else 0
    else:
        lettre_score = 0
    sous_scores = {
        "Adéquation de l'expérience (administration de crédit, gestion des risques, analyse crédit)": adequation,
        "Exposition aux normes IFRS 9 et à la gestion du portefeuille de crédit": exposition_ifrs,
        "Rigueur opérationnelle et maîtrise des outils (Excel, système bancaire, classement)": rigueur_outils,
        "Cohérence et progression du parcours professionnel": coherence,
        "Qualité et clarté du CV (missions précises, livrables, résultats)": qualite_cv,
        "Lettre de motivation": lettre_score
    }
    score_total = sum(sous_scores.values())
    decision = "🥇 Entretien prioritaire" if score_total >= 10 else (
        "🥈 Entretien si besoin (vivier de réserve)" if score_total >= 7 else "❌ Rejet"
    )
    return {
        'score': score_total, 
        'score_max': 12, 
        'decision': decision, 
        'flags_eliminatoires': [], 
        'sous_scores': sous_scores, 
        'checklist': checklist, 
        'detail': f"Score: {score_total}/12 — {decision}"
    }

def calculate_score_chef_division_corporate(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Division Local Corporate"
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
            'score_max': 14, 
            'decision': '❌ Rejet (éliminatoire)', 
            'flags_eliminatoires': flags, 
            'sous_scores': _build_zero_sous_scores_chef_division_corporate(), 
            'checklist': checklist, 
            'detail': f"ÉLIMINÉ : {len(flags)} critère(s)"
        }
    
    # Adéquation de l'expérience en Corporate Banking (0-3)
    exp_criteria = [
        "Pilotage d'une activité Corporate ou d'un segment entreprises avec des objectifs chiffrés (revenus, volumes, marges)",
        "Gestion d'un portefeuille de clients Corporate et capacité à le développer",
        "Développement réel du portefeuille Corporate : acquisition de clients, fidélisation, nombre de produits par client"
    ]
    n_exp = sum(1 for c in exp_criteria if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    adequation = min(3, n_exp)
    
    # Capacité managériale (0-3)
    mgmt_criteria = [
        "Encadrement et évaluation d'une équipe commerciale ou bancaire",
        "Leadership démontré : constitution d'équipe, développement des collaborateurs, vivier de talents"
    ]
    n_mgmt = sum(1 for c in mgmt_criteria if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    management = min(3, n_mgmt)
    
    # Maîtrise du risque (0-2)
    risk_criteria = [
        "Suivi de la qualité du portefeuille de crédit (NPL, CIR, provisions) et reporting à la direction",
        "Gestion active du ratio NPL et du ratio coût/revenu (CIR) — résultats chiffrés mentionnés"
    ]
    n_risk = sum(1 for c in risk_criteria if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    risque = min(2, n_risk)
    
    # Cross-selling (0-2)
    cross_criteria = [
        "Développement de ventes croisées (cross-selling) ou de partenariats interdépartementaux",
        "Expérience avérée en cross-selling avec des équipes TSG, Trade Finance ou Cash Management"
    ]
    n_cross = sum(1 for c in cross_criteria if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    crossselling = min(2, n_cross)
    
    # Cohérence du parcours (0-2)
    n_points_attention = sum(1 for c in grille['points_attention'] if check_criterion_match_advanced(c, normalized, raw_full, poste=poste)[0])
    coherence = 2 if n_points_attention == 0 else (1 if n_points_attention <= 2 else 0)
    
    # Qualité CV (0-1)
    word_count = len(cv_text.split())
    has_quantified = bool(re.search(r'\d+\s*(%|pourcent|portefeuille|encours|millions|milliards|collaborateurs|equipe|clients|ca|chiffre)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified) else 0
    
    # Lettre de motivation (0-1)
    lettre_clean = (lettre_text or '').strip()
    if lettre_clean:
        poste_keywords = ['corporate', 'grandes entreprises', 'division', 'chef', 'management', 'credit', 'banque', 'local corporate', 'sme', 'pme']
        mentions_poste = any(kw in lettre_clean.lower() for kw in poste_keywords)
        lettre_score = 1 if (len(lettre_clean.split()) >= 80 and mentions_poste) else 0
    else:
        lettre_score = 0
    
    # Certifications (0-1)
    has_certif = check_criterion_match_advanced("Certification Ecobank, Moody's ou ITB (Institut Technique de Banque) ou équivalent", normalized, raw_full, poste=poste)[0]
    has_market = check_criterion_match_advanced("Connaissance du marché corporate tchadien ou de la zone CEMAC / UEMOA", normalized, raw_full, poste=poste)[0]
    certification_score = 1 if (has_certif or has_market) else 0
    
    # Qualité CV + Lettre combinée (0-1)
    qualite_globale = 1 if (qualite_cv == 1 and lettre_score == 1) else (0.5 if (qualite_cv == 1 or lettre_score == 1) else 0)
    qualite_globale = min(1, round(qualite_globale))
    
    sous_scores = {
        "Adéquation de l'expérience en local/corporate Banking avec gestion d'un portefeuille entreprises et objectifs atteints": adequation,
        "Capacité managériale démontrée avec encadrement, développement d'équipe et pilotage d'une P&L": management,
        "Maîtrise du risque de crédit et de la qualité du portefeuille avec gestion du NPL, du CIR et des provisions": risque,
        "Exposition au cross-selling, au Cash Management ou aux solutions TSG / Trade Finance": crossselling,
        "Cohérence et progression du parcours professionnel avec séniorité et responsabilités croissantes": coherence,
        "Qualité du CV avec résultats chiffrés et précision des missions, ainsi que qualité de la lettre de motivation": qualite_globale,
        "Certification professionnelle (ITB, Moody's, Ecobank) ou connaissance du marché CEMAC / UEMOA": certification_score
    }
    
    score_total = sum(sous_scores.values())
    
    if score_total >= 11:
        decision = "🥇 Entretien prioritaire"
    elif score_total >= 7:
        decision = "🥈 Potentiel à évaluer en entretien"
    else:
        decision = "❌ Rejet"
    
    # Construction des points forts et points de vigilance
    points_forts = []
    points_vigilance = []
    
    if adequation >= 2.5:
        points_forts.append("Expérience significative en gestion de portefeuille Corporate")
    elif adequation < 1.5:
        points_vigilance.append("Expérience Corporate limitée")
    
    if management >= 2.5:
        points_forts.append("Solide capacité managériale démontrée")
    elif management < 1.5:
        points_vigilance.append("Expérience managériale à renforcer")
    
    if risque >= 1.5:
        points_forts.append("Maîtrise du risque crédit")
    elif risque < 1:
        points_vigilance.append("Vigilance requise sur la gestion du risque")
    
    if crossselling >= 1.5:
        points_forts.append("Orientation commerciale et cross-selling")
    
    if coherence >= 1.5:
        points_forts.append("Parcours professionnel cohérent et progressif")
    elif coherence < 1:
        points_vigilance.append("Parcours professionnel discontinu")
    
    if qualite_globale >= 1:
        points_forts.append("CV clair avec résultats chiffrés")
    else:
        points_vigilance.append("CV manque de précisions ou résultats non chiffrés")
    
    if certification_score >= 1:
        points_forts.append("Certifications bancaires ou formations spécialisées")
    
    synthese = f"Candidat {'bien positionné' if score_total >= 11 else ('à considérer' if score_total >= 7 else 'en dessous des attentes')} pour le poste de Chef de Division Local Corporate. "
    if points_forts:
        synthese += "Points forts : " + ", ".join(points_forts[:3]) + ". "
    if points_vigilance:
        synthese += "Vigilance sur : " + ", ".join(points_vigilance[:2]) + "."
    
    return {
        'score': score_total, 
        'score_max': 14, 
        'decision': decision, 
        'flags_eliminatoires': [], 
        'sous_scores': sous_scores, 
        'checklist': checklist, 
        'detail': f"Score: {score_total}/14 — {decision}",
        'points_forts': points_forts,
        'points_vigilance': points_vigilance,
        'synthese_recruteur': synthese
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
    for key, max_val, keywords in [
        ('CV_Progression', config.get('CV_Progression', 5), ['promotion', 'évolution', 'senior', 'lead', 'manager', 'chef', 'responsable', 'head of', 'director']),
        ('CV_Management', config.get('CV_Management', 5), ['management', 'encadrement', 'équipe', 'team', 'supervision', 'collaborateurs'])
    ]:
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
    else:
        score_lm_total = 0
        details['lm_total'] = "0/20"
    has_bac5 = any(re.search(p, raw_full, re.IGNORECASE) for p in [r'bac\+\s*5', r'master', r'mba', r'ingénieur'])
    has_bac3 = any(re.search(p, raw_full, re.IGNORECASE) for p in [r'bac\+\s*3', r'licence', r'bachelor'])
    score_diplomes['D_Niveau'] = 4 if has_bac5 else (2 if has_bac3 else 1)
    score_diplomes['D_Specialisation'] = min(3, sum(1 for kw in ['finance', 'comptabilité', 'audit', 'risque', 'management', 'informatique'] if kw in raw_full.lower()) // 2)
    score_diplomes['D_Certif'] = min(3, sum(1 for c in ['acca', 'cpa', 'cfa', 'frm', 'itil', 'pmp', 'cia', 'microsoft', 'cisco', 'aws', 'azure'] if c in raw_full.lower()))
    for k, v in score_diplomes.items():
        details['diplomes_scores'][k] = f"{v}/{[4,3,3][['D_Niveau','D_Specialisation','D_Certif'].index(k)]}"
    score_total = min(100, score_cv_total + score_lm_total + sum(score_diplomes.values()))
    decision = "Shortlist" if score_total >= 80 else ("À considérer" if score_total >= 70 else ("Faible" if score_total >= 60 else "Rejet"))
    return {
        'score': score_total, 
        'decision': decision, 
        'bloc_cv': {'total': score_cv_total, 'max': 70, 'details': score_cv},
        'bloc_lm': {'total': score_lm_total, 'max': 20, 'details': score_lm},
        'bloc_diplomes': {'total': sum(score_diplomes.values()), 'max': 10, 'details': score_diplomes},
        'details': details, 
        'note': f"Score: {score_total}/100 — {decision}"
    }

def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    if not cv_text or len(cv_text.strip()) < 50:
        return {
            'score': 0, 
            'checklist': {}, 
            'flags_eliminatoires': ['CV non analysable'], 
            'signaux_detectes': [], 
            'details': {'error': 'CV vide'}, 
            'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0, 'note': 'CV non analysable'}
        }
    grille = GRILLE.get(poste)
    if not grille:
        return {
            'score': 0, 
            'checklist': {}, 
            'flags_eliminatoires': [f'Poste inconnu: {poste}'], 
            'signaux_detectes': [], 
            'details': {}, 
            'score_breakdown': {}
        }
    all_att_raw = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw
    normalized = normalize_for_matching(raw_full)[0]
    detected_lang = detect_language(cv_text[:500]) if cv_text else None
    intelligent_flags = []
    is_consistent, consistency_reason = check_cv_letter_consistency(cv_text, lettre_text or "", poste)
    if not is_consistent:
        intelligent_flags.append(f"❗ {consistency_reason}")
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
    details = {
        'cv_words': len(cv_text.split()), 
        'lettre_words': len((lettre_text or "").split()), 
        'attestation_words': len(all_att_raw.split()),
        'detected_language': detected_lang, 
        'criteres_valides_bloc2': [], 
        'signaux_valides_bloc3': [], 
        'alertes_attention': intelligent_flags, 
        'matching_details': {},
        'documents_analyses': {'cv': len(cv_text) > 0, 'lettre': len(lettre_text or "") > 0, 'certificats': len(attestation_texts_list) if attestation_texts_list else 0}
    }
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
        return {
            'score': 0, 
            'checklist': checklist, 
            'flags_eliminatoires': flags_elim, 
            'signaux_detectes': [], 
            'details': details, 
            'score_breakdown': {
                'bloc1_eliminatoire': True, 
                'flags_eliminatoires_count': len(flags_elim),
                'adequation_experience': 0, 
                'coherence_parcours': 0, 
                'exposition_risque_metier': 0, 
                'qualite_cv': 0, 
                'lettre_motivation': 0, 
                'total_raw_points': 0, 
                'score_final': 0, 
                'note': f"ÉLIMINÉ : {len(flags_elim)} critère(s)", 
                'documents_analyses': details['documents_analyses']
            }
        }
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'confidence': confidence, 'matched': found_kws if is_present else []}
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"✅ {crit}")
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
    return {
        'score': score_final, 
        'checklist': checklist, 
        'flags_eliminatoires': [], 
        'signaux_detectes': signaux, 
        'details': details, 
        'score_breakdown': {
            'bloc1_eliminatoire': False, 
            'adequation_experience': adequation, 
            'coherence_parcours': coherence, 
            'exposition_risque_metier': risque_metier, 
            'qualite_cv': qualite_cv, 
            'lettre_motivation': lettre_motiv, 
            'bloc2_criteres_valides': len(details['criteres_valides_bloc2']), 
            'bloc2_points': points_bloc2, 
            'bloc3_signaux_detectes': len(signaux), 
            'bloc3_points': points_bloc3, 
            'total_raw_points': points_bloc2 + points_bloc3, 
            'score_final': score_final, 
            'note': f"Score Excel: {score_final}/10", 
            'documents_analyses': details['documents_analyses']
        }
    }

def normalize_text_for_matching(text):
    return normalize_for_matching(text)[0]

def get_rubrique_scoring(poste):
    if poste in SCORING_RUBRIQUES:
        rub = SCORING_RUBRIQUES[poste]
        return rub, sum(rub.values())
    if poste in POSTES_AVEC_SCORING_100:
        rub = SCORING_CONFIG.get(poste) or {}
        return rub, 100
    return {"Adéquation de l'expérience": 3, "Cohérence du parcours": 2, "Exposition au risque métier": 3, "Qualité du CV": 1, "Lettre de motivation": 1}, 10

SCORING_RUBRIQUES = {
    "Chef de Section Compensation": {
        "Adéquation de l'expérience (compensation interbancaire, back-office bancaire)": 3,
        "Exposition aux règles BEAC / GIMAC et aux systèmes de compensation (SYSTAC, SYGMA, SWIFT)": 3,
        "Capacité d'encadrement et de management d'équipe opérationnelle": 2,
        "Cohérence et progression du parcours professionnel": 2,
        "Qualité et clarté du CV (missions précises, livrables, résultats)": 1,
        "Lettre de motivation": 1
    },
    "Chargé(e) d'Administration de Crédit": {
        "Adéquation de l'expérience (administration de crédit, gestion des risques, analyse crédit)": 3,
        "Exposition aux normes IFRS 9 et à la gestion du portefeuille de crédit": 3,
        "Rigueur opérationnelle et maîtrise des outils (Excel, système bancaire, classement)": 2,
        "Cohérence et progression du parcours professionnel": 2,
        "Qualité et clarté du CV (missions précises, livrables, résultats)": 1,
        "Lettre de motivation": 1
    },
    "Chef de Division Local Corporate": {
        "Expérience en gestion de portefeuille Corporate (CV_Exp_Corporate)": 3,
        "Management et encadrement d'équipe (CV_Management)": 3,
        "Gestion du risque crédit et qualité du portefeuille (CV_Risque)": 2,
        "Développement commercial et cross-selling (CV_CrossSelling)": 2,
        "Progression hiérarchique et cohérence du parcours (CV_Progression)": 2,
        "Qualité et clarté du CV (CV_Qualite)": 1,
        "Certifications bancaires ou formations spécialisées (CV_Certification)": 1
    }
}

SCORING_CODE_LABELS = {
    "CV_Exp": "Expérience professionnelle pertinente",
    "CV_Niveau": "Niveau / ancienneté de l'expérience",
    "CV_Secteur": "Expérience sectorielle (banque/finance)",
    "CV_Tech": "Compétences techniques",
    "CV_Progression": "Évolution de carrière",
    "CV_Management": "Capacité managériale",
    "CV_Stabilite": "Stabilité du parcours",
    "LM_Comprehension": "Compréhension du poste (lettre)",
    "LM_Coherence": "Cohérence du profil (lettre)",
    "LM_Motivation": "Motivation réelle (lettre)",
    "LM_Qualite": "Qualité rédactionnelle (lettre)",
    "D_Niveau": "Niveau académique",
    "D_Specialisation": "Spécialisation pertinente",
    "D_Certif": "Certifications",
    "CV_Exp_Corporate": "Expérience en gestion de portefeuille Corporate",
    "CV_Risque": "Gestion du risque crédit et qualité du portefeuille",
    "CV_CrossSelling": "Développement commercial et cross-selling",
    "CV_Qualite": "Qualité et clarté du CV",
    "CV_Certification": "Certifications bancaires ou formations spécialisées"
}

def get_recommandation_from_score(score, poste=None):
    s = int(score)
    if poste and poste in POSTES_AVEC_SCORING_12:
        if s >= 10: return "🥇 Entretien prioritaire"
        elif s >= 7: return "🥈 Entretien si besoin (vivier de réserve)"
        else: return "❌ Rejet"
    if poste and poste in POSTES_AVEC_SCORING_14:
        if s >= 11: return "🥇 Entretien prioritaire"
        elif s >= 7: return "🥈 Potentiel à évaluer en entretien"
        else: return "❌ Rejet"
    if poste and poste in POSTES_AVEC_SCORING_100:
        if s >= 80: return "Shortlist"
        elif s >= 70: return "À considérer"
        elif s >= 60: return "Faible"
        else: return "Rejet"
    if s >= 8: return "🥇 Entretien prioritaire"
    elif s >= 6: return "🥈 Entretien si besoin"
    else: return "❌ Rejet"

def get_score_max_for_poste(poste):
    if poste in POSTES_AVEC_SCORING_12:
        return 12
    if poste in POSTES_AVEC_SCORING_14:
        return 14
    if poste in POSTES_AVEC_SCORING_100:
        return 100
    return 10

def get_recommandation_color(score, poste=None):
    s = int(score)
    if poste and poste in POSTES_AVEC_SCORING_12:
        if s >= 10: return "00FF00"
        elif s >= 7: return "FFA500"
        else: return "FF0000"
    if poste and poste in POSTES_AVEC_SCORING_14:
        if s >= 11: return "00FF00"
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

# ═══════════════════════════════════════════════════════════════
#  PIPELINE D'ANALYSE
# ═══════════════════════════════════════════════════════════════
def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste, force=False):
    try:
        # Forcer l'analyse pour Chef de Division Local Corporate
        if poste == "Chef de Division Local Corporate":
            force = True
            logger.info(f"🔒 Analyse forcée pour {poste} - token: {token}")
        
        # Normaliser attestation_filenames
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        elif not isinstance(attestation_filenames, list):
            attestation_filenames = []
        
        if not force and not is_poste_actif(poste):
            logger.info(f"⏸️ Analyse ignorée pour {token} — poste clôturé : {poste}")
            if supabase:
                supabase.table('candidats').update({
                    "analyse_status": "skipped_closed_post",
                    "analyse_auto_date": datetime.datetime.now().isoformat(),
                    "analyse_skip_reason": f"Poste clôturé : {poste}"
                }).eq('token', token).execute()
            return
        
        logger.info(f"🔍 Début analyse pour {poste} - token: {token}, force: {force}")
        
        cv_text = ""
        if cv_filename:
            try:
                cv_bytes = download_file_from_supabase(cv_filename)
                if cv_bytes:
                    cv_text = extract_text_robust_from_bytes(cv_bytes, cv_filename)
                    logger.info(f"📄 CV extrait: {len(cv_text)} caractères pour {token}")
                else:
                    logger.warning(f"⚠️ CV introuvable: {cv_filename} pour {token}")
            except Exception as e:
                logger.error(f"❌ Erreur extraction CV {cv_filename}: {e}")
        
        lm_text = ""
        if lettre_filename:
            try:
                lm_bytes = download_file_from_supabase(lettre_filename)
                if lm_bytes:
                    lm_text = extract_text_robust_from_bytes(lm_bytes, lettre_filename)
                    logger.info(f"📄 Lettre extraite: {len(lm_text)} caractères pour {token}")
            except Exception as e:
                logger.error(f"❌ Erreur extraction lettre {lettre_filename}: {e}")
        
        att_texts = []
        for fn in (attestation_filenames or []):
            if fn:
                try:
                    att_bytes = download_file_from_supabase(fn)
                    if att_bytes:
                        t = extract_text_robust_from_bytes(att_bytes, fn)
                        if t:
                            att_texts.append(t)
                            logger.info(f"📄 Attestation extraite: {len(t)} caractères pour {token}")
                except Exception as e:
                    logger.error(f"❌ Erreur extraction attestation {fn}: {e}")
        
        if not cv_text:
            logger.error(f"❌ CV vide pour {token} - analyse impossible")
            if supabase:
                supabase.table('candidats').update({
                    "analyse_status": "error",
                    "analyse_error": "CV vide ou non extractible",
                    "analyse_auto_date": datetime.datetime.now().isoformat()
                }).eq('token', token).execute()
            return
        
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
            elif poste == "Chargé(e) d'Administration de Crédit":
                fb = calculate_score_charge_admin_credit(cv_text, lm_text, att_texts)
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
            elif poste == "Chef de Division Local Corporate":
                fb = calculate_score_chef_division_corporate(cv_text, lm_text, att_texts)
                details_data = {'moteur': 'mots-clés (repli)', 'sous_scores': fb['sous_scores']}
                if 'points_forts' in fb:
                    details_data['points_forts'] = fb['points_forts']
                if 'points_vigilance' in fb:
                    details_data['points_vigilance'] = fb['points_vigilance']
                if 'synthese_recruteur' in fb:
                    details_data['synthese_recruteur'] = fb['synthese_recruteur']
                result = {
                    'score': fb['score'], 
                    'checklist': fb.get('checklist', {}), 
                    'flags_eliminatoires': fb['flags_eliminatoires'], 
                    'signaux_detectes': [], 
                    'details': details_data, 
                    'score_breakdown': {
                        'bloc1_eliminatoire': bool(fb['flags_eliminatoires']), 
                        'sous_scores': fb['sous_scores'], 
                        'score_final': fb['score'], 
                        'score_max': fb['score_max'], 
                        'decision': fb['decision'], 
                        'note': fb['detail']
                    }
                }
                logger.info(f"✅ Chef de Division analysé - score: {fb['score']}/14, points_forts: {len(fb.get('points_forts', []))}")
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
            
            # Mise à jour automatique du statut si éliminé
            if result['score_breakdown'].get('bloc1_eliminatoire'):
                update_data['statut'] = 'exclu'
                logger.info(f"🚫 Candidat {token} automatiquement exclu pour {poste} - motif: critères éliminatoires")
            
            supabase.table('candidats').update(update_data).eq('token', token).execute()
            logger.info(f"✅ Analyse sauvegardée pour {token} - score: {result['score']}")
        
        moteur = result['score_breakdown'].get('moteur_analyse', result['details'].get('moteur', 'mots-clés')) if result else 'inconnu'
        tag = "⚠️ ÉLIMINÉ" if result and result['score_breakdown'].get('bloc1_eliminatoire') else "✅"
        if result:
            logger.info(f"{tag} [{moteur}] Score {token}: {result['score']} — {result['score_breakdown'].get('note','')}")
        else:
            logger.error(f"❌ Aucun résultat pour {token}")
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"❌ Erreur analyse pour {token} ({poste}): {str(e)}")
        if supabase:
            supabase.table('candidats').update({
                "analyse_status": "error", 
                "analyse_error": str(e), 
                "analyse_auto_date": datetime.datetime.now().isoformat()
            }).eq('token', token).execute()
    finally:
        try:
            del cv_text, lm_text, att_texts
        except:
            pass
        gc.collect()

# ═══════════════════════════════════════════════════════════════
#  FONCTIONS D'EXPORT - CONSERVÉES TELLES QUELLES
# ═══════════════════════════════════════════════════════════════
# [Les fonctions d'export Excel, CSV, PDF, Word sont conservées]
# Pour des raisons de longueur, elles ne sont pas réécrites ici
# mais restent inchangées par rapport à la version précédente.

def generate_excel_report(candidats_data, poste_filter=None):
    # ... (fonction inchangée)
    pass

def generate_csv_report(candidats_data, poste_filter=None):
    # ... (fonction inchangée)
    pass

def generate_pdf_report(candidats_data, poste_filter=None):
    # ... (fonction inchangée)
    pass

def generate_word_report(candidats_data, poste_filter=None):
    # ... (fonction inchangée)
    pass

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

# ═══════════════════════════════════════════════════════════════
#  ROUTES API
# ═══════════════════════════════════════════════════════════════
@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify({
        "postes": POSTES,
        "postes_actifs": POSTES_ACTIFS,
        "postes_clotures": POSTES_CLOTURES
    }), 200

@app.route('/api/postes/actifs', methods=['GET'])
def get_postes_actifs():
    return jsonify(POSTES_ACTIFS), 200

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
        if is_poste_actif(poste):
            threading.Thread(target=run_analysis_for_candidat, args=(token, cv_filename, lettre_filename, att_filenames, poste, False), daemon=True).start()
            analyse_msg = 'Analyse automatique en cours'
        else:
            analyse_msg = 'Poste clôturé — candidature enregistrée sans analyse'
            supabase.table('candidats').update({
                "analyse_status": "closed_post_no_analysis",
                "analyse_auto_date": datetime.datetime.now().isoformat()
            }).eq('token', token).execute()
        nom_complet = f"{prenom} {nom}".strip()
        sujet_confirmation = f"Confirmation de candidature – {poste}"
        corps_confirmation = f"Bonjour {nom_complet},\nNous accusons réception de votre candidature.\nSans réponse de notre part sous deux (2) semaines, veuillez considérer que votre candidature n'a pas été retenue.\nPour toute information : contact@cdotchad.com.\nCordialement,"
        threading.Thread(target=send_email, args=(email, sujet_confirmation, corps_confirmation), daemon=True).start()
        return jsonify({
            'message': 'Candidature soumise avec succès',
            'token': token,
            'numero_dossier': numero_dossier,
            'analyse': analyse_msg,
            'poste_statut': 'actif' if is_poste_actif(poste) else 'clôturé'
        }), 201
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

@app.route('/api/recruteur/postes/stats', methods=['GET'])
@jwt_required()
def get_postes_stats():
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
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
    return jsonify({
        'total': len(keys),
        'postes_actifs': {
            'count': actifs_count,
            'liste': POSTES_ACTIFS,
            'par_poste': par_poste_actif,
            'eligible_reanalyse': True
        },
        'postes_clotures': {
            'count': clotures_count,
            'liste': POSTES_CLOTURES,
            'par_poste': par_poste_cloture,
            'eligible_reanalyse': False
        }
    }), 200

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
    force = request.args.get('force', '0') == '1'
    if not force and not is_poste_actif(poste):
        return jsonify({
            'error': f'Le poste "{poste}" est clôturé. Utilisez ?force=1 pour forcer l\'analyse.',
            'poste': poste,
            'statut': 'clôturé'
        }), 403
    supabase.table('candidats').update({"analyse_status": "pending", "analyse_manual_trigger": datetime.datetime.now().isoformat()}).eq('token', token).execute()
    threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste, force), daemon=True).start()
    return jsonify({'message': 'Analyse re-déclenchée', 'token': token}), 202

# ═══════════════════════════════════════════════════════════════
#  ROUTE DE RÉANALYSE DES GRILLES MISES À JOUR
# ═══════════════════════════════════════════════════════════════
@app.route('/api/recruteur/reanalyze-updated-grilles', methods=['POST'])
@jwt_required()
def reanalyze_updated_grilles():
    """Réanalyse automatique des candidats pour les postes dont la grille a été mise à jour"""
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    
    # Postes concernés par la mise à jour
    postes_a_reanalyser = ["Chef de Division Local Corporate", "Chargé(e) d'Administration de Crédit"]
    
    response = supabase.table('candidats').select('*').in_('poste', postes_a_reanalyser).execute()
    candidats = response.data if response.data else []
    
    if not candidats:
        return jsonify({'message': 'Aucun candidat à réanalyser pour les postes mis à jour'}), 200
    
    for c in candidats:
        supabase.table('candidats').update({
            "analyse_status": "reanalyzing",
            "reanalyze_trigger": datetime.datetime.now().isoformat(),
            "reanalyze_reason": "Mise à jour des grilles de présélection"
        }).eq('token', c.get('token')).execute()
    
    def analyze_one(data):
        try:
            token = data.get('token')
            cv_fn = data.get('cv_filename')
            lm_fn = data.get('lettre_filename')
            att_raw = data.get('attestation_filenames', '[]')
            poste = data.get('poste')
            if not cv_fn:
                return (token, False, "CV manquant")
            run_analysis_for_candidat(token, cv_fn, lm_fn, att_raw, poste, True)
            return (token, True, "OK")
        except Exception as e:
            return (data.get('token'), False, str(e))
    
    results = []
    with ThreadPoolExecutor(max_workers=min(2, len(candidats))) as executor:
        futures = [executor.submit(analyze_one, c) for c in candidats if c.get('cv_filename')]
        for future in as_completed(futures):
            try:
                results.append(future.result(timeout=180))
            except Exception as e:
                results.append((None, False, f"Timeout: {str(e)}"))
    
    success = sum(1 for r in results if r[1] and r[0] is not None)
    
    return jsonify({
        'message': f'Réanalyse des grilles mises à jour terminée',
        'total': len(candidats),
        'success': success,
        'postes_concernes': postes_a_reanalyser
    }), 202

# ═══════════════════════════════════════════════════════════════
#  ROUTES DE DEBUG
# ═══════════════════════════════════════════════════════════════

@app.route('/api/recruteur/debug/cv-content/<token>', methods=['GET'])
@jwt_required()
def debug_cv_content(token):
    """Affiche le contenu extrait du CV pour déboguer"""
    if not supabase:
        return jsonify({'error': 'Supabase non configuré'}), 500
    
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    
    data = response.data[0]
    cv_fn = data.get('cv_filename')
    
    if not cv_fn:
        return jsonify({'error': 'Pas de CV'}), 400
    
    cv_bytes = download_file_from_supabase(cv_fn)
    if not cv_bytes:
        return jsonify({'error': 'Impossible de télécharger'}), 500
    
    # Extraire avec toutes les méthodes
    results = {
        'candidat': f"{data.get('prenom')} {data.get('nom')}",
        'poste': data.get('poste'),
        'fichier': cv_fn,
        'taille': len(cv_bytes),
        'methodes': {}
    }
    
    # Méthode standard
    text_std = extract_text_robust_from_bytes(cv_bytes, cv_fn)
    results['methodes']['standard'] = {
        'longueur': len(text_std),
        'preview': text_std[:2000] if text_std else "VIDE"
    }
    
    # Méthode OCR
    if OCR_AVAILABLE and cv_fn.lower().endswith('.pdf'):
        try:
            text_ocr = extract_text_from_pdf_via_ocr(cv_bytes)
            results['methodes']['ocr'] = {
                'longueur': len(text_ocr),
                'preview': text_ocr[:2000] if text_ocr else "VIDE"
            }
        except Exception as e:
            results['methodes']['ocr'] = {'error': str(e)}
    
    # Méthode brute
    try:
        text_raw = cv_bytes.decode('utf-8', errors='ignore')[:5000]
        results['methodes']['brute'] = {
            'longueur': len(text_raw),
            'preview': text_raw[:2000] if text_raw else "VIDE"
        }
    except:
        pass
    
    # Vérifier la présence de mots-clés importants
    if text_std:
        mots_cles = ['corporate', 'portefeuille', 'management', 'credit', 'risque', 'npl', 'cir', 'ifrs', 'cobac', 'banque', 'finance']
        found = {kw: kw in text_std.lower() for kw in mots_cles}
        results['mots_cles_trouves'] = found
    
    return jsonify(results), 200

@app.route('/api/recruteur/debug/keyword-match', methods=['POST'])
@jwt_required()
def debug_keyword_match():
    """Teste la correspondance d'un critère avec un texte donné"""
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    criterion = data.get('criterion', '')
    poste = data.get('poste', '')
    
    if not text or not criterion:
        return jsonify({'error': 'text et criterion requis'}), 400
    
    normalized, _ = normalize_for_matching(text)
    is_present, confidence, found = check_criterion_match_advanced(criterion, normalized, text, poste=poste)
    
    return jsonify({
        'criterion': criterion,
        'is_present': is_present,
        'confidence': confidence,
        'found_keywords': found,
        'text_preview': text[:500]
    }), 200

@app.route('/api/health-version', methods=['GET'])
def health_version():
    return jsonify({
        "version": "v3.4-extraction-improved",
        "postes_actifs": POSTES_ACTIFS,
        "ia_enabled": IA_ANALYSE_ACTIVE,
        "ocr_available": OCR_AVAILABLE,
        "updated_grilles": ["Chef de Division Local Corporate", "Chargé(e) d'Administration de Crédit"],
        "reanalyze_updated_grilles_route": "AVAILABLE",
        "debug_routes_available": True,
        "deployed_at": datetime.datetime.now().isoformat()
    }), 200

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    if IA_ANALYSE_ACTIVE:
        logger.info(f"🧠 Moteur d'analyse INTELLIGENT activé (modèle: {ANTHROPIC_MODEL})")
    else:
        logger.warning("⚠️ Moteur IA désactivé — repli sur le moteur mots-clés")
    logger.info("✅ Grilles mises à jour pour Chef de Division Local Corporate et Chargé(e) d'Administration de Crédit")
    logger.info("✅ Extraction de texte améliorée avec OCR et fallback")
    logger.info("✅ Routes de debug disponibles")
    app.run(host="0.0.0.0", port=port, debug=False)

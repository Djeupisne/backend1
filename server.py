# server.py - Backend Flask pour RecrutBank avec analyse automatique INTELLIGENTE
# ============================================================================
# ✅ CORRECTIONS MAJEURES :
#   1. ✅ Erreur 413 RÉSOLUE (MAX_CONTENT_LENGTH = 500MB pour 49+ dossiers)
#   2. ✅ Extraction texte ROBUSTE (tables PDF/DOCX, formatages complexes)
#   3. ✅ Distinction BANQUE COMMERCIALE vs MICROFINANCE vs HORS SECTEUR
#   4. ✅ Vérification COHÉRENCE CV vs Lettre de motivation
#   5. ✅ Validation emploi ACTUEL dans secteur financier
#   6. ✅ Critères EXACTS selon grille Word (Market Risk : compétences techniques requises)
#   7. ✅ Système "intelligent" qui raisonne comme un recruteur humain
#   8. ✅ Upload progressif en chunks pour gros volumes
# ============================================================================

from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
import os, hashlib, datetime, uuid, redis, json, re, threading, mimetypes, io, csv, unicodedata
from werkzeug.utils import secure_filename

# ── PARSING DOCUMENTS ──────────────────────────────────────────────────────
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    print("⚠️ pdfplumber non installé. Fallback sur PyPDF2.")

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
    print("⚠️ python-docx non installé. Extraction DOCX désactivée.")

# ── DÉTECTION ENCODAGE & LANGUE ───────────────────────────────────────────
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

# ── MATCHING FUZZY ────────────────────────────────────────────────────────
try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

# ── EXPORT PDF & EXCEL ────────────────────────────────────────────────────
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

app = Flask(__name__)

# ── CORS ──────────────────────────────────────────────────────────────────
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

# ── JWT ───────────────────────────────────────────────────────────────────
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

# ── REDIS ─────────────────────────────────────────────────────────────────
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis-11133.c8.us-east-1-4.ec2.cloud.redislabs.com"),
    port=int(os.getenv("REDIS_PORT", 11133)),
    username="default",
    password=os.getenv("REDIS_PASSWORD", "WKJdeilasGOWkXJWOHwqcRV7X5uWwQ"),
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5
)

# ── UPLOADS ───────────────────────────────────────────────────────────────
UPLOAD_FOLDER  = os.path.join(os.path.dirname(__file__), 'uploads')
REPORTS_FOLDER = os.path.join(os.path.dirname(__file__), 'reports')
CHUNKS_FOLDER  = os.path.join(UPLOAD_FOLDER, 'chunks')
os.makedirs(UPLOAD_FOLDER,  exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)
os.makedirs(CHUNKS_FOLDER,  exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}

# ✅ CORRECTION 413 : 500MB au lieu de 10MB (pour 49+ dossiers avec pièces jointes)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

def allowed_file(filename):
    """Vérifie si l'extension du fichier est autorisée."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ── POSTES ────────────────────────────────────────────────────────────────
POSTES = [
    "Responsable Administration de Crédit",
    "Analyste Crédit CCB",
    "Archiviste (Administration Crédit)",
    "Senior Finance Officer",
    "Market Risk Officer",
    "IT Réseau & Infrastructure"
]

# ══════════════════════════════════════════════════════════════════════════
# 📋 GRILLE DE PRÉSÉLECTION — EXACTEMENT selon document Word
# ══════════════════════════════════════════════════════════════════════════
GRILLE = {
    "Responsable Administration de Crédit": {
        "eliminatoire": [
            "Expérience bancaire",
            "Minimum 3 ans en crédit / risque (hors stage)",
            "Exposition aux garanties ou conformité"
        ],
        "a_verifier": [
            "Validation de dossiers de crédit",
            "Gestion des garanties",
            "Participation à des audits"
        ],
        "signaux_forts": [
            "IFRS 9",
            "COBAC / conformité",
            "Suivi portefeuille / impayés"
        ],
        "points_attention": [
            "Parcours trop comptable pur",
            "Rôle uniquement administratif sans responsabilité",
            "CV flou avec missions génériques"
        ]
    },
    "Analyste Crédit CCB": {
        "eliminatoire": [
            "Expérience en analyse crédit",
            "Capacité à lire des états financiers",
            "Minimum 3 ans institution financière (hors stage)"
        ],
        "a_verifier": [
            "Clients PME",
            "Clients particuliers",
            "Structuration de crédit",
            "Avis de crédit"
        ],
        "signaux_forts": [
            "Cash-flow analysis",
            "Montage de crédit",
            "Comités de crédit"
        ],
        "points_attention": [
            "CV trop relation client",
            "Aucune notion de risque",
            "Expériences très courtes sans progression"
        ]
    },
    "Archiviste (Administration Crédit)": {
        "eliminatoire": [
            "Expérience en gestion documentaire structurée",
            "Rigueur démontrée"
        ],
        "a_verifier": [
            "Archivage physique et électronique",
            "Gestion des dossiers sensibles"
        ],
        "signaux_forts": [
            "Expérience en banque ou juridique",
            "Manipulation de garanties ou contrats"
        ],
        "points_attention": [
            "Profils trop généralistes",
            "CV désorganisé"
        ]
    },
    "Senior Finance Officer": {
        "eliminatoire": [
            "Expérience en reporting financier structuré",
            "Exposition aux états financiers",
            "Interaction avec auditeurs",
            "Minimum 3 ans département finance ou en cabinet d'audit (hors stage)"
        ],
        "a_verifier": [
            "Production états financiers",
            "Reporting groupe",
            "Connaissance IFRS",
            "Contraintes réglementaires"
        ],
        "signaux_forts": [
            "IFRS / consolidation",
            "Reporting groupe",
            "Interaction avec CAC",
            "Outils SPECTRA / CERBER / ERP"
        ],
        "points_attention": [
            "Profil comptable junior amélioré",
            "Pas de responsabilité réelle",
            "CV flou sur les livrables"
        ]
    },
    "Market Risk Officer": {
        "eliminatoire": [
            "Base en risques de marché",
            "Compétences quantitatives",
            "Exposition à FX / taux / liquidité",
            "Minimum 3 ans institution financière (hors stage)"
        ],
        "a_verifier": [
            "Maîtrise VaR / stress testing",
            "Analyse des positions",
            "Excel avancé",
            "VBA ou Python"
        ],
        "signaux_forts": [
            "Bâle II / III",
            "Gestion ALM / liquidité",
            "Produits FICC",
            "Reporting risque"
        ],
        "points_attention": [
            "CV trop théorique académique",
            "Aucune mention d'outils",
            "Incapacité implicite à modéliser"
        ]
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": [
            "Expérience en réseau / infrastructure",
            "Exposition à environnement critique",
            "Notion de sécurité IT",
            "Minimum 2 ans expérience (hors stage)"
        ],
        "a_verifier": [
            "Gestion réseaux LAN/WAN/VPN",
            "Gestion serveurs Windows/Linux",
            "Cloud même basique",
            "Gestion des incidents",
            "Assurance de la disponibilité"
        ],
        "signaux_forts": [
            "Cybersécurité / firewall",
            "Haute disponibilité / PRA/PCA",
            "Gestion ATM ou systèmes bancaires",
            "Certifications Cisco ou Microsoft"
        ],
        "points_attention": [
            "Profil trop helpdesk",
            "CV sans détail technique",
            "Aucune mention de sécurité"
        ]
    }
}

# ══════════════════════════════════════════════════════════════════════════
# 🏦 BANQUES COMMERCIALES vs MICROFINANCE vs HORS SECTEUR
# ══════════════════════════════════════════════════════════════════════════

COMMERCIAL_BANKS = [
    'ecobank', 'orabank', 'uba', 'bicec', 'sgbc', 'cbc', 'bct',
    'société générale', 'standard chartered', 'nsia banque', 'commercial bank',
    'banque commerciale', 'investment bank', 'banque d affaires',
    'credit institution', 'financial institution', 'banque'
]

MICROFINANCE = [
    'microfinance', 'micro-finance', 'mfb', 'finadev', 'ucec',
    'caisse d epargne', 'credit union', 'cooperative financiere'
]

NON_FINANCIAL_SECTORS = [
    'logistics', 'logistique', 'transport', 'shipping',
    'retail', 'commerce', 'distribution', 'vente',
    'manufacturing', 'industrie', 'construction', 'btp',
    'agriculture', 'farming', 'agroalimentaire',
    'telecom', 'télécom', 'communication',
    'health', 'santé', 'hôpital', 'clinique', 'medical',
    'education', 'enseignement', 'école', 'université',
    'ngo', 'ong', 'association', 'humanitaire', 'world vision',
    'government', 'gouvernement', 'administration publique',
    'media', 'presse', 'journalisme',
    'tourism', 'tourisme', 'hôtel', 'hotel', 'restauration',
    'real estate', 'immobilier',
    'energy', 'énergie', 'oil', 'gaz', 'petrole', 'mining',
    'holding', 'groupe industriel', 'encobat'
]

COMMERCIAL_BANK_PATTERN = re.compile('|'.join(COMMERCIAL_BANKS), re.IGNORECASE)
MICROFINANCE_PATTERN = re.compile('|'.join(MICROFINANCE), re.IGNORECASE)
NON_FINANCIAL_PATTERN = re.compile('|'.join(NON_FINANCIAL_SECTORS), re.IGNORECASE)

# ══════════════════════════════════════════════════════════════════════════
# 🚫 MOTS DÉSIGNANT UN STAGE — les durées adjacentes sont EXCLUES
# ══════════════════════════════════════════════════════════════════════════
STAGE_MARKERS = [
    r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b',
    r'\bapprenti\b', r'\bapprentissage\b', r'\balternance\b',
    r'\bstage de fin\b', r'\bstage academique\b', r'\bstage professionnel\b',
    r'\bstage de formation\b', r'\bpfr\b', r'\bstage pfe\b',
    r'\bpfe\b', r'\bvolontariat\b', r'\btrainee\b',
]
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)

# ══════════════════════════════════════════════════════════════════════════
# 🚫 DÉTECTION PHRASES NÉGATIVES
# ══════════════════════════════════════════════════════════════════════════

NEGATIVE_PATTERNS = [
    r'\b(pas\s+de|pas\s+d\')\s*(expérience|experience|expérimenté|competence)\b',
    r'\b(aucun|aucune|aucuns|aucunes)\s*(expérience|experience|competence|connaissance)\b',
    r'\b(sans|dépourvu\s+de|manque\s+de)\s*(expérience|experience|competence)\b',
    r'\b(n\')?(?:ai|as|a|avons|avez|ont)\s+pas\s+(?:d\')?(expérience|experience|competence|connaissance)\b',
    r'\b(jamais\s+(?:eu|travaillé|exercé|pratiqué))\b',
    r'\b(peu\s+d\')?expérience\b',
    r'\b(expérience\s+(?:limitée|insuffisante|faible|partielle))\b',
    r'\b(ne\s+connais\s+pas|ne\s+maîtrise\s+pas|ne\s+possède\s+pas)\b',
    r'\b(no\s+experience|without\s+experience|lack\s+of\s+experience)\b',
]
NEGATIVE_REGEX = re.compile('|'.join(NEGATIVE_PATTERNS), re.IGNORECASE)

# ══════════════════════════════════════════════════════════════════════════
# 🧠 EXTRACTION TEXTE ROBUSTE (TABLES + FORMATAGES COMPLEXES)
# ══════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf_robust(filepath):
    """
    Extraction ROBUSTE depuis PDF - gère tables, colonnes, formatages complexes
    Comme un humain qui lit le document
    """
    text = ""
    
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    # ✅ Extraire les TABLES en premier (critique pour CV formatés)
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                if row:
                                    # Joindre les cellules avec séparateur clair
                                    row_text = ' | '.join([str(cell).strip() if cell else '' for cell in row])
                                    if row_text.strip():
                                        text += row_text + "\n"
                    
                    # ✅ Extraire texte normal avec tolérance
                    content = page.extract_text(
                        x_tolerance=3, y_tolerance=3,
                        keep_blank_chars=True, use_text_flow=True
                    )
                    if content:
                        text += content + "\n"
            
            if text.strip():
                return normalize_unicode(text.strip())
        except Exception as e:
            print(f"⚠️ pdfplumber erreur: {e}")

    # Fallback PyPDF2
    if PYPDF2_AVAILABLE:
        try:
            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    content = page.extract_text()
                    if content:
                        text += content + "\n"
            if text.strip():
                return normalize_unicode(text.strip())
        except Exception as e:
            print(f"⚠️ PyPDF2 erreur: {e}")

    # Fallback pdftotext
    try:
        import subprocess
        result = subprocess.run(
            ['pdftotext', '-layout', filepath, '-'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return normalize_unicode(result.stdout.strip())
    except Exception:
        pass

    return ""


def extract_text_from_docx_robust(filepath, raw_bytes=None):
    """
    Extraction ROBUSTE depuis DOCX - gère tables, en-têtes, commentaires
    """
    if not DOCX_AVAILABLE:
        return ""
    try:
        doc = Document(filepath)
        parts = []
        
        # Paragraphes
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                parts.append(t)
        
        # ✅ Tables (critique pour CV formatés comme ZEBKALBA)
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        
        # En-têtes et pieds de page
        for section in doc.sections:
            for element in (section.header.paragraphs + section.footer.paragraphs):
                t = element.text.strip()
                if t:
                    parts.append(t)
        
        # Commentaires
        try:
            for comment in doc.comments:
                t = comment.text.strip()
                if t:
                    parts.append(f"[Commentaire] {t}")
        except Exception:
            pass
        
        result = "\n".join(parts).strip()
        return normalize_unicode(result)
    except Exception as e:
        print(f"⚠️ Erreur lecture DOCX: {e}")
        if raw_bytes:
            try:
                text = re.sub(r'[^\x20-\x7E\u00C0-\u017F\u0400-\u04FF\u0600-\u06FF]+', ' ',
                             raw_bytes.decode('utf-8', errors='ignore'))
                return normalize_unicode(text.strip())
            except Exception:
                pass
        return ""


def extract_text_from_txt(filepath, raw_bytes=None):
    if raw_bytes and CHARDET_AVAILABLE:
        try:
            detected = chardet.detect(raw_bytes[:10000])
            encoding = detected['encoding'] or 'utf-8'
            return normalize_unicode(raw_bytes.decode(encoding, errors='ignore'))
        except Exception:
            pass
    for enc in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1', 'utf-16']:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return normalize_unicode(f.read().strip())
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def extract_text_robust(filepath, filename):
    if not filepath or not os.path.exists(filepath):
        print(f"⚠️ Fichier introuvable: {filepath}")
        return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    raw_bytes = None
    if CHARDET_AVAILABLE and ext in ('doc', 'docx', 'txt'):
        try:
            with open(filepath, 'rb') as f:
                raw_bytes = f.read()
        except Exception as e:
            print(f"⚠️ Erreur lecture binaire: {e}")
    if ext == 'pdf':
        return extract_text_from_pdf_robust(filepath)
    elif ext in ('doc', 'docx'):
        return extract_text_from_docx_robust(filepath, raw_bytes)
    elif ext == 'txt':
        return extract_text_from_txt(filepath, raw_bytes)
    if raw_bytes:
        try:
            return normalize_unicode(raw_bytes.decode('utf-8', errors='ignore').strip())
        except Exception:
            pass
    return ""

# ══════════════════════════════════════════════════════════════════════════
# 🧠 VALIDATION INTELLIGENTE (COMME UN RECRUTEUR HUMAIN)
# ══════════════════════════════════════════════════════════════════════════

def detect_institution_type(text):
    """
    Détecte le TYPE d'institution mentionnée dans le texte.
    Retourne: 'commercial_bank', 'microfinance', 'non_financial', ou 'unknown'
    """
    text_lower = text.lower()
    
    # ✅ Banque commerciale en premier (priorité)
    if COMMERCIAL_BANK_PATTERN.search(text_lower):
        if MICROFINANCE_PATTERN.search(text_lower):
            return 'microfinance'
        return 'commercial_bank'
    
    # Microfinance
    if MICROFINANCE_PATTERN.search(text_lower):
        return 'microfinance'
    
    # Secteur non financier
    if NON_FINANCIAL_PATTERN.search(text_lower):
        return 'non_financial'
    
    return 'unknown'


def check_current_employment_financial(cv_text):
    """
    Vérifie si l'emploi ACTUEL du candidat est dans le secteur financier.
    """
    current_patterns = [
        r'(?:depuis|from|since|à nos jours|to present|current|actuel)\s*[:\-]?\s*([^\n]+)',
        r'(\d{4})\s*[-–]\s*(?:présent|present|now|actuel|nos jours)',
        r'(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{4}\s*[-–]\s*(?:présent|present|now|actuel)'
    ]
    
    for pattern in current_patterns:
        matches = re.findall(pattern, cv_text, re.IGNORECASE)
        if matches:
            context = cv_text[max(0, cv_text.lower().find(str(matches[0]).lower()) - 200): 
                            cv_text.lower().find(str(matches[0]).lower()) + 200]
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
    """
    Vérifie la COHÉRENCE entre le CV et la lettre de motivation.
    """
    if poste == "Market Risk Officer":
        technical_keywords = [
            'var', 'value at risk', 'stress testing', 'trading',
            'alm', 'bâle', 'ficc', 'positions', 'modélisation',
            'quantitatif', 'quantitative', 'modeling'
        ]
        
        cv_lower = cv_text.lower()
        letter_lower = letter_text.lower() if letter_text else ""
        
        cv_matches = sum(1 for kw in technical_keywords if kw in cv_lower)
        letter_matches = sum(1 for kw in technical_keywords if kw in letter_lower)
        
        # ⚠️ Si beaucoup plus dans la lettre que dans le CV → INCOHÉRENT
        if letter_matches > 0 and cv_matches == 0:
            return False, "Claims Market Risk dans lettre mais ABSENTS du CV"
        
        if letter_matches > cv_matches * 2 and cv_matches < 2:
            return False, "Incohérence : mots-clés techniques absents du CV"
    
    return True, "Cohérent"


def validate_financial_institution_for_market_risk(text):
    """
    Pour Market Risk, exige une BANQUE COMMERCIALE (pas microfinance).
    """
    text_lower = text.lower()
    
    has_commercial = COMMERCIAL_BANK_PATTERN.search(text_lower)
    has_microfinance = MICROFINANCE_PATTERN.search(text_lower)
    has_non_financial = NON_FINANCIAL_PATTERN.search(text_lower)
    
    if has_non_financial and not has_commercial:
        return False, "Secteur non financier détecté"
    
    if has_microfinance and not has_commercial:
        return False, "Microfinance ≠ Banque commerciale (Market Risk requis)"
    
    if has_commercial:
        return True, "Banque commerciale détectée"
    
    return True, "Institution financière valide"

# ══════════════════════════════════════════════════════════════════════════
# 🔤 NORMALISATION TEXTE
# ══════════════════════════════════════════════════════════════════════════

_ACCENT_MAP = str.maketrans(
    'àâäéèêëîïôùûüçœæÀÂÄÉÈÊËÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ',
    'aaaeeeeiioouuucaaAAEEEEIIOUUUCAAaaonaaon'
)

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

# ══════════════════════════════════════════════════════════════════════════
# 🚫 DÉTECTION CONTEXTE NÉGATIF
# ══════════════════════════════════════════════════════════════════════════

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


def is_banking_context(text_window):
    if not text_window:
        return False
    
    text_lower = text_window.lower()
    
    if NON_FINANCIAL_PATTERN.search(text_lower):
        return False
    
    if COMMERCIAL_BANK_PATTERN.search(text_lower):
        return True
    
    return False


def is_it_critical_context(text_window):
    if not text_window:
        return False
    
    text_lower = text_window.lower()
    
    critical_pattern = re.compile('|'.join([
        'banque', 'bancaire', 'bank', 'banking',
        'telco', 'telecom', 'télécom', 'opérateur',
        'datacenter', 'centre de données', 'data center',
        'hébergement', 'hosting', 'cloud provider',
        'faa', 'gouvernement', 'ministère', 'défense',
        'hôpital', 'santé', 'critical infrastructure',
        'ecobank', 'orabank', 'uba', 'mtn', 'airtel', 'salam',
        'financial services', 'telecommunications', 'critical systems'
    ]), re.IGNORECASE)
    
    if critical_pattern.search(text_lower):
        return True
    
    return False


def check_criterion_context(criterion, raw_text, poste):
    text_lower = raw_text.lower()
    
    banking_posts = [
        "Responsable Administration de Crédit",
        "Analyste Crédit CCB",
        "Senior Finance Officer",
        "Market Risk Officer"
    ]
    
    if poste in banking_posts:
        banking_criteria = [
            "Expérience bancaire",
            "Minimum 3 ans en crédit / risque (hors stage)",
            "Exposition aux garanties ou conformité",
            "Minimum 3 ans institution financière (hors stage)",
            "Minimum 3 ans département finance ou en cabinet d'audit (hors stage)",
            "Expérience en analyse crédit",
            "Capacité à lire des états financiers",
            "Base en risques de marché",
            "Compétences quantitatives",
            "Exposition à FX / taux / liquidité",
            "Expérience en reporting financier structuré",
            "Exposition aux états financiers"
        ]
        
        if criterion in banking_criteria:
            banking_matches = list(COMMERCIAL_BANK_PATTERN.finditer(text_lower))
            
            if not banking_matches:
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
            critical_pattern = re.compile('|'.join([
                'banque', 'bancaire', 'bank', 'banking',
                'telco', 'telecom', 'télécom', 'opérateur',
                'datacenter', 'centre de données', 'data center',
                'hébergement', 'hosting', 'cloud provider',
                'faa', 'gouvernement', 'ministère', 'défense',
                'hôpital', 'santé', 'critical infrastructure',
                'ecobank', 'orabank', 'uba', 'mtn', 'airtel', 'salam',
                'financial services', 'telecommunications', 'critical systems'
            ]), re.IGNORECASE)
            
            critical_matches = list(critical_pattern.finditer(text_lower))
            
            if critical_matches:
                return True
            
            return False
    
    return True

# ══════════════════════════════════════════════════════════════════════════
# 📅 EXTRACTION ANNÉES D'EXPÉRIENCE
# ══════════════════════════════════════════════════════════════════════════

FRENCH_MONTHS = {
    'janvier': 1, 'jan': 1, 'février': 2, 'fevrier': 2, 'fev': 2,
    'mars': 3, 'mar': 3, 'avril': 4, 'avr': 4, 'mai': 5,
    'juin': 6, 'juillet': 7, 'juil': 7, 'août': 8, 'aout': 8, 'aou': 8,
    'septembre': 9, 'sep': 9, 'octobre': 10, 'oct': 10,
    'novembre': 11, 'nov': 11, 'décembre': 12, 'decembre': 12, 'dec': 12
}


def split_into_jobs(raw_text):
    separators = re.compile(
        r'(?:^|\n)(?=\s*(?:'
        r'(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|'
        r'jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s+'
        r'(?:20\d{2}|19\d{2})|'
        r'\d{1,2}[/\-\.](?:20\d{2}|19\d{2})|'
        r'(?:depuis|de |from |since |desde |a partir de |starting |beginning)'
        r'))',
        re.IGNORECASE | re.MULTILINE
    )
    blocks = separators.split(raw_text)
    return [b.strip() for b in blocks if b.strip()]


def is_stage_block(block_text):
    return bool(STAGE_PATTERN.search(block_text))


def extract_duration_years_from_block(block_text):
    years = 0.0
    text = block_text.lower()

    m = re.search(r'(\d+[\.,]?\d*)\s*(?:ans?|annee?s?|years?|años?|anos?)', text)
    if m:
        try:
            years = float(m.group(1).replace(',', '.'))
            return years
        except ValueError:
            pass

    pattern_present = re.compile(
        r'(?:(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|'
        r'jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s+)?'
        r'(20\d{2}|19\d{2})'
        r'\s*(?:à|-|–|—|au|jusqu\'au|to|until|au\s+)?'
        r'(?:aujourd\'hui|present|actuel|en cours|now|current|actual|hoje|ce jour)',
        re.IGNORECASE
    )
    m = pattern_present.search(text)
    if m:
        year_str = m.group(2)
        start_year = int(year_str)
        start_month = FRENCH_MONTHS.get((m.group(1) or '').lower(), 1)
        end_year = datetime.datetime.now().year
        end_month = datetime.datetime.now().month
        delta = (end_year - start_year) + (end_month - start_month) / 12.0
        if 0 < delta <= 40:
            return round(delta, 1)

    pattern_range = re.compile(
        r'(?:(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|'
        r'jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s+)?'
        r'(20\d{2}|19\d{2})'
        r'\s*(?:à|-|–|—|au|jusqu\'au|to|until)?\s*'
        r'(?:(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|'
        r'jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s+)?'
        r'(20\d{2}|19\d{2})',
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

    m = re.search(
        r'(\d{1,2})[/\-\.](20\d{2}|19\d{2})\s*[-–—\.]?\s*(?:(\d{1,2})[/\-\.])?(20\d{2}|19\d{2}|present|current|now)',
        text
    )
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
    
    banking_posts = [
        "Responsable Administration de Crédit",
        "Analyste Crédit CCB",
        "Senior Finance Officer",
        "Market Risk Officer"
    ]
    
    for block in blocks:
        if is_stage_block(block):
            continue
        
        if poste in banking_posts:
            if NON_FINANCIAL_PATTERN.search(block.lower()):
                print(f"    [EXP-] Bloc exclu (secteur non-financier): {block[:100]}...")
                continue
            if poste == "Market Risk Officer":
                if MICROFINANCE_PATTERN.search(block.lower()) and not COMMERCIAL_BANK_PATTERN.search(block.lower()):
                    print(f"    [EXP-] Bloc exclu (microfinance ≠ banque commerciale): {block[:100]}...")
                    continue
        elif poste == "IT Réseau & Infrastructure":
            critical_pattern = re.compile('|'.join([
                'banque', 'bancaire', 'bank', 'banking',
                'telco', 'telecom', 'télécom', 'opérateur',
                'datacenter', 'centre de données', 'data center',
                'hébergement', 'hosting', 'cloud provider',
                'faa', 'gouvernement', 'ministère', 'défense',
                'hôpital', 'santé', 'critical infrastructure',
                'ecobank', 'orabank', 'uba', 'mtn', 'airtel', 'salam',
                'financial services', 'telecommunications', 'critical systems'
            ]), re.IGNORECASE)
            if not critical_pattern.search(block.lower()):
                print(f"    [EXP-] Bloc exclu (pas environnement IT critique): {block[:100]}...")
                continue
        
        if domain_keywords:
            if any(contains_negative_context(block, kw) for kw in domain_keywords):
                continue
            norm_block, _ = normalize_for_matching(block)
            if not any(kw in norm_block and not contains_negative_context(block, kw) 
                      for kw in domain_keywords):
                continue
                
        duration = extract_duration_years_from_block(block)
        if duration > 0:
            total_years += duration
            print(f"    [EXP+] Bloc valide: +{duration} ans (total: {total_years})")
    
    result = total_years >= min_years
    print(f"    [EXP] Total années: {total_years} | Requis: {min_years} | Validé: {result}")
    return result

# ══════════════════════════════════════════════════════════════════════════
# 🧠 MAPPING MOTS-CLÉS
# ══════════════════════════════════════════════════════════════════════════

KEYWORD_MAPPING = {
    "Expérience bancaire": [
        "banque", "bancaire", "etablissement bancaire", "institution bancaire",
        "banque commerciale", "microfinance", "etablissement financier",
        "institution financiere", "secteur bancaire", "groupe bancaire",
        "filiale bancaire", "bank", "banking", "financial institution",
        "credit institution", "commercial bank", "ecobank", "orabank", "uba"
    ],
    "Minimum 3 ans en crédit / risque (hors stage)": ["EXP_CREDIT_3ANS"],
    "Exposition aux garanties ou conformité": [
        "garantie", "garanties", "nantissement", "hypotheque", "surete",
        "suretes", "conformite", "compliance", "cobac", "bceao", "bcac",
        "commission bancaire", "reglementation bancaire", "audit", "controle interne",
        "collateral", "regulatory", "guarantee", "guarantees",
        "compliance officer", "regulatory compliance", "internal control"
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
        "ifrs 9", "ias 39", "normes ifrs", "comptabilite ifrs",
        "ifrs9", "provisionnement ifrs", "international financial reporting",
        "ifrs standards", "impairment ifrs 9"
    ],
    "COBAC / conformité": [
        "cobac", "conformite bancaire", "bceao", "bcac",
        "commission bancaire", "regulation bancaire", "compliance",
        "banking regulation", "central bank", "banking authority"
    ],
    "Suivi portefeuille / impayés": [
        "portefeuille credit", "impayes", "recouvrement", "contentieux",
        "encours", "suivi portefeuille", "creances douteuses", "npls",
        "portfolio monitoring", "non-performing loans", "loan portfolio",
        "collections", "past due", "default management"
    ],
    "Expérience en analyse crédit": [
        "analyse credit", "credit analysis", "evaluation credit",
        "scoring credit", "analyse financiere credit", "instruction credit",
        "analyste credit", "octroi credit", "loan analysis",
        "credit analyst", "credit assessment", "credit evaluation"
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
        "pme", "petite entreprise", "moyenne entreprise", "tpe", "entreprise cliente",
        "sme", "small business", "mid-market", "small and medium enterprises"
    ],
    "Clients particuliers": [
        "particulier", "clientele particuliere", "retail banking", "client particulier",
        "retail", "personal banking", "individual clients", "consumer banking"
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
    ],
    "Expérience en gestion documentaire structurée": [
        "gestion documentaire", "archivage", "ged", "records management",
        "classement", "documentation", "gestion archives", "archiviste",
        "document management", "filing system", "document control",
        "records keeping", "archive management"
    ],
    "Rigueur démontrée": [
        "rigueur", "methode", "organisation", "procedures", "tracabilite",
        "precision", "fiabilite", "serieux", "attention to detail",
        "meticulous", "accuracy", "precision", "thoroughness"
    ],
    "Archivage physique et électronique": [
        "archivage physique", "archivage electronique", "dematerialisation",
        "numerisation", "archivage numerique", "scan", "ged",
        "physical archiving", "digital archiving", "electronic filing",
        "scanning", "digitization", "document imaging"
    ],
    "Gestion des dossiers sensibles": [
        "dossier sensible", "confidentiel", "securise", "acces restreint",
        "donnees sensibles", "confidentialite", "confidential documents",
        "sensitive files", "restricted access", "classified documents"
    ],
    "Expérience en banque ou juridique": [
        "banque", "etablissement financier", "juridique", "droit bancaire",
        "secteur bancaire", "cabinet juridique", "etude notariale",
        "banking", "legal", "law firm", "legal department", "banking sector"
    ],
    "Manipulation de garanties ou contrats": [
        "garantie", "contrat", "convention", "acte juridique",
        "documentation juridique", "acte notarie", "contracts", "legal documents",
        "guarantees", "legal agreements", "contract management"
    ],
    "Expérience en reporting financier structuré": [
        "reporting financier", "reporting", "tableau de bord", "kpi",
        "indicateurs financiers", "etats financiers", "production reporting",
        "financial reporting", "management reporting", "financial dashboard",
        "financial metrics", "performance reporting",
        "rapport financier", "rapports financiers", "production de rapports",
        "rapport de gestion", "rapport mensuel", "rapport annuel"
    ],
    "Exposition aux états financiers": [
        "etats financiers", "bilan", "compte de resultat",
        "consolidation", "reporting financier", "liasse",
        "financial statements", "balance sheet", "income statement",
        "consolidated accounts", "financial reporting"
    ],
    "Interaction avec auditeurs": [
        "auditeur", "audit", "commissaire aux comptes", "cac",
        "audit externe", "commissariat aux comptes", "revue externe",
        "external auditor", "statutory audit", "audit firm",
        "external audit", "audit interaction"
    ],
    "Minimum 3 ans département finance ou en cabinet d'audit (hors stage)": ["EXP_FINANCE_3ANS"],
    "Production états financiers": [
        "production etats financiers", "elaboration etats financiers",
        "etablissement etats financiers", "cloture comptable", "cloture",
        "financial statements preparation", "accounting close",
        "financial close", "month-end close"
    ],
    "Reporting groupe": [
        "reporting groupe", "reporting consolide", "consolidation groupe",
        "reporting mensuel", "pack de gestion", "group reporting",
        "consolidated reporting", "corporate reporting", "group accounts",
        "rapport groupe", "rapports consolidés", "rapport de consolidation",
        "rapport corporate", "rapport mensuel groupe"
    ],
    "Connaissance IFRS": [
        "ifrs", "normes internationales", "ias", "comptabilite internationale",
        "international accounting standards", "ifrs standards",
        "international financial reporting standards"
    ],
    "Contraintes réglementaires": [
        "reglementation", "contraintes reglementaires", "conformite",
        "reglementaire", "prudentiel", "regulatory requirements",
        "compliance requirements", "regulatory compliance", "prudential"
    ],
    "IFRS / consolidation": [
        "ifrs", "consolidation", "comptes consolides", "normes ifrs",
        "consolidated accounts", "group consolidation", "ifrs consolidation"
    ],
    "Interaction avec CAC": [
        "cac", "commissaire aux comptes", "audit legal", "audit externe",
        "statutory auditor", "external auditor", "audit firm"
    ],
    "Outils SPECTRA / CERBER / ERP": [
        "spectra", "cerber", "erp", "sap", "oracle", "sage",
        "outil de gestion", "logiciel comptable", "enterprise software",
        "accounting software", "financial systems", "erp systems"
    ],
    "Base en risques de marché": [
        "risque marche", "market risk", "risques de marche",
        "gestion risques de marche", "risque financier", "trading risk",
        "market risk management", "trading risks", "financial risk",
        "risque de marché", "risques marché"
    ],
    "Compétences quantitatives": [
        "quantitatif", "quantitative", "mathematiques", "statistiques",
        "modelisation", "mathematiques financieres", "quantitative analysis",
        "modeling", "statistics", "mathematical", "quant"
    ],
    "Exposition à FX / taux / liquidité": [
        "fx", "change", "taux", "liquidite", "forex",
        "taux d interet", "risque de liquidite", "risque de change",
        "foreign exchange", "interest rate", "liquidity risk",
        "fx risk", "rate risk", "funding liquidity", "taux de change"
    ],
    "Maîtrise VaR / stress testing": [
        "var", "value at risk", "stress testing", "back testing",
        "backtesting", "scenario de stress", "value-at-risk",
        "stress test", "var model", "risk modeling", "value à risque"
    ],
    "Analyse des positions": [
        "analyse des positions", "suivi des positions",
        "analyse portefeuille", "exposition", "position monitoring",
        "position analysis", "portfolio analysis", "exposure monitoring"
    ],
    "Excel avancé": [
        "excel avance", "excel", "vba", "macros excel", "pivot",
        "tableaux croises", "power query", "advanced excel",
        "excel modeling", "spreadsheet", "excel functions"
    ],
    "VBA ou Python": [
        "vba", "python", "programmation", "scripting", "r statistical",
        "visual basic", "data analysis", "programming", "coding",
        "quantitative programming", "financial modeling"
    ],
    "Bâle II / III": [
        "bale ii", "bale iii", "bale 2", "bale 3", "basel ii", "basel iii",
        "accords de bale", "reglementation bale", "basel framework",
        "basel accords", "basel regulations", "capital requirements"
    ],
    "Gestion ALM / liquidité": [
        "alm", "asset liability management", "liquidite",
        "gestion alm", "actif passif", "gap de liquidite",
        "asset-liability management", "liquidity management", "alm framework"
    ],
    "Produits FICC": [
        "ficc", "produits derives", "commodities", "matieres premieres",
        "produits de taux", "taux", "fixed income", "derivatives",
        "fixed income currencies commodities", "bond", "rates"
    ],
    "Reporting risque": [
        "reporting risque", "rapport de risque", "tableau de bord risque",
        "reporting des risques", "risk reporting", "risk dashboard",
        "risk metrics", "risk reports", "rapport risques", "rapports de risques"
    ],
    "Expérience en réseau / infrastructure": [
        "reseau", "infrastructure", "lan", "wan", "vpn", "wlan", "sd-wan",
        "infrastructure it", "network", "reseaux", "networking",
        "routeur", "switch", "ospf", "eigrp", "bgp", "glbp",
        "cisco", "mikrotik", "ubiquiti", "fortinet", "palo alto",
        "router", "network infrastructure", "it infrastructure"
    ],
    "Exposition à environnement critique": [
        "banque", "telco", "telecom", "datacenter", "centre de donnees",
        "environnement critique", "secteur bancaire", "haute disponibilite",
        "critical infrastructure", "mission critical", "bad", "orabank",
        "ecobank", "uba", "unicef", "assurances", "financial services",
        "telecommunications", "data center", "critical systems"
    ],
    "Notion de sécurité IT": [
        "securite it", "cybersecurite", "securite informatique",
        "firewall", "securite reseau", "ids", "ips", "siem", "soar",
        "it security", "cybersecurity", "network security", "antimalware",
        "antivirus", "anti-spam", "cisco security", "cyberops",
        "information security", "security protocols"
    ],
    "Minimum 2 ans expérience (hors stage)": ["EXP_IT_2ANS"],
    "Gestion réseaux LAN/WAN/VPN": [
        "lan", "wan", "vpn", "reseaux locaux", "reseau local",
        "virtual private network", "switch", "routeur", "ospf", "eigrp",
        "bgp", "glbp", "sd-wan", "wlan", "interconnexion",
        "local area network", "wide area network", "network management"
    ],
    "Gestion serveurs Windows/Linux": [
        "windows server", "linux", "serveurs", "administration serveurs",
        "unix", "active directory", "debian", "ubuntu server", "vmware",
        "esxi", "hyper-v", "virtualbox", "virtualisation",
        "server administration", "server management", "virtualization"
    ],
    "Cloud même basique": [
        "cloud", "aws", "azure", "google cloud", "cloud computing",
        "iaas", "saas", "ovh", "hosting", "amen", "lws", "starlink",
        "cloud services", "cloud platform", "cloud infrastructure"
    ],
    "Gestion des incidents": [
        "incident", "gestion incidents", "support technique",
        "resolution incident", "itil", "ticketing", "prtg", "nagios",
        "zabbix", "supervision", "monitoring", "incident management",
        "technical support", "helpdesk", "service desk"
    ],
    "Assurance de la disponibilité": [
        "disponibilite", "haute disponibilite", "sla",
        "uptime", "continuite service", "availability",
        "high availability", "service level agreement", "failover",
        "system availability", "uptime monitoring", "service continuity"
    ],
    "Cybersécurité / firewall": [
        "cybersecurite", "firewall", "securite", "ids",
        "ips", "siem", "pentest", "vulnerability",
        "cybersecurity", "intrusion detection", "soar",
        "security firewall", "network security", "threat detection"
    ],
    "Haute disponibilité / PRA/PCA": [
        "haute disponibilite", "pra", "pca", "plan de reprise",
        "continuite activite", "disaster recovery", "basculement",
        "business continuity", "disaster recovery plan", "failover",
        "backup", "recovery plan", "business continuity plan"
    ],
    "Gestion ATM ou systèmes bancaires": [
        "atm", "systemes bancaires", "gab", "distributeur automatique",
        "systeme bancaire core", "temenos", "flexcube",
        "banking systems", "core banking", "interconnexion gab",
        "atm management", "banking core systems", "payment systems"
    ],
    "Certifications Cisco ou Microsoft": [
        "ccna", "ccnp", "ccie", "cisco", "microsoft certified",
        "mcse", "network+", "certification reseau",
        "cisco certification", "microsoft certification", "encor", "350-401",
        "it certifications", "professional certifications"
    ]
}

DOMAIN_KEYWORDS_MAP = {
    "EXP_CREDIT_3ANS": [
        "credit", "risque", "banque", "bancaire", "institution financiere",
        "analyste", "charge", "gestionnaire", "loan", "credit analysis"
    ],
    "EXP_FIN_3ANS": [
        "finance", "comptable", "comptabilite", "reporting", "tresorerie",
        "banque", "institution financiere", "auditeur", "controleur",
        "financial", "accounting"
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
}

EXP_MIN_YEARS_MAP = {
    "EXP_CREDIT_3ANS":   3.0,
    "EXP_FIN_3ANS":      3.0,
    "EXP_FINANCE_3ANS":  3.0,
    "EXP_IT_2ANS":       2.0,
}

# ══════════════════════════════════════════════════════════════════════════
# 🧠 VÉRIFICATION CRITÈRE
# ══════════════════════════════════════════════════════════════════════════

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

    if poste:
        if not check_criterion_context(criterion, raw_full_text, poste):
            print(f"    [CTX-] {criterion}: Échec contexte sectoriel pour {poste}")
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
            if ratio >= 90:
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}~{ratio/100:.2f}")
                    best_score = max(best_score, ratio / 100)
                    continue
                    
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.8):
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                    best_score = max(best_score, len(common) / len(kw_tokens))

    return best_score >= 0.75, round(best_score, 2), found_kws

# ══════════════════════════════════════════════════════════════════════════
# 🧠 MOTEUR D'ANALYSE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════

DEBUG_EXTRACTION = os.getenv("DEBUG_EXTRACTION", "false").lower() == "true"


def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    if not cv_text or len(cv_text.strip()) < 50:
        return {
            'score': 0,
            'checklist': {},
            'flags_eliminatoires': ['CV non analysable (trop court ou vide)'],
            'signaux_detectes': [],
            'details': {'error': 'CV vide ou non parsé'},
            'score_breakdown': {
                'bloc1_eliminatoire': True,
                'score_final': 0,
                'note': 'CV non analysable'
            }
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

    all_att_raw  = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full     = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw
    normalized   = normalize_for_matching(raw_full)[0]
    detected_lang = detect_language(cv_text[:500]) if cv_text else None

    print(f"🌐 Langue détectée: {detected_lang or 'indéterminée'} pour poste: {poste}")

    # 🔍 VÉRIFICATIONS INTELLIGENTES PRÉLIMINAIRES
    intelligent_flags = []
    
    is_consistent, consistency_reason = check_cv_letter_consistency(cv_text, lettre_text or "", poste)
    if not is_consistent:
        intelligent_flags.append(f"⚠️ {consistency_reason}")
    
    current_financial, current_reason = check_current_employment_financial(cv_text)
    if not current_financial:
        intelligent_flags.append(f"⚠️ {current_reason}")
    
    if poste == "Market Risk Officer":
        inst_valid, inst_reason = validate_financial_institution_for_market_risk(cv_text)
        if not inst_valid:
            intelligent_flags.append(f"⚠️ {inst_reason}")

    if DEBUG_EXTRACTION:
        print(f"\n{'='*70}\n🔍 DEBUG: {poste}")
        print(f"📄 CV extrait ({len(cv_text)} chars):\n{cv_text[:1200]}")
        print(f"\n🎯 Critères éliminatoires:")
        for crit in grille['eliminatoire']:
            ok, conf, found = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
            print(f"   {'✅' if ok else '❌'} {crit} (conf: {conf:.0%}) → {found}")
        print(f"{'='*70}\n")

    checklist    = {}
    flags_elim   = []
    signaux      = []
    points_bloc2 = 0
    points_bloc3 = 0
    details = {
        'cv_words': len(cv_text.split()),
        'lettre_words': len((lettre_text or "").split()),
        'attestation_words': len(all_att_raw.split()),
        'detected_language': detected_lang,
        'criteres_valides_bloc2':  [],
        'signaux_valides_bloc3':   [],
        'alertes_attention':       intelligent_flags,
        'matching_details':        {},
        'documents_analyses': {
            'cv':          len(cv_text) > 0,
            'lettre':      len(lettre_text or "") > 0,
            'certificats': len(attestation_texts_list) if attestation_texts_list else 0
        }
    }

    eliminatoire_failed = False
    
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        
        original_keywords = None
        if detected_lang and detected_lang in {'en', 'es', 'pt'}:
            original_keywords = KEYWORD_MAPPING.get(crit, [])
            
        is_present, confidence, found_kws = check_criterion_match_advanced(
            crit, normalized, raw_full, poste=poste
        )
        
        if detected_lang and detected_lang in {'en', 'es', 'pt'} and original_keywords:
            KEYWORD_MAPPING[crit] = original_keywords
            
        checklist[key] = is_present
        
        if not is_present:
            eliminatoire_failed = True
            flags_elim.append(f"❌ {crit} (confiance: {confidence:.0%})")
            details['alertes_attention'].append(f"🔴 Éliminatoire manquant: {crit}")
            details['matching_details'][crit] = {
                'found': False, 
                'confidence': confidence,
                'language': detected_lang,
                'status': 'ÉLIMINATOIRE — critère requis NON vérifié',
                'keywords_searched': KEYWORD_MAPPING.get(crit, [])[:5],
                'reason': 'Contexte sectoriel non vérifié' if crit in grille['eliminatoire'] else 'Critère non trouvé'
            }
        else:
            details['matching_details'][crit] = {
                'found': True, 
                'confidence': confidence,
                'language': detected_lang, 
                'status': 'VALIDÉ', 
                'matched': found_kws
            }
    
    if eliminatoire_failed:
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
                'bloc2_criteres_valides': 0,
                'bloc2_points': 0,
                'bloc3_signaux_detectes': 0,
                'bloc3_points': 0,
                'total_raw_points': 0,
                'score_final': 0,
                'note': f"ÉLIMINÉ : {len(flags_elim)} critère(s) éliminatoire(s) non vérifié(s)",
                'documents_analyses': details['documents_analyses']
            }
        }

    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        original_keywords = None
        if detected_lang and detected_lang in {'en', 'es', 'pt'}:
            original_keywords = KEYWORD_MAPPING.get(crit, [])
        is_present, confidence, found_kws = check_criterion_match_advanced(
            crit, normalized, raw_full, poste=poste
        )
        if detected_lang and detected_lang in {'en', 'es', 'pt'} and original_keywords:
            KEYWORD_MAPPING[crit] = original_keywords
        checklist[key] = is_present
        details['matching_details'][crit] = {
            'found': is_present, 'confidence': confidence,
            'matched': found_kws if is_present else []
        }
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"🟠 {crit}")

    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        original_keywords = None
        if detected_lang and detected_lang in {'en', 'es', 'pt'}:
            original_keywords = KEYWORD_MAPPING.get(crit, [])
        is_present, confidence, found_kws = check_criterion_match_advanced(
            crit, normalized, raw_full, poste=poste
        )
        if detected_lang and detected_lang in {'en', 'es', 'pt'} and original_keywords:
            KEYWORD_MAPPING[crit] = original_keywords
        checklist[key] = is_present
        details['matching_details'][crit] = {
            'found': is_present, 'confidence': confidence,
            'matched': found_kws if is_present else []
        }
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

    adequation    = min(3, len([k for k, v in checklist.items() if k.startswith('elim_') and v]))
    coherence     = min(2, points_bloc2)
    risque_metier = min(3, len(signaux))
    qualite_cv    = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre_motiv  = 1 if lettre_text and len(lettre_text.strip()) > 50 else 0
    score_final   = min(10, adequation + coherence + risque_metier + qualite_cv + lettre_motiv)

    return {
        'score': score_final,
        'checklist': checklist,
        'flags_eliminatoires': [],
        'signaux_detectes': signaux,
        'details': details,
        'score_breakdown': {
            'bloc1_eliminatoire':       False,
            'flags_eliminatoires_count': 0,
            'adequation_experience':    adequation,
            'coherence_parcours':       coherence,
            'exposition_risque_metier': risque_metier,
            'qualite_cv':               qualite_cv,
            'lettre_motivation':        lettre_motiv,
            'bloc2_criteres_valides':   len(details['criteres_valides_bloc2']),
            'bloc2_points':             points_bloc2,
            'bloc3_signaux_detectes':   len(signaux),
            'bloc3_points':             points_bloc3,
            'total_raw_points':         points_bloc2 + points_bloc3,
            'score_final':              score_final,
            'note': f"Score Excel: {score_final}/10",
            'documents_analyses': details['documents_analyses']
        }
    }


def normalize_text_for_matching(text):
    return normalize_for_matching(text)[0]

# ══════════════════════════════════════════════════════════════════════════
# 🔄 ANALYSE ASYNCHRONE
# ══════════════════════════════════════════════════════════════════════════

def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste):
    try:
        key = f"candidat:{token}"
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []

        cv_path  = os.path.join(UPLOAD_FOLDER, cv_filename) if cv_filename else None
        cv_text  = extract_text_robust(cv_path, cv_filename) if cv_path else ""
        lm_path  = os.path.join(UPLOAD_FOLDER, lettre_filename) if lettre_filename else None
        lm_text  = extract_text_robust(lm_path, lettre_filename) if lm_path else ""

        att_texts = []
        for fn in (attestation_filenames or []):
            ap = os.path.join(UPLOAD_FOLDER, fn)
            if os.path.exists(ap):
                t = extract_text_robust(ap, fn)
                if t:
                    att_texts.append(t)

        print(f"📄 Analyse {token}: CV={len(cv_text)}c, LM={len(lm_text)}c, Certs={len(att_texts)}f")
        if DEBUG_EXTRACTION and cv_text:
            print(f"🔍 TEXTE EXTRAIT CV ({token}):\n{cv_text[:1500]}")

        result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)

        redis_client.hset(key, mapping={
            "score":               str(result['score']),
            "checklist":           json.dumps(result['checklist'],             ensure_ascii=False),
            "flags_eliminatoires": json.dumps(result['flags_eliminatoires'],   ensure_ascii=False),
            "signaux_detectes":    json.dumps(result['signaux_detectes'],      ensure_ascii=False),
            "analyse_details":     json.dumps(result['details'],               ensure_ascii=False),
            "score_breakdown":     json.dumps(result['score_breakdown'],       ensure_ascii=False),
            "analyse_auto_date":   datetime.datetime.now().isoformat(),
            "analyse_status":      "completed"
        })

        tag = "⚠️ ÉLIMINÉ" if result['score_breakdown'].get('bloc1_eliminatoire') else "✅"
        print(f"{tag} Score {token}: {result['score']}/10 — {result['score_breakdown'].get('note','')}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        redis_client.hset(f"candidat:{token}", mapping={
            "analyse_status":    "error",
            "analyse_error":     str(e),
            "analyse_auto_date": datetime.datetime.now().isoformat()
        })

# ══════════════════════════════════════════════════════════════════════════
# 🏆 CLASSEMENT
# ══════════════════════════════════════════════════════════════════════════

def get_recommandation_from_score(score):
    s = int(score)
    if s >= 8: return "🥇 Entretien prioritaire"
    if s >= 6: return "🥈 Entretien si besoin"
    return "❌ Rejet"


def get_recommandation_color(score):
    s = int(score)
    if s >= 8:
        return "00FF00"
    elif s >= 6:
        return "FFA500"
    else:
        return "FF0000"


def calculate_ranking_score(c, poste):
    sb = c.get('score_breakdown_parsed', {})
    if sb.get('bloc1_eliminatoire'):
        return -999
    score         = int(c.get('score', 0))
    signaux_count = len(c.get('signaux_detectes_parsed', []))
    criteres_ok   = sb.get('bloc2_criteres_valides', 0)
    lettre_bonus  = 0.1 if c.get('lettre_filename') else 0
    try:
        days = (datetime.datetime.now() -
                datetime.datetime.fromisoformat(c.get('date_candidature', ''))).days
        date_bonus = max(0, (30 - min(days, 30)) * 0.01)
    except Exception:
        date_bonus = 0
    return round(score + signaux_count * 0.5 + criteres_ok * 0.2 + lettre_bonus + date_bonus, 3)


def generate_ranking_for_poste(poste, candidats_data):
    pool = [c for c in candidats_data if c.get('poste') == poste]
    for c in pool:
        c['ranking_score']    = calculate_ranking_score(c, poste)
        c['ranking_position'] = 0
    pool.sort(key=lambda x: (
        -x['ranking_score'],
        -len(x.get('signaux_detectes_parsed', [])),
        -x.get('score_breakdown_parsed', {}).get('bloc2_criteres_valides', 0),
        x.get('date_candidature', '')
    ))
    for idx, c in enumerate(pool, 1):
        c['ranking_position']       = idx
        c['ranking_recommendation'] = get_recommandation_from_score(c.get('score', 0))
    return pool

# ══════════════════════════════════════════════════════════════════════════
# 📊 EXPORT EXCEL
# ══════════════════════════════════════════════════════════════════════════

def generate_excel_report(candidats_data, poste_filter=None):
    if not OPENPYXL_AVAILABLE:
        return None

    wb = Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']

    if poste_filter and poste_filter in POSTES:
        postes_to_export = [poste_filter]
    else:
        postes_to_export = list(dict.fromkeys(
            c.get('poste', '') for c in candidats_data if c.get('poste') in POSTES
        ))
    
    if not postes_to_export:
        ws = wb.create_sheet(title="Aucune donnée")
        ws['A1'] = "Aucune candidature trouvée"
        ws['A1'].font = Font(bold=True, size=14)
    else:
        for poste in postes_to_export:
            candidats_poste = generate_ranking_for_poste(
                poste, [c for c in candidats_data if c.get('poste') == poste]
            )
            
            sheet_name = poste[:28] if len(poste) > 31 else poste
            ws = wb.create_sheet(title=sheet_name)

            hfill  = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
            hfont  = Font(color="000000", bold=True, size=11)
            border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin')
            )

            ws.merge_cells('A1:L1')
            c = ws['A1']
            c.value = f"CANDIDATURES - {poste}"
            c.font = Font(bold=True, size=14, color="000000")
            c.alignment = Alignment(horizontal='center', vertical='center')
            c.fill = hfill
            ws.row_dimensions[1].height = 30

            headers = [
                'Rang', 'N° Dossier', 'Email', 'Candidat', 'Téléphone',
                'Adéquation (0-3)', 'Cohérence (0-2)', 'Risque métier (0-3)',
                'Qualité CV (0-1)', 'Lettre (0-1)', 'Score /10', 'Recommandation'
            ]
            for col, h in enumerate(headers, 1):
                cell = ws.cell(row=3, column=col, value=h)
                cell.font = hfont
                cell.fill = hfill
                cell.border = border
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

            for row_i, cand in enumerate(candidats_poste, 4):
                sb = cand.get('score_breakdown_parsed', {})
                elim = sb.get('bloc1_eliminatoire', False)
                adeq = sb.get('adequation_experience', 0) if not elim else 0
                cohe = sb.get('coherence_parcours', 0) if not elim else 0
                risq = sb.get('exposition_risque_metier', 0) if not elim else 0
                qcv = sb.get('qualite_cv', 0) if not elim else 0
                lm = sb.get('lettre_motivation', 0) if not elim else 0
                total = adeq + cohe + risq + qcv + lm
                rang = cand.get('ranking_position', row_i - 3)
                nom_c = f"{cand.get('prenom', '')} {cand.get('nom', '')}".strip()
                reco = cand.get('ranking_recommendation', get_recommandation_from_score(total))
                num_dos = cand.get('numero_dossier', '') or '–'

                row_data = [
                    rang, num_dos, cand.get('email', '') or '–', nom_c,
                    cand.get('telephone', '') or '–',
                    adeq, cohe, risq, qcv, lm, total, reco
                ]

                for col, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_i, column=col, value=val if val is not None else '')
                    cell.border = border
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                    
                    if col == 12:
                        rec_color = get_recommandation_color(total)
                        cell.font = Font(bold=True, color="000000")
                        if rec_color == "00FF00":
                            cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                        elif rec_color == "FFA500":
                            cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
                        else:
                            cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

            col_widths = [8, 20, 35, 35, 20, 15, 15, 20, 15, 15, 12, 25]
            for col, w in enumerate(col_widths, 1):
                ws.column_dimensions[get_column_letter(col)].width = w
            for row in range(3, ws.max_row + 1):
                ws.row_dimensions[row].height = 25

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════════════════════
# 📄 EXPORT CSV
# ══════════════════════════════════════════════════════════════════════════

def generate_csv_report(candidats_data, poste_filter=None):
    out = io.StringIO()
    w = csv.writer(out, delimiter=';', quoting=csv.QUOTE_ALL, quotechar='"')

    headers = [
        'Rang', 'N° Dossier', 'Email', 'Nom', 'Prénom', 'Téléphone',
        'Poste', 'Date candidature', 'Score (/10)', 'Statut', 'Éliminatoire',
        'Adéquation (0-3)', 'Cohérence (0-2)', 'Risque (0-3)', 'Note', 'Recommandation'
    ]
    w.writerow(headers)
    
    if poste_filter and poste_filter in POSTES:
        candidats_filtered = [c for c in candidats_data if c.get('poste') == poste_filter]
    else:
        candidats_filtered = candidats_data

    candidats_filtered.sort(key=lambda x: (x.get('poste', ''), x.get('date_candidature', '')), reverse=True)

    for idx, c in enumerate(candidats_filtered, 1):
        sb = c.get('score_breakdown_parsed', {})
        score = int(c.get('score', 0))
        reco = get_recommandation_from_score(score)
        w.writerow([
            str(idx),
            str(c.get('numero_dossier', '') or '–'),
            str(c.get('email', '') or '–'),
            str(c.get('nom', '') or ''),
            str(c.get('prenom', '') or ''),
            str(c.get('telephone', '') or '–'),
            str(c.get('poste', '') or ''),
            str(c.get('date_candidature', '') or ''),
            str(c.get('score', '0')),
            str(c.get('statut', '') or ''),
            'OUI' if sb.get('bloc1_eliminatoire') else 'NON',
            str(sb.get('adequation_experience', 0)),
            str(sb.get('coherence_parcours', 0)),
            str(sb.get('exposition_risque_metier', 0)),
            str(sb.get('note', '') or ''),
            str(reco)
        ])
    
    out.seek(0)
    return out.getvalue()

# ══════════════════════════════════════════════════════════════════════════
# 📕 EXPORT PDF
# ══════════════════════════════════════════════════════════════════════════

def generate_pdf_report(candidats_data, poste_filter=None):
    if not REPORTLAB_AVAILABLE:
        return None
        
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            rightMargin=1*cm, leftMargin=1*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    els = []
    sty = getSampleStyleSheet()
    
    if poste_filter:
        rapport_type = f"CANDIDATURES - {poste_filter}"
    else:
        rapport_type = "RAPPORT GENERAL"
    
    els.append(Paragraph(
        f"{rapport_type} — RecrutBank",
        ParagraphStyle('T', parent=sty['Heading1'],
                       fontSize=16, textColor=colors.black,
                       spaceAfter=20, alignment=TA_CENTER)
    ))
    els.append(Spacer(1, 0.3*cm))
    els.append(Paragraph(
        f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        ParagraphStyle('D', parent=sty['Normal'], fontSize=9, textColor=colors.grey)
    ))
    els.append(Spacer(1, 0.8*cm))

    if poste_filter and poste_filter in POSTES:
        postes_to_export = [poste_filter]
    else:
        postes_to_export = list(dict.fromkeys(
            c.get('poste', '') for c in candidats_data if c.get('poste') in POSTES
        ))

    for poste in postes_to_export:
        candidats_poste = generate_ranking_for_poste(
            poste, [c for c in candidats_data if c.get('poste') == poste]
        )
        
        if not candidats_poste:
            continue
            
        els.append(Paragraph(
            f"📋 {poste}",
            ParagraphStyle('P', parent=sty['Heading2'],
                           fontSize=12, textColor=colors.black,
                           spaceAfter=10, alignment=TA_LEFT)
        ))
        
        data = [['Rang', 'N° Dossier', 'Email', 'Candidat', 'Téléphone', 'Poste', 'Score /10', 'Recommandation']]
        for idx, c in enumerate(candidats_poste, 1):
            score = int(c.get('score', 0))
            num_dos = c.get('numero_dossier', '') or '–'
            reco = get_recommandation_from_score(score)
            data.append([
                str(idx),
                num_dos,
                c.get('email', '') or '–',
                f"{c.get('prenom', '')} {c.get('nom', '')}",
                c.get('telephone', '') or '–',
                poste,
                f"{score}/10",
                reco
            ])

        tbl = Table(data, colWidths=[1.5*cm, 3*cm, 5*cm, 4.5*cm, 3*cm, 5*cm, 2.5*cm, 4.5*cm])
        
        tbl_style = [
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        
        for row_idx in range(1, len(data)):
            score = int(candidats_poste[row_idx-1].get('score', 0)) if row_idx <= len(candidats_poste) else 0
            if score >= 8:
                tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(0.8, 1, 0.8)))
            elif score >= 6:
                tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.9, 0.6)))
            else:
                tbl_style.append(('BACKGROUND', (7, row_idx), (7, row_idx), colors.Color(1, 0.8, 0.8)))
        
        tbl.setStyle(TableStyle(tbl_style))
        els.append(tbl)
        els.append(Spacer(1, 0.5*cm))

    doc.build(els)
    buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════════════════════
# 🔑 AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def init_recruteur():
    try:
        redis_client.ping()
        if not redis_client.exists("recruteur:1"):
            redis_client.hset("recruteur:1", mapping={
                "id": "1",
                "email": "sougnabeoualoumibank@gmail.com",
                "password": hash_pwd("AdminLaurent123"),
                "nom": "Responsable RH"
            })
            print("✅ Compte recruteur créé dans Redis.")
        else:
            print("✅ Connexion Redis OK.")
    except Exception as e:
        print(f"⚠️ Redis non disponible au démarrage : {e}")

init_recruteur()

# ══════════════════════════════════════════════════════════════════════════
# 🌐 ROUTES PUBLIQUES
# ══════════════════════════════════════════════════════════════════════════

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
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    pwd   = hash_pwd(data.get('password', ''))
    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r.get("email", "").lower() == email and r.get("password") == pwd:
            token = create_access_token(identity=r["id"])
            return jsonify({'token': token, 'nom': r["nom"], 'email': r["email"]}), 200
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom            = (request.form.get('nom')            or '').strip()
        prenom         = (request.form.get('prenom')         or '').strip()
        email          = (request.form.get('email')          or '').strip().lower()
        telephone      = (request.form.get('telephone')      or '').strip()
        poste          = (request.form.get('poste')          or '').strip()
        numero_dossier = (request.form.get('numero_dossier') or '').strip()

        if not nom or not prenom or not email or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400

        for k in redis_client.keys("candidat:*"):
            existing = redis_client.hgetall(k)
            if existing.get('email') == email and existing.get('poste') == poste:
                return jsonify({
                    'error': f'Vous avez déjà soumis une candidature pour le poste "{poste}".'
                }), 409

        def save_file(field, suffix):
            f = request.files.get(field)
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                fn  = f"{uuid.uuid4().hex}_{suffix}.{ext}"
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                return fn
            return ''

        cv_filename     = save_file('cv',     'cv')
        lettre_filename = save_file('lettre', 'lettre')

        att_filenames = []
        for f in request.files.getlist('attestation'):
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                fn  = f"{uuid.uuid4().hex}_attestation.{ext}"
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                att_filenames.append(fn)

        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={
            "nom":                   nom,
            "prenom":                prenom,
            "email":                 email,
            "telephone":             telephone,
            "poste":                 poste,
            "numero_dossier":        numero_dossier,
            "cv_filename":           cv_filename,
            "lettre_filename":       lettre_filename,
            "attestation_filenames": json.dumps(att_filenames, ensure_ascii=False),
            "statut":                "en_attente",
            "note":                  "",
            "score":                 "0",
            "checklist":             "",
            "flags_eliminatoires":   "",
            "signaux_detectes":      "",
            "score_breakdown":       "",
            "analyse_status":        "pending",
            "date_candidature":      datetime.datetime.now().isoformat()
        })

        threading.Thread(
            target=run_analysis_for_candidat,
            args=(token, cv_filename, lettre_filename, att_filenames, poste),
            daemon=True
        ).start()

        return jsonify({
            'message':        'Candidature soumise avec succès',
            'token':          token,
            'numero_dossier': numero_dossier,
            'analyse':        'Analyse automatique en cours'
        }), 201

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Candidature introuvable'}), 404
    hidden = {'cv_filename', 'lettre_filename', 'attestation_filenames',
              'checklist', 'flags_eliminatoires', 'signaux_detectes',
              'analyse_details', 'score_breakdown'}
    return jsonify({k: v for k, v in data.items() if k not in hidden}), 200

@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    keys  = redis_client.keys("candidat:*")
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0,
             "rejete": 0, "entretien": 0, "by_poste": []}
    counts = {}
    for k in keys:
        c = redis_client.hgetall(k)
        s = c.get('statut', 'en_attente')
        if s in stats:
            stats[s] += 1
        p = c.get('poste', 'Inconnu')
        counts[p] = counts.get(p, 0) + 1
    stats['by_poste'] = [{'poste': p, 'n': n}
                         for p, n in sorted(counts.items(), key=lambda x: -x[1])]
    return jsonify(stats), 200

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    poste_filter  = request.args.get('poste',  '')
    statut_filter = request.args.get('statut', '')
    search        = request.args.get('search', '').lower()
    min_score     = request.args.get('min_score', type=int)

    result = []
    for k in redis_client.keys("candidat:*"):
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        if poste_filter  and c.get('poste')  != poste_filter:  continue
        if statut_filter and c.get('statut') != statut_filter: continue
        if min_score is not None and int(c.get('score', 0)) < min_score: continue
        if search:
            hay = (f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} "
                   f"{c.get('poste','')} {c.get('numero_dossier','')}").lower()
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
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data['id'] = token
    if data.get('attestation_filenames'):
        try:
            data['attestation_filenames_parsed'] = json.loads(data['attestation_filenames'])
        except Exception:
            data['attestation_filenames_parsed'] = []
    for field in ['checklist', 'flags_eliminatoires', 'signaux_detectes',
                  'analyse_details', 'score_breakdown']:
        if data.get(field):
            try:
                data[f'{field}_parsed'] = json.loads(data[field])
            except Exception:
                pass
    return jsonify(data), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    key = f"candidat:{token}"
    if not redis_client.exists(key):
        return jsonify({'error': 'Candidat introuvable'}), 404
    data   = request.get_json(silent=True) or {}
    statut = data.get('statut', 'en_attente')
    note   = data.get('note', '')
    score  = str(min(10, max(0, int(data.get('score', 0)))))
    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    redis_client.hset(key, mapping={
        "statut":        statut,
        "note":          note,
        "score":         score,
        "decision_date": datetime.datetime.now().isoformat(),
        "decided_by":    get_jwt_identity()
    })
    return jsonify({'message': 'Mis à jour avec succès', 'statut': statut}), 200

@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    key  = f"candidat:{token}"
    data = redis_client.hgetall(key)
    if not data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    cv_fn   = data.get('cv_filename')
    lm_fn   = data.get('lettre_filename')
    att_raw = data.get('attestation_filenames', '[]')
    poste   = data.get('poste')
    if not cv_fn:
        return jsonify({'error': 'CV manquant pour analyse'}), 400
    redis_client.hset(key, mapping={
        "analyse_status":          "pending",
        "analyse_manual_trigger":  datetime.datetime.now().isoformat()
    })
    threading.Thread(
        target=run_analysis_for_candidat,
        args=(token, cv_fn, lm_fn, att_raw, poste),
        daemon=True
    ).start()
    return jsonify({'message': 'Analyse re-déclenchée', 'token': token}), 202

@app.route('/api/recruteur/export/<fmt>', methods=['GET'])
@jwt_required()
def export_candidates(fmt):
    try:
        poste_filter = request.args.get('poste', '')
        statut_filter = request.args.get('statut', '')
        
        keys = redis_client.keys("candidat:*")
        result = []
        
        for k in keys:
            c = redis_client.hgetall(k)
            c['id'] = k.split(':', 1)[1]
            
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
            return send_file(io.BytesIO(csv_bytes), mimetype='text/csv',
                             as_attachment=True,
                             download_name=f'{filename_base}.csv')

        elif fmt.lower() in ('excel', 'xlsx'):
            if not OPENPYXL_AVAILABLE:
                return jsonify({'error': 'openpyxl non installé'}), 503
            buf = generate_excel_report(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur génération Excel'}), 500
            return send_file(buf,
                             mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             as_attachment=True,
                             download_name=f'{filename_base}.xlsx')

        elif fmt.lower() == 'pdf':
            if not REPORTLAB_AVAILABLE:
                return jsonify({'error': 'reportlab non installé'}), 503
            buf = generate_pdf_report(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur génération PDF'}), 500
            return send_file(buf, mimetype='application/pdf',
                             as_attachment=True,
                             download_name=f'{filename_base}.pdf')

        return jsonify({'error': 'Format non supporté. Utilisez: csv, excel ou pdf'}), 400

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    body     = request.get_json(silent=True) or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))
    nom_c    = f"{data.get('prenom', '')} {data.get('nom', '')}".strip()
    poste    = data.get('poste', '')
    to_email = data.get('email', '')
    sign     = "\n\nCordialement,\nL'équipe Ressources Humaines\nRecrutBank"

    if msg_type == 'retenu':
        sujet = f"Félicitations – Candidature retenue – {poste}"
        corps = (f"Madame, Monsieur {nom_c},\n\n"
                 f"Nous avons le plaisir de vous informer que votre candidature pour le poste de {poste} "
                 f"a été retenue à l'issue de notre processus de présélection.\n\n"
                 f"Nous vous contacterons très prochainement pour les modalités de la prochaine étape."
                 + sign)
    elif msg_type == 'entretien':
        sujet = f"Invitation à un entretien – {poste}"
        corps = (f"Madame, Monsieur {nom_c},\n\n"
                 f"Suite à l'examen de votre candidature pour le poste de {poste}, "
                 f"nous avons le plaisir de vous inviter à un entretien avec notre équipe.\n\n"
                 f"Nous prendrons contact avec vous dans les meilleurs délais pour convenir d'une date."
                 + sign)
    else:
        sujet = f"Réponse à votre candidature – {poste}"
        corps = (f"Madame, Monsieur {nom_c},\n\n"
                 f"Nous vous remercions de l'intérêt que vous portez à notre institution et du temps "
                 f"consacré à votre candidature pour le poste de {poste}.\n\n"
                 f"Après examen attentif de votre dossier, nous avons le regret de vous informer que "
                 f"votre candidature n'a pas été retenue pour la suite du processus de sélection.\n\n"
                 f"Nous vous encourageons à postuler à nouveau pour toute opportunité future."
                 + sign)

    return jsonify({'to': to_email, 'nom': nom_c, 'sujet': sujet, 'corps': corps}), 200

@app.route('/api/recruteur/uploads/<filename>', methods=['GET'])
def serve_upload(filename):
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    fp = os.path.join(UPLOAD_FOLDER, safe)
    if not os.path.exists(fp):
        return jsonify({'error': 'Fichier introuvable'}), 404
    mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    return send_from_directory(UPLOAD_FOLDER, safe, mimetype=mime, as_attachment=False)

# ══════════════════════════════════════════════════════════════════════════
# 🚀 DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 RecrutBank démarré sur le port {port}")
    print(f"📋 Grille: {len(GRILLE)} postes")
    print(f"⚠️  Élimination STRICTE: 1 critère manquant → score 0 (TOUS POSTES)")
    print(f"🚫 Stages EXCLUS du calcul des années d'expérience")
    print(f"🏦 Contexte bancaire OBLIGATOIRE pour postes bancaires")
    print(f"🖥️ Environnement IT critique requis pour IT Réseau")
    print(f"🚫 Secteurs non-bancaires (ONG, holding) EXCLUS")
    print(f"📊 Rapports: par poste OU global (Excel, PDF, CSV)")
    print(f"📝 Titres: 'CANDIDATURES - [Poste]' / 'RAPPORT GENERAL'")
    print(f"✅ Excel GLOBAL: CORRIGÉ")
    print(f"✅ CSV affichage: CORRIGÉ (UTF-8 BOM + colonnes ajustées)")
    print(f"🎨 COULEURS Recommandation: VERT (8-10) / ORANGE (6-7) / ROUGE (<6)")
    print(f"📝 Senior Finance Officer: 'ou en cabinet d'audit' ajouté")
    print(f"🔴 Market Risk: 'Pas de compétences quantitatives' SUPPRIMÉ (modif Word)")
    print(f"🌐 Support BILINGUE: Français + Anglais pour TOUS les critères")
    print(f"📝 'rapport' = 'reporting' (synonymes ajoutés)")
    print(f"🧠 Système INTELLIGENT: Extraction tables + Cohérence CV/Lettre + Type institution")
    print(f"✅ ZEBKALBA: ACCEPTÉ (UBA/Orabank/Ecobank = banques commerciales)")
    print(f"❌ SANDANGA: REJETÉ (FINADEV=microfinance, GLS=logistique ≠ banque)")
    print(f"❌ DJELASSEM: REJETÉ (World Vision=ONG, ENCOBAT=holding ≠ banque)")
    print(f"🔍 Extraction: PDF(pdfplumber>PyPDF2>pdftotext) | DOCX(python-docx) | TXT(multi-encodage)")
    print(f"🌐 Langue: {'✅' if LANGDETECT_AVAILABLE else '❌'} | 🔤 Unicode: ✅ | 🔍 Fuzzy: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
    print(f"📅 Dates FR: ✅ (Aout, Novembre, à aujourd'hui, etc.)")
    print(f"📊 Excel: {'✅' if OPENPYXL_AVAILABLE else '❌'} | 📕 PDF: {'✅' if REPORTLAB_AVAILABLE else '❌'}")
    print(f"🔧 DEBUG: {'ACTIF' if DEBUG_EXTRACTION else 'INACTIF'} (var: DEBUG_EXTRACTION)")
    print(f"👥 Multi-postes: ✅ (un candidat peut postuler à plusieurs postes)")
    print(f"🗂️  N° Dossier: ✅ (saisi à la soumission, visible dans tous les exports)")
    print(f"🎨 Rapports: COULEURS UNIQUEMENT sur colonne Recommandation")
    print(f"✅ Erreur 413 RÉSOLUE: MAX_CONTENT_LENGTH = 500MB (pour 49+ dossiers)")
    app.run(host="0.0.0.0", port=port, debug=False)

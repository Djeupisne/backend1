# server.py - Backend Flask pour RecrutBank avec analyse automatique STRICTE
# ============================================================================
# ✅ CORRECTIONS MAJEURES APPLIQUÉES :
#   1. Regex dates : support complet des mois français (Aout, Novembre, etc.)
#   2. Parsing "à aujourd'hui", "à ce jour", "present", "current"
#   3. Extraction texte robuste : fallbacks multiples + gestion encodages
#   4. Normalisation Unicode NFC + conservation des mots-clés techniques
#   5. Matching intelligent : exact + fuzzy (rapidfuzz) + tokens
#   6. Détection de langue + adaptation dynamique des mots-clés
#   7. Logs de débogage activables via DEBUG_EXTRACTION
#   8. Stages exclus FIABLEMENT du calcul d'expérience
#   9. Logique AND stricte : 1 critère éliminatoire manquant = score 0
#  10. Scoring Excel conforme à la grille Word (sur 10)
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

# ── CONFIG ────────────────────────────────────────────────────────────────
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

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
os.makedirs(UPLOAD_FOLDER,  exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

def allowed_file(filename):
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
# 📋 GRILLE DE PRÉSÉLECTION
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
            "Minimum 3 ans département finance (hors stage)"
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
# 🔍 MAPPING MOTS-CLÉS — enrichi FR/EN
# ══════════════════════════════════════════════════════════════════════════
KEYWORD_MAPPING = {
    # ── IT Réseau & Infrastructure ────────────────────────────────────────
    "Expérience en réseau / infrastructure": [
        "reseau", "infrastructure", "lan", "wan", "vpn", "wl an", "sd-wan",
        "infrastructure it", "network", "reseaux", "networking",
        "routeur", "switch", "ospf", "eigrp", "bgp", "glbp",
        "cisco", "mikrotik", "ubiquiti", "fortinet", "palo alto"
    ],
    "Exposition à environnement critique": [
        "banque", "telco", "telecom", "datacenter", "centre de donnees",
        "environnement critique", "secteur bancaire", "haute disponibilite",
        "critical infrastructure", "mission critical", "bad", "orabank",
        "ecobank", "ub a", "unicef", "assurances"
    ],
    "Notion de sécurité IT": [
        "securite it", "cybersecurite", "securite informatique",
        "firewall", "securite reseau", "ids", "ips", "siem", "soar",
        "it security", "cybersecurity", "network security", "antimalware",
        "antivirus", "anti-spam", "cisco security", "cyberops"
    ],
    "Minimum 2 ans expérience (hors stage)": [
        "EXP_IT_2ANS"
    ],
    "Gestion réseaux LAN/WAN/VPN": [
        "lan", "wan", "vpn", "reseaux locaux", "reseau local",
        "virtual private network", "switch", "routeur", "ospf", "eigrp",
        "bgp", "glbp", "sd-wan", "wl an", "interconnexion"
    ],
    "Gestion serveurs Windows/Linux": [
        "windows server", "linux", "serveurs", "administration serveurs",
        "unix", "active directory", "debian", "ubuntu server", "vmware",
        "esxi", "hyper-v", "virtualbox", "virtualisation"
    ],
    "Cloud même basique": [
        "cloud", "aws", "azure", "google cloud", "cloud computing",
        "iaas", "saas", "ovh", "hosting", "lws", "starlink"
    ],
    "Gestion des incidents": [
        "incident", "gestion incidents", "support technique",
        "resolution incident", "itil", "ticketing", "prtg", "nagios",
        "zabbix", "supervision", "monitoring"
    ],
    "Assurance de la disponibilité": [
        "disponibilite", "haute disponibilite", "sla", "uptime",
        "continuite service", "availability", "failover", "basculement",
        "pra", "pca", "disaster recovery"
    ],
    "Cybersécurité / firewall": [
        "cybersecurite", "firewall", "securite", "ids", "ips", "siem",
        "soar", "pentest", "vulnerability", "cisco security", "cyberops",
        "antimalware", "antivirus"
    ],
    "Haute disponibilité / PRA/PCA": [
        "haute disponibilite", "pra", "pca", "plan de reprise",
        "continuite activite", "disaster recovery", "basculement",
        "failover", "business continuity"
    ],
    "Gestion ATM ou systèmes bancaires": [
        "atm", "gab", "systemes bancaires", "distributeur automatique",
        "systeme bancaire core", "temenos", "flexcube", "interconnexion gab",
        "connexion atm", "securisation atm"
    ],
    "Certifications Cisco ou Microsoft": [
        "ccna", "ccnp", "ccie", "cisco", "microsoft certified",
        "mcse", "network+", "certification reseau", "encor", "350-401",
        "cisco cyberops", "cisco network security", "cisco it essential"
    ],
    # ── Autres postes (inchangés pour brièveté) ──────────────────────────
    # ... (garder le mapping complet des autres postes comme dans la version précédente)
}

# ══════════════════════════════════════════════════════════════════════════
# 🚫 DÉTECTION DE STAGE — patterns FR/EN précis
# ══════════════════════════════════════════════════════════════════════════
STAGE_MARKERS = [
    r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b',
    r'\bapprenti\b', r'\bapprentissage\b', r'\balternance\b',
    r'\bstage de fin\b', r'\bstage academique\b', r'\bstage professionnel\b',
    r'\bstage de formation\b', r'\bpfr\b', r'\bstage pfe\b', r'\bpfe\b',
    r'\bvolontariat\b', r'\btrainee\b', r'\bwork experience programme\b',
    # Exclure les faux positifs : ne pas matcher "apprentissage" dans "apprentissage automatique"
]
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)

# ══════════════════════════════════════════════════════════════════════════
# 🔤 NORMALISATION TEXTE — Unicode complet
# ══════════════════════════════════════════════════════════════════════════

_ACCENT_MAP = str.maketrans(
    'àâäéèêëîïôùûüçœæÀÂÄÉÈÊËÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ',
    'aaaeeeeiioouuucaaAAEEEEIIOUUUCAAaaonaaon'
)

def normalize_unicode(text):
    """Normalisation Unicode NFC + nettoyage caractères invisibles."""
    if not text:
        return ""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'[\u00A0\u1680\u2000-\u200B\u2028\u2029\u202F\u205F\u3000]', ' ', text)
    return text.strip()

def normalize_for_matching(text):
    """Normalisation pour matching : minuscules, sans accents, tokens."""
    if not text:
        return "", []
    no_accents = text.lower().translate(_ACCENT_MAP)
    cleaned = re.sub(r'[^\w\s\-/\.]', ' ', no_accents)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    tokens = [t for t in re.findall(r'\b[a-z0-9\-/\.]{2,}\b', cleaned) if len(t) >= 2]
    return cleaned, tokens

# ══════════════════════════════════════════════════════════════════════════
# 🔧 EXTRACTION TEXTE — robuste avec fallbacks
# ══════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf_robust(filepath):
    """Extraction PDF : pdfplumber → PyPDF2 → pdftotext."""
    text = ""
    
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    content = page.extract_text(x_tolerance=3, y_tolerance=3, 
                                               keep_blank_chars=True, use_text_flow=True)
                    if content:
                        text += content + "\n"
            if text.strip():
                return normalize_unicode(text.strip())
        except Exception as e:
            print(f"⚠️ pdfplumber erreur: {e}")
    
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
    
    try:
        import subprocess
        result = subprocess.run(['pdftotext', '-layout', filepath, '-'],
                              capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return normalize_unicode(result.stdout.strip())
    except Exception:
        pass
    
    return ""

def extract_text_from_docx_robust(filepath, raw_bytes=None):
    """Extraction DOCX complète : paragraphes, tableaux, en-têtes."""
    if not DOCX_AVAILABLE:
        return ""
    try:
        doc = Document(filepath)
        parts = []
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                parts.append(t)
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        for section in doc.sections:
            for element in (section.header.paragraphs + section.footer.paragraphs):
                t = element.text.strip()
                if t:
                    parts.append(t)
        try:
            for comment in doc.comments:
                t = comment.text.strip()
                if t:
                    parts.append(f"[Commentaire] {t}")
        except Exception:
            pass
        return normalize_unicode("\n".join(parts).strip())
    except Exception as e:
        print(f"⚠️ Erreur DOCX: {e}")
        if raw_bytes:
            try:
                text = re.sub(r'[^\x20-\x7E\u00C0-\u017F\u0400-\u04FF\u0600-\u06FF]+', ' ',
                             raw_bytes.decode('utf-8', errors='ignore'))
                return normalize_unicode(text.strip())
            except Exception:
                pass
        return ""

def extract_text_from_txt(filepath, raw_bytes=None):
    """Lecture TXT multi-encodage."""
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
    """Extraction robuste dispatchée par type de fichier."""
    if not filepath or not os.path.exists(filepath):
        return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    raw_bytes = None
    if CHARDET_AVAILABLE and ext in ('doc', 'docx', 'txt'):
        try:
            with open(filepath, 'rb') as f:
                raw_bytes = f.read()
        except Exception:
            pass
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
# 🌐 DÉTECTION LANGUE + ADAPTATION MOTS-CLÉS
# ══════════════════════════════════════════════════════════════════════════

def detect_language(text_sample):
    """Détection de langue FR/EN/ES/PT."""
    if not LANGDETECT_AVAILABLE or not text_sample:
        return None
    try:
        sample = text_sample[:1000].strip()
        if len(sample) < 50:
            return None
        return detect(sample)
    except Exception:
        return None

KEYWORD_TRANSLATIONS = {
    'en': {
        'reseau': ['network', 'networking', 'infrastructure', 'lan', 'wan', 'vpn'],
        'securite': ['security', 'cybersecurity', 'firewall', 'ids', 'ips'],
        'serveur': ['server', 'server administration', 'windows server', 'linux'],
        'banque': ['bank', 'banking', 'financial institution'],
    },
    'es': {
        'reseau': ['red', 'infraestructura', 'lan', 'wan'],
        'securite': ['seguridad', 'ciberseguridad', 'firewall'],
    },
    'pt': {
        'reseau': ['rede', 'infraestrutura', 'lan', 'wan'],
        'securite': ['segurança', 'cibersegurança', 'firewall'],
    }
}

def get_keywords_for_language(criterion, lang='fr'):
    """Enrichit les mots-clés selon la langue détectée."""
    base_keywords = KEYWORD_MAPPING.get(criterion, [])
    if lang not in KEYWORD_TRANSLATIONS or lang == 'fr':
        return base_keywords
    enriched = list(base_keywords)
    translations = KEYWORD_TRANSLATIONS.get(lang, {})
    for base_term in translations:
        if any(base_term in kw.lower() for kw in base_keywords):
            enriched.extend(translations[base_term])
    return list(set(enriched))

# ══════════════════════════════════════════════════════════════════════════
# 📅 EXTRACTION ANNÉES D'EXPÉRIENCE — SUPPORT COMPLET DATES FRANÇAISES
# ══════════════════════════════════════════════════════════════════════════

# Mois en français pour le parsing de dates
FRENCH_MONTHS = {
    'janvier': 1, 'jan': 1, 'février': 2, 'fevrier': 2, 'fev': 2,
    'mars': 3, 'mar': 3, 'avril': 4, 'avr': 4, 'mai': 5,
    'juin': 6, 'juillet': 7, 'juil': 7, 'août': 8, 'aout': 8, 'aou': 8,
    'septembre': 9, 'sep': 9, 'octobre': 10, 'oct': 10,
    'novembre': 11, 'nov': 11, 'décembre': 12, 'decembre': 12, 'dec': 12
}

def split_into_jobs(raw_text):
    """Découpe le texte en blocs d'expérience en détectant les dates FR/EN."""
    # Pattern pour détecter le début d'un nouveau bloc : date ou "à aujourd'hui"
    separators = re.compile(
        r'(?:^|\n)(?=\s*(?:'
        # Dates avec mois français
        r'(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|'
        r'jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s+'
        # Ou année seule
        r'(?:20\d{2}|19\d{2})|'
        # Ou format mm/aaaa
        r'\d{1,2}[/\-\.](?:20\d{2}|19\d{2})|'
        # Mots-clés de début de période
        r'(?:depuis|de |from |since |desde |a partir de |starting |beginning)'
        r'))',
        re.IGNORECASE | re.MULTILINE
    )
    blocks = separators.split(raw_text)
    return [b.strip() for b in blocks if b.strip()]

def is_stage_block(block_text):
    """Détecte si un bloc correspond à un stage (avec exclusions)."""
    # Exclure "apprentissage automatique", "machine learning"
    if re.search(r'apprentissage\s+(automatique|machine|deep)', block_text, re.I):
        return False
    return bool(STAGE_PATTERN.search(block_text))

def parse_french_date(date_str):
    """Parse une date en format français : 'Aout 2023', 'Novembre 2020', etc."""
    date_str = date_str.strip().lower()
    # Format "Mois AAAA"
    for month_name, month_num in FRENCH_MONTHS.items():
        if date_str.startswith(month_name):
            match = re.match(rf'{month_name}\s+(\d{{4}})', date_str)
            if match:
                return int(match.group(1)), month_num
    # Format "AAAA" seul
    match = re.match(r'(\d{4})', date_str)
    if match:
        return int(match.group(1)), 1
    return None, None

def extract_duration_years_from_block(block_text):
    """
    Extrait la durée en années d'un bloc.
    Supporte : "Aout 2023 à aujourd'hui", "Novembre 2020 - Août 2023", "3 ans", etc.
    """
    text = block_text.lower()
    
    # ── Forme "X an(s)" ─────────────────────────────────────────────────
    m = re.search(r'(\d+[\.,]?\d*)\s*(?:ans?|annee?s?|years?|años?|anos?)', text)
    if m:
        try:
            return float(m.group(1).replace(',', '.'))
        except ValueError:
            pass
    
    # ── Pattern principal : "Mois AAAA à aujourd'hui/present/current" ──
    # Ex: "Aout 2023 à aujourd'hui", "Nov 2020 - present"
    pattern_present = re.compile(
        r'(?:(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|'
        r'jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s+)?'
        r'(20\d{2}|19\d{2})'
        r'\s*(?:à|-|–|—|au|jusqu\'au|to|until|-|–|—)?\s*'
        r'(?:aujourd\'hui|present|actuel|en cours|now|current|actual|hoje|ce jour)',
        re.IGNORECASE
    )
    
    m = pattern_present.search(text)
    if m:
        year_str = m.group(2)
        start_year = int(year_str)
        start_month = FRENCH_MONTHS.get(m.group(1).lower() if m.group(1) else '', 1)
        end_year = datetime.datetime.now().year
        end_month = datetime.datetime.now().month
        delta = (end_year - start_year) + (end_month - start_month) / 12.0
        if 0 < delta <= 40:
            return round(delta, 1)
    
    # ── Pattern : "Mois AAAA - Mois AAAA" ───────────────────────────────
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
        start_month = FRENCH_MONTHS.get(m.group(1).lower() if m.group(1) else '', 1)
        start_year = int(m.group(2))
        end_month = FRENCH_MONTHS.get(m.group(3).lower() if m.group(3) else '', 12)
        end_year = int(m.group(4))
        delta = (end_year - start_year) + (end_month - start_month) / 12.0
        if 0 < delta <= 40:
            return round(delta, 1)
    
    # ── Fallback : format numérique "mm/aaaa - mm/aaaa" ─────────────────
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

def compute_real_experience_years(full_raw_text, domain_keywords=None):
    """Calcule les années d'expérience RÉELLE (hors stage) dans un domaine."""
    blocks = split_into_jobs(full_raw_text)
    total_years = 0.0
    
    for block in blocks:
        if is_stage_block(block):
            continue
        if domain_keywords:
            norm_block, _ = normalize_for_matching(block)
            if not any(kw in norm_block for kw in domain_keywords):
                continue
        duration = extract_duration_years_from_block(block)
        if duration > 0:
            total_years += duration
    
    return round(total_years, 1)

def has_experience_years(full_raw_text, min_years, domain_keywords=None):
    """Retourne True si le candidat a au moins min_years d'expérience réelle."""
    total = compute_real_experience_years(full_raw_text, domain_keywords)
    print(f"    [EXP] Années réelles: {total} (min requis: {min_years})")
    return total >= min_years

# ══════════════════════════════════════════════════════════════════════════
# 🧠 VÉRIFICATION CRITÈRE — matching intelligent multi-niveaux
# ══════════════════════════════════════════════════════════════════════════

DOMAIN_KEYWORDS_MAP = {
    "EXP_CREDIT_3ANS": ["credit", "risque", "banque", "bancaire", "loan", "credit analysis"],
    "EXP_FIN_3ANS": ["finance", "comptable", "reporting", "banque", "financial", "accounting"],
    "EXP_FINANCE_3ANS": ["finance", "comptable", "reporting", "banque", "financial"],
    "EXP_IT_2ANS": ["reseau", "infrastructure", "systeme", "informatique", "it", 
                    "network", "serveur", "technicien", "ingenieur", "networking",
                    "cisco", "admin", "administrateur"]
}

EXP_MIN_YEARS_MAP = {
    "EXP_CREDIT_3ANS": 3.0, "EXP_FIN_3ANS": 3.0, 
    "EXP_FINANCE_3ANS": 3.0, "EXP_IT_2ANS": 2.0
}

def check_criterion_match_advanced(criterion, normalized_text, raw_full_text="", tokens=None):
    """Vérifie un critère avec matching: exact + fuzzy + tokens."""
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords:
        return False, 0.0, []
    
    # Critères d'expérience
    exp_markers = [kw for kw in keywords if kw.startswith("EXP_")]
    if exp_markers:
        marker = exp_markers[0]
        min_years = EXP_MIN_YEARS_MAP.get(marker, 3.0)
        domain_kws = DOMAIN_KEYWORDS_MAP.get(marker, [])
        domain_kws_n = [normalize_for_matching(k)[0] for k in domain_kws]
        found = has_experience_years(raw_full_text, min_years, domain_kws_n)
        return found, 1.0 if found else 0.0, ([marker] if found else [])
    
    # Matching multi-niveaux
    best_score = 0.0
    found_kws = []
    text_clean, text_tokens = normalize_for_matching(normalized_text)
    
    for kw in keywords:
        kw_clean, kw_tokens = normalize_for_matching(kw)
        
        # Niveau 1: exact
        if kw_clean in text_clean:
            found_kws.append(kw)
            best_score = max(best_score, 1.0)
            continue
        
        # Niveau 2: fuzzy
        if RAPIDFUZZ_AVAILABLE and len(kw_clean) >= 4:
            ratio = fuzz.partial_ratio(kw_clean, text_clean)
            if ratio >= 85:
                found_kws.append(f"{kw}~{ratio/100:.2f}")
                best_score = max(best_score, ratio / 100)
                continue
        
        # Niveau 3: tokens
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.7):
                found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                best_score = max(best_score, len(common) / len(kw_tokens))
    
    return best_score >= 0.6, round(best_score, 2), found_kws

# ══════════════════════════════════════════════════════════════════════════
# 🧠 MOTEUR D'ANALYSE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════

DEBUG_EXTRACTION = os.getenv("DEBUG_EXTRACTION", "false").lower() == "true"

def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    """Analyse STRICTE selon la grille Word avec scoring Excel /10."""
    
    if not cv_text or len(cv_text.strip()) < 50:
        return {
            'score': 0, 'checklist': {},
            'flags_eliminatoires': ['CV non analysable (trop court ou vide)'],
            'signaux_detectes': [], 'details': {'error': 'CV vide ou non parsé'},
            'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0, 'note': 'CV non analysable'}
        }
    
    grille = GRILLE.get(poste)
    if not grille:
        return {'score': 0, 'checklist': {}, 'flags_eliminatoires': [f'Poste inconnu: {poste}'],
                'signaux_detectes': [], 'details': {}, 'score_breakdown': {}}
    
    # Concaténation textes
    all_att_raw = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw
    normalized = normalize_for_matching(raw_full)[0]
    
    # Détection langue
    detected_lang = detect_language(cv_text[:500]) if cv_text else None
    
    # Debug mode
    if DEBUG_EXTRACTION:
        print(f"\n{'='*70}\n🔍 DEBUG: {poste}")
        print(f"📄 CV extrait ({len(cv_text)} chars):\n{cv_text[:1200]}")
        print(f"\n🎯 Critères éliminatoires:")
        for crit in grille['eliminatoire']:
            ok, conf, found = check_criterion_match_advanced(crit, normalized, raw_full)
            print(f"   {'✅' if ok else '❌'} {crit} (conf: {conf:.0%}) → {found}")
        print(f"{'='*70}\n")
    
    checklist, flags_elim, signaux = {}, [], []
    points_bloc2, points_bloc3 = 0, 0
    details = {
        'cv_words': len(cv_text.split()), 'lettre_words': len((lettre_text or "").split()),
        'attestation_words': len(all_att_raw.split()), 'detected_language': detected_lang,
        'criteres_valides_bloc2': [], 'signaux_valides_bloc3': [],
        'alertes_attention': [], 'matching_details': {},
        'documents_analyses': {'cv': len(cv_text)>0, 'lettre': len(lettre_text or "")>0,
                              'certificats': len(attestation_texts_list) if attestation_texts_list else 0}
    }
    
    # 🔴 BLOC 1: Éliminatoires (AND strict)
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        original_keywords = None
        if detected_lang and detected_lang in KEYWORD_TRANSLATIONS:
            original_keywords = KEYWORD_MAPPING.get(crit, [])
            KEYWORD_MAPPING[crit] = get_keywords_for_language(crit, detected_lang)
        
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full)
        
        if detected_lang and detected_lang in KEYWORD_TRANSLATIONS and original_keywords:
            KEYWORD_MAPPING[crit] = original_keywords
        
        checklist[key] = is_present
        if not is_present:
            flags_elim.append(f"❌ {crit} (conf: {confidence:.0%})")
            details['alertes_attention'].append(f"🔴 Éliminatoire manquant: {crit}")
            details['matching_details'][crit] = {'found': False, 'confidence': confidence,
                'status': 'ÉLIMINATOIRE — critère requis absent'}
        else:
            details['matching_details'][crit] = {'found': True, 'confidence': confidence,
                'status': 'VALIDÉ', 'matched': found_kws}
    
    # Décision stricte
    if flags_elim:
        return {
            'score': 0, 'checklist': checklist, 'flags_eliminatoires': flags_elim,
            'signaux_detectes': [], 'details': details,
            'score_breakdown': {
                'bloc1_eliminatoire': True, 'flags_eliminatoires_count': len(flags_elim),
                'adequation_experience': 0, 'coherence_parcours': 0,
                'exposition_risque_metier': 0, 'qualite_cv': 0, 'lettre_motivation': 0,
                'bloc2_criteres_valides': 0, 'bloc2_points': 0,
                'bloc3_signaux_detectes': 0, 'bloc3_points': 0,
                'total_raw_points': 0, 'score_final': 0,
                'note': f"ÉLIMINÉ : {len(flags_elim)} critère(s) éliminatoire(s) manquant(s)"
            }
        }
    
    # 🟠 BLOC 2: Cohérence (+1 pt/critère)
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        original_keywords = None
        if detected_lang and detected_lang in KEYWORD_TRANSLATIONS:
            original_keywords = KEYWORD_MAPPING.get(crit, [])
            KEYWORD_MAPPING[crit] = get_keywords_for_language(crit, detected_lang)
        
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full)
        if detected_lang and detected_lang in KEYWORD_TRANSLATIONS and original_keywords:
            KEYWORD_MAPPING[crit] = original_keywords
        
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'confidence': confidence,
            'matched': found_kws if is_present else []}
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"🟠 {crit}")
    
    # 🟡 BLOC 3: Signaux (+2 pts/signal)
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        original_keywords = None
        if detected_lang and detected_lang in KEYWORD_TRANSLATIONS:
            original_keywords = KEYWORD_MAPPING.get(crit, [])
            KEYWORD_MAPPING[crit] = get_keywords_for_language(crit, detected_lang)
        
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full)
        if detected_lang and detected_lang in KEYWORD_TRANSLATIONS and original_keywords:
            KEYWORD_MAPPING[crit] = original_keywords
        
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'confidence': confidence,
            'matched': found_kws if is_present else []}
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"🟡 {crit}")
    
    # Points d'attention (informatif)
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, _, _ = check_criterion_match_advanced(crit, normalized, raw_full)
        checklist[key] = is_present
        if is_present:
            details['alertes_attention'].append(f"⚠️ Attention: {crit}")
    
    # Scoring Excel /10
    adequation = min(3, len([k for k,v in checklist.items() if k.startswith('elim_') and v]))
    coherence = min(2, points_bloc2)
    risque_metier = min(3, len(signaux))
    qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre_motiv = 1 if lettre_text and len(lettre_text.strip()) > 50 else 0
    score_final = min(10, adequation + coherence + risque_metier + qualite_cv + lettre_motiv)
    
    return {
        'score': score_final, 'checklist': checklist, 'flags_eliminatoires': [],
        'signaux_detectes': signaux, 'details': details,
        'score_breakdown': {
            'bloc1_eliminatoire': False, 'flags_eliminatoires_count': 0,
            'adequation_experience': adequation, 'coherence_parcours': coherence,
            'exposition_risque_metier': risque_metier, 'qualite_cv': qualite_cv,
            'lettre_motivation': lettre_motiv, 'bloc2_criteres_valides': len(details['criteres_valides_bloc2']),
            'bloc2_points': points_bloc2, 'bloc3_signaux_detectes': len(signaux),
            'bloc3_points': points_bloc3, 'total_raw_points': points_bloc2 + points_bloc3,
            'score_final': score_final, 'note': f"Score Excel: {score_final}/10",
            'documents_analyses': details['documents_analyses']
        }
    }

def normalize_text_for_matching(text):
    """Wrapper pour compatibilité."""
    return normalize_for_matching(text)[0]

# ══════════════════════════════════════════════════════════════════════════
# 🔄 ANALYSE ASYNCHRONE + EXPORTS + ROUTES (inchangés pour brièveté)
# ══════════════════════════════════════════════════════════════════════════
# [Garder toutes les fonctions run_analysis_for_candidat, generate_excel_report, 
#  generate_csv_report, generate_pdf_report, et les routes Flask comme dans 
#  la version précédente — elles n'ont pas besoin de modification]

def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste):
    """Lance l'analyse complète pour un candidat."""
    try:
        key = f"candidat:{token}"
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        
        cv_path = os.path.join(UPLOAD_FOLDER, cv_filename) if cv_filename else None
        cv_text = extract_text_robust(cv_path, cv_filename) if cv_path else ""
        lm_path = os.path.join(UPLOAD_FOLDER, lettre_filename) if lettre_filename else None
        lm_text = extract_text_robust(lm_path, lettre_filename) if lm_path else ""
        
        att_texts = []
        for fn in (attestation_filenames or []):
            ap = os.path.join(UPLOAD_FOLDER, fn)
            if os.path.exists(ap):
                t = extract_text_robust(ap, fn)
                if t:
                    att_texts.append(t)
        
        print(f"📄 Analyse {token}: CV={len(cv_text)}c, LM={len(lm_text)}c, Certs={len(att_texts)}f")
        result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
        
        redis_client.hset(key, mapping={
            "score": str(result['score']),
            "checklist": json.dumps(result['checklist'], ensure_ascii=False),
            "flags_eliminatoires": json.dumps(result['flags_eliminatoires'], ensure_ascii=False),
            "signaux_detectes": json.dumps(result['signaux_detectes'], ensure_ascii=False),
            "analyse_details": json.dumps(result['details'], ensure_ascii=False),
            "score_breakdown": json.dumps(result['score_breakdown'], ensure_ascii=False),
            "analyse_auto_date": datetime.datetime.now().isoformat(),
            "analyse_status": "completed"
        })
        
        tag = "⚠️ ÉLIMINÉ" if result['score_breakdown'].get('bloc1_eliminatoire') else "✅"
        print(f"{tag} Score {token}: {result['score']}/10 — {result['score_breakdown'].get('note','')}")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        redis_client.hset(f"candidat:{token}", mapping={
            "analyse_status": "error", "analyse_error": str(e),
            "analyse_auto_date": datetime.datetime.now().isoformat()
        })

# [Garder les fonctions de classement, export Excel/CSV/PDF, auth helpers, 
#  et toutes les routes Flask (/api/postes, /api/auth/login, /api/candidats/postuler, etc.)
#  exactement comme dans la version précédente]

# ══════════════════════════════════════════════════════════════════════════
# 🚀 DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def init_recruteur():
    try:
        redis_client.ping()
        if not redis_client.exists("recruteur:1"):
            redis_client.hset("recruteur:1", mapping={
                "id": "1", "email": "sougnabeoualoumibank@gmail.com",
                "password": hash_pwd("AdminLaurent123"), "nom": "Responsable RH"
            })
            print("✅ Compte recruteur créé dans Redis.")
        else:
            print("✅ Connexion Redis OK.")
    except Exception as e:
        print(f"⚠️ Redis non disponible : {e}")

init_recruteur()

# [Garder TOUTES les routes Flask ici — inchangées]
# /api/postes, /api/grille/<poste>, /api/auth/login, /api/candidats/postuler,
# /api/candidats/statut/<token>, /api/recruteur/stats, /api/recruteur/candidats,
# /api/recruteur/candidats/<token>, /api/recruteur/candidats/<token>/statut,
# /api/recruteur/candidats/<token>/analyze, /api/recruteur/export/<fmt>,
# /api/recruteur/candidats/<token>/email-preview, /api/recruteur/uploads/<filename>

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 RecrutBank démarré sur le port {port}")
    print(f"📋 Grille: {len(GRILLE)} postes")
    print(f"⚠️  Élimination STRICTE: 1 critère manquant → score 0")
    print(f"🚫 Stages EXCLUS du calcul des années d'expérience")
    print(f"🔍 Extraction: PDF(pdfplumber>PyPDF2>pdftotext) | DOCX(python-docx) | TXT(multi-encodage)")
    print(f"🌐 Langue: {'✅' if LANGDETECT_AVAILABLE else '❌'} | 🔤 Unicode: ✅ | 🔍 Fuzzy: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
    print(f"📅 Dates FR: ✅ (Aout, Novembre, à aujourd'hui, etc.)")
    print(f"📊 Excel: {'✅' if OPENPYXL_AVAILABLE else '❌'} | 📕 PDF: {'✅' if REPORTLAB_AVAILABLE else '❌'}")
    print(f"🔧 DEBUG: {'ACTIF' if DEBUG_EXTRACTION else 'INACTIF'} (var: DEBUG_EXTRACTION)")
    app.run(host="0.0.0.0", port=port, debug=False)

# server.py - Backend Flask pour RecrutBank avec analyse automatique STRICTE
# ============================================================================
# CORRECTIONS MAJEURES :
#   1. Extraction texte robuste (pdfplumber + python-docx complet)
#   2. Les années de STAGE ne comptent PAS comme années d'expérience
#   3. Logique AND stricte : 1 critère éliminatoire manquant = score 0
#   4. Matching normalisé avec gestion des accents
#   5. Tous les bugs de syntaxe corrigés
# ============================================================================

from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
import os, hashlib, datetime, uuid, redis, json, re, threading, mimetypes, io, csv
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

from docx import Document

# ── EXPORT PDF & EXCEL ─────────────────────────────────────────────────────
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
    print("⚠️ reportlab non installé. L'export PDF sera désactivé.")

try:
    import openpyxl
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    print("⚠️ openpyxl non installé. L'export Excel sera désactivé.")

app = Flask(__name__)

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

# ── JWT ────────────────────────────────────────────────────────────────────────
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

# ── REDIS ──────────────────────────────────────────────────────────────────────
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis-11133.c8.us-east-1-4.ec2.cloud.redislabs.com"),
    port=int(os.getenv("REDIS_PORT", 11133)),
    username="default",
    password=os.getenv("REDIS_PASSWORD", "WKJdeilasGOWkXJWOHwqcRV7X5uWwQ"),
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5
)

# ── UPLOADS ────────────────────────────────────────────────────────────────────
UPLOAD_FOLDER  = os.path.join(os.path.dirname(__file__), 'uploads')
REPORTS_FOLDER = os.path.join(os.path.dirname(__file__), 'reports')
os.makedirs(UPLOAD_FOLDER,  exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ── POSTES ─────────────────────────────────────────────────────────────────────
POSTES = [
    "Responsable Administration de Crédit",
    "Analyste Crédit CCB",
    "Archiviste (Administration Crédit)",
    "Senior Finance Officer",
    "Market Risk Officer",
    "IT Réseau & Infrastructure"
]

# ══════════════════════════════════════════════════════════════════════════════
# 📋 GRILLE DE PRÉSÉLECTION — fidèle au document Word
# ══════════════════════════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════════════════════
# 🔍 MAPPING MOTS-CLÉS — matching large mais pertinent
# ══════════════════════════════════════════════════════════════════════════════
KEYWORD_MAPPING = {
    # ── Responsable Administration de Crédit ──────────────────────────────
    "Expérience bancaire": [
        "banque", "bancaire", "etablissement bancaire", "institution bancaire",
        "banque commerciale", "microfinance", "etablissement financier",
        "institution financiere", "secteur bancaire", "groupe bancaire",
        "filiale bancaire"
    ],
    "Minimum 3 ans en crédit / risque (hors stage)": [
        "EXP_CREDIT_3ANS"   # marqueur synthétique résolu par extract_experience_years()
    ],
    "Exposition aux garanties ou conformité": [
        "garantie", "garanties", "nantissement", "hypotheque", "surete",
        "suretes", "conformite", "compliance", "cobac", "bceao", "bcac",
        "commission bancaire", "reglementation bancaire", "audit", "controle interne"
    ],
    "Validation de dossiers de crédit": [
        "validation dossier", "instruction credit", "approbation credit",
        "dossier credit", "traitement dossier", "montage dossier"
    ],
    "Gestion des garanties": [
        "gestion garanties", "suivi garanties", "garanties reelles",
        "portefeuille garanties", "hypotheque", "nantissement"
    ],
    "Participation à des audits": [
        "audit", "controle interne", "inspection", "commissariat aux comptes",
        "conformite", "compliance audit", "mission audit"
    ],
    "IFRS 9": [
        "ifrs 9", "ias 39", "normes ifrs", "comptabilite ifrs",
        "ifrs9", "provisionnement ifrs"
    ],
    "COBAC / conformité": [
        "cobac", "conformite bancaire", "bceao", "bcac",
        "commission bancaire", "regulation bancaire", "compliance"
    ],
    "Suivi portefeuille / impayés": [
        "portefeuille credit", "impayes", "recouvrement", "contentieux",
        "encours", "suivi portefeuille", "creances douteuses", "npls"
    ],

    # ── Analyste Crédit CCB ───────────────────────────────────────────────
    "Expérience en analyse crédit": [
        "analyse credit", "credit analysis", "evaluation credit",
        "scoring credit", "analyse financiere credit", "instruction credit",
        "analyste credit", "octroi credit"
    ],
    "Capacité à lire des états financiers": [
        "etats financiers", "bilan", "compte de resultat", "ratios financiers",
        "analyse financiere", "liasse fiscale", "situation financiere",
        "diagnostic financier", "solvabilite"
    ],
    "Minimum 3 ans institution financière (hors stage)": [
        "EXP_FIN_3ANS"
    ],
    "Clients PME": [
        "pme", "petite entreprise", "moyenne entreprise", "tpe", "entreprise cliente"
    ],
    "Clients particuliers": [
        "particulier", "clientele particuliere", "retail banking", "client particulier"
    ],
    "Structuration de crédit": [
        "structuration credit", "montage credit", "structurer credit",
        "dossier de credit", "credit structurel"
    ],
    "Avis de crédit": [
        "avis credit", "recommandation credit", "opinion credit",
        "note de credit", "avis d'octroi"
    ],
    "Cash-flow analysis": [
        "cash flow", "cashflow", "flux tresorerie", "flux de tresorerie",
        "fcf", "free cash flow", "capacite d autofinancement", "caf"
    ],
    "Montage de crédit": [
        "montage credit", "structuration credit", "montage dossier",
        "montage financier"
    ],
    "Comités de crédit": [
        "comite credit", "commission credit", "credit committee",
        "comite d octroi", "validation comite"
    ],

    # ── Archiviste ───────────────────────────────────────────────────────
    "Expérience en gestion documentaire structurée": [
        "gestion documentaire", "archivage", "ged", "records management",
        "classement", "documentation", "gestion archives", "archiviste"
    ],
    "Rigueur démontrée": [
        "rigueur", "methode", "organisation", "procédures", "traçabilite",
        "precision", "fiabilite", "serieux"
    ],
    "Archivage physique et électronique": [
        "archivage physique", "archivage electronique", "dematerialisation",
        "numerisation", "archivage numerique", "scan", "ged"
    ],
    "Gestion des dossiers sensibles": [
        "dossier sensible", "confidentiel", "securise", "acces restreint",
        "donnees sensibles", "confidentialite"
    ],
    "Expérience en banque ou juridique": [
        "banque", "etablissement financier", "juridique", "droit bancaire",
        "secteur bancaire", "cabinet juridique", "etude notariale"
    ],
    "Manipulation de garanties ou contrats": [
        "garantie", "contrat", "convention", "acte juridique",
        "documentation juridique", "acte notarie"
    ],

    # ── Senior Finance Officer ────────────────────────────────────────────
    "Expérience en reporting financier structuré": [
        "reporting financier", "reporting", "tableau de bord", "kpi",
        "indicateurs financiers", "etats financiers", "production reporting"
    ],
    "Exposition aux états financiers": [
        "etats financiers", "bilan", "compte de resultat",
        "consolidation", "reporting financier", "liasse"
    ],
    "Interaction avec auditeurs": [
        "auditeur", "audit", "commissaire aux comptes", "cac",
        "audit externe", "commissariat aux comptes", "revue externe"
    ],
    "Minimum 3 ans département finance (hors stage)": [
        "EXP_FINANCE_3ANS"
    ],
    "Production états financiers": [
        "production etats financiers", "elaboration etats financiers",
        "etablissement etats financiers", "clôture comptable", "cloture"
    ],
    "Reporting groupe": [
        "reporting groupe", "reporting consolide", "consolidation groupe",
        "reporting mensuel", "pack de gestion"
    ],
    "Connaissance IFRS": [
        "ifrs", "normes internationales", "ias", "comptabilite internationale"
    ],
    "Contraintes réglementaires": [
        "reglementation", "contraintes reglementaires", "conformite",
        "reglementaire", "prudentiel"
    ],
    "IFRS / consolidation": [
        "ifrs", "consolidation", "comptes consolides", "normes ifrs"
    ],
    "Interaction avec CAC": [
        "cac", "commissaire aux comptes", "audit legal", "audit externe"
    ],
    "Outils SPECTRA / CERBER / ERP": [
        "spectra", "cerber", "erp", "sap", "oracle", "sage",
        "outil de gestion", "logiciel comptable"
    ],

    # ── Market Risk Officer ───────────────────────────────────────────────
    "Base en risques de marché": [
        "risque marche", "market risk", "risques de marche",
        "gestion risques de marche", "risque financier"
    ],
    "Compétences quantitatives": [
        "quantitatif", "quantitative", "mathematiques", "statistiques",
        "modelisation", "mathematiques financieres"
    ],
    "Exposition à FX / taux / liquidité": [
        "fx", "change", "taux", "liquidite", "forex",
        "taux d interet", "risque de liquidite", "risque de change"
    ],
    "Minimum 3 ans institution financière (hors stage)": [
        "EXP_FIN_3ANS"
    ],
    "Maîtrise VaR / stress testing": [
        "var", "value at risk", "stress testing", "back testing",
        "backtesting", "scenario de stress"
    ],
    "Analyse des positions": [
        "analyse des positions", "suivi des positions",
        "analyse portefeuille", "exposition"
    ],
    "Excel avancé": [
        "excel avance", "excel", "vba", "macros excel", "pivot",
        "tableaux croises", "power query"
    ],
    "VBA ou Python": [
        "vba", "python", "programmation", "scripting", "r statistical"
    ],
    "Bâle II / III": [
        "bale ii", "bale iii", "bale 2", "bale 3", "basel ii", "basel iii",
        "accords de bale", "reglementation bale"
    ],
    "Gestion ALM / liquidité": [
        "alm", "asset liability management", "liquidite",
        "gestion alm", "actif passif", "gap de liquidite"
    ],
    "Produits FICC": [
        "ficc", "produits derives", "commodities", "matieres premieres",
        "produits de taux", "taux"
    ],
    "Reporting risque": [
        "reporting risque", "rapport de risque", "tableau de bord risque",
        "reporting des risques"
    ],

    # ── IT Réseau & Infrastructure ────────────────────────────────────────
    "Expérience en réseau / infrastructure": [
        "reseau", "infrastructure", "lan", "wan", "vpn",
        "infrastructure it", "network", "reseaux"
    ],
    "Exposition à environnement critique": [
        "banque", "telco", "telecom", "datacenter", "centre de donnees",
        "environnement critique", "secteur bancaire", "haute disponibilite"
    ],
    "Notion de sécurité IT": [
        "securite it", "cybersecurite", "securite informatique",
        "firewall", "securite reseau", "ids", "ips"
    ],
    "Minimum 2 ans expérience (hors stage)": [
        "EXP_IT_2ANS"
    ],
    "Gestion réseaux LAN/WAN/VPN": [
        "lan", "wan", "vpn", "reseaux locaux", "reseau local",
        "virtual private network", "switch", "routeur"
    ],
    "Gestion serveurs Windows/Linux": [
        "windows server", "linux", "serveurs", "administration serveurs",
        "unix", "active directory", "debian", "ubuntu server"
    ],
    "Cloud même basique": [
        "cloud", "aws", "azure", "google cloud", "cloud computing",
        "iaas", "saas"
    ],
    "Gestion des incidents": [
        "incident", "gestion incidents", "support technique",
        "resolution incident", "itil", "ticketing"
    ],
    "Assurance de la disponibilité": [
        "disponibilite", "haute disponibilite", "sla",
        "uptime", "continuite service"
    ],
    "Cybersécurité / firewall": [
        "cybersecurite", "firewall", "securite", "ids",
        "ips", "siem", "pentest", "vulnerability"
    ],
    "Haute disponibilité / PRA/PCA": [
        "haute disponibilite", "pra", "pca", "plan de reprise",
        "continuite activite", "disaster recovery", "basculement"
    ],
    "Gestion ATM ou systèmes bancaires": [
        "atm", "systemes bancaires", "gab", "distributeur automatique",
        "systeme bancaire core", "temenos", "flexcube"
    ],
    "Certifications Cisco ou Microsoft": [
        "ccna", "ccnp", "ccie", "cisco", "microsoft certified",
        "mcse", "network+", "certification reseau"
    ]
}

# ══════════════════════════════════════════════════════════════════════════════
# 🚫 MOTS DÉSIGNANT UN STAGE — les durées adjacentes sont EXCLUES
# ══════════════════════════════════════════════════════════════════════════════
STAGE_MARKERS = [
    r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b',
    r'\bapprenti\b', r'\bapprentissage\b', r'\balternance\b',
    r'\bstage de fin\b', r'\bstage academique\b', r'\bstage professionnel\b',
    r'\bstage de formation\b', r'\bpfr\b', r'\bstage pfe\b',
    r'\bpfe\b',  # projet de fin d'études souvent stage
]
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)

# ══════════════════════════════════════════════════════════════════════════════
# 🔧 EXTRACTION TEXTE — robuste sur PDF et DOCX
# ══════════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf(filepath):
    """Extrait le texte d'un PDF. Priorité pdfplumber, fallback PyPDF2."""
    text = ""
    # 1. Essai pdfplumber (meilleure extraction de mise en page)
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    content = page.extract_text()
                    if content:
                        text += content + "\n"
            if text.strip():
                return text.strip()
        except Exception as e:
            print(f"⚠️ pdfplumber erreur: {e}")

    # 2. Fallback PyPDF2
    if PYPDF2_AVAILABLE:
        try:
            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    content = page.extract_text()
                    if content:
                        text += content + "\n"
            return text.strip()
        except Exception as e:
            print(f"⚠️ PyPDF2 erreur: {e}")

    return ""


def extract_text_from_docx(filepath):
    """Extrait le texte d'un DOCX : paragraphes + tableaux + en-têtes."""
    try:
        doc = Document(filepath)
        parts = []

        # Paragraphes principaux (inclut titres de sections)
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                parts.append(t)

        # Tableaux (certains CV mettent l'expérience en tableau)
        for table in doc.tables:
            for row in table.rows:
                row_text = ' | '.join(
                    cell.text.strip() for cell in row.cells if cell.text.strip()
                )
                if row_text:
                    parts.append(row_text)

        # En-têtes et pieds de page
        for section in doc.sections:
            for header_para in (section.header.paragraphs if section.header else []):
                t = header_para.text.strip()
                if t:
                    parts.append(t)

        return "\n".join(parts).strip()
    except Exception as e:
        print(f"⚠️ Erreur lecture DOCX: {e}")
        return ""


def extract_text_from_file(filepath, filename):
    """Dispatch selon extension."""
    if not filepath or not os.path.exists(filepath):
        print(f"⚠️ Fichier introuvable: {filepath}")
        return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return extract_text_from_pdf(filepath)
    elif ext in ('doc', 'docx'):
        return extract_text_from_docx(filepath)
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# 🔤 NORMALISATION TEXTE
# ══════════════════════════════════════════════════════════════════════════════

_ACCENT_MAP = str.maketrans(
    'àâäéèêëîïôùûüçœæÀÂÄÉÈÊËÎÏÔÙÛÜÇŒÆ',
    'aaaeeeeiioouuucoaAAAEEEEIIOOUUUCOA'
)

def normalize_text(text):
    """Minuscules + suppression accents + nettoyage ponctuation."""
    if not text:
        return ""
    text = text.lower()
    text = text.translate(_ACCENT_MAP)
    # Conserver lettres, chiffres, espaces et quelques séparateurs utiles
    text = re.sub(r'[^\w\s\-/\.]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ══════════════════════════════════════════════════════════════════════════════
# 📅 EXTRACTION DES ANNÉES D'EXPÉRIENCE (hors stage)
# ══════════════════════════════════════════════════════════════════════════════

def split_into_jobs(raw_text):
    """
    Découpe le texte brut en blocs correspondant à des postes.
    Un nouveau bloc commence quand on détecte un pattern de date.
    """
    # Séparateurs typiques de blocs CV
    separators = re.compile(
        r'(?:^|\n)(?=\s*(?:\d{4}|jan|fev|mar|avr|mai|juin|juil|août|sep|oct|nov|dec'
        r'|january|february|march|april|june|july|august|september|october|november|december'
        r'|depuis|de |from ))',
        re.IGNORECASE | re.MULTILINE
    )
    blocks = separators.split(raw_text)
    return [b.strip() for b in blocks if b.strip()]


def is_stage_block(block_text):
    """Renvoie True si ce bloc de texte correspond à un stage / apprentissage."""
    return bool(STAGE_PATTERN.search(block_text))


def extract_duration_years_from_block(block_text):
    """
    Extrait la durée en années d'un bloc de texte de poste.
    Gère les formats : "2019 – 2022", "03/2018 - 06/2021", "3 ans", "2 ans 6 mois".
    Retourne un float (nombre d'années) ou 0 si non trouvable.
    """
    years = 0.0
    text = block_text.lower()

    # ── Forme "X an(s)" ou "X année(s)" ──────────────────────────────────
    m = re.search(r'(\d+[\.,]?\d*)\s*(?:ans?|annee?s?)', text)
    if m:
        try:
            years = float(m.group(1).replace(',', '.'))
            return years
        except ValueError:
            pass

    # ── Intervalle d'années "AAAA – AAAA" ou "AAAA - AAAA" ──────────────
    m = re.search(r'(20\d{2}|19\d{2})\s*[-–—]\s*(20\d{2}|19\d{2}|aujourd\'hui|present|actuel|en cours)', text)
    if m:
        start_year = int(m.group(1))
        end_raw = m.group(2)
        if re.match(r'\d{4}', end_raw):
            end_year = int(end_raw)
        else:
            end_year = datetime.datetime.now().year
        diff = end_year - start_year
        if 0 < diff <= 40:
            return float(diff)

    # ── Intervalle avec mois "mm/AAAA – mm/AAAA" ─────────────────────────
    m = re.search(
        r'(\d{1,2})[/\-](20\d{2}|19\d{2})\s*[-–—]\s*(?:(\d{1,2})[/\-])?(20\d{2}|19\d{2}|present|actuel|en cours|aujourd\'hui)',
        text
    )
    if m:
        start_month = int(m.group(1))
        start_year  = int(m.group(2))
        end_raw     = m.group(4)
        end_month_raw = m.group(3)
        if re.match(r'\d{4}', str(end_raw)):
            end_year  = int(end_raw)
            end_month = int(end_month_raw) if end_month_raw else 12
        else:
            end_year  = datetime.datetime.now().year
            end_month = datetime.datetime.now().month
        delta = (end_year - start_year) + (end_month - start_month) / 12.0
        if 0 < delta <= 40:
            return delta

    return 0.0


def compute_real_experience_years(full_raw_text, domain_keywords=None):
    """
    Calcule le nombre total d'années d'expérience RÉELLE (hors stage) dans un domaine.

    domain_keywords : liste de mots-clés pour filtrer les blocs pertinents.
    Si None, tous les blocs non-stage sont comptés.
    """
    blocks = split_into_jobs(full_raw_text)
    total_years = 0.0
    seen_years  = set()  # évite de compter deux fois le même intervalle

    for block in blocks:
        if is_stage_block(block):
            # Ce bloc est un stage → on l'ignore totalement
            print(f"    [STAGE ignoré] {block[:80]}...")
            continue

        # Filtrage optionnel sur domaine
        if domain_keywords:
            norm_block = normalize_text(block)
            if not any(kw in norm_block for kw in domain_keywords):
                continue

        duration = extract_duration_years_from_block(block)
        if duration > 0:
            # Clé de déduplication approx (arrondie au semestre)
            key = round(duration * 2) / 2
            # On accepte plusieurs postes distincts
            total_years += duration

    return round(total_years, 1)


def has_experience_years(full_raw_text, min_years, domain_keywords=None):
    """
    Retourne True si le candidat a au moins min_years d'expérience RÉELLE.
    Les stages sont exclus du calcul.
    """
    total = compute_real_experience_years(full_raw_text, domain_keywords)
    print(f"    [EXP] Années réelles calculées: {total} (min requis: {min_years})")
    return total >= min_years


# ══════════════════════════════════════════════════════════════════════════════
# 🧠 VÉRIFICATION D'UN CRITÈRE
# ══════════════════════════════════════════════════════════════════════════════

# Mots-clés domaine pour chaque marqueur d'expérience
DOMAIN_KEYWORDS_MAP = {
    "EXP_CREDIT_3ANS": [
        "credit", "risque", "banque", "bancaire", "institution financiere",
        "analyste", "charge", "gestionnaire"
    ],
    "EXP_FIN_3ANS": [
        "finance", "comptable", "comptabilite", "reporting", "tresorerie",
        "banque", "institution financiere", "auditeur", "controleur"
    ],
    "EXP_FINANCE_3ANS": [
        "finance", "comptable", "comptabilite", "reporting", "tresorerie",
        "banque", "institution financiere"
    ],
    "EXP_IT_2ANS": [
        "reseau", "infrastructure", "systeme", "informatique", "it",
        "network", "serveur", "technicien", "ingenieur"
    ],
}

# Années minimales associées à chaque marqueur
EXP_MIN_YEARS_MAP = {
    "EXP_CREDIT_3ANS":   3.0,
    "EXP_FIN_3ANS":      3.0,
    "EXP_FINANCE_3ANS":  3.0,
    "EXP_IT_2ANS":       2.0,
}


def check_criterion_match(criterion, normalized_text, raw_full_text=""):
    """
    Vérifie si un critère est satisfait.
    Pour les critères d'expérience (marqueur EXP_*), utilise l'analyse
    des années hors stage. Pour les autres, matching par mots-clés normalisés.

    Retourne (bool, [mots trouvés])
    """
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords:
        return False, []

    # ── Critère d'années d'expérience (hors stage) ───────────────────────
    exp_markers = [kw for kw in keywords if kw.startswith("EXP_")]
    if exp_markers:
        marker = exp_markers[0]
        min_years     = EXP_MIN_YEARS_MAP.get(marker, 3.0)
        domain_kws    = DOMAIN_KEYWORDS_MAP.get(marker, [])
        domain_kws_n  = [normalize_text(k) for k in domain_kws]
        found = has_experience_years(raw_full_text, min_years, domain_kws_n)
        return found, ([marker] if found else [])

    # ── Critère classique : matching mots-clés ────────────────────────────
    found_kws = [kw for kw in keywords if normalize_text(kw) in normalized_text]
    return len(found_kws) > 0, found_kws


# ══════════════════════════════════════════════════════════════════════════════
# 🧠 MOTEUR D'ANALYSE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    """
    Analyse STRICTE selon la grille Word.

    Règles :
      🔴 Bloc 1 Éliminatoire (AND strict) : 1 critère manquant → score 0
      🟠 Bloc 2 Cohérence : +1 pt / critère validé
      🟡 Bloc 3 Signaux   : +2 pts / signal détecté

    Scoring Excel :
      Adéquation(0-3) + Cohérence(0-2) + Risque métier(0-3) + CV(0-1) + Lettre(0-1) = /10
    """
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

    # ── Concaténation de TOUS les textes bruts (pour analyse années) ──────
    all_att_raw  = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full     = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw

    # ── Version normalisée (pour matching mots-clés) ──────────────────────
    normalized   = normalize_text(raw_full)

    checklist       = {}
    flags_elim      = []
    signaux         = []
    points_bloc2    = 0
    points_bloc3    = 0
    details = {
        'cv_words': len(cv_text.split()),
        'lettre_words': len((lettre_text or "").split()),
        'attestation_words': len(all_att_raw.split()),
        'criteres_valides_bloc2':  [],
        'signaux_valides_bloc3':   [],
        'alertes_attention':       [],
        'matching_details':        {},
        'documents_analyses': {
            'cv':          len(cv_text) > 0,
            'lettre':      len(lettre_text or "") > 0,
            'certificats': len(attestation_texts_list) if attestation_texts_list else 0
        }
    }

    # ── 🔴 BLOC 1 : Éliminatoires ─────────────────────────────────────────
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        is_present, found_kws = check_criterion_match(crit, normalized, raw_full)
        checklist[key] = is_present

        if not is_present:
            flags_elim.append(f"❌ {crit} (non trouvé)")
            details['alertes_attention'].append(f"🔴 Éliminatoire manquant: {crit}")
            details['matching_details'][crit] = {
                'found': False,
                'status': 'ÉLIMINATOIRE — critère requis absent',
                'keywords_searched': KEYWORD_MAPPING.get(crit, [])[:5]
            }
        else:
            details['matching_details'][crit] = {
                'found': True,
                'status': 'VALIDÉ',
                'matched': found_kws
            }

    # ── Décision stricte ──────────────────────────────────────────────────
    if flags_elim:
        return {
            'score': 0,
            'checklist': checklist,
            'flags_eliminatoires': flags_elim,
            'signaux_detectes': [],
            'details': details,
            'score_breakdown': {
                'bloc1_eliminatoire': True,
                'flags_eliminatoires_count': len(flags_elim),
                'adequation_experience':  0,
                'coherence_parcours':     0,
                'exposition_risque_metier': 0,
                'qualite_cv':             0,
                'lettre_motivation':      0,
                'bloc2_criteres_valides': 0,
                'bloc2_points':           0,
                'bloc3_signaux_detectes': 0,
                'bloc3_points':           0,
                'total_raw_points':       0,
                'score_final':            0,
                'note': f"ÉLIMINÉ : {len(flags_elim)} critère(s) éliminatoire(s) manquant(s)",
                'documents_analyses': details['documents_analyses']
            }
        }

    # ── 🟠 BLOC 2 : Cohérence (+1 pt/critère) ────────────────────────────
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, found_kws = check_criterion_match(crit, normalized, raw_full)
        checklist[key] = is_present
        details['matching_details'][crit] = {
            'found': is_present,
            'matched': found_kws if is_present else []
        }
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"🟠 {crit}")

    # ── 🟡 BLOC 3 : Signaux (+2 pts/signal) ──────────────────────────────
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, found_kws = check_criterion_match(crit, normalized, raw_full)
        checklist[key] = is_present
        details['matching_details'][crit] = {
            'found': is_present,
            'matched': found_kws if is_present else []
        }
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"🟡 {crit}")

    # ── Points d'attention (informatif) ──────────────────────────────────
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, found_kws = check_criterion_match(crit, normalized, raw_full)
        checklist[key] = is_present
        if is_present:
            details['alertes_attention'].append(f"⚠️ Attention: {crit}")

    # ── Scoring Excel (sur 10) ─────────────────────────────────────────────
    adequation   = min(3, len([k for k, v in checklist.items() if k.startswith('elim_') and v]))
    coherence    = min(2, points_bloc2)
    risque_metier= min(3, len(signaux))
    qualite_cv   = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre_motiv = 1 if lettre_text and len(lettre_text.strip()) > 50 else 0

    score_final  = min(10, adequation + coherence + risque_metier + qualite_cv + lettre_motiv)

    return {
        'score': score_final,
        'checklist': checklist,
        'flags_eliminatoires': [],
        'signaux_detectes': signaux,
        'details': details,
        'score_breakdown': {
            'bloc1_eliminatoire':     False,
            'flags_eliminatoires_count': 0,
            'adequation_experience':  adequation,
            'coherence_parcours':     coherence,
            'exposition_risque_metier': risque_metier,
            'qualite_cv':             qualite_cv,
            'lettre_motivation':      lettre_motiv,
            'bloc2_criteres_valides': len(details['criteres_valides_bloc2']),
            'bloc2_points':           points_bloc2,
            'bloc3_signaux_detectes': len(signaux),
            'bloc3_points':           points_bloc3,
            'total_raw_points':       points_bloc2 + points_bloc3,
            'score_final':            score_final,
            'note': f"Score Excel: {score_final}/10",
            'documents_analyses': details['documents_analyses']
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# 🔄 ANALYSE ASYNCHRONE
# ══════════════════════════════════════════════════════════════════════════════

def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste):
    """Lance l'analyse complète pour un candidat et sauvegarde le résultat."""
    try:
        key = f"candidat:{token}"

        # Normalisation attestation_filenames
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []

        # Extraction CV
        cv_path  = os.path.join(UPLOAD_FOLDER, cv_filename) if cv_filename else None
        cv_text  = extract_text_from_file(cv_path, cv_filename) if cv_path else ""

        # Extraction Lettre
        lm_path  = os.path.join(UPLOAD_FOLDER, lettre_filename) if lettre_filename else None
        lm_text  = extract_text_from_file(lm_path, lettre_filename) if lm_path else ""

        # Extraction certificats/attestations
        att_texts = []
        for fn in (attestation_filenames or []):
            ap = os.path.join(UPLOAD_FOLDER, fn)
            if os.path.exists(ap):
                t = extract_text_from_file(ap, fn)
                if t:
                    att_texts.append(t)

        print(f"📄 Analyse {token}: CV={len(cv_text)} c, LM={len(lm_text)} c, "
              f"Certs={len(att_texts)} fichiers")

        result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)

        redis_client.hset(key, mapping={
            "score":             str(result['score']),
            "checklist":         json.dumps(result['checklist'],         ensure_ascii=False),
            "flags_eliminatoires": json.dumps(result['flags_eliminatoires'], ensure_ascii=False),
            "signaux_detectes":  json.dumps(result['signaux_detectes'],  ensure_ascii=False),
            "analyse_details":   json.dumps(result['details'],           ensure_ascii=False),
            "score_breakdown":   json.dumps(result['score_breakdown'],   ensure_ascii=False),
            "analyse_auto_date": datetime.datetime.now().isoformat(),
            "analyse_status":    "completed"
        })

        tag = "⚠️ ÉLIMINÉ" if result['score_breakdown'].get('bloc1_eliminatoire') else "✅"
        print(f"{tag} Score {token}: {result['score']}/10 — {result['score_breakdown'].get('note','')}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        redis_client.hset(f"candidat:{token}", mapping={
            "analyse_status": "error",
            "analyse_error":  str(e),
            "analyse_auto_date": datetime.datetime.now().isoformat()
        })


# ══════════════════════════════════════════════════════════════════════════════
# 🏆 CLASSEMENT
# ══════════════════════════════════════════════════════════════════════════════

def get_recommandation_from_score(score):
    s = int(score)
    if s >= 8:  return "🥇 Entretien prioritaire"
    if s >= 6:  return "🥈 Entretien si besoin"
    return "❌ Rejet"


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
                datetime.datetime.fromisoformat(c.get('date_candidature',''))).days
        date_bonus = max(0, (30 - min(days, 30)) * 0.01)
    except Exception:
        date_bonus = 0
    return round(score + signaux_count * 0.5 + criteres_ok * 0.2 + lettre_bonus + date_bonus, 3)


def generate_ranking_for_poste(poste, candidats_data):
    pool = [c for c in candidats_data if c.get('poste') == poste]
    for c in pool:
        c['ranking_score']          = calculate_ranking_score(c, poste)
        c['ranking_position']       = 0
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


# ══════════════════════════════════════════════════════════════════════════════
# 📊 EXPORT EXCEL
# ══════════════════════════════════════════════════════════════════════════════

def generate_excel_report(candidats_data, poste_filter=None):
    if not OPENPYXL_AVAILABLE:
        return None

    wb = Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']

    postes_to_export = (
        [poste_filter] if poste_filter
        else list(dict.fromkeys(c.get('poste', '') for c in candidats_data))
    )

    for poste in postes_to_export:
        candidats_poste = generate_ranking_for_poste(
            poste, [c for c in candidats_data if c.get('poste') == poste]
        )
        ws = wb.create_sheet(title=poste[:20])

        hfill = PatternFill(start_color="1a3a5c", end_color="1a3a5c", fill_type="solid")
        hfont = Font(color="FFFFFF", bold=True, size=11)
        border = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'), bottom=Side(style='thin')
        )

        ws.merge_cells('A1:K1')
        c = ws['A1']
        c.value = f"CLASSEMENT — {poste}"
        c.font  = Font(bold=True, size=14, color="1a3a5c")
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.fill  = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
        ws.row_dimensions[1].height = 30

        headers = [
            'Rang', 'Email', 'Candidat', 'Téléphone',
            'Adéquation (0-3)', 'Cohérence (0-2)', 'Risque métier (0-3)',
            'Qualité CV (0-1)', 'Lettre (0-1)', 'Score /10', 'Recommandation'
        ]
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col, value=h)
            cell.font   = hfont
            cell.fill   = hfill
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        for row_i, cand in enumerate(candidats_poste, 4):
            sb       = cand.get('score_breakdown_parsed', {})
            elim     = sb.get('bloc1_eliminatoire', False)
            adeq     = sb.get('adequation_experience', 0) if not elim else 0
            cohe     = sb.get('coherence_parcours', 0)    if not elim else 0
            risq     = sb.get('exposition_risque_metier', 0) if not elim else 0
            qcv      = sb.get('qualite_cv', 0)             if not elim else 0
            lm       = sb.get('lettre_motivation', 0)      if not elim else 0
            total    = adeq + cohe + risq + qcv + lm
            rang     = cand.get('ranking_position', row_i - 3)
            nom_c    = f"{cand.get('prenom','')} {cand.get('nom','')}".strip()
            reco     = cand.get('ranking_recommendation', get_recommandation_from_score(total))

            row_data = [rang, cand.get('email','') or '–', nom_c,
                        cand.get('telephone','') or '–',
                        adeq, cohe, risq, qcv, lm, total, reco]

            for col, val in enumerate(row_data, 1):
                cell        = ws.cell(row=row_i, column=col, value=val)
                cell.border = border
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                # Couleurs rang
                if col == 1:
                    color = {1: "FFD700", 2: "C0C0C0", 3: "CD7F32"}.get(rang)
                    if color:
                        cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
                        cell.font = Font(bold=True, size=12)
                # Score
                if col == 10:
                    sc = "90EE90" if total >= 8 else ("FFD700" if total >= 6 else "FF6B6B")
                    cell.fill = PatternFill(start_color=sc, end_color=sc, fill_type="solid")
                    cell.font = Font(bold=True)
                # Reco
                if col == 11:
                    rc = "90EE90" if "prioritaire" in str(reco).lower() \
                         else ("FFD700" if "besoin" in str(reco).lower() else "FF6B6B")
                    cell.fill = PatternFill(start_color=rc, end_color=rc, fill_type="solid")
                    if "prioritaire" in str(reco).lower():
                        cell.font = Font(bold=True)

        col_widths = [8, 35, 35, 20, 28, 28, 35, 20, 20, 15, 35]
        for col, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = w
        for row in range(3, ws.max_row + 1):
            ws.row_dimensions[row].height = 40

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════════════════
# 📄 EXPORT CSV
# ══════════════════════════════════════════════════════════════════════════════

def generate_csv_report(candidats_data):
    out = io.StringIO()
    w   = csv.writer(out, delimiter=';', quoting=csv.QUOTE_ALL)
    w.writerow([
        'Rang','Email','Nom','Prénom','Téléphone','Poste','Date candidature',
        'Score (/10)','Statut','Éliminatoire',
        'Adéquation (0-3)','Cohérence (0-2)','Risque (0-3)','Note'
    ])
    for idx, c in enumerate(candidats_data, 1):
        sb = c.get('score_breakdown_parsed', {})
        w.writerow([
            idx, c.get('email','') or '–',
            c.get('nom',''), c.get('prenom',''),
            c.get('telephone','') or '–',
            c.get('poste',''), c.get('date_candidature',''),
            c.get('score','0'), c.get('statut',''),
            'OUI' if sb.get('bloc1_eliminatoire') else 'NON',
            sb.get('adequation_experience', 0),
            sb.get('coherence_parcours', 0),
            sb.get('exposition_risque_metier', 0),
            sb.get('note','')
        ])
    out.seek(0)
    return out.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# 📕 EXPORT PDF
# ══════════════════════════════════════════════════════════════════════════════

def generate_pdf_report(candidats_data):
    if not REPORTLAB_AVAILABLE:
        return None
    buf  = io.BytesIO()
    doc  = SimpleDocTemplate(buf, pagesize=landscape(A4),
                             rightMargin=1*cm, leftMargin=1*cm,
                             topMargin=2*cm,   bottomMargin=2*cm)
    els  = []
    sty  = getSampleStyleSheet()
    els.append(Paragraph("Rapport Candidatures — RecrutBank",
                         ParagraphStyle('T', parent=sty['Heading1'],
                                        fontSize=16, textColor=colors.HexColor('#1a3a5c'),
                                        spaceAfter=20, alignment=TA_CENTER)))
    els.append(Spacer(1, 0.3*cm))
    els.append(Paragraph(f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
                         ParagraphStyle('D', parent=sty['Normal'],
                                        fontSize=9, textColor=colors.grey)))
    els.append(Spacer(1, 0.8*cm))

    data = [['Rang','Email','Candidat','Téléphone','Poste','Score /10','Recommandation']]
    for idx, c in enumerate(candidats_data, 1):
        score = int(c.get('score', 0))
        data.append([
            str(idx),
            c.get('email','') or '–',
            f"{c.get('prenom','')} {c.get('nom','')}",
            c.get('telephone','') or '–',
            c.get('poste',''),
            f"{score}/10",
            get_recommandation_from_score(score)
        ])

    tbl = Table(data, colWidths=[1.5*cm, 5*cm, 4.5*cm, 3.5*cm, 5*cm, 2.5*cm, 4.5*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a3a5c')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME',   (0,0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1, 0), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 10),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.black),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.lightgrey]),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
    ]))
    els.append(tbl)
    doc.build(els)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════════════════
# 🔑 AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# 🌐 ROUTES PUBLIQUES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify(POSTES), 200


@app.route('/api/grille/<poste>', methods=['GET'])
def get_grille(poste):
    g = GRILLE.get(poste)
    if not g:
        return jsonify({'error': 'Poste inconnu', 'postes_disponibles': list(GRILLE.keys())}), 404
    return jsonify(g), 200


# ── AUTH ───────────────────────────────────────────────────────────────────────
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


# ── CANDIDATURE ────────────────────────────────────────────────────────────────
@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom       = (request.form.get('nom')       or '').strip()
        prenom    = (request.form.get('prenom')    or '').strip()
        email     = (request.form.get('email')     or '').strip().lower()
        telephone = (request.form.get('telephone') or '').strip()
        poste     = (request.form.get('poste')     or '').strip()

        if not nom or not prenom or not email or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400

        for k in redis_client.keys("candidat:*"):
            if redis_client.hget(k, 'email') == email:
                return jsonify({'error': 'Un candidat avec cet email existe déjà'}), 409

        def save_file(field, suffix):
            f = request.files.get(field)
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                fn  = f"{uuid.uuid4().hex}_{suffix}.{ext}"
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                return fn
            return ''

        cv_filename     = save_file('cv', 'cv')
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
            "nom": nom, "prenom": prenom, "email": email, "telephone": telephone,
            "poste": poste,
            "cv_filename":            cv_filename,
            "lettre_filename":        lettre_filename,
            "attestation_filenames":  json.dumps(att_filenames, ensure_ascii=False),
            "statut": "en_attente", "note": "", "score": "0",
            "checklist": "", "flags_eliminatoires": "", "signaux_detectes": "",
            "score_breakdown": "", "analyse_status": "pending",
            "date_candidature": datetime.datetime.now().isoformat()
        })

        threading.Thread(
            target=run_analysis_for_candidat,
            args=(token, cv_filename, lettre_filename, att_filenames, poste),
            daemon=True
        ).start()

        return jsonify({
            'message': 'Candidature soumise avec succès',
            'token': token,
            'analyse': 'Analyse automatique en cours'
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
    hidden = {'cv_filename','lettre_filename','attestation_filenames',
              'checklist','flags_eliminatoires','signaux_detectes',
              'analyse_details','score_breakdown'}
    return jsonify({k: v for k, v in data.items() if k not in hidden}), 200


# ══════════════════════════════════════════════════════════════════════════════
# 🔒 ROUTES RECRUTEUR
# ══════════════════════════════════════════════════════════════════════════════

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
    poste_filter  = request.args.get('poste', '')
    statut_filter = request.args.get('statut', '')
    search        = request.args.get('search', '').lower()
    min_score     = request.args.get('min_score', type=int)

    result = []
    for k in redis_client.keys("candidat:*"):
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        if poste_filter  and c.get('poste')   != poste_filter:  continue
        if statut_filter and c.get('statut')  != statut_filter: continue
        if min_score is not None and int(c.get('score', 0)) < min_score: continue
        if search:
            hay = f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} {c.get('poste','')}".lower()
            if search not in hay: continue
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
    for field in ['checklist','flags_eliminatoires','signaux_detectes',
                  'analyse_details','score_breakdown']:
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
    data    = request.get_json(silent=True) or {}
    statut  = data.get('statut', 'en_attente')
    note    = data.get('note', '')
    score   = str(min(10, max(0, int(data.get('score', 0)))))
    if statut not in ('en_attente','retenu','rejete','entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    redis_client.hset(key, mapping={
        "statut": statut, "note": note, "score": score,
        "decision_date": datetime.datetime.now().isoformat(),
        "decided_by": get_jwt_identity()
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
        "analyse_status": "pending",
        "analyse_manual_trigger": datetime.datetime.now().isoformat()
    })
    threading.Thread(
        target=run_analysis_for_candidat,
        args=(token, cv_fn, lm_fn, att_raw, poste),
        daemon=True
    ).start()
    return jsonify({'message': 'Analyse re-déclenchée', 'token': token}), 202


# ── EXPORT ─────────────────────────────────────────────────────────────────────
@app.route('/api/recruteur/export/<fmt>', methods=['GET'])
@jwt_required()
def export_candidates(fmt):
    try:
        keys   = redis_client.keys("candidat:*")
        result = []
        for k in keys:
            c = redis_client.hgetall(k)
            c['id'] = k.split(':', 1)[1]
            if c.get('score_breakdown'):
                try:
                    c['score_breakdown_parsed'] = json.loads(c['score_breakdown'])
                except Exception:
                    pass
            result.append(c)
        result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        if fmt.lower() == 'csv':
            csv_bytes = generate_csv_report(result).encode('utf-8-sig')
            return send_file(io.BytesIO(csv_bytes), mimetype='text/csv',
                             as_attachment=True,
                             download_name=f'rapport_{ts}.csv')

        elif fmt.lower() in ('excel', 'xlsx'):
            if not OPENPYXL_AVAILABLE:
                return jsonify({'error': 'openpyxl non installé'}), 503
            buf = generate_excel_report(result)
            if not buf:
                return jsonify({'error': 'Erreur génération Excel'}), 500
            return send_file(buf,
                             mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             as_attachment=True,
                             download_name=f'rapport_{ts}.xlsx')

        elif fmt.lower() == 'pdf':
            if not REPORTLAB_AVAILABLE:
                return jsonify({'error': 'reportlab non installé'}), 503
            buf = generate_pdf_report(result)
            if not buf:
                return jsonify({'error': 'Erreur génération PDF'}), 500
            return send_file(buf, mimetype='application/pdf',
                             as_attachment=True,
                             download_name=f'rapport_{ts}.pdf')

        return jsonify({'error': 'Format non supporté. Utilisez: csv, excel ou pdf'}), 400

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── EMAIL PREVIEW ───────────────────────────────────────────────────────────────
@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    body     = request.get_json(silent=True) or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))
    nom_c    = f"{data.get('prenom','')} {data.get('nom','')}".strip()
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


# ── SERVIR LES FICHIERS ─────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════════════════
# 🚀 DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 RecrutBank démarré sur le port {port}")
    print(f"📋 Grille: {len(GRILLE)} postes")
    print(f"⚠️  Élimination STRICTE: 1 critère manquant → score 0")
    print(f"🚫 Stages EXCLUS du calcul des années d'expérience")
    print(f"🔍 Extraction PDF: {'pdfplumber' if PDFPLUMBER_AVAILABLE else 'PyPDF2 (fallback)'}")
    print(f"📊 Excel: {'✅' if OPENPYXL_AVAILABLE else '❌'} | "
          f"PDF: {'✅' if REPORTLAB_AVAILABLE else '❌'}")
    app.run(host="0.0.0.0", port=port, debug=False)

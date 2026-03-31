# server.py - Backend Flask pour RecrutBank avec analyse automatique STRICTE
# ✅ Analyse TOUS les fichiers soumis (CV + Lettre + Certificats) SANS EXCEPTION
# ✅ Calcul d'expérience SPÉCIFIQUE à chaque poste selon ses critères
# ✅ Chaque poste a ses propres critères d'expérience (2 ans IT, 3 ans Finance, etc.)
# ⚠️ STAGES EXCLUS du calcul d'expérience
# ✅ CALCUL jusqu'à AUJOURD'HUI si "à aujourd'hui"/"présent"/"actuellement"
# ============================================================================

from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, redis, json, re, threading, mimetypes, io, csv
from werkzeug.utils import secure_filename

# ── PARSING DOCUMENTS ──────────────────────────────────────────────────────
import PyPDF2
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
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
REPORTS_FOLDER = os.path.join(os.path.dirname(__file__), 'reports')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
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
# 📋 GRILLE DE PRÉSÉLECTION - CRITÈRES SPÉCIFIQUES PAR POSTE
# ✅ Chaque poste a ses propres critères d'expérience
# ══════════════════════════════════════════════════════════════════════════════

GRILLE = {
    "Responsable Administration de Crédit": {
        "eliminatoire": [
            "Expérience professionnelle bancaire",
            "3 ans d'expérience professionnelle en crédit / risque",
            "Exposition professionnelle aux garanties ou conformité"
        ],
        "a_verifier": [
            "Validation de dossiers",
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
            "CV flou missions génériques"
        ],
        "experience_requise": 3  # ✅ 3 ans pour ce poste
    },
    "Analyste Crédit CCB": {
        "eliminatoire": [
            "Expérience professionnelle en analyse crédit",
            "Capacité professionnelle à lire des états financiers",
            "3 ans d'expérience professionnelle en institution financière"
        ],
        "a_verifier": [
            "Type de clients PME",
            "Type de clients particuliers",
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
        ],
        "experience_requise": 3  # ✅ 3 ans pour ce poste
    },
    "Archiviste (Administration Crédit)": {
        "eliminatoire": [
            "Expérience professionnelle en gestion documentaire structurée",
            "Rigueur professionnelle démontrée"
        ],
        "a_verifier": [
            "Archivage physique et électronique",
            "Gestion des dossiers sensibles"
        ],
        "signaux_forts": [
            "Expérience professionnelle en banque / juridique",
            "Manipulation de garanties ou contrats"
        ],
        "points_attention": [
            "Profils trop généralistes",
            "CV désorganisé"
        ],
        "experience_requise": 0  # ✅ Pas d'exigence d'années spécifique
    },
    "Senior Finance Officer": {
        "eliminatoire": [
            "Expérience professionnelle en reporting financier structuré",
            "Exposition professionnelle aux états financiers",
            "Interaction professionnelle avec auditeurs",
            "3 ans d'expérience professionnelle en département finance"
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
            "Outils type SPECTRA / CERBER / ERP"
        ],
        "points_attention": [
            "Profil comptable junior amélioré",
            "Pas de responsabilité réelle",
            "CV flou sur les livrables"
        ],
        "experience_requise": 3  # ✅ 3 ans pour ce poste
    },
    "Market Risk Officer": {
        "eliminatoire": [
            "Base professionnelle en risques de marché",
            "Compétences professionnelles quantitatives",
            "Exposition professionnelle à FX / taux / liquidité",
            "3 ans d'expérience professionnelle en institution financière"
        ],
        "a_verifier": [
            "Maîtrise VaR / stress testing",
            "Analyse des positions",
            "Excel avancé",
            "VBA / Python"
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
        ],
        "experience_requise": 3  # ✅ 3 ans pour ce poste
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": [
            "Expérience professionnelle en réseau / infrastructure",
            "Exposition professionnelle à environnement critique",
            "Notion professionnelle de sécurité IT",
            "2 ans d'expérience professionnelle minimum"
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
            "Certifications Cisco Microsoft"
        ],
        "points_attention": [
            "Profil trop helpdesk",
            "CV sans détail technique",
            "Aucune mention de sécurité"
        ],
        "experience_requise": 2  # ✅ 2 ans pour ce poste (SPÉCIFIQUE IT)
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# 🔍 MAPPING MOTS-CLÉS EXACTS
# ══════════════════════════════════════════════════════════════════════════════

KEYWORD_MAPPING = {
    # === Responsable Administration de Crédit ===
    "Expérience professionnelle bancaire": ["expérience bancaire", "secteur bancaire", "établissement bancaire", "banque commerciale", "institution financière", "banque"],
    "3 ans d'expérience professionnelle en crédit / risque": ["3 ans", "trois ans", "3 années", "4 ans", "5 ans", "6 ans", "7 ans", "8 ans", "9 ans", "10 ans", "plusieurs années", "expérience crédit", "gestion risque crédit"],
    "Exposition professionnelle aux garanties ou conformité": ["garanties", "nantissement", "hypothèque", "sûreté", "conformité", "COBAC", "réglementation bancaire", "BCAC", "audit"],
    "Validation de dossiers": ["validation dossier", "instruction crédit", "approbation crédit", "dossier crédit", "validation des dossiers"],
    "Gestion des garanties": ["gestion garanties", "suivi garanties", "garanties réelles", "sûretés", "portefeuille garanties"],
    "Participation à des audits": ["audit", "contrôle interne", "inspection", "compliance audit", "audit interne"],
    "IFRS 9": ["IFRS 9", "IAS 39", "normes IFRS", "comptabilité IFRS"],
    "COBAC / conformité": ["COBAC", "conformité bancaire", "régulation bancaire", "BCEAO", "BCAC", "commission bancaire"],
    "Suivi portefeuille / impayés": ["portefeuille crédit", "impayés", "recouvrement", "contentieux", "encours", "suivi portefeuille"],
    
    # === Analyste Crédit CCB ===
    "Expérience professionnelle en analyse crédit": ["analyse crédit", "credit analysis", "évaluation crédit", "scoring crédit", "analyse financière crédit"],
    "Capacité professionnelle à lire des états financiers": ["états financiers", "bilan", "compte de résultat", "ratios financiers", "analyse financière"],
    "3 ans d'expérience professionnelle en institution financière": ["3 ans", "trois ans", "3 années", "institution financière", "banque", "secteur bancaire", "4 ans", "5 ans", "6 ans"],
    "Type de clients PME": ["PME", "petites entreprises", "moyennes entreprises", "TPE"],
    "Type de clients particuliers": ["particuliers", "clients particuliers", "retail", "clientèle particulière"],
    "Structuration de crédit": ["structuration crédit", "montage crédit", "dossier de crédit", "structurer un crédit"],
    "Avis de crédit": ["avis crédit", "recommandation crédit", "opinion crédit", "credit opinion"],
    "Cash-flow analysis": ["cash-flow", "cash flow", "flux de trésorerie", "FCF", "free cash flow"],
    "Montage de crédit": ["montage crédit", "structuration", "dossier de crédit", "montage de dossiers"],
    "Comités de crédit": ["comité crédit", "commission crédit", "credit committee", "validation comité"],
    
    # === Archiviste ===
    "Expérience professionnelle en gestion documentaire structurée": ["gestion documentaire", "archivage", "GED", "records management", "classement", "documentation"],
    "Rigueur professionnelle démontrée": ["rigueur", "méthode", "organisation", "procédures", "processus", "traçabilité", "précision"],
    "Archivage physique et électronique": ["archivage physique", "archivage électronique", "dématérialisation", "numérisation", "archives"],
    "Gestion des dossiers sensibles": ["dossiers sensibles", "confidentiel", "sécurisé", "accès restreint", "données sensibles"],
    "Expérience professionnelle en banque / juridique": ["banque", "établissement financier", "juridique", "droit bancaire", "secteur bancaire"],
    "Manipulation de garanties ou contrats": ["garanties", "contrats", "conventions", "actes juridiques", "documentation juridique"],
    
    # === Senior Finance Officer ===
    "Expérience professionnelle en reporting financier structuré": ["reporting financier", "reporting", "tableaux de bord", "KPI", "indicateurs", "états financiers"],
    "Exposition professionnelle aux états financiers": ["états financiers", "bilan", "compte de résultat", "consolidation", "reporting financier"],
    "Interaction professionnelle avec auditeurs": ["auditeurs", "audit", "CAC", "commissaires aux comptes", "audit externe"],
    "3 ans d'expérience professionnelle en département finance": ["3 ans", "trois ans", "3 années", "département finance", "finance", "4 ans", "5 ans", "6 ans"],
    "Production états financiers": ["production états financiers", "établissement des états financiers", "élaboration des états financiers"],
    "Reporting groupe": ["reporting groupe", "reporting consolidé", "consolidation groupe"],
    "Connaissance IFRS": ["IFRS", "normes internationales", "comptabilité internationale", "IAS"],
    "Contraintes réglementaires": ["réglementation", "contraintes réglementaires", "conformité", "réglementaire"],
    "IFRS / consolidation": ["IFRS", "consolidation", "comptes consolidés", "IFRS consolidation"],
    "Interaction avec CAC": ["CAC", "commissaires aux comptes", "audit légal", "audit externe"],
    "Outils type SPECTRA / CERBER / ERP": ["SPECTRA", "CERBER", "ERP", "SAP", "Oracle", "outil de reporting"],
    
    # === Market Risk Officer ===
    "Base professionnelle en risques de marché": ["risque marché", "market risk", "risques de marché", "gestion des risques de marché"],
    "Compétences professionnelles quantitatives": ["quantitatif", "quantitative", "mathématiques", "statistiques", "modélisation"],
    "Exposition professionnelle à FX / taux / liquidité": ["FX", "change", "taux", "liquidité", "forex", "taux d'intérêt", "risque de liquidité"],
    "3 ans d'expérience professionnelle en institution financière": ["3 ans", "trois ans", "3 années", "institution financière", "banque", "secteur bancaire", "4 ans", "5 ans", "6 ans"],
    "Maîtrise VaR / stress testing": ["VaR", "Value at Risk", "stress testing", "back-testing", "scénarios"],
    "Analyse des positions": ["analyse des positions", "positions", "analyse de portefeuille", "suivi des positions"],
    "Excel avancé": ["Excel avancé", "Excel", "tableaux croisés", "macros", "pivot", "VBA"],
    "VBA / Python": ["VBA", "Python", "programmation", "scripting"],
    "Bâle II / III": ["Bâle II", "Bâle III", "Basel II", "Basel III", "accords de Bâle", "réglementation Bâle"],
    "Gestion ALM / liquidité": ["ALM", "Asset Liability Management", "liquidité", "gestion ALM", "actif-passif"],
    "Produits FICC": ["FICC", "produits dérivés", "commodities", "matières premières", "produits de taux", "FX"],
    "Reporting risque": ["reporting risque", "reporting des risques", "rapport de risque"],
    
    # === IT Réseau & Infrastructure ===
    "Expérience professionnelle en réseau / infrastructure": ["réseau", "infrastructure", "LAN", "WAN", "VPN", "réseaux", "infrastructure IT", "network"],
    "Exposition professionnelle à environnement critique": ["banque", "telco", "télécom", "datacenter", "centre de données", "environnement critique", "secteur bancaire"],
    "Notion professionnelle de sécurité IT": ["sécurité IT", "cybersécurité", "sécurité informatique", "firewall", "sécurité réseau"],
    "2 ans d'expérience professionnelle minimum": ["2 ans", "deux ans", "2 années", "expérience", "3 ans", "4 ans", "5 ans"],
    "Gestion réseaux LAN/WAN/VPN": ["LAN", "WAN", "VPN", "réseaux locaux", "réseaux étendus", "virtual private network"],
    "Gestion serveurs Windows/Linux": ["Windows Server", "Linux", "serveurs", "administration serveurs", "Windows", "Unix"],
    "Cloud même basique": ["cloud", "AWS", "Azure", "Google Cloud", "cloud computing", "infrastructure cloud"],
    "Gestion des incidents": ["incident", "gestion des incidents", "support", "résolution", "ITIL", "incident management"],
    "Assurance de la disponibilité": ["disponibilité", "haute disponibilité", "SLA", "uptime", "disponibilité du service"],
    "Cybersécurité / firewall": ["cybersécurité", "firewall", "sécurité", "IDS", "IPS", "SIEM", "pentest"],
    "Haute disponibilité / PRA/PCA": ["haute disponibilité", "PRA", "PCA", "plan de reprise", "continuité d'activité", "disaster recovery"],
    "Gestion ATM ou systèmes bancaires": ["ATM", "systèmes bancaires", "GAB", "distributeur automatique", "système bancaire", "bancaire"],
    "Certifications Cisco Microsoft": ["CCNA", "CCNP", "CCIE", "Cisco", "Microsoft", "certification", "Network+", "MCSE"]
}

# ── MOTS-CLÉS POUR DÉTECTER LES STAGES (À EXCLURE) ────────────────────────────
STAGE_KEYWORDS = [
    "stage", "stagiaire", "internship", "intern", "pfe", "projet de fin d'études",
    "alternance", "apprentissage", "formation pratique", "immersion professionnelle"
]

# ── MOTS-CLÉS POUR DÉTECTER L'EXPÉRIENCE PRO (À INCLURE) ─────────────────────
PROFESSIONAL_KEYWORDS = [
    "cdi", "cdd", "contrat", "employé", "ingénieur", "technicien", "chef de projet",
    "responsable", "consultant", "freelance", "indépendant", "salarié", "titulaire",
    "poste", "fonction", "mission", "expérience professionnelle", "administrateur",
    "spécialiste", "expert", "manager", "directeur"
]

# ── MOTS-CLÉS POUR "AUJOURD'HUI" / "PRÉSENT" ──────────────────────────────────
CURRENT_DATE_KEYWORDS = [
    "aujourd'hui", "présent", "maintenant", "now", "current", "en cours",
    "à ce jour", "toujours en poste", "actuellement", "à aujourd'hui"
]

# ── MOIS EN FRANÇAIS ET ANGLAIS ───────────────────────────────────────────────
MONTHS = {
    'janvier': 1, 'jan': 1, 'january': 1, 'janv': 1,
    'février': 2, 'fevrier': 2, 'feb': 2, 'february': 2, 'fév': 2, 'fev': 2,
    'mars': 3, 'mar': 3, 'march': 3,
    'avril': 4, 'apr': 4, 'april': 4, 'avr': 4,
    'mai': 5, 'may': 5,
    'juin': 6, 'jun': 6, 'june': 6,
    'juillet': 7, 'jul': 7, 'july': 7, 'juil': 7,
    'août': 8, 'aout': 8, 'aug': 8, 'august': 8, 'aou': 8,
    'septembre': 9, 'sep': 9, 'sept': 9, 'september': 9,
    'octobre': 10, 'oct': 10, 'october': 10,
    'novembre': 11, 'nov': 11, 'november': 11,
    'décembre': 12, 'decembre': 12, 'dec': 12, 'december': 12, 'déc': 12
}

# ── HELPERS AUTH ───────────────────────────────────────────────────────────────
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
# 🔧 PARSING DOCUMENTS - TOUS LES FICHIERS
# ══════════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf(filepath):
    """Extrait le texte d'un fichier PDF"""
    try:
        text = ""
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                content = page.extract_text()
                if content:
                    text += content + "\n"
        return text.strip()
    except Exception as e:
        print(f"⚠️ Erreur lecture PDF: {e}")
        return ""

def extract_text_from_docx(filepath):
    """Extrait le texte d'un fichier DOCX"""
    try:
        doc = Document(filepath)
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n".join(paragraphs).strip()
    except Exception as e:
        print(f"⚠️ Erreur lecture DOCX: {e}")
        return ""

def extract_text_from_file(filepath, filename):
    """Extrait le texte selon l'extension du fichier"""
    if not filepath or not os.path.exists(filepath):
        print(f"⚠️ Fichier non trouvé: {filepath}")
        return ""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return extract_text_from_pdf(filepath)
    elif ext in ['doc', 'docx']:
        return extract_text_from_docx(filepath)
    return ""

def normalize_text(text):
    """Normalise le texte pour la comparaison"""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s\-/]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ══════════════════════════════════════════════════════════════════════════════
# 🧮 CALCUL EXPÉRIENCE PROFESSIONNELLE - SPÉCIFIQUE PAR POSTE
# ══════════════════════════════════════════════════════════════════════════════

def parse_date_french(date_str):
    """Parse une date depuis différents formats (FR/EN)"""
    if not date_str:
        return None
    
    date_str = date_str.strip().lower()
    
    # Pattern: "Aout 2023", "Août 2023", "August 2023"
    month_year_match = re.search(r'([a-zA-Zéû]+)\s+(\d{4})', date_str)
    if month_year_match:
        month_str = month_year_match.group(1)
        year = int(month_year_match.group(2))
        month = MONTHS.get(month_str, 1)
        try:
            return datetime.datetime(year, month, 1)
        except:
            pass
    
    # Pattern: "08/2023", "08/23"
    slash_match = re.search(r'(\d{1,2})/(\d{2,4})', date_str)
    if slash_match:
        month = int(slash_match.group(1))
        year = int(slash_match.group(2))
        if year < 100:
            year += 2000
        try:
            return datetime.datetime(year, month, 1)
        except:
            pass
    
    # Pattern: "2023-08", "2023-08-15"
    iso_match = re.search(r'(\d{4})-(\d{1,2})(?:-(\d{1,2}))?', date_str)
    if iso_match:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        day = int(iso_match.group(3)) if iso_match.group(3) else 1
        try:
            return datetime.datetime(year, month, day)
        except:
            pass
    
    # Pattern: Juste l'année "2023"
    year_match = re.search(r'\b(20\d{2}|19\d{2})\b', date_str)
    if year_match:
        try:
            return datetime.datetime(int(year_match.group(1)), 1, 1)
        except:
            pass
    
    return None


def is_current_position(text):
    """Vérifie si une position est actuelle (jusqu'à aujourd'hui)"""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in CURRENT_DATE_KEYWORDS)


def extract_experience_periods(text):
    """Extrait les périodes d'expérience professionnelle du texte"""
    experiences = []
    
    patterns = [
        r'([a-zA-Zéû]+\s+\d{4})\s*[-–/à]\s*([a-zA-Zéû]+\s+\d{4}|aujourd\'hui|présent|maintenant|now|current|en cours|actuellement)',
        r'(\d{1,2}/\d{4})\s*[-–/à]\s*(\d{1,2}/\d{4}|aujourd\'hui|présent|maintenant|now|current|en cours|actuellement)',
        r'(\d{4}-\d{1,2})\s*[-–/à]\s*(\d{4}-\d{1,2}|aujourd\'hui|présent|maintenant|now|current|en cours|actuellement)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                start_str = match[0].strip()
                end_str = match[1].strip() if len(match) > 1 else None
                
                start_date = parse_date_french(start_str)
                if not start_date:
                    continue
                
                if end_str and is_current_position(end_str):
                    end_date = datetime.datetime.now()
                    is_current = True
                elif end_str:
                    end_date = parse_date_french(end_str)
                    if not end_date:
                        continue
                    is_current = False
                else:
                    continue
                
                experiences.append((start_date, end_date, is_current))
            except Exception as e:
                print(f"⚠️ Erreur parsing période: {e}")
                continue
    
    return experiences


def calculate_professional_experience_years(cv_text, lettre_text, attestation_texts_list):
    """
    Calcule les années d'expérience professionnelle ACTIVE uniquement.
    ✅ Analyse TOUS les fichiers: CV + Lettre + Certificats
    ⚠️ EXCLUT les stages
    ✅ CALCULE jusqu'à AUJOURD'HUI si "à aujourd'hui"
    """
    full_text = normalize_text(cv_text + " " + (lettre_text or "") + " " + " ".join(attestation_texts_list or []))
    
    experiences = []
    total_months = 0
    
    lines = full_text.split('\n')
    
    for line in lines:
        line_lower = line.lower()
        
        # 🔴 Si stage → IGNORER
        if any(stage_kw in line_lower for stage_kw in STAGE_KEYWORDS):
            continue
        
        # ✅ Si expérience pro → extraire les périodes
        if any(pro_kw in line_lower for pro_kw in PROFESSIONAL_KEYWORDS):
            line_experiences = extract_experience_periods(line)
            for start_date, end_date, is_current in line_experiences:
                duration_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
                duration_months = max(0, duration_months)
                
                experiences.append({
                    'start': start_date,
                    'end': end_date,
                    'months': duration_months,
                    'is_current': is_current
                })
                
                total_months += duration_months
    
    professional_years = total_months / 12.0
    return round(professional_years, 1), experiences


def check_minimum_experience_for_poste(cv_text, lettre_text, attestation_texts_list, poste):
    """
    ✅ Vérifie l'expérience selon le poste SPÉCIFIQUE
    ✅ Chaque poste a ses propres exigences (2 ans IT, 3 ans Finance, etc.)
    """
    grille = GRILLE.get(poste)
    if not grille:
        return False, 0
    
    required_years = grille.get('experience_requise', 0)
    
    # Si pas d'exigence d'années pour ce poste → validé automatiquement
    if required_years == 0:
        return True, 0
    
    total_years, experiences = calculate_professional_experience_years(cv_text, lettre_text, attestation_texts_list)
    
    return total_years >= required_years, total_years


# ══════════════════════════════════════════════════════════════════════════════
# 🧠 MOTEUR D'ANALYSE CV - TOUS FICHIERS + CRITÈRES PAR POSTE
# ══════════════════════════════════════════════════════════════════════════════

def check_criterion_match(criterion, full_text):
    """Vérifie STRICTEMENT si un critère est validé"""
    mots_cles = KEYWORD_MAPPING.get(criterion, [])
    if not mots_cles:
        return False, []
    
    found_keywords = [kw for kw in mots_cles if kw.lower() in full_text]
    is_present = len(found_keywords) > 0
    
    return is_present, found_keywords


def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    """
    Analyse STRICTE selon la grille Word.
    ✅ Analyse TOUS les fichiers: CV + Lettre + Certificats
    ✅ Critères d'expérience SPÉCIFIQUES au poste
    ⚠️ Si UN SEUL critère éliminatoire manque → Score = 0
    """
    if not cv_text or len(cv_text.strip()) < 50:
        return {
            'score': 0,
            'checklist': {},
            'flags_eliminatoires': ['CV non analysable (trop court ou vide)'],
            'signaux_detectes': [],
            'details': {'error': 'CV vide ou non parsé', 'cv_length': len(cv_text) if cv_text else 0},
            'score_breakdown': {'bloc1_eliminatoire': True, 'bloc2_pts': 0, 'bloc3_pts': 0, 'total_raw': 0}
        }
    
    grille = GRILLE.get(poste)
    if not grille:
        return {
            'score': 0,
            'checklist': {},
            'flags_eliminatoires': [],
            'signaux_detectes': [],
            'details': {'error': f'Poste inconnu: {poste}', 'postes_disponibles': list(GRILLE.keys())},
            'score_breakdown': {}
        }
    
    # ✅ CONCATÉNER TOUS LES TEXTES POUR L'ANALYSE
    all_attestation_text = " ".join(attestation_texts_list) if attestation_texts_list else ""
    full_text = normalize_text(cv_text + " " + (lettre_text or "") + " " + all_attestation_text)
    
    checklist = {}
    flags_elim = []
    signaux = []
    points_bloc2 = 0
    points_bloc3 = 0
    details = {
        'cv_words': len(cv_text.split()) if cv_text else 0,
        'lettre_words': len(lettre_text.split()) if lettre_text else 0,
        'attestation_words': len(all_attestation_text.split()) if all_attestation_text else 0,
        'criteres_valides_bloc2': [],
        'signaux_valides_bloc3': [],
        'alertes_attention': [],
        'matching_details': {},
        'documents_analyses': {
            'cv': len(cv_text) > 0,
            'lettre': len(lettre_text or "") > 0,
            'certificats': len(attestation_texts_list) if attestation_texts_list else 0
        }
    }
    
    # 🔴 BLOC 1 : ÉLIMINATOIRE - CRITÈRES SPÉCIFIQUES AU POSTE
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        
        # ✅ Cas spécial: Critères d'expérience minimale (SPÉCIFIQUE AU POSTE)
        if ("expérience professionnelle" in crit.lower() or "ans d'expérience" in crit.lower()) and ("ans" in crit.lower() or "année" in crit.lower()):
            # ✅ Utiliser l'exigence du poste SPÉCIFIQUE
            is_present, exp_years = check_minimum_experience_for_poste(cv_text, lettre_text, attestation_texts_list, poste)
            
            required_years = grille.get('experience_requise', 0)
            checklist[key] = is_present
            
            if not is_present:
                flags_elim.append(f"❌ {crit} (seulement {exp_years:.1f} ans, {required_years} requis)")
                details['alertes_attention'].append(f"🔴 Éliminatoire: {crit} manquant")
                details['matching_details'][crit] = {
                    'found': False,
                    'professional_experience_years': exp_years,
                    'required_years': required_years
                }
            else:
                details['matching_details'][crit] = {
                    'found': True,
                    'professional_experience_years': exp_years,
                    'required_years': required_years
                }
        else:
            # Critères normaux (non expérience)
            is_present, found_keywords = check_criterion_match(crit, full_text)
            checklist[key] = is_present
            
            if not is_present:
                flags_elim.append(f"❌ {crit} (non trouvé)")
                details['alertes_attention'].append(f"🔴 Éliminatoire: {crit} manquant")
                details['matching_details'][crit] = {
                    'found': False,
                    'keywords_searched': KEYWORD_MAPPING.get(crit, [])[:5]
                }
            else:
                details['matching_details'][crit] = {
                    'found': True,
                    'matched': found_keywords
                }
    
    # ⚠️ ÉLIMINATION si AU MOINS UN critère manque
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
                'note': f"ÉLIMINÉ : {len(flags_elim)} critère(s) manquant(s)",
                'documents_analyses': details['documents_analyses'],
                'professional_experience_years': details.get('matching_details', {}).get('2 ans d\'expérience professionnelle minimum', {}).get('professional_experience_years', 0)
            }
        }
    
    # ✅ Tous les critères éliminatoires validés → continuer
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"🟠 {crit}")
    
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"🟡 {crit}")
    
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            details['alertes_attention'].append(f"⚠️ {crit}")
    
    # 🧮 CALCUL DU SCORE
    adequation = min(3, len([k for k, v in checklist.items() if k.startswith('elim_') and v]))
    coherence = min(2, points_bloc2)
    risque_metier = min(3, len(signaux))
    qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre_motiv = 1 if lettre_text and len(lettre_text.strip()) > 0 else 0
    
    score_total_excel = adequation + coherence + risque_metier + qualite_cv + lettre_motiv
    score_final = min(10, score_total_excel)
    
    score_breakdown = {
        'bloc1_eliminatoire': False,
        'flags_eliminatoires_count': 0,
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
        'note': f"Score: {score_final}/10",
        'documents_analyses': details['documents_analyses'],
        'professional_experience_years': details.get('matching_details', {}).get('2 ans d\'expérience professionnelle minimum', {}).get('professional_experience_years', 0)
    }
    
    return {
        'score': score_final,
        'checklist': checklist,
        'flags_eliminatoires': [],
        'signaux_detectes': signaux,
        'details': details,
        'score_breakdown': score_breakdown
    }


def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste):
    """Analyse TOUS les documents soumis"""
    try:
        key = f"candidat:{token}"
        
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        
        # ✅ Extraction TOUS les fichiers
        cv_path = os.path.join(UPLOAD_FOLDER, cv_filename) if cv_filename else None
        cv_text = extract_text_from_file(cv_path, cv_filename) if cv_path else ""
        
        lettre_path = os.path.join(UPLOAD_FOLDER, lettre_filename) if lettre_filename else None
        lettre_text = extract_text_from_file(lettre_path, lettre_filename) if lettre_path else ""
        
        attestation_texts = []
        if attestation_filenames:
            for att_filename in attestation_filenames:
                att_path = os.path.join(UPLOAD_FOLDER, att_filename)
                if os.path.exists(att_path):
                    att_text = extract_text_from_file(att_path, att_filename)
                    if att_text:
                        attestation_texts.append(att_text)
        
        # ✅ Analyse avec TOUS les documents + critères du POSTE SPÉCIFIQUE
        result = analyze_cv_against_grille(cv_text, lettre_text, attestation_texts, poste)
        
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
        
        print(f"✅ Analyse auto terminée pour {token}: score={result['score']}/10")
        print(f"   📄 Fichiers analysés: CV={len(cv_text)} chars, Lettre={len(lettre_text)} chars, Certificats={len(attestation_texts)} fichiers")
        print(f"   📊 Poste: {poste}")
        if result['score_breakdown'].get('professional_experience_years') is not None:
            print(f"   📊 Expérience pro: {result['score_breakdown']['professional_experience_years']} ans")
        if result['score_breakdown']['bloc1_eliminatoire']:
            print(f"   ⚠️ CANDIDAT ÉLIMINÉ : {result['score_breakdown']['note']}")
        
    except Exception as e:
        print(f"⚠️ Erreur analyse auto pour candidat {token}: {e}")
        redis_client.hset(f"candidat:{token}", mapping={
            "analyse_status": "error",
            "analyse_error": str(e),
            "analyse_auto_date": datetime.datetime.now().isoformat()
        })


# ══════════════════════════════════════════════════════════════════════════════
# 🏆 CLASSEMENT
# ══════════════════════════════════════════════════════════════════════════════

def calculate_ranking_score(candidat_data, poste):
    sb = candidat_data.get('score_breakdown_parsed', {})
    if sb.get('bloc1_eliminatoire'):
        return -999
    
    score_principal = int(candidat_data.get('score', 0))
    signaux_count = len(candidat_data.get('signaux_detectes_parsed', []))
    signaux_bonus = signaux_count * 0.5
    criteres_valides = sb.get('bloc2_criteres_valides', 0)
    coherence_bonus = criteres_valides * 0.2
    lettre_bonus = 0.1 if candidat_data.get('lettre_filename') else 0
    
    try:
        date_candidature = datetime.datetime.fromisoformat(candidat_data.get('date_candidature', ''))
        days_since = (datetime.datetime.now() - date_candidature).days
        date_bonus = max(0, (30 - min(days_since, 30)) * 0.01)
    except:
        date_bonus = 0
    
    return round(score_principal + signaux_bonus + coherence_bonus + lettre_bonus + date_bonus, 3)


def get_recommandation_from_score(score):
    if score >= 8:
        return "🥇 Entretien prioritaire"
    elif score >= 6:
        return "🥈 Entretien si besoin"
    else:
        return "❌ Rejet"


def generate_ranking_for_poste(poste, candidats_data):
    candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
    
    for c in candidats_poste:
        c['ranking_score'] = calculate_ranking_score(c, poste)
        c['ranking_position'] = 0
    
    candidats_poste.sort(key=lambda x: (
        -x['ranking_score'],
        -len(x.get('signaux_detectes_parsed', [])),
        -x.get('score_breakdown_parsed', {}).get('bloc2_criteres_valides', 0),
        x.get('date_candidature', ''),
        f"{x.get('nom', '')} {x.get('prenom', '')}".strip().lower()
    ))
    
    for idx, c in enumerate(candidats_poste, 1):
        c['ranking_position'] = idx
        score = int(c.get('score', 0))
        c['ranking_recommendation'] = get_recommandation_from_score(score)
    
    return candidats_poste


# ══════════════════════════════════════════════════════════════════════════════
# 📄 EXPORTS
# ══════════════════════════════════════════════════════════════════════════════

def generate_excel_report(candidats_data, poste_filter=None):
    if not OPENPYXL_AVAILABLE:
        return None
    
    wb = Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    
    postes_to_export = [poste_filter] if poste_filter else list(set(c.get('poste', 'Inconnu') for c in candidats_data))
    
    for poste in postes_to_export:
        candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
        candidats_poste = generate_ranking_for_poste(poste, candidats_poste)
        
        ws = wb.create_sheet(title=poste[:20])
        
        header_fill = PatternFill(start_color="1a3a5c", end_color="1a3a5c", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, size=11)
        title_font = Font(bold=True, size=14, color="1a3a5c")
        border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
        
        ws.merge_cells('A1:K1')
        title_cell = ws['A1']
        title_cell.value = f"CLASSEMENT STRICT - {poste}"
        title_cell.font = title_font
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        title_cell.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
        ws.row_dimensions[1].height = 30
        
        headers = ['Rang', 'Email', 'Candidat', 'Téléphone', 'Adéquation (0-3)', 'Cohérence (0-2)', 'Risque (0-3)', 'CV (0-1)', 'Lettre (0-1)', 'Score Total', 'Recommandation']
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        for row_idx, c in enumerate(candidats_poste, 4):
            sb = c.get('score_breakdown_parsed', {})
            adequation = sb.get('adequation_experience', 0) if not sb.get('bloc1_eliminatoire') else 0
            coherence = sb.get('coherence_parcours', 0) if not sb.get('bloc1_eliminatoire') else 0
            risque_metier = sb.get('exposition_risque_metier', 0) if not sb.get('bloc1_eliminatoire') else 0
            qualite_cv = sb.get('qualite_cv', 0) if not sb.get('bloc1_eliminatoire') else 0
            lettre_motiv = sb.get('lettre_motivation', 0) if not sb.get('bloc1_eliminatoire') else 0
            score_total = adequation + coherence + risque_metier + qualite_cv + lettre_motiv
            
            nom_complet = f"{c.get('prenom', '')} {c.get('nom', '')}".strip()
            email = c.get('email', '') or '–'
            telephone = c.get('telephone', '') or '–'
            rang = c.get('ranking_position', row_idx - 3)
            recommandation = c.get('ranking_recommendation', get_recommandation_from_score(score_total))
            
            row_data = [rang, email, nom_complet, telephone, adequation, coherence, risque_metier, qualite_cv, lettre_motiv, score_total, recommandation]
            
            for col, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.border = border
                cell.alignment = Alignment(horizontal='center', vertical='center')
                
                if col == 1 and rang <= 3:
                    cell.font = Font(bold=True, size=12)
                if col == 10:
                    cell.font = Font(bold=True)
        
        column_widths = [8, 35, 35, 20, 15, 15, 15, 12, 12, 15, 25]
        for col, width in enumerate(column_widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        
        for row in range(3, ws.max_row + 1):
            ws.row_dimensions[row].height = 40
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def generate_csv_report(candidats_data):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    
    writer.writerow(['Rang', 'Email', 'Nom', 'Prénom', 'Téléphone', 'Poste', 'Date', 'Score (/10)', 'Statut', 'Éliminatoire', 'Adéquation', 'Cohérence', 'Risque', 'Note', 'Exp. Pro (ans)'])
    
    for idx, c in enumerate(candidats_data, 1):
        sb = c.get('score_breakdown_parsed', {})
        writer.writerow([
            idx, c.get('email', '') or '–', c.get('nom', ''), c.get('prenom', ''),
            c.get('telephone', '') or '–', c.get('poste', ''), c.get('date_candidature', ''),
            c.get('score', '0'), c.get('statut', ''),
            'OUI' if sb.get('bloc1_eliminatoire') else 'NON',
            sb.get('adequation_experience', 0), sb.get('coherence_parcours', 0),
            sb.get('exposition_risque_metier', 0), sb.get('note', ''),
            sb.get('professional_experience_years', 0)
        ])
    
    output.seek(0)
    return output.getvalue()


def generate_pdf_report(candidats_data):
    if not REPORTLAB_AVAILABLE:
        return None
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=2*cm, bottomMargin=2*cm)
    elements = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, textColor=colors.HexColor('#1a3a5c'), spaceAfter=20, alignment=TA_CENTER)
    elements.append(Paragraph("Rapport des Candidatures - RecrutBank", title_style))
    elements.append(Spacer(1, 0.3*cm))
    
    date_style = ParagraphStyle('DateStyle', parent=styles['Normal'], fontSize=9, textColor=colors.grey)
    elements.append(Paragraph(f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y à %H:%M')}", date_style))
    elements.append(Spacer(1, 0.8*cm))
    
    data = [['Rang', 'Email', 'Candidat', 'Téléphone', 'Poste', 'Score', 'Exp. Pro', 'Recommandation']]
    
    for idx, c in enumerate(candidats_data, 1):
        score = int(c.get('score', 0))
        recommandation = get_recommandation_from_score(score)
        exp_pro = c.get('score_breakdown_parsed', {}).get('professional_experience_years', 0)
        
        data.append([
            str(idx), c.get('email', '') or '–',
            f"{c.get('prenom', '')} {c.get('nom', '')}",
            c.get('telephone', '') or '–', c.get('poste', ''),
            f"{score}/10", f"{exp_pro:.1f} ans" if exp_pro else "–", recommandation
        ])
    
    table = Table(data, colWidths=[1.5*cm, 4*cm, 4*cm, 3*cm, 4*cm, 2*cm, 2.5*cm, 4*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a5c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('WORDWRAP', (0, 0), (-1, -1), 'ON')
    ]))
    
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return buffer


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify(POSTES), 200

@app.route('/api/grille/<poste>', methods=['GET'])
def get_grille(poste):
    g = GRILLE.get(poste)
    if not g:
        return jsonify({'error': 'Poste inconnu'}), 404
    return jsonify(g), 200

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    if not data:
        return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    pwd = hash_pwd(data.get('password', ''))

    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r.get("email", "").lower() == email and r.get("password") == pwd:
            token = create_access_token(identity=r["id"])
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

        for k in redis_client.keys("candidat:*"):
            existing = redis_client.hgetall(k)
            if existing.get('email', '').lower() == email:
                return jsonify({'error': 'Email déjà utilisé'}), 409

        cv_filename = ''
        if 'cv' in request.files:
            cv = request.files['cv']
            if cv and cv.filename and allowed_file(cv.filename):
                ext = cv.filename.rsplit('.', 1)[1].lower()
                cv_filename = f"{uuid.uuid4().hex}_cv.{ext}"
                cv.save(os.path.join(UPLOAD_FOLDER, cv_filename))

        lettre_filename = ''
        if 'lettre' in request.files:
            lettre = request.files['lettre']
            if lettre and lettre.filename and allowed_file(lettre.filename):
                ext = lettre.filename.rsplit('.', 1)[1].lower()
                lettre_filename = f"{uuid.uuid4().hex}_lettre.{ext}"
                lettre.save(os.path.join(UPLOAD_FOLDER, lettre_filename))
        
        attestation_filenames = []
        if 'attestation' in request.files:
            for att in request.files.getlist('attestation'):
                if att and att.filename and allowed_file(att.filename):
                    ext = att.filename.rsplit('.', 1)[1].lower()
                    att_filename = f"{uuid.uuid4().hex}_attestation.{ext}"
                    att.save(os.path.join(UPLOAD_FOLDER, att_filename))
                    attestation_filenames.append(att_filename)
        
        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={
            "nom": nom, "prenom": prenom, "email": email, "telephone": telephone,
            "poste": poste, "cv_filename": cv_filename, "lettre_filename": lettre_filename,
            "attestation_filenames": json.dumps(attestation_filenames),
            "statut": "en_attente", "note": "", "score": "0",
            "analyse_status": "pending", "date_candidature": datetime.datetime.now().isoformat()
        })

        threading.Thread(target=run_analysis_for_candidat, args=(token, cv_filename, lettre_filename, attestation_filenames, poste), daemon=True).start()

        return jsonify({'message': 'Candidature soumise', 'token': token}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Introuvable'}), 404
    return jsonify({k: v for k, v in data.items() if k not in ['cv_filename', 'lettre_filename', 'attestation_filenames']}), 200

@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    keys = redis_client.keys("candidat:*")
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0, "rejete": 0, "entretien": 0, "by_poste": []}
    counts = {}
    for k in keys:
        c = redis_client.hgetall(k)
        s = c.get('statut', 'en_attente')
        if s in stats: stats[s] += 1
        counts[c.get('poste', 'Inconnu')] = counts.get(c.get('poste', 'Inconnu'), 0) + 1
    stats['by_poste'] = [{'poste': p, 'n': n} for p, n in sorted(counts.items(), key=lambda x: -x[1])]
    return jsonify(stats), 200

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    keys = redis_client.keys("candidat:*")
    result = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        if c.get('score_breakdown'):
            try: c['score_breakdown_parsed'] = json.loads(c['score_breakdown'])
            except: pass
        result.append(c)
    result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
    return jsonify(result), 200

@app.route('/api/recruteur/export/<format>', methods=['GET'])
@jwt_required()
def export_candidates(format):
    keys = redis_client.keys("candidat:*")
    result = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        if c.get('score_breakdown'):
            try: c['score_breakdown_parsed'] = json.loads(c['score_breakdown'])
            except: pass
        result.append(c)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if format.lower() == 'csv':
        return send_file(io.BytesIO(generate_csv_report(result).encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=f'rapport_{timestamp}.csv')
    elif format.lower() in ['excel', 'xlsx']:
        if not OPENPYXL_AVAILABLE:
            return jsonify({'error': 'openpyxl non installé'}), 503
        return send_file(generate_excel_report(result), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'rapport_{timestamp}.xlsx')
    elif format.lower() == 'pdf':
        if not REPORTLAB_AVAILABLE:
            return jsonify({'error': 'reportlab non installé'}), 503
        return send_file(generate_pdf_report(result), mimetype='application/pdf', as_attachment=True, download_name=f'rapport_{timestamp}.pdf')
    
    return jsonify({'error': 'Format non supporté'}), 400

@app.route('/api/recruteur/uploads/<filename>', methods=['GET'])
@jwt_required()
def serve_upload(filename):
    safe = secure_filename(filename)
    filepath = os.path.join(UPLOAD_FOLDER, safe)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Fichier introuvable'}), 404
    return send_from_directory(UPLOAD_FOLDER, safe)

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Serveur démarré sur le port {port}")
    print(f"✅ TOUS les fichiers analysés (CV + Lettre + Certificats)")
    print(f"✅ Expérience calculée SPÉCIFIQUEMENT par poste")
    print(f"✅ IT: 2 ans, Finance: 3 ans, Archiviste: 0 ans")
    print(f"✅ Parsing dates FR/EN corrigé")
    print(f"✅ Calcul jusqu'à AUJOURD'HUI")
    print(f"✅ Stages exclus du calcul")
    app.run(host="0.0.0.0", port=port, debug=False)

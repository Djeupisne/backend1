# server.py - Backend Flask pour RecrutBank avec analyse automatique STRICTE
# Élimination AUTOMATIQUE si UN critère éliminatoire manque (logique AND stricte)
# Analyse TOUS les documents (CV + Lettre + Certificats) avec matching EXACT
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
# 📋 GRILLE DE PRÉSÉLECTION - ÉLIMINATION STRICTE (TOUS critères requis)
# ══════════════════════════════════════════════════════════════════════════════
# ⚠️ LOGIQUE STRICTE : Si UN SEUL critère éliminatoire n'est PAS trouvé → ÉLIMINATION AUTOMATIQUE
# Même si les autres critères sont validés, le candidat est rejeté.

GRILLE = {
    "Responsable Administration de Crédit": {
        "eliminatoire": [
            "Expérience bancaire",
            "3 ans ou plus en crédit / risque",
            "Exposition aux garanties ou conformité"
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
        ]
    },
    "Analyste Crédit CCB": {
        "eliminatoire": [
            "Expérience en analyse crédit",
            "Capacité à lire des états financiers",
            "3 ans expérience institution financière"
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
            "Expérience en banque / juridique",
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
            "3 ans expérience département finance"
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
        ]
    },
    "Market Risk Officer": {
        "eliminatoire": [
            "Base en risques de marché",
            "Compétences quantitatives",
            "Exposition à FX / taux / liquidité",
            "3 ans expérience institution financière"
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
        ]
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": [
            "Expérience en réseau / infrastructure",
            "Exposition à environnement critique",
            "Notion de sécurité IT",
            "2 ans expérience minimum"
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
        ]
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# 🔍 MAPPING MOTS-CLÉS EXACTS - MATCHING STRICT (SOIT ÇA PASSE, SOIT ÇA CASSE)
# ══════════════════════════════════════════════════════════════════════════════

KEYWORD_MAPPING = {
    # === Responsable Administration de Crédit ===
    "Expérience bancaire": ["expérience bancaire", "secteur bancaire", "établissement bancaire", "banque commerciale", "institution financière", "banque"],
    "3 ans ou plus en crédit / risque": ["3 ans", "trois ans", "3 années", "4 ans", "5 ans", "6 ans", "7 ans", "8 ans", "9 ans", "10 ans", "plusieurs années", "expérience crédit", "gestion risque crédit"],
    "Exposition aux garanties ou conformité": ["garanties", "nantissement", "hypothèque", "sûreté", "conformité", "COBAC", "réglementation bancaire", "BCAC", "audit"],
    "Validation de dossiers": ["validation dossier", "instruction crédit", "approbation crédit", "dossier crédit", "validation des dossiers"],
    "Gestion des garanties": ["gestion garanties", "suivi garanties", "garanties réelles", "sûretés", "portefeuille garanties"],
    "Participation à des audits": ["audit", "contrôle interne", "inspection", "compliance audit", "audit interne"],
    "IFRS 9": ["IFRS 9", "IAS 39", "normes IFRS", "comptabilité IFRS"],
    "COBAC / conformité": ["COBAC", "conformité bancaire", "régulation bancaire", "BCEAO", "BCAC", "commission bancaire"],
    "Suivi portefeuille / impayés": ["portefeuille crédit", "impayés", "recouvrement", "contentieux", "encours", "suivi portefeuille"],
    
    # === Analyste Crédit CCB ===
    "Expérience en analyse crédit": ["analyse crédit", "credit analysis", "évaluation crédit", "scoring crédit", "analyse financière crédit"],
    "Capacité à lire des états financiers": ["états financiers", "bilan", "compte de résultat", "ratios financiers", "analyse financière"],
    "3 ans expérience institution financière": ["3 ans", "trois ans", "3 années", "institution financière", "banque", "secteur bancaire", "4 ans", "5 ans", "6 ans"],
    "Type de clients PME": ["PME", "petites entreprises", "moyennes entreprises", "TPE"],
    "Type de clients particuliers": ["particuliers", "clients particuliers", "retail", "clientèle particulière"],
    "Structuration de crédit": ["structuration crédit", "montage crédit", "dossier de crédit", "structurer un crédit"],
    "Avis de crédit": ["avis crédit", "recommandation crédit", "opinion crédit", "credit opinion"],
    "Cash-flow analysis": ["cash-flow", "cash flow", "flux de trésorerie", "FCF", "free cash flow"],
    "Montage de crédit": ["montage crédit", "structuration", "dossier de crédit", "montage de dossiers"],
    "Comités de crédit": ["comité crédit", "commission crédit", "credit committee", "validation comité"],
    
    # === Archiviste ===
    "Expérience en gestion documentaire structurée": ["gestion documentaire", "archivage", "GED", "records management", "classement", "documentation"],
    "Rigueur démontrée": ["rigueur", "méthode", "organisation", "procédures", "processus", "traçabilité", "précision"],
    "Archivage physique et électronique": ["archivage physique", "archivage électronique", "dématérialisation", "numérisation", "archives"],
    "Gestion des dossiers sensibles": ["dossiers sensibles", "confidentiel", "sécurisé", "accès restreint", "données sensibles"],
    "Expérience en banque / juridique": ["banque", "établissement financier", "juridique", "droit bancaire", "secteur bancaire"],
    "Manipulation de garanties ou contrats": ["garanties", "contrats", "conventions", "actes juridiques", "documentation juridique"],
    
    # === Senior Finance Officer ===
    "Expérience en reporting financier structuré": ["reporting financier", "reporting", "tableaux de bord", "KPI", "indicateurs", "états financiers"],
    "Exposition aux états financiers": ["états financiers", "bilan", "compte de résultat", "consolidation", "reporting financier"],
    "Interaction avec auditeurs": ["auditeurs", "audit", "CAC", "commissaires aux comptes", "audit externe"],
    "3 ans expérience département finance": ["3 ans", "trois ans", "3 années", "département finance", "finance", "4 ans", "5 ans", "6 ans"],
    "Production états financiers": ["production états financiers", "établissement des états financiers", "élaboration des états financiers"],
    "Reporting groupe": ["reporting groupe", "reporting consolidé", "consolidation groupe"],
    "Connaissance IFRS": ["IFRS", "normes internationales", "comptabilité internationale", "IAS"],
    "Contraintes réglementaires": ["réglementation", "contraintes réglementaires", "conformité", "réglementaire"],
    "IFRS / consolidation": ["IFRS", "consolidation", "comptes consolidés", "IFRS consolidation"],
    "Interaction avec CAC": ["CAC", "commissaires aux comptes", "audit légal", "audit externe"],
    "Outils type SPECTRA / CERBER / ERP": ["SPECTRA", "CERBER", "ERP", "SAP", "Oracle", "outil de reporting"],
    
    # === Market Risk Officer ===
    "Base en risques de marché": ["risque marché", "market risk", "risques de marché", "gestion des risques de marché"],
    "Compétences quantitatives": ["quantitatif", "quantitative", "mathématiques", "statistiques", "modélisation"],
    "Exposition à FX / taux / liquidité": ["FX", "change", "taux", "liquidité", "forex", "taux d'intérêt", "risque de liquidité"],
    "3 ans expérience institution financière": ["3 ans", "trois ans", "3 années", "institution financière", "banque", "secteur bancaire", "4 ans", "5 ans", "6 ans"],
    "Maîtrise VaR / stress testing": ["VaR", "Value at Risk", "stress testing", "back-testing", "scénarios"],
    "Analyse des positions": ["analyse des positions", "positions", "analyse de portefeuille", "suivi des positions"],
    "Excel avancé": ["Excel avancé", "Excel", "tableaux croisés", "macros", "pivot", "VBA"],
    "VBA / Python": ["VBA", "Python", "programmation", "scripting"],
    "Bâle II / III": ["Bâle II", "Bâle III", "Basel II", "Basel III", "accords de Bâle", "réglementation Bâle"],
    "Gestion ALM / liquidité": ["ALM", "Asset Liability Management", "liquidité", "gestion ALM", "actif-passif"],
    "Produits FICC": ["FICC", "produits dérivés", "commodities", "matières premières", "produits de taux", "FX"],
    "Reporting risque": ["reporting risque", "reporting des risques", "rapport de risque"],
    
    # === IT Réseau & Infrastructure ===
    "Expérience en réseau / infrastructure": ["réseau", "infrastructure", "LAN", "WAN", "VPN", "réseaux", "infrastructure IT", "network"],
    "Exposition à environnement critique": ["banque", "telco", "télécom", "datacenter", "centre de données", "environnement critique", "secteur bancaire"],
    "Notion de sécurité IT": ["sécurité IT", "cybersécurité", "sécurité informatique", "firewall", "sécurité réseau"],
    "2 ans expérience minimum": ["2 ans", "deux ans", "2 années", "expérience", "3 ans", "4 ans", "5 ans"],
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
# 🔧 PARSING DOCUMENTS - ANALYSE TOUS LES DOCUMENTS SOUMIS
# ══════════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf(filepath):
    """Extrait le texte d'un fichier PDF de manière ROBUSTE"""
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
    """Extrait le texte d'un fichier DOCX de manière ROBUSTE"""
    try:
        doc = Document(filepath)
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n".join(paragraphs).strip()
    except Exception as e:
        print(f"⚠️ Erreur lecture DOCX: {e}")
        return ""

def extract_text_from_file(filepath, filename):
    """Extrait le texte selon l'extension du fichier - ROBUSTE"""
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
    """Normalise le texte pour la comparaison - MATCHING EXACT"""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s\-/]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ══════════════════════════════════════════════════════════════════════════════
# 🧠 MOTEUR D'ANALYSE CV - ÉLIMINATION STRICTE (TOUS critères requis)
# Analyse TOUS les documents : CV + Lettre + TOUS les certificats
# ══════════════════════════════════════════════════════════════════════════════

def check_criterion_match(criterion, full_text):
    """
    Vérifie STRICTEMENT et EXACTEMENT si un critère est validé.
    MATCHING EXACT : soit le mot-clé est trouvé, soit il ne l'est pas.
    """
    mots_cles = KEYWORD_MAPPING.get(criterion, [])
    if not mots_cles:
        return False, []
    
    # Recherche EXACTE : au moins UNE variante doit être trouvée
    found_keywords = [kw for kw in mots_cles if kw.lower() in full_text]
    is_present = len(found_keywords) > 0
    
    return is_present, found_keywords


def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    """
    Analyse STRICTE selon la grille Word - ÉLIMINATION AUTOMATIQUE.
    
    ⚠️ RÈGLE STRICTE : Si UN SEUL critère éliminatoire n'est PAS trouvé → Score = 0
    Même si les autres critères éliminatoires sont validés, le candidat est éliminé.
    
    Analyse TOUS les documents soumis : CV + Lettre + TOUS les certificats.
    
    🔴 Bloc 1: Éliminatoire (filtre dur, logique AND) → Score = 0 si UN critère manquant
    🟠 Bloc 2: Cohérence → +1 point par critère validé
    🟡 Bloc 3: Signaux → +2 points par signal détecté
    
    Modèle Excel: Adéquation(0-3)+Cohérence(0-2)+Risque(0-3)+CV(0-1)+Lettre(0-1)=/10
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
    
    # 🔍 ANALYSE TOUS LES DOCUMENTS SOUMIS
    # Concaténation de TOUS les textes : CV + Lettre + TOUS les certificats
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
    
    # 🔴 BLOC 1 : ÉLIMINATOIRE (critères POSITIFS requis) - LOGIQUE AND STRICTE
    # ⚠️ Si UN SEUL critère n'est PAS trouvé → ÉLIMINATION AUTOMATIQUE
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        
        checklist[key] = is_present
        
        if not is_present:
            # ⚠️ MATCHING EXACT ÉCHOUÉ → CRITÈRE ÉLIMINATOIRE MANQUANT
            flags_elim.append(f"❌ {crit} (non trouvé)")
            details['alertes_attention'].append(f"🔴 Éliminatoire: {crit} manquant")
            details['matching_details'][crit] = {
                'found': False, 
                'status': 'ÉLIMINATOIRE - Critère requis non trouvé dans les documents',
                'keywords_searched': KEYWORD_MAPPING.get(crit, [])[:5]
            }
        else:
            # ✅ MATCHING EXACT RÉUSSI → Critère validé
            details['matching_details'][crit] = {
                'found': True, 
                'status': 'VALIDÉ',
                'matched': found_keywords
            }
    
    # ⚠️ VÉRIFICATION STRICTE : Si AU MOINS UN critère éliminatoire manque → ÉLIMINATION
    if flags_elim:
        # Le candidat est éliminé même si d'autres critères éliminatoires sont validés
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
                'note': f"ÉLIMINÉ : {len(flags_elim)} critère(s) éliminatoire(s) manquant(s)",
                'documents_analyses': details['documents_analyses']
            }
        }
    
    # ✅ Tous les critères éliminatoires sont validés → on continue l'analyse
    
    # 🟠 BLOC 2 : COHÉRENCE (+1 point par critère validé) - MATCHING EXACT
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"🟠 {crit}")
    
    # 🟡 BLOC 3 : SIGNAUX (+2 points par signal détecté) - MATCHING EXACT
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"🟡 {crit}")
    
    # ⚠️ POINTS D'ATTENTION - MATCHING EXACT
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            details['alertes_attention'].append(f"⚠️ {crit}")
    
    # 🧮 CALCUL DU SCORE FINAL selon modèle Excel (sur 10) - STRICT
    # Mapping selon modèle Excel :
    # Adéquation expérience (0-3) = critères éliminatoires validés (max 3)
    adequation = min(3, len([k for k, v in checklist.items() if k.startswith('elim_') and v]))
    # Cohérence parcours (0-2) = critères à vérifier validés (max 2)
    coherence = min(2, points_bloc2)
    # Exposition au risque de métier (0-3) = signaux forts détectés (max 3)
    risque_metier = min(3, len(signaux))
    # Qualité du CV (0-1) = 1 si score partiel >= 5
    qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    # Lettre motivation (0-1) = 1 si lettre fournie
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
        'note': f"Score Excel: {score_final}/10",
        'documents_analyses': details['documents_analyses']
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
    """
    Analyse TOUS les documents soumis par le candidat.
    """
    try:
        key = f"candidat:{token}"
        
        # Gestion attestation_filenames (liste ou string)
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        
        # Extraction CV
        cv_path = os.path.join(UPLOAD_FOLDER, cv_filename) if cv_filename else None
        cv_text = extract_text_from_file(cv_path, cv_filename) if cv_path else ""
        
        # Extraction Lettre
        lettre_path = os.path.join(UPLOAD_FOLDER, lettre_filename) if lettre_filename else None
        lettre_text = extract_text_from_file(lettre_path, lettre_filename) if lettre_path else ""
        
        # 🔍 Extraction TOUS les certificats/attestations
        attestation_texts = []
        if attestation_filenames:
            for att_filename in attestation_filenames:
                att_path = os.path.join(UPLOAD_FOLDER, att_filename)
                if os.path.exists(att_path):
                    att_text = extract_text_from_file(att_path, att_filename)
                    if att_text:
                        attestation_texts.append(att_text)
        
        # 🧠 Analyse avec TOUS les documents
        result = analyze_cv_against_grille(cv_text, lettre_text, attestation_texts, poste)
        
        # 💾 Sauvegarde dans Redis
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
        print(f"   Documents analysés: CV={len(cv_text)} chars, Lettre={len(lettre_text)} chars, Certificats={len(attestation_texts)} fichiers")
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
# 🏆 SYSTÈME DE CLASSEMENT TRÈS STRICT DES CANDIDATS
# ══════════════════════════════════════════════════════════════════════════════

def calculate_ranking_score(candidat_data, poste):
    """
    Calcule un score de classement EXTRÊMEMENT STRICT.
    Hiérarchie stricte : Éliminatoire → Score → Signaux → Cohérence → Date
    """
    sb = candidat_data.get('score_breakdown_parsed', {})
    
    # 🔴 Facteur 1: Éliminatoire (poids maximal - bloquant)
    if sb.get('bloc1_eliminatoire'):
        return -999  # Dernier automatiquement
    
    # 🟡 Facteur 2: Score principal (0-10)
    score_principal = int(candidat_data.get('score', 0))
    
    # 🟡 Facteur 3: Signaux forts détectés
    signaux_count = len(candidat_data.get('signaux_detectes_parsed', []))
    signaux_bonus = signaux_count * 0.5
    
    # 🟠 Facteur 4: Critères "à vérifier" validés
    criteres_valides = sb.get('bloc2_criteres_valides', 0)
    coherence_bonus = criteres_valides * 0.2
    
    # 📄 Facteur 5: Lettre de motivation
    lettre_bonus = 0.1 if candidat_data.get('lettre_filename') else 0
    
    # 📅 Facteur 6: Date de candidature
    try:
        date_candidature = datetime.datetime.fromisoformat(candidat_data.get('date_candidature', ''))
        days_since = (datetime.datetime.now() - date_candidature).days
        date_bonus = max(0, (30 - min(days_since, 30)) * 0.01)
    except:
        date_bonus = 0
    
    ranking_score = score_principal + signaux_bonus + coherence_bonus + lettre_bonus + date_bonus
    
    return round(ranking_score, 3)


def get_recommandation_from_score(score):
    """
    Détermine la recommandation STRICTEMENT selon le score (modèle Excel)
    8-10 : entretien prioritaire
    6-7 : entretien si besoin
    <6 : rejet
    """
    if score >= 8:
        return "🥇 Entretien prioritaire"
    elif score >= 6:
        return "🥈 Entretien si besoin"
    else:
        return "❌ Rejet"


def generate_ranking_for_poste(poste, candidats_data):
    """
    Génère un classement EXTRÊMEMENT STRICT.
    RANG classé automatiquement selon score + expérience
    """
    candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
    
    for c in candidats_poste:
        c['ranking_score'] = calculate_ranking_score(c, poste)
        c['ranking_position'] = 0
    
    # 🔍 Tri STRICT
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
# 📄 FONCTIONS D'EXPORT DE RAPPORTS - LARGES POUR TEXTE NON COUPÉ
# ══════════════════════════════════════════════════════════════════════════════

def generate_excel_report(candidats_data, poste_filter=None):
    """
    Génère un rapport Excel avec colonnes LARGES pour texte non coupé.
    RANG | Email | Candidat | Téléphone | Adéquation(0-3) | Cohérence(0-2) | Risque(0-3) | CV(0-1) | Lettre(0-1) | Score | Recommandation
    """
    if not OPENPYXL_AVAILABLE:
        return None
    
    wb = Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    
    postes_to_export = [poste_filter] if poste_filter else list(set(c.get('poste', 'Inconnu') for c in candidats_data))
    
    for poste in postes_to_export:
        candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
        
        # 🔍 CLASSEMENT STRICT
        candidats_poste = generate_ranking_for_poste(poste, candidats_poste)
        
        ws = wb.create_sheet(title=poste[:20])
        
        # Styles
        header_fill = PatternFill(start_color="1a3a5c", end_color="1a3a5c", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, size=11)
        title_font = Font(bold=True, size=14, color="1a3a5c")
        border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
        
        # Titre
        ws.merge_cells('A1:K1')
        title_cell = ws['A1']
        title_cell.value = f"CLASSEMENT STRICT - {poste}"
        title_cell.font = title_font
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        title_cell.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
        ws.row_dimensions[1].height = 30
        
        # ✅ En-têtes
        headers = [
            'Rang',
            'Email',
            'Candidat',
            'Téléphone',
            'Adéquation expérience (0-3)',
            'Cohérence parcours (0-2)',
            'Exposition risque métier (0-3)',
            'Qualité CV (0-1)',
            'Lettre motivation (0-1)',
            'Score Total',
            'Recommandation'
        ]
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        # Données
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
            
            row_data = [
                rang,
                email,
                nom_complet,
                telephone,
                adequation,
                coherence,
                risque_metier,
                qualite_cv,
                lettre_motiv,
                score_total,
                recommandation
            ]
            
            for col, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.border = border
                cell.alignment = Alignment(horizontal='center', vertical='center')
                
                # Colorer RANG
                if col == 1:
                    if rang == 1:
                        cell.fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
                        cell.font = Font(bold=True, size=12)
                    elif rang == 2:
                        cell.fill = PatternFill(start_color="C0C0C0", end_color="C0C0C0", fill_type="solid")
                        cell.font = Font(bold=True, size=12)
                    elif rang == 3:
                        cell.fill = PatternFill(start_color="CD7F32", end_color="CD7F32", fill_type="solid")
                        cell.font = Font(bold=True, size=12)
                
                # Colorer Email
                if col == 2:
                    cell.font = Font(italic=True)
                
                # Colorer Score Total
                if col == 10:
                    if score_total >= 8:
                        cell.fill = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
                    elif score_total >= 6:
                        cell.fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
                    else:
                        cell.fill = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
                    cell.font = Font(bold=True)
                
                # Colorer Recommandation
                if col == 11:
                    if "prioritaire" in str(recommandation).lower():
                        cell.fill = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
                        cell.font = Font(bold=True)
                    elif "besoin" in str(recommandation).lower():
                        cell.fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
                    else:
                        cell.fill = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
        
        # 📏 Largeurs colonnes LARGES pour texte non coupé
        column_widths = [8, 35, 35, 20, 28, 28, 35, 20, 25, 15, 35]
        for col, width in enumerate(column_widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        
        # Activation du wrap text pour toutes les cellules
        for row in ws.iter_rows(min_row=3, max_row=ws.max_row, min_col=1, max_col=11):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
        
        for row in range(3, ws.max_row + 1):
            ws.row_dimensions[row].height = 40  # Hauteur suffisante pour texte
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def generate_csv_report(candidats_data):
    """Génère un rapport CSV avec RANG + EMAIL"""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    
    writer.writerow([
        'Rang', 'Email', 'Nom', 'Prénom', 'Téléphone', 'Poste', 'Date candidature',
        'Score (/10)', 'Statut', 'Éliminatoire', 'Adéquation (0-3)', 'Cohérence (0-2)', 'Risque (0-3)', 'Note'
    ])
    
    for idx, c in enumerate(candidats_data, 1):
        sb = c.get('score_breakdown_parsed', {})
        writer.writerow([
            idx,
            c.get('email', '') or '–',
            c.get('nom', ''),
            c.get('prenom', ''),
            c.get('telephone', '') or '–',
            c.get('poste', ''),
            c.get('date_candidature', ''),
            c.get('score', '0'),
            c.get('statut', ''),
            'OUI' if sb.get('bloc1_eliminatoire') else 'NON',
            sb.get('adequation_experience', 0),
            sb.get('coherence_parcours', 0),
            sb.get('exposition_risque_metier', 0),
            sb.get('note', '')
        ])
    
    output.seek(0)
    return output.getvalue()


def generate_pdf_report(candidats_data):
    """Génère un rapport PDF avec colonnes LARGES pour texte non coupé"""
    if not REPORTLAB_AVAILABLE:
        return None
    
    buffer = io.BytesIO()
    # Format paysage pour plus de largeur
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=2*cm, bottomMargin=2*cm)
    elements = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, textColor=colors.HexColor('#1a3a5c'), spaceAfter=20, alignment=TA_CENTER)
    elements.append(Paragraph("Rapport des Candidatures - RecrutBank", title_style))
    elements.append(Spacer(1, 0.3*cm))
    
    date_style = ParagraphStyle('DateStyle', parent=styles['Normal'], fontSize=9, textColor=colors.grey)
    elements.append(Paragraph(f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y à %H:%M')}", date_style))
    elements.append(Spacer(1, 0.8*cm))
    
    # ✅ Tableau avec colonnes LARGES
    data = [['Rang', 'Email', 'Candidat', 'Téléphone', 'Poste', 'Score (/10)', 'Recommandation']]
    
    for idx, c in enumerate(candidats_data, 1):
        score = int(c.get('score', 0))
        recommandation = get_recommandation_from_score(score)
        
        data.append([
            str(idx),
            c.get('email', '') or '–',
            f"{c.get('prenom', '')} {c.get('nom', '')}",
            c.get('telephone', '') or '–',
            c.get('poste', ''),
            f"{score}/10",
            recommandation
        ])
    
    # Colonnes LARGES pour texte non coupé
    table = Table(data, colWidths=[1.5*cm, 5*cm, 4.5*cm, 3.5*cm, 5*cm, 2.5*cm, 4.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a5c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('WORDWRAP', (0, 0), (-1, -1), 'ON')
    ]))
    
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return buffer


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES PUBLIQUES
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
    data = request.json
    if not 
        return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    pwd = hash_pwd(data.get('password', ''))

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
        nom      = (request.form.get('nom') or '').strip()
        prenom   = (request.form.get('prenom') or '').strip()
        email    = (request.form.get('email') or '').strip().lower()
        telephone= (request.form.get('telephone') or '').strip()
        poste    = (request.form.get('poste') or '').strip()

        if not nom or not prenom or not email or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400

        for k in redis_client.keys("candidat:*"):
            existing = redis_client.hgetall(k)
            if existing.get('email', '').lower() == email:
                return jsonify({'error': 'Un candidat avec cet email existe déjà'}), 409

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
            attestation_files = request.files.getlist('attestation')
            for att in attestation_files:
                if att and att.filename and allowed_file(att.filename):
                    ext = att.filename.rsplit('.', 1)[1].lower()
                    att_filename = f"{uuid.uuid4().hex}_attestation.{ext}"
                    att.save(os.path.join(UPLOAD_FOLDER, att_filename))
                    attestation_filenames.append(att_filename)
        
        attestation_filenames_json = json.dumps(attestation_filenames, ensure_ascii=False) if attestation_filenames else ""

        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={
            "nom": nom, "prenom": prenom, "email": email, "telephone": telephone,
            "poste": poste, 
            "cv_filename": cv_filename, 
            "lettre_filename": lettre_filename,
            "attestation_filenames": attestation_filenames_json,
            "statut": "en_attente", "note": "", "score": "0", 
            "checklist": "", "flags_eliminatoires": "", "signaux_detectes": "",
            "score_breakdown": "", "analyse_status": "pending",
            "date_candidature": datetime.datetime.now().isoformat()
        })

        threading.Thread(
            target=run_analysis_for_candidat,
            args=(token, cv_filename, lettre_filename, attestation_filenames, poste),
            daemon=True
        ).start()

        return jsonify({
            'message': 'Candidature soumise avec succès',
            'token': token,
            'analyse': 'L\'analyse automatique de votre dossier est en cours'
        }), 201

    except Exception as e:
        print(f"❌ Erreur postuler: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Candidature introuvable'}), 404
    public = {k: v for k, v in data.items() if k not in (
        'cv_filename', 'lettre_filename', 'attestation_filenames', 
        'checklist', 'flags_eliminatoires', 'signaux_detectes', 
        'analyse_details', 'score_breakdown'
    )}
    return jsonify(public), 200

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES RECRUTEUR (protégées JWT)
# ══════════════════════════════════════════════════════════════════════════════

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

    keys = redis_client.keys("candidat:*")
    result = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        if poste_filter and c.get('poste') != poste_filter: continue
        if statut_filter and c.get('statut') != statut_filter: continue
        if min_score is not None and int(c.get('score', 0)) < min_score: continue
        if search:
            haystack = f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} {c.get('poste','')}".lower()
            if search not in haystack: continue
        if c.get('score_breakdown'):
            try: c['score_breakdown_parsed'] = json.loads(c['score_breakdown'])
            except: pass
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
        except: 
            data['attestation_filenames_parsed'] = []
    
    for field in ['checklist', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details', 'score_breakdown']:
        if data.get(field):
            try: data[f'{field}_parsed'] = json.loads(data[field])
            except: pass
    
    return jsonify(data), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    key = f"candidat:{token}"
    if not redis_client.exists(key):
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = request.json or {}
    statut = data.get('statut', 'en_attente')
    note = data.get('note', '')
    score = str(min(10, max(0, int(data.get('score', 0)))))
    checklist = data.get('checklist', '')
    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    redis_client.hset(key, mapping={
        "statut": statut, "note": note, "score": score, "checklist": checklist,
        "decision_date": datetime.datetime.now().isoformat(),
        "decided_by": get_jwt_identity()
    })
    return jsonify({'message': 'Mis à jour avec succès', 'statut': statut}), 200

@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    key = f"candidat:{token}"
    data = redis_client.hgetall(key)
    if not data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    
    cv_filename = data.get('cv_filename')
    lettre_filename = data.get('lettre_filename')
    attestation_filenames = data.get('attestation_filenames', '[]')
    poste = data.get('poste')
    
    if not cv_filename:
        return jsonify({'error': 'CV manquant pour analyse'}), 400
    
    redis_client.hset(key, mapping={
        "analyse_status": "pending",
        "analyse_manual_trigger": datetime.datetime.now().isoformat()
    })
    
    threading.Thread(
        target=run_analysis_for_candidat,
        args=(token, cv_filename, lettre_filename, attestation_filenames, poste),
        daemon=True
    ).start()
    
    return jsonify({'message': 'Analyse automatique re-déclenchée', 'token': token}), 202

# ══════════════════════════════════════════════════════════════════════════════
# 📄 ROUTES D'EXPORT DE RAPPORTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/recruteur/export/<format>', methods=['GET'])
@jwt_required()
def export_candidates(format):
    try:
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
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if format.lower() == 'csv':
            csv_data = generate_csv_report(result)
            return send_file(
                io.BytesIO(csv_data.encode('utf-8-sig')),
                mimetype='text/csv',
                as_attachment=True,
                download_name=f'rapport_candidats_{timestamp}.csv'
            )
        
        elif format.lower() in ['excel', 'xlsx']:
            if not OPENPYXL_AVAILABLE:
                return jsonify({'error': 'Export Excel non disponible. Installez openpyxl.'}), 503
            excel_data = generate_excel_report(result)
            if not excel_
                return jsonify({'error': 'Erreur lors de la génération du fichier Excel'}), 500
            return send_file(
                excel_data,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'rapport_candidats_{timestamp}.xlsx'
            )
        
        elif format.lower() == 'pdf':
            if not REPORTLAB_AVAILABLE:
                return jsonify({'error': 'Export PDF non disponible. Installez reportlab.'}), 503
            pdf_data = generate_pdf_report(result)
            if not pdf_
                return jsonify({'error': 'Erreur lors de la génération du fichier PDF'}), 500
            return send_file(
                pdf_data,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f'rapport_candidats_{timestamp}.pdf'
            )
        
        else:
            return jsonify({'error': 'Format non supporté. Utilisez: csv, excel ou pdf'}), 400
    
    except Exception as e:
        print(f"❌ Erreur export: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not 
        return jsonify({'error': 'Candidat introuvable'}), 404
    body = request.json or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))
    nom_complet = f"{data.get('prenom', '')} {data.get('nom', '')}"
    poste = data.get('poste', '')
    to_email = data.get('email', '')
    
    if msg_type == 'retenu':
        sujet = f"Félicitations – Votre candidature pour le poste {poste} a été retenue"
        corps = f"""Madame, Monsieur {nom_complet},\n\nNous avons le plaisir de vous informer que votre candidature pour le poste de {poste} a été retenue à l'issue de notre processus de présélection.\n\nNous vous contacterons très prochainement pour vous communiquer les modalités de la prochaine étape du processus de recrutement.\n\nDans l'attente, nous restons disponibles pour toute question.\n\nCordialement,\nL'équipe Ressources Humaines\nRecrutBank"""
    elif msg_type == 'entretien':
        sujet = f"Invitation à un entretien – Poste {poste}"
        corps = f"""Madame, Monsieur {nom_complet},\n\nSuite à l'examen attentif de votre candidature pour le poste de {poste}, nous avons le plaisir de vous inviter à un entretien avec notre équipe.\n\nNous prendrons contact avec vous dans les meilleurs délais pour convenir d'une date et d'un horaire qui vous conviennent.\n\nCordialement,\nL'équipe Ressources Humaines\nRecrutBank"""
    else:
        sujet = f"Réponse à votre candidature – Poste {poste}"
        corps = f"""Madame, Monsieur {nom_complet},\n\nNous vous remercions sincèrement de l'intérêt que vous portez à notre institution et du temps consacré à votre candidature pour le poste de {poste}.\n\nAprès examen attentif de votre dossier et compte tenu du nombre important de candidatures reçues, nous avons le regret de vous informer que votre candidature n'a pas été retenue pour la suite du processus de sélection.\n\nNous vous encourageons vivement à postuler à nouveau pour toute opportunité future qui correspondrait à votre profil et vous souhaitons plein succès dans votre recherche d'emploi.\n\nCordialement,\nL'équipe Ressources Humaines\nRecrutBank"""
    
    return jsonify({'to': to_email, 'nom': nom_complet, 'sujet': sujet, 'corps': corps}), 200

# ══════════════════════════════════════════════════════════════════════════════
# 🔓 SERVIR LES FICHIERS UPLOADÉS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/recruteur/uploads/<filename>', methods=['GET'])
def serve_upload(filename):
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    
    filepath = os.path.join(UPLOAD_FOLDER, safe)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Fichier introuvable', 'filename': filename, 'path': filepath}), 404
    
    mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    return send_from_directory(UPLOAD_FOLDER, safe, mimetype=mime_type, as_attachment=False)

# ══════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Serveur RecrutBank démarré sur le port {port}")
    print(f"📋 Grille Word chargée: {len(GRILLE)} postes")
    print(f"⚠️ ÉLIMINATION STRICTE : Si UN critère éliminatoire manque → Score=0")
    print(f"🔍 Analyse auto: MATCHING EXACT (soit ça passe, soit ça casse)")
    print(f"📄 Analyse TOUS documents: CV + Lettre + Certificats")
    print(f"🏆 Classement STRICT avec RANG automatique + EMAIL")
    print(f"📊 Scoring Excel: Adéquation(0-3)+Cohérence(0-2)+Risque(0-3)+CV(0-1)+Lettre(0-1)=/10")
    print(f"📧 Email extrait dans TOUS les formats")
    print(f"📏 Rapports LARGES pour texte non coupé")
    if REPORTLAB_AVAILABLE:
        print(f"   ✅ reportlab installé (PDF)")
    if OPENPYXL_AVAILABLE:
        print(f"   ✅ openpyxl installé (Excel)")
    app.run(host="0.0.0.0", port=port, debug=False)

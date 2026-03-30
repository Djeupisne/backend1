# server.py - Backend Flask pour RecrutBank avec analyse automatique EXACTE des CV
# Basé sur la grille Word : 3 blocs (Éliminatoire / Cohérence / Signaux)
# Classement STRICT des candidats par poste + Export rapports avec Téléphone
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
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER
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
# 📋 GRILLE DE PRÉSÉLECTION - VÉRIFICATION STRICTE (Basée sur Word)
# ══════════════════════════════════════════════════════════════════════════════

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
            "CV flou (missions génériques)"
        ]
    },
    "Analyste Crédit CCB": {
        "eliminatoire": [
            "Expérience en analyse crédit",
            "Capacité à lire des états financiers",
            "Expérience technique ou analytique"
        ],
        "a_verifier": [
            "Type de clients : PME",
            "Type de clients : particuliers",
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
            "Interaction avec auditeurs"
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
            "Exposition à FX / taux / liquidité"
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
            "Produits FICC (FX, taux, commodities)",
            "Reporting risque"
        ],
        "points_attention": [
            "CV trop théorique (académique sans pratique)",
            "Aucune mention d'outils",
            "Incapacité implicite à modéliser"
        ]
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": [
            "Expérience en réseau / infrastructure",
            "Exposition à environnement critique (banque, telco, datacenter)",
            "Notion de sécurité IT"
        ],
        "a_verifier": [
            "Gestion réseaux (LAN/WAN, VPN)",
            "Gestion serveurs (Windows/Linux)",
            "Cloud (même basique)",
            "Gestion des incidents",
            "Assurance de la disponibilité"
        ],
        "signaux_forts": [
            "Cybersécurité / firewall",
            "Haute disponibilité / PRA/PCA",
            "Gestion ATM ou systèmes bancaires",
            "Certifications (Cisco, Microsoft, etc.)"
        ],
        "points_attention": [
            "Profil trop helpdesk",
            "CV sans détail technique",
            "Aucune mention de sécurité"
        ]
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# 🔍 MAPPING MOTS-CLÉS EXACTS - VÉRIFICATION STRICTE
# ══════════════════════════════════════════════════════════════════════════════

KEYWORD_MAPPING = {
    # === Responsable Administration de Crédit ===
    "Expérience bancaire": ["expérience bancaire", "secteur bancaire", "établissement bancaire", "banque commerciale", "métier bancaire", "banque"],
    "3 ans ou plus en crédit / risque": ["3 ans crédit", "trois ans crédit", "3 années crédit", "expérience crédit", "gestion risque crédit", "3 ans risque", "4 ans", "5 ans", "6 ans", "7 ans", "8 ans", "9 ans", "10 ans"],
    "Exposition aux garanties ou conformité": ["garanties", "nantissement", "hypothèque", "sûreté", "conformité", "COBAC", "réglementation bancaire", "BCAC", "garantie"],
    "Validation de dossiers": ["validation dossier", "instruction crédit", "approbation crédit", "dossier crédit", "validation des dossiers", "valider des dossiers"],
    "Gestion des garanties": ["gestion garanties", "suivi garanties", "garanties réelles", "sûretés", "portefeuille garanties", "gérer les garanties"],
    "Participation à des audits": ["audit", "contrôle interne", "inspection", "review", "compliance audit", "audit interne", "participer aux audits"],
    "IFRS 9": ["IFRS 9", "IAS 39", "normes IFRS", "comptabilité IFRS", "IFRS"],
    "COBAC / conformité": ["COBAC", "conformité bancaire", "régulation bancaire", "BCEAO", "BCAC", "commission bancaire"],
    "Suivi portefeuille / impayés": ["portefeuille crédit", "impayés", "recouvrement", "contentieux", "encours", "suivi portefeuille"],
    
    # === Analyste Crédit CCB ===
    "Expérience en analyse crédit": ["analyse crédit", "credit analysis", "évaluation crédit", "scoring crédit", "analyse financière crédit", "analyste crédit"],
    "Capacité à lire des états financiers": ["états financiers", "bilan", "compte de résultat", "ratios financiers", "analyse financière", "lire les états financiers"],
    "Expérience technique ou analytique": ["analyse", "technique", "évaluation", "modélisation", "étude", "analytique"],
    "Type de clients : PME": ["PME", "petites entreprises", "moyennes entreprises", "TPE", "entreprises"],
    "Type de clients : particuliers": ["particuliers", "clients particuliers", "retail", "clientèle particulière"],
    "Structuration de crédit": ["structuration crédit", "montage crédit", "dossier de crédit", "structurer un crédit", "montage de crédits"],
    "Avis de crédit": ["avis crédit", "recommandation crédit", "opinion crédit", "credit opinion", "avis de crédit", "donner un avis"],
    "Cash-flow analysis": ["cash-flow", "cash flow", "flux de trésorerie", "FCF", "free cash flow", "cash-flow analysis"],
    "Montage de crédit": ["montage crédit", "structuration", "dossier de crédit", "montage de dossiers", "montage des crédits"],
    "Comités de crédit": ["comité crédit", "commission crédit", "credit committee", "validation comité", "comité des engagements"],
    
    # === Archiviste ===
    "Expérience en gestion documentaire structurée": ["gestion documentaire", "archivage", "GED", "records management", "classement", "documentation", "gestion des documents"],
    "Rigueur démontrée": ["rigueur", "méthode", "organisation", "procédures", "processus", "traçabilité", "précision", "rigoureux"],
    "Archivage physique et électronique": ["archivage physique", "archivage électronique", "dématérialisation", "numérisation", "archives", "archivage physique et électronique"],
    "Gestion des dossiers sensibles": ["dossiers sensibles", "confidentiel", "sécurisé", "accès restreint", "données sensibles", "dossiers confidentiels"],
    "Expérience en banque / juridique": ["banque", "établissement financier", "juridique", "droit bancaire", "secteur bancaire"],
    "Manipulation de garanties ou contrats": ["garanties", "contrats", "conventions", "actes juridiques", "documentation juridique", "manipulation des garanties"],
    
    # === Senior Finance Officer ===
    "Expérience en reporting financier structuré": ["reporting financier", "reporting", "tableaux de bord", "KPI", "indicateurs", "reporting structuré"],
    "Exposition aux états financiers": ["états financiers", "bilan", "compte de résultat", "consolidation", "reporting financier", "états financiers consolidés"],
    "Interaction avec auditeurs": ["auditeurs", "audit", "CAC", "commissaires aux comptes", "interaction avec auditeurs", "audit externe"],
    "Production états financiers": ["production états financiers", "établissement des états financiers", "élaboration des états financiers", "production des comptes"],
    "Reporting groupe": ["reporting groupe", "reporting consolidé", "reporting groupe", "consolidation groupe"],
    "Connaissance IFRS": ["IFRS", "normes internationales", "comptabilité internationale", "IAS", "IFRS consolidation", "normes IFRS"],
    "Contraintes réglementaires": ["réglementation", "contraintes réglementaires", "conformité", "réglementaire", "normes réglementaires"],
    "IFRS / consolidation": ["IFRS", "consolidation", "comptes consolidés", "IFRS consolidation", "normes IFRS"],
    "Interaction avec CAC": ["CAC", "commissaires aux comptes", "audit légal", "interaction CAC", "audit externe"],
    "Outils type SPECTRA / CERBER / ERP": ["SPECTRA", "CERBER", "ERP", "SAP", "Oracle", "outil de reporting", "logiciel de consolidation"],
    
    # === Market Risk Officer ===
    "Base en risques de marché": ["risque marché", "market risk", "risques de marché", "gestion des risques de marché", "risque de marché"],
    "Compétences quantitatives": ["quantitatif", "quantitative", "mathématiques", "statistiques", "modélisation", "compétences quantitatives"],
    "Exposition à FX / taux / liquidité": ["FX", "change", "taux", "liquidité", "forex", "taux d'intérêt", "risque de liquidité", "FX taux"],
    "Maîtrise VaR / stress testing": ["VaR", "Value at Risk", "stress testing", "back-testing", "scénarios", "value at risk", "VaR stress"],
    "Analyse des positions": ["analyse des positions", "positions", "analyse de portefeuille", "suivi des positions", "positions de marché"],
    "Excel avancé": ["Excel avancé", "Excel", "tableaux croisés", "macros Excel", "Excel VBA", "maîtrise Excel"],
    "VBA / Python": ["VBA", "Python", "programmation", "scripting", "VBA Python", "développement VBA"],
    "Bâle II / III": ["Bâle II", "Bâle III", "Basel II", "Basel III", "accords de Bâle", "réglementation Bâle", "Bâle 2", "Bâle 3"],
    "Gestion ALM / liquidité": ["ALM", "Asset Liability Management", "liquidité", "gestion ALM", "actif-passif", "gestion de la liquidité"],
    "Produits FICC (FX, taux, commodities)": ["FICC", "produits dérivés", "commodities", "matières premières", "produits de taux", "FX taux commodities"],
    "Reporting risque": ["reporting risque", "reporting des risques", "rapport de risque", "reporting risque marché"],
    
    # === IT Réseau & Infrastructure ===
    "Expérience en réseau / infrastructure": ["réseau", "infrastructure", "LAN", "WAN", "VPN", "réseaux", "infrastructure IT", "network"],
    "Exposition à environnement critique (banque, telco, datacenter)": ["banque", "telco", "télécom", "datacenter", "centre de données", "environnement critique", "secteur bancaire", "opérateur télécom"],
    "Notion de sécurité IT": ["sécurité IT", "cybersécurité", "sécurité informatique", "firewall", "sécurité réseau", "IT security"],
    "Gestion réseaux (LAN/WAN, VPN)": ["LAN", "WAN", "VPN", "réseaux locaux", "réseaux étendus", "virtual private network", "gestion des réseaux"],
    "Gestion serveurs (Windows/Linux)": ["Windows Server", "Linux", "serveurs", "administration serveurs", "Windows", "Unix", "gestion des serveurs"],
    "Cloud (même basique)": ["cloud", "AWS", "Azure", "Google Cloud", "cloud computing", "infrastructure cloud", "cloud basique"],
    "Gestion des incidents": ["incident", "gestion des incidents", "support", "résolution d'incidents", "incident management", "ticketing"],
    "Assurance de la disponibilité": ["disponibilité", "haute disponibilité", "SLA", "uptime", "disponibilité du service", "assurer la disponibilité"],
    "Cybersécurité / firewall": ["cybersécurité", "firewall", "sécurité", "IDS", "IPS", "SIEM", "pentest", "sécurité informatique"],
    "Haute disponibilité / PRA/PCA": ["haute disponibilité", "PRA", "PCA", "plan de reprise", "continuité d'activité", "disaster recovery", "high availability"],
    "Gestion ATM ou systèmes bancaires": ["ATM", "systèmes bancaires", "GAB", "distributeur automatique", "système bancaire", "bancaire"],
    "Certifications (Cisco, Microsoft, etc.)": ["CCNA", "CCNP", "CCIE", "Cisco", "Microsoft", "certification", "Network+", "MCSE", "certification IT"]
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
# 🔧 PARSING DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf(filepath):
    try:
        text = ""
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                content = page.extract_text()
                if content:
                    text += content + "\n"
        return text
    except Exception as e:
        print(f"⚠️ Erreur lecture PDF: {e}")
        return ""

def extract_text_from_docx(filepath):
    try:
        doc = Document(filepath)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        print(f"⚠️ Erreur lecture DOCX: {e}")
        return ""

def extract_text_from_file(filepath, filename):
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
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s\-/]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ══════════════════════════════════════════════════════════════════════════════
# 🧠 MOTEUR D'ANALYSE CV — VÉRIFICATION STRICTE ET EXACTE
# ══════════════════════════════════════════════════════════════════════════════

def check_criterion_match(criterion, full_text):
    """
    Vérifie STRICTEMENT et EXACTEMENT si un critère est validé.
    """
    mots_cles = KEYWORD_MAPPING.get(criterion, [])
    if not mots_cles:
        return False, []
    
    found_keywords = [kw for kw in mots_cles if kw.lower() in full_text]
    is_present = len(found_keywords) > 0
    
    return is_present, found_keywords


def analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste):
    """
    Analyse STRICTE selon la grille Word.
    🔴 Bloc 1: Éliminatoire (critères POSITIFS requis) → Score = 0 si manquant
    🟠 Bloc 2: Cohérence → +1 point par critère validé
    🟡 Bloc 3: Signaux → +2 points par signal détecté
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
    
    full_text = normalize_text(cv_text + " " + (lettre_text or "") + " " + (attestation_text or ""))
    
    checklist = {}
    flags_elim = []
    signaux = []
    points_bloc2 = 0
    points_bloc3 = 0
    details = {
        'cv_words': len(cv_text.split()) if cv_text else 0,
        'lettre_words': len(lettre_text.split()) if lettre_text else 0,
        'attestation_words': len(attestation_text.split()) if attestation_text else 0,
        'criteres_valides_bloc2': [],
        'signaux_valides_bloc3': [],
        'alertes_attention': [],
        'matching_details': {}
    }
    
    # 🔴 BLOC 1 : ÉLIMINATOIRE (critères POSITIFS requis)
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        
        checklist[key] = is_present
        
        if not is_present:
            flags_elim.append(f"❌ {crit} (non trouvé)")
            details['alertes_attention'].append(f"🔴 Éliminatoire: {crit} manquant")
            details['matching_details'][crit] = {
                'found': False, 
                'status': 'ÉLIMINATOIRE - Critère requis non trouvé',
                'keywords_searched': KEYWORD_MAPPING.get(crit, [])[:5]
            }
        else:
            details['matching_details'][crit] = {
                'found': True, 
                'status': 'VALIDÉ',
                'matched': found_keywords
            }
    
    # 🟠 BLOC 2 : COHÉRENCE (+1 point par critère validé)
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"🟠 {crit}")
    
    # 🟡 BLOC 3 : SIGNAUX (+2 points par signal détecté)
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"🟡 {crit}")
    
    # ⚠️ POINTS D'ATTENTION
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        if is_present:
            details['alertes_attention'].append(f"⚠️ {crit}")
    
    # 🧮 CALCUL DU SCORE FINAL selon modèle Excel (sur 10)
    if flags_elim:
        score_final = 0
        details['alertes_attention'].insert(0, f"🚫 Score bloqué à 0 : {len(flags_elim)} critère(s) éliminatoire(s) manquant(s)")
    else:
        # Mapping selon modèle Excel :
        adequation = min(3, len(details['criteres_valides_bloc2']))
        coherence = min(2, points_bloc2)
        risque_metier = min(3, len(signaux))
        qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
        lettre_motiv = 1 if attestation_text and len(attestation_text.strip()) > 0 else 0
        
        score_total_excel = adequation + coherence + risque_metier + qualite_cv + lettre_motiv
        score_final = min(10, score_total_excel)
    
    score_breakdown = {
        'bloc1_eliminatoire': len(flags_elim) > 0,
        'flags_eliminatoires_count': len(flags_elim),
        'bloc2_criteres_valides': len(details['criteres_valides_bloc2']),
        'bloc2_points': points_bloc2,
        'bloc3_signaux_detectes': len(signaux),
        'bloc3_points': points_bloc3,
        'total_raw_points': points_bloc2 + points_bloc3,
        'score_final': score_final,
        'note': f"Score 0 = {len(flags_elim)} critère(s) éliminatoire(s) manquant(s)" if flags_elim else f"Score Excel: {score_final}/10"
    }
    
    return {
        'score': score_final,
        'checklist': checklist,
        'flags_eliminatoires': flags_elim,
        'signaux_detectes': signaux,
        'details': details,
        'score_breakdown': score_breakdown
    }


def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste):
    try:
        key = f"candidat:{token}"
        
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        
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
        attestation_text = " ".join(attestation_texts)
        
        result = analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste)
        
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
        
    except Exception as e:
        print(f"⚠️ Erreur analyse auto pour candidat {token}: {e}")
        redis_client.hset(f"candidat:{token}", mapping={
            "analyse_status": "error",
            "analyse_error": str(e),
            "analyse_auto_date": datetime.datetime.now().isoformat()
        })


# ══════════════════════════════════════════════════════════════════════════════
# 🏆 SYSTÈME DE CLASSEMENT STRICT DES CANDIDATS
# ══════════════════════════════════════════════════════════════════════════════

def calculate_ranking_score(candidat_data, poste):
    """
    Calcule un score de classement STRICT pour comparer les candidats.
    Priorité : Éliminatoire → Score → Signaux → Cohérence → Date
    """
    sb = candidat_data.get('score_breakdown_parsed', {})
    details = candidat_data.get('analyse_details_parsed', {})
    
    # 🔴 Facteur 1: Éliminatoire (poids maximal)
    if sb.get('bloc1_eliminatoire'):
        return -999  # Dernier automatiquement
    
    # 🟡 Facteur 2: Score principal (0-10)
    score_principal = int(candidat_data.get('score', 0))
    
    # 🟡 Facteur 3: Signaux forts détectés (départage, poids élevé)
    signaux_count = len(candidat_data.get('signaux_detectes_parsed', []))
    signaux_bonus = signaux_count * 0.5  # +0.5 par signal fort
    
    # 🟠 Facteur 4: Critères "à vérifier" validés (départage)
    criteres_valides = sb.get('bloc2_criteres_valides', 0)
    coherence_bonus = criteres_valides * 0.2  # +0.2 par critère
    
    # 📄 Facteur 5: Lettre de motivation fournie (léger bonus)
    lettre_bonus = 0.1 if candidat_data.get('lettre_filename') else 0
    
    # 📅 Facteur 6: Ancienneté (plus récent = léger avantage)
    try:
        date_candidature = datetime.datetime.fromisoformat(candidat_data.get('date_candidature', ''))
        days_since = (datetime.datetime.now() - date_candidature).days
        date_bonus = max(0, (30 - min(days_since, 30)) * 0.01)  # Max +0.3 pour candidature très récente
    except:
        date_bonus = 0
    
    # Calcul du score de classement (sur ~12 points max)
    ranking_score = score_principal + signaux_bonus + coherence_bonus + lettre_bonus + date_bonus
    
    return round(ranking_score, 2)


def generate_ranking_for_poste(poste, candidats_data):
    """
    Génère un classement STRICT des candidats pour un poste donné.
    Retourne une liste triée avec détails de comparaison.
    """
    # Filtrer les candidats pour ce poste
    candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
    
    # Calculer le score de classement pour chaque candidat
    for c in candidats_poste:
        c['ranking_score'] = calculate_ranking_score(c, poste)
        c['ranking_position'] = 0  # Sera calculé après tri
    
    # 🔍 Tri STRICT selon critères hiérarchisés :
    # 1. Éliminatoire d'abord (score -999 = dernier)
    # 2. Score de classement décroissant
    # 3. Nombre de signaux forts décroissant (départage)
    # 4. Date de candidature décroissante (départage final)
    candidats_poste.sort(key=lambda x: (
        -x['ranking_score'],  # Score principal décroissant
        -len(x.get('signaux_detectes_parsed', [])),  # Signaux décroissant
        x.get('date_candidature', '')  # Date croissante (plus récent en premier)
    ), reverse=False)
    
    # Assigner les positions
    for idx, c in enumerate(candidats_poste, 1):
        c['ranking_position'] = idx
        
        # Déterminer la recommandation basée sur le rang
        total_candidats = len(candidats_poste)
        if idx == 1 and c['ranking_score'] >= 8:
            c['ranking_recommendation'] = "🥇 Top candidat - Entretien prioritaire"
        elif idx <= 3 and c['ranking_score'] >= 6:
            c['ranking_recommendation'] = "🥈 Shortlist - Entretien recommandé"
        elif c['ranking_score'] >= 4:
            c['ranking_recommendation'] = "🥉 Potentiel - À considérer"
        else:
            c['ranking_recommendation'] = "❌ Non prioritaire"
    
    return candidats_poste


# ══════════════════════════════════════════════════════════════════════════════
# 📄 FONCTIONS D'EXPORT DE RAPPORTS (Modèle Excel avec Téléphone)
# ══════════════════════════════════════════════════════════════════════════════

def generate_excel_report(candidats_data, poste_filter=None):
    """Génère un rapport Excel similaire au modèle avec Téléphone"""
    if not OPENPYXL_AVAILABLE:
        return None
    
    wb = Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    
    postes_to_export = [poste_filter] if poste_filter else list(set(c.get('poste', 'Inconnu') for c in candidats_data))
    
    for poste in postes_to_export:
        candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
        ws = wb.create_sheet(title=poste[:31])
        
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
        ws.merge_cells('A1:I1')
        title_cell = ws['A1']
        title_cell.value = f"RAPPORT DE RECRUTEMENT - {poste}"
        title_cell.font = title_font
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        title_cell.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
        ws.row_dimensions[1].height = 30
        
        # En-têtes du tableau (Modèle Excel avec Téléphone)
        headers = [
            'Candidat',
            'Téléphone',
            'Adéquation expérience (0-3)',
            'Cohérence parcours (0-2)',
            'Exposition au risque de métier (0-3)',
            'Qualité du CV (0-1)',
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
        
        # Données des candidats
        for row_idx, c in enumerate(candidats_poste, 4):
            sb = c.get('score_breakdown_parsed', {})
            
            adequation = min(3, sb.get('bloc2_criteres_valides', 0))
            coherence = min(2, sb.get('bloc2_points', 0))
            risque_metier = min(3, sb.get('bloc3_signaux_detectes', 0))
            qualite_cv = 1 if int(c.get('score', 0)) >= 5 else 0
            lettre_motiv = 1 if c.get('lettre_filename') else 0
            score_total = adequation + coherence + risque_metier + qualite_cv + lettre_motiv
            
            if int(c.get('score', 0)) >= 8:
                recommandation = "Entretien prioritaire"
            elif int(c.get('score', 0)) >= 6:
                recommandation = "Entretien si besoin"
            elif int(c.get('score', 0)) >= 4:
                recommandation = "À revoir"
            else:
                recommandation = "Rejet"
            
            nom_complet = f"{c.get('prenom', '')} {c.get('nom', '')}".strip()
            telephone = c.get('telephone', '') or '–'
            
            row_data = [
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
                
                if col == 8:
                    if score_total >= 8:
                        cell.fill = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
                    elif score_total >= 6:
                        cell.fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
                    elif score_total >= 4:
                        cell.fill = PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid")
                    else:
                        cell.fill = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
                    cell.font = Font(bold=True)
                
                if col == 9:
                    if recommandation == "Entretien prioritaire":
                        cell.fill = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
                    elif recommandation == "Entretien si besoin":
                        cell.fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
                    elif recommandation == "Rejet":
                        cell.fill = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
        
        column_widths = [25, 18, 25, 25, 30, 20, 22, 15, 25]
        for col, width in enumerate(column_widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        
        for row in range(3, ws.max_row + 1):
            ws.row_dimensions[row].height = 20
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def generate_csv_report(candidats_data):
    """Génère un rapport CSV avec Téléphone"""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    
    writer.writerow([
        'Nom', 'Prénom', 'Email', 'Téléphone', 'Poste', 'Date candidature',
        'Score (/10)', 'Statut', 'Éliminatoire', 'Cohérence (pts)', 'Signaux (pts)', 'Note'
    ])
    
    for c in candidats_data:
        sb = c.get('score_breakdown_parsed', {})
        writer.writerow([
            c.get('nom', ''),
            c.get('prenom', ''),
            c.get('email', ''),
            c.get('telephone', '') or '–',
            c.get('poste', ''),
            c.get('date_candidature', ''),
            c.get('score', '0'),
            c.get('statut', ''),
            'OUI' if sb.get('bloc1_eliminatoire') else 'NON',
            sb.get('bloc2_points', 0),
            sb.get('bloc3_points', 0),
            sb.get('note', '')
        ])
    
    output.seek(0)
    return output.getvalue()


def generate_pdf_report(candidats_data):
    """Génère un rapport PDF avec Téléphone"""
    if not REPORTLAB_AVAILABLE:
        return None
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=2*cm, bottomMargin=2*cm)
    elements = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor('#1a3a5c'), spaceAfter=20, alignment=TA_CENTER)
    elements.append(Paragraph("Rapport des Candidatures - RecrutBank", title_style))
    elements.append(Spacer(1, 0.3*cm))
    
    date_style = ParagraphStyle('DateStyle', parent=styles['Normal'], fontSize=9, textColor=colors.grey)
    elements.append(Paragraph(f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y à %H:%M')}", date_style))
    elements.append(Spacer(1, 0.8*cm))
    
    data = [['Candidat', 'Téléphone', 'Poste', 'Score (/10)', 'Statut', 'Recommandation']]
    
    for c in candidats_data:
        score = int(c.get('score', 0))
        if score >= 8:
            recommandation = "Entretien prioritaire"
        elif score >= 6:
            recommandation = "Entretien si besoin"
        elif score >= 4:
            recommandation = "À revoir"
        else:
            recommandation = "Rejet"
        
        data.append([
            f"{c.get('prenom', '')} {c.get('nom', '')}",
            c.get('telephone', '') or '–',
            c.get('poste', ''),
            f"{score}/10",
            c.get('statut', ''),
            recommandation
        ])
    
    table = Table(data, colWidths=[4*cm, 3.5*cm, 4*cm, 2*cm, 2.5*cm, 3.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a5c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey])
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
# 🏆 ROUTES DE CLASSEMENT STRICT DES CANDIDATS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/recruteur/classement/<poste>', methods=['GET'])
@jwt_required()
def get_classement(poste):
    """
    Retourne un classement STRICT des candidats pour un poste donné.
    Comparaison basée sur : Score → Signaux forts → Cohérence → Date
    """
    if poste not in POSTES:
        return jsonify({'error': 'Poste inconnu', 'postes_disponibles': POSTES}), 404
    
    # Récupérer tous les candidats
    keys = redis_client.keys("candidat:*")
    result = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        # Parser les champs JSON
        for field in ['score_breakdown', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details']:
            if c.get(field):
                try: c[f'{field}_parsed'] = json.loads(c[field])
                except: pass
        result.append(c)
    
    # Générer le classement STRICT
    classement = generate_ranking_for_poste(poste, result)
    
    # Préparer la réponse
    response = {
        'poste': poste,
        'total_candidats': len(classement),
        'classement': [
            {
                'rang': c['ranking_position'],
                'nom': f"{c.get('prenom', '')} {c.get('nom', '')}".strip(),
                'email': c.get('email', ''),
                'telephone': c.get('telephone', ''),
                'score': int(c.get('score', 0)),
                'ranking_score': c['ranking_score'],
                'recommandation': c['ranking_recommendation'],
                'signaux_forts': len(c.get('signaux_detectes_parsed', [])),
                'criteres_valides': c.get('score_breakdown_parsed', {}).get('bloc2_criteres_valides', 0),
                'eliminatoires_manquants': c.get('score_breakdown_parsed', {}).get('flags_eliminatoires_count', 0),
                'date_candidature': c.get('date_candidature', '')
            }
            for c in classement
        ],
        'criteres_classement': {
            '1_priorite': 'Score global (0-10) - critères éliminatoires bloquent à 0',
            '2_departage': 'Nombre de signaux forts détectés (pondération x0.5)',
            '3_departage': 'Nombre de critères "à vérifier" validés (pondération x0.2)',
            '4_departage': 'Date de candidature (plus récent avantagé)'
        }
    }
    
    return jsonify(response), 200


@app.route('/api/recruteur/classement/<poste>/export/<format>', methods=['GET'])
@jwt_required()
def export_classement(poste, format):
    """
    Export du classement en CSV, Excel ou PDF
    """
    if poste not in POSTES:
        return jsonify({'error': 'Poste inconnu'}), 404
    
    # Récupérer et classer les candidats
    keys = redis_client.keys("candidat:*")
    result = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        for field in ['score_breakdown', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details']:
            if c.get(field):
                try: c[f'{field}_parsed'] = json.loads(c[field])
                except: pass
        result.append(c)
    
    classement = generate_ranking_for_poste(poste, result)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if format.lower() == 'csv':
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
        writer.writerow(['Rang', 'Candidat', 'Téléphone', 'Score', 'Signaux forts', 'Critères validés', 'Recommandation'])
        for c in classement:
            writer.writerow([
                c['ranking_position'],
                f"{c.get('prenom', '')} {c.get('nom', '')}".strip(),
                c.get('telephone', '') or '–',
                c.get('score', '0'),
                len(c.get('signaux_detectes_parsed', [])),
                c.get('score_breakdown_parsed', {}).get('bloc2_criteres_valides', 0),
                c['ranking_recommendation']
            ])
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'classement_{poste}_{timestamp}.csv'
        )
    
    elif format.lower() in ['excel', 'xlsx']:
        if not OPENPYXL_AVAILABLE:
            return jsonify({'error': 'Export Excel non disponible'}), 503
        
        wb = Workbook()
        if 'Sheet' in wb.sheetnames:
            del wb['Sheet']
        ws = wb.active
        ws.title = f"Classement {poste[:20]}"
        
        header_fill = PatternFill(start_color="1a3a5c", end_color="1a3a5c", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
        
        headers = ['Rang', 'Candidat', 'Téléphone', 'Score', 'Signaux forts', 'Critères validés', 'Recommandation']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
            cell.alignment = Alignment(horizontal='center')
        
        for row_idx, c in enumerate(classement, 2):
            row = [
                c['ranking_position'],
                f"{c.get('prenom', '')} {c.get('nom', '')}".strip(),
                c.get('telephone', '') or '–',
                int(c.get('score', 0)),
                len(c.get('signaux_detectes_parsed', [])),
                c.get('score_breakdown_parsed', {}).get('bloc2_criteres_valides', 0),
                c['ranking_recommendation']
            ]
            for col, value in enumerate(row, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.border = border
                cell.alignment = Alignment(horizontal='center')
                if col == 1:  # Rang
                    cell.font = Font(bold=True)
                    if c['ranking_position'] == 1:
                        cell.fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
                    elif c['ranking_position'] <= 3:
                        cell.fill = PatternFill(start_color="C0C0C0", end_color="C0C0C0", fill_type="solid")
        
        for col in range(1, 8):
            ws.column_dimensions[get_column_letter(col)].width = 20
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'classement_{poste}_{timestamp}.xlsx'
        )
    
    elif format.lower() == 'pdf':
        if not REPORTLAB_AVAILABLE:
            return jsonify({'error': 'Export PDF non disponible'}), 503
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=2*cm, bottomMargin=2*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, textColor=colors.HexColor('#1a3a5c'), spaceAfter=15, alignment=TA_CENTER)
        elements.append(Paragraph(f"CLASSEMENT - {poste}", title_style))
        elements.append(Spacer(1, 0.2*cm))
        
        date_style = ParagraphStyle('DateStyle', parent=styles['Normal'], fontSize=9, textColor=colors.grey)
        elements.append(Paragraph(f"Généré le {datetime.datetime.now().strftime('%d/%m/%Y')}", date_style))
        elements.append(Spacer(1, 0.5*cm))
        
        data = [['Rang', 'Candidat', 'Score', 'Signaux', 'Recommandation']]
        for c in classement[:20]:  # Top 20 pour PDF
            data.append([
                str(c['ranking_position']),
                f"{c.get('prenom', '')} {c.get('nom', '')}".strip()[:25],
                str(c.get('score', 0)),
                str(len(c.get('signaux_detectes_parsed', []))),
                c['ranking_recommendation'][:30]
            ])
        
        table = Table(data, colWidths=[1.5*cm, 6*cm, 2*cm, 2*cm, 5*cm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a5c')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
        ]))
        
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=f'classement_{poste}_{timestamp}.pdf')
    
    else:
        return jsonify({'error': 'Format non supporté'}), 400


# ══════════════════════════════════════════════════════════════════════════════
# 📄 ROUTES D'EXPORT DE RAPPORTS GÉNÉRAUX
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
            if not excel_data:
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
            if not pdf_data:
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
    if not data:
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
    print(f"🔍 Analyse auto: VÉRIFICATION STRICTE ET EXACTE")
    print(f"🏆 Classement STRICT des candidats par poste disponible")
    print(f"📊 Scoring Excel: Adéquation(0-3)+Cohérence(0-2)+Risque(0-3)+CV(0-1)+Lettre(0-1)=/10")
    print(f"📞 Téléphone inclus dans tous les exports")
    print(f"📁 Upload multiple certificats supporté")
    if REPORTLAB_AVAILABLE:
        print(f"   ✅ reportlab installé (PDF)")
    if OPENPYXL_AVAILABLE:
        print(f"   ✅ openpyxl installé (Excel)")
    app.run(host="0.0.0.0", port=port, debug=False)

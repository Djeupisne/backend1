# server.py - Backend Flask pour RecrutBank avec analyse automatique EXACTE des CV
# Basé sur la grille Word : 3 blocs (Éliminatoire / Cohérence / Signaux)
# Support upload multiple de certificats analysés ensemble
# ============================================================================

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, redis, json, re, threading, mimetypes
from werkzeug.utils import secure_filename

# ── PARSING DOCUMENTS ──────────────────────────────────────────────────────
import PyPDF2
from docx import Document

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
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 Mo

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

# ── GRILLE DE PRÉSÉLECTION (3 blocs Word - vérification EXACTE) ───────────────
# 🔴 Bloc 1: Adéquation structurelle (filtre dur) → Score = 0 si déclenché
# 🟠 Bloc 2: Cohérence du parcours → +1 point par critère validé EXACTEMENT
# 🟡 Bloc 3: Signaux de qualité → +2 points par signal détecté EXACTEMENT
# ⚠️ Points d'attention → Alertes uniquement (pas d'impact score)

GRILLE = {
    "Responsable Administration de Crédit": {
        "eliminatoire": [
            "Pas d'expérience bancaire",
            "Moins de 3 ans en crédit / risque",
            "Aucune exposition aux garanties ou conformité"
        ],
        "a_verifier": [
            "A-t-il déjà validé des dossiers ?",
            "A-t-il géré des garanties ?",
            "A-t-il participé à des audits ?"
        ],
        "signaux_forts": [
            "Mention de : IFRS 9",
            "Mention de : COBAC / conformité",
            "Mention de : suivi portefeuille / impayés"
        ],
        "points_attention": [
            "Parcours trop « comptable pur »",
            "Rôle uniquement administratif sans responsabilité",
            "CV flou (missions génériques)"
        ]
    },
    "Analyste Crédit CCB": {
        "eliminatoire": [
            "Pas d'expérience en analyse crédit",
            "Profil purement commercial sans analyse",
            "Incapacité à lire des états financiers"
        ],
        "a_verifier": [
            "Type de clients : PME ?",
            "Type de clients : particuliers ?",
            "A-t-il déjà structuré un crédit ?",
            "A-t-il déjà donné un avis ?"
        ],
        "signaux_forts": [
            "Mention de : cash-flow analysis",
            "Mention de : montage de crédit",
            "Mention de : comités de crédit"
        ],
        "points_attention": [
            "CV trop « relation client »",
            "Aucune notion de risque",
            "Expériences très courtes sans progression"
        ]
    },
    "Archiviste (Administration Crédit)": {
        "eliminatoire": [
            "Aucune expérience en gestion documentaire structurée",
            "Absence de rigueur démontrée"
        ],
        "a_verifier": [
            "Expérience avec : archivage physique + électronique",
            "Expérience avec : gestion des dossiers sensibles"
        ],
        "signaux_forts": [
            "Expérience en banque / juridique",
            "Manipulation de garanties ou contrats"
        ],
        "points_attention": [
            "Profils trop généralistes (assistants administratifs sans spécialisation)",
            "CV désorganisé"
        ]
    },
    "Senior Finance Officer": {
        "eliminatoire": [
            "Pas d'expérience en finance senior",
            "Aucune maîtrise des outils de reporting financier"
        ],
        "a_verifier": [
            "Maîtrise des normes IFRS ?",
            "Expérience en pilotage budgétaire ?"
        ],
        "signaux_forts": [
            "Expérience en consolidation financière",
            "Maîtrise Excel avancé / Power BI"
        ],
        "points_attention": [
            "Profil trop opérationnel sans vision stratégique",
            "Manque d'expérience en environnement bancaire"
        ]
    },
    "Market Risk Officer": {
        "eliminatoire": [
            "Pas d'expérience en gestion des risques de marché",
            "Aucune connaissance des produits financiers"
        ],
        "a_verifier": [
            "Maîtrise des modèles VaR / stress testing ?",
            "Expérience en back-testing ?"
        ],
        "signaux_forts": [
            "Mention Basel III / IV",
            "Expérience en salle des marchés",
            "Maîtrise Python / R pour modélisation"
        ],
        "points_attention": [
            "Profil purement académique sans expérience terrain",
            "Expérience uniquement en risque crédit"
        ]
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": [
            "Pas de certification réseau (Cisco, CompTIA…)",
            "Aucune expérience en administration système"
        ],
        "a_verifier": [
            "Expérience environnements bancaires sécurisés ?",
            "Gestion d'incidents en production ?"
        ],
        "signaux_forts": [
            "Certification CCNA / CCNP",
            "Expérience en cybersécurité",
            "Maîtrise virtualisation (VMware, Hyper-V)"
        ],
        "points_attention": [
            "Profil trop orienté développement",
            "Manque d'expérience en environnement haute disponibilité"
        ]
    }
}

# ── MAPPING MOTS-CLÉS EXACTS (pour vérification stricte) ─────────────────────
# Chaque critère a ses variantes EXACTES acceptées
# Seul un match EXACT valide le critère

KEYWORD_MAPPING = {
    # === Responsable Administration de Crédit ===
    "Pas d'expérience bancaire": ["expérience bancaire", "secteur bancaire", "établissement bancaire", "banque commerciale", "métier bancaire"],
    "Moins de 3 ans en crédit / risque": ["3 ans crédit", "trois ans crédit", "3 années crédit", "expérience crédit", "gestion risque crédit", "3 ans risque"],
    "Aucune exposition aux garanties ou conformité": ["garanties", "nantissement", "hypothèque", "sûreté", "conformité", "COBAC", "réglementation bancaire", "BCAC"],
    
    "A-t-il déjà validé des dossiers ?": ["validation dossier", "instruction crédit", "approbation crédit", "dossier crédit", "validation des dossiers"],
    "A-t-il géré des garanties ?": ["gestion garanties", "suivi garanties", "garanties réelles", "sûretés", "portefeuille garanties"],
    "A-t-il participé à des audits ?": ["audit", "contrôle interne", "inspection", "review", "compliance audit", "audit interne"],
    
    "Mention de : IFRS 9": ["IFRS 9", "IAS 39", "normes IFRS", "comptabilité IFRS", "IFRS"],
    "Mention de : COBAC / conformité": ["COBAC", "conformité bancaire", "régulation bancaire", "BCEAO", "BCAC", "commission bancaire"],
    "Mention de : suivi portefeuille / impayés": ["portefeuille crédit", "impayés", "recouvrement", "contentieux", "encours", "suivi portefeuille"],
    
    "Parcours trop « comptable pur »": ["comptable", "comptabilité", "expert comptable"],
    "Rôle uniquement administratif sans responsabilité": ["administratif", "secrétariat", "assistant sans responsabilité"],
    "CV flou (missions génériques)": ["missions génériques", "diverses missions", "tâches variées"],
    
    # === Analyste Crédit CCB ===
    "Pas d'expérience en analyse crédit": ["analyse crédit", "credit analysis", "évaluation crédit", "scoring crédit", "analyse financière crédit"],
    "Profil purement commercial sans analyse": ["commercial", "vente", "business development", "chargé d'affaires commercial"],
    "Incapacité à lire des états financiers": ["états financiers", "bilan", "compte de résultat", "ratios financiers", "analyse financière"],
    
    "Type de clients : PME ?": ["PME", "petites entreprises", "moyennes entreprises", "TPE", "entreprises"],
    "Type de clients : particuliers ?": ["particuliers", "clients particuliers", "retail", "clientèle particulière"],
    "A-t-il déjà structuré un crédit ?": ["structuration crédit", "montage crédit", "dossier de crédit", "structurer un crédit"],
    "A-t-il déjà donné un avis ?": ["avis crédit", "recommandation crédit", "opinion crédit", "credit opinion", "avis de crédit"],
    
    "Mention de : cash-flow analysis": ["cash-flow", "cash flow", "flux de trésorerie", "FCF", "free cash flow", "cash-flow analysis"],
    "Mention de : montage de crédit": ["montage crédit", "structuration", "dossier de crédit", "montage de dossiers"],
    "Mention de : comités de crédit": ["comité crédit", "commission crédit", "credit committee", "validation comité", "comité des engagements"],
    
    "CV trop « relation client »": ["relation client", "accueil client", "service client", "customer service"],
    "Aucune notion de risque": ["risque", "risk", "gestion des risques"],
    "Expériences très courtes sans progression": ["CDD", "contrat court", "expérience courte", "moins d'un an"],
    
    # === Archiviste (Administration Crédit) ===
    "Aucune expérience en gestion documentaire structurée": ["gestion documentaire", "archivage", "GED", "records management", "classement", "documentation"],
    "Absence de rigueur démontrée": ["rigueur", "méthode", "organisation", "procédures", "processus", "traçabilité"],
    
    "Expérience avec : archivage physique + électronique": ["archivage physique", "archivage électronique", "dématérialisation", "numérisation", "archives"],
    "Expérience avec : gestion des dossiers sensibles": ["dossiers sensibles", "confidentiel", "sécurisé", "accès restreint", "données sensibles"],
    
    "Expérience en banque / juridique": ["banque", "établissement financier", "juridique", "droit bancaire", "secteur bancaire"],
    "Manipulation de garanties ou contrats": ["garanties", "contrats", "conventions", "actes juridiques", "documentation juridique"],
    
    "Profils trop généralistes (assistants administratifs sans spécialisation)": ["assistant administratif", "secrétaire", "généraliste", "polyvalent"],
    "CV désorganisé": ["désorganisé", "non structuré", "confus"],
    
    # === Senior Finance Officer ===
    "Pas d'expérience en finance senior": ["finance senior", "finance manager", "contrôle de gestion", "reporting financier", "direction financière"],
    "Aucune maîtrise des outils de reporting financier": ["reporting", "tableaux de bord", "KPI", "indicateurs", "Power BI", "Excel avancé"],
    
    "Maîtrise des normes IFRS ?": ["IFRS", "normes internationales", "comptabilité internationale", "IAS", "IFRS consolidation"],
    "Expérience en pilotage budgétaire ?": ["pilotage budgétaire", "budget", "forecast", "prévisions", "contrôle budgétaire", "budget forecasting"],
    
    "Expérience en consolidation financière": ["consolidation", "comptes consolidés", "groupe", "IFRS consolidation", "consolidation financière"],
    "Maîtrise Excel avancé / Power BI": ["Excel avancé", "Power BI", "VBA", "macros", "pivot", "DAX", "Power Query"],
    
    "Profil trop opérationnel sans vision stratégique": ["opérationnel", "exécution", "tâches", "sans vision"],
    "Manque d'expérience en environnement bancaire": ["banque", "établissement financier", "secteur bancaire"],
    
    # === Market Risk Officer ===
    "Pas d'expérience en gestion des risques de marché": ["risque marché", "market risk", "VaR", "trading", "salle des marchés", "risques de marché"],
    "Aucune connaissance des produits financiers": ["produits financiers", "dérivés", "obligations", "actions", "forex", "instruments financiers"],
    
    "Maîtrise des modèles VaR / stress testing ?": ["VaR", "Value at Risk", "stress testing", "back-testing", "scénarios", "value at risk"],
    "Expérience en back-testing ?": ["back-testing", "backtesting", "validation modèle", "test historique", "back test"],
    
    "Mention Basel III / IV": ["Basel III", "Basel IV", "Bâle 3", "Bâle 4", "accords de Bâle", "réglementation Bâle"],
    "Expérience en salle des marchés": ["salle des marchés", "trading", "front office", "markets", "trading floor"],
    "Maîtrise Python / R pour modélisation": ["Python", "R", "modélisation", "quantitative", "pandas", "numpy", "scikit-learn"],
    
    "Profil purement académique sans expérience terrain": ["académique", "université", "recherche", "thèse", "sans expérience"],
    "Expérience uniquement en risque crédit": ["risque crédit", "credit risk", "sans risque marché"],
    
    # === IT Réseau & Infrastructure ===
    "Pas de certification réseau (Cisco, CompTIA…)": ["CCNA", "CCNP", "CCIE", "Cisco", "CompTIA", "Network+", "Juniper", "certification réseau"],
    "Aucune expérience en administration système": ["administration système", "sysadmin", "Windows Server", "Linux", "Active Directory", "système"],
    
    "Expérience environnements bancaires sécurisés ?": ["banque", "secteur financier", "PCI-DSS", "sécurité bancaire", "environnement sécurisé", "financier"],
    "Gestion d'incidents en production ?": ["incident", "production", "P1", "P2", "support", "ITIL", "résolution", "incident management"],
    
    "Certification CCNA / CCNP": ["CCNA", "CCNP", "Cisco Certified", "CCIE"],
    "Expérience en cybersécurité": ["cybersécurité", "security", "firewall", "IDS", "IPS", "SIEM", "pentest", "sécurité"],
    "Maîtrise virtualisation (VMware, Hyper-V)": ["VMware", "Hyper-V", "virtualisation", "vSphere", "ESXi", "Nutanix", "hyperviseur"],
    
    "Profil trop orienté développement": ["développement", "developer", "code", "programmation", "software"],
    "Manque d'expérience en environnement haute disponibilité": ["haute disponibilité", "HA", "cluster", "failover", "disponibilité", "SLA"]
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
                "email": "recruteur@banque.com",
                "password": hash_pwd("admin123"),
                "nom": "Responsable RH"
            })
            print("✅ Compte recruteur créé dans Redis.")
        else:
            print("✅ Connexion Redis OK.")
    except Exception as e:
        print(f"⚠️ Redis non disponible au démarrage : {e}")

init_recruteur()

# ══════════════════════════════════════════════════════════════════════════════
# 🔧 PARSING DOCUMENTS (PDF, DOCX)
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
        return text
    except Exception as e:
        print(f"⚠️ Erreur lecture PDF: {e}")
        return ""

def extract_text_from_docx(filepath):
    """Extrait le texte d'un fichier DOCX"""
    try:
        doc = Document(filepath)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        print(f"⚠️ Erreur lecture DOCX: {e}")
        return ""

def extract_text_from_file(filepath, filename):
    """Extrait le texte selon l'extension du fichier"""
    if not filepath or not os.path.exists(filepath):
        return ""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return extract_text_from_pdf(filepath)
    elif ext in ['doc', 'docx']:
        return extract_text_from_docx(filepath)
    return ""

def normalize_text(text):
    """Normalise le texte pour la comparaison (minuscules, suppression ponctuation)"""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s\-/]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ══════════════════════════════════════════════════════════════════════════════
# 🧠 MOTEUR D'ANALYSE CV — VÉRIFICATION EXACTE (3 blocs Word)
# ══════════════════════════════════════════════════════════════════════════════

def check_criterion_match(criterion, full_text):
    """
    Vérifie EXACTEMENT si un critère est validé.
    Retourne: (is_validated, matched_keywords)
    """
    mots_cles = KEYWORD_MAPPING.get(criterion, [])
    if not mots_cles:
        return False, []
    
    # 🔍 Recherche EXACTE : au moins UNE variante doit être trouvée TELLE QUELLE
    found_keywords = [kw for kw in mots_cles if kw.lower() in full_text]
    is_present = len(found_keywords) > 0
    
    return is_present, found_keywords


def analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste):
    """
    Analyse STRICTE selon grille Word (3 blocs).
    Un critère n'est validé QUE si ses mots-clés EXACTS sont trouvés.
    Pas de fuzzy matching, pas d'approximation.
    
    🔴 Bloc 1: Éliminatoire → Score = 0 si déclenché
    🟠 Bloc 2: Cohérence → +1 point par critère validé
    🟡 Bloc 3: Signaux → +2 points par signal détecté
    ⚠️ Points d'attention → Alertes uniquement
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
    
    # Texte complet normalisé (minuscules, sans ponctuation excessive)
    # Concaténation CV + Lettre + TOUS les certificats
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
        'matching_details': {}  # 🔍 Debug: quel critère a matché quoi
    }
    
    # ═══════════════════════════════════════════════════════════════
    # 🔴 BLOC 1 : ÉLIMINATOIRE (filtre dur — Score = 0 si déclenché)
    # ═══════════════════════════════════════════════════════════════
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        
        # Les critères éliminatoires sont formulés NÉGATIVEMENT
        is_negative = any(neg in crit.lower() for neg in ['pas d', 'aucun', 'sans ', 'incapacité', 'absence', 'manque de'])
        
        if is_negative:
            if not is_present:
                flags_elim.append(crit)
                checklist[key] = False
                details['alertes_attention'].append(f"🔴 Éliminatoire: {crit}")
                details['matching_details'][crit] = {'found': False, 'keywords_searched': KEYWORD_MAPPING.get(crit, [])[:3]}
            else:
                checklist[key] = True
                details['matching_details'][crit] = {'found': True, 'matched': found_keywords}
        else:
            checklist[key] = is_present
            if not is_present:
                flags_elim.append(crit)
                details['alertes_attention'].append(f"🔴 Éliminatoire: {crit}")
            details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
    
    # ═══════════════════════════════════════════════════════════════
    # 🟠 BLOC 2 : COHÉRENCE DU PARCOURS (+1 point par critère VALIDÉ EXACTEMENT)
    # ═══════════════════════════════════════════════════════════════
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"🟠 {crit}")
    
    # ═══════════════════════════════════════════════════════════════
    # 🟡 BLOC 3 : SIGNAUX DE QUALITÉ (+2 points par signal DÉTECTÉ EXACTEMENT)
    # ═══════════════════════════════════════════════════════════════
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"🟡 {crit}")
    
    # ═══════════════════════════════════════════════════════════════
    # ⚠️ POINTS D'ATTENTION (Alertes uniquement — pas d'impact score)
    # ═══════════════════════════════════════════════════════════════
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        is_present, found_keywords = check_criterion_match(crit, full_text)
        
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'matched': found_keywords if is_present else None}
        
        if is_present:
            details['alertes_attention'].append(f"⚠️ {crit}")
    
    # ═══════════════════════════════════════════════════════════════
    # 🧮 CALCUL DU SCORE FINAL (0-5 étoiles) — STRICT
    # ═══════════════════════════════════════════════════════════════
    if flags_elim:
        score_final = 0
        details['alertes_attention'].insert(0, f"🚫 Score bloqué à 0 : {len(flags_elim)} critère(s) éliminatoire(s)")
    else:
        total_raw = points_bloc2 + points_bloc3
        score_final = min(5, max(0, round(total_raw / 2.5)))
    
    score_breakdown = {
        'bloc1_eliminatoire': len(flags_elim) > 0,
        'flags_eliminatoires_count': len(flags_elim),
        'bloc2_criteres_valides': len(details['criteres_valides_bloc2']),
        'bloc2_points': points_bloc2,
        'bloc3_signaux_detectes': len(signaux),
        'bloc3_points': points_bloc3,
        'total_raw_points': points_bloc2 + points_bloc3,
        'score_final': score_final,
        'note': "Score 0 = éliminatoire déclenché" if flags_elim else f"Score calculé: ({points_bloc2}+{points_bloc3})/2.5 ≈ {score_final}/5"
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
    """
    Fonction exécutée en arrière-plan pour analyser les documents d'un candidat.
    attestation_filenames peut être une liste de fichiers ou une chaîne vide.
    """
    try:
        key = f"candidat:{token}"
        
        # Gestion attestation_filenames (liste ou chaîne)
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
        
        # Extraction TOUS les certificats (concaténation pour analyse globale)
        attestation_texts = []
        if attestation_filenames:
            for att_filename in attestation_filenames:
                att_path = os.path.join(UPLOAD_FOLDER, att_filename)
                if os.path.exists(att_path):
                    att_text = extract_text_from_file(att_path, att_filename)
                    if att_text:
                        attestation_texts.append(att_text)
        attestation_text = " ".join(attestation_texts)  # Concaténation pour analyse
        
        # 🧠 Analyse automatique EXACTE avec TOUS les textes
        result = analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste)
        
        # 💾 Sauvegarde des résultats dans Redis
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
        
        print(f"✅ Analyse auto terminée pour {token}: score={result['score']}/5")
        
    except Exception as e:
        print(f"⚠️ Erreur analyse auto pour candidat {token}: {e}")
        redis_client.hset(f"candidat:{token}", mapping={
            "analyse_status": "error",
            "analyse_error": str(e),
            "analyse_auto_date": datetime.datetime.now().isoformat()
        })


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
    """
    Soumission de candidature avec support MULTIPLE fichiers certificats.
    → Analyse automatique EXACTE lancée en arrière-plan IMMÉDIATEMENT.
    """
    try:
        nom      = (request.form.get('nom') or '').strip()
        prenom   = (request.form.get('prenom') or '').strip()
        email    = (request.form.get('email') or '').strip().lower()
        telephone= (request.form.get('telephone') or '').strip()
        poste    = (request.form.get('poste') or '').strip()

        if not nom or not prenom or not email or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400

        # Vérifier email unique
        for k in redis_client.keys("candidat:*"):
            existing = redis_client.hgetall(k)
            if existing.get('email', '').lower() == email:
                return jsonify({'error': 'Un candidat avec cet email existe déjà'}), 409

        # Sauvegarde CV (obligatoire)
        cv_filename = ''
        if 'cv' in request.files:
            cv = request.files['cv']
            if cv and cv.filename and allowed_file(cv.filename):
                ext = cv.filename.rsplit('.', 1)[1].lower()
                cv_filename = f"{uuid.uuid4().hex}_cv.{ext}"
                cv.save(os.path.join(UPLOAD_FOLDER, cv_filename))

        # Sauvegarde Lettre (optionnel)
        lettre_filename = ''
        if 'lettre' in request.files:
            lettre = request.files['lettre']
            if lettre and lettre.filename and allowed_file(lettre.filename):
                ext = lettre.filename.rsplit('.', 1)[1].lower()
                lettre_filename = f"{uuid.uuid4().hex}_lettre.{ext}"
                lettre.save(os.path.join(UPLOAD_FOLDER, lettre_filename))
        
        # 🎓 Sauvegarde MULTIPLES Certificats (optionnel)
        attestation_filenames = []
        if 'attestation' in request.files:
            # getlist() récupère TOUS les fichiers avec le nom "attestation"
            attestation_files = request.files.getlist('attestation')
            for att in attestation_files:
                if att and att.filename and allowed_file(att.filename):
                    ext = att.filename.rsplit('.', 1)[1].lower()
                    att_filename = f"{uuid.uuid4().hex}_attestation.{ext}"
                    att.save(os.path.join(UPLOAD_FOLDER, att_filename))
                    attestation_filenames.append(att_filename)
        
        # Stockage liste certificats en JSON
        attestation_filenames_json = json.dumps(attestation_filenames, ensure_ascii=False) if attestation_filenames else ""

        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={
            "nom": nom, "prenom": prenom, "email": email, "telephone": telephone,
            "poste": poste, 
            "cv_filename": cv_filename, 
            "lettre_filename": lettre_filename,
            "attestation_filenames": attestation_filenames_json,  # ← Liste JSON
            "statut": "en_attente", "note": "", "score": "0", 
            "checklist": "", "flags_eliminatoires": "", "signaux_detectes": "",
            "score_breakdown": "", "analyse_status": "pending",
            "date_candidature": datetime.datetime.now().isoformat()
        })

        # 🚀 LANCEMENT ANALYSE AUTO EN ARRIÈRE-PLAN avec TOUS les fichiers
        threading.Thread(
            target=run_analysis_for_candidat,
            args=(token, cv_filename, lettre_filename, attestation_filenames, poste),
            daemon=True
        ).start()

        nb_certs = len(attestation_filenames)
        return jsonify({
            'message': 'Candidature soumise avec succès',
            'token': token,
            'analyse': f'L\'analyse automatique de votre dossier (CV + lettre + {nb_certs} certificat{"s" if nb_certs!=1 else ""}) est en cours'
        }), 201

    except Exception as e:
        print(f"❌ Erreur postuler: {e}")
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
    
    # Parse attestation_filenames (liste JSON)
    if data.get('attestation_filenames'):
        try: 
            data['attestation_filenames_parsed'] = json.loads(data['attestation_filenames'])
        except: 
            data['attestation_filenames_parsed'] = []
    
    # Parse tous les autres champs JSON
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
    score = str(min(5, max(0, int(data.get('score', 0)))))
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
        corps = f"""Madame, Monsieur {nom_complet},

Nous avons le plaisir de vous informer que votre candidature pour le poste de {poste} a été retenue à l'issue de notre processus de présélection.

Nous vous contacterons très prochainement pour vous communiquer les modalités de la prochaine étape du processus de recrutement.

Dans l'attente, nous restons disponibles pour toute question.

Cordialement,
L'équipe Ressources Humaines
RecrutBank"""
    elif msg_type == 'entretien':
        sujet = f"Invitation à un entretien – Poste {poste}"
        corps = f"""Madame, Monsieur {nom_complet},

Suite à l'examen attentif de votre candidature pour le poste de {poste}, nous avons le plaisir de vous inviter à un entretien avec notre équipe.

Nous prendrons contact avec vous dans les meilleurs délais pour convenir d'une date et d'un horaire qui vous conviennent.

Cordialement,
L'équipe Ressources Humaines
RecrutBank"""
    else:
        sujet = f"Réponse à votre candidature – Poste {poste}"
        corps = f"""Madame, Monsieur {nom_complet},

Nous vous remercions sincèrement de l'intérêt que vous portez à notre institution et du temps consacré à votre candidature pour le poste de {poste}.

Après examen attentif de votre dossier et compte tenu du nombre important de candidatures reçues, nous avons le regret de vous informer que votre candidature n'a pas été retenue pour la suite du processus de sélection.

Nous vous encourageons vivement à postuler à nouveau pour toute opportunité future qui correspondrait à votre profil et vous souhaitons plein succès dans votre recherche d'emploi.

Cordialement,
L'équipe Ressources Humaines
RecrutBank"""
    
    return jsonify({'to': to_email, 'nom': nom_complet, 'sujet': sujet, 'corps': corps}), 200

# ══════════════════════════════════════════════════════════════════════════════
# 🔓 SERVIR LES FICHIERS UPLOADÉS (sans JWT pour affichage navigateur)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/recruteur/uploads/<filename>', methods=['GET'])
# 🔓 Pas de @jwt_required() → permet l'affichage direct dans le navigateur
# 🔐 Sécurité assurée par noms UUID uniques (impossibles à deviner)
def serve_upload(filename):
    """Servir les fichiers uploadés — sécurisé par noms UUID uniques"""
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    
    filepath = os.path.join(UPLOAD_FOLDER, safe)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Fichier introuvable'}), 404
    
    mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    return send_from_directory(UPLOAD_FOLDER, safe, mimetype=mime_type, as_attachment=False)

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT TEST (à désactiver en production)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/test/analyze', methods=['POST'])
def test_analyze():
    data = request.json or {}
    poste = data.get('poste', 'Analyste Crédit CCB')
    cv_text = data.get('cv_text', '')
    lettre_text = data.get('lettre_text', '')
    attestation_text = data.get('attestation_text', '')
    result = analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste)
    return jsonify(result), 200

@app.route('/api/test/matching', methods=['POST'])
def test_matching():
    data = request.json or {}
    texte = data.get('texte', '').lower()
    critere = data.get('critere', '')
    mots_cles = KEYWORD_MAPPING.get(critere, [])
    found = [kw for kw in mots_cles if kw.lower() in texte]
    return jsonify({
        'critere': critere,
        'keywords_searched': mots_cles,
        'found': found,
        'is_validated': len(found) > 0
    }), 200

# ══════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Serveur RecrutBank démarré sur le port {port}")
    print(f"📋 Grille de présélection chargée: {len(GRILLE)} postes")
    print(f"🔍 Analyse auto: 3 blocs (éliminatoire / cohérence / signaux) — VÉRIFICATION EXACTE")
    print(f"📁 Upload multiple certificats supporté via attestation_filenames[]")
    print(f"🔓 Fichiers accessibles via /api/recruteur/uploads/<filename>")
    app.run(host="0.0.0.0", port=port, debug=False)

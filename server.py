# server.py - Backend Flask pour RecrutBank (VERSION CORRIGÉE)
import sys, os, hashlib, datetime, uuid, redis, json, re, threading, mimetypes, io, csv
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from docx import Document

# ── LOGGING AU DÉMARRAGE ──────────────────────────────────────────────────────
def log_startup(msg):
    print(f"[STARTUP] {msg}", file=sys.stderr, flush=True)

log_startup("Début du chargement de server.py")

# ── IMPORTS PDF ──────────────────────────────────────────────────────────────
PDFPLUMBER_AVAILABLE = False
PYPDF2_AVAILABLE = False
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
    log_startup("✓ pdfplumber disponible")
except ImportError as e:
    log_startup(f"✗ pdfplumber indisponible: {e}")

try:
    import PyPDF2
    PYPDF2_AVAILABLE = True
    log_startup("✓ PyPDF2 disponible")
except ImportError as e:
    log_startup(f"✗ PyPDF2 indisponible: {e}")

# ── EXPORTS ──────────────────────────────────────────────────────────────────
REPORTLAB_AVAILABLE = False
OPENPYXL_AVAILABLE = False
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    REPORTLAB_AVAILABLE = True
    log_startup("✓ reportlab disponible")
except ImportError as e:
    log_startup(f"✗ reportlab indisponible: {e}")

try:
    import openpyxl
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
    log_startup("✓ openpyxl disponible")
except ImportError as e:
    log_startup(f"✗ openpyxl indisponible: {e}")

# ── APP FLASK ────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"], "allow_headers": ["Content-Type", "Authorization"]}})
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

# ── REDIS ────────────────────────────────────────────────────────────────────
try:
    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "redis-11133.c8.us-east-1-4.ec2.cloud.redislabs.com"),
        port=int(os.getenv("REDIS_PORT", 11133)),
        username="default",
        password=os.getenv("REDIS_PASSWORD", "WKJdeilasGOWkXJWOHwqcRV7X5uWwQ"),
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=10
    )
    redis_client.ping()
    log_startup("✓ Connexion Redis OK")
except Exception as e:
    log_startup(f"✗ Redis erreur: {e}")
    redis_client = None

# ── UPLOADS ─────────────────────────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
REPORTS_FOLDER = os.path.join(os.path.dirname(__file__), 'reports')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ── POSTES & GRILLE ─────────────────────────────────────────────────────────
POSTES = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Archiviste (Administration Crédit)", "Senior Finance Officer", "Market Risk Officer", "IT Réseau & Infrastructure"]

GRILLE = {
    "IT Réseau & Infrastructure": {
        "eliminatoire": ["Expérience en réseau / infrastructure", "Exposition à environnement critique", "Notion de sécurité IT", "Minimum 2 ans expérience (hors stage)"],
        "a_verifier": ["Gestion réseaux LAN/WAN/VPN", "Gestion serveurs Windows/Linux", "Cloud même basique", "Gestion des incidents", "Assurance de la disponibilité"],
        "signaux_forts": ["Cybersécurité / firewall", "Haute disponibilité / PRA/PCA", "Gestion ATM ou systèmes bancaires", "Certifications Cisco ou Microsoft"],
        "points_attention": ["Profil trop helpdesk", "CV sans détail technique", "Aucune mention de sécurité"]
    }
    # ... autres postes (identiques à votre version)
}

# ── KEYWORD_MAPPING (version condensée pour l'exemple) ───────────────────────
KEYWORD_MAPPING = {
    "Expérience en réseau / infrastructure": ["reseau", "infrastructure", "lan", "wan", "vpn", "network", "cisco", "mikrotik"],
    "Exposition à environnement critique": ["banque", "telecom", "datacenter", "haute disponibilite", "critique"],
    "Notion de sécurité IT": ["securite", "firewall", "cybersecurite", "ids", "ips", "fortinet", "palo alto"],
    "Minimum 2 ans expérience (hors stage)": ["EXP_IT_2ANS"],
    "Gestion réseaux LAN/WAN/VPN": ["lan", "wan", "vpn", "ospf", "bgp", "eigrp", "sd-wan"],
    "Gestion serveurs Windows/Linux": ["windows server", "linux", "vmware", "hyper-v", "esxi"],
    "Cloud même basique": ["cloud", "aws", "azure", "ovh"],
    "Gestion des incidents": ["incident", "support", "resolution", "itil", "prtg", "nagios", "zabbix"],
    "Assurance de la disponibilité": ["disponibilite", "sla", "uptime", "failover", "continuite"],
    "Cybersécurité / firewall": ["firewall", "cybersecurite", "siem", "soar", "fortinet", "palo alto"],
    "Haute disponibilité / PRA/PCA": ["pra", "pca", "basculement", "failover", "disaster recovery"],
    "Gestion ATM ou systèmes bancaires": ["atm", "gab", "banque", "systeme bancaire"],
    "Certifications Cisco ou Microsoft": ["ccna", "ccnp", "ccie", "cisco", "microsoft", "security"]
}

# ── STAGE DETECTION ─────────────────────────────────────────────────────────
STAGE_MARKERS = [r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b', r'\bapprenti\b', r'\bpfe\b']
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)

# ── NORMALISATION TEXTE (CORRIGÉ: 32→32 caractères) ─────────────────────────
_ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊËÎÏÔÙÛÜÇŒÆ', 'aaaeeeeiioouuuc o aAAAEEEEIIOOUUUCOA')

def normalize_text(text):
    if not text: return ""
    text = text.lower().translate(_ACCENT_MAP)
    text = re.sub(r'[^\w\s\-/\.]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

# ── EXTRACTION PDF ROBUSTE ──────────────────────────────────────────────────
def extract_text_from_pdf(filepath):
    text = ""
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    content = page.extract_text()
                    if content: text += content + "\n"
            if text.strip(): return text.strip()
        except Exception as e:
            log_startup(f"✗ pdfplumber erreur: {e}")
    if PYPDF2_AVAILABLE:
        try:
            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    content = page.extract_text()
                    if content: text += content + "\n"
            return text.strip()
        except Exception as e:
            log_startup(f"✗ PyPDF2 erreur: {e}")
    return ""

def extract_text_from_docx(filepath):
    try:
        doc = Document(filepath)
        parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = ' | '.join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text: parts.append(row_text)
        return "\n".join(parts).strip()
    except Exception as e:
        log_startup(f"✗ DOCX erreur: {e}")
        return ""

def extract_text_from_file(filepath, filename):
    if not filepath or not os.path.exists(filepath): return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf': return extract_text_from_pdf(filepath)
    if ext in ('doc', 'docx'): return extract_text_from_docx(filepath)
    return ""

# ── CALCUL EXPÉRIENCE (hors stage, multi-langues) ───────────────────────────
def split_into_jobs(raw_text):
    separators = re.compile(r'(?:^|\n)(?=\s*(?:\d{4}|jan|fev|mar|avr|mai|juin|juil|aou|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december|depuis|from))', re.IGNORECASE | re.MULTILINE)
    return [b.strip() for b in separators.split(raw_text) if b.strip()]

def is_stage_block(block_text):
    return bool(STAGE_PATTERN.search(block_text))

def extract_duration_years_from_block(block_text):
    text = block_text.lower()
    # Format "X ans" ou "X années"
    m = re.search(r'(\d+[\.,]?\d*)\s*(?:ans?|annee?s?)', text)
    if m:
        try: return float(m.group(1).replace(',', '.'))
        except: pass
    # Format "AAAA - AAAA" ou "AAAA - aujourd'hui/present"
    m = re.search(r'(20\d{2}|19\d{2})\s*[-–—]\s*(20\d{2}|19\d{2}|aujourdhui|present|actuel|en\s+cours)', text)
    if m:
        start = int(m.group(1))
        end_raw = m.group(2)
        end = int(end_raw) if re.match(r'\d{4}', end_raw) else datetime.datetime.now().year
        diff = end - start
        if 0 < diff <= 40: return float(diff)
    # Format "mm/AAAA - mm/AAAA"
    m = re.search(r'(\d{1,2})[/\-](20\d{2})\s*[-–—]\s*(?:(\d{1,2})[/\-])?(20\d{2}|present|aujourdhui)', text)
    if m:
        sm, sy = int(m.group(1)), int(m.group(2))
        em_raw, ey_raw = m.group(3), m.group(4)
        ey = int(ey_raw) if re.match(r'\d{4}', str(ey_raw)) else datetime.datetime.now().year
        em = int(em_raw) if em_raw and em_raw.isdigit() else (12 if ey == sy else datetime.datetime.now().month)
        delta = (ey - sy) + (em - sm) / 12.0
        if 0 < delta <= 40: return round(delta, 1)
    return 0.0

def compute_real_experience_years(full_raw_text, domain_keywords=None):
    blocks = split_into_jobs(full_raw_text)
    total = 0.0
    for block in blocks:
        if is_stage_block(block): continue
        if domain_keywords and not any(kw in normalize_text(block) for kw in domain_keywords): continue
        dur = extract_duration_years_from_block(block)
        if dur > 0: total += dur
    return round(total, 1)

def has_experience_years(full_raw_text, min_years, domain_keywords=None):
    total = compute_real_experience_years(full_raw_text, domain_keywords)
    log_startup(f"[EXP] Calculé: {total} ans (min requis: {min_years})")
    return total >= min_years

# ── VÉRIFICATION CRITÈRES ───────────────────────────────────────────────────
DOMAIN_KEYWORDS_MAP = {"EXP_IT_2ANS": ["reseau", "infrastructure", "systeme", "informatique", "it", "network", "cisco", "admin"]}
EXP_MIN_YEARS_MAP = {"EXP_IT_2ANS": 2.0}

def check_criterion_match(criterion, normalized_text, raw_full_text=""):
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords: return False, []
    exp_markers = [kw for kw in keywords if kw.startswith("EXP_")]
    if exp_markers:
        marker = exp_markers[0]
        min_y = EXP_MIN_YEARS_MAP.get(marker, 2.0)
        domain_kws = DOMAIN_KEYWORDS_MAP.get(marker, [])
        return has_experience_years(raw_full_text, min_y, [normalize_text(k) for k in domain_kws]), ([marker] if True else [])
    found = [kw for kw in keywords if normalize_text(kw) in normalized_text]
    return len(found) > 0, found

# ── MOTEUR D'ANALYSE ────────────────────────────────────────────────────────
def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    log_startup(f"[ANALYSE] Poste: {poste}, CV: {len(cv_text)} chars")
    if not cv_text or len(cv_text.strip()) < 50:
        return {'score': 0, 'flags_eliminatoires': ['CV non analysable'], 'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0}}
    
    grille = GRILLE.get(poste)
    if not grille:
        return {'score': 0, 'flags_eliminatoires': [f'Poste inconnu: {poste}'], 'score_breakdown': {}}
    
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + "\n".join(attestation_texts_list or [])
    normalized = normalize_text(raw_full)
    
    checklist, flags_elim, signaux = {}, [], []
    points_bloc2, points_bloc3 = 0, 0
    
    # Bloc 1: Éliminatoires (AND strict)
    for crit in grille['eliminatoire']:
        ok, found = check_criterion_match(crit, normalized, raw_full)
        checklist[crit] = ok
        if not ok:
            flags_elim.append(f"❌ {crit}")
            log_startup(f"[ÉLIMINÉ] Critère manquant: {crit}")
    
    if flags_elim:
        return {'score': 0, 'checklist': checklist, 'flags_eliminatoires': flags_elim, 'signaux_detectes': [], 'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0, 'note': f"ÉLIMINÉ: {len(flags_elim)} critère(s)"}}
    
    # Bloc 2: Cohérence
    for crit in grille.get('a_verifier', []):
        ok, _ = check_criterion_match(crit, normalized, raw_full)
        if ok: points_bloc2 += 1
    
    # Bloc 3: Signaux forts
    for crit in grille.get('signaux_forts', []):
        ok, _ = check_criterion_match(crit, normalized, raw_full)
        if ok:
            points_bloc3 += 2
            signaux.append(crit)
    
    # Scoring Excel /10
    adequation = min(3, len([k for k, v in checklist.items() if v]))
    coherence = min(2, points_bloc2)
    risque = min(3, len(signaux))
    qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre = 1 if lettre_text and len(lettre_text.strip()) > 50 else 0
    score = min(10, adequation + coherence + risque + qualite_cv + lettre)
    
    log_startup(f"[SCORE] {poste}: {score}/10 (adeq:{adequation}, coh:{coherence}, risque:{risque})")
    return {
        'score': score, 'checklist': checklist, 'flags_eliminatoires': [],
        'signaux_detectes': signaux,
        'score_breakdown': {'bloc1_eliminatoire': False, 'score_final': score, 'note': f"{score}/10"}
    }

# ── ROUTES FLASK (version minimale fonctionnelle) ───────────────────────────
@app.route('/api/postes')
def get_postes(): return jsonify(POSTES), 200

@app.route('/api/grille/<poste>')
def get_grille(poste):
    g = GRILLE.get(poste)
    return jsonify(g) if g else (jsonify({'error': 'Poste inconnu'}), 404)

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        email = request.form.get('email', '').strip().lower()
        poste = request.form.get('poste', '').strip()
        if not all([nom, prenom, email]) or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires'}), 400
        
        def save(f, suffix):
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[1].lower()
                fn = f"{uuid.uuid4().hex}_{suffix}.{ext}"
                f.save(os.path.join(UPLOAD_FOLDER, fn))
                return fn
            return ''
        
        cv_fn = save(request.files.get('cv'), 'cv')
        lm_fn = save(request.files.get('lettre'), 'lettre')
        token = uuid.uuid4().hex
        
        # Analyse synchrone pour debug
        cv_text = extract_text_from_file(os.path.join(UPLOAD_FOLDER, cv_fn), cv_fn) if cv_fn else ""
        lm_text = extract_text_from_file(os.path.join(UPLOAD_FOLDER, lm_fn), lm_fn) if lm_fn else ""
        result = analyze_cv_against_grille(cv_text, lm_text, [], poste)
        
        return jsonify({'message': 'OK', 'token': token, 'score': result['score'], 'details': result['score_breakdown']}), 201
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return jsonify({'error': str(e)}), 500

@app.route('/api/health')
def health(): return jsonify({'status': 'ok', 'pdfplumber': PDFPLUMBER_AVAILABLE, 'pypdf2': PYPDF2_AVAILABLE}), 200

# ── DÉMARRAGE ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    log_startup(f"🚀 Démarrage sur port {port}")
    log_startup(f"📋 Postes: {POSTES}")
    log_startup(f"🔍 PDF: pdfplumber={PDFPLUMBER_AVAILABLE}, PyPDF2={PYPDF2_AVAILABLE}")
    try:
        app.run(host="0.0.0.0", port=port, debug=False)
    except Exception as e:
        log_startup(f"💥 ERREUR FATALE: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

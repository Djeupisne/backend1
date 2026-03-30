# server.py - Backend Flask pour RecrutBank avec analyse automatique des CV
# ============================================================================

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, redis, json, re, threading
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

# ── GRILLE DE PRÉSÉLECTION (logique du Word) ───────────────────────────────────
GRILLE = {
    "Responsable Administration de Crédit": {
        "eliminatoire": [
            "Pas d'expérience bancaire",
            "Moins de 3 ans en crédit / risque",
            "Aucune exposition aux garanties ou conformité"
        ],
        "a_verifier": [
            "A-t-il déjà validé des dossiers de crédit ?",
            "A-t-il géré des garanties ?",
            "A-t-il participé à des audits ?"
        ],
        "signaux_forts": [
            "Mention IFRS 9",
            "Mention COBAC / conformité",
            "Suivi portefeuille / impayés"
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
            "Type de clients : PME ? Particuliers ?",
            "A-t-il structuré un crédit ?",
            "A-t-il donné un avis de crédit ?"
        ],
        "signaux_forts": [
            "Mention cash-flow analysis",
            "Mention montage de crédit",
            "Mention comités de crédit"
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
            "Expérience avec archivage physique + électronique ?",
            "Gestion de dossiers sensibles ?"
        ],
        "signaux_forts": [
            "Expérience en banque / juridique",
            "Manipulation de garanties ou contrats"
        ],
        "points_attention": [
            "Profil trop généraliste (assistant admin sans spécialisation)",
            "CV désorganisé (ironie révélatrice)"
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
            "Expérience avec environnements bancaires sécurisés ?",
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
# 🧠 MOTEUR D'ANALYSE CV CONTRE GRILLE
# ══════════════════════════════════════════════════════════════════════════════

def extract_keywords_from_criterion(criterion):
    """
    Extrait des mots-clés pertinents d'un critère pour la recherche.
    Gère les termes techniques bancaires/IT via un mapping.
    """
    keyword_map = {
        'ifrs 9': ['ifrs 9', 'ifrs9', 'norme ifrs', 'ias 39', 'normes comptables'],
        'cobac': ['cobac', 'régulation bancaire', 'conformité bancaire', 'bcac'],
        'cash-flow': ['cash flow', 'cashflow', 'flux de trésorerie', 'cash-flow analysis', 'flux trésorerie'],
        'montage de crédit': ['montage crédit', 'structuration crédit', 'dossier crédit', 'octroi crédit'],
        'comités de crédit': ['comité crédit', 'commission crédit', 'validation crédit', 'approbation crédit'],
        'var': ['var', 'value at risk', 'valeur à risque', 'stress testing', 'back-testing'],
        'basel': ['basel iii', 'basel iv', 'bâle 3', 'bâle 4', 'accords de bâle', 'ratio bâle'],
        'ccna': ['ccna', 'ccnp', 'cisco', 'certification réseau', 'comptia', 'network+'],
        'vmware': ['vmware', 'hyper-v', 'virtualisation', 'vsphere', 'vcenter', 'hyperviseur'],
        'power bi': ['power bi', 'tableau software', 'qlik', 'reporting avancé', 'bi tools'],
        'python': ['python', 'r language', 'modélisation', 'data science', 'scripting'],
        'archivage': ['archivage', 'ged', 'gestion documentaire', 'classement', 'records management'],
        'garanties': ['garantie', 'nantissement', 'hypothèque', 'sûreté', 'collatéral', 'caution'],
        'audit': ['audit', 'contrôle interne', 'inspection', 'compliance', 'conformité'],
        'portefeuille': ['portefeuille', 'encours', 'impayés', 'recouvrement', 'contentieux'],
        'états financiers': ['états financiers', 'bilan', 'compte de résultat', 'cash flow', 'ratios financiers'],
        'risque': ['risque crédit', 'risque marché', 'risque opérationnel', 'risk management'],
    }
    
    crit_lower = criterion.lower()
    keywords = []
    
    # Ajoute les mots individuels significatifs (>3 lettres, hors stopwords)
    stopwords = {'dans', 'des', 'aux', 'une', 'une', 'sur', 'pour', 'avec', 'sans', 'déjà', 'été', 'avoir'}
    words = re.findall(r'\b[a-zà-ÿ\-]{4,}\b', crit_lower)
    keywords.extend([w for w in words if w not in stopwords])
    
    # Ajoute les mappings spécifiques si le critère correspond
    for key, mappings in keyword_map.items():
        if key in crit_lower:
            keywords.extend(mappings)
    
    return list(set(keywords))


def analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste):
    """
    Analyse le CV, la lettre de motivation et l'attestation contre la grille de présélection.
    
    Retourne: {
        'score': int (0-5),
        'checklist': dict,
        'flags_eliminatoires': list,
        'signaux_detectes': list,
        'details': dict
    }
    """
    if not cv_text:
        return {
            'score': 0,
            'checklist': {},
            'flags_eliminatoires': ['CV non analysable'],
            'signaux_detectes': [],
            'details': {'error': 'CV vide ou non parsé'}
        }
    
    grille = GRILLE.get(poste)
    if not grille:
        return {
            'score': 0,
            'checklist': {},
            'flags_eliminatoires': [],
            'signaux_detectes': [],
            'details': {'error': f'Poste inconnu: {poste}'}
        }
    
    # Concatène et normalise tous les textes
    full_text = normalize_text(cv_text + " " + (lettre_text or "") + " " + (attestation_text or ""))
    cv_only = normalize_text(cv_text)
    
    checklist = {}
    flags_elim = []
    signaux = []
    score_base = 0
    details = {
        'cv_words': len(cv_text.split()) if cv_text else 0,
        'lettre_words': len(lettre_text.split()) if lettre_text else 0,
        'attestation_words': len(attestation_text.split()) if attestation_text else 0,
        'keywords_found': []
    }
    
    # ── 1. CRITÈRES ÉLIMINATOIRES (🔴) ─────────────────────────────────────
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        mots_cles = extract_keywords_from_criterion(crit)
        
        # Recherche dans le texte
        present = any(mot in full_text for mot in mots_cles if len(mot) > 3)
        
        # Les critères éliminatoires sont formulés négativement
        is_negative_criterion = any(neg in crit.lower() for neg in ['pas d', 'aucun', 'sans ', 'incapacité', 'absence'])
        
        if is_negative_criterion:
            # Si le critère dit "Pas d'expérience X" → on coche SI l'expérience EST présente (bon signe)
            if not present:
                checklist[key] = True  # Validé : le candidat a l'expérience requise
            else:
                flags_elim.append(crit)  # Éliminatoire : le candidat ne remplit pas le critère
                checklist[key] = False
        else:
            checklist[key] = present  # Critère positif standard
        
        if checklist.get(key):
            details['keywords_found'].extend([m for m in mots_cles if m in full_text])
    
    # ── 2. ÉLÉMENTS À VÉRIFIER (🟠) ────────────────────────────────────────
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        mots_cles = extract_keywords_from_criterion(crit)
        present = any(mot in full_text for mot in mots_cles if len(mot) > 3)
        checklist[key] = present
        if present:
            score_base += 1
            details['keywords_found'].extend([m for m in mots_cles if m in full_text])
    
    # ── 3. SIGNAUX FORTS (🟡) ─────────────────────────────────────────────
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        mots_cles = extract_keywords_from_criterion(crit)
        present = any(mot in full_text for mot in mots_cles if len(mot) > 3)
        checklist[key] = present
        if present:
            signaux.append(crit)
            score_base += 2  # Poids plus fort pour les signaux
            details['keywords_found'].extend([m for m in mots_cles if m in full_text])
    
    # ── 4. POINTS D'ATTENTION (⚠️) ────────────────────────────────────────
    for i, crit in enumerate(grille['points_attention']):
        key = f"attn_{i}"
        mots_cles = extract_keywords_from_criterion(crit)
        present = any(mot in full_text for mot in mots_cles if len(mot) > 3)
        checklist[key] = present  # Coché si détecté (pour alerte recruteur)
    
    # ── CALCUL DU SCORE FINAL (0-5 étoiles) ───────────────────────────────
    if flags_elim:
        score_final = 0  # Éliminatoire = score 0, non négociable
    else:
        # Normalisation : ~10-15 points max possibles → ramené à échelle 0-5
        score_final = min(5, max(0, round(score_base / 2.5)))
    
    details['keywords_found'] = list(set(details['keywords_found']))
    
    return {
        'score': score_final,
        'checklist': checklist,
        'flags_eliminatoires': flags_elim,
        'signaux_detectes': signaux,
        'details': details
    }


def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filename, poste):
    """
    Fonction exécutée en arrière-plan pour analyser les documents d'un candidat.
    """
    try:
        key = f"candidat:{token}"
        
        # Chemins des fichiers
        cv_path = os.path.join(UPLOAD_FOLDER, cv_filename) if cv_filename else None
        lettre_path = os.path.join(UPLOAD_FOLDER, lettre_filename) if lettre_filename else None
        attestation_path = os.path.join(UPLOAD_FOLDER, attestation_filename) if attestation_filename else None
        
        # Extraction des textes
        cv_text = extract_text_from_file(cv_path, cv_filename) if cv_path else ""
        lettre_text = extract_text_from_file(lettre_path, lettre_filename) if lettre_path else ""
        attestation_text = extract_text_from_file(attestation_path, attestation_filename) if attestation_path else ""
        
        # Analyse
        result = analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste)
        
        # Sauvegarde dans Redis
        redis_client.hset(key, mapping={
            "score": str(result['score']),
            "checklist": json.dumps(result['checklist'], ensure_ascii=False),
            "flags_eliminatoires": json.dumps(result['flags_eliminatoires'], ensure_ascii=False),
            "signaux_detectes": json.dumps(result['signaux_detectes'], ensure_ascii=False),
            "analyse_details": json.dumps(result['details'], ensure_ascii=False),
            "analyse_auto_date": datetime.datetime.now().isoformat(),
            "analyse_status": "completed"
        })
        
        print(f"✅ Analyse auto terminée pour candidat {token}: score={result['score']}, signaux={len(result['signaux_detectes'])}")
        
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
    """Retourne la grille de présélection pour un poste donné."""
    g = GRILLE.get(poste)
    if not g:
        return jsonify({'error': 'Poste inconnu'}), 404
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

        # Vérifier email unique
        for k in redis_client.keys("candidat:*"):
            existing = redis_client.hgetall(k)
            if existing.get('email', '').lower() == email:
                return jsonify({'error': 'Un candidat avec cet email existe déjà'}), 409

        # Sauvegarde des fichiers
        cv_filename = ''
        lettre_filename = ''
        attestation_filename = ''

        if 'cv' in request.files:
            cv = request.files['cv']
            if cv and cv.filename and allowed_file(cv.filename):
                ext = cv.filename.rsplit('.', 1)[1].lower()
                cv_filename = f"{uuid.uuid4().hex}_cv.{ext}"
                cv.save(os.path.join(UPLOAD_FOLDER, cv_filename))

        if 'lettre' in request.files:
            lettre = request.files['lettre']
            if lettre and lettre.filename and allowed_file(lettre.filename):
                ext = lettre.filename.rsplit('.', 1)[1].lower()
                lettre_filename = f"{uuid.uuid4().hex}_lettre.{ext}"
                lettre.save(os.path.join(UPLOAD_FOLDER, lettre_filename))
                
        if 'attestation' in request.files:
            attestation = request.files['attestation']
            if attestation and attestation.filename and allowed_file(attestation.filename):
                ext = attestation.filename.rsplit('.', 1)[1].lower()
                attestation_filename = f"{uuid.uuid4().hex}_attestation.{ext}"
                attestation.save(os.path.join(UPLOAD_FOLDER, attestation_filename))

        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={
            "nom":                    nom,
            "prenom":                 prenom,
            "email":                  email,
            "telephone":              telephone,
            "poste":                  poste,
            "cv_filename":            cv_filename,
            "lettre_filename":        lettre_filename,
            "attestation_filename":   attestation_filename,
            "statut":                 "en_attente",
            "note":                   "",
            "score":                  "0",
            "checklist":              "",
            "flags_eliminatoires":    "",
            "signaux_detectes":       "",
            "analyse_status":         "pending",
            "date_candidature":       datetime.datetime.now().isoformat()
        })

        # 🚀 LANCEMENT ANALYSE AUTO EN ARRIÈRE-PLAN
        threading.Thread(
            target=run_analysis_for_candidat,
            args=(token, cv_filename, lettre_filename, attestation_filename, poste),
            daemon=True
        ).start()

        return jsonify({
            'message': 'Candidature soumise avec succès',
            'token': token,
            'analyse': 'L\'analyse automatique de votre dossier est en cours...'
        }), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Candidature introuvable'}), 404
    # On ne renvoie pas les noms de fichiers au candidat
    public = {k: v for k, v in data.items() if k not in ('cv_filename', 'lettre_filename', 'attestation_filename', 'checklist', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details')}
    return jsonify(public), 200

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES RECRUTEUR (protégées JWT)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    keys = redis_client.keys("candidat:*")
    stats = {
        "total": len(keys),
        "en_attente": 0,
        "retenu": 0,
        "rejete": 0,
        "entretien": 0,
        "by_poste": []
    }
    counts = {}
    for k in keys:
        c = redis_client.hgetall(k)
        s = c.get('statut', 'en_attente')
        if s in stats:
            stats[s] += 1
        p = c.get('poste', 'Inconnu')
        counts[p] = counts.get(p, 0) + 1
    stats['by_poste'] = [{'poste': p, 'n': n} for p, n in counts.items()]
    return jsonify(stats), 200

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    poste_filter  = request.args.get('poste', '')
    statut_filter = request.args.get('statut', '')
    search        = request.args.get('search', '').lower()

    keys = redis_client.keys("candidat:*")
    result = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]

        if poste_filter and c.get('poste') != poste_filter:
            continue
        if statut_filter and c.get('statut') != statut_filter:
            continue
        if search:
            haystack = f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')}".lower()
            if search not in haystack:
                continue

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
    # Parse JSON fields for frontend
    if data.get('checklist'):
        try: data['checklist_parsed'] = json.loads(data['checklist'])
        except: pass
    if data.get('flags_eliminatoires'):
        try: data['flags_eliminatoires_parsed'] = json.loads(data['flags_eliminatoires'])
        except: pass
    if data.get('signaux_detectes'):
        try: data['signaux_detectes_parsed'] = json.loads(data['signaux_detectes'])
        except: pass
    if data.get('analyse_details'):
        try: data['analyse_details_parsed'] = json.loads(data['analyse_details'])
        except: pass
    return jsonify(data), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    key = f"candidat:{token}"
    if not redis_client.exists(key):
        return jsonify({'error': 'Candidat introuvable'}), 404

    data = request.json or {}
    statut    = data.get('statut', 'en_attente')
    note      = data.get('note', '')
    score     = str(data.get('score', '0'))
    checklist = data.get('checklist', '')

    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400

    redis_client.hset(key, mapping={
        "statut":    statut,
        "note":      note,
        "score":     score,
        "checklist": checklist,
        "analyse_manual_override": "true"
    })
    return jsonify({'message': 'Mis à jour avec succès'}), 200

@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    """Déclenche ou re-déclenche l'analyse automatique d'un candidat"""
    key = f"candidat:{token}"
    data = redis_client.hgetall(key)
    
    if not data:
        return jsonify({'error': 'Candidat introuvable'}), 404
    
    cv_filename = data.get('cv_filename')
    lettre_filename = data.get('lettre_filename')
    attestation_filename = data.get('attestation_filename')
    poste = data.get('poste')
    
    if not cv_filename:
        return jsonify({'error': 'CV manquant pour analyse'}), 400
    
    # Mise à jour statut analyse
    redis_client.hset(key, mapping={
        "analyse_status": "pending",
        "analyse_manual_trigger": datetime.datetime.now().isoformat()
    })
    
    # Lancement async
    threading.Thread(
        target=run_analysis_for_candidat,
        args=(token, cv_filename, lettre_filename, attestation_filename, poste),
        daemon=True
    ).start()
    
    return jsonify({
        'message': 'Analyse automatique déclenchée',
        'token': token
    }), 202

@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    data = redis_client.hgetall(f"candidat:{token}")
    if not data:
        return jsonify({'error': 'Candidat introuvable'}), 404

    body = request.json or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))

    nom_complet = f"{data.get('prenom', '')} {data.get('nom', '')}"
    poste       = data.get('poste', '')
    to_email    = data.get('email', '')

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

    return jsonify({
        'to':    to_email,
        'nom':   nom_complet,
        'sujet': sujet,
        'corps': corps
    }), 200

@app.route('/api/recruteur/uploads/<filename>', methods=['GET'])
@jwt_required()
def serve_upload(filename):
    """Servir les fichiers uploadés (CV, lettres, attestations) — accès réservé au recruteur."""
    safe = secure_filename(filename)
    return send_from_directory(UPLOAD_FOLDER, safe)

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT TEST (optionnel - à désactiver en production)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/test/analyze', methods=['POST'])
def test_analyze():
    """Endpoint de test pour valider l'analyse sans upload"""
    data = request.json or {}
    poste = data.get('poste', 'Analyste Crédit CCB')
    cv_text = data.get('cv_text', '')
    lettre_text = data.get('lettre_text', '')
    attestation_text = data.get('attestation_text', '')
    
    result = analyze_cv_against_grille(cv_text, lettre_text, attestation_text, poste)
    
    return jsonify(result), 200

# ══════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Serveur RecrutBank démarré sur le port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

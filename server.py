from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, redis, docx, json
from werkzeug.utils import secure_filename
from pdfminer.high_level import extract_text

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
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# ── LOGIQUE D'EXTRACTION DE TEXTE ─────────────────────────────────────────────
def extract_text_from_file(filepath):
    ext = filepath.rsplit('.', 1)[1].lower()
    try:
        if ext == 'pdf':
            return extract_text(filepath).lower()
        elif ext in ['docx', 'doc']:
            doc = docx.Document(filepath)
            return "\n".join([p.text for p in doc.paragraphs]).lower()
    except Exception as e:
        print(f"Erreur d'extraction : {e}")
        return ""
    return ""

# ── GRILLE DE PRÉSÉLECTION & MOTS-CLÉS (Source: Grille de présélection.docx) ──
GRILLE = {
    "Responsable Administration de Crédit": {
        "keywords": ["ifrs 9", "cobac", "conformité", "impayés", "portefeuille", "garantie", "audit", "crédit", "risque"],
        "labels": {
            "eliminatoire": ["expérience bancaire", "crédit", "risque", "garantie", "conformité"],
            "signaux_forts": ["ifrs 9", "cobac", "impayés", "portefeuille"],
            "a_verifier": ["validation dossiers", "audit"]
        }
    },
    "Analyste Crédit CCB": {
        "keywords": ["cash-flow", "montage", "analyse financière", "états financiers", "comité", "pme"],
        "labels": {
            "eliminatoire": ["analyse crédit", "états financiers"],
            "signaux_forts": ["cash-flow", "montage", "comité"],
            "a_verifier": ["pme", "particulier"]
        }
    },
    "Archiviste (Administration Crédit)": {
        "keywords": ["archivage", "documentaire", "juridique", "numérisation", "classement"],
        "labels": {
            "eliminatoire": ["gestion documentaire", "archivage"],
            "signaux_forts": ["banque", "juridique", "contrat"],
            "a_verifier": ["physique", "électronique"]
        }
    },
    "Senior Finance Officer": {
        "keywords": ["ifrs", "budget", "reporting", "power bi", "excel", "consolidation"],
        "labels": {
            "eliminatoire": ["finance", "reporting"],
            "signaux_forts": ["consolidation", "power bi", "ifrs"],
            "a_verifier": ["budget", "pilotage"]
        }
    },
    "Market Risk Officer": {
        "keywords": ["basel", "var", "stress testing", "marché", "modélisation", "python"],
        "labels": {
            "eliminatoire": ["risque de marché", "produit financier"],
            "signaux_forts": ["basel", "var", "python"],
            "a_verifier": ["stress testing", "back-testing"]
        }
    },
    "IT Réseau & Infrastructure": {
        "keywords": ["cisco", "ccna", "ccnp", "sécurité", "vmware", "réseau", "infrastructure"],
        "labels": {
            "eliminatoire": ["réseau", "système", "certification"],
            "signaux_forts": ["cisco", "cybersécurité", "virtualisation"],
            "a_verifier": ["haute disponibilité", "incident"]
        }
    }
}

POSTES = list(GRILLE.keys())

# ── HELPERS ────────────────────────────────────────────────────────────────────
def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES PUBLIQUES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify(POSTES), 200

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    if not data: return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    pwd = hash_pwd(data.get('password', ''))
    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r.get("email", "").lower() == email and r.get("password") == pwd:
            token = create_access_token(identity=r["id"])
            return jsonify({'token': token, 'nom': r["nom"], 'email': r["email"]}), 200
    return jsonify({'error': 'Identifiants incorrects'}), 401

# ── CANDIDATURE (AVEC SCORING ET CHECKLIST) ───────────────────────────────────
@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        email = request.form.get('email', '').strip().lower()
        poste = request.form.get('poste', '').strip()

        if not nom or not email or poste not in POSTES:
            return jsonify({'error': 'Données invalides'}), 400

        # Sauvegarde et Analyse du CV
        cv_filename = ''
        score = 0
        checklist_auto = {"eliminatoire": [], "signaux_forts": [], "a_verifier": []}

        if 'cv' in request.files:
            cv = request.files['cv']
            if cv and allowed_file(cv.filename):
                cv_filename = f"{uuid.uuid4().hex}_{secure_filename(cv.filename)}"
                path = os.path.join(UPLOAD_FOLDER, cv_filename)
                cv.save(path)
                
                # Extraction et Analyse
                text_content = extract_text_from_file(path)
                critere_poste = GRILLE[poste]["labels"]
                
                # Analyse des catégories
                for cat in ["eliminatoire", "signaux_forts", "a_verifier"]:
                    for word in critere_poste[cat]:
                        if word in text_content:
                            checklist_auto[cat].append(word)
                
                # Calcul Score : 40% éliminatoires, 40% signaux forts, 20% vérifications
                score_calc = 0
                if checklist_auto["eliminatoire"]: score_calc += 40
                score_calc += (len(checklist_auto["signaux_forts"]) / len(critere_poste["signaux_forts"])) * 40
                score_calc += (len(checklist_auto["a_verifier"]) / len(critere_poste["a_verifier"])) * 20
                score = round(min(100, score_calc))

        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={
            "nom": nom, "prenom": prenom, "email": email, "poste": poste,
            "cv_filename": cv_filename, "statut": "en_attente",
            "score": str(score),
            "checklist": json.dumps(checklist_auto),
            "date_candidature": datetime.datetime.now().isoformat()
        })
        return jsonify({'message': 'Candidature soumise', 'token': token, 'score': score}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES RECRUTEUR
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    keys = redis_client.keys("candidat:*")
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0, "rejete": 0, "entretien": 0}
    for k in keys:
        s = redis_client.hget(k, 'statut')
        if s in stats: stats[s] += 1
    return jsonify(stats), 200

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    keys = redis_client.keys("candidat:*")
    result = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':', 1)[1]
        result.append(c)
    result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
    return jsonify(result), 200

@app.route('/api/recruteur/uploads/<filename>', methods=['GET'])
@jwt_required()
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, secure_filename(filename))

if __name__ == '__main__':
    # Initialisation recruteur par défaut
    if not redis_client.exists("recruteur:1"):
        redis_client.hset("recruteur:1", mapping={"email": "recruteur@banque.com", "password": hash_pwd("admin123"), "nom": "RH Admin"})
    
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

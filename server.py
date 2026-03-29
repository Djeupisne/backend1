from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
import os, hashlib, datetime, uuid, redis
from werkzeug.utils import secure_filename

# ── CONFIGURATION DE L'APP ──────────────────────────────────────────────
app = Flask(__name__, static_folder='../frontend', static_url_path='')

# Configuration CORS complète pour autoriser votre frontend Render et le local
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://recrutment.onrender.com", "http://localhost:5000", "http://127.0.0.1:5000"],
        "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
jwt = JWTManager(app)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}

POSTES = [
    "Responsable Administration de Crédit",
    "Analyste Crédit CCB",
    "Archiviste (Administration Crédit)",
    "Senior Finance Officer",
    "Market Risk Officer",
    "IT Réseau & Infrastructure"
]

# ── CONNEXION REDIS ────────────────────────────────────────────────────
# Utilisation de getenv avec des valeurs par défaut ou None
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=os.getenv("REDIS_PORT"),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True
)

# ── HELPERS ─────────────────────────────────────────────────────────────
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

# ── INITIALISATION (Exécutée au chargement par Gunicorn) ───────────────
def init_recruteur():
    try:
        # On vérifie la connexion
        redis_client.ping()
        if not redis_client.exists("recruteur:1"):
            redis_client.hset("recruteur:1", mapping={
                "id": "1",
                "email": "recruteur@banque.com",
                "password": hash_pwd("admin123"),
                "nom": "Responsable RH"
            })
            print("✅ Recruteur par défaut initialisé.")
    except Exception as e:
        print(f"⚠️ Attention: Connexion Redis impossible ou erreur d'init: {e}")

# Appel direct pour que Render l'exécute au démarrage
init_recruteur()

# ── ROUTES STATIQUES & PAGES ──────────────────────────────────────────
@app.route('/')
def index():
    # Pour éviter le 404 sur la racine si le dossier frontend n'est pas trouvé
    try:
        return send_from_directory('../frontend', 'index.html')
    except:
        return jsonify({"status": "online", "message": "API Backend is running"}), 200

@app.route('/login')
def login_page():
    return send_from_directory('../frontend', 'login.html')

@app.route('/dashboard-recruteur')
def dash_recruteur():
    return send_from_directory('../frontend', 'dashboard-recruteur.html')

@app.route('/dashboard-candidat')
def dash_candidat():
    return send_from_directory('../frontend', 'dashboard-candidat.html')

# ── AUTHENTIFICATION ───────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    if not data:
        return jsonify({'error': 'Données JSON manquantes'}), 400
        
    email = data.get('email')
    pwd = hash_pwd(data.get('password', ''))

    # Recherche simplifiée du recruteur
    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r.get("email") == email and r.get("password") == pwd:
            token = create_access_token(identity=r["id"])
            return jsonify({'token': token, 'nom': r["nom"], 'email': r["email"]}), 200
            
    return jsonify({'error': 'Identifiants incorrects'}), 401

# ── CANDIDATS ──────────────────────────────────────────────────────────
@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom = request.form.get('nom','').strip()
        prenom = request.form.get('prenom','').strip()
        email = request.form.get('email','').strip().lower()
        telephone = request.form.get('telephone','').strip()
        poste = request.form.get('poste','').strip()

        if not all([nom, prenom, email, poste]):
            return jsonify({'error': 'Champs obligatoires manquants'}), 400
        if poste not in POSTES:
            return jsonify({'error': 'Poste invalide'}), 400

        if redis_client.exists(f"email:{email}"):
            return jsonify({'error': 'Un candidat avec cet email existe déjà'}), 409

        cv_filename = None
        lettre_filename = None

        if 'cv' in request.files:
            cv = request.files['cv']
            if cv and allowed_file(cv.filename):
                ext = cv.filename.rsplit('.', 1)[1].lower()
                cv_filename = f"{uuid.uuid4().hex}_cv.{ext}"
                cv.save(os.path.join(UPLOAD_FOLDER, cv_filename))

        if 'lettre' in request.files:
            lettre = request.files['lettre']
            if lettre and allowed_file(lettre.filename):
                ext = lettre.filename.rsplit('.', 1)[1].lower()
                lettre_filename = f"{uuid.uuid4().hex}_lettre.{ext}"
                lettre.save(os.path.join(UPLOAD_FOLDER, lettre_filename))

        token = uuid.uuid4().hex
        key = f"candidat:{token}"

        redis_client.hset(key, mapping={
            "nom": nom, "prenom": prenom, "email": email,
            "telephone": telephone, "poste": poste,
            "cv_filename": str(cv_filename), "lettre_filename": str(lettre_filename),
            "statut": "en_attente", "note": "", "score": "0",
            "date_candidature": datetime.datetime.now().isoformat()
        })
        redis_client.set(f"email:{email}", key)

        return jsonify({'message': 'Candidature soumise avec succès', 'token': token}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    key = f"candidat:{token}"
    if not redis_client.exists(key):
        return jsonify({'error': 'Candidature introuvable'}), 404
    return jsonify(redis_client.hgetall(key)), 200

# ── RECRUTEUR (protégé) ────────────────────────────────────────────────
@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def stats():
    keys = redis_client.keys("candidat:*")
    total = len(keys)
    en_attente = retenu = rejete = entretien = 0
    by_poste = {}

    for k in keys:
        c = redis_client.hgetall(k)
        statut = c.get("statut", "en_attente")
        poste = c.get("poste", "Non spécifié")
        
        if statut == "en_attente": en_attente += 1
        elif statut == "retenu": retenu += 1
        elif statut == "rejete": rejete += 1
        elif statut == "entretien": entretien += 1
        
        by_poste[poste] = by_poste.get(poste, 0) + 1

    return jsonify({
        'total': total, 'en_attente': en_attente, 'retenu': retenu,
        'rejete': rejete, 'entretien': entretien,
        'by_poste': [{'poste': p, 'n': n} for p, n in by_poste.items()]
    }), 200

# ── DÉMARRAGE ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Ce code ne s'exécute qu'en local
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

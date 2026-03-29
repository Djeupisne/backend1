from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
import os, hashlib, datetime, uuid, redis
from werkzeug.utils import secure_filename

# ── CONFIG ──────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='../frontend', static_url_path='')

# Autoriser CORS pour ton frontend Render et le dev local
CORS(app, origins=[
    "http://localhost:5000",              # pour tester en local
    "https://recrutment.onrender.com"     # ton site frontend en prod
])

app.config['JWT_SECRET_KEY'] = 'gestion-candidatures-secret-2024'
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

# ── REDIS CONNECTION ────────────────────────────────────────────────────
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

# ── INIT DEFAULT RECRUTEUR ──────────────────────────────────────────────
def init_recruteur():
    if not redis_client.exists("recruteur:1"):
        redis_client.hset("recruteur:1", mapping={
            "id": "1",
            "email": "recruteur@banque.com",
            "password": hash_pwd("admin123"),
            "nom": "Responsable RH"
        })

# ── STATIC PAGES ───────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/login')
def login_page():
    return send_from_directory('../frontend', 'login.html')

@app.route('/dashboard-recruteur')
def dash_recruteur():
    return send_from_directory('../frontend', 'dashboard-recruteur.html')

@app.route('/dashboard-candidat')
def dash_candidat():
    return send_from_directory('../frontend', 'dashboard-candidat.html')

# ── AUTH ───────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    pwd = hash_pwd(data.get('password', ''))

    # Recherche recruteur
    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r["email"] == email and r["password"] == pwd:
            token = create_access_token(identity=r["id"])
            return jsonify({'token': token, 'nom': r["nom"], 'email': r["email"]})
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

        if redis_client.exists(f"email:{email}"):
            return jsonify({'error': 'Un candidat avec cet email existe déjà'}), 409

        redis_client.hset(key, mapping={
            "nom": nom, "prenom": prenom, "email": email,
            "telephone": telephone, "poste": poste,
            "cv_filename": cv_filename, "lettre_filename": lettre_filename,
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
    return jsonify(redis_client.hgetall(key))

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
        poste = c.get("poste", "")
        if statut == "en_attente": en_attente += 1
        elif statut == "retenu": retenu += 1
        elif statut == "rejete": rejete += 1
        elif statut == "entretien": entretien += 1
        by_poste[poste] = by_poste.get(poste, 0) + 1

    return jsonify({
        'total': total, 'en_attente': en_attente, 'retenu': retenu,
        'rejete': rejete, 'entretien': entretien,
        'by_poste': [{'poste': p, 'n': n} for p, n in by_poste.items()]
    })

# ── STARTUP ────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_recruteur()
    print("✅ Serveur démarré avec Redis")
    port = int(os.getenv("PORT", 5000))  # Render fournit PORT
    app.run(host="0.0.0.0", port=port)

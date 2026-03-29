from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
import os, hashlib, datetime, uuid, redis

# ── CONFIGURATION ──────────────────────────────────────────────────────
app = Flask(__name__, static_folder='../frontend', static_url_path='')

# Configuration CORS pour autoriser votre frontend Render
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://recrutment.onrender.com", "http://localhost:5000"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

app.config['JWT_SECRET_KEY'] = 'gestion-candidatures-secret-2024'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

POSTES = [
    "Responsable Administration de Crédit",
    "Analyste Crédit CCB",
    "Archiviste (Administration Crédit)",
    "Senior Finance Officer",
    "Market Risk Officer",
    "IT Réseau & Infrastructure"
]

# ── REDIS ──────────────────────────────────────────────────────────────
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=os.getenv("REDIS_PORT"),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True
)

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def init_recruteur():
    try:
        if not redis_client.exists("recruteur:1"):
            redis_client.hset("recruteur:1", mapping={
                "id": "1",
                "email": "recruteur@banque.com",
                "password": hash_pwd("admin123"),
                "nom": "Responsable RH"
            })
            print("✅ Recruteur initialisé.")
    except:
        print("⚠️ Erreur connexion Redis au démarrage.")

init_recruteur()

# ── ROUTES API ─────────────────────────────────────────────────────────

@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify(POSTES), 200

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    pwd = hash_pwd(data.get('password', ''))
    
    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r.get("email") == email and r.get("password") == pwd:
            token = create_access_token(identity=r["id"])
            return jsonify({'token': token, 'nom': r["nom"]})
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom = request.form.get('nom')
        email = request.form.get('email')
        poste = request.form.get('poste')
        
        if not nom or not email or poste not in POSTES:
            return jsonify({'error': 'Données invalides'}), 400

        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={
            "nom": nom, "prenom": request.form.get('prenom'),
            "email": email, "poste": poste, "statut": "en_attente",
            "date_candidature": datetime.datetime.now().isoformat()
        })
        return jsonify({'message': 'Succès', 'token': token}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    data = redis_client.hgetall(f"candidat:{token}")
    return jsonify(data) if data else (jsonify({'error': 'Introuvable'}), 404)

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

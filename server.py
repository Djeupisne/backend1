from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
import os, hashlib, datetime, uuid, redis

app = Flask(__name__, static_folder='../frontend', static_url_path='')

# Configuration CORS pour autoriser ton frontend Render et le local
CORS(app, resources={r"/api/*": {"origins": ["https://recrutment.onrender.com", "http://localhost:5000"], "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"], "allow_headers": ["Content-Type", "Authorization"]}})

app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

# Connexion Redis utilisant tes variables d'environnement Render (corrigées)
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis-11133.c8.us-east-1-4.ec2.cloud.redislabs.com"),
    port=int(os.getenv("REDIS_PORT", 11133)),
    password=os.getenv("REDIS_PASSWORD", "63365a00edce4c2295425a36ec476d50"),
    decode_responses=True
)

POSTES = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Archiviste (Administration Crédit)", "Senior Finance Officer", "Market Risk Officer", "IT Réseau & Infrastructure"]

def hash_pwd(pwd): return hashlib.sha256(pwd.encode()).hexdigest()

def init_recruteur():
    try:
        if not redis_client.exists("recruteur:1"):
            redis_client.hset("recruteur:1", mapping={"id": "1", "email": "recruteur@banque.com", "password": hash_pwd("admin123"), "nom": "Responsable RH"})
            print("✅ Recruteur initialisé.")
        else:
            print("✅ Connexion Redis établie.")
    except Exception as e:
        print(f"⚠️ Erreur Redis: {e}")

init_recruteur()

@app.route('/api/postes', methods=['GET'])
def get_postes(): return jsonify(POSTES), 200

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    if not data: return jsonify({'error': 'JSON manquant'}), 400
    email, pwd = data.get('email'), hash_pwd(data.get('password', ''))
    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r.get("email") == email and r.get("password") == pwd:
            return jsonify({'token': create_access_token(identity=r["id"]), 'nom': r["nom"], 'email': r["email"]}), 200
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom, prenom, email, poste = request.form.get('nom'), request.form.get('prenom'), request.form.get('email'), request.form.get('poste')
        if not nom or not email or poste not in POSTES: return jsonify({'error': 'Données invalides'}), 400
        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={"nom": nom, "prenom": prenom, "email": email, "poste": poste, "statut": "en_attente", "note": "", "score": "0", "date_candidature": datetime.datetime.now().isoformat()})
        return jsonify({'message': 'Succès', 'token': token}), 201
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    data = redis_client.hgetall(f"candidat:{token}")
    return jsonify(data) if data else (jsonify({'error': 'Introuvable'}), 404)

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    keys = redis_client.keys("candidat:*")
    res = []
    for k in keys:
        c = redis_client.hgetall(k)
        c['id'] = k.split(':')[-1]
        res.append(c)
    res.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
    return jsonify(res), 200

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
    stats['by_poste'] = [{'poste': k, 'n': v} for k, v in counts.items()]
    return jsonify(stats), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    key = f"candidat:{token}"
    if not redis_client.exists(key): return jsonify({"error": "Non trouvé"}), 404
    data = request.json
    redis_client.hset(key, mapping={"statut": data.get('statut'), "note": data.get('note', ''), "score": str(data.get('score', '0'))})
    return jsonify({"message": "Mis à jour"}), 200

if __name__ == '__main__':
    # Utilisation du port 10000 obligatoire pour éviter le Timeout sur Render
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

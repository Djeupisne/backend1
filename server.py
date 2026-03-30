from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, redis, docx
from werkzeug.utils import secure_filename
from pdfminer.high_level import extract_text

app = Flask(__name__)

# ── CONFIGURATION CORS RENFORCÉE ─────────────────────────────────────────────
# On autorise explicitement l'origine du frontend et l'en-tête Authorization pour le JWT
CORS(app, resources={r"/api/*": {
    "origins": ["https://recrutment.onrender.com", "http://localhost:3000"],
    "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],
    "allow_headers": ["Content-Type", "Authorization"],
    "expose_headers": ["Content-Type", "Authorization"]
}})

app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

redis_client = redis.Redis(host=os.getenv("REDIS_HOST", "redis-11133.c8.us-east-1-4.ec2.cloud.redislabs.com"), port=int(os.getenv("REDIS_PORT", 11133)), username="default", password=os.getenv("REDIS_PASSWORD", "WKJdeilasGOWkXJWOHwqcRV7X5uWwQ"), decode_responses=True)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def extract_content(fp):
    ext = fp.rsplit('.', 1)[1].lower()
    try:
        if ext == 'pdf': return extract_text(fp).lower()
        if ext in ['docx', 'doc']:
            d = docx.Document(fp)
            return "\n".join([p.text for p in d.paragraphs]).lower()
    except: return ""
    return ""

POSTES = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Archiviste (Administration Crédit)", "Senior Finance Officer", "Market Risk Officer", "IT Réseau & Infrastructure"]
GRILLE = {
    "Responsable Administration de Crédit": ["ifrs 9", "cobac", "conformité", "impayés", "portefeuille"],
    "Analyste Crédit CCB": ["cash-flow", "montage de crédit", "comité de crédit", "analyse financière"],
    "Archiviste (Administration Crédit)": ["archivage", "juridique", "garanties", "gestion documentaire"],
    "Senior Finance Officer": ["consolidation", "ifrs", "budget", "reporting", "power bi"],
    "Market Risk Officer": ["basel", "var", "stress testing", "produits financiers"],
    "IT Réseau & Infrastructure": ["cisco", "ccna", "ccnp", "cybersécurité", "vmware"]
}

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email, pwd = data.get('email', '').lower(), hashlib.sha256(data.get('password', '').encode()).hexdigest()
    for k in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(k)
        if r.get("email", "").lower() == email and r.get("password") == pwd:
            return jsonify({'token': create_access_token(identity=r["id"]), 'nom': r["nom"]}), 200
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    keys = redis_client.keys("candidat:*")
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0, "rejete": 0, "entretien": 0}
    for k in keys:
        s = redis_client.hget(k, 'statut')
        if s in stats: stats[s] += 1
    return jsonify(stats), 200

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom, prenom, email, poste = request.form.get('nom'), request.form.get('prenom'), request.form.get('email', '').lower(), request.form.get('poste')
        cv = request.files.get('cv')
        cv_fn, score = "", 0
        if cv:
            cv_fn = f"{uuid.uuid4().hex}_{secure_filename(cv.filename)}"
            path = os.path.join(UPLOAD_FOLDER, cv_fn)
            cv.save(path)
            txt = extract_content(path)
            matches = sum(1 for k in GRILLE.get(poste, []) if k in txt)
            score = min(100, matches * 25)
        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={"nom": nom, "prenom": prenom, "email": email, "poste": poste, "cv_filename": cv_fn, "statut": "en_attente", "score": str(score), "date_candidature": datetime.datetime.now().isoformat()})
        return jsonify({'token': token, 'score': score}), 201
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    res = []
    for k in redis_client.keys("candidat:*"):
        c = redis_client.hgetall(k)
        c['id'] = k.split(':')[1]
        res.append(c)
    return jsonify(res), 200

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

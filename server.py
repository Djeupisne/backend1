from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, redis, docx
from werkzeug.utils import secure_filename
from pdfminer.high_level import extract_text

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"], "allow_headers": ["Content-Type", "Authorization"]}})
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)
redis_client = redis.Redis(host=os.getenv("REDIS_HOST", "redis-11133.c8.us-east-1-4.ec2.cloud.redislabs.com"), port=int(os.getenv("REDIS_PORT", 11133)), username="default", password=os.getenv("REDIS_PASSWORD", "WKJdeilasGOWkXJWOHwqcRV7X5uWwQ"), decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
def hash_pwd(pwd): return hashlib.sha256(pwd.encode()).hexdigest()

def extract_content(filepath):
    ext = filepath.rsplit('.', 1)[1].lower()
    try:
        if ext == 'pdf': return extract_text(filepath).lower()
        if ext in ['docx', 'doc']:
            doc = docx.Document(filepath)
            return "\n".join([p.text for p in doc.paragraphs]).lower()
    except: return ""
    return ""

POSTES = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Archiviste (Administration Crédit)", "Senior Finance Officer", "Market Risk Officer", "IT Réseau & Infrastructure"]
GRILLE = {
    "Responsable Administration de Crédit": {"keywords": ["ifrs 9", "cobac", "conformité", "impayés", "portefeuille"], "eliminatoire": ["Pas d'expérience bancaire", "Moins de 3 ans en crédit / risque"]},
    "Analyste Crédit CCB": {"keywords": ["cash-flow", "montage de crédit", "comité de crédit", "analyse financière"], "eliminatoire": ["Pas d'expérience en analyse crédit", "Profil purement commercial"]},
    "Archiviste (Administration Crédit)": {"keywords": ["archivage", "juridique", "garanties", "gestion documentaire"], "eliminatoire": ["Aucune expérience en gestion documentaire"]},
    "Senior Finance Officer": {"keywords": ["consolidation", "ifrs", "budget", "reporting", "power bi"], "eliminatoire": ["Pas d'expérience en finance senior"]},
    "Market Risk Officer": {"keywords": ["basel", "var", "stress testing", "produits financiers"], "eliminatoire": ["Pas d'expérience en gestion des risques de marché"]},
    "IT Réseau & Infrastructure": {"keywords": ["cisco", "ccna", "ccnp", "cybersécurité", "vmware"], "eliminatoire": ["Pas de certification réseau"]}
}

@app.route('/api/postes', methods=['GET'])
def get_postes(): return jsonify(POSTES), 200

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email, pwd = data.get('email', '').lower(), hash_pwd(data.get('password', ''))
    for key in redis_client.keys("recruteur:*"):
        r = redis_client.hgetall(key)
        if r.get("email", "").lower() == email and r.get("password") == pwd:
            return jsonify({'token': create_access_token(identity=r["id"]), 'nom': r["nom"], 'email': r["email"]}), 200
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom, prenom, email, poste = request.form.get('nom'), request.form.get('prenom'), request.form.get('email', '').lower(), request.form.get('poste')
        if not all([nom, prenom, email]) or poste not in POSTES: return jsonify({'error': 'Champs manquants'}), 400
        for k in redis_client.keys("candidat:*"):
            if redis_client.hget(k, 'email') == email: return jsonify({'error': 'Email déjà utilisé'}), 409
        cv_fn, score = "", 0
        if 'cv' in request.files:
            cv = request.files['cv']
            if cv and allowed_file(cv.filename):
                cv_fn = f"{uuid.uuid4().hex}_{secure_filename(cv.filename)}"
                path = os.path.join(UPLOAD_FOLDER, cv_fn)
                cv.save(path)
                txt = extract_content(path)
                kws = GRILLE[poste]["keywords"]
                matches = sum(1 for k in kws if k in txt)
                score = min(100, matches * 20)
        token = uuid.uuid4().hex
        redis_client.hset(f"candidat:{token}", mapping={"nom": nom, "prenom": prenom, "email": email, "telephone": request.form.get('telephone', ''), "poste": poste, "cv_filename": cv_fn, "statut": "en_attente", "score": str(score), "date_candidature": datetime.datetime.now().isoformat()})
        return jsonify({'message': 'Succès', 'token': token, 'score': score}), 201
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    res = []
    for k in redis_client.keys("candidat:*"):
        c = redis_client.hgetall(k)
        c['id'] = k.split(':')[1]
        res.append(c)
    res.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
    return jsonify(res), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_statut(token):
    data = request.json
    redis_client.hset(f"candidat:{token}", mapping={"statut": data.get('statut'), "note": data.get('note', '')})
    return jsonify({'message': 'OK'}), 200

@app.route('/api/recruteur/uploads/<filename>')
@jwt_required()
def serve_file(filename): return send_from_directory(UPLOAD_FOLDER, secure_filename(filename))

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

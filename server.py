from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import sqlite3, os, hashlib, datetime, uuid
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='../frontend', static_url_path='')
CORS(app)
app.config['JWT_SECRET_KEY'] = 'gestion-candidatures-secret-2024'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
jwt = JWTManager(app)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}

DB_PATH = os.path.join(os.path.dirname(__file__), 'candidatures.db')

POSTES = [
    "Responsable Administration de Crédit",
    "Analyste Crédit CCB",
    "Archiviste (Administration Crédit)",
    "Senior Finance Officer",
    "Market Risk Officer",
    "IT Réseau & Infrastructure"
]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS recruteurs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        nom TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS candidats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        prenom TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        telephone TEXT,
        poste TEXT NOT NULL,
        cv_filename TEXT,
        lettre_filename TEXT,
        statut TEXT DEFAULT 'en_attente',
        note TEXT DEFAULT '',
        score INTEGER DEFAULT 0,
        date_candidature TEXT DEFAULT CURRENT_TIMESTAMP,
        token TEXT UNIQUE
    )''')
    # Insert default recruiter: admin / admin123
    pwd = hashlib.sha256('admin123'.encode()).hexdigest()
    c.execute("INSERT OR IGNORE INTO recruteurs (email, password, nom) VALUES (?, ?, ?)",
              ('recruteur@banque.com', pwd, 'Responsable RH'))
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ── STATIC PAGES ──────────────────────────────────────────────────────────────
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

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    pwd = hashlib.sha256(data.get('password','').encode()).hexdigest()
    conn = get_db()
    recruteur = conn.execute("SELECT * FROM recruteurs WHERE email=? AND password=?",
                              (data.get('email'), pwd)).fetchone()
    conn.close()
    if recruteur:
        token = create_access_token(identity=str(recruteur['id']))
        return jsonify({'token': token, 'nom': recruteur['nom'], 'email': recruteur['email']})
    return jsonify({'error': 'Identifiants incorrects'}), 401

# ── CANDIDATS ─────────────────────────────────────────────────────────────────
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
        conn = get_db()
        try:
            conn.execute("""INSERT INTO candidats (nom,prenom,email,telephone,poste,cv_filename,lettre_filename,token)
                           VALUES (?,?,?,?,?,?,?,?)""",
                         (nom, prenom, email, telephone, poste, cv_filename, lettre_filename, token))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({'error': 'Un candidat avec cet email existe déjà'}), 409
        conn.close()
        return jsonify({'message': 'Candidature soumise avec succès', 'token': token}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    conn = get_db()
    c = conn.execute("SELECT nom,prenom,poste,statut,date_candidature,note FROM candidats WHERE token=?", (token,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Candidature introuvable'}), 404
    return jsonify(dict(row))

# ── RECRUTEUR (protégé) ───────────────────────────────────────────────────────
@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as n FROM candidats").fetchone()['n']
    en_attente = conn.execute("SELECT COUNT(*) as n FROM candidats WHERE statut='en_attente'").fetchone()['n']
    retenu = conn.execute("SELECT COUNT(*) as n FROM candidats WHERE statut='retenu'").fetchone()['n']
    rejete = conn.execute("SELECT COUNT(*) as n FROM candidats WHERE statut='rejete'").fetchone()['n']
    entretien = conn.execute("SELECT COUNT(*) as n FROM candidats WHERE statut='entretien'").fetchone()['n']
    by_poste = conn.execute("SELECT poste, COUNT(*) as n FROM candidats GROUP BY poste").fetchall()
    conn.close()
    return jsonify({
        'total': total, 'en_attente': en_attente, 'retenu': retenu,
        'rejete': rejete, 'entretien': entretien,
        'by_poste': [dict(r) for r in by_poste]
    })

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    poste = request.args.get('poste', '')
    statut = request.args.get('statut', '')
    search = request.args.get('search', '')
    query = "SELECT id,nom,prenom,email,telephone,poste,statut,score,date_candidature,cv_filename,lettre_filename FROM candidats WHERE 1=1"
    params = []
    if poste:
        query += " AND poste=?"; params.append(poste)
    if statut:
        query += " AND statut=?"; params.append(statut)
    if search:
        query += " AND (nom LIKE ? OR prenom LIKE ? OR email LIKE ?)"; params += [f'%{search}%']*3
    query += " ORDER BY date_candidature DESC"
    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/recruteur/candidats/<int:cid>', methods=['GET'])
@jwt_required()
def get_candidat(cid):
    conn = get_db()
    row = conn.execute("SELECT * FROM candidats WHERE id=?", (cid,)).fetchone()
    conn.close()
    if not row: return jsonify({'error': 'Introuvable'}), 404
    return jsonify(dict(row))

@app.route('/api/recruteur/candidats/<int:cid>/statut', methods=['PUT'])
@jwt_required()
def update_statut(cid):
    data = request.json
    statut = data.get('statut')
    note = data.get('note', '')
    score = data.get('score', 0)
    if statut not in ['en_attente', 'retenu', 'rejete', 'entretien']:
        return jsonify({'error': 'Statut invalide'}), 400
    conn = get_db()
    conn.execute("UPDATE candidats SET statut=?,note=?,score=? WHERE id=?", (statut, note, score, cid))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Mis à jour'})

@app.route('/api/recruteur/candidats/<int:cid>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(cid):
    conn = get_db()
    row = conn.execute("SELECT * FROM candidats WHERE id=?", (cid,)).fetchone()
    conn.close()
    if not row: return jsonify({'error': 'Introuvable'}), 404
    c = dict(row)
    data = request.json or {}
    message_type = data.get('type', c['statut'])

    if message_type == 'retenu':
        sujet = f"Félicitations – Votre candidature pour le poste {c['poste']} a été retenue"
        corps = f"""Madame, Monsieur {c['prenom']} {c['nom']},

Nous avons le plaisir de vous informer que votre candidature pour le poste de {c['poste']} a été retenue à l'issue de notre processus de présélection.

Nous vous contacterons très prochainement pour vous communiquer les modalités de la prochaine étape du processus de recrutement.

Dans l'attente, nous restons disponibles pour toute question.

Cordialement,
L'équipe Ressources Humaines"""
    elif message_type == 'entretien':
        sujet = f"Invitation à un entretien – Poste {c['poste']}"
        corps = f"""Madame, Monsieur {c['prenom']} {c['nom']},

Suite à l'examen de votre candidature pour le poste de {c['poste']}, nous souhaitons vous inviter à un entretien.

Nous prendrons contact avec vous dans les meilleurs délais pour convenir d'une date et d'un horaire.

Cordialement,
L'équipe Ressources Humaines"""
    else:
        sujet = f"Réponse à votre candidature – Poste {c['poste']}"
        corps = f"""Madame, Monsieur {c['prenom']} {c['nom']},

Nous vous remercions de l'intérêt que vous portez à notre institution et du temps consacré à votre candidature pour le poste de {c['poste']}.

Après examen attentif de votre dossier, nous avons le regret de vous informer que votre candidature n'a pas été retenue pour la suite du processus de sélection. Cette décision a été prise en tenant compte de l'ensemble des candidatures reçues et des critères spécifiques du poste.

Nous vous encourageons à postuler à nouveau pour toute opportunité future qui correspondrait à votre profil.

Nous vous souhaitons plein succès dans votre recherche.

Cordialement,
L'équipe Ressources Humaines"""

    return jsonify({'to': c['email'], 'sujet': sujet, 'corps': corps, 'nom': f"{c['prenom']} {c['nom']}"})

@app.route('/api/recruteur/uploads/<filename>', methods=['GET'])
@jwt_required()
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify(POSTES)

if __name__ == '__main__':
    init_db()
    print("✅ Serveur démarré sur http://localhost:5000")
    print("📧 Recruteur: recruteur@banque.com / admin123")
    app.run(debug=True, port=5000)

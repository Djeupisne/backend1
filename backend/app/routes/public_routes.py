# ═══════════════════════════════════════════════════════
# Routes publiques : sante, postes, grille, authentification, candidature, suivi
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from flask import request, jsonify
from flask_jwt_extended import create_access_token
import threading, uuid, json, datetime
from ..config import (app, logger, supabase, POSTES, POSTES_ACTIFS, POSTES_CLOTURES,
                      GRILLE, ACTIVE_MODELS, hash_pwd, allowed_file,
                      upload_file_to_supabase, send_email)
from ..services.text_extraction import extract_text_robust_from_bytes  # noqa: F401
from ..services.ia_analysis import run_analysis_for_candidat
from ..data.postes import is_poste_actif

@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify({"postes": POSTES, "postes_actifs": POSTES_ACTIFS, "postes_clotures": POSTES_CLOTURES}), 200

@app.route('/api/postes/actifs', methods=['GET'])
def get_postes_actifs():
    return jsonify(POSTES_ACTIFS), 200

@app.route('/api/grille/<poste>', methods=['GET'])
def get_grille(poste):
    g = GRILLE.get(poste)
    if not g:
        return jsonify({'error': 'Poste inconnu', 'postes_disponibles': list(GRILLE.keys())}), 404
    return jsonify(g), 200

@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return '', 204
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    hashed_password = hash_pwd(password)
    if supabase:
        try:
            response = supabase.table('recruteurs').select('*').eq('email', email).execute()
            if response.data and len(response.data) > 0:
                recruteur = response.data[0]
                if recruteur.get('password') == hashed_password:
                    access_token = create_access_token(identity=str(recruteur['id']))
                    return jsonify({
                        'token': access_token,
                        'nom': recruteur.get('nom', 'Recruteur'),
                        'email': recruteur.get('email', email)
                    }), 200
        except Exception as e:
            logger.error(f"Erreur login: {e}")
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom = (request.form.get('nom') or '').strip()
        prenom = (request.form.get('prenom') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        telephone = (request.form.get('telephone') or '').strip()
        poste = (request.form.get('poste') or '').strip()
        if not nom or not prenom or not email or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400
        if supabase:
            existing = supabase.table('candidats').select('*').eq('email', email).eq('poste', poste).execute()
            if existing.data and len(existing.data) > 0:
                return jsonify({'error': f'Vous avez deja soumis une candidature pour le poste "{poste}".'}), 409
            all_candidats = supabase.table('candidats').select('numero_dossier').eq('poste', poste).execute()
            max_num = 0
            for c in all_candidats.data:
                existing_num = c.get('numero_dossier', '')
                if existing_num:
                    try:
                        num_val = int(existing_num)
                        if num_val > max_num:
                            max_num = num_val
                    except ValueError:
                        pass
            new_num = max_num + 1
            numero_dossier = str(new_num)
        def save_file_to_supabase(field, suffix):
            f = request.files.get(field)
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_{suffix}.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                return result if result else ''
            return ''
        cv_filename = save_file_to_supabase('cv', 'cv')
        if request.files.get('cv') and not cv_filename:
            return jsonify({'error': "Echec de l'envoi du CV, merci de reessayer."}), 500
        lettre_filename = save_file_to_supabase('lettre', 'lettre')
        if request.files.get('lettre') and not lettre_filename:
            return jsonify({'error': "Echec de l'envoi de la lettre de motivation, merci de reessayer."}), 500
        att_filenames = []
        for f in request.files.getlist('attestation'):
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_attestation.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                if result:
                    att_filenames.append(blob_name)
        token = uuid.uuid4().hex
        supabase.table('candidats').insert({
            "token": token,
            "nom": nom,
            "prenom": prenom,
            "email": email,
            "telephone": telephone,
            "poste": poste,
            "numero_dossier": numero_dossier,
            "cv_filename": cv_filename,
            "lettre_filename": lettre_filename,
            "attestation_filenames": json.dumps(att_filenames, ensure_ascii=False),
            "statut": "en_attente",
            "note": "",
            "score": "0",
            "checklist": "",
            "flags_eliminatoires": "",
            "score_breakdown": "",
            "analyse_status": "pending",
            "date_candidature": datetime.datetime.now().isoformat()
        }).execute()
        if is_poste_actif(poste):
            threading.Thread(target=run_analysis_for_candidat, args=(token, cv_filename, lettre_filename, att_filenames, poste, False), daemon=True).start()
            analyse_msg = f'Analyse automatique en cours avec {len(ACTIVE_MODELS)} modele(s) IA'
        else:
            analyse_msg = 'Poste cloture — candidature enregistree sans analyse'
            supabase.table('candidats').update({
                "analyse_status": "closed_post_no_analysis",
                "analyse_auto_date": datetime.datetime.now().isoformat()
            }).eq('token', token).execute()
        nom_complet = f"{prenom} {nom}".strip()
        sujet_confirmation = f"Confirmation de candidature – {poste}"
        corps_confirmation = f"""Bonjour {nom_complet},
Nous accusons reception de votre candidature pour le poste de {poste}.
Votre dossier sera analyse par notre systeme d'intelligence artificielle dans les plus brefs delais.
Vous recevrez une notification lorsque l'analyse sera terminee.
Sans reponse de notre part sous deux (2) semaines, veuillez considerer que votre candidature n'a pas ete retenue.
Pour toute information : contact@cdotchad.com.
Cordialement,
L'equipe RecrutBank"""
        threading.Thread(target=send_email, args=(email, sujet_confirmation, corps_confirmation), daemon=True).start()
        return jsonify({
            'message': 'Candidature soumise avec succes',
            'token': token,
            'numero_dossier': numero_dossier,
            'analyse': analyse_msg,
            'poste_statut': 'cloture',
            'ia_engine': f"{len(ACTIVE_MODELS)} modeles IA"
        }), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    if supabase:
        response = supabase.table('candidats').select('*').eq('token', token).execute()
        if response.data and len(response.data) > 0:
            data = response.data[0]
            hidden = {'cv_filename', 'lettre_filename', 'attestation_filenames', 'checklist', 'flags_eliminatoires', 'analyse_details', 'score_breakdown'}
            return jsonify({k: v for k, v in data.items() if k not in hidden}), 200
    return jsonify({'error': 'Candidature introuvable'}), 404

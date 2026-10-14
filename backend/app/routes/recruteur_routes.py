# ═══════════════════════════════════════════════════════
# Routes recruteur protegees JWT : dashboard, analyses, exports, suppression
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from flask import request, jsonify, send_file, redirect
from werkzeug.utils import secure_filename
from flask_jwt_extended import jwt_required, get_jwt_identity
import threading, uuid, os, re, io, json, time, gc, tempfile, zipfile, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..config import (app, logger, supabase, SUPABASE_STORAGE_BUCKET, POSTES, POSTES_ACTIFS,
                      POSTES_CLOTURES, GRILLE, ACTIVE_MODELS, sort_candidats,
                      analyze_cv_with_ia_only, send_email, get_signed_url,
                      download_file_from_supabase_robust, extract_text_robust_from_bytes,
                      generate_excel_report_enhanced, generate_pdf_report_enhanced,
                      generate_csv_report_enhanced, generate_word_report_enhanced,
                      _ZIP_JOBS, _ZIP_JOBS_LOCK, _ZIP_MAX_WORKERS)
from ..services.ia_analysis import run_analysis_for_candidat
from ..data.postes import is_poste_actif

@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    keys = response.data if response.data else []
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0, "rejete": 0, "entretien": 0, "by_poste": []}
    counts = {}
    for c in keys:
        statut = c.get('statut', 'en_attente')
        if statut in stats:
            stats[statut] += 1
        else:
            stats['en_attente'] += 1
        p = c.get('poste', 'Inconnu')
        counts[p] = counts.get(p, 0) + 1
    stats['by_poste'] = [{'poste': p, 'n': n} for p, n in sorted(counts.items(), key=lambda x: -x[1])]
    return jsonify(stats), 200

@app.route('/api/recruteur/postes/stats', methods=['GET'])
@jwt_required()
def get_postes_stats():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    keys = response.data if response.data else []
    actifs_count = 0
    clotures_count = 0
    par_poste_actif = {}
    par_poste_cloture = {}
    for c in keys:
        poste = c.get('poste', '')
        if poste in POSTES_ACTIFS:
            actifs_count += 1
            par_poste_actif[poste] = par_poste_actif.get(poste, 0) + 1
        else:
            clotures_count += 1
            par_poste_cloture[poste] = par_poste_cloture.get(poste, 0) + 1
    return jsonify({
        'total': len(keys),
        'postes_actifs': {
            'count': actifs_count,
            'liste': POSTES_ACTIFS,
            'par_poste': par_poste_actif,
            'eligible_reanalyse': False
        },
        'postes_clotures': {
            'count': clotures_count,
            'liste': POSTES_CLOTURES,
            'par_poste': par_poste_cloture,
            'eligible_reanalyse': False
        }
    }), 200

@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    poste_filter = request.args.get('poste', '')
    statut_filter = request.args.get('statut', '')
    search = request.args.get('search', '').lower()
    min_score = request.args.get('min_score', type=int)
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    all_candidats = response.data if response.data else []
    result = []
    for c in all_candidats:
        c['id'] = c.get('token', '')
        if poste_filter and c.get('poste') != poste_filter:
            continue
        if statut_filter:
            statut = c.get('statut', 'en_attente')
            if statut != statut_filter:
                continue
        if min_score is not None and int(c.get('score', 0)) < min_score:
            continue
        if search:
            hay = (f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} {c.get('poste','')} {c.get('numero_dossier','')}").lower()
            if search not in hay:
                continue
        for field in ['score_breakdown', 'flags_eliminatoires', 'analyse_details']:
            if c.get(field):
                try:
                    c[f'{field}_parsed'] = json.loads(c[field])
                except Exception:
                    pass
        result.append(c)
    result = sort_candidats(result)
    return jsonify(result), 200

@app.route('/api/recruteur/candidats/<token>', methods=['GET'])
@jwt_required()
def get_candidat_detail(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    data['id'] = token
    if data.get('attestation_filenames'):
        try:
            data['attestation_filenames_parsed'] = json.loads(data['attestation_filenames'])
        except Exception:
            data['attestation_filenames_parsed'] = []
    for field in ['checklist', 'flags_eliminatoires', 'analyse_details', 'score_breakdown']:
        if data.get(field):
            try:
                data[f'{field}_parsed'] = json.loads(data[field])
            except Exception:
                pass
    return jsonify(data), 200

@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = request.get_json(silent=True) or {}
    statut = data.get('statut', 'en_attente')
    note = data.get('note', '')
    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    update_data = {
        "statut": statut,
        "note": note,
        "decision_date": datetime.datetime.now().isoformat(),
        "decided_by": get_jwt_identity()
    }
    if statut == "rejete":
        update_data["decision"] = "Rejet - Decision du recruteur"
    elif statut == "retenu":
        update_data["decision"] = "Retenu - Decision du recruteur"
    elif statut == "entretien":
        update_data["decision"] = "Entretien - Decision du recruteur"
    supabase.table('candidats').update(update_data).eq('token', token).execute()
    return jsonify({'message': 'Mis a jour avec succes', 'statut': statut}), 200

@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    cv_fn = data.get('cv_filename')
    lm_fn = data.get('lettre_filename')
    att_raw = data.get('attestation_filenames', '[]')
    poste = data.get('poste')
    if not cv_fn:
        return jsonify({'error': 'CV manquant pour analyse'}), 400
    force = request.args.get('force', '0') == '1'
    if not force and not is_poste_actif(poste):
        return jsonify({
            'error': f'Le poste "{poste}" est cloture. Utilisez ?force=1 pour forcer l\'analyse.',
            'poste': poste,
            'statut': 'cloture'
        }), 403
    supabase.table('candidats').update({
        "analyse_status": "pending",
        "analyse_manual_trigger": datetime.datetime.now().isoformat()
    }).eq('token', token).execute()
    threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste, force), daemon=True).start()
    return jsonify({
        'message': f'Analyse relancee avec {len(ACTIVE_MODELS)} modele(s) IA',
        'token': token,
        'ia_engine': f"{len(ACTIVE_MODELS)} modeles"
    }), 202

@app.route('/api/recruteur/reanalyze-status', methods=['GET'])
@jwt_required()
def get_reanalyze_status():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('token, poste, analyse_status, analyse_auto_date').execute()
        keys = response.data if response.data else []
        active_candidates = [d for d in keys if d.get('poste') in POSTES_ACTIFS]
        total = len(active_candidates)
        status_counts = {'pending': 0, 'reanalyzing': 0, 'completed': 0, 'error': 0, 'skipped_closed_post': 0, 'closed_post_no_analysis': 0, 'reanalyzing_auto': 0}
        in_progress = False
        for data in active_candidates:
            status = data.get('analyse_status', 'pending')
            if status in status_counts:
                status_counts[status] += 1
            if status in ('reanalyzing', 'pending'):
                in_progress = True
        processed = status_counts.get('completed', 0) + status_counts.get('error', 0)
        return jsonify({
            'total': total,
            'processed': processed,
            'in_progress': in_progress,
            'status_counts': status_counts,
            'postes_concernes': POSTES_ACTIFS,
            'timestamp': datetime.datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Erreur reanalyze-status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-one/<token>', methods=['POST'])
@jwt_required()
def reanalyze_one_candidate(token):
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').eq('token', token).execute()
        if not response.data or len(response.data) == 0:
            return jsonify({'error': 'Candidat introuvable'}), 404
        data = response.data[0]
        poste = data.get('poste')
        if not is_poste_actif(poste):
            return jsonify({
                'error': f'Le poste "{poste}" est cloture. Reanalyse desactivee.',
                'poste': poste,
                'statut': 'cloture'
            }), 403
        cv_fn = data.get('cv_filename')
        if not cv_fn:
            return jsonify({'error': 'CV manquant pour analyse'}), 400
        lm_fn = data.get('lettre_filename')
        att_raw = data.get('attestation_filenames', '[]')
        supabase.table('candidats').update({
            "analyse_status": "reanalyzing",
            "reanalyze_trigger": datetime.datetime.now().isoformat(),
            "reanalyze_reason": "Reanalyse manuelle (un seul candidat)"
        }).eq('token', token).execute()
        threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste, True), daemon=True).start()
        return jsonify({'message': 'Reanalyse lancee (multi-modeles IA)', 'token': token, 'poste': poste}), 202
    except Exception as e:
        logger.error(f"Erreur reanalyze-one: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-all', methods=['POST'])
@jwt_required()
def reanalyze_all_candidates():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        keys = response.data if response.data else []
        if not keys:
            return jsonify({'message': 'Aucune candidature a reanalyser'}), 200
        candidates_to_reanalyze = [data for data in keys if data.get('poste') in POSTES_ACTIFS and data.get('cv_filename')]
        candidates_skipped = len(keys) - len(candidates_to_reanalyze)
        if not candidates_to_reanalyze:
            return jsonify({
                'message': 'Aucun candidat sur poste actif avec CV a reanalyser',
                'skipped_closed_posts': candidates_skipped
            }), 200
        now_iso = datetime.datetime.now().isoformat()
        for c in candidates_to_reanalyze:
            try:
                supabase.table('candidats').update({
                    "analyse_status": "reanalyzing",
                    "reanalyze_trigger": now_iso,
                    "reanalyze_reason": "Reanalyse parallellisee (postes actifs)"
                }).eq('token', c.get('token')).execute()
            except Exception:
                pass
        def analyze_one(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                poste = data.get('poste')
                if not cv_fn:
                    return (token, False, "CV manquant")
                run_analysis_for_candidat(token, cv_fn, lm_fn, att_raw, poste, False)
                return (token, True, "OK")
            except Exception as e:
                return (data.get('token'), False, str(e))
        MAX_WORKERS = min(8, len(candidates_to_reanalyze))
        logger.info(f"Reanalyse parallele : {len(candidates_to_reanalyze)} candidats, {MAX_WORKERS} workers")
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(analyze_one, c): c for c in candidates_to_reanalyze}
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=300)
                    if success:
                        reanalyzed_count += 1
                    else:
                        errors.append(f"Token {token}: {msg}")
                except Exception as e:
                    errors.append(f"Timeout ou erreur: {str(e)}")
        elapsed = time.time() - start_time
        gc.collect()
        return jsonify({
            'message': f'Reanalyse terminee en {elapsed:.1f}s',
            'reanalyzed_count': reanalyzed_count,
            'total_candidates': len(candidates_to_reanalyze),
            'skipped_closed_posts': candidates_skipped,
            'workers_used': MAX_WORKERS,
            'elapsed_seconds': round(elapsed, 1),
            'errors': errors[:10]
        }), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/reanalyze-poste/<poste>', methods=['POST'])
@jwt_required()
def reanalyze_by_poste(poste):
    if poste not in POSTES:
        return jsonify({'error': f'Poste inconnu: {poste}'}), 400
    if not is_poste_actif(poste):
        return jsonify({
            'error': f'Le poste "{poste}" est cloture. Reanalyse desactivee.',
            'poste': poste,
            'statut': 'cloture',
            'postes_actifs': POSTES_ACTIFS
        }), 403
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').eq('poste', poste).execute()
        keys = response.data if response.data else []
        if not keys:
            return jsonify({'message': f'Aucune candidature pour le poste "{poste}"'}), 200
        candidates_with_cv = [k for k in keys if k.get('cv_filename')]
        if not candidates_with_cv:
            return jsonify({'message': f'Aucun CV trouve pour le poste "{poste}"'}), 200
        now_iso = datetime.datetime.now().isoformat()
        for data in candidates_with_cv:
            try:
                supabase.table('candidats').update({
                    "analyse_status": "reanalyzing",
                    "reanalyze_trigger": now_iso,
                    "reanalyze_reason": f"Reanalyse manuelle parallele : {poste}"
                }).eq('token', data.get('token')).execute()
            except Exception:
                pass
        def analyze_one(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                if not cv_fn:
                    return (token, False, "CV manquant")
                run_analysis_for_candidat(token, cv_fn, lm_fn, att_raw, poste, True)
                return (token, True, "OK")
            except Exception as e:
                return (data.get('token'), False, str(e))
        MAX_WORKERS = min(8, len(candidates_with_cv))
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(analyze_one, c) for c in candidates_with_cv]
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=300)
                    if success:
                        reanalyzed_count += 1
                    else:
                        errors.append(f"Token {token}: {msg}")
                except Exception as e:
                    errors.append(f"Erreur: {str(e)}")
        elapsed = time.time() - start_time
        gc.collect()
        return jsonify({
            'message': f'Reanalyse terminee pour le poste "{poste}"',
            'poste': poste,
            'statut': 'actif',
            'reanalyzed_count': reanalyzed_count,
            'total_candidates': len(candidates_with_cv),
            'workers_used': MAX_WORKERS,
            'elapsed_seconds': round(elapsed, 1),
            'errors': errors[:10]
        }), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/cleanup-closed', methods=['POST'])
@jwt_required()
def cleanup_closed_statuses():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('token, poste, analyse_status').execute()
    fixed = 0
    for row in (response.data or []):
        if row.get('poste') in POSTES_CLOTURES and row.get('analyse_status') in ('reanalyzing', 'pending'):
            supabase.table('candidats').update({"analyse_status": "completed"}).eq('token', row['token']).execute()
            fixed += 1
    return jsonify({
        'message': f'{fixed} dossier(s) de postes clotures stabilises (scores conserves)',
        'fixed': fixed,
        'postes_concernes': POSTES_CLOTURES
    }), 200

@app.route('/api/recruteur/export/<fmt>', methods=['GET'])
@jwt_required()
def export_candidates(fmt):
    try:
        poste_filter = request.args.get('poste', '')
        statut_filter = request.args.get('statut', '')
        date_start = request.args.get('date_start', '')
        date_end = request.args.get('date_end', '')
        min_score = request.args.get('min_score', type=int)
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        result = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            if statut_filter:
                statut = c.get('statut', 'en_attente')
                if statut != statut_filter:
                    continue
            if date_start and c.get('date_candidature'):
                cand_date = c.get('date_candidature', '').split('T')[0]
                if cand_date < date_start:
                    continue
            if date_end and c.get('date_candidature'):
                cand_date = c.get('date_candidature', '').split('T')[0]
                if cand_date > date_end:
                    continue
            if min_score is not None and int(c.get('score', 0)) < min_score:
                continue
            for field in ['score_breakdown', 'flags_eliminatoires', 'analyse_details']:
                if c.get(field):
                    try:
                        c[f'{field}_parsed'] = json.loads(c[field])
                    except Exception:
                        pass
            result.append(c)
        result = sort_candidats(result)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else "_global"
        filename_base = f"rapport{poste_suffix}_{ts}"
        if fmt.lower() == 'excel' or fmt.lower() == 'xlsx':
            buf = generate_excel_report_enhanced(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation Excel'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'{filename_base}.xlsx')
        elif fmt.lower() == 'pdf':
            buf = generate_pdf_report_enhanced(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation PDF'}), 500
            return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{filename_base}.pdf')
        elif fmt.lower() == 'csv':
            csv_data = generate_csv_report_enhanced(result, poste_filter=poste_filter)
            return send_file(io.BytesIO(csv_data.encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=f'{filename_base}.csv')
        elif fmt.lower() in ('word', 'docx'):
            buf = generate_word_report_enhanced(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation Word'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', as_attachment=True, download_name=f'{filename_base}.docx')
        return jsonify({'error': 'Format non supporte. Utilisez: csv, excel, pdf ou word'}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats/<token>', methods=['DELETE'])
@jwt_required()
def delete_candidat(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    files_to_delete = []
    if data.get('cv_filename'):
        files_to_delete.append(data.get('cv_filename'))
    if data.get('lettre_filename'):
        files_to_delete.append(data.get('lettre_filename'))
    try:
        attestations = json.loads(data.get('attestation_filenames', '[]'))
        for att in attestations:
            if att:
                files_to_delete.append(att)
    except:
        pass
    deleted_files = []
    failed_files = []
    for filename in files_to_delete:
        try:
            supabase.storage.from_(SUPABASE_STORAGE_BUCKET).remove([filename])
            deleted_files.append(filename)
            logger.info(f"Fichier supprime: {filename}")
        except Exception as e:
            logger.error(f"Erreur suppression fichier {filename}: {e}")
            failed_files.append(filename)
    try:
        supabase.table('candidats').delete().eq('token', token).execute()
        logger.info(f"Candidat {token} supprime avec succes")
    except Exception as e:
        logger.error(f"Erreur suppression candidat {token}: {e}")
        return jsonify({'error': 'Erreur lors de la suppression du candidat', 'details': str(e)}), 500
    return jsonify({
        'message': 'Candidat supprime avec succes',
        'token': token,
        'files_deleted': deleted_files,
        'files_failed': failed_files,
        'candidat': {
            'nom': data.get('nom'),
            'prenom': data.get('prenom'),
            'email': data.get('email'),
            'poste': data.get('poste')
        }
    }), 200

@app.route('/api/recruteur/dossiers/zip/start', methods=['GET'])
@jwt_required()
def start_zip_export():
    _cleanup_old_zip_jobs()
    poste_filter = request.args.get('poste', '')
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    job_id = uuid.uuid4().hex
    with _ZIP_JOBS_LOCK:
        _ZIP_JOBS[job_id] = {
            'status': 'pending',
            'created_at': time.time(),
            'progress': 0,
            'total': 0,
            'filepath': None,
            'filename': None,
            'error': None
        }
    threading.Thread(target=_run_zip_export_job, args=(job_id, poste_filter, date_start, date_end), daemon=True).start()
    return jsonify({'job_id': job_id}), 202

@app.route('/api/recruteur/dossiers/zip/status/<job_id>', methods=['GET'])
@jwt_required()
def zip_export_status(job_id):
    with _ZIP_JOBS_LOCK:
        job = _ZIP_JOBS.get(job_id)
        if not job:
            return jsonify({'error': 'Job introuvable ou expire'}), 404
        return jsonify({
            'status': job['status'],
            'progress': job.get('progress', 0),
            'total': job.get('total', 0),
            'error': job.get('error')
        }), 200

@app.route('/api/recruteur/dossiers/zip/download/<job_id>', methods=['GET'])
@jwt_required()
def zip_export_download(job_id):
    with _ZIP_JOBS_LOCK:
        job = _ZIP_JOBS.get(job_id)
        if not job:
            return jsonify({'error': 'Job introuvable ou expire'}), 404
        if job['status'] == 'error':
            return jsonify({'error': job.get('error', 'Erreur inconnue')}), 500
        if job['status'] != 'done':
            return jsonify({'error': 'Export pas encore termine', 'status': job['status']}), 425
        filepath = job.get('filepath')
        if not filepath or not os.path.exists(filepath):
            return jsonify({'error': 'Fichier expire ou deja supprime'}), 410
        response_obj = send_file(filepath, mimetype='application/zip', as_attachment=True, download_name=job['filename'])
        @response_obj.call_on_close
        def _cleanup_after_send():
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.warning(f"Nettoyage fichier temporaire ZIP (job {job_id}) echoue: {e}")
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS.pop(job_id, None)
        return response_obj
def _run_zip_export_job(job_id, poste_filter, date_start, date_end):
    tmp_zip_path = None
    start_time = time.time()
    try:
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'processing'
            logger.info(f"[job {job_id}] Debut export ZIP optimise - {datetime.datetime.now()}")
        if not supabase:
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Supabase non configure'
            return
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        candidats = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            date_cand = c.get('date_candidature', '')
            if date_cand:
                date_only = date_cand.split('T')[0] if 'T' in date_cand else date_cand[:10]
                if date_start and date_only < date_start:
                    continue
                if date_end and date_only > date_end:
                    continue
            candidats.append(c)
        if not candidats:
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Aucun dossier a exporter'
            return
        download_tasks = []
        candidats_meta = {}
        for cand in candidats:
            poste_nom = cand.get('poste', 'Poste_Inconnu')
            poste_nom_clean = re.sub(r'[<>:"/\\|?*]', '_', poste_nom)
            num_dossier = cand.get('numero_dossier', '') or f"candidat_{cand['id'][:8]}"
            nom_candidat = cand.get('nom', 'N/A').upper()
            prenom_candidat = cand.get('prenom', 'N/A')
            dossier_candidat_nom = f"{num_dossier} - {nom_candidat} {prenom_candidat}"
            dossier_candidat_nom = re.sub(r'[<>:"/\\|?*]', '_', dossier_candidat_nom)
            dossier_parent = f"{poste_nom_clean}/{dossier_candidat_nom}"
            candidats_meta[cand['id']] = {
                'dossier_parent': dossier_parent,
                'num_dossier': num_dossier,
                'cand': cand,
                'files_written': 0
            }
            cv_file = cand.get('cv_filename', '')
            if cv_file:
                download_tasks.append((cand['id'], cv_file, dossier_parent, 'CV'))
            lettre_file = cand.get('lettre_filename', '')
            if lettre_file:
                download_tasks.append((cand['id'], lettre_file, dossier_parent, 'Lettre_de_motivation'))
            att_raw = cand.get('attestation_filenames', '[]')
            try:
                att_files = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                for idx, att_file in enumerate(att_files, 1):
                    if att_file:
                        download_tasks.append((cand['id'], att_file, dossier_parent, f'Attestation_{idx}'))
            except Exception:
                pass
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['total'] = len(download_tasks)
            logger.info(f"[job {job_id}] {len(download_tasks)} fichiers a telecharger")
        def _download_one(task):
            cand_id, blob_name, dossier_parent, prefix = task
            file_bytes = download_file_from_supabase_robust(blob_name)
            return (cand_id, blob_name, dossier_parent, prefix, file_bytes)
        tmp_fd = tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir='/tmp')
        tmp_zip_path = tmp_fd.name
        tmp_fd.close()
        files_added = 0
        max_workers = min(_ZIP_MAX_WORKERS, max(1, len(download_tasks)))
        with zipfile.ZipFile(tmp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if download_tasks:
                BATCH_SIZE = 50
                total_batches = (len(download_tasks) + BATCH_SIZE - 1) // BATCH_SIZE
                for batch_idx in range(0, len(download_tasks), BATCH_SIZE):
                    batch = download_tasks[batch_idx:batch_idx + BATCH_SIZE]
                    logger.info(f"[job {job_id}] Traitement du lot {batch_idx//BATCH_SIZE + 1}/{total_batches} ({len(batch)} fichiers)")
                    with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as executor:
                        futures = [executor.submit(_download_one, t) for t in batch]
                        for future in as_completed(futures):
                            try:
                                cand_id, blob_name, dossier_parent, prefix, file_bytes = future.result(timeout=60)
                            except Exception as e:
                                logger.error(f"[job {job_id}] Erreur telechargement: {e}")
                                continue
                            if file_bytes:
                                ext = blob_name.rsplit('.', 1)[-1].lower() if '.' in blob_name else ''
                                archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                                try:
                                    zip_file.writestr(archive_name, file_bytes)
                                    files_added += 1
                                    if cand_id in candidats_meta:
                                        candidats_meta[cand_id]['files_written'] += 1
                                except Exception as e:
                                    logger.error(f"[job {job_id}] Erreur ecriture ZIP: {e}")
                                finally:
                                    del file_bytes
                            with _ZIP_JOBS_LOCK:
                                _ZIP_JOBS[job_id]['progress'] = _ZIP_JOBS[job_id].get('progress', 0) + 1
                            gc.collect()
            for cand_id, meta in candidats_meta.items():
                if meta['files_written'] > 0:
                    continue
                cand = meta['cand']
                info_content = f"""Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}
Poste: {cand.get('poste', 'N/A')}
Numero dossier: {meta['num_dossier']}
Email: {cand.get('email', 'N/A')}
Telephone: {cand.get('telephone', 'N/A')}
Date candidature: {cand.get('date_candidature', 'N/A')}"""
                archive_name = f"{meta['dossier_parent']}/INFOS_CANDIDAT.txt"
                zip_file.writestr(archive_name, info_content.encode('utf-8'))
                files_added += 1
        elapsed = time.time() - start_time
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else ""
        filename = f"dossiers_candidats{poste_suffix}_{ts}.zip"
        logger.info(f"[job {job_id}] Export ZIP termine en {elapsed:.2f}s pour {len(candidats)} candidats ({files_added} fichiers)")
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'done'
            _ZIP_JOBS[job_id]['filepath'] = tmp_zip_path
            _ZIP_JOBS[job_id]['filename'] = filename
        del candidats_meta, download_tasks
        gc.collect()
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"[job {job_id}] Erreur export ZIP: {e}")
        if tmp_zip_path and os.path.exists(tmp_zip_path):
            try:
                os.remove(tmp_zip_path)
            except Exception:
                pass
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'error'
            _ZIP_JOBS[job_id]['error'] = str(e)
def _cleanup_old_zip_jobs():
    now = time.time()
    with _ZIP_JOBS_LOCK:
        stale = [jid for jid, j in _ZIP_JOBS.items() if now - j.get('created_at', now) > _ZIP_JOBS_MAX_AGE_SECONDS]
        for jid in stale:
            job = _ZIP_JOBS.pop(jid, None)
            if job and job.get('filepath') and os.path.exists(job['filepath']):
                try:
                    os.remove(job['filepath'])
                except Exception:
                    pass

def _run_zip_export_job(job_id, poste_filter, date_start, date_end):
    tmp_zip_path = None
    start_time = time.time()
    try:
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'processing'
            logger.info(f"[job {job_id}] Debut export ZIP optimise - {datetime.datetime.now()}")
        if not supabase:
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Supabase non configure'
            return
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        candidats = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            date_cand = c.get('date_candidature', '')
            if date_cand:
                date_only = date_cand.split('T')[0] if 'T' in date_cand else date_cand[:10]
                if date_start and date_only < date_start:
                    continue
                if date_end and date_only > date_end:
                    continue
            candidats.append(c)
        if not candidats:
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Aucun dossier a exporter'
            return
        download_tasks = []
        candidats_meta = {}
        for cand in candidats:
            poste_nom = cand.get('poste', 'Poste_Inconnu')
            poste_nom_clean = re.sub(r'[<>:"/\\|?*]', '_', poste_nom)
            num_dossier = cand.get('numero_dossier', '') or f"candidat_{cand['id'][:8]}"
            nom_candidat = cand.get('nom', 'N/A').upper()
            prenom_candidat = cand.get('prenom', 'N/A')
            dossier_candidat_nom = f"{num_dossier} - {nom_candidat} {prenom_candidat}"
            dossier_candidat_nom = re.sub(r'[<>:"/\\|?*]', '_', dossier_candidat_nom)
            dossier_parent = f"{poste_nom_clean}/{dossier_candidat_nom}"
            candidats_meta[cand['id']] = {
                'dossier_parent': dossier_parent,
                'num_dossier': num_dossier,
                'cand': cand,
                'files_written': 0
            }
            cv_file = cand.get('cv_filename', '')
            if cv_file:
                download_tasks.append((cand['id'], cv_file, dossier_parent, 'CV'))
            lettre_file = cand.get('lettre_filename', '')
            if lettre_file:
                download_tasks.append((cand['id'], lettre_file, dossier_parent, 'Lettre_de_motivation'))
            att_raw = cand.get('attestation_filenames', '[]')
            try:
                att_files = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                for idx, att_file in enumerate(att_files, 1):
                    if att_file:
                        download_tasks.append((cand['id'], att_file, dossier_parent, f'Attestation_{idx}'))
            except Exception:
                pass
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['total'] = len(download_tasks)
            logger.info(f"[job {job_id}] {len(download_tasks)} fichiers a telecharger")
        def _download_one(task):
            cand_id, blob_name, dossier_parent, prefix = task
            file_bytes = download_file_from_supabase_robust(blob_name)
            return (cand_id, blob_name, dossier_parent, prefix, file_bytes)
        tmp_fd = tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir='/tmp')
        tmp_zip_path = tmp_fd.name
        tmp_fd.close()
        files_added = 0
        max_workers = min(_ZIP_MAX_WORKERS, max(1, len(download_tasks)))
        with zipfile.ZipFile(tmp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if download_tasks:
                BATCH_SIZE = 50
                total_batches = (len(download_tasks) + BATCH_SIZE - 1) // BATCH_SIZE
                for batch_idx in range(0, len(download_tasks), BATCH_SIZE):
                    batch = download_tasks[batch_idx:batch_idx + BATCH_SIZE]
                    logger.info(f"[job {job_id}] Traitement du lot {batch_idx//BATCH_SIZE + 1}/{total_batches} ({len(batch)} fichiers)")
                    with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as executor:
                        futures = [executor.submit(_download_one, t) for t in batch]
                        for future in as_completed(futures):
                            try:
                                cand_id, blob_name, dossier_parent, prefix, file_bytes = future.result(timeout=60)
                            except Exception as e:
                                logger.error(f"[job {job_id}] Erreur telechargement: {e}")
                                continue
                            if file_bytes:
                                ext = blob_name.rsplit('.', 1)[-1].lower() if '.' in blob_name else ''
                                archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                                try:
                                    zip_file.writestr(archive_name, file_bytes)
                                    files_added += 1
                                    if cand_id in candidats_meta:
                                        candidats_meta[cand_id]['files_written'] += 1
                                except Exception as e:
                                    logger.error(f"[job {job_id}] Erreur ecriture ZIP: {e}")
                                finally:
                                    del file_bytes
                            with _ZIP_JOBS_LOCK:
                                _ZIP_JOBS[job_id]['progress'] = _ZIP_JOBS[job_id].get('progress', 0) + 1
                            gc.collect()
            for cand_id, meta in candidats_meta.items():
                if meta['files_written'] > 0:
                    continue
                cand = meta['cand']
                info_content = f"""Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}
Poste: {cand.get('poste', 'N/A')}
Numero dossier: {meta['num_dossier']}
Email: {cand.get('email', 'N/A')}
Telephone: {cand.get('telephone', 'N/A')}
Date candidature: {cand.get('date_candidature', 'N/A')}"""
                archive_name = f"{meta['dossier_parent']}/INFOS_CANDIDAT.txt"
                zip_file.writestr(archive_name, info_content.encode('utf-8'))
                files_added += 1
        elapsed = time.time() - start_time
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else ""
        filename = f"dossiers_candidats{poste_suffix}_{ts}.zip"
        logger.info(f"[job {job_id}] Export ZIP termine en {elapsed:.2f}s pour {len(candidats)} candidats ({files_added} fichiers)")
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'done'
            _ZIP_JOBS[job_id]['filepath'] = tmp_zip_path
            _ZIP_JOBS[job_id]['filename'] = filename
        del candidats_meta, download_tasks
        gc.collect()
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"[job {job_id}] Erreur export ZIP: {e}")
        if tmp_zip_path and os.path.exists(tmp_zip_path):
            try:
                os.remove(tmp_zip_path)
            except Exception:
                pass
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'error'
            _ZIP_JOBS[job_id]['error'] = str(e)

def _cleanup_old_zip_jobs():
    now = time.time()
    with _ZIP_JOBS_LOCK:
        stale = [jid for jid, j in _ZIP_JOBS.items() if now - j.get('created_at', now) > _ZIP_JOBS_MAX_AGE_SECONDS]
        for jid in stale:
            job = _ZIP_JOBS.pop(jid, None)
            if job and job.get('filepath') and os.path.exists(job['filepath']):
                try:
                    os.remove(job['filepath'])
                except Exception:
                    pass

@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    body = request.get_json(silent=True) or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))
    nom_c = f"{data.get('prenom', '')} {data.get('nom', '')}".strip()
    poste = data.get('poste', '')
    to_email = data.get('email', '')
    sign = "\nCordialement,\nL'equipe Ressources Humaines\nRecrutBank"
    if msg_type == 'retenu':
        sujet = f"Felicitations – Candidature retenue – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nNous avons le plaisir de vous informer que votre candidature pour le poste de {poste} a ete retenue.\nNous vous contacterons tres prochainement." + sign
    elif msg_type == 'entretien':
        sujet = f"Invitation a un entretien – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nSuite a l'examen de votre candidature pour le poste de {poste}, nous avons le plaisir de vous inviter a un entretien.\nNous prendrons contact avec vous pour convenir d'une date." + sign
    else:
        sujet = f"Reponse a votre candidature – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nNous vous remercions de l'interet que vous portez a notre institution.\nApres examen attentif de votre dossier pour le poste de {poste}, nous avons le regret de vous informer que votre candidature n'a pas ete retenue.\nNous vous encourageons a postuler a nouveau." + sign
    return jsonify({'to': to_email, 'nom': nom_c, 'sujet': sujet, 'corps': corps}), 200

@app.route('/api/recruteur/uploads/<path:filename>', methods=['GET'])
def serve_upload(filename):
    safe = secure_filename(filename.replace('/', '_'))
    if not safe:
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    url = get_signed_url(safe, expiration_minutes=30)
    if not url:
        return jsonify({'error': 'Fichier introuvable'}), 404
    return redirect(url)

@app.route('/api/recruteur/debug/analyse-ia', methods=['POST'])
@jwt_required()
def debug_analyse_ia():
    data = request.get_json(silent=True) or {}
    cv_text = data.get('cv_text', '')
    lettre_text = data.get('lettre_text', '')
    poste = data.get('poste', '')
    if not cv_text or poste not in GRILLE:
        return jsonify({'error': 'cv_text requis et poste doit exister dans GRILLE'}), 400
    result = analyze_cv_with_ia_only(cv_text, lettre_text, [], poste)
    if not result:
        return jsonify({'error': "L'analyse IA a echoue"}), 500
    return jsonify(result), 200

@app.route('/api/test-email', methods=['GET'])
def test_email():
    try:
        to = request.args.get('to', '')
        if not to:
            return jsonify({'error': 'Parametre ?to= requis'}), 400
        ok = send_email(to, 'Test RecrutBank', f'Ceci est un email de test depuis RecrutBank avec {len(ACTIVE_MODELS)} modele(s) IA.')
        return jsonify({'sent': ok}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

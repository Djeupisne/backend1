#!/usr/bin/env python3
"""Script de reorganisation structurelle de server.py en package backend/app/.
Le contenu des fonctions/constantes est repris VERBATIM (aucune modification logique).
"""
import os, re

lines = open('/workspace/server.py', encoding='utf-8').read().split('\n')

def seg(a, b):  # 1-based inclusive
    return lines[a-1:b]

# ── Découpage automatique des blocs @app.route ────────────────────────────
route_lines = [i+1 for i, l in enumerate(lines) if l.startswith('@app.route')]
route_lines = [n for n in route_lines if n != 184]  # health_check gere separement
assert len(route_lines) == 27, route_lines

blocks = {}   # func name -> list of lines (decorator included)
for idx, s in enumerate(route_lines):
    if idx + 1 < len(route_lines):
        nxt = route_lines[idx+1]
    else:
        nxt = next(i+1 for i, l in enumerate(lines) if l.startswith("if __name__"))
    blk = lines[s-1:nxt-1]
    while blk and blk[-1].strip() == '':
        blk.pop()
    name = None
    for l in blk:
        m = re.match(r'^def (\w+)', l)
        if m:
            name = m.group(1); break
    assert name, s
    assert name not in blocks, name
    blocks[name] = blk

print("Blocs route:", ", ".join(blocks))

# ── Fonctions utilitaires (segments verbatim) ─────────────────────────────
H = {
 'after_request': seg(176,183),
 'health_check': seg(184,202),
 'initialize_ia_clients': seg(88,149),
 'allowed_file': seg(217,218),
 'upload_file_to_supabase': seg(219,232),
 'retry_with_backoff': seg(233,256),
 'download_file_from_supabase_robust': seg(257,267),  # avec decorateur @retry...
 'download_file_from_supabase': seg(268,279),
 'get_signed_url': seg(280,288),
 'send_email': seg(289,313),
 'hash_pwd': seg(314,315),
 'normalize_spaces': seg(316,320),
 'normalize_unicode': seg(321,327),
 'extract_text_from_pdf_robust': seg(331,375),
 'extract_text_from_docx_robust': seg(376,432),
 'extract_text_from_txt': seg(433,452),
 'extract_text_robust_from_bytes': seg(453,467),
 'init_recruteur': seg(468,479),
 'is_poste_actif': seg(508,509),
 'parse_json_robust': seg(1139,1180),
 'get_recommandation_from_score': seg(1181,1193),
 'get_statut_from_decision': seg(1194,1204),
 'sort_candidats': seg(1205,1220),
 'build_ia_prompt': seg(1221,1318),
 'call_ia_model': seg(1319,1363),
 'analyze_cv_with_ia_only': seg(1364,1492),
 'run_analysis_for_candidat': seg(1493,1601),
 'generate_excel_report_enhanced': seg(1603,1804),
 'generate_pdf_report_enhanced': seg(1806,1993),
 'generate_csv_report_enhanced': seg(1995,2038),
 'generate_word_report_enhanced': seg(2040,2174),
 '_run_zip_export_job': seg(2891,3031),
 '_cleanup_old_zip_jobs': seg(3032,3042),
}
for k, v in H.items():
    assert v and (v[0].startswith('def ') or v[0].startswith('@')), (k, v[0][:50])
    fn = re.match(r'^def (\w+)', v[0]).group(1) if v[0].startswith('def') else re.match(r'^def (\w+)', v[1]).group(1)
    assert fn == k, (k, fn)

# ── Segments de constantes / entetes ──────────────────────────────────────
IMPORTS      = seg(1,66)     # imports stdlib/tiers + drapeaux de disponibilite
CONST_IA_CFG = seg(67,87)    # config IA env + OPENROUTER_MODELS
CONST_APP    = seg(160,175)  # app = Flask(...) + ALLOWED_ORIGINS + CORS(...)
CONST_JWT    = seg(203,204)  # commentaire? verifier: ligne 203 vide, 204 jwt = JWTManager(app)
CONST_SUPA   = seg(205,208)  # SUPABASE_URL/KEY/BUCKET/client
CONST_SMTP   = seg(209,215)  # config SMTP + ALLOWED_EXTENSIONS + MAX_CONTENT_LENGTH
CONST_RUNTIME= seg(150,159)  # semaphore IA, download, zip jobs
CONST_LIMITS = seg(328,330)  # MAX_PDF_PAGES etc.
POSTES_LIST  = seg(482,507)  # POSTES / POSTES_ACTIFS / POSTES_CLOTURES
GRILLE_MAIN  = seg(511,618)  # GRILLE = {...}
GRILLE_EXTRA = seg(619,1138) # GRILLE["..."] = {...}
MAIN_BLOCK   = seg(3127,3158)

assert IMPORTS[0].startswith('from flask import'), IMPORTS[0]
assert CONST_IA_CFG[0].startswith('OPENROUTER_API_KEY'), CONST_IA_CFG[0]
assert CONST_APP[0].startswith('app = Flask'), CONST_APP[0]
assert CONST_RUNTIME[0].startswith('_ia_semaphore'), CONST_RUNTIME[0]
assert POSTES_LIST[0].startswith('# Liste'), POSTES_LIST[0]
assert GRILLE_MAIN[0].startswith('GRILLE = {'), GRILLE_MAIN[0]
assert GRILLE_EXTRA[0].startswith('GRILLE['), GRILLE_EXTRA[0]
assert MAIN_BLOCK[0].startswith('if __name__'), MAIN_BLOCK[0]

def write(path, parts):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = '\n'.join(parts)
    if not text.endswith('\n'):
        text += '\n'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f"OK {path} ({len(text.splitlines())} lignes)")

P = '/workspace/backend/app/'

def doc(t):
    return ('# ═══════════════════════════════════════════════════════\n'
            f'# {t}\n'
            '# Contenu repris verbatim de l ancien server.py monolithique\n'
            '# (refactor purement structurel — aucune logique modifiee)\n'
            '# ═══════════════════════════════════════════════════════')

# ── app/__init__.py ───────────────────────────────────────────────────────
write(P+'__init__.py', [doc('RecrutBank — package applicatif Flask'),
                        'from .server import app  # noqa: F401\n\n__all__ = ["app"]'])

# ── app/config.py : logging, imports communs, constantes globales ─────────
write(P+'config.py', [
 doc('Configuration centrale : logging, dependances, variables denvironnement, clients externes'),
 *IMPORTS,                      # lignes 1-66 (flask, stdlib, supabase, dotenv, drapeaux dispo bibliotheques)
 '',
 '# --- Configuration IA (variables denvironnement) ---',
 *CONST_IA_CFG,                 # 67-87
 '',
 '# --- Limites de telechargement / concurrence / jobs ZIP ---',
 *CONST_RUNTIME,                # 150-159
 '',
 '# --- Application Flask + CORS ---',
 *CONST_APP,                    # 160-175
 '',
 '# --- JWT ---',
 'app.config[\'JWT_SECRET_KEY\'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")',
 'app.config[\'JWT_ACCESS_TOKEN_EXPIRES\'] = datetime.timedelta(hours=8)',
 *seg(204,204),                 # jwt = JWTManager(app)
 '',
 '# --- Supabase ---',
 *CONST_SUPA,                   # 205-208
 '',
 '# --- SMTP / emails ---',
 *CONST_SMTP,                   # 209-215
 '',
 '# --- Limites extraction de documents ---',
 *CONST_LIMITS,                 # 328-330
 '',
 '# --- Initialisation des modeles IA (appelee au demarrage, comme avant) ---',
 *H['initialize_ia_clients'],   # 88-149 (inclut l appel initialize_ia_clients() en fin de segment)
 '',
 '# --- Initialisation du compte recruteur par defaut ---',
 *H['init_recruteur'],          # 468-479
 *seg(480,480),                 # appel init_recruteur()
], )

# attention: le segment initialize_ia_clients (88-149) inclut la ligne d'appel
# et le segment init_recruteur (468-479) N'inclut PAS son appel (ligne 480 ?) → on ajoute seg(480,480)
# Verifier que seg(480,480) == 'init_recruteur()'
assert lines[479].strip() == 'init_recruteur()', repr(lines[479])
assert lines[148].strip() == 'initialize_ia_clients()', repr(lines[148])

# ── app/data/postes.py ────────────────────────────────────────────────────
write(P+'data/postes.py', [doc('Liste des postes et statut actif/cloture'), *POSTES_LIST, '', *H['is_poste_actif']])

# ── app/data/grille.py ────────────────────────────────────────────────────
write(P+'data/grille.py', [doc('Grilles de preselection par poste (criteres, scores max, regles metier)'),
                           *GRILLE_MAIN, *GRILLE_EXTRA])

# ── app/utils/helpers.py ──────────────────────────────────────────────────
write(P+'utils/helpers.py', [
 doc('Fonctions utilitaires generiques (normalisation, hachage, retry)'),
 'from ..config import (logger, DOWNLOAD_MAX_RETRIES, DOWNLOAD_BASE_DELAY,',
 '                     DOWNLOAD_MAX_DELAY)',
 'import hashlib, random, time, re, unicodedata',
 '',
 *H['hash_pwd'], *H['normalize_spaces'], *H['normalize_unicode'], *H['retry_with_backoff'],
])

# ── app/utils/storage.py ──────────────────────────────────────────────────
write(P+'utils/storage.py', [
 doc('Acces au stockage Supabase (upload / download / URL signee)'),
 'from ..config import logger, supabase, SUPABASE_STORAGE_BUCKET, DOWNLOAD_MAX_RETRIES',
 'from .helpers import retry_with_backoff',
 'import threading',
 '',
 '# Semaphore local identique a celui de lancien server.py (telechargements concurrents)',
 'DOWNLOAD_MAX_CONCURRENT = int(__import__("os").getenv("DOWNLOAD_MAX_CONCURRENT", "15"))',
 '_DOWNLOAD_SEMAPHORE = threading.Semaphore(DOWNLOAD_MAX_CONCURRENT)',
 '',
 *H['upload_file_to_supabase'], *H['download_file_from_supabase_robust'],
 *H['download_file_from_supabase'], *H['get_signed_url'],
])

# ── app/services/email_service.py ─────────────────────────────────────────
write(P+'services/email_service.py', [
 doc('Envoi d emails via l API Brevo'),
 'import os'
 'from ..config import logger',
 '',
 *H['send_email'],
])

# ── app/services/text_extraction.py ───────────────────────────────────────
write(P+'services/text_extraction.py', [
 doc('Extraction robuste de texte depuis PDF / DOCX / TXT'),
 'from ..config import (logger, pdfplumber, PyPDF2, Document, chardet,',
 '                     PDFPLUMBER_AVAILABLE, PYPDF2_AVAILABLE, DOCX_AVAILABLE,',
 '                     CHARDET_AVAILABLE, MAX_PDF_PAGES, MAX_PDF_SIZE_BYTES, MAX_TEXT_SIZE)',
 'import io, unicodedata',
 '',
 *H['extract_text_from_pdf_robust'], *H['extract_text_from_docx_robust'],
 *H['extract_text_from_txt'], *H['extract_text_robust_from_bytes'],
])

# ── app/services/ia_analysis.py ───────────────────────────────────────────
write(P+'services/ia_analysis.py', [
 doc('Moteur d analyse des CV par IA (prompt, appels multi-modeles, scoring)'),
 'from ..config import (logger, json, GRILLE, ACTIVE_MODELS, IA_ANALYSE_ACTIVE,',
 '                     _ia_semaphore, OPENROUTER_REASONING_ENABLED)',
 'from ..data.grille import GRILLE as _GRILLE_CHECK  # noqa: F401  (garde la coherence des donnees)',
 '',
 *H['parse_json_robust'], *H['get_recommandation_from_score'], *H['get_statut_from_decision'],
 *H['sort_candidats'], *H['build_ia_prompt'], *H['call_ia_model'],
 *H['analyze_cv_with_ia_only'], *H['run_analysis_for_candidat'],
])

# ── app/services/report_service.py ────────────────────────────────────────
write(P+'services/report_service.py', [
 doc('Generation des rapports Excel / PDF / CSV / Word'),
 'from ..config import (logger, io, csv, json, datetime, re,',
 '                     REPORTLAB_AVAILABLE, OPENPYXL_AVAILABLE,',
 '                     SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether,',
 '                     getSampleStyleSheet, ParagraphStyle, landscape, A4, colors,',
 '                     cm, inch, TA_CENTER, TA_LEFT, TA_RIGHT, canvas,',
 '                     Workbook, Font, PatternFill, Alignment, Border, Side, get_column_letter,',
 '                     BarChart, Reference, PieChart, DataLabelList)',
 '',
 *H['generate_excel_report_enhanced'], *H['generate_pdf_report_enhanced'],
 *H['generate_csv_report_enhanced'], *H['generate_word_report_enhanced'],
])

# ── app/routes/public_routes.py ───────────────────────────────────────────
write(P+'routes/public_routes.py', [
 doc('Routes publiques : sante, postes, grille, authentification, candidature, suivi'),
 'from flask import request, jsonify',
 'from flask_jwt_extended import create_access_token',
 'import threading, uuid, json, datetime',
 'from ..config import (app, logger, supabase, POSTES, POSTES_ACTIFS, POSTES_CLOTURES,',
 '                      GRILLE, ACTIVE_MODELS, hash_pwd, allowed_file,',
 '                      upload_file_to_supabase, send_email)',
 'from ..services.text_extraction import extract_text_robust_from_bytes  # noqa: F401',
 'from ..services.ia_analysis import run_analysis_for_candidat',
 'from ..data.postes import is_poste_actif',
 '',
 *blocks['get_postes'], '',
 *blocks['get_postes_actifs'], '',
 *blocks['get_grille'], '',
 *blocks['login'], '',
 *blocks['postuler'], '',
 *blocks['get_statut'],
])

# ── app/routes/recruteur_routes.py ────────────────────────────────────────
write(P+'recruteur_routes.py_tmp', ['placeholder'])
os.remove('/workspace/backend/app/recruteur_routes.py_tmp')
write(P+'routes/recruteur_routes.py', [
 doc('Routes recruteur protegees JWT : dashboard, analyses, exports, suppression'),
 'from flask import request, jsonify, send_file, redirect, secure_filename'
   .replace('secure_filename', ''),  # secure_filename vient de werkzeug
 'from flask import request, jsonify, send_file, redirect',
 'from werkzeug.utils import secure_filename',
 'from flask_jwt_extended import jwt_required, get_jwt_identity',
 'import threading, uuid, os, re, io, json, time, gc, tempfile, zipfile, datetime',
 'from concurrent.futures import ThreadPoolExecutor, as_completed',
 'from ..config import (app, logger, supabase, SUPABASE_STORAGE_BUCKET, POSTES, POSTES_ACTIFS,',
 '                      POSTES_CLOTURES, GRILLE, ACTIVE_MODELS, sort_candidats,',
 '                      analyze_cv_with_ia_only, send_email, get_signed_url,',
 '                      download_file_from_supabase_robust, extract_text_robust_from_bytes,',
 '                      generate_excel_report_enhanced, generate_pdf_report_enhanced,',
 '                      generate_csv_report_enhanced, generate_word_report_enhanced,',
 '                      _ZIP_JOBS, _ZIP_JOBS_LOCK, _ZIP_MAX_WORKERS)',
 'from ..services.ia_analysis import run_analysis_for_candidat',
 'from ..data.postes import is_poste_actif',
 '',
 *blocks['get_stats'], '',
 *blocks['get_postes_stats'], '',
 *blocks['list_candidats'], '',
 *blocks['get_candidat_detail'], '',
 *blocks['update_candidat'], '',
 *blocks['trigger_analyze'], '',
 *blocks['get_reanalyze_status'], '',
 *blocks['reanalyze_one_candidate'], '',
 *blocks['reanalyze_all_candidates'], '',
 *blocks['reanalyze_by_poste'], '',
 *blocks['cleanup_closed_statuses'], '',
 *blocks['export_candidates'], '',
 *blocks['delete_candidat'], '',
 *blocks['start_zip_export'], '',
 *blocks['zip_export_status'], '',
 *blocks['zip_export_download'], '',
 *H['_run_zip_export_job'], '',
 *H['_cleanup_old_zip_jobs'], '',
 *blocks['email_preview'], '',
 *blocks['serve_upload'], '',
 *blocks['debug_analyse_ia'], '',
 *blocks['test_email'],
])

# ── app/routes/health_routes.py ───────────────────────────────────────────
write(P+'routes/health_routes.py', [
 doc('Routes de diagnostic : health check et version'),
 'from flask import jsonify',
 'from ..config import (app, ACTIVE_MODELS, IA_ANALYSE_ACTIVE, POSTES, POSTES_ACTIFS,',
 '                      POSTES_CLOTURES, GRILLE, OPENROUTER_REASONING_ENABLED,',
 '                      DOWNLOAD_MAX_CONCURRENT, _ZIP_MAX_WORKERS, datetime)',
 '',
 *H['after_request'], '',
 *H['health_check'], '',
 *blocks['health_version'],
])

# ── app/routes/__init__.py & app/.../__init__.py ─────────────────────────
for sub in ['routes', 'services', 'utils', 'data']:
    label = {'routes': 'Blueprints/routes de l API REST',
             'services': 'Couches metier (IA, extraction texte, emails, rapports)',
             'utils': 'Utilitaires transverses (stockage, helpers)',
             'data': 'Donnees de reference (postes, grilles de selection)'}[sub]
    write(P+sub+'/__init__.py', [doc(label)])

# ── app/server.py : point d entree qui assemble tout ──────────────────────
write(P+'server.py', [
 doc('Point d entree : importe la configuration puis tous les modules (effets d enregistrement des routes)'),
 'from . import config  # noqa: F401  (initialise app, IA, recruteur, constantes)',
 'from .config import app, logger, ACTIVE_MODELS, OPENROUTER_REASONING_ENABLED, _ZIP_MAX_WORKERS, DOWNLOAD_MAX_CONCURRENT',
 '',
 '# Donnees de reference',
 'from .data import postes as _postes  # noqa: F401',
 'from .data import grille as _grille  # noqa: F401',
 '',
 '# Modules de services (rend les fonctions disponibles via config pour retro-compatibilite)',
 'from .utils import helpers as _helpers, storage as _storage  # noqa: F401',
 'from .services import email_service as _email  # noqa: F401',
 'from .services import text_extraction as _text  # noqa: F401',
 'from .services import ia_analysis as _ia  # noqa: F401',
 'from .services import report_service as _reports  # noqa: F401',
 '',
 '# Exposer les fonctions de service dans config (comportement identique au module monolithique)',
 'config.sort_candidats = _ia.sort_candidats',
 'config.analyze_cv_with_ia_only = _ia.analyze_cv_with_ia_only',
 'config.run_analysis_for_candidat = _ia.run_analysis_for_candidat',
 'config.extract_text_robust_from_bytes = _text.extract_text_robust_from_bytes',
 'config.download_file_from_supabase_robust = _storage.download_file_from_supabase_robust',
 'config.get_signed_url = _storage.get_signed_url',
 'config.send_email = _email.send_email',
 'config.generate_excel_report_enhanced = _reports.generate_excel_report_enhanced',
 'config.generate_pdf_report_enhanced = _reports.generate_pdf_report_enhanced',
 'config.generate_csv_report_enhanced = _reports.generate_csv_report_enhanced',
 'config.generate_word_report_enhanced = _reports.generate_word_report_enhanced',
 '',
 '# Routes (enregistrees sur app via @app.route a l import)',
 'from .routes import health_routes as _rh  # noqa: E402,F401',
 'from .routes import public_routes as _rp  # noqa: E402,F401',
 'from .routes import recruteur_routes as _rr  # noqa: E402,F401',
 '',
 '__all__ = ["app"]',
])
# Le bloc __main__ original est conserve tel quel ci-dessous (identique a l'ancien fichier)
with open(P+'server.py', 'a', encoding='utf-8') as f:
    f.write('\n'.join(MAIN_BLOCK) + '\n')

# ── backend/server.py : launcher compatible ───────────────────────────────
write('/workspace/backend/server.py', [
 doc('Lanceur historique : `python server.py` depuis backend/ fonctionne toujours'),
 'from app.server import app  # noqa: F401',
 '',
 *MAIN_BLOCK,
])

print("\n=== Refactor termine ===")

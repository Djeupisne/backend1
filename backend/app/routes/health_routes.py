# ═══════════════════════════════════════════════════════
# Routes de diagnostic : health check et version
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from flask import jsonify, request
import os
from ..config import (app, ACTIVE_MODELS, IA_ANALYSE_ACTIVE, POSTES, POSTES_ACTIFS,
                      POSTES_CLOTURES, GRILLE, OPENROUTER_REASONING_ENABLED,
                      DOWNLOAD_MAX_CONCURRENT, _ZIP_MAX_WORKERS, datetime)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS,PUT,DELETE')
    response.headers.add('Access-Control-Max-Age', '600')
    if request.method == 'OPTIONS':
        response.status_code = 204
    return response

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    active_models = [{"name": m["name"], "model": m["model"], "provider": m["provider"], "supports_reasoning": m.get("supports_reasoning", False)} for m in ACTIVE_MODELS]
    return jsonify({
        'status': 'ok',
        'message': f'RecrutBank API with {len(ACTIVE_MODELS)} IA model(s)',
        'version': 'v13.2-rapports-ameliiores',
        'features': {
            'ia_available': IA_ANALYSE_ACTIVE,
            'ia_models': active_models,
            'active_models_count': len(ACTIVE_MODELS),
            'analysis_method': '100% IA avec fallback multi-modeles',
            'scoring_max': 14,
            'postes_actifs': [],
            'postes_clotures': ["Chef de Division Local Corporate", "Data Analyst Finance", "Responsable Administration de Credit", "Analyste Credit CCB", "Archiviste (Administration Credit)", "Senior Finance Officer", "Market Risk Officer", "IT Reseau & Infrastructure", "Auditeur interne", "Chef service controle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marche", "Chef service reporting reglementaire", "Chef de Section Compensation", "Charge(e) d'Administration de Credit"],
            'version': 'v13.2-rapports-ameliiores'
        }
    }), 200
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")

@app.route('/api/health-version', methods=['GET'])
def health_version():
    active_models = [{"name": m["name"], "model": m["model"], "supports_reasoning": m.get("supports_reasoning", False)} for m in ACTIVE_MODELS]
    return jsonify({
        "version": "v13.2-rapports-ameliiores",
        "postes_actifs": POSTES_ACTIFS,
        "postes_count": len(POSTES),
        "analysis_method": "100% IA avec fallback multi-modeles",
        "active_models": active_models,
        "active_models_count": len(ACTIVE_MODELS),
        "max_concurrent_downloads": DOWNLOAD_MAX_CONCURRENT,
        "zip_max_workers": _ZIP_MAX_WORKERS,
        "ia_provider": "OpenRouter",
        "reasoning_enabled": OPENROUTER_REASONING_ENABLED,
        "json_robust_parsing": True,
        "sous_scores_complets": True,
        "eliminatoire_rejet": True,
        "score_conserve": True,
        "score_somme_sous_scores": True,
        "business_rules_stable": True,
        "grilles_disponibles": list(GRILLE.keys()),
        "scoring_max_chef_division": 14,
        "scoring_max_data_analyst": 14,
        "seuils": {"prioritaire": 11, "potentiel_min": 7, "rejet_max": 6},
        "deployed_at": datetime.datetime.now().isoformat(),
        "version_info": "v13.2-rapports-ameliiores - Multi-modeles IA avec rapports améliorés"
    }), 200

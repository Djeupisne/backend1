# ═══════════════════════════════════════════════════════
# Point d entree : importe la configuration puis tous les modules (effets d enregistrement des routes)
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from . import config  # noqa: F401  (initialise app, IA, recruteur, constantes)
from .config import app, logger, ACTIVE_MODELS, OPENROUTER_REASONING_ENABLED, _ZIP_MAX_WORKERS, DOWNLOAD_MAX_CONCURRENT

# Donnees de reference
from .data import postes as _postes  # noqa: F401
from .data import grille as _grille  # noqa: F401

# Modules de services (rend les fonctions disponibles via config pour retro-compatibilite)
from .utils import helpers as _helpers, storage as _storage  # noqa: F401
from .services import email_service as _email  # noqa: F401
from .services import text_extraction as _text  # noqa: F401
from .services import ia_analysis as _ia  # noqa: F401
from .services import report_service as _reports  # noqa: F401

# Exposer les fonctions de service dans config (comportement identique au module monolithique)
config.sort_candidats = _ia.sort_candidats
config.analyze_cv_with_ia_only = _ia.analyze_cv_with_ia_only
config.run_analysis_for_candidat = _ia.run_analysis_for_candidat
config.extract_text_robust_from_bytes = _text.extract_text_robust_from_bytes
config.download_file_from_supabase_robust = _storage.download_file_from_supabase_robust
config.get_signed_url = _storage.get_signed_url
config.send_email = _email.send_email
config.generate_excel_report_enhanced = _reports.generate_excel_report_enhanced
config.generate_pdf_report_enhanced = _reports.generate_pdf_report_enhanced
config.generate_csv_report_enhanced = _reports.generate_csv_report_enhanced
config.generate_word_report_enhanced = _reports.generate_word_report_enhanced

# Routes (enregistrees sur app via @app.route a l import)
from .routes import health_routes as _rh  # noqa: E402,F401
from .routes import public_routes as _rp  # noqa: E402,F401
from .routes import recruteur_routes as _rr  # noqa: E402,F401

__all__ = ["app"]
if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    import multiprocessing
    cpu_count = multiprocessing.cpu_count()
    suggested_workers = min(4, cpu_count * 2)
    logger.info("=" * 60)
    logger.info("🚀 RecrutBank API v13.2 - Rapports améliorés")
    logger.info("=" * 60)
    logger.info(f"Port: {port}")
    logger.info(f"Workers suggeres: {suggested_workers}")
    if ACTIVE_MODELS:
        logger.info(f"✅ {len(ACTIVE_MODELS)} modele(s) IA actif(s):")
        for m in ACTIVE_MODELS:
            supports = "🧠" if m.get("supports_reasoning", False) and OPENROUTER_REASONING_ENABLED else "📝"
            logger.info(f"   {supports} {m['name']} ({m['model']})")
        logger.info(f"Reasoning: {'✅ Active' if OPENROUTER_REASONING_ENABLED else '❌ Desactive'}")
        logger.info(f"Analyse: 100% IA avec fallback automatique")
        logger.info(f"POSTES ACTIFS: {POSTES_ACTIFS}")
        logger.info(f"POSTES CLOTURES: {POSTES_CLOTURES}")
        logger.info(f"GRILLES DISPONIBLES: {len(GRILLE)} postes")
        logger.info(f"Concurrence IA max: {os.getenv('IA_MAX_CONCURRENCY', '5')}")
    else:
        logger.warning("⚠️ AUCUN MODELE IA DISPONIBLE - Le systeme ne peut pas fonctionner")
        logger.warning("   Veuillez configurer OPENROUTER_API_KEY")
    logger.info(f"Telechargements concurrents: {DOWNLOAD_MAX_CONCURRENT}")
    logger.info(f"Workers ZIP max: {_ZIP_MAX_WORKERS}")
    logger.info("=" * 60)
    try:
        import gunicorn
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

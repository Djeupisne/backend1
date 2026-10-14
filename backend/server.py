# ═══════════════════════════════════════════════════════
# Lanceur historique : `python server.py` depuis backend/ fonctionne toujours
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
import os  # noqa: F401
import logging  # noqa: F401
from app.config import logger, ACTIVE_MODELS, OPENROUTER_REASONING_ENABLED, POSTES_ACTIFS, POSTES_CLOTURES, DOWNLOAD_MAX_CONCURRENT, _ZIP_MAX_WORKERS  # noqa: F401
from app.data.grille import GRILLE  # noqa: F401
from app.server import app  # noqa: F401

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

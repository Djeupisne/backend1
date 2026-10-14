# ═══════════════════════════════════════════════════════
# RecrutBank — package applicatif Flask
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from .server import app  # noqa: F401

__all__ = ["app"]

# ═══════════════════════════════════════════════════════
# Liste des postes et statut actif/cloture
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
# Liste de tous les postes
POSTES = [
    "Chef de Division Local Corporate",
    "Data Analyst Finance",
    "Responsable Administration de Credit",
    "Analyste Credit CCB",
    "Archiviste (Administration Credit)",
    "Senior Finance Officer",
    "Market Risk Officer",
    "IT Reseau & Infrastructure",
    "Auditeur interne",
    "Chef service controle des engagements",
    "Chef service IT (maintenance/support)",
    "Chef service finance",
    "Chef service risques de marche",
    "Chef service reporting reglementaire",
    "Chef de Section Compensation",
    "Charge(e) d'Administration de Credit"
]

# TOUS LES POSTES SONT CLOTURES
POSTES_ACTIFS = []

# TOUS LES POSTES SONT CLOTURES
POSTES_CLOTURES = POSTES  # Tous les postes sont clôturés


def is_poste_actif(poste):
    return poste in POSTES_ACTIFS  # Retourne toujours False car POSTES_ACTIFS est vide

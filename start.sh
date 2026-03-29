#!/bin/bash
# ═══════════════════════════════════════════════════════
#  RecrutBank – Démarrage du serveur
# ═══════════════════════════════════════════════════════

echo ""
echo "  🏦  RecrutBank – Gestion des Candidatures"
echo "  ─────────────────────────────────────────"
echo ""

# Vérifier Python
if ! command -v python3 &> /dev/null; then
  echo "  ❌  Python 3 est requis. Installez-le d'abord."
  exit 1
fi

# Installer les dépendances Python si nécessaire
echo "  📦  Vérification des dépendances..."
pip3 install flask flask-cors flask-jwt-extended werkzeug --quiet --break-system-packages 2>/dev/null || \
pip3 install flask flask-cors flask-jwt-extended werkzeug --quiet

echo "  ✅  Dépendances OK"
echo ""
echo "  🚀  Démarrage du serveur sur http://localhost:5000"
echo ""
echo "  📄  Accueil public   : http://localhost:5000"
echo "  🔐  Espace recruteur : http://localhost:5000/login"
echo "  👤  Suivi candidat   : http://localhost:5000/dashboard-candidat"
echo ""
echo "  🔑  Identifiants recruteur :"
echo "      Email    : recruteur@banque.com"
echo "      Password : admin123"
echo ""
echo "  Appuyez sur Ctrl+C pour arrêter le serveur."
echo "  ─────────────────────────────────────────"
echo ""

cd "$(dirname "$0")/backend"
python3 server.py

# Guide de Mise en Production - RecrutBank

## 📋 Points Critiques à Surveiller

### 1. Gestion des Erreurs Utilisateur (UX)

#### Taille des Fichiers
Le code utilise `app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024` mais Flask renvoie une erreur 413 standard.

**Solution implémentée:** Module `modules/errors.py` avec:
- Gestionnaire d'erreur 413 personnalisé
- Décorateur `handle_file_size_limit()` pour validation avant traitement
- Message utilisateur clair et explicite

```python
from modules.errors import init_error_handlers, handle_file_size_limit

# Dans server.py, après la création de l'app Flask
init_error_handlers(app)

# Utilisation du décorateur sur la route postuler
@app.route('/api/candidats/postuler', methods=['POST'])
@handle_file_size_limit(max_size_mb=15)
def postuler():
    # ... code existant
```

#### Validation Email Renforcée
Le format email actuel est trop permissif (`test@com` passerait).

**Solution implémentée:** Module `modules/validation.py`:
```python
from modules.validation import validate_email

email_valid, error_msg = validate_email(email)
if not email_valid:
    return jsonify({'error': error_msg}), 400
```

**Règles de validation:**
- Format: `nom@domaine.ext`
- Domaine doit contenir un point
- TLD minimum 2 caractères
- Longueur max 254 caractères

---

### 2. Sécurité des Fichiers (CSV Injection)

#### Problème
Si un candidat s'appelle `=cmd|' /C calc'!A0`, Excel peut exécuter cette formule lors de l'ouverture du CSV.

**Solution implémentée:** Module `modules/export.py`:
```python
from modules.export import sanitize_csv_field, generate_csv_report

# Sanitization automatique dans generate_csv_report()
# Tous les champs texte sont préfixés par ' si nécessaire
```

**Caractères dangereux détectés:** `=`, `+`, `-`, `@`

---

### 3. Surcharge Mémoire

#### Problème
`download_file_from_supabase` charge l'intégralité du fichier en mémoire. Avec des fichiers de 15Mo en parallèle, la RAM peut atteindre 200-300Mo.

**Solution implémentée:** Module `modules/storage.py`:
```python
from modules.storage import download_file_from_supabase_streaming

# Pour les gros fichiers (>15Mo)
file_stream = download_file_from_supabase_streaming(blob_name)
```

**Recommandations:**
- Actuellement OK pour 15Mo
- Pour fichiers >50Mo: utiliser le mode streaming
- Limiter le nombre de threads simultanés (`IA_MAX_CONCURRENCY`)

---

### 4. Gestion des Clés API

#### Vérifications Requises

```bash
# Variables d'environnement obligatoires en production
export ANTHROPIC_API_KEY="votre_clé_api"
export SUPABASE_URL="https://xxx.supabase.co"
export SUPABASE_KEY="votre_clé_supabase"
export SUPABASE_STORAGE_BUCKET="candidats"
export JWT_SECRET_KEY="clé_secrète_complexe"
export BREVO_API_KEY="clé_brevo"  # optionnel
```

**Fallback IA:** Le code désactive proprement l'IA si `ANTHROPIC_API_KEY` manque.

---

### 5. Protection Anti-Bots (Route Postuler)

#### Problème
Aucune protection contre les spams/bots actuellement.

**Solution implémentée:** Module `modules/validation.py` avec Honeypot:

**Côté Frontend (HTML):**
```html
<!-- Champ caché honeypot -->
<div style="display:none;">
    <label for="website">Ne pas remplir</label>
    <input type="text" id="website" name="website" value="" tabindex="-1" autocomplete="off">
</div>
```

**Côté Backend:**
```python
from modules.validation import validate_candidat_form

result = validate_candidat_form(
    nom=request.form.get('nom'),
    prenom=request.form.get('prenom'),
    email=request.form.get('email'),
    telephone=request.form.get('telephone'),
    poste=request.form.get('poste'),
    postes_valides=POSTES,
    honeypot=request.form.get('website', '')  # Doit être vide
)

if not result['valid']:
    return jsonify({'error': result['errors'][0]}), 400
```

---

### 6. Typage et Maintenance

#### Problème
Fichier `server.py` de 3500+ lignes difficile à maintenir.

**Solution implémentée:** Découpage en modules:
```
/workspace/
├── server.py              (code principal allégé)
├── modules/
│   ├── __init__.py        (exports package)
│   ├── validation.py      (validation emails, téléphones, honeypot)
│   ├── export.py          (génération CSV/PDF/Excel)
│   ├── storage.py         (gestion Supabase + streaming)
│   └── errors.py          (gestion erreurs personnalisées)
└── PRODUCTION_README.md   (ce document)
```

**Avantages:**
- Code plus lisible et maintenable
- Tests unitaires facilités
- Équipes peuvent travailler en parallèle
- Réduction de la dette technique

---

## 🚀 Checklist de Déploiement

### Avant Mise en Production

- [ ] Vérifier toutes les variables d'environnement
- [ ] Tester la validation email stricte
- [ ] Valider la protection CSV Injection
- [ ] Configurer les gestionnaires d'erreurs
- [ ] Ajouter le champ honeypot au frontend
- [ ] Tester avec des fichiers de 15Mo
- [ ] Vérifier la consommation mémoire en charge
- [ ] Mettre en place monitoring (logs, erreurs)

### Configuration Serveur

```bash
# Limites recommandées
ulimit -n 65535  # Nombre de fichiers ouverts
export IA_MAX_CONCURRENCY=2  # Limiter threads IA
export GUNICORN_WORKERS=4  # Si utilisation de Gunicorn
```

### Monitoring Recommandé

```python
# Ajouter des logs structurés
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

---

## 📊 Métriques à Surveiller

1. **Taux d'erreur 413**: Indique si la limite 15Mo est adaptée
2. **Taux de rejet validation email**: Ajuster si trop élevé
3. **Consommation mémoire**: Alertes >80% RAM
4. **Temps de réponse analyse IA**: Timeout si >60s
5. **Taux de spam bloqué (honeypot)**: Efficacité anti-bot

---

## 🔧 Intégration dans server.py

Exemple d'intégration minimale:

```python
from flask import Flask
from modules.errors import init_error_handlers
from modules.validation import validate_candidat_form, validate_email
from modules.export import generate_csv_report, sanitize_csv_field
from modules.storage import get_supabase_client, upload_file_to_supabase

app = Flask(__name__)

# Initialiser gestionnaires d'erreurs
init_error_handlers(app)

# Route postuler améliorée
@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    # Validation complète avec honeypot
    result = validate_candidat_form(
        nom=request.form.get('nom'),
        prenom=request.form.get('prenom'),
        email=request.form.get('email'),
        telephone=request.form.get('telephone'),
        poste=request.form.get('poste'),
        postes_valides=POSTES,
        honeypot=request.form.get('website', '')
    )
    
    if not result['valid']:
        return jsonify({'error': result['errors'][0]}), 400
    
    # Utiliser data validée
    nom = result['data']['nom']
    prenom = result['data']['prenom']
    email = result['data']['email']
    # ...
```

---

## 📞 Support et Maintenance

Pour toute question ou amélioration:
- Revue de code trimestrielle recommandée
- Mise à jour des dépendances mensuelle
- Audit de sécurité semestriel

**Version actuelle:** 1.0.0  
**Date:** $(date +%Y-%m-%d)

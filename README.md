# 🏦 RecrutBank — Système de Gestion des Candidatures

Portail web complet pour la gestion du recrutement bancaire : dépôt de candidatures, tableau de bord recruteur, suivi en temps réel et préparation d'emails.

---

## 📁 Structure du projet

```
gestion-candidatures/
├── backend/
│   ├── server.py          # Serveur Flask (API REST + base de données SQLite)
│   ├── candidatures.db    # Base de données (créée automatiquement au démarrage)
│   └── uploads/           # CVs et lettres de motivation uploadés
├── frontend/
│   ├── index.html              # Page d'accueil publique + formulaire de candidature
│   ├── login.html              # Connexion sécurisée recruteur
│   ├── dashboard-recruteur.html# Tableau de bord recruteur (protégé JWT)
│   ├── dashboard-candidat.html # Portail suivi de dossier
│   ├── style.css               # Feuille de style complète
│   └── script.js               # Fonctions utilitaires partagées
├── start.sh               # Script de démarrage (Linux/Mac)
└── README.md
```

---

## 🚀 Installation & Démarrage

### Prérequis
- Python 3.8+

### 1. Installer les dépendances Python
```bash
pip install flask flask-cors flask-jwt-extended werkzeug
```

### 2. Démarrer le serveur
```bash
# Option A – Script automatique
bash start.sh

# Option B – Manuellement
cd backend
python3 server.py
```

### 3. Ouvrir dans le navigateur
| Page | URL |
|---|---|
| Accueil public | http://localhost:5000 |
| Connexion recruteur | http://localhost:5000/login |
| Tableau de bord recruteur | http://localhost:5000/dashboard-recruteur |
| Suivi candidat | http://localhost:5000/dashboard-candidat |

---

## 🔑 Identifiants de démonstration

| Rôle | Email | Mot de passe |
|---|---|---|
| Recruteur | recruteur@banque.com | admin123 |

---

## ✨ Fonctionnalités

### Page d'accueil (publique)
- Présentation des 6 postes ouverts
- Formulaire de candidature avec upload CV + lettre de motivation
- Section de suivi de dossier par numéro de token

### Tableau de bord Recruteur (🔐 protégé)
- **Statistiques en temps réel** : total, en attente, retenus, refusés, entretiens
- **Graphiques** : donut par statut, histogramme par poste
- **Liste des candidats** avec filtres (poste, statut, recherche texte)
- **Dossier complet** : infos candidat, accès CV/lettre, notes internes, score (⭐)
- **Gestion du statut** : retenir / inviter en entretien / rejeter
- **Email prêt à l'envoi** : aperçu personnalisé selon le statut, copie presse-papiers ou ouverture dans le client mail

### Portail Candidat
- Suivi de dossier via numéro de token unique
- Message RH personnalisé si commentaire ajouté

---

## 🗂️ Postes ouverts configurés

1. Responsable Administration de Crédit
2. Analyste Crédit CCB
3. Archiviste (Administration Crédit)
4. Senior Finance Officer
5. Market Risk Officer
6. IT Réseau & Infrastructure

---

## 🔒 Sécurité
- Authentification JWT (expiration 8h)
- Mots de passe hashés (SHA-256)
- Upload limité à 10 Mo, formats PDF/DOC/DOCX uniquement
- Routes recruteur entièrement protégées

---

## ⚙️ Configuration SMTP (email réel)

Pour activer l'envoi d'emails réels aux candidats après soumission de leur candidature :

### 1. Créer un fichier `.env` à la racine du projet

Copiez le fichier `.env.example` en `.env` et remplissez avec vos informations :

```bash
cp .env.example .env
```

### 2. Configurer vos identifiants SMTP

Exemple pour Gmail :

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=votre.email@gmail.com
SMTP_PASSWORD=votre_mot_de_passe_application
SMTP_FROM=RecrutBank RH <noreply@recrutbank.com>
SMTP_USE_TLS=true
```

**Important pour Gmail :**
1. Activez la validation en deux étapes sur votre compte Google
2. Générez un mot de passe d'application : https://myaccount.google.com/apppasswords
3. Utilisez ce mot de passe dans `SMTP_PASSWORD`

### 3. Redémarrer le serveur

Après avoir modifié le fichier `.env`, redémarrez le serveur pour prendre en compte les changements.

---

## 📧 Système de notification par email

Le système envoie automatiquement un email de confirmation à chaque candidat après la soumission de sa candidature.

**Contenu de l'email :**
- ✅ Salutation personnalisée avec le nom complet du candidat
- ✅ Confirmation de la soumission pour le poste spécifique
- ✅ Numéro de dossier unique
- ✅ Information que le dossier est en cours d'analyse
- ✅ Message indiquant qu'il sera informé du résultat
- ✅ Invitation à rester en attente
- ✅ Signature de l'équipe RH

Pour plus de détails, consultez le fichier [README_EMAIL.md](README_EMAIL.md).

---

## 🛠️ Technologies utilisées

- **Backend** : Python 3 / Flask / SQLite / Flask-JWT-Extended
- **Frontend** : HTML5, CSS3, JavaScript vanilla, Chart.js
- **Base de données** : SQLite (fichier local, sans configuration)

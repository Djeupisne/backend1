# 📧 Système de Notification par Email

## Configuration

Pour activer l'envoi d'emails de confirmation aux candidats, vous devez configurer les variables d'environnement suivantes :

### Variables d'environnement requises :

```bash
# Serveur SMTP
SMTP_SERVER=smtp.gmail.com

# Port SMTP (587 pour TLS, 465 pour SSL)
SMTP_PORT=587

# Identifiants SMTP
SMTP_USERNAME=votre_email@gmail.com
SMTP_PASSWORD=votre_mot_de_passe_application

# Utiliser TLS (true/false)
SMTP_USE_TLS=true

# Adresse d'expédition
EMAIL_FROM=noreply@recrutbank.com
```

## Configuration pour Gmail

Si vous utilisez Gmail comme serveur SMTP :

1. Activez la validation en deux étapes sur votre compte Google
2. Générez un **mot de passe d'application** :
   - Allez sur https://myaccount.google.com/apppasswords
   - Sélectionnez "Mail" et votre appareil
   - Copiez le mot de passe généré (16 caractères)
   - Utilisez ce mot de passe dans `SMTP_PASSWORD`

### Exemple de configuration pour Gmail :

```bash
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=votre_compte@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop  # Mot de passe d'application (sans espaces dans la vraie config)
SMTP_USE_TLS=true
EMAIL_FROM=votre_compte@gmail.com
```

## Configuration pour d'autres fournisseurs SMTP

### Outlook/Hotmail :
```bash
SMTP_SERVER=smtp-mail.outlook.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### Yahoo Mail :
```bash
SMTP_SERVER=smtp.mail.yahoo.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

### SMTP personnalisé (entreprise) :
```bash
SMTP_SERVER=smtp.votre-entreprise.com
SMTP_PORT=587
SMTP_USERNAME=votre_email@votre-entreprise.com
SMTP_PASSWORD=votre_mot_de_passe
SMTP_USE_TLS=true
EMAIL_FROM=noreply@votre-entreprise.com
```

## Fonctionnement

Lorsqu'un candidat soumet sa candidature :

1. Le backend enregistre la candidature dans Redis
2. Un thread séparé est lancé pour envoyer l'email de confirmation
3. L'email contient :
   - Confirmation de réception de la candidature
   - Le poste pour lequel le candidat a postulé
   - Le numéro de dossier unique
   - Un message indiquant que l'équipe RH examinera le profil

## Email de confirmation

Le candidat reçoit un email avec :

**Objet :** Confirmation de candidature - [Nom du poste]

**Contenu :**
- Version texte brut et HTML
- Salutation personnalisée avec nom et prénom
- Détails du poste et numéro de dossier
- Message professionnel de l'équipe RH

## Dépannage

### L'email n'est pas envoyé

Vérifiez les logs du serveur pour voir le message :
```
⚠️ Configuration SMTP incomplète. Email non envoyé à [email]
```

Solution : Configurez correctement `SMTP_USERNAME` et `SMTP_PASSWORD`.

### Erreur d'authentification

Message d'erreur possible :
```
❌ Erreur lors de l'envoi de l'email : Authentication failed
```

Solutions :
- Vérifiez que le mot de passe est correct
- Pour Gmail, utilisez un mot de passe d'application, pas votre mot de passe principal
- Vérifiez que la validation en deux étapes est activée (pour Gmail)

### Erreur de connexion SMTP

Message d'erreur possible :
```
❌ Erreur lors de l'envoi de l'email : Connection refused
```

Solutions :
- Vérifiez que le serveur SMTP et le port sont corrects
- Vérifiez votre connexion internet
- Certains réseaux bloquent les ports SMTP - essayez un autre réseau

## Sécurité

⚠️ **Important :** Ne commitez jamais vos identifiants SMTP dans le code source. Utilisez toujours des variables d'environnement ou un fichier `.env` (qui doit être dans `.gitignore`).

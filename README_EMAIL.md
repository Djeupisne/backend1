# 📧 Système de Notification par Email - RecrutBank

## Vue d'ensemble

Un système de notification par email a été implémenté pour informer automatiquement les candidats après la soumission de leur candidature.

## Fonctionnalité

Lorsqu'un candidat soumet sa candidature :
1. ✅ Un email de confirmation lui est envoyé automatiquement
2. ✅ L'email confirme que le dossier a été soumis avec succès
3. ✅ Il indique que le dossier est en cours d'analyse
4. ✅ Il informe le candidat qu'il sera contacté pour le résultat
5. ✅ Il demande au candidat de rester en attente

## Configuration Requise

Pour activer l'envoi d'emails, configurez les variables d'environnement suivantes :

```bash
# Serveur SMTP
SMTP_HOST=smtp.gmail.com          # ou votre serveur SMTP
SMTP_PORT=587                      # 587 pour TLS, 465 pour SSL

# Identifiants
SMTP_USER=votre.email@gmail.com    # Votre adresse email
SMTP_PASSWORD=votre_mot_de_passe   # Mot de passe d'application

# Expéditeur
SMTP_FROM=noreply@recrutbank.com   # Adresse d'expédition

# Sécurité (optionnel, par défaut: true)
SMTP_USE_TLS=true
```

### Pour Gmail

Si vous utilisez Gmail, vous devez :
1. Activer la validation en deux étapes sur votre compte Google
2. Générer un **mot de passe d'application** dans les paramètres de sécurité
3. Utiliser ce mot de passe dans `SMTP_PASSWORD`

## Exemple de .env

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=recrutement@recrutbank.com
SMTP_PASSWORD=abcd efgh ijkl mnop
SMTP_FROM=RecrutBank <noreply@recrutbank.com>
SMTP_USE_TLS=true
```

## Contenu de l'Email

Le candidat reçoit un email contenant :
- Salutation personnalisée avec son nom complet
- Confirmation de la soumission pour le poste spécifique
- Numéro de dossier unique
- Information que le dossier est en cours d'analyse
- Message indiquant qu'il sera informé du résultat
- Invitation à rester en attente
- Signature de l'équipe RH

## Implémentation Technique

- **Envoi asynchrone** : L'email est envoyé dans un thread séparé pour ne pas bloquer la réponse API
- **Format double** : Email en texte brut ET HTML pour une meilleure compatibilité
- **Gestion d'erreurs** : Si la configuration SMTP est incomplète, le système logue un avertissement mais ne bloque pas la candidature
- **Encodage UTF-8** : Support complet des caractères spéciaux et accents

## Test

Après avoir configuré les variables d'environnement :

1. Démarrez le serveur : `python server.py`
2. Soumettez une candidature via l'API `/api/candidats/postuler`
3. Vérifiez les logs du serveur pour confirmer l'envoi de l'email
4. Vérifiez la boîte de réception du candidat

## Logs

Lors du démarrage, le serveur affiche l'état du système d'email :
```
📧 SYSTEME DE NOTIFICATION EMAIL:
   ✅ Emails ACTIVÉS via smtp.gmail.com
      Expéditeur: noreply@recrutbank.com
```

Ou si non configuré :
```
📧 SYSTEME DE NOTIFICATION EMAIL:
   ⚠️  Emails DÉSACTIVÉS (configurez SMTP_USER et SMTP_PASSWORD)
```

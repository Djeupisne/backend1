"""
Module de validation des données candidats
Gère la validation des emails, téléphones, et la protection anti-spam
"""

import re
from typing import Optional, Tuple, Dict, Any


# Regex email plus strict - valide les domaines avec au moins 2 caractères après le dernier point
EMAIL_REGEX = re.compile(
    r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
)

# Regex téléphone - accepte formats internationaux
PHONE_REGEX = re.compile(
    r'^\+?[\d\s().-]{8,20}$'
)


def validate_email(email: str) -> Tuple[bool, Optional[str]]:
    """
    Valide le format d'un email.
    
    Args:
        email: L'adresse email à valider
        
    Returns:
        Tuple (est_valide, message_erreur)
        - (True, None) si l'email est valide
        - (False, message) si l'email est invalide
    """
    if not email:
        return False, "L'adresse email est requise"
    
    email = email.strip().lower()
    
    if len(email) > 254:
        return False, "L'adresse email est trop longue"
    
    if not EMAIL_REGEX.match(email):
        return False, "Format d'email invalide (ex: nom@domaine.com)"
    
    # Vérification supplémentaire: le domaine doit avoir au moins un point
    parts = email.split('@')
    if len(parts) != 2:
        return False, "Format d'email invalide"
    
    domain = parts[1]
    if '.' not in domain:
        return False, "Le domaine de l'email doit contenir un point (ex: .com, .fr)"
    
    # Vérifier que le TLD a au moins 2 caractères
    tld = domain.split('.')[-1]
    if len(tld) < 2:
        return False, "Le domaine de l'email est invalide"
    
    return True, None


def validate_phone(phone: str) -> Tuple[bool, Optional[str]]:
    """
    Valide le format d'un numéro de téléphone.
    
    Args:
        phone: Le numéro de téléphone à valider
        
    Returns:
        Tuple (est_valide, message_erreur)
    """
    if not phone:
        return True, None  # Téléphone optionnel
    
    phone = phone.strip()
    
    if len(phone) > 20:
        return False, "Le numéro de téléphone est trop long"
    
    if not PHONE_REGEX.match(phone):
        return False, "Format de téléphone invalide"
    
    # Compter les chiffres
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 8:
        return False, "Le numéro de téléphone doit contenir au moins 8 chiffres"
    
    return True, None


def validate_honeypot(honeypot_value: str) -> bool:
    """
    Vérifie le champ honeypot (piège à bots).
    Un humain ne remplira pas ce champ caché.
    
    Args:
        honeypot_value: La valeur du champ honeypot
        
    Returns:
        True si le champ est vide (comportement humain)
        False si le champ est rempli (probable bot)
    """
    return not honeypot_value or honeypot_value.strip() == ''


def validate_candidat_form(
    nom: str,
    prenom: str,
    email: str,
    telephone: str,
    poste: str,
    postes_valides: list,
    honeypot: str = ''
) -> Dict[str, Any]:
    """
    Valide tous les champs du formulaire de candidature.
    
    Args:
        nom: Nom du candidat
        prenom: Prénom du candidat
        email: Email du candidat
        telephone: Téléphone du candidat
        poste: Poste visé
        postes_valides: Liste des postes valides
        honeypot: Valeur du champ honeypot (optionnel)
        
    Returns:
        Dict avec:
        - 'valid': booléen indiquant si tout est valide
        - 'errors': liste des messages d'erreur
        - 'data': données nettoyées si valides
    """
    errors = []
    data = {}
    
    # Validation honeypot (anti-bot)
    if not validate_honeypot(honeypot):
        errors.append("Soumission invalide détectée")
        return {'valid': False, 'errors': errors, 'data': None}
    
    # Validation nom
    if not nom or not nom.strip():
        errors.append("Le nom est requis")
    else:
        data['nom'] = nom.strip()
    
    # Validation prénom
    if not prenom or not prenom.strip():
        errors.append("Le prénom est requis")
    else:
        data['prenom'] = prenom.strip()
    
    # Validation email
    email_valid, email_error = validate_email(email)
    if not email_valid:
        errors.append(email_error)
    else:
        data['email'] = email.strip().lower()
    
    # Validation téléphone (optionnel)
    if telephone:
        phone_valid, phone_error = validate_phone(telephone)
        if not phone_valid:
            errors.append(phone_error)
        else:
            data['telephone'] = telephone.strip()
    else:
        data['telephone'] = ''
    
    # Validation poste
    poste = poste.strip() if poste else ''
    if not poste:
        errors.append("Le poste est requis")
    elif poste not in postes_valides:
        errors.append(f"Poste invalide. Postes disponibles: {', '.join(postes_valides)}")
    else:
        data['poste'] = poste
    
    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'data': data if len(errors) == 0 else None
    }

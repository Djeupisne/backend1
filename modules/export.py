"""
Module d'export de rapports (CSV, Excel, PDF, Word)
Gère la génération de fichiers avec protection CSV Injection
"""

import io
import csv
import json
import datetime
from typing import List, Dict, Any, Optional


def sanitize_csv_field(value: Any) -> str:
    """
    Sanitize un champ pour prévenir l'injection CSV (CSV Injection).
    
    Les formules Excel commencent par =, +, -, @. On préfixe avec une apostrophe.
    
    Args:
        value: La valeur à sanitiser
        
    Returns:
        Chaîne sécurisée pour le CSV
    """
    if value is None:
        return ''
    
    str_value = str(value)
    
    # Si la valeur commence par un caractère dangereux pour Excel
    if str_value and str_value[0] in ('=', '+', '-', '@'):
        return "'" + str_value
    
    return str_value


def generate_csv_report(candidats_data: List[Dict], poste_filter: Optional[str] = None) -> str:
    """
    Génère un rapport CSV avec protection contre l'injection CSV.
    
    Args:
        candidats_data: Liste des données candidats
        poste_filter: Filtre optionnel par poste
        
    Returns:
        Contenu CSV formaté en string
    """
    out = io.StringIO()
    w = csv.writer(out, delimiter=';', quoting=csv.QUOTE_ALL, quotechar='"')
    
    headers = [
        'Rang', 'N° Dossier', 'Email', 'Nom', 'Prénom', 'Téléphone', 
        'Poste', 'Date candidature', 'Score', 'Statut', 'Éliminatoire', 
        'Adéquation (0-3)', 'Cohérence', 'Risque/Exposition', 'Note', 'Recommandation'
    ]
    w.writerow(headers)
    
    # Filtrage par poste si nécessaire
    if poste_filter:
        candidats_filtered = [c for c in candidats_data if c.get('poste') == poste_filter]
    else:
        candidats_filtered = candidats_data
    
    # Tri par poste et date
    candidats_filtered.sort(key=lambda x: (x.get('poste', ''), x.get('date_candidature', '')), reverse=True)
    
    for idx, c in enumerate(candidats_filtered, 1):
        sb = c.get('score_breakdown_parsed', {})
        score = int(c.get('score', 0))
        poste = c.get('poste', '')
        
        # Récupération des sous-scores avec fallbacks
        adeq_val = sb.get('adequation_experience', 0)
        if not adeq_val:
            adeq_val = sb.get('sous_scores', {}).get(
                "Adéquation de l'expérience (compensation interbancaire, back-office bancaire)", 0
            )
        if not adeq_val:
            adeq_val = sb.get('sous_scores', {}).get(
                "Adéquation de l'expérience (administration de crédit, gestion des risques, analyse crédit)", 0
            )
        
        coh_val = sb.get('coherence_parcours', 0)
        if not coh_val:
            coh_val = sb.get('sous_scores', {}).get(
                "Cohérence et progression du parcours professionnel", 0
            )
        
        risk_val = sb.get('exposition_risque_metier', 0)
        if not risk_val:
            risk_val = sb.get('sous_scores', {}).get(
                "Exposition aux règles BEAC / GIMAC et aux systèmes de compensation (SYSTAC, SYGMA, SWIFT)", 0
            )
        if not risk_val:
            risk_val = sb.get('sous_scores', {}).get(
                "Exposition aux normes IFRS 9 et à la gestion du portefeuille de crédit", 0
            )
        
        # Construction de la ligne avec sanitization CSV
        row = [
            sanitize_csv_field(idx),
            sanitize_csv_field(c.get('numero_dossier', '') or '–'),
            sanitize_csv_field(c.get('email', '') or '–'),
            sanitize_csv_field(c.get('nom', '') or ''),
            sanitize_csv_field(c.get('prenom', '') or ''),
            sanitize_csv_field(c.get('telephone', '') or '–'),
            sanitize_csv_field(poste or ''),
            sanitize_csv_field(c.get('date_candidature', '') or ''),
            sanitize_csv_field(c.get('score', '0')),
            sanitize_csv_field(c.get('statut', '') or ''),
            'OUI' if sb.get('bloc1_eliminatoire') else 'NON',
            sanitize_csv_field(adeq_val),
            sanitize_csv_field(coh_val),
            sanitize_csv_field(risk_val),
            sanitize_csv_field(sb.get('note', '') or ''),
            sanitize_csv_field(get_recommandation_from_score(score, poste))
        ]
        w.writerow(row)
    
    out.seek(0)
    return out.getvalue()


def get_recommandation_from_score(score: int, poste: str) -> str:
    """
    Obtient une recommandation basée sur le score.
    Fonction placeholder - devrait être importée du module d'analyse.
    
    Args:
        score: Score du candidat
        poste: Poste visé
        
    Returns:
        Recommandation textuelle
    """
    if score >= 80:
        return "Recommandé - Excellent profil"
    elif score >= 60:
        return "Recommandé - Bon profil"
    elif score >= 40:
        return "À considérer - Profil moyen"
    else:
        return "Non recommandé - Profil insuffisant"

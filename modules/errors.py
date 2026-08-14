"""
Module de gestion des erreurs personnalisées
Gère les erreurs HTTP et les messages utilisateur personnalisés
"""

from functools import wraps
from flask import request, jsonify
import logging

logger = logging.getLogger(__name__)


class FileSizeError(Exception):
    """Exception levée quand un fichier dépasse la taille maximale autorisée"""
    def __init__(self, message="Fichier trop volumineux", max_size_mb=15):
        self.message = message
        self.max_size_mb = max_size_mb
        super().__init__(self.message)


def handle_file_size_limit(max_size_mb: int = 15):
    """
    Décorateur pour gérer la limite de taille des fichiers avec un message personnalisé.
    
    Args:
        max_size_mb: Taille maximale en mégaoctets (défaut: 15 Mo)
    """
    def decorator(f):
        @wraps(f)
        def wrapped_function(*args, **kwargs):
            try:
                # Vérifier la taille des fichiers uploadés
                if request.files:
                    for file_key in request.files:
                        files = request.files.getlist(file_key)
                        for file in files:
                            if file and file.filename:
                                # Sauvegarder la position actuelle
                                current_pos = file.tell()
                                # Aller à la fin pour obtenir la taille
                                file.seek(0, 2)
                                file_size = file.tell()
                                # Revenir à la position initiale
                                file.seek(current_pos)
                                
                                # Vérifier la taille
                                if file_size > max_size_mb * 1024 * 1024:
                                    logger.warning(
                                        f"Fichier '{file.filename}' trop volumineux: "
                                        f"{file_size / (1024*1024):.2f} Mo (max: {max_size_mb} Mo)"
                                    )
                                    return jsonify({
                                        'error': f'Le fichier "{file.filename}" est trop volumineux. '
                                                 f'La taille maximale autorisée est de {max_size_mb} Mo.'
                                    }), 413
                
                return f(*args, **kwargs)
                
            except Exception as e:
                logger.error(f"Erreur lors du traitement du fichier: {e}")
                return jsonify({'error': 'Erreur lors du traitement du fichier'}), 500
        
        return wrapped_function
    return decorator


def init_error_handlers(app):
    """
    Initialise les gestionnaires d'erreurs personnalisés pour l'application Flask.
    
    Args:
        app: Application Flask
    """
    
    @app.errorhandler(413)
    def handle_request_entity_too_large(error):
        """Gestionnaire personnalisé pour l'erreur 413 (Request Entity Too Large)"""
        return jsonify({
            'error': 'Le fichier envoyé est trop volumineux. La taille maximale autorisée est de 15 Mo.'
        }), 413
    
    @app.errorhandler(400)
    def handle_bad_request(error):
        """Gestionnaire personnalisé pour l'erreur 400 (Bad Request)"""
        return jsonify({
            'error': 'Requête invalide. Veuillez vérifier les données soumises.'
        }), 400
    
    @app.errorhandler(404)
    def handle_not_found(error):
        """Gestionnaire personnalisé pour l'erreur 404 (Not Found)"""
        return jsonify({
            'error': 'Ressource non trouvée.'
        }), 404
    
    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        """Gestionnaire personnalisé pour l'erreur 405 (Method Not Allowed)"""
        return jsonify({
            'error': 'Méthode non autorisée pour cette ressource.'
        }), 405
    
    @app.errorhandler(409)
    def handle_conflict(error):
        """Gestionnaire personnalisé pour l'erreur 409 (Conflict)"""
        return jsonify({
            'error': 'Conflit détecté. Cette candidature existe déjà.'
        }), 409
    
    @app.errorhandler(500)
    def handle_internal_error(error):
        """Gestionnaire personnalisé pour l'erreur 500 (Internal Server Error)"""
        logger.error(f"Erreur interne du serveur: {error}")
        return jsonify({
            'error': 'Une erreur interne est survenue. Veuillez réessayer plus tard.'
        }), 500
    
    @app.errorhandler(Exception)
    def handle_generic_exception(error):
        """Gestionnaire pour toutes les exceptions non gérées"""
        logger.error(f"Exception non gérée: {error}")
        return jsonify({
            'error': 'Une erreur inattendue est survenue.'
        }), 500
    
    logger.info("Gestionnaires d'erreurs initialisés")


def validate_file_upload(file, allowed_extensions: set, max_size_mb: int = 15):
    """
    Valide un fichier uploadé (extension et taille).
    
    Args:
        file: Objet fichier Werkzeug
        allowed_extensions: Ensemble des extensions autorisées
        max_size_mb: Taille maximale en Mo
        
    Returns:
        Tuple (est_valide, message_erreur)
    """
    if not file or not file.filename:
        return False, "Aucun fichier fourni"
    
    # Vérifier l'extension
    if '.' not in file.filename:
        return False, "Extension de fichier invalide"
    
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in allowed_extensions:
        return False, f"Type de fichier non autorisé (.{' .' .join(allowed_extensions)})"
    
    # Vérifier la taille
    current_pos = file.tell()
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(current_pos)
    
    if file_size > max_size_mb * 1024 * 1024:
        return False, f"Fichier trop volumineux ({file_size / (1024*1024):.1f} Mo, max {max_size_mb} Mo)"
    
    return True, None

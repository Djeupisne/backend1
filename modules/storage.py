"""
Module de stockage et gestion des fichiers
Gère les opérations Supabase avec streaming pour optimiser la mémoire
"""

import os
import io
import logging
from typing import Optional, BinaryIO, List
from supabase import create_client, Client

logger = logging.getLogger(__name__)


def get_supabase_client() -> Optional[Client]:
    """
    Initialise et retourne le client Supabase.
    
    Returns:
        Client Supabase ou None si non configuré
    """
    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_KEY", "")
    
    if not supabase_url or not supabase_key:
        return None
    
    try:
        return create_client(supabase_url, supabase_key)
    except Exception as e:
        logger.error(f"Erreur initialisation Supabase: {e}")
        return None


def upload_file_to_supabase(
    file_obj: BinaryIO, 
    blob_name: str, 
    content_type: Optional[str] = None,
    supabase_client: Optional[Client] = None
) -> Optional[str]:
    """
    Upload un fichier vers Supabase Storage.
    
    Args:
        file_obj: Objet fichier (doit être reset à la position 0)
        blob_name: Nom du fichier dans le storage
        content_type: Type MIME du fichier
        supabase_client: Client Supabase (optionnel, sera créé si None)
        
    Returns:
        Nom du blob uploadé ou None en cas d'échec
    """
    if supabase_client is None:
        supabase_client = get_supabase_client()
    
    if not supabase_client:
        return None
    
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "candidats")
    
    try:
        # Lire le contenu du fichier
        file_bytes = file_obj.read()
        
        # Upload vers Supabase
        supabase_client.storage.from_(bucket_name).upload(
            blob_name,
            file_bytes,
            {"content-type": content_type} if content_type else {}
        )
        
        return blob_name
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return None


def download_file_from_supabase_streaming(
    blob_name: str,
    chunk_size: int = 8192,
    supabase_client: Optional[Client] = None
) -> Optional[BinaryIO]:
    """
    Télécharge un fichier depuis Supabase en mode streaming pour économiser la mémoire.
    
    Alternative à download_file_from_supabase qui charge tout en mémoire.
    Pour les gros fichiers (>15Mo), utiliser cette méthode avec traitement par chunks.
    
    Args:
        blob_name: Nom du fichier dans le storage
        chunk_size: Taille des chunks pour le streaming (défaut: 8KB)
        supabase_client: Client Supabase (optionnel)
        
    Returns:
        BytesIO avec le contenu du fichier ou None en cas d'échec
    """
    if supabase_client is None:
        supabase_client = get_supabase_client()
    
    if not supabase_client:
        return None
    
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "candidats")
    
    try:
        # Télécharger le fichier
        response = supabase_client.storage.from_(bucket_name).download(blob_name)
        
        # Retourner dans un BytesIO pour compatibilité
        return io.BytesIO(response)
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None


def download_file_from_supabase(
    blob_name: str,
    supabase_client: Optional[Client] = None
) -> Optional[bytes]:
    """
    Télécharge un fichier depuis Supabase (version legacy - charge tout en mémoire).
    
    NOTE: Pour les fichiers >15Mo, préférer download_file_from_supabase_streaming
    
    Args:
        blob_name: Nom du fichier dans le storage
        supabase_client: Client Supabase (optionnel)
        
    Returns:
        Contenu binaire du fichier ou None en cas d'échec
    """
    if supabase_client is None:
        supabase_client = get_supabase_client()
    
    if not supabase_client:
        return None
    
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "candidats")
    
    try:
        response = supabase_client.storage.from_(bucket_name).download(blob_name)
        return response
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None


def get_signed_url(
    blob_name: str, 
    expiration_minutes: int = 60,
    supabase_client: Optional[Client] = None
) -> Optional[str]:
    """
    Génère une URL signée pour un fichier.
    
    Args:
        blob_name: Nom du fichier dans le storage
        expiration_minutes: Durée de validité en minutes
        supabase_client: Client Supabase (optionnel)
        
    Returns:
        URL signée ou None en cas d'échec
    """
    if supabase_client is None:
        supabase_client = get_supabase_client()
    
    if not supabase_client:
        return None
    
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "candidats")
    
    try:
        response = supabase_client.storage.from_(bucket_name).create_signed_url(
            blob_name, expiration_minutes * 60
        )
        return response.get('signedURL') if response else None
    except Exception as e:
        logger.error(f"Signed URL error: {e}")
        return None


def delete_file_from_supabase(
    blob_name: str,
    supabase_client: Optional[Client] = None
) -> bool:
    """
    Supprime un fichier du storage Supabase.
    
    Args:
        blob_name: Nom du fichier à supprimer
        supabase_client: Client Supabase (optionnel)
        
    Returns:
        True si succès, False sinon
    """
    if supabase_client is None:
        supabase_client = get_supabase_client()
    
    if not supabase_client:
        return False
    
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "candidats")
    
    try:
        supabase_client.storage.from_(bucket_name).remove([blob_name])
        return True
    except Exception as e:
        logger.error(f"Delete error: {e}")
        return False


def list_files_in_bucket(
    prefix: str = "",
    limit: int = 100,
    supabase_client: Optional[Client] = None
) -> List[dict]:
    """
    Liste les fichiers dans un bucket Supabase.
    
    Args:
        prefix: Préfixe pour filtrer les fichiers
        limit: Nombre maximum de fichiers à retourner
        supabase_client: Client Supabase (optionnel)
        
    Returns:
        Liste des métadonnées de fichiers
    """
    if supabase_client is None:
        supabase_client = get_supabase_client()
    
    if not supabase_client:
        return []
    
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "candidats")
    
    try:
        response = supabase_client.storage.from_(bucket_name).list(
            path=prefix,
            options={"limit": limit}
        )
        return response if response else []
    except Exception as e:
        logger.error(f"List files error: {e}")
        return []

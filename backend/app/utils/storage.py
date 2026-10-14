# ═══════════════════════════════════════════════════════
# Acces au stockage Supabase (upload / download / URL signee)
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from ..config import logger, supabase, SUPABASE_STORAGE_BUCKET, DOWNLOAD_MAX_RETRIES
from .helpers import retry_with_backoff
import threading

# Semaphore local identique a celui de lancien server.py (telechargements concurrents)
DOWNLOAD_MAX_CONCURRENT = int(__import__("os").getenv("DOWNLOAD_MAX_CONCURRENT", "15"))
_DOWNLOAD_SEMAPHORE = threading.Semaphore(DOWNLOAD_MAX_CONCURRENT)

def upload_file_to_supabase(file_obj, blob_name, content_type=None):
    if not supabase:
        return None
    try:
        file_bytes = file_obj.read()
        supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            blob_name,
            file_bytes,
            {"content-type": content_type or "application/octet-stream", "upsert": "true"}
        )
        return blob_name
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return None
@retry_with_backoff(max_retries=DOWNLOAD_MAX_RETRIES)
def download_file_from_supabase_robust(blob_name):
    if not supabase:
        return None
    with _DOWNLOAD_SEMAPHORE:
        try:
            response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).download(blob_name)
            return response
        except Exception as e:
            logger.error(f"Erreur telechargement {blob_name}: {e}")
            raise
def download_file_from_supabase(blob_name):
    if not supabase:
        return None
    try:
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).download(blob_name)
        return response
    except Exception as e:
        error_str = str(e).lower()
        if any(kw in error_str for kw in ["errno 11", "resource temporarily unavailable", "timeout", "connection"]):
            return download_file_from_supabase_robust(blob_name)
        logger.error(f"Download error: {e}")
        return None
def get_signed_url(blob_name, expiration_minutes=60):
    if not supabase:
        return None
    try:
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).create_signed_url(blob_name, expiration_minutes * 60)
        return response.get('signedURL') if response else None
    except Exception as e:
        logger.error(f"Signed URL error: {e}")
        return None

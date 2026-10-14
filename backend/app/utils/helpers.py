# ═══════════════════════════════════════════════════════
# Fonctions utilitaires generiques (normalisation, hachage, retry)
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from ..config import (logger, ALLOWED_EXTENSIONS, DOWNLOAD_MAX_RETRIES, DOWNLOAD_BASE_DELAY,
                     DOWNLOAD_MAX_DELAY)
import hashlib, random, time, re, unicodedata

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()
def normalize_spaces(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
def normalize_unicode(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'[\u00A0\u1680\u2000-\u200B\u2028\u2029\u202F\u205F\u3000]', ' ', text)
    return text.strip()
def retry_with_backoff(max_retries=DOWNLOAD_MAX_RETRIES, base_delay=DOWNLOAD_BASE_DELAY, max_delay=DOWNLOAD_MAX_DELAY):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    retryable_keywords = ["errno 11", "resource temporarily unavailable", "timeout", "connection", "temporarily unavailable", "rate limit", "too many requests", "503", "502", "504", "connection refused", "connection reset"]
                    if not any(kw in error_str for kw in retryable_keywords):
                        raise
                    if attempt == max_retries - 1:
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.3)
                    total_delay = delay + jitter
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} echouee: {e}. Nouvel essai dans {total_delay:.2f}s")
                    time.sleep(total_delay)
            raise last_exception
        return wrapper
    return decorator

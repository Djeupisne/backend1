from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, json, re, threading, mimetypes, io, csv, unicodedata, zipfile, time, gc, random, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from werkzeug.utils import secure_filename
from supabase import create_client, Client
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
try:
    import PyPDF2
    PYPDF2_AVAILABLE = True
except ImportError:
    PYPDF2_AVAILABLE = False
try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
try:
    import chardet
    CHARDET_AVAILABLE = True
except ImportError:
    CHARDET_AVAILABLE = False
try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False
try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUfZZ_AVAILABLE = False
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
try:
    import openpyxl
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
try:
    from docx import Document as DocxDocument
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
try:
    from pdf2image import convert_from_path
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
IA_ANALYSE_ACTIVE = ANTHROPIC_AVAILABLE and bool(ANTHROPIC_API_KEY)
_claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if IA_ANALYSE_ACTIVE else None
_ia_semaphore = threading.Semaphore(int(os.getenv("IA_MAX_CONCURRENCY", "2")))
_Nlp_fr = None
_Nlp_en = None
DOWNLOAD_MAX_RETRIES = int(os.getenv("DOWNLOAD_MAX_RETRIES", "5"))
DOWNLOAD_BASE_DELAY = int(os.getenv("DOWNLOAD_BASE_DELAY", "1"))
DOWNLOAD_MAX_DELAY = int(os.getenv("DOWNLOAD_MAX_DELAY", "30"))
_DOWNLOAD_SEMAPHORE = threading.Semaphore(int(os.getenv("DOWNLOAD_MAX_CONCURRENT", "3")))

def retry_with_backoff(max_retries=DOWNLOAD_MAX_RETRIES, base_delay=DOWNLOAD_BASE_DELAY, max_delay=DOWNLOAD_MAX_DELAY):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"Tentative {attempt + 1}/{max_retries} réussie pour {func.__name__}")
                    return result
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    retryable_keywords = ["errno 11", "resource temporarily unavailable", "timeout", "connection", "temporarily unavailable", "rate limit", "too many requests", "503", "502", "504", "connection refused", "connection reset"]
                    if not any(kw in error_str for kw in retryable_keywords):
                        logger.error(f"Erreur non réessayable dans {func.__name__}: {e}")
                        raise
                    if attempt == max_retries - 1:
                        logger.error(f"Échec après {max_retries} tentatives pour {func.__name__}: {e}")
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.3)
                    total_delay = delay + jitter
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} échouée pour {func.__name__}: {e}. Nouvel essai dans {total_delay:.2f}s")
                    time.sleep(total_delay)
                    gc.collect()
            raise last_exception
        return wrapper
    return decorator

def _get_spacy_model(lang='fr'):
    global _Nlp_fr, _Nlp_en
    if not SPACY_AVAILABLE:
        return None
    if lang == 'fr':
        if _Nlp_fr is None:
            try:
                _Nlp_fr = spacy.load("fr_core_news_sm")
            except OSError:
                try:
                    _Nlp_fr = spacy.load("fr_core_news_md")
                except OSError:
                    return None
        return _Nlp_fr
    else:
        if _Nlp_en is None:
            try:
                _Nlp_en = spacy.load("en_core_web_sm")
            except OSError:
                return None
        return _Nlp_en
app = Flask(__name__)
ALLOWED_ORIGINS = ["https://recrutment.onrender.com", "https://backend1-fiq5.onrender.com", "http://localhost:5000", "http://localhost:3000"]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS, "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"], "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"], "supports_credentials": True, "max_age": 600}})
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS,PUT,DELETE')
    response.headers.add('Access-Control-Max-Age', '600')
    if request.method == 'OPTIONS':
        response.status_code = 204
    return response
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return jsonify({'status': 'ok', 'message': 'RecrutBank API is running', 'version': 'v5.9-final', 'features': {'pdf_available': PDFPLUMBER_AVAILABLE, 'docx_available': DOCX_AVAILABLE, 'reportlab_available': REPORTLAB_AVAILABLE, 'openpyxl_available': OPENPYXL_AVAILABLE, 'ia_available': IA_ANALYSE_ACTIVE, 'scoring_strict': True, 'manual_status_priority': True, 'auto_width_excel': True, 'async_export': True, 'persistent_tasks': True, 'force_mode': True}}), 200
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "candidatures")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
app.config['SMTP_HOST'] = os.getenv('SMTP_HOST', 'smtp.gmail.com')
app.config['SMTP_PORT'] = int(os.getenv('SMTP_PORT', 587))
app.config['SMTP_USER'] = os.getenv('SMTP_USER', '')
app.config['SMTP_PASSWORD'] = os.getenv('SMTP_PASSWORD', '')
app.config['SMTP_FROM'] = os.getenv('SMTP_FROM', 'RecrutBank RH <oualoumidjeupisne@gmail.com>')
app.config['SMTP_USE_TLS'] = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
def upload_file_to_supabase(file_obj, blob_name, content_type=None):
    if not supabase:
        return None
    try:
        file_bytes = file_obj.read()
        supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(blob_name, file_bytes, {"content-type": content_type or "application/octet-stream", "upsert": "true"})
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
            logger.error(f"Erreur téléchargement {blob_name}: {e}")
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
            logger.warning(f"Erreur temporaire détectée, activation du mode robuste pour {blob_name}")
            result = download_file_from_supabase_robust(blob_name)
            gc.collect()
            return result
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

def create_zip_task(task_id, total, poste_filter=None, date_start=None, date_end=None):
    if supabase:
        try:
            supabase.table('zip_tasks').insert({
                'task_id': task_id,
                'status': 'pending',
                'progress': 0,
                'total': total,
                'done': 0,
                'poste_filter': poste_filter or '',
                'date_start': date_start or '',
                'date_end': date_end or '',
                'zip_path': '',
                'error': '',
                'created_at': datetime.datetime.now().isoformat(),
                'updated_at': datetime.datetime.now().isoformat()
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Erreur création tâche ZIP: {e}")
    return False

def update_zip_task(task_id, **kwargs):
    if supabase:
        try:
            kwargs['updated_at'] = datetime.datetime.now().isoformat()
            supabase.table('zip_tasks').update(kwargs).eq('task_id', task_id).execute()
            return True
        except Exception as e:
            logger.error(f"Erreur mise à jour tâche ZIP {task_id}: {e}")
    return False

def get_zip_task(task_id):
    if supabase:
        try:
            result = supabase.table('zip_tasks').select('*').eq('task_id', task_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Erreur récupération tâche ZIP: {e}")
    return None

def delete_zip_task(task_id):
    if supabase:
        try:
            supabase.table('zip_tasks').delete().eq('task_id', task_id).execute()
            return True
        except Exception as e:
            logger.error(f"Erreur suppression tâche ZIP: {e}")
    return False

def cleanup_old_zip_tasks(max_age_hours=24):
    if supabase:
        try:
            cutoff = (datetime.datetime.now() - datetime.timedelta(hours=max_age_hours)).isoformat()
            tasks = supabase.table('zip_tasks').select('task_id, zip_path').lt('created_at', cutoff).execute()
            for task in tasks.data:
                zip_path = task.get('zip_path', '')
                if zip_path and os.path.exists(zip_path):
                    try:
                        os.remove(zip_path)
                        temp_dir = os.path.dirname(zip_path)
                        if os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.error(f"Cleanup fichier ZIP error: {e}")
                supabase.table('zip_tasks').delete().eq('task_id', task['task_id']).execute()
        except Exception as e:
            logger.error(f"Erreur cleanup old zip tasks: {e}")

def send_email(to_email, subject, body):
    import requests
    import re as _re
    brevo_api_key = os.getenv('BREVO_API_KEY', '')
    smtp_from = os.getenv('SMTP_FROM', 'RecrutBank RH <oualoumidjeupisne@gmail.com>')
    if not brevo_api_key:
        return False
    match = _re.search(r'<(.+?)>', smtp_from)
    sender_email = match.group(1) if match else smtp_from
    sender_name = smtp_from.split('<')[0].strip() if '<' in smtp_from else 'RecrutBank RH'
    html_content = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;"><div style="max-width: 600px; margin: 0 auto; padding: 20px;">{body.replace(chr(10), '<br>')}</div></body></html>"""
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"api-key": brevo_api_key, "Content-Type": "application/json", "Accept": "application/json"}
    payload = {"sender": {"name": sender_name, "email": sender_email}, "to": [{"email": to_email, "name": to_email.split('@')[0]}], "subject": subject, "htmlContent": html_content, "textContent": body}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 201
    except Exception:
        return False
def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()
def normalize_spaces(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\b(\w)\s+(\w\s+\w+)\b', r'\1\2', text)
    text = re.sub(r'\b(\w)\s+(\w)\b', r'\1\2', text)
    bank_corrections = {'u b a': 'UBA', 'e c o b a n k': 'ECOBANK', 'o r a b a n k': 'ORABANK', 'u b a -': 'UBA-', 'e c o o b a n k': 'ECOBANK', 'f i n a d e v': 'FINADEV', 'w o r l d': 'WORLD', 'v i s i o n': 'VISION', 'g l s': 'GLS', 'u b a g r o u p': 'UBAGROUP', 'c o r r e c t': 'CORRECT', 's e r v i c e s': 'SERVICES', 'c o n s u l t i n g': 'CONSULTING'}
    for wrong, correct in bank_corrections.items():
        text = re.sub(r'\b' + wrong + r'\b', correct, text, flags=re.IGNORECASE)
    typo_corrections = {'risque de marche': 'risque de marché', 'risque marche': 'risque marché', 'market risk': 'market risk', 'taux de change': 'taux de change', 'liquidite': 'liquidité', 'competence': 'compétence', 'experience': 'expérience'}
    for wrong, correct in typo_corrections.items():
        text = re.sub(r'\b' + wrong + r'\b', correct, text, flags=re.IGNORECASE)
    return text.strip()
def normalize_unicode(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'[\u00A0\u1680\u2000-\u200B\u2028\u2029\u202F\u205F\u3000]', ' ', text)
    return text.strip()
def normalize_for_matching(text):
    if not text:
        return "", []
    _ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouucaaAAEEEEIIOUUUCAAaaonaaon')
    no_accents = text.lower().translate(_ACCENT_MAP)
    cleaned = re.sub(r'[^\w\s\-/\.]', ' ', no_accents)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    tokens = [t for t in re.findall(r'\b[a-z0-9\-/\.]{2,}\b', cleaned) if len(t) >= 2]
    return cleaned, tokens
def contains_negative_context(text, keyword):
    if not text or not keyword:
        return False
    keyword_pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    matches = list(keyword_pattern.finditer(text))
    if not matches:
        return False
    negative_patterns = [r"\b(pas\s+de|pas\s+d')\s*(expérience|experience|expérimenté|competence)\b", r'\b(aucun|aucune|aucuns|aucunes)\s*(expérience|experience|competence|connaissance)\b', r'\b(sans|dépourvu\s+de|manque\s+de)\s*(expérience|experience|competence)\b', r"\b(n')?(?:ai|as|a|avons|avez|ont)\s+pas\s+(?:d')?(expérience|experience|competence|connaissance)\b", r'\b(jamais\s+(?:eu|travaillé|exercé|pratiqué))\b', r"\b(peu\s+d')?expérience\b", r'\b(expérience\s+(?:limitée|insuffisante|faible|partielle))\b', r'\b(ne\s+connais\s+pas|ne\s+maîtrise\s+pas|ne\s+possède\s+pas)\b', r'\b(no\s+experience|without\s+experience|lack\s+of\s+experience)\b']
    for match in matches:
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        context = text[start:end]
        for pattern in negative_patterns:
            if re.search(pattern, context, re.IGNORECASE):
                return True
    return False
def extract_text_from_pdf_via_ocr(file_bytes):
    if not OCR_AVAILABLE:
        return ""
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return ""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode != 'L':
            img = img.convert('L')
        custom_config = r'--oem 3 --psm 6 -l fra+eng'
        text = pytesseract.image_to_string(img, config=custom_config)
        if text.strip():
            text = normalize_spaces(text)
            text = re.sub(r'[|¦]', '', text)
            return normalize_unicode(text)
        return ""
    except Exception:
        return ""
MAX_PDF_PAGES = 10
MAX_PDF_SIZE_BYTES = 5 * 1024 * 1024
MAX_TEXT_SIZE = 8000
def extract_text_from_pdf_robust(file_bytes, filename):
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        logger.warning(f"PDF trop volumineux ({len(file_bytes) / 1024 / 1024:.1f} MB > 5 MB): {filename}")
        return ""
    text = ""
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                total_pages = len(pdf.pages)
                pages_to_read = min(MAX_PDF_PAGES, total_pages)
                for i, page in enumerate(pdf.pages):
                    if i >= pages_to_read:
                        break
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                if row:
                                    row_text = ' | '.join([str(cell).strip() if cell else '' for cell in row])
                                    if row_text.strip():
                                        text += normalize_spaces(row_text) + "\n"
                    content = page.extract_text(x_tolerance=3, y_tolerance=3, keep_blank_chars=True, use_text_flow=True)
                    if content:
                        text += normalize_spaces(content) + "\n"
                    if len(text) > MAX_TEXT_SIZE:
                        text = text[:MAX_TEXT_SIZE]
                        break
            if text.strip() and len(text.strip()) > 100:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"pdfplumber erreur: {e}")
    if PYPDF2_AVAILABLE:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            total_pages = len(reader.pages)
            pages_to_read = min(MAX_PDF_PAGES, total_pages)
            for i, page in enumerate(reader.pages):
                if i >= pages_to_read:
                    break
                content = page.extract_text()
                if content:
                    text += normalize_spaces(content) + "\n"
                if len(text) > MAX_TEXT_SIZE:
                    text = text[:MAX_TEXT_SIZE]
                    break
            if text.strip() and len(text.strip()) > 100:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"PyPDF2 erreur: {e}")
    if len(text.strip()) < 100:
        ocr_text = extract_text_from_pdf_via_ocr(file_bytes)
        if ocr_text and len(ocr_text.strip()) > 100:
            return ocr_text
    return ""
def extract_text_from_docx_robust(file_bytes):
    if not DOCX_AVAILABLE:
        return ""
    try:
        doc = Document(io.BytesIO(file_bytes))
        W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        W_T = f'{{{W_NS}}}t'
        texts = [e.text for e in doc.element.body.iter(W_T) if e.text and e.text.strip()]
        raw = ' '.join(texts)
        raw = re.sub(r'\s+', ' ', raw).strip()
        if len(raw) > MAX_TEXT_SIZE:
            raw = raw[:MAX_TEXT_SIZE]
        return normalize_unicode(raw)
    except Exception as e:
        logger.warning(f"Erreur lecture DOCX (XML): {e}")
    try:
        doc = Document(io.BytesIO(file_bytes))
        parts = []
        for para in doc.paragraphs:
            t = normalize_spaces(para.text)
            if t:
                parts.append(t)
        for table in doc.tables:
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    ct = normalize_spaces(cell.text)
                    if ct:
                        cells.append(ct)
                if cells:
                    parts.append(" | ".join(cells))
        result = "\n".join(parts).strip()
        if len(result) > MAX_TEXT_SIZE:
            result = result[:MAX_TEXT_SIZE]
        return normalize_unicode(result)
    except Exception as e2:
        logger.warning(f"Fallback DOCX échoué: {e2}")
    try:
        text = re.sub(r'[^\x20-\x7E\u00C0-\u017F]+', ' ', file_bytes.decode('utf-8', errors='ignore'))
        if len(text) > MAX_TEXT_SIZE:
            text = text[:MAX_TEXT_SIZE]
        return normalize_unicode(normalize_spaces(text.strip()))
    except Exception:
        pass
    return ""
def extract_text_from_txt(file_bytes):
    if CHARDET_AVAILABLE:
        try:
            detected = chardet.detect(file_bytes[:10000])
            encoding = detected['encoding'] or 'utf-8'
            text = file_bytes.decode(encoding, errors='ignore')
            if len(text) > MAX_TEXT_SIZE:
                text = text[:MAX_TEXT_SIZE]
            return normalize_unicode(normalize_spaces(text))
        except Exception:
            pass
    for enc in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1', 'utf-16']:
        try:
            text = file_bytes.decode(enc, errors='ignore').strip()
            if len(text) > MAX_TEXT_SIZE:
                text = text[:MAX_TEXT_SIZE]
            return normalize_unicode(normalize_spaces(text))
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""
def extract_text_robust_from_bytes(file_bytes, filename):
    if not file_bytes:
        return ""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return extract_text_from_pdf_robust(file_bytes, filename)
    elif ext in ('doc', 'docx'):
        return extract_text_from_docx_robust(file_bytes)
    elif ext == 'txt':
        return extract_text_from_txt(file_bytes)
    try:
        text = file_bytes.decode('utf-8', errors='ignore').strip()
        if len(text) > MAX_TEXT_SIZE:
            text = text[:MAX_TEXT_SIZE]
        return normalize_unicode(normalize_spaces(text))
    except Exception:
        pass
    return ""
def init_recruteur():
    try:
        if supabase:
            response = supabase.table('recruteurs').select('*').eq('email', 'sougnabeoualoumibank@gmail.com').execute()
            if not response.data:
                supabase.table('recruteurs').insert({"email": "sougnabeoualoumibank@gmail.com", "password": hash_pwd("AdminLaurent123"), "nom": "Responsable RH"}).execute()
    except Exception as e:
        logger.warning(f"Erreur initialisation recruteur : {e}")
init_recruteur()

def init_zip_tasks_table():
    if supabase:
        try:
            supabase.table('zip_tasks').select('task_id').limit(1).execute()
            logger.info("Table zip_tasks existe déjà")
        except Exception as e:
            logger.warning(f"Table zip_tasks non trouvée, veuillez la créer: {e}")
init_zip_tasks_table()

POSTES = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Archiviste (Administration Crédit)", "Senior Finance Officer", "Market Risk Officer", "IT Réseau & Infrastructure", "Auditeur interne", "Chef service contrôle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marché", "Chef service reporting réglementaire", "Chef de Section Compensation", "Chargé(e) d'Administration de Crédit", "Chef de Division Local Corporate", "Data Analyst Finance"]
POSTES_ACTIFS = ["Chef de Division Local Corporate", "Data Analyst Finance"]
POSTES_CLOTURES = [p for p in POSTES if p not in POSTES_ACTIFS]
def is_poste_actif(poste):
    return poste in POSTES_ACTIFS
GRILLE = {
    "Chef de Division Local Corporate": {
        "eliminatoire": ["A une expérience significative dans le secteur bancaire (minimum 5 ans)", "A un diplôme de niveau Bac+4 ou supérieur (Master, MBA ou équivalent)", "A géré un portefeuille de clients Corporate avec des résultats mesurables", "A une expérience managériale démontrée (encadrement d'équipe d'au moins 3 personnes)", "Maîtrise la gestion du risque de crédit et le suivi de portefeuille (NPL, provisions)"],
        "a_verifier": ["A piloté une activité Corporate avec des objectifs de revenus atteints", "A développé un portefeuille Corporate avec acquisition de nouveaux clients", "A encadré et évalué une équipe commerciale ou bancaire", "A suivi la qualité du portefeuille de crédit avec reporting à la direction", "A développé des ventes croisées (cross-selling) avec d'autres départements", "A produit ou supervisé des rapports de performance commerciale et financière", "A une connaissance de la réglementation bancaire locale (COBAC, BEAC)"],
        "signaux_forts": ["A piloté une division Corporate avec atteinte des objectifs de revenus", "A géré activement le ratio NPL avec des résultats chiffrés", "A une expérience avérée en cross-selling avec des équipes TSG ou Trade Finance", "A développé le portefeuille Corporate avec acquisition de clients majeurs", "A démontré un leadership fort avec développement des collaborateurs", "Possède une certification bancaire (Moody's, ITB, CFA, ou équivalent)", "A une connaissance du marché corporate tchadien ou de la zone CEMAC/UEMOA"],
        "points_attention": ["Parcours exclusivement back-office ou risques sans expérience commerciale Corporate", "Profil technique sans expérience managériale ni pilotage de P&L", "Expériences très courtes (moins de 2 ans par poste) sans progression hiérarchique", "CV sans résultats chiffrés (missions décrites sans indicateurs atteints)", "Mobilité géographique ou sectorielle excessive dans le parcours"]
    },
    "Chef de Section Compensation": {
        "eliminatoire": ["A une expérience en banque ou établissement financier réglementé", "A un diplôme de niveau Bac+3 minimum (Licence, Bachelor ou équivalent)", "A minimum 3 ans d'expérience en opérations bancaires ou back-office", "A une exposition aux opérations de compensation interbancaire", "A une connaissance des règles BEAC / GIMAC ou d'un système de compensation équivalent"],
        "a_verifier": ["Supervise quotidiennement les opérations de compensation interbancaire", "Gère les suspens, rejets et réclamations interbancaires", "Encadre et coordonne une équipe opérationnelle", "Utilise des systèmes bancaires de compensation (SYSTAC, SYGMA, SWIFT)", "Produit des reportings opérationnels ou réglementaires", "Participe à des contrôles internes, audits COBAC ou inspections réglementaires"],
        "signaux_forts": ["Maîtrise le règlement de positions nettes dans les délais réglementaires", "A une expérience dans une banque de la zone CEMAC / UEMOA", "A réussi des audits COBAC ou contrôles internes sans réserve majeure", "Gère une équipe avec des résultats mesurables", "Maîtrise le contrôle interne et la comptabilité bancaire (SYSCOHADA)"],
        "points_attention": ["Parcours purement comptable sans exposition aux opérations interbancaires", "Rôle uniquement administratif ou de support, sans responsabilité opérationnelle", "Absence de tout rôle managérial dans le parcours", "CV avec missions trop génériques, sans livrables ni résultats quantifiés"]
    },
    "Chargé(e) d'Administration de Crédit": {
        "eliminatoire": ["A une expérience dans une banque ou un établissement financier réglementé", "A un diplôme de niveau Bac+3 minimum (Licence, Bachelor ou équivalent)", "A minimum 1 an d'expérience dans une fonction bancaire", "A une exposition au cycle de vie du crédit bancaire", "A une connaissance des normes comptables bancaires ou de la réglementation COBAC"],
        "a_verifier": ["Gère le cycle complet d'un crédit (mise en place, suivi, garanties, clôture)", "Suit et sécurise les garanties (enregistrement, valorisation, renouvellement)", "Supervise les échéances et produit des alertes aux gestionnaires de portefeuille", "Détecte et remonte les impayés, dépassements ou incidents de portefeuille", "Produit des reportings de portefeuille (tableaux de bord, rapports)", "Participe à des comités de risque, audits internes ou inspections réglementaires", "Maîtrise un système bancaire de gestion du crédit (Finacle, T24, Amplitude)"],
        "signaux_forts": ["Maîtrise la norme IFRS 9 : staging du portefeuille (Stage 1, 2, 3), ECL, provisions", "Suit et sécurise les garanties avec coordination juridique", "Produit des reportings portefeuille (encours, impayés, dépassements, couverture)", "Participe aux comités de risque et traite les anomalies", "Maîtrise les Produits de Portefeuille (PP) et la politique de crédit (GCPPM)", "A réussi des audits ou contrôles internes sans réserve majeure", "Démontre une rigueur documentaire exemplaire"],
        "points_attention": ["Parcours purement commercial ou front-office sans exposition à l'administration des crédits", "Profil uniquement comptable (SYSCOHADA) sans gestion du cycle de crédit bancaire", "Profil exclusivement théorique (stage ou formation seule) sans expérience opérationnelle", "Expériences très courtes (< 1 an par poste) sans progression dans la fonction", "Absence de mention des outils bancaires (système de gestion du crédit, Excel avancé, reporting)"]
    },
    "Auditeur interne": {
        "eliminatoire": ["A une expérience réelle en audit interne ou externe", "A minimum 3 ans en audit bancaire ou cabinet d'audit", "A une connaissance des normes d'audit et contrôle interne", "A un diplôme de niveau Bac+4 ou supérieur", "A une expérience en rédaction de rapports d'audit"],
        "a_verifier": ["A réalisé des missions d'audit sur site", "Évalue les risques opérationnels", "Rédige des rapports d'audit détaillés", "Assure le suivi des recommandations", "Connaît les normes IIA / IPPF", "Maîtrise la réglementation bancaire (COBAC)", "A une expérience en audit IT ou systèmes d'information"],
        "signaux_forts": ["Possède une certification CIA / CPA / ACCA", "A une expérience dans une banque de la zone CEMAC / UEMOA", "A participé à des inspections réglementaires", "A une expertise en audit des risques de crédit", "Maîtrise les outils d'audit (ACL, IDEA, etc.)"],
        "points_attention": ["Profil purement comptable sans expérience d'audit", "Aucune expérience terrain en audit (uniquement du support)", "CV flou sur les missions d'audit réalisées", "Absence de connaissances en réglementation bancaire"]
    },
    "Chef service contrôle des engagements": {
        "eliminatoire": ["Maîtrise le risque crédit et l'analyse financière", "A une expérience significative en octroi de crédits", "A minimum 5 ans en institution financière", "A un diplôme de niveau Bac+4 ou supérieur", "A une expérience en animation de comité de crédit"],
        "a_verifier": ["Analyse financièrement les dossiers d'entreprises", "Structure des crédits complexes", "Anime des comités de crédit", "Encadre et manage une équipe", "Maîtrise la classification des risques (IFRS 9)", "A une expérience en restructuration de dossiers sensibles", "Possède une formation en risk management"],
        "signaux_forts": ["A géré des dossiers de crédit à enjeux importants", "A une expérience en banque Corporate", "A participé à des audits ou inspections réglementaires", "Possède une certification en risk management (FRM, PRMIA)"],
        "points_attention": ["Profil purement commercial sans analyse financière", "Aucune expérience en analyse de risque crédit", "CV orienté relation client uniquement"]
    },
    "Senior Finance Officer": {
        "eliminatoire": ["A une expérience en reporting financier structuré", "A une exposition aux états financiers", "A minimum 3 ans en département finance ou cabinet d'audit", "A une interaction avec les auditeurs", "A un diplôme de niveau Bac+4 ou supérieur en finance/comptabilité"],
        "a_verifier": ["Produit des états financiers", "Réalise le reporting groupe", "Connaît les normes IFRS", "Maîtrise les contraintes réglementaires", "A une expérience en consolidation de comptes", "Utilise des outils ERP (SPECTRA, CERBER, SAP)"],
        "signaux_forts": ["A une expertise en IFRS / consolidation", "A interagi avec les commissaires aux comptes (CAC)", "Maîtrise les outils SPECTRA / CERBER / ERP", "Possède une certification ACCA, CPA ou CFA", "A une expérience en reporting groupe"],
        "points_attention": ["Profil comptable junior sans responsabilité réelle", "Pas de responsabilité en production d'états financiers", "CV flou sur les livrables produits"]
    },
    "Market Risk Officer": {
        "eliminatoire": ["A une base solide en risques de marché", "A une exposition à FX / taux / liquidité", "A minimum 3 ans en institution financière", "A un diplôme de niveau Bac+4 ou supérieur en finance/quantitatif", "Maîtrise VaR ou stress testing"],
        "a_verifier": ["Analyse des positions de marché", "Maîtrise Excel avancé", "Connaît VBA ou Python", "Produit du reporting risque", "Connaît les produits FICC", "A une expérience en gestion ALM / liquidité"],
        "signaux_forts": ["Maîtrise Bâle II / III", "A une expérience en modélisation de risques", "Utilise des outils de quantification (R, Python)", "Possède une certification FRM ou équivalent", "A une expérience en reporting prudentiel"],
        "points_attention": ["CV trop théorique académique", "Aucune mention d'outils de modélisation", "Absence d'expérience en gestion de risques"]
    },
    "IT Réseau & Infrastructure": {
        "eliminatoire": ["A une expérience en réseau / infrastructure", "A une exposition à environnement critique", "A une notion de sécurité IT", "A minimum 2 ans d'expérience", "A une expérience en gestion de réseaux LAN/WAN/VPN"],
        "a_verifier": ["Gère les réseaux LAN/WAN/VPN", "Administre des serveurs Windows/Linux", "A une connaissance du Cloud (AWS, Azure, GCP)", "Gère les incidents IT", "Assure la disponibilité des systèmes", "A une expérience en cybersécurité / firewall"],
        "signaux_forts": ["A une certification Cisco ou Microsoft", "A une expérience en virtualisation (VMware, Hyper-V)", "A une expérience en systèmes bancaires core banking", "Maîtrise ITIL / gestion de services IT", "A une expérience en haute disponibilité / PRA/PCA"],
        "points_attention": ["Profil trop helpdesk sans expertise réseau", "CV sans détail technique précis", "Aucune mention de sécurité informatique"]
    },
    "Chef service reporting réglementaire": {
        "eliminatoire": ["A une comptabilité bancaire approfondie", "A une expérience en reporting réglementaire (BEAC, COBAC, SPECTRA)", "A minimum 5 ans en banque ou cabinet d'audit bancaire", "A un diplôme de niveau Bac+4 ou supérieur", "A une expérience en production de rapports réglementaires"],
        "a_verifier": ["Produit des rapports réglementaires", "Effectue le contrôle de cohérence des données", "Assure la veille réglementaire bancaire", "Interagit avec les autorités de tutelle", "Maîtrise SPECTRA / CERBER / outils BEAC", "Connaît les normes COBAC"],
        "signaux_forts": ["A une expertise en reporting prudentiel Bâle", "A une formation en comptabilité bancaire spécialisée", "A une expérience en audits réglementaires", "A participé à des inspections COBAC"],
        "points_attention": ["Profil généraliste sans spécialisation bancaire", "Aucune expérience en reporting réglementaire", "CV flou sur les livrables produits"]
    },
    "Archiviste (Administration Crédit)": {
        "eliminatoire": ["A une expérience en gestion documentaire structurée", "Démontre une rigueur dans son parcours", "A une expérience en archivage physique et électronique", "A une expérience en gestion de dossiers sensibles"],
        "a_verifier": ["Gère l'archivage physique et électronique", "Manipule des garanties ou contrats", "Utilise des systèmes GED", "Assure la traçabilité des documents", "Applique les procédures d'archivage", "A une expérience en banque ou juridique"],
        "signaux_forts": ["A une expérience en banque ou secteur juridique", "Manipule des garanties ou contrats", "A une certification en gestion documentaire", "A une expérience en dématérialisation"],
        "points_attention": ["Profil trop généraliste", "CV désorganisé sans expérience documentaire", "Absence de mention de GED ou d'archivage numérique"]
    },
    "Responsable Administration de Crédit": {
        "eliminatoire": ["A une expérience bancaire significative (minimum 3 ans en crédit/risque)", "A une exposition aux garanties ou à la conformité", "A un diplôme de niveau Bac+4 ou supérieur", "A une expérience en validation de dossiers de crédit", "A une expérience en gestion des garanties"],
        "a_verifier": ["A validé des dossiers de crédit", "A géré des garanties", "A participé à des audits", "Connaît IFRS 9", "Connaît COBAC / conformité", "A suivi un portefeuille / impayés"],
        "signaux_forts": ["Maîtrise IFRS 9", "Maîtrise COBAC / conformité", "A suivi un portefeuille avec résultats", "A participé à des comités de crédit", "Possède une certification en risk management"],
        "points_attention": ["Parcours trop comptable pur", "Rôle uniquement administratif sans responsabilité", "CV flou avec missions génériques"]
    },
    "Analyste Crédit CCB": {
        "eliminatoire": ["A une expérience en analyse crédit", "A une capacité à lire des états financiers", "A minimum 3 ans en institution financière", "A un diplôme de niveau Bac+4 ou supérieur en finance", "A une expérience en structuration de crédit"],
        "a_verifier": ["A travaillé avec des clients PME", "A travaillé avec des clients particuliers", "A structuré des crédits", "A rédigé des avis de crédit", "A réalisé des analyses financières (cash-flow)", "A participé à des comités de crédit"],
        "signaux_forts": ["Maîtrise l'analyse cash-flow", "A monté des crédits complexes", "A participé à des comités de crédit", "A une certification en analyse financière"],
        "points_attention": ["CV trop relation client sans analyse", "Aucune notion de risque", "Expériences très courtes sans progression"]
    },
    "Data Analyst Finance": {
        "eliminatoire": ["A une formation en Finance, Comptabilité, Contrôle de gestion, Statistiques, Data Analytics ou Informatique décisionnelle", "A un diplôme de niveau Bac+3 ou supérieur", "A une expérience en analyse financière, reporting financier, contrôle de gestion, audit ou data analytics", "Maîtrise Excel (TCD, formules, Power Query) - compétence incontournable", "A des connaissances en comptabilité et en états financiers (P&L, bilan, flux de trésorerie)"],
        "a_verifier": ["A produit des rapports financiers périodiques (mensuels, trimestriels)", "A conçu ou maintenu des tableaux de bord financiers (Power BI, Excel ou autre outil BI)", "A réalisé des analyses Budget / Réalisé / N-1 avec identification des écarts", "A travaillé avec SQL pour extraire ou interroger des données financières", "A assuré la réconciliation de données multi-sources (comptabilité / systèmes opérationnels)", "A participé à l'élaboration d'un budget ou d'un forecast financier", "A une expérience dans le secteur bancaire ou avec un Core Banking (FLEXCUBE, T24, Amplitude)"],
        "signaux_forts": ["Maîtrise explicite de Power BI (dashboards, DAX, Power Query) avec exemples concrets", "Expérience avérée en automatisation de reportings (Power Query, VBA, Python, outils ETL)", "Analyse d'écarts Budget / Réalisé / N-1 avec présentation à la Direction Financière ou à la DG", "Participation à la construction de modèles de prévision financière ou d'analyses de scénarios", "Exposition aux données bancaires : PNB, NPL, coût du risque, rentabilité par agence ou produit", "Maîtrise de SQL pour l'extraction et la manipulation de données en base relationnelle", "Connaissance de Python ou R pour des analyses statistiques avancées", "Mise en place de contrôles qualité sur les données et documentation des règles de calcul", "Résultats quantifiés dans le CV : gains de productivité, délais réduits, anomalies détectées"],
        "points_attention": ["Profil purement comptable sans exposition aux outils BI ou au reporting de gestion", "Profil exclusivement IT / développeur sans connaissance financière", "Expérience uniquement académique ou stage sans production de reportings réels en environnement professionnel", "CV sans aucun outil cité nommément", "Missions décrites en termes génériques sans livrables précis ni résultats mesurables", "Trous inexpliqués dans le parcours ou expériences très courtes sans progression visible"]
    }
}
POSTES_AVEC_SCORING_100 = ["Auditeur interne", "Chef service contrôle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marché", "Chef service reporting réglementaire"]
POSTES_AVEC_SCORING_12 = ["Chef de Section Compensation", "Chargé(e) d'Administration de Crédit"]
POSTES_AVEC_SCORING_14 = ["Chef de Division Local Corporate", "Data Analyst Finance"]
def get_score_max_for_poste(poste):
    if poste in POSTES_AVEC_SCORING_12:
        return 12
    if poste in POSTES_AVEC_SCORING_14:
        return 14
    if poste in POSTES_AVEC_SCORING_100:
        return 100
    return 10
def get_recommandation_from_score(score, poste=None):
    s = int(score)
    if poste and poste in POSTES_AVEC_SCORING_12:
        if s >= 10:
            return "Entretien prioritaire"
        elif s >= 7:
            return "Potentiel à évaluer en entretien"
        else:
            return "Rejet"
    if poste and poste in POSTES_AVEC_SCORING_14:
        if s >= 11:
            return "Entretien prioritaire"
        elif s >= 7:
            return "Potentiel à évaluer en entretien"
        else:
            return "Rejet"
    if poste and poste in POSTES_AVEC_SCORING_100:
        if s >= 80:
            return "Shortlist"
        elif s >= 70:
            return "À considérer"
        elif s >= 60:
            return "Faible"
        else:
            return "Rejet"
    if s >= 8:
        return "Entretien prioritaire"
    elif s >= 5:
        return "Potentiel à évaluer en entretien"
    else:
        return "Rejet"
def get_statut_from_decision(decision):
    if not decision:
        return 'en_attente'
    if "Entretien prioritaire" in decision or "Shortlist" in decision:
        return "retenu"
    elif "Potentiel" in decision or "considérer" in decision or "Faible" in decision:
        return "entretien"
    else:
        return "rejete"
def split_into_jobs(raw_text):
    separators = re.compile(r"(?:^|\n)(?=\s*(?:(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*(?:20\d{2}|19\d{2})|\d{1,2}[/\-\.](?:20\d{2}|19\d{2})|(?:depuis|de |from |since |desde |a partir de |starting |beginning)))", re.IGNORECASE | re.MULTILINE)
    blocks = separators.split(raw_text)
    return [b.strip() for b in blocks if b.strip()]
STAGE_MARKERS = [r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b', r'\bapprenti\b', r'\bapprentissage\b', r'\balternance\b', r'\bstage de fin\b', r'\bstage academique\b', r'\bstage professionnel\b', r'\bstage de formation\b', r'\bpfr\b', r'\bstage pfe\b', r'\bpfe\b', r'\bvolontariat\b', r'\btrainee\b']
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)
def is_stage_block(block_text):
    return bool(STAGE_PATTERN.search(block_text))
def extract_duration_years_from_block(block_text):
    years = 0.0
    text = block_text.lower()
    _ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouucaaAAEEEEIIOUUUCAAaaonaaon')
    text = text.translate(_ACCENT_MAP)
    duration_patterns = [r'(\d+[\.,]?\d*)\s*(?:ans?|annee?s?|years?|años?|anos?)', r'\(\s*(\d+)\s*\)\s*(?:ans?|annee?s?|years?)', r'\w+\s+\(\s*(\d+)\s*\)\s*(?:ans?|annee?s?|years?)', r'plus\s+de\s+(\d+)\s*(?:ans?|annee?s?|years?)', r'depuis\s+(?:plus\s+de\s+)?(\d+)\s*(?:ans?|annee?s?)']
    for dp in duration_patterns:
        m = re.search(dp, text)
        if m:
            try:
                years = float(m.group(1).replace(',', '.'))
                if 0 < years <= 40:
                    return years
            except (ValueError, IndexError):
                pass
    pattern_present = re.compile(r"(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})\s*(?:a|-|–|—|au|jusqu'au|to|until|au\s+)?\s*(?:aujourd'hui|present|actuel|en cours|now|current|actual|hoje|ce jour|nos\s+jours|a\s+nos\s+jours)", re.IGNORECASE)
    m = pattern_present.search(text)
    if m:
        start_year = int(m.group(2))
        current_year = datetime.datetime.now().year
        delta = current_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    pattern_range = re.compile(r"(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})\s*(?:a|-|–|—|au|jusqu'au|to|until)?\s*(?:(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*)?(20\d{2}|19\d{2})", re.IGNORECASE)
    m = pattern_range.search(text)
    if m:
        start_year = int(m.group(2))
        end_year = int(m.group(4))
        delta = end_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    m = re.search(r'(\d{1,2})[/\-\.](20\d{2}|19\d{2})\s*[-–—\.]?\s*(?:(\d{1,2})[/\-\.])?(20\d{2}|19\d{2}|present|current|now)', text)
    if m:
        start_year = int(m.group(2))
        end_raw = m.group(4)
        if re.match(r'\d{4}', str(end_raw)):
            end_year = int(end_raw)
        else:
            end_year = datetime.datetime.now().year
        delta = end_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    return 0.0
def detect_institution_type(text):
    text_lower = text.lower()
    commercial_banks = ['ecobank', 'orabank', 'uba', 'bicec', 'sgbc', 'cbc', 'bct', 'société générale', 'standard chartered', 'nsia banque', 'commercial bank', 'banque commerciale', 'investment bank', 'banque d affaires', 'credit institution', 'financial institution', 'banque', 'express union', 'coris bank']
    commercial_pattern = re.compile(r'\b(' + '|'.join(re.escape(b) for b in commercial_banks) + r')\b', re.IGNORECASE)
    if commercial_pattern.search(text_lower):
        return 'commercial_bank'
    return 'unknown'
def calculate_score_charge_admin_credit(cv_text, lettre_text, attestation_texts_list):
    poste = "Chargé(e) d'Administration de Crédit"
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized = normalize_for_matching(raw_full)[0]
    flags_elim = []
    diplome_ok = False
    diplome_patterns = [r'licence', r'bachelor', r'bac\+3', r'bac 3', r'baccalauréat.*université', r'master', r'mba', r'ingénieur', r'bac\+4', r'bac 4', r'bac\+5', r'bac 5', r'maîtrise', r'doctorat', r'phd', r'école de commerce', r'école supérieure']
    for pattern in diplome_patterns:
        if re.search(pattern, cv_text.lower()):
            diplome_ok = True
            break
    if not diplome_ok:
        flags_elim.append("Niveau de diplôme inférieur à Bac+3")
    banking_keywords = ['express union', 'coris bank', 'ecobank', 'orabank', 'uba', 'bicec', 'banque', 'bancaire', 'établissement financier', 'institution financière']
    blocks = split_into_jobs(cv_text)
    total_banking_years = 0.0
    for block in blocks:
        if is_stage_block(block):
            continue
        is_banking = False
        for kw in banking_keywords:
            if kw in block.lower():
                is_banking = True
                break
        if is_banking:
            duration = extract_duration_years_from_block(block)
            if duration > 0:
                total_banking_years += duration
    exp_bancaire_ok = total_banking_years >= 1.0
    if not exp_bancaire_ok:
        flags_elim.append(f"Moins de 1 an d'expérience bancaire ({total_banking_years:.1f} ans) - les stages ne sont pas comptabilisés")
    credit_cycle_ok = False
    credit_cycle_keywords = ['crédit', 'credit', 'dossier de crédit', 'analyse de crédit', 'instruction crédit', 'octroi', 'mise en place', 'suivi crédit', 'garantie', 'échéance', 'portefeuille', 'administration de crédit', 'back-office crédit', 'credit administration']
    for kw in credit_cycle_keywords:
        if kw in cv_text.lower():
            credit_cycle_ok = True
            break
    if not credit_cycle_ok:
        flags_elim.append("Aucune exposition au cycle de vie du crédit bancaire")
    reporting_ok = False
    reporting_keywords = ['reporting', 'rapport', 'tableau de bord', 'dashboard', 'statistiques', 'indicateur', 'kpi', 'report', 'suivi', 'monitoring']
    for kw in reporting_keywords:
        if kw in cv_text.lower():
            reporting_ok = True
            break
    if not reporting_ok:
        flags_elim.append("Aucune expérience de production de reportings")
    tools_ok = False
    tools_keywords = ['excel', 'word', 'powerpoint', 'outlook', 'office', 'bureautique', 'tableur']
    for kw in tools_keywords:
        if kw in cv_text.lower():
            tools_ok = True
            break
    if not tools_ok:
        flags_elim.append("Incapacité à utiliser des outils bureautiques courants")
    if flags_elim:
        return {'score': 0, 'score_max': 12, 'decision': 'Rejet', 'flags_eliminatoires': flags_elim, 'sous_scores': {"Adéquation formation/expérience au crédit": 0, "Exposition IFRS 9 / gestion portefeuille": 0, "Maîtrise outils bancaires": 0, "Cohérence et sérieux du parcours": 0, "Qualité CV + Lettre de motivation": 0}, 'checklist': {}, 'detail': f"REJET IMMÉDIAT - {len(flags_elim)} critère(s) éliminatoire(s) non satisfait(s)", 'points_forts': [], 'points_vigilance': flags_elim, 'synthese': f"Rejet immédiat : {', '.join(flags_elim[:3])}"}
    adequation = 0
    if re.search(r'(économie|gestion|finance|comptabilité|banque|commerce)', cv_text.lower()):
        adequation += 1
    credit_years = 0.0
    for block in blocks:
        if not is_stage_block(block) and ('crédit' in block.lower() or 'credit' in block.lower()):
            duration = extract_duration_years_from_block(block)
            if duration > 0:
                credit_years += duration
    if credit_years >= 3:
        adequation += 2
    elif credit_years >= 1:
        adequation += 1
    if re.search(r'(certification|certificat).*(bancaire|crédit|finance)', cv_text.lower()):
        adequation += 0.5
    adequation = min(3, int(adequation))
    ifrs_score = 0
    if re.search(r'ifrs|provisionnement|stage\s*[123]|ecl', cv_text.lower()):
        ifrs_score += 2
    if re.search(r'cobac|réglementation bancaire|conformité', cv_text.lower()):
        ifrs_score += 1
    if re.search(r'portefeuille|encours|impayés|dépassements', cv_text.lower()):
        ifrs_score += 0.5
    ifrs_score = min(3, int(ifrs_score))
    tools_score = 0
    banking_tools = ['amplitude', 'finacle', 't24', 'temenos', 'flexcube', 'sopra', 'système de gestion', 'ged']
    for tool in banking_tools:
        if tool in cv_text.lower():
            tools_score += 1
            break
    if re.search(r'excel|vba|power query', cv_text.lower()):
        tools_score += 1
    tools_score = min(2, tools_score)
    coherence = 0
    long_experience = False
    for block in blocks:
        if is_stage_block(block):
            continue
        duration = extract_duration_years_from_block(block)
        if duration >= 2:
            long_experience = True
            break
    if long_experience:
        coherence += 1
    if re.search(r'(banque|finance|économie|gestion)', cv_text.lower()):
        coherence += 1
    coherence = min(2, coherence)
    qualite = 0
    if re.search(r'\d+\s*%|\d+\s*dossiers|\d+\s*cas|\d+\s*rapports', cv_text.lower()):
        qualite += 1
    if lettre_text and len(lettre_text.strip()) > 50:
        if 'ecobank' in lettre_text.lower() and ('administration de crédit' in lettre_text.lower() or 'chargé' in lettre_text.lower()):
            qualite += 1
        elif 'chargé' in lettre_text.lower() and 'crédit' in lettre_text.lower():
            qualite += 0.5
    qualite = min(2, int(qualite))
    total_score = adequation + ifrs_score + tools_score + coherence + qualite
    total_score = min(12, total_score)
    if total_score >= 10:
        decision = "Entretien prioritaire"
    elif total_score >= 7:
        decision = "Potentiel à évaluer en entretien"
    else:
        decision = "Rejet"
    points_forts = []
    points_vigilance = []
    if adequation >= 2:
        points_forts.append(f"Expérience crédit de {credit_years:.1f} ans")
    if ifrs_score >= 2:
        points_forts.append("Connaissance IFRS 9 / COBAC")
    if tools_score >= 2:
        points_forts.append("Maîtrise des outils bancaires")
    if coherence >= 2:
        points_forts.append("Parcours cohérent et stable")
    if qualite >= 2:
        points_forts.append("CV détaillé avec résultats chiffrés")
    if adequation < 2:
        points_vigilance.append("Expérience crédit limitée")
    if ifrs_score < 2:
        points_vigilance.append("IFRS 9 à approfondir")
    if coherence < 2:
        points_vigilance.append("Parcours à stabiliser")
    if qualite < 2:
        points_vigilance.append("CV/lettre à enrichir")
    sous_scores = {"Adéquation formation/expérience au crédit": adequation, "Exposition IFRS 9 / gestion portefeuille": ifrs_score, "Maîtrise outils bancaires": tools_score, "Cohérence et sérieux du parcours": coherence, "Qualité CV + Lettre de motivation": qualite}
    synthese = _generate_synthese_rac(cv_text, lettre_text, total_score, points_forts, points_vigilance)
    return {'score': total_score, 'score_max': 12, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {total_score}/12 — {decision}", 'points_forts': points_forts, 'points_vigilance': points_vigilance, 'synthese': synthese}
def _generate_synthese_rac(cv_text, lettre_text, score, points_forts, points_vigilance):
    synthese = ""
    has_experience = bool(re.search(r'Express Union|Coris Bank|Ecobank|banque|bancaire|\d+\s*ans', cv_text.lower()))
    has_certification = bool(re.search(r'certification|certificat', cv_text.lower()))
    has_results = bool(re.search(r'\d+\s*%|\d+\s*dossiers|\d+\s*rapports', cv_text.lower()))
    lettre_personnalisee = bool(lettre_text and 'ecobank' in lettre_text.lower())
    if score >= 10:
        synthese = "Candidat très solide pour le poste. "
        if has_experience:
            synthese += "Expérience bancaire confirmée. "
        if has_certification:
            synthese += "Certifications pertinentes. "
        if has_results:
            synthese += "Résultats quantifiés démontrant la performance. "
        if lettre_personnalisee:
            synthese += "Lettre de motivation personnalisée. "
        synthese += "À recommander pour entretien prioritaire."
    elif score >= 7:
        synthese = "Bon profil avec du potentiel. "
        if has_experience:
            synthese += "Expérience bancaire présente. "
        else:
            synthese += "Expérience à consolider. "
        if not has_results:
            synthese += "Manque de résultats chiffrés. "
        synthese += "À convoquer en entretien."
    else:
        synthese = "Profil en dessous des attentes. "
        if not has_experience:
            synthese += "Expérience bancaire insuffisante. "
        if not has_certification:
            synthese += "Certifications professionnelles manquantes. "
        synthese += "Recommandation : ne pas retenir."
    return synthese
def calculate_score_chef_section_compensation(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Section Compensation"
    grille = GRILLE.get(poste, {})
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized = normalize_for_matching(raw_full)[0]
    flags = []
    for crit in grille.get('eliminatoire', []):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        if not ok:
            flags.append(crit)
    if flags:
        return {'score': 0, 'score_max': 12, 'decision': 'Rejet', 'flags_eliminatoires': flags, 'sous_scores': {"Adéquation de l'expérience (compensation interbancaire)": 0, "Exposition BEAC / GIMAC / SYSTAC": 0, "Capacité d'encadrement": 0, "Cohérence du parcours": 0, "Qualité CV + Lettre": 0}, 'checklist': {}, 'detail': f"REJET IMMÉDIAT - {len(flags)} critère(s) éliminatoire(s)", 'points_forts': [], 'points_vigilance': flags, 'synthese': f"Rejet immédiat : {', '.join(flags[:2])}"}
    from rapidfuzz import fuzz
    def check_crit(crit):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        return ok
    signaux_exp = ["Supervision quotidienne des opérations de compensation interbancaire", "Dénouement de positions nettes en fin de journée", "Gestion de suspens, rejets et réclamations interbancaires", "Utilisation de systèmes bancaires de compensation"]
    n_exp = sum(1 for c in signaux_exp if check_crit(c))
    adequation = min(3, n_exp)
    signaux_beac = ["BEAC / GIMAC / compensation interbancaire", "Règlement de positions nettes dans les délais réglementaires", "Expérience dans une banque de la zone CEMAC / UEMOA"]
    n_beac = sum(1 for c in signaux_beac if check_crit(c))
    exposition_beac = min(3, n_beac)
    encadrement_ok = check_crit("Encadrement et coordination d'une équipe opérationnelle")
    resultats_mesurables = check_crit("Gestion d'une équipe avec résultats mesurables")
    encadrement = (1 if encadrement_ok else 0) + (1 if resultats_mesurables else 0)
    n_points_attention = sum(1 for c in grille.get('points_attention', []) if check_crit(c))
    coherence = 2 if n_points_attention == 0 else (1 if n_points_attention <= 2 else 0)
    word_count = len(cv_text.split())
    has_quantified = bool(re.search(r'\d+\s*(%|pourcent|jours|heures|incidents|clients|operations|agences|collaborateurs)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified) else 0
    lettre_clean = (lettre_text or '').strip()
    if lettre_clean:
        poste_kw = ['compensation', 'beac', 'gimac', 'interbancaire', 'back-office']
        mentions_poste = any(kw in lettre_clean.lower() for kw in poste_kw)
        lettre_score = 1 if (len(lettre_clean.split()) >= 80 and mentions_poste) else 0
    else:
        lettre_score = 0
    sous_scores = {"Adéquation de l'expérience (compensation interbancaire)": adequation, "Exposition BEAC / GIMAC / SYSTAC": exposition_beac, "Capacité d'encadrement": encadrement, "Cohérence du parcours": coherence, "Qualité CV + Lettre": qualite_cv + lettre_score}
    total_score = sum(sous_scores.values())
    total_score = min(12, total_score)
    if total_score >= 10:
        decision = "Entretien prioritaire"
    elif total_score >= 7:
        decision = "Potentiel à évaluer en entretien"
    else:
        decision = "Rejet"
    return {'score': total_score, 'score_max': 12, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {total_score}/12 — {decision}", 'points_forts': ["Expérience en compensation interbancaire" if adequation >= 2 else ""], 'points_vigilance': ["Manque d'exposition BEAC" if exposition_beac < 2 else ""], 'synthese': f"Candidat avec un score de {total_score}/12"}
def calculate_score_chef_division_corporate(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Division Local Corporate"
    grille = GRILLE.get(poste, {})
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized = normalize_for_matching(raw_full)[0]
    flags = []
    for crit in grille.get('eliminatoire', []):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        if not ok:
            flags.append(crit)
    if flags:
        return {'score': 0, 'score_max': 14, 'decision': 'Rejet', 'flags_eliminatoires': flags, 'sous_scores': {"Expérience Corporate": 0, "Management d'équipe": 0, "Gestion du risque crédit": 0, "Cross-selling": 0, "Progression et cohérence": 0, "Qualité CV + Lettre": 0, "Certifications": 0}, 'checklist': {}, 'detail': f"REJET IMMÉDIAT - {len(flags)} critère(s) éliminatoire(s)", 'points_forts': [], 'points_vigilance': flags, 'synthese': f"Rejet immédiat : {', '.join(flags[:2])}"}
    from rapidfuzz import fuzz
    def check_crit(crit):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        return ok
    signaux_corp = ["Gestion de portefeuille Corporate", "Analyse crédit Corporate", "Relation clientèle Corporate"]
    n_corp = sum(1 for c in signaux_corp if check_crit(c))
    exp_corporate = min(3, n_corp)
    signaux_mgmt = ["Encadrement et management d'équipe", "Supervision de collaborateurs", "Animation d'équipe"]
    n_mgmt = sum(1 for c in signaux_mgmt if check_crit(c))
    management = min(3, n_mgmt)
    signaux_risque = ["Gestion du risque crédit", "Qualité du portefeuille", "Suivi des garanties"]
    n_risque = sum(1 for c in signaux_risque if check_crit(c))
    risque = min(2, n_risque)
    signaux_cs = ["Développement commercial", "Cross-selling", "Vente additionnelle"]
    n_cs = sum(1 for c in signaux_cs if check_crit(c))
    crossselling = min(2, n_cs)
    n_points_attention = sum(1 for c in grille.get('points_attention', []) if check_crit(c))
    progression = 2 if n_points_attention == 0 else (1 if n_points_attention <= 2 else 0)
    word_count = len(cv_text.split())
    has_quantified = bool(re.search(r'\d+\s*(%|pourcent|portefeuille|encours|millions|milliards|collaborateurs|equipe|clients)', cv_text.lower()))
    qualite_cv = 1 if (word_count >= 150 and has_quantified) else 0
    lettre_clean = (lettre_text or '').strip()
    certification_score = 0
    if lettre_clean:
        poste_keywords = ['corporate', 'grandes entreprises', 'division', 'chef', 'management', 'credit', 'banque']
        mentions_poste = any(kw in lettre_clean.lower() for kw in poste_keywords)
        is_generic = len(lettre_clean.split()) < 50 or not mentions_poste
        certification_score = 0 if is_generic else 1
    has_certif = check_crit("Certification bancaire ou formation spécialisée")
    if has_certif:
        certification_score = 1
    sous_scores = {"Expérience Corporate": exp_corporate, "Management d'équipe": management, "Gestion du risque crédit": risque, "Cross-selling": crossselling, "Progression et cohérence": progression, "Qualité CV + Lettre": qualite_cv, "Certifications": certification_score}
    total_score = sum(sous_scores.values())
    total_score = min(14, total_score)
    if total_score >= 11:
        decision = "Entretien prioritaire"
    elif total_score >= 7:
        decision = "Potentiel à évaluer en entretien"
    else:
        decision = "Rejet"
    points_forts = []
    if exp_corporate >= 2:
        points_forts.append("Expérience Corporate significative")
    if management >= 2:
        points_forts.append("Solide capacité managériale")
    if risque >= 2:
        points_forts.append("Maîtrise du risque crédit")
    if progression >= 2:
        points_forts.append("Parcours cohérent")
    points_vigilance = []
    if exp_corporate < 2:
        points_vigilance.append("Expérience Corporate à renforcer")
    if management < 2:
        points_vigilance.append("Management à consolider")
    if risque < 1:
        points_vigilance.append("Risque crédit à approfondir")
    synthese = f"Candidat avec un score de {total_score}/14. "
    if total_score >= 11:
        synthese += "Profil très solide pour le poste de Chef de Division Corporate. À recommander."
    elif total_score >= 7:
        synthese += "Profil intéressant avec du potentiel. À convoquer en entretien."
    else:
        synthese += "Profil insuffisant pour le poste."
    return {'score': total_score, 'score_max': 14, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {total_score}/14 — {decision}", 'points_forts': points_forts, 'points_vigilance': points_vigilance, 'synthese': synthese}
def calculate_score_data_analyst_finance(cv_text, lettre_text, attestation_texts_list):
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized = normalize_for_matching(raw_full)[0]
    flags_elim = []
    formation_keywords = ['finance', 'comptabilité', 'comptabilite', 'contrôle de gestion', 'controle de gestion', 'statistiques', 'statistique', 'data analytics', 'analyse de données', 'business intelligence', 'informatique décisionnelle', 'informatique decisionnelle', 'économie', 'economie']
    formation_ok = any(kw in cv_text.lower() for kw in formation_keywords)
    if not formation_ok:
        flags_elim.append("Formation en Finance, Comptabilité, Contrôle de gestion, Statistiques, Data Analytics ou Informatique décisionnelle")
    diplome_ok = False
    diplome_patterns = [r'bac\+3', r'bac 3', r'licence', r'bachelor', r'bac\+4', r'bac 4', r'master', r'mba', r'ingénieur', r'ingenieur', r'bac\+5', r'bac 5', r'maîtrise', r'maitrise', r'doctorat', r'phd', r'école de commerce', r'ecole de commerce', r'école supérieure', r'ecole superieure']
    for pattern in diplome_patterns:
        if re.search(pattern, cv_text.lower()):
            diplome_ok = True
            break
    if not diplome_ok:
        flags_elim.append("Diplôme de niveau Bac+3 ou supérieur")
    exp_keywords = ['analyse financière', 'analyse financiere', 'reporting financier', 'contrôle de gestion', 'controle de gestion', 'audit', 'data analytics', 'analyse de données', 'analyse de donnees', 'tableau de bord', 'dashboard', 'reporting', 'rapport financier']
    exp_ok = any(kw in cv_text.lower() for kw in exp_keywords)
    if not exp_ok:
        flags_elim.append("Expérience en analyse financière, reporting financier, contrôle de gestion, audit ou data analytics")
    excel_keywords = ['excel', 'power query', 'tableau croisé', 'tcd', 'formule excel', 'vba']
    excel_ok = any(kw in cv_text.lower() for kw in excel_keywords)
    if not excel_ok:
        flags_elim.append("Maîtrise Excel (TCD, formules, Power Query) - compétence incontournable")
    comptab_keywords = ['comptabilité', 'comptabilite', 'états financiers', 'etats financiers', 'p&l', 'bilan', 'flux de trésorerie', 'accounting', 'financial statements', 'income statement', 'balance sheet', 'cash flow']
    comptab_ok = any(kw in cv_text.lower() for kw in comptab_keywords)
    if not comptab_ok:
        flags_elim.append("Connaissances en comptabilité et en états financiers (P&L, bilan, flux de trésorerie)")
    if flags_elim:
        return {'score': 0, 'score_max': 14, 'decision': 'Rejet', 'flags_eliminatoires': flags_elim, 'sous_scores': {"Adéquation expérience (reporting/analyse/data)": 0, "Maîtrise outils BI (Excel/Power BI)": 0, "Connaissance SQL": 0, "Exposition données bancaires/Core Banking": 0, "Cohérence et progression": 0, "Qualité CV + Lettre": 0, "Compétences avancées": 0}, 'checklist': {}, 'detail': f"REJET IMMÉDIAT - {len(flags_elim)} critère(s) éliminatoire(s) non satisfait(s)", 'points_forts': [], 'points_vigilance': flags_elim, 'synthese': f"Rejet immédiat : {', '.join(flags_elim[:3])}"}
    exp_score = 0
    reporting_keywords = ['reporting', 'rapport', 'tableau de bord', 'dashboard', 'budget', 'réalisé', 'realise', 'écart', 'ecart', 'analyse', 'prévision', 'prevision', 'forecast', 'contrôle de gestion', 'controle de gestion', 'data analyst', 'analyste de données']
    found_exp = sum(1 for kw in reporting_keywords if kw in cv_text.lower())
    if found_exp >= 6:
        exp_score = 3
    elif found_exp >= 4:
        exp_score = 2
    elif found_exp >= 2:
        exp_score = 1
    bi_score = 0
    excel_advanced = ['excel avancé', 'excel avance', 'power query', 'tcd', 'tableau croisé', 'vba excel']
    powerbi = ['power bi', 'powerbi', 'dax']
    has_excel_advanced = any(kw in cv_text.lower() for kw in excel_advanced)
    has_powerbi = any(kw in cv_text.lower() for kw in powerbi)
    if has_powerbi and has_excel_advanced:
        bi_score = 3
    elif has_powerbi:
        bi_score = 2
    elif has_excel_advanced:
        bi_score = 1
    if 'tableau' in cv_text.lower() and bi_score > 0:
        bi_score = min(3, bi_score + 1)
    sql_score = 0
    sql_keywords = ['sql', 'base de données', 'base de donnees', 'extraction', 'requête', 'requete', 'data warehouse', 'etl', 'select', 'join']
    found_sql = sum(1 for kw in sql_keywords if kw in cv_text.lower())
    if found_sql >= 4:
        sql_score = 2
    elif found_sql >= 2:
        sql_score = 1
    bank_score = 0
    banking_keywords = ['banque', 'bancaire', 'core banking', 'flexcube', 't24', 'amplitude', 'financial institution', 'institution financière', 'pnb', 'npl', 'coût du risque', 'cout du risque', 'rentabilité', 'rentabilite', 'agence', 'produit bancaire', 'crédit', 'credit']
    found_bank = sum(1 for kw in banking_keywords if kw in cv_text.lower())
    if found_bank >= 4:
        bank_score = 2
    elif found_bank >= 2:
        bank_score = 1
    coher_score = 0
    blocks = split_into_jobs(cv_text)
    total_years = 0.0
    for block in blocks:
        if is_stage_block(block):
            continue
        duration = extract_duration_years_from_block(block)
        if duration > 0:
            total_years += duration
    if total_years >= 5:
        coher_score = 2
    elif total_years >= 3:
        coher_score = 1
    if re.search(r'(responsable|lead|senior|chef|manager|superviseur|coordinateur)', cv_text.lower()):
        coher_score = min(2, coher_score + 1)
    qualite_score = 0
    has_quantified = bool(re.search(r'\d+\s*(%|pourcent|réduction|reduction|gain|amélioration|amelioration|efficacité|efficacite)', cv_text.lower()))
    has_tools = bool(re.search(r'(power bi|powerbi|sql|excel|python|r|tableau|etl|vba|dax)', cv_text.lower()))
    if has_quantified and has_tools:
        qualite_score = 1
    if lettre_text and len(lettre_text.strip()) > 80:
        lettre_kw = ['data', 'finance', 'analyste', 'reporting', 'dashboard', 'analyse', 'données', 'donnees']
        if any(kw in lettre_text.lower() for kw in lettre_kw):
            if 'power bi' in lettre_text.lower() or 'excel' in lettre_text.lower() or 'sql' in lettre_text.lower():
                qualite_score = 1
    avance_score = 0
    advanced_keywords = ['python', 'r', 'automatisation', 'modélisation', 'modelisation', 'prévision', 'prevision', 'scénario', 'scenario', 'reporting réglementaire', 'reporting reglementaire', 'machine learning']
    found_adv = sum(1 for kw in advanced_keywords if kw in cv_text.lower())
    if found_adv >= 2:
        avance_score = 1
    total_score = exp_score + bi_score + sql_score + bank_score + coher_score + qualite_score + avance_score
    total_score = min(14, total_score)
    if total_score >= 11:
        decision = "Entretien prioritaire"
    elif total_score >= 7:
        decision = "Potentiel à évaluer en entretien"
    else:
        decision = "Rejet"
    points_forts = []
    points_vigilance = []
    if exp_score >= 2:
        points_forts.append("Expérience en reporting/analyse financière")
    if bi_score >= 2:
        points_forts.append("Maîtrise des outils BI (Excel/Power BI)")
    if sql_score >= 2:
        points_forts.append("Maîtrise de SQL")
    if bank_score >= 2:
        points_forts.append("Exposition au secteur bancaire")
    if coher_score >= 2:
        points_forts.append("Parcours cohérent avec progression")
    if avance_score >= 1:
        points_forts.append("Compétences avancées (Python/R/automatisation)")
    if exp_score < 2:
        points_vigilance.append("Expérience en analyse financière limitée")
    if bi_score < 2:
        points_vigilance.append("Maîtrise des outils BI à renforcer")
    if sql_score < 1:
        points_vigilance.append("Compétences SQL à approfondir")
    if bank_score < 1:
        points_vigilance.append("Exposition au secteur bancaire limitée")
    sous_scores = {"Adéquation expérience (reporting/analyse/data)": exp_score, "Maîtrise outils BI (Excel/Power BI)": bi_score, "Connaissance SQL": sql_score, "Exposition données bancaires/Core Banking": bank_score, "Cohérence et progression": coher_score, "Qualité CV + Lettre": qualite_score, "Compétences avancées": avance_score}
    synthese = f"Candidat avec un score de {total_score}/14. "
    if total_score >= 11:
        synthese += "Profil très solide pour le poste de Data Analyst Finance. Excellente adéquation avec les exigences du poste. À recommander pour entretien prioritaire."
    elif total_score >= 7:
        synthese += "Profil intéressant avec des compétences pertinentes. Certains domaines sont à approfondir mais le potentiel est présent. À convoquer en entretien."
    else:
        synthese += "Profil insuffisant pour le poste. Manque de compétences clés en analyse financière et outils BI."
    return {'score': total_score, 'score_max': 14, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {total_score}/14 — {decision}", 'points_forts': points_forts, 'points_vigilance': points_vigilance, 'synthese': synthese}
def analyze_cv_against_grille(cv_text, lettre_text, attestation_texts_list, poste):
    if not cv_text or len(cv_text.strip()) < 50:
        return {'score': 0, 'checklist': {}, 'flags_eliminatoires': ['CV non analysable'], 'signaux_detectes': [], 'details': {'error': 'CV vide'}, 'score_breakdown': {'bloc1_eliminatoire': True, 'score_final': 0, 'note': 'CV non analysable'}}
    grille = GRILLE.get(poste)
    if not grille:
        return {'score': 0, 'checklist': {}, 'flags_eliminatoires': [f'Poste inconnu: {poste}'], 'signaux_detectes': [], 'details': {}, 'score_breakdown': {}}
    all_att_raw = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att_raw
    normalized = normalize_for_matching(raw_full)[0]
    checklist = {}
    flags_elim = []
    signaux = []
    points_bloc2 = 0
    points_bloc3 = 0
    details = {'cv_words': len(cv_text.split()), 'lettre_words': len((lettre_text or "").split()), 'attestation_words': len(all_att_raw.split()), 'criteres_valides_bloc2': [], 'signaux_valides_bloc3': [], 'matching_details': {}, 'documents_analyses': {'cv': len(cv_text) > 0, 'lettre': len(lettre_text or "") > 0, 'certificats': len(attestation_texts_list) if attestation_texts_list else 0}}
    eliminatoire_failed = False
    for i, crit in enumerate(grille['eliminatoire']):
        key = f"elim_{i}"
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        if not is_present:
            eliminatoire_failed = True
            flags_elim.append(f"{crit} (confiance: {confidence:.0%})")
            details['matching_details'][crit] = {'found': False, 'confidence': confidence, 'status': 'MANQUANT'}
        else:
            details['matching_details'][crit] = {'found': True, 'confidence': confidence, 'matched': found_kws}
    for i, crit in enumerate(grille['a_verifier']):
        key = f"verif_{i}"
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'confidence': confidence, 'matched': found_kws if is_present else []}
        if is_present:
            points_bloc2 += 1
            details['criteres_valides_bloc2'].append(f"{crit}")
    for i, crit in enumerate(grille['signaux_forts']):
        key = f"signal_{i}"
        is_present, confidence, found_kws = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        checklist[key] = is_present
        details['matching_details'][crit] = {'found': is_present, 'confidence': confidence, 'matched': found_kws if is_present else []}
        if is_present:
            points_bloc3 += 2
            signaux.append(crit)
            details['signaux_valides_bloc3'].append(f"{crit}")
    adequation = min(3, len([k for k, v in checklist.items() if k.startswith('elim_') and v]))
    coherence = min(2, points_bloc2)
    risque_metier = min(3, len(signaux))
    qualite_cv = 1 if (points_bloc2 + points_bloc3) >= 5 else 0
    lettre_motiv = 1 if lettre_text and len(lettre_text.strip()) > 50 else 0
    score_final = min(10, adequation + coherence + risque_metier + qualite_cv + lettre_motiv)
    nb_elim_manquants = len(flags_elim)
    return {'score': score_final, 'checklist': checklist, 'flags_eliminatoires': flags_elim if nb_elim_manquants > 0 else [], 'signaux_detectes': signaux, 'details': details, 'score_breakdown': {'bloc1_eliminatoire': False, 'adequation_experience': adequation, 'coherence_parcours': coherence, 'exposition_risque_metier': risque_metier, 'qualite_cv': qualite_cv, 'lettre_motivation': lettre_motiv, 'score_final': score_final, 'note': f"Score: {score_final}/10"}}
KEYWORD_MAPPING = {
    "Expérience bancaire": ["banque", "bancaire", "etablissement bancaire", "institution bancaire", "banque commerciale", "microfinance", "etablissement financier", "institution financiere", "secteur bancaire", "groupe bancaire", "filiale bancaire", "bank", "banking", "financial institution", "credit institution", "commercial bank", "ecobank", "orabank", "uba", "finadev", "ucec", "microfinance"],
    "Minimum 3 ans en crédit / risque (hors stage)": ["EXP_CREDIT_3ANS"],
    "Minimum 1 an d'expérience dans une fonction bancaire": ["EXP_BANK_1ANS"],
    "Minimum 3 ans en opérations bancaires ou back-office (hors stage)": ["EXP_BACKOFFICE_3ANS"],
    "A une exposition au cycle de vie du crédit bancaire": ["cycle de credit", "mise en place credit", "suivi credit", "garantie", "echeances credit", "credit administration", "administration de credit"],
    "A une connaissance des normes comptables bancaires ou de la réglementation COBAC": ["cobac", "reglementation bancaire", "ifrs 9", "normes ifrs", "comptabilite bancaire", "syscohada", "bale ii", "bale iii"]
}
EXP_MIN_YEARS_MAP = {"EXP_CREDIT_3ANS": 3.0, "EXP_BANK_1ANS": 1.0, "EXP_BACKOFFICE_3ANS": 3.0}
DOMAIN_KEYWORDS_MAP = {"EXP_CREDIT_3ANS": ["credit", "risque", "banque", "bancaire", "institution financiere", "analyste", "charge", "gestionnaire", "loan", "credit analysis"], "EXP_BANK_1ANS": ["credit", "banque", "bancaire", "administration credit", "back office", "back-office", "risque", "risk", "analyse credit", "credit analysis", "loan", "institution financiere", "financial institution", "banking", "credit officer", "credit analyst", "credit administrator", "charge de credit", "gestionnaire credit", "analyste credit", "operations bancaires", "banking operations", "portfolio", "portefeuille", "garantie", "collateral"], "EXP_BACKOFFICE_3ANS": ["back-office", "back office", "operations bancaires", "compensation", "interbancaire", "banque", "bancaire", "middle office", "moyens de paiement", "traitement des operations", "chambre de compensation"]}
def check_criterion_match_advanced(criterion, normalized_text, raw_full_text="", tokens=None, poste=None):
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords:
        return False, 0.5, []
    exp_markers = [kw for kw in keywords if kw.startswith("EXP_")]
    if exp_markers:
        marker = exp_markers[0]
        min_years = EXP_MIN_YEARS_MAP.get(marker, 3.0)
        domain_kws = DOMAIN_KEYWORDS_MAP.get(marker, [])
        domain_kws_n = [normalize_for_matching(k)[0] for k in domain_kws]
        found = has_experience_years_strict(raw_full_text, min_years, domain_kws_n, poste)
        return found, 1.0 if found else 0.0, ([marker] if found else [])
    best_score = 0.0
    found_kws = []
    text_clean, text_tokens = normalize_for_matching(normalized_text)
    for kw in keywords:
        kw_clean, kw_tokens = normalize_for_matching(kw)
        if contains_negative_context(raw_full_text, kw):
            continue
        if kw_clean in text_clean:
            found_kws.append(kw)
            best_score = max(best_score, 1.0)
            continue
        if RAPIDFUZZ_AVAILABLE and len(kw_clean) >= 4:
            ratio = fuzz.partial_ratio(kw_clean, text_clean)
            if ratio >= 80:
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}~{ratio/100:.2f}")
                    best_score = max(best_score, ratio / 100)
                continue
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.6):
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                    best_score = max(best_score, len(common) / len(kw_tokens))
    return best_score >= 0.60, round(best_score, 2), found_kws
def has_experience_years_strict(full_raw_text, min_years, domain_keywords=None, poste=None):
    blocks = split_into_jobs(full_raw_text)
    total_years = 0.0
    years_patterns = [r'(\d+)\s*(?:années?|ans?)', r'plus\s+de\s+(\d+)\s*(?:années?|ans?)', r'\(\s*(\d+)\s*\)\s*(?:années?|ans?)', r'\w+\s+\(\s*(\d+)\s*\)\s*(?:années?|ans?)', r'depuis\s+(?:plus\s+de\s+)?(\d+)\s*(?:années?|ans?)', r'(\d+)\s*(?:années?|ans?)\s+(?:d[ée]?expérience|dans|en|de)', r'expérience\s+(?:de\s+)?(\d+)\s*(?:années?|ans?)']
    text_lower = full_raw_text.lower()
    for pattern in years_patterns:
        matches = re.findall(pattern, text_lower, re.IGNORECASE)
        for match in matches:
            try:
                years = float(match)
                if years >= min_years:
                    return True
            except (ValueError, TypeError):
                continue
    banking_posts = ["Responsable Administration de Crédit", "Analyste Crédit CCB", "Senior Finance Officer", "Market Risk Officer", "Chargé(e) d'Administration de Crédit"]
    for block in blocks:
        if is_stage_block(block):
            continue
        if poste in banking_posts:
            if detect_institution_type(block) == 'unknown':
                continue
        duration = extract_duration_years_from_block(block)
        if duration > 0:
            total_years += duration
    return total_years >= min_years
def analyze_cv_intelligent(cv_text, lettre_text, attestation_texts_list, poste):
    if not IA_ANALYSE_ACTIVE or not cv_text or len(cv_text.strip()) < 50 or poste not in GRILLE:
        return None
    tool = {"name": "soumettre_analyse_candidature", "description": "Soumet l'analyse structurée d'une candidature.", "input_schema": {"type": "object", "properties": {"eliminatoire": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "valide": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "valide", "justification"]}}, "a_verifier": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "detecte": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "detecte", "justification"]}}, "signaux_forts": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "detecte": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "detecte", "justification"]}}, "points_attention": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "present": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "present", "justification"]}}, "lettre_motivation": {"type": "object", "properties": {"presente": {"type": "boolean"}, "coherente_avec_cv": {"type": "boolean"}, "generique_ou_copiee": {"type": "boolean"}, "qualite_redactionnelle": {"type": "string", "enum": ["bonne", "moyenne", "faible", "non_evaluable"]}, "eliminatoire": {"type": "boolean"}, "commentaire": {"type": "string"}}, "required": ["presente", "coherente_avec_cv", "generique_ou_copiee", "qualite_redactionnelle", "eliminatoire", "commentaire"]}, "diplomes": {"type": "object", "properties": {"niveau_suffisant": {"type": "boolean"}, "domaine_pertinent": {"type": "boolean"}, "atout_complementaire_detecte": {"type": "boolean"}, "commentaire": {"type": "string"}}, "required": ["niveau_suffisant", "domaine_pertinent", "atout_complementaire_detecte", "commentaire"]}, "sous_scores": {"type": "object", "additionalProperties": {"type": "integer"}}, "score_total": {"type": "integer"}, "decision": {"type": "string"}, "points_forts": {"type": "array", "items": {"type": "string"}}, "points_vigilance": {"type": "array", "items": {"type": "string"}}, "synthese_recruteur": {"type": "string"}}, "required": ["eliminatoire", "a_verifier", "signaux_forts", "points_attention", "lettre_motivation", "diplomes", "sous_scores", "score_total", "decision", "points_forts", "points_vigilance", "synthese_recruteur"]}}
    SYSTEM_PROMPT_RECRUTEUR = """Tu es un responsable recrutement senior avec 15 ans d'expérience dans le secteur bancaire en Afrique centrale et de l'Ouest (CEMAC/UEMOA).
REGLES ABSOLUES D'AUTHENTICITE :
1. Tu ne JAMAIS inventer de faits qui ne sont PAS dans les documents fournis.
2. Si une information n'est PAS explicitement mentionnée, tu considères qu'elle N'EXISTE PAS.
3. Tu ne fais AUCUNE supposition, AUCUNE interprétation excessive.
4. Les stages, bénévolats et formations NE COMPTENT PAS comme expérience professionnelle.
5. Tu justifies CHAQUE évaluation avec une citation courte du document concerné.
6. Tu suis STRICTEMENT la grille fournie."""
    try:
        grille = GRILLE.get(poste, {})
        def fmt_list(items):
            return "\n".join(f"  {i+1}. {c}" for i, c in enumerate(items)) if items else "  (aucun)"
        user_msg = f"""POSTE : {poste}
GRILLE :
Eliminatoires :
{fmt_list(grille.get('eliminatoire', []))}
A vérifier :
{fmt_list(grille.get('a_verifier', []))}
Signaux forts :
{fmt_list(grille.get('signaux_forts', []))}
Points attention :
{fmt_list(grille.get('points_attention', []))}
DOCUMENTS :
CV : {cv_text[:8000]}
Lettre : {lettre_text[:3000] if lettre_text else '(aucune)'}
Attestations : {''.join(attestation_texts_list)[:3000] if attestation_texts_list else '(aucune)'}"""
        with _ia_semaphore:
            response = _claude_client.messages.create(model=ANTHROPIC_MODEL, max_tokens=4096, temperature=0, system=SYSTEM_PROMPT_RECRUTEUR, tools=[tool], tool_choice={"type": "tool", "name": "soumettre_analyse_candidature"}, messages=[{"role": "user", "content": user_msg}])
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if not tool_use:
            return None
        analyse = tool_use.input
        flags_elim = [e['critere'] for e in analyse.get('eliminatoire', []) if not e.get('valide')]
        lm = analyse.get('lettre_motivation', {})
        if lm.get('eliminatoire'):
            flags_elim.append(f"Lettre: {lm.get('commentaire', 'éliminatoire')}")
        score_total = 0 if flags_elim else int(analyse.get('score_total', 0))
        decision = get_recommandation_from_score(score_total, poste)
        score_max = get_score_max_for_poste(poste)
        return {'score': score_total, 'score_max': score_max, 'checklist': {}, 'flags_eliminatoires': flags_elim, 'signaux_detectes': [s['critere'] for s in analyse.get('signaux_forts', []) if s.get('detecte')], 'details': {'moteur': 'IA (Claude)', 'points_forts': analyse.get('points_forts', []), 'points_vigilance': analyse.get('points_vigilance', []), 'synthese_recruteur': analyse.get('synthese_recruteur', '')}, 'score_breakdown': {'bloc1_eliminatoire': bool(flags_elim), 'moteur_analyse': 'ia', 'sous_scores': analyse.get('sous_scores', {}), 'score_final': score_total, 'score_max': score_max, 'decision': decision}}
    except Exception as e:
        logger.error(f"IA analyse erreur: {e}")
        return None
def get_display_status(c):
    statut = c.get('statut', 'en_attente')
    if statut == "rejete":
        return "rejete"
    if statut == "retenu":
        return "retenu"
    if statut == "entretien":
        return "entretien"
    decision = c.get('decision', '')
    if decision:
        if "Entretien prioritaire" in decision or "Shortlist" in decision:
            return "retenu"
        elif "Potentiel" in decision or "considérer" in decision or "Faible" in decision:
            return "entretien"
        else:
            return "rejete"
    return 'en_attente'
def generate_detailed_reason(candidat, poste, score, score_max):
    statut = candidat.get('statut', 'en_attente')
    details = candidat.get('analyse_details_parsed', {})
    flags = candidat.get('flags_eliminatoires_parsed', [])
    strengths = details.get('points_forts', [])
    weaknesses = details.get('points_vigilance', [])
    sous_scores = candidat.get('score_breakdown_parsed', {}).get('sous_scores', {})
    note = candidat.get('note', '')
    decision_auto = get_recommandation_from_score(score, poste)
    if statut == "retenu":
        if strengths:
            lines = ["POINTS FORTS :"]
            for s in strengths[:4]:
                lines.append(f"  • {s}")
            if sous_scores:
                for key, value in sous_scores.items():
                    if value > 0:
                        lines.append(f"  • {key}: {value}/3")
            if note and "Décision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if note and "Décision" not in note and len(note) > 5:
            return f"RETENU - {note}"
        return "RETENU - Candidature retenue"
    if statut == "entretien":
        lines = ["POTENTIEL À ÉVALUER :"]
        if strengths:
            for s in strengths[:2]:
                lines.append(f"  • {s}")
        if weaknesses:
            lines.append("Points à vérifier :")
            for w in weaknesses[:2]:
                lines.append(f"  • {w}")
        if note and "Décision" not in note and len(note) > 5:
            lines.append(f"\nNOTE RECRUTEUR : {note}")
        return "\n".join(lines)
    if statut == "rejete":
        if flags:
            lines = ["CRITÈRES ÉLIMINATOIRES NON SATISFAITS :"]
            for flag in flags[:4]:
                clean = str(flag).replace('❌', '').replace('⚠️', '').strip()
                if clean and len(clean) > 3:
                    lines.append(f"  • {clean}")
            if len(flags) > 4:
                lines.append(f"  • +{len(flags)-4} autre(s)")
            if note and "Décision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if weaknesses:
            lines = ["POINTS DE VIGILANCE :"]
            for w in weaknesses[:4]:
                lines.append(f"  • {w}")
            if note and "Décision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if note and "Décision" not in note and len(note) > 5:
            return f"REJETÉ - {note}"
        if score == 0:
            return "REJETÉ - Analyse automatique : le candidat ne répond pas aux critères éliminatoires du poste"
        if score < 7:
            return f"REJETÉ - Score insuffisant ({score}/{score_max}) - Profil ne correspond pas aux exigences du poste"
        return "REJETÉ - Profil ne correspond pas aux exigences du poste"
    else:
        if flags:
            lines = ["CRITÈRES ÉLIMINATOIRES :"]
            for flag in flags[:3]:
                clean = str(flag).replace('❌', '').replace('⚠️', '').strip()
                if clean and len(clean) > 5:
                    lines.append(f"  • {clean}")
            return "\n".join(lines)
        if "Entretien prioritaire" in decision_auto or "Shortlist" in decision_auto:
            lines = ["PROFIL RECOMMANDÉ :"]
            if strengths:
                for s in strengths[:4]:
                    lines.append(f"  • {s}")
            if sous_scores:
                for key, value in sous_scores.items():
                    if value > 0:
                        lines.append(f"  • {key}: {value}/3")
            return "\n".join(lines)
        elif "Potentiel" in decision_auto:
            lines = ["POTENTIEL À ÉVALUER :"]
            if strengths:
                for s in strengths[:2]:
                    lines.append(f"  • {s}")
            if weaknesses:
                lines.append("Points de vigilance :")
                for w in weaknesses[:2]:
                    lines.append(f"  • {w}")
            return "\n".join(lines)
        else:
            lines = ["NON RETENU - Raisons :"]
            if weaknesses:
                for w in weaknesses[:3]:
                    lines.append(f"  • {w}")
            if not weaknesses and not flags:
                lines.append("  • Profil ne correspond pas aux exigences du poste")
            return "\n".join(lines)
def generate_excel_report_enhanced(candidats_data, poste_filter=None):
    if not OPENPYXL_AVAILABLE:
        return None
    wb = Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    ws_summary = wb.create_sheet(title="Vue d'ensemble")
    summary_border = Border(left=Side(style='thin', color='000000'), right=Side(style='thin', color='000000'), top=Side(style='thin', color='000000'), bottom=Side(style='thin', color='000000'))
    ws_summary.merge_cells('A1:E1')
    title_cell = ws_summary['A1']
    title_cell.value = "RAPPORT DE RECRUTEMENT - RecrutBank"
    title_cell.font = Font(bold=True, size=18, color="FFFFFF")
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    title_cell.fill = PatternFill(start_color="1F4E79", end_color="4472C4", fill_type="solid")
    ws_summary.row_dimensions[1].height = 40
    ws_summary['A2'] = f"Genere le {datetime.datetime.now().strftime('%d/%m/%Y a %H:%M')}"
    ws_summary['A2'].font = Font(italic=True, size=10, color="666666")
    total = len(candidats_data)
    retenus = sum(1 for c in candidats_data if get_display_status(c) == 'retenu')
    entretien = sum(1 for c in candidats_data if get_display_status(c) == 'entretien')
    rejetes = sum(1 for c in candidats_data if get_display_status(c) == 'rejete')
    en_attente = total - retenus - entretien - rejetes
    ws_summary['A4'] = "STATISTIQUES GLOBALES"
    ws_summary['A4'].font = Font(bold=True, size=14, color="1F4E79")
    ws_summary['A4'].fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    stats_headers = ['Total', 'Retenus', 'Entretien', 'Rejetes', 'En Attente']
    stats_values = [total, retenus, entretien, rejetes, en_attente]
    for col, (header, value) in enumerate(zip(stats_headers, stats_values), 1):
        cell_header = ws_summary.cell(row=6, column=col, value=header)
        cell_header.font = Font(bold=True, size=10, color="FFFFFF")
        cell_header.alignment = Alignment(horizontal='center')
        cell_header.border = summary_border
        cell_header.fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
        cell_value = ws_summary.cell(row=7, column=col, value=value)
        cell_value.font = Font(bold=True, size=12)
        cell_value.alignment = Alignment(horizontal='center')
        cell_value.border = summary_border
        if header == 'Retenus':
            cell_value.fill = PatternFill(start_color="00B050", end_color="00B050", fill_type="solid")
            cell_value.font = Font(color="FFFFFF", bold=True, size=12)
        elif header == 'Rejetes':
            cell_value.fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
            cell_value.font = Font(color="FFFFFF", bold=True, size=12)
        elif header == 'Entretien':
            cell_value.fill = PatternFill(start_color="FFC000", end_color="FFC000", fill_type="solid")
            cell_value.font = Font(color="000000", bold=True, size=12)
        elif header == 'En Attente':
            cell_value.fill = PatternFill(start_color="92D050", end_color="92D050", fill_type="solid")
            cell_value.font = Font(color="000000", bold=True, size=12)
    for col in range(1, 6):
        ws_summary.column_dimensions[get_column_letter(col)].width = 20
    if poste_filter:
        postes_to_export = [poste_filter]
    else:
        postes_to_export = list(dict.fromkeys(c.get('poste', '') for c in candidats_data if c.get('poste')))
    for poste in postes_to_export:
        candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
        if not candidats_poste:
            continue
        candidats_poste.sort(key=lambda x: -int(x.get('score', 0)))
        sheet_name = poste[:31] if len(poste) > 31 else poste
        ws = wb.create_sheet(title=sheet_name)
        ws.merge_cells('A1:H1')
        title_cell = ws['A1']
        title_cell.value = f"CANDIDATURES - {poste}"
        title_cell.font = Font(bold=True, size=14, color="FFFFFF")
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        title_cell.fill = PatternFill(start_color="1F4E79", end_color="4472C4", fill_type="solid")
        ws.row_dimensions[1].height = 35
        score_max = get_score_max_for_poste(poste)
        scores = [int(c.get('score', 0)) for c in candidats_poste]
        meilleur = max(scores) if scores else 0
        moyenne = sum(scores) / len(scores) if scores else 0
        ws.merge_cells('A2:H2')
        ws['A2'] = f"{len(candidats_poste)} candidat(s) | Score max: {meilleur}/{score_max} | Moyenne: {moyenne:.1f}/{score_max}"
        ws['A2'].font = Font(italic=True, size=10, color="333333")
        ws['A2'].alignment = Alignment(horizontal='center')
        headers = ['Rang', 'N° Dossier', 'Candidat', 'Email', f'Score /{score_max}', 'Decision', 'Analyse detaillee']
        header_fill = PatternFill(start_color="1F4E79", end_color="4472C4", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, size=10)
        header_border = Border(left=Side(style='medium', color='1F4E79'), right=Side(style='medium', color='1F4E79'), top=Side(style='medium', color='1F4E79'), bottom=Side(style='medium', color='1F4E79'))
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = header_border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        ws.row_dimensions[3].height = 40
        col_widths = [6, 14, 30, 35, 12, 18, 70]
        for col, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = w
        cell_border = Border(left=Side(style='thin', color='CCCCCC'), right=Side(style='thin', color='CCCCCC'), top=Side(style='thin', color='CCCCCC'), bottom=Side(style='thin', color='CCCCCC'))
        for row_i, c in enumerate(candidats_poste, 4):
            score = int(c.get('score', 0))
            score_max_local = get_score_max_for_poste(poste)
            decision_final = ""
            rec_color = ""
            statut = c.get('statut', 'en_attente')
            if statut == "rejete":
                decision_final = "Rejeté"
                rec_color = "FF0000"
            elif statut == "retenu":
                decision_final = "Retenu"
                rec_color = "00B050"
            elif statut == "entretien":
                decision_final = "Entretien"
                rec_color = "FFC000"
            else:
                decision_final = get_recommandation_from_score(score, poste)
                if "Entretien prioritaire" in decision_final or "Shortlist" in decision_final:
                    rec_color = "00B050"
                elif "Potentiel" in decision_final or "considérer" in decision_final or "Faible" in decision_final:
                    rec_color = "FFC000"
                else:
                    rec_color = "FF0000"
            motif = generate_detailed_reason(c, poste, score, score_max_local)
            nom_complet = f"{c.get('prenom', '')} {c.get('nom', '')}".strip() or '–'
            row_data = [row_i - 3, c.get('numero_dossier', '') or '–', nom_complet, c.get('email', '') or '–', f"{score}/{score_max_local}", decision_final, motif]
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_i, column=col, value=val if val is not None else '')
                cell.border = cell_border
                if col == 5:
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.font = Font(bold=True, size=11)
                elif col == 6:
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.fill = PatternFill(start_color=rec_color, end_color=rec_color, fill_type="solid")
                    cell.font = Font(color="FFFFFF" if rec_color != "FFC000" else "000000", bold=True, size=10)
                elif col == 7:
                    cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
                    cell.font = Font(size=9)
                else:
                    cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
                if row_i % 2 == 0 and col not in [6, 7]:
                    if cell.fill.start_color.rgb == "00000000" or not cell.fill:
                        cell.fill = PatternFill(start_color="F8F8F8", end_color="F8F8F8", fill_type="solid")
            motif_lines = len(motif.split('\n')) if motif else 1
            ws.row_dimensions[row_i].height = max(40, min(150, motif_lines * 15))
        for col in range(1, 8):
            column_letter = get_column_letter(col)
            max_length = 0
            for row in range(1, ws.max_row + 1):
                cell_value = ws.cell(row=row, column=col).value
                if cell_value:
                    length = len(str(cell_value))
                    if length > max_length:
                        max_length = length
            adjusted_width = min(max_length + 2, 80)
            ws.column_dimensions[column_letter].width = adjusted_width
        for row in range(4, ws.max_row + 1):
            cell = ws.cell(row=row, column=7)
            if cell.value:
                line_count = len(str(cell.value).split('\n'))
                ws.row_dimensions[row].height = max(40, min(150, line_count * 15))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
def generate_pdf_report_enhanced(candidats_data, poste_filter=None):
    if not REPORTLAB_AVAILABLE:
        return None
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), rightMargin=0.8*cm, leftMargin=0.8*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    els = []
    sty = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=sty['Heading1'], fontSize=20, textColor=colors.HexColor('#1F4E79'), spaceAfter=20, alignment=TA_CENTER, fontName='Helvetica-Bold')
    section_style = ParagraphStyle('SectionTitle', parent=sty['Heading2'], fontSize=14, textColor=colors.HexColor('#2F5597'), spaceAfter=12, spaceBefore=8, fontName='Helvetica-Bold')
    rapport_type = f"CANDIDATURES - {poste_filter}" if poste_filter else "RAPPORT GENERAL DE RECRUTEMENT"
    els.append(Paragraph(rapport_type, title_style))
    els.append(Paragraph(f"Genere le {datetime.datetime.now().strftime('%d/%m/%Y a %H:%M')}", ParagraphStyle('Sub', parent=sty['Normal'], fontSize=10, textColor=colors.HexColor('#666666'), alignment=TA_CENTER)))
    els.append(Spacer(1, 0.3*cm))
    if poste_filter:
        postes_to_export = [poste_filter]
    else:
        postes_to_export = list(dict.fromkeys(c.get('poste', '') for c in candidats_data if c.get('poste')))
    for poste in postes_to_export:
        candidats_poste = [c for c in candidats_data if c.get('poste') == poste]
        if not candidats_poste:
            continue
        candidats_poste.sort(key=lambda x: -int(x.get('score', 0)))
        els.append(Paragraph(f"{poste}", section_style))
        score_max = get_score_max_for_poste(poste)
        scores = [int(c.get('score', 0)) for c in candidats_poste]
        meilleur = max(scores) if scores else 0
        moyenne = sum(scores) / len(scores) if scores else 0
        els.append(Paragraph(f"{len(candidats_poste)} candidat(s) | Score max: {meilleur}/{score_max} | Moyenne: {moyenne:.1f}/{score_max}", ParagraphStyle('Stats', parent=sty['Normal'], fontSize=9, textColor=colors.HexColor('#666666'), spaceAfter=10)))
        headers = ['Rang', 'N° Dossier', 'Nom', 'Prenom', 'Telephone', 'Email', 'Statut', f'Score /{score_max}', 'Recommandation', 'Analyse']
        col_widths = [0.8*cm, 1.8*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3.5*cm, 2*cm, 1.8*cm, 2.5*cm, 6*cm]
        data = [headers]
        for idx, c in enumerate(candidats_poste, 1):
            score = int(c.get('score', 0))
            score_max_local = get_score_max_for_poste(poste)
            statut = c.get('statut', 'en_attente')
            if statut == "rejete":
                decision = "Rejeté"
            elif statut == "retenu":
                decision = "Retenu"
            elif statut == "entretien":
                decision = "Entretien"
            else:
                decision = get_recommandation_from_score(score, poste)
            motif = generate_detailed_reason(c, poste, score, score_max_local)
            statut_display = get_display_status(c)
            if statut_display == "rejete":
                statut_display = "Rejete"
            elif statut_display == "retenu":
                statut_display = "Retenu"
            elif statut_display == "entretien":
                statut_display = "Entretien"
            else:
                statut_display = "En attente"
            analyse_paragraph = Paragraph(motif, ParagraphStyle('AnalyseStyle', parent=sty['Normal'], fontSize=7, alignment=TA_LEFT, wordWrap='CJK', leading=10))
            data.append([str(idx), c.get('numero_dossier', '') or '–', c.get('nom', '') or '–', c.get('prenom', '') or '–', c.get('telephone', '') or '–', c.get('email', '') or '–', statut_display, f"{score}/{score_max_local}", decision, analyse_paragraph])
        tbl = Table(data, colWidths=col_widths)
        tbl_style = [('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 9), ('FONTSIZE', (0, 1), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, 0), 8), ('TOPPADDING', (0, 0), (-1, 0), 8), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E79')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('ALIGNMENT', (9, 1), (9, -1), 'LEFT'), ('VALIGN', (9, 1), (9, -1), 'TOP')]
        for row_idx in range(1, len(data)):
            if row_idx % 2 == 0:
                tbl_style.append(('BACKGROUND', (0, row_idx), (8, row_idx), colors.Color(0.97, 0.97, 0.97)))
            decision_val = data[row_idx][8]
            if decision_val == "Retenu":
                tbl_style.append(('BACKGROUND', (8, row_idx), (8, row_idx), colors.Color(0.8, 1, 0.8)))
            elif decision_val == "Entretien":
                tbl_style.append(('BACKGROUND', (8, row_idx), (8, row_idx), colors.Color(1, 0.95, 0.6)))
            elif decision_val == "Rejeté":
                tbl_style.append(('BACKGROUND', (8, row_idx), (8, row_idx), colors.Color(1, 0.85, 0.85)))
        tbl.setStyle(TableStyle(tbl_style))
        els.append(tbl)
        els.append(Spacer(1, 0.6*cm))
    footer_style = ParagraphStyle('Footer', parent=sty['Normal'], fontSize=8, textColor=colors.grey, alignment=TA_CENTER)
    els.append(Paragraph("— Fin du Rapport —", footer_style))
    els.append(Paragraph(f"Document genere automatiquement par RecrutBank • {datetime.datetime.now().year}", footer_style))
    doc.build(els)
    buf.seek(0)
    return buf
def generate_csv_report(candidats_data, poste_filter=None):
    out = io.StringIO()
    w = csv.writer(out, delimiter=';', quoting=csv.QUOTE_ALL, quotechar='"')
    headers = ['Rang', 'N° Dossier', 'Email', 'Nom', 'Prenom', 'Telephone', 'Poste', 'Date candidature', 'Score', 'Statut', 'Decision', 'Analyse']
    w.writerow(headers)
    if poste_filter:
        candidats_filtered = [c for c in candidats_data if c.get('poste') == poste_filter]
    else:
        candidats_filtered = candidats_data
    candidats_filtered.sort(key=lambda x: -int(x.get('score', 0)))
    for idx, c in enumerate(candidats_filtered, 1):
        score = int(c.get('score', 0))
        poste = c.get('poste', '')
        decision = get_recommandation_from_score(score, poste)
        motif = generate_detailed_reason(c, poste, score, get_score_max_for_poste(poste))
        statut = get_display_status(c)
        w.writerow([str(idx), str(c.get('numero_dossier', '') or '–'), str(c.get('email', '') or '–'), str(c.get('nom', '') or ''), str(c.get('prenom', '') or ''), str(c.get('telephone', '') or '–'), str(poste or ''), str(c.get('date_candidature', '') or ''), str(c.get('score', '0')), statut, decision, motif.replace('\n', ' | ')])
    out.seek(0)
    return out.getvalue()
def generate_word_report(candidats_data, poste_filter=None):
    if not DOCX_AVAILABLE:
        return None
    buf = io.BytesIO()
    doc = DocxDocument()
    title = doc.add_heading('Rapport Detaille de Recrutement', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = f"Genere le {datetime.datetime.now().strftime('%d/%m/%Y a %H:%M')}"
    if poste_filter:
        subtitle += f" - Poste: {poste_filter}"
    doc.add_paragraph(subtitle).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()
    doc.add_heading('1. Statistiques Generales', level=1)
    total = len(candidats_data)
    retenus = sum(1 for c in candidats_data if get_display_status(c) == 'retenu')
    exclus = sum(1 for c in candidats_data if get_display_status(c) == 'rejete')
    en_attente = sum(1 for c in candidats_data if get_display_status(c) == 'en_attente')
    entretien = sum(1 for c in candidats_data if get_display_status(c) == 'entretien')
    doc.add_paragraph(f"Total: {total}\nRetenus: {retenus}\nExclus: {exclus}\nEn attente: {en_attente}\nEntretien: {entretien}")
    doc.add_paragraph()
    doc.add_heading('2. Liste Complete', level=1)
    if candidats_data:
        table_all = doc.add_table(rows=1, cols=9)
        table_all.style = 'Table Grid'
        table_all.columns[0].width = Inches(0.5)
        table_all.columns[1].width = Inches(1.0)
        table_all.columns[2].width = Inches(1.5)
        table_all.columns[3].width = Inches(1.5)
        table_all.columns[4].width = Inches(2.5)
        table_all.columns[5].width = Inches(2.0)
        table_all.columns[6].width = Inches(1.2)
        table_all.columns[7].width = Inches(1.0)
        table_all.columns[8].width = Inches(2.5)
        hdr_cells_all = table_all.rows[0].cells
        for i, h in enumerate(['Rang', 'Dossier', 'Nom', 'Prenom', 'Email', 'Poste', 'Statut', 'Score', 'Recommandation']):
            hdr_cells_all[i].text = h
            hdr_cells_all[i].paragraphs[0].runs[0].bold = True
        sorted_data = sorted(candidats_data, key=lambda x: -int(x.get('score', 0)))
        for idx, c in enumerate(sorted_data, 1):
            score = int(c.get('score', 0))
            score_max = get_score_max_for_poste(c.get('poste', ''))
            statut = c.get('statut', 'en_attente')
            if statut == "rejete":
                recommandation = "Rejeté"
            elif statut == "retenu":
                recommandation = "Retenu"
            elif statut == "entretien":
                recommandation = "Entretien"
            else:
                recommandation = get_recommandation_from_score(score, c.get('poste', ''))
            row_cells = table_all.add_row().cells
            row_cells[0].text = str(idx)
            row_cells[1].text = str(c.get('numero_dossier', '') or '–')
            row_cells[2].text = c.get('nom', '') or '–'
            row_cells[3].text = c.get('prenom', '') or '–'
            row_cells[4].text = c.get('email', '') or '–'
            row_cells[5].text = c.get('poste', '') or '–'
            row_cells[6].text = get_display_status(c)
            row_cells[7].text = f"{score}/{score_max}"
            row_cells[8].text = recommandation
            for cell in row_cells:
                cell.paragraphs[0].paragraph_format.space_after = Pt(2)
                cell.paragraphs[0].paragraph_format.space_before = Pt(2)
    doc.add_paragraph()
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run('--- Fin du Rapport ---')
    footer_run.italic = True
    doc.save(buf)
    buf.seek(0)
    return buf
def _save_error(token, error_message, statut="rejete"):
    if supabase:
        try:
            supabase.table('candidats').update({"score": "0", "decision": f"Rejet - {error_message}", "statut": statut, "analyse_status": "error", "analyse_error": error_message, "analyse_auto_date": datetime.datetime.now().isoformat(), "analyse_details": json.dumps({"erreur": error_message, "moteur": "verification_cv"}, ensure_ascii=False)}).eq('token', token).execute()
        except Exception as e:
            logger.error(f"Erreur sauvegarde: {e}")
def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste, force=False):
    try:
        if not force and not is_poste_actif(poste):
            logger.info(f"Analyse ignoree pour {token} — poste cloture : {poste}")
            if supabase:
                supabase.table('candidats').update({"analyse_status": "skipped_closed_post", "analyse_auto_date": datetime.datetime.now().isoformat(), "analyse_skip_reason": f"Poste cloture : {poste}"}).eq('token', token).execute()
            return
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        cv_text = ""
        if cv_filename:
            cv_bytes = download_file_from_supabase_robust(cv_filename)
            if cv_bytes:
                cv_text = extract_text_robust_from_bytes(cv_bytes, cv_filename)
                if len(cv_text) > MAX_TEXT_SIZE:
                    cv_text = cv_text[:MAX_TEXT_SIZE]
            del cv_bytes
            gc.collect()
            time.sleep(0.3)
        lm_text = ""
        if lettre_filename:
            lm_bytes = download_file_from_supabase_robust(lettre_filename)
            if lm_bytes:
                lm_text = extract_text_robust_from_bytes(lm_bytes, lettre_filename)
                if len(lm_text) > MAX_TEXT_SIZE:
                    lm_text = lm_text[:MAX_TEXT_SIZE]
            del lm_bytes
            gc.collect()
            time.sleep(0.3)
        att_texts = []
        for fn in (attestation_filenames or []):
            if fn:
                att_bytes = download_file_from_supabase_robust(fn)
                if att_bytes:
                    t = extract_text_robust_from_bytes(att_bytes, fn)
                    if t and len(t) > MAX_TEXT_SIZE:
                        t = t[:MAX_TEXT_SIZE]
                    if t:
                        att_texts.append(t)
                del att_bytes
                gc.collect()
                time.sleep(0.3)
        if not cv_text or len(cv_text.strip()) < 50:
            logger.warning(f"CV manquant ou vide pour {token}")
            _save_error(token, "CV manquant ou vide", "rejete")
            return
        if lm_text and len(lm_text.strip()) > 50 and cv_text:
            cv_clean = re.sub(r'\s+', ' ', cv_text.strip().lower())
            lm_clean = re.sub(r'\s+', ' ', lm_text.strip().lower())
            if len(cv_clean) > 100 and len(lm_clean) > 100:
                cv_words = set(cv_clean.split())
                lm_words = set(lm_clean.split())
                if cv_words and lm_words:
                    common = len(cv_words & lm_words)
                    similarity = common / max(len(cv_words), len(lm_words))
                    if similarity > 0.85:
                        logger.warning(f"CV et lettre identiques pour {token} (similarite: {similarity:.0%})")
                        lm_text = ""
                        supabase.table('candidats').update({"note": "Attention: Le CV et la lettre de motivation sont identiques. Une lettre personnalisee est attendue."}).eq('token', token).execute()
        logger.info(f"Analyse pour {token} - poste: {poste}")
        gc.collect()
        if poste == "Chargé(e) d'Administration de Crédit":
            result = calculate_score_charge_admin_credit(cv_text, lm_text, att_texts)
            logger.info(f"Score calcule: {result.get('score', 0)}/12 - {result.get('decision', 'Inconnu')}")
        elif poste == "Chef de Section Compensation":
            result = calculate_score_chef_section_compensation(cv_text, lm_text, att_texts)
        elif poste == "Chef de Division Local Corporate":
            result = calculate_score_chef_division_corporate(cv_text, lm_text, att_texts)
        elif poste == "Data Analyst Finance":
            result = calculate_score_data_analyst_finance(cv_text, lm_text, att_texts)
        else:
            result = analyze_cv_intelligent(cv_text, lm_text, att_texts, poste)
            if result is None:
                result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
        score = result.get('score', 0)
        score_max = result.get('score_max', get_score_max_for_poste(poste))
        decision = result.get('decision') or get_recommandation_from_score(score, poste)
        statut = get_statut_from_decision(decision)
        if score > score_max:
            score = score_max
        details = result.get('details', {})
        details['points_forts'] = result.get('points_forts', [])
        details['points_vigilance'] = result.get('points_vigilance', [])
        details['synthese_recruteur'] = result.get('synthese', '')
        details['moteur'] = 'scoring_specifique_v2'
        score_breakdown = {'score_final': score, 'score_max': score_max, 'decision': decision, 'moteur_analyse': 'scoring_specifique_v2', 'sous_scores': result.get('sous_scores', {})}
        if supabase:
            update_data = {"score": str(score), "decision": decision, "statut": statut, "analyse_status": "completed", "analyse_auto_date": datetime.datetime.now().isoformat()}
            if result.get('checklist'):
                update_data["checklist"] = json.dumps(result.get('checklist', {}), ensure_ascii=False)
            if result.get('flags_eliminatoires'):
                update_data["flags_eliminatoires"] = json.dumps(result.get('flags_eliminatoires', []), ensure_ascii=False)
            if result.get('signaux_detectes'):
                update_data["signaux_detectes"] = json.dumps(result.get('signaux_detectes', []), ensure_ascii=False)
            update_data["analyse_details"] = json.dumps(details, ensure_ascii=False)
            update_data["score_breakdown"] = json.dumps(score_breakdown, ensure_ascii=False)
            supabase.table('candidats').update(update_data).eq('token', token).execute()
            logger.info(f"[{decision}] Score {token}: {score}/{score_max} → statut: {statut}")
        del cv_text, lm_text, att_texts, result
        gc.collect()
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Erreur analyse {token}: {str(e)}")
        if supabase:
            try:
                supabase.table('candidats').update({"analyse_status": "error", "analyse_error": str(e), "analyse_auto_date": datetime.datetime.now().isoformat()}).eq('token', token).execute()
            except:
                pass
@app.route('/api/postes', methods=['GET'])
def get_postes():
    return jsonify({"postes": POSTES, "postes_actifs": POSTES_ACTIFS, "postes_clotures": POSTES_CLOTURES}), 200
@app.route('/api/postes/actifs', methods=['GET'])
def get_postes_actifs():
    return jsonify(POSTES_ACTIFS), 200
@app.route('/api/grille/<poste>', methods=['GET'])
def get_grille(poste):
    g = GRILLE.get(poste)
    if not g:
        return jsonify({'error': 'Poste inconnu', 'postes_disponibles': list(GRILLE.keys())}), 404
    return jsonify(g), 200
@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return '', 204
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON manquant'}), 400
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    hashed_password = hash_pwd(password)
    if supabase:
        try:
            response = supabase.table('recruteurs').select('*').eq('email', email).execute()
            if response.data and len(response.data) > 0:
                recruteur = response.data[0]
                if recruteur.get('password') == hashed_password:
                    access_token = create_access_token(identity=str(recruteur['id']))
                    return jsonify({'token': access_token, 'nom': recruteur.get('nom', 'Recruteur'), 'email': recruteur.get('email', email)}), 200
        except Exception as e:
            logger.error(f"Erreur login: {e}")
    return jsonify({'error': 'Identifiants incorrects'}), 401
@app.route('/api/candidats/postuler', methods=['POST'])
def postuler():
    try:
        nom = (request.form.get('nom') or '').strip()
        prenom = (request.form.get('prenom') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        telephone = (request.form.get('telephone') or '').strip()
        poste = (request.form.get('poste') or '').strip()
        if not nom or not prenom or not email or poste not in POSTES:
            return jsonify({'error': 'Champs obligatoires manquants ou poste invalide'}), 400
        if supabase:
            existing = supabase.table('candidats').select('*').eq('email', email).eq('poste', poste).execute()
            if existing.data and len(existing.data) > 0:
                return jsonify({'error': f'Vous avez deja soumis une candidature pour le poste "{poste}".'}), 409
            all_candidats = supabase.table('candidats').select('numero_dossier').eq('poste', poste).execute()
            max_num = 0
            for c in all_candidats.data:
                existing_num = c.get('numero_dossier', '')
                if existing_num:
                    try:
                        num_val = int(existing_num)
                        if num_val > max_num:
                            max_num = num_val
                    except ValueError:
                        pass
            new_num = max_num + 1
            numero_dossier = str(new_num)
        def save_file_to_supabase(field, suffix):
            f = request.files.get(field)
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_{suffix}.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                return result if result else ''
            return ''
        cv_filename = save_file_to_supabase('cv', 'cv')
        if request.files.get('cv') and not cv_filename:
            return jsonify({'error': "Echec de l'envoi du CV, merci de reessayer."}), 500
        lettre_filename = save_file_to_supabase('lettre', 'lettre')
        if request.files.get('lettre') and not lettre_filename:
            return jsonify({'error': "Echec de l'envoi de la lettre de motivation, merci de reessayer."}), 500
        att_filenames = []
        for f in request.files.getlist('attestation'):
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[-1].lower()
                blob_name = f"{uuid.uuid4().hex}_attestation.{ext}"
                result = upload_file_to_supabase(f, blob_name, f.content_type)
                if result:
                    att_filenames.append(blob_name)
        token = uuid.uuid4().hex
        supabase.table('candidats').insert({"token": token, "nom": nom, "prenom": prenom, "email": email, "telephone": telephone, "poste": poste, "numero_dossier": numero_dossier, "cv_filename": cv_filename, "lettre_filename": lettre_filename, "attestation_filenames": json.dumps(att_filenames, ensure_ascii=False), "statut": "en_attente", "note": "", "score": "0", "checklist": "", "flags_eliminatoires": "", "signaux_detectes": "", "score_breakdown": "", "analyse_status": "pending", "date_candidature": datetime.datetime.now().isoformat()}).execute()
        if is_poste_actif(poste):
            threading.Thread(target=run_analysis_for_candidat, args=(token, cv_filename, lettre_filename, att_filenames, poste, False), daemon=True).start()
            analyse_msg = 'Analyse automatique en cours'
        else:
            analyse_msg = 'Poste cloture — candidature enregistree sans analyse'
            supabase.table('candidats').update({"analyse_status": "closed_post_no_analysis", "analyse_auto_date": datetime.datetime.now().isoformat()}).eq('token', token).execute()
        nom_complet = f"{prenom} {nom}".strip()
        sujet_confirmation = f"Confirmation de candidature – {poste}"
        corps_confirmation = f"Bonjour {nom_complet},\nNous accusons reception de votre candidature.\nSans reponse de notre part sous deux (2) semaines, veuillez considerer que votre candidature n'a pas ete retenue.\nPour toute information : contact@cdotchad.com.\nCordialement,"
        threading.Thread(target=send_email, args=(email, sujet_confirmation, corps_confirmation), daemon=True).start()
        return jsonify({'message': 'Candidature soumise avec succes', 'token': token, 'numero_dossier': numero_dossier, 'analyse': analyse_msg, 'poste_statut': 'actif' if is_poste_actif(poste) else 'cloture'}), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
@app.route('/api/candidats/statut/<token>', methods=['GET'])
def get_statut(token):
    if supabase:
        response = supabase.table('candidats').select('*').eq('token', token).execute()
        if response.data and len(response.data) > 0:
            data = response.data[0]
            hidden = {'cv_filename', 'lettre_filename', 'attestation_filenames', 'checklist', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details', 'score_breakdown'}
            return jsonify({k: v for k, v in data.items() if k not in hidden}), 200
    return jsonify({'error': 'Candidature introuvable'}), 404
@app.route('/api/recruteur/stats', methods=['GET'])
@jwt_required()
def get_stats():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    keys = response.data if response.data else []
    stats = {"total": len(keys), "en_attente": 0, "retenu": 0, "rejete": 0, "entretien": 0, "by_poste": []}
    counts = {}
    for c in keys:
        statut = c.get('statut', 'en_attente')
        if statut in stats:
            stats[statut] += 1
        else:
            stats['en_attente'] += 1
        p = c.get('poste', 'Inconnu')
        counts[p] = counts.get(p, 0) + 1
    stats['by_poste'] = [{'poste': p, 'n': n} for p, n in sorted(counts.items(), key=lambda x: -x[1])]
    return jsonify(stats), 200
@app.route('/api/recruteur/postes/stats', methods=['GET'])
@jwt_required()
def get_postes_stats():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    keys = response.data if response.data else []
    actifs_count = 0
    clotures_count = 0
    par_poste_actif = {}
    par_poste_cloture = {}
    for c in keys:
        poste = c.get('poste', '')
        if poste in POSTES_ACTIFS:
            actifs_count += 1
            par_poste_actif[poste] = par_poste_actif.get(poste, 0) + 1
        else:
            clotures_count += 1
            par_poste_cloture[poste] = par_poste_cloture.get(poste, 0) + 1
    return jsonify({'total': len(keys), 'postes_actifs': {'count': actifs_count, 'liste': POSTES_ACTIFS, 'par_poste': par_poste_actif, 'eligible_reanalyse': True}, 'postes_clotures': {'count': clotures_count, 'liste': POSTES_CLOTURES, 'par_poste': par_poste_cloture, 'eligible_reanalyse': False}}), 200
@app.route('/api/recruteur/candidats', methods=['GET'])
@jwt_required()
def list_candidats():
    poste_filter = request.args.get('poste', '')
    statut_filter = request.args.get('statut', '')
    search = request.args.get('search', '').lower()
    min_score = request.args.get('min_score', type=int)
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').execute()
    all_candidats = response.data if response.data else []
    result = []
    for c in all_candidats:
        c['id'] = c.get('token', '')
        if poste_filter and c.get('poste') != poste_filter:
            continue
        if statut_filter:
            statut = c.get('statut', 'en_attente')
            if statut != statut_filter:
                continue
        if min_score is not None and int(c.get('score', 0)) < min_score:
            continue
        if search:
            hay = (f"{c.get('nom','')} {c.get('prenom','')} {c.get('email','')} {c.get('poste','')} {c.get('numero_dossier','')}").lower()
            if search not in hay:
                continue
        for field in ['score_breakdown', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details']:
            if c.get(field):
                try:
                    c[f'{field}_parsed'] = json.loads(c[field])
                except Exception:
                    pass
        result.append(c)
    result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
    return jsonify(result), 200
@app.route('/api/recruteur/candidats/<token>', methods=['GET'])
@jwt_required()
def get_candidat_detail(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    data['id'] = token
    if data.get('attestation_filenames'):
        try:
            data['attestation_filenames_parsed'] = json.loads(data['attestation_filenames'])
        except Exception:
            data['attestation_filenames_parsed'] = []
    for field in ['checklist', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details', 'score_breakdown']:
        if data.get(field):
            try:
                data[f'{field}_parsed'] = json.loads(data[field])
            except Exception:
                pass
    return jsonify(data), 200
@app.route('/api/recruteur/candidats/<token>/statut', methods=['PUT'])
@jwt_required()
def update_candidat(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = request.get_json(silent=True) or {}
    statut = data.get('statut', 'en_attente')
    note = data.get('note', '')
    if statut not in ('en_attente', 'retenu', 'rejete', 'entretien'):
        return jsonify({'error': 'Statut invalide'}), 400
    update_data = {"statut": statut, "note": note, "decision_date": datetime.datetime.now().isoformat(), "decided_by": get_jwt_identity()}
    if statut == "rejete":
        update_data["decision"] = "Rejet - Décision du recruteur"
    elif statut == "retenu":
        update_data["decision"] = "Retenu - Décision du recruteur"
    elif statut == "entretien":
        update_data["decision"] = "Entretien - Décision du recruteur"
    supabase.table('candidats').update(update_data).eq('token', token).execute()
    return jsonify({'message': 'Mis a jour avec succes', 'statut': statut}), 200
@app.route('/api/recruteur/candidats/<token>/analyze', methods=['POST'])
@jwt_required()
def trigger_analyze(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    cv_fn = data.get('cv_filename')
    lm_fn = data.get('lettre_filename')
    att_raw = data.get('attestation_filenames', '[]')
    poste = data.get('poste')
    if not cv_fn:
        return jsonify({'error': 'CV manquant pour analyse'}), 400
    force = request.args.get('force', '0') == '1'
    if not force and not is_poste_actif(poste):
        return jsonify({'error': f'Le poste "{poste}" est cloture. Utilisez ?force=1 pour forcer l\'analyse.', 'poste': poste, 'statut': 'cloture'}), 403
    supabase.table('candidats').update({"analyse_status": "pending", "analyse_manual_trigger": datetime.datetime.now().isoformat()}).eq('token', token).execute()
    threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste, force), daemon=True).start()
    return jsonify({'message': 'Analyse re-declenchee', 'token': token}), 202
@app.route('/api/recruteur/reanalyze-status', methods=['GET'])
@jwt_required()
def get_reanalyze_status():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('token, poste, analyse_status, analyse_auto_date').execute()
        keys = response.data if response.data else []
        active_candidates = [d for d in keys if d.get('poste') in POSTES_ACTIFS]
        total = len(active_candidates)
        status_counts = {'pending': 0, 'reanalyzing': 0, 'completed': 0, 'error': 0, 'skipped_closed_post': 0, 'closed_post_no_analysis': 0, 'reanalyzing_auto': 0}
        in_progress = False
        for data in active_candidates:
            status = data.get('analyse_status', 'pending')
            if status in status_counts:
                status_counts[status] += 1
            if status in ('reanalyzing', 'pending'):
                in_progress = True
        processed = status_counts.get('completed', 0) + status_counts.get('error', 0)
        return jsonify({'total': total, 'processed': processed, 'in_progress': in_progress, 'status_counts': status_counts, 'postes_concernes': POSTES_ACTIFS, 'timestamp': datetime.datetime.now().isoformat()}), 200
    except Exception as e:
        logger.error(f"Erreur reanalyze-status: {e}")
        return jsonify({'error': str(e)}), 500
@app.route('/api/recruteur/reanalyze-one/<token>', methods=['POST'])
@jwt_required()
def reanalyze_one_candidate(token):
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').eq('token', token).execute()
        if not response.data or len(response.data) == 0:
            return jsonify({'error': 'Candidat introuvable'}), 404
        data = response.data[0]
        poste = data.get('poste')
        if not is_poste_actif(poste):
            return jsonify({'error': f'Le poste "{poste}" est cloture. Reanalyse desactivee.', 'poste': poste, 'statut': 'cloture'}), 403
        cv_fn = data.get('cv_filename')
        if not cv_fn:
            return jsonify({'error': 'CV manquant pour analyse'}), 400
        lm_fn = data.get('lettre_filename')
        att_raw = data.get('attestation_filenames', '[]')
        supabase.table('candidats').update({"analyse_status": "reanalyzing", "reanalyze_trigger": datetime.datetime.now().isoformat(), "reanalyze_reason": "Reanalyse manuelle (un seul candidat)"}).eq('token', token).execute()
        threading.Thread(target=run_analysis_for_candidat, args=(token, cv_fn, lm_fn, att_raw, poste, True), daemon=True).start()
        return jsonify({'message': 'Reanalyse lancee', 'token': token, 'poste': poste}), 202
    except Exception as e:
        logger.error(f"Erreur reanalyze-one: {e}")
        return jsonify({'error': str(e)}), 500
@app.route('/api/recruteur/reanalyze-all', methods=['POST'])
@jwt_required()
def reanalyze_all_candidates():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        keys = response.data if response.data else []
        if not keys:
            return jsonify({'message': 'Aucune candidature a reanalyser'}), 200
        candidates_to_reanalyze = [data for data in keys if data.get('poste') in POSTES_ACTIFS and data.get('cv_filename')]
        candidates_skipped = len(keys) - len(candidates_to_reanalyze)
        if not candidates_to_reanalyze:
            return jsonify({'message': 'Aucun candidat sur poste actif avec CV a reanalyser', 'skipped_closed_posts': candidates_skipped}), 200
        now_iso = datetime.datetime.now().isoformat()
        for c in candidates_to_reanalyze:
            try:
                supabase.table('candidats').update({"analyse_status": "reanalyzing", "reanalyze_trigger": now_iso, "reanalyze_reason": "Reanalyse parallellisee (postes actifs)"}).eq('token', c.get('token')).execute()
            except Exception:
                pass
        def analyze_one(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                poste = data.get('poste')
                if not cv_fn:
                    return (token, False, "CV manquant")
                run_analysis_for_candidat(token, cv_fn, lm_fn, att_raw, poste, False)
                return (token, True, "OK")
            except Exception as e:
                return (data.get('token'), False, str(e))
        MAX_WORKERS = min(1, len(candidates_to_reanalyze))
        logger.info(f"Reanalyse parallele : {len(candidates_to_reanalyze)} candidats, {MAX_WORKERS} workers")
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(analyze_one, c): c for c in candidates_to_reanalyze}
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=180)
                    if success:
                        reanalyzed_count += 1
                    else:
                        errors.append(f"Token {token}: {msg}")
                except Exception as e:
                    errors.append(f"Timeout ou erreur: {str(e)}")
        elapsed = time.time() - start_time
        gc.collect()
        return jsonify({'message': f'Reanalyse terminee en {elapsed:.1f}s', 'reanalyzed_count': reanalyzed_count, 'total_candidates': len(candidates_to_reanalyze), 'skipped_closed_posts': candidates_skipped, 'workers_used': MAX_WORKERS, 'elapsed_seconds': round(elapsed, 1), 'errors': errors[:10]}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
@app.route('/api/recruteur/reanalyze-poste/<poste>', methods=['POST'])
@jwt_required()
def reanalyze_by_poste(poste):
    if poste not in POSTES:
        return jsonify({'error': f'Poste inconnu: {poste}'}), 400
    if not is_poste_actif(poste):
        return jsonify({'error': f'Le poste "{poste}" est cloture. Reanalyse desactivee.', 'poste': poste, 'statut': 'cloture', 'postes_actifs': POSTES_ACTIFS}), 403
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').eq('poste', poste).execute()
        keys = response.data if response.data else []
        if not keys:
            return jsonify({'message': f'Aucune candidature pour le poste "{poste}"'}), 200
        candidates_with_cv = [k for k in keys if k.get('cv_filename')]
        if not candidates_with_cv:
            return jsonify({'message': f'Aucun CV trouve pour le poste "{poste}"'}), 200
        now_iso = datetime.datetime.now().isoformat()
        for data in candidates_with_cv:
            try:
                supabase.table('candidats').update({"analyse_status": "reanalyzing", "reanalyze_trigger": now_iso, "reanalyze_reason": f"Reanalyse manuelle parallele : {poste}"}).eq('token', data.get('token')).execute()
            except Exception:
                pass
        def analyze_one(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                if not cv_fn:
                    return (token, False, "CV manquant")
                run_analysis_for_candidat(token, cv_fn, lm_fn, att_raw, poste, True)
                return (token, True, "OK")
            except Exception as e:
                return (data.get('token'), False, str(e))
        MAX_WORKERS = min(1, len(candidates_with_cv))
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(analyze_one, c) for c in candidates_with_cv]
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=180)
                    if success:
                        reanalyzed_count += 1
                    else:
                        errors.append(f"Token {token}: {msg}")
                except Exception as e:
                    errors.append(f"Erreur: {str(e)}")
        elapsed = time.time() - start_time
        gc.collect()
        return jsonify({'message': f'Reanalyse terminee pour le poste "{poste}"', 'poste': poste, 'statut': 'actif', 'reanalyzed_count': reanalyzed_count, 'total_candidates': len(candidates_with_cv), 'workers_used': MAX_WORKERS, 'elapsed_seconds': round(elapsed, 1), 'errors': errors[:10]}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
@app.route('/api/recruteur/reanalyze-fast', methods=['POST'])
@jwt_required()
def reanalyze_fast():
    try:
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        keys = response.data if response.data else []
        candidates = [d for d in keys if d.get('poste') in POSTES_ACTIFS and d.get('cv_filename')]
        if not candidates:
            return jsonify({'message': 'Aucun candidat actif avec CV'}), 200
        def analyze_fast_only(data):
            try:
                token = data.get('token')
                cv_fn = data.get('cv_filename')
                lm_fn = data.get('lettre_filename')
                att_raw = data.get('attestation_filenames', '[]')
                poste = data.get('poste')
                cv_text = ""
                if cv_fn:
                    cv_bytes = download_file_from_supabase_robust(cv_fn)
                    if cv_bytes:
                        cv_text = extract_text_robust_from_bytes(cv_bytes, cv_fn)
                lm_text = ""
                if lm_fn:
                    lm_bytes = download_file_from_supabase_robust(lm_fn)
                    if lm_bytes:
                        lm_text = extract_text_robust_from_bytes(lm_bytes, lm_fn)
                att_texts = []
                if isinstance(att_raw, str):
                    try:
                        att_list = json.loads(att_raw) if att_raw else []
                    except:
                        att_list = []
                else:
                    att_list = att_raw or []
                for fn in att_list:
                    if fn:
                        att_bytes = download_file_from_supabase_robust(fn)
                        if att_bytes:
                            t = extract_text_robust_from_bytes(att_bytes, fn)
                            if t:
                                att_texts.append(t)
                if poste == "Chargé(e) d'Administration de Crédit":
                    result = calculate_score_charge_admin_credit(cv_text, lm_text, att_texts)
                elif poste == "Chef de Section Compensation":
                    result = calculate_score_chef_section_compensation(cv_text, lm_text, att_texts)
                elif poste == "Chef de Division Local Corporate":
                    result = calculate_score_chef_division_corporate(cv_text, lm_text, att_texts)
                elif poste == "Data Analyst Finance":
                    result = calculate_score_data_analyst_finance(cv_text, lm_text, att_texts)
                else:
                    result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
                if supabase:
                    supabase.table('candidats').update({"score": str(result.get('score', 0)), "checklist": json.dumps(result.get('checklist', {}), ensure_ascii=False), "flags_eliminatoires": json.dumps(result.get('flags_eliminatoires', []), ensure_ascii=False), "signaux_detectes": json.dumps(result.get('signaux_detectes', []), ensure_ascii=False), "analyse_details": json.dumps(result.get('details', {}), ensure_ascii=False), "score_breakdown": json.dumps(result.get('score_breakdown', {}), ensure_ascii=False), "analyse_auto_date": datetime.datetime.now().isoformat(), "analyse_status": "completed"}).eq('token', token).execute()
                return (token, True, result.get('score', 0))
            except Exception as e:
                return (data.get('token'), False, str(e))
        start = time.time()
        success_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=min(1, len(candidates))) as executor:
            futures = [executor.submit(analyze_fast_only, c) for c in candidates]
            for future in as_completed(futures):
                try:
                    token, ok, msg = future.result(timeout=60)
                    if ok:
                        success_count += 1
                    else:
                        errors.append(f"{token}: {msg}")
                except Exception as e:
                    errors.append(str(e))
        elapsed = time.time() - start
        gc.collect()
        return jsonify({'message': f'Reanalyse eclair terminee en {elapsed:.1f}s', 'success': success_count, 'total': len(candidates), 'elapsed_seconds': round(elapsed, 1), 'speed_per_candidate': round(elapsed / max(1, success_count), 2), 'errors': errors[:10]}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
@app.route('/api/recruteur/cleanup-closed', methods=['POST'])
@jwt_required()
def cleanup_closed_statuses():
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('token, poste, analyse_status').execute()
    fixed = 0
    for row in (response.data or []):
        if row.get('poste') in POSTES_CLOTURES and row.get('analyse_status') in ('reanalyzing', 'pending'):
            supabase.table('candidats').update({"analyse_status": "completed"}).eq('token', row['token']).execute()
            fixed += 1
    return jsonify({'message': f'{fixed} dossier(s) de postes clotures stabilises (scores conserves)', 'fixed': fixed, 'postes_concernes': POSTES_CLOTURES}), 200
@app.route('/api/recruteur/export/<fmt>', methods=['GET'])
@jwt_required()
def export_candidates(fmt):
    try:
        poste_filter = request.args.get('poste', '')
        statut_filter = request.args.get('statut', '')
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        result = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            if statut_filter:
                statut = c.get('statut', 'en_attente')
                if statut != statut_filter:
                    continue
            for field in ['score_breakdown', 'flags_eliminatoires', 'signaux_detectes', 'analyse_details']:
                if c.get(field):
                    try:
                        c[f'{field}_parsed'] = json.loads(c[field])
                    except Exception:
                        pass
            result.append(c)
        result.sort(key=lambda x: x.get('date_candidature', ''), reverse=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else "_global"
        statut_suffix = f"_{statut_filter}" if statut_filter else ""
        filename_base = f"rapport{poste_suffix}{statut_suffix}_{ts}"
        if fmt.lower() == 'excel' or fmt.lower() == 'xlsx':
            buf = generate_excel_report_enhanced(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation Excel'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'{filename_base}.xlsx')
        elif fmt.lower() == 'pdf':
            buf = generate_pdf_report_enhanced(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation PDF'}), 500
            return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{filename_base}.pdf')
        elif fmt.lower() == 'csv':
            csv_data = generate_csv_report(result, poste_filter=poste_filter)
            return send_file(io.BytesIO(csv_data.encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=f'{filename_base}.csv')
        elif fmt.lower() in ('word', 'docx'):
            buf = generate_word_report(result, poste_filter=poste_filter)
            if not buf:
                return jsonify({'error': 'Erreur generation Word'}), 500
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', as_attachment=True, download_name=f'{filename_base}.docx')
        return jsonify({'error': 'Format non supporte. Utilisez: csv, excel, pdf ou word'}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ===== FONCTIONS POUR L'EXPORT ZIP ASYNCHRONE =====

@app.route('/api/recruteur/dossiers/zip/start', methods=['POST'])
@jwt_required()
def start_zip_export():
    try:
        poste_filter = request.args.get('poste', '')
        date_start = request.args.get('date_start', '')
        date_end = request.args.get('date_end', '')
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        candidats = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            date_cand = c.get('date_candidature', '')
            if date_cand:
                date_only = date_cand.split('T')[0] if 'T' in date_cand else date_cand[:10]
                if date_start and date_only < date_start:
                    continue
                if date_end and date_only > date_end:
                    continue
            candidats.append(c)
        if not candidats:
            return jsonify({'error': 'Aucun dossier a exporter'}), 404
        task_id = uuid.uuid4().hex[:8]
        create_zip_task(task_id, len(candidats), poste_filter, date_start, date_end)
        def run_zip_export():
            try:
                update_zip_task(task_id, status='running')
                temp_dir = tempfile.mkdtemp(prefix=f"zip_export_{task_id}_")
                zip_path = os.path.join(temp_dir, f"export_{task_id}.zip")
                download_tasks = []
                candidats_meta = {}
                for cand in candidats:
                    poste_nom = cand.get('poste', 'Poste_Inconnu')
                    poste_nom_clean = re.sub(r'[<>:"/\\|?*]', '_', poste_nom)
                    num_dossier = cand.get('numero_dossier', '') or f"candidat_{cand['id'][:8]}"
                    nom_candidat = cand.get('nom', 'N/A').upper()
                    prenom_candidat = cand.get('prenom', 'N/A')
                    dossier_candidat_nom = f"{num_dossier} - {nom_candidat} {prenom_candidat}"
                    dossier_candidat_nom = re.sub(r'[<>:"/\\|?*]', '_', dossier_candidat_nom)
                    dossier_parent = f"{poste_nom_clean}/{dossier_candidat_nom}"
                    candidats_meta[cand['id']] = {'dossier_parent': dossier_parent, 'num_dossier': num_dossier, 'cand': cand}
                    cv_file = cand.get('cv_filename', '')
                    if cv_file:
                        download_tasks.append((cand['id'], cv_file, dossier_parent, 'CV'))
                    lettre_file = cand.get('lettre_filename', '')
                    if lettre_file:
                        download_tasks.append((cand['id'], lettre_file, dossier_parent, 'Lettre_de_motivation'))
                    att_raw = cand.get('attestation_filenames', '[]')
                    try:
                        att_files = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                        for idx, att_file in enumerate(att_files, 1):
                            if att_file:
                                download_tasks.append((cand['id'], att_file, dossier_parent, f'Attestation_{idx}'))
                    except Exception:
                        pass
                results_by_cand = {}
                total_files = len(download_tasks)
                for idx, task in enumerate(download_tasks):
                    try:
                        cand_id, blob_name, dossier_parent, prefix = task
                        logger.info(f"Telechargement {idx+1}/{total_files}: {blob_name}")
                        file_bytes = download_file_from_supabase_robust(blob_name)
                        if file_bytes:
                            results_by_cand.setdefault(cand_id, []).append((file_bytes, blob_name, prefix))
                        del file_bytes
                        gc.collect()
                        time.sleep(0.3)
                        update_zip_task(task_id, done=idx + 1, progress=int((idx + 1) / total_files * 50))
                    except Exception as e:
                        logger.error(f"Erreur telechargement {task[1]}: {e}")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    files_added = 0
                    total_candidates = len(candidats_meta)
                    for cand_idx, (cand_id, meta) in enumerate(candidats_meta.items()):
                        dossier_parent = meta['dossier_parent']
                        num_dossier = meta['num_dossier']
                        cand = meta['cand']
                        fichiers_a_inclure = results_by_cand.get(cand_id, [])
                        if not fichiers_a_inclure:
                            info_content = f"Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}\nPoste: {cand.get('poste', 'N/A')}\nNumero dossier: {num_dossier}\nEmail: {cand.get('email', 'N/A')}\nTelephone: {cand.get('telephone', 'N/A')}\nDate candidature: {cand.get('date_candidature', 'N/A')}"
                            archive_name = f"{dossier_parent}/INFOS_CANDIDAT.txt"
                            zip_file.writestr(archive_name, info_content.encode('utf-8'))
                            files_added += 1
                        else:
                            for file_bytes, original_filename, prefix in fichiers_a_inclure:
                                ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
                                archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                                try:
                                    zip_file.writestr(archive_name, file_bytes)
                                    files_added += 1
                                except Exception:
                                    pass
                        update_zip_task(task_id, progress=50 + int((cand_idx + 1) / total_candidates * 50))
                update_zip_task(task_id, status='completed', progress=100, zip_path=zip_path)
                del results_by_cand, download_tasks, candidats_meta
                gc.collect()
                logger.info(f"Export ZIP termine pour {task_id}: {files_added} fichiers, {len(candidats)} candidats")
            except Exception as e:
                logger.error(f"Erreur export ZIP {task_id}: {e}")
                update_zip_task(task_id, status='error', error=str(e))
        threading.Thread(target=run_zip_export, daemon=True).start()
        return jsonify({'task_id': task_id, 'status': 'pending', 'total_candidates': len(candidats)}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/dossiers/zip/status/<task_id>', methods=['GET'])
@jwt_required()
def get_zip_status(task_id):
    task = get_zip_task(task_id)
    if not task:
        # Vérifier si un fichier ZIP existe sur le disque
        temp_dir = tempfile.gettempdir()
        zip_path = os.path.join(temp_dir, f"zip_export_{task_id}_", f"export_{task_id}.zip")
        if os.path.exists(zip_path):
            return jsonify({
                'task_id': task_id,
                'status': 'completed',
                'progress': 100,
                'message': 'ZIP prêt',
                'zip_path': zip_path
            }), 200
        return jsonify({'error': 'Tache introuvable'}), 404
    return jsonify({
        'task_id': task_id,
        'status': task.get('status'),
        'progress': task.get('progress', 0),
        'total': task.get('total', 0),
        'done': task.get('done', 0),
        'error': task.get('error')
    }), 200

@app.route('/api/recruteur/dossiers/zip/download/<task_id>', methods=['GET'])
@jwt_required()
def download_zip(task_id):
    task = get_zip_task(task_id)
    if not task:
        # Vérifier si un fichier ZIP existe sur le disque
        temp_dir = tempfile.gettempdir()
        zip_path = os.path.join(temp_dir, f"zip_export_{task_id}_", f"export_{task_id}.zip")
        if os.path.exists(zip_path):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"dossiers_candidats_{ts}.zip"
            return send_file(zip_path, mimetype='application/zip', as_attachment=True, download_name=filename)
        return jsonify({'error': 'Tache introuvable'}), 404
    if task.get('status') != 'completed':
        return jsonify({'error': 'Tache non terminee', 'status': task.get('status')}), 400
    zip_path = task.get('zip_path')
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({'error': 'Fichier ZIP introuvable'}), 404
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"dossiers_candidats_{ts}.zip"
        return send_file(zip_path, mimetype='application/zip', as_attachment=True, download_name=filename)
    finally:
        def cleanup():
            try:
                time.sleep(5)
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                temp_dir = os.path.dirname(zip_path)
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                delete_zip_task(task_id)
            except Exception as e:
                logger.error(f"Cleanup ZIP error: {e}")
        threading.Thread(target=cleanup, daemon=True).start()

@app.route('/api/recruteur/dossiers/zip/force/<task_id>', methods=['POST'])
@jwt_required()
def force_zip_task(task_id):
    """Force l'exécution d'une tâche ZIP en mode synchrone"""
    task = get_zip_task(task_id)
    if not task:
        return jsonify({'error': 'Tache introuvable'}), 404
    logger.info(f"🚀 FORCE execution de la tâche {task_id}")
    try:
        poste_filter = task.get('poste_filter', '')
        date_start = task.get('date_start', '')
        date_end = task.get('date_end', '')
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        candidats = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            date_cand = c.get('date_candidature', '')
            if date_cand:
                date_only = date_cand.split('T')[0] if 'T' in date_cand else date_cand[:10]
                if date_start and date_only < date_start:
                    continue
                if date_end and date_only > date_end:
                    continue
            candidats.append(c)
        if not candidats:
            return jsonify({'error': 'Aucun dossier a exporter'}), 404
        update_zip_task(task_id, status='running')
        temp_dir = tempfile.mkdtemp(prefix=f"zip_export_{task_id}_")
        zip_path = os.path.join(temp_dir, f"export_{task_id}.zip")
        download_tasks = []
        candidats_meta = {}
        for cand in candidats:
            poste_nom = cand.get('poste', 'Poste_Inconnu')
            poste_nom_clean = re.sub(r'[<>:"/\\|?*]', '_', poste_nom)
            num_dossier = cand.get('numero_dossier', '') or f"candidat_{cand['id'][:8]}"
            nom_candidat = cand.get('nom', 'N/A').upper()
            prenom_candidat = cand.get('prenom', 'N/A')
            dossier_candidat_nom = f"{num_dossier} - {nom_candidat} {prenom_candidat}"
            dossier_candidat_nom = re.sub(r'[<>:"/\\|?*]', '_', dossier_candidat_nom)
            dossier_parent = f"{poste_nom_clean}/{dossier_candidat_nom}"
            candidats_meta[cand['id']] = {'dossier_parent': dossier_parent, 'num_dossier': num_dossier, 'cand': cand}
            cv_file = cand.get('cv_filename', '')
            if cv_file:
                download_tasks.append((cand['id'], cv_file, dossier_parent, 'CV'))
            lettre_file = cand.get('lettre_filename', '')
            if lettre_file:
                download_tasks.append((cand['id'], lettre_file, dossier_parent, 'Lettre_de_motivation'))
            att_raw = cand.get('attestation_filenames', '[]')
            try:
                att_files = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                for idx, att_file in enumerate(att_files, 1):
                    if att_file:
                        download_tasks.append((cand['id'], att_file, dossier_parent, f'Attestation_{idx}'))
            except Exception:
                pass
        results_by_cand = {}
        total_files = len(download_tasks)
        for idx, task_item in enumerate(download_tasks):
            try:
                cand_id, blob_name, dossier_parent, prefix = task_item
                logger.info(f"Telechargement {idx+1}/{total_files}: {blob_name}")
                file_bytes = download_file_from_supabase_robust(blob_name)
                if file_bytes:
                    results_by_cand.setdefault(cand_id, []).append((file_bytes, blob_name, prefix))
                del file_bytes
                gc.collect()
                time.sleep(0.3)
                update_zip_task(task_id, done=idx + 1, progress=int((idx + 1) / total_files * 50))
            except Exception as e:
                logger.error(f"Erreur telechargement {task_item[1]}: {e}")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            files_added = 0
            total_candidates = len(candidats_meta)
            for cand_idx, (cand_id, meta) in enumerate(candidats_meta.items()):
                dossier_parent = meta['dossier_parent']
                num_dossier = meta['num_dossier']
                cand = meta['cand']
                fichiers_a_inclure = results_by_cand.get(cand_id, [])
                if not fichiers_a_inclure:
                    info_content = f"Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}\nPoste: {cand.get('poste', 'N/A')}\nNumero dossier: {num_dossier}\nEmail: {cand.get('email', 'N/A')}\nTelephone: {cand.get('telephone', 'N/A')}\nDate candidature: {cand.get('date_candidature', 'N/A')}"
                    archive_name = f"{dossier_parent}/INFOS_CANDIDAT.txt"
                    zip_file.writestr(archive_name, info_content.encode('utf-8'))
                    files_added += 1
                else:
                    for file_bytes, original_filename, prefix in fichiers_a_inclure:
                        ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
                        archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                        try:
                            zip_file.writestr(archive_name, file_bytes)
                            files_added += 1
                        except Exception:
                            pass
                update_zip_task(task_id, progress=50 + int((cand_idx + 1) / total_candidates * 50))
        update_zip_task(task_id, status='completed', progress=100, zip_path=zip_path)
        logger.info(f"✅ Export force termine pour {task_id}: {files_added} fichiers")
        return jsonify({'task_id': task_id, 'status': 'completed', 'message': f'Export terminé avec {files_added} fichiers', 'download_url': f'/api/recruteur/dossiers/zip/download/{task_id}'}), 200
    except Exception as e:
        logger.error(f"❌ Erreur force export {task_id}: {e}")
        import traceback
        traceback.print_exc()
        update_zip_task(task_id, status='error', error=str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/dossiers/zip', methods=['GET'])
@jwt_required()
def export_dossiers_zip_legacy():
    try:
        poste_filter = request.args.get('poste', '')
        date_start = request.args.get('date_start', '')
        date_end = request.args.get('date_end', '')
        if not supabase:
            return jsonify({'error': 'Supabase non configure'}), 500
        response = supabase.table('candidats').select('*').execute()
        all_candidats = response.data if response.data else []
        candidats = []
        for c in all_candidats:
            c['id'] = c.get('token', '')
            if poste_filter and c.get('poste') != poste_filter:
                continue
            date_cand = c.get('date_candidature', '')
            if date_cand:
                date_only = date_cand.split('T')[0] if 'T' in date_cand else date_cand[:10]
                if date_start and date_only < date_start:
                    continue
                if date_end and date_only > date_end:
                    continue
            candidats.append(c)
        if not candidats:
            return jsonify({'error': 'Aucun dossier a exporter'}), 404
        task_id = uuid.uuid4().hex[:8]
        create_zip_task(task_id, len(candidats), poste_filter, date_start, date_end)
        def run_zip_export():
            try:
                update_zip_task(task_id, status='running')
                temp_dir = tempfile.mkdtemp(prefix=f"zip_export_{task_id}_")
                zip_path = os.path.join(temp_dir, f"export_{task_id}.zip")
                download_tasks = []
                candidats_meta = {}
                for cand in candidats:
                    poste_nom = cand.get('poste', 'Poste_Inconnu')
                    poste_nom_clean = re.sub(r'[<>:"/\\|?*]', '_', poste_nom)
                    num_dossier = cand.get('numero_dossier', '') or f"candidat_{cand['id'][:8]}"
                    nom_candidat = cand.get('nom', 'N/A').upper()
                    prenom_candidat = cand.get('prenom', 'N/A')
                    dossier_candidat_nom = f"{num_dossier} - {nom_candidat} {prenom_candidat}"
                    dossier_candidat_nom = re.sub(r'[<>:"/\\|?*]', '_', dossier_candidat_nom)
                    dossier_parent = f"{poste_nom_clean}/{dossier_candidat_nom}"
                    candidats_meta[cand['id']] = {'dossier_parent': dossier_parent, 'num_dossier': num_dossier, 'cand': cand}
                    cv_file = cand.get('cv_filename', '')
                    if cv_file:
                        download_tasks.append((cand['id'], cv_file, dossier_parent, 'CV'))
                    lettre_file = cand.get('lettre_filename', '')
                    if lettre_file:
                        download_tasks.append((cand['id'], lettre_file, dossier_parent, 'Lettre_de_motivation'))
                    att_raw = cand.get('attestation_filenames', '[]')
                    try:
                        att_files = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                        for idx, att_file in enumerate(att_files, 1):
                            if att_file:
                                download_tasks.append((cand['id'], att_file, dossier_parent, f'Attestation_{idx}'))
                    except Exception:
                        pass
                results_by_cand = {}
                total_files = len(download_tasks)
                for idx, task in enumerate(download_tasks):
                    try:
                        cand_id, blob_name, dossier_parent, prefix = task
                        logger.info(f"Telechargement {idx+1}/{total_files}: {blob_name}")
                        file_bytes = download_file_from_supabase_robust(blob_name)
                        if file_bytes:
                            results_by_cand.setdefault(cand_id, []).append((file_bytes, blob_name, prefix))
                        del file_bytes
                        gc.collect()
                        time.sleep(0.3)
                        update_zip_task(task_id, done=idx + 1, progress=int((idx + 1) / total_files * 50))
                    except Exception as e:
                        logger.error(f"Erreur telechargement {task[1]}: {e}")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    files_added = 0
                    total_candidates = len(candidats_meta)
                    for cand_idx, (cand_id, meta) in enumerate(candidats_meta.items()):
                        dossier_parent = meta['dossier_parent']
                        num_dossier = meta['num_dossier']
                        cand = meta['cand']
                        fichiers_a_inclure = results_by_cand.get(cand_id, [])
                        if not fichiers_a_inclure:
                            info_content = f"Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}\nPoste: {cand.get('poste', 'N/A')}\nNumero dossier: {num_dossier}\nEmail: {cand.get('email', 'N/A')}\nTelephone: {cand.get('telephone', 'N/A')}\nDate candidature: {cand.get('date_candidature', 'N/A')}"
                            archive_name = f"{dossier_parent}/INFOS_CANDIDAT.txt"
                            zip_file.writestr(archive_name, info_content.encode('utf-8'))
                            files_added += 1
                        else:
                            for file_bytes, original_filename, prefix in fichiers_a_inclure:
                                ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
                                archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                                try:
                                    zip_file.writestr(archive_name, file_bytes)
                                    files_added += 1
                                except Exception:
                                    pass
                        update_zip_task(task_id, progress=50 + int((cand_idx + 1) / total_candidates * 50))
                update_zip_task(task_id, status='completed', progress=100, zip_path=zip_path)
                del results_by_cand, download_tasks, candidats_meta
                gc.collect()
                logger.info(f"Export ZIP termine pour {task_id}: {files_added} fichiers, {len(candidats)} candidats")
                def auto_cleanup():
                    time.sleep(600)
                    try:
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
                        temp_dir = os.path.dirname(zip_path)
                        if os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                        delete_zip_task(task_id)
                    except Exception as e:
                        logger.error(f"Auto-cleanup ZIP error: {e}")
                threading.Thread(target=auto_cleanup, daemon=True).start()
            except Exception as e:
                logger.error(f"Erreur export ZIP {task_id}: {e}")
                update_zip_task(task_id, status='error', error=str(e))
        threading.Thread(target=run_zip_export, daemon=True).start()
        return jsonify({'message': 'Export ZIP demarre en arriere-plan', 'task_id': task_id, 'status': 'pending', 'total_candidates': len(candidats), 'note': 'Utilisez /api/recruteur/dossiers/zip/status/<task_id> pour suivre la progression'}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recruteur/candidats/<token>/email-preview', methods=['POST'])
@jwt_required()
def email_preview(token):
    if not supabase:
        return jsonify({'error': 'Supabase non configure'}), 500
    response = supabase.table('candidats').select('*').eq('token', token).execute()
    if not response.data or len(response.data) == 0:
        return jsonify({'error': 'Candidat introuvable'}), 404
    data = response.data[0]
    body = request.get_json(silent=True) or {}
    msg_type = body.get('type', data.get('statut', 'en_attente'))
    nom_c = f"{data.get('prenom', '')} {data.get('nom', '')}".strip()
    poste = data.get('poste', '')
    to_email = data.get('email', '')
    sign = "\nCordialement,\nL'equipe Ressources Humaines\nRecrutBank"
    if msg_type == 'retenu':
        sujet = f"Felicitations – Candidature retenue – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nNous avons le plaisir de vous informer que votre candidature pour le poste de {poste} a ete retenue.\nNous vous contacterons tres prochainement." + sign
    elif msg_type == 'entretien':
        sujet = f"Invitation a un entretien – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nSuite a l'examen de votre candidature pour le poste de {poste}, nous avons le plaisir de vous inviter a un entretien.\nNous prendrons contact avec vous pour convenir d'une date." + sign
    else:
        sujet = f"Reponse a votre candidature – {poste}"
        corps = f"Madame, Monsieur {nom_c},\nNous vous remercions de l'interet que vous portez a notre institution.\nApres examen attentif de votre dossier pour le poste de {poste}, nous avons le regret de vous informer que votre candidature n'a pas ete retenue.\nNous vous encourageons a postuler a nouveau." + sign
    return jsonify({'to': to_email, 'nom': nom_c, 'sujet': sujet, 'corps': corps}), 200
@app.route('/api/recruteur/uploads/<path:filename>', methods=['GET'])
def serve_upload(filename):
    safe = secure_filename(filename.replace('/', '_'))
    if not safe:
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    url = get_signed_url(safe, expiration_minutes=30)
    if not url:
        return jsonify({'error': 'Fichier introuvable'}), 404
    return redirect(url)
@app.route('/api/recruteur/debug/analyse-ia', methods=['POST'])
@jwt_required()
def debug_analyse_ia():
    data = request.get_json(silent=True) or {}
    cv_text = data.get('cv_text', '')
    lettre_text = data.get('lettre_text', '')
    poste = data.get('poste', '')
    if not cv_text or poste not in GRILLE:
        return jsonify({'error': 'cv_text requis et poste doit exister dans GRILLE'}), 400
    result = analyze_cv_against_grille(cv_text, lettre_text, [], poste)
    return jsonify(result), 200
@app.route('/api/test-email', methods=['GET'])
def test_email():
    try:
        to = request.args.get('to', '')
        if not to:
            return jsonify({'error': 'Parametre ?to= requis'}), 400
        ok = send_email(to, 'Test RecrutBank', 'Ceci est un email de test depuis RecrutBank.')
        return jsonify({'sent': ok}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/api/health-version', methods=['GET'])
def health_version():
    return jsonify({"version": "v5.9-final", "postes_actifs": POSTES_ACTIFS, "postes_count": len(POSTES), "scoring_seuils": "12: 10/7, 14: 11/7, 100: 80/70/60, 10: 8/5", "scoring_strict": True, "manual_status_priority": True, "auto_width_excel": True, "async_export": True, "persistent_tasks": True, "force_mode": True, "deployed_at": datetime.datetime.now().isoformat()}), 200
if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    logger.info(f"RecrutBank API v5.9-final demarree sur le port {port}")
    logger.info(f"Analyseur semantique: {'Active' if IA_ANALYSE_ACTIVE else 'Inactif (fallback mots-cles)'}")
    logger.info(f"Mode scoring STRICT: Active (rejet immediat si critere eliminaire non satisfait)")
    logger.info(f"Priorite statut manuel: Active (le statut du recruteur prime sur la decision auto)")
    logger.info(f"Auto-width Excel: Active (colonnes ajustees automatiquement)")
    logger.info(f"Download retry: Active (max {DOWNLOAD_MAX_RETRIES} tentatives, backoff exponentiel)")
    logger.info(f"Download concurrent: max {int(os.getenv('DOWNLOAD_MAX_CONCURRENT', '3'))} telechargements simultanes")
    logger.info(f"Export ZIP asynchrone: Active (fichier sur disque, pas en RAM)")
    logger.info(f"Persistance des taches ZIP: Active (table zip_tasks dans Supabase)")
    logger.info(f"Mode FORCE: Active (endpoint /force/ pour execution synchrone)")
    cleanup_old_zip_tasks()
    app.run(host="0.0.0.0", port=port, debug=False)

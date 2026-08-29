from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, json, re, threading, mimetypes, io, csv, unicodedata, zipfile, time, gc, random, tempfile
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
    RAPIDFUZZ_AVAILABLE = False
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
    from openai import OpenAI
    DEEPSEEK_AVAILABLE = True
except ImportError:
    DEEPSEEK_AVAILABLE = False
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
IA_ANALYSE_ACTIVE = DEEPSEEK_AVAILABLE and bool(DEEPSEEK_API_KEY)
_deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com") if IA_ANALYSE_ACTIVE else None
_ia_semaphore = threading.Semaphore(int(os.getenv("IA_MAX_CONCURRENCY", "5")))
_Nlp_fr = None
_Nlp_en = None
DOWNLOAD_MAX_RETRIES = int(os.getenv("DOWNLOAD_MAX_RETRIES", "3"))
DOWNLOAD_BASE_DELAY = float(os.getenv("DOWNLOAD_BASE_DELAY", "0.5"))
DOWNLOAD_MAX_DELAY = int(os.getenv("DOWNLOAD_MAX_DELAY", "10"))
DOWNLOAD_MAX_CONCURRENT = int(os.getenv("DOWNLOAD_MAX_CONCURRENT", "15"))
_DOWNLOAD_SEMAPHORE = threading.Semaphore(DOWNLOAD_MAX_CONCURRENT)
_ZIP_JOBS = {}
_ZIP_JOBS_LOCK = threading.Lock()
_ZIP_JOBS_MAX_AGE_SECONDS = 3600
_ZIP_MAX_WORKERS = int(os.getenv("ZIP_MAX_WORKERS", "25"))
def retry_with_backoff(max_retries=DOWNLOAD_MAX_RETRIES, base_delay=DOWNLOAD_BASE_DELAY, max_delay=DOWNLOAD_MAX_DELAY):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"Tentative {attempt + 1}/{max_retries} reussie pour {func.__name__}")
                    return result
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    retryable_keywords = ["errno 11", "resource temporarily unavailable", "timeout", "connection", "temporarily unavailable", "rate limit", "too many requests", "503", "502", "504", "connection refused", "connection reset"]
                    if not any(kw in error_str for kw in retryable_keywords):
                        logger.error(f"Erreur non reessayable dans {func.__name__}: {e}")
                        raise
                    if attempt == max_retries - 1:
                        logger.error(f"Echec apres {max_retries} tentatives pour {func.__name__}: {e}")
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.3)
                    total_delay = delay + jitter
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} echouee pour {func.__name__}: {e}. Nouvel essai dans {total_delay:.2f}s")
                    time.sleep(total_delay)
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
    return jsonify({
        'status': 'ok',
        'message': 'RecrutBank API is running with DeepSeek',
        'version': 'v7.0-deepseek-reasoning',
        'features': {
            'pdf_available': PDFPLUMBER_AVAILABLE,
            'docx_available': DOCX_AVAILABLE,
            'reportlab_available': REPORTLAB_AVAILABLE,
            'openpyxl_available': OPENPYXL_AVAILABLE,
            'ia_available': IA_ANALYSE_ACTIVE,
            'ia_provider': 'DeepSeek' if IA_ANALYSE_ACTIVE else 'None',
            'reasoning_mode': True,
            'scoring_strict': True,
            'manual_status_priority': True,
            'auto_width_excel': True,
            'max_concurrent_downloads': DOWNLOAD_MAX_CONCURRENT,
            'zip_max_workers': _ZIP_MAX_WORKERS,
            'intelligent_scoring': True,
            'advanced_reasoning': True
        }
    }), 200
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
            logger.warning(f"Erreur temporaire detectee, activation du mode robuste pour {blob_name}")
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
    typo_corrections = {'risque de marche': 'risque de marche', 'risque marche': 'risque marche', 'market risk': 'market risk', 'taux de change': 'taux de change', 'liquidite': 'liquidite', 'competence': 'competence', 'experience': 'experience'}
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
    negative_patterns = [r"\b(pas\s+de|pas\s+d')\s*(experience|experimente|competence)\b", r'\b(aucun|aucune|aucuns|aucunes)\s*(experience|competence|connaissance)\b', r'\b(sans|depourvu\s+de|manque\s+de)\s*(experience|competence)\b', r"\b(n')?(?:ai|as|a|avons|avez|ont)\s+pas\s+(?:d')?(experience|competence|connaissance)\b", r'\b(jamais\s+(?:eu|travaille|exerce|pratique))\b', r"\b(peu\s+d')?experience\b", r'\b(experience\s+(?:limitee|insuffisante|faible|partielle))\b', r'\b(ne\s+connais\s+pas|ne\s+maitrise\s+pas|ne\s+possede\s+pas)\b', r'\b(no\s+experience|without\s+experience|lack\s+of\s+experience)\b']
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
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024
MAX_TEXT_SIZE = 15000
def extract_text_from_pdf_robust(file_bytes, filename):
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        logger.warning(f"PDF trop volumineux ({len(file_bytes) / 1024 / 1024:.1f} MB > 10 MB): {filename}")
        return ""
    text = ""
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                total_pages = min(len(pdf.pages), MAX_PDF_PAGES)
                for i in range(total_pages):
                    try:
                        page = pdf.pages[i]
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
                    except Exception as e:
                        logger.warning(f"pdfplumber page {i} erreur: {e}")
                        continue
            if text.strip() and len(text.strip()) > 50:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"pdfplumber erreur: {e}")
    if PYPDF2_AVAILABLE:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            total_pages = min(len(reader.pages), MAX_PDF_PAGES)
            for i in range(total_pages):
                try:
                    content = reader.pages[i].extract_text()
                    if content:
                        text += normalize_spaces(content) + "\n"
                    if len(text) > MAX_TEXT_SIZE:
                        text = text[:MAX_TEXT_SIZE]
                        break
                except Exception as e:
                    logger.warning(f"PyPDF2 page {i} erreur: {e}")
                    continue
            if text.strip() and len(text.strip()) > 50:
                return normalize_unicode(text.strip())
        except Exception as e:
            logger.warning(f"PyPDF2 erreur: {e}")
    if len(text.strip()) < 50 and OCR_AVAILABLE:
        try:
            ocr_text = extract_text_from_pdf_via_ocr(file_bytes)
            if ocr_text and len(ocr_text.strip()) > 50:
                return ocr_text
        except Exception as e:
            logger.warning(f"OCR erreur: {e}")
    if len(text.strip()) < 30:
        try:
            for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    raw_text = file_bytes.decode(encoding, errors='ignore')
                    raw_text = re.sub(r'[^\w\s\.\,\:\;\-\?\!\@\#\%\&\*\(\)\-\+\=\/\'\"]', ' ', raw_text)
                    raw_text = re.sub(r'\s+', ' ', raw_text).strip()
                    if len(raw_text) > 50:
                        logger.info(f"Extraction brute reussie avec {encoding}")
                        return normalize_unicode(raw_text)
                except:
                    continue
        except Exception as e:
            logger.warning(f"Extraction brute echouee: {e}")
    return text.strip() if text.strip() else ""
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
        logger.warning(f"Fallback DOCX echoue: {e2}")
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
    text = ""
    if ext == 'pdf':
        text = extract_text_from_pdf_robust(file_bytes, filename)
        logger.info(f"Extraction PDF {filename}: {len(text)} caracteres")
    elif ext in ('doc', 'docx'):
        text = extract_text_from_docx_robust(file_bytes)
        logger.info(f"Extraction DOCX {filename}: {len(text)} caracteres")
    elif ext == 'txt':
        text = extract_text_from_txt(file_bytes)
        logger.info(f"Extraction TXT {filename}: {len(text)} caracteres")
    else:
        try:
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    text = file_bytes.decode(encoding, errors='ignore').strip()
                    if len(text) > 50:
                        break
                except:
                    continue
            if len(text) > MAX_TEXT_SIZE:
                text = text[:MAX_TEXT_SIZE]
            text = normalize_unicode(normalize_spaces(text))
            logger.info(f"Extraction brute {filename}: {len(text)} caracteres")
        except Exception:
            pass
    if len(text.strip()) < 30:
        logger.warning(f"Extraction faible pour {filename}: {len(text)} caracteres")
    return text.strip() if text.strip() else ""
def init_recruteur():
    try:
        if supabase:
            response = supabase.table('recruteurs').select('*').eq('email', 'sougnabeoualoumibank@gmail.com').execute()
            if not response.data:
                supabase.table('recruteurs').insert({"email": "sougnabeoualoumibank@gmail.com", "password": hash_pwd("AdminLaurent123"), "nom": "Responsable RH"}).execute()
    except Exception as e:
        logger.warning(f"Erreur initialisation recruteur : {e}")
init_recruteur()
POSTES = ["Responsable Administration de Credit", "Analyste Credit CCB", "Archiviste (Administration Credit)", "Senior Finance Officer", "Market Risk Officer", "IT Reseau & Infrastructure", "Auditeur interne", "Chef service controle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marche", "Chef service reporting reglementaire", "Chef de Section Compensation", "Charge(e) d'Administration de Credit", "Chef de Division Local Corporate", "Data Analyst Finance"]
POSTES_ACTIFS = ["Chef de Division Local Corporate", "Data Analyst Finance"]
POSTES_CLOTURES = [p for p in POSTES if p not in POSTES_ACTIFS]
def is_poste_actif(poste):
    return poste in POSTES_ACTIFS
GRILLE = {
    "Chef de Division Local Corporate": {
        "eliminatoire": [
            "A une experience dans le secteur bancaire ou financier reglemente",
            "A un diplome de niveau Bac+4 ou superieur (Master, MBA ou equivalent)",
            "A minimum 5 ans d'experience professionnelle dans une banque ou institution financiere",
            "A une experience manageriale demontree (encadrement d'equipe, pilotage d'activite commerciale)",
            "A une exposition a la gestion du risque de credit ou au suivi de la qualite d'un portefeuille (NPL, provisions)"
        ],
        "a_verifier": [
            "A encadre et evalue une equipe commerciale ou bancaire",
            "A assure le suivi de la qualite du portefeuille de credit (NPL, CIR, provisions) et rendu compte a la direction",
            "A developpe des ventes croisees (cross-selling) ou des partenariats interdépartementaux",
            "A produit ou supervise des rapports de performance commerciale et financiere",
            "A une exposition a la reglementation bancaire locale (COBAC, BEAC) ou internationale",
            "A pilote une activite Corporate avec des objectifs de revenus atteints",
            "A encadre, developpe et evalue les performances d'une equipe (fixation d'objectifs, evaluations annuelles, montee en competences)"
        ],
        "signaux_forts": [
            "A une experience averee en cross-selling avec des equipes TSG, Trade Finance ou Cash Management",
            "A demontre un leadership fort (constitution d'equipe, developpement des collaborateurs, vivier de talents)",
            "Possede une certification bancaire (Ecobank, Moody's, ITB - Institut Technique de Banque, ou equivalent)",
            "A une exposition aux plateformes numeriques bancaires (OMNI, Cash Management ou equivalent)",
            "Presente des resultats commerciaux quantifies et verifiables dans son CV (chiffres d'affaires, taux de croissance, NPS)",
            "A developpe le portefeuille Corporate avec acquisition de nouveaux clients majeurs",
            "A une connaissance approfondie du marche corporate tchadien ou de la zone CEMAC/UEMOA"
        ],
        "points_attention": [
            "Profil techniquement solide (credit, analyse) mais sans experience manageriale ni pilotage de P&L",
            "Experiences tres courtes (moins de 2 ans par poste) sans progression hierarchique visible",
            "CV sans resultats chiffres (missions decrites en responsabilites sans livrables ni indicateurs atteints)",
            "Trous inexpliques dans le parcours ou incoherences entre les postes declares"
        ]
    },
    "Chef de Section Compensation": {
        "eliminatoire": ["A une experience en banque ou etablissement financier reglemente", "A un diplome de niveau Bac+3 minimum (Licence, Bachelor ou equivalent)", "A minimum 3 ans d'experience en operations bancaires ou back-office", "A une exposition aux operations de compensation interbancaire", "A une connaissance des regles BEAC / GIMAC ou d'un systeme de compensation equivalent"],
        "a_verifier": ["Supervise quotidiennement les operations de compensation interbancaire", "Gere les suspens, rejets et reclamations interbancaires", "Encadre et coordonne une equipe operationnelle", "Utilise des systemes bancaires de compensation (SYSTAC, SYGMA, SWIFT)", "Produit des reportings operationnels ou reglementaires", "Participe a des controles internes, audits COBAC ou inspections reglementaires"],
        "signaux_forts": ["Maitrise le reglement de positions nettes dans les delais reglementaires", "A une experience dans une banque de la zone CEMAC / UEMOA", "A reussi des audits COBAC ou controles internes sans reserve majeure", "Gere une equipe avec des resultats mesurables", "Maitrise le controle interne et la comptabilite bancaire (SYSCOHADA)"],
        "points_attention": ["Parcours purement comptable sans exposition aux operations interbancaires", "Role uniquement administratif ou de support, sans responsabilite operationnelle", "Absence de tout role managerial dans le parcours", "CV avec missions trop generiques, sans livrables ni resultats quantifies"]
    },
    "Charge(e) d'Administration de Credit": {
        "eliminatoire": ["A une experience dans une banque ou un etablissement financier reglemente", "A un diplome de niveau Bac+3 minimum (Licence, Bachelor ou equivalent)", "A minimum 1 an d'experience dans une fonction bancaire", "A une exposition au cycle de vie du credit bancaire", "A une connaissance des normes comptables bancaires ou de la reglementation COBAC"],
        "a_verifier": ["Gere le cycle complet d'un credit (mise en place, suivi, garanties, cloture)", "Suit et securise les garanties (enregistrement, valorisation, renouvellement)", "Supervise les echeances et produit des alertes aux gestionnaires de portefeuille", "Detecte et remonte les impayes, depassements ou incidents de portefeuille", "Produit des reportings de portefeuille (tableaux de bord, rapports)", "Participe a des comites de risque, audits internes ou inspections reglementaires", "Maitrise un systeme bancaire de gestion du credit (Finacle, T24, Amplitude)"],
        "signaux_forts": ["Maitrise la norme IFRS 9 : staging du portefeuille (Stage 1, 2, 3), ECL, provisions", "Suit et securise les garanties avec coordination juridique", "Produit des reportings portefeuille (encours, impayes, depassements, couverture)", "Participe aux comites de risque et traite les anomalies", "Maitrise les Produits de Portefeuille (PP) et la politique de credit (GCPPM)", "A reussi des audits ou controles internes sans reserve majeure", "Demontre une rigueur documentaire exemplaire"],
        "points_attention": ["Parcours purement commercial ou front-office sans exposition a l'administration des credits", "Profil uniquement comptable (SYSCOHADA) sans gestion du cycle de credit bancaire", "Profil exclusivement theorique (stage ou formation seule) sans experience operationnelle", "Experiences tres courtes (< 1 an par poste) sans progression dans la fonction", "Absence de mention des outils bancaires (systeme de gestion du credit, Excel avance, reporting)"]
    },
    "Auditeur interne": {
        "eliminatoire": ["A une experience reelle en audit interne ou externe", "A minimum 3 ans en audit bancaire ou cabinet d'audit", "A une connaissance des normes d'audit et controle interne", "A un diplome de niveau Bac+4 ou superieur", "A une experience en redaction de rapports d'audit"],
        "a_verifier": ["A realise des missions d'audit sur site", "Evalue les risques operationnels", "Redige des rapports d'audit detailles", "Assure le suivi des recommandations", "Connait les normes IIA / IPPF", "Maitrise la reglementation bancaire (COBAC)", "A une experience en audit IT ou systemes d'information"],
        "signaux_forts": ["Possede une certification CIA / CPA / ACCA", "A une experience dans une banque de la zone CEMAC / UEMOA", "A participe a des inspections reglementaires", "A une expertise en audit des risques de credit", "Maitrise les outils d'audit (ACL, IDEA, etc.)"],
        "points_attention": ["Profil purement comptable sans experience d'audit", "Aucune experience terrain en audit (uniquement du support)", "CV flou sur les missions d'audit realisees", "Absence de connaissances en reglementation bancaire"]
    },
    "Chef service controle des engagements": {
        "eliminatoire": ["Maitrise le risque credit et l'analyse financiere", "A une experience significative en octroi de credits", "A minimum 5 ans en institution financiere", "A un diplome de niveau Bac+4 ou superieur", "A une experience en animation de comite de credit"],
        "a_verifier": ["Analyse financierement les dossiers d'entreprises", "Structure des credits complexes", "Anime des comites de credit", "Encadre et manage une equipe", "Maitrise la classification des risques (IFRS 9)", "A une experience en restructuration de dossiers sensibles", "Possede une formation en risk management"],
        "signaux_forts": ["A gere des dossiers de credit a enjeux importants", "A une experience en banque Corporate", "A participe a des audits ou inspections reglementaires", "Possede une certification en risk management (FRM, PRMIA)"],
        "points_attention": ["Profil purement commercial sans analyse financiere", "Aucune experience en analyse de risque credit", "CV oriente relation client uniquement"]
    },
    "Senior Finance Officer": {
        "eliminatoire": ["A une experience en reporting financier structure", "A une exposition aux etats financiers", "A minimum 3 ans en departement finance ou cabinet d'audit", "A une interaction avec les auditeurs", "A un diplome de niveau Bac+4 ou superieur en finance/comptabilite"],
        "a_verifier": ["Produit des etats financiers", "Realise le reporting groupe", "Connait les normes IFRS", "Maitrise les contraintes reglementaires", "A une experience en consolidation de comptes", "Utilise des outils ERP (SPECTRA, CERBER, SAP)"],
        "signaux_forts": ["A une expertise en IFRS / consolidation", "A interagi avec les commissaires aux comptes (CAC)", "Maitrise les outils SPECTRA / CERBER / ERP", "Possede une certification ACCA, CPA ou CFA", "A une experience en reporting groupe"],
        "points_attention": ["Profil comptable junior sans responsabilite reelle", "Pas de responsabilite en production d'etats financiers", "CV flou sur les livrables produits"]
    },
    "Market Risk Officer": {
        "eliminatoire": ["A une base solide en risques de marche", "A une exposition a FX / taux / liquidite", "A minimum 3 ans en institution financiere", "A un diplome de niveau Bac+4 ou superieur en finance/quantitatif", "Maitrise VaR ou stress testing"],
        "a_verifier": ["Analyse des positions de marche", "Maitrise Excel avance", "Connait VBA ou Python", "Produit du reporting risque", "Connait les produits FICC", "A une experience en gestion ALM / liquidite"],
        "signaux_forts": ["Maitrise Bale II / III", "A une experience en modelisation de risques", "Utilise des outils de quantification (R, Python)", "Possede une certification FRM ou equivalent", "A une experience en reporting prudentiel"],
        "points_attention": ["CV trop theorique academique", "Aucune mention d'outils de modelisation", "Absence d'experience en gestion de risques"]
    },
    "IT Reseau & Infrastructure": {
        "eliminatoire": ["A une experience en reseau / infrastructure", "A une exposition a environnement critique", "A une notion de securite IT", "A minimum 2 ans d'experience", "A une experience en gestion de reseaux LAN/WAN/VPN"],
        "a_verifier": ["Gere les reseaux LAN/WAN/VPN", "Administre des serveurs Windows/Linux", "A une connaissance du Cloud (AWS, Azure, GCP)", "Gere les incidents IT", "Assure la disponibilite des systemes", "A une experience en cybersecurite / firewall"],
        "signaux_forts": ["A une certification Cisco ou Microsoft", "A une experience en virtualisation (VMware, Hyper-V)", "A une experience en systemes bancaires core banking", "Maitrise ITIL / gestion de services IT", "A une experience en haute disponibilite / PRA/PCA"],
        "points_attention": ["Profil trop helpdesk sans expertise reseau", "CV sans detail technique precis", "Aucune mention de securite informatique"]
    },
    "Chef service reporting reglementaire": {
        "eliminatoire": ["A une comptabilite bancaire approfondie", "A une experience en reporting reglementaire (BEAC, COBAC, SPECTRA)", "A minimum 5 ans en banque ou cabinet d'audit bancaire", "A un diplome de niveau Bac+4 ou superieur", "A une experience en production de rapports reglementaires"],
        "a_verifier": ["Produit des rapports reglementaires", "Effectue le controle de coherence des donnees", "Assure la veille reglementaire bancaire", "Interagit avec les autorites de tutelle", "Maitrise SPECTRA / CERBER / outils BEAC", "Connait les normes COBAC"],
        "signaux_forts": ["A une expertise en reporting prudentiel Bale", "A une formation en comptabilite bancaire specialisee", "A une experience en audits reglementaires", "A participe a des inspections COBAC"],
        "points_attention": ["Profil generaliste sans specialisation bancaire", "Aucune experience en reporting reglementaire", "CV flou sur les livrables produits"]
    },
    "Archiviste (Administration Credit)": {
        "eliminatoire": ["A une experience en gestion documentaire structuree", "Demontre une rigueur dans son parcours", "A une experience en archivage physique et electronique", "A une experience en gestion de dossiers sensibles"],
        "a_verifier": ["Gere l'archivage physique et electronique", "Manipule des garanties ou contrats", "Utilise des systemes GED", "Assure la tracabilite des documents", "Applique les procedures d'archivage", "A une experience en banque ou juridique"],
        "signaux_forts": ["A une experience en banque ou secteur juridique", "Manipule des garanties ou contrats", "A une certification en gestion documentaire", "A une experience en dematerialisation"],
        "points_attention": ["Profil trop generaliste", "CV desorganise sans experience documentaire", "Absence de mention de GED ou d'archivage numerique"]
    },
    "Responsable Administration de Credit": {
        "eliminatoire": ["A une experience bancaire significative (minimum 3 ans en credit/risque)", "A une exposition aux garanties ou a la conformite", "A un diplome de niveau Bac+4 ou superieur", "A une experience en validation de dossiers de credit", "A une experience en gestion des garanties"],
        "a_verifier": ["A valide des dossiers de credit", "A gere des garanties", "A participe a des audits", "Connait IFRS 9", "Connait COBAC / conformite", "A suivi un portefeuille / impayes"],
        "signaux_forts": ["Maitrise IFRS 9", "Maitrise COBAC / conformite", "A suivi un portefeuille avec resultats", "A participe a des comites de credit", "Possede une certification en risk management"],
        "points_attention": ["Parcours trop comptable pur", "Role uniquement administratif sans responsabilite", "CV flou avec missions generiques"]
    },
    "Analyste Credit CCB": {
        "eliminatoire": ["A une experience en analyse credit", "A une capacite a lire des etats financiers", "A minimum 3 ans en institution financiere", "A un diplome de niveau Bac+4 ou superieur en finance", "A une experience en structuration de credit"],
        "a_verifier": ["A travaille avec des clients PME", "A travaille avec des clients particuliers", "A structure des credits", "A redige des avis de credit", "A realise des analyses financieres (cash-flow)", "A participe a des comites de credit"],
        "signaux_forts": ["Maitrise l'analyse cash-flow", "A monte des credits complexes", "A participe a des comites de credit", "A une certification en analyse financiere"],
        "points_attention": ["CV trop relation client sans analyse", "Aucune notion de risque", "Experiences tres courtes sans progression"]
    },
    "Data Analyst Finance": {
        "eliminatoire": ["A une formation en Finance, Comptabilite, Controle de gestion, Statistiques, Data Analytics ou Informatique decisionnelle", "A un diplome de niveau Bac+3 ou superieur", "A une experience en analyse financiere, reporting financier, controle de gestion, audit ou data analytics", "Maitrise Excel (TCD, formules, Power Query) - competence incontournable", "A des connaissances en comptabilite et en etats financiers (P&L, bilan, flux de tresorerie)"],
        "a_verifier": ["A produit des rapports financiers periodiques (mensuels, trimestriels)", "A conçu ou maintenu des tableaux de bord financiers (Power BI, Excel ou autre outil BI)", "A realise des analyses Budget / Realise / N-1 avec identification des ecarts", "A travaille avec SQL pour extraire ou interroger des donnees financieres", "A assure la reconciliation de donnees multi-sources (comptabilite / systemes operationnels)", "A participe a l'elaboration d'un budget ou d'un forecast financier", "A une experience dans le secteur bancaire ou avec un Core Banking (FLEXCUBE, T24, Amplitude)"],
        "signaux_forts": ["Maitrise explicite de Power BI (dashboards, DAX, Power Query) avec exemples concrets", "Experience averee en automatisation de reportings (Power Query, VBA, Python, outils ETL)", "Analyse d'ecarts Budget / Realise / N-1 avec presentation a la Direction Financiere ou a la DG", "Participation a la construction de modeles de prevision financiere ou d'analyses de scenarios", "Exposition aux donnees bancaires : PNB, NPL, cout du risque, rentabilite par agence ou produit", "Maitrise de SQL pour l'extraction et la manipulation de donnees en base relationnelle", "Connaissance de Python ou R pour des analyses statistiques avancees", "Mise en place de controles qualite sur les donnees et documentation des regles de calcul", "Resultats quantifies dans le CV : gains de productivite, delais reduits, anomalies detectees"],
        "points_attention": ["Profil purement comptable sans exposition aux outils BI ou au reporting de gestion", "Profil exclusivement IT / developpeur sans connaissance financiere", "Experience uniquement academique ou stage sans production de reportings reels en environnement professionnel", "CV sans aucun outil cite nommement", "Missions decrites en termes generiques sans livrables precis ni resultats mesurables", "Trous inexpliques dans le parcours ou experiences tres courtes sans progression visible"]
    }
}
POSTES_AVEC_SCORING_100 = ["Auditeur interne", "Chef service controle des engagements", "Chef service IT (maintenance/support)", "Chef service finance", "Chef service risques de marche", "Chef service reporting reglementaire"]
POSTES_AVEC_SCORING_12 = ["Chef de Section Compensation", "Charge(e) d'Administration de Credit"]
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
            return "Potentiel a evaluer en entretien"
        else:
            return "Rejet"
    if poste and poste in POSTES_AVEC_SCORING_14:
        if s >= 11:
            return "Entretien prioritaire"
        elif s >= 7:
            return "Potentiel a evaluer en entretien"
        else:
            return "Rejet"
    if poste and poste in POSTES_AVEC_SCORING_100:
        if s >= 80:
            return "Shortlist"
        elif s >= 70:
            return "A considerer"
        elif s >= 60:
            return "Faible"
        else:
            return "Rejet"
    if s >= 8:
        return "Entretien prioritaire"
    elif s >= 5:
        return "Potentiel a evaluer en entretien"
    else:
        return "Rejet"
def get_statut_from_decision(decision):
    if not decision:
        return 'en_attente'
    if "Entretien prioritaire" in decision or "Shortlist" in decision:
        return "retenu"
    elif "Potentiel" in decision or "considerer" in decision or "Faible" in decision:
        return "entretien"
    else:
        return "rejete"
def split_into_jobs(raw_text):
    if not raw_text:
        return []
    text = re.sub(r'\s+', ' ', raw_text)
    patterns = [
        r'(?:^|\n)(?=\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2})\s*[-–—])',
        r'(?:^|\n)(?=\s*(?:de|du|d[eu]|from|since)\s+(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2}))',
        r'(?:^|\n)(?=\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2})\s*[-–—]\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2}|present|current))',
        r'(?:^|\n)(?=\s*(?:[A-Z][A-Z\s]+)\s*(?:-|–|—)\s*(?:[A-Z][a-z]+\s+)?(?:20\d{2}|19\d{2}))',
    ]
    blocks = []
    positions = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            positions.append(match.start())
    positions = sorted(set(positions))
    if not positions:
        return [text.strip()] if text.strip() else []
    for i, pos in enumerate(positions):
        start = pos
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks
STAGE_MARKERS = [r'\bstage\b', r'\bstagiaire\b', r'\binternship\b', r'\bintern\b', r'\bapprenti\b', r'\bapprentissage\b', r'\balternance\b', r'\bstage de fin\b', r'\bstage academique\b', r'\bstage professionnel\b', r'\bstage de formation\b', r'\bpfr\b', r'\bstage pfe\b', r'\bpfe\b', r'\bvolontariat\b', r'\btrainee\b']
STAGE_PATTERN = re.compile('|'.join(STAGE_MARKERS), re.IGNORECASE)
def is_stage_block(block_text):
    return bool(STAGE_PATTERN.search(block_text))
def extract_duration_years_from_block(block_text):
    years = 0.0
    text = block_text.lower()
    _ACCENT_MAP = str.maketrans('àâäéèêëîïôùûüçœæÀÂÄÉÈÊÎÏÔÙÛÜÇŒÆáãõñÁÃÕÑ', 'aaaeeeeiioouucaaAAEEEEIIOUUUCAAaaonaaon')
    text = text.translate(_ACCENT_MAP)
    duration_patterns = [
        r'(\d+[.,]?\d*)\s*(?:ans?|annee?s?|years?|años?|anos?)',
        r'\(\s*(\d+[.,]?\d*)\s*\)\s*(?:ans?|annee?s?|years?)',
        r'\w+\s+\(\s*(\d+[.,]?\d*)\s*\)\s*(?:ans?|annee?s?|years?)',
        r'plus\s+de\s+(\d+[.,]?\d*)\s*(?:ans?|annee?s?|years?)',
        r'depuis\s+(?:plus\s+de\s+)?(\d+[.,]?\d*)\s*(?:ans?|annee?s?)',
        r'(\d+[.,]?\d*)\s*(?:ans?|annee?s?|years?)\s+(?:d[ée]?experience|dans|en|de)',
        r'experience\s+(?:de\s+)?(\d+[.,]?\d*)\s*(?:ans?|annee?s?)'
    ]
    for dp in duration_patterns:
        m = re.search(dp, text)
        if m:
            try:
                years = float(m.group(1).replace(',', '.'))
                if 0 < years <= 40:
                    return years
            except (ValueError, IndexError):
                pass
    date_patterns = [
        r'(?:de|du|d[eu])\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s*(?:[àa]|au|jusqu\'au|-|–|—)\s*(?:ce\s+jour|aujourd\'hui|present|actuel|en\s+cours|nos\s+jours|now|current)',
        r'(?:de|du|d[eu])\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s*(?:[àa]|au|jusqu\'au|-|–|—)\s*(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})',
        r'(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s*[-–—]\s*(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})',
        r'(?:de|du|d[eu])\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\s+(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})',
    ]
    for pattern in date_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                groups = m.groups()
                start_year = None
                end_year = None
                if groups and len(groups) >= 6:
                    try:
                        year_positions = []
                        for idx, g in enumerate(groups):
                            if g and re.match(r'^\d{4}$', g):
                                year_positions.append(idx)
                        if len(year_positions) >= 2:
                            start_year = int(groups[year_positions[0]])
                            end_year = int(groups[year_positions[1]])
                        elif len(year_positions) == 1:
                            start_year = int(groups[year_positions[0]])
                            end_year = datetime.datetime.now().year
                    except (ValueError, IndexError):
                        pass
                elif groups and len(groups) >= 3:
                    try:
                        for g in groups:
                            if g and re.match(r'^\d{4}$', g):
                                start_year = int(g)
                                end_year = datetime.datetime.now().year
                                break
                    except (ValueError, IndexError):
                        pass
                if start_year and end_year:
                    delta = end_year - start_year
                    if 0 < delta <= 40:
                        return round(float(delta), 1)
            except (ValueError, IndexError, TypeError):
                continue
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
    m = re.search(r'(20\d{2})\s*[-–—]\s*(20\d{2})', text)
    if m:
        start_year = int(m.group(1))
        end_year = int(m.group(2))
        delta = end_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    m = re.search(r'depuis\s+(20\d{2})', text)
    if m:
        start_year = int(m.group(1))
        current_year = datetime.datetime.now().year
        delta = current_year - start_year
        if 0 < delta <= 40:
            return round(float(delta), 1)
    m = re.search(r'(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)\s*(20\d{2})\s*(?:a|-|–|—|au|jusqu\'au)\s*(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|jan|fev|mar|avr|juil|aou|sep|oct|nov|dec)?\s*(20\d{2})', text, re.IGNORECASE)
    if m:
        try:
            start_year = int(m.group(2))
            end_year = int(m.group(3)) if m.group(3) else datetime.datetime.now().year
            delta = end_year - start_year
            if 0 < delta <= 40:
                return round(float(delta), 1)
        except (ValueError, IndexError):
            pass
    return 0.0
def detect_institution_type(text):
    text_lower = text.lower()
    commercial_banks = [
        'ecobank', 'orabank', 'uba', 'bicec', 'sgbc', 'cbc', 'bct',
        'societe generale', 'standard chartered', 'nsia banque',
        'commercial bank', 'banque commerciale', 'investment bank',
        'banque d affaires', 'credit institution', 'financial institution',
        'banque', 'express union', 'coris bank', 'orabank tchad',
        'uba tchad', 'commercial bank tchad', 'cbt', 'finadev',
        'united bank for africa', 'banque islamique', 'microfinance',
        'orabank tchad', 'commercial bank tchad', 'societe generale', 'soge'
    ]
    commercial_pattern = re.compile(r'\b(' + '|'.join(re.escape(b) for b in commercial_banks) + r')\b', re.IGNORECASE)
    if commercial_pattern.search(text_lower):
        return 'commercial_bank'
    return 'unknown'
def detect_language(text):
    if not LANGDETECT_AVAILABLE or not text or len(text.strip()) < 50:
        return 'fr'
    try:
        lang = detect(text)
        return 'en' if lang == 'en' else 'fr'
    except Exception:
        return 'fr'
def extract_quantified_results(text):
    patterns = [
        r'(\d+)\s*(?:%|pourcent|percent)\s*(?:d\'|de\s+)?(augmentation|croissance|reduction|hausse|baisse)',
        r'(augmentation|croissance|reduction|hausse|baisse)\s+(?:de\s+)?(\d+)',
        r'(\d+)\s*(?:millions|milliards|M|B|K)\s*(?:€|\$|FCFA|XOF)',
        r'CA\s*(?:de|:)?\s*(\d+)',
        r'chiffre\s*d\'affaires\s*(?:de|:)?\s*(\d+)',
        r'portefeuille\s*(?:de|:)?\s*(\d+)',
        r'clients\s*(?:acquis|developpes)?\s*(?:de|:)?\s*(\d+)',
        r'\b(?:NPL|CIR|provisions?)\s*(?:de|:)?\s*(\d+(?:[.,]\d+)?)\s*%',
    ]
    results = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            results.extend(matches)
    return results
def detect_banking_experience_years(text):
    if not text:
        return 0.0
    text_lower = text.lower()
    years = re.findall(r'\b(19|20)\d{2}\b', text)
    if years:
        years_int = sorted([int(y) for y in years])
        total_years = years_int[-1] - years_int[0]
        if total_years > 0 and total_years < 50:
            bank_keywords = ['ecobank', 'orabank', 'uba', 'banque', 'bank', 'bancaire', 'financial', 'credit', 'credit', 'institution financiere']
            for kw in bank_keywords:
                if kw in text_lower:
                    return total_years
    match = re.search(r'(\d+)\s*(?:ans|annees?)\s*(?:d[ée]?experience\s+)?(?:dans\s+la\s+banque|en\s+banque|bancaire|de\s+banque)', text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r'depuis\s+(19|20)\d{2}', text, re.IGNORECASE)
    if match:
        start_year = int(match.group(1))
        current_year = datetime.datetime.now().year
        return current_year - start_year
    blocks = split_into_jobs(text)
    total_years = 0.0
    for block in blocks:
        if is_stage_block(block):
            continue
        block_lower = block.lower()
        is_banking = False
        banking_keywords = ['ecobank', 'orabank', 'uba', 'banque', 'bank', 'bancaire', 'financial', 'credit', 'credit', 'institution financiere']
        for kw in banking_keywords:
            if kw in block_lower:
                is_banking = True
                break
        if is_banking:
            duration = extract_duration_years_from_block(block)
            if duration > 0:
                total_years += duration
    if total_years > 0:
        return total_years
    return 0.0
def check_criterion_match_semantic(criterion, normalized_text, raw_full_text="", poste=None, lang='fr'):
    keywords = KEYWORD_MAPPING.get(criterion, [])
    if not keywords:
        return False, 0.5, []
    semantic_expansions = {
        "management": ["manager", "leadership", "supervision", "coordination", "direction", "pilotage", "encadrement", "gestion d'equipe", "management d'equipe", "team management", "team lead", "superviseur"],
        "cross-selling": ["ventes croisees", "cross selling", "cross-selling", "synergie commerciale", "commercial synergy", "partenariat", "partnership", "collaboration commerciale"],
        "risk": ["risque", "risk", "NPL", "non performing", "provisions", "IFRS 9", "credit", "credit", "portefeuille", "portfolio", "CIR", "cout du risque"],
        "corporate": ["corporate", "grandes entreprises", "large entreprises", "entreprises", "clients corporate", "corporate clients", "grands comptes", "key accounts"],
        "certification": ["certification", "certificat", "certified", "ITB", "Moody's", "Ecobank", "MBA", "Master", "formation"]
    }
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
            if ratio >= 75:
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}~{ratio/100:.2f}")
                    best_score = max(best_score, ratio / 100)
                continue
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.5):
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                    best_score = max(best_score, len(common) / len(kw_tokens))
    if best_score < 0.5 and lang in ['en', 'fr']:
        for category, synonyms in semantic_expansions.items():
            for kw in keywords:
                if category in kw.lower() or kw.lower() in category:
                    for syn in synonyms:
                        syn_clean, _ = normalize_for_matching(syn)
                        if syn_clean in text_clean:
                            found_kws.append(f"{syn} (semantique)")
                            best_score = max(best_score, 0.75)
    threshold = 0.45 if len(normalized_text) < 500 else 0.55
    return best_score >= threshold, round(best_score, 2), found_kws
def analyze_cv_with_deepseek_reasoning(cv_text, lettre_text, attestation_texts_list, poste):
    if not IA_ANALYSE_ACTIVE or not cv_text or len(cv_text.strip()) < 50 or poste not in GRILLE:
        return None
    grille = GRILLE.get(poste, {})
    system_prompt = """Tu es un consultant senior en recrutement bancaire avec 20 ans d'experience en Afrique centrale et de l'Ouest (CEMAC/UEMOA).
    REGLES ABSOLUES D'ANALYSE :
    1. Tu DOIS raisonner etape par etape comme un expert humain.
    2. Tu ne JAMAIS inventer des faits qui ne sont PAS dans les documents.
    3. Si une information n'est PAS mentionnee, tu la consideres comme ABSENTE.
    4. Les stages, benefolats et formations NE COMPTENT PAS comme experience pro.
    5. Tu JUSTIFIES chaque evaluation avec des CITATIONS du CV/lettre.
    6. Tu utilises le contexte CEMAC/UEMOA (COBAC, BEAC, reglementation locale).
    METHODE DE RAISONNEMENT :
    Etape 1: Analyser la structure du CV (qualite, clarte, professionnalisme)
    Etape 2: Verifier les criteres eliminatoires UN PAR UN (avec justifications)
    Etape 3: Evaluer la coherence du parcours (progression, stabilite, logique)
    Etape 4: Identifier les competences techniques et manageriales
    Etape 5: Detecter les signaux de qualite (resultats quantifies, certifications)
    Etape 6: Synthetiser et donner une recommandation argumentee
    FORMAT DE SORTIE : Structure JSON avec des scores et des justifications detaillees."""
    def fmt_list(items):
        return "\n".join(f"  {i+1}. {c}" for i, c in enumerate(items)) if items else "  (aucun)"
    user_message = f"""POSTE : {poste}
=== GRILLE D'EVALUATION ===
CRITERES ELIMINATOIRES (rejet immediat si non valide) :
{fmt_list(grille.get('eliminatoire', []))}
POINTS A VERIFIER (qualite du profil) :
{fmt_list(grille.get('a_verifier', []))}
SIGNAUX FORTS (bonus, profil prioritaire) :
{fmt_list(grille.get('signaux_forts', []))}
POINTS D'ATTENTION (red flags a verifier) :
{fmt_list(grille.get('points_attention', []))}
=== DOCUMENTS DU CANDIDAT ===
CV :
{cv_text[:12000]}
LETTRE DE MOTIVATION :
{lettre_text[:3000] if lettre_text else '(Aucune lettre fournie)'}
ATTESTATIONS/CERTIFICATS :
{''.join(attestation_texts_list)[:3000] if attestation_texts_list else '(Aucune attestation)'}
=== INSTRUCTIONS ===
1. Analyse le CV comme un expert RH.
2. RAISONNE ETAPE PAR ETAPE avant de conclure.
3. Donne des SCORES JUSTIFIES sur chaque critere.
4. Identifie les FORCES et FAIBLESSES du profil.
5. Produis une SYNTHESE claire et actionable pour le recruteur.
6. Utilise le format JSON attendu."""
    try:
        with _ia_semaphore:
            response = _deepseek_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.1,
                max_tokens=4096,
                response_format={"type": "json_object"}
            )
        result_text = response.choices[0].message.content
        logger.info(f"Analyse DeepSeek terminee: {len(result_text)} caracteres")
        try:
            analyse = json.loads(result_text)
        except json.JSONDecodeError:
            import re as re_json
            json_match = re_json.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                analyse = json.loads(json_match.group())
            else:
                logger.error("Impossible de parser la reponse DeepSeek")
                return None
        flags_elim = analyse.get('flags_eliminatoires', [])
        if isinstance(flags_elim, list):
            flags_elim = [f for f in flags_elim if f]
        else:
            flags_elim = []
        lm = analyse.get('lettre_motivation', {})
        if lm.get('eliminatoire', False):
            flags_elim.append(f"Lettre: {lm.get('commentaire', 'eliminatoire')}")
        score_total = 0 if flags_elim else int(analyse.get('score_total', 0))
        score_max = get_score_max_for_poste(poste)
        decision = get_recommandation_from_score(score_total, poste)
        points_forts = analyse.get('points_forts', [])
        points_vigilance = analyse.get('points_vigilance', [])
        synthese = analyse.get('synthese_recruteur', '')
        sous_scores = analyse.get('sous_scores', {})
        details = {
            'moteur': 'DeepSeek Reasoning v2',
            'model': DEEPSEEK_MODEL,
            'analyse_raw': analyse,
            'points_forts': points_forts,
            'points_vigilance': points_vigilance,
            'synthese_recruteur': synthese,
            'raisonnement_detaille': analyse.get('raisonnement', '')
        }
        return {
            'score': score_total,
            'score_max': score_max,
            'decision': decision,
            'flags_eliminatoires': flags_elim,
            'sous_scores': sous_scores,
            'checklist': analyse.get('checklist', {}),
            'signaux_detectes': analyse.get('signaux_detectes', []),
            'points_forts': points_forts,
            'points_vigilance': points_vigilance,
            'synthese': synthese,
            'details': details
        }
    except Exception as e:
        logger.error(f"Erreur analyse DeepSeek: {e}")
        return None
def calculate_score_chef_division_corporate(cv_text, lettre_text, attestation_texts_list):
    poste = "Chef de Division Local Corporate"
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    banking_years = detect_banking_experience_years(raw_full)
    if banking_years >= 5:
        exp_bancaire = 4
    elif banking_years >= 3:
        exp_bancaire = 2
    elif banking_years >= 1:
        exp_bancaire = 1
    else:
        exp_bancaire = 0
    has_master = bool(re.search(r'master|mba|ingenieur|doctorat|phd', cv_text, re.IGNORECASE))
    has_bac4 = bool(re.search(r'bac\+[45]|bac [45]|maitrise|licence.*professionnelle', cv_text, re.IGNORECASE))
    if has_master:
        diplome = 3
    elif has_bac4:
        diplome = 2
    else:
        diplome = 0
    management_count = 0
    for kw in ['manager', 'directeur', 'chef', 'superviseur', 'encadrement', 'management', 'leadership', 'gestion d\'equipe']:
        if kw in cv_text.lower():
            management_count += 1
    management = min(3, management_count)
    credit_count = 0
    for kw in ['credit', 'credit', 'risque', 'risk', 'npl', 'provision', 'portefeuille', 'garantie', 'impaye']:
        if kw in cv_text.lower():
            credit_count += 1
    if credit_count >= 4:
        risque_credit = 2
    elif credit_count >= 2:
        risque_credit = 1
    else:
        risque_credit = 0
    jobs = cv_text.split('\n')
    job_count = 0
    for j in jobs:
        if 'chef' in j.lower() or 'manager' in j.lower() or 'responsable' in j.lower():
            job_count += 1
    coherence = 2 if job_count >= 3 else (1 if job_count >= 1 else 0)
    qualite_cv = 1 if len(cv_text) > 500 else 0
    score = exp_bancaire + diplome + management + risque_credit + coherence + qualite_cv
    score = min(14, score)
    checklist = {
        "elim_0": banking_years >= 5,
        "elim_1": has_master or has_bac4,
        "elim_2": management_count >= 2,
        "elim_3": credit_count >= 3,
        "elim_4": job_count >= 3
    }
    if score >= 11:
        decision = "Entretien prioritaire"
    elif score >= 7:
        decision = "Potentiel a evaluer en entretien"
    else:
        decision = "Rejet"
    points_forts = []
    points_vigilance = []
    if banking_years >= 5:
        points_forts.append(f"Plus de 5 ans d'experience bancaire ({banking_years:.1f} ans)")
    else:
        points_vigilance.append(f"Experience bancaire de {banking_years:.1f} ans (idealement 5 ans+)")
    if has_master:
        points_forts.append("Diplome Bac+5 ou superieur")
    elif has_bac4:
        points_forts.append("Diplome Bac+4")
    else:
        points_vigilance.append("Niveau de diplome inferieur a Bac+4")
    if management >= 2:
        points_forts.append(f"Experience manageriale (score: {management}/3)")
    else:
        points_vigilance.append("Experience manageriale a renforcer")
    if risque_credit >= 2:
        points_forts.append(f"Exposition au risque de credit (score: {risque_credit}/3)")
    else:
        points_vigilance.append("Exposition au risque de credit limitee")
    if coherence >= 2:
        points_forts.append("Parcours coherent avec des postes a responsabilite")
    if qualite_cv >= 1:
        points_forts.append("CV detaille")
    if banking_years < 5 or management < 2:
        points_vigilance.append("Verifier en entretien : experience et management")
    return {
        'score': score,
        'score_max': 14,
        'decision': decision,
        'flags_eliminatoires': [],
        'checklist': checklist,
        'sous_scores': {
            "Experience bancaire": exp_bancaire,
            "Diplome": diplome,
            "Management": management,
            "Risque de credit": risque_credit,
            "Coherence": coherence,
            "Qualite CV": qualite_cv
        },
        'points_forts': points_forts,
        'points_vigilance': points_vigilance,
        'synthese': f"Candidat avec un score de {score}/14. " + (decision if "prioritaire" in decision else "A evaluer.")
    }
def calculate_score_charge_admin_credit(cv_text, lettre_text, attestation_texts_list):
    poste = "Charge(e) d'Administration de Credit"
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized = normalize_for_matching(raw_full)[0]
    banking_years = detect_banking_experience_years(cv_text)
    flags_elim = []
    diplome_ok = False
    diplome_patterns = [r'licence', r'bachelor', r'bac\+3', r'bac 3', r'baccalaureat.*universite', r'master', r'mba', r'ingenieur', r'bac\+4', r'bac 4', r'bac\+5', r'bac 5', r'maitrise', r'doctorat', r'phd', r'ecole de commerce', r'ecole superieure']
    for pattern in diplome_patterns:
        if re.search(pattern, cv_text.lower()):
            diplome_ok = True
            break
    if not diplome_ok:
        flags_elim.append("Niveau de diplome inferieur a Bac+3")
    exp_bancaire_ok = banking_years >= 1.0
    if not exp_bancaire_ok:
        flags_elim.append(f"Moins de 1 an d'experience bancaire ({banking_years:.1f} ans) - les stages ne sont pas comptabilises")
    credit_cycle_ok = False
    credit_cycle_keywords = ['credit', 'credit', 'dossier de credit', 'analyse de credit', 'instruction credit', 'octroi', 'mise en place', 'suivi credit', 'garantie', 'echeance', 'portefeuille', 'administration de credit', 'back-office credit', 'credit administration']
    for kw in credit_cycle_keywords:
        if kw in cv_text.lower():
            credit_cycle_ok = True
            break
    if not credit_cycle_ok:
        flags_elim.append("Aucune exposition au cycle de vie du credit bancaire")
    reporting_ok = False
    reporting_keywords = ['reporting', 'rapport', 'tableau de bord', 'dashboard', 'statistiques', 'indicateur', 'kpi', 'report', 'suivi', 'monitoring']
    for kw in reporting_keywords:
        if kw in cv_text.lower():
            reporting_ok = True
            break
    if not reporting_ok:
        flags_elim.append("Aucune experience de production de reportings")
    tools_ok = False
    tools_keywords = ['excel', 'word', 'powerpoint', 'outlook', 'office', 'bureautique', 'tableur']
    for kw in tools_keywords:
        if kw in cv_text.lower():
            tools_ok = True
            break
    if not tools_ok:
        flags_elim.append("Incapacite a utiliser des outils bureautiques courants")
    adequation = 0
    if re.search(r'(economie|gestion|finance|comptabilite|banque|commerce)', cv_text.lower()):
        adequation += 1
    credit_years = 0.0
    blocks = split_into_jobs(cv_text)
    for block in blocks:
        if not is_stage_block(block) and ('credit' in block.lower() or 'credit' in block.lower()):
            duration = extract_duration_years_from_block(block)
            if duration > 0:
                credit_years += duration
    if credit_years >= 3:
        adequation += 2
    elif credit_years >= 1:
        adequation += 1
    if re.search(r'(certification|certificat).*(bancaire|credit|finance)', cv_text.lower()):
        adequation += 0.5
    adequation = min(3, int(adequation))
    ifrs_score = 0
    if re.search(r'ifrs|provisionnement|stage\s*[123]|ecl', cv_text.lower()):
        ifrs_score += 2
    if re.search(r'cobac|reglementation bancaire|conformite', cv_text.lower()):
        ifrs_score += 1
    if re.search(r'portefeuille|encours|impayes|depassements', cv_text.lower()):
        ifrs_score += 0.5
    ifrs_score = min(3, int(ifrs_score))
    tools_score = 0
    banking_tools = ['amplitude', 'finacle', 't24', 'temenos', 'flexcube', 'sopra', 'systeme de gestion', 'ged']
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
    if re.search(r'(banque|finance|economie|gestion)', cv_text.lower()):
        coherence += 1
    coherence = min(2, coherence)
    qualite = 0
    if re.search(r'\d+\s*%|\d+\s*dossiers|\d+\s*cas|\d+\s*rapports', cv_text.lower()):
        qualite += 1
    if lettre_text and len(lettre_text.strip()) > 50:
        if 'ecobank' in lettre_text.lower() and ('administration de credit' in lettre_text.lower() or 'charge' in lettre_text.lower()):
            qualite += 1
        elif 'charge' in lettre_text.lower() and 'credit' in lettre_text.lower():
            qualite += 0.5
    qualite = min(2, int(qualite))
    total_score = adequation + ifrs_score + tools_score + coherence + qualite
    total_score = min(12, total_score)
    if total_score >= 10:
        decision = "Entretien prioritaire"
    elif total_score >= 7:
        decision = "Potentiel a evaluer en entretien"
    else:
        decision = "Rejet"
    points_forts = []
    points_vigilance = []
    if adequation >= 2:
        points_forts.append(f"Experience credit de {credit_years:.1f} ans")
    if ifrs_score >= 2:
        points_forts.append("Connaissance IFRS 9 / COBAC")
    if tools_score >= 2:
        points_forts.append("Maitrise des outils bancaires")
    if coherence >= 2:
        points_forts.append("Parcours coherent et stable")
    if qualite >= 2:
        points_forts.append("CV detaille avec resultats chiffres")
    if adequation < 2:
        points_vigilance.append("Experience credit limitee")
    if ifrs_score < 2:
        points_vigilance.append("IFRS 9 a approfondir")
    if coherence < 2:
        points_vigilance.append("Parcours a stabiliser")
    if qualite < 2:
        points_vigilance.append("CV/lettre a enrichir")
    sous_scores = {"Adequation formation/experience au credit": adequation, "Exposition IFRS 9 / gestion portefeuille": ifrs_score, "Maitrise outils bancaires": tools_score, "Coherence et serieux du parcours": coherence, "Qualite CV + Lettre de motivation": qualite}
    synthese = _generate_synthese_rac(cv_text, lettre_text, total_score, points_forts, points_vigilance)
    return {'score': total_score, 'score_max': 12, 'decision': decision, 'flags_eliminatoires': flags_elim if flags_elim else [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {total_score}/12 — {decision}" + (f" - {len(flags_elim)} critere(s) eliminatoire(s) non satisfait(s)" if flags_elim else ""), 'points_forts': points_forts, 'points_vigilance': points_vigilance + flags_elim if flags_elim else points_vigilance, 'synthese': synthese}
def _generate_synthese_rac(cv_text, lettre_text, score, points_forts, points_vigilance):
    synthese = ""
    has_experience = bool(re.search(r'Express Union|Coris Bank|Ecobank|banque|bancaire|\d+\s*ans', cv_text.lower()))
    has_certification = bool(re.search(r'certification|certificat', cv_text.lower()))
    has_results = bool(re.search(r'\d+\s*%|\d+\s*dossiers|\d+\s*rapports', cv_text.lower()))
    lettre_personnalisee = bool(lettre_text and 'ecobank' in lettre_text.lower())
    if score >= 10:
        synthese = "Candidat tres solide pour le poste. "
        if has_experience:
            synthese += "Experience bancaire confirmee. "
        if has_certification:
            synthese += "Certifications pertinentes. "
        if has_results:
            synthese += "Resultats quantifies demontrant la performance. "
        if lettre_personnalisee:
            synthese += "Lettre de motivation personnalisee. "
        synthese += "A recommander pour entretien prioritaire."
    elif score >= 7:
        synthese = "Bon profil avec du potentiel. "
        if has_experience:
            synthese += "Experience bancaire presente. "
        else:
            synthese += "Experience a consolider. "
        if not has_results:
            synthese += "Manque de resultats chiffres. "
        synthese += "A convoquer en entretien."
    else:
        synthese = "Profil en dessous des attentes. "
        if not has_experience:
            synthese += "Experience bancaire insuffisante. "
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
    banking_years = detect_banking_experience_years(cv_text)
    flags = []
    if banking_years < 1.0:
        flags.append("A une experience en banque ou etablissement financier reglemente")
    if banking_years < 3.0:
        flags.append("Minimum 3 ans d'experience en operations bancaires ou back-office")
    has_diploma = bool(re.search(r'licence|bachelor|bac\+3|bac 3|master|mba', cv_text.lower()))
    if not has_diploma:
        flags.append("A un diplome de niveau Bac+3 minimum (Licence, Bachelor ou equivalent)")
    has_compensation = False
    compensation_keywords = ['compensation', 'interbancaire', 'systac', 'sygma', 'gimac', 'back-office', 'clearing']
    for kw in compensation_keywords:
        if kw in cv_text.lower():
            has_compensation = True
            break
    if not has_compensation:
        flags.append("A une exposition aux operations de compensation interbancaire")
        flags.append("A une connaissance des regles BEAC / GIMAC ou d'un systeme de compensation equivalent")
    for crit in grille.get('eliminatoire', []):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        if not ok:
            if "minimum 3 ans" in crit.lower() and banking_years >= 3.0:
                continue
            if crit not in flags:
                flags.append(crit)
    from rapidfuzz import fuzz
    def check_crit(crit):
        ok, _, _ = check_criterion_match_advanced(crit, normalized, raw_full, poste=poste)
        return ok
    signaux_exp = ["Supervision quotidienne des operations de compensation interbancaire", "Denouement de positions nettes en fin de journee", "Gestion de suspens, rejets et reclamations interbancaires", "Utilisation de systemes bancaires de compensation"]
    n_exp = sum(1 for c in signaux_exp if check_crit(c))
    adequation = min(3, n_exp)
    signaux_beac = ["BEAC / GIMAC / compensation interbancaire", "Reglement de positions nettes dans les delais reglementaires", "Experience dans une banque de la zone CEMAC / UEMOA"]
    n_beac = sum(1 for c in signaux_beac if check_crit(c))
    exposition_beac = min(3, n_beac)
    encadrement_ok = check_crit("Encadrement et coordination d'une equipe operationnelle")
    resultats_mesurables = check_crit("Gestion d'une equipe avec resultats mesurables")
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
    sous_scores = {"Adequation de l'experience (compensation interbancaire)": adequation, "Exposition BEAC / GIMAC / SYSTAC": exposition_beac, "Capacite d'encadrement": encadrement, "Coherence du parcours": coherence, "Qualite CV + Lettre": qualite_cv + lettre_score}
    total_score = sum(sous_scores.values())
    total_score = min(12, total_score)
    if total_score >= 10:
        decision = "Entretien prioritaire"
    elif total_score >= 7:
        decision = "Potentiel a evaluer en entretien"
    else:
        decision = "Rejet"
    return {'score': total_score, 'score_max': 12, 'decision': decision, 'flags_eliminatoires': [], 'sous_scores': sous_scores, 'checklist': {}, 'detail': f"Score: {total_score}/12 — {decision}", 'points_forts': ["Experience en compensation interbancaire" if adequation >= 2 else ""], 'points_vigilance': ["Manque d'exposition BEAC" if exposition_beac < 2 else ""], 'synthese': f"Candidat avec un score de {total_score}/12"}
def calculate_score_data_analyst_finance(cv_text, lettre_text, attestation_texts_list):
    all_att = "\n".join(attestation_texts_list) if attestation_texts_list else ""
    raw_full = cv_text + "\n" + (lettre_text or "") + "\n" + all_att
    normalized = normalize_for_matching(raw_full)[0]
    flags_elim = []
    formation_keywords = ['finance', 'comptabilite', 'comptabilite', 'controle de gestion', 'controle de gestion', 'statistiques', 'statistique', 'data analytics', 'analyse de donnees', 'business intelligence', 'informatique decisionnelle', 'informatique decisionnelle', 'economie', 'economie']
    formation_ok = any(kw in cv_text.lower() for kw in formation_keywords)
    if not formation_ok:
        flags_elim.append("Formation en Finance, Comptabilite, Controle de gestion, Statistiques, Data Analytics ou Informatique decisionnelle")
    diplome_ok = False
    diplome_patterns = [r'bac\+3', r'bac 3', r'licence', r'bachelor', r'bac\+4', r'bac 4', r'master', r'mba', r'ingenieur', r'ingenieur', r'bac\+5', r'bac 5', r'maitrise', r'maitrise', r'doctorat', r'phd', r'ecole de commerce', r'ecole de commerce', r'ecole superieure', r'ecole superieure']
    for pattern in diplome_patterns:
        if re.search(pattern, cv_text.lower()):
            diplome_ok = True
            break
    if not diplome_ok:
        flags_elim.append("Diplome de niveau Bac+3 ou superieur")
    exp_keywords = ['analyse financiere', 'analyse financiere', 'reporting financier', 'controle de gestion', 'controle de gestion', 'audit', 'data analytics', 'analyse de donnees', 'analyse de donnees', 'tableau de bord', 'dashboard', 'reporting', 'rapport financier']
    exp_ok = any(kw in cv_text.lower() for kw in exp_keywords)
    if not exp_ok:
        flags_elim.append("Experience en analyse financiere, reporting financier, controle de gestion, audit ou data analytics")
    excel_keywords = ['excel', 'power query', 'tableau croise', 'tcd', 'formule excel', 'vba']
    excel_ok = any(kw in cv_text.lower() for kw in excel_keywords)
    if not excel_ok:
        flags_elim.append("Maitrise Excel (TCD, formules, Power Query) - competence incontournable")
    comptab_keywords = ['comptabilite', 'comptabilite', 'etats financiers', 'etats financiers', 'p&l', 'bilan', 'flux de tresorerie', 'accounting', 'financial statements', 'income statement', 'balance sheet', 'cash flow']
    comptab_ok = any(kw in cv_text.lower() for kw in comptab_keywords)
    if not comptab_ok:
        flags_elim.append("Connaissances en comptabilite et en etats financiers (P&L, bilan, flux de tresorerie)")
    if flags_elim:
        return {'score': 0, 'score_max': 14, 'decision': 'Rejet', 'flags_eliminatoires': flags_elim, 'sous_scores': {"Adequation experience (reporting/analyse/data)": 0, "Maitrise outils BI (Excel/Power BI)": 0, "Connaissance SQL": 0, "Exposition donnees bancaires/Core Banking": 0, "Coherence et progression": 0, "Qualite CV + Lettre": 0, "Competences avancees": 0}, 'checklist': {}, 'detail': f"REJET IMMEDIAT - {len(flags_elim)} critere(s) eliminatoire(s) non satisfait(s)", 'points_forts': [], 'points_vigilance': flags_elim, 'synthese': f"Rejet immediat : {', '.join(flags_elim[:3])}"}
    exp_score = 0
    reporting_keywords = ['reporting', 'rapport', 'tableau de bord', 'dashboard', 'budget', 'realise', 'realise', 'ecart', 'ecart', 'analyse', 'prevision', 'prevision', 'forecast', 'controle de gestion', 'controle de gestion', 'data analyst', 'analyste de donnees']
    found_exp = sum(1 for kw in reporting_keywords if kw in cv_text.lower())
    if found_exp >= 6:
        exp_score = 3
    elif found_exp >= 4:
        exp_score = 2
    elif found_exp >= 2:
        exp_score = 1
    bi_score = 0
    excel_advanced = ['excel avance', 'excel avance', 'power query', 'tcd', 'tableau croise', 'vba excel']
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
    sql_keywords = ['sql', 'base de donnees', 'base de donnees', 'extraction', 'requete', 'requete', 'data warehouse', 'etl', 'select', 'join']
    found_sql = sum(1 for kw in sql_keywords if kw in cv_text.lower())
    if found_sql >= 4:
        sql_score = 2
    elif found_sql >= 2:
        sql_score = 1
    bank_score = 0
    banking_keywords = ['banque', 'bancaire', 'core banking', 'flexcube', 't24', 'amplitude', 'financial institution', 'institution financiere', 'pnb', 'npl', 'cout du risque', 'cout du risque', 'rentabilite', 'rentabilite', 'agence', 'produit bancaire', 'credit', 'credit']
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
    has_quantified = bool(re.search(r'\d+\s*(%|pourcent|reduction|reduction|gain|amelioration|amelioration|efficacite|efficacite)', cv_text.lower()))
    has_tools = bool(re.search(r'(power bi|powerbi|sql|excel|python|r|tableau|etl|vba|dax)', cv_text.lower()))
    if has_quantified and has_tools:
        qualite_score = 1
    if lettre_text and len(lettre_text.strip()) > 80:
        lettre_kw = ['data', 'finance', 'analyste', 'reporting', 'dashboard', 'analyse', 'donnees', 'donnees']
        if any(kw in lettre_text.lower() for kw in lettre_kw):
            if 'power bi' in lettre_text.lower() or 'excel' in lettre_text.lower() or 'sql' in lettre_text.lower():
                qualite_score = 1
    avance_score = 0
    advanced_keywords = ['python', 'r', 'automatisation', 'modelisation', 'modelisation', 'prevision', 'prevision', 'scenario', 'scenario', 'reporting reglementaire', 'reporting reglementaire', 'machine learning']
    found_adv = sum(1 for kw in advanced_keywords if kw in cv_text.lower())
    if found_adv >= 2:
        avance_score = 1
    total_score = exp_score + bi_score + sql_score + bank_score + coher_score + qualite_score + avance_score
    total_score = min(14, total_score)
    if total_score >= 11:
        decision = "Entretien prioritaire"
    elif total_score >= 7:
        decision = "Potentiel a evaluer en entretien"
    else:
        decision = "Rejet"
    points_forts = []
    points_vigilance = []
    if exp_score >= 2:
        points_forts.append("Experience en reporting/analyse financiere")
    if bi_score >= 2:
        points_forts.append("Maitrise des outils BI (Excel/Power BI)")
    if sql_score >= 2:
        points_forts.append("Maitrise de SQL")
    if bank_score >= 2:
        points_forts.append("Exposition au secteur bancaire")
    if coher_score >= 2:
        points_forts.append("Parcours coherent avec progression")
    if avance_score >= 1:
        points_forts.append("Competences avancees (Python/R/automatisation)")
    if exp_score < 2:
        points_vigilance.append("Experience en analyse financiere limitee")
    if bi_score < 2:
        points_vigilance.append("Maitrise des outils BI a renforcer")
    if sql_score < 1:
        points_vigilance.append("Competences SQL a approfondir")
    if bank_score < 1:
        points_vigilance.append("Exposition au secteur bancaire limitee")
    sous_scores = {"Adequation experience (reporting/analyse/data)": exp_score, "Maitrise outils BI (Excel/Power BI)": bi_score, "Connaissance SQL": sql_score, "Exposition donnees bancaires/Core Banking": bank_score, "Coherence et progression": coher_score, "Qualite CV + Lettre": qualite_score, "Competences avancees": avance_score}
    synthese = f"Candidat avec un score de {total_score}/14. "
    if total_score >= 11:
        synthese += "Profil tres solide pour le poste de Data Analyst Finance. Excellente adequation avec les exigences du poste. A recommander pour entretien prioritaire."
    elif total_score >= 7:
        synthese += "Profil interessant avec des competences pertinentes. Certains domaines sont a approfondir mais le potentiel est present. A convoquer en entretien."
    else:
        synthese += "Profil insuffisant pour le poste. Manque de competences cles en analyse financiere et outils BI."
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
    "Experience bancaire": ["banque", "bancaire", "etablissement bancaire", "institution bancaire", "banque commerciale", "microfinance", "etablissement financier", "institution financiere", "secteur bancaire", "groupe bancaire", "filiale bancaire", "bank", "banking", "financial institution", "credit institution", "commercial bank", "ecobank", "orabank", "uba", "finadev", "ucec", "microfinance"],
    "Minimum 3 ans en credit / risque (hors stage)": ["EXP_CREDIT_3ANS"],
    "Minimum 1 an d'experience dans une fonction bancaire": ["EXP_BANK_1ANS"],
    "Minimum 3 ans en operations bancaires ou back-office (hors stage)": ["EXP_BACKOFFICE_3ANS"],
    "A une exposition au cycle de vie du credit bancaire": ["cycle de credit", "mise en place credit", "suivi credit", "garantie", "echeances credit", "credit administration", "administration de credit"],
    "A une connaissance des normes comptables bancaires ou de la reglementation COBAC": ["cobac", "reglementation bancaire", "ifrs 9", "normes ifrs", "comptabilite bancaire", "syscohada", "bale ii", "bale iii"]
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
            if ratio >= 75:
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}~{ratio/100:.2f}")
                    best_score = max(best_score, ratio / 100)
                continue
        if kw_tokens and text_tokens:
            common = set(kw_tokens) & set(text_tokens)
            if len(common) >= max(2, len(kw_tokens) * 0.5):
                if not contains_negative_context(raw_full_text, kw):
                    found_kws.append(f"{kw}[{len(common)}/{len(kw_tokens)}]")
                    best_score = max(best_score, len(common) / len(kw_tokens))
    threshold = 0.45 if len(normalized_text) < 500 else 0.55
    return best_score >= threshold, round(best_score, 2), found_kws
def has_experience_years_strict(full_raw_text, min_years, domain_keywords=None, poste=None):
    blocks = split_into_jobs(full_raw_text)
    total_years = 0.0
    years_patterns = [r'(\d+)\s*(?:annees?|ans?)', r'plus\s+de\s+(\d+)\s*(?:annees?|ans?)', r'\(\s*(\d+)\s*\)\s*(?:annees?|ans?)', r'\w+\s+\(\s*(\d+)\s*\)\s*(?:annees?|ans?)', r'depuis\s+(?:plus\s+de\s+)?(\d+)\s*(?:annees?|ans?)', r'(\d+)\s*(?:annees?|ans?)\s+(?:d[ée]?experience|dans|en|de)', r'experience\s+(?:de\s+)?(\d+)\s*(?:annees?|ans?)']
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
    banking_posts = ["Responsable Administration de Credit", "Analyste Credit CCB", "Senior Finance Officer", "Market Risk Officer", "Charge(e) d'Administration de Credit"]
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
    tool = {"name": "soumettre_analyse_candidature", "description": "Soumet l'analyse structuree d'une candidature.", "input_schema": {"type": "object", "properties": {"eliminatoire": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "valide": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "valide", "justification"]}}, "a_verifier": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "detecte": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "detecte", "justification"]}}, "signaux_forts": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "detecte": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "detecte", "justification"]}}, "points_attention": {"type": "array", "items": {"type": "object", "properties": {"critere": {"type": "string"}, "present": {"type": "boolean"}, "justification": {"type": "string"}}, "required": ["critere", "present", "justification"]}}, "lettre_motivation": {"type": "object", "properties": {"presente": {"type": "boolean"}, "coherente_avec_cv": {"type": "boolean"}, "generique_ou_copiee": {"type": "boolean"}, "qualite_redactionnelle": {"type": "string", "enum": ["bonne", "moyenne", "faible", "non_evaluable"]}, "eliminatoire": {"type": "boolean"}, "commentaire": {"type": "string"}}, "required": ["presente", "coherente_avec_cv", "generique_ou_copiee", "qualite_redactionnelle", "eliminatoire", "commentaire"]}, "diplomes": {"type": "object", "properties": {"niveau_suffisant": {"type": "boolean"}, "domaine_pertinent": {"type": "boolean"}, "atout_complementaire_detecte": {"type": "boolean"}, "commentaire": {"type": "string"}}, "required": ["niveau_suffisant", "domaine_pertinent", "atout_complementaire_detecte", "commentaire"]}, "sous_scores": {"type": "object", "additionalProperties": {"type": "integer"}}, "score_total": {"type": "integer"}, "decision": {"type": "string"}, "points_forts": {"type": "array", "items": {"type": "string"}}, "points_vigilance": {"type": "array", "items": {"type": "string"}}, "synthese_recruteur": {"type": "string"}}, "required": ["eliminatoire", "a_verifier", "signaux_forts", "points_attention", "lettre_motivation", "diplomes", "sous_scores", "score_total", "decision", "points_forts", "points_vigilance", "synthese_recruteur"]}}
    SYSTEM_PROMPT_RECRUTEUR = """Tu es un responsable recrutement senior avec 15 ans d'experience dans le secteur bancaire en Afrique centrale et de l'Ouest (CEMAC/UEMOA).
    REGLES ABSOLUES D'AUTHENTICITE :
    1. Tu ne JAMAIS inventer de faits qui ne sont PAS dans les documents fournis.
    2. Si une information n'est PAS explicitement mentionnee, tu consideres qu'elle N'EXISTE PAS.
    3. Tu ne fais AUCUNE supposition, AUCUNE interpretation excessive.
    4. Les stages, benefolats et formations NE COMPTENT PAS comme experience professionnelle.
    5. Tu justifies CHAQUE evaluation avec une citation courte du document concerne.
    6. Tu suis STRICTEMENT la grille fournie."""
    try:
        grille = GRILLE.get(poste, {})
        def fmt_list(items):
            return "\n".join(f"  {i+1}. {c}" for i, c in enumerate(items)) if items else "  (aucun)"
        user_msg = f"""POSTE : {poste}
    GRILLE :
    Eliminatoires :
    {fmt_list(grille.get('eliminatoire', []))}
    A verifier :
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
            response = _deepseek_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_RECRUTEUR},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0.1,
                max_tokens=4096,
                response_format={"type": "json_object"}
            )
        result_text = response.choices[0].message.content
        try:
            analyse = json.loads(result_text)
        except json.JSONDecodeError:
            import re as re_json
            json_match = re_json.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                analyse = json.loads(json_match.group())
            else:
                return None
        tool_use = {"input": analyse}
        if not tool_use:
            return None
        analyse = tool_use["input"]
        flags_elim = [e['critere'] for e in analyse.get('eliminatoire', []) if not e.get('valide')]
        lm = analyse.get('lettre_motivation', {})
        if lm.get('eliminatoire'):
            flags_elim.append(f"Lettre: {lm.get('commentaire', 'eliminatoire')}")
        score_total = 0 if flags_elim else int(analyse.get('score_total', 0))
        decision = get_recommandation_from_score(score_total, poste)
        score_max = get_score_max_for_poste(poste)
        return {'score': score_total, 'score_max': score_max, 'checklist': {}, 'flags_eliminatoires': flags_elim, 'signaux_detectes': [s['critere'] for s in analyse.get('signaux_forts', []) if s.get('detecte')], 'details': {'moteur': 'IA (DeepSeek)', 'points_forts': analyse.get('points_forts', []), 'points_vigilance': analyse.get('points_vigilance', []), 'synthese_recruteur': analyse.get('synthese_recruteur', '')}, 'score_breakdown': {'bloc1_eliminatoire': bool(flags_elim), 'moteur_analyse': 'ia', 'sous_scores': analyse.get('sous_scores', {}), 'score_final': score_total, 'score_max': score_max, 'decision': decision}}
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
        elif "Potentiel" in decision or "considerer" in decision or "Faible" in decision:
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
            if note and "Decision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if note and "Decision" not in note and len(note) > 5:
            return f"RETENU - {note}"
        return "RETENU - Candidature retenue"
    if statut == "entretien":
        lines = ["POTENTIEL A EVALUER :"]
        if strengths:
            for s in strengths[:2]:
                lines.append(f"  • {s}")
        if weaknesses:
            lines.append("Points a verifier :")
            for w in weaknesses[:2]:
                lines.append(f"  • {w}")
        if note and "Decision" not in note and len(note) > 5:
            lines.append(f"\nNOTE RECRUTEUR : {note}")
        return "\n".join(lines)
    if statut == "rejete":
        if flags:
            lines = ["CRITERES ELIMINATOIRES NON SATISFAITS :"]
            for flag in flags[:4]:
                clean = str(flag).replace('', '').replace('', '').strip()
                if clean and len(clean) > 3:
                    lines.append(f"  • {clean}")
            if len(flags) > 4:
                lines.append(f"  • +{len(flags)-4} autre(s)")
            if note and "Decision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if weaknesses:
            lines = ["POINTS DE VIGILANCE :"]
            for w in weaknesses[:4]:
                lines.append(f"  • {w}")
            if note and "Decision" not in note and len(note) > 5:
                lines.append(f"\nNOTE RECRUTEUR : {note}")
            return "\n".join(lines)
        if note and "Decision" not in note and len(note) > 5:
            return f"REJETE - {note}"
        if score == 0:
            return "REJETE - Analyse automatique : le candidat ne repond pas aux criteres eliminatoires du poste"
        if score < 7:
            return f"REJETE - Score insuffisant ({score}/{score_max}) - Profil ne correspond pas aux exigences du poste"
        return "REJETE - Profil ne correspond pas aux exigences du poste"
    else:
        if flags:
            lines = ["CRITERES ELIMINATOIRES :"]
            for flag in flags[:3]:
                clean = str(flag).replace('', '').replace('', '').strip()
                if clean and len(clean) > 5:
                    lines.append(f"  • {clean}")
            return "\n".join(lines)
        if "Entretien prioritaire" in decision_auto or "Shortlist" in decision_auto:
            lines = ["PROFIL RECOMMANDE :"]
            if strengths:
                for s in strengths[:4]:
                    lines.append(f"  • {s}")
            if sous_scores:
                for key, value in sous_scores.items():
                    if value > 0:
                        lines.append(f"  • {key}: {value}/3")
            return "\n".join(lines)
        elif "Potentiel" in decision_auto:
            lines = ["POTENTIEL A EVALUER :"]
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
                decision_final = "Rejete"
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
                elif "Potentiel" in decision_final or "considerer" in decision_final or "Faible" in decision_final:
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
                decision = "Rejete"
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
            elif decision_val == "Rejete":
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
                recommandation = "Rejete"
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
                logger.info(f"CV extrait pour {token}: {len(cv_text)} caracteres")
                if len(cv_text) < 50:
                    logger.warning(f"CV {cv_filename} tres court ({len(cv_text)} caracteres)")
        lm_text = ""
        if lettre_filename:
            lm_bytes = download_file_from_supabase_robust(lettre_filename)
            if lm_bytes:
                lm_text = extract_text_robust_from_bytes(lm_bytes, lettre_filename)
                if len(lm_text) > MAX_TEXT_SIZE:
                    lm_text = lm_text[:MAX_TEXT_SIZE]
                logger.info(f"Lettre extraite pour {token}: {len(lm_text)} caracteres")
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
        if not cv_text or len(cv_text.strip()) < 30:
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
        logger.info(f"Analyse pour {token} - poste: {poste}, CV: {len(cv_text)} caracteres")
        gc.collect()
        if IA_ANALYSE_ACTIVE and poste in GRILLE:
            logger.info(f"Utilisation de DeepSeek pour l'analyse du poste: {poste}")
            result = analyze_cv_with_deepseek_reasoning(cv_text, lm_text, att_texts, poste)
            if result:
                logger.info(f"Analyse DeepSeek reussie pour {token} - Score: {result.get('score', 0)}/{result.get('score_max', 0)}")
            else:
                logger.warning(f"DeepSeek a echoue, fallback vers scoring specifique pour {poste}")
                result = None
        else:
            logger.info(f"Fallback vers scoring specifique pour {poste}")
            result = None
        if result is None:
            if poste == "Charge(e) d'Administration de Credit":
                result = calculate_score_charge_admin_credit(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/12 - {result.get('decision', 'Inconnu')}")
            elif poste == "Chef de Section Compensation":
                result = calculate_score_chef_section_compensation(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/12 - {result.get('decision', 'Inconnu')}")
            elif poste == "Chef de Division Local Corporate":
                logger.info(f"Appel du scoring specifique pour {poste}")
                result = calculate_score_chef_division_corporate(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/14 - {result.get('decision', 'Inconnu')}")
            elif poste == "Data Analyst Finance":
                result = calculate_score_data_analyst_finance(cv_text, lm_text, att_texts)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/14 - {result.get('decision', 'Inconnu')}")
            else:
                result = analyze_cv_intelligent(cv_text, lm_text, att_texts, poste)
                if result is None:
                    logger.info(f"Fallback vers analyse_grille pour {poste}")
                    result = analyze_cv_against_grille(cv_text, lm_text, att_texts, poste)
                logger.info(f"Score calcule pour {poste}: {result.get('score', 0)}/10 - {result.get('decision', 'Inconnu')}")
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
        details['moteur'] = 'deepseek_reasoning' if IA_ANALYSE_ACTIVE else 'scoring_specifique_v2'
        score_breakdown = {'score_final': score, 'score_max': score_max, 'decision': decision, 'moteur_analyse': details['moteur'], 'sous_scores': result.get('sous_scores', {})}
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
            analyse_msg = 'Analyse automatique en cours avec DeepSeek'
        else:
            analyse_msg = 'Poste cloture — candidature enregistree sans analyse'
            supabase.table('candidats').update({"analyse_status": "closed_post_no_analysis", "analyse_auto_date": datetime.datetime.now().isoformat()}).eq('token', token).execute()
        nom_complet = f"{prenom} {nom}".strip()
        sujet_confirmation = f"Confirmation de candidature – {poste}"
        corps_confirmation = f"Bonjour {nom_complet},\nNous accusons reception de votre candidature.\nSans reponse de notre part sous deux (2) semaines, veuillez considerer que votre candidature n'a pas ete retenue.\nPour toute information : contact@cdotchad.com.\nCordialement,"
        threading.Thread(target=send_email, args=(email, sujet_confirmation, corps_confirmation), daemon=True).start()
        return jsonify({'message': 'Candidature soumise avec succes', 'token': token, 'numero_dossier': numero_dossier, 'analyse': analyse_msg, 'poste_statut': 'actif' if is_poste_actif(poste) else 'cloture', 'ia_engine': 'DeepSeek Reasoning' if IA_ANALYSE_ACTIVE else 'Fallback'}), 201
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
        update_data["decision"] = "Rejet - Decision du recruteur"
    elif statut == "retenu":
        update_data["decision"] = "Retenu - Decision du recruteur"
    elif statut == "entretien":
        update_data["decision"] = "Entretien - Decision du recruteur"
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
    return jsonify({'message': 'Analyse re-declenchee avec DeepSeek', 'token': token, 'ia_engine': 'DeepSeek Reasoning'}), 202
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
        MAX_WORKERS = min(8, len(candidates_to_reanalyze))
        logger.info(f"Reanalyse parallele : {len(candidates_to_reanalyze)} candidats, {MAX_WORKERS} workers")
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(analyze_one, c): c for c in candidates_to_reanalyze}
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=300)
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
        MAX_WORKERS = min(8, len(candidates_with_cv))
        start_time = time.time()
        reanalyzed_count = 0
        errors = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(analyze_one, c) for c in candidates_with_cv]
            for future in as_completed(futures):
                try:
                    token, success, msg = future.result(timeout=300)
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
                if poste == "Charge(e) d'Administration de Credit":
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
        MAX_WORKERS = min(8, len(candidates))
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(analyze_fast_only, c) for c in candidates]
            for future in as_completed(futures):
                try:
                    token, ok, msg = future.result(timeout=120)
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
def _run_zip_export_job(job_id, poste_filter, date_start, date_end):
    tmp_zip_path = None
    start_time = time.time()
    try:
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'processing'
        logger.info(f"[job {job_id}] Debut export ZIP optimise - {datetime.datetime.now()}")
        if not supabase:
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Supabase non configure'
            return
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
            with _ZIP_JOBS_LOCK:
                _ZIP_JOBS[job_id]['status'] = 'error'
                _ZIP_JOBS[job_id]['error'] = 'Aucun dossier a exporter'
            return
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
            candidats_meta[cand['id']] = {'dossier_parent': dossier_parent, 'num_dossier': num_dossier, 'cand': cand, 'files_written': 0}
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
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['total'] = len(download_tasks)
        logger.info(f"[job {job_id}] {len(download_tasks)} fichiers a telecharger")
        def _download_one(task):
            cand_id, blob_name, dossier_parent, prefix = task
            file_bytes = download_file_from_supabase_robust(blob_name)
            return (cand_id, blob_name, dossier_parent, prefix, file_bytes)
        tmp_fd = tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir='/tmp')
        tmp_zip_path = tmp_fd.name
        tmp_fd.close()
        files_added = 0
        max_workers = min(_ZIP_MAX_WORKERS, max(1, len(download_tasks)))
        logger.info(f"[job {job_id}] Utilisation de {max_workers} workers pour le telechargement")
        with zipfile.ZipFile(tmp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if download_tasks:
                BATCH_SIZE = 50
                total_batches = (len(download_tasks) + BATCH_SIZE - 1) // BATCH_SIZE
                for batch_idx in range(0, len(download_tasks), BATCH_SIZE):
                    batch = download_tasks[batch_idx:batch_idx + BATCH_SIZE]
                    logger.info(f"[job {job_id}] Traitement du lot {batch_idx//BATCH_SIZE + 1}/{total_batches} ({len(batch)} fichiers)")
                    with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as executor:
                        futures = [executor.submit(_download_one, t) for t in batch]
                        for future in as_completed(futures):
                            try:
                                cand_id, blob_name, dossier_parent, prefix, file_bytes = future.result(timeout=60)
                            except Exception as e:
                                logger.error(f"[job {job_id}] Erreur telechargement: {e}")
                                continue
                            if file_bytes:
                                ext = blob_name.rsplit('.', 1)[-1].lower() if '.' in blob_name else ''
                                archive_name = f"{dossier_parent}/{prefix}.{ext}" if ext else f"{dossier_parent}/{prefix}"
                                try:
                                    zip_file.writestr(archive_name, file_bytes)
                                    files_added += 1
                                    if cand_id in candidats_meta:
                                        candidats_meta[cand_id]['files_written'] += 1
                                except Exception as e:
                                    logger.error(f"[job {job_id}] Erreur ecriture ZIP: {e}")
                                finally:
                                    del file_bytes
                            with _ZIP_JOBS_LOCK:
                                _ZIP_JOBS[job_id]['progress'] = _ZIP_JOBS[job_id].get('progress', 0) + 1
                    gc.collect()
            for cand_id, meta in candidats_meta.items():
                if meta['files_written'] > 0:
                    continue
                cand = meta['cand']
                info_content = f"Candidat: {cand.get('nom', 'N/A')} {cand.get('prenom', 'N/A')}\nPoste: {cand.get('poste', 'N/A')}\nNumero dossier: {meta['num_dossier']}\nEmail: {cand.get('email', 'N/A')}\nTelephone: {cand.get('telephone', 'N/A')}\nDate candidature: {cand.get('date_candidature', 'N/A')}"
                archive_name = f"{meta['dossier_parent']}/INFOS_CANDIDAT.txt"
                zip_file.writestr(archive_name, info_content.encode('utf-8'))
                files_added += 1
        elapsed = time.time() - start_time
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        poste_suffix = f"_{poste_filter.replace(' ', '_')}" if poste_filter else ""
        filename = f"dossiers_candidats{poste_suffix}_{ts}.zip"
        logger.info(f"[job {job_id}] Export ZIP termine en {elapsed:.2f}s pour {len(candidats)} candidats ({files_added} fichiers)")
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'done'
            _ZIP_JOBS[job_id]['filepath'] = tmp_zip_path
            _ZIP_JOBS[job_id]['filename'] = filename
        del candidats_meta, download_tasks
        gc.collect()
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"[job {job_id}] Erreur export ZIP: {e}")
        if tmp_zip_path and os.path.exists(tmp_zip_path):
            try:
                os.remove(tmp_zip_path)
            except Exception:
                pass
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS[job_id]['status'] = 'error'
            _ZIP_JOBS[job_id]['error'] = str(e)
@app.route('/api/recruteur/dossiers/zip/start', methods=['GET'])
@jwt_required()
def start_zip_export():
    _cleanup_old_zip_jobs()
    poste_filter = request.args.get('poste', '')
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    job_id = uuid.uuid4().hex
    with _ZIP_JOBS_LOCK:
        _ZIP_JOBS[job_id] = {'status': 'pending', 'created_at': time.time(), 'progress': 0, 'total': 0, 'filepath': None, 'filename': None, 'error': None}
    threading.Thread(target=_run_zip_export_job, args=(job_id, poste_filter, date_start, date_end), daemon=True).start()
    return jsonify({'job_id': job_id}), 202
@app.route('/api/recruteur/dossiers/zip/status/<job_id>', methods=['GET'])
@jwt_required()
def zip_export_status(job_id):
    with _ZIP_JOBS_LOCK:
        job = _ZIP_JOBS.get(job_id)
        if not job:
            return jsonify({'error': 'Job introuvable ou expire'}), 404
        return jsonify({'status': job['status'], 'progress': job.get('progress', 0), 'total': job.get('total', 0), 'error': job.get('error')}), 200
@app.route('/api/recruteur/dossiers/zip/download/<job_id>', methods=['GET'])
@jwt_required()
def zip_export_download(job_id):
    with _ZIP_JOBS_LOCK:
        job = _ZIP_JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job introuvable ou expire'}), 404
    if job['status'] == 'error':
        return jsonify({'error': job.get('error', 'Erreur inconnue')}), 500
    if job['status'] != 'done':
        return jsonify({'error': 'Export pas encore termine', 'status': job['status']}), 425
    filepath = job.get('filepath')
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'Fichier expire ou deja supprime'}), 410
    response_obj = send_file(filepath, mimetype='application/zip', as_attachment=True, download_name=job['filename'])
    @response_obj.call_on_close
    def _cleanup_after_send():
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            logger.warning(f"Nettoyage fichier temporaire ZIP (job {job_id}) echoue: {e}")
        with _ZIP_JOBS_LOCK:
            _ZIP_JOBS.pop(job_id, None)
    return response_obj
def _cleanup_old_zip_jobs():
    now = time.time()
    with _ZIP_JOBS_LOCK:
        stale = [jid for jid, j in _ZIP_JOBS.items() if now - j.get('created_at', now) > _ZIP_JOBS_MAX_AGE_SECONDS]
        for jid in stale:
            job = _ZIP_JOBS.pop(jid, None)
            if job and job.get('filepath') and os.path.exists(job['filepath']):
                try:
                    os.remove(job['filepath'])
                except Exception:
                    pass
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
        ok = send_email(to, 'Test RecrutBank', 'Ceci est un email de test depuis RecrutBank avec DeepSeek.')
        return jsonify({'sent': ok}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/api/health-version', methods=['GET'])
def health_version():
    return jsonify({
        "version": "v7.0-deepseek-reasoning",
        "postes_actifs": POSTES_ACTIFS,
        "postes_count": len(POSTES),
        "scoring_seuils": "12: 10/7, 14: 11/7, 100: 80/70/60, 10: 8/5",
        "scoring_strict": True,
        "manual_status_priority": True,
        "auto_width_excel": True,
        "max_concurrent_downloads": DOWNLOAD_MAX_CONCURRENT,
        "zip_max_workers": _ZIP_MAX_WORKERS,
        "intelligent_scoring": True,
        "advanced_reasoning": True,
        "ia_provider": "DeepSeek" if IA_ANALYSE_ACTIVE else "Fallback",
        "ia_model": DEEPSEEK_MODEL if IA_ANALYSE_ACTIVE else "N/A",
        "deployed_at": datetime.datetime.now().isoformat()
    }), 200
if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    import multiprocessing
    cpu_count = multiprocessing.cpu_count()
    suggested_workers = min(4, cpu_count * 2)
    logger.info("=" * 60)
    logger.info(" RecrutBank API v7.0 - DeepSeek Reasoning Engine")
    logger.info("=" * 60)
    logger.info(f"Port: {port}")
    logger.info(f"Workers suggeres: {suggested_workers}")
    logger.info(f"Threads par worker: 4")
    logger.info(f"IA Provider: {'DeepSeek ' if IA_ANALYSE_ACTIVE else ' Aucune'}")
    if IA_ANALYSE_ACTIVE:
        logger.info(f"Modele DeepSeek: {DEEPSEEK_MODEL}")
        logger.info(f"Concurrence IA max: {os.getenv('IA_MAX_CONCURRENCY', '5')}")
    logger.info(f"Mode raisonnement avance: {'ACTIF ' if IA_ANALYSE_ACTIVE else 'INACTIF '}")
    logger.info(f"Telechargements concurrents: {DOWNLOAD_MAX_CONCURRENT}")
    logger.info(f"Workers ZIP max: {_ZIP_MAX_WORKERS}")
    logger.info("=" * 60)
    try:
        import gunicorn
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

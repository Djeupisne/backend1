# ═══════════════════════════════════════════════════════
# Configuration centrale : logging, dependances, variables denvironnement, clients externes
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, hashlib, datetime, uuid, json, re, threading, io, csv, unicodedata, zipfile, time, gc, random, tempfile
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
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm, inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib import fonts
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
try:
    import openpyxl
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, GradientFill, colors as xl_colors
    from openpyxl.utils import get_column_letter
    from openpyxl.chart import BarChart, Reference, PieChart
    from openpyxl.chart.label import DataLabelList
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    logger.info("✅ OpenAI importe avec succes")
except ImportError as e:
    OPENAI_AVAILABLE = False
    logger.error(f"❌ Erreur import OpenAI: {e}")

# --- Configuration IA (variables denvironnement) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_REASONING_ENABLED = os.getenv("OPENROUTER_REASONING_ENABLED", "false").lower() == "true"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
logger.info(f"🔑 OPENROUTER_API_KEY: {'✅ Presente' if OPENROUTER_API_KEY else '❌ Manquante'}")
logger.info(f"🧠 OPENROUTER_REASONING: {'✅ Active' if OPENROUTER_REASONING_ENABLED else '❌ Desactive'}")
logger.info(f"🔑 DEEPSEEK_API_KEY: {'✅ Presente' if DEEPSEEK_API_KEY else '❌ Manquante'}")
_client = None
_PROVIDER = "None"
_MODEL = None
IA_ANALYSE_ACTIVE = False
ACTIVE_MODELS = []
OPENROUTER_MODELS = [
    {"name": "Nemotron 3 Ultra", "model": os.getenv("IA_MODEL_1", "nvidia/nemotron-3-ultra-550b-a55b:free"), "priority": 1, "supports_reasoning": True},
    {"name": "MiniMax M3", "model": os.getenv("IA_MODEL_2", "minimax/minimax-m3:free"), "priority": 2, "supports_reasoning": False},
    {"name": "MiniMax M2.7", "model": os.getenv("IA_MODEL_3", "minimax/minimax-m2.7:free"), "priority": 3, "supports_reasoning": False}
]
IA_FALLBACK_ENABLED = os.getenv("IA_FALLBACK_ENABLED", "true").lower() == "true"
IA_MAX_RETRIES = int(os.getenv("IA_MAX_RETRIES", "3"))
IA_MODEL_TIMEOUT = int(os.getenv("IA_MODEL_TIMEOUT", "120"))

# --- Limites de telechargement / concurrence / jobs ZIP ---
_ia_semaphore = threading.Semaphore(int(os.getenv("IA_MAX_CONCURRENCY", "5")))
DOWNLOAD_MAX_RETRIES = int(os.getenv("DOWNLOAD_MAX_RETRIES", "3"))
DOWNLOAD_BASE_DELAY = float(os.getenv("DOWNLOAD_BASE_DELAY", "0.5"))
DOWNLOAD_MAX_DELAY = int(os.getenv("DOWNLOAD_MAX_DELAY", "10"))
DOWNLOAD_MAX_CONCURRENT = int(os.getenv("DOWNLOAD_MAX_CONCURRENT", "15"))
_DOWNLOAD_SEMAPHORE = threading.Semaphore(DOWNLOAD_MAX_CONCURRENT)
_ZIP_JOBS = {}
_ZIP_JOBS_LOCK = threading.Lock()
_ZIP_JOBS_MAX_AGE_SECONDS = 3600
_ZIP_MAX_WORKERS = int(os.getenv("ZIP_MAX_WORKERS", "25"))

# --- Application Flask + CORS ---
app = Flask(__name__)
ALLOWED_ORIGINS = [
    "https://recrutment.onrender.com",
    "https://backend1-fiq5.onrender.com",
    "http://localhost:5000",
    "http://localhost:3000"
]
CORS(app, resources={
    r"/api/*": {
        "origins": ALLOWED_ORIGINS,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
        "supports_credentials": True,
        "max_age": 600
    }
})

# --- JWT ---
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "gestion-candidatures-secret-2024")
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(hours=8)
jwt = JWTManager(app)

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "candidatures")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# --- SMTP / emails ---
app.config['SMTP_HOST'] = os.getenv('SMTP_HOST', 'smtp.gmail.com')
app.config['SMTP_PORT'] = int(os.getenv('SMTP_PORT', 587))
app.config['SMTP_USER'] = os.getenv('SMTP_USER', '')
app.config['SMTP_PASSWORD'] = os.getenv('SMTP_PASSWORD', '')
app.config['SMTP_FROM'] = os.getenv('SMTP_FROM', 'RecrutBank RH <oualoumidjeupisne@gmail.com>')
app.config['SMTP_USE_TLS'] = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}

# --- Limites extraction de documents ---
MAX_PDF_PAGES = 10
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024
MAX_TEXT_SIZE = 15000


# --- Donnees de reference (importees ici pour preserve le comportement du module monolithique :
#     toutes les fonctions/routes trouvent POSTES / POSTES_ACTIFS / POSTES_CLOTURES / GRILLE dans ce namespace) ---
from .data.postes import POSTES, POSTES_ACTIFS, POSTES_CLOTURES, is_poste_actif  # noqa: E402,F401
from .data.grille import GRILLE  # noqa: E402,F401

# --- Rétrocompatibilité : ré-export des utilitaires/services (importés ailleurs via config) ---
from .utils.helpers import (hash_pwd, normalize_spaces, normalize_unicode, retry_with_backoff,
                            allowed_file)  # noqa: E402,F401
from .utils.storage import upload_file_to_supabase, download_file_from_supabase, \
    download_file_from_supabase_robust, get_signed_url  # noqa: E402,F401
from .services.email_service import send_email  # noqa: E402,F401
from .services.text_extraction import extract_text_robust_from_bytes  # noqa: E402,F401
from .services.ia_analysis import (sort_candidats, analyze_cv_with_ia_only,  # noqa: E402,F401
                                   run_analysis_for_candidat)
from .services.report_service import (generate_excel_report_enhanced, generate_pdf_report_enhanced,
                                      generate_csv_report_enhanced, generate_word_report_enhanced)  # noqa: E402,F401

# --- Initialisation des modeles IA (appelee au demarrage, comme avant) ---
def initialize_ia_clients():
    global _client, _PROVIDER, _MODEL, IA_ANALYSE_ACTIVE, ACTIVE_MODELS
    ACTIVE_MODELS = []
    for model_config in OPENROUTER_MODELS:
        if OPENROUTER_API_KEY:
            try:
                client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
                test_response = client.chat.completions.create(
                    model=model_config["model"],
                    messages=[{"role": "user", "content": "Test"}],
                    max_tokens=5,
                    temperature=0
                )
                if test_response and test_response.choices:
                    ACTIVE_MODELS.append({
                        "client": client,
                        "model": model_config["model"],
                        "name": f"OpenRouter - {model_config['name']}",
                        "provider": "OpenRouter",
                        "base_url": OPENROUTER_BASE_URL,
                        "priority": model_config.get("priority", 10),
                        "supports_reasoning": model_config.get("supports_reasoning", False)
                    })
                    logger.info(f"✅ {model_config['name']} ({model_config['model']}) initialise avec succes")
            except Exception as e:
                logger.warning(f"⚠️ {model_config['name']} indisponible: {e}")
    if DEEPSEEK_API_KEY:
        try:
            client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
            test_response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=5,
                temperature=0
            )
            if test_response and test_response.choices:
                ACTIVE_MODELS.append({
                    "client": client,
                    "model": DEEPSEEK_MODEL,
                    "name": "DeepSeek",
                    "provider": "DeepSeek",
                    "base_url": "https://api.deepseek.com",
                    "priority": 99,
                    "supports_reasoning": False
                })
                logger.info(f"✅ DeepSeek initialise avec succes")
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek indisponible: {e}")
    ACTIVE_MODELS.sort(key=lambda x: x.get("priority", 10))
    if ACTIVE_MODELS:
        _client = ACTIVE_MODELS[0]["client"]
        _MODEL = ACTIVE_MODELS[0]["model"]
        _PROVIDER = ACTIVE_MODELS[0]["name"]
        IA_ANALYSE_ACTIVE = True
        logger.info(f"✅ {len(ACTIVE_MODELS)} modele(s) IA actif(s):")
        for m in ACTIVE_MODELS:
            supports = "🧠" if m.get("supports_reasoning", False) and OPENROUTER_REASONING_ENABLED else "📝"
            logger.info(f"   {supports} {m['name']} ({m['model']})")
    else:
        IA_ANALYSE_ACTIVE = False
        logger.warning("⚠️ AUCUN MODELE IA DISPONIBLE")
initialize_ia_clients()

# --- Initialisation du compte recruteur par defaut ---
def init_recruteur():
    try:
        if supabase:
            response = supabase.table('recruteurs').select('*').eq('email', 'sougnabeoualoumibank@gmail.com').execute()
            if not response.data:
                supabase.table('recruteurs').insert({
                    "email": "sougnabeoualoumibank@gmail.com",
                    "password": hash_pwd("AdminLaurent123"),
                    "nom": "Responsable RH"
                }).execute()
    except Exception as e:
        logger.warning(f"Erreur initialisation recruteur : {e}")
init_recruteur()

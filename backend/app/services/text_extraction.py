# ═══════════════════════════════════════════════════════
# Extraction robuste de texte depuis PDF / DOCX / TXT
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
from ..config import (logger, pdfplumber, PyPDF2, Document, chardet,
                     PDFPLUMBER_AVAILABLE, PYPDF2_AVAILABLE, DOCX_AVAILABLE,
                     CHARDET_AVAILABLE, MAX_PDF_PAGES, MAX_PDF_SIZE_BYTES, MAX_TEXT_SIZE)
import io, re, unicodedata

def extract_text_from_pdf_robust(file_bytes, filename):
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        logger.warning(f"PDF trop volumineux: {filename}")
        return ""
    text = ""
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                total_pages = min(len(pdf.pages), MAX_PDF_PAGES)
                for i in range(total_pages):
                    try:
                        page = pdf.pages[i]
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
    return text.strip() if text.strip() else ""
def extract_text_from_docx_robust(file_bytes):
    if not DOCX_AVAILABLE:
        return ""
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
        if len(result) > 50:
            return normalize_unicode(result)
    except Exception as e:
        logger.warning(f"DOCX extraction avec python-docx echouee: {e}")
    try:
        import struct
        text = ""
        for i in range(0, len(file_bytes), 2):
            if i + 1 < len(file_bytes):
                try:
                    char = struct.unpack('<H', file_bytes[i:i+2])[0]
                    if 32 <= char <= 126 or char in [10, 13]:
                        text += chr(char)
                    else:
                        text += ' '
                except:
                    text += ' '
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 50:
            return normalize_unicode(text[:MAX_TEXT_SIZE])
    except:
        pass
    try:
        for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
            try:
                text = file_bytes.decode(encoding, errors='ignore')
                text = re.sub(r'[^\w\s\.\,\:\;\-\?\!\@\#\%\&\*\(\)\-\+\=\/\'\"]', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 50:
                    return normalize_unicode(text[:MAX_TEXT_SIZE])
            except:
                continue
    except Exception as e:
        logger.warning(f"Extraction brute echouee: {e}")
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
        logger.info(f"Extraction DOC/DOCX {filename}: {len(text)} caracteres")
    elif ext == 'txt':
        text = extract_text_from_txt(file_bytes)
        logger.info(f"Extraction TXT {filename}: {len(text)} caracteres")
    return text.strip() if text.strip() else ""

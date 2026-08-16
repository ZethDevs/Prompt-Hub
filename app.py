"""
PromptHub — Flask Backend (Cloud Edition)
=========================================
Perubahan utama dari versi lokal:
  1. Konfigurasi via .env (python-dotenv).
  2. Database: MongoDB Atlas (bukan database.txt lagi).
  3. Media upload: Cloudinary (bukan folder asset/ lokal lagi).
  4. AI Tools (Gemini): dipanggil dari SERVER — API key tidak pernah
     terekspos ke browser. Frontend cukup POST ke /api/ai/*.
  5. WA_NUMBER di-inject ke template via Jinja2 dari .env.

Kompatibilitas: jika database.txt lama masih ada saat pertama kali server
start dan MongoDB masih kosong, data lama otomatis diimigrasi ke MongoDB.
"""

import os
import io
import json
import time
import logging
from datetime import datetime

import requests
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load .env SEBELUM import package yang membaca env (cloudinary, dsb.)
load_dotenv()

# ================= KONFIGURASI =================
PORT = int(os.environ.get('PORT', 8000))
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-please')
ADMIN_LOGIN_KEY = os.environ.get('ADMIN_LOGIN_KEY', 'change-me-please')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
MONGO_URI = os.environ.get('MONGO_URI', '')
CLOUDINARY_URL = os.environ.get('CLOUDINARY_URL', '')
WA_NUMBER = os.environ.get('WA_NUMBER', '6281234567890')

# Gemini endpoint — model configurable via .env
# Default 'gemini-2.0-flash' (stabil & tersedia untuk semua user).
# Jika butuh model lain, set GEMINI_MODEL di .env (mis. 'gemini-1.5-flash', 'gemini-flash-latest').
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.6-flash')
GEMINI_URL = (
    f'https://generativelanguage.googleapis.com/v1beta/models/'
    f'{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}'
)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}
MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20 MB batas untuk inline_data Gemini

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = 'asset'  # dipertahankan hanya untuk fallback lokal

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('prompthub')

# ================= MONGODB =================
_mongo_client = None
_mongo_db = None


def get_db():
    """Return handle database MongoDB (lazy connect, tahan reconnect)."""
    global _mongo_client, _mongo_db
    if _mongo_db is not None:
        return _mongo_db
    if not MONGO_URI:
        log.warning('MONGO_URI kosong — database nonaktif (mode tanpa DB).')
        return None
    try:
        from pymongo import MongoClient
        # serverSelectionTimeoutMS singkat supaya startup tidak menggantung lama
        _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
        _mongo_client.admin.command('ping')  # trigger koneksi awal
        # Ambil nama db dari URI; fallback ke 'prompthub'
        # NOTE: get_default_database() returns Database or None — compare with `is None`
        default_db = _mongo_client.get_default_database()
        db_name = default_db.name if default_db is not None else 'prompthub'
        _mongo_db = _mongo_client[db_name]
        log.info('MongoDB terhubung: db=%s', db_name)
        return _mongo_db
    except Exception as e:
        log.error('Gagal terhubung MongoDB: %s', e)
        _mongo_db = None
        return None


DB_DOC_ID = 'main'  # single-document app state (kompatibel dengan struktur lama)
DB_COLL = 'app_state'


def default_state():
    """Skema default state aplikasi."""
    return {
        'generatedCount': 0,
        'gallery': [],
        'announcements': [],
        'admin': {
            'name': 'Nathan',
            'avatar': 'https://ui-avatars.com/api/?name=Nathan&background=ffd43b&color=1b1b1f',
        },
    }


def read_db():
    """Baca state dari MongoDB. Jika kosong & database.txt ada, migrasi otomatis."""
    db = get_db()
    if db is None:
        # Fallback in-memory (mode darurat tanpa DB)
        if not hasattr(read_db, '_mem'):
            read_db._mem = _try_migrate_from_file() or default_state()
        return read_db._mem
    try:
        doc = db[DB_COLL].find_one({'_id': DB_DOC_ID})
        if doc:
            doc.pop('_id', None)
            return _normalize_state(doc)
        # Belum ada data — coba migrasi dari file lama
        migrated = _try_migrate_from_file()
        if migrated:
            db[DB_COLL].update_one({'_id': DB_DOC_ID}, {'$set': migrated}, upsert=True)
            log.info('Migrasi database.txt → MongoDB selesai.')
            return migrated
        # Seed default
        seed = default_state()
        db[DB_COLL].update_one({'_id': DB_DOC_ID}, {'$set': seed}, upsert=True)
        return seed
    except Exception as e:
        log.error('read_db gagal: %s', e)
        return default_state()


def write_db(data):
    """Simpan state ke MongoDB (replace satu dokumen)."""
    db = get_db()
    if db is None:
        read_db._mem = _normalize_state(data) if hasattr(read_db, '_mem') else _normalize_state(data)
        return
    try:
        normalized = _normalize_state(data)
        db[DB_COLL].update_one({'_id': DB_DOC_ID}, {'$set': normalized}, upsert=True)
    except Exception as e:
        log.error('write_db gagal: %s', e)


def _normalize_state(data):
    """Pastikan field wajib ada (kompatibilitas mundur)."""
    base = default_state()
    if not isinstance(data, dict):
        return base
    base.update(data)
    if not isinstance(base.get('gallery'), list):
        base['gallery'] = []
    if not isinstance(base.get('announcements'), list):
        base['announcements'] = []
    if not isinstance(base.get('admin'), dict):
        base['admin'] = default_state()['admin']
    if not isinstance(base.get('generatedCount'), int):
        base['generatedCount'] = int(base.get('generatedCount') or 0)
    return base


def _try_migrate_from_file():
    """Jika database.txt ada, baca sebagai data lama (untuk migrasi)."""
    path = os.path.join(os.path.dirname(__file__), 'database.txt')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return _normalize_state(json.load(f))
    except Exception as e:
        log.warning('Gagal membaca database.txt lama: %s', e)
        return None


# ================= CLOUDINARY =================
_cloud_ready = False


def init_cloudinary():
    """Inisialisasi Cloudinary dari CLOUDINARY_URL."""
    global _cloud_ready
    if _cloud_ready:
        return True
    if not CLOUDINARY_URL:
        log.warning('CLOUDINARY_URL kosong — upload media nonaktif.')
        return False
    try:
        import cloudinary
        import cloudinary.uploader
        import cloudinary.api
        cloudinary.config(cloudinary_url=CLOUDINARY_URL)
        _cloud_ready = True
        log.info('Cloudinary terkonfigurasi.')
        return True
    except Exception as e:
        log.error('Gagal konfigurasi Cloudinary: %s', e)
        return False


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_to_cloudinary(file_storage, resource_type='auto'):
    """
    Upload file ke Cloudinary, return secure_url.
    resource_type: 'image' | 'video' | 'auto'
    """
    if not init_cloudinary():
        raise RuntimeError('Cloudinary tidak terkonfigurasi.')
    import cloudinary.uploader
    # stream upload — tidak perlu simpan ke disk
    upload_result = cloudinary.uploader.upload(
        file_storage.stream,
        resource_type=resource_type,
        folder='prompthub',
        use_filename=False,
        unique_filename=True,
        overwrite=False,
    )
    return upload_result.get('secure_url')


# ================= GEMINI HELPER =================
def call_gemini(system_text, user_text, image_b64=None, image_mime=None, temperature=0.7):
    """
    Panggil Gemini generateContent dari server.
    - system_text: instruction sistem
    - user_text: prompt user
    - image_b64: opsional, base64 tanpa prefix data URI
    - image_mime: opsional, e.g. 'image/png'
    Return: string text hasil.
    Raises: RuntimeError dengan pesan yang user-friendly.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError('GEMINI_API_KEY belum diset di server (.env).')

    parts = [{'text': user_text}]
    if image_b64 and image_mime:
        parts.append({'inline_data': {'mime_type': image_mime, 'data': image_b64}})

    payload = {
        'systemInstruction': {'parts': [{'text': system_text}]},
        'contents': [{'parts': parts}],
        'generationConfig': {'temperature': temperature},
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            headers={'Content-Type': 'application/json'},
            json=payload,
            timeout=90,
        )
    except requests.exceptions.Timeout:
        raise RuntimeError('Gemini tidak merespons (timeout). Coba lagi.')
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f'Gagal terhubung ke Gemini: {e}')

    if resp.status_code != 200:
        # Coba ekstrak pesan error dari Google
        try:
            err = resp.json().get('error', {}).get('message', resp.text)
        except ValueError:
            err = resp.text
        raise RuntimeError(f'Gemini error ({resp.status_code}): {err}')

    data = resp.json()
    candidates = data.get('candidates') or []
    if not candidates or not candidates[0].get('content'):
        # Mungkin terkena safety filter
        finish = candidates[0].get('finishReason') if candidates else None
        raise RuntimeError(
            f'Respons Gemini kosong atau diblokir safety filter'
            + (f' (finishReason={finish}).' if finish else '.')
        )
    text = candidates[0]['content'].get('parts', [{}])[0].get('text', '')
    return text


def clean_model_output(raw):
    """Bersihkan output model: hapus <think>, markdown code fences, asterisks."""
    import re
    s = String(raw)
    s = re.sub(r'<think>[\s\S]*?</think>', '', s)
    s = re.sub(r'<think>[\s\S]*$', '', s, flags=re.IGNORECASE)
    s = s.replace('**', '')
    s = s.replace('*', '')
    s = re.sub(r'```[a-z]*\n?', '', s)
    s = s.replace('```', '')
    return s.strip()


# Wrapper kecil agar clean_model_output aman menerima None
def String(v):
    return '' if v is None else str(v)


# ================= ROUTES — PAGES =================
@app.route('/')
def index():
    # WA_NUMBER di-inject ke frontend via Jinja2
    return render_template('index.html', wa_number=WA_NUMBER)


# ================= ROUTES — DATABASE =================
@app.route('/api/database', methods=['GET'])
def get_database():
    return jsonify(read_db())


@app.route('/api/database', methods=['POST'])
def save_database():
    data = request.get_json(silent=True) or {}
    write_db(data)
    return jsonify({'success': True})


@app.route('/api/admin_login', methods=['POST'])
def admin_login():
    data = request.get_json(silent=True) or {}
    key = (data.get('key') or '').strip()
    if key and key == ADMIN_LOGIN_KEY:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Secret key salah.'}), 401


# ================= ROUTES — MEDIA UPLOAD (CLOUDINARY) =================
@app.route('/upload_media', methods=['POST'])
def upload_media():
    if 'media_file' not in request.files:
        return jsonify({'success': False, 'message': 'Tidak ada file yang dikirim.'}), 400
    file = request.files['media_file']
    if not file or file.filename == '':
        return jsonify({'success': False, 'message': 'Nama file kosong.'}), 400
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': 'Format file tidak didukung.'}), 400

    # Tentukan resource_type untuk Cloudinary
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext in {'mp4', 'webm', 'mov'}:
        resource_type = 'video'
    else:
        resource_type = 'image'

    try:
        url = upload_to_cloudinary(file, resource_type=resource_type)
        if not url:
            return jsonify({'success': False, 'message': 'Upload Cloudinary gagal.'}), 500
        return jsonify({'success': True, 'url': url})
    except Exception as e:
        log.error('upload_media error: %s', e)
        return jsonify({'success': False, 'message': f'Upload gagal: {e}'}), 500


# ================= ROUTES — AI TOOLS (GEMINI, SERVER-SIDE) =================
# Frontend TIDAK perlu API key. Semua pemanggilan Gemini terjadi di server.

SYS_ENHANCER = (
    "You are an expert AI image prompt engineer. Expand the user's simple idea "
    "into a rich, detailed English image-generation prompt (subject, outfit, pose, "
    "environment, lighting, camera, style, negative prompts). Output the prompt only, "
    "no markdown, no conversational text."
)

SYS_IMG2PROMPT = (
    "You are an expert AI image prompt engineer. Analyze the image and write a "
    "detailed, structured image-generation prompt in Indonesian, covering: subjek, "
    "outfit/pakaian, pose & ekspresi, latar belakang & pencahayaan, pengambilan "
    "gambar/kamera, dan final style. Output the prompt only, without markdown."
)

SYS_VIP_DUO = (
    'You are an expert AI image prompt engineer. Analyze the couple (pria & wanita) '
    'photo and generate a structured Indonesian prompt based on the photo. '
    'Do NOT add a header. Do NOT use markdown. Output ONLY the following format '
    'with exactly these labels:\n\n'
    'Outfit : [deskripsi]\n\n'
    'Pose : [deskripsi]\n\n'
    'latar belakang dan pencahayaan : [deskripsi]\n\n'
    'pengambilan gambar : [deskripsi]'
)

SYS_VIP_SOLO = (
    'You are an expert AI image prompt engineer. Analyze the single-person photo '
    'and generate a structured Indonesian prompt based on the photo. '
    'Do NOT add a header. Do NOT use markdown. Output ONLY the following format '
    'with exactly these labels:\n\n'
    'Outfit : [deskripsi]\n\n'
    'Pose : [deskripsi]\n\n'
    'latar belakang dan pencahayaan : [deskripsi]\n\n'
    'pengambilan gambar : [deskripsi]'
)


@app.route('/api/ai/enhance', methods=['POST'])
def ai_enhance():
    """Prompt Enhancer: text → enhanced prompt."""
    data = request.get_json(silent=True) or {}
    idea = (data.get('idea') or '').strip()
    if not idea:
        return jsonify({'success': False, 'message': 'Ide prompt wajib diisi.'}), 400
    try:
        raw = call_gemini(SYS_ENHANCER, idea, temperature=0.8)
        return jsonify({'success': True, 'result': clean_model_output(raw)})
    except Exception as e:
        log.error('ai_enhance error: %s', e)
        return jsonify({'success': False, 'message': str(e)}), 500


def _read_image_from_request():
    """Ambil gambar dari multipart 'image' ATAU json base64 'image_b64'+'image_mime'.
    Return (b64, mime) atau raise."""
    if 'image' in request.files:
        f = request.files['image']
        if not f or f.filename == '':
            raise ValueError('File gambar kosong.')
        mime = f.mimetype or 'image/png'
        raw = f.read()
    else:
        data = request.get_json(silent=True) or {}
        b64 = (data.get('image_b64') or '').split(',')[-1]
        mime = data.get('image_mime') or 'image/png'
        if not b64:
            raise ValueError('Gambar wajib diisi.')
        import base64
        try:
            raw = base64.b64decode(b64)
        except Exception:
            raise ValueError('Base64 gambar tidak valid.')
    if len(raw) > MAX_INLINE_BYTES:
        raise ValueError('Ukuran gambar melebihi 20MB.')
    import base64
    return base64.b64encode(raw).decode('ascii'), mime


@app.route('/api/ai/img2prompt', methods=['POST'])
def ai_img2prompt():
    """Image to Prompt: foto → prompt detail (ID)."""
    try:
        b64, mime = _read_image_from_request()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    try:
        raw = call_gemini(
            SYS_IMG2PROMPT,
            'Analisis foto ini dan buatkan prompt gambar yang lengkap dan detail dalam Bahasa Indonesia.',
            image_b64=b64, image_mime=mime, temperature=0.5,
        )
        return jsonify({'success': True, 'result': clean_model_output(raw)})
    except Exception as e:
        log.error('ai_img2prompt error: %s', e)
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/ai/vipduo', methods=['POST'])
def ai_vipduo():
    """VIP Duo: foto pasangan → prompt couple terstruktur."""
    try:
        b64, mime = _read_image_from_request()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    try:
        raw = call_gemini(
            SYS_VIP_DUO,
            'Analisis foto pasangan ini. Buatkan 4 bagian deskripsi sesuai format persis tanpa tambahan kata.',
            image_b64=b64, image_mime=mime, temperature=0.5,
        )
        return jsonify({'success': True, 'result': clean_model_output(raw)})
    except Exception as e:
        log.error('ai_vipduo error: %s', e)
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/ai/vipsolo', methods=['POST'])
def ai_vipsolo():
    """VIP Solo: foto satu orang → prompt solo terstruktur."""
    try:
        b64, mime = _read_image_from_request()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    try:
        raw = call_gemini(
            SYS_VIP_SOLO,
            'Analisis foto satu orang ini. Buatkan 4 bagian deskripsi sesuai format persis tanpa tambahan kata.',
            image_b64=b64, image_mime=mime, temperature=0.5,
        )
        return jsonify({'success': True, 'result': clean_model_output(raw)})
    except Exception as e:
        log.error('ai_vipsolo error: %s', e)
        return jsonify({'success': False, 'message': str(e)}), 500


# ================= ROUTES — HEALTH =================
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'success': True,
        'service': 'prompthub',
        'time': datetime.utcnow().isoformat() + 'Z',
        'mongo': get_db() is not None,
        'cloudinary': init_cloudinary(),
        'gemini_configured': bool(GEMINI_API_KEY),
    })


# ================= ENTRYPOINT =================
if __name__ == '__main__':
    log.info('Starting PromptHub on port %s', PORT)
    # Warm-up koneksi eksternal (tidak wajib, tapi langsung kelihatan kalau gagal)
    try:
        get_db()
    except Exception as e:
        log.warning('Warm-up MongoDB gagal: %s', e)
    try:
        init_cloudinary()
    except Exception as e:
        log.warning('Warm-up Cloudinary gagal: %s', e)

    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=PORT)
    except ImportError:
        app.run(host='0.0.0.0', port=PORT, debug=False)

import os
import time
import json
import urllib.request
import urllib.error
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from dotenv import load_dotenv

# Memuat variabel lingkungan dari file .env jika tersedia (untuk pengujian lokal)
load_dotenv()

# ==============================================================================
# KONFIGURASI SERVER DARI ENVIRONMENT VARIABLES
# ==============================================================================
SECRET_KEY = os.environ.get('SECRET_KEY', 'nathanvipkey')
ADMIN_LOGIN_KEY = os.environ.get('ADMIN_LOGIN_KEY', 'lutfifarid')

# API Key Gemini diambil dari Environment Variables Koyeb / .env
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '').strip()

# Konfigurasi MongoDB, Cloudinary, & WhatsApp
MONGO_URI = os.environ.get('MONGO_URI', '').strip()
CLOUDINARY_URL = os.environ.get('CLOUDINARY_URL', '').strip()
WA_NUMBER = os.environ.get('WA_NUMBER', '6281234567890').strip()

UPLOAD_FOLDER = 'asset'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}
# ==============================================================================

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Inisialisasi Cloudinary
cloudinary_available = False
if CLOUDINARY_URL:
    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(cloudinary_url=CLOUDINARY_URL)
        cloudinary_available = True
    except Exception as e:
        print("Gagal konfigurasi Cloudinary:", e)

# Inisialisasi MongoDB Client
mongo_client = None
db = None
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        mongo_client.admin.command('ping')
        db_name = mongo_client.get_database().name
        db = mongo_client.get_database() if db_name else mongo_client['prompthub']
        print("MongoDB Connected!")
    except Exception as e:
        print("MongoDB Fallback ke database.txt:", e)
        db = None
else:
    print("MONGO_URI tidak ditemukan di Environment Variables, fallback ke database.txt")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ================= UTILITAS DATABASE =================
def read_db():
    if db is not None:
        try:
            doc = db.app_data.find_one({"_id": "global_state"})
            if doc:
                doc.pop('_id', None)
                return doc
        except Exception:
            pass

    DB_FILE = 'database.txt'
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
            
    return {
        "generatedCount": 0,
        "gallery": [],
        "announcements": [],
        "admin": {
            "name": "Nathan",
            "avatar": "https://ui-avatars.com/api/?name=Nathan&background=ffd43b&color=1b1b1f"
        }
    }

def write_db(data):
    if db is not None:
        try:
            db.app_data.update_one({"_id": "global_state"}, {"$set": data}, upsert=True)
            return
        except Exception as e:
            print("MongoDB Write Error:", e)

    DB_FILE = 'database.txt'
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

# ================= HELPER PEMANGGILAN GEMINI API =================
def call_gemini(payload):
    key = GEMINI_API_KEY
    if not key:
        raise Exception("GEMINI_API_KEY belum disetel pada Environment Variables Koyeb / .env!")
        
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    req_data = json.dumps(payload).encode('utf-8')
    
    headers = {
        'Content-Type': 'application/json',
        'x-goog-api-key': key
    }
    
    req = urllib.request.Request(url, data=req_data, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if "candidates" in data and len(data["candidates"]) > 0:
                parts = data["candidates"][0]["content"]["parts"]
                return "".join([p.get("text", "") for p in parts])
            raise Exception("Response AI kosong atau terblokir filter keamanan.")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8')
        try:
            err_json = json.loads(err_msg)
            raise Exception(err_json.get('error', {}).get('message', f"HTTP Error {e.code}"))
        except json.JSONDecodeError:
            raise Exception(f"HTTP Error {e.code}: {e.reason}")

# ================= ROUTES =================
@app.route('/')
def index():
    return render_template('index.html', wa_number=WA_NUMBER)

@app.route('/api/database', methods=['GET'])
def get_database():
    return jsonify(read_db())

@app.route('/api/database', methods=['POST'])
def save_database():
    data = request.json or {}
    write_db(data)
    return jsonify({"success": True})

@app.route('/api/admin_login', methods=['POST'])
def admin_login():
    data = request.json or {}
    if data.get('key', '') == ADMIN_LOGIN_KEY:
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Secret key salah."}), 401

@app.route('/upload_media', methods=['POST'])
def upload_media():
    if 'media_file' not in request.files:
        return jsonify({'success': False, 'message': 'Tidak ada file.'}), 400

    file = request.files['media_file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Nama file kosong.'}), 400

    if file and allowed_file(file.filename):
        if cloudinary_available:
            try:
                import cloudinary.uploader
                ext = file.filename.rsplit('.', 1)[1].lower()
                res_type = "video" if ext in {'mp4', 'webm', 'mov'} else "image"
                upload_res = cloudinary.uploader.upload(file, folder="prompthub_assets", resource_type=res_type)
                return jsonify({'success': True, 'url': upload_res.get('secure_url')})
            except Exception as e:
                print("Cloudinary error, fallback ke lokal:", e)

        filename = secure_filename(file.filename)
        filename = f"{int(time.time())}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        return jsonify({'success': True, 'url': f"/asset/{filename}"})

    return jsonify({'success': False, 'message': 'Format file tidak didukung.'}), 400

@app.route('/asset/<path:filename>')
def serve_asset(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ================= BACKEND AI TOOLS API =================
@app.route('/api/ai/enhance', methods=['POST'])
def ai_enhance():
    data = request.json or {}
    idea = data.get('idea', '').strip()
    if not idea:
        return jsonify({'success': False, 'message': 'Ide prompt tidak boleh kosong.'}), 400
        
    payload = {
        "systemInstruction": {
            "parts": [{"text": "You are an expert AI image prompt engineer. Expand the user's simple idea into a rich, detailed English image-generation prompt (subject, outfit, pose, environment, lighting, camera, style, negative prompts). Output the prompt only, no markdown, no conversational text."}]
        },
        "contents": [{"parts": [{"text": idea}]}],
        "generationConfig": {"temperature": 0.8}
    }
    try:
        result = call_gemini(payload)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ai/vision', methods=['POST'])
def ai_vision():
    data = request.json or {}
    img_b64 = data.get('image_b64', '')
    mime_type = data.get('mime_type', 'image/jpeg')
    mode = data.get('mode', 'i2p')
    subject = data.get('subject', 'wanita')
    
    if not img_b64:
        return jsonify({'success': False, 'message': 'Gambar wajib disertakan.'}), 400

    if mode == 'i2p':
        system_text = "You are an expert AI image prompt engineer. Analyze the image and write a detailed, structured image-generation prompt in Indonesian, covering: subjek, outfit/pakaian, pose & ekspresi, latar belakang & pencahayaan, pengambilan gambar/kamera, dan final style. Output the prompt only, without markdown."
        user_text = "Analisis foto ini dan buatkan prompt gambar yang lengkap dan detail dalam Bahasa Indonesia."
    else:
        # Untuk mode duo atau solo
        subject_label = "couple (pria & wanita)" if mode == 'vipduo' else f"single-person ({subject})"
        system_text = f"You are an expert AI image prompt engineer. Analyze the {subject_label} photo and generate a structured Indonesian prompt based on the photo. Do NOT add a header. Do NOT use markdown. Output ONLY the following format with exactly these labels:\n\nOutfit : [deskripsi]\n\nPose : [deskripsi]\n\nlatar belakang dan pencahayaan : [deskripsi]\n\npengambilan gambar : [deskripsi]"
        user_text = f"Analisis foto {subject_label} ini. Buatkan 4 bagian deskripsi sesuai format persis tanpa tambahan kata."

    payload = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{
            "parts": [
                {"text": user_text},
                {"inlineData": {"mimeType": mime_type, "data": img_b64}}
            ]
        }],
        "generationConfig": {"temperature": 0.5}
    }
    
    try:
        result = call_gemini(payload)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=False)
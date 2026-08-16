import os
import time
import json
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from pymongo import MongoClient

# ==============================================================================
# KONFIGURASI SERVER (SEMUA DISIMPAN DI APP.PY)
# ==============================================================================
SECRET_KEY = 'lutfifarid'
ADMIN_LOGIN_KEY = 'lutfifarid'

# API Key bawaan server untuk AI Tools (Enhancer, Img2Prompt, VIP Duo, VIP Solo)
GEMINI_API_KEY = "AQ.Ab8RN6LMG8hz27fCD0DeGoU5ZFpeZoDDzGPniHSMjE2zvXkwMA"

# String Koneksi MongoDB Atlas
# Silakan ganti URL di bawah ini dengan URI MongoDB Atlas kamu:
# Contoh: "mongodb+srv://username:password@cluster0.abcde.mongodb.net/prompthub?retryWrites=true&w=majority"
#MONGO_URI = "mongodb://localhost:27017/prompthub"
# Ganti dengan username & password asli kamu
MONGO_URI = "mongodb+srv://lutfi:lutfi@cluster0.pa62uis.mongodb.net/prompthub?appName=Cluster0"


# String Koneksi Cloudinary untuk Storage Foto/Video Cloud (Opsional)
# Contoh: "cloudinary://123456789012345:abcdefghijklmnopqrstuvwxyz123@d1abcdefg"
CLOUDINARY_URL = "cloudinary://884358532769249:8oJlR7ej55G7Tszt1qKcSxqBiyg@jjin8nez"

# Folder penyimpanan lokal (fallback jika Cloudinary kosong)
UPLOAD_FOLDER = 'asset'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}
# ==============================================================================

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Membuat folder asset lokal jika belum ada
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Inisialisasi Cloudinary jika URL diisi
cloudinary_available = False
if CLOUDINARY_URL.strip():
    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(cloudinary_url=CLOUDINARY_URL.strip())
        cloudinary_available = True
        print("Cloudinary terkonfigurasi dengan sukses!")
    except Exception as e:
        print("Gagal mengkonfigurasi Cloudinary:", e)
        cloudinary_available = False

# Inisialisasi MongoDB Client
mongo_client = None
db = None

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    mongo_client.admin.command('ping')
    
    # Ambil database dari URI atau buat nama default 'prompthub'
    db_name = mongo_client.get_database().name
    db = mongo_client.get_database() if db_name else mongo_client['prompthub']
    print("Berhasil terhubung ke MongoDB!")
except Exception as e:
    print("Peringatan: Tidak dapat terhubung ke MongoDB (Menggunakan database lokal database.txt):", e)
    db = None

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ================= UTILITAS DATABASE =================
def read_db():
    # 1. Coba baca dari MongoDB
    if db is not None:
        try:
            doc = db.app_data.find_one({"_id": "global_state"})
            if doc:
                doc.pop('_id', None)
                return doc
        except Exception as e:
            print("Gagal membaca MongoDB:", e)

    # 2. Fallback baca berkas lokal database.txt
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
    # 1. Simpan ke MongoDB jika terhubung
    if db is not None:
        try:
            db.app_data.update_one(
                {"_id": "global_state"},
                {"$set": data},
                upsert=True
            )
            return
        except Exception as e:
            print("Gagal menulis ke MongoDB:", e)

    # 2. Fallback simpan ke database.txt lokal
    DB_FILE = 'database.txt'
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

# ================= RUTE APLIKASI =================
@app.route('/')
def index():
    return render_template('index.html')

# Endpoint API mengambil data
@app.route('/api/database', methods=['GET'])
def get_database():
    return jsonify(read_db())

# Endpoint API menyimpan data
@app.route('/api/database', methods=['POST'])
def save_database():
    data = request.json or {}
    write_db(data)
    return jsonify({"success": True})

# Endpoint API validasi login admin
@app.route('/api/admin_login', methods=['POST'])
def admin_login():
    data = request.json or {}
    key = data.get('key', '')
    if key == ADMIN_LOGIN_KEY:
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Secret key salah."}), 401

# Endpoint Upload Media (Mendukung Cloudinary & Simpan Lokal)
@app.route('/upload_media', methods=['POST'])
def upload_media():
    if 'media_file' not in request.files:
        return jsonify({'success': False, 'message': 'Tidak ada file yang dikirim.'}), 400

    file = request.files['media_file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Nama file kosong.'}), 400

    if file and allowed_file(file.filename):
        # Unggah ke Cloudinary jika terkonfigurasi
        if cloudinary_available:
            try:
                import cloudinary.uploader
                ext = file.filename.rsplit('.', 1)[1].lower()
                resource_type = "video" if ext in {'mp4', 'webm', 'mov'} else "image"
                upload_result = cloudinary.uploader.upload(
                    file,
                    folder="prompthub_assets",
                    resource_type=resource_type
                )
                return jsonify({'success': True, 'url': upload_result.get('secure_url')})
            except Exception as e:
                print("Gagal upload ke Cloudinary, beralih ke penyimpanan lokal:", e)

        # Simpan ke folder lokal /asset jika Cloudinary tidak aktif/gagal
        filename = secure_filename(file.filename)
        filename = f"{int(time.time())}_{filename}"

        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        file_url = f"/asset/{filename}"
        return jsonify({'success': True, 'url': file_url})

    return jsonify({'success': False, 'message': 'Format file tidak didukung.'}), 400

# Endpoint menyajikan berkas lokal dari folder asset
@app.route('/asset/<path:filename>')
def serve_asset(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=False)

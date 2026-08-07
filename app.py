import os
import time
import json
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)
# Secret key untuk sesi Flask (server-side)
app.secret_key = 'lutfifarid'

# Key login admin — disimpan langsung di server (app.py), TIDAK ada di client.
# Ganti nilai ini dengan key rahasia kamu sendiri.
ADMIN_LOGIN_KEY = 'lutfifarid'

# Konfigurasi
UPLOAD_FOLDER = 'asset'
DB_FILE = 'database.txt'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Otomatis buat folder asset jika belum ada
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ================= DATABASE TXT HANDLER =================
def read_db():
    if not os.path.exists(DB_FILE):
        return {"generatedCount": 0, "gallery": [], "announcements": []}
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"generatedCount": 0, "gallery": [], "announcements": []}

def write_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

# ================= ROUTES =================
@app.route('/')
def index():
    return render_template('index.html')

# API untuk mendapatkan data dari database.txt
@app.route('/api/database', methods=['GET'])
def get_database():
    return jsonify(read_db())

# API untuk menyimpan data ke database.txt
@app.route('/api/database', methods=['POST'])
def save_database():
    data = request.json
    write_db(data)
    return jsonify({"success": True})

# API validasi login admin — key diperiksa di server, bukan di client
@app.route('/api/admin_login', methods=['POST'])
def admin_login():
    data = request.json or {}
    key = data.get('key', '')
    if key == ADMIN_LOGIN_KEY:
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Secret key salah."}), 401

# API Endpoint untuk menerima upload dari Frontend ke folder asset
@app.route('/upload_media', methods=['POST'])
def upload_media():
    if 'media_file' not in request.files:
        return jsonify({'success': False, 'message': 'Tidak ada file yang dikirim.'}), 400

    file = request.files['media_file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Nama file kosong.'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filename = f"{int(time.time())}_{filename}"

        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        file_url = f"/asset/{filename}"
        return jsonify({'success': True, 'url': file_url})

    return jsonify({'success': False, 'message': 'Format file tidak didukung.'}), 400

# Endpoint untuk menyajikan file dari folder asset
@app.route('/asset/<path:filename>')
def serve_asset(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    # Untuk produksi (PaaS seperti Koyeb/Render): bind ke 0.0.0.0 dan
    # baca port dari environment variable PORT (disediakan otomatis oleh Koyeb).
    port = int(os.environ.get('PORT', 8000))
    # Jalankan memakai Waitress bila tersedia (lebih aman untuk produksi)
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=False)

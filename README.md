# PromptHub — Cloud Edition v3.1

Aplikasi Flask untuk membuat & memajang prompt AI. Versi cloud dengan:
- **MongoDB Atlas** untuk database (bukan `database.txt` lagi)
- **Cloudinary** untuk storage media gambar/video (bukan folder `asset/` lokal lagi)
- **Gemini AI** dipanggil dari **server** — API key tidak pernah terekspos ke browser
- **.env** untuk semua konfigurasi

## Struktur File

```
.
├── app.py                 # Backend Flask (MongoDB + Cloudinary + Gemini proxy)
├── templates/
│   └── index.html         # Frontend (tanpa input API key)
├── requirements.txt       # Dependensi Python
├── .env                   # Konfigurasi (RAHASIA — jangan di-commit)
├── .env.example           # Template konfigurasi (aman di-commit)
├── .gitignore             # Mengecualikan .env, __pycache__, dll.
└── README.md              # File ini
```

## Setup

### 1. Install dependensi
```bash
pip install -r requirements.txt
```

### 2. Konfigurasi `.env`
File `.env` sudah berisi kredensial yang kamu berikan. Untuk deployment baru, salin dari `.env.example` dan isi:
```bash
cp .env.example .env
# edit .env dengan editor favoritmu
```

Variabel yang diperlukan:
| Variabel | Wajib | Keterangan |
|---|---|---|
| `PORT` | tidak | Default `8000`. PaaS (Koyeb/Render) otomatis set. |
| `SECRET_KEY` | ya | Secret key sesi Flask. |
| `ADMIN_LOGIN_KEY` | ya | Password untuk login admin. |
| `GEMINI_API_KEY` | ya | API key dari https://aistudio.google.com/app/apikey |
| `GEMINI_MODEL` | tidak | Default `gemini-2.0-flash`. Alternatif: `gemini-1.5-flash`, `gemini-flash-latest`. |
| `MONGO_URI` | ya | Connection string MongoDB Atlas. |
| `CLOUDINARY_URL` | ya | URL Cloudinary dari dashboard. |
| `WA_NUMBER` | ya | Nomor WhatsApp admin (format internasional tanpa `+`). |

### 3. Jalankan server
```bash
python app.py
# atau produksi:
waitress-serve --port=8000 app:app
```

Buka http://localhost:8000

## Endpoint API

### Database (MongoDB)
- `GET /api/database` — baca seluruh state aplikasi
- `POST /api/database` — simpan state (body: JSON state)
- `POST /api/admin_login` — validasi key admin (body: `{"key":"..."}`)

### Media (Cloudinary)
- `POST /upload_media` — upload gambar/video ke Cloudinary (multipart form, field: `media_file`), return `{"success":true,"url":"https://res.cloudinary.com/..."}`

### AI Tools (Gemini, server-side)
Frontend tidak perlu API key. Semua panggilan Gemini terjadi di server.
- `POST /api/ai/enhance` — Prompt Enhancer (body: `{"idea":"..."}`)
- `POST /api/ai/img2prompt` — Image to Prompt (multipart form, field: `image`)
- `POST /api/ai/vipduo` — VIP Duo couple (multipart form, field: `image`)
- `POST /api/ai/vipsolo` — VIP Solo single (multipart form, field: `image`)

### Health Check
- `GET /api/health` — status koneksi MongoDB, Cloudinary, Gemini

## Migrasi dari Versi Lama

Jika kamu upgrade dari versi `database.txt`:
1. Letakkan file `database.txt` lama di folder yang sama dengan `app.py`.
2. Saat server pertama kali start dan MongoDB masih kosong, data lama otomatis diimpor ke MongoDB.
3. Setelah migrasi sukses, `database.txt` bisa dihapus (tidak dipakai lagi).

## Deployment ke PaaS (Koyeb / Render / Railway)

1. Push repo ke GitHub (pastikan `.env` TIDAK ikut ter-commit — cek `.gitignore`).
2. Buat service baru, set build command: `pip install -r requirements.txt`
3. Set start command: `python app.py` atau `waitress-serve --port=$PORT app:app`
4. Set environment variables di dashboard PaaS (salin isi `.env`).
5. Deploy.

## Catatan Keamanan

- **API key Gemini** hanya ada di server. Frontend memanggil `/api/ai/*`, server yang meneruskan ke Gemini.
- **MongoDB credentials** hanya di `.env`, tidak di kode.
- **Cloudinary secret** hanya di `.env`.
- **Admin login key** divalidasi di server (`/api/admin_login`), bukan di client.
- `.env` sudah di-exclude dari git via `.gitignore`.

## Troubleshooting

**"Gemini error (404): model no longer available"**
Region/akun kamu tidak support model tersebut. Coba ganti `GEMINI_MODEL` di `.env` ke `gemini-1.5-flash` atau `gemini-flash-latest`.

**"User location is not supported for the API use"**
Server kamu berada di region yang tidak didukung Gemini API. Deploy ke region US/EU/Asia-Pasifik yang didukung. Daftar region: https://ai.google.dev/available_regions

**"Gagal terhubung MongoDB"**
- Cek `MONGO_URI` di `.env` sudah benar.
- Cek IP server sudah di-whitelist di MongoDB Atlas (Network Access).
- Untuk testing, set `0.0.0.0/0` di Atlas (tidak disarankan untuk produksi).

**"Cloudinary tidak terkonfigurasi"**
Cek `CLOUDINARY_URL` di `.env`. Format: `cloudinary://<api_key>:<api_secret>@<cloud_name>`

import os
import io
import json
import base64
import pandas as pd
from PIL import Image, ImageDraw
import pillow_heif  # ← HEIC対応
import exifread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from github import Github

# ===== 環境変数 =====
FOLDER_ID = os.environ["FOLDER_ID"]
SERVICE_ACCOUNT_B64 = os.environ["SERVICE_ACCOUNT_B64"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO_NAME = os.environ["REPO_NAME"]
HTML_NAME = os.environ.get("HTML_NAME", "index.html")
BRANCH_NAME = os.environ.get("BRANCH_NAME", "main")
CACHE_FILE = os.environ.get("CACHE_FILE", "photomap_cache.json")

# ===== Google Drive 認証 =====
SERVICE_ACCOUNT_INFO = json.loads(base64.b64decode(SERVICE_ACCOUNT_B64))
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
creds = service_account.Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=creds)

# ===== GitHub クライアント =====
gh = Github(GITHUB_TOKEN)
repo = gh.get_repo(REPO_NAME)

# ===== Drive 画像一覧 =====
def list_image_files(folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    return results.get('files', [])

# ===== ファイル取得 =====
def get_file_bytes(file_id):
    fh = io.BytesIO()
    request = drive_service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()

# ===== PILオープン（HEIC/JPEG対応）=====
def pil_open_safe(file_bytes, mime_type):
    try:
        if 'heic' in mime_type.lower():
            heif_file = pillow_heif.read_heif(file_bytes)
            pil_img = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data)
        else:
            pil_img = Image.open(io.BytesIO(file_bytes))
        return pil_img
    except Exception as e:
        print(f"⚠️ Cannot open image: {e}")
        return None

# ===== EXIF抽出 =====
def extract_exif(file_bytes, mime_type):
    lat, lon, dt = '', '', ''
    try:
        img_bytes = file_bytes
        if 'heic' in mime_type.lower():
            pil_img = pil_open_safe(file_bytes, mime_type)
            buf = io.BytesIO()
            pil_img.save(buf, format='JPEG')
            buf.seek(0)
            img_bytes = buf.getvalue()

        tags = exifread.process_file(io.BytesIO(img_bytes), details=False)
        if 'GPS GPSLatitude' in tags and 'GPS GPSLongitude' in tags:
            def dms_to_dd(dms, ref):
                deg = float(dms.values[0].num)/dms.values[0].den
                minute = float(dms.values[1].num)/dms.values[1].den
                sec = float(dms.values[2].num)/dms.values[2].den
                dd = deg + minute/60 + sec/3600
                if ref.values not in ['N','E']:
                    dd = -dd
                return dd
            lat = dms_to_dd(tags['GPS GPSLatitude'], tags['GPS GPSLatitudeRef'])
            lon = dms_to_dd(tags['GPS GPSLongitude'], tags['GPS GPSLongitudeRef'])
        if 'EXIF DateTimeOriginal' in tags:
            dt = str(tags['EXIF DateTimeOriginal'])
    except Exception as e:
        print(f"⚠️ EXIF parse error: {e}")
    return lat, lon, dt

# ===== サムネ作成 =====
def heic_to_base64_circle(file_bytes, size=50):
    img = pil_open_safe(file_bytes, 'image/heic')
    if img is None:
        return None
    img = img.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,size,size), fill=255)
    img = img.convert("RGBA")
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

def heic_to_base64_popup(file_bytes, width=200):
    img = pil_open_safe(file_bytes, 'image/heic')
    if img is None:
        return None
    w, h = img.size
    img = img.resize((width, int(h*(width/w))))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

# ===== キャッシュ読み込み =====
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    try:
        c = repo.get_contents(CACHE_FILE, ref=BRANCH_NAME)
        return json.loads(c.decoded_content.decode())
    except Exception:
        return {}

cached_files = load_cache()

# ===== 画像収集 =====
loaded_images = []
failed_images = []
rows = []

for f in list_image_files(FOLDER_ID):
    print(f"Processing {f['name']}...")
    try:
        file_bytes = get_file_bytes(f['id'])
        img = pil_open_safe(file_bytes, f['mimeType'])
        if img is None:
            failed_images.append(f['name'])
            print(f"⚠️ Failed to load: {f['name']}")
            continue
        loaded_images.append(f['name'])

        lat, lon, dt = extract_exif(file_bytes, f['mimeType'])
        row = {
            'filename': f['name'],
            'latitude': lat,
            'longitude': lon,
            'datetime': dt,
            'file_id': f['id'],
            'mime_type': f['mimeType']
        }
        rows.append(row)
        cached_files[f['id']] = row
    except Exception as e:
        failed_images.append(f['name'])
        print(f"⚠️ Failed to load: {f['name']} ({e})")

# キャッシュ保存
with open(CACHE_FILE, 'w') as f:
    json.dump(cached_files, f)

df = pd.DataFrame(rows)

# ===== HTML生成 =====
html_lines = [
    "<!DOCTYPE html>",
    "<html><head><meta charset='utf-8'><title>Photo Map</title>",
    "<style>#map { height: 100vh; width: 100%; }</style>",
    "<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>",
    "<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script></head><body>",
    "<div id='map'></div><script>",
    "var map = L.map('map').setView([35.0, 138.0], 5);",
    "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19}).addTo(map);",
    "var markers = [];",
    "var bounds = L.latLngBounds();"
]

for _, row in df.iterrows():
    if row['latitude'] and row['longitude']:
        file_bytes = get_file_bytes(row['file_id'])
        icon_data_uri = heic_to_base64_circle(file_bytes)
        popup_data_uri = heic_to_base64_popup(file_bytes)
        if icon_data_uri and popup_data_uri:
            html_lines.append(f"""
var icon = L.icon({{iconUrl: '{icon_data_uri}', iconSize: [50,50]}});
var lat = {row['latitude']};
var lon = {row['longitude']};
var marker = L.marker([lat, lon], {{icon: icon}}).addTo(map);
markers.push(marker);
bounds.extend([lat, lon]);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{popup_data_uri}' width='200'/>");
""")

html_lines.append("""
if (!bounds.isEmpty()) { map.fitBounds(bounds.pad(0.2)); }
map.on('zoomend', function(){
    var zoom = map.getZoom();
    var scale = Math.min(zoom/5, 1.2);
    markers.forEach(function(m){
        var img = m.getElement().querySelector('img');
        if(img){ var size = 50*scale; if(size>60){size=60;} img.style.width=size+'px'; img.style.height=size+'px'; }
    });
});
""")
html_lines += ["</script></body></html>"]
html_str = "\n".join(html_lines)

# ===== GitHubにアップロード =====
def upsert_file(path, content, message):
    try:
        c = repo.get_contents(path, ref=BRANCH_NAME)
        repo.update_file(path, message, content, c.sha, branch=BRANCH_NAME)
        print(f"Updated: {path}")
    except Exception:
        repo.create_file(path, message, content, branch=BRANCH_NAME)
        print(f"Created: {path}")

upsert_file(HTML_NAME, html_str, "update HTML (Leaflet)")
upsert_file(CACHE_FILE, json.dumps(cached_files), "update cache")

# ===== 読み込んだ/失敗した画像リストを表示 =====
print("✅ Loaded images:")
for n in loaded_images:
    print(f" - {n}")
if failed_images:
    print("⚠️ Failed to load images:")
    for n in failed_images:
        print(f" - {n}")

print("✅ Done.")


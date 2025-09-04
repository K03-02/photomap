#!/usr/bin/env python3
import os
import io
import json
import base64
import pandas as pd
from PIL import Image, ImageDraw
from pillow_heif import register_heif_opener
import exifread
from github import Github, Auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

register_heif_opener()  # HEIC対応

# ===== 設定 =====
FOLDER_ID = '1d9C_qIKxBlzngjpZjgW68kIZkPZ0NAwH'
REPO_NAME = 'K03-02/photomap'
HTML_NAME = 'index.html'
CACHE_FILE = 'photomap_cache.json'
BRANCH_NAME = 'main'

# ===== Google Drive 認証（ユーザー認証版） =====
token_info = json.loads(base64.b64decode(os.environ['USER_OAUTH_B64']))
creds = Credentials(
    token=token_info['token'],
    refresh_token=token_info.get('refresh_token'),
    token_uri=token_info['token_uri'],
    client_id=token_info.get('client_id'),
    client_secret=token_info.get('client_secret'),
    scopes=token_info.get('scopes', ["https://www.googleapis.com/auth/drive"])
)
drive_service = build('drive', 'v3', credentials=creds)

# ===== ヘルパー関数 =====
def list_image_files(folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    return results.get('files', [])

def get_file_bytes(file_id):
    fh = io.BytesIO()
    request = drive_service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()

def pil_open_safe(file_bytes, mime_type):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        return img
    except Exception as e:
        print(f"⚠️ Cannot open image: {e}")
        return None

def extract_exif(file_bytes, mime_type):
    lat = lon = dt = ''
    try:
        tags = exifread.process_file(io.BytesIO(file_bytes), details=False)
        if 'GPS GPSLatitude' in tags and 'GPS GPSLongitude' in tags:
            def dms_to_dd(dms, ref):
                deg = float(dms.values[0].num)/dms.values[0].den
                min = float(dms.values[1].num)/dms.values[1].den
                sec = float(dms.values[2].num)/dms.values[2].den
                dd = deg + min/60 + sec/3600
                if ref.values not in ['N','E']:
                    dd = -dd
                return dd
            lat = dms_to_dd(tags['GPS GPSLatitude'], tags['GPS GPSLatitudeRef'])
            lon = dms_to_dd(tags['GPS GPSLongitude'], tags['GPS GPSLongitudeRef'])
        if 'EXIF DateTimeOriginal' in tags:
            dt = str(tags['EXIF DateTimeOriginal'])
    except:
        print(f"⚠️ EXIF not found for {mime_type}")
    return lat, lon, dt

def pil_to_base64_circle(img, size=50):
    img = img.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,size,size), fill=255)
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

def pil_to_base64_popup(img, width=200):
    w, h = img.size
    new_h = int(h * (width / w))
    img = img.resize((width, new_h))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

# ===== キャッシュ読み込み =====
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE,'r') as f:
        cached_files = json.load(f)
else:
    cached_files = {}

rows = []
for f in list_image_files(FOLDER_ID):
    # 新しい画像だけ処理
    if f['id'] in cached_files:
        rows.append(cached_files[f['id']])
        continue
    print(f"Processing new file: {f['name']}...")
    file_bytes = get_file_bytes(f['id'])
    img = pil_open_safe(file_bytes, f['mimeType'])
    if img is None:
        print(f"⚠️ Skipping non-supported file: {f['name']}")
        continue
    lat, lon, dt = extract_exif(file_bytes, f['mimeType'])
    row = {
        'filename': f['name'],
        'latitude': lat,
        'longitude': lon,
        'datetime': dt,
        'file_id': f['id'],
        'mime_type': f['mimeType'],
        'img_obj': img
    }
    rows.append(row)
    cached_files[f['id']] = row  # 新規キャッシュ追加

# キャッシュ更新（ローカル）
with open(CACHE_FILE,'w') as f:
    json.dump(cached_files,f)

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
    "var markers = [];"]

for _, row in df.iterrows():
    if row['latitude'] and row['longitude']:
        icon_data_uri = pil_to_base64_circle(row['img_obj'])
        popup_data_uri = pil_to_base64_popup(row['img_obj'], width=200)
        html_lines.append(f"""
var icon = L.icon({{iconUrl: '{icon_data_uri}', iconSize: [50,50]}}); 
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{popup_data_uri}' width='200'/>");
""")

html_lines.append("""
map.on('zoomend', function(){
    var zoom = map.getZoom();
    var scale = Math.min(zoom/5, 1.2);
    markers.forEach(function(m){
        var img = m.getElement().querySelector('img');
        if(img){
            var size = 50 * scale;
            if(size>60){ size=60; }
            img.style.width = size + 'px';
            img.style.height = size + 'px';
        }
    });
});
""")
html_lines.append("</script></body></html>")

html_str = "\n".join(html_lines)

# ===== GitHub更新 =====
g = Github(auth=Auth.Token(os.environ['GITHUB_TOKEN']))
repo = g.get_repo(REPO_NAME)

# HTML更新
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML with new images", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")

# キャッシュ永続化（GitHub）
try:
    contents = repo.get_contents(CACHE_FILE, ref=BRANCH_NAME)
    repo.update_file(CACHE_FILE, "update photomap cache", json.dumps(cached_files), contents.sha, branch=BRANCH_NAME)
    print("Cache updated on GitHub.")
except:
    repo.create_file(CACHE_FILE, "create photomap cache", json.dumps(cached_files), branch=BRANCH_NAME)
    print("Cache created on GitHub.")

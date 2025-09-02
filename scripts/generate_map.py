#!/usr/bin/env python3
import os
import io
import json
import base64
import pandas as pd
from PIL import Image, ImageDraw
import pyheif
import exifread
from github import Github
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ===== 設定 =====
FOLDER_ID = '1d9C_qIKxBlzngjpZjgW68kIZkPZ0NAwH'
REPO_NAME = 'K03-02/photomap'
HTML_NAME = 'index.html'
BRANCH_NAME = 'main'
CACHE_FILE = 'photomap_cache.json'

# ===== Google Drive 認証 =====
service_account_info = json.loads(base64.b64decode(os.environ['SERVICE_ACCOUNT_B64']))
credentials = service_account.Credentials.from_service_account_info(
    service_account_info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
)
drive_service = build('drive', 'v3', credentials=credentials)

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
        if 'heic' in mime_type.lower() or 'heif' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            return Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
        else:
            return Image.open(io.BytesIO(file_bytes))
    except Exception as e:
        print(f"⚠️ Cannot open image: {e}")
        return None

def extract_exif(file_bytes, mime_type):
    lat = lon = dt = ''
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            img = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
            fbytes = io.BytesIO()
            img.save(fbytes, format='JPEG')
            fbytes.seek(0)
            tags = exifread.process_file(fbytes, details=False)
        else:
            tags = exifread.process_file(io.BytesIO(file_bytes), details=False)

        # EXIFをログ出力
        if tags:
            print(f"--- EXIF for {mime_type} ---")
            for k, v in tags.items():
                print(f"{k}: {v}")
            print("--- end EXIF ---")
        else:
            print(f"⚠️ EXIF not found for {mime_type}")

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
        pass
    return lat, lon, dt

def heic_to_base64_circle(file_bytes, size=50):
    img = pil_open_safe(file_bytes, 'image/heic')
    if img is None:
        return None
    img = img.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,size,size), fill=255)
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

def heic_to_base64_popup(file_bytes, mime_type, width=200):
    img = pil_open_safe(file_bytes, mime_type)
    if img is None: return None
    w, h = img.size
    new_h = int(h * (width / w))
    img = img.resize((width, new_h))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

# ===== キャッシュ =====
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE,'r') as f:
        cached_files = json.load(f)
else:
    cached_files = {}

rows = []
for f in list_image_files(FOLDER_ID):
    if f['id'] in cached_files:
        rows.append(cached_files[f['id']])
        continue
    print(f"Processing {f['name']}...")
    file_bytes = get_file_bytes(f['id'])
    lat, lon, dt = extract_exif(file_bytes, f['mimeType'])
    row = {'filename': f['name'], 'latitude': lat, 'longitude': lon, 'datetime': dt,
           'file_id': f['id'], 'mime_type': f['mimeType']}
    rows.append(row)
    cached_files[f['id']] = row

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
    "var markers = [];"
]

for _, row in df.iterrows():
    if row['latitude'] and row['longitude']:
        file_bytes = get_file_bytes(row['file_id'])
        popup_data_uri = heic_to_base64_popup(file_bytes, row['mime_type'], width=200)
        icon_data_uri = None
        if 'heic' in row['mime_type'].lower():
            icon_data_uri = heic_to_base64_circle(file_bytes)

        icon_js = f"L.icon({{iconUrl: '{icon_data_uri}', iconSize: [50,50]}})" if icon_data_uri else "undefined"

        html_lines.append(f"""
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: {icon_js}}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{popup_data_uri or ''}' width='200'/>");
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

# ===== GitHub 更新 =====
g = Github(os.environ['GITHUB_TOKEN'])
repo = g.get_repo(REPO_NAME)
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")

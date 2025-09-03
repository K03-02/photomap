#!/usr/bin/env python3
import os
import io
import json
import base64
from PIL import Image, ImageDraw
from pillow_heif import register_heif_opener
import exifread
from github import Github, Auth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

register_heif_opener()  # HEIC対応

# ===== 設定 =====
FOLDER_ID = '1d9C_qIKxBlzngjpZjgW68kIZkPZ0NAwH'
REPO_NAME = 'K03-02/photomap'
HTML_NAME = 'index.html'
BRANCH_NAME = 'main'
CACHE_FILE = 'photomap_cache.json'
THUMBNAIL_SIZE = 50
POPUP_WIDTH = 400

# ===== Google Drive 認証 =====
service_account_info = json.loads(base64.b64decode(os.environ['SERVICE_ACCOUNT_B64']))
credentials = service_account.Credentials.from_service_account_info(
    service_account_info, scopes=["https://www.googleapis.com/auth/drive"]
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

def pil_open_safe(file_bytes):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        return img
    except Exception as e:
        print(f"⚠️ Cannot open image: {e}")
        return None

def extract_exif(file_bytes):
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
        print(f"⚠️ EXIF not found")
    return lat, lon, dt

def create_thumbnail(img, size=THUMBNAIL_SIZE):
    img_thumb = img.copy()
    img_thumb.thumbnail((size, size))
    mask = Image.new("L", img_thumb.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,img_thumb.size[0], img_thumb.size[1]), fill=255)
    img_thumb.putalpha(mask)
    # 白い縁を追加
    border = Image.new("RGBA", (img_thumb.size[0]+4, img_thumb.size[1]+4), (255,255,255,255))
    border.paste(img_thumb, (2,2), img_thumb)
    return border

def save_to_drive(img, name, parent_id):
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    file_metadata = {'name': name, 'parents':[parent_id]}
    media = MediaFileUpload(io.BytesIO(buf.read()), mimetype='image/png', resumable=True)
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webContentLink').execute()
    return file['webContentLink'].replace('&export=download','')

# ===== キャッシュ読み込み =====
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

    print(f"Processing new file: {f['name']}...")
    file_bytes = get_file_bytes(f['id'])
    lat, lon, dt = extract_exif(file_bytes)
    img = pil_open_safe(file_bytes)
    if img is None:
        continue

    # サムネイルとポップアップ用画像を Drive にアップ
    thumb_link = save_to_drive(create_thumbnail(img), f"{f['name']}_thumb.png", FOLDER_ID)
    popup_link = save_to_drive(img, f"{f['name']}_popup.png", FOLDER_ID)

    row = {
        'filename': f['name'],
        'latitude': lat,
        'longitude': lon,
        'datetime': dt,
        'thumb_url': thumb_link,
        'popup_url': popup_link
    }
    rows.append(row)
    cached_files[f['id']] = row

# キャッシュ更新
with open(CACHE_FILE,'w') as f:
    json.dump(cached_files,f)

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

for row in rows:
    if row['latitude'] and row['longitude']:
        html_lines.append(f"""
var icon = L.icon({{iconUrl: '{row['thumb_url']}', iconSize: [50,50]}}); 
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{row['popup_url']}' width='{POPUP_WIDTH*2}'/>");
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
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML with Drive images", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")


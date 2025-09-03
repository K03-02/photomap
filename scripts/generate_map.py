#!/usr/bin/env python3
import os
import io
import json
import pandas as pd
from PIL import Image, ImageDraw
from pillow_heif import register_heif_opener
import exifread
from github import Github, Auth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

register_heif_opener()  # HEIC対応

# ===== 設定 =====
FOLDER_ID = '1d9C_qIKxBlzngjpZjgW68kIZkPZ0NAwH'
REPO_NAME = 'K03-02/photomap'
HTML_NAME = 'index.html'
BRANCH_NAME = 'main'
CACHE_FILE = 'photomap_cache.json'
IMAGES_DIR = 'images'  # GitHub内の画像フォルダ

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

def create_thumbnail_with_frame(img, size=50):
    """丸く切り抜き＋ピン風フレーム"""
    img_thumb = img.copy()
    img_thumb.thumbnail((size,size))
    # マスク作成
    mask = Image.new("L", img_thumb.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,img_thumb.size[0],img_thumb.size[1]), fill=255)
    img_thumb.putalpha(mask)
    # 枠作成
    frame = Image.new("RGBA", (size, size), (0,0,0,0))
    frame_draw = ImageDraw.Draw(frame)
    frame_draw.ellipse((0,0,size-1,size-1), outline=(255,0,0,255), width=3)  # 赤い枠
    frame.paste(img_thumb, (0,0), mask=img_thumb)
    # 保存
    buf = io.BytesIO()
    frame.save(buf, format='PNG')
    return buf.getvalue()

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
    row = {
        'filename': f['name'],
        'latitude': lat,
        'longitude': lon,
        'datetime': dt,
        'file_id': f['id']
    }
    rows.append(row)
    cached_files[f['id']] = row

with open(CACHE_FILE,'w') as f:
    json.dump(cached_files,f)

df = pd.DataFrame(rows)

# ===== GitHub更新準備 =====
g = Github(auth=Auth.Token(os.environ['GITHUB_TOKEN']))
repo = g.get_repo(REPO_NAME)

# imagesフォルダ作成
try:
    repo.get_contents(IMAGES_DIR, ref=BRANCH_NAME)
except:
    repo.create_file(f"{IMAGES_DIR}/.gitkeep", "create images dir", "", branch=BRANCH_NAME)

# 新規画像アップロード（サムネイル＋ポップアップ）
for _, row in df.iterrows():
    base_name = row['filename'].rsplit('.',1)[0] + '.png'
    remote_path = f"{IMAGES_DIR}/{base_name}"
    try:
        repo.get_contents(remote_path, ref=BRANCH_NAME)
        continue
    except:
        file_bytes = get_file_bytes(row['file_id'])
        img = pil_open_safe(file_bytes)
        if img is None: continue
        thumb_bytes = create_thumbnail_with_frame(img, size=50)  # サムネイル
        repo.create_file(remote_path, f"upload {base_name}", thumb_bytes, branch=BRANCH_NAME)

# ===== HTML生成 =====
html_lines = [
    "<!DOCTYPE html>",
    "<html><head><meta charset='utf-8'><title>Photo Map</title>",
    "<style>",
    "#map { height: 100vh; width: 100%; }",
    "img.popup-img { width: 200%; max-width: none; }",  # ポップアップで2倍表示
    ".circle-icon img { border-radius:50%; border:2px solid red; }",
    "</style>",
    "<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>",
    "<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script></head><body>",
    "<div id='map'></div><script>",
    "var map = L.map('map').setView([35.0, 138.0], 5);",
    "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19}).addTo(map);",
    "var markers = [];"]

for _, row in df.iterrows():
    if row['latitude'] and row['longitude']:
        img_name = row['filename'].rsplit('.',1)[0]+'.png'
        img_url = f"https://raw.githubusercontent.com/{REPO_NAME}/main/{IMAGES_DIR}/{img_name}"
        html_lines.append(f"""
var icon = L.icon({{iconUrl:'{img_url}', iconSize:[50,50], className:'circle-icon'}});
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img class='popup-img' src='{img_url}'/>");
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
</script></body></html>
""")

html_str = "\n".join(html_lines)

# ===== GitHub HTML更新 =====
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML with framed thumbnails", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")

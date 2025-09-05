#!/usr/bin/env python3
import os
import io
import json
import base64
from PIL import Image, ImageDraw
from pillow_heif import register_heif_opener
import exifread
from github import Github, Auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

register_heif_opener()

# ===== 設定 =====
FOLDER_ID = '1d9C_qIKxBlzngjpZjgW68kIZkPZ0NAwH'
REPO_NAME = 'K03-02/photomap'
HTML_NAME = 'index.html'
CACHE_PATH = 'cache/photomap_cache.json'
BRANCH_NAME = 'main'

# ===== Google Drive 認証 =====
token_info = json.loads(base64.b64decode(os.environ['USER_OAUTH_B64']))
creds = Credentials(
    token=token_info['token'],
    refresh_token=token_info['refresh_token'],
    token_uri=token_info['token_uri'],
    client_id=token_info['client_id'],
    client_secret=token_info.get('client_secret'),
    scopes=token_info.get('scopes')
)
drive_service = build('drive', 'v3', credentials=creds)

# ===== GitHub 認証 =====
g = Github(auth=Auth.Token(os.environ['GITHUB_TOKEN']))
repo = g.get_repo(REPO_NAME)

# ===== ヘルパー関数 =====
def list_image_files(folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    return results.get('files', [])

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
        print("⚠️ EXIF not found")
    return lat, lon, dt

def resize_and_convert_jpeg(file_bytes, size):
    """ポップアップ用: 最大サイズを size に制限してJPEGに変換"""
    img = Image.open(io.BytesIO(file_bytes))
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()

def create_icon_jpeg(file_bytes, size=240, border=16):
    """丸型＋白枠アイコンをJPEGで生成"""
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    img.thumbnail((size, size))
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, img.size[0], img.size[1]), fill=255)
    icon = Image.new("RGB", img.size, (255, 255, 255))
    icon.paste(img, mask=mask)

    if border > 0:
        border_size = (img.size[0]+border*2, img.size[1]+border*2)
        bordered = Image.new("RGB", border_size, (255, 255, 255))
        bordered.paste(icon, (border, border), mask)
        icon = bordered

    buf = io.BytesIO()
    icon.save(buf, format="JPEG", quality=90)
    return buf.getvalue()

def upload_to_github(path, content_bytes, message):
    try:
        contents = repo.get_contents(path, ref=BRANCH_NAME)
        repo.update_file(path, message, content_bytes, contents.sha, branch=BRANCH_NAME)
    except:
        repo.create_file(path, message, content_bytes, branch=BRANCH_NAME)

# ===== キャッシュ読み込み (GitHub) =====
try:
    contents = repo.get_contents(CACHE_PATH, ref=BRANCH_NAME)
    cached_files = json.loads(contents.decoded_content.decode())
    cache_sha = contents.sha
except:
    cached_files = {}
    cache_sha = None

rows = []
for f in list_image_files(FOLDER_ID):
    if f['id'] in cached_files:
        rows.append(cached_files[f['id']])
        continue

    print(f"Processing new file: {f['name']}...")
    file_bytes = drive_service.files().get_media(fileId=f['id']).execute()
    lat, lon, dt = extract_exif(file_bytes)

    base_name, _ = os.path.splitext(f['name'])
    popup_name = f"{base_name}.jpg"
    icon_name = f"{base_name}_icon.jpg"

    popup_bytes = resize_and_convert_jpeg(file_bytes, 400)
    icon_bytes = create_icon_jpeg(file_bytes, size=240, border=16)

    popup_path = f"images/{popup_name}"
    icon_path = f"images/{icon_name}"

    upload_to_github(popup_path, popup_bytes, f"Upload {popup_path}")
    upload_to_github(icon_path, icon_bytes, f"Upload {icon_path}")

    popup_url = f"https://K03-02.github.io/photomap/{popup_path}"
    icon_url = f"https://K03-02.github.io/photomap/{icon_path}"

    row = {
        'filename': f['name'],
        'latitude': lat,
        'longitude': lon,
        'datetime': dt,
        'popup_url': popup_url,
        'icon_url': icon_url
    }
    rows.append(row)
    cached_files[f['id']] = row

# ===== キャッシュ更新 (GitHub) =====
cache_bytes = json.dumps(cached_files, indent=2).encode()
if cache_sha:
    repo.update_file(CACHE_PATH, "update cache", cache_bytes, cache_sha, branch=BRANCH_NAME)
else:
    repo.create_file(CACHE_PATH, "create cache", cache_bytes, branch=BRANCH_NAME)

# ===== HTML生成 =====
html_lines = [
    "<!DOCTYPE html>",
    "<html><head><meta charset='utf-8'><title>Photo Map</title>",
    "<style>#map { height: 100vh; width: 100%; }",
    ".leaflet-popup-content img{max-width:400px;height:auto;}</style>",
    "<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>",
    "<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script></head><body>",
    "<div id='map'></div><script>",
    "var map = L.map('map').setView([35.0, 138.0], 5);",
    "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19}).addTo(map);",
]

for row in rows:
    if row['latitude'] and row['longitude'] and row['popup_url'] and row['icon_url']:
        html_lines.append(f"""
var icon = L.icon({{
    iconUrl: '{row['icon_url']}',
    iconSize: [60, 60],
    popupAnchor: [0, -30]
}});
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{row['popup_url']}'/>");
""")

html_lines.append("</script></body></html>")
html_str = "\n".join(html_lines)

# ===== GitHub HTML更新 =====
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML", html_str, contents.sha, branch=BRANCH_NAME)
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)

print("✅ HTML updated on GitHub")

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

register_heif_opener()  # HEIC対応

# ===== 設定 =====
FOLDER_ID = '1d9C_qIKxBlzngjpZjgW68kIZkPZ0NAwH'  # 元写真フォルダ
UPLOAD_FOLDER = '15UUPKFqrXl2TZBhVTVqOQuZxchEYawGE'  # 出力先フォルダ
REPO_NAME = 'K03-02/photomap'
HTML_NAME = 'index.html'
CACHE_FILE = 'photomap_cache.json'
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

# ===== ヘルパー関数 =====
def list_image_files(folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType, webViewLink)").execute()
    return results.get('files', [])

def extract_exif(file_bytes):
    lat = lon = dt = ''
    try:
        tags = exifread.process_file(io.BytesIO(file_bytes), details=False)
        if 'GPS GPSLatitude' in tags and 'GPS GPSLongitude' in tags:
            def dms_to_dd(dms, ref):
                deg = float(dms.values[0].num)/dms.values[0].den
                min_ = float(dms.values[1].num)/dms.values[1].den
                sec = float(dms.values[2].num)/dms.values[2].den
                dd = deg + min_/60 + sec/3600
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

def create_popup_jpeg(image, size=400):
    # 長辺を指定サイズに縮小
    w, h = image.size
    if w > h:
        new_w = size
        new_h = int(h * size / w)
    else:
        new_h = size
        new_w = int(w * size / h)
    return image.resize((new_w, new_h), Image.Resampling.LANCZOS)

def create_icon_jpeg(image, size=240, border_size=8):
    # 正方形トリミング（中央で切り抜き）
    w, h = image.size
    min_side = min(w, h)
    left = (w - min_side) // 2
    top = (h - min_side) // 2
    right = left + min_side
    bottom = top + min_side
    image = image.crop((left, top, right, bottom))

    # リサイズ
    image = image.resize((size, size), Image.Resampling.LANCZOS)

    # アイコン全体のキャンバスサイズ（縁を含む）
    canvas_size = size + border_size * 2
    result = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 0))

    # 白丸（枠）を描画
    mask_circle = Image.new("L", (canvas_size, canvas_size), 0)
    draw = ImageDraw.Draw(mask_circle)
    draw.ellipse((0, 0, canvas_size, canvas_size), fill=255)
    draw.ellipse((border_size, border_size, canvas_size-border_size, canvas_size-border_size), fill=0)

    border_layer = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 255))
    result.paste(border_layer, (0, 0), mask_circle)

    # 写真を円形にマスクして貼り付け
    mask_photo = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask_photo)
    draw.ellipse((0, 0, size, size), fill=255)
    result.paste(image, (border_size, border_size), mask_photo)

    return result

# ===== キャッシュ読み込み =====
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE,'r') as f:
        cached_files = json.load(f)
else:
    cached_files = {}

rows = []
for f in list_image_files(FOLDER_ID):
    if f['id'] in cached_files and "icon_url" in cached_files[f['id']]:
        rows.append(cached_files[f['id']])
        continue

    print(f"Processing new file: {f['name']}...")
    file_bytes = drive_service.files().get_media(fileId=f['id']).execute()
    lat, lon, dt = extract_exif(file_bytes)

    image = Image.open(io.BytesIO(file_bytes)).convert("RGB")

    base_name, _ = os.path.splitext(f['name'])
    popup_name = f"{base_name}_popup.jpg"
    icon_name = f"{base_name}_icon.jpg"

    # ポップアップ画像
    popup_img = create_popup_jpeg(image, 400)
    popup_path = f"images/{popup_name}"
    popup_img.save(popup_path, "JPEG", quality=85)

    # アイコン画像
    icon_img = create_icon_jpeg(image, size=240, border_size=8)
    icon_path = f"images/{icon_name}"
    icon_img.save(icon_path, "JPEG", quality=90)

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

# キャッシュ更新
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
    if row['latitude'] and row['longitude'] and row['popup_url'] and row['icon_url']:
        html_lines.append(f"""
var icon = L.icon({{
    iconUrl: '{row['icon_url']}',
    iconSize: [60, 60],
    className: 'custom-icon'
}});
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{row['popup_url']}' style='max-width:400px; height:auto;'/>");
""")

html_lines.append("</script></body></html>")

html_str = "\n".join(html_lines)

# ===== GitHub更新 =====
g = Github(auth=Auth.Token(os.environ['GITHUB_TOKEN']))
repo = g.get_repo(REPO_NAME)
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML with new images", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")

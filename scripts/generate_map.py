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
FOLDER_ID = '1d9C_qIKxBlzngjpZjgW68kIZkPZ0NAwH'
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
    query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
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
        print(f"⚠️ EXIF not found")
    return lat, lon, dt

def heic_to_jpeg(file_bytes, max_width):
    """HEIC→JPEG変換しリサイズ"""
    with Image.open(io.BytesIO(file_bytes)) as im:
        im.thumbnail((max_width, max_width))
        buf = io.BytesIO()
        im.convert('RGB').save(buf, format='JPEG', quality=85)
        return buf.getvalue(), im  # bytes, PIL.Imageオブジェクト

def create_highres_icon(image, size=240, border=16):
    """高解像度丸型アイコン作成（正方形トリミング＋白枠）"""
    # 正方形トリミング（中心）
    w, h = image.size
    min_side = min(w, h)
    left = (w - min_side)//2
    top = (h - min_side)//2
    square = image.crop((left, top, left+min_side, top+min_side))

    # 高解像度リサイズ
    icon = square.resize((size, size), Image.LANCZOS)

    # 白背景
    bg = Image.new("RGB", icon.size, (255,255,255))

    # 丸型マスク
    mask = Image.new("L", icon.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,size,size), fill=255)
    bg.paste(icon, (0,0), mask)

    # 白枠追加
    size_with_border = (icon.size[0]+border*2, icon.size[1]+border*2)
    final_icon = Image.new("RGB", size_with_border, (255,255,255))
    final_icon.paste(bg, (border,border))

    # JPEG変換
    buf = io.BytesIO()
    final_icon.save(buf, format='JPEG', quality=85)
    return buf.getvalue(), final_icon

def upload_to_github(repo, branch, path, file_bytes, commit_msg):
    try:
        contents = repo.get_contents(path, ref=branch)
        repo.update_file(path, commit_msg, file_bytes, contents.sha, branch=branch)
    except:
        repo.create_file(path, commit_msg, file_bytes, branch=branch)

# ===== キャッシュ読み込み =====
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE,'r') as f:
        cached_files = json.load(f)
else:
    cached_files = {}

# ===== GitHub認証 =====
g = Github(auth=Auth.Token(os.environ['GITHUB_TOKEN']))
repo = g.get_repo(REPO_NAME)

rows = []
for f in list_image_files(FOLDER_ID):
    if f['id'] in cached_files:
        rows.append(cached_files[f['id']])
        continue
    print(f"Processing new file: {f['name']}...")
    file_bytes = drive_service.files().get_media(fileId=f['id']).execute()
    lat, lon, dt = extract_exif(file_bytes)
    
    # ポップアップ用JPEG 400px
    popup_bytes, popup_image = heic_to_jpeg(file_bytes, 400)
    popup_name = f['name'].replace('.HEIC','.jpg')
    popup_path = f"images/{popup_name}"
    upload_to_github(repo, BRANCH_NAME, popup_path, popup_bytes, f"Upload {popup_path}")
    
    # マーカーアイコン 60px表示（高解像度240pxで作成）
    icon_bytes, _ = create_highres_icon(popup_image, size=240, border=16)
    icon_name = f['name'].replace('.HEIC','_icon.jpg')
    icon_path = f"images/{icon_name}"
    upload_to_github(repo, BRANCH_NAME, icon_path, icon_bytes, f"Upload {icon_path}")
    
    # GitHub Pages URL
    base_url = f"https://K03-02.github.io/photomap/images/"
    row = {
        'filename': f['name'],
        'latitude': lat,
        'longitude': lon,
        'datetime': dt,
        'popup_url': base_url + popup_name,
        'icon_url': base_url + icon_name
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
    if row['latitude'] and row['longitude']:
        html_lines.append(f"""
var customIcon = L.icon({{
    iconUrl: '{row['icon_url']}',
    iconSize: [60, 60]
}});
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: customIcon}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{row['popup_url']}' width='400'/>");
""")

html_lines.append("</script></body></html>")

html_str = "\n".join(html_lines)

# ===== HTML更新 =====
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML with new images", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")

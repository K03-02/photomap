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
CACHE_FILE = 'photomap_cache.json'
BRANCH_NAME = 'main'
IMAGES_DIR = 'images'

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

def upload_file_to_github(local_bytes, path, commit_msg):
    """GitHubにファイルをアップロード"""
    try:
        contents = repo.get_contents(path, ref=BRANCH_NAME)
        repo.update_file(path, commit_msg, local_bytes, contents.sha, branch=BRANCH_NAME)
    except:
        repo.create_file(path, commit_msg, local_bytes, branch=BRANCH_NAME)
    return f"https://{os.environ.get('GITHUB_USER','K03-02')}.github.io/photomap/{path}"

# ===== ポップアップ用JPEG作成 =====
def create_popup_jpeg(image, size=800):
    w, h = image.size
    if w > h:
        new_w = size
        new_h = int(h * size / w)
    else:
        new_h = size
        new_w = int(w * size / h)
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    with io.BytesIO() as output:
        resized.convert("RGB").save(output, "JPEG", quality=85)
        return output.getvalue()

# ===== 白枠丸アイコン生成（PNG透過→白背景JPEG化） =====
def create_round_icon_png_then_jpeg(image, final_diameter=60, border_thickness=6, base_size=240):
    # 1. 中央クロップ + リサイズ
    w, h = image.size
    min_side = min(w, h)
    left = (w - min_side)//2
    top = (h - min_side)//2
    square = image.crop((left, top, left+min_side, top+min_side))
    square = square.resize((base_size, base_size), Image.Resampling.LANCZOS)

    # 2. 透過PNGキャンバス
    canvas_size = base_size + 2*border_thickness
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0,0,0,0))

    # 3. 白丸枠を描く
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((0,0,canvas_size,canvas_size), fill=(255,255,255,255))

    # 4. 写真を丸く貼る
    mask = Image.new("L", (base_size, base_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0,0,base_size,base_size), fill=255)
    canvas.paste(square, (border_thickness,border_thickness), mask)

    # 5. JPEG化（白背景に合成）
    bg = Image.new("RGB", (canvas_size, canvas_size), (255,255,255))
    bg.paste(canvas, mask=canvas.split()[3])  # alphaチャンネルをマスクとして使用
    icon_jpeg = bg.resize((final_diameter, final_diameter), Image.Resampling.LANCZOS)

    with io.BytesIO() as output:
        icon_jpeg.save(output, "JPEG", quality=90)
        return output.getvalue()

# ===== キャッシュ読み込み =====
try:
    contents = repo.get_contents(CACHE_FILE, ref=BRANCH_NAME)
    cached_files = json.loads(contents.decoded_content.decode())
except:
    cached_files = {}

rows = []
for f in list_image_files(FOLDER_ID):
    if f['id'] in cached_files:
        rows.append(cached_files[f['id']])
        continue

    print(f"Processing new file: {f['name']}...")
    try:
        file_bytes = drive_service.files().get_media(fileId=f['id']).execute()
    except Exception as e:
        print(f"⚠️ Skipped {f['name']}: {e}")
        continue

    lat, lon, dt = extract_exif(file_bytes)
    image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    base_name, _ = os.path.splitext(f['name'])
    popup_path = f"{IMAGES_DIR}/{base_name}_popup.jpg"
    icon_path = f"{IMAGES_DIR}/{base_name}_icon.jpg"

    # ポップアップJPEG
    popup_bytes = create_popup_jpeg(image, 800)
    popup_url = upload_file_to_github(popup_bytes, popup_path, f"Upload popup {base_name}")

    # 白枠丸アイコン
    icon_bytes = create_round_icon_png_then_jpeg(image, final_diameter=60, border_thickness=6, base_size=240)
    icon_url = upload_file_to_github(icon_bytes, icon_path, f"Upload round icon {base_name}")

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

# キャッシュをGitHubに保存
upload_file_to_github(json.dumps(cached_files), CACHE_FILE, "Update photomap cache")

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
var icon = L.icon({{
    iconUrl: '{row['icon_url']}',
    iconSize: [60, 60],
    className: 'custom-icon'
}});
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{row['popup_url']}' style='max-width:800px; width:100%; height:auto;'/>");
""")

html_lines.append("</script></body></html>")

html_str = "\n".join(html_lines)
upload_file_to_github(html_str, HTML_NAME, "Update HTML with round icons and large popups")
print("HTML updated on GitHub with fully round white-border icons and large popups.")

import os
import io
import json
import base64
import pandas as pd
from googleapiclient.discovery import build
from google.oauth2 import service_account
from PIL import Image, ImageDraw
import pyheif
import exifread
from github import Github

# ==== 環境変数 ====
BRANCH_NAME = os.getenv("BRANCH_NAME", "gh-pages")
FOLDER_ID = os.getenv("FOLDER_ID")
HTML_NAME = os.getenv("HTML_NAME", "index.html")
REPO_NAME = os.getenv("REPO_NAME")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# ==== Service Account JSON 読み込み ====
drive_credentials_json = os.getenv("DRIVE_CREDENTIALS_JSON")
service_account_info = json.loads(drive_credentials_json)
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/drive.readonly"]
)

# ==== Google Drive API 初期化 ====
drive_service = build("drive", "v3", credentials=credentials)

# ==== GitHub 認証 ====
g = Github(GITHUB_TOKEN)
repo = g.get_repo(REPO_NAME)

# ==== キャッシュファイル ====
CACHE_FILE = "processed_files.json"
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        processed_files = set(json.load(f))
else:
    processed_files = set()

# ==== ヘルパー関数 ====
def get_file_bytes(file_id):
    fh = io.BytesIO()
    request = drive_service.files().get_media(fileId=file_id)
    downloader = build("drive", "v3", credentials=credentials)._http.request(request.uri)
    return fh.getvalue()

def pil_open_safe(file_bytes, mime_type):
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            return Image.frombytes(
                heif_file.mode,
                heif_file.size,
                heif_file.data,
                "raw",
                heif_file.mode
            )
        else:
            return Image.open(io.BytesIO(file_bytes))
    except Exception as e:
        print(f"⚠️ Cannot open image: {e}")
        return None

def extract_exif(file_bytes, mime_type):
    lat, lon, dt = None, None, None
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
    except Exception as e:
        print(f"⚠️ Cannot extract EXIF: {e}")
    return lat, lon, dt

# ==== 画像一覧取得 ====
results = drive_service.files().list(
    q=f"'{FOLDER_ID}' in parents and (mimeType contains 'image/')",
    fields="files(id, name, mimeType, modifiedTime)"
).execute()
files = results.get("files", [])

rows = []
for f in files:
    if f["id"] in processed_files:
        continue
    print(f"Processing {f['name']}...")
    file_bytes = get_file_bytes(f["id"])
    lat, lon, dt = extract_exif(file_bytes, f["mimeType"])
    if lat and lon:
        rows.append({"filename": f["name"], "lat": lat, "lon": lon, "datetime": dt})
    processed_files.add(f["id"])

# ==== キャッシュ保存 ====
with open(CACHE_FILE, "w") as f:
    json.dump(list(processed_files), f)

# ==== HTML 作成 ====
html_lines = [
    "<!DOCTYPE html>",
    "<html><head><meta charset='utf-8'><title>Photo Map</title>",
    "<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>",
    "<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>",
    "</head><body><div id='map' style='height:100vh;'></div><script>",
    "var map = L.map('map').setView([35,135], 5);",
    "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19}).addTo(map);"
]

for row in rows:
    html_lines.append(f"L.marker([{row['lat']},{row['lon']}]).addTo(map).bindPopup('<b>{row['filename']}</b><br>{row['datetime']}');")

html_lines.append("</script></body></html>")
html_content = "\n".join(html_lines)

# ==== GitHub Pages へアップロード ====
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(contents.path, "Update map", html_content, contents.sha, branch=BRANCH_NAME)
except:
    repo.create_file(HTML_NAME, "Create map", html_content, branch=BRANCH_NAME)

print("✅ HTML updated on GitHub Pages")

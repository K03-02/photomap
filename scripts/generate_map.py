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
DRIVE_CREDENTIALS_JSON = os.getenv("DRIVE_CREDENTIALS_JSON")

# ==== Google Drive 認証 ====
service_account_info = json.loads(DRIVE_CREDENTIALS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/drive.readonly"]
)
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

# ==== HEIC → PIL ====
def pil_open_safe(file_bytes, mime_type):
    try:
        if "heic" in mime_type.lower() or "heif" in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            img = Image.frombytes(
                heif_file.mode,
                heif_file.size,
                heif_file.data,
                "raw",
                heif_file.mode
            )
        else:
            img = Image.open(io.BytesIO(file_bytes))
        return img
    except Exception as e:
        print(f"⚠️ Cannot open image: {e}")
        return None

# ==== EXIF 取得 ====
def extract_exif(file_bytes, mime_type):
    try:
        img = pil_open_safe(file_bytes, mime_type)
        if img is None:
            return None, None, None
        f_like = io.BytesIO(file_bytes)
        tags = exifread.process_file(f_like, details=False)
        if "GPS GPSLatitude" in tags and "GPS GPSLongitude" in tags:
            lat = convert_to_degrees(tags["GPS GPSLatitude"])
            lon = convert_to_degrees(tags["GPS GPSLongitude"])
            if str(tags.get("GPS GPSLatitudeRef")) == "S":
                lat = -lat
            if str(tags.get("GPS GPSLongitudeRef")) == "W":
                lon = -lon
        else:
            lat = lon = None
        dt = str(tags.get("EXIF DateTimeOriginal", ""))
        return lat, lon, dt
    except Exception as e:
        print(f"⚠️ Cannot extract EXIF: {e}")
        return None, None, None

def convert_to_degrees(value):
    d = float(value.values[0].num) / float(value.values[0].den)
    m = float(value.values[1].num) / float(value.values[1].den)
    s = float(value.values[2].num) / float(value.values[2].den)
    return d + m/60.0 + s/3600.0

# ==== Google Drive ファイル取得 ====
results = drive_service.files().list(
    q=f"'{FOLDER_ID}' in parents and (mimeType contains 'image/')",
    fields="files(id,name,mimeType)"
).execute()

files = results.get("files", [])

rows = []

for f in files:
    if f["id"] in processed_files:
        continue
    print(f"Processing {f['name']}...")
    try:
        request = drive_service.files().get_media(fileId=f["id"])
        fh = io.BytesIO()
        downloader = build("drive", "v3", credentials=credentials)._http.request(request.uri)
        file_bytes = fh.getvalue()
        lat, lon, dt = extract_exif(file_bytes, f["mimeType"])
        if lat and lon:
            rows.append({"filename": f["name"], "lat": lat, "lon": lon, "datetime": dt})
        processed_files.add(f["id"])
    except Exception as e:
        print(f"⚠️ Skip {f['name']} ({e})")

# ==== キャッシュ保存 ====
with open(CACHE_FILE, "w") as f:
    json.dump(list(processed_files), f)

# ==== HTML 出力 ====
html_content = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Photo Map</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
</head><body><div id='map' style='height:100vh;'></div><script>
var map = L.map('map').setView([35,135],5);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);
"""

for row in rows:
    html_content += f"L.marker([{row['lat']},{row['lon']}]).addTo(map).bindPopup('<b>{row['filename']}</b><br>{row['datetime']}');\n"

html_content += "</script></body></html>"

# ==== GitHub Pages アップロード ====
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(contents.path, "Update map", html_content, contents.sha, branch=BRANCH_NAME)
except:
    repo.create_file(HTML_NAME, "Create map", html_content, branch=BRANCH_NAME)

print("✅ HTML updated on GitHub Pages")

import os
import io
import json
import base64
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from PIL import Image
import pyheif
import exifread
from github import Github

# === 環境変数 ===
FOLDER_ID = os.environ["FOLDER_ID"]
SERVICE_ACCOUNT_B64 = os.environ["SERVICE_ACCOUNT_B64"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO_NAME = os.environ["REPO_NAME"]
HTML_NAME = os.environ.get("HTML_NAME", "index.html")
BRANCH_NAME = os.environ.get("BRANCH_NAME", "main")
CACHE_FILE = os.environ.get("CACHE_FILE", "photomap_cache.json")

# === Google Drive 認証 ===
creds_json = base64.b64decode(SERVICE_ACCOUNT_B64).decode("utf-8")
creds = service_account.Credentials.from_service_account_info(json.loads(creds_json))
drive_service = build("drive", "v3", credentials=creds)

# === GitHub 認証 ===
gh = Github(GITHUB_TOKEN)
repo = gh.get_repo(REPO_NAME)


def list_image_files(folder_id):
    """Google Drive から静止画だけ取得 (HEIC, JPEG, PNG)"""
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get("files", [])
    return [
        f for f in files
        if f["mimeType"].lower() in ("image/heic", "image/jpeg", "image/png")
    ]


def get_exif_data(image_bytes):
    """Exif からGPSと日時を抽出"""
    tags = exifread.process_file(io.BytesIO(image_bytes), details=False)
    lat, lon = None, None
    dt = None

    if "GPS GPSLatitude" in tags and "GPS GPSLongitude" in tags:
        lat_values = tags["GPS GPSLatitude"].values
        lon_values = tags["GPS GPSLongitude"].values
        lat_ref = tags.get("GPS GPSLatitudeRef", "N").values
        lon_ref = tags.get("GPS GPSLongitudeRef", "E").values

        lat = float(lat_values[0].num) / float(lat_values[0].den) + \
              float(lat_values[1].num) / float(lat_values[1].den) / 60 + \
              float(lat_values[2].num) / float(lat_values[2].den) / 3600
        lon = float(lon_values[0].num) / float(lon_values[0].den) + \
              float(lon_values[1].num) / float(lon_values[1].den) / 60 + \
              float(lon_values[2].num) / float(lon_values[2].den) / 3600

        if lat_ref == "S":
            lat = -lat
        if lon_ref == "W":
            lon = -lon

    if "EXIF DateTimeOriginal" in tags:
        dt = str(tags["EXIF DateTimeOriginal"])

    return lat, lon, dt


def heic_to_jpeg_bytes(file_bytes):
    """HEIC → JPEG 変換"""
    heif_file = pyheif.read_heif(file_bytes)
    image = Image.frombytes(
        heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode, heif_file.stride
    )
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def process_file(file_id, filename):
    """1枚の画像を処理"""
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    file_bytes = fh.getvalue()
    if filename.lower().endswith(".heic"):
        jpeg_bytes = heic_to_jpeg_bytes(file_bytes)
    else:
        jpeg_bytes = file_bytes

    lat, lon, dt = get_exif_data(file_bytes)
    if not lat or not lon:
        return None

    # サムネイル作成
    img = Image.open(io.BytesIO(jpeg_bytes))
    img.thumbnail((400, 400))
    thumb_buf = io.BytesIO()
    img.save(thumb_buf, format="PNG")
    thumb_b64 = base64.b64encode(thumb_buf.getvalue()).decode("utf-8")

    return {
        "name": filename,
        "lat": lat,
        "lon": lon,
        "datetime": dt,
        "thumbnail": thumb_b64,
    }


def generate_html(photo_data):
    """LeafletマップHTMLを生成"""
    markers_js = ""
    for photo in photo_data:
        markers_js += f"""
var iconDiv = document.createElement('div');
iconDiv.style.width = '50px';
iconDiv.style.height = '50px';
iconDiv.style.borderRadius = '50%';
iconDiv.style.backgroundImage = "url('data:image/png;base64,{photo['thumbnail']}')";
iconDiv.style.backgroundSize = 'cover';

var marker = L.marker([{photo['lat']},{photo['lon']}], {{
    icon: L.divIcon({{className:'photo-marker', html: iconDiv.outerHTML}}),
    riseOnHover: true
}}).addTo(map);
markers.push(marker);
bounds.extend([{photo['lat']},{photo['lon']}]);
marker.bindPopup("<b>{photo['name']}</b><br>{photo['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={photo['lat']},{photo['lon']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='data:image/png;base64,{photo['thumbnail']}' width='200'/>");
"""

    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Photo Map</title>
<style>#map {{ height: 100vh; width: 100%; }}</style>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script></head><body>
<div id='map'></div><script>
var map = L.map('map').setView([35.0, 138.0], 5);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19}}).addTo(map);
var markers = [];
var bounds = L.latLngBounds();
{markers_js}
if (!bounds.isEmpty()) {{ map.fitBounds(bounds.pad(0.2)); }}

map.on('zoomend', function(){{
    var zoom = map.getZoom();
    var scale = Math.min(zoom/5, 1.2);
    document.querySelectorAll('.photo-marker div').forEach(function(div){{
        var size = 50 * scale;
        if(size>60){{ size=60; }}
        div.style.width = size + 'px';
        div.style.height = size + 'px';
    }});
}});
</script></body></html>"""


def main():
    # キャッシュ読み込み
    try:
        cache_content = repo.get_contents(CACHE_FILE, ref=BRANCH_NAME)
        cache = json.loads(cache_content.decoded_content.decode())
    except Exception:
        cache = {}

    photo_data = []
    updated_cache = {}

    files = list_image_files(FOLDER_ID)
    for f in files:
        file_id, filename = f["id"], f["name"]
        cache_key = f"{file_id}_{filename}"
        if cache_key in cache:
            photo_data.append(cache[cache_key])
            updated_cache[cache_key] = cache[cache_key]
            continue

        processed = process_file(file_id, filename)
        if processed:
            photo_data.append(processed)
            updated_cache[cache_key] = processed
            print(f"✅ {filename} processed")
        else:
            print(f"⚠️ {filename} skipped (no GPS)")

    if not photo_data:
        print("❌ 写真に位置情報がありません")
        exit(1)

    html_content = generate_html(photo_data)

    # HTMLアップロード
    try:
        contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
        repo.update_file(contents.path, "update map", html_content, contents.sha, branch=BRANCH_NAME)
    except Exception:
        repo.create_file(HTML_NAME, "create map", html_content, branch=BRANCH_NAME)

    # キャッシュアップロード
    try:
        contents = repo.get_contents(CACHE_FILE, ref=BRANCH_NAME)
        repo.update_file(contents.path, "update cache", json.dumps(updated_cache), contents.sha, branch=BRANCH_NAME)
    except Exception:
        repo.create_file(CACHE_FILE, "create cache", json.dumps(updated_cache), branch=BRANCH_NAME)

    print("✅ HTML uploaded to GitHub Pages")

for fn in os.listdir(PHOTO_DIR):
    print("📷 Found file:", fn)


if __name__ == "__main__":
    main()

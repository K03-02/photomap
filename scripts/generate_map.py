import os
import io
import json
import base64
from PIL import Image, ImageDraw, UnidentifiedImageError
import pyheif
import exifread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from github import Github

# ========= 設定 =========
SERVICE_ACCOUNT_JSON = os.environ["SERVICE_ACCOUNT_JSON"]
FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]
OUTPUT_HTML = "index.html"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ===== JSON を一時ファイルに書き出す =====
SERVICE_ACCOUNT_FILE = "service_account.json"
with open(SERVICE_ACCOUNT_FILE, "w", encoding="utf-8") as f:
    f.write(SERVICE_ACCOUNT_JSON)

# ========= Google Driveサービス =========
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)

def list_photos():
    service = get_drive_service()
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and (mimeType contains 'image/')",
        fields="files(id, name, mimeType)"
    ).execute()
    return results.get("files", [])

def download_file(file_id):
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read()

# ========= 画像処理 =========
def heic_to_pil(file_bytes):
    heif_file = pyheif.read_heif(file_bytes)
    return Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)

def open_image(file_bytes, mime_type):
    try:
        if "heic" in mime_type.lower():
            return heic_to_pil(file_bytes)
        else:
            return Image.open(io.BytesIO(file_bytes))
    except (UnidentifiedImageError, Exception):
        return None

def make_thumbnail_circle(image, size=(80, 80)):
    im = image.copy()
    im.thumbnail(size)
    mask = Image.new("L", im.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, im.size[0], im.size[1]), fill=255)
    result = Image.new("RGBA", im.size)
    result.paste(im, (0, 0), mask=mask)
    return result

def to_base64(image, format="JPEG"):
    buf = io.BytesIO()
    image.save(buf, format=format)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

# ========= EXIF GPS =========
def get_lat_lon(file_bytes):
    try:
        tags = exifread.process_file(io.BytesIO(file_bytes), details=False)
        gps_lat = tags["GPS GPSLatitude"]
        gps_lat_ref = tags["GPS GPSLatitudeRef"].printable
        gps_lon = tags["GPS GPSLongitude"]
        gps_lon_ref = tags["GPS GPSLongitudeRef"].printable

        def to_deg(value):
            d, m, s = [float(x.num) / float(x.den) for x in value.values]
            return d + (m / 60.0) + (s / 3600.0)

        lat = to_deg(gps_lat)
        if gps_lat_ref != "N":
            lat = -lat
        lon = to_deg(gps_lon)
        if gps_lon_ref != "E":
            lon = -lon
        return lat, lon
    except Exception:
        return None, None

# ========= HTML生成 =========
def generate_html(photo_data):
    markers_js = ""
    for lat, lon, thumb_b64, full_b64 in photo_data:
        map_link = f"https://www.google.com/maps?q={lat},{lon}"
        markers_js += f"""
        var icon = L.icon({{
            iconUrl: 'data:image/png;base64,{thumb_b64}',
            iconSize: [50, 50],
            className: 'circle-icon'
        }});
        L.marker([{lat},{lon}], {{icon: icon}})
            .addTo(map)
            .bindPopup("<img src='data:image/jpeg;base64,{full_b64}' style='max-width:300px;'><br><a href='{map_link}' target='_blank'>📍 Googleマップで見る</a>");
        """

    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Photo Map</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<style>
.circle-icon {{
  border-radius: 50%;
}}
</style>
</head><body><div id='map' style='height:100vh;'></div><script>
var map = L.map('map').setView([35,135], 5);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19}}).addTo(map);
{markers_js}
</script></body></html>
"""

# ========= メイン =========
def main():
    files = list_photos()
    photo_data = []

    for f in files:
        print(f"Processing {f['name']}...")
        file_bytes = download_file(f["id"])
        image = open_image(file_bytes, f["mimeType"])
        if not image:
            print(f"⚠️ Cannot open {f['name']}")
            continue
        lat, lon = get_lat_lon(file_bytes)
        if lat is None or lon is None:
            print(f"⚠️ No GPS info in {f['name']}")
            continue
        thumb = make_thumbnail_circle(image)
        thumb_b64 = to_base64(thumb, format="PNG")
        full_b64 = to_base64(image, format="JPEG")
        photo_data.append((lat, lon, thumb_b64, full_b64))

    html = generate_html(photo_data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ HTML generated")

    # GitHub Pages へアップロード
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)
    try:
        contents = repo.get_contents(OUTPUT_HTML)
        repo.update_file(contents.path, "Update map", html, contents.sha)
    except Exception:
        repo.create_file(OUTPUT_HTML, "Create map", html)
    print("✅ Uploaded to GitHub")

if __name__ == "__main__":
    main()

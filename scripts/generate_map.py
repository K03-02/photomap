import os
import io
import base64
from PIL import Image, ImageDraw, UnidentifiedImageError
import pyheif
import exifread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from github import Github

# ===== 設定 =====
SERVICE_ACCOUNT_FILE = os.environ["SERVICE_ACCOUNT_FILE"]
FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]
OUTPUT_HTML = "index.html"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ===== Google Drive API =====
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)

def list_photos():
    service = get_drive_service()
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and (mimeType contains 'image/') and trashed=false",
        fields="files(id, name, mimeType)"
    ).execute()
    return results.get("files", [])

def download_file(file_id):
    service = get_drive_service()
    fh = io.BytesIO()
    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read()

# ===== 画像処理 =====
def open_image(file_bytes, mime_type):
    try:
        if "heic" in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            return Image.frombytes(
                heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode
            )
        else:
            return Image.open(io.BytesIO(file_bytes))
    except UnidentifiedImageError:
        return None

def make_thumbnail_circle(image, size=(80,80)):
    im = image.copy()
    im.thumbnail(size)
    mask = Image.new("L", im.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,im.size[0],im.size[1]), fill=255)
    result = Image.new("RGBA", im.size)
    result.paste(im, (0,0), mask=mask)
    return result

def to_base64(img, fmt="JPEG"):
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

# ===== EXIF から GPS 抽出 =====
def get_lat_lon(file_bytes, mime_type):
    try:
        if "heic" in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            pil_img = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
            fbuf = io.BytesIO()
            pil_img.save(fbuf, format="JPEG")
            fbuf.seek(0)
            tags = exifread.process_file(fbuf, details=False)
        else:
            tags = exifread.process_file(io.BytesIO(file_bytes), details=False)

        if "GPS GPSLatitude" in tags and "GPS GPSLongitude" in tags:
            lat_ref = tags["GPS GPSLatitudeRef"].printable
            lon_ref = tags["GPS GPSLongitudeRef"].printable
            lat_vals = [float(x.num)/float(x.den) for x in tags["GPS GPSLatitude"].values]
            lon_vals = [float(x.num)/float(x.den) for x in tags["GPS GPSLongitude"].values]
            lat = lat_vals[0]+lat_vals[1]/60+lat_vals[2]/3600
            lon = lon_vals[0]+lon_vals[1]/60+lon_vals[2]/3600
            if lat_ref != "N":
                lat = -lat
            if lon_ref != "E":
                lon = -lon
            return lat, lon
    except Exception:
        pass
    return None, None

# ===== HTML生成 =====
def generate_html(photo_data):
    markers_js = ""
    for lat, lon, thumb_b64, full_b64, filename in photo_data:
        map_link = f"https://www.google.com/maps?q={lat},{lon}"
        markers_js += f"""
var icon = L.icon({{iconUrl:'data:image/png;base64,{thumb_b64}', iconSize:[50,50]}});
L.marker([{lat},{lon}], {{icon: icon}})
  .addTo(map)
  .bindPopup("<b>{filename}</b><br><img src='data:image/jpeg;base64,{full_b64}' style='max-width:300px;'><br><a href='{map_link}' target='_blank'>📍 Googleマップで見る</a>");
"""
    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Photo Map</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<style>.leaflet-marker-icon {{ border-radius:50%; }}</style>
</head><body>
<div id='map' style='height:100vh;'></div>
<script>
var map = L.map('map').setView([35,135],5);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19}}).addTo(map);
{markers_js}
</script></body></html>
"""

# ===== メイン =====
def main():
    files = list_photos()
    photo_data = []

    for f in files:
        print(f"Processing {f['name']}...")
        file_bytes = download_file(f["id"])
        lat, lon = get_lat_lon(file_bytes, f["mimeType"])
        if lat is None or lon is None:
            print(f"⚠️ GPSなし {f['name']}")
            continue
        img = open_image(file_bytes, f["mimeType"])
        if img is None:
            print(f"⚠️ 画像開けない {f['name']}")
            continue
        thumb = make_thumbnail_circle(img)
        thumb_b64 = to_base64(thumb, fmt="PNG")
        full_b64 = to_base64(img, fmt="JPEG")
        photo_data.append((lat, lon, thumb_b64, full_b64, f["name"]))

    html = generate_html(photo_data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ HTML生成完了")

    # GitHub Pages へ
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)
    try:
        contents = repo.get_contents(OUTPUT_HTML)
        repo.update_file(contents.path, "Update Photo Map", html, contents.sha)
    except Exception:
        repo.create_file(OUTPUT_HTML, "Create Photo Map", html)
    print("✅ GitHub更新完了")

if __name__ == "__main__":
    main()

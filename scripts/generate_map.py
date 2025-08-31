import os
import base64
import io
from PIL import Image, ImageDraw, UnidentifiedImageError
import pyheif
import exifread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
import pandas as pd

# ========= 設定 =========
SERVICE_ACCOUNT_FILE = os.environ["SERVICE_ACCOUNT_FILE"]
FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
OUTPUT_HTML = "output/index.html"  # GitHub Pages 用に output フォルダに生成

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ========= Google Drive API =========
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
    fh = io.BytesIO()
    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read()

# ========= EXIF 解析 =========
def get_lat_lon(file_bytes, mime_type):
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            img = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            buf.seek(0)
            tags = exifread.process_file(buf, details=False)
        else:
            tags = exifread.process_file(io.BytesIO(file_bytes), details=False)

        if "GPS GPSLatitude" in tags and "GPS GPSLongitude" in tags:
            def dms_to_dd(dms, ref):
                deg = float(dms.values[0].num)/dms.values[0].den
                min = float(dms.values[1].num)/dms.values[1].den
                sec = float(dms.values[2].num)/dms.values[2].den
                dd = deg + min/60 + sec/3600
                if ref.values not in ["N","E"]:
                    dd = -dd
                return dd
            lat = dms_to_dd(tags["GPS GPSLatitude"], tags["GPS GPSLatitudeRef"])
            lon = dms_to_dd(tags["GPS GPSLongitude"], tags["GPS GPSLongitudeRef"])
            return lat, lon
    except:
        return None, None
    return None, None

# ========= 画像処理 =========
def open_image(file_bytes, mime_type):
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            return Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
        else:
            return Image.open(io.BytesIO(file_bytes))
    except:
        return None

def make_circle_icon(img, size=(80,80)):
    im = img.copy()
    im.thumbnail(size)
    mask = Image.new("L", im.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,im.size[0],im.size[1]), fill=255)
    out = Image.new("RGBA", im.size)
    out.paste(im, (0,0), mask)
    return out

def to_base64(img, fmt="JPEG"):
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()

# ========= HTML 生成 =========
def generate_html(photo_data):
    markers_js = ""
    for lat, lon, thumb_b64, full_b64, name in photo_data:
        map_link = f"https://www.google.com/maps?q={lat},{lon}"
        markers_js += f"""
        var icon = L.icon({{
            iconUrl: 'data:image/png;base64,{thumb_b64}',
            iconSize: [50,50],
            className: 'circle-icon'
        }});
        L.marker([{lat},{lon}], {{icon: icon}})
          .addTo(map)
          .bindPopup("<b>{name}</b><br><img src='data:image/jpeg;base64,{full_b64}' style='max-width:300px;'><br><a href='{map_link}' target='_blank'>📍 Googleマップで見る</a>");
        """
    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Photo Map</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<style>.circle-icon{{border-radius:50%;}}</style>
</head><body><div id='map' style='height:100vh;'></div>
<script>
var map = L.map('map').setView([35,135],5);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19}}).addTo(map);
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
        lat, lon = get_lat_lon(file_bytes, f["mimeType"])
        if lat is None or lon is None:
            print(f"⚠️ GPSなし: {f['name']}")
            continue
        img = open_image(file_bytes, f["mimeType"])
        if img is None:
            print(f"⚠️ 画像開けず: {f['name']}")
            continue
        thumb = make_circle_icon(img)
        photo_data.append((lat, lon, to_base64(thumb,"PNG"), to_base64(img,"JPEG"), f["name"]))

    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(generate_html(photo_data))
    print("✅ HTML生成完了")

if __name__ == "__main__":
    main()

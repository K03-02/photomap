import os
import io
import base64
import json
from PIL import Image, UnidentifiedImageError
import pyheif
import exifread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account

# 環境変数からサービスアカウント情報を取得
SERVICE_ACCOUNT_B64 = os.environ["SERVICE_ACCOUNT_B64"]
SERVICE_ACCOUNT_INFO = json.loads(base64.b64decode(SERVICE_ACCOUNT_B64))
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        SERVICE_ACCOUNT_INFO, scopes=SCOPES
    )
    return build('drive', 'v3', credentials=creds)

def list_photos():
    service = get_drive_service()
    results = service.files().list(
        q="mimeType contains 'image/'",
        pageSize=100,
        fields="files(id, name)"
    ).execute()
    return results.get('files', [])

def download_file(file_id, file_name):
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    file_path = os.path.join("publish", file_name)
    with open(file_path, 'wb') as f:
        f.write(fh.read())
    return file_path

def create_thumbnail(file_path, thumb_path, size=(128,128)):
    try:
        if file_path.lower().endswith(".heic"):
            heif_file = pyheif.read(file_path)
            image = Image.frombytes(
                heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode
            )
        else:
            image = Image.open(file_path)
        image.thumbnail(size)
        image.save(thumb_path)
        return thumb_path
    except UnidentifiedImageError:
        print(f"Cannot open {file_path}")
        return None

def extract_gps(file_path):
    try:
        with open(file_path, 'rb') as f:
            tags = exifread.process_file(f, details=False)
        gps_lat = tags.get('GPS GPSLatitude')
        gps_lat_ref = tags.get('GPS GPSLatitudeRef')
        gps_lon = tags.get('GPS GPSLongitude')
        gps_lon_ref = tags.get('GPS GPSLongitudeRef')

        if not all([gps_lat, gps_lat_ref, gps_lon, gps_lon_ref]):
            return None, None

        lat = float(gps_lat.values[0].num) / gps_lat.values[0].den + \
              float(gps_lat.values[1].num) / gps_lat.values[1].den / 60 + \
              float(gps_lat.values[2].num) / gps_lat.values[2].den / 3600
        if gps_lat_ref.values != "N":
            lat = -lat

        lon = float(gps_lon.values[0].num) / gps_lon.values[0].den + \
              float(gps_lon.values[1].num) / gps_lon.values[1].den / 60 + \
              float(gps_lon.values[2].num) / gps_lon.values[2].den / 3600
        if gps_lon_ref.values != "E":
            lon = -lon

        return lat, lon
    except Exception as e:
        print(f"Failed to extract GPS from {file_path}: {e}")
        return None, None

def generate_map_html(photo_list, output_file="publish/map.html"):
    html_content = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Photo Map</title>
<style>
  #map {{ height: 100vh; width: 100%; }}
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
</head>
<body>
<div id="map"></div>
<script>
var map = L.map('map').setView([0,0], 2);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
"""
    for photo in photo_list:
        lat, lon = photo['gps']
        thumb = photo['thumb'].replace("\\", "/")
        if lat is not None and lon is not None:
            html_content += f"""
L.marker([{lat},{lon}]).addTo(map).bindPopup('<img src="{thumb}" width="128"/>');
"""
    html_content += "</script></body></html>"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"Map saved to {output_file}")

def main():
    os.makedirs("publish/thumbs", exist_ok=True)
    photos = list_photos()
    photo_data = []

    for p in photos:
        print(f"Processing {p['name']}...")
        file_path = download_file(p['id'], p['name'])
        thumb_path = os.path.join("publish", "thumbs", f"{p['name']}.png")
        thumb_result = create_thumbnail(file_path, thumb_path)
        if thumb_result is None:
            continue

        lat, lon = extract_gps(file_path)
        photo_data.append({'thumb': f"thumbs/{p['name']}.png", 'gps': (lat, lon)})

    generate_map_html(photo_data)

if __name__ == "__main__":
    main()

import os
import io
import base64
import json
from PIL import Image, ImageDraw, UnidentifiedImageError
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
    service = build('drive', 'v3', credentials=creds)
    return service

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
    with open(file_name, 'wb') as f:
        f.write(fh.read())
    return file_name

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
    with open(file_path, 'rb') as f:
        tags = exifread.process_file(f, details=False)
    try:
        gps_lat = tags['GPS GPSLatitude'].values
        gps_lat_ref = tags['GPS GPSLatitudeRef'].values
        gps_lon = tags['GPS GPSLongitude'].values
        gps_lon_ref = tags['GPS GPSLongitudeRef'].values
        lat = float(gps_lat[0].num) / gps_lat[0].den + \
              float(gps_lat[1].num) / gps_lat[1].den / 60 + \
              float(gps_lat[2].num) / gps_lat[2].den / 3600
        if gps_lat_ref != "N":
            lat = -lat
        lon = float(gps_lon[0].num) / gps_lon[0].den + \
              float(gps_lon[1].num) / gps_lon[1].den / 60 + \
              float(gps_lon[2].num) / gps_lon[2].den / 3600
        if gps_lon_ref != "E":
            lon = -lon
        return lat, lon
    except KeyError:
        return None, None

def generate_map_html(photo_list, output_file="map.html"):
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
        if lat is not None:
            html_content += f"""
L.marker([{lat},{lon}]).addTo(map).bindPopup('<img src="{photo['thumb']}" width="128"/>');
"""
    html_content += "</script></body></html>"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"Map saved to {output_file}")

def main():
    photos = list_photos()
    photo_data = []
    os.makedirs("thumbs", exist_ok=True)
    for p in photos:
        file_path = download_file(p['id'], p['name'])
        thumb_path = f"thumbs/{p['name']}.png"
        create_thumbnail(file_path, thumb_path)
        lat, lon = extract_gps(file_path)
        photo_data.append({'thumb': thumb_path, 'gps': (lat, lon)})

    generate_map_html(photo_data)

if __name__ == "__main__":
    main()


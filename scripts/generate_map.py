import os
import io
import base64
import tempfile
import pyheif
from PIL import Image, ExifTags
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from github import Github

# === 設定 ===
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = os.getenv("FOLDER_ID")
CREDENTIALS_JSON = os.getenv("DRIVE_CREDENTIALS_JSON")
REPO_NAME = os.getenv("REPO_NAME")
BRANCH_NAME = os.getenv("BRANCH_NAME", "main")
HTML_NAME = os.getenv("HTML_NAME", "index.html")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# === Drive API 認証 ===
creds = service_account.Credentials.from_service_account_info(
    eval(CREDENTIALS_JSON), scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=creds)

# === GitHub 認証 ===
gh = Github(GITHUB_TOKEN)
repo = gh.get_repo(REPO_NAME)

# === EXIF GPS抽出 ===
def extract_gps(image):
    try:
        exif = image._getexif()
        if not exif:
            return None
        gps_info = {}
        for tag, value in exif.items():
            decoded = ExifTags.TAGS.get(tag)
            if decoded == "GPSInfo":
                for t in value:
                    gps_info[ExifTags.GPSTAGS.get(t, t)] = value[t]
        if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
            lat = convert_to_degrees(gps_info["GPSLatitude"])
            lon = convert_to_degrees(gps_info["GPSLongitude"])
            if gps_info.get("GPSLatitudeRef") == "S":
                lat = -lat
            if gps_info.get("GPSLongitudeRef") == "W":
                lon = -lon
            return (lat, lon)
    except Exception as e:
        print("⚠️ GPS extraction error:", e)
    return None

def convert_to_degrees(value):
    d, m, s = value
    return float(d[0]/d[1] + (m[0]/m[1])/60 + (s[0]/s[1])/3600)

# === Drive からファイル一覧 ===
results = drive_service.files().list(
    q=f"'{FOLDER_ID}' in parents and (mimeType contains 'image/')",
    fields="files(id, name, mimeType)"
).execute()
files = results.get("files", [])

locations = []

for f in files:
    file_id = f["id"]
    file_name = f["name"]
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)

    try:
        if file_name.lower().endswith(".heic"):
            heif_file = pyheif.read(fh.read())
            image = Image.frombytes(
                heif_file.mode, heif_file.size, heif_file.data,
                "raw", heif_file.mode, heif_file.stride
            )
        else:
            image = Image.open(fh)

        gps = extract_gps(image)
        if gps:
            # サムネイル生成
            thumb = image.copy()
            thumb.thumbnail((200, 200))
            buf = io.BytesIO()
            thumb.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            locations.append((gps[0], gps[1], b64))
            print(f"✅ {file_name} → {gps}")
        else:
            print(f"⚠️ {file_name}: GPSなし")

    except Exception as e:
        print(f"⚠️ Cannot open {file_name}:", e)

# === HTML 生成 ===
html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Photo Map</title>
  <link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
  <script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
</head>
<body>
  <div id='map' style='height:100vh;'></div>
  <script>
    var map = L.map('map').setView([35, 135], 5);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19
    }}).addTo(map);

    var markers = [
      {",".join([f"[{lat},{lon},'{b64}']" for lat,lon,b64 in locations])}
    ];

    markers.forEach(function(m) {{
      var img = "<img src='data:image/jpeg;base64," + m[2] + "' width='200'/>";
      L.marker([m[0], m[1]]).addTo(map).bindPopup(img);
    }});
  </script>
</body>
</html>"""

# === GitHub Pages へ反映 ===
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(contents.path, "Update map", html_content, contents.sha, branch=BRANCH_NAME)
    print("✅ HTML updated on GitHub Pages")
except Exception:
    repo.create_file(HTML_NAME, "Create map", html_content, branch=BRANCH_NAME)
    print("✅ HTML created on GitHub Pages")

# Colab/ローカル兼用
import os, io, json, base64
import pandas as pd
from PIL import Image, ImageDraw
import pyheif, exifread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from github import Github

# ===== 設定 =====
FOLDER_ID = os.environ.get('FOLDER_ID')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
REPO_NAME = 'K03-02/photomap'
HTML_NAME = 'index.html'
BRANCH_NAME = 'main'
PROCESSED_JSON = 'data/processed_files.json'

os.makedirs('data', exist_ok=True)

# ===== Drive API =====
drive_service = build('drive', 'v3')

def list_image_files(folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    return results.get('files', [])

def get_file_bytes(file_id):
    fh = io.BytesIO()
    request = drive_service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()

def pil_open_safe(file_bytes, mime_type):
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            return Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
        else:
            return Image.open(io.BytesIO(file_bytes))
    except:
        return None

def extract_exif(file_bytes, mime_type):
    lat = lon = dt = ''
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            img = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
            buf = io.BytesIO()
            img.save(buf, format='JPEG')
            buf.seek(0)
            tags = exifread.process_file(buf, details=False)
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
    except:
        pass
    return lat, lon, dt

def heic_to_base64_circle(file_bytes, size=50):
    img = pil_open_safe(file_bytes, 'image/heic')
    if img is None: return None
    img = img.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0,0,size,size), fill=255)
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

def heic_to_base64_popup(file_bytes, width=200):
    img = pil_open_safe(file_bytes, 'image/heic')
    if img is None: return None
    w, h = img.size
    img = img.resize((width, int(h * (width / w))))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

# ===== 以前処理済みファイルをロード =====
if os.path.exists(PROCESSED_JSON):
    with open(PROCESSED_JSON, 'r') as f:
        processed_files = json.load(f)
else:
    processed_files = []

# ===== 画像情報収集（新規のみ） =====
rows = []
for f in list_image_files(FOLDER_ID):
    if f['id'] in processed_files:
        continue
    print(f"Processing {f['name']}...")
    file_bytes = get_file_bytes(f['id'])
    lat, lon, dt = extract_exif(file_bytes, f['mimeType'])
    rows.append({
        'filename': f['name'],
        'latitude': lat,
        'longitude': lon,
        'datetime': dt,
        'file_id': f['id'],
        'mime_type': f['mimeType']
    })
    processed_files.append(f['id'])

# 保存
with open(PROCESSED_JSON, 'w') as f:
    json.dump(processed_files, f)

# ===== HTML 作成 =====
html_lines = [
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Photo Map</title>",
    "<style>#map { height: 100vh; width: 100%; }</style>",
    "<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>",
    "<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script></head><body>",
    "<div id='map'></div><script>",
    "var map = L.map('map').setView([35.0, 138.0], 5);",
    "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19}).addTo(map);",
    "var markers = [];",
]

for r in rows:
    if r['latitude'] and r['longitude']:
        file_bytes = get_file_bytes(r['file_id'])
        icon_data_uri = heic_to_base64_circle(file_bytes)
        popup_data_uri = heic_to_base64_popup(file_bytes)
        if icon_data_uri and popup_data_uri:
            html_lines.append(f"""
var icon = L.icon({{iconUrl: '{icon_data_uri}', iconSize: [50,50]}});
var marker = L.marker([{r['latitude']},{r['longitude']}], {{icon: icon}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{r['filename']}</b><br>{r['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={r['latitude']},{r['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{popup_data_uri}' width='200'/>");
""")

html_lines.append("""
map.on('zoomend', function(){
    var zoom = map.getZoom();
    var scale = Math.min(zoom/5, 1.2);
    markers.forEach(function(m){
        var img = m.getElement().querySelector('img');
        if(img){
            var size = 50 * scale;
            if(size>60){ size=60; }
            img.style.width = size + 'px';
            img.style.height = size + 'px';
        }
    });
});
""")
html_lines.append("</script></body></html>")
html_str = "\n".join(html_lines)

# ===== GitHub にアップロード =====
g = Github(GITHUB_TOKEN)
repo = g.get_repo(REPO_NAME)

try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "Update HTML", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "Create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")


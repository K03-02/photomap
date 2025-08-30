import os
import io
import base64
import pandas as pd
from PIL import Image, ImageDraw
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from github import Github
import pyheif, exifread

# ===== 環境変数 =====
FOLDER_ID = os.environ['FOLDER_ID']
GITHUB_TOKEN = os.environ['GITHUB_TOKEN']
REPO_NAME = os.environ['REPO_NAME']
HTML_NAME = os.environ['HTML_NAME']
BRANCH_NAME = os.environ['BRANCH_NAME']

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
        status, done = downloader.next_chunk()
    return fh.getvalue()

def pil_open_safe(file_bytes, mime_type):
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            pil_img = Image.frombytes(
                heif_file.mode,
                heif_file.size,
                heif_file.data,
                "raw",
                heif_file.mode
            )
        else:
            pil_img = Image.open(io.BytesIO(file_bytes))
        return pil_img
    except Exception as e:
        print(f"⚠️ Cannot open image: {e}")
        return None

def extract_exif(file_bytes, mime_type):
    lat, lon, dt = '', '', ''
    try:
        if 'heic' in mime_type.lower():
            heif_file = pyheif.read_heif(file_bytes)
            image = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
            fbytes = io.BytesIO()
            image.save(fbytes, format='JPEG')
            fbytes.seek(0)
            tags = exifread.process_file(fbytes, details=False)
        else:
            tags = exifread.process_file(io.BytesIO(file_bytes), details=False)

        if 'GPS GPSLatitude' in tags and 'GPS GPSLongitude' in tags:
            def dms_to_dd(dms, ref):
                deg = float(dms.values[0].num)/dms.values[0].den
                min = float(dms.values[1].num)/dms.values[1].den
                sec = float(dms.values[2].num)/dms.values[2].den
                dd = deg + min/60 + sec/3600
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
    if img is None:
        return None
    img = img.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,size,size), fill=255)
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

def heic_to_base64_popup(file_bytes, width=200):
    img = pil_open_safe(file_bytes, 'image/heic')
    if img is None:
        return None
    w, h = img.size
    new_h = int(h * (width / w))
    img = img.resize((width, new_h))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

# ===== 画像情報収集 =====
rows = []
for f in list_image_files(FOLDER_ID):
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

df = pd.DataFrame(rows)

# ===== GitHub へアップロード =====
g = Github(GITHUB_TOKEN)
repo = g.get_repo(REPO_NAME)

try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    html_str = contents.decoded_content.decode()
    insert_pos = html_str.rfind('</script>')
except:
    html_str = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Photo Map</title></head><body><div id='map'></div><script>var markers=[];</script></body></html>"
    insert_pos = html_str.rfind('</script>')

# ===== 新規マーカー生成 =====
marker_js = ""
for _, row in df.iterrows():
    if row['latitude'] and row['longitude']:
        file_bytes = get_file_bytes(row['file_id'])
        icon_data_uri = heic_to_base64_circle(file_bytes)
        popup_data_uri = heic_to_base64_popup(file_bytes)
        if icon_data_uri and popup_data_uri:
            marker_js += f"""
var icon = L.icon({{iconUrl: '{icon_data_uri}', iconSize: [50,50]}}); 
var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
markers.push(marker);
marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
+ "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
+ "<img src='{popup_data_uri}' width='200'/>");
"""
        else:
            print(f"⚠️ Skip {row['filename']} (cannot open)")

# ===== HTML に追加 =====
html_str = html_str[:insert_pos] + marker_js + html_str[insert_pos:]

# ===== GitHub に保存 =====
try:
    contents = repo.get_contents(HTML_NAME, ref=BRANCH_NAME)
    repo.update_file(HTML_NAME, "update HTML with new markers", html_str, contents.sha, branch=BRANCH_NAME)
    print("HTML updated on GitHub.")
except:
    repo.create_file(HTML_NAME, "create HTML", html_str, branch=BRANCH_NAME)
    print("HTML created on GitHub.")


#!/usr/bin/env python3
import os
import io
import json
import requests
from PIL import Image
import pyheif
import exifread
from datetime import datetime

# ========== Google Drive からファイルを取ってくる部分 ==========
def download_file(file_id):
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    headers = {"Authorization": f"Bearer {os.environ.get('GDRIVE_TOKEN','')}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.content
    else:
        print(f"⚠️ Download failed for {file_id}: {r.status_code}")
        return None

# ========== 画像を安全に開く ==========
def pil_open_safe(file_bytes, mime_type):
    if mime_type in ["image/heic", "image/heif"]:
        heif_file = pyheif.read_heif(file_bytes)
        img = Image.frombytes(
            heif_file.mode,
            heif_file.size,
            heif_file.data,
            "raw",
            heif_file.mode,
            heif_file.stride,
        )
        return img, heif_file.metadata
    else:
        return Image.open(io.BytesIO(file_bytes)), None

# ========== GPS 変換用ユーティリティ ==========
def _convert_to_degrees(value):
    d, m, s = value.values
    return d.num/d.den + (m.num/m.den)/60 + (s.num/s.den)/3600

def _convert_heif_gps(data):
    # ISO BMFFのExifは tiffバイト列になっている場合が多い
    tags = exifread.process_file(io.BytesIO(data), details=False)
    return _extract_from_exifread(tags)

def _extract_from_exifread(tags):
    lat = lon = dt = None
    if "GPS GPSLatitude" in tags and "GPS GPSLatitudeRef" in tags:
        lat = _convert_to_degrees(tags["GPS GPSLatitude"])
        if tags["GPS GPSLatitudeRef"].values in ["S", "南"]:
            lat = -lat
    if "GPS GPSLongitude" in tags and "GPS GPSLongitudeRef" in tags:
        lon = _convert_to_degrees(tags["GPS GPSLongitude"])
        if tags["GPS GPSLongitudeRef"].values in ["W", "西"]:
            lon = -lon
    if "EXIF DateTimeOriginal" in tags:
        dt = str(tags["EXIF DateTimeOriginal"])
    return lat, lon, dt

# ========== EXIF 抽出 ==========
def extract_exif(file_bytes, mime_type, heif_metadata=None):
    try:
        if mime_type in ["image/heic", "image/heif"]:
            # pyheif.metadata に Exif チャンクがある場合
            exif_data = None
            if heif_metadata:
                for m in heif_metadata:
                    if m["type"] == "Exif":
                        exif_data = m["data"]
                        break
            if exif_data:
                return _convert_heif_gps(exif_data)
            else:
                return None, None, None
        else:
            fbytes = io.BytesIO(file_bytes)
            tags = exifread.process_file(fbytes, details=False)
            return _extract_from_exifread(tags)
    except Exception as e:
        print(f"⚠️ EXIF extraction error: {e}")
        return None, None, None

# ========== メイン処理 ==========
def process_file(file_id, filename, mime_type):
    print(f"\n=== Processing file: {filename} ===")
    file_bytes = download_file(file_id)
    if not file_bytes:
        return None

    print(f"-> Downloaded {len(file_bytes)} bytes")

    try:
        img, heif_metadata = pil_open_safe(file_bytes, mime_type)
        print(f"-> Opened successfully: {filename}, size={img.size}, mode={img.mode}")
    except Exception as e:
        print(f"⚠️ Cannot open image {filename}: {e}")
        return None

    lat, lon, dt = extract_exif(file_bytes, mime_type, heif_metadata)
    print(f"-> EXIF extracted: lat={lat}, lon={lon}, dt={dt}")

    return {
        "filename": filename,
        "lat": lat,
        "lon": lon,
        "datetime": dt,
    }

def main():
    # ダミー: Google Drive から拾うファイルリストを仮定
    files = [
        {"id":"xxxx", "name":"IMG_3901.HEIC", "mimeType":"image/heic"},
        {"id":"yyyy", "name":"PXL_20250828_233920019.jpg", "mimeType":"image/jpeg"},
    ]

    results = []
    for f in files:
        res = process_file(f["id"], f["name"], f["mimeType"])
        if res: results.append(res)

    with open("mapdata.json","w",encoding="utf-8") as f:
        json.dump(results,f,ensure_ascii=False,indent=2)

if __name__ == "__main__":
    main()


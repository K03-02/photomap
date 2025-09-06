import os
import glob
from PIL import Image, ImageDraw
import piexif
import folium
import csv
from datetime import datetime

# ==== 白枠付き丸アイコンWebP生成 ====
def create_round_icon_webp(image, final_diameter=120, border_thickness=6, base_size=480):
    """
    与えられた画像を丸く切り抜き、白枠を付けたWebP形式のアイコンに変換する。
    """
    # 正方形にリサイズ
    image = image.resize((base_size, base_size), Image.LANCZOS)

    # 円形マスク作成
    mask = Image.new("L", (base_size, base_size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, base_size, base_size), fill=255)

    # 丸く切り抜き
    result = Image.new("RGBA", (base_size, base_size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask=mask)

    # 白い縁を追加
    border_size = base_size + 2 * border_thickness
    bordered = Image.new("RGBA", (border_size, border_size), (0, 0, 0, 0))
    border_mask = Image.new("L", (border_size, border_size), 0)
    draw = ImageDraw.Draw(border_mask)
    draw.ellipse((0, 0, border_size, border_size), fill=255)
    draw.ellipse(
        (border_thickness, border_thickness,
         border_size - border_thickness,
         border_size - border_thickness),
        fill=0
    )
    bordered.paste((255, 255, 255, 255), (0, 0), border_mask)
    bordered.paste(result, (border_thickness, border_thickness), mask)

    # 最終サイズにリサイズ
    final_img = bordered.resize((final_diameter, final_diameter), Image.LANCZOS)
    return final_img

# ==== EXIFから撮影日時を取得 ====
def get_datetime_from_exif(filepath):
    try:
        exif_dict = piexif.load(filepath)
        if piexif.ExifIFD.DateTimeOriginal in exif_dict["Exif"]:
            date_str = exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal].decode("utf-8")
            return datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None

# ==== メイン処理 ====
def generate_map(image_folder, output_html, output_csv, icon_folder, popup_folder):
    os.makedirs(icon_folder, exist_ok=True)
    os.makedirs(popup_folder, exist_ok=True)

    rows = []
    for filepath in glob.glob(os.path.join(image_folder, "*")):
        try:
            filename = os.path.basename(filepath)
            with Image.open(filepath) as img:
                # サムネイル用アイコン作成
                icon_img = create_round_icon_webp(img, final_diameter=120, base_size=480)
                icon_path = os.path.join(icon_folder, filename + ".webp")
                icon_img.save(icon_path, "WEBP", quality=95)

                # ポップアップ用写真（リサイズ無し、軽く圧縮）
                popup_img = img.convert("RGB")
                popup_path = os.path.join(popup_folder, filename + ".webp")
                popup_img.save(popup_path, "WEBP", quality=90)

                # 緯度経度（今回はEXIFなし想定）
                lat, lon = 35.681236, 139.767125  # 東京駅に仮置き

                # 撮影日時
                dt = get_datetime_from_exif(filepath)
                dt_str = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

                rows.append({
                    "filename": filename,
                    "latitude": lat,
                    "longitude": lon,
                    "datetime": dt_str,
                    "icon_url": icon_path,
                    "popup_url": popup_path
                })
        except Exception as e:
            print(f"⚠️ Error processing {filepath}: {e}")

    # ==== CSV保存 ====
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "latitude", "longitude", "datetime", "icon_url", "popup_url"])
        writer.writeheader()
        writer.writerows(rows)

    # ==== HTML保存 ====
    html_head = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Photo Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
  <style>
    #map { width: 100%; height: 100vh; }
    .leaflet-popup-content img { display: block; margin: auto; border-radius: 12px; }
  </style>
</head>
<body>
<div id="map"></div>
<script>
  var map = L.map('map').setView([35.681236, 139.767125], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {{
      maxZoom: 19
  }}).addTo(map);
"""

    html_tail = """
</script>
</body>
</html>
"""

    html_lines = [html_head]
    for row in rows:
        if row['latitude'] and row['longitude']:
            html_lines.append(f"""
  var icon = L.icon({{
      iconUrl: '{row['icon_url']}',
      iconSize: [120, 120],   // サムネイルを2倍大きく
      className: 'custom-icon'
  }});
  var marker = L.marker([{row['latitude']},{row['longitude']}], {{icon: icon}}).addTo(map);
  marker.bindPopup("<b>{row['filename']}</b><br>{row['datetime']}<br>"
    + "<a href='https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}' target='_blank'>Google Mapsで開く</a><br>"
    + "<img src='{row['popup_url']}' style='max-width:2700px; width:100%; height:auto;'/>",
    {{ maxWidth: 2800 }}  // ← ポップアップの大きさを3倍に
  );
""")

    html_lines.append(html_tail)

    with open(output_html, "w", encoding="utf-8") as f:
        f.write("\n".join(html_lines))

# ==== 実行例 ====
if __name__ == "__main__":
    generate_map(
        image_folder="images",          # 元画像の入っているフォルダ
        output_html="map.html",         # 出力するHTML
        output_csv="data.csv",          # 出力するCSV
        icon_folder="icons",            # 丸アイコン保存先
        popup_folder="popups"           # ポップアップ画像保存先
    )

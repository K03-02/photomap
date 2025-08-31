import os
import base64
import folium
from PIL import Image
from io import BytesIO
import exifread

# 保存先フォルダを作成
PHOTO_DIR = "photos"
os.makedirs(PHOTO_DIR, exist_ok=True)

THUMB_DIR = "thumbs"
os.makedirs(THUMB_DIR, exist_ok=True)

# ==== 写真フォルダと出力先 ====
PHOTO_DIR = "photos"
OUTPUT_HTML = "index.html"

# ---- 位置情報の取得 ----
def get_lat_lng(path):
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f)

        def conv(v):
            d, m, s = [x.num / x.den for x in v.values]
            return d + (m / 60.0) + (s / 3600.0)

        lat = conv(tags["GPS GPSLatitude"])
        if tags["GPS GPSLatitudeRef"].values != "N":
            lat = -lat
        lng = conv(tags["GPS GPSLongitude"])
        if tags["GPS GPSLongitudeRef"].values != "E":
            lng = -lng
        return lat, lng
    except Exception:
        return None, None

# ---- サムネイル作成（Base64埋め込み用） ----
def get_thumbnail(path, size=100):
    try:
        im = Image.open(path)
        im.thumbnail((size, size))
        buf = BytesIO()
        im.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None

# ---- 写真データ収集 ----
photos = []
for fn in os.listdir(PHOTO_DIR):
    if fn.lower().endswith((".jpg", ".jpeg", ".png", ".heic")):
        path = os.path.join(PHOTO_DIR, fn)
        lat, lng = get_lat_lng(path)
        if lat and lng:
            thumb = get_thumbnail(path, size=100)
            if thumb:
                photos.append({
                    "lat": lat,
                    "lng": lng,
                    "thumb": thumb,
                    "name": fn
                })

if not photos:
    raise SystemExit("❌ 写真に位置情報がありません")

# ---- マップ作成 ----
center = (photos[0]["lat"], photos[0]["lng"])
m = folium.Map(location=center, zoom_start=12)

# ---- 写真マーカー追加 ----
for p in photos:
    html = f'<div style="text-align:center;"><img src="{p["thumb"]}" style="width:50px;height:50px;border-radius:8px;"><br>{p["name"]}</div>'
    icon = folium.DivIcon(html=html)
    folium.Marker(location=(p["lat"], p["lng"]), icon=icon).add_to(m)

# ---- HTML 出力 ----
m.save(OUTPUT_HTML)

# ---- Leaflet のズーム連動スクリプトを追加 ----
with open(OUTPUT_HTML, "r", encoding="utf-8") as f:
    html_lines = f.read()

extra_js = """
<script>
var markers = [];
map.eachLayer(function(layer){
    if(layer instanceof L.Marker){
        markers.push(layer);
    }
});

map.on('zoomend', function(){
    var zoom = map.getZoom();
    var scale = zoom / 10.0; // ズームに比例
    markers.forEach(function(m){
        var el = m.getElement();
        if(el){
            var img = el.querySelector('img');
            if(img){
                var size = Math.max(20, Math.min(80, 50 * scale));
                img.style.width = size + 'px';
                img.style.height = size + 'px';
            }
        }
    });
});
</script>
</body>"""

# 末尾の </body> を置換して追記
html_lines = html_lines.replace("</body>", extra_js)

with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
    f.write(html_lines)

print(f"✅ 地図を生成しました: {OUTPUT_HTML}")


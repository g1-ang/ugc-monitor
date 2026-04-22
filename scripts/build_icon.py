"""
build_icon.py — UGC Monitor macOS 앱 아이콘 생성
브랜드 색상(teal #3D9E8E)에 "ugc" 레이블 + amber 점 액센트.

생성:
  ugc_monitor_icon_1024.png   (마스터)
  icon.iconset/                (다양한 크기)
  UGC Monitor.icns             (macOS용 아이콘 번들)
"""

import os, subprocess, shutil
from PIL import Image, ImageDraw, ImageFont

SIZE   = 1024
RADIUS = 225  # iOS-like
BG     = (0x3D, 0x9E, 0x8E, 255)  # teal dark
FG     = (0xFF, 0xFF, 0xFF, 255)  # white
DOT    = (0xE0, 0x9A, 0x5A, 255)  # amber accent (같은 브랜드 팔레트)


def find_font(size):
    """macOS 기본 폰트 중 사용 가능한 것 반환."""
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=2)  # bold
            except Exception:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
    return ImageFont.load_default()


def make_icon():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 라운드 사각형 배경
    d.rounded_rectangle([(0, 0), (SIZE - 1, SIZE - 1)], radius=RADIUS, fill=BG)

    # 메인 텍스트 "ugc"
    font = find_font(440)
    text = "ugc"
    bbox = d.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (SIZE - w) / 2 - bbox[0]
    y = (SIZE - h) / 2 - bbox[1] - 20  # 살짝 위로
    d.text((x, y), text, fill=FG, font=font)

    # 하단 작은 레이블 "monitor"
    sub_font = find_font(92)
    sub_text = "monitor"
    sub_bbox = d.textbbox((0, 0), sub_text, font=sub_font)
    sw = sub_bbox[2] - sub_bbox[0]
    sx = (SIZE - sw) / 2 - sub_bbox[0]
    sy = y + h + 30
    d.text((sx, sy), sub_text, fill=(0xFF, 0xFF, 0xFF, 200), font=sub_font)

    # 상단 우측 amber dot (scan/live indicator 느낌)
    dot_r = 55
    cx, cy = 820, 200
    d.ellipse([(cx - dot_r, cy - dot_r), (cx + dot_r, cy + dot_r)], fill=DOT)

    return img


def make_icns(png_path, out_icns):
    """iconutil로 .icns 번들 생성."""
    iconset = "icon.iconset"
    if os.path.exists(iconset):
        shutil.rmtree(iconset)
    os.makedirs(iconset)

    master = Image.open(png_path)
    # macOS iconset 표준 크기
    sizes = [
        (16, "icon_16x16.png"),     (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),     (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),  (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),  (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),  (1024, "icon_512x512@2x.png"),
    ]
    for size, name in sizes:
        resized = master.resize((size, size), Image.LANCZOS)
        resized.save(os.path.join(iconset, name), "PNG")

    try:
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out_icns], check=True)
        print(f"✓ {out_icns} 생성")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ iconutil 실패: {e}")

    shutil.rmtree(iconset)


def main():
    png = "ugc_monitor_icon_1024.png"
    icns = "UGC Monitor.icns"
    icon = make_icon()
    icon.save(png, "PNG")
    print(f"✓ {png} 생성 ({os.path.getsize(png)//1024} KB)")
    make_icns(png, icns)


if __name__ == "__main__":
    main()

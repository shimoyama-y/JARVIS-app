"""JARVIS のアンドロイドの顔 (Pillow で描くオリジナルのイラスト)

白いセラミックの顔、機械の側頭部と光る耳のリング、首のケーブル。
顔の部品 (土台・リング・眉・目・口) を別々の透明な画像として作り、
画面ではそれを重ねて表情を作る (部品を差し替えるだけなので軽い)。

女性 (female) と男性 (male) で顔立ちが変わる。
"""
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 300, 400   # 基準の大きさ (この座標で描いてから拡大縮小する)
SS = 2            # なめらかにするため、いったん大きく描いてから縮める

EYES = ("open", "wide", "up", "happy", "half", "closed")
BROWS = ("neutral", "raised", "sad", "think")
MOUTHS = ("neutral", "smile", "sad", "o")
MOUTH_LEVELS = 5
RING_COLORS = {"cyan": (60, 220, 245), "blue": (90, 150, 255), "amber": (255, 176, 40)}

# 顔立ちの違い
STYLES = {
    "female": {"jaw": 0.86, "chin": 0.0, "lip": (214, 160, 170), "lip_line": (168, 118, 130),
               "brow_w": 2.2, "brow_col": (96, 104, 118), "lashes": True, "nose": 0.85, "mouth_w": 0.9},
    "male": {"jaw": 1.0, "chin": 6.0, "lip": (196, 196, 204), "lip_line": (130, 136, 148),
             "brow_w": 3.6, "brow_col": (84, 92, 104), "lashes": False, "nose": 1.1, "mouth_w": 1.05},
}


# ---------- 描画の道具 ----------
def _canvas():
    return Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))


def _p(points):
    return [(x * SS, y * SS) for x, y in points]


def _box(x1, y1, x2, y2):
    return [x1 * SS, y1 * SS, x2 * SS, y2 * SS]


def _blur(img, r):
    return img.filter(ImageFilter.GaussianBlur(r * SS))


def _spline(points, closed=False, n=12):
    """点をなめらかな曲線でつなぐ (Catmull-Rom)"""
    pts = list(points)
    if closed:
        pts = [pts[-1]] + pts + pts[:2]
    else:
        pts = [pts[0]] + pts + [pts[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for k in range(n):
            t = k / n
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in (0, 1)))
    if not closed:
        out.append(points[-1])
    return out


def _radial(size, center, radius, inner, outer):
    """円形のグラデーション (inner → outer)"""
    cx, cy = center
    grad = Image.radial_gradient("L").resize((max(1, int(radius * 2)), max(1, int(radius * 2))))
    mask = Image.new("L", size, 255)
    mask.paste(grad, (int(cx - radius), int(cy - radius)))
    return Image.composite(Image.new("RGBA", size, outer), Image.new("RGBA", size, inner), mask)


def _bezier(p0, p1, p2, p3, n=24):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t * t * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / n for i in range(n + 1))]


def _mirror(points):
    return [(300 - x, y) for x, y in points]


def _face_outline(style):
    j, chin = style["jaw"], style["chin"]
    half = [(150, 30), (186, 34), (216, 54), (234, 90), (240, 138), (237, 186),
            (150 + 80 * j, 232), (150 + 66 * j, 274), (150 + 46 * j + chin * 0.5, 306),
            (150 + 22 * j + chin, 330), (150, 338 + chin * 0.3)]
    right = half[:-1]
    left = [(300 - x, y) for x, y in reversed(half[1:-1])]
    return _spline(right + [half[-1]] + left, closed=True, n=10)


# ---------- 部品 ----------
def _base(style):
    """動かない部分 (首・ケーブル・頭・顔の陰影・継ぎ目・鼻)"""
    img = _canvas()
    d = ImageDraw.Draw(img)

    # 首の機械部分とケーブル
    d.rounded_rectangle(_box(124, 280, 176, 400), radius=10 * SS, fill=(48, 56, 66, 255))
    for y in range(296, 400, 15):
        d.rounded_rectangle(_box(120, y, 180, y + 8), radius=4 * SS, fill=(92, 102, 114, 255))
        d.line(_p([(122, y + 2), (178, y + 2)]), fill=(160, 170, 182, 255), width=SS)
    cables = [((98, 250), (72, 300), (82, 360), (62, 400), 7, (30, 34, 40)),
              ((110, 272), (96, 322), (108, 372), (96, 400), 5, (42, 48, 56)),
              ((128, 304), (116, 344), (130, 384), (120, 400), 3, (25, 120, 140))]
    for p0, p1, p2, p3, wdt, col in cables + [(tuple(_mirror([c[0]])[0]), tuple(_mirror([c[1]])[0]),
                                                tuple(_mirror([c[2]])[0]), tuple(_mirror([c[3]])[0]),
                                                c[4], c[5]) for c in cables]:
        d.line(_p(_bezier(p0, p1, p2, p3)), fill=col + (255,), width=wdt * SS, joint="curve")
        d.line(_p(_bezier(*[(x - 1, y) for x, y in (p0, p1, p2, p3)])), fill=(120, 130, 142, 255),
               width=max(1, wdt // 3) * SS, joint="curve")

    # 頭の後ろの機械の殻
    d.ellipse(_box(46, 32, 254, 298), fill=(36, 42, 50, 255))
    d.ellipse(_box(52, 38, 248, 292), outline=(72, 82, 94, 255), width=3 * SS)
    for ang in range(196, 345, 22):
        a = math.radians(ang)
        d.line(_p([(150 + 72 * math.cos(a), 160 + 92 * math.sin(a)),
                   (150 + 100 * math.cos(a), 165 + 128 * math.sin(a))]), fill=(72, 82, 94, 255), width=2 * SS)

    # 顔: セラミックの陰影 (中心が明るく、ふちに向かって暗い)
    outline = _face_outline(style)
    face_mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(face_mask).polygon(_p(outline), fill=255)
    face_mask = face_mask.filter(ImageFilter.GaussianBlur(0.6 * SS))
    shade = _radial(img.size, (150 * SS, 160 * SS), 180 * SS, (248, 250, 253, 255), (140, 152, 166, 255))
    img.paste(shade, (0, 0), face_mask)

    soft = _canvas()
    sd = ImageDraw.Draw(soft)
    sd.ellipse(_box(118, 58, 182, 100), fill=(255, 255, 255, 130))          # おでこのツヤ
    sd.ellipse(_box(98, 166, 138, 196), fill=(96, 108, 124, 70))            # 目のくぼみ
    sd.ellipse(_box(162, 166, 202, 196), fill=(96, 108, 124, 70))
    sd.polygon(_p([(146, 196), (139, 236), (145, 244), (150, 238)]), fill=(100, 112, 126, 80))  # 鼻の影
    sd.ellipse(_box(134, 242, 166, 254), fill=(96, 108, 122, 70))
    sd.ellipse(_box(116, 320, 184, 344), fill=(70, 80, 94, 100))            # あごの下
    sd.polygon(_p([(76, 236), (104, 254), (116, 302), (92, 276)]), fill=(100, 110, 124, 80))  # 頬骨の下の影
    sd.polygon(_p(_mirror([(76, 236), (104, 254), (116, 302), (92, 276)])), fill=(100, 110, 124, 80))
    sd.ellipse(_box(96, 206, 122, 228), fill=(255, 255, 255, 50))           # 頬のツヤ
    sd.ellipse(_box(178, 206, 204, 228), fill=(255, 255, 255, 50))
    soft = _blur(soft, 6)
    img.alpha_composite(Image.composite(soft, Image.new("RGBA", img.size, (0, 0, 0, 0)), face_mask))

    # パネルの継ぎ目とボルト
    d = ImageDraw.Draw(img)
    seam = (128, 140, 154, 255)
    d.line(_p(_spline([(68, 112), (104, 98), (150, 92), (196, 98), (232, 112)])), fill=seam, width=2 * SS)
    d.line(_p(_spline([(74, 150), (82, 200), (90, 248), (110, 296)])), fill=seam, width=2 * SS)
    d.line(_p(_spline(_mirror([(74, 150), (82, 200), (90, 248), (110, 296)]))), fill=seam, width=2 * SS)
    d.line(_p(_spline([(126, 312), (150, 320), (174, 312)])), fill=seam, width=2 * SS)
    d.line(_p([(150, 40), (150, 92)]), fill=seam, width=SS)
    for x, y in [(94, 104), (206, 104), (86, 214), (214, 214)]:
        d.ellipse(_box(x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=(156, 166, 178, 255), outline=(96, 106, 118, 255))

    # 鼻
    n = style["nose"]
    d.line(_p(_spline([(150, 194), (151, 218), (153, 232 + 4 * n)])), fill=(206, 212, 222, 255), width=2 * SS)
    d.arc(_box(150 - 14 * n, 236, 150 - 2, 246), 20, 160, fill=(118, 128, 142, 255), width=2 * SS)
    d.arc(_box(152, 236, 150 + 14 * n, 246), 20, 160, fill=(118, 128, 142, 255), width=2 * SS)

    # ふちの水色の反射光
    rim = _canvas()
    ImageDraw.Draw(rim).line(_p(outline[:len(outline) // 2]), fill=(80, 220, 255, 150), width=4 * SS)
    img.alpha_composite(_blur(rim, 3))
    return img


def _ring(color, bright):
    """耳の位置の機械の関節と光るリング"""
    img, glow = _canvas(), _canvas()
    d, gd = ImageDraw.Draw(img), ImageDraw.Draw(glow)
    for cx in (60, 240):
        cy = 178
        d.ellipse(_box(cx - 30, cy - 30, cx + 30, cy + 30), fill=(54, 62, 72, 255),
                  outline=(124, 134, 146, 255), width=2 * SS)
        gd.ellipse(_box(cx - 25, cy - 25, cx + 25, cy + 25), outline=color + (int(255 * bright),), width=8 * SS)
        d.ellipse(_box(cx - 22, cy - 22, cx + 22, cy + 22), outline=color + (255,), width=3 * SS)
        d.ellipse(_box(cx - 13, cy - 13, cx + 13, cy + 13), fill=(30, 36, 44, 255),
                  outline=(152, 162, 172, 255), width=SS)
        d.ellipse(_box(cx - 5, cy - 5, cx + 5, cy + 5), fill=color + (255,))
        for k in range(6):
            a = math.radians(k * 60 + 30)
            bx, by = cx + 27 * math.cos(a), cy + 27 * math.sin(a)
            d.ellipse(_box(bx - 2, by - 2, bx + 2, by + 2), fill=(172, 180, 190, 255))
    out = _blur(glow, 7 if bright > 0.6 else 4)
    out.alpha_composite(img)
    return out


def _eye_shape(cx, cy, state):
    top = {"open": 9, "wide": 12, "up": 9, "happy": 5, "half": 4}[state]
    bottom = 8 if state != "happy" else 3
    return _spline([(cx - 18, cy + 1), (cx - 9, cy - top), (cx + 4, cy - top - 1), (cx + 17, cy - 1),
                    (cx + 9, cy + bottom - 1), (cx - 6, cy + bottom)], closed=True, n=8)


def _eyes(state, style):
    img, glow = _canvas(), _canvas()
    d, gd = ImageDraw.Draw(img), ImageDraw.Draw(glow)
    for side, cx in (("r", 118), ("l", 182)):
        cy = 182
        flip = side == "l"
        if state == "closed":
            pts = [(118 - 17, cy + 1), (118, cy + 5), (118 + 17, cy + 1)]  # 右目の形を左右反転して左目にする
            d.line(_p(_spline(_mirror(pts) if flip else pts)), fill=(74, 84, 98, 255), width=3 * SS)
            continue
        shape = _eye_shape(150 - (cx - 150) if flip else cx, cy, state)
        shape = _mirror(shape) if flip else shape
        d.polygon(_p(shape), fill=(232, 240, 246, 255))
        ix, iy = cx, cy + (1 if state == "happy" else 0)
        if state == "up":
            ix, iy = cx + 2, cy - 4
        r = 8.5 if state == "wide" else 7.5
        eye_mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(eye_mask).polygon(_p(shape), fill=255)
        circle = Image.new("L", img.size, 0)
        ImageDraw.Draw(circle).ellipse(_box(ix - r, iy - r, ix + r, iy + r), fill=255)
        grad = _radial(img.size, (ix * SS, iy * SS), r * SS, (196, 252, 255, 255), (16, 120, 160, 255))
        iris = _canvas()
        iris.paste(grad, (0, 0), ImageChops.multiply(circle, eye_mask))
        idr = ImageDraw.Draw(iris)
        idr.ellipse(_box(ix - 3, iy - 3, ix + 3, iy + 3), fill=(8, 20, 30, 255))
        idr.ellipse(_box(ix + 1.5, iy - 4.5, ix + 4, iy - 2), fill=(255, 255, 255, 255))
        img.alpha_composite(Image.composite(iris, Image.new("RGBA", img.size, (0, 0, 0, 0)), eye_mask))
        gd.ellipse(_box(ix - 11, iy - 11, ix + 11, iy + 11), fill=(60, 220, 255, 100))
        upper = shape[:len(shape) // 2 + 1]
        d.line(_p(upper), fill=(64, 74, 88, 255), width=(3 if style["lashes"] else 2) * SS)
        if style["lashes"]:  # まつげ
            for k in range(3):
                x0, y0 = upper[len(upper) * (k + 2) // 6]
                dx = 3 if not flip else -3
                d.line(_p([(x0, y0), (x0 + dx, y0 - 3)]), fill=(64, 74, 88, 255), width=SS)
        if state == "half":  # 半分閉じたまぶた
            d.line(_p(upper), fill=(150, 160, 174, 255), width=3 * SS)
    out = _blur(glow, 4)
    out.alpha_composite(img)
    return out


def _brows(state, style):
    img = _canvas()
    d = ImageDraw.Draw(img)
    base = {"neutral": [(102, 162), (116, 157), (134, 159)],
            "raised": [(102, 155), (116, 149), (134, 151)],
            "sad": [(102, 163), (118, 158), (136, 152)],
            "think": [(102, 162), (116, 158), (134, 161)]}[state]
    left = _mirror(base)
    if state == "think":  # 片方の眉だけ上げる
        left = _mirror([(102, 152), (116, 146), (134, 150)])
    for pts in (base, left):
        d.line(_p(_spline(pts)), fill=style["brow_col"] + (255,), width=round(style["brow_w"] * SS))
    return img


def _mouth(shape, level, style):
    """口 (shape: neutral/smile/sad/o, level 0 = 閉じている 〜 4 = 大きく開いている)"""
    img = _canvas()
    d = ImageDraw.Draw(img)
    mw = 22 * style["mouth_w"]
    cy = 276
    curve = {"neutral": 0, "smile": -5, "sad": 4, "o": 0}[shape]  # 口角の上げ下げ
    gap = level * 3.2
    if shape == "o":
        mw *= 0.6
        gap = max(gap, 4)
    left, right = (150 - mw, cy + curve), (150 + mw, cy + curve)
    if gap > 0:
        inner = _spline([left, (150 - mw * 0.5, cy - 1), (150, cy - 1.5), (150 + mw * 0.5, cy - 1), right,
                         (150 + mw * 0.45, cy + gap + (curve * -0.2)), (150, cy + gap + 2),
                         (150 - mw * 0.45, cy + gap + (curve * -0.2))], closed=True, n=6)
        d.polygon(_p(inner), fill=(26, 26, 34, 255))
        if gap > 4 and shape != "o":  # 歯
            d.rectangle(_box(150 - mw * 0.55, cy - 1, 150 + mw * 0.55, cy + min(3, gap / 3)), fill=(222, 226, 232, 255))
    lip, line = style["lip"] + (255,), style["lip_line"] + (255,)
    upper = _spline([left, (150 - mw * 0.55, cy - 5 + curve * 0.3), (150, cy - 3.5),
                     (150 + mw * 0.55, cy - 5 + curve * 0.3), right,
                     (150 + mw * 0.5, cy + 0.5), (150, cy + 1), (150 - mw * 0.5, cy + 0.5)], closed=True, n=6)
    d.polygon(_p(upper), fill=lip, outline=line)
    low = cy + gap
    lower = _spline([left, (150 - mw * 0.5, low + 1), (150, low + 1.5), (150 + mw * 0.5, low + 1), right,
                     (150 + mw * 0.5, low + 7.5 - curve * 0.3), (150, low + 8.5), (150 - mw * 0.5, low + 7.5 - curve * 0.3)],
                    closed=True, n=6)
    d.polygon(_p(lower), fill=tuple(min(255, c + 8) for c in lip[:3]) + (255,), outline=line)
    d.line(_p([(150 - mw * 0.4, low + 4), (150 + mw * 0.4, low + 4)]), fill=(242, 236, 240, 200), width=SS)
    return img


class AndroidFace:
    """顔の部品の画像をまとめて作る。parts[種類][名前] = PIL.Image (透明な背景)"""

    def __init__(self, height, gender="female"):
        style = STYLES.get(gender, STYLES["female"])
        self.size = (max(1, round(W * height / H)), max(1, round(height)))

        def fit(img):
            return img.resize(self.size, Image.LANCZOS)

        self.parts = {
            "base": {"": fit(_base(style))},
            "ring": {f"{c}_{b}": fit(_ring(rgb, 1.0 if b == "on" else 0.45))
                     for c, rgb in RING_COLORS.items() for b in ("on", "dim")},
            "brows": {b: fit(_brows(b, style)) for b in BROWS},
            "eyes": {e: fit(_eyes(e, style)) for e in EYES},
            "mouth": {f"{m}_{lv}": fit(_mouth(m, lv, style)) for m in MOUTHS for lv in range(MOUTH_LEVELS)},
        }


if __name__ == "__main__":  # 試しに表情の一覧を書き出す
    import sys
    sheet = Image.new("RGBA", (W * 4, H * 2), (5, 7, 13, 255))
    looks = [("female", "cyan_on", "neutral", "open", "neutral_0"), ("female", "cyan_on", "raised", "happy", "smile_2"),
             ("female", "cyan_dim", "sad", "half", "sad_0"), ("female", "amber_on", "think", "up", "neutral_0"),
             ("male", "cyan_on", "neutral", "open", "neutral_3"), ("male", "blue_on", "raised", "wide", "o_2"),
             ("male", "cyan_on", "raised", "happy", "smile_1"), ("female", "cyan_on", "neutral", "closed", "neutral_4")]
    faces = {g: AndroidFace(H, g) for g in ("female", "male")}
    for i, (g, ring, brow, eye, mouth) in enumerate(looks):
        p = faces[g].parts
        frame = p["base"][""].copy()
        for layer in (p["ring"][ring], p["brows"][brow], p["eyes"][eye], p["mouth"][mouth]):
            frame.alpha_composite(layer)
        sheet.alpha_composite(frame, ((i % 4) * W, (i // 4) * H))
    sheet.save(sys.argv[1] if len(sys.argv) > 1 else "android_preview.png")

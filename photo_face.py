"""写真のような顔の画像に動きを付ける (まばたき・口の開閉・笑顔/困り顔・光る窓のふち)

顔の画像と、目・口の位置 (FACES の設定) から、android_face.AndroidFace と同じ形の
部品 (base / ring / brows / eyes / mouth) を作る。画面ではそれを重ねて表情を作る。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

FACES_DIR = Path(__file__).with_name("faces")

# 顔の画像ごとの設定 (元の画像の座標)
FACES = {
    "female": {  # 銀白の髪の精霊の守護者 (画像生成 AI で作成)
        "file": "guardian.jpg",
        "crop": (70, 40, 610, 840),             # 使う範囲 (右下のロゴは入れない)
        "eyes": [(280, 351, 31, 13), (410, 352, 31, 13)],  # 目の中心 x, y と 横/縦の半径
        "mouth": (339, 512, 46),                # 唇の合わせ目の中心 x, y と 口の半分の幅
    },
    "male": {  # 銀白の髪の精霊の守護者・男性版 (画像生成 AI で作成)
        "file": "guardian_male.jpg",
        "crop": (60, 40, 610, 840),
        "eyes": [(262, 345, 30, 12), (382, 344, 30, 12)],
        "mouth": (322, 502, 42),
    },
}
EYES = ("open", "wide", "up", "happy", "half", "closed")
BROWS = ("neutral", "raised", "sad", "think")
MOUTHS = ("neutral", "smile", "sad", "o")
MOUTH_LEVELS = 5
# 窓のふちの色 (名前は画面側と共通: cyan=待機中・話している間, blue=聞いている間, amber=考え中)
RING_COLORS = {"cyan": (232, 196, 128), "blue": (150, 215, 200), "amber": (255, 140, 60)}


def available(gender):
    cfg = FACES.get(gender)
    return bool(cfg) and (FACES_DIR / cfg["file"]).exists()


def _clear(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _oval_mask(size, inset, feather):
    w, h = size
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).ellipse([inset, inset, w - inset, h - inset], fill=255)
    return m.filter(ImageFilter.GaussianBlur(feather))


def _eyelid(photo, eye, cover):
    """目の上にまぶたを描く (cover: 0〜1、1で完全に閉じる)
    目の部分を周りの肌で埋め (画像修復)、閉じたまぶたの線を描き足す"""
    import cv2
    x, y, rx, ry = eye
    top = y - ry - 2
    bottom = top + (2 * ry + 4) * cover                       # まぶたの下の端
    pad = 14
    box = (int(x - rx - pad), int(y - ry - pad), int(x + rx + pad), int(y + ry + pad))
    patch = np.asarray(photo.crop(box).convert("RGB"))
    hole = np.zeros(patch.shape[:2], np.uint8)
    cv2.ellipse(hole, (x - box[0], y - box[1]), (rx + 3, ry + 4), 0, 0, 360, 255, -1)
    skin = cv2.inpaint(patch, hole, 9, cv2.INPAINT_TELEA)       # 目を肌で埋めた画像
    skin = cv2.GaussianBlur(skin, (0, 0), 1.2)
    lid = Image.fromarray(skin).convert("RGBA")
    # まぶたが覆う範囲 (目の形の上から bottom まで) だけ見せる
    mask = Image.new("L", lid.size, 0)
    ImageDraw.Draw(mask).ellipse([pad - 4, pad - 5, lid.width - pad + 4, lid.height - pad + 5], fill=255)
    cut = Image.new("L", lid.size, 0)
    ImageDraw.Draw(cut).rectangle([0, 0, lid.width, bottom - box[1]], fill=255)
    lid.putalpha(ImageChops.multiply(mask, cut).filter(ImageFilter.GaussianBlur(1.5)))
    d = ImageDraw.Draw(lid)  # まつげの線 (閉じるほど下向きの弧)
    by = bottom - box[1]
    sag = ry * 0.45 * cover
    cx = x - box[0]
    pts = [(cx - rx, by - sag * 0.6), (cx - rx * 0.5, by - sag * 0.1), (cx, by), (cx + rx * 0.5, by - sag * 0.1),
           (cx + rx, by - sag * 0.6)]
    d.line(pts, fill=(54, 40, 44, 210), width=2, joint="curve")
    layer = _clear(photo.size)
    layer.alpha_composite(lid.filter(ImageFilter.GaussianBlur(0.4)), (box[0], box[1]))
    return layer


def _mouth(photo, mouth, shape, level):
    """口の部分を作り直す (口角の上げ下げと、開き具合)"""
    mx, my, hw = mouth
    if shape == "neutral" and level == 0:
        return _clear(photo.size)
    import cv2
    box = (mx - hw - 22, my - 26, mx + hw + 22, my + 86)  # あごまで含める (口を開けるとあごも下がる)
    patch = np.asarray(photo.crop(box).convert("RGB")).astype(float)
    ph, pw = patch.shape[:2]
    cy = my - box[1]
    gap = {"o": max(level, 2)}.get(shape, level) * 4.6
    curve = {"smile": -4.5, "sad": 3.5}.get(shape, 0.0)   # 口角の動き (マイナスで上がる)
    out = np.empty_like(patch)
    rows = np.arange(ph).astype(float)
    inner = np.zeros((ph, pw))   # 口の中が見える範囲
    teeth = np.zeros((ph, pw))   # 上の歯がのぞく範囲
    fade_end = ph - 10           # ここより下は元の写真のまま (つなぎ目を出さない)
    for col in range(pw):
        t = (col - (pw / 2)) / hw
        bend = curve * min(1.0, t * t) * (1 if abs(t) <= 1.25 else max(0, 1 - (abs(t) - 1.25) * 3))
        open_here = gap * max(0.0, 1 - (t * (1.6 if shape == "o" else 1.0)) ** 2)
        up = open_here * 0.14        # 上唇も少し上がる
        down = open_here - up        # 下唇とあごが下がる
        src = rows - bend * np.exp(-((rows - cy) / 14.0) ** 2)             # 唇のまわりを曲げる
        src = src + np.where(rows <= cy - up, up * np.exp(-((cy - up - rows) / 9.0) ** 2), 0.0)
        # 下唇はそのまま下がり、あごに向かって少しずつ元の位置に戻る
        src = src - np.where(rows >= cy + down,
                             down * np.clip((fade_end - rows) / max(1.0, fade_end - cy - down), 0, 1), 0.0)
        src = np.clip(src, 0, ph - 1)
        lo = np.floor(src).astype(int)
        hi = np.minimum(lo + 1, ph - 1)
        f = (src - lo)[:, None]
        out[:, col] = patch[lo, col] * (1 - f) + patch[hi, col] * f
        if open_here > 0.6:
            top, bottom = cy + bend - up, cy + bend + down
            inner[:, col] = np.clip(np.minimum(rows - top, bottom - rows) + 0.5, 0, 1)
            if open_here > 6 and shape != "o":
                edge = top + min(open_here * 0.36, 6.5) * max(0.0, 1 - (t / 0.72) ** 2)
                teeth[:, col] = np.clip(np.minimum(rows - top, edge - rows) + 0.5, 0, 1)
    if gap > 0.5:  # 口の中 (奥ほど暗い) と、上の歯
        depth = np.clip((rows - cy) / max(gap, 1.0), 0, 1)[:, None, None]
        dark = np.array([74.0, 30, 38]) * (1 - depth) + np.array([30.0, 11, 17]) * depth
        a = cv2.GaussianBlur(inner, (0, 0), 1.2)[:, :, None]
        out = out * (1 - a) + dark * a
        a = (cv2.GaussianBlur(teeth, (0, 0), 1.2) * inner)[:, :, None] * 0.8
        out = out * (1 - a) + np.array([216.0, 202, 198]) * a
    img = Image.fromarray(out.clip(0, 255).astype(np.uint8)).convert("RGBA")
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).ellipse([4, 4, pw - 4, ph - 4], fill=255)
    img.putalpha(mask.filter(ImageFilter.GaussianBlur(6)))  # ふちを透明にぼかして写真になじませる
    layer = _clear(photo.size)
    layer.alpha_composite(img, (box[0], box[1]))
    return layer


def _ring(size, color, bright):
    """顔の窓のふちの光 (状態で色が変わる)"""
    w, h = size
    glow = _clear(size)
    d = ImageDraw.Draw(glow)
    inset = int(min(w, h) * 0.035)
    d.ellipse([inset, inset, w - inset, h - inset], outline=color + (int(255 * bright),), width=max(3, w // 90))
    out = glow.filter(ImageFilter.GaussianBlur(max(4, w // 45)))
    ImageDraw.Draw(out).ellipse([inset, inset, w - inset, h - inset], outline=color + (int(200 * bright),),
                                width=max(1, w // 250))
    return out


class PhotoFace:
    """AndroidFace と同じ形の部品を、写真のような顔の画像から作る"""

    def __init__(self, height, gender="female"):
        cfg = FACES[gender]
        photo = Image.open(FACES_DIR / cfg["file"]).convert("RGBA")
        crop = cfg["crop"]
        scale = height / (crop[3] - crop[1])
        self.size = (round((crop[2] - crop[0]) * scale), round(height))

        def fit(layer):
            return layer.crop(crop).resize(self.size, Image.LANCZOS)

        # 土台: 楕円の窓の中に顔。ふちは宇宙に溶けこむようにぼかす
        base = photo.copy()
        frame = base.crop(crop)
        alpha = _oval_mask(frame.size, int(frame.size[0] * 0.05), frame.size[0] * 0.03)
        frame.putalpha(alpha)
        base = _clear(photo.size)
        base.paste(frame, (crop[0], crop[1]))
        eyes = cfg["eyes"]
        eye_layers = {
            "open": _clear(photo.size), "wide": _clear(photo.size), "up": _clear(photo.size),
            "happy": self._both(photo, eyes, 0.3), "half": self._both(photo, eyes, 0.55),
            "closed": self._both(photo, eyes, 1.0),
        }
        self.parts = {
            "base": {"": fit(base)},
            "ring": {f"{c}_{b}": _ring(self.size, rgb, 1.0 if b == "on" else 0.45)
                     for c, rgb in RING_COLORS.items() for b in ("on", "dim")},
            "brows": {b: _clear(self.size) for b in BROWS},
            "eyes": {name: fit(layer) for name, layer in eye_layers.items()},
            "mouth": {f"{m}_{lv}": fit(_mouth(photo.convert("RGB"), cfg["mouth"], m, lv))
                      for m in MOUTHS for lv in range(MOUTH_LEVELS)},
        }

    @staticmethod
    def _both(photo, eyes, cover):
        layer = _clear(photo.size)
        for eye in eyes:
            layer.alpha_composite(_eyelid(photo.convert("RGB"), eye, cover))
        return layer


if __name__ == "__main__":  # 試しに表情の一覧を書き出す
    import sys
    f = PhotoFace(500, sys.argv[2] if len(sys.argv) > 2 else "female")
    w, h = f.size
    looks = [("cyan_on", "open", "neutral_0"), ("cyan_on", "happy", "smile_0"), ("cyan_on", "open", "neutral_3"),
             ("amber_on", "half", "neutral_0"), ("cyan_on", "closed", "neutral_0"), ("blue_on", "open", "sad_0"),
             ("cyan_on", "open", "smile_3"), ("cyan_dim", "open", "o_2")]
    sheet = Image.new("RGBA", (w * 4, h * 2), (5, 20, 40, 255))
    for i, (ring, eye, mouth) in enumerate(looks):
        frame = f.parts["base"][""].copy()
        for layer in (f.parts["ring"][ring], f.parts["eyes"][eye], f.parts["mouth"][mouth]):
            frame.alpha_composite(layer)
        sheet.alpha_composite(frame, ((i % 4) * w, (i // 4) * h))
    sheet.save(sys.argv[1] if len(sys.argv) > 1 else "photo_face_preview.png")

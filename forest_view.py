"""JARVIS の画面: 幻想的な森 (顔の雰囲気に合わせた背景)

- 夜の森の木々のシルエット (奥と手前の2層)、顔の後ろの月のような光、地面の霧
- ふわふわ漂うホタル (精霊の光) と、ひらひら舞い落ちる秋の葉
- 状態で動きが変わる: 考え中はホタルが顔のまわりを回り、話している間は光が脈打つ
- 時間帯 (朝・昼・夕方・夜) と天気 (晴れ・くもり・霧・雨・雪・雷) で色と雰囲気が変わる
- 顔 (表情・まばたき・口の動き) は SpaceView の仕組みをそのまま使う
"""
import math
import random
from pathlib import Path

from space_view import SpaceView, blend, time_of_day

# 背景の絵 (画像生成 AI で作成)。あれば森のシルエットの代わりに使う
BACKGROUND = {
    "file": Path(__file__).with_name("backgrounds") / "night.jpg",
    "crop": (0, 0, 1024, 540),        # 使う範囲 (右下のロゴは入れない)
    "fire": (488, 505),               # たき火の位置 (元の画像の座標)
    "sky_bottom": 0.42,               # 星が見える範囲 (上からの割合)
}
# 時間帯ごとの色合い (夜の絵に重ねる色と割合、明るさ)
BG_TINTS = {"朝": ("#ff9fb0", 0.22, 1.15), "昼": ("#9fd0ff", 0.30, 1.45), "夕方": ("#ff9a40", 0.28, 1.1),
            "夜": (None, 0, 1.0)}

# 時間帯ごとの色: 空の上/下, 顔の後ろの光, 木 (奥/手前), 霧, ホタル
TIME_THEMES = {
    "朝": {"sky": ("#2a1a2e", "#5a3a40"), "glow": "#ffd2b4", "trees": ("#1c1220", "#0e0910"),
           "mist": "#6a4a52", "light": "#ffd8a8"},
    "昼": {"sky": ("#1c2f28", "#3d5a48"), "glow": "#fff0c8", "trees": ("#14231c", "#0a130e"),
           "mist": "#557060", "light": "#fff2b0"},
    "夕方": {"sky": ("#24120e", "#6a3418"), "glow": "#ffb060", "trees": ("#160a06", "#0b0503"),
            "mist": "#6a3a22", "light": "#ffc070"},
    "夜": {"sky": ("#081216", "#132a26"), "glow": "#d8e4f0", "trees": ("#0a1412", "#040908"),
           "mist": "#1e3834", "light": "#d8f0a0"},
}
# 天気ごとの雰囲気: 混ぜる色と割合, 光の強さ, 落ち葉の量, 演出
WEATHER_THEMES = {
    "晴れ": {"mix": None, "light": 1.0, "leaves": 1.0, "effect": None},
    "くもり": {"mix": ("#3a4044", 0.35), "light": 0.75, "leaves": 0.8, "effect": None},
    "霧": {"mix": ("#5a6466", 0.45), "light": 0.6, "leaves": 0.5, "effect": "fog"},
    "雨": {"mix": ("#1e2a34", 0.4), "light": 0.6, "leaves": 0.3, "effect": "rain"},
    "雪": {"mix": ("#8a96a8", 0.3), "light": 0.85, "leaves": 0.0, "effect": "snow"},
    "雷": {"mix": ("#20183a", 0.4), "light": 0.55, "leaves": 0.3, "effect": "thunder"},
}
LEAF_COLORS = ["#d9722b", "#e8a33a", "#b8461f", "#f0c060", "#c25a26", "#8f3a1a"]
LEAF_SHAPE = [(0, -1), (0.45, -0.45), (0.5, 0.1), (0.25, 0.6), (0, 1), (-0.25, 0.6), (-0.5, 0.1), (-0.45, -0.45)]

GOLD, IVORY = "#e0b56a", "#f3ead8"


class ForestView(SpaceView):
    FIREFLIES = 45
    LEAVES = 26
    FACE_HEIGHT = 0.55

    def __init__(self, canvas, get_mode, title="JARVIS"):  # SpaceView の宇宙は作らず、森を作る
        self.c = canvas
        self.get_mode = get_mode
        self.w, self.h = int(canvas["width"]), int(canvas["height"])
        self.cx, self.cy = self.w / 2, self.h / 2
        self.k = 1.0
        self.t = 0
        self.flash = 0
        self.place = ""
        self.time_key, self.weather_key = None, None
        self.theme = None
        self.glow_ids = []

        self.flies = [self._new_fly() for _ in range(self.FIREFLIES)]
        for f in self.flies:
            f["halo"] = canvas.create_oval(0, 0, 0, 0, outline="", tags="fly")
            f["dot"] = canvas.create_oval(0, 0, 0, 0, outline="", tags="fly")
        self.leaves = [self._new_leaf(random.uniform(0, 1)) for _ in range(self.LEAVES)]
        for lf in self.leaves:
            lf["id"] = canvas.create_polygon(0, 0, 0, 0, 0, 0, outline="", fill=lf["color"], smooth=True,
                                             tags="leaf")
        self.drops = [{"x": random.uniform(0, self.w), "y": random.uniform(0, self.h), "v": random.uniform(0.7, 1.3),
                       "id": canvas.create_line(0, 0, 0, 0, fill="#9cc0d8", width=1, state="hidden")}
                      for _ in range(80)]
        self.flakes = [{"x": random.uniform(0, self.w), "y": random.uniform(0, self.h),
                        "r": random.uniform(1, 2.8), "phase": random.uniform(0, 6.3),
                        "id": canvas.create_oval(0, 0, 0, 0, fill="#ffffff", outline="", state="hidden")}
                       for _ in range(60)]

        self.use_image = BACKGROUND["file"].exists()
        self.bg_photo = None
        self.stars = [{"fx": random.random(), "fy": random.random() ** 1.3 * BACKGROUND["sky_bottom"],
                       "r": random.uniform(0.6, 1.6), "phase": random.uniform(0, 6.3),
                       "id": canvas.create_oval(0, 0, 0, 0, outline="", fill="#ffffff", tags="star",
                                                state="normal" if self.use_image else "hidden")}
                      for _ in range(70)]
        self.sparks = [{"x": 0, "y": 0, "life": random.randint(0, 40), "vx": 0.0,
                        "id": canvas.create_oval(0, 0, 0, 0, outline="", fill="#ffb347", tags="fire",
                                                 state="normal" if self.use_image else "hidden")}
                       for _ in range(16)]
        self.fire_xy = (0, 0)

        self._build_face()
        self.shadow = canvas.create_text(0, 0, text=title, fill="#2a1c0c", font=("Georgia", 18, "bold"))
        self.title = canvas.create_text(0, 0, text=title, fill=IVORY, font=("Georgia", 18, "bold"))
        self.label = canvas.create_text(12, 12, text="", anchor="nw", fill="#c8b48a", font=("Meiryo", 9))
        self.set_theme(time_of_day(), "晴れ")
        self._layout_face()

    # ---------- 色と背景 ----------
    def set_theme(self, time_key, weather_key):
        if (time_key, weather_key) == (self.time_key, self.weather_key):
            return
        self.time_key, self.weather_key = time_key, weather_key
        base, wx = TIME_THEMES[time_key], WEATHER_THEMES[weather_key]
        mix = (lambda col: blend(col, *wx["mix"])) if wx["mix"] else (lambda col: col)
        self.theme = {"sky": tuple(mix(x) for x in base["sky"]), "glow": mix(base["glow"]),
                      "trees": tuple(mix(x) for x in base["trees"]), "mist": mix(base["mist"]),
                      "light": base["light"], "level": wx["light"], "leaves": wx["leaves"],
                      "effect": wx["effect"], "bg": mix(base["sky"][0])}
        self._draw_scenery()
        effect = wx["effect"]
        for d in self.drops:
            self.c.itemconfigure(d["id"], state="normal" if effect in ("rain", "thunder") else "hidden")
        for f in self.flakes:
            self.c.itemconfigure(f["id"], state="normal" if effect == "snow" else "hidden")
        for i, lf in enumerate(self.leaves):
            show = i < round(self.LEAVES * wx["leaves"])
            self.c.itemconfigure(lf["id"], state="normal" if show else "hidden")
        place = f"{self.place} " if self.place else ""
        self.c.itemconfigure(self.label, text=f"{place}{weather_key}・{time_key}")

    def _draw_scenery(self):
        """空・顔の後ろの光・木々・霧を描く (時間帯や画面の大きさが変わったら描き直す)"""
        c, th, w, h = self.c, self.theme, self.w, self.h
        c.configure(bg=th["sky"][0])
        for tag in ("sky", "glow", "trees_far", "mist", "trees_near"):
            c.delete(tag)
        if self.use_image:
            self._draw_background_image()
            return
        bands = 24  # 空のグラデーション
        for i in range(bands):
            y1, y2 = h * i / bands, h * (i + 1) / bands + 1
            c.create_rectangle(0, y1, w, y2, outline="", fill=blend(th["sky"][0], th["sky"][1], i / (bands - 1)),
                               tags="sky")
        self.glow_ids = []  # 顔の後ろの月のような光
        for i in range(16):
            t = i / 15
            self.glow_ids.append((c.create_oval(0, 0, 0, 0, outline="", tags="glow",
                                                fill=blend(th["sky"][1], th["glow"], (t ** 1.8) * 0.55 * th["level"])),
                                  1 - t))
        rnd = random.Random(7)
        self._draw_trees("trees_far", th["trees"][0], rnd, count=int(w / 55), height=(0.45, 0.8), width=(10, 22))
        for i in range(10):  # 地面の霧
            t = i / 9
            rx, ry = w * (0.75 - 0.3 * t), h * (0.16 - 0.08 * t)
            c.create_oval(w / 2 - rx, h - ry * 0.6, w / 2 + rx, h + ry, outline="", tags="mist",
                          fill=blend(th["sky"][1], th["mist"], 0.25 + 0.6 * t * (1.4 if th["effect"] == "fog" else 1)))
        self._draw_trees("trees_near", th["trees"][1], rnd, count=6, height=(0.85, 1.05), width=(26, 48),
                         sides_only=True)
        for tag in ("trees_near", "mist", "trees_far", "glow", "sky"):  # 重なり順: 空が一番奥
            c.tag_lower(tag)
        self._place_glow(1.0)

    def _draw_background_image(self):
        """背景の絵を画面いっぱいに敷き、時間帯と天気の色合いを付ける"""
        from PIL import Image, ImageEnhance, ImageTk
        c, th, w, h = self.c, self.theme, self.w, self.h
        img = Image.open(BACKGROUND["file"]).convert("RGB").crop(BACKGROUND["crop"])
        iw, ih = img.size
        scale = max(w / iw, h / ih)  # 画面をすき間なく覆う大きさ
        img = img.resize((max(1, round(iw * scale)), max(1, round(ih * scale))), Image.LANCZOS)
        ox, oy = (w - img.width) / 2, (h - img.height) / 2
        tint, amount, bright = BG_TINTS[self.time_key]
        img = ImageEnhance.Brightness(img).enhance(bright * (0.6 + 0.4 * th["level"]))
        if tint:
            img = Image.blend(img, Image.new("RGB", img.size, tint), amount)
        wx = th["effect"]
        if self.weather_key in ("くもり", "霧", "雨", "雷"):  # 天気が悪いと色がくすむ
            img = ImageEnhance.Color(img).enhance(0.6)
        if wx == "fog":
            img = Image.blend(img, Image.new("RGB", img.size, "#8a9496"), 0.35)
        self.bg_photo = ImageTk.PhotoImage(img)
        c.create_image(ox, oy, image=self.bg_photo, anchor="nw", tags="sky")
        c.tag_lower("sky")
        # 星とたき火の位置 (元の画像の座標 → 画面の座標)
        for st in self.stars:
            st["x"], st["y"] = ox + st["fx"] * img.width, oy + st["fy"] * img.height
        fx, fy = BACKGROUND["fire"]
        cx0, cy0 = BACKGROUND["crop"][:2]
        self.fire_xy = (ox + (fx - cx0) * scale, oy + (fy - cy0) * scale)
        self.bg_scale = scale
        star_state = "normal" if th["level"] > 0.7 and self.time_key in ("夜", "朝", "夕方") else "hidden"
        for st in self.stars:
            c.itemconfigure(st["id"], state=star_state)

    def _animate_background(self, mode):
        """星のまたたきと、たき火から立ちのぼる火の粉"""
        c, s = self.c, getattr(self, "bg_scale", 1.0)
        for st in self.stars:
            tw = 0.5 + 0.5 * math.sin(self.t * 0.06 + st["phase"] * 5)
            r = st["r"] * (0.6 + 0.6 * tw) * max(1.0, s * 0.8)
            c.coords(st["id"], st["x"] - r, st["y"] - r, st["x"] + r, st["y"] + r)
            c.itemconfigure(st["id"], fill=blend("#2a3a60", "#ffffff", 0.35 + 0.65 * tw))
        fx, fy = self.fire_xy
        for sp in self.sparks:  # 立ちのぼる火の粉
            sp["life"] -= 1
            if sp["life"] <= 0:
                sp.update(x=fx + random.uniform(-12, 12) * s, y=fy - 10 * s, life=random.randint(25, 60),
                          vx=random.uniform(-0.4, 0.4))
            sp["y"] -= 1.3 * s
            sp["x"] += sp["vx"] * s + math.sin(self.t * 0.2 + sp["life"]) * 0.3
            r = max(1.0, 1.6 * s * sp["life"] / 60)
            c.coords(sp["id"], sp["x"] - r, sp["y"] - r, sp["x"] + r, sp["y"] + r)
            c.itemconfigure(sp["id"], fill=blend("#5a2a10", "#ffd27a", min(1, sp["life"] / 40)))

    def _draw_trees(self, tag, color, rnd, count, height, width, sides_only=False):
        """木のシルエット (幹と枝葉の塊)"""
        c, w, h = self.c, self.w, self.h
        for i in range(count):
            if sides_only:  # 手前の木は画面の左右にだけ置く (顔を隠さない)
                x = rnd.uniform(-0.04, 0.16) * w if i % 2 == 0 else rnd.uniform(0.84, 1.04) * w
            else:
                x = (i + rnd.uniform(-0.3, 0.3)) / count * w
            top = h * (1 - rnd.uniform(*height))
            tw = rnd.uniform(*width) * self.k
            c.create_polygon(x - tw / 2, h, x - tw * 0.3, top + 40 * self.k, x, top, x + tw * 0.3, top + 40 * self.k,
                             x + tw / 2, h, fill=color, outline="", tags=tag)
            for j in range(rnd.randint(3, 6)):  # 枝葉
                bx = x + rnd.uniform(-1, 1) * tw * 2.4
                by = top + rnd.uniform(0, 0.45) * (h - top)
                r = rnd.uniform(1.2, 2.6) * tw
                c.create_oval(bx - r, by - r * 0.7, bx + r, by + r * 0.7, fill=color, outline="", tags=tag)
            for j in range(rnd.randint(2, 4)):  # 細い枝
                by = top + rnd.uniform(0.1, 0.6) * (h - top)
                side = rnd.choice((-1, 1))
                c.create_line(x, by, x + side * rnd.uniform(2, 4) * tw, by - rnd.uniform(1, 3) * tw,
                              fill=color, width=max(2, tw * 0.15), tags=tag)

    def _layout_face(self):
        super()._layout_face()
        size = max(14, min(34, round(18 * self.k)))  # 大きな画面でも大きすぎないように
        for item in (self.title, self.shadow):
            self.c.itemconfigure(item, font=("Georgia", size, "bold"))

    def _place_glow(self, pulse):
        x, y = self.face_center if hasattr(self, "face_center") else (self.cx, self.cy)
        R = self.face_height() * 0.78 * pulse
        for item, scale in self.glow_ids:
            r = R * (0.35 + 0.65 * scale)
            self.c.coords(item, x - r, y - r, x + r, y + r)

    def resize(self, w, h):
        if w < 50 or h < 50 or (w, h) == (self.w, self.h):
            return
        self.w, self.h = w, h
        self.cx, self.cy = w / 2, h / 2
        self.k = min(w / 400, h / 360)
        for d in self.drops + self.flakes:
            d["x"], d["y"] = random.uniform(0, w), random.uniform(0, h)
        for f in self.flies:
            f["x"], f["y"] = random.uniform(0, w), random.uniform(h * 0.15, h)
        self._layout_face()
        self._draw_scenery()

    # ---------- ホタルと落ち葉 ----------
    def _new_fly(self):
        return {"x": random.uniform(0, self.w), "y": random.uniform(self.h * 0.15, self.h),
                "vx": random.uniform(-0.3, 0.3), "vy": random.uniform(-0.25, 0.05),
                "phase": random.uniform(0, 6.3), "size": random.uniform(1.2, 2.6), "angle": random.uniform(0, 6.3)}

    def _new_leaf(self, y_frac=0.0):
        return {"x": random.uniform(-0.1, 1.0) * self.w, "y": -20 + y_frac * self.h,
                "vx": random.uniform(0.4, 1.2), "vy": random.uniform(0.5, 1.2), "angle": random.uniform(0, 6.3),
                "spin": random.uniform(-0.06, 0.06), "flip": random.uniform(0, 6.3),
                "size": random.uniform(5, 10), "color": random.choice(LEAF_COLORS)}

    def step(self):
        if self.t % 900 == 0:  # 30秒ごとに時間帯を確認
            self.set_theme(time_of_day(), self.weather_key)
        mode = self.get_mode()
        self.t += 1
        c, th, k = self.c, self.theme, self.k
        fx, fy = getattr(self, "face_center", (self.cx, self.cy))

        if self.use_image:
            self._animate_background(mode)

        # 顔の後ろの光 (話している間は脈打つ)
        amp = 0.05 if mode == "speaking" else 0.015
        self._place_glow(1 + amp * math.sin(self.t * (0.3 if mode == "speaking" else 0.05)))

        # ホタル: ふわふわ漂い、考え中は顔のまわりを回る
        bright = th["level"] * (1.25 if mode == "speaking" else 1.0)
        orbit_r = self.face_height() * 0.62
        for f in self.flies:
            if mode == "thinking":
                f["angle"] += 0.03
                tx = fx + orbit_r * math.cos(f["angle"] + f["phase"]) * 0.85
                ty = fy + orbit_r * math.sin(f["angle"] + f["phase"]) * 0.75
                f["x"] += (tx - f["x"]) * 0.04
                f["y"] += (ty - f["y"]) * 0.04
            else:
                f["x"] += f["vx"] * k + math.sin(self.t * 0.02 + f["phase"]) * 0.4 * k
                f["y"] += f["vy"] * k + math.cos(self.t * 0.017 + f["phase"]) * 0.3 * k
                if not (-20 < f["x"] < self.w + 20 and self.h * 0.05 < f["y"] < self.h + 20):
                    f.update(self._new_fly())
            twinkle = 0.5 + 0.5 * math.sin(self.t * 0.07 + f["phase"] * 3)
            r = f["size"] * k
            c.coords(f["dot"], f["x"] - r, f["y"] - r, f["x"] + r, f["y"] + r)
            c.coords(f["halo"], f["x"] - r * 4, f["y"] - r * 4, f["x"] + r * 4, f["y"] + r * 4)
            dark = "#3a3424" if self.use_image else th["sky"][1]
            c.itemconfigure(f["dot"], fill=blend(dark, th["light"], min(1, (0.45 + 0.55 * twinkle) * bright)))
            if self.use_image:  # 絵の背景には単色のぼかしがなじまないので、光の点だけにする
                c.itemconfigure(f["halo"], state="hidden")
            else:
                c.itemconfigure(f["halo"], fill=blend(th["sky"][1], th["light"], min(1, 0.18 * twinkle * bright)))

        # 秋の葉: 風に流されながら、くるくる回って落ちる
        wind = 1.0 + (0.8 if mode == "thinking" else 0.0) + 0.4 * math.sin(self.t * 0.01)
        for lf in self.leaves:
            lf["x"] += lf["vx"] * wind * k + math.sin(self.t * 0.04 + lf["flip"]) * 0.6 * k
            lf["y"] += lf["vy"] * k
            lf["angle"] += lf["spin"] * wind
            lf["flip"] += 0.05
            if lf["y"] > self.h + 20 or lf["x"] > self.w + 30:
                lf.update({key: v for key, v in self._new_leaf().items() if key != "color"})
            s = lf["size"] * k
            sx = s * (0.35 + 0.65 * abs(math.cos(lf["flip"])))  # ひらひら裏返る
            ca, sa = math.cos(lf["angle"]), math.sin(lf["angle"])
            pts = []
            for px, py in LEAF_SHAPE:
                x, y = px * sx, py * s
                pts += [lf["x"] + x * ca - y * sa, lf["y"] + x * sa + y * ca]
            c.coords(lf["id"], *pts)

        # 天気の演出
        effect = th["effect"]
        if effect in ("rain", "thunder"):
            for d in self.drops:
                d["y"] += 10 * d["v"]
                d["x"] -= 2 * d["v"]
                if d["y"] > self.h or d["x"] < 0:
                    d["x"], d["y"] = random.uniform(0, self.w + 60), random.uniform(-40, 0)
                c.coords(d["id"], d["x"], d["y"], d["x"] + 2.5, d["y"] - 13)
        if effect == "snow":
            for f in self.flakes:
                f["y"] += 0.6 + f["r"] * 0.3
                f["x"] += math.sin(self.t * 0.03 + f["phase"]) * 0.6
                if f["y"] > self.h:
                    f["x"], f["y"] = random.uniform(0, self.w), -5
                r = f["r"]
                c.coords(f["id"], f["x"] - r, f["y"] - r, f["x"] + r, f["y"] + r)
        if effect == "thunder":
            if self.flash == 0 and random.random() < 0.004:
                self.flash = 8
            if self.flash and not self.use_image:
                c.itemconfigure("sky", fill=blend(th["sky"][0], "#d8e8ff", 0.6 if self.flash in (8, 7, 4) else 0.05))
                self.flash -= 1
                if self.flash == 0:
                    self._draw_scenery()

        # 重なり順: 背景 → 星・たき火 → ホタル・落ち葉 → 顔 → 文字
        c.tag_raise("star", "sky")
        c.tag_raise("fire", "star")
        c.tag_raise("face")
        for item in (self.shadow, self.title, self.label):
            c.tag_raise(item)
        self._animate_face(mode)

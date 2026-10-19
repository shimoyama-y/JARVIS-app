"""JARVIS の画面の宇宙空間アニメーション

- 奥から星が飛んでくるワープ空間と、色のついた星雲
- 中央に光る星の核、そのまわりを回る惑星と軌道
- 状態で動きが変わる: 待機中はゆっくり、考え中はワープ加速、話している間は核が脈打つ
- 時間帯 (朝・昼・夕方・夜) と天気 (晴れ・くもり・霧・雨・雪・雷) で色と雰囲気が変わる
"""
import math
import random
import traceback
from datetime import datetime


def blend(c1, c2, t):
    """2つの色を t (0〜1) の割合で混ぜる"""
    t = max(0.0, min(1.0, t))  # 範囲外だと存在しない色になり、描画が止まってしまう
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


# 時間帯ごとの色: 背景, 星雲3つ, 核の外側/中心, 星の色, 星の明るさ
TIME_THEMES = {
    "朝": {"bg": "#120b1e", "nebula": ["#5a2140", "#6b3a12", "#2b1a52"],
           "core": ("#2a1408", "#fff1d6"), "tints": ["#ffe9c7", "#ffd1dc", "#ffffff"], "stars": 0.8},
    "昼": {"bg": "#06203a", "nebula": ["#0f4a7a", "#1c6a8a", "#2a3f8a"],
           "core": ("#0b3050", "#ffffff"), "tints": ["#ffffff", "#d6f3ff"], "stars": 0.55},
    "夕方": {"bg": "#150818", "nebula": ["#7a2a12", "#4a1452", "#7a5212"],
            "core": ("#2a0c08", "#ffd9a8"), "tints": ["#ffd9a8", "#ffb4a2", "#ffffff"], "stars": 0.85},
    "夜": {"bg": "#05070d", "nebula": ["#2b1052", "#0b3a4a", "#3a0f3a"],
           "core": ("#071a2e", "#e0fbff"), "tints": ["#ffffff", "#bfefff", "#ffe9c7", "#e3d0ff"], "stars": 1.0},
}

# 天気ごとの雰囲気: 星雲に混ぜる色と割合, 星の明るさ, 演出
WEATHER_THEMES = {
    "晴れ": {"mix": None, "stars": 1.0, "effect": None},
    "くもり": {"mix": ("#3a4250", 0.55), "stars": 0.6, "effect": None},
    "霧": {"mix": ("#5a6470", 0.7), "stars": 0.4, "effect": None},
    "雨": {"mix": ("#1e2a3a", 0.5), "stars": 0.5, "effect": "rain"},
    "雪": {"mix": ("#8a96a8", 0.4), "stars": 0.7, "effect": "snow"},
    "雷": {"mix": ("#20183a", 0.5), "stars": 0.5, "effect": "thunder"},
}


def time_of_day(now=None):
    h = (now or datetime.now()).hour
    return "朝" if 5 <= h < 9 else "昼" if 9 <= h < 16 else "夕方" if 16 <= h < 19 else "夜"


def weather_kind(code):
    """wttr.in の天気コードを 晴れ/くもり/霧/雨/雪/雷 に分ける"""
    code = int(code)
    if code in (200, 386, 389, 392, 395):
        return "雷"
    if code in (179, 182, 185, 227, 230, 281, 284, 311, 314, 317, 320, 323, 326, 329, 332,
                335, 338, 350, 362, 365, 368, 371, 374, 377):
        return "雪"
    if code in (176, 263, 266, 293, 296, 299, 302, 305, 308, 353, 356, 359):
        return "雨"
    if code in (143, 248, 260):
        return "霧"
    if code in (116, 119, 122):
        return "くもり"
    return "晴れ"


class SpaceView:
    STARS = 260
    SPEEDS = {"idle": 0.006, "listening": 0.009, "thinking": 0.045, "speaking": 0.012}

    def __init__(self, canvas, get_mode, title="JARVIS"):
        self.c = canvas
        self.get_mode = get_mode
        self.w, self.h = int(canvas["width"]), int(canvas["height"])
        self.cx, self.cy = self.w / 2, self.h / 2
        self.k = 1.0  # 画面の大きさに合わせた拡大率 (基準は 400x360)
        self.speed = self.SPEEDS["idle"]
        self.t = 0
        self.flash = 0
        self.place = ""
        self.time_key, self.weather_key = None, None
        self.theme = None

        self.stars = [self._new_star(random.uniform(0.05, 1.0)) for _ in range(self.STARS)]
        for s in self.stars:
            s["id"] = canvas.create_line(0, 0, 0, 0, fill="#ffffff", capstyle="round")

        # 軌道 (少し傾いた楕円) と惑星
        self.orbits = [(150, 46, 0.010, "#7dd3fc", 7), (112, 34, -0.016, "#f0abfc", 5), (182, 58, 0.006, "#fcd34d", 9)]
        self.orbit_ids = [canvas.create_oval(self.cx - rx, self.cy - ry, self.cx + rx, self.cy + ry,
                                             outline="#12324a", width=1) for rx, ry, *_ in self.orbits]
        # 中央の核 (外側から内側へ明るくなる光の玉)
        self.core = [(canvas.create_oval(0, 0, 0, 0, outline=""), 1 - i / 13) for i in range(14)]
        self.planets = []
        for i, (rx, ry, sp, color, size) in enumerate(self.orbits):
            glow = canvas.create_oval(0, 0, 0, 0, outline="")
            body = canvas.create_oval(0, 0, 0, 0, outline="", fill=color)
            self.planets.append({"rx": rx, "ry": ry, "speed": sp, "size": size, "color": color,
                                 "angle": i * 2.1, "glow": glow, "body": body})
        self.meteor = canvas.create_line(0, 0, 0, 0, fill="#ffffff", width=2, state="hidden")
        self.meteor_pos = None

        # 雨・雪の粒 (天気が雨や雪のときだけ表示)
        self.drops = [{"x": random.uniform(0, self.w), "y": random.uniform(0, self.h),
                       "v": random.uniform(0.7, 1.3),
                       "id": canvas.create_line(0, 0, 0, 0, fill="#9cc7ff", width=1, state="hidden")}
                      for _ in range(70)]
        self.flakes = [{"x": random.uniform(0, self.w), "y": random.uniform(0, self.h),
                        "r": random.uniform(1, 2.6), "phase": random.uniform(0, 6.3),
                        "id": canvas.create_oval(0, 0, 0, 0, fill="#ffffff", outline="", state="hidden")}
                       for _ in range(55)]

        self._build_face()
        self.shadow = canvas.create_text(self.cx + 2, self.cy + 2, text=title, fill="#0b3b52",
                                         font=("Arial", 26, "bold"))
        self.title = canvas.create_text(self.cx, self.cy, text=title, fill="#e6fdff", font=("Arial", 26, "bold"))
        self.label = canvas.create_text(10, 10, text="", anchor="nw", fill="#9fb8c8", font=("Meiryo", 9))
        self.set_theme(time_of_day(), "晴れ")
        self._layout_face()

    # ---------- 色の切り替え ----------
    def set_weather(self, weather_key, place=""):
        """天気 (晴れ/くもり/霧/雨/雪/雷) を反映する。メインスレッドから呼ぶ"""
        self.place = place
        self.time_key = None  # ラベルを更新するため必ず描き直す
        self.set_theme(time_of_day(), weather_key)

    def set_theme(self, time_key, weather_key):
        if (time_key, weather_key) == (self.time_key, self.weather_key):
            return
        self.time_key, self.weather_key = time_key, weather_key
        base, wx = TIME_THEMES[time_key], WEATHER_THEMES[weather_key]
        nebula = [blend(col, *wx["mix"]) if wx["mix"] else col for col in base["nebula"]]
        self.theme = {"bg": base["bg"], "nebula": nebula, "tints": base["tints"],
                      "star_level": base["stars"] * wx["stars"], "effect": wx["effect"]}
        c = self.c
        c.configure(bg=base["bg"])
        c.delete("nebula")
        self._draw_nebula()
        outer, center = base["core"]
        for i, (item, _) in enumerate(self.core):
            c.itemconfigure(item, fill=blend(outer, center, (i / 13) ** 1.6))
        orbit = blend(base["bg"], "#5fa8c8", 0.35)
        for item in self.orbit_ids:
            c.itemconfigure(item, outline=orbit)
        for p in self.planets:
            c.itemconfigure(p["glow"], fill=blend(base["bg"], p["color"], 0.35))
        c.itemconfigure(self.shadow, fill=blend(base["bg"], "#0b3b52", 0.8))
        for s in self.stars:
            s["tint"] = random.choice(base["tints"])
        effect = self.theme["effect"]
        for d in self.drops:
            c.itemconfigure(d["id"], state="normal" if effect in ("rain", "thunder") else "hidden")
        for f in self.flakes:
            c.itemconfigure(f["id"], state="normal" if effect == "snow" else "hidden")
        place = f"{self.place} " if self.place else ""
        c.itemconfigure(self.label, text=f"{place}{weather_key}・{time_key}")

    def _draw_nebula(self):
        """色のついた星雲 (同心円を重ねてぼかしたように見せる)"""
        bg = self.theme["bg"]
        spots = [(0.22, 0.22, 150), (0.8, 0.65, 115), (0.75, 0.19, 90)]
        for (fx, fy, base_r), color in zip(spots, self.theme["nebula"]):
            x, y, r = self.w * fx, self.h * fy, base_r * self.k
            for i in range(18):
                t = i / 17
                rr = r * (1 - t * 0.85)
                self.c.create_oval(x - rr, y - rr * 0.8, x + rr, y + rr * 0.8, outline="", tags="nebula",
                                   fill=blend(bg, color, t ** 0.7 * 0.9))
        self.c.tag_lower("nebula")

    def resize(self, w, h):
        """ウィンドウの大きさが変わったら、宇宙をその大きさに合わせて描き直す"""
        if w < 50 or h < 50 or (w, h) == (self.w, self.h):
            return
        self.w, self.h = w, h
        self.cx, self.cy = w / 2, h / 2
        self.k = min(w / 400, h / 360)
        for item, (rx, ry, *_) in zip(self.orbit_ids, self.orbits):
            rx, ry = rx * self.k, ry * self.k
            self.c.coords(item, self.cx - rx, self.cy - ry, self.cx + rx, self.cy + ry)
        size = max(14, min(34, round(18 * self.k)))  # 大きな画面でも大きすぎないように
        self._layout_face()
        self.c.itemconfigure(self.title, font=("Arial", size, "bold"))
        self.c.itemconfigure(self.shadow, font=("Arial", size, "bold"))
        for d in self.drops + self.flakes:
            d["x"], d["y"] = random.uniform(0, w), random.uniform(0, h)
        self.c.delete("nebula")
        self._draw_nebula()

    # ---------- アンドロイドの顔 (android_face.py の部品を重ねて表情を作る) ----------
    FACE_HEIGHT = 0.55  # 画面の高さに対する顔の高さ
    RING_BY_MODE = {"idle": "cyan", "listening": "blue", "thinking": "amber", "speaking": "cyan"}
    voice_level = None  # 今話している声の大きさ (0〜1)。わからないときは None (app.py から入れる)

    def _build_face(self):
        c = self.c
        self.gender = "female"
        self.layers = ("base", "ring", "brows", "eyes", "mouth")
        self.face_items = {name: c.create_image(0, 0, tags="face") for name in self.layers}
        self.face_images = None      # {部品: {名前: PhotoImage}}
        self.face_shown = {}         # 今表示している部品の名前
        self.face_job = None
        self.face_building = False
        self.expression, self.expression_until = "neutral", 0
        self.blink = 0
        self.mouth_level = 0.0
        self.face_center = (self.cx, self.cy)

    def set_gender(self, gender):
        """声に合わせて顔 (female / male) を変える"""
        if gender != self.gender:
            self.gender = gender
            self._request_face()

    def set_expression(self, name, seconds=4.0):
        """表情 (neutral/happy/sad/curious/surprised) をしばらくの間変える"""
        self.expression = name
        self.expression_until = self.t + int(seconds * 30)

    def _request_face(self):
        """顔の画像を作り直す (大きさが落ち着いてから、別スレッドで作る)"""
        if self.face_job:
            self.c.after_cancel(self.face_job)
        self.face_job = self.c.after(350, self._render_face)

    def _render_face(self):
        import threading
        from android_face import AndroidFace
        if self.face_building:
            return self._request_face()
        self.face_building = True
        height, gender = int(self.face_height()), self.gender

        def work():
            try:
                import photo_face
                if photo_face.available(gender):  # 顔の画像があればそれを動かす
                    parts = photo_face.PhotoFace(height, gender).parts
                else:                              # なければイラストのアンドロイド
                    parts = AndroidFace(height, gender).parts
            except Exception:
                parts = None
            self.c.after(0, self._apply_face, parts)
        threading.Thread(target=work, daemon=True).start()

    def _apply_face(self, parts):
        from PIL import ImageTk
        self.face_building = False
        if not parts:
            return
        self.face_images = {kind: {name: ImageTk.PhotoImage(img) for name, img in group.items()}
                            for kind, group in parts.items()}
        self.face_shown = {}
        self.face_size = next(iter(parts["base"].values())).size

    TOP_MARGIN = 50      # 顔の上の余白 (場所と天気の表示の分)
    BOTTOM_MARGIN = 150  # 顔の下の余白 (JARVIS の文字と、一番下の状態の表示の分)

    def face_height(self):
        """顔の高さ。JARVIS の文字と状態の表示が顔の下に収まる大きさにする"""
        return max(120, min(self.h * self.FACE_HEIGHT, self.h - self.TOP_MARGIN - self.BOTTOM_MARGIN))

    def set_face_area(self, left, right):
        """顔を置く横の範囲 (会話の列の右側の残りの場所) を決め、その真ん中に顔を置く"""
        x = (left + right) / 2
        if getattr(self, "face_x", None) != x:
            self.face_x = x
            self._layout_face()

    def _layout_face(self):
        """顔の位置と JARVIS の文字の位置を決める (大きさが変われば顔を作り直す)"""
        fh = self.face_height()
        x = getattr(self, "face_x", None) or self.cx
        top = self.TOP_MARGIN + max(0, (self.h - self.TOP_MARGIN - self.BOTTOM_MARGIN - fh) / 2)
        self.face_center = (x, top + fh / 2)  # 上下の余白の間の真ん中
        size = max(14, min(34, round(18 * self.k)))  # 大きな画面でも大きすぎないように
        title_y = top + fh + 18 + size * 0.6  # 顔のすぐ下
        self.c.coords(self.title, x, title_y)
        self.c.coords(self.shadow, x + 2, title_y + 2)
        self.c.itemconfigure(self.title, font=("Arial", size, "bold"))
        self.c.itemconfigure(self.shadow, font=("Arial", size, "bold"))
        self._request_face()

    def _show(self, layer, name):
        if self.face_shown.get(layer) != name and name in self.face_images[layer]:
            self.c.itemconfigure(self.face_items[layer], image=self.face_images[layer][name])
            self.face_shown[layer] = name

    def _animate_face(self, mode):
        """状態と表情に合わせて、目・眉・口・リングを差し替え、顔をゆったり動かす"""
        if not self.face_images:
            return
        if self.t > self.expression_until:
            self.expression = "neutral"
        exp = self.expression

        # まばたき
        if self.blink == 0 and random.random() < 0.01:
            self.blink = 6
        blink_state = None
        if self.blink:
            blink_state = "closed" if 2 <= self.blink <= 4 else "half"
            self.blink -= 1

        # 目と眉
        if blink_state:
            eyes = blink_state
        elif mode == "thinking":
            eyes = "up"
        elif exp == "happy":
            eyes = "happy"
        elif exp == "surprised":
            eyes = "wide"
        else:
            eyes = "open"
        if mode == "thinking":
            brows = "think"
        elif exp == "sad":
            brows = "sad"
        elif exp in ("happy", "curious", "surprised") or mode == "listening":
            brows = "raised"
        else:
            brows = "neutral"

        # 口: 話している間は音節ごとに開閉、表情で口角が変わる
        shape = {"happy": "smile", "sad": "sad", "surprised": "o"}.get(exp, "neutral")
        if mode != "speaking":
            target = 0
        elif self.voice_level is not None:  # 声の大きさがわかるときは、声に合わせて開閉する
            target = self.voice_level * 4
        else:                               # わからないときは、音節のリズムで開いたり閉じたりする
            if self.t % 4 == 0:
                opened = getattr(self, "mouth_target", 0) >= 2
                self.mouth_target = random.choice([0, 0, 1]) if opened else random.choice([2, 3, 3, 4])
            target = getattr(self, "mouth_target", 0)
        self.mouth_level += (target - self.mouth_level) * 0.6
        mouth = f"{shape}_{round(self.mouth_level)}"

        # 耳のリング (状態で色が変わり、話している間や考え中は点滅する)
        color = self.RING_BY_MODE.get(mode, "cyan")
        speed = {"speaking": 0.4, "thinking": 0.25, "listening": 0.1}.get(mode, 0.05)
        ring = f"{color}_{'on' if math.sin(self.t * speed) > -0.2 else 'dim'}"

        for layer, name in (("base", ""), ("ring", ring), ("brows", brows), ("eyes", eyes), ("mouth", mouth)):
            self._show(layer, name)

        # 動き: ゆったり浮かび、左右にわずかに揺れ、話している間は小さくうなずく
        x, y = self.face_center
        x += math.sin(self.t * 0.017) * 3 * self.k
        y += math.sin(self.t * 0.03) * 4 * self.k
        if mode == "speaking":
            y += math.sin(self.t * 0.3) * 1.5 * self.k
        for item in self.face_items.values():
            self.c.coords(item, x, y)

    # ---------- アニメーション ----------
    def _new_star(self, z=1.0):
        tints = self.theme["tints"] if self.theme else ["#ffffff"]
        return {"x": random.uniform(-1, 1) * self.w, "y": random.uniform(-1, 1) * self.h, "z": z,
                "tint": random.choice(tints)}

    def _project(self, s, z):
        f = 0.35
        return self.cx + s["x"] * f / z, self.cy + s["y"] * f / z

    def step(self):
        if self.t % 900 == 0:  # 30秒ごとに時間帯を確認
            self.set_theme(time_of_day(), self.weather_key)
        mode = self.get_mode()
        self.speed += (self.SPEEDS.get(mode, self.SPEEDS["idle"]) - self.speed) * 0.08  # なめらかに加速/減速
        self.t += 1
        c, bg, level = self.c, self.theme["bg"], self.theme["star_level"]

        # ワープする星 (速いほど長い光の筋になる)
        for s in self.stars:
            old_z = s["z"]
            s["z"] -= self.speed
            if s["z"] <= 0.04:
                s.update(self._new_star())
                c.coords(s["id"], 0, 0, 0, 0)
                continue
            x1, y1 = self._project(s, min(old_z + self.speed * 2.5, 1.0))
            x2, y2 = self._project(s, s["z"])
            if not (-20 < x2 < self.w + 20 and -20 < y2 < self.h + 20):
                s.update(self._new_star())
                continue
            near = 1 - s["z"]
            c.coords(s["id"], x1, y1, x2, y2)
            c.itemconfigure(s["id"], width=0.5 + near * 2.6,
                            fill=blend(bg, s["tint"], min(1, (0.25 + near * 1.1) * level)))

        # 中央の核 (話している間は大きく脈打つ)
        amp = 0.16 if mode == "speaking" else 0.05 if mode == "thinking" else 0.03
        pulse = 1 + amp * math.sin(self.t * (0.35 if mode == "speaking" else 0.12))
        for item, scale in self.core:
            r = (14 + scale * 50) * pulse
            r *= self.k
            c.coords(item, self.cx - r, self.cy - r, self.cx + r, self.cy + r)

        # 惑星 (奥側にいるときは核の後ろに回る)
        boost = 3 if mode == "thinking" else 1
        for p in self.planets:
            p["angle"] += p["speed"] * boost
            x = self.cx + p["rx"] * self.k * math.cos(p["angle"])
            y = self.cy + p["ry"] * self.k * math.sin(p["angle"])
            depth = math.sin(p["angle"])          # -1 (奥) 〜 1 (手前)
            size = p["size"] * self.k * (0.75 + 0.35 * (depth + 1) / 2)
            c.coords(p["glow"], x - size * 2, y - size * 2, x + size * 2, y + size * 2)
            c.coords(p["body"], x - size, y - size, x + size, y + size)
            if depth < 0:
                c.tag_lower(p["body"], self.core[0][0])
                c.tag_lower(p["glow"], p["body"])
            else:
                c.tag_raise(p["glow"], self.core[-1][0])
                c.tag_raise(p["body"], p["glow"])

        c.tag_raise("face")  # 顔はいつも一番手前 (惑星が顔の上を横切らないように)
        self._animate_face(mode)

        # 天気の演出
        effect = self.theme["effect"]
        if effect in ("rain", "thunder"):
            for d in self.drops:
                d["y"] += 9 * d["v"]
                d["x"] -= 2.5 * d["v"]
                if d["y"] > self.h or d["x"] < 0:
                    d["x"], d["y"] = random.uniform(0, self.w + 60), random.uniform(-40, 0)
                c.coords(d["id"], d["x"], d["y"], d["x"] + 3, d["y"] - 12)
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
            if self.flash:
                c.configure(bg=blend(bg, "#d8e8ff", 0.8 if self.flash in (8, 7, 4) else 0.1))
                self.flash -= 1
                if self.flash == 0:
                    c.configure(bg=bg)

        for item in (self.shadow, self.title, self.label):
            c.tag_raise(item)

        # ときどき流れ星 (晴れの夜ほど多い)
        if self.meteor_pos is None and random.random() < 0.006 * level:
            self.meteor_pos = [random.uniform(0, self.w), random.uniform(0, self.h * 0.4), 0]
            c.itemconfigure(self.meteor, state="normal")
        if self.meteor_pos:
            x, y, k = self.meteor_pos
            c.coords(self.meteor, x, y, x - 40, y - 18)
            c.itemconfigure(self.meteor, fill=blend("#ffffff", bg, k / 25))
            self.meteor_pos = [x + 9, y + 4, k + 1]
            if k > 25:
                self.meteor_pos = None
                c.itemconfigure(self.meteor, state="hidden")

    def run(self, root, interval=33):
        try:
            self.step()
        except Exception:
            traceback.print_exc()  # 1フレームの失敗でアニメーション全体を止めない (顔が固まらないように)
        root.after(interval, self.run, root, interval)

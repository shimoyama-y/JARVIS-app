"""待ち時間に流すリラックスできる音 (JARVIS の中で作るオリジナルのローファイ・ジャズ)

エレピの和音、歩くようなウッドベース、ブラシのドラム、ときどきビブラフォン。
最初に使うときに sounds/jazz.wav を作り、あとはそれを繰り返し再生する。
(川の音 _make_river と鳥の声 _bird も、雰囲気を変えたいときのために残してある)
"""
import threading
import wave
import winsound
from pathlib import Path

import numpy as np

SOUND_FILE = Path(__file__).with_name("sounds") / "jazz.wav"
RATE = 22050
SECONDS = 60


def _shaped_noise(rng, n, shape):
    """周波数ごとの強さ (shape) を決めたノイズ。FFT で作るので、そのまま切れ目なくループする"""
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    out = np.fft.irfft(spec * shape(freqs), n)
    return out / (np.max(np.abs(out)) + 1e-9)


def _tone(freq_curve, amp_curve):
    """周波数の変化 (Hz の配列) と音量の変化から、鳥の声のような音を作る"""
    phase = 2 * np.pi * np.cumsum(freq_curve) / RATE
    return amp_curve * (np.sin(phase) + 0.18 * np.sin(2 * phase) + 0.06 * np.sin(3 * phase))


def _note(f0, f1, dur, shape="down"):
    n = max(8, int(RATE * dur))
    x = np.linspace(0, 1, n)
    curve = f0 + (f1 - f0) * (x ** 0.6 if shape == "down" else x ** 1.6)
    curve = curve * (1 + 0.015 * np.sin(2 * np.pi * 40 * x * dur))   # わずかな震え
    env = np.sin(np.pi * x) ** 0.7
    return _tone(curve, env)


def _bird(rng):
    """いろいろな鳴き方の小鳥を1回分作る"""
    kind = rng.choice(["trill", "two", "chatter", "whistle"])
    parts = []
    if kind == "trill":      # チチチチ… と続く
        base = rng.uniform(3200, 4800)
        for k in range(rng.integers(6, 14)):
            f = base * (1 - 0.012 * k)
            parts += [_note(f * 1.08, f * 0.86, rng.uniform(0.035, 0.06)), np.zeros(int(RATE * 0.025))]
    elif kind == "two":      # ピー・チュー
        base = rng.uniform(2400, 3400)
        parts = [_note(base, base * 1.25, 0.22, "up"), np.zeros(int(RATE * 0.06)), _note(base * 1.2, base * 0.8, 0.28)]
    elif kind == "chatter":  # チュンチュン
        for k in range(rng.integers(3, 7)):
            f = rng.uniform(3500, 5200)
            parts += [_note(f, f * rng.uniform(0.7, 1.3), rng.uniform(0.05, 0.1)), np.zeros(int(RATE * rng.uniform(0.04, 0.12)))]
    else:                    # ホー・ホケキョ のような口笛
        base = rng.uniform(1600, 2400)
        parts = [_note(base, base * 1.02, 0.5, "up"), np.zeros(int(RATE * 0.08)),
                 _note(base * 1.6, base * 1.9, 0.12, "up"), _note(base * 1.9, base * 1.4, 0.2)]
    return np.concatenate(parts)


def _bubble(rng):
    """水面で泡がはじける、ちょろちょろという小さな音"""
    dur = rng.uniform(0.02, 0.07)
    n = int(RATE * dur)
    x = np.arange(n) / RATE
    f0 = rng.uniform(350, 1400)
    freq = f0 * (1 + 2.5 * x / dur)          # 泡の音は少しずつ高くなる
    return np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-x / (dur * 0.35))


def _make_river():
    """静かに流れる川の音 (使いたいときは _make の代わりにこれを呼ぶ)"""
    rng = np.random.default_rng(21)
    n = RATE * SECONDS
    t = np.arange(n) / RATE
    # 低い水の流れ (ゴーッ) と、水面のサラサラという音
    low = _shaped_noise(rng, n, lambda f: 1 / np.sqrt(np.maximum(f, 40)) * (f < 900))
    mid = _shaped_noise(rng, n, lambda f: np.exp(-((f - 1600) / 1100) ** 2))
    swell = 0.85 + 0.1 * np.sin(2 * np.pi * t / 12) + 0.05 * np.sin(2 * np.pi * t / 5 + 2)  # ゆるやかな波
    out = 0.5 * low * swell + 0.22 * mid * (0.9 + 0.1 * np.sin(2 * np.pi * t / 4))
    # ちょろちょろと泡がはじける音 (ランダムに、まとまって起きる)
    for _ in range(2600):
        b = _bubble(rng) * rng.uniform(0.02, 0.09)
        pos = int(rng.uniform(0, n))
        idx = (pos + np.arange(len(b))) % n   # ループの境目もまたいで置く
        out[idx] += b
    out = np.convolve(out, np.ones(3) / 3, mode="same")  # 角を取ってやわらかい音に
    out = out / np.max(np.abs(out)) * 0.2  # 静かな音量
    SOUND_FILE.parent.mkdir(exist_ok=True)
    with wave.open(str(SOUND_FILE), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((out * 32767).astype(np.int16).tobytes())


# ---------- ローファイ・ジャズ ----------
BPM = 80
BEAT = 60 / BPM
SWING = 0.62  # 8分音符のスイング (裏拍を少し遅らせる)
# 1小節ずつの和音 (Fメジャーの ii-V-I を中心に)。数字は MIDI の音の番号、最初がベースの根音
PROGRESSION = [
    (43, [58, 62, 65, 69]),  # Gm9
    (36, [58, 62, 64, 69]),  # C13
    (41, [57, 60, 64, 67]),  # Fmaj9
    (38, [57, 60, 64, 65]),  # Dm9
    (43, [58, 62, 65, 69]),  # Gm9
    (36, [58, 61, 64, 68]),  # C7(b9)
    (41, [57, 60, 64, 67]),  # Fmaj9
    (41, [57, 62, 67, 69]),  # F6/9
]
SCALE = [65, 67, 69, 72, 74, 77, 79]  # ビブラフォンのフレーズに使う音 (Fペンタトニック)


def _midi(note):
    return 440.0 * 2 ** ((note - 69) / 12)


def _add(out, sig, start):
    i = int(start * RATE) % len(out)
    idx = (i + np.arange(len(sig))) % len(out)  # ループの境目をまたいでも切れないように
    out[idx] += sig


def _epiano(note, dur, vel):
    """エレピ (やわらかいベルのような音で、ゆっくり消える)"""
    t = np.arange(int(RATE * (dur + 1.2))) / RATE
    f = _midi(note)
    env = np.exp(-t * 1.6) * np.minimum(1, t / 0.006)
    tine = np.exp(-t * 6) * 0.35 * np.sin(2 * np.pi * f * 3.98 * t)
    trem = 1 + 0.08 * np.sin(2 * np.pi * 4.5 * t)
    return vel * env * trem * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t) + tine)


def _bass(note, dur, vel):
    """ウッドベース (はじいた音)"""
    t = np.arange(int(RATE * dur * 1.1)) / RATE
    f = _midi(note)
    env = np.exp(-t * 3.2) * np.minimum(1, t / 0.01)
    sig = np.sin(2 * np.pi * f * t) + 0.45 * np.sin(2 * np.pi * 2 * f * t) + 0.15 * np.sin(2 * np.pi * 3 * f * t)
    return vel * env * sig


def _vibes(note, vel):
    t = np.arange(int(RATE * 1.6)) / RATE
    f = _midi(note)
    env = np.exp(-t * 2.2) * (1 + 0.25 * np.sin(2 * np.pi * 5.5 * t))
    return vel * env * (np.sin(2 * np.pi * f * t) + 0.15 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-t * 8))


def _noise_hit(rng, dur, decay, band, vel):
    """シンバルやブラシ (ノイズを帯域で切って、すっと消える音)"""
    n = int(RATE * dur)
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    lo, hi = band
    sig = np.fft.irfft(spec * ((freqs > lo) & (freqs < hi)), n)
    sig /= np.max(np.abs(sig)) + 1e-9
    t = np.arange(n) / RATE
    return vel * sig * np.exp(-t * decay)


def _kick(vel):
    t = np.arange(int(RATE * 0.35)) / RATE
    f = 55 + 60 * np.exp(-t * 25)
    return vel * np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 9)


def _make():
    rng = np.random.default_rng(8)
    bars = len(PROGRESSION) * 2                      # 8小節 × 2回 (2回目は少し変える)
    total = bars * 4 * BEAT
    out = np.zeros(int(RATE * total))
    sw = lambda beat: beat + (SWING - 0.5) if beat % 1 else beat  # 裏拍をスイングさせる

    for bar in range(bars):
        root, chord = PROGRESSION[bar % len(PROGRESSION)]
        nxt_root = PROGRESSION[(bar + 1) % len(PROGRESSION)][0]
        t0 = bar * 4 * BEAT
        # エレピの伴奏 (1拍目と、2拍目の裏など、少しずつ変える)
        pattern = [(0, 1.4), (1.5, 0.9)] if bar % 2 == 0 else [(0.5, 1.2), (2.5, 1.0)]
        if bar >= len(PROGRESSION):
            pattern = [(0, 1.0), (2, 0.8), (3.5, 0.6)] if bar % 2 else pattern
        for beat, length in pattern:
            for k, note in enumerate(chord):
                _add(out, _epiano(note, length * BEAT, 0.11), t0 + sw(beat) * BEAT + k * 0.008)  # 少しずらして弾く
        # 歩くようなベース (根音 → 3度/5度 → 経過音 → 次の和音へ半音で近づく)
        third = root + (3 if root in (43, 38) else 4)
        walk = [root, third if bar % 2 else root + 7, root + 7 if bar % 2 else third, nxt_root + rng.choice([-1, 1])]
        for beat, note in enumerate(walk):
            _add(out, _bass(note, BEAT * 0.95, 0.32), t0 + beat * BEAT)
        # ドラム: ライドシンバル (チーン、チ・チーン)、2拍と4拍にブラシ、1拍目に小さなキック
        for beat in (0, 1, 1.5, 2, 3, 3.5):
            vel = 0.05 if beat % 1 else 0.07
            _add(out, _noise_hit(rng, 0.5, 9, (5000, 10000), vel), t0 + sw(beat) * BEAT)
        for beat in (1, 3):
            _add(out, _noise_hit(rng, 0.35, 10, (1500, 6000), 0.06), t0 + beat * BEAT)
        _add(out, _kick(0.16), t0)
        # ときどきビブラフォンの短いフレーズ
        if rng.random() < 0.55:
            start = rng.choice([1.5, 2, 2.5])
            for i in range(rng.integers(2, 5)):
                _add(out, _vibes(rng.choice(SCALE), 0.07), t0 + sw(start + i * 0.5) * BEAT)

    # レコードのような小さなノイズ
    crackle = np.zeros_like(out)
    pops = rng.integers(0, len(out), int(total * 6))
    crackle[pops] = rng.uniform(-0.05, 0.05, len(pops))
    out += crackle + 0.004 * rng.standard_normal(len(out))
    out = np.convolve(out, np.ones(3) / 3, mode="same")  # 角を取ってやわらかく (ローファイ感)
    out = out / np.max(np.abs(out)) * 0.25  # 静かな音量
    SOUND_FILE.parent.mkdir(exist_ok=True)
    with wave.open(str(SOUND_FILE), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((out * 32767).astype(np.int16).tobytes())


class Relax:
    """after 秒たっても終わっていなければ音楽を流し、stop() で止める"""

    _lock = threading.Lock()

    def __init__(self, after=0.0):
        self.playing = False
        self.timer = threading.Timer(after, self._play)
        self.timer.daemon = True
        self.timer.start()

    def _play(self):
        with self._lock:
            if not SOUND_FILE.exists():
                _make()
            winsound.PlaySound(str(SOUND_FILE), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            self.playing = True

    def stop(self):
        self.timer.cancel()
        with self._lock:
            if self.playing:
                winsound.PlaySound(None, winsound.SND_PURGE)
                self.playing = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


if __name__ == "__main__":  # 試しに作って10秒流す
    import time
    _make()
    r = Relax()
    time.sleep(10)
    r.stop()

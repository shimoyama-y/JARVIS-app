"""既定のマイクで5秒録音し、音量レベルを表示して mic_test.wav に保存する。

使い方: python tools/mic_test.py  (要: pip install sounddevice numpy)
"""
import wave
from pathlib import Path
import numpy as np
import sounddevice as sd

dev = sd.query_devices(kind="input")
print("default input:", dev["name"], "| channels:", dev["max_input_channels"], "| rate:", dev["default_samplerate"])

rate = 16000
sec = 5
print(f"Recording {sec}s ...")
data = sd.rec(int(rate * sec), samplerate=rate, channels=1, dtype="int16")
sd.wait()

x = data[:, 0].astype(np.float32) / 32768
rms = float(np.sqrt(np.mean(x ** 2)))
peak = float(np.max(np.abs(x)))
db = 20 * np.log10(rms + 1e-12)
print(f"RMS: {rms:.4f} ({db:.1f} dBFS)  Peak: {peak:.4f}")
# per-0.5s level
for i in range(0, len(x), rate // 2):
    seg = x[i:i + rate // 2]
    lvl = float(np.sqrt(np.mean(seg ** 2)))
    print(f"{i/rate:4.1f}s {'#' * min(60, int(lvl * 600))}")

with wave.open(str(Path(__file__).with_name("mic_test.wav")), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(data.tobytes())
print("saved mic_test.wav")

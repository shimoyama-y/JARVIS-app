import asyncio
import audioop
import ctypes
import base64
import io
import json
import os
import secrets
import subprocess
import sys
import queue
import re
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from pathlib import Path

import edge_tts
import pythoncom
import speech_recognition as sr
import win32com.client

import features
import cleanup
import memory_store
import n8n_tools
from ambient import Relax
import phone_server
import qrcode
from forest_view import ForestView
from space_view import weather_kind

try:
    import ai
    assistant = ai.Assistant()
except Exception as e:  # API キー未設定など。従来の決まった言葉での操作だけ使う
    assistant = None
    AI_ERROR = str(e)

BG = "#0b1210"
PANEL = "#141a17"
CYAN = "#e0b56a"  # アクセントの色 (金色)
CYAN_DIM = "#5a4a2e"
TEXT = "#f3ead8"

# 呼びかけ語。音声認識が「ジャービス」をよく取り違える (ジェービス/ジェームス など) ので、
# 近い聞き取り間違いも呼びかけとして受ける。小文字化して部分一致で判定する
WAKE_WORDS = ["ジャービス", "じゃーびす", "jarvis", "ジャーヴィス", "じゃーゔぃす",
              "ジェービス", "じぇーびす", "ジェーヴィス", "ジェーびす",
              "ジェームス", "ジェームズ", "じぇーむす", "じぇーむず", "じゃーびー"]

# 二重起動を防ぐ (2つ動くとマイクや音声ファイルを取り合ってしまう)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_mutex = _kernel32.CreateMutexW(None, False, "JARVIS_single_instance")
if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
    ctypes.windll.user32.MessageBoxW(None, "JARVIS はすでに起動しています。", "JARVIS", 0x40)
    sys.exit(0)

root = tk.Tk()
root.title("JARVIS")
root.geometry("1280x760")
root.minsize(900, 600)
root.state("zoomed")  # 最大化して開く


def toggle_fullscreen(_=None):
    root.attributes("-fullscreen", not root.attributes("-fullscreen"))


root.bind("<F11>", toggle_fullscreen)                                   # F11 で全画面
root.bind("<Escape>", lambda _: root.attributes("-fullscreen", False))  # Esc で戻す
root.configure(bg=BG)

# ウィンドウを閉じる操作 (×ボタンや close-window コマンドの WM_CLOSE) を受けたとき、既定の動作 (destroy) では
# ルートウィンドウの破棄とともに mainloop が終わり、音声認識ループなど他のスレッドも一緒に落ちてしまう。
# 画面だけを消して、音声でのやりとり (バックエンド) は動かし続けるため、destroy ではなく withdraw にする
root.protocol("WM_DELETE_WINDOW", root.withdraw)

# show-window コマンド (「画面を出して」) は別プロセス (jarvis_tools.py) から実行されるため、
# withdraw の逆である deiconify はプロセス内 (Tkinterのメインスレッド) の API しか直接呼べない。
# そのため show_window() はしるしファイルを立てるだけにし、ここで定期的に確認して画面を戻す
SHOW_WINDOW_POLL_MS = 1000


def check_show_window_request():
    if features.show_window_requested():
        root.deiconify()
        root.lift()
        root.focus_force()
        log("画面操作: show-windowの要求で画面を再表示")
    root.after(SHOW_WINDOW_POLL_MS, check_show_window_request)


root.after(SHOW_WINDOW_POLL_MS, check_show_window_request)


def short(text, n=34):
    """画面下の1行表示用に短くする (全文は真ん中の欄に出る)"""
    return text if len(text) <= n else text[:n] + "…"


def ui(fn, *args):
    """別スレッドから画面を更新する"""
    root.after(0, fn, *args)


# ---------- 音声で返答 (Windows の読み上げ機能 SAPI) ----------
# pyttsx3 は2回目以降が無音になる不具合があるため、SAPI を直接使う
speech_queue = queue.Queue()
speaking = threading.Event()
interrupt_tts = threading.Event()  # ユーザーが話し始めたら立てて、再生中の音声を止める (バージイン)
no_barge_in = threading.Event()  # 立っている間はバージインしない (自動操作中の実況が自分の声で途切れるのを防ぐ)
SETTINGS_FILE = Path(__file__).with_name("settings.json")
VOICE_CATEGORIES = [  # OneCore には Ayumi/Sayaka/Ichiro など、通常の SAPI より多くの声がある
    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices",
    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices",
]


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(**changes):
    settings = load_settings() | changes
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# スマホから使うときの合言葉 (初回起動時だけランダムに作って使い続ける。他人には教えない)
if "phone_token" not in load_settings():
    save_settings(phone_token=secrets.token_urlsafe(24))


def japanese_voices():
    """日本語の声を {表示名: 声の情報} で返す (呼び出したスレッドでのみ使える)"""
    voices = {}
    for category_id in VOICE_CATEGORIES:
        category = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
        try:
            category.SetId(category_id, False)
        except pythoncom.com_error:
            continue
        for token in category.EnumerateTokens():
            if token.GetAttribute("Language") != "411":  # 411 = 日本語
                continue
            name = token.GetAttribute("Name").replace("Microsoft ", "").replace(" Desktop", "")
            gender = {"Female": "女性", "Male": "男性"}.get(token.GetAttribute("Gender"), "")
            voices.setdefault(f"{name} ({gender})", token)
    return voices


def voice_options(sapi_voices):
    """Windows の各音声に、ネット不要で選べる話速プリセットを追加する。"""
    options = dict(sapi_voices)
    rate_offsets = {}
    for name, token in sapi_voices.items():
        for style, offset in (("ゆっくり", -2), ("はきはき", 2)):
            label = f"{name} - {style}"
            options[label] = token
            rate_offsets[label] = offset
    return options, rate_offsets


# 高音質のニューラル音声 (Microsoft Edge の読み上げ音声。無料だがインターネットが必要)
# 名前: (声の種類, 声の高さの調整, 速さの調整%)
NEURAL_VOICES = {
    "Nanami 透明感 (女性・高音質)": ("ja-JP-NanamiNeural", "+12Hz", -5),  # 少し高く、ゆっくり
    "Nanami (女性・高音質)": ("ja-JP-NanamiNeural", "+0Hz", 0),
    "Emma 澄んだ声 (女性・多言語)": ("en-US-EmmaMultilingualNeural", "+0Hz", 0),
    "Ava やさしい声 (女性・多言語)": ("en-US-AvaMultilingualNeural", "+0Hz", 0),
    "Keita (男性・高音質)": ("ja-JP-KeitaNeural", "+0Hz", 0),
}
DEFAULT_VOICE = "Keita (男性・高音質)"  # 初期設定は男性の声 (声の選択で女性にも変更できる)
TTS_FILE = Path(tempfile.gettempdir()) / "jarvis_tts.mp3"

# 画面から変更される読み上げ設定 (tts_worker が話す直前に反映する)
voice_settings = {"voice": DEFAULT_VOICE, "rate": 0} | {
    k: v for k, v in load_settings().items() if k in ("voice", "rate")}


VOICE_STEP_MS = 25  # 声の大きさを測る間隔


def set_voice_level(level):
    """顔に口の開き具合 (0〜1) を伝える。None なら声の大きさがわからないので、顔の側がリズムで口を動かす"""
    view = globals().get("space")
    if view is not None:
        view.voice_level = level


def voice_envelope(path):
    """音声ファイルの声の大きさを VOICE_STEP_MS ごとに 0〜1 で返す (口の動きを声に合わせるため)。
    ffmpeg が使えないときは None"""
    try:
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
                             capture_output=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception as e:
        log(f"声の大きさを測れません: {e!r}")
        return None
    size = 8000 * VOICE_STEP_MS // 1000 * 2  # 16bit なので 1 サンプル 2 バイト
    loud = [audioop.rms(pcm[i:i + size], 2) for i in range(0, len(pcm) - size + 1, size)]
    peak = sorted(loud)[int(len(loud) * 0.9)] if loud else 0  # 大きめの声を「口を大きく開ける」基準にする
    if peak <= 0:
        return None
    levels = [min(1.0, v / peak) ** 0.6 for v in loud]
    return [v if v > 0.12 else 0.0 for v in levels]  # 息つぎや無音では口を閉じる


def speak_neural(text, voice, rate):
    """ニューラル音声で mp3 を作って再生する (読み終わるまで待つが、バージインで即座に止められる)"""
    voice_id, pitch, base_rate = voice
    set_voice_level(0.0)  # 声を作っている間は、まだ口を閉じておく
    try:
        asyncio.run(edge_tts.Communicate(text, voice_id, rate=f"{rate * 10 + base_rate:+d}%", pitch=pitch)
                    .save(str(TTS_FILE)))
        levels = voice_envelope(TTS_FILE)
        mci = ctypes.windll.winmm.mciSendStringW
        mci(f'open "{TTS_FILE}" type mpegvideo alias jarvis_tts', None, 0, 0)
        mci("play jarvis_tts", None, 0, 0)  # wait せず再生し、止められるように状態を見ながら待つ
        status = ctypes.create_unicode_buffer(32)
        mci("status jarvis_tts length", status, len(status), 0)
        length = int(status.value) if status.value.isdigit() else 0
        at_end = None  # 再生位置が長さの表示に達した時刻 (長さが実際より短く出ることがあり、その先は時計で数える)
        while not interrupt_tts.is_set():
            mci("status jarvis_tts mode", status, len(status), 0)
            if status.value != "playing":
                break
            if levels:  # 今鳴っているところの声の大きさに合わせて口を開く (顔の動きの遅れの分、少し先を見る)
                mci("status jarvis_tts position", status, len(status), 0)
                pos = int(status.value) if status.value.isdigit() else 0
                if length and pos >= length:
                    at_end = at_end or time.time()
                    pos = length + int((time.time() - at_end) * 1000)
                i = (pos + 60) // VOICE_STEP_MS
                set_voice_level(levels[i] if i < len(levels) else 0.0)
            else:
                set_voice_level(None)
            time.sleep(0.03)
        mci("stop jarvis_tts", None, 0, 0)
        mci("close jarvis_tts", None, 0, 0)
    finally:
        set_voice_level(None)


# Windows の声が今出している音 (SAPI の口の形の番号) ごとの、口の開き具合 (0〜1)
VISEME_OPEN = {0: 0.0, 1: 0.8, 2: 1.0, 3: 0.8, 4: 0.6, 5: 0.5, 6: 0.4, 7: 0.35, 8: 0.7, 9: 0.8, 10: 0.7, 11: 0.9,
               12: 0.4, 13: 0.3, 14: 0.35, 15: 0.2, 16: 0.25, 17: 0.25, 18: 0.15, 19: 0.2, 20: 0.3, 21: 0.0}


def fallback_voice(sapi_voices, voice_name):
    """ニューラル音声が使えないときの代わりの声。選んでいた声と同じ性別を優先する"""
    prefer_male = "男性" in voice_name
    for name in sapi_voices:
        if ("男性" in name) == prefer_male:
            return name
    return next(iter(sapi_voices), None)


def tts_worker():
    pythoncom.CoInitialize()
    sapi = win32com.client.Dispatch("SAPI.SpVoice")
    sapi_voices = japanese_voices()
    sapi_voice_options, voice_rate_offsets = voice_options(sapi_voices)
    sapi.Volume = 100  # 音量 (0〜100)
    current = None
    while True:
        text = speech_queue.get()
        try:
            # Windows の音声パックを追加した場合、再起動なしでも次の発話から使えるよう更新する。
            try:
                sapi_voices = japanese_voices()
                sapi_voice_options, voice_rate_offsets = voice_options(sapi_voices)
            except Exception as e:
                log_once("voice_refresh", f"音声一覧を更新できません ({type(e).__name__})")
            name, rate = voice_settings["voice"], voice_settings["rate"]  # 速さ -10〜10
            if name in NEURAL_VOICES:
                try:
                    speak_neural(text, NEURAL_VOICES[name], rate)
                    continue
                except Exception as e:  # オフラインなど → Windows の声で読む
                    log_once("neural_voice", f"高音質の声が使えません ({type(e).__name__})。Windows の声で読みます")
                    name = fallback_voice(sapi_voices, name)
            if name in sapi_voice_options and name != current:
                sapi.Voice = sapi_voice_options[name]
                current = name
            sapi.Rate = max(-10, min(10, rate + voice_rate_offsets.get(name, 0)))
            sapi.Speak(text, 1)  # 1 = 非同期 (SVSFlagsAsync)。バージインで即座に止められるようにする
            # Speak は非同期なので、直後はまだ RunningState が 2 になっていない (開始まで一瞬かかる)。
            # 「始まるのを待ち → 読み上げ中は口を動かし → 終わったら抜ける」の順にする
            # (いきなり RunningState==2 を見ると、開始前に偽になってループに入れず、口が動かず声も待てなかった)
            waited = 0.0
            speaking_started = False
            while not interrupt_tts.is_set():
                if sapi.Status.RunningState == 2:  # 2 = 読み上げ中
                    speaking_started = True
                    set_voice_level(VISEME_OPEN.get(sapi.Status.VisemeId, 0.3))  # 今出している音に合わせて口を開く
                elif speaking_started:             # いったん始まって、もう読み上げ中でない → 終わり
                    break
                elif waited > 1.5:                 # 1.5秒待っても始まらなければ打ち切り (保険)
                    break
                time.sleep(0.03)
                waited += 0.03
            if interrupt_tts.is_set():
                sapi.Speak("", 3)  # 3 = 非同期+破棄 (SVSFlagsAsync|SVSFPurgeBeforeSpeak) で即座に止める
        finally:
            set_voice_level(None)
            speech_queue.task_done()


threading.Thread(target=tts_worker, daemon=True).start()


LOG_FILE = Path(__file__).with_name("jarvis.log")
LOG_MAX_BYTES = 1_000_000  # ログがこの大きさを超えたら、後ろ半分だけ残して古い分を捨てる (肥大化を防ぐ)


def log(line):
    """聞き取った言葉と返事を jarvis.log に記録する (うまく動かないときの確認用)"""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            tail = LOG_FILE.read_bytes()[-LOG_MAX_BYTES // 2:]
            LOG_FILE.write_bytes("(古いログは省略)\n".encode("utf-8") + tail[tail.find(b"\n") + 1:])
    except Exception:
        pass
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {line}\n")


_last_logged = {}  # 同じ原因の繰り返しを間引くための、前回記録した内容


def log_once(key, line):
    """同じ内容の記録が続くときは、繰り返しを書かずに1回だけ書く (403エラーの連打などでログが膨れないように)"""
    if _last_logged.get(key) != line:
        _last_logged[key] = line
        log(line)


def expression_for(text):
    """話す内容からアンドロイドの表情を決める"""
    if any(w in text for w in ("ごめん", "すみません", "申し訳", "残念", "できません", "できない")):
        return "sad"
    if any(w in text for w in ("えっ", "本当", "びっくり", "驚")):
        return "surprised"
    if any(w in text for w in ("！", "!", "よかった", "嬉し", "うれし", "楽し", "おめでと", "お疲れ",
                               "ありがとう", "いいですね", "こんにちは", "素敵", "すてき")):
        return "happy"
    if text.rstrip("。 ").endswith(("？", "?", "か")):
        return "curious"
    return "neutral"


def speech_text(text):
    """読み上げ用に、コード (```で囲んだ部分) やマークダウンの記号を省く"""
    text = re.sub(r"```.*?```", "。", text, flags=re.S)
    text = re.sub(r"[`#*>|]", "", text)  # _ はファイル名 (memory_store など) に使うので残す
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"。+", "。", text)
    return text.strip(" 。") + "。" if text.strip() else text


def speak(text):
    log(f"JARVIS: {text}")
    ui(reply.set, short(f"JARVIS: {text}"))
    ui(space.set_expression, expression_for(text), 3 + len(text) * 0.15)  # 話す内容に合わせた表情
    speaking.set()  # 話している間は宇宙の核が脈打つ
    interrupt_tts.clear()
    threading.Thread(target=watch_for_barge_in, daemon=True).start()  # 話し始めたらすぐ止める (バージイン)
    speech_queue.put(speech_text(text))
    speech_queue.join()  # 話し終わるか、バージインで止まるまで待つ
    speaking.clear()


# ---------- 音声認識 (SpeechRecognition) ----------
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = True  # 雑音の変化には自動で追従する
# 無音がこの秒数続いたら「話し終わった」と判定する (考える間や「えっと」等の言い直しで切れないよう長めに)。
# ユーザーは考えながら数秒の間を取って話すので、間が 5秒 を超えて文の途中で切られる事例が続いた。
# 8秒 に延ばして、その間ならまだ話し続けているとみなす。応答までの待ちは延びるが、切れない方を優先する。
# settings.json に "pause_threshold": 数値 を書けば、再起動後にその秒数に変えられる (遅ければ下げる)
DEFAULT_PAUSE_THRESHOLD = 8.0
PAUSE_THRESHOLD = load_settings().get("pause_threshold", DEFAULT_PAUSE_THRESHOLD)
recognizer.pause_threshold = PAUSE_THRESHOLD
# 1回の発話をここまで録音したら区切る上限 (秒)。ポーズを取りながらゆっくり話すと、
# 短いとこの上限に達して文の途中で切れてしまうため、言い切るまで待てるよう長めにする。
# settings.json に "phrase_time_limit": 数値 を書けば、再起動後にその秒数に変えられる
DEFAULT_PHRASE_TIME_LIMIT = 30
PHRASE_TIME_LIMIT = load_settings().get("phrase_time_limit", DEFAULT_PHRASE_TIME_LIMIT)
recognizer.non_speaking_duration = 0.6  # 話し始めの前の音も少し残して、出だしを切らない
last_calibrated = 0  # 最後にキャリブレーションした時刻 (time.time())
RECALIBRATE_INTERVAL = 300  # 5分ごとに再キャリブレーション (雑音レベルの変化に追従するため)

# 会話の調子をはかるカウンタ (聞き取れた/聞き取れなかった/文の途中を継ぎ足した 回数)。
# conversation_health_monitor が定期的に見て、待ち時間やマイク感度を自動で調整する。
recog_stats = {"ok": 0, "unknown": 0, "stitched": 0}
recog_stats_lock = threading.Lock()


def _bump_recog(key):
    with recog_stats_lock:
        recog_stats[key] += 1
busy = threading.Lock()
# マイク (PortAudio) はスレッドセーフでないため、同時に2つ以上のストリームを開くと
# ネイティブクラッシュ (ヒープ破損) で JARVIS が無言で落ちる。バージイン監視と聞き取りが
# 同じデバイスを取り合わないよう、マイクを開く処理はすべてこのロックで直列化する。
mic_lock = threading.Lock()
wake_on = threading.Event()  # 呼びかけ待ち受け中かどうか


pending = queue.Queue()  # 待ち受け中に押されたボタンの処理
BARGE_IN_RATIO = 4  # 周りの雑音の何倍の音量で「話し始めた」とみなすか (スピーカーの音や小さな物音を拾いすぎないよう高めに)
BARGE_IN_SUSTAIN_SEC = 0.4  # この秒数ぶん続けて大きい音が出たときだけ割り込む (クリック音や咳など一瞬の音では止めない)


def watch_for_barge_in():
    """読み上げ中にユーザーが話し始めたら、再生中の音声をすぐに止める (バージイン)。
    止めたあとは、いつもの聞き取り (listen) がそのまま話した内容を受け付ける。
    マイクがスピーカーの音を拾いやすい環境では、ヘッドホンを使うとより確実に働く"""
    if no_barge_in.is_set():  # 自動操作の実況中などは、自分の声を拾って途切れないようバージインしない
        return
    try:
        with mic_lock, sr.Microphone() as source:
            threshold = max(recognizer.energy_threshold, 300) * BARGE_IN_RATIO
            # 一瞬の物音で止まらないよう、一定時間続けて大きいときだけ割り込む
            need = max(1, int(BARGE_IN_SUSTAIN_SEC * source.SAMPLE_RATE / source.CHUNK))
            loud = 0
            while speaking.is_set() and not interrupt_tts.is_set():
                buffer = source.stream.read(source.CHUNK)
                if audioop.rms(buffer, source.SAMPLE_WIDTH) >= threshold:
                    loud += 1
                    if loud >= need:  # 大きい音が続いた = 実際に話し始めた
                        interrupt_tts.set()
                else:
                    loud = 0  # 静かになったらやり直し (単発の音は無視する)
    except OSError:
        pass  # マイクを使えない環境ではバージインなしで動く


# 「〜を」「〜から」など、文がまだ続きそうな末尾 (言いさし)。ここで切れていたら続きを聞き足す。
# 「〜して」「開いて」など命令の正常な語尾になる「て/で/って」は、毎回3秒待たないよう入れない
CONTINUATION_ENDINGS = (
    "を", "に", "が", "は", "と", "へ", "や", "し",
    "から", "けど", "けれど", "けれども", "ので", "のに",
    "たら", "れば", "ながら", "つつ",
)
# 言いよどみ (この一言で止まったら、まだ続きを言おうとしている)
FILLER_ENDINGS = ("えっと", "ええと", "えー", "あの", "あのー", "その", "まあ", "なんか", "そのー")


def looks_unfinished(text):
    """認識した文が言いさし (まだ続きそう) かどうかを、末尾の助詞や言いよどみで判断する"""
    t = text.strip().rstrip("。.!！?？、,，　 ")
    if not t:
        return False
    return t.endswith(FILLER_ENDINGS) or t.endswith(CONTINUATION_ENDINGS)


def listen(prompt="話してください...", timeout=5, quiet=False, follow_up=False, announce=True):
    """マイクから1回聞き取って文字にする。聞き取れなければ None
    follow_up=True のときは、言いさしで切れていたら続きを聞き足してから返す (話し終わる前に動かないように)。
    announce=False のときは、聞き取った内容を画面やログに出さない (自動操作中の呼びかけ待ちなど、
    JARVIS自身の実況を拾っても記録を汚さないように)"""
    if prompt:
        ui(status.set, prompt)
    try:
        global last_calibrated
        with mic_lock, sr.Microphone() as source:
            now = time.time()
            if now - last_calibrated >= RECALIBRATE_INTERVAL:
                # 毎回キャリブレーションすると話し始めを取りこぼすので、
                # 初回 (last_calibrated=0) と、5分経って部屋の雑音が変わりうる時だけ
                # 短時間 (1.0秒) で測り直す
                recognizer.adjust_for_ambient_noise(source, duration=1.0)
                # 一瞬だけ偏った音量 (物音など) で閾値が決まり、5分間尾を引くのを防ぐため妥当な範囲に収める
                recognizer.energy_threshold = min(max(recognizer.energy_threshold, 50), 4000)
                last_calibrated = now
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=PHRASE_TIME_LIMIT)
        ui(status.set, "認識中...")
        text = recognizer.recognize_google(audio, language="ja-JP")
        tries = 0  # follow_up で続きを継ぎ足した回数 (会話の調子の指標)
        if follow_up:
            # 言いさし (「〜方が」「〜を」など) で切れていたら、続きを聞き足す。
            # 文法的に途中なので、考える間が長くても取りこぼさないよう pause_threshold と同じだけ待つ。
            # (この間ずっと無言ならそこで言い終えたとみなして確定する。最大3回まで足す)
            tries = 0
            while looks_unfinished(text) and tries < 3:
                tries += 1
                ui(status.set, "続きをどうぞ...")
                try:
                    with mic_lock, sr.Microphone() as source:
                        more = recognizer.listen(source, timeout=PAUSE_THRESHOLD, phrase_time_limit=PHRASE_TIME_LIMIT)
                    text += recognizer.recognize_google(more, language="ja-JP")
                except sr.UnknownValueError:
                    continue  # 何か聞こえたが聞き取れなかった → 打ち切らず、もう一度続きを待つ
                except (sr.WaitTimeoutError, OSError):
                    break  # 無言が続いた = 言い終えたとみなす
        if announce:
            _bump_recog("ok")
            if tries > 0:
                _bump_recog("stitched")
            ui(heard.set, short(f"あなた: {text}"))
            log(f"あなた: {text}")
        return text
    except sr.WaitTimeoutError:
        if not quiet:
            ui(status.set, "声が聞こえませんでした")
    except sr.UnknownValueError:
        if announce:
            _bump_recog("unknown")  # 何か聞こえたが文字にできなかった = 聞き取りの調子の指標
        if not quiet:
            ui(status.set, "聞き取れませんでした")
    except sr.RequestError as e:
        ui(status.set, f"音声認識サービスに接続できません: {e}")
    except OSError as e:
        ui(status.set, f"マイクを開けません: {e}")
    return None


# ---------- 認識した言葉に応じて処理 ----------
def respond(result):
    say, show = result
    ui(show_info, show)
    speak(say)


def handle(text, fallback=True):
    """命令を実行する。知らない言葉なら False (fallback=True なら聞き返す)"""
    try:
        if "天気" in text:
            respond(features.weather(text))
        elif "ニュース" in text:
            respond(features.news(text))
        elif any(w in text for w in ("追加", "登録", "入れて")) and ("予定" in text or "スケジュール" in text):
            result = features.add_schedule(text)
            if result:
                respond(result)
            else:
                speak("時間がわかりませんでした。「15時に会議を予定に追加して」のように話してください。")
        elif "予定" in text or "スケジュール" in text:
            respond(features.schedule())
        elif (any(w in text for w in ("流して", "かけて", "再生して", "聴かせて", "聞かせて"))
              or (any(w in text.lower() for w in ("youtube", "ユーチューブ"))
                  and any(w in text for w in ("検索", "探して")))):
            play_request(text)
        elif (any(w in text for w in ("曲", "音楽", "再生")) and (result := features.media_key(text))):
            respond(result)
        elif "開いて" in text and ("サイト" in text or any(k in text.lower() for k in features.SITES)):
            respond(features.open_site(text))
        elif "シャットダウン" in text:
            if "取り消" in text or "キャンセル" in text:
                respond(features.cancel_shutdown())
            else:
                confirm_shutdown()
        elif result := features.pc_action(text):
            respond(result)
        elif "検索" in text or "調べ" in text:
            query = features.search_query(text)
            if not query:
                speak("何を検索しますか？")
                query = listen()
            if query:
                respond(features.search(query))
        else:
            if fallback:
                speak("ごめんなさい、もう一度お話ししていただけますか？")
            return False
    except Exception as e:  # ネットワークエラーなど
        log(f"エラー: {e!r}")
        ui(show_info, f"エラー: {e}")
        speak("ごめんなさい、うまくできませんでした。もう一度試してみますね。")
    return True


def confirm_shutdown():
    speak("本当にシャットダウンしますか？「はい」か「いいえ」で答えてください。")
    answer = listen()
    if answer and "はい" in answer:
        respond(features.shutdown())
    else:
        speak("シャットダウンを中止しました。")


def confirm_user_restart(text):
    """ユーザーに「再起動して」と言われたとき、確認してから JARVIS を再起動する
    (再起動すると最新のプログラムが読み込まれる。会話の続きは復元される)"""
    add_chat("あなた", text)
    speak("再起動しますか？「はい」で再起動します。")
    answer = listen()
    if answer and is_yes(answer):
        speak("再起動します。少し待っていてくださいね。")
        restart()
    else:
        speak("わかりました、再起動はやめておきますね。")


def play_request(text=""):
    """曲名があればそれを、なければ気分を聞いて選曲して再生する"""
    query = features.music_query(text)
    if query is None:
        speak(features.MOOD_QUESTION)
        query, comment = features.mood_query(listen())
        speak(comment)
    respond(features.play_music(query))


def ask_and_handle(question):
    speak(question)
    text = listen(follow_up=True)
    if text:
        handle(text)


def has_wake_word(text):
    return any(w in text.lower() for w in WAKE_WORDS)


def strip_wake_word(text):
    for w in WAKE_WORDS:
        text = text.replace(w, "").replace(w.upper(), "").replace(w.capitalize(), "")
    return text.strip(" 、。,")


KEEP_TALKING_SECONDS = 180  # 返事のあと、この時間は会話中のまま (すぐ待機中に戻らない)
last_activity = [0.0]
idle_job = [None]


def mark_activity():
    """会話のあと3分間は「会話中」の表示にして、そのあと待機中に戻す"""
    last_activity[0] = time.time()

    def to_idle():
        if time.time() - last_activity[0] >= KEEP_TALKING_SECONDS - 1 and not busy.locked()                 and not wake_on.is_set():
            status.set("待機中...")
    if idle_job[0]:
        root.after_cancel(idle_job[0])
    idle_job[0] = root.after(KEEP_TALKING_SECONDS * 1000, to_idle)


def run_task(fn, *args):
    """音声処理をバックグラウンドで1つずつ実行する (実行中に頼まれたものは順番待ちにする)"""
    def task():
        if not busy.acquire(blocking=False):
            pending.put((fn, args))  # 会話中や作業中なら、終わってから順番に実行する
            ui(status.set, "受け付けました。少しお待ちください...")
            return
        try:
            fn(*args)
        finally:
            busy.release()
            if not pending.empty() and not wake_on.is_set():  # 順番待ちがあれば続けて実行
                nxt, nxt_args = pending.get()
                run_task(nxt, *nxt_args)
            elif not wake_on.is_set():
                ui(status.set, "会話中 (3分間は続けて話せます)")
                ui(mark_activity)
    threading.Thread(target=task, daemon=True).start()


GREETING = "こんにちは。何かお手伝いできることはありますか？"


WORK_WORDS = ("分析", "設計", "レポート", "調査", "実装", "修正して", "リファクタ", "仕様", "設計書", "コードを",
              "プログラムを", "まとめて", "比較して", "作成して")
# 画面を自分で操作して進める「自動操作モード」を頼む言葉。これらが来たら確認を挟まずそのまま始める
# (不可逆な操作はしない安全設計で、途中は名前を呼ぶか「止めて」で中断できるので、毎回の開始確認はしない)
OPERATE_WORDS = ("自動で", "自動操作", "自動モード", "全部やって", "ぜんぶやって", "最後までやって",
                 "自分で操作", "自分でやって", "代わりに操作", "代わりにやって", "自動でやって", "画面を操作して",
                 # 画面(タブ・ウィンドウ・ブラウザ)を片付ける依頼も、実際の画面操作なので自動操作モードで処理する
                 # (調査モードに回ると「閉じる」代わりにレポートを書くだけで、実際には閉じられないため)
                 "片付けて", "かたづけて", "整理して進めて", "整理して続けて", "画面を整理", "タブを整理",
                 "タブを閉じ", "ウィンドウを閉じ", "閉じて整理", "開きっぱなし", "クローズ",
                 "ブラウザを閉じ", "ブラウザは閉じ", "ブラウザを整理", "不要なタブ", "不要なブラウザ",
                 "不要なウィンドウ", "いらないタブ", "いらないブラウザ", "余計なブラウザ", "余計なタブ")
# n8n ワークフローを作る依頼の合図。これらが来たら Claude に n8n ワークフローJSONを作らせて取り込む
WORKFLOW_WORDS = ("ワークフロー", "n8n", "エヌエイトエヌ", "自動化を作", "自動化作", "自動化のフロー", "フローを作")
pending_dev = None  # 作業モードで「プログラムの変更が必要」となった依頼 (「はい」を待っている)
pending_operate = None  # 自動操作モードの開始確認を待っている依頼 (「はい」を待っている)
pending_operate_resume = False  # 自動操作が時間切れで一区切りし、「続けて」で続きから再開できる状態か
operate_stop = None  # 自動操作が動いている間だけ、その中断フラグ (「止めて」で外からも止められるように)


def is_work_request(text):
    """長い作業 (作業モード) として扱う依頼か"""
    return len(text) >= 80 or "\n" in text.strip() or any(w in text for w in WORK_WORDS)


def is_operate_request(text):
    """画面を自分で操作して進める『自動操作モード』の依頼か"""
    return any(w in text for w in OPERATE_WORDS)


def is_workflow_request(text):
    """n8n でワークフロー(自動化)を作る依頼か"""
    return any(w in text for w in WORKFLOW_WORDS)


attention_router = None


def _rule_based_intent(text):
    """ルールベースの判定だけで、短い文の意図がはっきり1つに決まるならそのラベルを返す。
    自己学習の教師データ用 (operate/work/chatのどれかに絶対に決まる場合だけ使う)"""
    matched = set()
    if is_operate_request(text):
        matched.add("operate")
    if is_work_request(text) or is_workflow_request(text):
        matched.add("work")
    return matched.pop() if len(matched) == 1 else None


def attention_intent(text):
    """明示的なルールで判定できない短い依頼をSelf-Attentionで分類する。
    モデルが利用できない場合は既存のルーティングを変更しない。
    確信度が低くて判定できなかった場合、ルールベースで正解が分かるなら実例として学習データに記録する
    (自己学習: 蓄積した実例は次回の起動時にAttentionIntentRouterの学習に使われる)"""
    global attention_router
    if len(text) > 40 or "\n" in text:
        return None
    if attention_router is None:
        try:
            from self_attention import AttentionIntentRouter
            attention_router = AttentionIntentRouter(extra_examples=memory_store.load_intent_examples())
        except (ImportError, RuntimeError, ValueError) as e:
            log(f"Self-Attention分類器を開始できません: {e!r}")
            attention_router = False
    if not attention_router:
        return None
    label, confidence = attention_router.classify(text)
    log(f"Self-Attention意図分類: {label} ({confidence:.2f})")
    # 自動操作(operate)は画面を乗っ取る重い処理で、誤爆すると会話が一番かみ合わなくなる。
    # 明示の合図(OPERATE_WORDS)は呼び出し元で既に拾っているので、ここに来た operate は
    # 分類器がいくら高確信でも採用せず、会話として扱う(本当に操作が要るなら会話のAIが促す)。
    # 調査(work)は、短い相づち・雑談を巻き込まないよう、確信が非常に高く かつ ある程度の長さの
    # 依頼のときだけ採用する。それ以外は会話に倒し、記憶や文脈を生かして自然に答える。
    if label == "work" and confidence >= 0.97 and len(text) >= 15:
        return "work"
    rule_label = _rule_based_intent(text)
    if rule_label:
        memory_store.add_intent_example(text, rule_label)
    return None


# 作業モード中(30分以内)に「その作業の続き・修正」を頼む一言の手がかり。
# これが無い短い相づちや雑談・暴言は作業の続きに飲み込まず、ふつうの会話として扱う
WORK_CONTINUE_CUES = ("続き", "続けて", "つづけ", "その調子", "進めて", "追加して",
                      "直して", "修正して", "書き直して", "まとめ直して", "やり直して")


def is_work_continuation(text):
    """作業中に、その作業の続きや手直しを頼む一言か"""
    return any(c in text for c in WORK_CONTINUE_CUES)


# 今していること (読み上げ・音楽・自動操作) をすぐ止めてほしい合図
STOP_WORDS = ("止めて", "とめて", "停止", "ストップ", "すとっぷ", "やめて", "中断", "stop")


def is_stop(text):
    """今の動作を止めてほしい、という合図か (短い一言のときだけ。長い文は普通の会話として扱う)"""
    t = " ".join(text.split())
    if len(t) > 14:
        return False
    return any(w in t.lower() for w in STOP_WORDS)


def is_yes(text):
    return any(w in text.lower() for w in ("はい", "お願い", "いいよ", "どうぞ", "進めて", "ok", "やって",
                                           "始めて", "はじめて", "始める", "スタート", "いいです", "うん",
                                           "hai", "yes", "ok"))


def is_resume_dev(text):
    """途中で止まった改修を『続きから再開』する合図か (作業モードの『続けて』とは別の言い回しにする)"""
    return any(w in text for w in ("続きをやって", "続きをお願い", "続きを作って", "続きから",
                                   "さっきの続き", "改修の続き", "途中から", "再開して"))


# JARVIS 自身の再起動/シャットダウン以外を指している (Kali などの話) と分かる手がかり。
# 「Kali Linux を再起動する」のような手順や話題に含まれる「再起動」で誤って自分を再起動しないため
OTHER_SUBJECT_WORDS = ("kali", "カリ", "ケイリ", "linux", "リナックス", "ubuntu", "ウブントゥ",
                       "wsl", "ダブリュエスエル", "windows", "ウィンドウズ", "パソコン", "pc",
                       "サーバ", "ルータ", "ルーター", "マシン", "仮想", "vm", "グラブ", "grub",
                       "ブラウザ", "edge", "chrome", "アプリ", "ソフト", "端末", "ターミナル")


def _is_self_command(text, words):
    """その言葉が『JARVIS 自身への短い直接命令』として言われたか。
    長い文・貼り付け・手順・質問や、他のもの (Kali など) の話に含まれるときは当てない"""
    t = " ".join(text.split())
    if "\n" in text.strip() or len(t) > 18:  # 長文・貼り付け・手順は対象外
        return False
    low = t.lower()
    if any(w in low for w in OTHER_SUBJECT_WORDS):  # 他のものを指している
        return False
    if any(q in t for q in ("何を", "どれ", "なに", "?", "？")):  # 質問は対象外
        return False
    return any(w in t for w in words)


def is_self_restart(text):
    """JARVIS 自身を再起動してほしい、という直接の命令か"""
    return _is_self_command(text, ("再起動", "リスタート"))


def is_self_shutdown(text):
    """JARVIS 自身を終了してほしい、という直接の命令か"""
    return _is_self_command(text, ("シャットダウン",))


def do_work(text, images=()):
    """作業モード: 調べて、docs に成果物を保存し、要点を読み上げる"""
    global pending_dev
    add_chat("あなた", text)
    show_sent_images(images)
    if assistant.work_active():
        speak("わかりました、続けて作業しますね。")
    else:
        speak("わかりました、作業しますね。数分かかることがあるので、終わったらお知らせします。")
    ui(status.set, "作業中... (数分かかることがあります)")
    def progress(message):  # Claude Code のように、今何をしているかを表示する
        log(f"作業中: {message}")
        ui(status.set, short("作業中: " + message, 60))
        add_chat("JARVIS (作業)", message)

    try:
        with Relax():  # 作業中はリラックス音楽
            reply, doc = assistant.work(text, on_progress=progress, images=images)
    except Exception as e:
        log(f"作業モードのエラー: {e!r}")
        speak("ごめんなさい、作業を最後までできませんでした。少し依頼を短くして、もう一度お願いできますか？")
        return
    log(f"作業結果: {reply[:200]} / {doc}")
    if ai.DEV_MARKER in reply:  # 前置きの文が付いていても、開発の依頼として扱う
        pending_dev = reply.split(ai.DEV_MARKER, 1)[1].strip()
        add_chat("JARVIS", "(プログラムの変更が必要です)\n" + pending_dev[:300])
        speak("この作業には、プログラムの変更が必要です。変更してよければ、はい、と話すか入力してください。")
        return
    add_chat("JARVIS", reply + (f"\n(保存: docs/{doc.name})" if doc else ""))
    speak(reply)
    if doc:
        os.startfile(doc)  # 成果物を開く


def do_workflow(text):
    """n8n ワークフロー作成モード: Claude がワークフロー JSON を作り、n8n に取り込んで画面で確認してもらう"""
    add_chat("あなた", text)
    speak("わかりました、n8n のワークフローを作りますね。少し時間がかかります。")
    ui(status.set, "ワークフロー作成中...")
    def progress(message):
        log(f"ワークフロー作成: {message}")
        ui(status.set, short("ワークフロー: " + message, 60))
        add_chat("JARVIS (作業)", message)

    try:
        n8n_tools.ensure_running(log)  # n8n を必要なら起動
    except Exception as e:  # noqa: BLE001
        log(f"n8n 起動エラー: {e!r}")
        speak("ごめんなさい、n8n を起動できませんでした。n8n が入っているか確認してもらえますか。")
        return
    try:
        with Relax():  # 作成中はリラックス音楽
            reply, wf = assistant.make_workflow(text, on_progress=progress)
    except Exception as e:  # noqa: BLE001
        log(f"ワークフロー生成エラー: {e!r}")
        speak("ごめんなさい、ワークフローを最後まで作れませんでした。もう少し短く、もう一度お願いできますか？")
        return
    if not wf:  # JSON が作られなかった (説明だけで終わった等)
        add_chat("JARVIS", reply)
        speak(reply)
        return
    try:
        n8n_tools.import_workflow(wf)
    except Exception as e:  # noqa: BLE001
        log(f"n8n 取り込みエラー: {e!r}")
        add_chat("JARVIS", reply + f"\n(生成: workflows/{wf.name} ※取り込みは失敗)")
        speak(reply + " ただ、n8n への取り込みがうまくいきませんでした。ファイルは残してあります。")
        return
    add_chat("JARVIS", reply + f"\n(生成・取り込み済み: {wf.name})")
    speak(reply)
    try:
        n8n_tools.open_ui()  # ブラウザで n8n を開いて確認してもらう
    except Exception:  # noqa: BLE001
        pass


def recent_context(limit=10, max_chars=2000):
    """直近の会話を「話者: 内容」の短い記録にまとめる。
    自動操作モードは新しいセッションで文脈を持たないので、これを渡して
    「進めて」のような短い目標が何を指すのかの手がかりにする"""
    lines = []
    for who, text in chat_log[-limit:]:
        if who in ("JARVIS (操作)", "JARVIS (作業)"):  # 実況の断片はノイズなので除く
            continue
        t = " ".join(str(text).split())
        if t:
            lines.append(f"{who}: {t[:300]}")
    return "\n".join(lines)[-max_chars:]


def watch_for_operate_interrupt(stop, question):
    """自動操作の間、別スレッドでマイクを聞き続け、『ジャービス』と呼びかけられたら操作を止める。
    物音やJARVIS自身の実況では止めない (実況に『ジャービス』は出てこないので、呼びかけ語だけを合図にする)。
    聞き取った呼びかけの『ジャービス』を除いた部分 (質問の中身) を question に入れ、stop を立てる"""
    while not stop.is_set():
        heard = listen(None, timeout=2, quiet=True, announce=False)  # 短く聞いて呼びかけを待つ (記録は汚さない)
        if heard and has_wake_word(heard):
            question[0] = strip_wake_word(heard).strip()
            interrupt_tts.set()  # 今読み上げている実況をすぐ止める
            stop.set()
            log(f"自動操作: 『ジャービス』の呼びかけで中断 (質問: {question[0] or '(なし)'})")
            return


def do_operate(text, resume=False):
    """自動操作モード: 画面を自分で見て判断し、マウス・キーボードを操作して目標を達成する。
    依頼はこの手前の確認で既にチャットに表示済みなので、ここでは開始だけ知らせる。
    操作中に『ジャービス』と呼びかけられたら中断して、その質問に答える。
    resume=True なら、時間切れで一区切りした前回の自動操作の続きから進める"""
    global pending_operate_resume, operate_stop
    if not resume:  # 新しい自動操作は、前回の続き(セッション)を引きずらないようにする
        assistant.operate_session_id = None
    pending_operate_resume = False
    no_barge_in.set()  # 操作の間は、実況の読み上げが自分の声を拾って途切れないようバージインを止める
    stop = threading.Event()
    operate_stop = stop  # 「止めて」で外からもこの操作を止められるようにする
    question = [None]
    # 案内文に呼びかけ語を入れると、自分で読み上げて自分の声で中断してしまうので、名前を直接言わない。
    # 見張りも、この案内を話し終えてから始める (案内中に自分の声で誤って止まらないように)
    speak("では、続きから進めますね。" if resume else
          "では、始めますね。途中で止めたいときは、わたしの名前を呼んでください。")
    watcher = threading.Thread(target=watch_for_operate_interrupt, args=(stop, question), daemon=True)
    watcher.start()
    try:
        ui(status.set, "自動操作中... (名前を呼ぶと中断できます)")
        def progress(message):  # 今どの画面操作をしているかを表示する。答えを見つけたときだけ声で知らせる
            if stop.is_set():  # 呼びかけで中断したら、もう表示しない
                return
            log(f"自動操作: {message}")
            if message.startswith(ai.SPEAK_PREFIX):  # 答え(フラグ・パスワードなど)が分かった → 声で読み上げる
                found = message[len(ai.SPEAK_PREFIX):].strip()
                ui(status.set, short("答えが分かりました: " + found, 60))
                add_chat("JARVIS", found)
                speak(found)
            else:  # ふだんの途中経過は画面表示だけ (声には出さない)
                ui(status.set, short("自動操作: " + message, 60))
                add_chat("JARVIS (操作)", message)

        try:
            reply = assistant.operate(text, context=recent_context(), on_progress=progress,
                                      should_stop=stop.is_set, resume=resume)
        except ai.OperationInterrupted:
            stop.set()
            no_barge_in.clear()  # ここから先は普通の会話なので、バージイン (話し始めで止める) を戻す
            add_chat("JARVIS", "(自動操作を中断しました)")
            asked = question[0]
            if asked:
                route(asked)  # 呼びかけと一緒に言われた質問にそのまま答える
            else:
                speak("はい、いったん止めました。何かありますか？")
            return
        except subprocess.TimeoutExpired:  # 20分の制限時間切れ。セッションは残っているので続きから再開できる
            log("自動操作: 時間切れで一区切り (続きから再開可能)")
            pending_operate_resume = True
            add_chat("JARVIS", "(時間切れで一区切り。「続けて」で続きから進められます)")
            speak("いったん時間が来たので、一区切りにしました。ここまでの操作は画面にそのまま残っています。"
                  "続けて、と言っていただければ、今の状態から続きを進めます。")
            return
        except Exception as e:
            log(f"自動操作のエラー: {e!r}")
            speak("ごめんなさい、自動操作を最後までできませんでした。もう一度お願いできますか？")
            return
        log(f"自動操作の結果: {reply[:200]}")
        if ai.FIX_MARKER in reply:  # 自分の道具の不具合で進めなかった → 状況を報告してから、自分で修復を試みる
            symptom = reply.split(ai.FIX_MARKER, 1)[1].strip()
            add_chat("JARVIS", symptom)
            stop.set()  # 修復に入るので見張りを終える
            no_barge_in.clear()  # develop 中は実況しないのでバージインを戻してよい
            speak(symptom + " まず、改修が必要か私の方で確認しますので、少しお待ちください。")
            self_repair(symptom)
            return
        add_chat("JARVIS", reply)
        speak(reply)
    finally:
        stop.set()  # 見張りスレッドを終わらせる
        operate_stop = None  # 操作が終わったので、外からの「止めて」対象も消す
        no_barge_in.clear()  # 操作が終わったら (エラーや中断でも) バージインを元に戻す


def route(text, typed=False, images=()):
    """話しかけられた言葉を AI に渡す (AI が使えなければ従来の処理)。
    typed=True はチャット入力。作業の続きの会話中なら、短い一言でも作業の続きとして扱う"""
    global assistant, pending_dev, pending_operate, pending_operate_resume
    if is_stop(text):  # 「止めて」: 今の動作 (読み上げ・音楽・自動操作) をすぐ止める。何を止めるか聞き返さない
        add_chat("あなた", text)
        interrupt_tts.set()  # 読み上げ中なら止める
        try:
            features.media_key("止めて")  # 音楽が鳴っていれば止める
        except Exception:  # noqa: BLE001 (音楽が無いなど、止められなくても構わない)
            pass
        if operate_stop is not None:  # 自動操作が動いていれば止める
            operate_stop.set()
        pending_operate = pending_dev = None  # 開始待ちの確認もキャンセルする
        speak("はい、止めました。")
        return
    if assistant is None:  # AI が使えないときは従来の処理へ
        handle(text)
        return
    if is_self_restart(text):  # JARVIS 自身の再起動 (確認付き)。Kali などの話に含まれる「再起動」では誤爆しない
        confirm_user_restart(text)
        return
    if is_self_shutdown(text):  # JARVIS 自身のシャットダウン (確認付き)
        handle(text)
        return
    if assistant.dev_resume and is_resume_dev(text):  # 途中で止まった改修を続きから再開する
        add_chat("あなた", text)
        develop(assistant.dev_resume.get("request", ""), resume=True)
        return
    if pending_operate_resume and (is_yes(text) or is_resume_dev(text)
                                   or any(w in text for w in ("続け", "つづけ", "再開", "進めて"))):
        # 時間切れで一区切りした自動操作を、続きから再開する
        pending_operate_resume = False
        add_chat("あなた", text)
        do_operate(text, resume=True)
        return
    if pending_dev:  # 作業モードで変更の確認を待っているとき
        request, pending_dev = pending_dev, None
        if is_yes(text):
            develop(request)
            return
        speak("わかりました、変更はやめておきますね。")
        if len(text) < 6:
            return
    if is_workflow_request(text):  # n8n ワークフローを作る依頼。Claudeが生成し n8n に取り込む
        do_workflow(text)
        return
    if is_operate_request(text):  # 画面を自分で操作して進める。確認は挟まず、そのまま始める
        add_chat("あなた", text)
        show_sent_images(images)
        do_operate(text)
        return
    if is_work_request(text) or (typed and assistant.work_active() and is_work_continuation(text)):
        do_work(text, images)
        return
    inferred_intent = attention_intent(text)
    if inferred_intent == "operate":
        add_chat("あなた", text)
        show_sent_images(images)
        do_operate(text)
        return
    if inferred_intent == "work":
        do_work(text, images)
        return
    ui(status.set, "考え中...")
    ctx = recent_context()  # 今の発言を add_chat する前の、直前までの会話 (session切れでも文脈を渡すため)
    add_chat("あなた", text)
    show_sent_images(images)
    try:
        with Relax(after=10):  # 10秒以上考えていたら、リラックス音楽を流して待つ
            reply = assistant.ask(text, images, context=ctx)
    except subprocess.TimeoutExpired:  # 時間のかかる処理 (大きなポートスキャンなど) で1ターンの制限時間を超えた
        log("AIエラー: 会話の応答がタイムアウトしました")
        ui(show_info, "考えるのに時間がかかりすぎたので、いったん中断しました。\n調べる範囲を少し狭めて、もう一度お願いします。")
        speak("ごめんなさい、考えるのに時間がかかりすぎました。範囲を少し狭めて、もう一度お願いできますか。")
        return
    except Exception as e:  # ネットワークや API キーの問題
        # 例外の文字列には実行コマンド (システムプロンプト全文) が丸ごと入ることがあるので、
        # 画面やログにそのまま出さず、1行に短くまとめる
        detail = next((ln for ln in str(e).splitlines() if ln.strip()), "") [:200] or e.__class__.__name__
        log(f"AIエラー: {e.__class__.__name__}: {detail}")
        ui(show_info, "ごめんなさい、今AIにつながりませんでした。")
        speak("ごめんなさい、今AIにつながらないので、いつもの機能でお手伝いしますね。")
        handle(text)
        return
    if ai.DEV_MARKER in reply:  # 前置きの文が付いていても、開発の依頼として扱う
        intro, request = reply.split(ai.DEV_MARKER, 1)
        if intro.strip():  # 「まず改修が必要か確認しますのでお待ちください」などの前置きは声でも伝える
            add_chat("JARVIS", intro.strip())
            speak(intro.strip())
        develop(request.strip())
        return
    add_chat("JARVIS", reply)
    def learn():
        try:
            memory_store.auto_learn(text, reply)
        except (RuntimeError, ValueError, OSError) as error:
            log(f"会話の記憶保存に失敗しました: {error}")

    threading.Thread(target=learn, daemon=True).start()  # 会話から自動で学習記録
    speak(reply)
    if features.restart_pending():  # commit でプログラムの変更が保存された (develop() と同様に再起動を確認する)
        confirm_restart()


def develop(request, resume=False):
    """開発担当の AI に JARVIS のプログラムを書き換えてもらい、再起動して反映する。
    resume=True なら、途中で止まった前回の改修を --resume で続きから仕上げる"""
    log(f"開発依頼: {request} (resume={resume})")
    add_chat("JARVIS", ("(続きから改修中) " if resume else "(開発中) ") + request)
    speak("では、続きから進めますね。" if resume else
          "わかりました、作ってみますね。数分かかることがあるので、少しだけ待っていてください。")
    ui(status.set, "プログラムを書き換え中... (数分かかることがあります)")
    try:
        with Relax():  # 数分かかることがあるので、書き換えている間はリラックス音楽を流す
            ok, report = assistant.develop(request, resume=resume)
    except Exception as e:
        ok, report = False, f"作業できませんでした。{e}"
    log(f"開発結果: {report}")
    add_chat("JARVIS", report)
    speak(report)
    if ok:
        # 直した内容を GitHub にも保存する (ローカルの backups だけでなく、私の作業と同じくリポジトリにも残す)
        try:
            result = features.git_commit("自動改修: " + " ".join(report.split())[:50])
            if result:
                log(f"自動Push: {result[1]}")
                add_chat("JARVIS", result[0])
        except Exception as e:
            log(f"自動Pushのエラー: {e!r}")
        features.restart_pending()  # git_commit が立てた再起動フラグは、この後の確認で扱うので消しておく
        confirm_restart()


def self_repair(symptom):
    """自動操作中に分かった『ジャービス自身の道具の不具合』を、ログと自分のプログラムを見て自分で直す。
    開発担当の AI (develop) に、不具合の修正として依頼する。直ったら再起動で反映する"""
    request = ("自動操作の途中で、ジャービス自身の道具の不具合、または『進めるのに必要な道具が無い』状況が"
               "見つかりました。これを直すか、足りない道具(コマンド)を追加してください。\n"
               f"内容: {symptom}\n"
               "jarvis.log の最近の「自動操作:」「画面操作:」の記録に、実際に実行したコマンドとその結果の"
               "手がかりがあります。不具合なら原因を突き止めて直し、道具が無いなら features.py と "
               "jarvis_tools.py に追加して ai.py のコマンド一覧にも載せてください。")
    develop(request)


def confirm_restart():
    """プログラムの変更を反映するために再起動してよいか確認し、同意されたら再起動する
    (develop() でのプログラムの書き換え後と、commit でプログラムの変更を保存したあとに使う)"""
    speak("変更を反映するには再起動が必要です。今すぐ再起動しますか？")
    answer = listen()
    if answer and any(w in answer for w in ("はい", "うん", "お願い", "して", "いいよ")):
        speak("再起動します。")
        restart()
    else:
        speak("わかりました。次に起動したときに反映されます。")


RESTART_STATE_FILE = Path(__file__).with_name("restart_state.json")


RESTART_RESTORE_WINDOW = 1800  # 30分以内の再起動なら、前の会話を続きとして復元する


def save_restart_state():
    """会話の続きを話せるように、直近の会話と学習中フラグを保存する。
    停止バット・プロセス終了など強制終了でも残るよう、動作中は定期的にも自動保存する。
    続きは復元した会話(chat_log → recent_context)から文脈として渡し直すので、
    プロバイダ(Gemini/Claude/Ollama)をまたいでも続く。session_id の引き継ぎには依存しない。"""
    if not chat_log:
        return
    state = {"chat_log": chat_log[-40:], "saved_at": time.time()}
    if assistant is not None:
        state["security_thread"] = assistant._security_thread
    try:
        RESTART_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def autosave_restart_state():
    """強制終了でも直近の会話が失われないよう、動いている間は定期的に保存し続ける。"""
    while True:
        threading.Event().wait(30)
        save_restart_state()


def restart():
    """JARVIS を起動し直す (新しいプログラムを読み込む)"""
    save_restart_state()
    _kernel32.CloseHandle(_mutex)  # 二重起動防止の目印を外してから新しい JARVIS を起動する
    subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=Path(__file__).parent)
    os._exit(0)


chat_log = []
restored = False  # 再起動直後に、前の会話を復元できたか


def restore_state():
    """再起動の直前に保存した会話があれば読み込んで復元する (読み込んだら消す)"""
    global assistant, restored
    if not RESTART_STATE_FILE.exists():
        return
    try:
        state = json.loads(RESTART_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    RESTART_STATE_FILE.unlink(missing_ok=True)
    # 長時間空いた後の起動は「続き」にせず、新しい会話として始める (古い文脈を蒸し返さない)
    if time.time() - state.get("saved_at", 0) > RESTART_RESTORE_WINDOW:
        return
    log_entries = state.get("chat_log", [])
    if not log_entries:
        return
    for who, text in log_entries:
        add_chat(who, text)
    if assistant is not None:
        # 続きは復元した会話を文脈として渡すので、session_id の resume には頼らない
        # (Gemini セッションは再起動で消えるため。chat_log があれば recent_context で続けられる)
        assistant.session_id = None
        if state.get("security_thread"):
            assistant._security_thread = True
            # 直後の発言で 10分ルールにより security_thread が即リセットされないようにする
            assistant.last_used = time.time()
    restored = True


def add_chat(who, text):
    """会話を左の会話の欄に流す (Claude Code のように、全部の会話が残る)"""
    chat_log.append((who, text))
    if who in ("JARVIS (作業)", "JARVIS (操作)"):
        ui(chat_append, "step", text)
    elif who in ("あなた", "スマホ"):
        ui(chat_append, "user", text, who)
    else:
        ui(chat_append, "ai", text, who)


def conversation(first=None):
    """AI と会話する。返事のあとも続けて話しかければ会話が続き、黙ると終わる"""
    if first is None:
        greet()
        first = listen(follow_up=True)
    text = first
    while text:
        route(strip_wake_word(text))
        text = None
        until = time.time() + KEEP_TALKING_SECONDS  # 3分間は続けて話しかけられる
        while time.time() < until and not text:
            if not pending.empty():  # 会話中にチャットやボタンで頼まれたもの
                fn, args = pending.get()
                fn(*args)
                until = time.time() + KEEP_TALKING_SECONDS
                continue
            left = int(until - time.time())
            text = listen(f"続けてどうぞ (あと {left // 60}分{left % 60:02d}秒で待機中)", timeout=6, quiet=True, follow_up=True)


def converse():
    conversation()


NOTICE_FILE = Path(__file__).with_name("notice.txt")


def speak_notice():
    """お知らせ (notice.txt) があれば、あいさつのあとに読み上げて消す (例: 頼まれた修正が終わったこと)"""
    if NOTICE_FILE.exists():
        text = NOTICE_FILE.read_text(encoding="utf-8").strip()
        NOTICE_FILE.unlink()
        if text:
            add_chat("JARVIS", text)
            speak(text)


def _proxy_warning():
    """バープなどが Windows のプロキシを有効にしていると HTTPS 通信が失敗しやすいので、
    有効なら一言そえる (無効ならなにも言わない)。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
        if enable and server:
            return f"ただ、プロキシ {server} が有効なので、通信がうまくいかないことがあります。"
    except OSError:
        pass
    return ""


def startup_system_check():
    """起動時に、使えるAIと通信まわりをざっと確認して、声で伝える一言にする。"""
    if assistant is None:
        return "ただ、AIの準備ができていないようです。設定を確認してください。"
    avail = []
    if assistant.gemini:
        avail.append("ジェミニ")
    try:
        if assistant._local_model_ready():
            avail.append("ローカルAI")
    except Exception:
        pass
    if assistant.exe:
        avail.append("クロード")
    status = ("使えるAIは、" + "、".join(avail) + "です。") if avail else "使えるAIが見つかりませんでした。"
    warn = _proxy_warning()
    log(f"起動時システムチェック: AI={avail or 'なし'} / proxy={'警告あり' if warn else 'OK'}")
    return "システムを確認しました。" + status + warn


def _last_topic():
    """復元した会話から、直前にユーザーが話していた話題を一言で拾う (あいさつで触れるため)。"""
    for who, text in reversed(chat_log):
        if who in ("あなた", "スマホ"):
            t = " ".join(str(text).split())
            if t:
                return t[:40]
    return ""


def greet():
    """最初のあいさつ。起動時にまずシステムチェックと前の会話の確認をしてから、それをもとに話し始める。"""
    # 起動あいさつの流れ: ①システムチェック結果 → ②直近の会話の確認 →
    # ③その続きを進めるか、別のことをするかの問いかけ。
    check = startup_system_check()
    if restored:
        topic = _last_topic()
        if topic:
            say = (f"おかえりなさい。{check}さっきは、{topic}、というお話でしたね。"
                   "この続きを進めますか。それとも、別のことをしますか。")
        else:
            say = (f"おかえりなさい。{check}さっきの続きがあります。"
                   "続きを進めますか。それとも、別のことをしますか。")
    else:
        say = f"{GREETING}{check}今日は何をしましょうか。"
    add_chat("JARVIS", say)
    speak(say)
    speak_notice()
    if assistant is not None and assistant.dev_resume:  # 前回の改修が途中で止まっていたら知らせる
        speak("前回の改修が途中で止まっています。続きをやって、と言っていただければ、続きから進めます。")


def wake_loop():
    """ずっと聞き続けて、話しかけられたら AI と会話する"""
    idle = "聞いています。いつでも話しかけてください"
    greet()
    ui(status.set, idle)
    while wake_on.is_set():
        while not pending.empty():  # 待ち受け中に押されたボタンの処理
            fn, args = pending.get()
            fn(*args)
            ui(status.set, idle)
        # 短い間隔で聞き直して、ボタンが押されたらすぐ反応できるようにする
        text = listen(None, timeout=2, quiet=True, follow_up=True)
        if not text:
            continue
        if assistant is None and not has_wake_word(text):
            # AI が使えないときは、知っている命令だけ「ジャービス」なしで実行する
            if not handle(text, fallback=False):
                ui(status.set, "命令が見つかりませんでした。" + idle)
            continue
        # 「ジャービス」だけなら「何かありますか？」と聞き、それ以外はそのまま会話を始める
        conversation(strip_wake_word(text) or None)
        ui(status.set, idle)
    while not pending.empty():  # 待ち受けをやめる直前に押されたボタンの処理
        fn, args = pending.get()
        fn(*args)
    ui(status.set, "待機中...")


def toggle_wake():
    save_settings(always_listen=wake_mode.get())
    if wake_mode.get():
        wake_on.set()

        def start():
            with busy:  # 実行中の処理があれば終わるのを待ってから待ち受ける
                wake_loop()
        threading.Thread(target=start, daemon=True).start()
    else:
        wake_on.clear()


# ---------- スマホから使う (Web アプリ。家の Wi-Fi か Tailscale 経由) ----------
phone = None


def show_phone_qr(url):
    """スマホで読み取る QR コードと使い方を真ん中の欄に出す"""
    buf = io.BytesIO()
    qrcode.make(url, box_size=4, border=2).save(buf, "PNG")
    photo = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
    chat_append("image", photo)
    remote_url = phone.remote_url if phone else None
    remote_note = (f"\n外出先からは、Tailscale 経由でこのアドレスでも使えます:\n{remote_url}\n"
                   "(出かける前にスマホのブラウザでブックマークしておくと便利です)" if remote_url else
                   "\n外出先からも使うには、パソコンとスマホの両方に Tailscale を入れてログインしてください。")
    chat_append("info", (
        "■ スマホから使う\n"
        "スマホのカメラでこの QR コードを読み取ると、JARVIS が開きます。\n"
        "ホーム画面に追加すると、アプリのように使えます。\n"
        "(スマホとパソコンを同じ Wi-Fi につないでください)"
        + remote_note))
    chat_images.append(photo)  # 画像が消えないように持っておく


def toggle_phone():
    global phone
    save_settings(phone_enabled=phone_mode.get())
    if phone_mode.get():
        try:
            phone = phone_server.PhoneServer(
                load_settings()["phone_token"], log=log,
                on_chat=lambda who, text: add_chat("JARVIS→スマホ" if who == "JARVIS" else who, text))
            phone.start()
        except Exception as e:
            phone = None
            phone_mode.set(False)
            show_info(f"スマホ用の画面を開始できませんでした:\n{e}")
            return
        show_phone_qr(phone.url)
    elif phone:
        phone.stop()
        phone = None


# ---------- 画面 ----------
# 宇宙をウィンドウ全体の背景にして、文字やボタンをその上に重ねる
canvas = tk.Canvas(root, width=400, height=360, bg=BG, highlightthickness=0)
canvas.pack(fill="both", expand=True)

status = tk.StringVar(value="待機中...")
heard = tk.StringVar(value="")
reply = tk.StringVar(value="")


def overlay_text(var=None, color=TEXT, size=11, anchor="center", width=0):
    """宇宙の上に読みやすい文字 (影付き) を置く。var を渡すと内容が自動で変わる"""
    font = ("Meiryo", size)
    shade = canvas.create_text(0, 0, text="", fill="#000000", font=font, anchor=anchor, width=width,
                               tags="overlay")
    item = canvas.create_text(0, 0, text="", fill=color, font=font, anchor=anchor, width=width,
                              tags="overlay")

    def set_text(text):
        canvas.itemconfigure(shade, text=text)
        canvas.itemconfigure(item, text=text)

    def move(x, y):
        canvas.coords(shade, x + 1, y + 1)
        canvas.coords(item, x, y)

    if var is not None:
        var.trace_add("write", lambda *_: set_text(var.get()))
        set_text(var.get())
    return set_text, move


status_text = overlay_text(status, CYAN, 12)
heard_text = overlay_text(heard, TEXT, 11)
reply_text = overlay_text(reply, TEXT, 11)



def space_mode():
    """宇宙アニメーションの動き方を JARVIS の状態から決める"""
    if speaking.is_set():
        return "speaking"
    text = status.get()
    if "考え中" in text or "書き換え" in text or "認識中" in text:
        return "thinking"
    if "聞いて" in text or "話して" in text or "どうぞ" in text:
        return "listening"
    return "idle"


space = ForestView(canvas, space_mode)  # 顔の雰囲気に合わせた幻想的な森


def weather_watcher():
    """30分ごとに今の天気を調べて、宇宙の色と雰囲気に反映する"""
    while True:
        try:
            city, query = features.current_location()  # 今いる場所の天気
            data = features.requests.get(f"https://wttr.in/{query}?format=j1", timeout=15).json()
            kind = weather_kind(data["current_condition"][0]["weatherCode"])
            ui(space.set_weather, kind, city)
        except Exception as e:  # オフラインなど。前の色のまま
            log(f"天気の取得に失敗 (画面の色): {e!r}")
        threading.Event().wait(1800)


HEALTH_CHECK_INTERVAL = 180  # 3分ごとに会話の調子をチェックする


def conversation_health_monitor():
    """定期的に聞き取りの調子を見て、スムーズにやり取りできるよう待ち時間やマイク感度を自動で調整する。
    自動操作の最中は、実況や操作を乱さないよう調整しない (落ち着いてからの回に回す)。"""
    global PAUSE_THRESHOLD, last_calibrated
    while True:
        threading.Event().wait(HEALTH_CHECK_INTERVAL)
        if operate_stop is not None or no_barge_in.is_set():
            continue  # 自動操作中は触らない。カウンタは残して、落ち着いた次の回に判断する
        with recog_stats_lock:
            ok, unknown, stitched = recog_stats["ok"], recog_stats["unknown"], recog_stats["stitched"]
        total = ok + unknown
        if total < 4:
            continue  # サンプルが少ないと調整が暴れるので見送る (カウンタは貯めたまま)
        with recog_stats_lock:
            recog_stats.update(ok=0, unknown=0, stitched=0)
        miss_rate = unknown / total
        note = None
        if miss_rate >= 0.4:  # 聞き取れない率が高い → 雑音/感度の問題。測り直して、間も少し延ばす
            last_calibrated = 0  # 次の聞き取りでマイクを測り直す
            if PAUSE_THRESHOLD < 10.0:
                PAUSE_THRESHOLD = round(min(10.0, PAUSE_THRESHOLD + 1.0), 1)
                recognizer.pause_threshold = PAUSE_THRESHOLD
                note = f"聞き取れないことが多めなので、待ち時間を{PAUSE_THRESHOLD}秒に延ばし、マイクを測り直します"
            else:
                note = "聞き取れないことが多めなので、マイクを測り直します"
        elif stitched >= 2 and stitched * 2 >= ok and PAUSE_THRESHOLD < 10.0:  # 文がよく途中で切れる → もっと待つ
            PAUSE_THRESHOLD = round(min(10.0, PAUSE_THRESHOLD + 1.0), 1)
            recognizer.pause_threshold = PAUSE_THRESHOLD
            note = f"文の途中で切れがちなので、待ち時間を{PAUSE_THRESHOLD}秒に延ばします"
        elif miss_rate <= 0.05 and stitched == 0 and PAUSE_THRESHOLD > 5.0:  # とても順調 → 間を詰めて反応を速く
            PAUSE_THRESHOLD = round(max(5.0, PAUSE_THRESHOLD - 0.5), 1)
            recognizer.pause_threshold = PAUSE_THRESHOLD
            note = f"調子がよいので、待ち時間を{PAUSE_THRESHOLD}秒に詰めて反応を速くします"
        if note:
            save_settings(pause_threshold=PAUSE_THRESHOLD)
            log(f"会話チェック: {note} (直近 聞き取り{ok}・聞き取れず{unknown}・継ぎ足し{stitched})")


def daily_usage_report():
    """毎日18時に、その日のAI使用量 (何にトークンを使ったか) を自動で報告する。
    自動操作の最中は邪魔しないよう、18時台のうちに落ち着いたタイミングで報告する。"""
    last_date = None
    while True:
        threading.Event().wait(60)  # 1分ごとに時刻を確認
        now = datetime.now()
        if now.hour == 18 and last_date != now.date():
            if operate_stop is not None or no_barge_in.is_set():
                continue  # 自動操作中は触らない。次の1分で再挑戦する
            last_date = now.date()
            try:
                say, show = features.usage_report()
                add_chat("JARVIS", say)
                ui(show_info, show)
                speak(say)
            except Exception as e:
                log(f"使用量レポートの自動報告に失敗: {e!r}")


threading.Thread(target=weather_watcher, daemon=True).start()
threading.Thread(target=conversation_health_monitor, daemon=True).start()  # 会話の調子を見て自動調整
threading.Thread(target=daily_usage_report, daemon=True).start()  # 毎日18時にAI使用量を報告
threading.Thread(target=cleanup.daily, args=(log,), daemon=True).start()  # 1日1回のお掃除


# ---------- 左: 会話の列 (Claude Code のような会話の欄と入力欄) ----------
INPUT_BG, USER_BG, STEP_FG, HINT_FG = "#1a211d", "#202824", "#7d8a80", "#8c8676"
convo = tk.Frame(root, bg=PANEL, highlightbackground=CYAN_DIM, highlightthickness=1)
header = tk.Frame(convo, bg=PANEL)
header.pack(fill="x")
tk.Label(header, text="会話", font=("Meiryo", 10, "bold"), bg=PANEL, fg=CYAN, padx=14, pady=8).pack(side="left")


def small_button(parent, text, command, **kw):
    return tk.Button(parent, text=text, command=command, font=("Meiryo", 9), bg=PANEL, fg=TEXT,
                     activebackground=CYAN_DIM, activeforeground=TEXT, relief="flat", bd=0, cursor="hand2",
                     highlightbackground=CYAN_DIM, highlightthickness=1, padx=10, pady=2, **kw)


# 設定の欄 (常に聞く・スマホから使う・声・速さ)。「⚙ 設定」で開いたり閉じたりする
panel = tk.Frame(convo, bg="#18201c", highlightbackground=CYAN_DIM, highlightthickness=1)


def toggle_settings():
    if panel.winfo_ismapped():
        panel.pack_forget()
    else:
        panel.pack(fill="x", padx=12, pady=(0, 6), after=header)


small_button(header, "⚙ 設定", toggle_settings).pack(side="right", padx=(0, 12))
small_button(header, "🎤 話す", lambda: run_task(converse)).pack(side="right", padx=4)

input_hint = tk.Label(convo, text="Enter で送信 / Shift+Enter で改行", font=("Meiryo", 8), bg=PANEL, fg=STEP_FG,
                      anchor="e", padx=14)
input_hint.pack(side="bottom", fill="x", pady=(0, 8))
input_box = tk.Frame(convo, bg=INPUT_BG, highlightbackground=CYAN_DIM, highlightcolor=CYAN, highlightthickness=1)
input_box.pack(side="bottom", fill="x", padx=12, pady=(8, 2))
attach_bar = tk.Frame(convo, bg=PANEL)  # 添付した画像の見本 (入力欄の上。添付したときだけ表示)
quick = tk.Frame(convo, bg=PANEL)  # すぐに使える操作 (入力欄の上)
quick.pack(side="bottom", fill="x", padx=12, pady=(6, 0))
QUICK_ACTIONS = [
    ("天気", lambda: run_task(handle, "今日の天気")),
    ("ニュース", lambda: run_task(handle, "ニュース")),
    ("予定", lambda: run_task(handle, "予定")),
    ("Web検索", lambda: run_task(handle, "検索")),
    ("音楽", lambda: run_task(play_request)),
    ("PC操作", lambda: run_task(ask_and_handle, "何をしますか？メモ帳、電卓、フォルダを開けます。")),
]
for i, (name, cmd) in enumerate(QUICK_ACTIONS):
    small_button(quick, name, cmd).grid(row=0, column=i, padx=(0, 6), sticky="w")
view_frame = tk.Frame(convo, bg=PANEL)
view_frame.pack(fill="both", expand=True)
chat_view = tk.Text(view_frame, height=5, wrap="char", bg=PANEL, fg=TEXT, font=("Meiryo", 11), relief="flat", bd=0,
                    padx=14, pady=6, state="disabled", cursor="arrow", spacing1=1, spacing3=1,
                    selectbackground=CYAN_DIM, highlightthickness=0)
view_scroll = tk.Scrollbar(view_frame, command=chat_view.yview, width=10)
chat_view.configure(yscrollcommand=view_scroll.set)
view_scroll.pack(side="right", fill="y")
chat_view.pack(side="left", fill="both", expand=True)
chat_view.tag_configure("who_user", foreground=CYAN, font=("Meiryo", 9, "bold"), spacing1=14)
chat_view.tag_configure("who_ai", foreground="#c8b48a", font=("Meiryo", 9, "bold"), spacing1=14)
chat_view.tag_configure("user", background=USER_BG, lmargin1=6, lmargin2=6, rmargin=6, spacing1=4, spacing3=4)
chat_view.tag_configure("ai", foreground=TEXT, lmargin1=6, lmargin2=6)
chat_view.tag_configure("code", font=("Consolas", 10), background="#0f1412", foreground="#e8e2d0",
                        lmargin1=14, lmargin2=14)
chat_view.tag_configure("step", foreground=STEP_FG, font=("Meiryo", 9), lmargin1=6, lmargin2=20)
chat_view.tag_configure("info", foreground="#b9b09a", font=("Meiryo", 10), background="#18201c",
                        lmargin1=6, lmargin2=6, spacing1=8, spacing3=8)
chat_images = []
last_kind = [None]


def chat_append(kind, text, who=None):
    """会話の欄に1つ書き足す (kind: user / ai / step / info / image)"""
    chat_view.configure(state="normal")
    if kind == "image":
        chat_view.insert("end", "\n")
        chat_view.image_create("end", image=text, padx=6)
        chat_view.insert("end", "\n")
    elif kind == "user":
        chat_view.insert("end", f"{who or 'あなた'}\n", "who_user")
        chat_view.insert("end", text.strip() + "\n", "user")
    elif kind == "ai":
        if last_kind[0] != "ai_body":
            chat_view.insert("end", f"{who or 'JARVIS'}\n", "who_ai")
        for i, part in enumerate(text.strip().split("```")):  # ``` で囲んだ部分はコードとして表示
            if i % 2:
                chat_view.insert("end", part.split("\n", 1)[-1].rstrip() + "\n", "code")
            elif part.strip():
                chat_view.insert("end", part.strip() + "\n", "ai")
        kind = "ai_body"
    elif kind == "step":
        chat_view.insert("end", "● " + text.strip() + "\n", "step")
    else:
        chat_view.insert("end", text.strip() + "\n", "info")
    last_kind[0] = kind
    chat_view.configure(state="disabled")
    chat_view.see("end")


def show_info(text):
    """天気などの結果を会話の欄に出す"""
    chat_append("info", text)


# 入力欄 (複数行。Enter で送信、Shift+Enter で改行。書いた量に合わせて高さが変わる)
CHAT_HINT = "メッセージを入力 (声で話しかけても大丈夫です)"
chat_entry = tk.Text(input_box, height=2, width=10, wrap="char", font=("Meiryo", 10), bg=INPUT_BG, fg=HINT_FG,
                     insertbackground=TEXT, relief="flat", bd=0, padx=12, pady=8, undo=True,
                     highlightthickness=0)
chat_entry.insert("1.0", CHAT_HINT)
chat_entry.pack(side="left", fill="both", expand=True)
tk.Button(input_box, text="送信", command=lambda: send_chat(), font=("Meiryo", 10, "bold"), bg=CYAN_DIM,
          fg=TEXT, activebackground=CYAN, activeforeground=BG, relief="flat", bd=0, cursor="hand2",
          padx=16).pack(side="right", fill="y")


def chat_text():
    text = chat_entry.get("1.0", "end").strip()
    return "" if text == CHAT_HINT else text


def fit_input(_=None):
    """書いた量に合わせて、入力欄の高さを2〜8行にする"""
    lines = chat_entry.count("1.0", "end", "displaylines")
    chat_entry.configure(height=max(2, min(8, (lines[0] if lines else 1))))


def send_chat(_=None):
    text = chat_text()
    images = [a["path"] for a in attachments]
    if not text and not images:
        return "break"
    if not text:
        text = "この画像について教えて"
    clear_attachments()
    chat_entry.delete("1.0", "end")
    fit_input()
    log(f"あなた (チャット): {text}")
    ui(heard.set, short(f"あなた: {text}"))
    run_task(route, text, True, images)  # 声で話しかけたときと同じように AI に渡して、返事を読み上げる
    return "break"


def new_line(_=None):
    chat_entry.insert("insert", "\n")
    fit_input()
    return "break"


def chat_focus(_):
    if chat_entry.get("1.0", "end").strip() == CHAT_HINT:  # 入力するときは案内の文を消す
        chat_entry.delete("1.0", "end")
        chat_entry.configure(fg=TEXT)


def chat_blur(_):
    if not chat_entry.get("1.0", "end").strip():
        chat_entry.insert("1.0", CHAT_HINT)
        chat_entry.configure(fg=HINT_FG)


# ---------- 画像の添付 (📎 で選ぶ / Ctrl+V で貼り付け) ----------
attachments = []  # {"path": Path, "thumb": PhotoImage, "frame": Frame}
ATTACH_TYPES = [("画像", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"), ("すべて", "*.*")]


def _thumb(path, size=64):
    from PIL import Image, ImageTk
    img = Image.open(path)
    img.thumbnail((size, size))
    return ImageTk.PhotoImage(img)


def add_attachment(src):
    """画像を attachments フォルダに保存して、入力欄の上に見本を出す"""
    from PIL import Image
    ai.ATTACH_DIR.mkdir(exist_ok=True)
    dest = ai.ATTACH_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{len(attachments) + 1}.png"
    img = src if isinstance(src, Image.Image) else Image.open(src)
    img.convert("RGB").save(dest, "PNG")
    item = {"path": dest, "thumb": _thumb(dest)}
    frame = tk.Frame(attach_bar, bg=PANEL)
    tk.Label(frame, image=item["thumb"], bg=PANEL, bd=1, relief="solid").pack(side="left")
    tk.Button(frame, text="×", font=("Meiryo", 8), bg=PANEL, fg=TEXT, relief="flat", bd=0, cursor="hand2",
              command=lambda: remove_attachment(item)).pack(side="left", anchor="n")
    frame.pack(side="left", padx=(0, 8))
    item["frame"] = frame
    attachments.append(item)
    if not attach_bar.winfo_ismapped():
        attach_bar.pack(side="bottom", fill="x", padx=12, pady=(6, 0), before=quick)


def remove_attachment(item):
    item["frame"].destroy()
    item["path"].unlink(missing_ok=True)  # 取り消した画像のファイルも消す
    attachments.remove(item)
    if not attachments:
        attach_bar.pack_forget()


def clear_attachments():
    for item in list(attachments):
        item["frame"].destroy()
    attachments.clear()
    attach_bar.pack_forget()


def choose_images():
    from tkinter import filedialog
    for f in filedialog.askopenfilenames(title="添付する画像を選ぶ", filetypes=ATTACH_TYPES):
        add_attachment(f)


def paste_image(_=None):
    """Ctrl+V: コピーした画像があれば添付する (文字なら普通に貼り付け)"""
    from PIL import ImageGrab
    try:
        data = ImageGrab.grabclipboard()
    except OSError:
        data = None
    files = []
    if data is not None:
        files = [data] if not isinstance(data, list) else [f for f in data if Path(f).suffix.lower() in
                                                              (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")]
    chat_focus(None)  # 案内の文が出ていれば消す
    for f in files:
        add_attachment(f)
    if not files:  # 文字の貼り付け (選んでいる部分があれば置き換える)
        try:
            text = root.clipboard_get()
        except tk.TclError:
            return "break"
        if chat_entry.tag_ranges("sel"):
            chat_entry.delete("sel.first", "sel.last")
        chat_entry.insert("insert", text)
        fit_input()
    return "break"


def show_sent_images(images):
    """送った画像の見本を、会話の欄のメッセージの下に出す"""
    for p in images:
        ui(lambda p=p: (chat_images.append(_thumb(p, 160)), chat_append("image", chat_images[-1])))


tk.Button(input_box, text="📎", command=choose_images, font=("Segoe UI Emoji", 12), bg=INPUT_BG, fg=TEXT,
          activebackground=CYAN_DIM, relief="flat", bd=0, cursor="hand2", padx=8).pack(side="left", fill="y",
                                                                                      before=chat_entry)
chat_entry.bind("<Control-v>", paste_image)
chat_entry.bind("<Control-V>", paste_image)
chat_entry.bind("<Shift-Insert>", paste_image)


# ---------- コピー・貼り付け (会話の欄と入力欄) ----------
def copy_selection(widget):
    try:
        text = widget.get("sel.first", "sel.last")
    except tk.TclError:
        return "break"  # 選んでいない
    root.clipboard_clear()
    root.clipboard_append(text)
    return "break"


def select_all(widget):
    widget.tag_add("sel", "1.0", "end-1c")
    return "break"


def cut_selection():
    if chat_entry.tag_ranges("sel"):
        copy_selection(chat_entry)
        chat_entry.delete("sel.first", "sel.last")
        fit_input()


# 会話の欄: 読み取り専用でも、クリックすればキーボードでコピーできるようにする
chat_view.bind("<Button-1>", lambda _: chat_view.focus_set(), add="+")
for key in ("<Control-c>", "<Control-C>", "<Control-Insert>"):
    chat_view.bind(key, lambda _: copy_selection(chat_view))
    chat_entry.bind(key, lambda _: copy_selection(chat_entry))
chat_view.bind("<Control-a>", lambda _: select_all(chat_view))
chat_entry.bind("<Control-a>", lambda _: select_all(chat_entry))
chat_entry.bind("<Control-x>", lambda _: (cut_selection(), "break")[1])

# 右クリックのメニュー
view_menu = tk.Menu(root, tearoff=False)
view_menu.add_command(label="コピー", command=lambda: copy_selection(chat_view))
view_menu.add_command(label="すべて選択", command=lambda: select_all(chat_view))
entry_menu = tk.Menu(root, tearoff=False)
entry_menu.add_command(label="切り取り", command=cut_selection)
entry_menu.add_command(label="コピー", command=lambda: copy_selection(chat_entry))
entry_menu.add_command(label="貼り付け", command=paste_image)
entry_menu.add_separator()
entry_menu.add_command(label="すべて選択", command=lambda: select_all(chat_entry))
chat_view.bind("<Button-3>", lambda e: (chat_view.focus_set(), view_menu.tk_popup(e.x_root, e.y_root)))
chat_entry.bind("<Button-3>", lambda e: (chat_entry.focus_set(), entry_menu.tk_popup(e.x_root, e.y_root)))
chat_entry.bind("<Return>", send_chat)
chat_entry.bind("<Shift-Return>", new_line)
chat_entry.bind("<KeyRelease>", fit_input)
chat_entry.bind("<FocusIn>", chat_focus)
chat_entry.bind("<FocusOut>", chat_blur)
convo_window = canvas.create_window(20, 20, window=convo, anchor="nw")

# 会話の列の右端のつまみ: ドラッグで横幅を変える (ダブルクリックで最初の幅に戻す)
convo_width = [load_settings().get("convo_width")]  # None なら画面の幅に合わせた最初の幅
sash = tk.Frame(root, width=6, bg=CYAN_DIM, cursor="sb_h_double_arrow")
sash_window = canvas.create_window(0, 20, window=sash, anchor="nw")


def default_convo_width(w):
    return min(620, max(340, int(w * 0.34)))


def drag_sash(event):
    w = canvas.winfo_width()
    x = event.x_root - canvas.winfo_rootx() - 20
    convo_width[0] = max(280, min(w - 420, x))  # 顔が隠れすぎない範囲で
    layout()


def save_sash(_=None):
    save_settings(convo_width=convo_width[0])


def reset_sash(_=None):
    convo_width[0] = None
    save_settings(convo_width=None)
    layout()


sash.bind("<B1-Motion>", drag_sash)
sash.bind("<ButtonRelease-1>", save_sash)
sash.bind("<Double-Button-1>", reset_sash)
sash.bind("<Enter>", lambda _: sash.configure(bg=CYAN))
sash.bind("<Leave>", lambda _: sash.configure(bg=CYAN_DIM))


def layout(event=None):
    """ウィンドウの大きさに合わせて、背景・会話の列・文字の位置を決め直す"""
    w, h = canvas.winfo_width(), canvas.winfo_height()
    space.resize(w, h)
    convo_w = convo_width[0] or default_convo_width(w)
    convo_w = max(280, min(max(280, w - 420), convo_w))  # ウィンドウを小さくしたときも収まるように
    canvas.itemconfigure(convo_window, width=convo_w, height=max(200, h - 40))
    canvas.coords(sash_window, 20 + convo_w, 20)
    canvas.itemconfigure(sash_window, height=max(200, h - 40))
    canvas.coords(space.label, convo_w + 40, 14)  # 場所と天気の表示は会話の列の右へ
    space.set_face_area(20 + convo_w + 6, w)  # 顔は会話の列の右側の、残りの場所の真ん中に
    status_text[1](space.face_center[0], h - 46)  # 状態は顔の下に1行だけ (会話は左の欄に出る)
    heard_text[1](-2000, -2000)
    reply_text[1](-2000, -2000)
    canvas.tag_raise("overlay")


canvas.bind("<Configure>", layout)
restore_state()  # 再起動前の会話があれば復元する (この後で自動保存を始める。順序が逆だと上書きしてしまう)
threading.Thread(target=autosave_restart_state, daemon=True).start()  # 強制終了でも会話が残るよう定期保存
show_info("話しかけるか、下の欄に入力してください。\n"
          "例: 「今日の天気は？」「ニュースを読んで」「XSS の対策をまとめて」\n"
          "「memory_store.py を分析して」のような長い作業も頼めます (作業モード)。\n"
          "F11 で全画面 / Esc で戻す")

wake_mode = tk.BooleanVar(value=load_settings().get("always_listen", True))
tk.Checkbutton(panel, text="常に聞く", variable=wake_mode, command=toggle_wake,
               font=("Meiryo", 10), bg="#18201c", fg=CYAN, selectcolor=PANEL,
               activebackground="#18201c", activeforeground=CYAN).pack(anchor="w", padx=12, pady=(8, 2))

phone_mode = tk.BooleanVar(value=load_settings().get("phone_enabled", False))
tk.Checkbutton(panel, text="スマホから使う", variable=phone_mode, command=toggle_phone,
               font=("Meiryo", 10), bg="#18201c", fg=CYAN, selectcolor=PANEL,
               activebackground="#18201c", activeforeground=CYAN).pack(anchor="w", padx=12, pady=(0, 6))

# 声と速さの選択
offline_voice_options, _ = voice_options(japanese_voices())
voice_names = list(NEURAL_VOICES) + list(offline_voice_options)
if voice_settings["voice"] not in voice_names:
    voice_settings["voice"] = DEFAULT_VOICE
voice_var = tk.StringVar(value=voice_settings["voice"])
rate_var = tk.IntVar(value=voice_settings["rate"])


def gender_of(voice_name):
    """声の名前から顔 (女性/男性) を決める"""
    return "male" if voice_name.startswith(("Keita", "Ichiro")) else "female"


def on_voice_change(*_):
    voice_settings["voice"], voice_settings["rate"] = voice_var.get(), rate_var.get()
    save_settings(**voice_settings)
    space.set_gender(gender_of(voice_settings["voice"]))  # 声に合わせて顔も変える
    run_task(speak, "この声でお話しします。")


def open_voice_download_settings():
    """Windows の音声設定を開き、追加音声パックをダウンロードできるようにする。"""
    try:
        os.startfile("ms-settings:speech")
    except OSError as e:
        show_info(f"Windowsの音声設定を開けませんでした: {e}")


def refresh_voice_list():
    """Windowsに追加された音声を選択欄へ再読み込みする。"""
    global voice_names
    offline_voice_options, _ = voice_options(japanese_voices())
    voice_names = list(NEURAL_VOICES) + list(offline_voice_options)
    voice_box.configure(values=voice_names)
    if voice_var.get() not in voice_names:
        voice_var.set(DEFAULT_VOICE)
    ui(status.set, "Windowsの音声一覧を更新しました")


space.set_gender(gender_of(voice_settings["voice"]))  # 起動時の顔


tk.Label(panel, text="声", font=("Meiryo", 9), bg="#18201c", fg=CYAN).pack(anchor="w", padx=14)
voice_box = ttk.Combobox(panel, textvariable=voice_var, values=voice_names, state="readonly",
                         width=28, font=("Meiryo", 9))
voice_box.pack(anchor="w", padx=14)
voice_box.bind("<<ComboboxSelected>>", on_voice_change)
ttk.Button(panel, text="Windowsから音声を追加・ダウンロード", command=open_voice_download_settings).pack(
    anchor="w", padx=12, pady=(4, 2))
ttk.Button(panel, text="インストール済み音声を再読み込み", command=refresh_voice_list).pack(
    anchor="w", padx=12, pady=(0, 4))
rate_scale = tk.Scale(panel, from_=-5, to=5, orient="horizontal", variable=rate_var, label="速さ",
                      showvalue=False, length=150, font=("Meiryo", 8), bg=PANEL, fg=CYAN,
                      troughcolor=CYAN_DIM, highlightthickness=0, bd=0)
rate_scale.configure(bg="#18201c")
rate_scale.pack(anchor="w", padx=12, pady=(2, 10))
rate_scale.bind("<ButtonRelease-1>", on_voice_change)  # つまみを離したら反映して試しに話す

space.run(root)
# 起動したら「何かありますか？」と聞き、「常に聞く」ならそのまま聞き続ける
root.after(800, lambda: toggle_wake() if wake_mode.get() else run_task(converse))
# 「外出先からの操作を許可」が入っていれば、起動時からリモート待ち受けを始める
if phone_mode.get():
    root.after(500, lambda: (phone_mode.set(True), toggle_phone()))
root.mainloop()

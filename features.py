"""JARVIS の便利機能 (天気・ニュース・Web検索・予定・サイト・PC操作)

各関数は「読み上げる文」と「画面に表示する文」のタプルを返す。
"""
import contextlib
import difflib
import html
import json
import os
import random
import re
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path

import feedparser
import requests
from ddgs import DDGS

DEFAULT_CITY = "大阪"
NEWS_RSS = "https://news.yahoo.co.jp/rss/topics/{}.xml"
NEWS_CATEGORIES = {
    "国内": "domestic",
    "世界": "world", "海外": "world", "国際": "world",
    "経済": "business",
    "エンタメ": "entertainment", "芸能": "entertainment",
    "スポーツ": "sports",
    "IT": "it", "科学": "it",
}
SCHEDULE_FILE = Path(__file__).with_name("schedule.json")

SITES = {
    "google": ("Google", "https://www.google.com/"),
    "グーグル": ("Google", "https://www.google.com/"),
    "youtube": ("YouTube", "https://www.youtube.com/"),
    "ユーチューブ": ("YouTube", "https://www.youtube.com/"),
    "yahoo": ("Yahoo! JAPAN", "https://www.yahoo.co.jp/"),
    "ヤフー": ("Yahoo! JAPAN", "https://www.yahoo.co.jp/"),
    "amazon": ("Amazon", "https://www.amazon.co.jp/"),
    "アマゾン": ("Amazon", "https://www.amazon.co.jp/"),
    # TryHackMe: 今取り組んでいるルーム (2026-10-06 に依頼)
    "tryhackme": ("TryHackMe", "https://tryhackme.com/room/basicpentestingjt"),
    "トライハックミー": ("TryHackMe", "https://tryhackme.com/room/basicpentestingjt"),
}


# ① 天気情報を取得する
_location_cache = {"time": 0, "value": None}


def current_location():
    """今いる場所 (地名, '緯度,経度') を返す。インターネットの接続元から調べるので
    市区町村くらいの精度。1時間は覚えておく。わからなければ (DEFAULT_CITY, DEFAULT_CITY)"""
    if time.time() - _location_cache["time"] < 3600 and _location_cache["value"]:
        return _location_cache["value"]
    try:
        loc = requests.get("https://ipinfo.io/json", timeout=10).json()["loc"]
        lat, lon = loc.split(",")
        addr = requests.get("https://nominatim.openstreetmap.org/reverse",
                            params={"lat": lat, "lon": lon, "format": "json",
                                    "accept-language": "ja", "zoom": 10},
                            headers={"User-Agent": "JARVIS-personal-assistant"}, timeout=10).json()["address"]
        name = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or loc
        value = (name, loc)
    except Exception:
        value = (DEFAULT_CITY, DEFAULT_CITY)
    _location_cache.update(time=time.time(), value=value)
    return value


def weather(text=""):
    m = re.search(r"([^\s、。]+?)の(?:今日の|明日の)?天気", text)
    if m and m.group(1) not in ("今日", "明日", "今", "ここ", "今いる場所", "現在地"):
        city = query = m.group(1)
    else:  # 場所の指定がなければ今いる場所
        city, query = current_location()
    data = requests.get(f"https://wttr.in/{query}?format=j1&lang=ja", timeout=10).json()
    now = data["current_condition"][0]
    today = data["weather"][0]
    desc = now.get("lang_ja", [{}])[0].get("value") or now["weatherDesc"][0]["value"]
    rain = max(int(h["chanceofrain"]) for h in today["hourly"])
    say = (f"{city}の今日の天気は{desc}です。最高気温は{today['maxtempC']}度、"
           f"最低気温は{today['mintempC']}度、降水確率は{rain}パーセントです。")
    show = (f"■ {city}の天気\n{desc}  現在 {now['temp_C']}℃\n"
            f"最高 {today['maxtempC']}℃ / 最低 {today['mintempC']}℃\n降水確率 {rain}%")
    return say, show


# ② ニュースを読み上げる
def news(text="", count=3):
    label, feed = "最新", "top-picks"
    for word, name in NEWS_CATEGORIES.items():
        if word.lower() in text.lower():
            label, feed = word, name
            break
    entries = feedparser.parse(NEWS_RSS.format(feed)).entries[:count]
    if not entries:
        return "ニュースを取得できませんでした。", "ニュースを取得できませんでした"
    titles = [e.title for e in entries]
    say = f"{label}のニュースです。" + "。".join(titles) + "。"
    show = f"■ {label}のニュース\n" + "\n".join(f"・{t}" for t in titles)
    return say, show


# ③ Web検索を行う
def search(query):
    results = DDGS().text(query, region="jp-jp", max_results=3)
    if not results:
        return f"{query}の検索結果が見つかりませんでした。", "検索結果なし"
    top = results[0]
    summary = top["body"].split("。")[0]
    say = f"{query}について。{summary}。"
    show = f"■ 検索: {query}\n" + "\n".join(f"・{r['title']}" for r in results)
    return say, show


def search_query(text):
    """「〇〇を検索して」「〇〇について調べて」から〇〇を取り出す"""
    q = re.sub(r"(について)?(を)?(検索|調べ)(して|て)?(ください)?", "", text)
    return q.strip(" 、。")


# ④ スケジュール・予定を管理
def load_schedule():
    try:
        return json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def schedule():
    items = load_schedule()
    if not items:
        return "今日の予定はありません。", "■ 今日の予定\nなし"
    say = "今日の予定です。" + "。".join(f"{i['time']}、{i['task']}" for i in items) + "。"
    show = "■ 今日の予定\n" + "\n".join(f"{i['time']}  {i['task']}" for i in items)
    return say, show


def add_schedule(text):
    """「15時に会議を予定に追加して」「9時半に散歩を登録して」などから予定を登録する"""
    m = re.search(r"(午後)?(\d{1,2})(?:時|:)(半|(\d{1,2})分?)?", text)
    if not m:
        return None
    hour = int(m.group(2)) + (12 if m.group(1) and int(m.group(2)) < 12 else 0)
    minute = 30 if m.group(3) == "半" else int(m.group(4) or 0)
    time = f"{hour:02d}:{minute:02d}"
    task = text[m.end():]
    task = re.sub(r"^(から|に|より)", "", task)
    task = re.sub(r"(を|って)?(予定|スケジュール)?(に|を)?(追加|登録|入れ)(して|て|る)?(ください|おいて)?", "", task)
    task = task.strip(" 、。") or "予定"
    items = load_schedule()
    items.append({"time": time, "task": task})
    items.sort(key=lambda i: i["time"])
    SCHEDULE_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"{time}に{task}を登録しました。", f"■ 予定を登録\n{time}  {task}"


# ⑤ Webサイトを開く
def open_site(text):
    lower = text.lower().replace(" ", "").replace("　", "")  # 「トライ ハック ミー」のような空白も無視
    for key, (name, url) in SITES.items():
        if key in lower:
            webbrowser.open(url)
            return f"{name}を開きます。", f"■ サイトを開く\n{name}"
    name = re.sub(r"(の)?(サイト|ページ)?を?開いて(ください)?", "", text).strip(" 、。")
    webbrowser.open(f"https://www.google.com/search?q={requests.utils.quote(name)}")
    return f"{name}を検索して開きます。", f"■ サイトを開く\n{name} (Google検索)"


def open_url(text):
    """指定した URL をブラウザで開く。ログインが必要で thm-page などでは取れないページを、
    自分で開いて read-screen / scroll-screen で読み取りたいときに使う。http/https の URL だけを開く"""
    m = re.search(r"https?://\S+", text)
    if not m:
        return "開けるURLが見つかりませんでした。", "■ ページを開く\nhttp/https のURLを指定してください"
    url = m.group(0).rstrip("、。）) \"'")
    webbrowser.open(url)
    time.sleep(2.5)  # ブラウザが読み込む時間を少し待つ (直後の read-screen が空にならないように)
    return "ページを開きました。", f"■ ページを開く\n{url}"


# 音楽の再生 (YouTube)
MUSIC_FILE = Path(__file__).with_name("music.json")
DEFAULT_MUSIC = "作業用BGM"  # music.json が空のとき
RANDOM_WORDS = ("", "音楽", "曲", "歌", "BGM", "おまかせ", "お任せ",
                "何でもいい", "なんでもいい", "何でも", "なんでも", "特にない", "わからない")
MOOD_QUESTION = "今日はどんな気分ですか？聴きたいジャンルやアーティストでも大丈夫です。"
last_random = None


def load_moods():
    try:
        return json.loads(MUSIC_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def pick(songs):
    """リストからランダムに選ぶ (直前と同じ曲は避ける)"""
    global last_random
    if not songs:
        return DEFAULT_MUSIC
    last_random = random.choice([s for s in songs if s != last_random] or songs)
    return last_random


def music_query(text):
    """「米津玄師のLemonを流して」から曲名を取り出す。曲名の指定がなければ None"""
    q = re.sub(r"(youtube|ユーチューブ)\s*(で|の)?", "", text, flags=re.I)
    q = re.sub(r"(の曲|の音楽|の歌)?(を)?(流して|かけて|再生して|聴かせて|聞かせて|検索して|探して)(ください)?", "", q)
    q = q.strip(" 、。")
    q = re.sub(r"^(何か|なんか|適当に|ランダムに?)(で|の)?", "", q).strip(" 、。")
    return None if q in RANDOM_WORDS else q


def mood_query(answer):
    """気分の答えから曲を選ぶ。(検索する曲名, 前置きのひとこと) を返す"""
    moods = load_moods()
    answer = (answer or "").strip(" 、。")
    for m in moods:
        if any(k in answer for k in m["keywords"]):
            return pick(m["songs"]), m.get("comment", "気分に合う曲を選びますね。")
    cleaned = re.sub(r"[なねよ!！]+$", "", answer)
    cleaned = re.sub(r"(な|の)?(曲|音楽|歌|気分|感じ)?(が|を)?(いい|良い|聴きたい|聞きたい|かな|です|で)+$", "", cleaned).strip(" 、。")
    if cleaned in RANDOM_WORDS:
        return pick([s for m in moods for s in m["songs"]]), "おまかせで選びますね。"
    return f"{cleaned} 曲", f"{cleaned}で探してみますね。"


def play_music(query):
    res = requests.get("https://www.youtube.com/results",
                       params={"search_query": query, "hl": "ja"},
                       headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja"}, timeout=10)
    html = res.content.decode("utf-8", errors="replace")
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"', html)
    if not m:
        webbrowser.open(f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}")
        return f"{query}の検索結果を開きます。", f"■ 音楽\nYouTubeで「{query}」を検索"
    video_id, title = m.group(1), json.loads(f'"{m.group(2)}"')
    webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")
    spoken = speakable_title(title)
    if len(spoken) > 30:  # BGM などの長いタイトルは読み上げない
        spoken = re.sub(r" 曲$", "", query) + "の動画"
    return f"{spoken}を再生します。", f"■ 再生中\n{title}"


def speakable_title(title):
    """読み上げ用に【OFFICIAL MUSIC VIDEO】などの飾りや絵文字を取り除く"""
    t = re.sub(r"[【\[［(（].*?[】\]］)）]", " ", title)
    t = re.sub(r"official\s*(music\s*)?(video|audio|mv)|music\s*video|\bmv\b|\blyrics?\b", " ", t, flags=re.I)
    t = re.sub(r"[^\w\s「」『』&'・ー-]", " ", t)  # 絵文字や記号
    t = re.sub(r"\s*-\s*", " ", t)
    return re.sub(r"\s+", " ", t).strip() or title


def media_key(text):
    """再生中の音楽をメディアキーで操作する (YouTubeを開いたブラウザにも効く)"""
    import ctypes
    keys = [(("次の曲", "スキップ"), 0xB0, "次の曲にします。"),
            (("前の曲",), 0xB1, "前の曲にします。"),
            (("止めて", "停止", "ストップ", "再開", "続き"), 0xB3, "わかりました。")]
    for words, vk, say in keys:
        if any(w in text for w in words):
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # キーを離す
            return say, "■ 音楽\n" + say
    return None


# ⑥ PCの基本操作を行う
def pc_action(text):
    if "メモ帳" in text:
        subprocess.Popen(["notepad.exe"])
        return "メモ帳を開きます。", "■ PC操作\nメモ帳を起動"
    if "電卓" in text:
        subprocess.Popen(["calc.exe"])
        return "電卓を開きます。", "■ PC操作\n電卓を起動"
    if "フォルダ" in text or "デスクトップ" in text:
        os.startfile(Path.home() / "Desktop")
        return "デスクトップのフォルダを開きます。", "■ PC操作\nデスクトップを開く"
    if "powershell" in text.lower() or "パワーシェル" in text:
        import shutil
        exe = shutil.which("pwsh.exe") or shutil.which("pwsh") or "powershell.exe"  # PowerShell 7があれば優先、無ければ従来のPowerShellにフォールバック
        try:
            subprocess.Popen(["cmd", "/c", "start", "", exe], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            return "PowerShellを開けませんでした。", f"■ PC操作\nPowerShellの起動に失敗: {e}"
        return "PowerShellを開きます。", "■ PC操作\nPowerShellを起動"
    return None


# ターミナルの種類ごとの、起動コマンドと、開いた窓を前面に出すためのタイトルの手がかり
TERMINALS = {
    "powershell": (["cmd", "/c", "start", "", "powershell.exe"], ("PowerShell",)),
    "ターミナル":  (["cmd", "/c", "start", "", "wt.exe"], ("ターミナル", "Terminal", "PowerShell")),
    "cmd":        (["cmd", "/c", "start", "", "cmd.exe"], ("コマンド", "Command", "cmd")),
    "kali":       (["cmd", "/c", "start", "", "wsl.exe", "-d", "kali-linux"], ("Kali", "kali", "wsl")),
    "wsl":        (["cmd", "/c", "start", "", "wsl.exe"], ("wsl", "Ubuntu", "Linux")),
}


def open_terminal(text=""):
    """ターミナルを開く。PowerShell / Windowsターミナル / コマンドプロンプト(cmd) / Kali(WSL) を、
    言葉で選んで開く (既定は PowerShell)。開いたら前面に出して最大化するので、そのまま read-screen や
    type-text --scan でコマンドを打てる。開く専用の機能が無くて止まっていた作業を、自分で進めるために使う"""
    t = text.lower()
    if any(w in t for w in ("kali", "カリ", "ケール", "ケイリ")):
        key = "kali"
    elif "wsl" in t or "ubuntu" in t or "リナックス" in t or "linux" in t:
        key = "wsl"
    elif "cmd" in t or "コマンドプロンプト" in t or "コマンド プロンプト" in t:
        key = "cmd"
    elif "windows terminal" in t or "ターミナル" in t or "terminal" in t:
        key = "ターミナル"
    else:
        key = "powershell"  # 「パワーシェル」含む既定
    cmd, title_hints = TERMINALS[key]
    before = _visible_top_windows()
    try:
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return (f"{key} を開けませんでした。入っていないかもしれません。",
                f"■ PC操作\n{key} の起動に失敗: {e}")
    time.sleep(1.5)  # 窓が出るまで待つ
    hwnd = None
    for hint in title_hints:  # タイトルで新しく出た窓を探して前面に出す
        hwnd = _focus_window_by_title(hint)
        if hwnd:
            break
    if not hwnd:  # タイトルで見つからなければ、起動後に新しく現れた窓を前面に出す
        appeared = _visible_top_windows() - before
        if appeared:
            hwnd = next(iter(appeared))
            _force_foreground(hwnd)
    if hwnd:
        _maximize(hwnd)
    label = {"powershell": "PowerShell", "ターミナル": "Windowsターミナル", "cmd": "コマンドプロンプト",
             "kali": "Kali", "wsl": "WSL"}[key]
    say = f"{label}を開いて前面に出しました。" if hwnd else f"{label}を起動しました。"
    return say, f"■ PC操作\n{say}"


# ---------- VPN接続 と Kali(WSL) ターミナル ----------
# ブラウザ版 AttackBox の埋め込み端末が反応しないときの代替手段。
# TryHackMe などの VPN に Kali(WSL) の openvpn でつなげば、ターゲットに直接届くので、
# あとは Kali のターミナル (ssh / nmap / hydra など) で操作できる。
# WSL は `-u root` でパスワード無しに root になれるため、root が要る openvpn を自動で起動できる。
KALI_DISTRO = "kali-linux"
KALI_TIMEOUT = 300  # kali コマンド1回の制限時間 (秒)。1ターンの中で収まるよう長すぎない値にする
_VPN_LOG = "/tmp/jarvis/vpn.log"
_VPN_PID = "/tmp/jarvis/vpn.pid"


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_kali(bash_cmd, root=False, timeout=60):
    """Kali(WSL) の中で bash コマンドを実行し、CompletedProcess を返す。
    root=True で (パスワード無しの) root 権限で動かす (openvpn など root が要るものに使う)。
    スクリプトは argv ではなく stdin から bash に渡す。wsl.exe へ引数で渡すと途中で引用符や
    $ が壊れる (awk の $4 が空になる等) ため、stdin 経由にして元の文字をそのまま解釈させる"""
    cmd = ["wsl.exe", "-d", KALI_DISTRO]
    if root:
        cmd += ["-u", "root"]
    cmd += ["--", "bash", "-l"]
    # stdin はバイトで渡す。テキストモードだと Windows では改行が \n→\r\n に変換され、
    # ヒアドキュメントで書いたスクリプトに \r が紛れて openvpn が「--verb 3\r」を弾いてしまう
    r = subprocess.run(cmd, input=bash_cmd.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout, creationflags=_no_window())
    r.stdout = r.stdout.decode("utf-8", "replace")
    r.stderr = r.stderr.decode("utf-8", "replace")
    return r


def _wsl_path(win_path):
    """Windows のパス (C:\\Users\\...) を WSL の /mnt/c/Users/... 形式に変換する。失敗したら空文字。
    wslpath に Windows パスを渡すと、引数渡しの途中でバックスラッシュが消えてしまうため、
    ここでは Python 側でドライブ文字を使って自前に組み立てる (スペースを含むパスもそのまま通せる)"""
    p = Path(win_path)
    drive = p.drive.rstrip(":").lower()  # 'C:' → 'c'
    if not drive:
        return ""
    return "/mnt/" + drive + "/" + "/".join(p.parts[1:])


def _tun_ip():
    """Kali(WSL) の VPN トンネル (tun0) に割り当てられた IPv4 を返す。つながっていなければ None。
    抽出は (シェルの $ を避けて) Python 側の正規表現で行う"""
    try:
        r = _run_kali("ip -4 -o addr show tun0 2>/dev/null", root=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", r.stdout)
    return m.group(1) if m else None


def _find_thm_ovpn():
    """TryHackMe の .ovpn 設定ファイルを探す (Downloads/デスクトップ/プロジェクト)。
    中身に tryhackme を含む最新のものを優先して返す。見つからなければ None"""
    candidates = []
    for base in (Path.home() / "Downloads", Path.home() / "Desktop", PROJECT_DIR):
        if base.exists():
            candidates += list(base.glob("*.ovpn"))
    for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "tryhackme" in text or ".thm" in text:  # THM の設定だと確認できたものだけ
            return p
    return None


def vpn_connect(ovpn=""):
    """Kali(WSL) の openvpn で VPN につなぐ。ovpn に設定ファイルのパスを渡せる。
    省略したときは TryHackMe の .ovpn を自動で探す。接続成否は openvpn のログで確かめる"""
    import shlex
    ovpn = ovpn.strip().strip('"').strip("'")
    if ovpn:
        win = Path(ovpn).expanduser()
        if not win.exists():
            return ("その設定ファイルが見つかりませんでした。", f"■ VPN\n設定ファイルが見つかりません: {win}")
    else:
        win = _find_thm_ovpn()
        if not win:
            return ("トライハックミーの接続設定ファイルが見つかりませんでした。"
                    "トライハックミーのアクセスのページから、オープンVPNの設定ファイルをダウンロードして、もう一度試してください。",
                    "■ VPN\nTryHackMe の .ovpn 設定ファイルが見つかりません\n"
                    "tryhackme.com/access から OpenVPN 設定をダウンロードして保存してください")
    already = _tun_ip()  # すでにつながっていれば二重接続しない
    if already:
        return (f"すでにVPNにつながっています。アドレスは {already} です。", f"■ VPN\n接続済み tun0: {already}")
    lin = _wsl_path(win)
    if not lin:
        return ("設定ファイルのパスを変換できませんでした。", f"■ VPN\nwslpath に失敗: {win}")
    _log_automation(f"VPN接続開始 {win.name}")
    # openvpn の起動スクリプトを /tmp に書き出しておく (前回のログ/PIDも消す)。
    # 設定ファイルのパスにスペースや括弧があってもよいよう、パスは shlex.quote で囲む。
    # このスクリプト書き込みは stdin 経由なので引用符がそのまま保たれる。
    script = "/tmp/jarvis/vpn_start.sh"
    setup = ("mkdir -p /tmp/jarvis\n"
             "rm -f %s %s\n"
             "cat > %s <<'JARVIS_EOF'\n"
             "#!/bin/bash\n"
             "exec openvpn --config %s --log %s --writepid %s --verb 3\n"
             "JARVIS_EOF\n"
             "chmod +x %s\n") % (_VPN_LOG, _VPN_PID, script, shlex.quote(lin), _VPN_LOG, _VPN_PID, script)
    try:
        _run_kali(setup, root=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ("VPNの準備に失敗しました。", f"■ VPN\n準備エラー: {e}")
    # スクリプトを root でフォアグラウンド実行し、その wsl.exe を Windows 側からデタッチして生かしておく。
    # こうしないと、bash の & で起動した直後に wsl セッションの終了で openvpn ごと片付けられてしまう。
    # 起動は固定パスのスクリプト名だけを渡すので、引数経由の引用符崩れも起きない。
    DETACHED = 0x00000008  # DETACHED_PROCESS。openvpn を生かしたままこの関数は先へ進む
    try:
        subprocess.Popen(["wsl.exe", "-d", KALI_DISTRO, "-u", "root", "--", "bash", script],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_no_window() | DETACHED)
    except OSError as e:
        return ("VPNの起動に失敗しました。", f"■ VPN\n起動エラー: {e}")
    # ログに「Initialization Sequence Completed」が出るまで待つ (最大60秒)
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(2)
        try:
            log = _run_kali(f"cat {_VPN_LOG} 2>/dev/null", root=True, timeout=15).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "Initialization Sequence Completed" in log:
            ip = _tun_ip() or "(取得できませんでした)"
            return (f"VPNにつながりました。割り当てられたアドレスは {ip} です。このあとはカリのターミナルで操作できます。",
                    f"■ VPN\n接続成功 tun0: {ip}\n設定: {win.name}")
        if "AUTH_FAILED" in log or "auth-failure" in log.lower():
            return ("認証に失敗しました。設定ファイルが新しいものか確認してください。", "■ VPN\n認証失敗 (AUTH_FAILED)")
    return ("VPNの接続確認が時間内に取れませんでした。少し待ってから状態を確認してください。",
            f"■ VPN\n接続確認が時間切れ (ログ: {_VPN_LOG})")


def vpn_status():
    """VPN (Kali の tun0) につながっているかを調べる"""
    ip = _tun_ip()
    if ip:
        return (f"VPNにつながっています。アドレスは {ip} です。", f"■ VPN\n接続中 tun0: {ip}")
    return ("今はVPNにつながっていません。", "■ VPN\n未接続")


def vpn_disconnect():
    """Kali の openvpn を止めて VPN を切断する"""
    stop = (f"[ -f {_VPN_PID} ] && kill $(cat {_VPN_PID}) 2>/dev/null; "
            "pkill -f 'openvpn --config' 2>/dev/null; true")
    try:
        _run_kali(stop, root=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ("VPNの切断に失敗しました。", f"■ VPN\n切断エラー: {e}")
    _log_automation("VPN切断")
    return ("VPNを切断しました。", "■ VPN\n切断しました")


def vpn(text=""):
    """VPN の接続・状態確認・切断をまとめて受け付ける。
    connect [設定ファイル] / status / disconnect (日本語の 接続/状態/切断 も可)"""
    parts = text.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "connect"
    arg = parts[1] if len(parts) > 1 else ""
    if sub in ("status", "state", "状態", "確認"):
        return vpn_status()
    if sub in ("disconnect", "down", "stop", "切断", "切って", "止めて"):
        return vpn_disconnect()
    if sub in ("connect", "接続", "つないで", "つなげて"):
        return vpn_connect(arg)
    return vpn_connect(text.strip())  # サブコマンドではなくパスが直接来たときは接続とみなす


def kali(text=""):
    """Kali(WSL) のターミナルの中でコマンドを1つ実行し、その結果 (標準出力・エラー) を返す。
    ブラウザの埋め込み端末が使えないとき、VPN接続後の ssh / nmap / hydra などの操作に使う。
    パスワードを途中で聞いてくるような対話が必要なコマンドには向かない"""
    cmd = text.strip()
    if not cmd:
        return ("実行するコマンドを教えてください。", "■ Kali\nコマンドが空です")
    _log_automation(f"Kali 実行: {cmd}")
    try:
        r = _run_kali(cmd, timeout=KALI_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ("コマンドが時間内に終わりませんでした。", f"■ Kali\n時間切れ: {cmd}")
    except OSError as e:
        return ("Kaliを実行できませんでした。", f"■ Kali\n実行エラー: {e}")
    out = (r.stdout or "").rstrip()
    if r.stderr and r.stderr.strip():
        out += ("\n" if out else "") + r.stderr.rstrip()
    out = out.strip() or "(出力なし)"
    if len(out) > 3000:  # 画面表示が長くなりすぎないよう頭を残して省く
        out = out[:3000] + "\n…(以下省略)"
    return "コマンドを実行しました。結果を画面に出します。", f"■ Kali: {cmd}\n\n{out}"


# PCの設定 (音量・設定画面)
SETTINGS_PAGES = {
    "設定": "ms-settings:",
    "ディスプレイ": "ms-settings:display",
    "サウンド": "ms-settings:sound",
    "wifi": "ms-settings:network-wifi",
    "bluetooth": "ms-settings:bluetooth",
    "電源": "ms-settings:powersleep",
    "通知": "ms-settings:notifications",
    "壁紙": "ms-settings:personalization-background",
    "windows update": "ms-settings:windowsupdate",
}
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF


def _press(vk, times=1):
    import ctypes
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def volume(action):
    """音量を操作する。action: 上げる/下げる/ミュート/0〜100の数字"""
    if action.isdigit():
        level = max(0, min(100, int(action)))
        _press(VK_VOLUME_DOWN, 50)        # 1回で2%ずつ動くので、一度0にしてから
        _press(VK_VOLUME_UP, level // 2)  # 指定の音量まで上げる
        say = f"音量を{level}パーセントにしました。"
    elif "上" in action:
        _press(VK_VOLUME_UP, 5)
        say = "音量を上げました。"
    elif "下" in action:
        _press(VK_VOLUME_DOWN, 5)
        say = "音量を下げました。"
    elif "ミュート" in action or "消" in action:
        _press(VK_VOLUME_MUTE)
        say = "ミュートを切り替えました。"
    else:
        return None
    return say, "■ 音量\n" + say


def open_settings(page):
    """Windows の設定画面を開く"""
    key = next((k for k in SETTINGS_PAGES if k in page.lower()), "設定")
    os.startfile(SETTINGS_PAGES[key])
    name = "" if key == "設定" else f"{key}の"
    return f"{name}設定画面を開きます。", f"■ 設定\n{key}"


def shutdown():
    # 60秒後にシャットダウン (取り消しは shutdown /a)
    subprocess.run(["shutdown", "/s", "/t", "60"])
    return ("1分後にシャットダウンします。取り消すときは「シャットダウンを取り消して」と言ってください。",
            "■ PC操作\n60秒後にシャットダウン")


def cancel_shutdown():
    subprocess.run(["shutdown", "/a"])
    return "シャットダウンを取り消しました。", "■ PC操作\nシャットダウン取り消し"


# ⑥-1 システムログ (Windowsイベントログ) を調べる (急な再起動・異常終了の原因調査用)
REBOOT_EVENT_IDS = (41, 1074, 6008)  # 41=予期しない再起動, 6008=異常シャットダウン, 1074=正常なシャットダウン/再起動


def get_system_log(hours=24, level="error"):
    """Windowsのイベントログ (システム・アプリケーション) から、直近N時間のエラー/重大イベントと、
    再起動の原因調査に役立つイベント (41=予期しない再起動, 6008=異常シャットダウン, 1074=正常な
    シャットダウン/再起動) を取得する。level="all" にすると警告レベルも含める"""
    levels = "1,2,3" if level == "all" else "1,2"
    ids = ",".join(str(i) for i in REBOOT_EVENT_IDS)
    ps = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
        f"$h1 = @{{LogName='System','Application'; Level={levels}; StartTime=(Get-Date).AddHours(-{hours})}};"
        f"$h2 = @{{LogName='System'; Id={ids}; StartTime=(Get-Date).AddHours(-{hours})}};"
        "$events = @();"
        "try { $events += @(Get-WinEvent -FilterHashtable $h1 -ErrorAction Stop) } catch {};"
        "try { $events += @(Get-WinEvent -FilterHashtable $h2 -ErrorAction Stop) } catch {};"
        "$events | Sort-Object TimeCreated -Descending -Unique | Select-Object -First 30 "
        "@{n='TimeCreated';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}},"
        "LevelDisplayName,ProviderName,Id,Message | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "システムログの取得に時間がかかりすぎました。", "■ システムログ\n取得がタイムアウトしました"
    if result.returncode != 0:
        return ("システムログを取得できませんでした。権限が必要な場合があります。",
                f"■ システムログ\n取得に失敗しました\n{result.stderr.strip()[:200]}")
    raw = result.stdout.strip()
    if not raw:
        return (f"直近{hours}時間に、目立ったエラーや再起動の記録は見つかりませんでした。",
                f"■ システムログ (直近{hours}時間)\n記録なし")
    try:
        events = json.loads(raw)
    except json.JSONDecodeError:
        return "システムログの内容を読み取れませんでした。", f"■ システムログ\n解析に失敗しました\n{raw[:200]}"
    if isinstance(events, dict):  # 1件だけだと配列でなくオブジェクトのまま返ってくる
        events = [events]
    lines = []
    for e in events:
        when = e.get("TimeCreated") or ""
        msg = (e.get("Message") or "").splitlines()[0][:150] if e.get("Message") else ""
        lines.append(f"{when} [{e.get('LevelDisplayName')}] {e.get('ProviderName')} (ID {e.get('Id')}): {msg}")
    show = f"■ システムログ (直近{hours}時間, {len(events)}件)\n" + "\n".join(lines)
    ids_found = {e.get("Id") for e in events}
    if 41 in ids_found:
        say = f"直近{hours}時間に、予期しない再起動の記録が見つかりました。詳しくは画面の表示をご確認ください。"
    elif 6008 in ids_found:
        say = f"直近{hours}時間に、異常なシャットダウンの記録が見つかりました。詳しくは画面の表示をご確認ください。"
    else:
        say = f"直近{hours}時間のシステムログを{len(events)}件取得しました。詳しくは画面の表示をご確認ください。"
    return say, show


# ⑥-2 このPCの基本的な脆弱性チェック (OS情報・Defender・ファイアウォール・開いているポート)
def check_pc_security():
    """このPCの基本的な脆弱性チェック(OS情報と最終起動時刻、Windows Defenderの状態、
    ファイアウォールの状態、開いているポートと待ち受けプロセス)をまとめて取得する。
    get_system_log と同じ形でPowerShellを呼ぶので、無人実行(work()の作業モードなど)でも
    承認なしで動く(生のBashコマンドだと承認待ちでブロックされてしまうため)"""
    ps = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
        "$os = Get-CimInstance Win32_OperatingSystem;"
        "$ci = try { Get-ComputerInfo -Property OsName,OsVersion } catch { $null };"
        "$defender = try { Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureLastUpdated } catch { $null };"
        "$firewall = @(try { Get-NetFirewallProfile | Select-Object Name,Enabled } catch { @() });"
        "$ports = @(try { Get-NetTCPConnection -State Listen | Select-Object LocalPort,OwningProcess -Unique } catch { @() });"
        "[PSCustomObject]@{"
        "OsName=$ci.OsName; OsVersion=$ci.OsVersion; LastBootUpTime=$os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss');"
        "Defender=$defender; Firewall=$firewall; Ports=$ports"
        "} | ConvertTo-Json -Compress -Depth 4"
    )
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "脆弱性チェックの取得に時間がかかりすぎました。", "■ 脆弱性チェック\n取得がタイムアウトしました"
    if result.returncode != 0:
        return ("脆弱性チェックの情報を取得できませんでした。権限が必要な場合があります。",
                f"■ 脆弱性チェック\n取得に失敗しました\n{result.stderr.strip()[:200]}")
    raw = result.stdout.strip()
    if not raw:
        return "脆弱性チェックの情報が取得できませんでした。", "■ 脆弱性チェック\n情報なし"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "脆弱性チェックの内容を読み取れませんでした。", f"■ 脆弱性チェック\n解析に失敗しました\n{raw[:200]}"

    defender = data.get("Defender") or {}
    firewalls = data.get("Firewall") or []
    ports = data.get("Ports") or []
    if isinstance(firewalls, dict):  # 1件だけだと配列でなくオブジェクトのまま返ってくる
        firewalls = [firewalls]
    if isinstance(ports, dict):
        ports = [ports]

    av_on = bool(defender.get("AntivirusEnabled"))
    rtp_on = bool(defender.get("RealTimeProtectionEnabled"))
    fw_off = [f["Name"] for f in firewalls if not f.get("Enabled")]

    lines = [
        "■ 脆弱性チェック",
        f"OS: {data.get('OsName') or '不明'} ({data.get('OsVersion') or '不明'})",
        f"最終起動: {data.get('LastBootUpTime') or '不明'}",
        f"Defender: ウイルス対策 {'有効' if av_on else '無効'} / リアルタイム保護 {'有効' if rtp_on else '無効'}"
        + (f" / 定義更新 {defender.get('AntivirusSignatureLastUpdated')}" if defender.get("AntivirusSignatureLastUpdated") else ""),
        "ファイアウォール: " + (", ".join(f"{f['Name']}:{'ON' if f.get('Enabled') else 'OFF'}" for f in firewalls) or "不明"),
        f"待ち受けポート ({len(ports)}件): "
        + (", ".join(f"{p.get('LocalPort')}(PID {p.get('OwningProcess')})" for p in ports[:30]) if ports else "なし"),
    ]
    show = "\n".join(lines)

    concerns = []
    if not av_on or not rtp_on:
        concerns.append("Windows Defenderのリアルタイム保護が無効になっています")
    if fw_off:
        concerns.append(f"ファイアウォール({', '.join(fw_off)})が無効になっています")
    if concerns:
        say = "基本的な脆弱性チェックをしました。" + "、".join(concerns) + "。詳しくは画面の表示をご確認ください。"
    else:
        say = (f"基本的な脆弱性チェックをしました。Defenderもファイアウォールも有効で、"
               f"待ち受けポートは{len(ports)}件見つかりました。詳しくは画面の表示をご確認ください。")
    return say, show


# ⑦ 外出先からの接続 (Tailscale)
def tailscale_ip():
    """この PC の Tailscale の IP アドレスを返す (未インストール/未ログインなら None)。
    家の Wi-Fi の外からでも、同じ Tailscale ネットワークの端末ならこの IP で安全に繋がる"""
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ip = result.stdout.strip().splitlines()[0] if result.returncode == 0 else ""
        return ip or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def remote_access_status():
    """外出先からスマホで使うための Tailscale の接続状況を伝える
    (合言葉付きのアドレスはパソコンの画面でのみ確認でき、声には出さない)"""
    if tailscale_ip():
        say = ("外出先からの接続はつながっています。"
               "パソコンの画面の、スマホから使う、の欄にアドレスが出ているので、出かける前に確認してください。")
        show = "■ 外出先からの接続\nTailscale 接続中\nアドレスは「スマホから使う」の欄で確認してください"
    else:
        say = ("外出先から使うにはTailscaleが必要です。"
               "パソコンとスマホの両方にTailscaleを入れて、同じアカウントでログインしてください。")
        show = "■ 外出先からの接続\nTailscale 未接続\n両方の端末にTailscaleを入れてログインしてください"
    return say, show


# ⑧ TryHackMe のルームページを取得する (タイトル・説明・タスク・質問)
THM_ROOM_URL = "https://tryhackme.com/room/{}"
# 存在しないルームでもHTTP 200でトップページ共通のタイトルが返るため、これと同じなら「見つからない」とみなす
THM_GENERIC_TITLE = "TryHackMe | Cyber Security Training"


def _thm_room_id(text):
    """URLやルーム名から、TryHackMeのルームID (URLの末尾の部分) を取り出す"""
    text = text.strip()
    m = re.search(r"tryhackme\.com/room/([\w-]+)", text)
    return m.group(1) if m else re.sub(r"\s+", "", text)


def _thm_strip_tags(text):
    """HTML/MDXのタグを取り除いて読みやすい文にする (bs4が無いので簡易的に)"""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _thm_meta(page_html, prop):
    """<meta property="og:title" content="..."> のような値を取り出す"""
    pattern = rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)["\']'
    m = re.search(pattern, page_html) or re.search(
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(prop)}["\']', page_html)
    return html.unescape(m.group(1)).strip() if m else ""


def _thm_next_data(page_html):
    """Next.js のページに埋め込まれた __NEXT_DATA__ (ページの元データ) をJSONとして取り出す"""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _thm_find_key(obj, key, depth=12):
    """ネストしたJSONの中から指定したキーの値を探す (ページの作りが変わっても拾えるように再帰的に調べる)"""
    if depth <= 0:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _thm_find_key(v, key, depth - 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _thm_find_key(v, key, depth - 1)
            if found is not None:
                return found
    return None


def _thm_task_questions(task, limit=6):
    """タスクの本文から、質問らしい行 (「？」で終わる行) を取り出す"""
    content = task.get("content") or task.get("body") or task.get("description") or ""
    questions = []
    for line in content.split("\n"):
        text = re.sub(r"^[#*\->\d.\s]+", "", _thm_strip_tags(line)).strip()
        if text.endswith(("?", "？")) and len(text) > 4:
            questions.append(text)
    return questions[:limit]


def _thm_browser_fallback(room_id, url, base_show):
    """Webからタスクが取れない (ログインが必要など) とき、ユーザーに頼らず自分で解決する。
    すでにそのルームが画面に出ていればそれを読み、無ければブラウザで開いてから画面をOCRで読み取る。
    base_show は Web から取れたぶん (タイトル・説明)。戻り値は (読み上げる文, 画面表示用の文)"""
    def screen_text():
        _, show = read_screen()
        head = "■ 画面の文字\n"
        return show[len(head):].strip() if show.startswith(head) else ""

    def looks_like_room(c):  # 画面にそのTryHackMeルームが映っていそうか
        c = re.sub(r"[\s　]", "", c).lower()
        return bool(c) and ("tryhackme" in c or "task" in c or room_id.lower() in c)

    content = screen_text()
    opened = False
    if not looks_like_room(content):  # 今は画面に出ていない → 自分でブラウザに開く
        webbrowser.open(url)
        time.sleep(5)  # ページが読み込まれて表示されるのを待つ
        opened = True
        content = screen_text()
    if looks_like_room(content):
        how = "ブラウザで開いて画面から" if opened else "画面に出ていたので"
        return (f"ログインが必要でしたが、{how}読み取りました。タスクの内容からお手伝いできますね。",
                base_show + f"\n\n■ ブラウザ画面から読み取った内容\n{content}")
    return ("ログインが必要で、ブラウザで開いてみましたが、まだ画面から内容を読み取れませんでした。"
            "ページが表示されてから、もう一度お願いできますか。",
            base_show + "\n\n(ブラウザを開きましたが、画面からも内容を読み取れませんでした)")


def thm_page(text):
    """TryHackMeのルームページ (URLまたはルーム名) を取得し、タイトル・説明・タスク一覧・
    各タスクの質問をまとめる。ログインが必要でWebから中身を取れないときは、ユーザーに頼らず
    自分でブラウザに開いて画面から読み取る (_thm_browser_fallback)。
    ここで分かったルーム名は、features.update_room (jarvis_tools.py の thm コマンド) で
    進み具合を記録するときの room 名としても使える"""
    room_id = _thm_room_id(text)
    if not room_id:
        return "ルーム名か、TryHackMeのURLを教えてください。", "■ TryHackMe\nルーム名が必要です"
    url = THM_ROOM_URL.format(room_id)
    try:
        res = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        return "TryHackMeに接続できませんでした。インターネットの接続を確認してください。", f"■ TryHackMe\n接続エラー: {e}"
    if res.status_code == 404:
        return f"{room_id}というルームが見つかりませんでした。", f"■ TryHackMe\n{room_id} は見つかりませんでした\nURL: {url}"
    if res.status_code in (401, 403):  # ログインが必要 → 自分でブラウザに開いて画面から読む
        return _thm_browser_fallback(room_id, url, f"■ TryHackMe\n{room_id}\nURL: {url}")
    if res.status_code != 200:
        return "ルームのページを取得できませんでした。", f"■ TryHackMe\nHTTPエラー {res.status_code}\nURL: {url}"

    data = _thm_next_data(res.text)
    room = _thm_find_key(data, "room") if data else None
    if isinstance(room, dict):
        title = room.get("title") or room_id
        description = _thm_strip_tags(room.get("description", ""))
    else:
        title = _thm_meta(res.text, "og:title") or room_id
        description = _thm_meta(res.text, "og:description") or ""
    if title == THM_GENERIC_TITLE:  # ルーム専用のタイトルが取れていない → そのIDのルームは存在しない
        return f"{room_id}というルームが見つかりませんでした。", f"■ TryHackMe\n{room_id} は見つかりませんでした\nURL: {url}"
    tasks = _thm_find_key(data, "tasks") if data else None
    tasks = [t for t in (tasks or []) if isinstance(t, dict) and t.get("title")]

    if not description and not tasks and title == room_id:  # 何も取れない → ブラウザの画面から読む
        return _thm_browser_fallback(room_id, url, f"■ TryHackMe\n{room_id}\nURL: {url}")

    say = f"TryHackMeのルーム、{title}です。"
    if description:
        say += description.split("。")[0].strip() + "。"

    show = [f"■ TryHackMe: {title}", f"URL: {url}"]
    if description:
        show += ["", description]
    if tasks:
        say += f"タスクは{len(tasks)}個あります。"
        show += ["", f"タスク ({len(tasks)}個):"]
        for i, t in enumerate(tasks, 1):
            show.append(f"{i}. {t.get('title')}")
            show += [f"   - {q}" for q in _thm_task_questions(t)]
        return say, "\n".join(show)
    # タイトル・説明は取れたがタスクはログインが必要 → ブラウザの画面から読み取って補う
    return _thm_browser_fallback(room_id, url, "\n".join(show))


# ⑨ 画面を見て文字を読み取る (OCR。Windows標準の機能だけを使い、画像は外部に送らずPC内だけで処理する)
OCR_SCRIPT = Path(__file__).with_name("ocr_screen.ps1")
IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _reading_target_window():
    """read-screen で読むウィンドウ。前面のウィンドウ (focus-window や switch-tab で出したブラウザなど) を読む。
    前面が JARVIS 自身の画面のときは、そのすぐ後ろにある普通のアプリのウィンドウにする。無ければ None"""
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    front = user32.GetForegroundWindow()
    if front and not user32.IsIconic(ctypes.c_void_p(front)) and _window_title(front).strip() \
            and _window_title(front) not in KEEP_WINDOW_TITLES:
        return front
    windows = _arrangeable_windows()  # 手前から順に並んでいる
    return windows[0] if windows else None


def read_screen(text=""):
    """画面を撮影して、映っている文字を読み取る (ブラウザに表示されたTryHackMeのルームなども対象)。
    既定では画面全体を読む。「前面」を付けると前面のウィンドウだけを読む (文字が多くて時間がかかるときや、
    1つのアプリの中だけ見たいとき。ただし後ろのウィンドウやメモ帳など別の窓は見えなくなる)。
    OCRはWindows標準の機能 (Windows.Media.Ocr) をPowerShell経由で使うので、新しいソフトは不要。
    失敗したときは、撮影・OCR・文字なしのどこで失敗したかを区別して伝える"""
    import ctypes
    from PIL import ImageGrab
    shot = Path(tempfile.gettempdir()) / "jarvis_screen.png"
    out = Path(tempfile.gettempdir()) / "jarvis_screen_ocr.txt"
    front_only = any(w in text for w in ("前面", "最前", "このウィンドウ", "アクティブ", "active"))
    target = _reading_target_window() if front_only else None
    try:
        with _dpi_aware():  # ウィンドウの範囲と撮影を物理ピクセルでそろえる
            rect = _window_rect(target) if target else None
            if rect:  # 最大化したウィンドウは画面の外に数pxはみ出すので、画面の範囲に収める
                get = ctypes.windll.user32.GetSystemMetrics
                vx, vy = get(76), get(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
                vr, vb = vx + get(78), vy + get(79)  # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
                rect = (max(rect[0], vx), max(rect[1], vy), min(rect[2], vr), min(rect[3], vb))
            if rect and rect[2] - rect[0] > 50 and rect[3] - rect[1] > 50:
                ImageGrab.grab(bbox=rect, all_screens=True).save(shot, "PNG")
                where = _window_title(target)
            else:
                ImageGrab.grab(all_screens=True).save(shot, "PNG")
                where = "画面全体"
    except OSError as e:
        return "画面を撮影できませんでした。", f"■ 画面の文字\n撮影に失敗しました: {e}"
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT),
                                 "-ImagePath", str(shot), "-OutputPath", str(out)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "画面の文字の読み取りに時間がかかりすぎました。", "■ 画面の文字\nOCRがタイムアウトしました"
    finally:
        shot.unlink(missing_ok=True)
    try:
        content = out.read_text(encoding="utf-8").strip() if out.exists() else ""
    finally:
        out.unlink(missing_ok=True)
    if result.returncode == 2:  # スクリーンショットの画像を読み込めなかった
        return "撮影した画面の画像を読み込めませんでした。", f"■ 画面の文字\n画像の読み込みに失敗しました\n{result.stderr.strip()[:200]}"
    if result.returncode == 3:  # OCRの言語パックが入っていない
        return ("文字を読み取るための言語パックがパソコンに入っていないようです。"
                "設定の、時刻と言語、言語と地域で、文字認識の言語パックを追加してください。",
                f"■ 画面の文字\nOCR言語パックが見つかりません\n{result.stderr.strip()[:200]}")
    if result.returncode == 4:  # OCRの認識自体に失敗した
        return "画面の文字の読み取りに失敗しました。", f"■ 画面の文字\nOCRでエラーが発生しました\n{result.stderr.strip()[:200]}"
    if result.returncode != 0:
        return "画面の文字を読み取れませんでした。", f"■ 画面の文字\n不明なエラー (終了コード {result.returncode})\n{result.stderr.strip()[:200]}"
    if not content:
        return ("画面に文字が見つかりませんでした。読みたい文字が画面に表示され、スクロールして見える位置にあるか確認してください。",
                "■ 画面の文字\n文字が見つかりませんでした (スクロール位置を確認してください)")
    ips = list(dict.fromkeys(IP_PATTERN.findall(content)))  # 順番を保ったまま重複を除く
    show = f"■ 画面の文字 ({where})\n" + content
    if ips:
        show += "\n\n■ 見つかったIPアドレス\n" + "\n".join(ips)
        say = f"画面にIPアドレス {ips[0]} が見つかりました。"
        if len(ips) > 1:
            say += f"他にも{len(ips) - 1}件あります。"
    else:
        say = "画面の文字を読み取りました。詳しい内容は画面の表示でご確認ください。"
    return say, show


# ⑨-1 画面を撮影して、画像を残したまま文字を読み取る (read_screen と同じ撮影方法だが、
# OCRが苦手な画像中心のページやボタンの見た目を、AIがReadツールで画像を直接見て確認できるようにする)
CAPTURE_PATH = Path(tempfile.gettempdir()) / "jarvis_screen_capture.png"


def capture_screen(text=""):
    """read_screen と同じ方法 (前面/画面全体の切り替えも含む) で画面を撮影してOCRにかけ、文字を読み取る。
    read_screen と違い、撮影した画像ファイルは消さずに CAPTURE_PATH (tempfile配下の固定ファイル名) に残す。
    OCRで文字がうまく読み取れない場面 (画像中心のページ、ボタンの見た目確認など) で、
    AIがReadツールでその画像を直接見て、視覚的に確認するために使う"""
    import ctypes
    from PIL import ImageGrab
    shot = CAPTURE_PATH
    out = Path(tempfile.gettempdir()) / "jarvis_screen_ocr.txt"
    front_only = any(w in text for w in ("前面", "最前", "このウィンドウ", "アクティブ", "active"))
    target = _reading_target_window() if front_only else None
    try:
        with _dpi_aware():  # ウィンドウの範囲と撮影を物理ピクセルでそろえる
            rect = _window_rect(target) if target else None
            if rect:  # 最大化したウィンドウは画面の外に数pxはみ出すので、画面の範囲に収める
                get = ctypes.windll.user32.GetSystemMetrics
                vx, vy = get(76), get(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
                vr, vb = vx + get(78), vy + get(79)  # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
                rect = (max(rect[0], vx), max(rect[1], vy), min(rect[2], vr), min(rect[3], vb))
            if rect and rect[2] - rect[0] > 50 and rect[3] - rect[1] > 50:
                ImageGrab.grab(bbox=rect, all_screens=True).save(shot, "PNG")
                where = _window_title(target)
            else:
                ImageGrab.grab(all_screens=True).save(shot, "PNG")
                where = "画面全体"
    except OSError as e:
        return "画面を撮影できませんでした。", f"■ 画面の文字\n撮影に失敗しました: {e}"
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT),
                                 "-ImagePath", str(shot), "-OutputPath", str(out)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "画面の文字の読み取りに時間がかかりすぎました。", "■ 画面の文字\nOCRがタイムアウトしました"
    # shot は read_screen と違い消さずに残す (画像をそのままReadツールで確認できるように)
    try:
        content = out.read_text(encoding="utf-8").strip() if out.exists() else ""
    finally:
        out.unlink(missing_ok=True)
    if result.returncode == 2:  # スクリーンショットの画像を読み込めなかった
        return "撮影した画面の画像を読み込めませんでした。", f"■ 画面の文字\n画像の読み込みに失敗しました\n{result.stderr.strip()[:200]}"
    if result.returncode == 3:  # OCRの言語パックが入っていない
        return ("文字を読み取るための言語パックがパソコンに入っていないようです。"
                "設定の、時刻と言語、言語と地域で、文字認識の言語パックを追加してください。",
                f"■ 画面の文字\nOCR言語パックが見つかりません\n{result.stderr.strip()[:200]}")
    if result.returncode == 4:  # OCRの認識自体に失敗した
        return "画面の文字の読み取りに失敗しました。", f"■ 画面の文字\nOCRでエラーが発生しました\n{result.stderr.strip()[:200]}"
    if result.returncode != 0:
        return "画面の文字を読み取れませんでした。", f"■ 画面の文字\n不明なエラー (終了コード {result.returncode})\n{result.stderr.strip()[:200]}"
    image_note = f"\n\n■ 画像ファイル (Readツールで直接確認できます)\n{shot}"
    if not content:
        return ("画面に文字が見つかりませんでした。読みたい文字が画面に表示され、スクロールして見える位置にあるか確認してください。",
                "■ 画面の文字\n文字が見つかりませんでした (スクロール位置を確認してください)" + image_note)
    ips = list(dict.fromkeys(IP_PATTERN.findall(content)))  # 順番を保ったまま重複を除く
    show = f"■ 画面の文字 ({where})\n" + content + image_note
    if ips:
        show += "\n\n■ 見つかったIPアドレス\n" + "\n".join(ips)
        say = f"画面にIPアドレス {ips[0]} が見つかりました。"
        if len(ips) > 1:
            say += f"他にも{len(ips) - 1}件あります。"
    else:
        say = "画面の文字を読み取りました。詳しい内容は画面の表示でご確認ください。"
    return say, show


# ⑨-2 ポートスキャン (TryHackMe など、自分で起動した認可済みの学習・CTF 用ターゲットの偵察)
# nmap が無くても動くよう、Python 標準の socket だけで TCP 接続スキャンを行う (追加インストール不要)
COMMON_PORTS = {  # 初期偵察でまず見るよく使われるポート
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 80: "http", 110: "pop3",
    111: "rpcbind", 135: "msrpc", 139: "netbios-ssn", 143: "imap", 443: "https", 445: "smb",
    993: "imaps", 995: "pop3s", 1433: "mssql", 2049: "nfs", 3000: "http-alt", 3306: "mysql",
    3389: "rdp", 5432: "postgresql", 5900: "vnc", 5985: "winrm", 6379: "redis",
    8000: "http-alt", 8080: "http-proxy", 8443: "https-alt", 8888: "http-alt", 27017: "mongodb"}
SCAN_PORT_LIMIT = 3000  # 会話の制限時間内に収めるため、一度に調べるポート数の上限
NMAP_TIMEOUT = 160  # nmap 1回あたりの制限時間。会話モード(600秒)の中で数回のスキャンが収まるよう、1回は短く抑える


def _find_nmap():
    """nmap があればその実行ファイルのパスを返す (PATH → Windows標準のインストール先)。無ければ None"""
    import shutil
    p = shutil.which("nmap")
    if p:
        return p
    for c in (r"C:\Program Files (x86)\Nmap\nmap.exe", r"C:\Program Files\Nmap\nmap.exe"):
        if Path(c).exists():
            return c
    return None


def _scan_with_nmap(nmap, host, ip, port_spec, scope):
    """nmap でポートとサービスのバージョンを調べる。-sT (接続スキャン) なので Npcap や管理者権限が不要。
    -Pn で ping を省く (CTF のホストは ping を返さないことが多い)"""
    cmd = [nmap, "-sT", "-sV", "-T4", "-Pn", "-p", port_spec, ip]
    _log_automation(f"nmap スキャン {host} ({scope}, -p {port_spec})")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=NMAP_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return ("スキャンに時間がかかりすぎました。ポートの範囲を狭めて、もう一度お願いできますか。",
                f"■ ポートスキャン (nmap): {host}\n時間切れになりました。範囲を狭めて再実行してください")
    except OSError:
        return None  # nmap を起動できない → 呼び出し元で Python スキャナにフォールバックする
    out = (r.stdout or "").strip()
    if r.returncode != 0 or "Nmap scan report" not in out:
        return None  # nmap が正常に動かなかった (Npcap未導入でDLL不足、など) → フォールバック
    open_lines = [ln.strip() for ln in out.splitlines() if re.match(r"\d+/tcp\s+open", ln.strip())]
    show = f"■ ポートスキャン (nmap): {host} ({ip})\n{scope}\n\n{out or '(出力なし)'}"
    if open_lines:
        heads = []
        for ln in open_lines[:4]:
            mm = re.match(r"(\d+)/tcp\s+open\s+(\S+)", ln)
            heads.append(f"{mm.group(1)}番{mm.group(2)}" if mm else ln)
        say = f"{host} で開いているポートが{len(open_lines)}個見つかりました。" + "、".join(heads) + "などです。"
    else:
        say = f"{host} で開いているポートは見つかりませんでした。"
    return say, show


def _scan_port(host, port, timeout=1.0):
    """1ポートにTCP接続してみて、開いていれば (ポート番号, バナー) を返す。閉じていれば None"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            banner = ""
            try:  # 接続直後に何か送ってくるサービス (ssh/ftp/smtp など) のバナーを少しだけ読む
                s.settimeout(0.8)
                banner = s.recv(96).decode("latin-1", "replace").strip()
            except OSError:
                pass
        return port, banner
    except OSError:
        return None


def scan_ports(text):
    """指定したホスト (IP かドメイン) のポートを調べて、開いているものとサービスを一覧する。
    TryHackMe や HackTheBox など、自分で起動した認可済みの学習・CTF 用ターゲットの偵察に使う。
    「10.48.170.229」や「10.48.170.229 ポート 1-1000」のように対象とポート範囲を指定できる
    (範囲の指定がなければ、よく使われるポートだけを手早く調べる)。
    nmap が無くても Python 標準の socket で動く"""
    import socket
    import concurrent.futures
    m = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9-]+)+\b", text)
    if not m:
        return "調べる相手のIPアドレスかホスト名を教えてください。", "■ ポートスキャン\n対象 (IP/ホスト名) が必要です"
    host = m.group(0)
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return f"{host} に接続先が見つかりませんでした。VPNにつながっているか、名前が正しいか確認してください。", \
               f"■ ポートスキャン\n{host} を解決できませんでした (VPN接続や名前を確認してください)"
    rng = re.search(r"(\d{1,5})\s*[-〜~]\s*(\d{1,5})", text)
    if rng:
        lo, hi = sorted((int(rng.group(1)), int(rng.group(2))))
        lo, hi = max(1, lo), min(65535, hi)
        ports = list(range(lo, hi + 1))
        truncated = len(ports) > SCAN_PORT_LIMIT
        ports = ports[:SCAN_PORT_LIMIT]
        scope = f"ポート {lo}〜{ports[-1]}"
        port_spec = f"{lo}-{ports[-1]}"
    else:
        ports = sorted(COMMON_PORTS)
        truncated = False
        scope = "よく使われるポート"
        port_spec = ",".join(str(p) for p in ports)
    nmap = _find_nmap()
    if nmap:  # nmap があれば、サービスのバージョンまで分かるので優先して使う
        result = _scan_with_nmap(nmap, host, ip, port_spec, scope)
        if result is not None:
            return result
        # nmap が動かなかった (Npcap未導入など) → Python スキャナにフォールバック
    # Python 標準の socket で TCP 接続スキャンする
    _log_automation(f"ポートスキャン {host} ({scope}, {len(ports)}ポート)")
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=200) as pool:
        for r in pool.map(lambda p: _scan_port(ip, p), ports):
            if r:
                found.append(r)
    found.sort()
    if not found:
        return (f"{host} で開いているポートは見つかりませんでした。",
                f"■ ポートスキャン: {host} ({ip})\n{scope}を調べましたが、開いているポートはありませんでした。\n"
                "ファイアウォールで閉じているか、VPNでその相手につながっていない可能性があります。")
    lines = [f"■ ポートスキャン: {host} ({ip})", f"{scope} / 開いているポート {len(found)}個"]
    for port, banner in found:
        name = COMMON_PORTS.get(port, "")
        line = f"  {port}/tcp" + (f"  {name}" if name else "")
        if banner:
            line += f"  « {banner[:60]}"
        lines.append(line)
    if truncated:
        lines.append(f"(ポート数が多いため先頭{SCAN_PORT_LIMIT}個までにしました。続きは範囲を分けて調べてください)")
    names = "、".join(f"{p}番{COMMON_PORTS.get(p, '')}" for p, _ in found[:4])
    say = f"{host} で開いているポートが{len(found)}個見つかりました。{names}などです。"
    return say, "\n".join(lines)


# ⑩ 画面を操作する (マウスのクリック・キーボードの入力。読み取り専用のread-screenと違い、実際にPCを操作する)
# 新しいライブラリは入れられないので、標準ライブラリのctypes経由でWindowsのAPIを直接呼ぶ
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120  # ホイール1ノッチ分の回転量
VK_RETURN = 0x0D
AUTOMATION_LOG_FILE = Path(__file__).with_name("jarvis.log")


def _log_automation(text):
    """クリックや入力を実行する前に、何をするかをjarvis.logに記録する (誤操作があったときの確認用)"""
    with AUTOMATION_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%m-%d %H:%M:%S')} 画面操作: {text}\n")


# AIの使用量レポート。jarvis.log に残る目印から、何にAIを使ったかを集計する。
# パソコン側では Claude Pro の正確なトークン数は取れないので、活動ベースの推定。
_USAGE_LINE = re.compile(r"^(\d{2}-\d{2}) \d{2}:\d{2}:\d{2} (.*)$")
_USAGE_MARKERS = {
    "user": re.compile(r"あなた.*?: "),              # ユーザーの発言(声/チャット)
    "operate": re.compile(r"では、始めますね"),         # 自動操作モードの開始
    "work": re.compile(r"作業しますね"),               # 作業(調査)モードの開始
    "dev": re.compile(r"自動改修|変更が終わりました"),    # 開発(プログラム改修)
    "to_ollama": re.compile(r"Geminiが使えないため"),    # 無料AI(Gemini)→ローカルAI(Ollama)へ切替
    "to_claude": re.compile(r"無料のAIが使えないため"),   # 無料AI不可→Claudeへ切替
    "ollama": re.compile(r"ローカルAI"),               # ローカルAI(Ollama)が稼働
    "limit": re.compile(r"session limit|利用上限|hit your session"),  # Claude Pro の上限に到達
    "error": re.compile(r"エラー"),                     # 何らかのエラー
}


def usage_report(day=None):
    """jarvis.log を集計して、今日(または MM-DD 指定日)にAIを何に使ったかを報告する。
    (読み上げる要約, 画面に出す内訳) を返す。正確なトークン数は取得できないため、
    ログに残る活動ベースの推定。"""
    day = day or time.strftime("%m-%d")
    counts = {name: 0 for name in _USAGE_MARKERS}
    try:
        lines = AUTOMATION_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "ログが読めなかったので、使用量をお伝えできません。", "■ AI使用量\nログが読めませんでした"
    for raw in lines:
        m = _USAGE_LINE.match(raw)
        if not m or m.group(1) != day:
            continue
        body = m.group(2)
        for name, pat in _USAGE_MARKERS.items():
            if pat.search(body):
                counts[name] += 1
    total = counts["user"]
    chat = max(0, total - counts["operate"] - counts["work"])
    heavy = counts["operate"] + counts["work"] + counts["dev"]
    if total == 0:
        return (f"今日はまだ、AIとのやり取りがありません。",
                f"■ AI使用量レポート {day}\nまだやり取りがありません")
    say = (f"今日はAIと合計{total}回やり取りしました。"
           f"会話が{chat}回、自動操作が{counts['operate']}回、調べ物が{counts['work']}回です。")
    if counts["limit"]:
        say += (f"重い処理でクロードの利用上限に{counts['limit']}回当たりましたが、"
                "ふだんの会話は無料のAIでまかなえています。")
    else:
        say += "利用上限には当たっていません。ふだんの会話は無料のAIが中心です。"
    show = (f"■ AI使用量レポート {day} (活動ベースの推定)\n"
            f"やり取り合計: {total} 回\n"
            f"  会話 {chat} / 自動操作 {counts['operate']} / 調べ物 {counts['work']} / 開発 {counts['dev']}\n"
            f"無料AI→ローカルAI 切替: {counts['to_ollama']} 回 / ローカルAI稼働: {counts['ollama']} 回\n"
            f"無料AI不可→Claude 切替: {counts['to_claude']} 回\n"
            f"Claude Pro 上限到達: {counts['limit']} 回 / エラー全体: {counts['error']} 回\n"
            f"重い処理(自動操作+調べ物+開発)={heavy} 回 は主に Claude(Pro枠)を使います\n"
            f"※ 正確なトークン数はパソコン側で取得できないため推定です")
    return say, show


@contextlib.contextmanager
def _dpi_aware():
    """この処理の間だけ、スレッドを Per-Monitor v2 の DPI 認識に切り替える。
    こうしないと、拡大表示 (125%/150% など) のモニターでは
    スクリーンショットの座標 (物理ピクセル) とマウス移動 (SetCursorPos) やGetSystemMetricsの
    座標 (論理ピクセル) がズレて、クリックが全然違う場所に落ちてしまう。
    既存の顔GUI(Tkinter)の表示に影響しないよう、プロセス全体ではなくスレッド単位で一時的に変える"""
    import ctypes
    user32 = ctypes.windll.user32
    prev = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        prev = None  # 古いWindowsでは未対応。その場合は従来どおり動かす
    try:
        yield
    finally:
        if prev:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))


def _virtual_screen_offset():
    """複数モニターをまたいで画面全体を撮影したとき、撮影した画像の原点 (0, 0) に対応する
    実際の画面上の座標 (プライマリでないモニターが左や上にあると0より小さいことがある)"""
    import ctypes
    get = ctypes.windll.user32.GetSystemMetrics
    return get(76), get(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN


def _ocr_words():
    """画面を撮影して、映っている単語と、それぞれの画面上の座標の一覧を返す"""
    from PIL import ImageGrab
    shot = Path(tempfile.gettempdir()) / "jarvis_screen.png"
    text_out = Path(tempfile.gettempdir()) / "jarvis_screen_ocr.txt"
    words_out = Path(tempfile.gettempdir()) / "jarvis_screen_words.json"
    with _dpi_aware():  # 撮影サイズと画面原点を物理ピクセルでそろえる (拡大表示でのズレ防止)
        ImageGrab.grab(all_screens=True).save(shot, "PNG")
        offset_x, offset_y = _virtual_screen_offset()
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT),
                       "-ImagePath", str(shot), "-OutputPath", str(text_out), "-WordsPath", str(words_out)],
                      capture_output=True, timeout=20,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # PowerShell の [Text.Encoding]::UTF8 は BOM 付きで書き出すため utf-8-sig で読む
        # (utf-8 のままだと json.loads が BOM で失敗し、下の except で常に単語0件になってしまう)
        words = json.loads(words_out.read_text(encoding="utf-8-sig")) if words_out.exists() else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        words = []
    finally:
        shot.unlink(missing_ok=True)
        text_out.unlink(missing_ok=True)
        words_out.unlink(missing_ok=True)
    for w in words:  # 撮影した画像内の座標 → 実際の画面上の座標に直す
        w["x"] += offset_x
        w["y"] += offset_y
    # JARVIS自身の画面に出ている文字 (「クリックしています: Start AttackBox」などの実況や会話) は除く。
    # 残すと本物のボタンと同じ文字が何箇所も見つかり、クリックを中止したり別の場所を押したりしてしまう
    with _dpi_aware():
        return [w for w in words
                if _window_title(_root_window_at(w["x"] + w["w"] // 2, w["y"] + w["h"] // 2)) not in KEEP_WINDOW_TITLES]


def _root_window_at(x, y):
    """画面上の (x, y) で一番手前に見えているウィンドウ (子ウィンドウではなく、その親のアプリのウィンドウ)。
    座標は物理ピクセルなので、呼び出し側で _dpi_aware() の中で使う"""
    import ctypes
    from ctypes import wintypes
    GA_ROOT = 2
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.restype = ctypes.c_void_p
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    return user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None


def _in_foreground_window(matches):
    """候補のうち、今前面にあるウィンドウ (focus-window や switch-tab で出したブラウザなど) の中にあるものだけを返す。
    背後のエディタやログに同じ文字が映っていても、操作中の画面のボタンを選べるようにする"""
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    front = user32.GetForegroundWindow()
    if not front:
        return []
    with _dpi_aware():
        return [mt for mt in matches if _root_window_at(mt[0], mt[1]) == front]


FUZZY_LABEL_THRESHOLD = 0.85  # あいまい一致とみなす文字列の類似度 (SequenceMatcher.ratio)
NEAR_MATCH_PX = 50  # これ以内の近さの一致は、同じボタンを単語の切れ目違いで拾った重複とみなす
                    # (同じボタンは1単語分=数十pxずれて重複しうる。ボタンの行間は約130pxあるので別行は誤統合しない)


def _dedupe_matches(matches):
    """同じボタンの文字を、単語の切れ目違いで複数回拾ってしまった近接一致を1件にまとめる。
    十数pxしか離れていない「別々の一致」が残ると、本当は1つのボタンなのに
    click_screen が「複数箇所見つかった」と誤判定してクリックを拒否してしまうため。
    完全一致を優先し (どちらかが完全一致なら統合後も完全一致)、座標は平均に寄せる"""
    merged = []
    for x, y, fuzzy in matches:
        for i, (mx, my, mfuzzy) in enumerate(merged):
            if abs(x - mx) <= NEAR_MATCH_PX and abs(y - my) <= NEAR_MATCH_PX:
                merged[i] = (round((mx + x) / 2), round((my + y) / 2), mfuzzy and fuzzy)
                break
        else:  # 近い既存の一致が無ければ、新しいボタンとして追加する
            merged.append((x, y, fuzzy))
    return merged


def _find_label(label):
    """画面に映っている単語をつなげていき、指定した文字列と一致する場所の中心座標をすべて探す
    (「Start AttackBox」のように複数の単語にまたがるボタンの文字も見つけられる)。
    完全一致が1件も見つからなかった場合に限り、OCRの1〜2文字程度の誤読を想定して
    difflibで類似度の高い箇所も「あいまい一致」として救済する。
    戻り値は (x, y, あいまい一致かどうか) のタプルの一覧"""
    words = _ocr_words()
    label = re.sub(r"[\s　]", "", label).lower()
    matches = []
    for i in range(len(words)):
        joined, boxes = "", []
        for w in words[i:]:
            joined += re.sub(r"[\s　]", "", w["text"]).lower()
            boxes.append(w)
            if label in joined:
                if label in joined[len(re.sub(r"[\s　]", "", words[i]["text"]).lower()):]:
                    break  # 先頭の単語なしでも一致する = 同じ文字を手前の単語から拾っただけ (その単語から始まる回で数える)
                x = sum(b["x"] + b["w"] / 2 for b in boxes) / len(boxes)
                y = sum(b["y"] + b["h"] / 2 for b in boxes) / len(boxes)
                matches.append((round(x), round(y), False))
                break
            if len(joined) > len(label) + 20:  # 関係ない単語が続くなら切り上げる
                break
    if matches:
        return _dedupe_matches(matches)
    for i in range(len(words)):  # 完全一致が無いときだけ、あいまい一致を試す
        joined, boxes = "", []
        for w in words[i:]:
            joined += re.sub(r"[\s　]", "", w["text"]).lower()
            boxes.append(w)
            if len(joined) >= len(label):
                candidate = joined[:len(label)]
                if difflib.SequenceMatcher(None, label, candidate).ratio() >= FUZZY_LABEL_THRESHOLD:
                    x = sum(b["x"] + b["w"] / 2 for b in boxes) / len(boxes)
                    y = sum(b["y"] + b["h"] / 2 for b in boxes) / len(boxes)
                    matches.append((round(x), round(y), True))
                break
            if len(joined) > len(label) + 20:
                break
    return _dedupe_matches(matches)


def _dedupe_rects(rects):
    """同じ文字を単語の切れ目違いで複数回拾った近接重複を1件にまとめる (_dedupe_matches の範囲版)。
    中心どうしが近い範囲は、広い方に包含して1件にする"""
    merged = []
    for r in rects:
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        for i, mr in enumerate(merged):
            mcx, mcy = (mr[0] + mr[2]) / 2, (mr[1] + mr[3]) / 2
            if abs(cx - mcx) <= NEAR_MATCH_PX and abs(cy - mcy) <= NEAR_MATCH_PX:
                merged[i] = (min(r[0], mr[0]), min(r[1], mr[1]),
                             max(r[2], mr[2]), max(r[3], mr[3]), mr[4] and r[4])
                break
        else:
            merged.append(r)
    return merged


def _label_rects(label):
    """ラベル文字の画面上の範囲 (left, top, right, bottom, あいまい一致か) をすべて返す。
    click_near で『その文字の下／右』を押すには、中心だけでなく文字の範囲が要る。
    誤クリックを避けるため、ここでは確実な完全一致だけを対象にする (あいまい一致は使わない)"""
    words = _ocr_words()
    target = re.sub(r"[\s　]", "", label).lower()
    if not target:
        return []
    rects = []
    for i in range(len(words)):
        joined, boxes = "", []
        for w in words[i:]:
            joined += re.sub(r"[\s　]", "", w["text"]).lower()
            boxes.append(w)
            if target in joined:
                if target in joined[len(re.sub(r"[\s　]", "", words[i]["text"]).lower()):]:
                    break  # 手前の単語を含めただけの重複 (_find_label と同じ)
                rects.append((min(b["x"] for b in boxes), min(b["y"] for b in boxes),
                              max(b["x"] + b["w"] for b in boxes), max(b["y"] + b["h"] for b in boxes), False))
                break
            if len(joined) > len(target) + 20:  # 関係ない単語が続くなら切り上げる
                break
    return _dedupe_rects(rects)


INPUT_NEAR_ROW_HEIGHT = 50     # 直後の行を探すときの、1行ぶんの高さの目安
INPUT_NEAR_MIN_WIDTH = 60      # 入力欄とみなす空白領域の最小の幅(ピクセル)
INPUT_NEAR_MAX_WIDTH = 400     # 空白領域として探す最大の幅 (離れすぎた次の文字までは入力欄とみなさない)


def _blank_gap(words, y_center, x_start, x_limit):
    """高さ y_center で、x_start から右へ向かって他の単語に当たるまでの空白の幅を返す。
    他の単語に全く当たらなければ x_limit までの幅"""
    blocking = [w["x"] for w in words
               if w["x"] >= x_start and w["y"] <= y_center <= w["y"] + w["h"]]
    end = min(blocking) if blocking else x_limit
    return max(0, end - x_start)


def _find_input_near(hint):
    """ラベルの無い入力欄を、近くの手がかりの文字 (質問文の一部や答えの単語など) から探す。
    手がかりの文字が画面でちょうど1か所だけ見つかったとき、まず同じ行の右側、
    無ければ直後の行にある「文字の無い空白領域」の中心付近の座標 (x, y) を1件返す。
    手がかりが見つからない、または複数見つかって場所を決められないときは None"""
    rects = _label_rects(hint)
    if len(rects) != 1:
        return None
    left, top, right, bottom, _ = rects[0]
    words = _ocr_words()
    cy = (top + bottom) // 2
    gap = _blank_gap(words, cy, right, right + INPUT_NEAR_MAX_WIDTH)
    if gap >= INPUT_NEAR_MIN_WIDTH:  # 同じ行の右側に十分な空きがある
        return round(right + min(gap, INPUT_NEAR_MAX_WIDTH) / 2), cy
    next_cy = bottom + INPUT_NEAR_ROW_HEIGHT // 2
    gap = _blank_gap(words, next_cy, left, left + INPUT_NEAR_MAX_WIDTH)
    if gap >= INPUT_NEAR_MIN_WIDTH:  # 直後の行に空きがある
        return round(left + min(gap, INPUT_NEAR_MAX_WIDTH) / 2), next_cy
    return None


TRAY_ICON_STEP = 28     # タスクトレイのアイコン1つぶんのおおよその幅 (走査する間隔)
TRAY_HOVER_WAIT = 0.35  # マウスを乗せてからツールチップが表示されるまで待つ時間
TRAY_OPEN_WAIT = 0.4    # 「隠れているアイコンを表示」を押してからフライアウトが開くまで待つ時間
TRAY_ACTIVATE_WAIT = 0.6  # タスクトレイのアイコンをクリックしてから、ウィンドウが現れるまで待つ時間


def _window_rect(hwnd):
    """ウィンドウの画面上の範囲 (left, top, right, bottom) を返す。無効なハンドルなら None"""
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    if not hwnd or not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def _open_hidden_tray_icons():
    """タスクトレイの「隠れているアイコンを表示」(^) ボタンを押して、通知領域に入りきらず
    隠れているアイコン (OpenVPN Connectなど) の一覧をいったん開く。すでに開いていても害はない"""
    import ctypes
    user32 = ctypes.windll.user32
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    notify = user32.FindWindowExW(tray, 0, "TrayNotifyWnd", None) if tray else 0
    chevron = user32.FindWindowExW(notify, 0, "Button", None) if notify else 0
    rect = _window_rect(chevron)
    if not rect:
        return False
    x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    with _dpi_aware():
        user32.SetCursorPos(x, y)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(TRAY_OPEN_WAIT)
    return True


def _tray_scan_rects():
    """タスクトレイの通知領域と、「隠れているアイコンを表示」で開いたフライアウトの、
    画面上の走査範囲 (left, top, right, bottom) の一覧。開いていないフライアウトは含めない"""
    import ctypes
    user32 = ctypes.windll.user32
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    notify = user32.FindWindowExW(tray, 0, "TrayNotifyWnd", None) if tray else 0
    rects = [r for r in (_window_rect(notify), _window_rect(user32.FindWindowW("NotifyIconOverflowWindow", None)))
             if r]
    return rects


def _tooltip_text():
    """今画面に出ているツールチップ (マウスを乗せたときに出る吹き出し) の文字を読む。
    タスクトレイのアイコンは文字が無く、名前がツールチップにしか出ないことがあるため使う"""
    import ctypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _):
        buf = ctypes.create_unicode_buffer(32)
        user32.GetClassNameW(hwnd, buf, 32)
        if buf.value == "tooltips_class32" and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                text = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, text, length + 1)
                if text.value.strip():
                    found.append(text.value.strip())
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else None


def _find_tray_icon(label):
    """文字の無いタスクトレイのアイコン (OpenVPN Connectなど) を、アイコン1つぶんずつマウスでなぞって
    ツールチップを読み取りながら探す。OCRでは見つからないアイコンだけの判断材料になる。
    見つかった画面上の座標 (x, y) を返す。見つからなければ None"""
    import ctypes
    from ctypes import wintypes
    rects = _tray_scan_rects()
    if not rects:
        return None
    label_norm = re.sub(r"[\s　]", "", label).lower()
    user32 = ctypes.windll.user32
    origin = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(origin))
    found = None
    with _dpi_aware():
        for left, top, right, bottom in rects:
            y = (top + bottom) // 2
            x = left + TRAY_ICON_STEP // 2
            while x < right and found is None:
                user32.SetCursorPos(x, y)
                time.sleep(TRAY_HOVER_WAIT)
                tip = _tooltip_text()
                if tip:
                    tip_norm = re.sub(r"[\s　]", "", tip).lower()
                    if label_norm in tip_norm or difflib.SequenceMatcher(None, label_norm, tip_norm).ratio() >= FUZZY_LABEL_THRESHOLD:
                        found = (x, y)
                x += TRAY_ICON_STEP
        if found is None:
            user32.SetCursorPos(origin.x, origin.y)  # 見つからなかったので、マウスを元の位置に戻す
    return found


def _visible_top_windows():
    """今、タスクバーに表示されるような通常のウィンドウとして画面に見えているハンドルの集合。
    タスクトレイのアイコンをクリックした後、新しく現れたウィンドウを見つけるために使う"""
    import ctypes
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER = -20, 0x00000080, 4
    user32 = ctypes.windll.user32
    found = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER):
            if not (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
                found.add(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return found


def _force_foreground(hwnd):
    """指定したウィンドウを最前面に出す。Windowsは既定で、直前に入力を受け取ったウィンドウ以外からの
    SetForegroundWindow を無視する制限があるため、今の前面ウィンドウのスレッドに一時的に自分を
    結びつけてその許可を借りる (対象ウィンドウ側のスレッドに結びつけても許可は借りられないため、
    以前はこれが原因で focus-window の直後に read-screen をしても、まだ前に居た別のウィンドウ
    (VSCodeなど) が前面として返ってきてしまう不具合があった)。
    それでも無視されることがあるため、Altキーを押して離して入力ロックを一度外してから試す
    (定番の回避策)。タスクトレイのアイコンをクリックしても、アプリの作りによってはウィンドウが
    前面に出てこないことがあるので、その対策としても使う"""
    import ctypes
    SW_RESTORE = 9
    VK_MENU, KEYUP = 0x12, 2
    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = foreground_thread and foreground_thread != current_thread \
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    try:
        if user32.IsIconic(hwnd):  # 最小化中だけ戻す (最大化中のウィンドウに SW_RESTORE すると小さくなってしまう)
            user32.ShowWindow(hwnd, SW_RESTORE)
        if not user32.SetForegroundWindow(hwnd):
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYUP, 0)
            user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, False)


def _maximize(hwnd):
    """ウィンドウを最大化する。小さい/浮いたウィンドウだと画面が重なって読み取りにくいので、
    対象を前面に出すときに大きく広げて read-screen や click-screen を確実にするために使う"""
    import ctypes
    SW_MAXIMIZE = 3
    ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE)


def _focus_window_by_title(keyword):
    """表示中のトップレベルウィンドウを列挙し、タイトルに keyword を含む最初のウィンドウを
    _force_foreground で前面に出す。見つかればそのハンドルを、無ければ None を返す。
    他のウィンドウが重なって read-screen や click-screen が対象を読み取れないときに使う"""
    import ctypes
    user32 = ctypes.windll.user32
    keyword_lower = keyword.lower()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if keyword_lower in buf.value.lower():
                    found.append(hwnd)
        return not found  # 見つかったら列挙を打ち切る (最初の1件でよい)

    user32.EnumWindows(_callback, 0)
    if found:
        _force_foreground(found[0])
        return found[0]
    return None


def focus_window(text):
    """タイトルに指定した文字を含むウィンドウを探して前面に出す。
    複数のウィンドウが重なっていて read-screen や click-screen が対象をうまく
    読み取れないときに、まずこれで対象(ブラウザなど)を前面に出してから読み直すために使う"""
    keyword = text.strip(" 「」\"'")
    if not keyword:
        return "どのウィンドウを前面に出すか、名前を教えてください。", "■ 画面操作\nfocus-window: 指定がありません"
    hwnd = _focus_window_by_title(keyword)
    if hwnd:
        _maximize(hwnd)  # 小さいウィンドウだと読み取りにくいので最大化して広げる
        say = f"{keyword}のウィンドウを前面に出して最大化しました。"
        return say, f"■ 画面操作\n{say}"
    return (f"「{keyword}」というウィンドウが見つかりませんでした。",
            f"■ 画面操作\n「{keyword}」のウィンドウが見つかりません")


BROWSER_MARKERS = ("edge", "chrome", "firefox", "brave", "opera", "vivaldi")  # タイトルでブラウザのウィンドウを見分ける語
TAB_SWITCH_WAIT = 0.35  # タブを切り替えてから、ウィンドウのタイトルが新しいページ名に変わるまで待つ時間
MAX_TAB_CYCLES = 25     # 無限ループ防止。この回数ぶん切り替えても見つからなければあきらめる


def _window_title(hwnd):
    """ウィンドウのタイトルを返す。ブラウザのタイトルは今アクティブなタブのページ名になるので、
    どのタブが開いているかの判断に使う"""
    import ctypes
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _find_browser_window():
    """表示中のトップレベルウィンドウから、タイトルでブラウザと分かる最初のウィンドウを返す。
    無ければ None"""
    import ctypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = _window_title(hwnd).lower()
            if title and any(b in title for b in BROWSER_MARKERS):
                found.append(hwnd)
        return not found  # 最初の1件が見つかったら列挙を打ち切る

    user32.EnumWindows(_callback, 0)
    return found[0] if found else None


def _press_ctrl_tab():
    """ブラウザで次のタブへ移る (Ctrlを押しながらTab)"""
    import ctypes
    VK_CONTROL, VK_TAB, KEYUP = 0x11, 0x09, 2
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_TAB, 0, 0, 0)
    user32.keybd_event(VK_TAB, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)


def switch_tab(text):
    """ブラウザのタブを、ページ名(タイトル)に含まれる文字で指定して切り替える。
    タブごとに別のページ (例「Index of /development」) を開いていて、目的のタブを前面に
    出したいときに使う。focus-window はウィンドウ単位でタブは切り替えられないので、
    タブを選ぶときはこちらを使う。
    仕組み: ブラウザを前面に出し、今のタブのページ名が目的の文字を含むまで Ctrl+Tab で順に切り替える。
    使い方: switch-tab "Index of development" """
    keyword = re.sub(r"[\s　/]", "", text.strip(" 「」\"'")).lower()
    if not keyword:
        return "どのタブを開くか、ページ名を教えてください。", "■ 画面操作\nswitch-tab: 指定がありません"
    hwnd = _find_browser_window()
    if not hwnd:
        return "ブラウザのウィンドウが見つかりませんでした。", "■ 画面操作\nswitch-tab: ブラウザが見つかりません"
    _force_foreground(hwnd)
    _maximize(hwnd)  # 小さい/浮いたウィンドウだと読み取りにくいので最大化して広げる
    time.sleep(TAB_SWITCH_WAIT)
    # 同じタイトルのタブが複数あることがあるため、タイトルの重複では打ち切らず、
    # 一定回数まで素直に Ctrl+Tab で巡回して目的のタブを探す
    for _ in range(MAX_TAB_CYCLES):
        title = _window_title(hwnd)
        norm = re.sub(r"[\s　/]", "", title).lower()
        if keyword in norm:  # 目的のページ名を含むタブが今開いている
            _log_automation(f"タブを切り替え: {title}")
            say = f"「{text.strip()}」のタブを開きました。"
            return say, f"■ 画面操作\n{say}"
        _press_ctrl_tab()
        time.sleep(TAB_SWITCH_WAIT)
    return (f"「{text.strip()}」というタブが見つかりませんでした。ページ名の一部で指定してみてください。",
            f"■ 画面操作\n「{text.strip()}」のタブが見つかりません")


LAST_CLICK_FILE = Path(tempfile.gettempdir()) / "jarvis_last_click.json"
LAST_CLICK_MAX_AGE = 600  # これより前 (秒) のクリック位置は、別の作業のものとみなして使わない


def _load_last_click_y():
    """直前にクリックした場所のy座標をファイルから読む。コマンドは1回ごとに別のプロセスで動くので、
    変数だけだと次のコマンドに引き継がれず「直前の行」が常に分からなかったため、ファイルに残す"""
    try:
        data = json.loads(LAST_CLICK_FILE.read_text(encoding="utf-8"))
        return data["y"] if time.time() - data["time"] <= LAST_CLICK_MAX_AGE else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _remember_click_y(y):
    """クリックした場所のy座標を、次のコマンドから使えるようにファイルに残す。y をそのまま返す"""
    try:
        LAST_CLICK_FILE.write_text(json.dumps({"y": y, "time": time.time()}), encoding="utf-8")
    except OSError:
        pass
    return y


_LAST_CLICK_Y = _load_last_click_y()  # 直前にクリックした場所のy座標。「Check」ボタンのように
# 同じ文字の候補が縦に並ぶ画面で、直前に操作した行に対応する候補を選ぶ目印に使う
SAME_ROW_Y_TOLERANCE = 15  # 直前のクリック位置と「同じ行」とみなすy座標の差の上限(ピクセル)
CLICK_FOCUS_WAIT = 0.25  # クリック直前に対象ウィンドウを前面に出したあと、前面化が効くのを待つ秒数

LAST_FOCUS_FILE = Path(tempfile.gettempdir()) / "jarvis_last_focus_window.json"
LAST_FOCUS_MAX_AGE = 30  # これより前 (秒) のクリックは、間が空きすぎて別の作業とみなし、前面に戻さない


def _remember_click_window(hwnd):
    """直前のクリックで操作した対象のウィンドウを覚えておく。クリックとキー入力は別々のコマンド
    (別プロセス) で実行されるため、クリックの直後に VSCode の Copilot などが前面を奪っても、
    キー入力の直前にこの窓を前面に戻せるようにする"""
    try:
        LAST_FOCUS_FILE.write_text(json.dumps({"hwnd": int(hwnd), "time": time.time()}), encoding="utf-8")
    except OSError:
        pass


def _restore_click_window_foreground():
    """直前にクリックした窓が今も前面ならそのまま、そうでなければ前面に戻す。
    ブラウザの埋め込みターミナル (AttackBoxなど) をクリックした直後に、VSCodeのCopilotなど
    別のアプリが前面を奪っていると、キーボード入力がそちらに届いてしまい何も反応しないため、
    type_text / press_keys の直前に呼んで対象の窓を確実に前面へ戻す"""
    import ctypes
    try:
        data = json.loads(LAST_FOCUS_FILE.read_text(encoding="utf-8"))
        if time.time() - data["time"] > LAST_FOCUS_MAX_AGE:
            return
        hwnd = data["hwnd"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    if not hwnd or not user32.IsWindow(hwnd):
        return
    if user32.GetForegroundWindow() != hwnd:
        _force_foreground(hwnd)
        time.sleep(CLICK_FOCUS_WAIT)


def _raise_target_before_click(x, y, label):
    """クリックしたい座標に乗っているウィンドウを前面に出し、その点が本当に見えている
    (VSCode やメモ帳など別のウィンドウに隠れていない) ことを確かめる。
    OCRは画面全体から文字を探すため、対象(ブラウザ)が別の窓に隠れた点をそのまま押すと
    誤って別のアプリをクリックしてしまう (ラボマシンの起動ボタンが押せなかった件の原因)。
    返り値 (押してよいか, 今その点にあるウィンドウのタイトル)。押してよくないときは誤クリックせず中止する"""
    with _dpi_aware():
        owner = _root_window_at(x, y)
    owner_title = _window_title(owner) if owner else ""
    if not owner or owner_title in KEEP_WINDOW_TITLES:
        # 対象が分からない、または JARVIS 自身の上 → むやみに押さない
        return (owner is None), owner_title
    _force_foreground(owner)  # そのボタンが乗っているウィンドウを前面へ (背後に回っていても引き上げる)
    time.sleep(CLICK_FOCUS_WAIT)
    with _dpi_aware():
        now = _root_window_at(x, y)
    if now == owner:  # その点が対象ウィンドウで見えている = 安全に押せる
        _remember_click_window(owner)  # 続くキー入力のコマンドが、この窓を前面に戻せるようにする
        return True, owner_title
    return False, (_window_title(now) if now else "")


def click_screen(text):
    """画面上のボタンなどをクリックする。「120,340」のような座標か、
    「Start AttackBoxを押して」のようなボタンの文字で指定する。
    「development の近くの入力欄」のように「〜の近くの入力欄」と指定すると、
    ラベルの無い入力欄を手がかりの文字の近くの空白領域から探してクリックする。
    クリックする前に、どこを押すかをjarvis.logに記録する。同じ文字が複数見つかったときは、
    直前にクリックした場所と同じ行(y座標が近い)候補があればそれをクリックし、
    無ければ誤クリックを避けるためクリックせずに知らせる。
    画面の文字だけでは見つからないとき (タスクトレイの文字の無いアイコンなど) は、
    「隠れているアイコンを表示」を自動で開いたうえでツールチップの名前からも探し、
    見つけてクリックした後はアプリのウィンドウを前面に出す"""
    import ctypes
    global _LAST_CLICK_Y
    tray_point = False  # タスクトレイのアイコンを探した結果 (False=探していない, None=見つからなかった)
    same_row = False  # 複数候補の中から直前の行の近さで選んだかどうか
    m = re.match(r"\s*(\d+)\s*[,、]\s*(\d+)\s*$", text)
    near = re.match(r"^\s*(.+?)\s*の近くの入力欄", text)
    if m:
        x, y, label = int(m.group(1)), int(m.group(2)), None
    elif near:
        hint = near.group(1).strip(" 「」\"'")
        point = _find_input_near(hint)
        if point is None:
            return (f"「{hint}」の近くの入力欄が見つかりませんでした。もっと詳しい文字で指定してください。",
                    f"■ 画面操作\n「{hint}」の近くの入力欄が見つかりません")
        x, y, label = point[0], point[1], None
        # 他のクリックと同じ理由で、押す直前に対象が隠れていないか確認する
        # (別のウィンドウが前面に出ている間はクリックがそちらに吸われ、何も届かないため)
        ok, where_now = _raise_target_before_click(x, y, hint)
        if not ok:
            hidden = f"「{where_now}」に隠れています" if where_now else "対象のウィンドウを前面に出せませんでした"
            return (f"「{hint}」の近くの入力欄がほかのウィンドウに隠れていて、確実には押せませんでした。"
                    "対象の画面を前面に出してから、もう一度お願いします。",
                    f"■ 画面操作\nクリック中止 (誤クリック防止): {hidden}")
        _log_automation(f"({x}, {y}) をクリック 「{hint}」の近くの入力欄")
        _LAST_CLICK_Y = _remember_click_y(y)
        with _dpi_aware():
            ctypes.windll.user32.SetCursorPos(x, y)
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        say = f"「{hint}」の近くの入力欄をクリックしました。"
        return say, f"■ 画面操作\n{say}"
    else:
        label = re.sub(r"(を)?(押して|クリックして|タップして|選んで)(ください)?", "", text).strip(" 、。")
        if not label:
            return "どこをクリックするか、座標かボタンの文字を教えてください。", "■ 画面操作\n指定がありません"
        matches = _find_label(label)
        if not matches:  # 画面の文字で見つからないときは、タスクトレイの文字の無いアイコンかもしれないので探す
            tray_point = _find_tray_icon(label)
            if tray_point is None and _open_hidden_tray_icons():  # 「^」の中に隠れているかもしれない
                tray_point = _find_tray_icon(label)
        if not matches and not tray_point:
            return f"「{label}」が画面に見つかりませんでした。", f"■ 画面操作\n「{label}」が見つかりません"
        if tray_point:
            x, y, fuzzy = tray_point[0], tray_point[1], False
        else:
            if len(matches) > 1:  # 前面のウィンドウの中に1つに絞れるなら、それが操作中の画面のボタン
                in_front = _in_foreground_window(matches)
                if in_front:
                    matches = in_front
            if len(matches) > 1:
                closest = min(matches, key=lambda mt: abs(mt[1] - _LAST_CLICK_Y)) if _LAST_CLICK_Y is not None else None
                if closest is None or abs(closest[1] - _LAST_CLICK_Y) > SAME_ROW_Y_TOLERANCE:
                    return (f"「{label}」が画面に{len(matches)}箇所見つかったので、誤クリックを避けるためクリックしませんでした。",
                            f"■ 画面操作\n「{label}」が{len(matches)}箇所見つかりました: {matches}\n"
                            "誤操作防止のためクリックを中止しました。もっと詳しい文字で指定してください")
                x, y, fuzzy = closest
                same_row = True
            else:
                x, y, fuzzy = matches[0]
    if label and not tray_point:  # ボタンの文字で探したときは、押す直前に対象を前面に出して隠れていないか確認する
        ok, where_now = _raise_target_before_click(x, y, label)
        if not ok:
            hidden = f"「{where_now}」に隠れています" if where_now else "対象のウィンドウを前面に出せませんでした"
            return (f"「{label}」のボタンがほかのウィンドウに隠れていて、確実には押せませんでした。"
                    "対象の画面を前面に出してから、もう一度お願いします。",
                    f"■ 画面操作\nクリック中止 (誤クリック防止): {hidden}")
    _log_automation(f"({x}, {y}) をクリック" + (f" 「{label}」" if label else "")
                     + ("(あいまい一致)" if label and fuzzy else "") + ("(タスクトレイ)" if tray_point else "")
                     + ("(直前の行に近い候補を選択)" if same_row else ""))
    _LAST_CLICK_Y = _remember_click_y(y)
    before = _visible_top_windows() if tray_point else None
    with _dpi_aware():  # SetCursorPos も物理ピクセルで扱わせ、OCRの座標と一致させる
        ctypes.windll.user32.SetCursorPos(x, y)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        owner = _root_window_at(x, y)
    if owner:  # 座標指定のクリック (文字では探していない) でも、続くキー入力がこの窓を前面に戻せるようにする
        _remember_click_window(owner)
    if tray_point:  # タスクトレイのアイコンのクリックは、アプリのウィンドウが前面に出ないことがあるので手助けする
        time.sleep(TRAY_ACTIVATE_WAIT)
        appeared = _visible_top_windows() - before
        if appeared:
            _force_foreground(next(iter(appeared)))
    say = f"{label}をクリックしました。" if label else f"座標 {x}, {y} をクリックしました。"
    return say, f"■ 画面操作\n{say}"


NEAR_CLICK_DIRECTIONS = {"右": (1, 0), "左": (-1, 0), "下": (0, 1), "上": (0, -1)}
NEAR_CLICK_GAP_PX = 45  # ラベルの端から、押したい入力欄までのおおよその隙間


def click_near(text):
    """指定した文字 (質問文など) を画面で探し、その右/下/左/上の少し離れた位置をクリックする。
    空の入力欄のように文字が無くOCRで直接探せない欄を、近くの文字を手がかりに押すために使う。
    使い方: click-near "質問文の一部|下|60"  (| 区切りで 方向=右/下/左/上 既定は下、距離px は省略可)"""
    import ctypes
    global _LAST_CLICK_Y
    parts = [p.strip() for p in text.split("|")]
    label = parts[0] if parts else ""
    direction = parts[1] if len(parts) > 1 and parts[1] in NEAR_CLICK_DIRECTIONS else "下"
    try:
        gap = int(parts[2]) if len(parts) > 2 and parts[2] else NEAR_CLICK_GAP_PX
    except ValueError:
        gap = NEAR_CLICK_GAP_PX
    if not label:
        return "どの文字の近くを押すか教えてください。", "■ 画面操作\nclick-near: 文字の指定がありません"
    rects = _label_rects(label)
    if not rects:
        return f"画面に「{label}」が見つかりませんでした。", f"■ 画面操作\n「{label}」が見つかりません"
    if len(rects) > 1:  # 手がかりの文字自体が複数あると、どの欄の近くか決められない
        return (f"「{label}」が画面に{len(rects)}か所あって、どこの近くか決められませんでした。もっと長めの文字で指定してください。",
                f"■ 画面操作\n「{label}」が{len(rects)}か所見つかりました。もっと詳しい文字で指定してください")
    left, top, right, bottom, _ = rects[0]
    cx, cy = (left + right) // 2, (top + bottom) // 2
    dx, dy = NEAR_CLICK_DIRECTIONS[direction]
    if dx:  # 右/左: ラベルの端から gap だけ横へ
        x, y = (right + gap if dx > 0 else left - gap), cy
    else:   # 下/上: ラベルの端から gap だけ縦へ
        x, y = cx, (bottom + gap if dy > 0 else top - gap)
    # click_screen と同じ理由で、押す直前に対象を前面に出して隠れていないか確認する。
    # ここが無いと、別のウィンドウ (VSCode など) が前面に出ている間はクリックがそちらに吸われ、
    # 本来押したい画面 (ブラウザの埋め込みターミナルなど) には何も届かない
    ok, where_now = _raise_target_before_click(x, y, label)
    if not ok:
        hidden = f"「{where_now}」に隠れています" if where_now else "対象のウィンドウを前面に出せませんでした"
        return (f"「{label}」の{direction}の位置がほかのウィンドウに隠れていて、確実には押せませんでした。"
                "対象の画面を前面に出してから、もう一度お願いします。",
                f"■ 画面操作\nクリック中止 (誤クリック防止): {hidden}")
    _log_automation(f"({x}, {y}) をクリック 「{label}」の{direction}")
    _LAST_CLICK_Y = _remember_click_y(y)
    with _dpi_aware():  # OCRの座標と原点をそろえる (click_screen と同じ理由)
        ctypes.windll.user32.SetCursorPos(x, y)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    return (f"「{label}」の{direction}の位置をクリックしました。",
            f"■ 画面操作\n「{label}」の{direction} ({x}, {y}) をクリックしました")


def _send_unicode_char(ch, keyup=False):
    """1文字をキーボード入力として送る (Unicode対応。日本語もそのまま送れる)"""
    import ctypes

    class _KeyInput(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_uint),
                   ("time", ctypes.c_uint), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _MouseInput(ctypes.Structure):  # 使わないが、INPUT の大きさを Windows の定義 (64bitで40バイト) に合わせるのに要る
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_uint),
                   ("dwFlags", ctypes.c_uint), ("time", ctypes.c_uint), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", _KeyInput), ("mi", _MouseInput)]

    class _Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_uint), ("union", _InputUnion)]

    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = _Input(type=1, union=_InputUnion(ki=_KeyInput(0, ord(ch), flags, 0, None)))
    # 大きさが違うと Windows は何も入力せず 0 を返す (以前はこれで1文字も入らないのに成功と報告していた)
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_Input)) == 1


def _send_scancode(scan, keyup=False, extended=False):
    """スキャンコード (実際のキーボードの物理キー番号) で1キーを送る。
    ブラウザ内のリモート画面 (noVNC の AttackBox など) は、Unicode のキー入力や貼り付けを
    受け取らず、物理キーの押下イベントだけを拾う。そこに届けるために使う"""
    import ctypes

    class _KeyInput(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_uint),
                   ("time", ctypes.c_uint), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _MouseInput(ctypes.Structure):  # INPUT の大きさ (64bitで40バイト) を合わせるためだけに持つ
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_uint),
                   ("dwFlags", ctypes.c_uint), ("time", ctypes.c_uint), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", _KeyInput), ("mi", _MouseInput)]

    class _Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_uint), ("union", _InputUnion)]

    KEYEVENTF_SCANCODE, KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY = 0x0008, 0x0002, 0x0001
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    inp = _Input(type=1, union=_InputUnion(ki=_KeyInput(0, scan, flags, 0, None)))
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_Input)) == 1


SHIFT_SCAN, ENTER_SCAN = 0x2A, 0x1C  # Shift と Enter の物理キー番号


def _ime_off():
    """前面のウィンドウの日本語IME (かな漢字変換) を一時的にオフ (直接入力) にする。
    IMEがオンだと、ssh:// が ｓｓｈ：・・ のように全角に変換されてコマンドが壊れるため、
    リモート端末などに打ち込む前に直接入力へ切り替える。切り替えられたら True"""
    import ctypes
    user32, imm32 = ctypes.windll.user32, ctypes.windll.imm32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    imm32.ImmGetContext.restype = ctypes.c_void_p
    imm32.ImmGetDefaultIMEWnd.restype = ctypes.c_void_p
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    # まず入力スレッド宛に「IMEを閉じる」を通知する (ブラウザなど、既定のIMEウィンドウ経由の作りに効く)
    ime_wnd = imm32.ImmGetDefaultIMEWnd(ctypes.c_void_p(hwnd))
    WM_IME_CONTROL, IMC_SETOPENSTATUS = 0x0283, 0x0006
    if ime_wnd:
        user32.SendMessageW(ctypes.c_void_p(ime_wnd), WM_IME_CONTROL, IMC_SETOPENSTATUS, 0)
    himc = imm32.ImmGetContext(ctypes.c_void_p(hwnd))
    if himc:
        imm32.ImmSetOpenStatus(ctypes.c_void_p(himc), False)  # 直接入力 (IMEオフ) にする
        imm32.ImmReleaseContext(ctypes.c_void_p(hwnd), ctypes.c_void_p(himc))
    return True


def _type_scancodes(text, char_delay=0.03):
    """文字列を、実機のキーボードのように1キーずつスキャンコードで送る。
    リモート画面 (AttackBox の端末など) に確実に届けるためのもの。
    今のキーボード配列で打てない文字 (日本語など) は Unicode 入力で補う。
    (入力できなかった文字数, 最初に失敗した文字) を返す"""
    import ctypes
    user32 = ctypes.windll.user32
    _ime_off()  # 全角変換を防ぐため、打ち始める前にIMEを直接入力にする
    time.sleep(0.05)
    failed, first_bad = 0, None
    for ch in text:
        if ch == "\n":
            _send_scancode(ENTER_SCAN); _send_scancode(ENTER_SCAN, keyup=True)
            time.sleep(char_delay)
            continue
        vk_shift = user32.VkKeyScanW(ord(ch))  # 下位8bit=仮想キー, 上位8bit=修飾(1=Shift,2=Ctrl,4=Alt)
        vk, mods = vk_shift & 0xFF, (vk_shift >> 8) & 0xFF
        scan = user32.MapVirtualKeyW(vk, 0) if vk_shift != -1 else 0  # MAPVK_VK_TO_VSC
        if vk_shift == -1 or scan == 0 or mods & 0x06:  # 打てない or Ctrl/Alt が要る文字は Unicode で補う
            if not _send_unicode_char(ch):
                failed += 1
                first_bad = first_bad or ch
            else:
                _send_unicode_char(ch, keyup=True)
            time.sleep(char_delay)
            continue
        shift = bool(mods & 0x01)
        if shift:
            _send_scancode(SHIFT_SCAN)
        _send_scancode(scan)
        _send_scancode(scan, keyup=True)
        if shift:
            _send_scancode(SHIFT_SCAN, keyup=True)
        time.sleep(char_delay)  # リモートが取りこぼさないよう、1キーごとに少し待つ
    return failed, first_bad


def _parse_type_flags(text):
    """type-text / click-type の先頭フラグ (--replace / --scan / --keys) を読み取り、
    (本文, replace, keys_only, scan_mode) を返す。type_text と click_type で共通に使う"""
    replace = keys_only = scan_mode = False
    while text.startswith(("--replace", "--keys", "--scan")):
        if text.startswith("--replace"):
            replace, text = True, text[len("--replace"):].strip()
        elif text.startswith("--scan"):
            scan_mode, text = True, text[len("--scan"):].strip()
        else:
            keys_only, text = True, text[len("--keys"):].strip()
    return text, replace, keys_only, scan_mode


def _perform_type(text, replace=False, keys_only=False, scan_mode=False):
    """実際に文字を入力する中核部分。入力欄のフォーカス (カーソル) が生きている前提で、
    全選択の置き換え・貼り付け・1キー送りを行う。前面への復帰は呼び出し側の責任:
    type_text はクリックと別プロセスなので事前に前面へ戻してから呼び、
    click_type は同じプロセスでクリック直後に呼ぶので戻さない (戻すとカーソルが外れるため)"""
    import ctypes
    text = text.replace("\\n", "\n")  # 「コマンド\n」で改行 (実行) まで送れるようにする
    if not text:
        return "入力する文字がありません。", "■ キーボード入力\n文字がありません"
    if replace:  # ctrl+a で欄の中身を全選択し、入力した文字で上書きする
        user32 = ctypes.windll.user32
        user32.keybd_event(0x11, 0, 0, 0)
        user32.keybd_event(ord("A"), 0, 0, 0)
        user32.keybd_event(ord("A"), 0, 2, 0)
        user32.keybd_event(0x11, 0, 2, 0)
        time.sleep(0.05)
    shown = text.replace("\n", "⏎")
    _log_automation(f"キーボード入力{'(置き換え)' if replace else ''}{'(スキャンコード)' if scan_mode else ''}: {shown[:50]}")
    if scan_mode:  # リモート端末向け: 実機のキーのように1キーずつ送る
        failed, bad = _type_scancodes(text)
        if failed:
            return (f"一部の文字 (「{bad}」など{failed}文字) を送れませんでした。",
                    f"■ キーボード入力\n{shown}\n(うち{failed}文字が送れませんでした)")
        return f"{shown}と入力しました。", f"■ キーボード入力\n{shown}"
    if keys_only:  # 貼り付けを受け付けない画面向け (古い方式): 1文字ずつ Unicode のキーとして送る
        for ch in text:
            if ch == "\n":
                _press(VK_RETURN)
            elif not _send_unicode_char(ch):
                return ("キーボード入力をWindowsに受け付けてもらえず、文字を入力できませんでした。",
                        "■ キーボード入力\n入力に失敗しました (SendInput が拒否)")
            else:
                _send_unicode_char(ch, keyup=True)
            time.sleep(0.01)
        return f"{shown}と入力しました。", f"■ キーボード入力\n{shown}"
    if not _paste_text(text):
        return "クリップボードが使えず、文字を入力できませんでした。", "■ キーボード入力\nクリップボードを開けませんでした"
    return f"{text}と入力しました。", f"■ キーボード入力\n{text}"


def type_text(text):
    """今キーボードの入力を受け付けている場所 (クリックして選んだ入力欄など) に文字を入力する。
    クリップボード経由の貼り付けで入れる (日本語も可)。改行はEnterキーとして送る。
    先頭に「--replace」を付けると、欄の中身を全選択 (ctrl+a) してから入力し、前の文字を置き換える
    (入力し直すときに、前の文字の後ろに足されて答えが壊れるのを防ぐ。端末では使わない)。
    先頭に「--scan」を付けると、実機のキーボードのように1キーずつスキャンコードで送る。
    ブラウザ内のリモート端末 (AttackBox の黒い端末画面など) は貼り付けも Unicode 入力も効かないので、
    そこにコマンドを打つときはこれを使う。改行まで入れればコマンドが実行される。
    先頭に「--keys」を付けると、貼り付けでなく1文字ずつ Unicode のキーとして送る (古い方式)。
    クリックした入力欄に文字が入らないときは、クリックと入力を1回でまとめて行う click-type を使う"""
    text, replace, keys_only, scan_mode = _parse_type_flags(text)
    # クリックした後に別のアプリ (VSCodeのCopilotなど) が前面を奪っていたら、入力の前に戻す
    # (クリックした場所にキーが届かず、何も反応しない不具合の対策)
    _restore_click_window_foreground()
    return _perform_type(text, replace, keys_only, scan_mode)


def click_type(text):
    """入力欄をクリックしてフォーカス (カーソル) を入れ、そのまま続けて文字を入力する、を1回で行う。
    クリック (click-screen / click-near) と入力 (type-text) を別々のコマンドで実行すると、
    その2つは別プロセスで動くため、間に別のアプリが前面を奪ったり、ブラウザの入力欄の
    カーソルが外れたりして、クリックしたのに文字が入らないことがある。
    これは同じプロセスの中でクリックの直後に打つので、クリックで入れたカーソルが生きている
    うちに確実に入力できる (TryHackMe の答え欄などで、入力が入らないときに使う)。
    使い方: click-type "<クリックの指定> >> <入力する文字>"
      クリックの指定は click-screen / click-near と同じ:
        "4044,532"                    座標を直接指定
        "Check|左|150"                その文字の左150pxの入力欄 (click-near と同じ | 区切り。方向と距離px)
        "development の近くの入力欄"   ラベルの無い入力欄を近くの文字から探す
        "送信"                        ボタンなど、欄の中に文字がある場合はその文字
      入力する文字の先頭には type-text と同じ --replace / --scan / --keys を付けられる
      (答え欄に入れ直すときは --replace を推奨)。
    例: click-type "Check|左|150 >> --replace 8881" """
    parts = re.split(r"\s*>>\s*", text, maxsplit=1)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        return ("click-type は「クリックの指定 >> 入力する文字」の形で指定してください。"
                ' 例: click-type "Check|左|150 >> --replace 8881"',
                "■ 画面操作\nclick-type: 指定の形式が正しくありません (「指定 >> 文字」の形)")
    target, body = parts[0].strip(), parts[1].strip()
    # click-near の | 区切り (方向/距離) があれば click_near、無ければ click_screen を使う。
    # どちらも対象ウィンドウを前面に出してクリックし、カーソルを入力欄に置く
    say, show = click_near(target) if "|" in target else click_screen(target)
    if "クリックしました" not in say:  # クリックに失敗したら入力しない (誤った場所への誤入力を防ぐ)
        return say, show
    time.sleep(0.15)  # クリックでフォーカスが入るのを少し待つ (前面復帰はしない: すでに対象が前面にある)
    body_text, replace, keys_only, scan_mode = _parse_type_flags(body)
    type_say, type_show = _perform_type(body_text, replace, keys_only, scan_mode)
    return f"{say} {type_say}", f"{show}\n{type_show}"


def _set_clipboard(text):
    """クリップボードに文字を入れる。他のアプリが一瞬使っていて開けないことがあるので数回試す"""
    import win32clipboard
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
        except Exception:  # noqa: BLE001 (pywin32 の error。他のアプリが開いている間は少し待って再試行)
            time.sleep(0.05)
            continue
        try:
            win32clipboard.EmptyClipboard()
            if text is not None:
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            return True
        finally:
            win32clipboard.CloseClipboard()
    return False


def _get_clipboard_text():
    """クリップボードの文字を返す (文字以外や空なら None)。貼り付けの後に元に戻すために使う"""
    import win32clipboard
    try:
        win32clipboard.OpenClipboard()
    except Exception:  # noqa: BLE001
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        return None
    finally:
        win32clipboard.CloseClipboard()


def _paste_text(text):
    """文字をクリップボード経由で ctrl+v で貼り付ける。ブラウザ (Edge) は1文字ずつ送る Unicode の
    キー入力を受け取らないことがあり、答えの欄に何も入らなかったため、確実に届く ctrl+v を使う。
    改行は Enter キーとして送る。ユーザーのクリップボードの文字は貼り付け後に元に戻す"""
    import ctypes
    user32 = ctypes.windll.user32
    saved = _get_clipboard_text()
    try:
        for i, line in enumerate(text.split("\n")):
            if i:
                _press(VK_RETURN)
            if not line:
                continue
            if not _set_clipboard(line):
                return False
            user32.keybd_event(0x11, 0, 0, 0)  # ctrl+v
            user32.keybd_event(ord("V"), 0, 0, 0)
            user32.keybd_event(ord("V"), 0, 2, 0)
            user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.25)  # 貼り付けが終わる前にクリップボードを戻すと、元の文字が貼られてしまう
        return True
    finally:
        _set_clipboard(saved)


_MODIFIER_VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
                "win": 0x5B, "cmd": 0x5B, "meta": 0x5B}
_KEY_VK = {"esc": 0x1B, "escape": 0x1B, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
           "space": 0x20, "backspace": 0x08, "back": 0x08, "delete": 0x2E, "del": 0x2E,
           "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
           "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
           "-": 0xBD, "+": 0xBB, "=": 0xBB}  # VK_OEM_MINUS/PLUS (ブラウザの ctrl+- / ctrl+= ズーム用)
# スキャンコードで送るとき、拡張キー(E0)として送る必要がある仮想キー (矢印・Home/End類・Win・Insert/Delete)。
# これらは拡張の印を付けないと、テンキー側の別のキーとして届いてしまう
EXTENDED_VKS = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B, 0x5C, 0x5D, 0xA3, 0xA5}


def _key_to_vk(name):
    """キー名を Windows の仮想キーcoード(VK)に変換する。分からなければ None"""
    name = name.strip().lower()
    if name in _KEY_VK:
        return _KEY_VK[name]
    m = re.fullmatch(r"f([1-9]|1[0-2])", name)  # F1〜F12
    if m:
        return 0x70 + int(m.group(1)) - 1
    if len(name) == 1 and (name.isalpha() or name.isdigit()):
        return ord(name.upper())
    return None


def press_keys(text):
    """キーボードのショートカットを送る。「ctrl+w」(タブを閉じる)、「ctrl+shift+w」(ウィンドウを閉じる)、
    「alt+f4」「ctrl+l」「f5」「esc」「ctrl+-」「ctrl+=」(ブラウザのズーム)「ctrl+0」(ズームを戻す)のように、
    修飾キー(ctrl/alt/shift/win)と組み合わせて指定する。
    文字をそのまま打つ type-text と違い、Ctrl や Alt を押しながらの操作に使う。
    使い方: press-keys "ctrl+w" """
    import ctypes
    combo = text.strip().strip(" 「」\"'")
    parts = [p for p in re.split(r"\s*\+\s*", combo) if p]
    if not parts:
        return "どのキーを押すか教えてください。例: ctrl+w", "■ キーボード操作\nキーの指定がありません"
    *mods, key = parts
    mod_vks = []
    for mod in mods:
        vk = _MODIFIER_VK.get(mod.strip().lower())
        if vk is None:
            return (f"「{mod}」という修飾キーは分かりませんでした。ctrl / alt / shift / win が使えます。",
                    f"■ キーボード操作\n不明な修飾キー: {mod}")
        mod_vks.append(vk)
    key_vk = _key_to_vk(key)
    if key_vk is None:
        return (f"「{key}」というキーは分かりませんでした。",
                f"■ キーボード操作\n不明なキー: {key}")
    # クリックした後に別のアプリ (VSCodeのCopilotなど) が前面を奪っていたら、入力の前に戻す
    _restore_click_window_foreground()
    user32 = ctypes.windll.user32
    _log_automation(f"キーボード: {combo}")
    # VKのままの keybd_event だと、ブラウザ内のリモート端末 (AttackBoxなど) では修飾キー(Ctrl等)が
    # 伝わらず、ctrl+c が「c」としてしか届かない。実機と同じスキャンコードで送ると修飾キーも届く
    _ime_off()
    seq = [(vk, vk in EXTENDED_VKS) for vk in mod_vks] + [(key_vk, key_vk in EXTENDED_VKS)]
    scans = [(user32.MapVirtualKeyW(vk, 0), ext) for vk, ext in seq]
    if any(scan == 0 for scan, _ in scans):  # スキャンコードに直せないキーは、従来の方式で送る
        for vk in mod_vks:
            user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(key_vk, 0, 0, 0)
        user32.keybd_event(key_vk, 0, 2, 0)
        for vk in reversed(mod_vks):
            user32.keybd_event(vk, 0, 2, 0)
    else:
        for scan, ext in scans:  # 修飾キー→本体キーの順に押す
            _send_scancode(scan, extended=ext)
            time.sleep(0.01)
        for scan, ext in reversed(scans):  # 逆順に離す
            _send_scancode(scan, keyup=True, extended=ext)
            time.sleep(0.01)
    return f"{combo} を押しました。", f"■ キーボード操作\n{combo} を押しました"


def scroll_screen(text):
    """画面を上下にスクロールする (縦長のページで画面外のボタンや文字を表示したいときに使う)。
    「下」「上」で向きを、数字でスクロール量 (ノッチ数、省略時は3) を指定する。例:「下」「上 5」。
    マウスカーソルの位置 (直前にクリックした場所など) でスクロールする。
    「下 1349,600」のように座標を付けると、その位置へ移動してからスクロールする"""
    import ctypes
    coord = None
    m = re.search(r"(\d+)\s*[,、]\s*(\d+)", text)
    if m:  # 座標指定があれば取り出して、残りの文字から向き・量を読む
        coord = (int(m.group(1)), int(m.group(2)))
        text = text[:m.start()] + text[m.end():]
    up = any(w in text for w in ("上", "うえ", "up", "アップ"))
    nm = re.search(r"\d+", text)
    notches = min(int(nm.group()), 20) if nm else 3  # 行き過ぎ防止に上限20ノッチ
    direction = 1 if up else -1  # ホイールは上が正、下が負
    with _dpi_aware():  # 座標指定のとき物理ピクセルでそろえる (click_screen と同じ理由)
        if coord:
            ctypes.windll.user32.SetCursorPos(*coord)
        for _ in range(notches):
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, direction * WHEEL_DELTA, 0)
            time.sleep(0.05)
    where = "上" if up else "下"
    _log_automation(f"画面を{where}に{notches}スクロール" + (f" ({coord[0]}, {coord[1]})" if coord else ""))
    return f"画面を{where}にスクロールしました。", f"■ 画面操作\n画面を{where}に{notches}スクロールしました"


# 散らかった画面の整理 (ウィンドウの整列・デスクトップのアイコンの整列)。
# 閉じる・消すことはせず、並べ方と配置を変えるだけ
KEEP_WINDOW_TITLES = ("JARVIS",)  # 整列や最小化で動かさないウィンドウ (JARVIS自身の画面)
MINIMIZED_RECORD = Path(tempfile.gettempdir()) / "jarvis_minimized_windows.json"  # 「元に戻す」で戻すウィンドウ


def _arrangeable_windows():
    """整列の対象になる、普通に画面に出ているアプリのウィンドウのハンドルの一覧 (前面にあるものから順)。
    最小化中・タイトルの無いもの・裏で隠れている(クローク)ストアアプリの枠・JARVIS自身は除く"""
    import ctypes
    from ctypes import wintypes
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER, DWMWA_CLOAKED = -20, 0x00000080, 4, 14
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CLOAKED,
                                                   ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        title = _window_title(hwnd)
        if not cloaked.value and title.strip() and title not in KEEP_WINDOW_TITLES:
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 64)
            if buf.value not in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                found.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return found


def organize_windows(text=""):
    """開いているウィンドウを整理する。Windows標準の「ウィンドウを並べて表示」「重ねて表示」
    「デスクトップを表示」に相当する。JARVIS自身の画面は動かさない。
    「並べる」(既定、左右に並べる)/「上下」/「重ねる」/「最小化」(デスクトップを表示)/「元に戻す」"""
    import ctypes
    user32 = ctypes.windll.user32
    SW_MINIMIZE, SW_RESTORE = 6, 9
    MDITILE_VERTICAL, MDITILE_HORIZONTAL, MDITILE_SKIPDISABLED = 0, 1, 2
    mode = text.strip(" 「」\"'")
    if any(w in mode for w in ("戻", "restore", "undo")):
        try:
            handles = json.loads(MINIMIZED_RECORD.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            handles = []
        restored = [h for h in handles if user32.IsWindow(ctypes.c_void_p(h)) and user32.IsIconic(ctypes.c_void_p(h))]
        for h in reversed(restored):  # 最小化前の重なり順に近くなるよう、奥のものから戻す
            user32.ShowWindow(ctypes.c_void_p(h), SW_RESTORE)
        MINIMIZED_RECORD.unlink(missing_ok=True)
        if not restored:
            return "元に戻すウィンドウはありませんでした。", "■ 画面の整理\n最小化して戻せるウィンドウがありません"
        say = f"最小化していたウィンドウを{len(restored)}個、元に戻しました。"
        _log_automation(f"ウィンドウを元に戻す: {len(restored)}個")
        return say, f"■ 画面の整理\n{say}"
    windows = _arrangeable_windows()
    if not windows:
        return "整理するウィンドウはありませんでした。", "■ 画面の整理\n開いているウィンドウがありません"
    titles = "\n".join(f"・{_window_title(h)[:40]}" for h in windows)
    if any(w in mode for w in ("最小化", "デスクトップ", "隠", "しまう", "minimize")):
        for h in windows:
            user32.ShowWindow(ctypes.c_void_p(h), SW_MINIMIZE)
        try:  # 「元に戻す」で同じウィンドウだけを戻せるよう覚えておく
            MINIMIZED_RECORD.write_text(json.dumps(windows), encoding="utf-8")
        except OSError:
            pass
        say = f"ウィンドウを{len(windows)}個最小化して、デスクトップを表示しました。"
        _log_automation(f"ウィンドウを最小化: {len(windows)}個")
        return say, f"■ 画面の整理\n{say}\n{titles}"
    kids = (ctypes.c_void_p * len(windows))(*windows)
    with _dpi_aware():  # 物理ピクセルで画面いっぱいに配置させる (DPI仮想化でずれないように)
        if any(w in mode for w in ("重ね", "カスケード", "cascade")):
            done, how = user32.CascadeWindows(None, MDITILE_SKIPDISABLED, None, len(windows), kids), "重ねて"
        elif any(w in mode for w in ("上下", "横長", "horizontal")):
            done, how = user32.TileWindows(None, MDITILE_HORIZONTAL, None, len(windows), kids), "上下に並べて"
        else:
            done, how = user32.TileWindows(None, MDITILE_VERTICAL, None, len(windows), kids), "左右に並べて"
    if not done:
        return "ウィンドウの整列に失敗しました。", "■ 画面の整理\nウィンドウを整列できませんでした"
    say = f"ウィンドウを{len(windows)}個、{how}表示しました。"
    _log_automation(f"ウィンドウを整列({how}): {len(windows)}個")
    return say, f"■ 画面の整理\n{say}\n{titles}"


def _owner_process_name(hwnd):
    """そのウィンドウを持つプロセスの実行ファイル名 (例: pythonw.exe) を返す。取得できなければ空文字
    GetModuleBaseNameWはホスト(JARVIS)とウィンドウ先のプロセスのビット幅(32/64bit)が食い違うと
    取得に失敗するため、ビット幅に影響されないQueryFullProcessImageNameWでフルパスを取得する"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def close_window(text=""):
    """『画面を閉じて』で、JARVIS自身のアプリウィンドウ(表示UI)だけを安全に閉じる。
    タイトルが「JARVIS」と完全に一致し、かつそのウィンドウを持つプロセスが python(w).exe であることまで
    確認してから操作するので、たまたま同じ名前の別アプリなど、無関係なウィンドウを誤って閉じることはない。
    WM_CLOSE を送って通常のウィンドウクローズ操作をするだけで、プロセスを殺すわけではない。
    app.py 側で WM_DELETE_WINDOW を withdraw に差し替えてあるので、ウィンドウは画面・タスクバーから
    消えるが、音声認識ループなどのバックエンドはそのまま動き続け、声でのやりとりは続けられる"""
    import ctypes
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and _window_title(hwnd) == "JARVIS"
                and _owner_process_name(hwnd).lower() in ("python.exe", "pythonw.exe")):
            found.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    if not found:
        return "JARVISの画面が見つかりませんでした。", "■ 画面操作\nJARVISのウィンドウが見つかりません"
    for h in found:
        user32.PostMessageW(ctypes.c_void_p(h), WM_CLOSE, 0, 0)
    _log_automation("JARVISの画面を閉じる")
    say = "画面を閉じました。声では引き続きお話しできますね。"
    return say, f"■ 画面操作\n{say}"


SHOW_WINDOW_FLAG = Path(tempfile.gettempdir()) / "jarvis_show_window.flag"  # show-windowコマンドが立てる、画面を出してほしいしるし


def show_window(text=""):
    """『画面を出して』『画面を表示して』で、close_windowでwithdrawして隠れたJARVISの画面を再表示する。
    close_window と同様に、タイトルが「JARVIS」に完全一致し、かつそのウィンドウを持つプロセスが
    python(w).exe であることまで確認してから操作するので、無関係なウィンドウには触れない
    (withdraw中は非表示になるので、close_windowと違いIsWindowVisibleでは絞り込まない)。
    tkinterのdeiconify/lift/focus_forceはプロセス内APIで、このコマンドを実行する別プロセスからは
    直接呼べないため、しるしファイルを立てて、app.py側の音声認識ループが定期的に確認して画面を出す"""
    import ctypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _):
        if (_window_title(hwnd) == "JARVIS"
                and _owner_process_name(hwnd).lower() in ("python.exe", "pythonw.exe")):
            found.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    if not found:
        return "JARVISの画面が見つかりませんでした。", "■ 画面操作\nJARVISのウィンドウが見つかりません"
    SHOW_WINDOW_FLAG.touch()
    _log_automation("JARVISの画面を表示する要求")
    say = "画面を表示しますね。"
    return say, f"■ 画面操作\n{say}"


def show_window_requested():
    """show-windowコマンド(画面を出して)の要求があったばかりか確認する (確認したらしるしを消す)。
    app.py がこれを定期的に確認して、立っていれば画面をdeiconifyする"""
    if SHOW_WINDOW_FLAG.exists():
        SHOW_WINDOW_FLAG.unlink()
        return True
    return False


def organize_desktop(text=""):
    """デスクトップのアイコンを名前順に並べ替え、等間隔にそろえる (右クリック →「並べ替え → 名前」
    「表示 → 等間隔に整列」と同じ)。ファイルは動かさず、アイコンの表示位置だけを変える。
    「種類」「日付」「サイズ」を指定すると、その順に並べ替える"""
    import pythoncom
    import win32com.client
    FWF_SNAPTOGRID, SWC_DESKTOP, SWFO_NEEDDISPATCH = 0x4, 8, 1
    orders = {"種類": ("prop:System.ItemTypeText;", "種類"), "日付": ("prop:System.DateModified;", "日付"),
              "更新": ("prop:System.DateModified;", "日付"), "サイズ": ("prop:System.Size;", "サイズ"),
              "大きさ": ("prop:System.Size;", "サイズ")}
    sort, label = next((v for k, v in orders.items() if k in text), ("prop:System.ItemNameDisplay;", "名前"))
    pythoncom.CoInitialize()
    try:
        windows = win32com.client.Dispatch("Shell.Application").Windows()
        view = windows.FindWindowSW(0, None, SWC_DESKTOP, 0, SWFO_NEEDDISPATCH).Document
        view.FolderFlags = view.FolderFlags | FWF_SNAPTOGRID  # 等間隔に整列
        if view.SortColumns == sort:  # 同じ並べ替えを指定し直しても動かないので、いったん別の順にしてから戻す
            view.SortColumns = "prop:System.Size;" if label != "サイズ" else "prop:System.ItemNameDisplay;"
        view.SortColumns = sort
    except Exception as e:  # noqa: BLE001 (COMの失敗は種類を問わず、読み上げ用の文で知らせる)
        return "デスクトップのアイコンを整列できませんでした。", f"■ 画面の整理\nデスクトップの整列に失敗: {e}"
    finally:
        pythoncom.CoUninitialize()
    say = f"デスクトップのアイコンを{label}順に整列しました。"
    _log_automation(f"デスクトップのアイコンを{label}順に整列")
    return say, f"■ 画面の整理\n{say}"


# ⑪ GitHub への保存 (git のコミットと Push)
PROJECT_DIR = Path(__file__).parent
RESTART_FLAG = PROJECT_DIR / "restart_pending.flag"  # commit でプログラムが変わったかもしれないしるし (app.py が確認する)


def _git(*args, timeout=60):
    """JARVIS のフォルダで git を実行して (成功したか, 出力) を返す"""
    try:
        result = subprocess.run(["git", *args], cwd=PROJECT_DIR, capture_output=True, encoding="utf-8",
                                errors="replace", timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:  # 成功したときは警告 (改行コードなど) を出さない
            return True, result.stdout.rstrip()
        return False, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def git_changes():
    """まだコミットしていない変更の一覧 (コミットのメッセージを考えるために使う)"""
    ok, status = _git("status", "--short")
    if not ok:
        return "変更を調べられませんでした。", f"■ GitHub\n{status[:300]}"
    if not status:
        return "まだ保存していない変更はありません。", "■ GitHub\n変更なし"
    _, stat = _git("diff", "--stat", "HEAD")
    return (f"保存していない変更が{len(status.splitlines())}件あります。",
            f"■ GitHub (未コミットの変更)\n{status}\n\n{stat}")


def git_commit(message):
    """変更をすべてコミットして GitHub に Push する (.gitignore のファイルは含まれない)"""
    message = message.strip()
    if not message:
        return None
    ok, status = _git("status", "--short")
    if ok and status:
        _git("add", "-A")
        ok, out = _git("commit", "-m", message)
        if not ok:
            return "コミットできませんでした。", f"■ GitHub\nコミット失敗\n{out[:300]}"
        RESTART_FLAG.touch()  # プログラムの変更が含まれているかもしれないので、あとで再起動を確認してもらう
    elif not ok:
        return "変更を調べられませんでした。", f"■ GitHub\n{status[:300]}"
    ok, out = _git("push", timeout=120)
    if not ok:
        return ("コミットはしましたが、GitHubへのPushに失敗しました。ネットの接続を確かめてください。",
                f"■ GitHub\nPush 失敗\n{out[:300]}")
    _, head = _git("log", "--oneline", "-1")
    if not status:
        return "新しい変更はなかったので、まだ送っていないコミットだけをGitHubに送りました。", f"■ GitHub\nPush 済み\n{head}"
    return "変更をコミットして、GitHubにPushしました。", f"■ GitHub\nコミット・Push 済み\n{head}"


def restart_pending():
    """commit でプログラムの変更が保存されたばかりか (確認したらしるしを消す)"""
    if RESTART_FLAG.exists():
        RESTART_FLAG.unlink()
        return True
    return False


# ⑫ 成長記録 (ジャービスが日々できるようになったことの、読んで楽しむ日記)
GROWTH_FILE = PROJECT_DIR / "docs" / "成長記録.md"
GROWTH_MARKER = "<!-- 新しい記録はこの行の上に追記します -->"
GROWTH_HEADER = ("# JARVIS 成長記録\n\n"
                 "ジャービスが日々できるようになったことを、やさしい日記として残していきます。\n"
                 "（技術的な詳しい履歴は GitHub のコミットに残っています。これはその別の、読んで楽しむための記録です）\n\n"
                 "---\n\n")


def _growth_record(text):
    """成長記録に、今日の日付の見出しの下へ 1 行 (箇条書き) を書き足す。同じ内容は重ねない"""
    text = " ".join(text.split()).strip()
    if not text:
        return False
    heading = f"## {time.strftime('%Y-%m-%d')}"
    bullet = f"- {text}"
    GROWTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = GROWTH_FILE.read_text(encoding="utf-8") if GROWTH_FILE.exists() else GROWTH_HEADER
    if GROWTH_MARKER not in content:
        content = content.rstrip() + "\n\n" + GROWTH_MARKER + "\n"
    if heading in content:  # 今日の見出しが既にある → その区切りの末尾に足す
        start = content.index(heading)
        after = content.find("\n## ", start + len(heading))
        end = after if after != -1 else content.index(GROWTH_MARKER)
        section = content[start:end]
        if bullet in section:  # まったく同じ一文は重ねない
            return False
        section = section.rstrip("\n") + "\n" + bullet + "\n\n"
        content = content[:start] + section + content[end:]
    else:  # 今日の見出しを新しく作る
        block = f"{heading}\n\n{bullet}\n\n"
        content = content.replace(GROWTH_MARKER, block + GROWTH_MARKER)
    GROWTH_FILE.write_text(content, encoding="utf-8")
    return True


def growth_add(text):
    """声やチャットから、成長記録に 1 行を書き足す (「成長記録に〜と書いて」)"""
    text = text.strip()
    if not text:
        return "何を記録しましょうか。", "■ 成長記録\n記録する内容がありませんでした。"
    if not _growth_record(text):
        return "それはもう記録してありました。", f"■ 成長記録\n既に同じ内容があります:\n・{text}"
    return "成長記録に書き加えました。", f"■ 成長記録 ({time.strftime('%Y-%m-%d')})\n・{text}"


def growth_log():
    """成長記録を読む (読み上げは最新の日の分だけ、画面には全文を出す)"""
    if not GROWTH_FILE.exists():
        return "まだ成長記録はありません。", "■ 成長記録\nまだ記録がありません。"
    content = GROWTH_FILE.read_text(encoding="utf-8")
    headings = [i for i in range(len(content)) if content.startswith("\n## ", i)]
    if headings:  # 最新 (いちばん下) の日の見出しから、次の見出しか印の手前まで
        start = headings[-1] + 1
        after = content.find("\n## ", start + 3)
        end = after if after != -1 else (content.index(GROWTH_MARKER) if GROWTH_MARKER in content else len(content))
        latest = content[start:end].strip()
        first_line = latest.splitlines()[0].lstrip("# ").strip()
        bullets = [ln[2:].strip() for ln in latest.splitlines() if ln.startswith("- ")]
        say = f"最新の成長記録は {first_line} の分です。" + (f"たとえば、{bullets[0]}。" if bullets else "")
    else:
        say = "成長記録を表示します。"
    show = "■ 成長記録\n" + content.split("---\n\n", 1)[-1].replace(GROWTH_MARKER, "").strip()
    return say, show


# 記憶 (MEMORY.md): AI が話しかけられるたびに読み込む
MEMORY_FILE = Path(__file__).with_name("MEMORY.md")
MEMORY_HEADER = ("# JARVIS の記憶\n\n"
                 "ユーザーについて覚えていること。JARVIS が会話のたびに読み込みます。手で編集してもかまいません。\n\n")


def remember(text):
    text = text.strip()
    if not text:
        return None
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(MEMORY_HEADER, encoding="utf-8")
    current = MEMORY_FILE.read_text(encoding="utf-8")
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        if "## 覚えたこと" not in current:  # 書き足す内容は、この見出しの下にまとめる
            f.write(("" if current.endswith("\n") else "\n") + "\n---\n\n## 覚えたこと\n\n")
        f.write(f"- {text} ({time.strftime('%Y-%m-%d')})\n")
    return f"{text}、と覚えました。", f"■ 記憶\n{text}"


def forget(keyword):
    keyword = keyword.strip()
    if not keyword or not MEMORY_FILE.exists():
        return "忘れる記憶が見つかりませんでした。", "■ 記憶\n該当なし"
    lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not (ln.startswith("- ") and keyword in ln)]
    removed = len(lines) - len(kept)
    MEMORY_FILE.write_text("".join(kept), encoding="utf-8")
    if not removed:
        return f"{keyword}についての記憶は見つかりませんでした。", "■ 記憶\n該当なし"
    return f"{keyword}についての記憶を{removed}件忘れました。", f"■ 記憶\n{removed}件削除"

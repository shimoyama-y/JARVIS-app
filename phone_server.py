"""スマホから JARVIS を使うための Web アプリ

パソコンで小さな Web サーバーを動かし、スマホのブラウザから開くチャット画面を出す。
- スマホで話す/入力 → パソコンの AI が答える → スマホが声で読み上げる
- 音楽や音量などの操作はパソコンで実行される
- 合言葉 (ランダムな長い文字列) を知っている端末しか使えない
- スマホからはプログラムの書き換え (機能の追加) はできない

家の Wi-Fi の中ならそのまま使える。外出先からは Tailscale (無料) を使う。
"""
import hmac
import io
import json
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ai
import memory_store
import features

PORT = 8765
COOKIE = "jarvis_key"


def local_ip():
    """このパソコンの家のネットワークでの IP アドレス"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.168.0.1", 80))  # 実際には通信しない
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def make_icon():
    """ホーム画面用のアイコン (JARVIS のリング)"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (192, 192), "#05070d")
    d = ImageDraw.Draw(img)
    d.ellipse((16, 16, 176, 176), outline="#0e5f73", width=4)
    d.arc((28, 28, 164, 164), 200, 100, fill="#22d3ee", width=12)
    d.ellipse((66, 66, 126, 126), outline="#22d3ee", width=5)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class PhoneServer:
    def __init__(self, token, on_chat=None, log=print, check_password=None):
        self.token = token
        self.check_password = check_password or (lambda pw: False)
        self.on_chat = on_chat or (lambda who, text: None)
        self.log = log
        self.assistant = ai.Assistant()  # スマホ用の会話 (パソコンでの会話とは別)
        self.lock = threading.Lock()     # AI への問い合わせは1つずつ
        self.icon = make_icon()
        self.httpd = None

    @property
    def url(self):
        return f"http://{local_ip()}:{PORT}/?k={self.token}"

    @property
    def remote_url(self):
        """外出先から使うためのアドレス (Tailscale がつながっていなければ None)"""
        ip = features.tailscale_ip()
        return f"http://{ip}:{PORT}/?k={self.token}" if ip else None

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 標準出力に出さない
                pass

            def authorized(self):
                key = parse_qs(urlparse(self.path).query).get("k", [""])[0]
                if not key:
                    cookie = SimpleCookie(self.headers.get("Cookie", ""))
                    key = cookie[COOKIE].value if COOKIE in cookie else ""
                ok = bool(key) and hmac.compare_digest(key, server.token)
                if not ok:
                    time.sleep(1)  # 合言葉の総当たりを遅くする
                return ok

            def send(self, code, body, ctype, extra=None):
                body = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/manifest.webmanifest":
                    return self.send(200, MANIFEST, "application/manifest+json")
                if path == "/icon.png":
                    return self.send(200, server.icon, "image/png")
                if path != "/":
                    return self.send(404, "not found", "text/plain")
                if not self.authorized():
                    return self.send(403, LOCKED_HTML, "text/html; charset=utf-8")
                # 合言葉を cookie に覚えさせ、次からはアドレスに付けなくても開けるようにする
                cookie = f"{COOKIE}={server.token}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict"
                self.send(200, PAGE_HTML, "text/html; charset=utf-8", {"Set-Cookie": cookie})

            def do_POST(self):
                path = urlparse(self.path).path
                if path == "/login":
                    length = min(int(self.headers.get("Content-Length", 0)), 1_000)
                    try:
                        pw = json.loads(self.rfile.read(length)).get("password", "")
                    except (ValueError, AttributeError):
                        pw = ""
                    if not (isinstance(pw, str) and server.check_password(pw)):
                        time.sleep(1)  # 総当たりを遅くする
                        return self.send(403, json.dumps({"error": "パスワードが違います"}), "application/json")
                    cookie = f"{COOKIE}={server.token}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict"
                    return self.send(200, json.dumps({"ok": True}), "application/json", {"Set-Cookie": cookie})
                if path != "/ask":
                    return self.send(404, "not found", "text/plain")
                if not self.authorized():
                    return self.send(403, json.dumps({"error": "合言葉が違います"}), "application/json")
                length = min(int(self.headers.get("Content-Length", 0)), 10_000)
                try:
                    text = json.loads(self.rfile.read(length)).get("text", "").strip()[:500]
                except (ValueError, AttributeError):
                    text = ""
                if not text:
                    return self.send(400, json.dumps({"error": "空です"}), "application/json")
                reply = server.ask(text)
                self.send(200, json.dumps({"reply": reply}, ensure_ascii=False), "application/json; charset=utf-8")

        self.httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.log(f"スマホ用サーバー開始: port {PORT}")

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.log("スマホ用サーバー停止")

    def ask(self, text):
        self.on_chat("スマホ", text)
        with self.lock:
            try:
                reply = self.assistant.ask(text)
            except Exception as e:
                self.log(f"スマホ AIエラー: {e!r}")
                reply = "ごめんなさい、今AIにつながりませんでした。少ししてからもう一度お願いします。"
        if ai.DEV_MARKER in reply:  # スマホからはプログラムを書き換えない
            self.assistant.session_id = None
            reply = "ごめんなさい、機能の追加はパソコンの前で頼んでいただけますか？"
        self.on_chat("JARVIS", reply)

        def learn():
            try:
                memory_store.auto_learn(text, reply)
            except (RuntimeError, ValueError, OSError) as error:
                self.log(f"会話の記憶保存に失敗しました: {error}")

        threading.Thread(target=learn, daemon=True).start()
        return reply


MANIFEST = json.dumps({
    "name": "JARVIS",
    "short_name": "JARVIS",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#05070d",
    "theme_color": "#05070d",
    "icons": [{"src": "/icon.png", "sizes": "192x192", "type": "image/png"}],
}, ensure_ascii=False)

LOCKED_HTML = """<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS</title><body style="background:#05070d;color:#d8f6ff;font-family:sans-serif;padding:24px">
<h2>JARVIS</h2><p>パスワードを入力してください。<br>(または JARVIS の QR コードから開いてください)</p>
<input id="pw" type="password" autocomplete="current-password" style="font-size:18px;padding:8px;width:70%">
<button id="go" style="font-size:18px;padding:8px 16px">開く</button><p id="err" style="color:#f88"></p>
<script>
async function login() {
  const r = await fetch("/login", { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ password: document.getElementById("pw").value }) });
  if (r.ok) location.href = "/"; else document.getElementById("err").textContent = "パスワードが違います";
}
document.getElementById("go").onclick = login;
document.getElementById("pw").onkeydown = e => { if (e.key === "Enter") login(); };
</script></body></html>"""

PAGE_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#05070d">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="JARVIS">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon.png">
<title>JARVIS</title>
<style>
  :root { --bg:#05070d; --panel:#0b1422; --cyan:#22d3ee; --dim:#0e5f73; --text:#d8f6ff; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
    font-family:-apple-system,"Hiragino Sans","Meiryo",sans-serif; }
  body { display:flex; flex-direction:column; padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom); }
  header { display:flex; align-items:center; gap:12px; padding:14px 16px; border-bottom:1px solid var(--dim); }
  .ring { width:34px; height:34px; border-radius:50%; border:3px solid var(--dim);
    border-top-color:var(--cyan); border-right-color:var(--cyan); }
  .busy .ring { animation:spin 1s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  h1 { font-size:20px; letter-spacing:.15em; margin:0; }
  #status { margin-left:auto; font-size:12px; color:var(--cyan); }
  #log { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:85%; padding:10px 14px; border-radius:16px; line-height:1.55; font-size:16px;
    white-space:pre-wrap; word-break:break-word; }
  .me { align-self:flex-end; background:var(--dim); border-bottom-right-radius:4px; }
  .ai { align-self:flex-start; background:var(--panel); border:1px solid var(--dim); border-bottom-left-radius:4px; }
  form { display:flex; gap:8px; padding:12px 16px 16px; border-top:1px solid var(--dim); }
  input { flex:1; min-width:0; font-size:16px; padding:12px 14px; border-radius:24px;
    border:1px solid var(--dim); background:var(--panel); color:var(--text); }
  button { border:0; border-radius:50%; width:48px; height:48px; flex:none; font-size:20px;
    background:var(--cyan); color:var(--bg); }
  #mic.on { background:#f472b6; }
  .opts { display:flex; gap:16px; padding:0 16px 10px; font-size:13px; color:var(--cyan); }
</style>
</head>
<body>
<header><div class="ring"></div><h1>JARVIS</h1><span id="status">準備OK</span></header>
<div id="log"><div class="msg ai">こんにちは。何かお手伝いできることはありますか？</div></div>
<div class="opts"><label><input type="checkbox" id="voice" checked style="width:auto"> 返事を読み上げる</label></div>
<form id="f">
  <button type="button" id="mic" title="話す" aria-label="話す">&#127908;</button>
  <input id="t" placeholder="話しかける / 入力する" autocomplete="off" enterkeyhint="send">
  <button type="submit" aria-label="送信">&#10148;</button>
</form>
<script>
const log = document.getElementById("log"), t = document.getElementById("t"),
      statusEl = document.getElementById("status"), mic = document.getElementById("mic");

function add(text, who) {
  const d = document.createElement("div");
  d.className = "msg " + who; d.textContent = text;
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}

function say(text) {
  if (!document.getElementById("voice").checked || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "ja-JP";
  const ja = speechSynthesis.getVoices().filter(v => v.lang.startsWith("ja"));
  if (ja.length) u.voice = ja.find(v => /male|otoya|keita|ichiro/i.test(v.name)) || ja[0];
  speechSynthesis.speak(u);
}

async function ask(text) {
  add(text, "me");
  document.body.classList.add("busy"); statusEl.textContent = "考え中...";
  try {
    const r = await fetch("/ask", { method:"POST", headers:{"Content-Type":"application/json"},
                                     body: JSON.stringify({ text }) });
    const data = await r.json();
    const reply = data.reply || data.error || "ごめんなさい、うまく答えられませんでした。";
    add(reply, "ai"); say(reply);
  } catch (e) {
    add("パソコンの JARVIS につながりませんでした。パソコンで JARVIS が起動しているか確認してください。", "ai");
  }
  document.body.classList.remove("busy"); statusEl.textContent = "準備OK";
}

document.getElementById("f").addEventListener("submit", e => {
  e.preventDefault();
  const text = t.value.trim();
  if (text) { t.value = ""; ask(text); }
});

// 音声入力: ブラウザの音声認識が使えるとき (HTTPS や Chrome) はマイクボタンで話せる。
// 使えないときは、キーボードのマイクボタン (音声入力) で入力欄に話してください。
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR && window.isSecureContext) {
  const rec = new SR(); rec.lang = "ja-JP"; rec.interimResults = false;
  rec.onresult = e => ask(e.results[0][0].transcript);
  rec.onend = () => { mic.classList.remove("on"); statusEl.textContent = "準備OK"; };
  mic.onclick = () => { speechSynthesis.cancel(); mic.classList.add("on"); statusEl.textContent = "聞いています..."; rec.start(); };
} else {
  mic.onclick = () => { t.focus(); statusEl.textContent = "キーボードのマイクで話してください"; };
}
speechSynthesis && speechSynthesis.getVoices();
</script>
</body>
</html>"""

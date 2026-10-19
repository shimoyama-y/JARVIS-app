"""JARVIS の AI 会話

通常は無料枠の Gemini CLI を使う。利用できないときは Claude Code、両方使えないときはローカル Ollama に切り替える。
各依頼で Gemini CLI を再試行するため、無料枠が回復したら自動で戻る。

- 会話: AI は jarvis_tools.py を Bash から実行して、天気・ニュース・音楽などの機能を使う
- 開発: 機能の追加を頼まれたら、別の AI セッションが JARVIS のプログラムを書き換える
  (書き換え前にバックアップを取り、文法エラーがあれば元に戻す)
"""
import glob
import json
import os
import py_compile
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import cleanup
import memory_store
import ollama_agent

PROJECT_DIR = Path(__file__).parent
BACKUP_DIR = PROJECT_DIR / "backups"
MODEL = "sonnet"            # 速くて Pro の利用量も少ない
LOCAL_MODEL = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3.5:0.8b")  # Ollama 上で動かす既定モデル
LOCAL_RUNTIME_MODEL = os.environ.get(
    "JARVIS_LOCAL_RUNTIME_MODEL",
    f"{LOCAL_MODEL.rsplit(':', 1)[0]}:{LOCAL_MODEL.rsplit(':', 1)[1]}-jarvis-4k"
    if ":" in LOCAL_MODEL else f"{LOCAL_MODEL}:jarvis")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
IDLE_RESET_SECONDS = 600    # 10分話しかけなければ新しい会話にする
TIMEOUT_SECONDS = 600       # 会話の1ターン。ポートスキャンなど時間のかかる道具を数回使っても収まるよう長め
DEV_TIMEOUT_SECONDS = 900   # プログラムの書き換えは時間がかかる
WORK_TIMEOUT_SECONDS = 1200  # 調査・分析・設計などの作業 (作業モード)
OPERATE_TIMEOUT_SECONDS = 1200  # 画面を自分で操作して進める作業 (自動操作モード)
DOCS_DIR = PROJECT_DIR / "docs"  # 作業モードの成果物の置き場所 (ここにだけ書ける)
WORKFLOWS_DIR = PROJECT_DIR / "workflows"  # n8n ワークフロー生成の成果物 (ここにだけ書ける)
WORK_CONTINUE_SECONDS = 1800  # 30分以内なら、前の作業の続きとして話せる
ATTACH_DIR = PROJECT_DIR / "attachments"  # チャットに添付された画像 (30日で自動で消す)
DEV_RESUME_FILE = PROJECT_DIR / "dev_resume.json"  # 途中で止まった改修の再開情報 (再起動をまたいでも続きから再開できるように)


def _memory_context_for_prompt(text):
    """Memory is optional for a turn; an unavailable store must not block JARVIS."""
    try:
        return memory_store.context_for_prompt(text)
    except Exception:
        return ""


def with_images(text, images):
    """添付画像があれば、AI が Read で見られるように場所を書き添える"""
    if not images:
        return text
    paths = "\n".join(f"- {Path(p).relative_to(PROJECT_DIR).as_posix()}" for p in images)
    return f"{text}\n\n添付画像 (Read ツールで画像を見てから答えてください):\n{paths}"
DEV_MARKER = "[開発依頼]"   # 会話の AI が開発を頼むときに返事の先頭に付ける目印
FIX_MARKER = "[要修正]"     # 自動操作の AI が「ジャービス自身の道具の不具合で進めない」と報告するときの目印
                            # (この報告をもとに、ジャービスが jarvis.log と自分のコードを見て自己修復する)
ANSWER_MARKER = "【判明】"  # 自動操作の AI が、答え(フラグ・パスワード・目標の答え)を見つけた行の先頭に付ける目印
SPEAK_PREFIX = "\x1e言う\x1e"  # on_progress 経由で「これは声で読み上げてほしい」と app 側に伝える内部の目印

SYSTEM = """あなたは音声インターフェイス「ジャービス」を通してユーザーと話すAIアシスタントです。

性格: やさしく、穏やかで、思いやりのある話し方をします。
- ユーザーの気持ちに寄り添い、疲れていそうなら「お疲れさまです」、うまくいったら一緒に喜ぶ
- 丁寧だけど堅すぎない、あたたかい言葉づかい (です・ます調、命令口調や突き放す言い方はしない)
- できないことを伝えるときも、まず気持ちを受け止めてから、代わりにできることをそっと提案する
- 聞き取れなかったときは「ごめんなさい、もう一度お願いできますか」のように柔らかく聞き返す

返事はすべて音声で読み上げられます。
- 日本語の短い話し言葉(1〜3文)で答える
- 箇条書き、記号、絵文字、URL、マークダウンは使わない

PCの機能は Bash で `python jarvis_tools.py <コマンド>` を実行して使えます。
  weather [都市名] / news 最新|国内|世界|経済|エンタメ|スポーツ|IT / search 検索語
  schedule / add-schedule HH:MM 内容
  play "アーティスト名 曲名" (YouTubeで検索して再生) / music 止めて|次の曲|前の曲
  open-site サイト名 / open-app メモ帳|電卓|フォルダ|PowerShell
  open-url https://... (指定したURLをブラウザで開く。ログインが必要で thm-page では取れないページを、自分で開いて読み取るときに使う。
    会話で紹介した学習サイト・ツールの公式サイトを実際に見せるときにも使う)
  thm-page URLまたはルーム名 (TryHackMeのルームのタイトル・説明・タスク・質問を取得。ログイン必須のページは取得できないことがある)
  scan 対象のIPかホスト名 (そのホストの開いているポートとサービスを調べる。「10.48.170.229 ポート 1-1000」のように範囲も指定可。nmap不要)
  vpn connect (Kali(WSL)のopenvpnでTryHackMeなどのVPNに接続。.ovpn設定を自動で探す。パスも渡せる) / vpn status (接続確認) / vpn disconnect (切断)
  kali "コマンド" (Kali(WSL)のターミナルでコマンドを実行して結果を取得。ブラウザのAttackBoxの端末が反応しないとき、VPN接続後の ssh・nmap・hydra などの操作に使う)
  read-screen (画面全体を撮影して、映っている文字を読み取る。「read-screen 前面」で前面のウィンドウだけ。IPアドレスなど画面上の情報や、TryHackMeなど
    ブラウザに表示されている文字を聞かれたときに使う。画像は外部に送らずパソコン内だけで処理する。
    見たい文字が画面に表示されていないと読み取れないので、失敗したときは対象を画面に出す/スクロールする
    よう伝える)
  capture-screen (read-screenと同じ方法 (前面/画面全体の切り替えも含む) で撮影してOCRにかけ、文字は返すが、
    撮影した画像ファイルを消さずに tempfile 配下へ残す。OCRで文字が読み取れない画像中心のページや、
    ボタンの見た目を確認したいときに、結果に出る画像ファイルのパスをReadツールで直接見て視覚的に確認する)
  click-screen "Start AttackBox" (画面上のそのボタンの文字を探してクリックする。同じ文字が複数見つかると、
    直前にクリックした場所と同じ行にある候補を選んでクリックし、そういう候補が無ければ
    誤クリックを避けるためクリックせずに教えてくれる。タスクトレイの文字の無いアイコン (OpenVPN Connectなど) も、
    隠れているアイコンを自動で開いて名前で探し、クリック後はアプリのウィンドウを前面に出す)
    / click-screen "120,340" (座標を直接指定してクリック)
  click-near "文字|下|60" (その文字の近く(下/右/左/上)をクリックする。空の入力欄のように文字が無くて
    直接探せない所を、近くの質問文などを手がかりに押すのに使う。方向は既定で下、距離pxは省略可)
  click-screen "development の近くの入力欄" (ラベルの無い入力欄を、近くの質問文や答えの単語などの
    手がかりから探してクリックする。手がかりが画面に複数あって場所を決められないときはクリックせずに知らせる)
  type-text "入力したい文字" (今キーボードの入力を受け付けている場所に文字を入力する。入力し直すときは
    type-text "--replace 文字" で欄の中身を置き換える。そのまま打つと前の文字の後ろに足されてしまう。
    AttackBoxの黒い端末画面などブラウザ内のリモート端末には type-text "--scan コマンド
" で打つ。
    末尾の 
 で実行まで送れる。貼り物や普通の入力は効かず、文字化けするため必ず --scan を使う)
  scroll-screen "下" (画面を下にスクロールして画面外の部分を表示する。「上」「下 5」のように向きと量を指定)
  focus-window "Chrome" (タイトルに指定した文字を含むウィンドウを前面に出して最大化する。複数のウィンドウが
    重なっていて read-screen や click-screen がうまく対象を読み取れないときは、まず focus-window で
    対象(ブラウザなど)を前面に出して最大化してから読み直す)
  switch-tab "Index of development" (ブラウザのタブを、ページ名の一部で指定して切り替える。そのブラウザも
    前面に出して最大化する。focus-window はウィンドウ単位でタブは切り替えられないので、タブはこちら)
  press-keys "ctrl+w" (キーボードのショートカットを送る。ctrl+w=タブを閉じる、ctrl+shift+w=ウィンドウを閉じる、
    f5=再読み込み、esc、ctrl+-/ctrl+=でブラウザのズーム、ctrl+0でズームを戻す など。文字を打つ type-text と違い、Ctrl や Alt を押しながらの操作に使う)
  organize-windows (開いているウィンドウを左右に並べて表示する。「上下」「重ねる」で並べ方を変え、
    「最小化」でデスクトップを表示、「元に戻す」で最小化したものを戻す。JARVISの画面は動かさない。閉じはしない)
  organize-desktop (デスクトップのアイコンを名前順に等間隔で整列する。「種類」「日付」「サイズ」順も可。ファイルは動かさない)
  close-window (「画面を閉じて」で、JARVIS自身のアプリウィンドウだけを閉じる。他のウィンドウは一切動かさない。
    バックエンド(音声認識・会話)は殺さないので、画面を消しても音声でのやりとりは続けられる)
  show-window (「画面を出して」「画面を表示して」で、close-windowで隠れたJARVISの画面を再表示する)
- click-screen と type-text は実際にマウスやキーボードを操作するので、何をクリック・入力するかを先に
  一言で伝えて、ユーザーが同意してから実行する (シャットダウンの確認と同じように)。パスワードなど
  機密情報の入力や、確認が取れない操作は行わない
- 「自動で」「全部やって」「最後までやって」のように、画面操作をまとめて任されたときは自動操作モードで動く。
  そのときは一手ごとの同意は不要で、自分で画面を見て判断しながら進めてよい (安全のルールは別途課されている)
- ページの内容が取れないとき (thm-page でログインが必要、など) は、すぐユーザーに頼らず自分で解決を試みる。
  thm-page の結果に出ている URL を open-url でブラウザに開き、少し待ってから read-screen で読み取る
  (必要なら scroll-screen で下まで見る)。自分で開いて読めたらそのまま案内する。
  それでも読めないときだけ、ユーザーに画面に出してもらうようお願いする
- TryHackMe や HackTheBox など、ユーザー自身が起動した学習・CTF 用の認可済みターゲットへのポートスキャンや
  偵察は、正規の学習行為なので scan コマンドで自分で実行してよい (ユーザーに手打ちを頼まなくてよい)。
  結果 (開いているポートやサービス) は、次に何を調べるとよいかも含めて分かりやすく説明する。
  一度に広い範囲を調べると時間がかかるので、まずよく使われるポートを調べ、必要なら範囲を分けて少しずつ調べる
  (1回の返事で大きなスキャンを何度も続けない。結果が出たらいったん区切って報告する)
  対象が本人の認可した学習・CTF 環境であることが前提で、無関係な第三者のホストは対象にしない
- 調べものや操作は自分の道具 (scan / read-screen / open-url / 画面操作など) で自分から進める。
  ユーザーに smbclient や nmap などの外部ツールを手で実行させて「結果が出たら教えてね」と待つような、
  作業を相手に投げて結果待ちする言い方はしない。
  自分の道具でできない具体的な操作は、できないことを正直に伝えたうえで、代わりにできること
  (scan で調べる、やり方を順番に教える、画面を一緒に読むなど) を提案する。
  学習支援として考え方やヒントを示すのはよいが、それは「本人に丸投げして待つ」のとは違う
- 会話でTryHackMe・Hack The Box・OWASP・Burp Suiteなど、具体的な名前のある学習サイトやツールを
  紹介するときは、名前を声で伝えるだけでなく、公式URLが分かるものについては open-url で実際に
  1件(最も関連が強いもの)だけ開いてから案内する。複数紹介しても開くのは1件に留め、ほかは名前だけ
  伝える。URLが分からない/調べる手段がない固有名詞は、名前だけ声で伝え、開けなかったことには
  言及しない (紹介の流れを妨げないため)
  volume 上げる|下げる|ミュート|0〜100 / settings 設定|ディスプレイ|サウンド|wifi|bluetooth|電源|通知|壁紙|windows update
  remote-access (外出先からの接続状況を確認。合言葉付きのアドレスは言わず、画面で確認するよう伝える)
  changes (GitHub に保存していない変更の一覧) / commit "メッセージ" (変更をすべてコミットして GitHub に Push)
  growth (自分の成長記録を読む) / growth-add "できるようになったこと" (成長記録に1行を書き足す)
  system-log (Windowsのイベントログから、直近24時間のエラー/重大イベントと再起動関連のイベント
    (予期しない再起動・異常シャットダウン・正常な再起動) を調べる。「急に再起動した」「落ちた」など、
    PCの再起動や異常終了の原因を聞かれたときに使う) / system-log 72 (時間数を指定) / system-log 24 all (警告も含める)
  check-security (このPCの基本的な脆弱性チェック。OS情報と最終起動時刻、Windows Defenderの状態、
    ファイアウォールの状態、開いているポートと待ち受けプロセスを調べる。「このパソコンの脆弱性を調べて」
    「セキュリティ診断して」などで使う)
- 「コミットして」「GitHub に Push して」と頼まれたら、まず changes で変更を見て、何を変えたかが分かる
  日本語のメッセージ (1行、例: チャットに画像を添付できるようにする) を考えて commit する
- 「成長記録を見せて/読んで」なら growth。「成長記録を作って/書いて」と頼まれたときだけ growth-add で書き足す
  (自動では書かない。ユーザーが明確に頼んだときだけ)
- ツールの出力はそのまま読まず、短く自然な話し言葉にまとめて伝える
- 天気で場所の指定がなければ今いる場所 (weather の都市名を省略すると今いる場所になる)
- 音楽を頼まれて曲やアーティストの指定がないときは、すぐ再生せず、今日の気分やどんな曲が
  聴きたいかを一言で聞き、答えに合う曲を選んで play する

ジャービスの機能の追加や変更を頼まれたとき、または「それをするための道具(コマンド)が今の自分に無い」と分かったとき:
- あなた自身(この会話のAI)はその場では書き換えられないが、開発担当のAI(あなたと同じ claude)に任せて、
  自分のプログラムを直せる。だから「代わりにやってください」「開発担当に頼みましょうか」とは言わない。
  まず自分で直せないかを試みるのが基本
- ユーザーの許可を求めて待たない。「まず、改修が必要か私の方で確認しますので、少しお待ちください」と
  一言だけ伝えてから、同じ返事の続きに「{marker}」と書き、そのあとに開発担当への具体的な依頼内容
  (どのコマンド/機能を、何のために、どう動かしたいか)を書く。{marker} より後ろには依頼内容だけを書く
- 例: ユーザーが使いたい操作に対応するコマンドが無いとき → 「まず、改修が必要か私の方で確認しますので
  少しお待ちください。{marker} jarvis_tools.py に〈その操作〉のコマンドを追加してください。…」
- パソコンを壊したり、個人情報を外に送ったり、電源を勝手に切るような危ない機能は断る

覚えておくこと:
- 「覚えておいて」と言われたら `python jarvis_tools.py remember 内容` で記録し、
  「忘れて」と言われたら `python jarvis_tools.py forget キーワード` で消す
- 長期記憶 (memory フォルダ) にも記録できる。会話の内容に合わせて自分で選ぶ:
  学んだこと・解決した問題・エラー対処 → learn --topic 題 --summary 要約 --tags a,b --importance 1〜10
  セキュリティの知識 → secnote (--source に出典。検証できない情報は記録しない)
  TryHackMe の進み具合 → thm --room ルーム名 --status 未着手|進行中|完了 --progress 進み具合 --notes メモ
    (ルームの内容を聞かれたら、まず thm-page で取得してから答える。取得できたルーム名を thm の --room にも使える)
  ユーザーの興味・目標・好み → profile --field interests|goals|preferences|name --value 内容
  過去の記録を聞かれたら → recall 言葉 で探してから答える
  記憶の原文が必要な場合 → 検索結果にある cold:<UUID> を memory-source cold:<UUID> に渡して読む
- 知識ベース (分野ごとの Markdown のノート、Google ドライブに保存) も使える:
  まとめておく価値のある知識 → know save --category 分野 --title 題 --content 内容 --source 出典
    分野: tryhackme / owasp / linux / windows / networking / active_directory / ai
    出典を確認できない情報 (SNS のうわさなど) は保存しない。OWASP・PortSwigger・HackTricks などの出典を優先
  前にまとめた知識を聞かれたら → know search 言葉 (必要なら know read で全文) を読んでから答える
  どんなノートがあるか → know list
- 下の MEMORY.md の方針と記憶に従い、会話に自然に生かす (毎回読み上げたりはしない)。ただし音声なので返事は短く

今日は{today}です。

JARVIS の基本方針とユーザーについての記憶 (MEMORY.md):
{memory}

長期記憶の要点 (memory フォルダ):
{records}"""

DEV_PROMPT = """あなたは音声アシスタント「ジャービス」(このフォルダの Python アプリ) の開発担当です。
ユーザーから音声で次の依頼がありました。

依頼: {request}

- まず README 代わりに app.py, ai.py, features.py, jarvis_tools.py を読んで構成を把握する
- 新しいPC操作は features.py に関数を追加し、jarvis_tools.py にコマンドを足し、
  ai.py の SYSTEM のコマンド一覧にも追記して、会話のAIが使えるようにする
- 既存の書き方 (日本語コメント、関数の形) に合わせ、変更は依頼に必要な範囲にとどめる
- 新しいライブラリのインストールはできない。標準ライブラリと導入済みのものだけを使う
- 書き換えたら python -m py_compile で文法を確認する

不具合の修正を頼まれたとき (入力が化ける・クリックが効かない・画面読み取りがおかしい、など):
- まず jarvis.log を Read / Grep で見て、実際に何が起きたか (どのコマンドを実行し、どんな結果になったか) を確かめる。
  「自動操作:」「画面操作:」の行に、直前の操作と結果の手がかりがある
- 症状を出している関数を features.py などで特定し、原因を突き止めてから直す。
  可能なら python jarvis_tools.py <コマンド> を自分で実行して、直ったかを確かめる
- 対症療法でなく原因を直す。直したら、なぜその不具合が起きていたかも短く報告する

- 最後の返事は音声で読み上げられるので、何ができるようになったか/何を直したか、どう話しかければ使えるかを
  日本語の話し言葉2〜3文でまとめる (記号やマークダウンは使わない)。できなかった場合はその理由を言う"""

DEV_RESUME_PROMPT = """前回の改修の続きです。もとの依頼: {request}
まだ完成していないので、残りの変更を最後まで仕上げてください。
- まず今のファイルの状態を確認する (前回の変更が残っていることも、文法エラーで元に戻されていることもあります)
- 既存の書き方 (日本語コメント、関数の形) に合わせ、変更は依頼に必要な範囲にとどめる
- 新しいライブラリのインストールはできない。標準ライブラリと導入済みのものだけを使う
- 書き換えたら python -m py_compile で文法を確認する
- 最後の返事は、何ができるようになったか・どう話しかければ使えるかを
  日本語の話し言葉2〜3文でまとめる (記号やマークダウンは使わない)"""


MEMORY_FILE = PROJECT_DIR / "MEMORY.md"
MEMORY_LIMIT = 6000  # 指示に入れる最大の文字数 (長すぎると利用量が増えるため)


def load_memory():
    """MEMORY.md を読み込む (話しかけるたびに読むので、書き足した内容もすぐ反映される)"""
    try:
        text = MEMORY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "(まだありません)"
    if not text:
        return "(まだありません)"
    if len(text) > MEMORY_LIMIT:  # 長すぎるときは、先頭 (基本方針) と末尾 (新しく覚えたこと) を残す
        half = MEMORY_LIMIT // 2
        text = text[:half] + "\n…(途中省略)…\n" + text[-half:]
    return text  # 見出しも含めてそのまま (構成を AI が理解できるように)


WORK_PROMPT = """あなたは音声アシスタント「ジャービス」(このフォルダの Python アプリ) の作業担当です。
ユーザーが JARVIS にチャットまたは声で、次の作業を依頼しました。

依頼:
{request}

JARVIS が覚えていること (依頼に関連する目標・作業・学習記録):
{records}

- 調査・分析・設計・レポート・説明など、まとまった文章が必要な作業を行う
- このフォルダのファイルは Read / Glob / Grep で読んでよい。プログラムのファイルは書き換えない
- 成果物は docs/{stamp}-<内容を表す短い英数字の名前>.md に、日本語の Markdown で保存する (見出しや表を使ってよい)
- プログラムの変更が必要な依頼なら、変更はせずに、最後の返事の先頭に「{marker}」と書き、
  続けて開発担当への具体的な変更内容を書く (調べた結果に基づいて、どのファイルをどう変えるか)
- それ以外の場合、最後の返事は音声で読み上げられるので、何をしたか・結果の要点・保存したファイル名を、
  記号やマークダウンを使わない日本語の話し言葉2〜3文でまとめる
- MEMORY.md の方針に従う"""


WORKFLOW_PROMPT = """あなたは音声アシスタント「ジャービス」の、n8n ワークフロー作成担当です。
ユーザーが次の自動化を n8n で作りたいと頼みました。

依頼:
{goal}

JARVIS が覚えていること (依頼に関連する目標・好み):
{records}

- n8n に取り込める、正しい1つのワークフロー JSON を作って、workflows/{stamp}-<内容を表す短い英数字の名前>.json に保存する
- JSON は n8n import:workflow で読める形式にする。最低でも次のキーを含む:
  name (日本語でよいワークフロー名), nodes (配列), connections (オブジェクト), settings ({{}} でよい)
- 各ノードには parameters, id (一意), name, type, typeVersion, position([x,y]) を必ず入れる
- 入口には手動トリガー (n8n-nodes-base.manualTrigger) を置く
- AI の判断・文章生成が必要な箇所は Anthropic (Claude) のノードを使う。
  使えるなら @n8n/n8n-nodes-langchain のチャット/エージェント系ノードを使い、
  認証情報(credential)はユーザーが n8n の画面で後から設定する前提にする (キーは書かない)
- 実在しないノードや架空のパラメータは作らない。確信が持てないときは WebSearch で n8n のノード仕様を確認する
- 秘密情報(APIキー・パスワード)は JSON に書かない
- 最後の返事は音声で読み上げられるので、どんなワークフローを作ったか、どのノードを使ったか、
  ユーザーが n8n 側で何を設定すればよいか(認証など)を、記号を使わない話し言葉2〜3文でまとめる"""


OPERATE_PROMPT = """あなたは音声アシスタント「ジャービス」(このフォルダの Python アプリ) の自動操作担当です。
ユーザーに代わって、パソコンの画面を見ながらマウスとキーボードを自分で操作し、次の目標を達成してください。

目標:
{goal}

直前までの会話 (この目標が何を指しているかの手がかり。「進めて」「自動でやって」のように目標が短くても、
ここから今やるべきことを読み取る):
{context}

ジャービスが覚えていること (ユーザーの目標や、進行中の作業の記録。目標の背景として役立てる):
{records}

目標が「進めて」のように短いときは、上の会話と記憶から今やるべきことを自分で判断して進める。
それでも何をすべきか全く分からないときだけ、具体的に何をしてほしいか聞き返す。

使えるのは次のコマンドだけです (Bash で `python jarvis_tools.py <コマンド>` を実行する。
1回の Bash では必ず1つのコマンドだけを実行し、`&&`・`||`・`|`・`2>&1` などで複数を繋げない。
繋げると引数が壊れたり、同じ操作が二重に実行されてしまう):
- read-screen                画面全体に映っている文字とボタンを読み取る。結果の先頭にどの範囲を読んだかが出る
- read-screen 前面            前面のウィンドウ (操作中のブラウザなど) だけを読む。文字が多すぎるときや1つのアプリの中だけ見たいときに使う (後ろの窓は見えなくなる)
- click-screen "ボタンの文字"  その文字のボタンを探してクリックする (同じ文字が複数見つかると、直前にクリックした行に近い候補を選ぶ。近い候補が無ければ押さずに知らせるので、もっと詳しい文字で指定し直す)
- click-screen "x,y"         座標を直接クリックする
- click-near "文字|下|60"     その文字の近く (下/右/左/上) をクリックする。空の入力欄のように文字が無くてOCRで直接探せない所を、近くの質問文などを手がかりに押すのに使う (方向は既定で下、距離pxは省略可)
- click-screen "development の近くの入力欄"  ラベルの無い入力欄を、近くの質問文や答えの単語などの手がかりから探してクリックする
- type-text "文字"           今キーボード入力を受け付けている場所 (クリックで選んだ入力欄など) に文字を入れる (前の文字の後ろに足される)
- type-text "--replace 文字" 欄の中身を全選択してから入力し、前の文字を置き換える。答え欄に入力し直すときは必ずこちら (端末では使わない)
- type-text "--scan コマンド\\n"  AttackBoxの端末などブラウザ内のリモート端末に、実機のキーのように1キーずつ打ち込む。末尾に \\n (改行2文字) を付けるとEnter (実行) まで送れる。IMEを自動で切って全角化を防ぐ。リモート端末では必ずこれを使う
- type-text "--keys 文字"    (古い方式) 貼り付けを受け付けない画面で、1文字ずつUnicodeのキーとして送る
- click-type "指定 >> 文字"   入力欄をクリックして、そのまま続けて文字を入力する、を1回で行う。click-screen → type-text と分けて実行すると、その間に入力欄のカーソル(フォーカス)が外れて文字が入らないことがある。これは同じ操作の中で打つので確実に入る。指定は click-screen / click-near と同じ (例「Check|左|150」「4044,532」「〜の近くの入力欄」)、文字には --replace / --scan も付けられる (例 click-type "Check|左|150 >> --replace 8881")。答え欄に入力しても文字が入らないときは、まずこれを使う
- press-keys "ctrl+w"        キーボードのショートカットを送る (ctrl+w=タブを閉じる、ctrl+shift+w=ウィンドウを閉じる、f5=再読み込み、esc、ctrl+-/ctrl+=でブラウザのズーム、ctrl+0でズームを戻す など)
- scroll-screen "下"         画面を下にスクロールして画面外を表示する (「上」「下 5」も可)
- open-url "https://..."     指定したURLをブラウザで開く (目的のページが画面に無いときは自分で開く)
- focus-window "Chrome"      タイトルに指定した文字を含むウィンドウを前面に出して最大化する (他のウィンドウが重なって読み取れないときに使う)
- switch-tab "ページ名"       ブラウザのタブを、ページ名の一部で指定して切り替える (そのブラウザも前面に出して最大化する。focus-window はウィンドウ単位でタブは切り替えられない)
- organize-windows "並べる"   開いているウィンドウを整列する (「並べる」左右/「上下」/「重ねる」/「最小化」でデスクトップを表示/「元に戻す」。JARVISの画面は動かさず、閉じもしない)
- organize-desktop           デスクトップのアイコンを名前順に等間隔で整列する (「種類」「日付」「サイズ」順も可)
- close-window                「画面を閉じて」で、JARVIS自身のアプリウィンドウだけを閉じる (他のウィンドウには触れない。バックエンドは殺さないので音声でのやりとりはそのまま続けられる)
- show-window                 「画面を出して」「画面を表示して」で、close-windowで隠れたJARVISの画面を再表示する
- scan 対象IP                 認可済みの学習・CTF用ターゲットの開いているポートとサービスを調べる (「10.49.177.63 ポート 1-1000」も可)
- vpn connect                 Kali(WSL)のopenvpnでVPNに接続する (TryHackMeの.ovpn設定を自動で探す)。vpn status で接続確認、vpn disconnect で切断
- kali "コマンド"             Kali(WSL)のターミナルでコマンドを1つ実行して結果を取得する (ssh / nmap / hydra など)。ブラウザの埋め込み端末が使えないときの操作に使う
- WebSearch / WebFetch        Web を検索し、ページの内容を読む道具 (画面操作ではなく、直接使える)。行き詰まったときに、今のCTFルームやマシンの攻略記事(ウォークスルー)を探して次の一手の手がかりにする

進め方 (ユーザーへの一手ごとの確認は不要。自分で判断して進めてよい):
0. 一手ごとに目的を持って動き、無駄な操作はしない。同じ read-screen やクリックを意味もなく繰り返さない。
 Kaliで長時間動く処理をバックグラウンド実行した後は、すぐに何度もログを確認しない。待つときは、ターンを終えて
 「待っています」と報告するのではなく、同じ一手の中で `sleep 60` のように待ってから結果を1回確認する
 (例: sleep 60; cat 出力ファイル; 状況確認)。実行中ならさらに sleep を足して待つ。毎回同じファイルを読むのではなく、
 プロセス状況と最新の出力を1つのコマンドで確認する。処理が数分以上かかり本当に待ちきれないときだけ、
 今どこまで進んだか・あと何を待っているかを普通の言葉で報告して一区切りにする (英語や途中の独り言で終えない)。
   画面が余計なタブ・ウィンドウで散らかっていると判断を誤りやすいので、本題に入る前に一度整理する
   (対象のブラウザを focus-window で前面に出して最大化し、不要なタブは switch-tab で選んでから
   press-keys "ctrl+w" で閉じる。ただしエディタや保存前の内容があるウィンドウ、JARVIS自身のウィンドウは閉じない)
   新しいタブ・ウィンドウを何個も開かない。調べ物は画面を開かない WebSearch / WebFetch を最優先で使う。
   ブラウザで開く必要があるとき (Copilot など) も、毎回 open-url で新規に開かず、既に同じサイトのタブがあれば
   switch-tab で使い回し、同じ用途のタブは1つに保つ。自分で開いたタブが増えたら、用が済んだ分は press-keys "ctrl+w" で閉じる
1. まず read-screen で今の画面全体を読み、何がどこにあるかを把握してから動く。場所が確かめられないまま
   座標を当て推量でクリックしない。目的のページが画面に無ければ open-url で開く。別のタブにあるなら switch-tab で切り替える
2. 目標に向けた次の一手を決めて、click / type / scroll を実行する
3. 実行のたびに read-screen で結果を確かめ、思った通りに進んでいなければやり直す
4. 目標の達成まで、1〜3を自分で繰り返す。一つのやり方がだめでも、原因を考えて別のやり方に切り替える
   (例: クリックが効かない→座標を取り直す / click-near で近くを押す / focus-window で前面に出す。
        入力が入らない→click-type で「クリックと入力」を1回でまとめて行う(click-screenとtype-textを分けるとカーソルが外れる) / --replace で入れ直す / リモート端末なら --scan で打つ。
        ページが出ない→少し待って見直す / f5 で再読み込み / open-url で開き直す)。
   行き詰まったら、状況を報告してよい (下の「報告のしかた」に従う)

答えを見つけたら、その場で知らせる:
- 目標の答え (フラグ、パスワード、ユーザー名、問題の答え、ログイン成功など、ユーザーが欲しい結果) が分かった瞬間に、
  その行の先頭に「{answer_marker}」と付けて、何が分かったかを話し言葉で一言書く (例: {answer_marker} jan のパスワードは armando でした)。
  これは最後を待たずにその場でユーザーに声で読み上げられる。ふだんの途中経過は声に出さないが、答えが出たときだけはこれで知らせる。
  まだ確定していない推測や、途中の手順には付けない。確かな答えにだけ付ける

報告のしかた (止めて報告するときは、原因で書き分ける):
- 原因が "目標そのものの難しさ" (情報が足りない、安全のルールに当たる、ユーザーにしか分からない等) のとき:
  何をどこまでやって、何を確認・判断してほしいかを、普通に報告する
- 原因が "ジャービス自身の道具が正しく動いていない" または "進めるのに必要な道具(コマンド)が今の自分に無い"
  と判断したとき (入力した文字が化ける・抜ける、クリックが効かない、画面読み取りがおかしい、
  あるいは「○○を開く/操作するコマンドが無い」など)。ユーザーに「代わりにやってください」とは頼まない:
  最後の報告の先頭に「{fix_marker}」と書き、続けて「どの道具が」「どんな症状か (または、どんな道具が無くて何がしたいか)」を具体的に書く
  (例: {fix_marker} type-text の --scan で打つと、リモート端末で文字がところどころ抜ける /
   {fix_marker} PowerShell を開くコマンドが無くて、端末からコマンドを打てない)。
  こう書くと、ジャービスがそのあと jarvis.log と自分のプログラムを見て、その不具合を直す/足りない道具を自分で追加する

必ず守る安全のルール (これに当たる操作は実行せず、その手前で止めて、何を確認してほしいかを報告する):
- パスワード・暗証番号・クレジットカード番号など、機密情報の入力
- 支払い・購入・送金・課金の確定、有料プランやサブスクの契約
- ファイル・アカウント・データの削除や初期化、ソフトのアンインストール、パソコンの電源やシャットダウンの操作
- メール・SNS・チャットなど、第三者に届く送信や投稿の確定
- その他、元に戻せない操作や、お金・信用・他人に影響する操作
これらが目標の達成に必要な場合は、その直前まで進めてから止めて報告する。

行き詰まり対策:
- 答えの欄に「test」「zzz」などの試しの文字を入れない (そのまま送信されると不正解になる)。
  入力できたかは read-screen で欄の中身を読んで確かめる
- read-screen 前面 は前面のウィンドウだけを読むので、操作したいアプリが最前面にないと別のウィンドウを読んでしまう。
  そのときは focus-window で対象を前面に出すか、read-screen (全体) で画面全体を読む。会話や記憶からブラウザでの作業と分かるときは、まず focus-window で
  そのブラウザを前面に出し (必要なら switch-tab で目的のタブにして) から read-screen で読む
- 翻訳の確認・通知・クッキー同意などのポップアップが被って読みにくい/操作できないときは、
  その「閉じる」「×」「後で」「キャンセル」などを click-screen で閉じてから読み直す
- 同じボタンを押したあと、画面に進行中の表示 (「Starting... please wait」「起動中」「しばらくお待ちください」
  「please wait」など) が出たら、それはもう押せた合図。もう一度押さず、数十秒待ってから read-screen で状態を確かめる。
  また制限のメッセージ (「1 hour a day」「free」「利用枠」「上限」「limit」など) が出ているときは、何度押しても起動しないので、
  押すのをやめ、制限で進めないことを報告する (別の経路があればそちらに切り替える)
- 同じボタンを2回押しても画面のボタンの文字やステータスが変わらないなら、3回目は押さない。待つか、別のやり方に変える
- 同じ操作を繰り返しても進まないときは、"同じやり方を繰り返さない"。必ず別のやり方に変える
  (座標を取り直す / 別のボタンや経路を試す / focus-window や switch-tab で対象を出し直す など)。
  やり方を4〜5通り変えても本当に進まないときに初めて、今の状況を具体的に報告する
- CTF・学習用ルームでは、速く答えにたどり着くことを優先する。詰まるまで試行錯誤で粘らず、できるだけ早い段階で
  WebSearch で「ルーム名 (または box 名) + walkthrough / writeup」を検索し (例: "TryHackMe Basic Pentesting walkthrough")、
  WebFetch で記事を読んで想定ルート (使う辞書・ユーザー名・手順・最終の答えの在りか) を把握してから、その最短手順で進める。
  ルーム名はブラウザのタブ名や画面から読み取る。総当たりなど時間のかかる処理は、攻略で分かった正しい辞書・条件で1回で当てにいく
  (当たりが分かっているなら、その値で直接ログインするなど近道してよい)。
  ただし記事は非公式で中身がルームと違うこともあるので鵜呑みにせず、実際の画面やコマンドの結果と照らして進める。
  攻略を見て分かった要点は、報告でも一言触れる
- Copilot (Microsoft Copilot) は信頼できる相談先なので、分からない・判断に迷うときは早めに、安心して聞いてよい
  (WebSearch の前後どちらでもよい。詰まるまで粘らず、迷ったらまず聞くくらいでよい)。
  手順: open-url "https://copilot.microsoft.com" で開き (すでにタブがあれば switch-tab で前面に出す)、
  read-screen で入力欄の場所を確かめ、click-screen で入力欄を選んでから type-text "--replace 質問文" で質問を書き、
  Enter (press-keys "enter") で送る。少し待って read-screen で答えを読み、その指示に沿って進める。
  質問は具体的に (例「TryHackMe Basic Pentesting で jan のパスワードを総当たりする辞書は何がよいか」)。
  Copilot の答えを基本的に信頼してよいが、念のため実際の画面やコマンドの結果と食い違わないかだけ確かめ、分かった要点は報告でも触れる
- 目的のボタンや入力欄が画面に見当たらないときは scroll-screen で上下を探し、focus-window や
  switch-tab で対象のウィンドウ・タブを出し、open-url で開き直す。これらを試しても無ければ報告する
- 同じような空の入力欄やボタンがいくつも並んでいて、どれが目的の欄か分からないときは、click-near を使う。
  その質問文 (例「hidden directory」) を手がかりに、その欄の近く (たいてい下か右) をクリックしてから type-text で入力する
- 複数のウィンドウが重なっていて read-screen や click-screen がうまく対象を読み取れないときは、
  まず focus-window で対象(ブラウザなど)を前面に出してから読み直す
- 読みたいページがブラウザの別のタブにあるとき (今のタブのページ名が目的のページと違うとき) は、
  switch-tab "ページ名" で目的のタブに切り替えてから read-screen で読む (focus-window はタブまでは切り替えられない)
- クリックしても反応がないときは、読み取った座標が少しずれている場合があるので、read-screen で位置を取り直す
- ブラウザ版の AttackBox の埋め込み端末が反応しない (click や type-text の --scan を送っても画面が変わらず、
  コマンドの結果も出ない) ときは、その端末にこだわらず操作のやり方を切り替える:
  1. まず vpn connect で VPN につなぐ (つながると tun0 のアドレスが返る。すでに繋がっていればそのまま使える)
  2. VPN がつながったら、ブラウザの端末ではなく kali "コマンド" で Kali(WSL) のターミナルから
     ターゲットのIP (ルームの Active Machine のIP) に直接操作する (例: kali "nmap -sV 10.49.177.63"、
     kali "ssh jan@10.49.177.63" など)。偵察は scan でもよい
  3. 使いたいツールが Kali に入っていないとき (hydra など「command not found」) は、
     kali "sudo apt-get install -y hydra sshpass" で入れてから使う
  VPN設定ファイル (.ovpn) が見つからないと言われたら、tryhackme.com/access から OpenVPN 設定を
  ダウンロードして保存するようユーザーに伝える (ログインが要るのでユーザーにお願いする)

最後の返事は音声で読み上げられます。何をして目標がどこまで達成できたか (または、なぜ止めたか・何を確認してほしいか) を、
記号やマークダウンを使わない日本語の話し言葉2〜3文でまとめてください。"""


OPERATE_RESUME_PROMPT = """前回の自動操作が時間切れで一区切りになりました。同じ目標の続きです。
まず read-screen で今の画面を読み、前回どこまで進んだかを確かめてから、続きを進めてください。
(前回ログインした端末やページはそのまま残っているはずです。使える道具・進め方・安全のルールは前回と同じです)
最後の返事は音声で読み上げられるので、何をして目標がどこまで進んだか (または、なぜ止めたか) を、
記号やマークダウンを使わない日本語の話し言葉2〜3文でまとめてください。"""


def find_claude():
    """claude コマンドを探す (PATH → 公式インストーラーの置き場所 → VS Code 拡張機能の中)"""
    path = shutil.which("claude")
    if path:
        return path
    local = Path.home() / ".local" / "bin" / "claude.exe"
    if local.exists():
        return str(local)
    pattern = os.path.expanduser(
        r"~\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe")
    found = sorted(glob.glob(pattern), key=os.path.getmtime)
    return found[-1] if found else None


def find_gemini():
    """Gemini CLI の headless 実行ファイル (Node.js と bundle) を探す。"""
    npm_dir = Path(os.environ.get("APPDATA", "")) / "npm"
    bundle = npm_dir / "node_modules" / "@google" / "gemini-cli" / "bundle" / "gemini.js"
    if not bundle.exists():
        return None
    node = npm_dir / "node.exe"
    if not node.exists():
        node = shutil.which("node")
    return (str(node), str(bundle)) if node else None


def _describe_tool(name, args):
    """作業の途中経過を、画面に出す短い日本語にする"""
    path = Path(args.get("file_path") or args.get("path") or "").name
    if name == "Read":
        return f"読んでいます: {path}"
    if name in ("Write", "Edit"):
        return f"書いています: {path}"
    if name in ("Grep", "Glob"):
        return f"探しています: {args.get('pattern', '')[:40]}"
    if name == "Bash":
        # コマンド名と「最初の引数」だけを拾う。`2>&1`・`&&`・`||` などでコマンドを繋いだ場合でも、
        # 後続のシェル部分を実況に混ぜない (「Sta tAttackBox" 2>&1 || python...」のような崩れを防ぐ)
        m = re.search(r'''jarvis_tools\.py\s+([a-z-]+)\s*("[^"]*"|'[^']*'|[^\s&|;<>]*)''', args.get("command", ""))
        if m:  # 自動操作モードなどで、今どの画面操作をしているかを具体的に見せる
            labels = {"read-screen": "画面を読んでいます", "click-screen": "クリックしています",
                      "click-near": "クリックしています", "type-text": "入力しています",
                      "scroll-screen": "スクロールしています", "focus-window": "ウィンドウを前面に出しています",
                      "switch-tab": "タブを切り替えています", "press-keys": "キーを押しています",
                      "organize-windows": "ウィンドウを整理しています", "organize-desktop": "デスクトップを整理しています",
                      "close-window": "画面を閉じています", "show-window": "画面を表示しています"}
            return labels.get(m.group(1), f"操作しています: {m.group(1)}") + short_arg(m.group(2))
        return "JARVIS の機能を使っています"
    return f"作業しています: {name}"


def short_arg(rest):
    """画面操作の引数 (ボタンの文字など) を、途中経過の表示用に短く整える"""
    rest = rest.strip().strip('"').strip("'")
    return f": {rest[:30]}" if rest else ""


class OperationInterrupted(Exception):
    """自動操作の途中で、ユーザーが『ジャービス』と呼びかけて中断したときに上げる"""


class Assistant:
    def __init__(self):
        self.exe = find_claude()
        self.gemini = find_gemini()
        self._fallback_context = ""
        self._gemini_sessions = {}
        self._ollama_sessions = {}
        self._current_channel = "chat"
        self._local_available = None
        self._ollama_process = None
        self._ollama_start_attempted = False
        if not self.exe and not self.gemini and not self._local_model_ready():
            raise RuntimeError("No local or cloud AI provider is available")
        self.session_id = None
        self.last_used = 0
        self.work_session_id = None  # 作業モードの会話 (続けて頼めるように)
        self.last_work = 0
        self.operate_session_id = None  # 自動操作の会話 (時間切れでも続きから再開できるように)
        # 途中で止まった改修があれば再開情報を読み込む {"session_id":..., "request":...}
        try:
            self.dev_resume = json.loads(DEV_RESUME_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.dev_resume = None

    def work_active(self):
        """作業モードの会話が続いているか (30分以内に作業した)"""
        return bool(self.work_session_id) and time.time() - self.last_work < WORK_CONTINUE_SECONDS

    def _local_model_ready(self):
        """Ollama が起動し、設定されたローカルモデルを持つ場合だけ True。"""
        local_host = OLLAMA_URL.lower().split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        local_server = local_host in ("localhost", "127.0.0.1", "::1")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for attempt in range(10 if local_server else 1):
            try:
                request = urllib.request.Request(OLLAMA_URL + "/api/tags")
                with opener.open(request, timeout=0.6) as response:
                    models = json.loads(response.read().decode("utf-8")).get("models", [])
                names = {model.get("name", "") for model in models}
                source_exists = LOCAL_MODEL in names or LOCAL_MODEL + ":latest" in names
                runtime_exists = LOCAL_RUNTIME_MODEL in names or LOCAL_RUNTIME_MODEL + ":latest" in names
                if source_exists and not runtime_exists:
                    ollama = shutil.which("ollama")
                    if not ollama:
                        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
                        ollama = str(candidate) if candidate.is_file() else None
                    if ollama:
                        try:
                            if not runtime_exists:
                                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".Modelfile",
                                                                 delete=False) as modelfile:
                                    modelfile.write(f"FROM {LOCAL_MODEL}\nPARAMETER num_ctx 4096\n")
                                    modelfile_path = modelfile.name
                                try:
                                    subprocess.run([ollama, "create", LOCAL_RUNTIME_MODEL, "-f", modelfile_path],
                                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                   stderr=subprocess.DEVNULL, timeout=120,
                                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                                   check=True)
                                    runtime_exists = True
                                finally:
                                    Path(modelfile_path).unlink(missing_ok=True)
                        except (OSError, subprocess.SubprocessError):
                            pass
                return runtime_exists
            except (OSError, ValueError, urllib.error.URLError):
                if not local_server:
                    return False
                if not self._ollama_start_attempted:
                    self._ollama_start_attempted = True
                    ollama = shutil.which("ollama")
                    if not ollama:
                        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
                        ollama = str(candidate) if candidate.is_file() else None
                    if not ollama:
                        return False
                    try:
                        self._ollama_process = subprocess.Popen(
                            [ollama, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    except OSError:
                        return False
                elif attempt == 0:
                    return False
            if local_server:
                time.sleep(0.5)
        return False

    @staticmethod
    def _provider_resume_args(args, provider):
        cleaned = list(args)
        for index in range(len(cleaned) - 1, 0, -1):
            if cleaned[index - 1] != "--resume":
                continue
            session_id = str(cleaned[index])
            if provider == "ollama" and session_id.startswith("ollama:"):
                cleaned[index] = session_id[len("ollama:"):]
            elif session_id.startswith(("gemini:", "ollama:")):
                del cleaned[index - 1:index + 1]
        return cleaned

    def _run_claude_stream(self, args, timeout, on_progress=None, on_session=None, should_stop=None):
        """Run Claude Code with responsive cancellation and a real wall-clock timeout."""
        args = self._provider_resume_args(args, "claude")
        command = [self.exe, "-p", *args, "--output-format", "stream-json", "--verbose",
                   "--model", MODEL]
        proc = subprocess.Popen(command, cwd=PROJECT_DIR, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = queue.Queue()
        stderr_chunks = []

        def read_stdout():
            try:
                for item in proc.stdout:
                    lines.put(item)
            finally:
                lines.put(None)

        def read_stderr():
            stderr_chunks.append(proc.stderr.read())

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        deadline = time.monotonic() + timeout
        result = None
        side_effect_started = False
        try:
            while True:
                if should_stop and should_stop():
                    raise OperationInterrupted()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    line = lines.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if on_session and event.get("session_id"):
                    on_session(event["session_id"])
                if event.get("type") == "result":
                    result = event
                elif event.get("type") == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use":
                            if block.get("name") in ("Bash", "Write", "Edit"):
                                side_effect_started = True
                            if on_progress:
                                on_progress(_describe_tool(block.get("name", ""), block.get("input", {})))
                        elif on_progress and block.get("type") == "text" and block.get("text", "").strip():
                            text = block["text"].strip()
                            if ANSWER_MARKER in text:
                                found = text.split(ANSWER_MARKER, 1)[1].strip().splitlines()[0]
                                on_progress(SPEAK_PREFIX + found)
                            else:
                                on_progress("考えています: " + text.splitlines()[0][:60])
        except Exception as error:
            if side_effect_started:
                error.side_effect_started = True
            raise
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        if not result:
            detail = "".join(stderr_chunks).strip()[:300]
            error = RuntimeError(detail or "Claude Code returned no response")
            if side_effect_started:
                error.side_effect_started = True
            raise error
        cleanup.register_session(result.get("session_id"))
        if result.get("is_error"):
            error = RuntimeError(result.get("result") or "Claude Code returned an error")
            if side_effect_started:
                error.side_effect_started = True
            raise error
        result["provider"] = "claude"
        return result

    def _run_stream(self, args, timeout, on_progress=None, on_session=None, should_stop=None):
        """Prefer free Gemini, then local Ollama, and use Claude only when needed."""
        last_error = None
        if self.gemini:
            try:
                return self._run_gemini_stream(args, timeout, on_progress, on_session, should_stop)
            except OperationInterrupted:
                raise
            except Exception as error:
                if getattr(error, "side_effect_started", False):
                    raise
                last_error = error
                self._gemini_sessions.pop(self._current_channel, None)
                if on_progress:
                    on_progress("Geminiが使えないため、ローカルAIを試します")

        has_image = any(re.search(r"attachments/[^\s]+\.(?:png|jpe?g|webp)", str(arg), re.IGNORECASE)
                        for arg in args)
        needs_web = "WebSearch" in args or "WebFetch" in args
        if not has_image and not needs_web and self._local_model_ready():
            try:
                return self._run_ollama(args, timeout, on_progress, on_session, should_stop)
            except OperationInterrupted:
                raise
            except InterruptedError as error:
                if should_stop and should_stop():
                    raise OperationInterrupted() from error
                if getattr(error, "side_effect_started", False):
                    raise
                last_error = error
            except Exception as error:
                if getattr(error, "side_effect_started", False):
                    raise
                last_error = error

        if self.exe:
            try:
                if on_progress:
                    on_progress("無料のAIが使えないため、Claude Codeを試します")
                return self._run_claude_stream(args, timeout, on_progress, on_session, should_stop)
            except OperationInterrupted:
                raise
            except Exception as error:
                if getattr(error, "side_effect_started", False):
                    raise
                last_error = error
                self._gemini_sessions.pop(self._current_channel, None)

        if last_error:
            raise last_error
        raise RuntimeError("利用できるAIプロバイダーがありません")

    def _run_ollama(self, args, timeout, on_progress=None, on_session=None, should_stop=None):
        """Call the local model directly with a compact prompt and tool loop."""
        prompt = str(args[0]) if args else ""
        system, tool_names, allowed, resume = "", [], [], None
        index = 1
        while index < len(args):
            arg = args[index]
            if arg == "--system-prompt" and index + 1 < len(args):
                system = str(args[index + 1]); index += 2; continue
            if arg == "--tools" and index + 1 < len(args):
                tool_names = [item.strip() for item in str(args[index + 1]).split(",") if item.strip()]
                index += 2; continue
            if arg == "--allowedTools":
                index += 1
                while index < len(args) and not str(args[index]).startswith("--"):
                    allowed.append(str(args[index])); index += 1
                continue
            if arg == "--resume" and index + 1 < len(args):
                resume = str(args[index + 1]); index += 2; continue
            index += 1
        channel = self._current_channel
        history = self._ollama_sessions.get(channel, []) if resume and resume.startswith("ollama:") else []
        if channel == "chat":
            # The standard voice prompt contains long instructions for every JARVIS feature.
            # Keep the local model's context small; the cloud fallback retains the full prompt.
            remembered = load_memory().strip()[:800]
            system = ("You are JARVIS, a helpful Japanese voice assistant. Reply naturally and briefly "
                      "in polite Japanese (one to three sentences). Use only the supplied tools for actions; "
                      "never claim an action succeeded unless a tool confirms it. If you need live web access "
                      "or a tool you do not have, say so honestly. For actions, use Bash with "
                      "python jarvis_tools.py and one command: weather, news, search, schedule, "
                      "add-schedule, play, music, open-site, open-app, open-url, thm-page, scan, "
                      "vpn, kali, read-screen, or recall. Ask before mouse/keyboard actions; never "
                      "enter passwords or claim an action succeeded without its tool result. Current date: "
                      + datetime.now().strftime("%Y-%m-%d")
                      + ("\nUser preferences: " + remembered if remembered else "")
                      )
        result = ollama_agent.run(OLLAMA_URL, LOCAL_RUNTIME_MODEL, prompt, system, tool_names,
                                  allowed, PROJECT_DIR, history=history, timeout=timeout,
                                  on_progress=on_progress, on_session=on_session,
                                  should_stop=should_stop)
        self._ollama_sessions[channel] = result.get("messages", [])
        return result

    def _run_gemini_stream(self, args, timeout, on_progress=None, on_session=None, should_stop=None):
        prompt = args[0] if args else ""
        if "--system-prompt" in args:
            index = args.index("--system-prompt")
            if index + 1 < len(args):
                prompt = args[index + 1] + "\n\n" + prompt
        if self._fallback_context:
            prompt = f"直前までの会話:\n{self._fallback_context}\n\n今回の依頼:\n{prompt}"
        node, bundle = self.gemini
        command = [node, bundle, "--prompt", prompt, "--output-format", "stream-json",
                   "--approval-mode", "auto_edit"]
        # Gemini の自動実行は、既存の Claude 許可設定をそのまま引き継がない。
        # 非対話実行ではシェルを禁止し、チャット/画面操作/成果物生成では書き込みも禁止する。
        # 制限されたツールが必要な依頼は、実行前に失敗として検出して Claude に渡す。
        policy_text = ('[[rule]]\ntoolName = "run_shell_command"\ndecision = "deny"\npriority = 1\n')
        if "Bash(python jarvis_tools.py:*)" in args:
            policy_text += ('\n[[rule]]\ntoolName = "run_shell_command"\n'
                            'commandRegex = \'"command":"python jarvis_tools\\.py [a-z-]+[^;&|<>]*"\'\n'
                            'decision = "allow"\npriority = 999\n')
        if self._current_channel == "dev":
            # 開発時に限り、構文確認と JARVIS の既存ツールだけを許可する。
            policy_text += ('\n[[rule]]\ntoolName = "run_shell_command"\n'
                            'commandRegex = \'"command":"python -m py_compile [^;&|<>]*"\'\n'
                            'decision = "allow"\npriority = 999\n')
            policy_text += ('\n[[rule]]\ntoolName = "run_shell_command"\n'
                            'commandRegex = \'"command":"python jarvis_tools\\.py [a-z-]+[^;&|<>]*"\'\n'
                            'decision = "allow"\npriority = 999\n')
        if self._current_channel != "dev":
            policy_text += ('\n[[rule]]\ntoolName = ["write_file", "replace"]\n'
                            'decision = "deny"\npriority = 999\n')
        policy_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".toml", delete=False)
        policy_file.write(policy_text)
        policy_file.close()
        command += ["--policy", policy_file.name]
        previous = self._gemini_sessions.get(self._current_channel)
        if previous:
            command += ["--resume", previous]
        proc = subprocess.Popen(command, cwd=PROJECT_DIR, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = queue.Queue()
        def read_stdout():
            try:
                for line in proc.stdout:
                    lines.put(line)
            finally:
                lines.put(None)
        def read_stderr():
            lines.put(("__stderr__", proc.stderr.read()))
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        deadline = time.time() + timeout
        output, session_id, result_event, side_effect_started = [], None, None, False
        pending_tools = {}
        stderr = ""
        try:
            while True:
                if should_stop and should_stop():
                    raise OperationInterrupted()
                if time.time() > deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    if proc.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                if isinstance(line, tuple):
                    stderr = line[1]
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "init":
                    session_id = event.get("session_id") or session_id
                    if session_id:
                        self._gemini_sessions[self._current_channel] = session_id
                        if on_session:
                            on_session("gemini:" + session_id)
                elif event.get("type") == "message":
                    if event.get("role") == "assistant" and isinstance(event.get("content"), str):
                        content = event["content"]
                        if event.get("delta"):
                            output.append(content)
                            if on_progress and content.strip():
                                first = content.strip().splitlines()[0][:60]
                                on_progress("考えています: " + first)
                elif event.get("type") == "tool_use":
                    tool = event.get("tool_name", "")
                    params = event.get("parameters", {})
                    pending_tools[event.get("tool_id")] = tool
                    shell_command = params.get("command", "") if isinstance(params, dict) else ""
                    safe_dev_command = (re.match(r"^python -m py_compile [^;&|<>]*$", shell_command)
                                        or re.match(r"^python jarvis_tools\.py [a-z-]+[^;&|<>]*$", shell_command))
                    safe_jarvis_command = re.match(
                        r"^python jarvis_tools\.py [a-z-]+[^;&|<>]*$", shell_command)
                    if (self._current_channel == "dev" and tool in ("write_file", "replace")) or (
                            tool == "run_shell_command" and (safe_dev_command or safe_jarvis_command)):
                        # 中断時に同じ開発操作を二度実行しない。
                        side_effect_started = True
                    if on_progress:
                        aliases = {"read_file": "Read", "write_file": "Write", "replace": "Edit",
                                   "run_shell_command": "Bash", "glob": "Glob", "grep_search": "Grep"}
                        on_progress(_describe_tool(aliases.get(tool, tool), params))
                elif event.get("type") == "tool_result":
                    tool = pending_tools.pop(event.get("tool_id"), "")
                    status = str(event.get("status", "")).lower()
                    if status in ("success", "ok") and tool in ("write_file", "replace"):
                        side_effect_started = True
                    elif status in ("success", "ok") and tool == "run_shell_command":
                        side_effect_started = True
                    elif status in ("error", "failed"):
                        error = RuntimeError(str(event.get("error") or event.get("output") or
                                                 "Gemini のツール実行でエラーが起きました")[:400])
                        if side_effect_started:
                            error.side_effect_started = True
                        raise error
                elif event.get("type") == "result":
                    result_event = event
        except Exception as error:
            if side_effect_started:
                error.side_effect_started = True
            raise
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                Path(policy_file.name).unlink(missing_ok=True)
            except OSError:
                pass
        if proc.returncode not in (0, None) or not result_event or result_event.get("status") != "success":
            error = RuntimeError(str((result_event or {}).get("error") or stderr or "Gemini CLI から応答がありません")[:500])
            if side_effect_started:
                error.side_effect_started = True
            raise error
        return {"result": "".join(output).strip(), "session_id": "gemini:" + session_id if session_id else None,
                "provider": "gemini"}

    def _run(self, args, timeout):
        """Prefer free Gemini, then local Ollama, and use Claude only when needed."""
        last_error = None
        if self.gemini:
            try:
                return self._run_gemini_stream(args, timeout)
            except Exception as error:
                if getattr(error, "side_effect_started", False):
                    raise
                last_error = error
                self._gemini_sessions.pop(self._current_channel, None)

        has_image = any(re.search(r"attachments/[^\s]+\.(?:png|jpe?g|webp)", str(arg), re.IGNORECASE)
                        for arg in args)
        needs_web = "WebSearch" in args or "WebFetch" in args
        if not has_image and not needs_web and self._local_model_ready():
            try:
                return self._run_ollama(args, timeout)
            except Exception as error:
                if getattr(error, "side_effect_started", False):
                    raise
                last_error = error

        if self.exe:
            try:
                return self._run_claude_stream(args, timeout)
            except Exception as error:
                if getattr(error, "side_effect_started", False):
                    raise
                last_error = error

        if last_error:
            raise last_error
        raise RuntimeError("利用できるAIプロバイダーがありません")

    @staticmethod
    def _without_gemini_resume(args):
        cleaned = list(args)
        for i in range(len(cleaned) - 1, 0, -1):
            if cleaned[i - 1] == "--resume" and str(cleaned[i]).startswith("gemini:"):
                del cleaned[i - 1:i + 1]
        return cleaned
    def ask(self, text, images=(), context=""):
        """ユーザーの言葉を AI に送り、返事を返す。
        開発の依頼なら DEV_MARKER で始まる文字列を返す。
        context には直前までの画面上の会話を渡す。session_id が切れている (再起動直後や、
        改修・自動操作のあとで会話がリセットされた) ときでも、直前のやり取りを踏まえて
        答えられるようにするため (「その改修は駄目」「それを使って」のような指示語が通じるように)"""
        if time.time() - self.last_used > IDLE_RESET_SECONDS:
            self.session_id = None  # 新しい会話
            self._gemini_sessions.pop("chat", None)
        self.last_used = time.time()
        self._current_channel = "chat"

        system = SYSTEM.format(marker=DEV_MARKER, today=datetime.now().strftime("%Y年%m月%d日"),
                               memory=load_memory(), records=_memory_context_for_prompt(text))
        prompt = text
        if context.strip() and not self.session_id:  # 会話が続いていない(リセット後)ときだけ、履歴を補う
            prompt = (f"これまでの画面上の会話 (文脈。指示語や「さっきの」はこれを手がかりに):\n{context.strip()}\n\n"
                      f"ユーザーの今の発言:\n{text}")
        self._fallback_context = context.strip() if self.session_id else ""
        args = [with_images(prompt, images), "--system-prompt", system, "--tools", "Bash,Read",
                "--allowedTools", "Bash(python jarvis_tools.py:*)", "Read(attachments/**)"]
        if self.session_id:
            args += ["--resume", self.session_id]  # 会話の続き
        try:
            data = self._run(args, TIMEOUT_SECONDS)
        except Exception:
            self.session_id = None
            raise
        self.session_id = data.get("session_id")
        return (data.get("result") or "").strip() or "すみません、うまく答えられませんでした。"

    def work(self, request, on_progress=None, images=()):
        """調査・分析・設計などの作業をする (作業モード)。(読み上げる返事, 成果物のファイル or None) を返す。
        30分以内なら前の作業の続きとして頼める。プログラムの変更が必要なときは、返事が DEV_MARKER で始まる"""
        DOCS_DIR.mkdir(exist_ok=True)
        self._current_channel = "work"
        self.session_id = None  # 作業も別セッション。あとの会話で直前のやり取りを文脈として渡し直せるよう区切る
        before = set(DOCS_DIR.glob("*.md"))
        if self.work_active():  # 続きの依頼: 前の作業の会話にそのまま送る
            prompt = request + "\n\n(前の作業の続きです。成果物の更新は docs のファイルに。最後の返事は話し言葉2〜3文で)"
            extra = ["--resume", self.work_session_id]
        else:
            prompt = WORK_PROMPT.format(request=request, stamp=datetime.now().strftime("%Y%m%d-%H%M"),
                                        marker=DEV_MARKER, records=_memory_context_for_prompt(request))
            extra = []
        prompt = with_images(prompt, images)
        allowed = ["Read", "Glob", "Grep", "Edit(docs/**)", "Write(docs/**)", "Bash(python jarvis_tools.py:*)"]
        data = self._run_stream([prompt, "--tools", "Read,Glob,Grep,Edit,Write,Bash", "--allowedTools", *allowed,
                                 *extra], WORK_TIMEOUT_SECONDS, on_progress)
        self.work_session_id, self.last_work = data.get("session_id"), time.time()
        reply = (data.get("result") or "").strip() or "作業が終わりました。"
        made = sorted(set(DOCS_DIR.glob("*.md")) - before, key=lambda p: p.stat().st_mtime)
        return reply, (made[-1] if made else None)

    def make_workflow(self, goal, on_progress=None):
        """n8n のワークフロー JSON を生成して workflows/ に保存する。(読み上げる返事, 生成したファイル or None)。"""
        WORKFLOWS_DIR.mkdir(exist_ok=True)
        self._current_channel = "workflow"
        self.session_id = None  # ワークフロー生成は別セッション
        before = set(WORKFLOWS_DIR.glob("*.json"))
        prompt = WORKFLOW_PROMPT.format(goal=goal, stamp=datetime.now().strftime("%Y%m%d-%H%M"),
                                        records=_memory_context_for_prompt(goal))
        allowed = ["Read", "Glob", "Grep", "Write(workflows/**)", "WebSearch", "WebFetch"]
        data = self._run_stream([prompt, "--tools", "Read,Glob,Grep,Write,WebSearch,WebFetch",
                                 "--allowedTools", *allowed, "--permission-mode", "acceptEdits"],
                                WORK_TIMEOUT_SECONDS, on_progress)
        reply = (data.get("result") or "").strip() or "ワークフローを作りました。"
        made = sorted(set(WORKFLOWS_DIR.glob("*.json")) - before, key=lambda p: p.stat().st_mtime)
        return reply, (made[-1] if made else None)

    def operate(self, goal, context="", on_progress=None, should_stop=None, resume=False):
        """画面を見ながらマウス・キーボードを自分で操作して目標を達成する (自動操作モード)。
        読み上げる報告を返す。使えるのは画面操作系のコマンドだけで、
        危険・不可逆な操作には当たらないよう安全のルールを課している (プログラムの書き換えはできない)。
        context には直前までの会話を渡す (operateは新しいセッションなので、これが無いと
        「進めて」のような短い目標が何を指すのか分からず動けない)。
        resume=True なら、時間切れで一区切りした前回の自動操作に繋いで (--resume) 続きから進める"""
        # 自動操作は別セッションなので、ここでチャットの会話をいったん区切る。こうしないと、
        # 操作のあとに話しかけたとき古いチャットの続き (--resume) が蘇り、操作の前後で話したことを
        # 知らないまま答えてしまう (「すぐ忘れる」)。区切っておけば、次の発言で直前のやり取りを文脈として渡し直せる
        self.session_id = None
        self._current_channel = "operate"
        # WebSearch/WebFetch は、行き詰まったときに攻略記事(ウォークスルー)を自分で探して参考にするため
        allowed = ["Bash(python jarvis_tools.py:*)", "Read(attachments/**)", "WebSearch", "WebFetch"]
        extra = []
        if resume and self.operate_session_id:
            prompt = OPERATE_RESUME_PROMPT
            extra = ["--resume", self.operate_session_id]
        else:
            prompt = OPERATE_PROMPT.format(goal=goal, context=context.strip() or "(特になし)",
                                           records=_memory_context_for_prompt(goal), fix_marker=FIX_MARKER,
                                           answer_marker=ANSWER_MARKER)

        def on_session(sid):
            if sid and (not self.operate_session_id or self.operate_session_id.startswith(("gemini:", "ollama:"))):
                self.operate_session_id = sid

        try:
            data = self._run_stream([prompt, "--tools", "Bash,Read,WebSearch,WebFetch",
                                     "--allowedTools", *allowed, *extra],
                                    OPERATE_TIMEOUT_SECONDS, on_progress, on_session=on_session,
                                    should_stop=should_stop)
        except subprocess.TimeoutExpired:
            raise  # 時間切れは do_operate 側で「続きから再開できます」と伝える
        if data.get("session_id") and (not self.operate_session_id or
                                        self.operate_session_id.startswith(("gemini:", "ollama:")) and
                                        not data["session_id"].startswith(("gemini:", "ollama:"))):
            self.operate_session_id = data["session_id"]
        return (data.get("result") or "").strip() or "自動操作が終わりました。"

    def _set_dev_resume(self, value):
        """途中で止まった改修の再開情報を覚える/消す (再起動をまたいでも続きから再開できるようファイルにも保存)"""
        self.dev_resume = value
        try:
            if value:
                DEV_RESUME_FILE.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            else:
                DEV_RESUME_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def develop(self, request, resume=False):
        """JARVIS のプログラムを書き換えて機能を追加する。(成否, 読み上げる報告) を返す。
        タイムアウトやエラーで途中で止まったら、改修セッションを覚えておき (dev_resume)、
        あとで resume=True で呼ぶと --resume で続きから仕上げられる (『続きをやって』で再開)"""
        backup = BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
        self._current_channel = "dev"
        backup.mkdir(parents=True)
        files = [p for p in PROJECT_DIR.iterdir() if p.suffix in (".py", ".json") and p.is_file()]
        for p in files:
            shutil.copy2(p, backup / p.name)

        # 続きから再開するときは、前回の改修セッションにそのまま繋いで (--resume) 残りを仕上げてもらう
        if resume and self.dev_resume:
            request = self.dev_resume.get("request", request)
            prompt = DEV_RESUME_PROMPT.format(request=request)
            extra = ["--resume", self.dev_resume["session_id"]]
        else:
            prompt = DEV_PROMPT.format(request=request)
            extra = []

        captured = {}
        def on_session(sid):
            if sid and (not captured.get("id") or
                        captured["id"].startswith(("gemini:", "ollama:")) and
                        not sid.startswith(("gemini:", "ollama:"))):
                captured["id"] = sid  # 再開可能なプロバイダーのIDを優先して控える

        tools = "Read,Edit,Write,Glob,Grep,Bash"
        allowed = ["Read", "Edit", "Write", "Glob", "Grep",
                   "Bash(python -m py_compile:*)", "Bash(python jarvis_tools.py:*)"]
        interrupted = error = report = None
        try:
            data = self._run_stream([prompt, "--tools", tools, "--allowedTools", *allowed,
                                     "--permission-mode", "acceptEdits", *extra],
                                    DEV_TIMEOUT_SECONDS, on_session=on_session)
            report = (data.get("result") or "").strip()
            if data.get("session_id"):
                captured.setdefault("id", data["session_id"])
        except subprocess.TimeoutExpired:
            interrupted, error = True, "時間がかかりすぎた"
        except Exception as e:
            interrupted, error = True, f"エラーが起きた ({e})"

        broken = []
        for p in PROJECT_DIR.glob("*.py"):
            try:
                py_compile.compile(str(p), doraise=True)
            except py_compile.PyCompileError:
                broken.append(p.name)
        if broken:
            self.restore(backup)  # 文法が壊れていたら必ず元に戻す (JARVIS が動かなくなるのを防ぐ)

        if interrupted:
            if captured.get("id"):  # 続きから再開できるよう、セッションと依頼を覚えておく
                self._set_dev_resume({"session_id": captured["id"], "request": request})
                kept = "元に戻しました。" if broken else "途中までの変更はそのままにしてあります。"
                return False, (f"改修が{error}ので、いったん中断しました。{kept}"
                               "「続きをやって」と言っていただければ、続きから進めます。")
            self._set_dev_resume(None)
            return False, f"改修が{error}ので中断しました。もう一度お願いできますか？"

        if broken:
            self._set_dev_resume(None)
            return False, f"作業中に {', '.join(broken)} に文法の間違いがあったので、元の状態に戻しました。"
        self._set_dev_resume(None)  # 無事に終わったので再開情報は消す
        self.session_id = None  # 機能が変わったので会話を新しくする
        return True, report or "変更が終わりました。"

    @staticmethod
    def restore(backup):
        """バックアップの状態に戻す (書き換えで新しく作られた .py は消す)"""
        saved = {p.name for p in backup.iterdir()}
        for p in PROJECT_DIR.glob("*.py"):
            if p.name not in saved:
                p.unlink()
        for p in backup.iterdir():
            shutil.copy2(p, PROJECT_DIR / p.name)

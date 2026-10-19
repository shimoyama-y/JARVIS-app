"""AI が JARVIS の機能を使うためのコマンド

使い方 (AI が Bash から実行する):
  python jarvis_tools.py weather 東京
  python jarvis_tools.py news IT            (最新/国内/世界/経済/エンタメ/スポーツ/IT)
  python jarvis_tools.py search 大谷翔平
  python jarvis_tools.py schedule
  python jarvis_tools.py add-schedule 15:00 会議
  python jarvis_tools.py play "YOASOBI アイドル"
  python jarvis_tools.py music 止めて        (止めて/次の曲/前の曲)
  python jarvis_tools.py open-site YouTube
  python jarvis_tools.py open-url https://tryhackme.com/room/basicpentestingjt  (指定URLをブラウザで開く)
  python jarvis_tools.py open-app メモ帳     (メモ帳/電卓/フォルダ/PowerShell)
  python jarvis_tools.py open-app PowerShell (PowerShell 7があれば優先、無ければ従来のPowerShellを新しいウィンドウで起動)
  python jarvis_tools.py volume 上げる       (上げる/下げる/ミュート/0〜100)
  python jarvis_tools.py settings wifi       (設定/ディスプレイ/サウンド/wifi/bluetooth/電源/通知/壁紙/windows update)
  python jarvis_tools.py remember 好きな飲み物はコーヒー   (MEMORY.md に書き足す)
  python jarvis_tools.py forget コーヒー                   (その言葉を含む記憶を消す)
  python jarvis_tools.py learn --topic SQLインジェクション --summary "要約" --tags web,owasp --importance 7
  python jarvis_tools.py secnote --topic SSRF対策 --summary "要約" --tags web --importance 8 --source PortSwigger
  python jarvis_tools.py thm --room Blue --status 完了 --progress 全問正解 --notes "EternalBlue を学んだ"
  python jarvis_tools.py thm-page https://tryhackme.com/room/basicpentestingjt  (ルームの詳細を取得)
  python jarvis_tools.py thm-page BasicPentestingJT                            (ルーム名でもよい)
  python jarvis_tools.py scan 10.48.170.229   (認可済みのCTF/学習用ターゲットのポートを調べる。「10.48.170.229 ポート 1-1000」も可。nmap不要)
  python jarvis_tools.py vpn connect          (Kali(WSL)のopenvpnでVPNに接続。TryHackMeの.ovpn設定を自動で探す。パスを渡すこともできる)
  python jarvis_tools.py vpn connect "C:\\Users\\user\\Downloads\\thm.ovpn"  (設定ファイルを指定して接続)
  python jarvis_tools.py vpn status           (VPNにつながっているか確認) / vpn disconnect (切断)
  python jarvis_tools.py kali "nmap -sV 10.49.177.63"  (Kali(WSL)のターミナルでコマンドを実行して結果を返す。ブラウザのAttackBoxが使えないときの操作に使う)
  python jarvis_tools.py read-screen         (画面全体を撮影して文字を読み取る。「read-screen 前面」で前面のウィンドウだけ。IPアドレスなど画面上の情報を知りたいときに使う)
  python jarvis_tools.py capture-screen      (read-screenと同じ方法で撮影してOCRにかけるが、撮影した画像ファイルを消さずに残す。
    「capture-screen 前面」も可。OCRで読み取りにくい画像中心のページやボタンの見た目を、Readツールで画像を直接見て確認したいときに使う)
  python jarvis_tools.py click-screen "Start AttackBox"   (画面上のそのボタンの文字を探してクリックする)
  python jarvis_tools.py click-screen "OpenVPN Connect"    (文字の無いタスクトレイのアイコンも、隠れているものを開いて名前で探す)
  python jarvis_tools.py click-screen "120,340"            (座標を直接指定してクリックする)
  python jarvis_tools.py click-screen "development の近くの入力欄"  (ラベルの無い入力欄を、近くの文字を手がかりに探してクリック)
  python jarvis_tools.py click-near "hidden directory|下|60"  (その文字の下/右/左/上の近くをクリック。空の入力欄など文字が無い所を、近くの文字を手がかりに押す。方向は既定で下、距離pxは省略可)
  python jarvis_tools.py focus-window Chrome                (タイトルに「Chrome」を含むウィンドウを前面に出す。他のウィンドウが重なって読み取れないときに使う)
  python jarvis_tools.py switch-tab "Index of development"  (ブラウザのタブをページ名で切り替える。focus-windowはウィンドウ単位なので、別タブのページを読むときはこちら)
  python jarvis_tools.py type-text "こんにちは"             (今の入力欄に文字を入れる。「--replace」で置き換え、「--scan コマンド
」でリモート端末に打つ)
  python jarvis_tools.py click-type "Check|左|150 >> --replace 8881"  (入力欄をクリックして、そのまま続けて入力する。
    クリックと入力を別々にすると文字が入らないとき(フォーカスが外れる)に使う。「指定 >> 文字」の形で、指定は click-screen/click-near と同じ)
  python jarvis_tools.py press-keys "ctrl+w"               (キーボードのショートカットを送る。ctrl+w=タブを閉じる、ctrl+shift+w=ウィンドウを閉じる、alt+f4、f5、esc、ctrl+-/ctrl+=でブラウザのズーム、ctrl+0でズームを戻す など)
  python jarvis_tools.py scroll-screen "下"                 (画面を下にスクロールして画面外を表示。「上」「下 5」も可)
  python jarvis_tools.py organize-windows                  (開いているウィンドウを左右に並べて表示。「上下」「重ねる」「最小化」(デスクトップを表示)「元に戻す」も可。JARVISの画面は動かさない)
  python jarvis_tools.py close-window                      (JARVIS自身の画面だけを閉じる。他のウィンドウは動かさない。「画面を閉じて」で使う。
    声でのやりとりは続けられる)
  python jarvis_tools.py show-window                       (close-windowで隠れたJARVISの画面を再表示する。「画面を出して」「画面を表示して」で使う)
  python jarvis_tools.py organize-desktop                  (デスクトップのアイコンを名前順に等間隔で整列。「種類」「日付」「サイズ」順も可。ファイルは動かさない)
  python jarvis_tools.py profile --field goals --value OSCP取得
  python jarvis_tools.py recall SQL                         (全ての記録から探す)
  python jarvis_tools.py memory-source cold:<UUID>          (記憶に紐づくローカル会話原文を読む)
  python jarvis_tools.py memory-check                     (Supabase と Gemini の設定・接続を検証)
  python jarvis_tools.py memory-migrate                    (既存のJSON記憶を空のSupabaseへ一度だけ移行)
  python jarvis_tools.py memory-decay [日数] [重要度以下]    (長期間使われていない低優先度のCold記憶を忘却。既定: 90日・重要度4以下)
  python jarvis_tools.py know save --category owasp --title XSS --content "仕組みと対策" --source OWASP
  python jarvis_tools.py know search 権限昇格 [--category linux]   (ノートから探す)
  python jarvis_tools.py know read --category owasp --title XSS     (ノートを読む)
  python jarvis_tools.py know list [owasp]                          (ノートの一覧)
    分野: tryhackme / owasp / linux / windows / networking / active_directory / ai
  python jarvis_tools.py remote-access       (外出先からの接続状況を確認)
  python jarvis_tools.py changes             (GitHub に保存していない変更の一覧)
  python jarvis_tools.py commit "チャットでコピーできるようにする"   (変更をコミットして GitHub に Push)
  python jarvis_tools.py growth              (ジャービスの成長記録を読む)
  python jarvis_tools.py growth-add "画面を見て自分で操作できるようになった"  (成長記録に1行を書き足す。ユーザーに「成長記録を作って/書いて」と頼まれたときだけ使う)
  python jarvis_tools.py system-log          (Windowsのイベントログから、直近24時間のエラー/重大イベントと
    再起動関連のイベントを取得する。再起動・異常終了の原因調査に使う)
  python jarvis_tools.py system-log 72       (時間数を指定。直近72時間分を調べる)
  python jarvis_tools.py system-log 24 all   (2つめの引数を all にすると、警告レベルも含めて調べる)
  python jarvis_tools.py check-security      (このPCの基本的な脆弱性チェック。OS情報と最終起動時刻、
    Windows Defenderの状態、ファイアウォールの状態、開いているポートと待ち受けプロセスを取得する)

結果は「読み上げ用の文」と「画面表示用の文」を出力する。
画面表示用の文は SHOW: で始まる行以降に出す (JARVIS が画面に表示する)。
"""
import json
import sys

import argparse

import features
import memory_store
import memory_tiering

sys.stdout.reconfigure(encoding="utf-8")


def _memory_command(cmd, argv):
    p = argparse.ArgumentParser(prog=cmd)
    if cmd in ("learn", "secnote"):
        p.add_argument("--topic", required=True)
        p.add_argument("--summary", required=True)
        p.add_argument("--tags", default="")
        p.add_argument("--importance", type=int, default=5)
        p.add_argument("--source", default="")
    elif cmd == "thm":
        p.add_argument("--room", required=True)
        p.add_argument("--status", default="進行中", choices=["未着手", "進行中", "完了"])
        p.add_argument("--progress", default="")
        p.add_argument("--notes", default="")
        p.add_argument("--tags", default="")
    else:
        p.add_argument("--field", required=True, choices=["name", "interests", "goals", "preferences"])
        p.add_argument("--value", required=True)
    a = p.parse_args(argv)
    if cmd == "learn":
        return memory_store.add_learning(a.topic, a.summary, a.tags, a.importance)
    if cmd == "secnote":
        return memory_store.add_security(a.topic, a.summary, a.tags, a.importance, a.source)
    if cmd == "thm":
        return memory_store.update_room(a.room, a.status, a.progress, a.notes, a.tags)
    return memory_store.update_profile(a.field, a.value)


def _know_command(argv):
    import knowledge
    if not argv:
        return None
    action, rest = argv[0], argv[1:]
    p = argparse.ArgumentParser(prog=f"know {action}")
    if action == "save":
        p.add_argument("--category", required=True, choices=list(knowledge.CATEGORIES))
        p.add_argument("--title", required=True)
        p.add_argument("--content", required=True)
        p.add_argument("--source", default="")
        a = p.parse_args(rest)
        return knowledge.save(a.category, a.title, a.content, a.source)
    if action == "search":
        p.add_argument("keyword")
        p.add_argument("--category", default=None)
        a = p.parse_args(rest)
        return knowledge.search(a.keyword, a.category)
    if action == "read":
        p.add_argument("--category", required=True, choices=list(knowledge.CATEGORIES))
        p.add_argument("--title", required=True)
        a = p.parse_args(rest)
        return knowledge.read(a.category, a.title)
    if action == "list":
        return knowledge.listing(rest[0] if rest else None)
    return None


def _migrate_memory():
    if not memory_tiering.enabled():
        raise RuntimeError("Set JARVIS_MEMORY_BACKEND=supabase before importing legacy memory")
    payloads = {}
    for kind in memory_store.NAMES:
        path = memory_store.FILES[kind]
        if path.exists():
            try:
                payloads[kind] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"Could not read legacy memory file {path.name}") from error
        else:
            payloads[kind] = memory_store.DEFAULT_PROFILE.copy() if kind == "profile" else []
    count = memory_tiering.migrate_local_data(payloads)
    return f"既存の記憶 {count} 件をSupabaseへ移行しました。", f"■ Supabase記憶移行\n登録件数: {count}"


def _decay_memory(rest):
    if not memory_tiering.enabled():
        raise RuntimeError("Set JARVIS_MEMORY_BACKEND=supabase to manage cloud memories")
    parts = rest.split()
    days = int(parts[0]) if len(parts) >= 1 and parts[0].isdigit() else 90
    importance = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 4
    count = memory_tiering.decay_memories(days, importance)
    return (f"長い間使われていない記憶を {count} 件、整理しました。",
            f"■ 記憶の整理 (忘却・減衰)\n{days}日以上未アクセス・重要度{importance}以下のCold記憶を {count} 件削除")


def main(args):
    if not args:
        return None
    cmd, rest = args[0], " ".join(args[1:]).strip()
    if cmd == "weather":
        return features.weather(f"{rest}の天気" if rest else "")
    if cmd == "news":
        return features.news(rest)
    if cmd == "search":
        results = features.DDGS().text(rest, region="jp-jp", max_results=5)
        show = f"■ 検索: {rest}\n" + "\n".join(f"・{r['title']}" for r in results)
        return json.dumps(results, ensure_ascii=False), show
    if cmd == "schedule":
        return features.schedule()
    if cmd == "add-schedule" and len(args) >= 3:
        return features.add_schedule(f"{args[1]}に{' '.join(args[2:])}を予定に追加")
    if cmd == "play":
        return features.play_music(rest)
    if cmd == "music":
        return features.media_key(rest)
    if cmd == "open-site":
        return features.open_site(f"{rest}を開いて")
    if cmd == "open-url":
        return features.open_url(rest)
    if cmd == "open-app":
        return features.pc_action(f"{rest}を開いて")
    if cmd == "open-terminal":
        return features.open_terminal(rest)
    if cmd == "vpn":  # Kali(WSL) の openvpn で VPN 接続・状態確認・切断
        return features.vpn(rest)
    if cmd == "kali":  # Kali(WSL) のターミナルでコマンドを実行して結果を返す
        return features.kali(rest)
    if cmd == "thm-page":
        return features.thm_page(rest)
    if cmd == "scan":
        return features.scan_ports(rest)
    if cmd == "read-screen":
        return features.read_screen(rest)
    if cmd == "capture-screen":
        return features.capture_screen(rest)
    if cmd == "click-screen":
        return features.click_screen(rest)
    if cmd == "click-near":
        return features.click_near(rest)
    if cmd == "type-text":
        return features.type_text(rest)
    if cmd == "click-type":  # クリックと入力を1プロセスで連続実行 (クリックしたカーソルが生きたまま打つ)
        return features.click_type(rest)
    if cmd == "press-keys":
        return features.press_keys(rest)
    if cmd == "scroll-screen":
        return features.scroll_screen(rest)
    if cmd == "focus-window":
        return features.focus_window(rest)
    if cmd == "switch-tab":
        return features.switch_tab(rest)
    if cmd == "organize-windows":
        return features.organize_windows(rest)
    if cmd == "close-window":
        return features.close_window(rest)
    if cmd == "show-window":
        return features.show_window(rest)
    if cmd == "organize-desktop":
        return features.organize_desktop(rest)
    if cmd == "volume":
        return features.volume(rest)
    if cmd == "settings":
        return features.open_settings(rest)
    if cmd in ("learn", "secnote", "thm", "profile"):  # 長期記憶 (memory フォルダ)
        return _memory_command(cmd, args[1:])
    if cmd == "know":  # 知識ベース (分野ごとの Markdown のノート)
        return _know_command(args[1:])
    if cmd == "recall":
        return memory_store.recall(rest)
    if cmd == "memory-source":
        return memory_store.read_cold_archive(rest)
    if cmd == "memory-check":
        memory_tiering.check_connections()
        return "記憶サービスへ接続できました。", "■ 記憶サービス接続確認\nSupabase・Geminiとも正常です。記録の追加・削除はしていません。"
    if cmd == "memory-migrate":
        return _migrate_memory()
    if cmd == "memory-decay":
        return _decay_memory(rest)
    if cmd == "remember":
        return features.remember(rest)
    if cmd == "forget":
        return features.forget(rest)
    if cmd == "remote-access":
        return features.remote_access_status()
    if cmd == "changes":  # GitHub に保存していない変更の一覧
        return features.git_changes()
    if cmd == "commit":  # 変更をコミットして GitHub に Push
        return features.git_commit(rest)
    if cmd == "growth":  # 成長記録を読む
        return features.growth_log()
    if cmd == "growth-add":  # 成長記録に 1 行を書き足す
        return features.growth_add(rest)
    if cmd == "system-log":  # Windowsイベントログから、エラー/重大イベントと再起動関連のイベントを調べる
        hours = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 24
        level = "all" if len(args) >= 3 and args[2] == "all" else "error"
        return features.get_system_log(hours, level)
    if cmd == "check-security":  # このPCの基本的な脆弱性チェック (OS情報・Defender・ファイアウォール・開いているポート)
        return features.check_pc_security()
    return None


if __name__ == "__main__":
    result = main(sys.argv[1:])
    if not result:
        print(__doc__)
        sys.exit(1)
    say, show = result
    print(say)
    print("SHOW:")
    print(show)

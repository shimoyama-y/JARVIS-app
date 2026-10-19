"""JARVIS の知識ベース (分野ごとの Markdown のノート)

knowledge/
├─ tryhackme/  ├─ owasp/  ├─ linux/  ├─ windows/
├─ networking/ ├─ active_directory/  └─ ai/

保存場所: Google ドライブの「マイドライブ\\JARVIS_knowledge」(人が読める Markdown なので、
iPhone の Google ドライブアプリからも読める)。つながらないときは、いったんパソコンの knowledge フォルダに
保存し、次につながったときに Google ドライブへ移す。
"""
import re
import shutil
from datetime import datetime
from pathlib import Path

import memory_store

CATEGORIES = {
    "tryhackme": "TryHackMe のルームの解き方・学んだこと",
    "owasp": "OWASP Top 10 と Web セキュリティ (XSS, SQLi, SSRF, CSRF など)",
    "linux": "Linux のコマンド・仕組み・権限昇格",
    "windows": "Windows・PowerShell・権限昇格",
    "networking": "ネットワーク (TCP/IP, Nmap, Wireshark など)",
    "active_directory": "Active Directory・Kerberos",
    "ai": "AI 開発・自動化",
}
LOCAL_DIR = Path(__file__).with_name("knowledge")
DRIVE_FOLDER = "JARVIS_knowledge"
README = "# JARVIS の知識ベース\n\n分野ごとの知識のノートです。JARVIS が会話の中で書き足し、聞かれたときに読み返します。\n" \
         "手で書き足したり直したりしてもかまいません。\n\n" + "\n".join(f"- {k}/ : {v}" for k, v in CATEGORIES.items()) + "\n"


def knowledge_dir():
    """今使う知識ベースのフォルダ。Google ドライブがあればそこ (パソコンに残っていたノートは移す)"""
    drive = memory_store._google_drive()
    root = drive / DRIVE_FOLDER if drive else LOCAL_DIR
    for cat in CATEGORIES:
        (root / cat).mkdir(parents=True, exist_ok=True)
    if not (root / "README.md").exists():
        (root / "README.md").write_text(README, encoding="utf-8")
    if drive and LOCAL_DIR.exists():
        _move_local_to(root)
    return root


def _move_local_to(root):
    for f in LOCAL_DIR.rglob("*.md"):
        if f.name == "README.md":
            continue
        dest = root / f.relative_to(LOCAL_DIR)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():  # 同じノートがあれば、パソコン側の内容を後ろに書き足す
            with dest.open("a", encoding="utf-8") as out:
                out.write("\n" + f.read_text(encoding="utf-8"))
            f.unlink()
        else:
            shutil.move(str(f), str(dest))
    shutil.rmtree(LOCAL_DIR, ignore_errors=True)


def _file_name(title):
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", title.strip())[:60].strip("_")
    return (name or "note") + ".md"


def save(category, title, content, source=""):
    """ノートを保存する。同じタイトルのノートがあれば、日付付きで書き足す"""
    if category not in CATEGORIES:
        return f"分野は {', '.join(CATEGORIES)} のどれかにしてください。", None
    path = knowledge_dir() / category / _file_name(title)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    section = f"\n## {stamp}\n\n{content.strip()}\n" + (f"\n出典: {source}\n" if source else "")
    if path.exists():
        with path.open("a", encoding="utf-8") as f:
            f.write(section)
        action = "書き足しました"
    else:
        path.write_text(f"# {title}\n\n分野: {category}\n" + section, encoding="utf-8")
        action = "作りました"
    return f"{category} の「{title}」のノートを{action}。", f"■ 知識ベース\n{category}/{path.name}"


def search(keyword, category=None, limit=5):
    """ノートから言葉を探す (タイトルに含まれるものを優先し、該当部分を抜き出す)"""
    root = knowledge_dir()
    files = (root / category).glob("*.md") if category in CATEGORIES else root.glob("*/*.md")
    kw = keyword.lower()
    hits = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        low = text.lower()
        if kw not in low and kw not in f.stem.lower():
            continue
        i = max(0, low.find(kw))
        snippet = " ".join(text[max(0, i - 80): i + 220].split())
        hits.append((kw in f.stem.lower(), f.stat().st_mtime, f"[{f.parent.name}/{f.stem}] {snippet}"))
    hits.sort(reverse=True)
    if not hits:
        return f"{keyword}についてのノートは見つかりませんでした。", "■ 知識ベース\n該当なし"
    lines = [h[2] for h in hits[:limit]]
    return "\n".join(lines), "■ 知識ベースの検索: " + keyword + "\n" + "\n".join(l[:80] for l in lines)


def read(category, title):
    path = knowledge_dir() / category / _file_name(title)
    if not path.exists():
        return f"{category} に「{title}」のノートはありません。", None
    text = path.read_text(encoding="utf-8")
    return text[-4000:], f"■ 知識ベース\n{category}/{path.name}"


def listing(category=None):
    root = knowledge_dir()
    cats = [category] if category in CATEGORIES else list(CATEGORIES)
    lines = []
    for c in cats:
        names = sorted(f.stem for f in (root / c).glob("*.md"))
        lines.append(f"{c}: {'、'.join(names) if names else '(まだなし)'}")
    return "\n".join(lines), "■ 知識ベース\n" + "\n".join(lines)

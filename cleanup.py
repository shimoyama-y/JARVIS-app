"""パソコンの容量を少しずつ使うものの自動お掃除 (JARVIS の起動時に1日1回)

- JARVIS が AI と話したときの Claude Code の会話記録: 30日より古いものを消す
  (VS Code での会話記録と同じフォルダにあるため、JARVIS が記録した会話の番号の分だけを消す)
- backups (機能追加の前の保存): 新しい10回分だけ残す
"""
import shutil
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
SESSIONS_FILE = PROJECT_DIR / "jarvis_sessions.txt"   # JARVIS が作った会話の番号の一覧
LAST_RUN_FILE = PROJECT_DIR / "backups" / ".last_cleanup"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
SESSION_DAYS = 30
KEEP_BACKUPS = 10


def register_session(session_id):
    """JARVIS が AI と話した会話の番号を記録する (お掃除のときに、これだけを消すため)"""
    if not session_id:
        return
    known = set(SESSIONS_FILE.read_text(encoding="utf-8").split()) if SESSIONS_FILE.exists() else set()
    if session_id not in known:
        with SESSIONS_FILE.open("a", encoding="utf-8") as f:
            f.write(session_id + "\n")


def _session_dirs():
    """この JARVIS フォルダの会話記録が入っている Claude Code のフォルダ"""
    name = "".join(c if c.isalnum() else "-" for c in str(PROJECT_DIR))
    return [d for d in CLAUDE_PROJECTS.glob("*") if d.is_dir() and d.name.lower() == name.lower()]


def clean_sessions(days=SESSION_DAYS):
    if not SESSIONS_FILE.exists():
        return 0
    ids = SESSIONS_FILE.read_text(encoding="utf-8").split()
    limit = time.time() - days * 86400
    keep, removed = [], 0
    for sid in ids:
        files = [f for d in _session_dirs() for f in (d / f"{sid}.jsonl", d / sid) if f.exists()]
        if not files:
            continue  # もうない会話は一覧からも外す
        if all(f.stat().st_mtime < limit for f in files):
            for f in files:
                shutil.rmtree(f) if f.is_dir() else f.unlink()
            removed += 1
        else:
            keep.append(sid)
    SESSIONS_FILE.write_text("".join(s + "\n" for s in keep), encoding="utf-8")
    return removed


def clean_backups(keep=KEEP_BACKUPS):
    folder = PROJECT_DIR / "backups"
    dirs = sorted((d for d in folder.glob("2*") if d.is_dir()), key=lambda d: d.name, reverse=True)
    for d in dirs[keep:]:
        shutil.rmtree(d)
    return max(0, len(dirs) - keep)


def clean_attachments(days=30):
    """チャットに添付した画像のうち、30日より古いものを消す"""
    folder = PROJECT_DIR / "attachments"
    limit = time.time() - days * 86400
    old = [f for f in folder.glob("*") if f.is_file() and f.stat().st_mtime < limit] if folder.exists() else []
    for f in old:
        f.unlink()
    return len(old)


def clean_cold_memories(log=print):
    """忘却・減衰: 長期間使われていない低優先度のクラウド Cold 記憶を整理する (Supabase のときだけ)"""
    try:
        import memory_tiering
        if not memory_tiering.enabled():
            return 0
        return memory_tiering.decay_memories()
    except Exception as error:  # 掃除はベストエフォート: 失敗しても他の掃除や起動は止めない
        log(f"クラウド記憶の整理に失敗しました: {error}")
        return 0


def daily(log=print):
    """1日1回だけ実行する"""
    if LAST_RUN_FILE.exists() and time.time() - LAST_RUN_FILE.stat().st_mtime < 86400:
        return
    LAST_RUN_FILE.parent.mkdir(exist_ok=True)
    s, b, a = clean_sessions(), clean_backups(), clean_attachments()
    c = clean_cold_memories(log)
    LAST_RUN_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    if s or b or a or c:
        extra = f"、古いクラウド記憶 {c} 件" if c else ""
        log(f"お掃除: 古い会話記録 {s} 件、古いバックアップ {b} 回分、古い添付画像 {a} 枚{extra}を削除")

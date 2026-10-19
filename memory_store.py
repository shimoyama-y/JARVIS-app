"""JARVIS の長期記憶 (ローカル JSON または選択した Supabase バックエンド)

- learning_history.json : 学んだこと・解決した問題・エラー対処法
- tryhackme_rooms.json  : TryHackMe のルームごとの進み具合
- security_notes.json   : セキュリティの知識 (出典付き)
- user_profile.json     : ユーザーの興味・目標・好み

記録の形式は MEMORY.md の「保存形式」に合わせる (timestamp, topic, summary, tags, importance)。

保存場所: Google ドライブの「マイドライブ\\JARVIS_memory」。
Google ドライブにつながらないとき (オフライン・未起動) は、いったんパソコンの memory フォルダに保存し、
次につながったときに Google ドライブへまとめて移す (記録が消えないように)。
"""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import memory_tiering

LOCAL_DIR = Path(__file__).with_name("memory")   # Google ドライブにつながらないときの一時的な置き場所
COLD_DIR = LOCAL_DIR / "cold"
DRIVE_FOLDER = "JARVIS_memory"
NAMES = {
    "learning": "learning_history.json",
    "thm": "tryhackme_rooms.json",
    "security": "security_notes.json",
    "profile": "user_profile.json",
}
MEMORY_DIR = None  # テストなどで場所を固定したいときに設定する (None なら自動で決める)


def _google_drive():
    """Google ドライブ パソコン版の「マイドライブ」の場所 (見つからなければ None)"""
    import string
    for letter in string.ascii_uppercase[3:]:  # D: 以降 (多くは G:)
        for name in ("マイドライブ", "My Drive"):
            p = Path(f"{letter}:/") / name
            try:
                if p.is_dir():
                    return p
            except OSError:
                continue
    return None


def memory_dir():
    """今使う学習記憶フォルダ。Google ドライブがあればそこ、なければパソコンの memory フォルダ"""
    if MEMORY_DIR:
        return Path(MEMORY_DIR)
    drive = _google_drive()
    if not drive:
        return LOCAL_DIR
    target = drive / DRIVE_FOLDER
    target.mkdir(exist_ok=True)
    _move_local_to(target)
    return target


def _key(kind, item):
    if kind == "thm":
        return item.get("room", "").lower()
    return (_when(item), item.get("topic", ""))


def _move_local_to(target):
    """パソコンに残っている記録を Google ドライブへ移す (同じ記録は重ねない)。移したらパソコン側は消す"""
    for kind, name in NAMES.items():
        local = LOCAL_DIR / name
        if not local.exists():
            continue
        try:
            here = json.loads(local.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        remote = target / name
        there = json.loads(remote.read_text(encoding="utf-8")) if remote.exists() else None
        if kind == "profile":
            merged = there or here  # プロフィールは Google ドライブ側を優先
        else:
            merged = list(there or [])
            seen = {_key(kind, i): n for n, i in enumerate(merged)}
            for item in here:
                k = _key(kind, item)
                if k not in seen:
                    seen[k] = len(merged)
                    merged.append(item)
                elif kind == "thm" and _when(item) > _when(merged[seen[k]]):  # ルームは新しい方
                    merged[seen[k]] = item
        remote.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        local.unlink()


class _Files(dict):
    """FILES[kind] で、そのときの保存場所のファイルを返す"""

    def __missing__(self, kind):
        return memory_dir() / NAMES[kind]

    def __iter__(self):
        return iter(NAMES)


FILES = _Files()
DEFAULT_PROFILE = {
    "name": "",
    "interests": ["AI開発", "サイバーセキュリティ"],
    "goals": ["TryHackMe攻略", "Linux習得", "Windows習得", "Networking習得", "Web Security習得",
              "Active Directory習得", "AI開発スキル向上", "自動化技術の習得", "JARVISを継続的に成長させる"],
    "preferences": {"answer_style": "正確性・実用性・分かりやすさ・学習効果を優先。TryHackMe はヒント→考え方→学習ポイント→解法の順"},
    "updated": "",
}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _load(kind):
    if memory_tiering.enabled():
        data = memory_tiering.load_kind(kind)
        if kind == "profile" and data is None:
            return DEFAULT_PROFILE.copy()
        return data
    path = FILES[kind]
    if not path.exists():
        return DEFAULT_PROFILE.copy() if kind == "profile" else []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:  # 壊れていたら上書きしないよう、別名で残して空から始める
        path.rename(path.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json"))
        return DEFAULT_PROFILE.copy() if kind == "profile" else []


def _save(kind, data):
    if memory_tiering.enabled():
        memory_tiering.save_kind(kind, data)
        return
    path = FILES[kind]
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init():
    """初回: memory フォルダと4つのファイルを用意する (既にあれば何もしない)"""
    for kind in FILES:
        if not FILES[kind].exists():
            _save(kind, _load(kind))


def _when(item):
    """記録の日時 (timestamp / date / updated のどれで書かれていても読む)"""
    return item.get("timestamp") or item.get("date") or item.get("updated") or ""


def _tags(text):
    return [t.strip() for t in (text or "").replace("、", ",").split(",") if t.strip()]


def _importance(value):
    level = int(value)
    if not 1 <= level <= 10:
        raise ValueError("importance must be between 1 and 10")
    return level


def add_learning(topic, summary, tags="", importance=5):
    items = _load("learning")
    items.append({"timestamp": _now(), "topic": topic, "summary": summary, "tags": _tags(tags),
                  "importance": _importance(importance)})
    _save("learning", items)
    return f"{topic}を学習履歴に記録しました。", f"■ 学習履歴に記録\n{topic}: {summary}"


def add_security(topic, summary, tags="", importance=5, source=""):
    items = _load("security")
    items.append({"timestamp": _now(), "topic": topic, "summary": summary, "tags": _tags(tags),
                  "importance": _importance(importance), "source": source})
    _save("security", items)
    return f"{topic}をセキュリティノートに記録しました。", f"■ セキュリティノート\n{topic}: {summary}"


def update_room(room, status="進行中", progress="", notes="", tags=""):
    """TryHackMe のルームの進み具合を記録する (同じルームは上書きして更新)"""
    rooms = _load("thm")
    entry = next((r for r in rooms if r["room"].lower() == room.lower()), None)
    if entry is None:
        entry = {"room": room, "started": _now(), "status": status, "progress": "", "notes": [], "tags": []}
        rooms.append(entry)
    entry.update(status=status, updated=_now())
    if progress:
        entry["progress"] = progress
    if notes:
        entry["notes"].append(f"{_now()[:10]} {notes}")
    entry["tags"] = sorted(set(entry["tags"]) | set(_tags(tags)))
    _save("thm", rooms)
    return f"TryHackMe の {room} を「{status}」として記録しました。", f"■ TryHackMe\n{room}: {status} {progress}"


def update_profile(field, value):
    """プロフィールの項目を更新する (interests/goals はリストに追加、それ以外は上書き)"""
    profile = _load("profile")
    if isinstance(profile.get(field), list):
        if value not in profile[field]:
            profile[field].append(value)
    elif field == "preferences":
        profile["preferences"]["note"] = value
    else:
        profile[field] = value
    profile["updated"] = _now()
    _save("profile", profile)
    return f"プロフィールの{field}を更新しました。", f"■ プロフィール\n{field}: {value}"


def recall(keyword, limit=6):
    """全ての記録から言葉を探す (新しい順・重要度順)"""
    if memory_tiering.enabled():
        names = {"learning": "学習", "security": "セキュリティ", "thm": "TryHackMe"}
        results = memory_tiering.search_records(keyword, limit)
        if not results:
            return f"{keyword}についての記録は見つかりませんでした。", "■ 記録の検索\n該当なし"
        lines = [_format_memory_hit(result, names) for result in results]
        return "\n".join(lines), "■ 記録の検索: " + keyword + "\n" + "\n".join(lines)

    kw = keyword.lower()
    hits = []
    for kind in ("learning", "security", "thm"):
        for item in _load(kind):
            text = json.dumps(item, ensure_ascii=False).lower()
            if kw in text:
                hits.append((kind, item))
    hits.sort(key=lambda h: (h[1].get("importance", 5), _when(h[1])), reverse=True)
    if not hits:
        return f"{keyword}についての記録は見つかりませんでした。", "■ 記録の検索\n該当なし"
    names = {"learning": "学習", "security": "セキュリティ", "thm": "TryHackMe"}
    lines = []
    for kind, item in hits[:limit]:
        line = f"[{names[kind]}] " + (item.get("topic") or item.get("room")) + ": "
        line += item.get("summary") or f"{item.get('status')} {item.get('progress', '')}"
        source_ref = item.get("source_ref") or (item.get("source_refs") or [None])[-1]
        if source_ref:
            line += f" [原文: {source_ref}]"
        lines.append(line)
    return "\n".join(lines), "■ 記録の検索: " + keyword + "\n" + "\n".join(lines)


def summary_for_prompt():
    """AI への指示に毎回入れる短い要約 (利用量を抑えるため、プロフィールと最近の要点だけ)"""
    profile = _load("profile")
    learning = sorted(_load("learning"), key=_when, reverse=True)[:3]
    all_rooms = _load("thm")
    rooms = [r for r in all_rooms if r.get("status") != "完了"][:3]
    done = sum(1 for r in all_rooms if r.get("status") == "完了")
    parts = [f"プロフィール: 興味={'、'.join(profile.get('interests', []))} / 目標={'、'.join(profile.get('goals', [])[:5])}"]
    if learning:
        parts.append("最近の学習: " + " / ".join(f"{i['topic']}" for i in learning))
    if rooms or done:
        parts.append(f"TryHackMe: 完了{done}件" + ("、進行中=" + "、".join(f"{r['room']}({r.get('progress', '')})" for r in rooms) if rooms else ""))
    return "\n".join(parts)


def _format_memory_hit(result, names):
    item = result["data"]
    topic = item.get("topic") or item.get("room") or "記録"
    summary = (item.get("summary")
               or f"{item.get('status', '')} {item.get('progress', '')}".strip()
               or item.get("assistant") or item.get("user") or "")
    source_ref = item.get("source_ref") or (item.get("source_refs") or [None])[-1]
    suffix = f" [原文: {source_ref}]" if source_ref else ""
    label = names.get(result["kind"], result["kind"])
    if result.get("tier") == "cold":  # 生ログを RAG で引いたもの
        label += "・原文"
    return f"[{label}] {topic}: {str(summary)[:240]}{suffix}"


def context_for_prompt(query):
    """Return the compact profile summary plus relevant memories. Warm first, then cold (RAG)."""
    parts = [summary_for_prompt()]
    if memory_tiering.enabled():
        names = {"learning": "学習", "security": "セキュリティ", "thm": "TryHackMe"}
        related = memory_tiering.search_records(query, limit=3, tier="warm")
        if len(related) < 3:  # Warm で足りなければ Cold の生ログを意味検索で補う
            related = related + memory_tiering.search_records(query, limit=3 - len(related), tier="cold")
        if related:
            lines = [_format_memory_hit(result, names) for result in related]
            parts.append("関連する過去の記憶:\n" + "\n".join(lines))
    return "\n".join(part for part in parts if part)[:1600]


# ---------- 意図分類 (AttentionIntentRouter) の自己学習 ----------
# 組み込みの例文だけでなく、実際の会話で判定できた「文章→意図」を蓄積し、再起動時の学習に使う。
# Google ドライブ/Supabase へは同期せず、常にパソコン側の memory フォルダに置く軽量なキャッシュ
INTENT_EXAMPLES_FILE = LOCAL_DIR / "intent_examples.json"
INTENT_LABELS = ("operate", "work", "chat")
MAX_INTENT_EXAMPLES = 200


def load_intent_examples():
    """蓄積した意図分類の実例を読み込む (ラベルごとのリスト)。ファイルが無い/壊れていれば空から始める"""
    if not INTENT_EXAMPLES_FILE.exists():
        return {label: [] for label in INTENT_LABELS}
    try:
        data = json.loads(INTENT_EXAMPLES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        INTENT_EXAMPLES_FILE.rename(INTENT_EXAMPLES_FILE.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json"))
        return {label: [] for label in INTENT_LABELS}
    return {label: list(data.get(label, [])) for label in INTENT_LABELS}


def add_intent_example(text, label):
    """会話で判定できた「文章→正解ラベル」を追記する (ラベルごと最大200件、古いものから間引く)"""
    if label not in INTENT_LABELS:
        raise ValueError(f"Unsupported intent label: {label}")
    examples = load_intent_examples()
    examples[label] = [t for t in examples[label] if t != text]
    examples[label].append(text)
    examples[label] = examples[label][-MAX_INTENT_EXAMPLES:]
    INTENT_EXAMPLES_FILE.parent.mkdir(exist_ok=True)
    INTENT_EXAMPLES_FILE.write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- 会話からの自動学習 ----------
# 学習テーマ: (見つける言葉, 記録するテーマ名, タグ, 重要度)。MEMORY.md の重点学習領域に合わせている
LEARNING_TOPICS = [
    (("xss", "クロスサイトスクリプティング"), "XSS", ["security", "web", "owasp"], 7),
    (("sqlインジェクション", "sql injection", "sqli"), "SQLインジェクション", ["security", "web", "owasp"], 7),
    (("ssrf",), "SSRF", ["security", "web", "owasp"], 7),
    (("csrf", "クロスサイトリクエストフォージェリ"), "CSRF", ["security", "web", "owasp"], 6),
    (("xxe",), "XXE", ["security", "web", "owasp"], 6),
    (("ssti", "テンプレートインジェクション"), "SSTI", ["security", "web"], 6),
    (("owasp",), "OWASP Top 10", ["security", "web", "owasp"], 6),
    (("認証", "authentication", "セッション"), "Authentication", ["security", "web"], 5),
    (("アクセス制御", "access control", "idor"), "Access Control", ["security", "web", "owasp"], 6),
    (("権限昇格", "privilege escalation", "privesc"), "Privilege Escalation", ["security", "linux", "windows"], 7),
    (("active directory", "ケルベロス", "kerberos"), "Active Directory", ["security", "windows", "ad"], 7),
    (("nmap", "ポートスキャン"), "Nmap", ["security", "network"], 5),
    (("wireshark", "パケット"), "Wireshark", ["security", "network"], 5),
    (("burp",), "Burp Suite", ["security", "web", "tool"], 5),
    (("metasploit",), "Metasploit", ["security", "tool"], 5),
    (("linux", "bash", "chmod", "sudo"), "Linux", ["linux"], 4),
    (("powershell",), "PowerShell", ["windows"], 4),
]
LEARNING_WORDS = ("学ん", "勉強", "教えて", "とは", "わかった", "分かった", "理解", "解決", "エラー", "やり方", "仕組み",
                  "対策", "原因", "TryHackMe", "tryhackme")


def _first_sentence(text, limit=90):
    for mark in ("。", "！", "？", "\n"):
        if mark in text:
            text = text.split(mark)[0] + ("。" if mark == "。" else "")
            break
    return text.strip()[:limit]


def _archive_conversation(topic, user_text, ai_response, importance=3, tags=None):
    """会話の原文を Cold 層に保存する (Supabase ならクラウドの埋め込み付き、なければローカル)"""
    archive_id = str(uuid.uuid4())
    payload = {
        "created_at": _now(),
        "topic": topic,
        "user": user_text[:20000],
        "assistant": ai_response[:20000],
    }
    if memory_tiering.enabled():
        memory_tiering.save_cold_memory(archive_id, "learning", payload, importance, tags or [])
    else:
        COLD_DIR.mkdir(parents=True, exist_ok=True)
        path = COLD_DIR / f"{archive_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"cold:{archive_id}"


def read_cold_archive(source_ref):
    match = re.fullmatch(r"cold:([0-9a-fA-F-]{36})", source_ref.strip())
    if not match:
        raise ValueError("Invalid cold-memory reference")
    try:
        archive_id = str(uuid.UUID(match.group(1)))
    except ValueError as error:
        raise ValueError("Invalid cold-memory reference") from error
    if memory_tiering.enabled():
        payload = memory_tiering.load_cold_memory(archive_id)
    else:
        path = COLD_DIR / f"{archive_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise RuntimeError("The local source for this memory was not found") from error
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Could not read the local source for this memory") from error
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(field), str)
        for field in ("created_at", "topic", "user", "assistant")
    ):
        raise RuntimeError("The local source for this memory has an invalid format")
    display = (
        f"■ 記憶の原文 ({payload['created_at']})\n"
        f"テーマ: {payload['topic']}\n"
        f"ユーザー: {payload['user']}\n"
        f"JARVIS: {payload['assistant']}"
    )
    return f"原文を読み出しました: {payload['topic']}", display


def auto_learn(user_text, ai_response):
    """会話から学習テーマを見つけて、自動で長期記憶に記録する (AI は使わない)。
    同じ日の同じテーマは1件にまとめる。記録したテーマ名のリストを返す"""
    text = f"{user_text}\n{ai_response}".lower()
    recorded = _auto_room(user_text, text)  # TryHackMe のルームの話は、学習の言葉がなくても記録する
    if not any(w.lower() in text for w in LEARNING_WORDS):  # 学習の話でなければ、ここまで
        return recorded
    today = datetime.now().strftime("%Y-%m-%d")
    items = _load("learning")
    source_ref = None
    for words, topic, tags, importance in LEARNING_TOPICS:
        if not any(w in text for w in words) or sum(1 for r in recorded if ":" not in r) >= 2:
            continue
        summary = _first_sentence(ai_response) or _first_sentence(user_text)
        episode = {}
        if memory_tiering.enabled():
            episode = memory_tiering.summarize_episode(
                topic, user_text, ai_response, tags, importance
            )
            summary = episode["summary"]
            tags = episode["tags"]
            importance = episode["importance"]
        if source_ref is None:
            source_ref = _archive_conversation(topic, user_text, ai_response, importance, tags)
        same = next((i for i in items if i.get("topic") == topic and _when(i)[:10] == today), None)
        if same:  # 同じ日の同じテーマ: 要約が違えば書き足す
            if summary and summary not in same.get("summary", ""):
                same["summary"] = (same.get("summary", "") + " / " + summary)[-400:]
            same.update({key: value for key, value in episode.items() if key != "summary"})
            refs = same.setdefault("source_refs", [])
            if source_ref not in refs:
                refs.append(source_ref)
            same["timestamp"] = _now()
        else:
            items.append({
                "timestamp": _now(),
                "topic": topic,
                "summary": summary,
                "tags": tags,
                "importance": importance,
                "source": "auto",
                "source_ref": source_ref,
                **{key: value for key, value in episode.items() if key != "summary"},
            })
        recorded.append(topic)
    if recorded:
        _save("learning", items)
    return recorded


def _auto_room(user_text, text):
    """TryHackMe のルームの話 (例: 「Blue のルーム」「ルームの Pickle Rick」) なら進み具合を記録する"""
    import re
    if "ルーム" not in user_text and "room" not in text:
        return []
    m = re.search(r"([A-Za-z0-9][A-Za-z0-9\- ]{0,30}?)\s*(?:の)?ルーム|ルーム(?:の)?\s*([A-Za-z0-9][A-Za-z0-9\- ]{0,30})",
                  user_text)
    if not m:
        return []
    room = (m.group(1) or m.group(2)).strip()
    status = "完了" if any(w in user_text for w in ("終わ", "クリア", "完了")) else "進行中"
    update_room(room, status, notes=_first_sentence(user_text, 60))
    return [f"TryHackMe:{room}"]

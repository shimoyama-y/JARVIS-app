"""Supabase-backed tiered memory storage using only the Python standard library."""
from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

KINDS = ("learning", "security", "thm", "profile")
TIERS = {
    "learning": "warm",
    "security": "warm",
    "thm": "warm",
    "profile": "hot",
}
EMBEDDING_DIMENSION = 1536
EMBEDDING_MODEL = "gemini-embedding-001"
SUMMARY_MODEL = "gemini-3.5-flash-lite"
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|access[_-]?token|refresh[_-]?token)\b(\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)
# JARVIS 自身のクラウド通信 (Supabase / Gemini) は、Windows のシステムプロキシ
# (例: Burp Lite の監視プロキシ) を無視して必ず直結する。urllib の既定オープナーは
# 初回使用時の getproxies() をプロセスが終わるまでキャッシュするため、プロキシを
# オンのまま起動すると、あとで切っても古い設定を握り続けて接続できなくなる
# (WinError 10061 で Supabase/Gemini に届かなくなる)。専用オープナーでプロキシを
# 明示的に空にし、プロキシの状態や起動順に左右されないようにする。
_DIRECT_OPENER = build_opener(ProxyHandler({}))

_client: _SupabaseRest | None = None


def enabled() -> bool:
    """Return whether Supabase is selected as the memory backend."""
    backend = os.environ.get("JARVIS_MEMORY_BACKEND", "local").strip().lower()
    if backend not in ("local", "supabase"):
        raise ValueError("JARVIS_MEMORY_BACKEND must be 'local' or 'supabase'")
    return backend == "supabase"


def _config() -> tuple[str, str, str, str]:
    values = (
        os.environ.get("SUPABASE_URL", "").strip(),
        os.environ.get("SUPABASE_ANON_KEY", "").strip(),
        os.environ.get("SUPABASE_EMAIL", "").strip(),
        os.environ.get("SUPABASE_PASSWORD", ""),
    )
    missing = [
        name for name, value in zip(
            ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_EMAIL", "SUPABASE_PASSWORD"),
            values,
        ) if not value
    ]
    if missing:
        raise RuntimeError("Supabase memory is enabled but configuration is missing: " + ", ".join(missing))
    if not values[0].startswith("https://"):
        raise ValueError("SUPABASE_URL must use HTTPS")
    return values


def _gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Gemini memory features require GEMINI_API_KEY")
    return key


def _gemini_request(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        "https://generativelanguage.googleapis.com/v1beta/" + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": _gemini_key()},
        method="POST",
    )
    try:
        with _DIRECT_OPENER.open(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as error:
        raise RuntimeError(f"Gemini request failed with HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach Gemini API: {error.reason}") from error
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Gemini returned an invalid JSON response") from error
    if not isinstance(result, dict):
        raise RuntimeError("Gemini returned an unexpected response")
    return result


def _embedding_text(text: str, *, query: bool) -> str:
    if query:
        return "task: search result | query: " + text[:2000]
    return "title: none | text: " + text[:8000]


def _embed_texts(texts: list[str], *, query: bool = False) -> list[list[float]]:
    embeddings: list[list[float]] = []
    model = os.environ.get("GEMINI_EMBEDDING_MODEL", EMBEDDING_MODEL).strip()
    if not model:
        raise ValueError("GEMINI_EMBEDDING_MODEL must not be empty")
    for start in range(0, len(texts), 100):
        batch = texts[start:start + 100]
        requests = [
            {
                "model": "models/" + model,
                "content": {"parts": [{"text": _embedding_text(text, query=query)}]},
                "outputDimensionality": EMBEDDING_DIMENSION,
            }
            for text in batch
        ]
        if len(requests) == 1:
            request_body = requests[0]
            endpoint = f"models/{model}:embedContent"
        else:
            request_body = {"requests": requests}
            endpoint = f"models/{model}:batchEmbedContents"
        response = _gemini_request(endpoint, request_body)
        values = response.get("embeddings", [])
        if len(batch) == 1 and "embedding" in response:
            values = [response["embedding"]]
        if not isinstance(values, list) or len(values) != len(batch):
            raise RuntimeError("Gemini returned an unexpected embedding response")
        for item in values:
            vector = item.get("values") if isinstance(item, dict) else None
            if (
                not isinstance(vector, list)
                or len(vector) != EMBEDDING_DIMENSION
                or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
            ):
                raise RuntimeError("Gemini returned an embedding with an unexpected dimension or value")
            embeddings.append([float(value) for value in vector])
    return embeddings


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".8g") for value in vector) + "]"


def summarize_episode(
    topic: str,
    user_text: str,
    ai_response: str,
    tags: list[str],
    importance: int,
) -> dict[str, Any]:
    """Create a concise memory from one exchange without allowing unsupported facts."""
    user_text, ai_response = _redact_sensitive_text(user_text), _redact_sensitive_text(ai_response)
    prompt = (
        "会話から再利用できる短い学習記憶を作成してください。"
        "会話にない事実は推測せず、認証情報・個人情報は要約に含めないでください。"
        "日本語のJSONオブジェクトで、summary (200字以内), problem, approach, outcome, "
        "lesson (各120字以内), tags (文字列配列、最大8件), importance (1から10の整数) "
        "を返してください。\n"
        f"テーマ: {topic}\n既存タグ: {json.dumps(tags, ensure_ascii=False)}\n"
        f"ユーザー: {user_text[:4000]}\nJARVIS: {ai_response[:4000]}"
    )
    model = os.environ.get("GEMINI_SUMMARY_MODEL", SUMMARY_MODEL).strip()
    if not model:
        raise ValueError("GEMINI_SUMMARY_MODEL must not be empty")
    response = _gemini_request(
        f"models/{model}:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        },
    )
    try:
        text = response["candidates"][0]["content"]["parts"][0]["text"]
        summary = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Gemini returned an invalid memory summary") from error
    if not isinstance(summary, dict) or not isinstance(summary.get("summary"), str):
        raise RuntimeError("Gemini returned a memory summary without a summary")
    output_tags = summary.get("tags", tags)
    importance_value = summary.get("importance", importance)
    if (
        not isinstance(output_tags, list)
        or not all(isinstance(tag, str) for tag in output_tags)
        or not isinstance(importance_value, int)
        or isinstance(importance_value, bool)
        or not 1 <= importance_value <= 10
    ):
        raise RuntimeError("Gemini returned invalid memory tags or importance")
    summary["summary"] = summary["summary"].strip()[:200]
    if not summary["summary"]:
        raise RuntimeError("Gemini returned an empty memory summary")
    summary["tags"] = list(dict.fromkeys(tags + output_tags))[:8]
    summary["importance"] = importance_value
    for field in ("problem", "approach", "outcome", "lesson"):
        value = summary.get(field, "")
        if not isinstance(value, str):
            raise RuntimeError(f"Gemini returned an invalid {field} in its memory summary")
        summary[field] = value.strip()[:120]
    return summary


def check_connections() -> None:
    """Verify Supabase schema/auth and both Gemini memory endpoints without saving data."""
    rows = _get_client().request(
        "GET", "/rest/v1/memory_records?select=owner_id&limit=1"
    )
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an unexpected memory response during setup check")
    _embed_texts(["JARVIS memory connection check"])
    summarize_episode(
        "接続確認",
        "JARVIS memory connection check",
        "接続確認用の短い応答です。",
        [],
        5,
    )


def _redact_sensitive_text(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class _SupabaseRest:
    def __init__(self, url: str, anon_key: str, email: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.email = email
        self.password = password
        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0.0

    def _send(
        self,
        method: str,
        path: str,
        body: Any = None,
        authenticated: bool = True,
        retried_auth: bool = False,
    ) -> Any:
        if authenticated and not self.access_token:
            self._sign_in()
        headers = {
            "apikey": self.anon_key,
            "Accept": "application/json",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.access_token}"
        encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=encoded, headers=headers, method=method)
        try:
            with _DIRECT_OPENER.open(request, timeout=20) as response:
                raw = response.read()
        except HTTPError as error:
            if authenticated and error.code == 401 and not retried_auth:
                self.access_token = ""
                self._sign_in()
                return self._send(method, path, body, retried_auth=True)
            if path.startswith("/auth/v1/token?grant_type=password"):
                hint = self._auth_error_hint(error)
                if hint:
                    raise RuntimeError(
                        f"Supabase sign-in failed with HTTP {error.code}: {hint}"
                    ) from error
            raise RuntimeError(f"Supabase request failed with HTTP {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"Could not reach Supabase: {error.reason}") from error
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Supabase returned an invalid JSON response") from error

    @staticmethod
    def _auth_error_hint(error: HTTPError) -> str:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            return ""
        if not isinstance(payload, dict):
            return ""
        code = str(payload.get("code", "")).lower()
        message = str(payload.get("message", payload.get("msg", ""))).lower()
        detail = code + " " + message
        if "email_not_confirmed" in detail or "email not confirmed" in detail:
            return "the Auth user's email is not confirmed; confirm it in Supabase Authentication."
        if "invalid_credentials" in detail or "invalid login credentials" in detail:
            return "the Auth email or password is incorrect; verify SUPABASE_EMAIL and use that user's password."
        if "user_not_found" in detail or "user not found" in detail:
            return "the Auth user was not found; create it in Supabase Authentication and confirm its email."
        return ""

    def _sign_in(self) -> None:
        if self.access_token and time.time() < self.expires_at:
            return
        if self.refresh_token:
            try:
                session = self._send(
                    "POST",
                    "/auth/v1/token?grant_type=refresh_token",
                    {"refresh_token": self.refresh_token},
                    authenticated=False,
                )
            except RuntimeError:
                session = None
            if session:
                self._set_session(session)
                return
        session = self._send(
            "POST",
            "/auth/v1/token?grant_type=password",
            {"email": self.email, "password": self.password},
            authenticated=False,
        )
        self._set_session(session)

    def _set_session(self, session: dict[str, Any]) -> None:
        token = session.get("access_token")
        if not token:
            raise RuntimeError("Supabase Auth did not return an access token")
        self.access_token = token
        self.refresh_token = session.get("refresh_token", "")
        self.expires_at = time.time() + max(0, int(session.get("expires_in", 3600)) - 30)

    def request(self, method: str, path: str, body: Any = None) -> Any:
        if not self.access_token or time.time() >= self.expires_at:
            self._sign_in()
        return self._send(method, path, body)


def _get_client() -> _SupabaseRest:
    global _client
    url, anon_key, email, password = _config()
    if _client is None or (_client.url, _client.anon_key, _client.email, _client.password) != (
        url.rstrip("/"), anon_key, email, password
    ):
        _client = _SupabaseRest(url, anon_key, email, password)
    return _client


def _records(kind: str, data: Any) -> list[dict[str, Any]]:
    if kind not in KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")
    values = [data] if kind == "profile" else data
    if not isinstance(values, list):
        raise ValueError(f"Memory data for {kind} must be a list")
    rows = []
    for position, item in enumerate(values):
        if not isinstance(item, dict):
            raise ValueError(f"Memory item {position} for {kind} must be an object")
        text = " ".join(str(value) for value in (
            item.get("topic", ""),
            item.get("room", ""),
            item.get("summary", ""),
            item.get("problem", ""),
            item.get("approach", ""),
            item.get("outcome", ""),
            item.get("lesson", ""),
            item.get("progress", ""),
            item.get("notes", ""),
            item.get("interests", ""),
            item.get("goals", ""),
            item.get("preferences", ""),
            item.get("source", ""),
        ) if value)
        tags = item.get("tags", [])
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.replace("、", ",").split(",") if tag.strip()]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"Memory tags for {kind} at position {position} must be strings")
        importance = int(item.get("importance", 5))
        if not 1 <= importance <= 10:
            raise ValueError(f"Memory importance for {kind} at position {position} must be between 1 and 10")
        rows.append({
            "record_key": "profile" if kind == "profile" else str(position),
            "position": position,
            "tier": TIERS[kind],
            "data": item,
            "content": text,
            "tags": tags,
            "importance": importance,
            "embedding": None,
        })
    return rows


def _add_embeddings(kind: str, rows: list[dict[str, Any]]) -> None:
    if kind == "profile" or not rows:
        return
    query = urlencode({"select": "record_key,content,embedding", "kind": f"eq.{kind}"})
    stored = _get_client().request("GET", f"/rest/v1/memory_records?{query}")
    if not isinstance(stored, list):
        raise RuntimeError("Supabase returned an unexpected embedding response")
    previous = {
        row["record_key"]: row
        for row in stored
        if isinstance(row, dict) and isinstance(row.get("record_key"), str)
    }
    missing = []
    for row in rows:
        old = previous.get(row["record_key"])
        vector = old.get("embedding") if old and old.get("content") == row["content"] else None
        if isinstance(vector, str) and vector.startswith("[") and vector.endswith("]"):
            row["embedding"] = vector
        else:
            missing.append(row)
    to_embed = [row for row in missing if row["content"].strip()]
    vectors = _embed_texts([row["content"] for row in to_embed]) if to_embed else []
    for row, vector in zip(to_embed, vectors):
        row["embedding"] = _vector_literal(vector)


def load_kind(kind: str) -> Any:
    if kind not in KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")
    query = urlencode({"select": "position,data", "kind": f"eq.{kind}", "order": "position.asc"})
    rows = _get_client().request("GET", f"/rest/v1/memory_records?{query}")
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an unexpected memory response")
    values = [row["data"] for row in rows]
    if kind == "profile":
        return values[0] if values else None
    return values


def save_kind(kind: str, data: Any) -> None:
    rows = _records(kind, data)
    _add_embeddings(kind, rows)
    _get_client().request("POST", "/rest/v1/rpc/replace_memory_kind", {
        "p_kind": kind,
        "p_records": rows,
    })


def search_records(query_text: str, limit: int = 6, tier: str = "warm") -> list[dict[str, Any]]:
    if not query_text.strip():
        return []
    if limit <= 0:
        raise ValueError("limit must be positive")
    if tier not in ("warm", "cold", "hot"):
        raise ValueError("tier must be 'warm', 'cold' or 'hot'")
    query_text = _redact_sensitive_text(query_text).strip()
    results = _get_client().request("POST", "/rest/v1/rpc/search_memory_records", {
        "p_query": query_text.strip()[:200],
        "p_limit": min(limit, 20),
        "p_query_embedding": _vector_literal(
            _embed_texts([query_text.strip()], query=True)[0]
        ),
        "p_tier": tier,
    })
    if not isinstance(results, list):
        raise RuntimeError("Supabase returned an unexpected search response")
    return results


def save_cold_memory(
    archive_id: str,
    kind: str,
    payload: dict[str, Any],
    importance: int = 3,
    tags: list[str] | None = None,
) -> None:
    """Store one raw-conversation archive in the cold tier with an embedding (secrets redacted)."""
    safe_payload = dict(payload)
    for field in ("user", "assistant"):
        if isinstance(safe_payload.get(field), str):
            safe_payload[field] = _redact_sensitive_text(safe_payload[field])
    content = _redact_sensitive_text(
        " ".join(str(safe_payload.get(field, "")) for field in ("topic", "user", "assistant")
                 if safe_payload.get(field))
    ).strip()[:8000]
    embedding = _vector_literal(_embed_texts([content])[0]) if content else None
    record = {
        "kind": kind if kind in KINDS else "learning",
        "record_key": str(archive_id),
        "tier": "cold",
        "data": safe_payload,
        "content": content,
        "tags": list(tags or []),
        "importance": max(1, min(10, int(importance))),
        "embedding": embedding,
    }
    _get_client().request("POST", "/rest/v1/rpc/add_cold_memory", {"p_record": record})


def load_cold_memory(archive_id: str) -> Any:
    """Return one cold archive's stored payload, or raise if it is gone (e.g. decayed)."""
    query = urlencode({
        "select": "data",
        "tier": "eq.cold",
        "record_key": f"eq.{archive_id}",
        "limit": "1",
    })
    rows = _get_client().request("GET", f"/rest/v1/memory_records?{query}")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("The cloud source for this memory was not found")
    return rows[0].get("data")


def decay_memories(max_idle_days: int = 90, min_importance: int = 4) -> int:
    """Forget cold memories that are both low-importance and long unused. Returns the count removed."""
    result = _get_client().request("POST", "/rest/v1/rpc/prune_memories", {
        "p_max_idle_days": int(max_idle_days),
        "p_min_importance": int(min_importance),
    })
    return int(result or 0)


def migrate_local_data(payloads: dict[str, Any]) -> int:
    """Import all legacy files atomically, only when the Supabase account is empty."""
    existing = _get_client().request(
        "GET", "/rest/v1/memory_records?select=record_key&limit=1"
    )
    if not isinstance(existing, list):
        raise RuntimeError("Supabase returned an unexpected response before memory import")
    if existing:
        raise RuntimeError("Supabase already contains memories; legacy import was not applied")
    rows = []
    for kind in KINDS:
        if kind not in payloads:
            raise ValueError(f"Missing legacy memory kind: {kind}")
        kind_rows = _records(kind, payloads[kind])
        _add_embeddings(kind, kind_rows)
        rows.extend({"kind": kind, **record} for record in kind_rows)
    result = _get_client().request("POST", "/rest/v1/rpc/import_legacy_memories", {
        "p_records": rows,
    })
    return int(result or 0)

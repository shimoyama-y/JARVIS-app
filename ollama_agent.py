"""Local Anthropic-compatible agent loop for Ollama-backed JARVIS requests."""
import json
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


TOOL_SCHEMAS = {
    "Bash": {"command": {"type": "string", "description": "A permitted JARVIS command."}},
    "Read": {"file_path": {"type": "string"}, "offset": {"type": "integer"},
             "limit": {"type": "integer"}},
    "Glob": {"pattern": {"type": "string"}, "path": {"type": "string"}},
    "Grep": {"pattern": {"type": "string"}, "path": {"type": "string"}},
    "Write": {"file_path": {"type": "string"}, "content": {"type": "string"}},
    "Edit": {"file_path": {"type": "string"}, "old_string": {"type": "string"},
             "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}},
}


def _inside(path, root):
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _project_path(value, project_dir):
    path = Path(value)
    if not path.is_absolute():
        path = project_dir / path
    path = path.resolve()
    if not _inside(path, project_dir):
        raise ValueError("Path is outside the JARVIS project")
    return path


def _tool_allowed(name, path, allowed, project_dir):
    if name in ("Read", "Glob", "Grep"):
        if name in allowed:
            return True
        if name == "Read" and any(rule.startswith("Read(") for rule in allowed):
            return any(rule == "Read(attachments/**)" and _inside(path, project_dir / "attachments")
                       for rule in allowed)
        return False
    if name in ("Write", "Edit"):
        if name in allowed:
            return True
        rel = path.relative_to(project_dir).as_posix()
        return any(rule == f"{name}({prefix}/**)" and rel.startswith(prefix + "/")
                   for rule in allowed for prefix in ("docs", "workflows"))
    return False


def _run_tool(name, data, allowed, project_dir, timeout):
    if name == "Bash":
        command = str(data.get("command", "")).strip()
        if not command or re.search(r"[;&|<>`$%\n\r]", command):
            raise ValueError("Only a single permitted JARVIS command can run")
        argv = shlex.split(command)
        rules = [rule for rule in allowed if rule.startswith("Bash(")]
        is_jarvis = (len(argv) >= 2 and Path(argv[1]).name.lower() == "jarvis_tools.py"
                     and any("python jarvis_tools.py:" in rule for rule in rules))
        is_compile = (len(argv) >= 4 and argv[1:3] == ["-m", "py_compile"]
                      and any("python -m py_compile:" in rule for rule in rules))
        if not (is_jarvis or is_compile):
            raise ValueError("That shell command is not allowed in this JARVIS mode")
        if argv[0].lower() not in ("python", "python.exe"):
            raise ValueError("Only Python-based JARVIS commands are allowed")
        if is_compile:
            for filename in argv[3:]:
                if not _inside(_project_path(filename, project_dir), project_dir):
                    raise ValueError("Compile path is outside the JARVIS project")
        result = subprocess.run(argv, cwd=project_dir, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = (result.stdout + result.stderr).strip()
        return output[:12000] or f"Command finished with exit code {result.returncode}", True

    if name == "WebSearch" or name == "WebFetch":
        raise ValueError("Live web tools are unavailable in the local-only provider")

    raw_path = data.get("file_path") or data.get("path") or "."
    path = _project_path(raw_path, project_dir)
    if not _tool_allowed(name, path, allowed, project_dir):
        raise ValueError(f"{name} is not allowed for this path")

    if name == "Read":
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(0, int(data.get("offset", 0)))
        limit = max(1, min(int(data.get("limit", 300)), 1000))
        return "\n".join(lines[start:start + limit])[:20000], False

    if name == "Glob":
        pattern = str(data.get("pattern", "*"))
        if ".." in Path(pattern).parts:
            raise ValueError("Glob pattern cannot leave the project")
        matches = sorted(p.relative_to(project_dir).as_posix()
                         for p in project_dir.glob(pattern) if _inside(p, project_dir))
        return "\n".join(matches[:300]) or "No matching files", False

    if name == "Grep":
        regex = re.compile(str(data.get("pattern", "")), re.IGNORECASE)
        root = path if path.is_dir() else path.parent
        files = [path] if path.is_file() else sorted(root.rglob("*"))
        hits = []
        for candidate in files[:500]:
            if not candidate.is_file() or candidate.stat().st_size > 1_000_000:
                continue
            try:
                for number, line in enumerate(candidate.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if regex.search(line):
                        hits.append(f"{candidate.relative_to(project_dir).as_posix()}:{number}:{line[:400]}")
                        if len(hits) >= 100:
                            break
            except OSError:
                continue
            if len(hits) >= 100:
                break
        return "\n".join(hits) or "No matches", False

    if name == "Write":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(data.get("content", "")), encoding="utf-8")
        return f"Wrote {path.relative_to(project_dir).as_posix()}", True

    if name == "Edit":
        if not path.is_file():
            raise FileNotFoundError(path)
        original = path.read_text(encoding="utf-8", errors="replace")
        old = str(data.get("old_string", ""))
        if not old or old not in original:
            raise ValueError("The text to replace was not found")
        new = str(data.get("new_string", ""))
        updated = original.replace(old, new) if data.get("replace_all") else original.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8")
        return f"Edited {path.relative_to(project_dir).as_posix()}", True

    raise ValueError(f"Unsupported tool: {name}")


def run(base_url, model, prompt, system, tool_names, allowed, project_dir,
        history=None, timeout=600, on_progress=None, on_session=None, should_stop=None):
    session_id = str(uuid.uuid4())
    messages = list(history or [])[-6:]
    messages.append({"role": "user", "content": prompt})
    tools = []
    descriptions = {
        "Bash": "Run one allowed command as python jarvis_tools.py <command>.",
        "Read": "Read a file permitted by the current mode.",
        "Glob": "List matching project files.",
        "Grep": "Search project files.",
        "Write": "Write a file permitted by the current mode.",
        "Edit": "Replace text in a file permitted by the current mode.",
    }
    for name in tool_names:
        if name not in TOOL_SCHEMAS:
            continue
        schema = TOOL_SCHEMAS[name]
        tools.append({"name": name, "description": descriptions[name],
                      "input_schema": {"type": "object", "properties": schema,
                                       "required": [key for key in schema if key in
                                                    ("command", "file_path", "pattern")]}})

    headers = {"x-api-key": "ollama", "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    deadline = time.monotonic() + timeout
    side_effect_started = False
    if on_session:
        on_session("ollama:" + session_id)
    if "WebSearch" in tool_names or "WebFetch" in tool_names:
        raise RuntimeError("This request needs live web access; use an online provider")

    for _ in range(10):
        if should_stop and should_stop():
            raise InterruptedError("Local AI request interrupted")
        remaining = max(1, deadline - time.monotonic())
        body = {"model": model, "max_tokens": 1536 if tool_names else 768,
                "temperature": 0.2,
                "thinking": {"type": "disabled"}, "system": system,
                "messages": messages, "stream": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = {"type": "auto"}
        request = urllib.request.Request(base_url.rstrip("/") + "/v1/messages",
                                         data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    request, timeout=min(remaining, 60)) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as error:
            if side_effect_started:
                error.side_effect_started = True
            raise
        content = result.get("content", [])
        messages.append({"role": "assistant", "content": content})
        calls = [block for block in content if block.get("type") == "tool_use"]
        if not calls:
            answer = "\n".join(block.get("text", "") for block in content
                                if block.get("type") == "text").strip()
            return {"result": answer, "session_id": "ollama:" + session_id, "provider": "ollama",
                    "messages": messages, "side_effect_started": side_effect_started}

        tool_results = []
        for call in calls:
            if on_progress:
                on_progress(f"ローカルAIが{call.get('name', 'tool')}を実行しています")
            if call.get("name") in ("Bash", "Write", "Edit"):
                side_effect_started = True
            try:
                output, changed = _run_tool(call.get("name", ""), call.get("input", {}),
                                            allowed, project_dir, max(1, min(remaining, 120)))
                side_effect_started = side_effect_started or changed
                tool_results.append({"type": "tool_result", "tool_use_id": call["id"], "content": output})
            except Exception as error:
                if side_effect_started:
                    error.side_effect_started = True
                tool_results.append({"type": "tool_result", "tool_use_id": call["id"],
                                     "content": str(error)[:1000], "is_error": True})
        messages.append({"role": "user", "content": tool_results})
        if on_progress and not calls:
            on_progress("ローカルAIから返事を受け取りました")

    error = RuntimeError("Local AI reached the tool-call limit for this turn")
    if side_effect_started:
        error.side_effect_started = True
    raise error

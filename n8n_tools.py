"""n8n (ワークフロー自動化ツール) の起動と、ワークフロー JSON の取り込み。

JARVIS が「○○するワークフロー作って」と言われたときの流れ:
  1. ensure_running(): ローカルの n8n (http://localhost:5678) を必要なら起動する
  2. (Claude が n8n ワークフローの JSON を workflows/ に生成する  … ai.make_workflow)
  3. import_workflow(): n8n に取り込む (n8n のコマンドを使う。API キー不要)
  4. open_ui(): ブラウザで n8n を開いて確認してもらう

n8n は Node.js で動く外部ツール。npm で入れておく (npm install -g n8n)。
"""
import json
import shutil
import socket
import subprocess
import time
import uuid
import webbrowser
from pathlib import Path

N8N_HOST = "127.0.0.1"
N8N_PORT = 5678
N8N_URL = f"http://localhost:{N8N_PORT}"
WORKFLOWS_DIR = Path(__file__).with_name("workflows")
_START_TIMEOUT = 60      # 起動を待つ最大秒数
_IMPORT_TIMEOUT = 120


def _n8n_cmd():
    """n8n の実行ファイル (PATH、なければ npm のグローバル置き場)。見つからなければ None"""
    found = shutil.which("n8n")
    if found:
        return found
    guess = Path.home() / "AppData" / "Roaming" / "npm" / "n8n.cmd"
    return str(guess) if guess.exists() else None


def is_installed():
    return _n8n_cmd() is not None


def is_running():
    """n8n が 5678 で応答しているか (起動済みか)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((N8N_HOST, N8N_PORT)) == 0


def ensure_running(log=print):
    """n8n が動いていなければ起動して、応答し始めるまで待つ。起動できたら True"""
    if is_running():
        return True
    cmd = _n8n_cmd()
    if not cmd:
        raise RuntimeError("n8n がインストールされていません (npm install -g n8n)")
    log("n8n を起動しています…")
    # 画面を出さずにバックグラウンドで起動する
    subprocess.Popen([cmd, "start"], cwd=str(Path(__file__).parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.time() + _START_TIMEOUT
    while time.time() < deadline:
        if is_running():
            time.sleep(2)  # 起動直後は API がまだ準備中のことがあるので少し待つ
            return True
        time.sleep(1)
    raise RuntimeError(f"n8n が {_START_TIMEOUT} 秒以内に起動しませんでした")


def _normalize(workflow):
    """n8n が取り込みに必要とする最低限のキーを補う (生成物に無くても落ちないように)"""
    if not isinstance(workflow, dict):
        raise RuntimeError("ワークフローの形式が正しくありません (オブジェクトではありません)")
    workflow.setdefault("id", uuid.uuid4().hex[:16])
    workflow.setdefault("name", "JARVIS workflow")
    workflow.setdefault("nodes", [])
    workflow.setdefault("connections", {})
    workflow.setdefault("settings", {})
    workflow.setdefault("active", False)
    for node in workflow.get("nodes", []):
        if isinstance(node, dict):
            node.setdefault("id", str(uuid.uuid4()))


def import_workflow(path):
    """ワークフロー JSON ファイルを n8n に取り込む。(成功か, メッセージ) を返す"""
    cmd = _n8n_cmd()
    if not cmd:
        raise RuntimeError("n8n がインストールされていません (npm install -g n8n)")
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"ワークフローのファイルが見つかりません: {path.name}")
    # n8n は BOM 付き JSON を受け付けないので、BOM を外し、JSON として検証してから書き戻す
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"ワークフローの JSON が正しくありません: {error}") from error
    for workflow in (data if isinstance(data, list) else [data]):
        _normalize(workflow)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    result = subprocess.run([cmd, "import:workflow", f"--input={path}"],
                            cwd=str(Path(__file__).parent), stdin=subprocess.DEVNULL,
                            capture_output=True, encoding="utf-8", errors="replace",
                            timeout=_IMPORT_TIMEOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(f"n8n への取り込みに失敗しました: {output[-300:]}")
    return True, output[-300:]


def open_ui():
    """ブラウザで n8n を開く"""
    webbrowser.open(N8N_URL)

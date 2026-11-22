#!/usr/bin/env python3
"""harness — ローカル LLM を頭脳にする、小さなエージェントハーネスの雛形。

設計（Guix の強みをそのまま使う）:
  - 頭脳は llama-server（OpenAI 互換 API）。このスクリプトは標準ライブラリ
    だけで書き、モデルには依存しない。
  - サーバーはハーネス自身が面倒を見る。起動時に生死を確認し、居なければ
    自分で llama-server を起動して、読み込み完了まで待ってから会話に入る。
  - ツール（シェル実行）は `guix shell --container` のサンドボックスで動く。
    使える道具は manifest（tools.scm）で宣言的に決める。
  - 道具の追加は専用ツール add_tool が行う。manifest の編集はハーネス側が
    決定的に行い、モデルはパッケージ名を言うだけ。
  - システムプロンプトと記憶はプレーンテキストのファイル。Guix Home で
    配置・世代管理する想定で、$HARNESS_HOME から読む。
  - 全ツール呼び出しは監査ログ（$HARNESS_STATE_DIR/audit.log）に残る。
  - 同じ呼び出しで同じ結果が続いたらモデルに警告し、それでも同じ
    呼び出しが来たら、そのターンを止める（結果が変われば数えない）。

環境変数:
  HARNESS_API_BASE        既定 http://127.0.0.1:8080/v1
  HARNESS_MODEL           既定 local-model（llama-server は読み捨てる）
  HARNESS_MODEL_FILE      自動起動する llama-server の GGUF パス。
                          未指定なら $HARNESS_HOME/model.path の記載を使う
  HARNESS_MMPROJ_FILE     vision モデル用の mmproj の GGUF パス。
                          未指定なら model.path の2行目を使う
  HARNESS_HOME            既定 ~/.config/harness（system.md / MEMORY.md /
                          tools.scm / model.path を置く場所）
  HARNESS_STATE_DIR       既定 ~/.local/state/harness（ログの置き場）
  HARNESS_SANDBOX         0 でサンドボックス無効（素の bash で実行）
  HARNESS_MAX_TURNS       1回の依頼で回すツールループの上限。既定 20
  HARNESS_MAX_NEW_TOOLS   1セッションで add_tool できる上限。既定 5
  HARNESS_SERVER_ARGS     自動起動時に llama-server へ渡す追加の引数
                          （空白区切り。そのまま末尾に足される）
  HARNESS_NO_THINK        0 で Qwen3 系の思考モードを有効に戻す。
                          既定は無効（応答速度優先。chat_template_kwargs で
                          enable_thinking=false を送る）
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("HARNESS_API_BASE", "http://127.0.0.1:8080/v1")
MODEL = os.environ.get("HARNESS_MODEL", "local-model")
HOME = os.path.expanduser(os.environ.get("HARNESS_HOME", "~/.config/harness"))
STATE_DIR = os.path.expanduser(
    os.environ.get("HARNESS_STATE_DIR", "~/.local/state/harness"))
TOOLS_MANIFEST = os.path.join(HOME, "tools.scm")
SANDBOX = os.environ.get("HARNESS_SANDBOX", "1") != "0"
MAX_TURNS = int(os.environ.get("HARNESS_MAX_TURNS", "20"))
MAX_NEW_TOOLS = int(os.environ.get("HARNESS_MAX_NEW_TOOLS", "5"))
NO_THINK = os.environ.get("HARNESS_NO_THINK", "1") != "0"
SHELL_TIMEOUT = 180
OUTPUT_LIMIT = 8000
SERVER_WAIT_SECONDS = 180

SESSION = {"added_tools": 0}


# ---------- ログ ----------

def audit(kind, data):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "audit.log"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "kind": kind, **data},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------- テキスト読み ----------

def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def load_system_prompt():
    parts = [read_text(os.path.join(HOME, "system.md"))]
    memory = read_text(os.path.join(HOME, "MEMORY.md"))
    if memory:
        parts.append("# 記憶\n" + memory)
    return "\n\n".join(p for p in parts if p) or "あなたは小さなエージェントです。"


# ---------- llama-server の自動起動 ----------

def server_root():
    root = API_BASE.rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def server_healthy():
    try:
        with urllib.request.urlopen(server_root() + "/health",
                                    timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def model_config_lines():
    """model.path の有効な行（コメントと空行を除く）を順に返す。"""
    lines = []
    for line in read_text(os.path.join(HOME, "model.path")).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def resolve_model_file():
    env = os.environ.get("HARNESS_MODEL_FILE", "").strip()
    if env:
        return os.path.expanduser(env)
    lines = model_config_lines()
    return os.path.expanduser(lines[0]) if lines else ""


def resolve_mmproj_file():
    env = os.environ.get("HARNESS_MMPROJ_FILE", "").strip()
    if env:
        return os.path.expanduser(env)
    lines = model_config_lines()
    return os.path.expanduser(lines[1]) if len(lines) > 1 else ""


def ensure_server():
    """llama-server が居なければ自分で起動し、準備完了まで待つ。"""
    if server_healthy():
        print("llama-server: 起動済み")
        return True
    exe = shutil.which("llama-server")
    model = resolve_model_file()
    if not exe or not model:
        print("llama-server を自動起動できない:")
        if not exe:
            print("  - llama-server が PATH に無い")
        if not model:
            print("  - モデルの場所が未設定。~/.config/harness/model.path に"
                  " GGUF のパスを1行書くか、HARNESS_MODEL_FILE で指定してね")
        return False
    if not os.path.exists(model):
        print(f"llama-server を自動起動できない: モデルが無い: {model}")
        return False
    mmproj = resolve_mmproj_file()
    parsed = urllib.parse.urlparse(server_root())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080
    # ポートとホストは HARNESS_API_BASE に合わせて明示する（サーバー側の
    # 既定に任せると、待ち受けポートがずれて生死確認が外れる）。
    # 速度の既定: 全レイヤーを GPU へ、flash attention、文脈は作業用に
    # 8192、テンプレートは jinja で評価する（思考オフの指定に必要）。
    server_args = [exe, "-m", model,
                   "--host", host, "--port", str(port),
                   "-ngl", "99", "--flash-attn", "on",
                   "-c", "8192", "--jinja"]
    if mmproj:
        if not os.path.exists(mmproj):
            print(f"llama-server を自動起動できない: mmproj が無い: {mmproj}")
            return False
        server_args += ["--mmproj", mmproj]
    extra = os.environ.get("HARNESS_SERVER_ARGS", "").strip()
    if extra:
        server_args += shlex.split(extra)
    os.makedirs(STATE_DIR, exist_ok=True)
    log_path = os.path.join(STATE_DIR, "llama-server.log")
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(server_args, stdout=log,
                                stderr=subprocess.STDOUT,
                                start_new_session=True)
    audit("server_start", {"pid": proc.pid, "model": model,
                           "mmproj": mmproj})
    print(f"llama-server を起動した (pid {proc.pid})。"
          "モデルの読み込みを待っています...")
    for _ in range(SERVER_WAIT_SECONDS):
        if server_healthy():
            print("llama-server: 準備完了")
            return True
        if proc.poll() is not None:
            if server_healthy():
                return True
            print(f"llama-server の起動に失敗した。ログ: {log_path}")
            return False
        time.sleep(1)
    print(f"llama-server の準備が {SERVER_WAIT_SECONDS} 秒で終わらなかった。"
          f"ログ: {log_path}")
    return False


# ---------- ツール ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "シェルコマンドをサンドボックス内で実行し、出力を返す。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "テキストファイルを読む。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "テキストファイルに書き込む（上書き）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_tool",
            "description": "サンドボックスで使える道具を増やす。Guix の"
                           "パッケージ名を1つ指定すると tools.scm に追加され、"
                           "次のシェル実行から使えるようになる。",
            "parameters": {
                "type": "object",
                "properties": {"package": {"type": "string"}},
                "required": ["package"],
            },
        },
    },
]


def sandbox_wrap(command):
    """guix shell --container でコマンドを包む。使えなければ素の bash。"""
    guix = shutil.which("guix")
    if SANDBOX and guix and os.path.exists(TOOLS_MANIFEST):
        return [
            guix, "shell", "--container", "--network",
            "--manifest=" + TOOLS_MANIFEST,
            "--share=" + os.getcwd(),
            "--", "bash", "-c", command,
        ]
    return ["bash", "-c", command]


def tool_run_shell(args):
    cmd = sandbox_wrap(args["command"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=SHELL_TIMEOUT)
        out = (proc.stdout or "") + (proc.stderr or "")
        return (out or "(出力なし)")[:OUTPUT_LIMIT] + \
            f"\n[exit {proc.returncode}]"
    except subprocess.TimeoutExpired:
        return f"タイムアウト（{SHELL_TIMEOUT}秒）"


def tool_read_file(args):
    text = read_text(os.path.expanduser(args["path"]))
    return text[:OUTPUT_LIMIT] if text else "(読めないか空のファイル)"


def tool_write_file(args):
    path = os.path.expanduser(args["path"])
    with open(path, "w", encoding="utf-8") as f:
        f.write(args["content"])
    return f"{path} に書いた（{len(args['content'])} 文字）"


def tool_add_tool(args):
    """tools.scm に Guix パッケージ名を1つ足す。編集は決定的に行う。"""
    name = (args.get("package") or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9+._-]*(@[0-9][0-9A-Za-z.-]*)?",
                        name):
        return f"パッケージ名の形が正しくない: {name}"
    if SESSION["added_tools"] >= MAX_NEW_TOOLS:
        return (f"このセッションでの道具の追加は上限（{MAX_NEW_TOOLS}個）に"
                "達した。その旨を相手に伝えること。")
    try:
        with open(TOOLS_MANIFEST, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return f"{TOOLS_MANIFEST} が読めないため追加できない。"
    names = re.findall(r'"([^"]+)"', text)
    if name in names or name.split("@")[0] in names:
        return f"{name} は既に tools.scm にある。そのまま使える。"
    start = text.find("'(")
    if start < 0:
        return "tools.scm のリストが見つからないため追加できない。"
    depth, end = 0, -1
    for i in range(start + 1, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return "tools.scm のリストの終わりが見つからないため追加できない。"
    inner = "\n".join(f'   "{n}"' for n in names + [name])
    new_text = text[:start] + "'(\n" + inner + "\n" + text[end:]
    with open(TOOLS_MANIFEST, "w", encoding="utf-8") as f:
        f.write(new_text)
    SESSION["added_tools"] += 1
    return (f"{name} を tools.scm に追加した。"
            "次のシェル実行から使える（取得に時間がかかることがある）。")


DISPATCH = {
    "run_shell": tool_run_shell,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "add_tool": tool_add_tool,
}


# ---------- 対話ループ ----------

def chat(messages):
    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
    }
    if NO_THINK:
        # Qwen3 系の思考モードを切る。思考トレースはエージェントの
        # ターンごとに数百トークンの待ち時間になるため、既定で切る。
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_BASE.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.load(resp)
    except urllib.error.URLError as e:
        raise SystemExit(
            f"llama-server に繋がらない（{API_BASE}）: {e}\n"
            "自動起動のログは ~/.local/state/harness/llama-server.log にある。")
    return data["choices"][0]["message"]


def run_turn(messages):
    """ツールのループを回し、最後の返事を返す。

    ループ検出は「同じ引数で呼んで、同じ結果が返った」場合だけ数える。
    結果が変わる繰り返し（状態を見ながらの再実行など）は正当なので
    数えない。同じ結果が3回続いたら結果に警告を付け、それでも同じ
    呼び出しが来たらターンを止める。
    """
    seen = {}
    for _ in range(MAX_TURNS):
        msg = chat(messages)
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""
        stop = False
        for call in calls:
            fn = call["function"]
            name = fn["name"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            sig = name + ":" + json.dumps(args, sort_keys=True,
                                          ensure_ascii=False)
            print(f"  [tool] {name}: "
                  f"{json.dumps(args, ensure_ascii=False)[:120]}")
            audit("tool_call", {"name": name, "args": args})
            entry = seen.setdefault(sig, {"result": None, "same": 0,
                                          "warned": False})
            if entry["warned"]:
                result = ("同じ呼び出しが警告のあとも繰り返されたため、"
                          "このターンは止めた。")
                stop = True
            else:
                handler = DISPATCH.get(name)
                result = handler(args) if handler else f"未知のツール: {name}"
                if result == entry["result"]:
                    entry["same"] += 1
                else:
                    entry["same"] = 0
                    entry["warned"] = False
                entry["result"] = result
                if entry["same"] >= 2 and not entry["warned"]:
                    entry["warned"] = True
                    result += ("\n[ハーネスからの注意] この呼び出しは同じ"
                               "結果が続いている。次に同じ呼び出しをしたら"
                               "ターンを止める。別のやり方に変えるか、"
                               "できない理由を相手に伝えること。")
            audit("tool_result", {"name": name, "result": result[:500]})
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })
        if stop:
            return "（同じ操作の繰り返しが警告後も続いたので、このターンは止めた）"
    return "（ツールループの上限に達したので止めた）"


def main():
    print(f"harness: api={API_BASE} model={MODEL} home={HOME} "
          f"sandbox={'on' if SANDBOX else 'off'}")
    if not ensure_server():
        print("サーバーが用意できないため終了する。")
        return
    print("終了は Ctrl-D。")
    audit("session_start", {"api": API_BASE, "model": MODEL})
    messages = [{"role": "system", "content": load_system_prompt()}]
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        messages.append({"role": "user", "content": line})
        print("\nharness> " + run_turn(messages))


if __name__ == "__main__":
    main()

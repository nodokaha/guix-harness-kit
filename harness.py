#!/usr/bin/env python3
"""harness — ローカル LLM を頭脳にする、小さなエージェントハーネスの雛形。

設計（Guix の強みをそのまま使う）:
  - 頭脳は llama-server（OpenAI 互換 API）に任せる。このスクリプトは標準
    ライブラリだけで書き、モデルには依存しない。
  - ツール（シェル実行）は `guix shell --container` のサンドボックスで動く。
    使える道具は manifest（tools.scm）で宣言的に決める。
  - システムプロンプトと記憶はプレーンテキストのファイル。Guix Home で
    配置・世代管理する想定で、$HARNESS_HOME から読む。

環境変数:
  HARNESS_API_BASE        既定 http://127.0.0.1:8080/v1
  HARNESS_MODEL           既定 local-model（llama-server は読み捨てる）
  HARNESS_HOME            既定 ~/.config/harness（system.md / MEMORY.md /
                          tools.scm を置く場所）
  HARNESS_SANDBOX         0 でサンドボックス無効（素の bash で実行）
  HARNESS_MAX_TURNS       1回の依頼で回すツールループの上限。既定 20
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

API_BASE = os.environ.get("HARNESS_API_BASE", "http://127.0.0.1:8080/v1")
MODEL = os.environ.get("HARNESS_MODEL", "local-model")
HOME = os.path.expanduser(os.environ.get("HARNESS_HOME", "~/.config/harness"))
TOOLS_MANIFEST = os.path.join(HOME, "tools.scm")
SANDBOX = os.environ.get("HARNESS_SANDBOX", "1") != "0"
MAX_TURNS = int(os.environ.get("HARNESS_MAX_TURNS", "20"))
SHELL_TIMEOUT = 180
OUTPUT_LIMIT = 8000


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


DISPATCH = {
    "run_shell": tool_run_shell,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
}


def chat(messages):
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
    }).encode()
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
            "先に llama-server を起動してね。")
    return data["choices"][0]["message"]


def run_turn(messages):
    """ツールのループを回し、最後の返事を返す。"""
    for _ in range(MAX_TURNS):
        msg = chat(messages)
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""
        for call in calls:
            fn = call["function"]
            name = fn["name"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"  [tool] {name}: {json.dumps(args, ensure_ascii=False)[:120]}")
            handler = DISPATCH.get(name)
            result = handler(args) if handler else f"未知のツール: {name}"
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })
    return "（ツールループの上限に達したので止めた）"


def main():
    print(f"harness: api={API_BASE} model={MODEL} home={HOME} "
          f"sandbox={'on' if SANDBOX else 'off'}")
    print("終了は Ctrl-D。")
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

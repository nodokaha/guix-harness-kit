#!/usr/bin/env python3
"""vision_probe — VLM にスクリーンショットを見せて、ボタンの座標を聞く実験。

使い方:
  python3 vision_probe.py /tmp/shot.png 再挑戦ボタン

前提: llama-server（Qwen3-VL などの vision モデル + --mmproj）が
127.0.0.1:8080 で動いていること。モデルには座標を 0〜1000 の相対値で
返させる。標準ライブラリだけ。
"""

import base64
import json
import sys
import urllib.request

API = "http://127.0.0.1:8080/v1/chat/completions"


def main():
    if len(sys.argv) < 3:
        print("使い方: vision_probe.py <画像> <探すものの名前>")
        return
    path, target = sys.argv[1], sys.argv[2]
    with open(path, "rb") as f:
        img = base64.b64encode(f.read()).decode()
    prompt = (
        f"この画像の中で「{target}」の中心の座標を、x・y ともに 0〜1000 の"
        "相対座標で答えてください。JSON だけを返してください: "
        '{"x": 数値, "y": 数値}。見つからなければ {"x": -1, "y": -1}。'
    )
    body = {
        "model": "local-model",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{img}"}},
            ],
        }],
    }
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    print(data["choices"][0]["message"].get("content"))


if __name__ == "__main__":
    main()

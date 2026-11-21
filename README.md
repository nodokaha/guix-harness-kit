# guix-harness-kit — Guix で管理するエージェントハーネスの雛形

ローカル LLM（llama-server）を頭脳に、道具・箱・記憶・履歴を全部 Guix の
宣言的な仕組みに乗せるための最小構成。

## 構成

| ファイル | 役割 |
|---|---|
| `harness.py` | ハーネス本体。標準ライブラリだけの Python スクリプト |
| `harness.scm` | llama.cpp とハーネスの Guix パッケージ定義（llama.cpp は伝播入力で一緒に並ぶ） |
| `texts/tools.scm` | サンドボックス用ツール manifest の雛形 |
| `texts/system.md` | システムプロンプトの雛形 |
| `texts/MEMORY.md` | 記憶テキストの雛形 |

## Guix との対応

- **ツールをその場で用意** — シェル実行のたびに `guix shell --container
  --manifest=tools.scm` で環境を組み立てる。道具の追加は manifest に1行。
- **サンドボックス** — コンテナ内で実行し、作業ディレクトリだけを共有する。
  ネットワーク可否も `--network` の付け外しで宣言できる。
- **テキストも Guix で管理** — システムプロンプトと記憶はプレーンテキスト。
  `~/.config/harness/` に置き、Guix Home の home-files-service で配置すれば
  home の世代と一緒に管理できる。
- **消したり世代で管理** — ツール環境をプロファイルとして固定すれば
  `--list-generations` / `--roll-back` / `--delete-generations` が使える。
  ハーネス本体やテキストも、それぞれ Guix と git の世代で追える。

## 自己拡張ループ

エージェントは、道具が足りなければ tools.scm に Guix パッケージ名を足して
自分で道具を増やせる（手順は texts/system.md に記載）。サンドボックスは
実行ごとに manifest から作り直されるため、本体を再起動せずに道具だけが
世代交代する。tools.scm を git で管理しておけば、変更をいつでも戻せる。

## 使い方

1. テキスト類を配置する:

   ```
   mkdir -p ~/.config/harness
   cp texts/tools.scm texts/system.md texts/MEMORY.md ~/.config/harness/
   ```

2. シェルに入る（llama-server と harness の両方が PATH に並ぶ）:

   ```
   guix shell -f harness.scm
   ```

3. 端末1で llama-server、端末2でハーネスを起動する（モデルは好きな GGUF を）:

   ```
   llama-server -m Qwen3-30B-A3B-Q4_K_M.gguf
   harness
   ```

## 環境変数

- `HARNESS_API_BASE` — 既定 `http://127.0.0.1:8080/v1`
- `HARNESS_MODEL` — 既定 `local-model`
- `HARNESS_HOME` — 既定 `~/.config/harness`
- `HARNESS_SANDBOX=0` — サンドボックスを切って素の bash で実行
- `HARNESS_MAX_TURNS` — ツールループの上限。既定 20

## 次に育てる候補

- 危険なコマンドの承認フロー（実行前に相手へ確認）
- 記憶への追記をツール化する（write 専用の memory ツール）
- ツール manifest をタスク種別ごとに分ける（構築用／調査用など）
- 会話ログを世代管理されるディレクトリへ残す
- Guix Home のサービス化（llama-server と合わせて常駐）

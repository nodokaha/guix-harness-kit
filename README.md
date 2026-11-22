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
| `texts/model.path` | 自動起動する llama-server のモデル（GGUF）の場所 |

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

エージェントは、道具が足りなければ専用ツール `add_tool` で Guix パッケージを
指定して道具を増やせる（手順は texts/system.md に記載）。manifest の編集は
ハーネス側が決定的に行い、モデルは名前を言うだけ。サンドボックスは実行ごとに
manifest から作り直されるため、本体を再起動せずに道具だけが世代交代する。
追加は1セッション最大5個まで。tools.scm を git で管理しておけば戻せる。

## サーバーの自動起動

ハーネスは起動時に llama-server の生死を確認し、居なければ自分で起動して
読み込み完了まで待つ。モデルの場所は `~/.config/harness/model.path` に書く
（1行目がモデル本体、2行目に mmproj を書くと vision モデルで起動する）。
サーバーはハーネスを終了しても残る（止めるときは
`pkill llama-server`）。サーバーの出力と監査ログは
`~/.local/state/harness/` に残る。

自動起動の引数は速度寄りに固定してある: 待ち受けは `HARNESS_API_BASE` の
ホストとポートを明示し、全レイヤーを GPU に載せ（`-ngl 99`）、flash
attention を有効にし、文脈は 8192、テンプレートは jinja 評価にする。
追加の引数は `HARNESS_SERVER_ARGS` で末尾に足せる。手動でサーバーを
立てる場合も、同じ引数で立てること（特にポートは 8080 を明示する）。

Qwen3 系の思考モードは、リクエスト側で `enable_thinking=false` を送って
既定で切る（ターンのたびに思考トレースを生成すると応答が大きく遅れる）。
思考が欲しいときは `HARNESS_NO_THINK=0`。

## 使い方

1. ハーネスを起動する（サーバーは自動で立つ）:

   ```
   guix shell -f harness.scm -- harness
   ```

   初回起動時に、`~/.config/harness/` へ初期ファイル一式
   （tools.scm / system.md / MEMORY.md / model.path）が自動でそろう。
   あとは model.path に自分のモデルのパスを書けばよい。既にある
   ファイルは上書きしない。Guix Home で管理したい場合は、texts/ の
   ファイルを自分で配置してもよい（その場合はそちらが使われる）。

## 環境変数

- `HARNESS_API_BASE` — 既定 `http://127.0.0.1:8080/v1`
- `HARNESS_MODEL` — 既定 `local-model`
- `HARNESS_MODEL_FILE` — 自動起動するモデルの GGUF パス（model.path より優先）
- `HARNESS_MMPROJ_FILE` — mmproj の GGUF パス（model.path の2行目より優先）
- `HARNESS_HOME` — 既定 `~/.config/harness`
- `HARNESS_STATE_DIR` — 既定 `~/.local/state/harness`
- `HARNESS_SANDBOX=0` — サンドボックスを切って素の bash で実行
- `HARNESS_MAX_TURNS` — ツールループの上限。既定 20
- `HARNESS_MAX_NEW_TOOLS` — 1セッションの道具追加の上限。既定 5
- `HARNESS_SERVER_ARGS` — 自動起動時に llama-server へ渡す追加の引数
- `HARNESS_NO_THINK=0` — Qwen3 系の思考モードを有効に戻す（既定は無効）
- `HARNESS_SHELL_TIMEOUT` — シェル実行1回の制限時間（秒）。既定 600

起動時は、サーバーの用意のあとにサンドボックス環境の構築（ウォーム
アップ）を先に済ませる。manifest が変わった直後の初回構築は数分かかる
ことがあるが、ターンの途中で黙って待たせる代わりに、ここで進捗と
所要時間を表示する。

## 次に育てる候補

- 危険なコマンドの承認フロー（実行前に相手へ確認）
- 記憶への追記をツール化する（write 専用の memory ツール）
- ツール manifest をタスク種別ごとに分ける（構築用／調査用など）
- 会話ログを世代管理されるディレクトリへ残す
- Guix Home のサービス化（llama-server と合わせて常駐）

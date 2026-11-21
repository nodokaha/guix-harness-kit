;;; harness.scm — llama.cpp とハーネスを一緒にした Guix パッケージ定義
;;;
;;; 同じフォルダに harness.py を置いて使う:
;;;   guix shell -f harness.scm        # llama-server と harness の両方が入る
;;;   llama-server -m model.gguf       # 端末1: 頭脳を起動
;;;   harness                          # 端末2: エージェントを起動
;;;
;;; 設計メモ:
;;; - llama-cpp は harness の propagated-inputs に入れてある。だから
;;;   harness を入れたプロファイルには llama-server も一緒に並ぶ。
;;; - ハーネス本体は標準ライブラリだけの Python スクリプト1本。依存は
;;;   python と、サンドボックス実行のために呼ぶ guix 本体だけ。
;;; - システムプロンプト・記憶・ツール manifest はパッケージに入れず、
;;;   ~/.config/harness/ のプレーンテキストとして Guix Home で管理する想定。
;;; - llama.cpp 側の注意: GGML_NATIVE=OFF（オフロード先の CPU 向けに
;;;   最適化されると desktop で落ちるため）。LLAMA_CURL=OFF（モデルは
;;;   別に落とす前提）。実行時に NVIDIA の Vulkan ICD が見える必要がある。

(define-module (harness)
  #:use-module (guix gexp)
  #:use-module (guix packages)
  #:use-module (guix download)
  #:use-module (guix build-system cmake)
  #:use-module (guix build-system gnu)
  #:use-module (guix licenses)
  #:use-module (gnu packages package-management)
  #:use-module (gnu packages pkg-config)
  #:use-module (gnu packages python)
  #:use-module (gnu packages vulkan))

(define-public llama-cpp
  (package
    (name "llama-cpp")
    (version "12606")
    (source
     (origin
       (method url-fetch)
       (uri (string-append "https://github.com/ggml-org/llama.cpp/archive/"
                           "refs/tags/b" version ".tar.gz"))
       (sha256
        (base32 "08xxp68lcj8nn641rbs3zwcjnl7q4n6nbna0bksj40r3wqgp00rw"))))
    (build-system cmake-build-system)
    (arguments
     (list #:tests? #f
           #:configure-flags
           #~(list "-DBUILD_SHARED_LIBS=ON"
                   "-DGGML_VULKAN=ON"
                   "-DGGML_NATIVE=OFF"
                   "-DLLAMA_CURL=OFF"
                   "-DCMAKE_BUILD_TYPE=Release")))
    (native-inputs (list pkg-config python-minimal shaderc vulkan-headers
                         spirv-headers glslang))
    (inputs (list vulkan-loader))
    (home-page "https://github.com/ggml-org/llama.cpp")
    (synopsis "Port of Facebook's LLaMA model in C/C++")
    (description
     "llama.cpp は LLaMA 系モデルの推論を C/C++ で実装したもの。
llama-server（OpenAI 互換 API とブラウザ UI 付き）、llama-cli、llama-bench
などを含む。この定義では ggml の Vulkan バックエンドを有効にしてある。")
    (license expat)))

(define-public harness
  (package
    (name "harness")
    (version "0.1")
    (source (local-file "harness.py"))
    (build-system gnu-build-system)
    (arguments
     (list #:tests? #f
           #:phases
           #~(modify-phases %standard-phases
               (delete 'configure)
               (delete 'build)
               (replace 'install
                 (lambda* (#:key inputs outputs #:allow-other-keys)
                   (let* ((out (assoc-ref outputs "out"))
                          (bin (string-append out "/bin"))
                          (file (string-append bin "/harness")))
                     (mkdir-p bin)
                     (copy-file "harness.py" file)
                     (chmod file #o555)
                     ;; shebang をストアの python に差し替える
                     (substitute* file
                       (("/usr/bin/env python3")
                        (string-append (assoc-ref inputs "python")
                                       "/bin/python3")))
                     ;; 実行時に python と guix（サンドボックス用）が見えるように
                     (wrap-program file
                       `("PATH" ":" prefix
                         (,(string-append (assoc-ref inputs "python") "/bin")
                          ,(string-append (assoc-ref inputs "guix")
                                          "/bin"))))))))))
    (inputs (list python guix))
    ;; llama-server を同じプロファイルに並べるための伝播入力
    (propagated-inputs (list llama-cpp))
    (home-page "")
    (synopsis "ローカル LLM を頭脳にする小さなエージェントハーネス")
    (description
     "llama-server（OpenAI 互換 API）を頭脳に、シェル実行・ファイル読み書きの
ツールを持つ対話型エージェントの雛形。シェル実行は guix shell --container の
サンドボックスで走り、使える道具は manifest で宣言する。システムプロンプトと
記憶は ~/.config/harness/ のプレーンテキストから読む。llama-cpp を伝播入力に
持ち、同じプロファイルに llama-server が一緒に並ぶ。")
    (license expat)))

harness

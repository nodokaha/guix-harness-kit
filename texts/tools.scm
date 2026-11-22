;;; tools.scm — ハーネスのサンドボックス用ツール manifest の雛形
;;;
;;; 置き場所: ~/.config/harness/tools.scm
;;; ハーネスはシェル実行のたびに、この manifest で guix shell --container を
;;; 組み立てて、その中でコマンドを走らせる。道具を増やしたければここに足す。
;;;
;;; 世代管理したいときは、プロファイルとして固定する:
;;;   guix package -m tools.scm -p ~/.local/share/harness/profiles/tools
;;;   guix package -p ~/.local/share/harness/profiles/tools --list-generations
;;;   guix package -p ~/.local/share/harness/profiles/tools --roll-back
;;;   guix package -p ~/.local/share/harness/profiles/tools --delete-generations=2

(specifications->manifest
 '("bash"
   "coreutils"
   "findutils"
   "diffutils"
   "grep"
   "sed"
   "gawk"
   "git"
   "curl"
   "python"
   "procps"
   "openssh"))

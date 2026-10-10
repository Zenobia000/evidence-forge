#!/usr/bin/env bash
# PreToolUse hook (matcher: Bash). Blocks git commands the repo's skills forbid.
# Contract: exit 2 = block the tool call; stderr goes back to the model as the reason.

# T3/T4：這些路徑無論檢查多綠都回到人手上。**按路徑判，不按對 diff 的判斷** ——
# 判斷正是分級要拿掉的東西（同一顆 PR 曾被兩個獨立代理判出相反的等級，
# 家規第 28 條記過）。
#
# 每一條都對得上 GRANULARITY.md 的 T3／T4：
#   閘門與代理指示   守衛、技能、CLAUDE.md、AGENTS.md —— 改了它就是在改
#                    「什麼算通過」，受審的人不能自己定標準
#   schema           實體契約。改錯不會當場紅，會在三個月後的某份檔案上紅
#   ADR              決策紀錄。它是後來每一次判斷的依據
#   CI/CD 本身       跑檢查的東西，不能由檢查放行
#   Human Gate       人類否決權的機制本身，不能由受審的人改
#   家規與帳本       規則本身。規則自己放行自己的修改，就沒有規則了
#
# 上面這幾類每個專案都一樣，所以寫死在這裡（通用區塊）。schema 那條是**位置
# 無關**的樣式：專案的 schema 住哪事先不知道，而漏掉一條的代價（契約被自己改掉、
# 三個月後才在某份檔案上紅）遠高於多擋幾次。
#
# 每個專案自己的高風險路徑（弄錯會壞使用者的資料、或牽涉安全的地基）放在
# 同目錄的 `human-only-paths.conf`，一行一個 glob。那份檔本身在 `.claude/hooks/*`
# 底下，所以改它也是人的按鈕——規則不能自己放行自己的修改。
#
# 讀不到 conf 時**出聲、照擋通用區塊**：不因為少一份設定檔就整個放行，也不因
# 為少一份設定檔就把每一顆 PR 都擋死（那會訓練人繞過閘門）。
HUMAN_ONLY_CONF="$(dirname "${BASH_SOURCE[0]}")/human-only-paths.conf"
DEFAULT_CHECK='pnpm test && pnpm typecheck && pnpm lint'

# 去掉前後空白。
_trim() { _t=$1; _t=${_t#"${_t%%[![:space:]]*}"}; printf '%s' "${_t%"${_t##*[![:space:]]}"}"; }

# conf 的一行樣式正規化成 repo 根目錄起算的 glob。
#
# 這段存在的理由是「寫錯了不會出聲」：審查實測過，`src/server`（沒帶 /*）、
# `./src/server/*`、`"src/server/*"`、行尾 `# 註解`、Windows 的反斜線、檔頭 BOM，
# 每一種都會讓那條樣式**一個檔都比不中**，而 PR 照樣被當成 T1／T2 放行
# ——正是 L-007「存在但形狀不對」那一半。人會照直覺寫，所以把直覺的寫法都收進來。
_norm_glob() {
  _g=$1
  _g=${_g#$'\xef\xbb\xbf'}                         # UTF-8 BOM
  case "$_g" in *[[:space:]]'#'*) _g=${_g%%[[:space:]]#*} ;; esac   # 行尾註解
  _g=$(_trim "$_g")
  case "$_g" in \"*\"|\'*\') _g=${_g#?}; _g=${_g%?} ;; esac         # 前後引號
  _g=${_g//\\//}                                   # 反斜線 → 斜線
  while :; do case "$_g" in ./*) _g=${_g#./} ;; /*) _g=${_g#/} ;; *) break ;; esac; done
  case "$_g" in */) _g="${_g}*" ;; esac            # 目錄寫法 src/server/ → src/server/*
  printf '%s' "$_g"
}

# 把 conf 讀進 CONF_GLOBS（換行分隔）與 CONF_CHECK。同一個行程只讀一次；
# 測試改了 HUMAN_ONLY_CONF 之後會重讀。
load_conf() {
  [ "${_conf_from:-}" = "$HUMAN_ONLY_CONF" ] && return 0
  _conf_from=$HUMAN_ONLY_CONF
  CONF_GLOBS=""; CONF_CHECK=$DEFAULT_CHECK
  if [ ! -r "$HUMAN_ONLY_CONF" ]; then
    printf '%s\n' "guard-git: 讀不到 $HUMAN_ONLY_CONF —— 只擋通用區塊（守衛、技能、代理設定、CLAUDE.md、schema、ADR、CI、家規與帳本），專案自己的高風險路徑這次沒有擋；本機檢查用預設的「$DEFAULT_CHECK」。" >&2
    return 0
  fi
  while IFS= read -r _l || [ -n "$_l" ]; do
    _l=${_l%$'\r'}
    _l=${_l#$'\xef\xbb\xbf'}
    _l=$(_trim "$_l")
    case "$_l" in
      ''|'#'*) continue ;;
      check:*)
        _c=$(_trim "${_l#check:}")
        # 只有註解（`check: # 之後再填`）或空白就當沒寫：`bash -c '# …'` 回 0，
        # 退路會一個檢查都沒跑就放行。
        case "$_c" in ''|'#'*) ;; *) CONF_CHECK=$_c ;; esac ;;
      *)
        _g=$(_norm_glob "$_l")
        [ -z "$_g" ] && continue
        # 沒有萬用字元的樣式同時當「檔」與「目錄」：寫 `src/server` 的人兩種都想擋。
        case "$_g" in *'*'*|*'?'*|*'['*) CONF_GLOBS="$CONF_GLOBS$_g"$'\n' ;;
          *) CONF_GLOBS="$CONF_GLOBS$_g"$'\n'"$_g/*"$'\n' ;; esac ;;
    esac
  done < "$HUMAN_ONLY_CONF"
}

# 合併閘退路要跑的本機檢查。抽成函式是為了測得到（L-007、L-008）：審查實測過，
# 把這裡換成預設值、或漏掉 load_conf（子 shell 裡讀的 conf 不會帶回來，
# CONF_CHECK 變空字串，`bash -c ""` 回 0）都不會讓任何一發測試紅。
# 空字串再保一次底：這條路寧可跑預設的，也不能什麼都不跑。
run_local_checks() {
  load_conf
  [ -z "$CONF_CHECK" ] && CONF_CHECK=$DEFAULT_CHECK
  (cd "$1" && bash -c "$CONF_CHECK") 2>&1
}

human_only_path() {
  case "$1" in
    .claude/hooks/*|.claude/skills/*|CLAUDE.md|AGENTS.md|*/CLAUDE.md|*/AGENTS.md) return 0 ;;
    # 守衛是靠 settings.json 掛上去的、子代理的模型與權限寫在 agents/：
    # 改它們等於改「什麼算通過」，跟改守衛本身同一級。
    .claude/settings.json|.claude/agents/*) return 0 ;;
    schemas/*|*/schemas/*) return 0 ;;
    docs/10-truth/adr/*) return 0 ;;
    .github/workflows/*|.github/actions/*) return 0 ;;
    Ai-coding-principle/README.md|Ai-coding-principle/LESSONS.md) return 0 ;;
  esac
  load_conf
  while IFS= read -r _g; do
    [ -z "$_g" ] && continue
    # $_g 不加引號：case 把它當 glob 樣式比對（`*` 也吃斜線，跟上面寫死的一樣）。
    # shellcheck disable=SC2254
    case "$1" in $_g) return 0 ;; esac
  done <<EOF
$CONF_GLOBS
EOF
  return 1
}

# 逐行判 T3，把命中的路徑印出來（空白分隔，空字串代表沒有命中）。
#
# **這支存在的理由是「逐行」那三個字。** 2026-09-09 之前這段是內嵌在合併閘裡的
# 迴圈，而餵進 while 的字串先被 `tr -d '<換行>'` 清過一次——原意是清行尾的 CR
# （同一支檔案後面那個 fail 清單寫的就是 `tr -d '\r'`），寫成了清掉換行本身。
# 於是整份檔案清單黏成一行，`read` 只拿到一個大字串，分級**靜靜地半開**：
#
#   有萬用字元的樣式（.claude/hooks/*、.claude/skills/*、docs/10-truth/adr/*…）
#     只有在那個檔剛好排清單第一個時才命中；
#   精確比對的樣式（CLAUDE.md、Ai-coding-principle/LESSONS.md…）
#     只有在它是唯一變更時才命中。
#
# 也就是說 `CLAUDE.md` 只要跟任何另一個檔一起改就會被放行，而它正是
# 「什麼算通過」的定義檔。這個洞從一次重寫就在，活了好幾週才被踩出來
# （帳本 L-007「守衛只擋了一半，而漏掉的那半無聲」——這次是守衛自己）。
#
# 抽成函式不是整理，是為了讓它測得到：當時那 37 發測試沒有一發碰得到內嵌迴圈，
# 那才是它活下來的原因。CR 的清理改成逐行的參數展開，範圍縮到一行的結尾。
t3_hits() {
  _hits=""
  while IFS= read -r _f; do
    _f=${_f%$'\r'}
    [ -z "$_f" ] && continue
    if human_only_path "$_f"; then _hits="$_hits $_f"; fi
  done <<EOF
$1
EOF
  printf '%s' "$_hits"
}

# 目前沒有呼叫端 —— 2026-09-07 拿掉 ai-verified 那道閘門時，唯一用到它的
# 「有沒有註解點名這顆 head」檢查跟著移除了。刻意留著這支函式：把閘門放回去
# 是一次 revert 的事，而這段比對規則（短 sha、前一個字元不能是 hex）是實測
# 換來的，重寫一次就有機會寫錯，而寫錯的症狀是它安靜地永遠通過。
#
# Is commit $1 named anywhere in the text $2? Review agents write short shas in
# backticks ("verified `e544489`"), so match the first 7 hex characters and let
# any further hex follow — that way a comment quoting the full 40-character sha
# counts too. The character before must not be hex, or a 7-character window in
# the middle of some other sha would match.
sha_named_in() {
  _s=$(printf '%s' "$1" | tr 'A-F' 'a-f' | cut -c1-7)
  [ "${#_s}" -eq 7 ] || return 1
  printf '%s' "$2" | tr 'A-F' 'a-f' | grep -qE "(^|[^0-9a-f])${_s}[0-9a-f]*"
}

# Does $1 look like a blob sha? Length alone is not enough — when 'git rev-parse'
# cannot resolve its argument it prints the argument back on stdout, and the
# Git Bash path-mangled form of a 'ref:path' argument is a plausible-length
# string of junk. Both halves of that were found in review.
ok_blob() { case "$1" in *[!0-9a-f]* | "") return 1 ;; esac; [ ${#1} -eq 40 ]; }

# main 上那一份 guard-git.sh 的 blob，或什麼都不回。
#
# 兩個旗標缺一不可，而且缺哪一個的症狀不一樣：沒有 --verify，rev-parse 解不開
# 時會把參數原樣印到 stdout（不是空、不是只有 stderr），於是「讀不到就擋」那道
# 判斷永遠不會觸發；沒有 MSYS_NO_PATHCONV，Git Bash 會把 'origin/main:.claude/…'
# 改寫成 'origin\main;.claude\…'，永遠解不開。兩個都是審查抓出來的。
#
# 這是一個函式而不是一行內嵌指令，因為測試要能呼叫**這一份**——把指令抄一份
# 到測試檔裡跑，驗的是 git 不是這支守衛（帳本 L-008）。
main_guard_blob() {
  MSYS_NO_PATHCONV=1 git -C "${1:-.}" rev-parse --verify --quiet "${2:-origin/main}:.claude/hooks/guard-git.sh" 2>/dev/null
}

# Sourcing with GUARD_GIT_LIB=1 stops here, so the test file can call the helper
# above directly instead of going through a hook payload. The BASH_SOURCE test
# asks the real question — am I being sourced — because 'return' outside a
# sourced file is an error, and the variable could leak in from an environment
# the hook does not control. Without it a stray GUARD_GIT_LIB=1 still fails
# closed, but bash prints its complaint into the same stderr the block reason
# travels on.
if [ "${GUARD_GIT_LIB:-}" = "1" ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi

deny() { printf '%s\n' "$1" >&2; exit 2; }

# 沒有 jq 就讀不到指令，下面每一條規則都會拿到空字串而靜靜放行——守衛等於
# 不存在，而且不會有任何訊息。所以缺 jq 時出聲並擋，不靜默失效。
if ! command -v jq >/dev/null 2>&1; then
  deny "Blocked: guard-git.sh 需要 jq 才讀得到指令，這台機器找不到 jq。沒有它守衛會靜靜全部放行，所以先擋。請人裝好 jq（例：apt install jq、brew install jq、winget install jqlang.jq）再試。"
fi

cmd=$(jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$cmd" ] && exit 0

# 把「引號分隔字」heredoc 的內容拿掉，再拿去比對規則。
#
# 為什麼安全：`<<'EOF'` 與 `<<"EOF"` 的內容 shell **不會**展開也不會執行 ——
# 裡面寫一百次 `gh pr merge` 也不會有任何一次跑起來，那些字不該被當成指令看。
# 不帶引號的 `<<EOF` 會展開 `$( )`，因此**不動它**：那裡面的東西真的會執行。
#
# 為什麼非修不可：下面那條「整串裡出現兩次就擋」不看位置（刻意的，為了擋
# `$( )` 裡藏第二發），而 **markdown 表格的欄位分隔線長得跟 shell 的管線一
# 模一樣**。於是「一份談論合併閘、而且用表格排版的文件」在結構上幾乎必然
# 踩中 —— 而這個 repo 正好有一整條線在寫這種文件。
#
# 被誤擋的人下一步一定是找路繞過去，那才是真正的損失：**一道會誤傷的閘門，
# 訓練人養成繞過閘門的習慣**（帳本 L-007 的同一個道理）。
#
# 分隔字沒有收尾時**原樣退回，不剝**：未閉合的 heredoc 在 shell 裡是語法錯誤，
# 什麼都不會執行，但守衛在這種形狀下寧可多看不要少看。
strip_quoted_heredocs() {
  awk '
    { orig[NR] = $0 }
    skip == 1 {
      probe = $0; sub(/^[ \t]+/, "", probe)
      if (probe == delim) { skip = 0 }
      next
    }
    {
      keep[++k] = $0
      if (match($0, /<<-?[ \t]*(\047[^\047]+\047|"[^"]+")/)) {
        d = substr($0, RSTART, RLENGTH)
        sub(/^<<-?[ \t]*/, "", d); gsub(/\047|"/, "", d)
        delim = d; skip = 1
      }
    }
    END {
      if (skip == 1) { for (n = 1; n <= NR; n++) print orig[n] }
      else { for (n = 1; n <= k; n++) print keep[n] }
    }
  '
}
cmd=$(printf '%s\n' "$cmd" | strip_quoted_heredocs)


# Rules match only at command position (line start or after && || ; | $( ),
# so prose *mentioning* a forbidden command — PR bodies, commit messages,
# markdown backticks — does not trip them.
#
# **那句話對「錨定型」的規則成立，對合併那條計數規則不成立**：它數的是整串，
# 不看位置。下一個人照上面那句推論會得到錯的結論，所以這裡講明白。誤傷的那
# 一半由 `strip_quoted_heredocs` 處理 —— 文件走 heredoc 或 `--body-file` 就
# 不會被當成指令；直接內嵌在 `--body "…"` 裡的字串仍然會被數到。
P='(^|&&|\|\||[;|]|\$\()[[:space:]]*'

if printf '%s\n' "$cmd" | grep -qE "${P}git[[:space:]]+add[[:space:]]+([^;|&]*[[:space:]])?(-A|--all)([[:space:]]|;|\)|$)" \
   || printf '%s\n' "$cmd" | grep -qE "${P}git[[:space:]]+add[[:space:]]+\.([[:space:]]|;|\)|$)"; then
  deny "Blocked: bulk 'git add'. The staging area is a boundary the user draws — never stage everything on their behalf. Stage specific files the user asked for, or report what is unstaged and ask."
fi

if printf '%s\n' "$cmd" | grep -qE "${P}git[[:space:]]+push[^;|&]*[[:space:]](-f|--force)"; then
  deny "Blocked: force push. A rejected push means the remote has history you have not seen. Report the situation; forcing is the user's call, typed by the user."
fi

# 'git reset --hard': only the exact form 'git reset --hard HEAD' passes — it
# drops uncommitted changes without moving the branch pointer (the /refactor
# retreat). Any other target (bare, HEAD~1, a sha) rewrites history → blocked.
if printf '%s\n' "$cmd" | grep -qE "${P}git[[:space:]]+reset[^;|&]*--hard"; then
  if printf '%s\n' "$cmd" | tr ';&|' '\n' | sed -E 's/^[[:space:]]*(\$\()?[[:space:]]*//' \
     | grep -E "^git[[:space:]]+reset([[:space:]]|$)" | grep -e '--hard' \
     | grep -qvE "^git[[:space:]]+reset[[:space:]]+--hard[[:space:]]+HEAD[[:space:]]*$"; then
    deny "Blocked: 'git reset --hard' with any target other than exactly HEAD discards commits irreversibly. Allowed form: 'git reset --hard HEAD' (drops uncommitted changes only, e.g. the /refactor retreat). Moving the branch pointer is the user's call."
  fi
fi

if printf '%s\n' "$cmd" | grep -qE "${P}git[[:space:]]+commit[^;|&]*[[:space:]](--no-verify|-n)([[:space:]]|;|\)|$)"; then
  deny "Blocked: commit with hooks bypassed. A failing hook is a signal to fix, not to mute. Investigate the failure; bypassing is the user's call."
fi

# Merging a PR lands code on a shared branch and can trigger deploys — the one
# button in the whole flow that nobody can un-press. 'gh pr merge' is the front
# door; a PUT to the REST merge endpoint is the same act through the back one.
#
# Quotes are transparent to the shell ('gh "pr" merge' runs gh pr merge) but
# opaque to a regex, and $( ) opens a command position mid-string. So the gh
# gates below match on a normalized copy: quotes removed, $( treated as a
# segment break (round-5 review finding). Normalization is for MATCHING
# only — the executed command is untouched.
cmdn=$(printf '%s\n' "$cmd" | tr -d "'\"")
ghsegs=$(printf '%s\n' "$cmdn" | tr ';&|' '\n' | sed -E 's/\$\(/\n/g' \
         | sed -E 's/^[[:space:]]+//')

# gh accepts global/positional flags ahead of the subcommand path ('gh -R x
# pr merge', 'gh pr -R x merge'), which dodges every pattern below — an
# unverified front door (round-4 review finding). Canonical order only: flags
# go after the subcommand. Flag-led 'gh … pr' forms are denied outright,
# fail-closed, whatever the subcommand — a read-only cross-repo call can put
# its -R after the subcommand instead. ('gh pr --help' is caught too; use
# 'gh help pr'.)
if printf '%s\n' "$ghsegs" | grep -E '^gh[[:space:]]+-' | grep -qE '[[:space:]]pr([[:space:]]|$)' \
   || printf '%s\n' "$ghsegs" | grep -qE '^gh[[:space:]]+pr[[:space:]]+-'; then
  deny "Blocked: 'gh' with flags ahead of the 'pr' subcommand path. Canonical form only — subcommand first, flags after it (e.g. 'gh pr view 5 -R owner/repo'). Flag-led forms can smuggle a merge past the gate, so they are denied outright."
fi

# The gate is conditional, not absolute: the user decided that, and the
# authorization is recorded on a ticket — record yours the same way.
#
# 2026-09-07: the 'ai-verified' label requirement is OFF, and the gate routes by
# RISK TIER instead. The user's decision; their reasoning, in their words: at
# this stage the project iterates in vertical feature slices, and a label only a
# separate review agent can apply turns every merge into a two-session round
# trip — the gate stopped being a check and became a stall.
#
# The tiers come from 'Ai-coding-principle/GRANULARITY.md' (industry survey,
# 2026-09-06) and are written into 家規 第 39 條. The point of that document is
# one sentence: a system with no tiers can only treat everything by its
# strictest standard. Removing the gate outright would be the failure mode that
# same document names — "分級變成寬鬆的藉口, T1 一路擴張, 最後什麼都是 T1".
#
#   T1 例行 / T2 標準  -> this gate may merge when the evidence is green
#   T3 敏感 / T4 例外  -> always the user's button, no matter how green
#
# T3/T4 is decided by PATH, not by anyone's judgement of the diff, because
# judgement is what tiering is supposed to remove. The list is below in
# 'human_only_path'. 分不清的時候算高的那一級 (GRANULARITY 失效模式帳).
#
# What that costs, stated plainly so the next reader does not have to guess:
# inside T1/T2 there is now nothing between "the author reviewed their own work"
# and a merge — the label was that thing. A session can merge code it wrote
# itself. That is the user's accepted risk for this class, and the reason the
# T3/T4 list exists at all: it is where that risk is refused.
#
# What still holds for T1/T2, and is doing the work now:
#   1. every check on the PR is green ('gh pr checks' exits 0 — pending is
#      not green);
#   2. when GitHub Actions is dead (billing, quota — jobs reported failed with
#      zero steps executed), the fallback below measures the PR's exact head
#      locally with the local checks (human-only-paths.conf), from a tree whose guard matches main.
# Both verify evidence rather than a claim, which is why they stay.
#
# To put the label back: restore the 'case ",$labels," in' block and the
# head-sha comment check. They were removed in one commit, deliberately, so
# 'git revert' brings both back together — half of that pair is worse than
# neither: a label with nobody checking which commit it refers to reads as a
# guarantee while guaranteeing nothing.
if printf '%s\n' "$cmdn" | grep -qE "${P}gh[[:space:]]+pr[[:space:]]+merge([[:space:]]|;|\)|$)"; then
  # One merge per command, counted anywhere in the string — not just at
  # top-level segment starts. A second invocation nested in $( ), backticks,
  # or a --body "$( )" substitution executes before or beside the verified
  # one and would ride along un-checked (round-2 finding). Counting runs on
  # the quote-stripped copy so 'gh "pr" merge' splices count too (round 5).
  if [ "$(printf '%s\n' "$cmdn" | grep -oE 'gh[[:space:]]+pr[[:space:]]+merge' | grep -c .)" -gt 1 ]; then
    deny "Blocked: 'gh pr merge' appears more than once in this command (nesting counts). One merge per command, at top level — the gate verifies exactly the PR it can see."
  fi
  # Read the number from the actual 'gh pr merge' segment, never from other
  # text on the line — extracting the first 'merge <n>' anywhere is fail-open
  # (a decoy 'true merge 160' would verify 160 and merge something else).
  seg=$(printf '%s\n' "$ghsegs" \
        | grep -E '^gh[[:space:]]+pr[[:space:]]+merge([[:space:]]|$)' | head -1)
  # The merge must target this repository — with --repo/-R the hook verifies
  # PR <n> here while gh merges PR <n> somewhere else (confused deputy,
  # round-2 finding).
  # No suffix requirement after -R/--repo: gh accepts the stuck form
  # '-Rowner/repo' too (round-3 finding). Lowercase -r is rebase — not matched.
  if printf '%s\n' "$seg" | grep -qE '[[:space:]](--repo|-R)'; then
    deny "Blocked: 'gh pr merge' with --repo/-R. The gate verifies PRs in this repository only; a merge aimed at another repo cannot be verified here."
  fi
  # 合併方式只准 --squash 或 --rebase，不准合併鍵（家規第 27 條）。
  # **放在讀 PR 號之前**：這條看的是指令怎麼寫，不看那顆 PR 是什麼，先問便宜。
  #
  # 擋掉的是合併鍵。用它合的話，工作分支上那些「把 main 灌回來」的 merge 會
  # 原樣落進 main，歷史長出一堆菱形 —— 而菱形讓「這顆 PR 到底改了什麼」變難讀：
  # 審查的人得先分辨哪些行是作者寫的、哪些是併進來的。前一個專案的 main 上
  # 就留著兩個菱形，來源正是那兩顆用了合併鍵的 PR。
  #
  # `--rebase` 2026-09-07 開放，條件是**這顆 PR 的每一顆 commit 各自都通過檢查**
  # ——那時 bisect 與 revert 的粒度是收益不是負擔（例如分三步的重構：先加新路徑、
  # 再搬呼叫端、最後刪舊路徑，三顆各自可回退）。
  #
  # **這個條件閘門驗不了。** 它沒有辦法對每一顆 commit 各跑一次檢查，所以選
  # `--rebase` 的人是在**宣稱**那件事成立。寫出來是刻意的：一個閘門說不出自己
  # 在保證什麼的時候，讀的人會以為它保證了全部。
  if ! printf '%s
' "$seg" | grep -qE '[[:space:]]--(squash|rebase)([[:space:]]|$)'; then
    deny "Blocked: 'gh pr merge' 只准 --squash 或 --rebase（家規第 27 條）。合併鍵會把工作分支上那些把 main 灌回來的 merge 原樣帶進 main，歷史長出菱形，而菱形讓「這顆 PR 到底改了什麼」變難讀。預設用 --squash；每一顆 commit 各自都通過檢查時才用 --rebase，那個條件閘門驗不了，選它就是你在宣稱它成立。"
  fi
  pr=$(printf '%s\n' "$seg" \
       | grep -oE '^gh[[:space:]]+pr[[:space:]]+merge[[:space:]]+[0-9]+' | grep -oE '[0-9]+$')
  if [ -z "$pr" ]; then
    deny "Blocked: 'gh pr merge' without an explicit PR number right after 'merge'. The gate verifies one specific PR — name it, e.g. 'gh pr merge 123 --squash'."
  fi

  # `--auto` 一律擋。
  #
  # 延遲合併驗不了**合併當下**的 head：閘門在按下去那一刻讀檢查、讀路徑分級，
  # 而 `--auto` 是「條件滿足時再合」——中間可以推新的 commit，合進去的是
  # 一份沒有人驗過的 diff，而且**畫面上不會有任何錯誤**，GitHub 照樣顯示
  # 合併成功。
  #
  # 這條沒有正當用途可以權衡：私有 repo 免費方案本來就沒有 branch protection
  # （探路時 API 回 HTTP 403），所以 `--auto` 等的那個「條件」根本不存在。
  # 有 branch protection 的方案也一樣擋：閘門驗不了按下去之後才推的 commit。
  if printf '%s
' "$seg" | grep -qE '[[:space:]]--auto([[:space:]=]|$)'; then
    deny "Blocked: 'gh pr merge --auto' 延遲到條件滿足才合，而閘門驗的是**按下去那一刻**的 head 與路徑分級。中間推上去的 commit 沒有人驗過，合進去也不會有任何錯誤訊息。用即時形式：gh pr merge <n> --squash。"
  fi
  # 'gh' needs repository context. Outside the project tree it has none —
  # and that must still be caught here, before anything below reads the PR:
  # a gate that cannot see the PR has to say so, not fall through quietly
  # 「讀不到」與「讀到了，沒問題」是兩件事。
  if ! gh pr view "$pr" --json number >/tmp/gh-err.$$ 2>&1; then
    why=$(tr -d '
' < "/tmp/gh-err.$$" | head -2); rm -f "/tmp/gh-err.$$"
    deny "Blocked: the gate could not read PR #$pr, so nothing about it can be verified. gh said: ${why:-（沒有輸出）}. If gh cannot find a repository, you are outside the project tree — run from inside it; a worktree under '.claude/worktrees/' counts."
  fi
  rm -f "/tmp/gh-err.$$" 2>/dev/null
  # 風險分級路由（家規第 39 條）。**在讀檢查之前先問這一題** —— 一顆 T3 的 PR
  # 就算全綠也不該由這裡合，先跑完檢查再擋只是多花時間得到同一個答案。
  #
  # 讀不到檔案清單就擋：fail-closed。「讀不到」與「讀到了，沒有敏感路徑」
  # 是兩件事，混成同一種的話，API 出問題的那一天閘門會自己變成全開。
  if ! files=$(gh pr view "$pr" --json files --jq '.files[].path' 2>/dev/null)      || [ -z "$files" ]; then
    deny "Blocked: PR #$pr — the gate could not read which files it changes, so it cannot tell T1/T2 from T3/T4. Never merge on an unread diff."
  fi
  # 在父 shell 先讀一次 conf：t3_hits 跑在 $( ) 子 shell 裡，那邊讀的不會帶回來，
  # 讀不到時的警告也會印兩次。
  load_conf
  hits=$(t3_hits "$files")
  if [ -n "$hits" ]; then
    deny "Blocked: PR #$pr touches T3/T4 paths, so it is the user's button no matter how green the checks are:$hits
分級的出處是 Ai-coding-principle/GRANULARITY.md 與家規第 39 條。這一級不是「比較危險」而已 —— 它的驗證標準本身是人的品味，或者它決定了「什麼算通過」。回報 PR 網址與檢查狀態，然後停下來。"
  fi
  # 刪除一律回到人手上（家規第 28 條已經列了「任何刪除」）。新增與修改看得出
  # 意圖，刪除看不出 —— 少掉的東西不會出現在任何一份輸出裡。
  if ! removed=$(gh api "repos/{owner}/{repo}/pulls/$pr/files" --paginate        --jq '.[] | select(.status=="removed") | .filename' 2>/dev/null); then
    deny "Blocked: PR #$pr — the gate could not read file statuses, so it cannot tell whether anything was deleted. Never merge on an unread diff."
  fi
  if [ -n "$(printf '%s' "$removed" | tr -d '[:space:]')" ]; then
    deny "Blocked: PR #$pr deletes files, and deletions are the user's button (家規第 28 條):
$removed
新增與修改看得出意圖，刪除看不出——少掉的東西不會出現在任何一份輸出裡。"
  fi
  if ! gh pr checks "$pr" >/dev/null 2>&1; then
    # A non-zero 'gh pr checks' used to mean one thing: the code is bad. Since
    # 2026-09-06 it can also mean GitHub Actions itself cannot run — when
    # billing lapses or quota runs out, every job is reported failed within
    # seconds having executed zero steps. That red carries no information about
    # the code, and treating it as a failure locks every PR in the repo shut.
    #
    # So separate the two, by evidence:
    #   a job that RAN and failed (steps > 0)  -> real signal, still blocked
    #   a job with zero steps                  -> never scheduled, CI is down
    #
    # When CI is down the gate falls back to the local checks — the 'check:'
    # line of human-only-paths.conf, default 'pnpm test && pnpm typecheck &&
    # pnpm lint' — run against the PR's exact head commit. Anything
    # the hook cannot verify — a pending check, a non-Actions check, an API
    # call that fails — blocks, so the fallback never opens on ignorance.
    nwo=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)
    rows=$(gh pr checks "$pr" --json bucket,name,link 2>/dev/null)
    if [ -z "$nwo" ] || [ -z "$rows" ]; then
      deny "Blocked: PR #$pr is not green, and the hook could not read its checks to tell a real failure from a dead CI. Never merge on an unread check."
    fi
    # Pending and cancelled are not 'CI is down' — they are 'not finished'.
    if printf '%s' "$rows" | jq -e 'map(select(.bucket != "pass" and .bucket != "skipping" and .bucket != "fail")) | length > 0' >/dev/null 2>&1; then
      deny "Blocked: PR #$pr has pending or cancelled checks. The gate needs every check settled and green — wait for the run to finish."
    fi
    while IFS='|' read -r cname clink; do
      [ -z "$cname" ] && continue
      case "$clink" in
        "https://github.com/$nwo/actions/runs/"*"/job/"*) : ;;
        *) deny "Blocked: PR #$pr check '$cname' failed and is not a GitHub Actions job in this repository, so the hook cannot tell a dead CI from a real failure. Fix the check; never merge red." ;;
      esac
      jobid=${clink##*/}
      steps=$(gh api "repos/$nwo/actions/jobs/$jobid" --jq '.steps|length' 2>/dev/null)
      case "$steps" in
        ''|*[!0-9]*) deny "Blocked: PR #$pr — the hook could not read job $jobid to tell whether CI ran at all. Never merge on an unread check." ;;
      esac
      if [ "$steps" -gt 0 ]; then
        deny "Blocked: PR #$pr check '$cname' ran and failed ($steps steps executed). That is a real failure, not a dead CI — fix it; never merge red."
      fi
    done <<EOF
$(printf '%s' "$rows" | jq -r '.[] | select(.bucket=="fail") | "\(.name)|\(.link)"' | tr -d '\r')
EOF
    # CI never ran. Local evidence only counts when it describes this PR's
    # exact code: same commit, no uncommitted edits to tracked files.
    # Untracked files are deliberately tolerated — this repo always carries
    # scratch files, and demanding a pristine tree would make the fallback
    # unusable, which is how gates get routed around.
    # 前提：跑退路的工作目錄必須已經裝好相依（例：pnpm install）。相依沒裝的話
    # 檢查一定紅，而紅的原因不是這個 PR 的程式——新開的 worktree 要先裝再走這條路。
    root=$(git rev-parse --show-toplevel 2>/dev/null)
    # Which guard is running matters here and nowhere else. This path is the one
    # that says "trust the working tree instead of CI" — and the hook itself is
    # in that working tree, reached by the relative path settings.json launches.
    # So a PR that edits this file would be judged by its own edited copy: the
    # thing under review deciding what counts as evidence. Compare against main.
    # The side effect is deliberate — a PR touching the gate cannot use the
    # fallback, and that class needs a human anyway.
    #
    # What this catches and what it does not, stated plainly: it catches honest
    # drift — a branch that predates a gate change, someone who forgot to rebase.
    # It does NOT catch deliberate removal, because a self-hosted guard cannot
    # pin itself: delete these lines on the branch and there is nothing left to
    # run the check. It also passes when the local 'origin/main' is stale in the
    # same way the working copy is. Calling it a security boundary would be a
    # lie; it is a tripwire for the mistake that has actually happened here.
    #
    # The two shell contracts this depends on are documented on main_guard_blob.
    self_blob=$(git -C "${root:-.}" hash-object "$0" 2>/dev/null)
    main_blob=$(main_guard_blob "${root:-.}")
    if ! ok_blob "$self_blob" || ! ok_blob "$main_blob"; then
      deny "Blocked: CI is down, so the gate needs local evidence — but it could not read its own version, or main's, as a blob to compare them ('git rev-parse origin/main:.claude/hooks/guard-git.sh'). Fetch origin/main and try again; never fall back on a guard nobody has checked."
    fi
    if [ "$self_blob" != "$main_blob" ]; then
      deny "Blocked: CI is down, so the gate would fall back to local evidence — but the guard running right now is not the one on main. It was loaded from '$0' relative to $root, and this path only runs when that directory is the PR's own head, so the code under review would be setting its own bar. Merge this one by hand after a human reads the gate diff, or run the fallback from a tree whose guard matches main."
    fi
    head_sha=$(gh pr view "$pr" --json headRefOid --jq .headRefOid 2>/dev/null)
    local_sha=$(git -C "${root:-.}" rev-parse HEAD 2>/dev/null)
    if [ -z "$root" ] || [ -z "$head_sha" ] || [ "$head_sha" != "$local_sha" ]; then
      deny "Blocked: CI is down (jobs never started), so the gate needs local evidence — but the tree it measured is not PR #$pr's head. It measured $root, whose HEAD is $local_sha; the PR's head is $head_sha. That directory is where this hook was launched from — if it is the project root when you expected a worktree, the worktree is outside the project tree and the relative hook path could not resolve from it. Put the worktree under the project: 'git worktree add .claude/worktrees/<name> <pr-branch>' (that path is already git-ignored), then install dependencies there and merge from there. Do not 'gh pr checkout' here — another session may be working in this directory (L-006)."
    fi
    if [ -n "$(git -C "$root" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
      deny "Blocked: CI is down, so the gate needs local evidence — but tracked files are modified, so the local checks would describe code that is not in PR #$pr. Commit them, or run the gate from a clean worktree — never stash in a shared checkout, the stack is shared (L-006)."
    fi
    # 指令來自 conf 的 'check:' 行。conf 在 .claude/hooks/ 底下，改它的 PR 屬於
    # T3，在上面的分級就被擋掉，走不到這裡——所以這裡讀到的是沒被這顆 PR 動過的那一行。
    load_conf
    if ! out=$(run_local_checks "$root"); then
      deny "Blocked: CI is down (jobs never started), so the gate fell back to the local checks ($CONF_CHECK) — and they failed. Check first that this worktree has its dependencies installed — missing dependencies fail the checks without any code being broken. Otherwise fix what breaks; never merge red. Last output:
$(printf '%s' "$out" | tail -5)"
    fi
  fi
fi

if printf '%s\n' "$cmd" | grep -qE "${P}gh[[:space:]]+api[^;|&]*/merge([[:space:]]|'|\"|;|\)|$)"; then
  deny "Blocked: merging a PR through the REST API. Going around 'gh pr merge' does not change what it is — the merge button belongs to the user. Report the PR URL and stop."
fi

exit 0

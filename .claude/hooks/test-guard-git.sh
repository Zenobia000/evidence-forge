#!/usr/bin/env bash
# 對 guard-git.sh 的測試彈：模擬 PreToolUse JSON，比對期望 exit code。
#
# **這支有掛進 CI**（`.github/workflows/ci.yml` 的 check job）。合併閘要求 PR
# 的 checks 全綠才放行，而它自己的測試如果不在 checks 裡，就只在有人手動想到
# 時才跑——空包彈能活過兩輪審查，原因就是這個。
#
# 需要 gh 的只有幾發：放行案例 `gh pr merge $SAMPLE_PR --squash` 與它的變形。
# `SAMPLE_PR` 由環境變數給，要選一顆**已合併、只碰 T1／T2 的路徑、沒有刪除、
# 檢查全綠**的 PR（例：SAMPLE_PR=42 bash .claude/hooks/test-guard-git.sh）。
# 沒給、或 gh 不可用時，那幾發跳過並計入 skip，說出為什麼。
#
# 其餘帶號碼的（123、160、999999…）都是虛構的例子：它們在讀 PR 之前就被擋掉
# （跨 repo、旗標順序、合併方式、--auto），走不到分級這一關，所以號碼是誰不影響。
# 擋下來那一組刻意用不存在的號碼——擋的理由是「這顆 PR 根本讀不到」，而每一發
# 要證明的事是：誘餌、巢狀、引號拼接都不能把號碼換掉。
#
# 其餘每一發都 fail-closed，沒有 gh 照樣
# 得到期望的 exit 2。gh 不可用時那一發會被**跳過並計入 skip**，不是靜靜消失，
# 也不會誤報 FAIL。收尾那行三個數字都印出來，看的人才知道這次驗了多少。
#
# **已知邊界：沒有東西在守這支測試本身。** 收尾那行 `[ "$fail" -eq 0 ]` 是這支
# 腳本唯一決定退出碼的地方，而全 repo 只有 `ci.yml` 一處在跑它、沒有任何測試在
# 守它。改壞它的方式不只一種，這裡不列舉——**列舉本身永遠不完整，而且會讓下一
# 個人以為清單以外是安全的**。要記的是那個耐久的性質：
#
#   **最強的那種改法不留指紋。**
#
# 例如讓失敗被記成通過：`pass` 數字照常、沒有 `FAIL` 行、退出碼 0，整份輸出跟
# 乾淨跑逐位元組相同。看輸出看不出來，因為輸出正是被動手腳的那個東西。所以
# 「這次跑的結果看起來正常」不構成「這支腳本沒被動過」的證據——要那個結論得去
# 讀 diff，或有另一支東西在守它，而後者現在不存在。
#
# 守衛的守衛遞迴停在這一層是刻意的：再加一層仍然需要有人守那一層。**能做的是
# 把停在哪裡說準確**，而不是假裝停在更高的地方。

h="$(dirname "$0")/guard-git.sh"
pass=0; fail=0; skip=0
# gh 能不能用，先問一次。auth status 不打 API 也答得出來。
gh auth status >/dev/null 2>&1 && GH_OK=1 || GH_OK=0
# 放行案例的樣本 PR（見檔頭）。沒給就是空字串，那幾發會跳過。
SP=${SAMPLE_PR:-}
case "$SP" in *[!0-9]*) echo "SAMPLE_PR 要是數字，拿到 '$SP'——當作沒給"; SP="" ;; esac
# 不需要真 PR 的那幾發用的虛構號碼（例）。
N=123
# 標籤綁 commit 的比對函式單獨測。GUARD_GIT_LIB=1 讓 guard-git.sh 只
# 載入函式就停住，不去讀 hook payload。用完馬上 unset，否則下面 t() 起的子
# 行程會繼承到它。
GUARD_GIT_LIB=1; . "$h"; unset GUARD_GIT_LIB
u() { # $1=期望結果(0=算有提到) $2=head sha $3=留言文字 $4=說明
  if sha_named_in "$2" "$3"; then got=0; else got=1; fi
  if [ "$got" -eq "$1" ]; then pass=$((pass+1)); echo "ok   sha_named_in=$got [$4]"
  else fail=$((fail+1)); echo "FAIL sha_named_in=$got want=$1 [$4]"; fi
}

# T3 分級的檔案清單解析（2026-09-09）。
#
# 這一組存在的理由，是它上線前那一版**沒有任何一發碰得到**。分級當時是內嵌在
# 合併閘裡的 while 迴圈，只有 gh 讀得到真 PR 時才會執行，而 CI 的 runner 沒有 gh
# 認證——於是它在 CI 上永遠是 skip，在本機只有合併真 PR 時才跑一次。
# 它壞掉的那半年沒有任何一發會紅：**測不到的路徑不會出聲**（帳本 L-007）。
#
# 所以修法有兩半，缺一不可：把迴圈抽成 t3_hits（讓它測得到），以及修掉那個
# 清掉換行的 tr。只修後者的話，下一次有人重寫這段仍然沒有東西在守。
p() { # $1=期望(0=有命中 1=沒命中) $2=檔案清單(換行分隔) $3=說明
  _got_hits=$(t3_hits "$2")
  if [ -n "$_got_hits" ]; then got=0; else got=1; fi
  if [ "$got" -eq "$1" ]; then pass=$((pass+1)); echo "ok   t3_hits=[$_got_hits] [$3]"
  else fail=$((fail+1)); echo "FAIL t3_hits=[$_got_hits] want=$1 [$3]"; fi
}
# 舊版做得到的（排第一的萬用字元）——留著，證明修法沒有把原本會擋的弄丟
p 0 "$(printf '.claude/skills/x/SKILL.md\ndocs/a.md\n')" "萬用字元排第一：擋"
p 0 "$(printf 'Ai-coding-principle/LESSONS.md\n')"        "精確比對且唯一變更：擋"
# 舊版做不到的三發。每一發都對應一個真實情境，不是想像出來的排列組合。
p 0 "$(printf 'docs/a.md\n.claude/hooks/guard-git.sh\n')" "萬用字元排第二：擋（舊版放行）"
p 0 "$(printf 'Ai-coding-principle/PLAYBOOK.md\nAi-coding-principle/LESSONS.md\n')" \
    "精確比對排第二：擋（前一個專案真的有一顆從這裡溜過去）"
p 0 "$(printf 'README.md\nCLAUDE.md\n')" \
    "CLAUDE.md 跟別的檔一起改：擋（舊版放行——而它是「什麼算通過」的定義檔）"
# 反向：不該擋的不要擋。少了這一發，把 t3_hits 寫成「永遠回傳非空」也會全綠。
p 1 "$(printf 'docs/a.md\nsrc/ui/page.tsx\n')" "全都不是 T3：放行"
p 1 ""                                                "空清單：沒有命中（讀不到的情況由上層 fail-closed 擋）"

# ── 專案自己的路徑：human-only-paths.conf ─────────────────────────────────
# 範本的 conf 只有註解掉的範例（`#ex ` 開頭）。這裡把範例抽出來寫成一份暫存
# conf、讓守衛去讀它——驗的是**真的 conf 格式**被讀對了，不是另外編一套。
# 跟上面 CLAUDE.md 那三發同一個理由：只測「單獨出現」擋不住排序造成的漏判，
# 所以每一條範例都測「單獨出現」與「排在第二個」。
#
# **這一組只用暫存 conf，不拿專案自己那份當夾具。** 專案照 conf 的指示啟用一條
# 範例（例：拿掉 `#ex ` 讓 src/server/* 生效）是正常操作，測試不能因此變紅。
real_conf=$HUMAN_ONLY_CONF
tmpconf=$(mktemp)
examples=$(sed -n 's/^#ex //p' "$real_conf" 2>/dev/null)
ex_path() { printf '%s' "$1" | sed 's/^\*\//a\//; s/\*/x\/y.ts/g'; }  # glob → 一個該被擋的檔名
if [ -z "$examples" ]; then
  skip=$((skip+1)); echo "skip [conf 範例那組] —— human-only-paths.conf 沒有 '#ex ' 範例（專案刪掉了？），conf 格式改用下面的固定案例驗"
else
  printf '%s\n' "$examples" > "$tmpconf"
  HUMAN_ONLY_CONF=$tmpconf
  while IFS= read -r g; do
    [ -z "$g" ] && continue
    f=$(ex_path "$g")
    p 0 "$(printf '%s\n' "$f")"              "conf 範例 [$g] 單獨出現：擋（$f）"
    p 0 "$(printf 'docs/a.md\n%s\n' "$f")"  "conf 範例 [$g] 排在第二個：擋"
  done <<EOF
$examples
EOF
  # 反向：範例還是 `#ex ` 註解的時候不生效。
  sed -n 's/^#ex /#ex /p' "$real_conf" > "$tmpconf"
  HUMAN_ONLY_CONF=$tmpconf
  g=$(printf '%s\n' "$examples" | head -1)
  p 1 "$(printf '%s\n' "$(ex_path "$g")")" "範例還是 '#ex ' 註解：不生效"
fi

# conf 的寫法：人會照直覺寫，每一種直覺寫法都要比得中（守衛那邊 _norm_glob 的理由）。
printf '%s\r\n' $'\xef\xbb\xbfbom/first/*' 'src/dir-only' 'src/trailing/' './dot/*' '/abs/*' \
  '"quoted/*"' 'commented/* # 行尾註解' 'win\path\*' '# 註解行不生效' '' '   ' > "$tmpconf"
HUMAN_ONLY_CONF=$tmpconf
for f in bom/first/a.ts src/dir-only src/dir-only/a.ts src/trailing/a.ts dot/a.ts abs/a.ts quoted/a.ts commented/a.ts win/path/a.ts; do
  p 0 "$(printf 'docs/a.md\n%s\n' "$f")" "conf 寫法正規化後比得中：$f"
done
p 1 "$(printf 'src/ui/App.tsx\n')" "沒列在 conf 的畫面程式：放行——不能連帶誤擋"
p 1 "$(printf 'src/dir-onlyx/a.ts\n')" "目錄寫法不會吃到名字相近的鄰居"
p 1 "$(printf '# 註解行不生效\n')" "conf 的註解行不會變成樣式"

# check: 行
cc() { # $1=conf 內容 $2=期望的 CONF_CHECK $3=說明
  printf '%s\n' "$1" > "$tmpconf"; HUMAN_ONLY_CONF=$tmpconf; _conf_from=; load_conf
  if [ "$CONF_CHECK" = "$2" ]; then pass=$((pass+1)); echo "ok   [check: $3]"
  else fail=$((fail+1)); echo "FAIL [check: $3] 讀成 '$CONF_CHECK'"; fi
}
cc 'check:   echo 自訂檢查  ' 'echo 自訂檢查' "讀得到（前後空白已去掉）"
cc 'check: # 之後再填' 'pnpm test && pnpm typecheck && pnpm lint' "只有註解：當沒寫，用預設（不能讓 bash -c 空跑放行）"
cc 'check:' 'pnpm test && pnpm typecheck && pnpm lint' "空白：用預設"
cc 'src/x/*' 'pnpm test && pnpm typecheck && pnpm lint' "沒寫 check: 行：用預設"

# 退路真的跑的是 conf 那一行（run_local_checks）。審查實測過兩種改壞法——換成預設值、
# 漏掉 load_conf——原本都不會讓任何一發紅。這兩發釘住「跑的是 conf 那行，而且它的失敗會傳回來」。
rdir=$(mktemp -d)
printf '%s\n' 'check: echo 跑的是conf那行; exit 3' > "$tmpconf"; HUMAN_ONLY_CONF=$tmpconf; _conf_from=; unset CONF_CHECK
rout=$(run_local_checks "$rdir"); rrc=$?
if [ "$rrc" -ne 0 ] && case "$rout" in *跑的是conf那行*) true ;; *) false ;; esac; then
  pass=$((pass+1)); echo "ok   [退路跑 conf 的 check:，失敗會傳回來]"
else fail=$((fail+1)); echo "FAIL [退路沒跑 conf 的 check: 或吞掉失敗] rc=$rrc out=$(printf '%s' "$rout" | head -c 60)"; fi
printf '%s\n' 'check: true' > "$tmpconf"; _conf_from=
if run_local_checks "$rdir" >/dev/null; then pass=$((pass+1)); echo "ok   [退路：conf 的 check: 綠就是綠]"
else fail=$((fail+1)); echo "FAIL [退路：check: true 卻回失敗]"; fi
# CONF_CHECK 被清成空字串（例：在子 shell 讀的 conf 沒帶回來）時，不能 `bash -c ""` 空跑放行。
_conf_from=$tmpconf; CONF_CHECK=""
rout=$(run_local_checks "$rdir"); rrc=$?
if [ "$rrc" -ne 0 ]; then pass=$((pass+1)); echo "ok   [CONF_CHECK 是空的：改跑預設（這個空目錄裡會紅），不是空跑放行]"
else fail=$((fail+1)); echo "FAIL [CONF_CHECK 是空的卻回 0——退路會一個檢查都沒跑就放行]"; fi
rm -rf "$rdir"

# conf 是專案那一份時：改它本身是 T3。
HUMAN_ONLY_CONF=$real_conf; _conf_from=
p 0 "$(printf 'docs/a.md\n.claude/hooks/human-only-paths.conf\n')" "改 conf 本身：擋（它在 .claude/hooks/ 底下）"
p 0 "$(printf 'docs/a.md\n.claude/settings.json\n')" "改 settings.json：擋（守衛是靠它掛上去的）"
p 0 "$(printf 'docs/a.md\n.claude/agents/builder-sonnet-high.md\n')" "改子代理角色：擋"
# 讀不到 conf：出聲，而且通用區塊照擋、本機檢查退回預設值。
HUMAN_ONLY_CONF=/nonexistent/human-only-paths.conf; _conf_from=
warn=$(load_conf 2>&1 >/dev/null)
case "$warn" in
  *"讀不到"*) pass=$((pass+1)); echo "ok   [讀不到 conf 會出聲]" ;;
  *) fail=$((fail+1)); echo "FAIL [讀不到 conf 卻沒出聲] got: $warn" ;;
esac
p 0 "$(printf 'docs/a.md\nCLAUDE.md\n')" "讀不到 conf：通用區塊照擋" 2>/dev/null
load_conf 2>/dev/null
if [ "$CONF_CHECK" = "pnpm test && pnpm typecheck && pnpm lint" ]; then pass=$((pass+1)); echo "ok   [讀不到 conf：本機檢查退回預設值]"
else fail=$((fail+1)); echo "FAIL [讀不到 conf 時 check 是 '$CONF_CHECK']"; fi
HUMAN_ONLY_CONF=$real_conf; _conf_from=
rm -f "$tmpconf"
# CRLF：原本那個 tr 想做的事是清行尾的 CR。改成逐行的參數展開之後它還要成立，
# 否則這個修法就是拿一個洞換另一個洞。
p 0 "$(printf 'docs/a.md\r\nCLAUDE.md\r\n')" "行尾帶 CR：照樣擋"
t() { # $1=期望exit $2=指令（jq -Rs 封裝，引號安全）
  printf '%s' "$2" | jq -Rs '{tool_input:{command:.}}' | bash "$h" >/dev/null 2>&1
  got=$?
  if [ "$got" -eq "$1" ]; then pass=$((pass+1)); echo "ok   exit=$got [$2]"
  else fail=$((fail+1)); echo "FAIL exit=$got want=$1 [$2]"; fi
}
# 擋下來了**還要問是被哪一條擋的**。
#
# 只看退出碼的話，一發指令會因為別條規則先擋而「通過」，而那支測試看起來是綠的
# ——實測過：把 --squash 或 --auto 那條規則換成 `if false`，那幾發全部照樣綠，因為 CI 停擺時
# 釘版那關在更後面把它們擋掉了。**測試釘在自己身上，不是釘在程式上**（帳本 L-008）。
#
# $3 是理由裡必須出現的一段字。挑的字要屬於那條規則本身，不要挑通用詞。
tm() { # $1=期望exit $2=指令 $3=理由裡要出現的字 $4=說明
  msg=$(printf '%s' "$2" | jq -Rs '{tool_input:{command:.}}' | bash "$h" 2>&1 >/dev/null)
  got=$?
  case "$msg" in
    *"$3"*) hit=1 ;;
    *) hit=0 ;;
  esac
  if [ "$got" -eq "$1" ] && [ "$hit" -eq 1 ]; then
    pass=$((pass+1)); echo "ok   exit=$got [$4]"
  else
    fail=$((fail+1)); echo "FAIL exit=$got want=$1 hit=$hit [$4] got: $(printf '%s' "$msg" | head -c 70)"
  fi
}
# 繞過修補驗證（號碼必須抽自真正的 gh pr merge 段；一指令一發 merge）
t 2 "true merge 160 && gh pr merge 999999 --merge"
t 2 "gh pr merge $N --merge && gh pr merge 999999 --merge"
# 第二輪繞過修補（嵌套計數＋--repo 混淆代理人；單引號防測試框架先展開）
t 2 'gh pr merge 123 --merge --body $(gh pr merge 999999 --merge)'
t 2 'echo --body $(gh pr merge 999999 --merge)'
t 2 'gh pr merge 123 --repo other/repo'
t 2 'gh pr merge 123 -R other/repo'
t 2 'gh pr merge 123 --merge --repo=other/repo'
# 第三輪：黏附式短旗標
t 2 'gh pr merge 123 -Rowner/other-repo --merge'
# 第四輪：旗標前置——gh 與 pr、pr 與 merge 之間夾旗標的免驗形式
t 2 'gh -Rowner/other-repo pr merge 160'
t 2 'gh -R other/repo pr merge 160 --merge'
t 2 'gh pr -R other/repo merge 160'
# 旗標後置的唯讀跨 repo 用法不誤傷
t 0 'gh pr view 161 -Rowner/other-repo --json number'
# 第五輪：引號正規化＋$( 段落開口＋數字開頭帳號
t 2 'gh pr -R "other/repo" merge 160'
t 2 "gh pr -R 'other/repo' merge 160"
t 2 'gh "-R" other/repo pr merge 160'
t 2 'gh pr -R 0wner/repo merge 160'
t 2 'echo x $(gh -R other/repo pr merge 160)'
t 2 'echo x $(gh pr -R other/repo merge 160)'
t 2 'gh "pr" merge 999999 --merge'
# 條件閘門
# 需要 gh 的一發：它要真的去讀樣本 PR（SAMPLE_PR）的檔案清單與檢查狀態才知道該放行。
#
# 期望值分兩邊，理由跟下面釘版那一發相同：CI 掛著的時候閘門會走本機退路，
# 而退路的第一關是「跑這支守衛的樹，守衛是不是 main 那一份」。在改閘門的
# 分支上它本來就該擋——寫死期望放行的話，**每一次動閘門這支測試都會紅，
# 而紅的原因跟改對改錯無關**，那種紅最後只會教人忽略它。
#
# 第三種情況：**CI 停擺的時候這一發驗不了。**
#
# 樣本 PR 自己的 checks 紅著的話，閘門會正確地走本機退路，而退路要求
# 「跑測試的那棵樹的 HEAD 等於這顆 PR 的 head」—— 跑測試的時候那永遠不成立。
# 於是它擋下來，而擋的理由跟這一發要證明的事（T1／T2 全綠就放行）無關。
#
# **不把期望改成擋。** 改成擋是會綠，但那在驗拒絕路徑，而拒絕路徑上面十幾發
# 已經涵蓋了 —— 真正的放行路徑會靜靜地沒有被驗到，而輸出看起來是全綠的。
# 那正是空包彈的形狀。跳過並說出為什麼，才看得出這次少驗了什麼。
if [ -z "$SP" ]; then
  skip=$((skip+1)); echo "skip [gh pr merge <SAMPLE_PR> --squash] —— 沒給 SAMPLE_PR（一顆已合併、只碰 T1／T2、檢查全綠的 PR 號），放行案例驗不了"
elif [ "$GH_OK" -ne 1 ]; then
  skip=$((skip+1)); echo "skip [gh pr merge $SP --squash] —— gh 不可用，唯一的放行案例驗不了"
elif ! gh pr checks "$SP" >/dev/null 2>&1; then
  skip=$((skip+1))
  echo "skip [gh pr merge $SP --squash] —— 樣本 PR #$SP 的 checks 不是全綠（CI 停擺？）。"
  echo "     閘門會走本機退路並要求 HEAD 等於它的 head，那在測試裡永遠不成立 ——"
  echo "     擋下來的理由跟這一發要證明的事無關，所以這次沒有驗到放行路徑。"
else
  gexp=$(MSYS_NO_PATHCONV=1 git rev-parse --verify --quiet "origin/main:.claude/hooks/guard-git.sh" 2>/dev/null)
  if [ "$(git hash-object .claude/hooks/guard-git.sh 2>/dev/null)" = "$gexp" ]; then
    t 0 "gh pr merge $SP --squash"
  else
    t 2 "gh pr merge $SP --squash"
    echo "     （守衛≠main，所以這一發期望的是擋下來——閘門改動合進 main 之後會變回放行）"
  fi
fi
t 2 "gh pr merge 999999 --merge"
t 2 "gh pr merge --auto"
# ── 一律 squash（家規第 27 條）──────────────────────────────────────────
# 這條在讀 PR 號之前就問，所以不需要一顆真的 PR 也驗得到。
tm 2 "gh pr merge $N --merge" "--squash" "合併鍵形式要擋"
tm 2 "gh pr merge $N" "--squash" "什麼都不帶也要擋"
# --rebase 2026-09-07 開放（每一顆 commit 各自通過檢查時用）。這一發驗的是
# 「它不再被合併方式那條擋掉」——擋它的理由要是別條，不能是這條。
# 「下一關」要讀樣本 PR，gh 不可用時讀不到，擋的理由會是「could not read PR」——跟這一發要證明的事無關，
# 照上面放行案例的做法跳過並說出來，不改期望值。
#
# 樣本 PR 的 CI 停擺時，放行之後會落到本機退路，理由是「CI is down」；樣本 PR
# 全綠時，閘門會直接往下走。兩種都只驗一件事：**擋它的不是合併方式那條**。
if [ -z "$SP" ] || [ "$GH_OK" -ne 1 ]; then
  skip=$((skip+1)); echo "skip [rebase 形式放行到下一關] —— 沒給 SAMPLE_PR 或 gh 不可用，讀不到樣本 PR"
else
  msg=$(printf '%s' "gh pr merge $SP --rebase" | jq -Rs '{tool_input:{command:.}}' | bash "$h" 2>&1 >/dev/null)
  case "$msg" in
    *"只准 --squash 或 --rebase"*) fail=$((fail+1)); echo "FAIL [rebase 形式被合併方式那條擋了]" ;;
    *) pass=$((pass+1)); echo "ok   [rebase 形式放行到下一關]" ;;
  esac
fi
# 只是提到它，不是在下這個指令。
t 0 "echo 合併用 gh pr merge --squash 或 --rebase"

# ── --auto 一律擋 ──────────────────────────────────────────────────────
# 上面那一發是「沒帶號碼」被擋的，擋的不是 --auto 本身。帶了號碼才驗得到這條
# 規則；--auto 在讀 PR 之前就問，所以虛構號碼就夠，不需要真 PR。
# 每一發都帶 --squash：不帶的話會先被那條規則擋掉，驗到的不是 --auto
# （這正是 tm() 存在的理由）。
tm 2 "gh pr merge $N --auto --squash" "--auto" "--auto 在前"
tm 2 "gh pr merge $N --squash --auto" "--auto" "--auto 在後"
# 黏附形式：gh 不吃 --auto=true，但守衛不必替 gh 判斷語法，多擋不會錯。
tm 2 "gh pr merge $N --auto=true --squash" "--auto" "--auto=true"
# 只是提到它、不是在下這個指令，不該誤傷。
t 0 "echo 不要用 --auto，那個驗不了合併當下的 head"
t 2 "gh pr merge --merge 160"
# CI 掛掉的退路（2026-09-06 加）沒有辦法在這裡下 fixture：要湊出「帶
# ai-verified、而且 job steps=0」的 PR，得真的去貼標籤，那是審查代理的事。
# 這條路當時是這樣驗的——把 guard-git.sh 複製到暫存區，用 sed 把標籤閘換成
# 放行，再對真實 PR 跑，四個分支各驗一次：
#   steps=0（CI 沒起跑）      -> 往下走本機證據
#   steps>0（真的跑了才紅）   -> 仍然擋，訊息說「ran and failed」
#   HEAD 不等於 PR head      -> 擋，叫人另開對齊的 worktree（訊息明文禁止在
#                               共用目錄下 gh pr checkout，L-006）
#   tracked 檔案有改動        -> 擋，叫人 commit，或改從乾淨的 worktree 跑；
#                               共用 checkout 裡絕對不 stash——stash stack 是
#                               共用的（L-006），訊息也是這樣寫的
#   以上都過、本機檢查全綠    -> exit 0
# 重驗前記得那個 worktree 要先 pnpm install，不然第四關會紅在缺相依上。
# 要重驗就照這個做法，不要為了測試去動真的 PR 標籤。
# 散文提及不誤傷
t 0 "echo docs mention gh pr merge 152"

# ── 引號 heredoc 裡的文件內容不算指令 ──────────────────────────────────────
#
# **先擺攻擊，再擺放行。**當初的票寫明順序：先把「原本要擋的攻擊」列成測試再改
# 規則，不然很容易補了誤傷、開了破口。
#
# 攻擊面沒有變動的那幾發（$( ) 藏第二發、| 串第二發）本來就在上面驗過了；
# 這裡補的是「攻擊躲進 heredoc」的變形。

# 不帶引號的 heredoc 會展開 $( )，所以裡面的東西**真的會執行**——不准剝。
t 2 "$(printf 'gh pr comment 1 --body \"$(cat <<EOF
$(gh pr merge 2 --merge)
EOF
)\"')"

# heredoc 開了沒收尾：shell 這時是語法錯誤，什麼都不會執行，但守衛寧可多看。
# 原樣退回不剝，所以裡面那一發照樣被數到。
t 2 "$(printf 'gh pr merge 3 --merge <<%sEOF%s
gh pr merge 4 --merge
' "'" "'")"

# 引號 heredoc 的內容不執行，所以裡面提幾次合併都不算指令。
# 這是這張票真正要放行的形狀：用 heredoc 寫一份談論合併閘的文件。
t 0 "$(printf 'gh pr comment 5 --body \"$(cat <<%sEOF%s
gh pr merge 1 --merge 跟 gh pr merge 2 --merge 是兩回事。
EOF
)\"' "'" "'")"

# markdown 表格：欄位分隔線長得跟 shell 管線一模一樣，這正是最容易誤傷的形狀。
t 0 "$(printf 'gh pr comment 6 --body \"$(cat <<%sEOF%s
| 指令 | 說明 |
| --- | --- |
| gh pr merge 1 | 合第一顆 |
| gh pr merge 2 | 合第二顆 |
EOF
)\"' "'" "'")"

# 剝掉 heredoc 之後，**外面**那一發還是要被看到——不能因為裡面有文件就整串放行。
t 2 "$(printf 'git push -f origin main <<%sEOF%s
gh pr merge 1 --merge
EOF
' "'" "'")"

# 原規則回歸
t 2 "git push -f origin main"
t 2 "gh api -X PUT repos/o/r/pulls/1/merge"
t 2 "git add -A"
t 2 "git reset --hard HEAD~1"
t 0 "git reset --hard HEAD"
t 2 "git commit --no-verify -m x"
t 0 "git status"
# 標籤綁 commit
S=826c833264e14f42f63b0dbade77f294a999c902
u 0 "$S" 'PASS，驗的是 `826c833`。'                      "短 sha 加反引號"
u 0 "$S" "reviewed $S end to end"                        "留言寫完整 40 碼"
u 0 "$S" 'verified 826C833'                              "大小寫不同照樣算"
u 0 "$S" '增量審查 `e544489..826c833`：PASS'             "寫成 range 的尾端"
u 1 "$S" '驗的是 `826c83`'                               "只有 6 碼不算"
u 1 "$S" '驗的是 `e544489`'                              "指的是上一版"
u 1 "$S" 'run 1826c833264e14f42f63b0dbade77f294a999c902' "夾在別的 sha 中間不算"
u 1 "$S" ''                                              "完全沒有留言"
u 1 "abc"  'abc'                                         "head 讀不出完整 sha 就不算"
# 「貼標籤 → 再 push → 閘門要擋」沒辦法在這裡下 fixture：要湊出這個狀態得去
# 動真實 PR 的標籤，而那是審查代理的職權。這條路當時是這樣驗的——把
# guard-git.sh 複製到暫存區，用 sed 把標籤閘換成放行，對真實 PR 跑：
#   某顆 PR（留言有寫 826c833，head 也是它）     -> 通過 sha 閘
#   同一顆但把比對目標換成前一版 e544489        -> 擋，訊息說 label 指的是舊版
# 要重驗就照這個做法，不要為了測試去動真的 PR 標籤。
# 退路的執行環境。這幾發要用絕對路徑起 hook，因為它們刻意從別的
# 目錄呼叫——而「從哪裡呼叫」正是某顆 PR 六輪審查全部漏掉的那一層（帳本
# L-010）。上一版這裡有兩發空包彈：一發 cd 到空字串（TMPDIR 在這台機器
# 是空的，`cd ""` 成功且不換目錄），一發 grep 一句當時任何輸入都會走到的
# 訊息。⚠ 那兩發是為了修 L-010 而加的，它們自己就是 L-010。
habs="$(cd "$(dirname "$0")" && pwd)/guard-git.sh"
outside=$(mktemp -d)
# 從專案樹外面呼叫：gh 沒有 repo context，什麼都驗不了，一律擋——而且理由
# 要說「讀不到這顆 PR」，不是「這顆 PR 沒有標籤」。後者是它不知道的事。
#
# 指令要帶 --squash：不帶的話會先被那條規則擋掉，驗不到這一關（2026-09-07）。
# 這種「被更前面的規則擋掉，測試照樣綠」正是 tm() 存在的理由。
msg=$( cd "$outside" && printf '{"tool_input":{"command":"gh pr merge 185 --squash"}}' | bash "$habs" 2>&1 >/dev/null; )
rc=$( cd "$outside" && printf '{"tool_input":{"command":"gh pr merge 185 --squash"}}' | bash "$habs" >/dev/null 2>&1; echo $? )
if [ "$rc" -eq 2 ]; then pass=$((pass+1)); echo "ok   exit=2 [從專案樹外呼叫一律擋]"
else fail=$((fail+1)); echo "FAIL exit=$rc want=2 [從專案樹外呼叫一律擋]"; fi
# 理由要說「讀不到這顆 PR」並帶上 gh 自己說的話，不能講成「這顆 PR 沒有標籤」
# ——後者是它不知道的事。
case "$msg" in
  *"could not read PR"*"gh said:"*)
    case "$msg" in
      *"no ai-verified label"*) fail=$((fail+1)); echo "FAIL [讀不到卻講成沒有標籤]" ;;
      *) pass=$((pass+1)); echo "ok   [理由說的是讀不到，而且引用了 gh 的原話]" ;;
    esac ;;
  *) fail=$((fail+1)); echo "FAIL [理由不對] got: $(printf '%s' "$msg" | head -c 90)" ;;
esac
rmdir "$outside" 2>/dev/null
# ok_blob：釘住「rev-parse 解不開時會把參數印回 stdout」這個坑
GUARD_GIT_LIB=1; . "$habs"; unset GUARD_GIT_LIB
b() { if ok_blob "$2"; then got=0; else got=1; fi
  if [ "$got" -eq "$1" ]; then pass=$((pass+1)); echo "ok   ok_blob=$got [$3]"
  else fail=$((fail+1)); echo "FAIL ok_blob=$got want=$1 [$3]"; fi; }
b 0 "596eb6a213f2333d769255581ab05851bbaac7fe" "真的 blob sha"
b 1 "origin\main;.claude\hooks\guard-git.sh" "Git Bash 咬爛的路徑（rev-parse 會原樣印回來）"
b 1 "" "空字串"
b 1 "596eb6a213f2333d769255581ab05851bbaac7f" "39 碼"
b 1 "596eb6a213f2333d769255581ab05851bbaac7fez" "41 碼"
b 1 "596EB6A213F2333D769255581AB05851BBAAC7FE" "大寫"
# 長度對、內容不是 hex —— 這一發釘的是 ok_blob 的另一半。少了它，把
# ok_blob 改成只看長度也不會被抓到（審查實測過）。
b 1 "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ" "剛好 40 碼但不是 hex"
# 呼叫守衛裡**那一份** main_guard_blob，不是把它那行指令抄一份到這裡跑。
# 抄一份的話驗的是 git，不是這支守衛（帳本 L-008，上一輪就是這樣中的）。
groot=$(git -C "$(dirname "$habs")" rev-parse --show-toplevel 2>/dev/null)
# origin/main 不一定存在：淺 clone（CI 的 actions/checkout 預設只抓一顆
# commit）就沒有它。那時這兩發驗不了，要跳過並出聲，不能誤報 FAIL——也不能
# 靜靜跳過，那會讓全綠看起來比實際涵蓋的多（同一條道理）。
if MSYS_NO_PATHCONV=1 git -C "$groot" rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
  if ok_blob "$(main_guard_blob "$groot")"; then pass=$((pass+1)); echo "ok   [main_guard_blob 解得出 main 的 blob]"
  else fail=$((fail+1)); echo "FAIL [main_guard_blob 解不出 main 的 blob]"; fi
# 解不開的時候必須回**空字串**，不是把參數印回來。少了 --verify 就會回那串
# 參數，而「非空但不是 blob」會讓上游任何以空判斷的地方失效。
  bogus=$(main_guard_blob "$groot" "no-such-ref-for-tests")
  if [ -z "$bogus" ]; then pass=$((pass+1)); echo "ok   [解不開時回空字串，不是把參數印回來]"
  else fail=$((fail+1)); echo "FAIL [解不開時回了 '$bogus']"; fi
  # 走完整條路到**釘版判斷本身**。上面兩發驗的是零件（ok_blob、
  # main_guard_blob），而零件對不代表接線對：刪掉退路裡那八行呼叫、把
  # origin/main 改成 HEAD、把 != 改成 =，三種改壞法零件測試都照樣全綠。
  #
  # 走得到的前提是樣本 PR 已合併、只碰 T1／T2 的路徑、沒有刪除，而 CI 掛著，
  # 所以它會一路落到退路的第一道關卡。樣本 PR 檢查全綠時走不到這關，跳過。
  #
  # 前一個專案換過樣本：原本那顆改的
  # `.claude/skills/` 屬於 T3，會在更前面就被擋掉——**擋對了，只是擋在
  # 這條測試想驗的那一關之前**。樣本要選一顆走得到目標關卡的。斷言分兩邊，因為對的答案取決於這棵樹
  # 的守衛是不是 main 那一份——在 PR 分支上該擋，合併進 main 之後該放行，
  # 兩邊都要對，這條測試才不會在合併當天變紅。
  if [ -n "$SP" ] && [ "$GH_OK" -eq 1 ] && ! gh pr checks "$SP" >/dev/null 2>&1; then
    gmsg=$(printf '%s' "gh pr merge $SP --squash" | jq -Rs '{tool_input:{command:.}}' | bash "$habs" 2>&1 >/dev/null)
    # 這一行**刻意**不呼叫 main_guard_blob，而是自己去問一次。判斷該期望哪
    # 個答案是這條測試的裁判，裁判不能用被驗的那支函式算出來——用了的話，
    # 把 origin/main 改成 HEAD 這種改壞法會同時騙過守衛與裁判，兩邊一起偏
    # 移，測試照樣全綠（實測過）。這不是 L-008 說的「複製程式碼」，是裁判
    # 必須獨立於被驗的東西。
    expect_main=$(MSYS_NO_PATHCONV=1 git -C "$groot" rev-parse --verify --quiet "origin/main:.claude/hooks/guard-git.sh" 2>/dev/null)
    if [ "$(git -C "$groot" hash-object "$habs" 2>/dev/null)" = "$expect_main" ]; then
      case "$gmsg" in
        *"not the one on main"*) fail=$((fail+1)); echo "FAIL [守衛就是 main 那份，釘版不該擋]" ;;
        *) pass=$((pass+1)); echo "ok   [守衛＝main：釘版放行，走到下一關]" ;;
      esac
    else
      case "$gmsg" in
        *"not the one on main"*) pass=$((pass+1)); echo "ok   [守衛≠main：釘版擋下，而且是釘版擋的]" ;;
        *) fail=$((fail+1)); echo "FAIL [守衛≠main 卻沒被釘版擋] got: $(printf '%s' "$gmsg" | head -c 80)" ;;
      esac
    fi
  else
    skip=$((skip+1)); echo "skip [釘版判斷的整條路] —— 需要 SAMPLE_PR、gh，而且樣本 PR 的 CI 停擺（檢查全綠時走不到退路）"
  fi
else
  skip=$((skip+3)); echo "skip [main_guard_blob 兩發＋釘版整條路] —— 這棵樹沒有 origin/main（淺 clone？）"
fi
# ── 私人內容關卡（#126）：commit 與 push 之前掃指紋 ──────────────────────
#
# 這一組驗的是「真的擋得下」：在暫存的 git repo 裡暫存／提交含假名字的檔，餵真的 payload 給守衛，
# 看它 exit 2、說得出哪個檔哪一行、而且訊息裡沒有命中的字。**角色全是虛構的**（假名字、假品牌、假色票，
# 寫進暫存的 EF_DATA）——測試資料不准用真人的名字或真實的品牌內容（帳本 L-028）。
# 「沒有 EF_DATA 時固定指紋照跑」要有真的固定指紋才驗得到、而那些不能明文進測試，所以放在
# tests/unit/test_brand_guard.py（換一組假的固定指紋）；這裡只驗沒有 EF_DATA 時不壞、乾淨的照放行。
if command -v git >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  bgroot="$(cd "$(dirname "$h")/../.." && pwd)"
  hb="$bgroot/.claude/hooks/guard-git.sh"   # 在別的目錄跑，要用絕對路徑
  bt=$(mktemp -d)
  ef="$bt/ef"; repo="$bt/repo"; bare="$bt/remote.git"; noef="$bt/noef"
  mkdir -p "$ef/authors/quillon" "$noef" "$repo"
  printf '%s\n' '---' 'id: quillon' 'name: Quillon Tavernash' '---' > "$ef/authors/quillon/voice.md"
  printf '%s\n' 'name: Marbled Fox Atelier' 'palette:' '  paper: "#13579B"' > "$ef/authors/quillon/brand.yaml"
  # 每個 git 都關掉使用者的 hooksPath，測試才不會被那台機器的設定牽著走
  gq() { git -C "$repo" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false "$@"; }
  gq init -q -b main
  printf 'hello\n' > "$repo/README.txt"
  gq add README.txt && gq commit -q -m init
  git init -q --bare "$bare"
  gq remote add origin "$bare" && gq push -q origin HEAD

  # $1=期望exit $2=指令 $3=說明 $4=訊息裡必須出現的位置（空＝不問） $5=EF_DATA（預設假角色）
  # 擋下時再問：訊息指得出位置、而且不含任何一個假字
  tb() {
    _ef=${5-$ef}
    msg=$(cd "$repo" && printf '%s' "$2" | jq -Rs '{tool_input:{command:.}}' | EF_DATA="$_ef" bash "$hb" 2>&1 >/dev/null)
    got=$?
    ok=1
    [ "$got" -eq "$1" ] || ok=0
    if [ -n "${4-}" ]; then case "$msg" in *"$4"*) ;; *) ok=0 ;; esac; fi
    if printf '%s' "$msg" | grep -qiE 'quillon|tavernash|marbled|13579b'; then ok=0; fi
    if [ "$ok" -eq 1 ]; then pass=$((pass+1)); echo "ok   exit=$got [$3]"
    else fail=$((fail+1)); echo "FAIL exit=$got want=$1 [$3] got: $(printf '%s' "$msg" | head -c 120)"; fi
  }

  tb 0 "git commit -m x" "私人內容：暫存區是空的，放行"
  tb 0 "git status" "私人內容：不是 commit／push 的指令不碰"
  mkdir -p "$repo/tests"
  printf "name = 'Quillon Tavernash'\n" > "$repo/tests/test_a.py"
  gq add tests/test_a.py
  tb 2 "git commit -m x" "私人內容：暫存區有假名字，commit 擋下，只講檔案與行號" "tests/test_a.py:1"
  tb 2 "git commit -m 'a b c'" "私人內容：帶 -m 的 commit 一樣擋"
  tb 2 "git status && git commit -m x" "私人內容：接在別的指令後面的 commit 也擋"
  tb 0 "git commit -m x" "私人內容：EF_DATA 是空的（CI 的樣子）時不壞、也不亂擋（固定指紋那半在 pytest 驗）" "" "$noef"
  gq reset -q tests/test_a.py
  printf "accent = '#13579b'\n" > "$repo/tests/test_b.py"
  gq add tests/test_b.py
  tb 2 "git commit -m x" "私人內容：假色票也擋" "tests/test_b.py:1"
  gq reset -q tests/test_b.py
  rm -f "$repo/tests/test_a.py" "$repo/tests/test_b.py"

  # 暫存區現在是空的、但這條指令會把東西帶進去：commit -a、同一條指令才 add
  printf 'hello Quillon\n' > "$repo/README.txt"
  tb 0 "git commit -m x" "私人內容：沒暫存的髒檔不在這次 commit 裡，放行"
  tb 2 "git commit -am x" "私人內容：commit -a 會帶進追蹤中的檔，擋" "README.txt:1"
  tb 2 "git add README.txt && git commit -m x" "私人內容：同一條指令才 add，擋" "README.txt:1"
  gq checkout -q README.txt
  printf "x = 'Tavernash'\n" > "$repo/untracked.py"
  tb 2 "git add untracked.py && git commit -m x" "私人內容：同一條指令才 add 的未追蹤檔也擋" "untracked.py:1"
  rm -f "$repo/untracked.py"
  mkdir -p "$repo/docs"
  printf 'Quillon is the pen name used in this doc\n' > "$repo/docs/note.md"
  gq add docs/note.md
  tb 0 "git commit -m x" "私人內容：docs/ 底下不掃（文件裡本來就會提到公開的名字）"
  gq reset -q docs/note.md; rm -f "$repo/docs/note.md"

  # push：只看還沒有任何遠端有的 commit。洩漏的 commit 後面補一個刪掉它的 commit 也沒用——歷史上還在。
  tb 0 "git push origin HEAD" "私人內容：沒有新 commit，push 放行"
  printf "name = 'Quillon'\n" > "$repo/leak.py"
  gq add leak.py && gq commit -q -m leak
  leak_sha=$(gq rev-parse --short=8 HEAD)
  gq rm -q leak.py && gq commit -q -m remove
  tb 2 "git push origin HEAD" "私人內容：push 擋下有洩漏的 commit，而且指出是哪一個" "$leak_sha leak.py:1"
  tb 2 "git push -u origin main" "私人內容：push -u 一樣擋"
  tb 2 "git push" "私人內容：沒寫遠端與分支也擋（推目前分支）"
  tb 2 "git status && git push origin HEAD 2>&1 | tail -3" "私人內容：接在後面、帶管線的 push 也擋"
  tb 2 "git push -f origin HEAD" "私人內容：force push 本來就擋（舊規則先擋，這裡只確認不放行）"
  tb 0 "git push origin HEAD" "私人內容：EF_DATA 是空的時不壞（只剩固定指紋，不認得假角色）" "" "$noef"

  # git 原生 hook（.githooks/）：手動 commit／push 也擋。用同一支掃描，所以只確認接得上、退出碼對
  base=$(gq rev-parse HEAD~2); head=$(gq rev-parse HEAD)
  printf "name = 'Quillon'\n" > "$repo/leak2.py"; gq add leak2.py
  nh() { # $1=期望的退出碼（0 放行、1 擋；127 之類是跑不起來，不算擋） $2=說明 其餘=指令
    _w=$1; _d=$2; shift 2
    _m=$("$@" 2>&1); _r=$?
    if [ "$_r" -eq "$_w" ]; then
      if printf '%s' "$_m" | grep -qiE 'quillon|tavernash'; then fail=$((fail+1)); echo "FAIL rc=$_r [$_d] 輸出含假名字"
      else pass=$((pass+1)); echo "ok   rc=$_r [$_d]"; fi
    else fail=$((fail+1)); echo "FAIL rc=$_r [$_d] got: $(printf '%s' "$_m" | head -c 100)"; fi
  }
  nh 1 "原生 pre-commit：暫存區有假名字，擋" bash -c 'cd "$1" && EF_DATA="$2" bash "$3/.githooks/pre-commit"' _ "$repo" "$ef" "$bgroot"
  gq reset -q leak2.py; rm -f "$repo/leak2.py"
  nh 0 "原生 pre-commit：暫存區乾淨，放行" bash -c 'cd "$1" && EF_DATA="$2" bash "$3/.githooks/pre-commit"' _ "$repo" "$ef" "$bgroot"
  nh 1 "原生 pre-push：要推的 commit 有假名字，擋" bash -c 'cd "$1" && printf "refs/heads/main %s refs/heads/main %s\n" "$4" "$5" | EF_DATA="$2" bash "$3/.githooks/pre-push" origin url' _ "$repo" "$ef" "$bgroot" "$head" "$base"
  nh 0 "原生 pre-push：沒有新內容（遠端已經是這個 commit），放行" bash -c 'cd "$1" && printf "refs/heads/main %s refs/heads/main %s\n" "$4" "$4" | EF_DATA="$2" bash "$3/.githooks/pre-push" origin url' _ "$repo" "$ef" "$bgroot" "$head"

  # 掃不了就擋（fail-closed）：沒有 python3、找不到腳本、腳本壞掉（不在 git repo 裡）
  nopy="$bt/nopy"; mkdir -p "$nopy"
  for c in bash jq grep sed tr cat cut head awk dirname; do
    cp_src=$(command -v "$c" 2>/dev/null) && ln -s "$cp_src" "$nopy/$c"
  done
  pmsg=$(printf '%s' '{"tool_input":{"command":"git commit -m x"}}' | PATH="$nopy" "$nopy/bash" "$hb" 2>&1 >/dev/null); prc=$?
  if [ "$prc" -eq 2 ] && case "$pmsg" in *python3*) true ;; *) false ;; esac; then
    pass=$((pass+1)); echo "ok   exit=2 [私人內容：沒有 python3：出聲並擋，不靜靜放行]"
  else fail=$((fail+1)); echo "FAIL exit=$prc [私人內容：沒有 python3] got: $(printf '%s' "$pmsg" | head -c 80)"; fi
  mkdir -p "$bt/fake/.claude/hooks"; cp "$hb" "$bt/fake/.claude/hooks/guard-git.sh"
  fmsg=$(cd "$repo" && printf '%s' '{"tool_input":{"command":"git push origin HEAD"}}' | bash "$bt/fake/.claude/hooks/guard-git.sh" 2>&1 >/dev/null); frc=$?
  if [ "$frc" -eq 2 ] && case "$fmsg" in *brand_guard.py*) true ;; *) false ;; esac; then
    pass=$((pass+1)); echo "ok   exit=2 [私人內容：找不到腳本：出聲並擋]"
  else fail=$((fail+1)); echo "FAIL exit=$frc [私人內容：找不到腳本] got: $(printf '%s' "$fmsg" | head -c 80)"; fi
  mkdir -p "$bt/notrepo"
  nmsg=$(cd "$bt/notrepo" && printf '%s' '{"tool_input":{"command":"git commit -m x"}}' | EF_DATA="$ef" bash "$hb" 2>&1 >/dev/null); nrc=$?
  if [ "$nrc" -eq 2 ] && case "$nmsg" in *"掃描自己壞了"*) true ;; *) false ;; esac; then
    pass=$((pass+1)); echo "ok   exit=2 [私人內容：掃描自己壞了（不在 git repo 裡）：擋，不放行]"
  else fail=$((fail+1)); echo "FAIL exit=$nrc [私人內容：掃描壞了] got: $(printf '%s' "$nmsg" | head -c 80)"; fi
  rm -rf "$bt"
else
  skip=$((skip+1)); echo "skip [私人內容關卡那一組] —— 需要 git 與 python3"
fi

# 缺 jq：守衛讀不到指令就等於不存在，所以要出聲並擋，不能靜靜放行。
# 造一個只有基本工具、沒有 jq 的 PATH 來跑。
nojq=$(mktemp -d)
for c in bash dirname cat grep sed awk tr cut head; do
  cp_src=$(command -v "$c" 2>/dev/null) && ln -s "$cp_src" "$nojq/$c"
done
jmsg=$(printf '{"tool_input":{"command":"git status"}}' | PATH="$nojq" "$nojq/bash" "$habs" 2>&1 >/dev/null)
jrc=$(printf '{"tool_input":{"command":"git status"}}' | PATH="$nojq" "$nojq/bash" "$habs" >/dev/null 2>&1; echo $?)
if [ "$jrc" -eq 2 ] && case "$jmsg" in *jq*) true ;; *) false ;; esac; then
  pass=$((pass+1)); echo "ok   exit=2 [缺 jq：出聲並擋]"
else
  fail=$((fail+1)); echo "FAIL exit=$jrc want=2 [缺 jq：出聲並擋] got: $(printf '%s' "$jmsg" | head -c 80)"
fi
rm -rf "$nojq"
echo "---"
echo "pass=$pass fail=$fail skip=$skip"
[ "$skip" -gt 0 ] && echo "（跳過的案例代表那條路徑這次沒有被驗過，全綠不等於全部涵蓋）"
[ "$fail" -eq 0 ]

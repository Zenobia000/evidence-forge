"""C3 不照抄（PRD §7）：稿子跟原文的逐字重疊不能超過門檻。

量兩個數：

- **最長共同片段**：稿子裡最長的一段、跟原文某一段一字不差（以詞計）。
- **n-gram 重疊率**：稿子裡連續 n 個詞的組合，有幾成在原文裡也出現。

任何一個超過門檻就 fail，並列出稿子裡每一段至少 n 個詞、跟原文相同的位置，
讓二創段知道要改寫哪裡。門檻是設定值（`OverlapThresholds`，預設見 `defaults.yaml`）；
Q-28 還沒定，S0.14 用實際稿子量，所以結果的理由會註明「門檻暫定」。

詞的算法見 `text.tokenize`：中日韓文一個字一個詞，其他文字一串字母數字一個詞，
標點、空白、大小寫、全形半形都不算差別。
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from evidence_forge.checks.report import CheckEntry, Location
from evidence_forge.checks.text import Token, line_column, tokenize


class OverlapThresholds(BaseModel):
    """C3 的門檻。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # 算重疊率時，幾個連續的詞算一組
    ngram_size: PositiveInt
    # 稿子的 n-gram 有多少比例出現在原文，超過就 fail（0 表示一組都不能有）
    max_ngram_overlap: float = Field(ge=0, le=1)
    # 最長共同片段最多幾個詞，超過就 fail
    max_common_tokens: PositiveInt
    # Q-28 定案前是 True，結果的理由會註明門檻暫定
    provisional: bool = True


@dataclass(frozen=True)
class _Span:
    """稿子裡跟原文相同的一段：第 first 到第 last 個詞（含）。"""

    first: int
    last: int

    @property
    def length(self) -> int:
        return self.last - self.first + 1


def _match_lengths(source: list[str], draft: list[str]) -> list[int]:
    """對稿子每個位置 i，算「以第 i 個詞結尾、也出現在原文裡的最長一段」有幾個詞。

    用原文建後綴自動機（suffix automaton），整份稿子走一遍，時間跟兩邊長度相加成正比；
    文章幾千個詞也不會慢。
    """
    links = [-1]
    lengths = [0]
    edges: list[dict[str, int]] = [{}]
    last = 0
    for token in source:
        current = len(lengths)
        lengths.append(lengths[last] + 1)
        links.append(-1)
        edges.append({})
        state = last
        while state != -1 and token not in edges[state]:
            edges[state][token] = current
            state = links[state]
        if state == -1:
            links[current] = 0
        else:
            target = edges[state][token]
            if lengths[state] + 1 == lengths[target]:
                links[current] = target
            else:
                clone = len(lengths)
                lengths.append(lengths[state] + 1)
                links.append(links[target])
                edges.append(dict(edges[target]))
                while state != -1 and edges[state].get(token) == target:
                    edges[state][token] = clone
                    state = links[state]
                links[target] = clone
                links[current] = clone
        last = current

    result: list[int] = []
    state, matched = 0, 0
    for token in draft:
        while state != 0 and token not in edges[state]:
            state = links[state]
            matched = lengths[state]
        if token in edges[state]:
            state = edges[state][token]
            matched += 1
        result.append(matched)
    return result


def _copied_spans(lengths: list[int], minimum: int) -> list[_Span]:
    """至少 `minimum` 個詞、跟原文相同的段落，相鄰或重疊的併成一段。"""
    spans: list[_Span] = []
    for index, length in enumerate(lengths):
        extends = index + 1 < len(lengths) and lengths[index + 1] == length + 1
        if length < minimum or extends:
            continue
        span = _Span(index - length + 1, index)
        if spans and span.first <= spans[-1].last + 1:
            spans[-1] = _Span(spans[-1].first, max(spans[-1].last, span.last))
        else:
            spans.append(span)
    return spans


def check_overlap(
    draft: str, source_text: str, thresholds: OverlapThresholds, *, file: str
) -> list[CheckEntry]:
    """沒超過門檻就一條 pass；超過就一條總結的 fail，加上每段重疊各一條 fail。"""
    draft_tokens: list[Token] = tokenize(draft)
    lengths = _match_lengths(
        [token.text for token in tokenize(source_text)],
        [token.text for token in draft_tokens],
    )
    n = thresholds.ngram_size
    ngrams = max(len(draft_tokens) - n + 1, 0)
    shared = sum(1 for length in lengths if length >= n)
    rate = shared / ngrams if ngrams else 0.0
    longest = max(lengths, default=0)

    summary = (
        f"最長共同片段 {longest} 個詞（上限 {thresholds.max_common_tokens}）；"
        f"{n}-gram 重疊率 {rate:.1%}（上限 {thresholds.max_ngram_overlap:.1%}）"
    )
    if thresholds.provisional:
        summary += "；門檻暫定（Q-28 未定）"

    if longest <= thresholds.max_common_tokens and rate <= thresholds.max_ngram_overlap:
        return [CheckEntry(check="C3", status="pass", reason=summary)]

    entries = [
        CheckEntry(check="C3", status="fail", reason=f"跟原文逐字重疊太多：{summary}")
    ]
    # 列片段的最短長度：n-gram 的長度，或剛好超過最長共同片段上限的長度，取短的。
    # 只用 n 的話，上限設得比 n 短時會 fail 卻一個位置都列不出來。
    for span in _copied_spans(lengths, min(n, thresholds.max_common_tokens + 1)):
        start = draft_tokens[span.first].start
        end = draft_tokens[span.last].end
        line, column = line_column(draft, start)
        entries.append(
            CheckEntry(
                check="C3",
                status="fail",
                reason=f"這段 {span.length} 個詞都落在跟原文一字不差的片段裡（相鄰的片段已併成一段）",
                location=Location(file=file, line=line, column=column),
                excerpt=draft[start:end],
            )
        )
    return entries

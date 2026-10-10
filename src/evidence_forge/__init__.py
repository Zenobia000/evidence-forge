"""專案的進入點。**目前只有一支示範函式。**

這個檔案存在的理由：pytest 一支測試都收集不到時會回 exit code 5（不是警告），
mypy 在 `files` 指到的目錄沒有任何 .py 時也會失敗。沒有這兩支，`uv run pytest`、
`uv run mypy` 與 CI 從第一天就是紅的，而一條長期紅著的檢查等於沒有檢查。

寫出第一個真的模組之後，把 `greet` 與 `tests/test_greet.py` 一起刪掉。
"""


def greet(name: str) -> str:
    return f"你好，{name}"

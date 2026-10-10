from evidence_forge import greet


# 示範測試：讓 `uv run pytest` 從第一天就有一條真的會跑的測試。寫出第一個真的模組後刪掉。
def test_greet_puts_name_in_greeting() -> None:
    assert greet("世界") == "你好，世界"

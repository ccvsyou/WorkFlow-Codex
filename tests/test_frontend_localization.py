from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_visible_labels_are_chinese():
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

    assert "访问令牌" in js
    assert "访问令牌" in html
    assert "End-to-end delivery" not in js
    assert "Knowledge flywheel" not in js
    assert ">Project<" not in js
    assert "New project" not in js
    assert ">Provider<" not in js
    assert ">Tokens<" not in js
    assert ">Worktree<" not in js
    assert ">STDOUT<" not in js
    assert ">STDERR<" not in js
    assert "Mock 模式" not in js
    assert "隔离工作区" in js
    assert "关键问题" in js
    assert "改进建议" in js
    assert "标准输出" in js
    assert "模型服务" in js
    assert "结构化规格" in js
    assert "在隔离 worktree 内执行" not in js

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_run_view_uses_manual_refresh_instead_of_polling():
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "setTimeout(() => loadRun(runId, true), 2400)" not in js
    assert "状态不会自动刷新" in js
    assert "刷新状态" in js

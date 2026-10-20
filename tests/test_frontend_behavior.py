from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_run_view_uses_manual_refresh_instead_of_polling():
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "setTimeout(() => loadRun(runId, true), 2400)" not in js
    assert "状态不会自动刷新" in js
    assert "刷新状态" in js


def test_demo_onboarding_entry_is_present(  ):
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "/onboarding/demo" in js
    assert "开始 10 分钟体验" in js


def test_product_p0_controls_are_present():
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "项目设置" in js
    assert "data-form=\"project-settings\"" in js
    assert "method: 'PATCH'" in js
    assert "批准入库" in js
    assert "驳回" in js
    assert "预估成本" in js
    assert "用量与成本" in js

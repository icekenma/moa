"""테스트 공통: 실제 앱은 AI 사용이 기본 꺼짐(설정 ai_enabled)이지만, 기존 테스트는 AI가 켜진 상태를 본다.
AI 스위치 자체를 보는 테스트는 @pytest.mark.ai_switch로 표시해 이 바꿔 끼우기를 받지 않는다."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "ai_switch: AI 사용 스위치 기본값(꺼짐)을 그대로 보는 테스트")


@pytest.fixture(autouse=True)
def _ai_on(request, monkeypatch):
    if request.node.get_closest_marker("ai_switch"):
        yield
        return
    from moa import ai
    real = ai.AI.enabled
    monkeypatch.setattr(ai.AI, "enabled", lambda self: True if self.rt.db.get_setting("ai_enabled") is not False else real(self))
    yield

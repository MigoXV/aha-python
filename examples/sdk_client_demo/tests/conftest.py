import pytest
from fake_upstream import fake_app, running


@pytest.fixture
def app(monkeypatch):
    upstream = fake_app()
    with running(upstream) as url:
        monkeypatch.setenv("AHA_BASE_URL", url)
        monkeypatch.setenv("AHA_API_KEY", "test-secret")
        from aha_sdk_demo.web.app import create_app

        yield create_app(), upstream
    assert upstream.state.active == 0

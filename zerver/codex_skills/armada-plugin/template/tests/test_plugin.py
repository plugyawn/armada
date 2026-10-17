import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import plugin

FIXTURE = Path(__file__).parent.parent / "fixtures" / "queue.json"


def test_the_widget_is_the_fixtures_document() -> None:
    assert plugin.document(plugin.read_jobs()) == json.loads(FIXTURE.read_text())


def test_the_figure_never_outgrows_its_sizer() -> None:
    jobs = [{"id": f"job-{n}", "name": "x", "owner": "o", "state": "waiting"} for n in range(88)]
    document = plugin.document(jobs)
    assert len(document["data"]["waiting"]) <= len(document["rest"]["sizer"])
    assert len(document["data"]["jobs"]) == 32


def test_only_armadas_token_reaches_the_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARMADA_PLUGIN_TOKEN", "token")
    with TestClient(plugin.RequireToken(plugin.app())) as client:
        assert client.get("/health").text == "ok"
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401

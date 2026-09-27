"""Проверка настоящих форм Streamlit с изолированным приложением FastAPI."""

import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from msa.api import create_app
from msa.config import ROOT
from msa.llm.mock import MockLLM


def test_streamlit_full_flow(config, case, monkeypatch):
    with TestClient(create_app(config, MockLLM(case))) as client:

        def request(method, url, json=None, **kwargs):
            return client.request(method, httpx.URL(url).path, json=json)

        monkeypatch.setattr(httpx, "request", request)
        app = AppTest.from_file(str(ROOT / "ui/app.py"), default_timeout=30).run()
        assert not app.exception
        next(b for b in app.button if b.label == "Построить сценарий").click().run()
        assert not app.exception
        assert len(app.number_input) == 9
        app.text_input(key="scenario_actor").set_value("Аналитик")
        app.checkbox[0].check()
        next(b for b in app.button if b.label == "Подтвердить и рассчитать").click().run()
        assert not app.exception
        assert any(h.value == "Рекомендации" for h in app.subheader)
        assert len(app.dataframe) >= 4
        app.text_input[0].set_value("Преподаватель")
        app.checkbox[0].check()
        next(b for b in app.button if b.label == "Утвердить отчёт").click().run()
        assert not app.exception
        assert any("Отчёт утверждён" in message.value for message in app.success)
        run_id = app.query_params["run"][0]
        assert client.get(f"/v1/runs/{run_id}/report").status_code == 200

"""Streamlit отображает данные API; расчёты выполняет бэкенд."""

import base64
import os
from pathlib import Path

import altair as alt
import httpx
import pandas as pd
import streamlit as st

API = os.getenv("MSA_API_URL", "http://127.0.0.1:8091")
STATUSES = {
    "awaiting_scenario": "Ожидает подтверждения сценария",
    "awaiting_approval": "Ожидает утверждения отчёта",
    "running": "Выполняется",
    "calculating": "Идёт расчёт",
    "recommending": "Формируются рекомендации",
    "failed": "Этап не завершён",
    "approved": "Утверждён",
    "rejected": "Отклонён",
}


def request(method, path, data=None):
    try:
        response = httpx.request(method, API + path, json=data, timeout=900)
        response.raise_for_status()
        return response.text if path.endswith("/report") else response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "Ошибка запроса")
        except ValueError:
            detail = "Сервер вернул ошибку"
        st.warning(str(detail))
    except httpx.HTTPError:
        # После таймаута POST мог уже изменить состояние; предупреждение предлагает
        # проверить историю перед повторной отправкой.
        st.warning(
            "Нет ответа от сервера. Проверьте запуск API и обновите историю "
            "перед повторной отправкой."
        )
    return None


def activate(run):
    if run:
        # В URL хранится только ID; после rerun состояние заново читается из API.
        st.query_params["run"] = run["id"]
        st.rerun()


def interval_rows(metrics, definitions):
    return [
        {
            "Показатель": d["label"],
            "Единица": d["unit"],
            "База": metrics[d["name"]]["baseline"],
            "P5": metrics[d["name"]]["p05"],
            "P50": metrics[d["name"]]["p50"],
            "P95": metrics[d["name"]]["p95"],
        }
        for d in definitions
    ]


def show_fact(fact):
    st.write(fact["label"])
    if "values" in fact:
        v = fact["values"]
        st.table(
            [
                {
                    "Единица": fact["unit"],
                    "База": round(v["baseline"], 3),
                    "P5": round(v["p05"], 3),
                    "P50": round(v["p50"], 3),
                    "P95": round(v["p95"], 3),
                }
            ]
        )
    else:
        st.write(f"{fact['value']:.1%}")
    st.caption(fact["source"])


def scenario_panel(run, catalog):
    scenario = run["scenario"]
    declarations = catalog[run["portfolio"]["assets"][0]["asset_type"]]["drivers"]
    by_driver = {item["name"]: item for item in declarations}
    st.subheader("Сценарий")
    st.write(" → ".join(scenario["transmission"]))
    st.caption("Основание — фрагмент введённого события:")
    st.write(scenario["event_quote"])
    if run["provider"]["provider"] == "mock":
        st.info(
            "Заглушка предлагает один фиксированный сценарий для любого текста. "
            "Это иллюстрация, которую можно изменить вручную."
        )
    st.caption(
        "Минимум / наиболее вероятное значение / максимум — параметры распределения"
    )
    if run["status"] != "awaiting_scenario":
        rows = []
        for shock in scenario["shocks"]:
            d = by_driver[shock["driver"]]
            rows.append(
                {
                    "Драйвер": d["label"],
                    "Единица": d["unit"],
                    "Минимум": shock["low"] * d["scale"],
                    "Мода": shock["mode"] * d["scale"],
                    "Максимум": shock["high"] * d["scale"],
                    "Основание": shock["explanation"],
                }
            )
        st.dataframe(rows, hide_index=True, width="stretch")
        if run.get("confirmation"):
            st.caption(f"Подтвердил: {run['confirmation']['actor']} · {run['confirmation']['at']}")
        return
    with st.form("scenario-" + run["id"]):
        shocks = []
        for shock in scenario["shocks"]:
            d = by_driver[shock["driver"]]
            st.write(f"**{d['label']} · {d['unit']}**")
            values = {}
            for column, field, label in zip(
                st.columns(3), ("low", "mode", "high"), ("Минимум", "Мода", "Максимум"), strict=True
            ):
                with column:
                    values[field] = (
                        st.number_input(
                            label,
                            min_value=float(d["min"] * d["scale"]),
                            max_value=float(d["max"] * d["scale"]),
                            value=float(shock[field] * d["scale"]),
                            step=0.1,
                            # Изолируем черновики параметров разных запусков в Streamlit.
                            key=f"{run['id']}-{shock['driver']}-{field}",
                        )
                        / d["scale"]
                    )
            explanation = st.text_input(
                "Обоснование допущения",
                value=shock["explanation"],
                key=f"{run['id']}-{shock['driver']}-reason",
            )
            shocks.append({**shock, **values, "explanation": explanation})
        actor = st.text_input("Кто подтверждает сценарий", key="scenario_actor")
        accepted = st.checkbox("Я проверил параметры")
        submit = st.form_submit_button("Подтвердить и рассчитать", type="primary")
    if submit:
        if not actor.strip() or not accepted:
            st.warning("Укажите имя и подтвердите проверку допущений.")
        else:
            with st.spinner("Выполняется расчёт и формируются рекомендации…"):
                activate(
                    request(
                        "POST",
                        f"/v1/runs/{run['id']}/confirm",
                        {"actor": actor, "scenario": {**scenario, "shocks": shocks}},
                    )
                )


def result_panel(run, catalog):
    result = run["result"]
    definitions = catalog[run["portfolio"]["assets"][0]["asset_type"]]["metrics"]
    st.subheader("Результат стресс-теста")
    st.caption(
        f"Дата оценки: {result['as_of']} · {result['points']} реализаций · "
        f"seed {result['seed']} · модель {result['model_version']}"
    )
    st.dataframe(
        interval_rows(result["portfolio"]["metrics"], definitions), hide_index=True, width="stretch"
    )
    st.caption(
        "Портфель: сначала агрегируются значения в каждой реализации, затем квантили. "
        "База — расчёт без шоков."
    )
    probability = result["portfolio"]["breach_probability"]
    st.write(f"Вероятность хотя бы одного нарушения ковенанта в портфеле: **{probability:.1%}**.")
    st.caption("Источник: доля общих реализаций с нарушением хотя бы у одной компании.")
    options = {d["name"]: d for d in definitions}
    metric = st.selectbox(
        "Помесячный показатель портфеля",
        list(options),
        format_func=lambda key: options[key]["label"],
    )
    monthly = result["portfolio"]["monthly"][metric]
    frame = pd.DataFrame(
        {
            "Месяц": pd.to_datetime(result["months"]),
            "P5": monthly["p05"],
            "P50": monthly["p50"],
            "P95": monthly["p95"],
        }
    )
    frame = frame.melt("Месяц", var_name="Квантиль", value_name="Значение")
    chart = (
        alt.Chart(frame)
        .mark_line()
        .encode(
            x=alt.X("Месяц:T", title="Месяц"),
            y=alt.Y("Значение:Q", title=options[metric]["unit"], scale=alt.Scale(zero=False)),
            color=alt.Color(
                "Квантиль:N",
                scale=alt.Scale(
                    domain=["P5", "P50", "P95"], range=["#697A8A", "#2D5F87", "#318477"]
                ),
            ),
            strokeDash=alt.StrokeDash("Квантиль:N"),
            tooltip=["Месяц:T", "Квантиль:N", alt.Tooltip("Значение:Q", format=".3f")],
        )
        .properties(height=280)
    )
    st.altair_chart(chart, width="stretch")
    st.caption(
        "Источник: помесячные реализации расчётной модели. "
        "P5/P50/P95 показывают разброс исходов, а не доверительный интервал."
    )
    for asset in result["assets"]:
        with st.expander(asset["name"]):
            st.dataframe(
                interval_rows(asset["metrics"], definitions), hide_index=True, width="stretch"
            )
            st.write(f"Вероятность нарушения ковенанта: {asset['breach_probability']:.1%}")
            st.caption(asset["source"] + ". Вероятность — доля реализаций с нарушением.")
    with st.expander("Допущения и происхождение результата"):
        for assumption in result["assumptions"]:
            st.write(assumption)
        for definition in definitions:
            st.write(f"{definition['label']} = {definition['definition']}")
        #st.caption("SHA-256 общей матрицы сценариев")
        #st.code(result["draws_sha256"], language=None)
    if not run.get("advice"):
        return
    st.subheader("Рекомендации")
    st.caption(
        f"Автор: {run.get('advice_provider', run['provider'])['model']}. "
        "Результаты расчета получены LLM через MCP."
    )
    st.write(run["advice"]["summary"])
    for recommendation in run["advice"]["recommendations"]:
        st.write(f"**{recommendation['action']}**")
        st.write(recommendation["rationale"])
        with st.expander("Основания рекомендации"):
            for reference in recommendation["evidence"]:
                show_fact(run["facts"][reference])
                st.caption("Ссылка: " + reference)
    if run["status"] == "awaiting_approval":
        with st.container(key="human-approval"):
            with st.form("decision-" + run["id"]):
                actor = st.text_input("Кто принимает решение по отчёту")
                accepted = st.checkbox("Я проверил результаты и рекомендации")
                approved = st.form_submit_button("Утвердить отчёт", type="primary")
                rejected = st.form_submit_button("Отклонить отчёт")
            if approved or rejected:
                if not actor.strip() or not accepted:
                    st.warning("Укажите имя и подтвердите проверку отчёта.")
                else:
                    activate(
                        request(
                            "POST",
                            f"/v1/runs/{run['id']}/decision",
                            {
                                "action": "approve" if approved else "reject",
                                "actor": actor,
                            },
                        )
                    )
    if run["status"] == "approved":
        st.success(f"Отчёт утверждён: {run['decision']['actor']} · {run['decision']['at']}")
        report = request("GET", f"/v1/runs/{run['id']}/report")
        if report:
            st.download_button(
                "Скачать утверждённый отчёт",
                report,
                file_name=f"stress-test-{run['id']}.md",
                mime="text/markdown",
            )
    elif run["status"] == "rejected":
        st.info(f"Отчёт отклонён: {run['decision']['actor']}")


def main():
    st.set_page_config(page_title="MacroStress", layout="wide")
    font_file = Path(__file__).parent / "assets/golos-cyrillic.woff2"
    if font_file.exists():
        font = base64.b64encode(font_file.read_bytes()).decode()
        st.html(f"""<style>
        @font-face {{font-family: Golos; src: url(data:font/woff2;base64,{font}) format('woff2');}}
        html, body, [data-testid="stApp"], [data-testid="stMarkdownContainer"] {{
            font-family: Golos, sans-serif; font-variant-numeric: tabular-nums;
        }}
        .st-key-human-approval button[kind="primary"] {{
            background: #E30611; border-color: #E30611;
        }}
        </style>""")
    st.title("MacroStress")
    st.write("Стресс-тест кредитного портфеля")
    demo = request("GET", "/v1/demo")
    types = request("GET", "/v1/asset-types")
    history = request("GET", "/v1/runs")
    if demo is None or types is None or history is None:
        st.stop()
    catalog = {t["asset_type"]: t for t in types}
    with st.sidebar:
        st.header("Запуски")
        st.caption("LLM: " + demo["provider"]["model"])
        ids = ["new", *[r["id"] for r in history]]
        current = st.query_params.get("run", "new")
        if current not in ids:
            ids.append(current)
        labels = {
            r["id"]: r["created_at"][:16].replace("T", " ")
            + " · "
            + STATUSES.get(r["status"], r["status"])
            for r in history
        }
        selected = st.selectbox(
            "История",
            ids,
            index=ids.index(current),
            format_func=lambda key: "Новый стресс-тест" if key == "new" else labels.get(key, key),
        )
        if selected != current:
            if selected == "new":
                st.query_params.clear()
            else:
                st.query_params["run"] = selected
            st.rerun()
        if st.button("Обновить состояние"):
            st.rerun()
        st.caption("Все компании и финансовые данные синтетические.")
    if current == "new":
        portfolio = demo["portfolio"]
        st.subheader("Исходные данные")
        with st.expander(portfolio["name"] + " · исходные показатели"):
            for asset in portfolio["assets"]:
                st.write(asset["name"])
                fields = catalog[asset["asset_type"]]["fields"]
                st.dataframe(
                    [
                        {
                            "Поле": f["label"],
                            "Значение": asset["inputs"][f["name"]],
                            "Единица": f["unit"],
                        }
                        for f in fields
                    ],
                    hide_index=True,
                    width="stretch",
                )
                st.caption(asset["source"])
        event = st.selectbox(
            "Событие",
            range(len(demo["events"])),
            format_func=lambda i: demo["events"][i]["title"],
        )
        with st.form("new-run"):
            text = st.text_area(
                "Текст события",
                value=demo["events"][event]["text"],
                height=140,
                key=f"event-{event}",
            )
            st.caption(
                "Фиксированные настройки: 1024 реализации, seed 42, горизонт 12 месяцев. "
                "Дата оценки берётся из портфеля."
            )
            submit = st.form_submit_button("Построить сценарий", type="primary")
        if submit:
            with st.spinner("Формируется сценарий…"):
                activate(request("POST", "/v1/runs", {"text": text, "seed": 42, "points": 1024}))
        return
    run = request("GET", f"/v1/runs/{current}")
    if run is None:
        st.stop()
    st.write("**" + STATUSES.get(run["status"], run["status"]) + "**")
    with st.expander("Исходное событие"):
        st.write(run["request"]["text"])
        st.caption(f"Запуск {run['id']} · создан {run['created_at']}")
    if run["status"] in {"failed", "running", "calculating", "recommending"}:
        st.warning(
            run.get("error")
            or "Этап выполняется или был прерван. Обновите состояние; "
            "после перезапуска сервера можно продолжить."
        )
        if st.button("Повторить незавершённый этап"):
            with st.spinner("Продолжается обработка…"):
                activate(request("POST", f"/v1/runs/{current}/retry"))
    if run.get("scenario"):
        scenario_panel(run, catalog)
    if run.get("result"):
        result_panel(run, catalog)


if __name__ == "__main__":
    main()

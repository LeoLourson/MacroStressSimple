"""Числа берутся из расчёта; LLM формирует текст со ссылками на эти результаты."""

from msa.assets import model_for
from msa.contracts import Advice


def facts_for(result):
    facts = {}
    groups = [("portfolio", "Портфель", result["portfolio"])]
    groups += [(a["asset_id"], a["name"], a) for a in result["assets"]]
    for key, label, group in groups:
        for definition in result["metric_definitions"]:
            name = definition["name"]
            facts[f"{key}.{name}"] = {
                "label": f"{label} · {definition['label']}",
                "unit": definition["unit"],
                "values": group["metrics"][name],
                "definition": definition["definition"],
                "source": f"Расчёт {result['model_version']}; матрица {result['draws_sha256']}",
            }
        facts[f"{key}.breach_probability"] = {
            "label": f"{label} · вероятность хотя бы одного нарушения ковенанта",
            "value": group["breach_probability"],
            "unit": "доля",
            "source": f"Доля реализаций с нарушением; {result['points']} реализаций, "
            f"seed {result['seed']}",
        }
    return facts


def validate_advice(advice: Advice, facts: dict):
    # Проверка подтверждает наличие ссылок, но не смысловую обоснованность совета;
    # последнее остаётся за человеком при утверждении отчёта.
    prose = [advice.summary]
    for recommendation in advice.recommendations:
        prose.extend([recommendation.action, recommendation.rationale])
        if any(reference not in facts for reference in recommendation.evidence):
            raise ValueError("Рекомендация ссылается на отсутствующую метрику")
    # Численные значения выводятся из facts, чтобы LLM не переписывала их в тексте.
    # Фильтр ловит цифры, но не числительные, записанные словами.
    if any(character.isdigit() for text in prose for character in text):
        raise ValueError("Числа в рекомендациях допустимы только через ссылки на метрики")
    return advice


def fact_text(fact):
    if "values" in fact:
        v = fact["values"]
        return (
            f"{fact['label']}: P5 {v['p05']:.3f} / P50 {v['p50']:.3f} / "
            f"P95 {v['p95']:.3f} {fact['unit']}; база {v['baseline']:.3f}"
        )
    return f"{fact['label']}: {fact['value']:.1%}"


def markdown(run):
    result, advice = run["result"], run["advice"]
    facts = facts_for(result)
    provider = run.get("advice_provider", run["provider"])
    model = model_for(run["portfolio"]["assets"][0]["asset_type"])
    lines = [
        "# Стресс-тест",
        "",
        f"Запуск: {run['id']}",
        "Статус: утверждён",
        f"Дата оценки: {result['as_of']}",
        "",
        "## Событие",
        "",
        run["request"]["text"],
        "",
        "## Сценарий",
        "",
        run["scenario"]["title"],
        " → ".join(run["scenario"]["transmission"]),
        "Численные параметры — допущения, подтверждённые человеком.",
        "",
    ]
    for shock in run["scenario"]["shocks"]:
        definition = next(d for d in model.drivers if d["name"] == shock["driver"])
        scale = definition["scale"]
        lines.append(
            f"- {definition['label']}: минимум {shock['low'] * scale:.3f} / "
            f"мода {shock['mode'] * scale:.3f} / максимум {shock['high'] * scale:.3f} "
            f"{definition['unit']}; {shock['explanation']}"
        )
    lines.extend(["", "## Исходные данные", ""])
    for asset in run["portfolio"]["assets"]:
        lines.extend(["", f"### {asset['name']}", f"Источник: {asset['source']}"])
        for field in model.fields:
            lines.append(f"- {field['label']}: {asset['inputs'][field['name']]} {field['unit']}")
    lines.extend(["", "## Результаты", ""])
    for reference, fact in facts.items():
        lines.append(f"- {fact_text(fact)}. Источник: {fact['source']} [{reference}]")
    lines.extend(["", "## Рекомендации", "", advice["summary"]])
    for recommendation in advice["recommendations"]:
        lines.extend(["", f"### {recommendation['action']}", recommendation["rationale"]])
        lines.extend(f"- {fact_text(facts[ref])} [{ref}]" for ref in recommendation["evidence"])
    lines.extend(["", "## Допущения", "", *("- " + a for a in result["assumptions"])])
    lines.extend(
        [
            "",
            f"Профиль рекомендаций: {provider['provider']}",
            f"Модель: {provider['model']}",
            f"Seed: {result['seed']}; реализаций: {result['points']}",
            f"SHA-256 матрицы: {result['draws_sha256']}",
            f"Подтверждение сценария: {run.get('confirmation')}",
            f"Решение по отчёту: {run.get('decision')}",
            "",
        ]
    )
    return "\n".join(lines)

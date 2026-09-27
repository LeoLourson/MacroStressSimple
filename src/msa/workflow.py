from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from msa.agents import advise, propose
from msa.assets import validate_scenario
from msa.contracts import Confirmation, Decision, Portfolio
from msa.db.store import now
from msa.mcp import build_server, call


class State(TypedDict):
    run_id: str


def build_graph(store, llm, checkpointer):
    async def scenario(state):
        run_id = state["run_id"]
        run = store.get(run_id)
        portfolio = await call(build_server(store, run_id), "get_portfolio")
        proposal = await propose(llm, run["request"], portfolio)
        store.save(
            run_id,
            scenario=proposal,
            provider=llm.description,
            status="awaiting_scenario",
            error=None,
        )
        return {}

    async def review(state):
        # При resume LangGraph выполняет узел с начала; запись подтверждения должна
        # оставаться после interrupt, чтобы ожидание решения не меняло данные.
        value = interrupt({"kind": "scenario"})
        confirmation = Confirmation.model_validate(value)
        run = store.get(state["run_id"])
        validate_scenario(
            confirmation.scenario,
            Portfolio.model_validate(run["portfolio"]),
            run["request"]["text"],
        )
        store.save(
            state["run_id"],
            scenario=confirmation.scenario.model_dump(mode="json"),
            confirmation={
                "actor": confirmation.actor,
                "at": now(),
                "source": "Подтверждённое допущение аналитика",
            },
            status="calculating",
        )
        return {}

    async def calculate(state):
        result = await call(build_server(store, state["run_id"]), "run_calculation")
        store.save(state["run_id"], result=result, status="recommending")
        return {}

    async def recommendations(state):
        advice, trace = await advise(llm, build_server(store, state["run_id"]))
        store.save(
            state["run_id"],
            advice=advice,
            tool_trace=trace,
            advice_provider=llm.description,
            status="awaiting_approval",
            error=None,
        )
        return {}

    async def approval(state):
        # Как и в review, до interrupt нельзя сохранять решение: узел будет переигран.
        value = interrupt({"kind": "approval"})
        decision = Decision.model_validate(value)
        store.save(
            state["run_id"],
            status="approved" if decision.action == "approve" else "rejected",
            decision={**decision.model_dump(), "at": now()},
        )
        return {}

    graph = StateGraph(State)
    nodes = [scenario, review, calculate, recommendations, approval]
    previous = START
    for node in nodes:
        graph.add_node(node.__name__, node)
        graph.add_edge(previous, node.__name__)
        previous = node.__name__
    graph.add_edge(previous, END)
    return graph.compile(checkpointer=checkpointer)

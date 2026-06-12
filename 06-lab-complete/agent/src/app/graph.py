from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent

from app.config import Settings
from app.data_access import ShoppingDataStore, build_data_tools
from app.prompts import (
    SUPERVISOR_PROMPT,
    POLICY_WORKER_PROMPT,
    DATA_WORKER_SYSTEM_PROMPT,
    RESPONSE_WORKER_PROMPT,
)
from app.state import ShoppingState
from app.utils import dump_json, extract_json_payload
from provider import get_chat_model
from rag.embeddings import SentenceTransformerEmbeddings
from rag.vector_store import ChromaPolicyStore


class ShoppingAssistant:
    """Multi-agent shopping assistant powered by LangGraph."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()

        # Load LLM from configured provider
        self.llm = get_chat_model(self.settings)

        # Load mock data store and build tools
        self.store = ShoppingDataStore(self.settings.orders_path)
        self.data_tools = build_data_tools(self.store)

        # Load embedding model and vector store
        self.embedding_model = SentenceTransformerEmbeddings(
            self.settings.embedding_model_name
        )
        self.vector_store = ChromaPolicyStore(
            persist_directory=self.settings.chroma_dir,
            embedding_model=self.embedding_model,
        )
        self.vector_store.ensure_index(self.settings.policy_path)

        # Ensure output dirs exist
        self.settings.traces_dir.mkdir(parents=True, exist_ok=True)

        # Compile LangGraph
        self.graph = build_graph(
            llm=self.llm,
            vector_store=self.vector_store,
            data_tools=self.data_tools,
            settings=self.settings,
        )

    def ask(
        self,
        question: str,
        trace_file: Path | None = None,
        rebuild_index: bool = False,
    ) -> dict[str, Any]:
        if rebuild_index:
            self.vector_store.rebuild(self.settings.policy_path)

        initial_state: ShoppingState = {
            "question": question,
            "trace": [],
        }

        final_state = self.graph.invoke(initial_state)

        payload: dict[str, Any] = {
            "question": question,
            "route": final_state.get("route", {}),
            "policy_result": final_state.get("policy_result", {}),
            "data_result": final_state.get("data_result", {}),
            "final_answer": final_state.get("final_answer", ""),
            "trace": final_state.get("trace", []),
        }

        if trace_file:
            trace_file.parent.mkdir(parents=True, exist_ok=True)
            trace_file.write_text(dump_json(payload), encoding="utf-8")

        return payload

    def run_batch(
        self,
        test_file: Path,
        output_dir: Path,
        rebuild_index: bool = False,
    ) -> dict[str, Any]:
        test_cases = json.loads(test_file.read_text(encoding="utf-8"))
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        first = True
        for case in test_cases:
            qid = case.get("id", "unknown")
            question = case.get("question", "")
            trace_file = output_dir / f"{qid}_trace.json"

            try:
                result = self.ask(
                    question,
                    trace_file=trace_file,
                    rebuild_index=(rebuild_index and first),
                )
                first = False

                final_answer = result.get("final_answer", "")
                answer_lower = final_answer.lower()

                if "clarification_needed" in answer_lower:
                    actual_status = "clarification_needed"
                elif "not_found" in answer_lower:
                    actual_status = "not_found"
                else:
                    actual_status = "ok"

                expected_status = case.get("expected_status", "ok")
                status_match = actual_status == expected_status

                results.append(
                    {
                        "id": qid,
                        "question": question,
                        "expected_status": expected_status,
                        "actual_status": actual_status,
                        "status_match": status_match,
                        "final_answer": final_answer[:500],
                        "trace_file": str(trace_file),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "id": qid,
                        "question": question,
                        "error": str(exc),
                        "status_match": False,
                    }
                )

        total = len(results)
        passed = sum(1 for r in results if r.get("status_match", False))
        summary = {"total": total, "passed": passed, "results": results}

        summary_file = output_dir / "summary.json"
        summary_file.write_text(dump_json(summary), encoding="utf-8")
        return summary


# LangGraph workflow


def build_graph(
    llm: Any,
    vector_store: ChromaPolicyStore,
    data_tools: list,
    settings: Settings,
) -> Any:
    """Build and compile the multi-agent LangGraph workflow."""

    # ReAct data agent with all lookup tools
    data_agent = create_react_agent(
        llm,
        data_tools,
        prompt=SystemMessage(content=DATA_WORKER_SYSTEM_PROMPT),
    )

    # Node implementations

    def supervisor_node(state: ShoppingState) -> dict[str, Any]:
        question = state["question"]
        prompt = SUPERVISOR_PROMPT.format(question=question)
        response = llm.invoke([HumanMessage(content=prompt)])
        route = extract_json_payload(response.content)
        # Fallback if parse fails
        if not route or "status" not in route:
            route = {
                "status": "ok",
                "needs_policy": True,
                "needs_data": False,
                "clarification_question": None,
            }
        return {
            "route": route,
            "trace": [{"step": "supervisor", "output": route}],
        }

    def worker_1_policy_node(state: ShoppingState) -> dict[str, Any]:
        question = state["question"]
        hits = vector_store.search(query=question, top_k=settings.top_k)

        if hits:
            chunks_text = "\n\n---\n\n".join(
                f"[{h['citation']}]\n{h['content']}" for h in hits
            )
        else:
            chunks_text = "Không tìm thấy đoạn chính sách liên quan."

        prompt = POLICY_WORKER_PROMPT.format(
            question=question,
            policy_chunks=chunks_text,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        policy_result = extract_json_payload(response.content)

        if not policy_result or "summary" not in policy_result:
            policy_result = {
                "status": "ok",
                "summary": response.content,
                "facts": [],
                "citations": [h["citation"] for h in hits],
            }

        return {
            "policy_result": policy_result,
            "trace": [
                {
                    "step": "worker_1_policy",
                    "hits": len(hits),
                    "citations": [h["citation"] for h in hits],
                }
            ],
        }

    def worker_2_data_node(state: ShoppingState) -> dict[str, Any]:
        question = state["question"]

        agent_result = data_agent.invoke(
            {"messages": [HumanMessage(content=question)]}
        )
        messages = agent_result.get("messages", [])

        # Collect tool call log
        tool_calls_log = []
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls_log.append(
                        {"tool": tc.get("name", ""), "args": tc.get("args", {})}
                    )

        # Extract final AI answer
        last_content = ""
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content:
                c = msg.content
                if isinstance(c, str) and c.strip():
                    last_content = c
                    break

        data_result: dict[str, Any] = {
            "status": "ok",
            "summary": last_content,
            "facts": [last_content] if last_content else [],
            "tool_calls": tool_calls_log,
        }

        # Detect not_found from tool responses
        for msg in messages:
            if hasattr(msg, "content") and isinstance(msg.content, str):
                try:
                    parsed = json.loads(msg.content)
                    if parsed.get("status") == "not_found":
                        data_result["status"] = "not_found"
                except (json.JSONDecodeError, AttributeError):
                    pass

        return {
            "data_result": data_result,
            "trace": [
                {"step": "worker_2_data", "tool_calls": tool_calls_log}
            ],
        }

    def worker_3_response_node(state: ShoppingState) -> dict[str, Any]:
        route = state.get("route", {})
        question = state.get("question", "")
        policy_result = state.get("policy_result", {})
        data_result = state.get("data_result", {})

        # Short-circuit for clarification
        if route.get("status") == "clarification_needed":
            clarification_q = route.get(
                "clarification_question",
                "Bạn có thể cung cấp order_id hoặc customer_id không?",
            )
            final_answer = (
                f"Status: clarification_needed\nQuestion: {clarification_q}"
            )
            return {
                "final_answer": final_answer,
                "trace": [
                    {"step": "worker_3_response", "status": "clarification_needed"}
                ],
            }

        prompt = RESPONSE_WORKER_PROMPT.format(
            question=question,
            route=dump_json(route),
            policy_result=dump_json(policy_result) if policy_result else "Không có",
            data_result=dump_json(data_result) if data_result else "Không có",
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        final_answer = response.content

        return {
            "final_answer": final_answer,
            "trace": [
                {
                    "step": "worker_3_response",
                    "answer_length": len(final_answer),
                }
            ],
        }

    # Routing functions

    def route_after_supervisor(state: ShoppingState) -> str:
        route = state.get("route", {})
        if route.get("status") == "clarification_needed":
            return "worker_3_response"
        if route.get("needs_policy", False):
            return "worker_1_policy"
        if route.get("needs_data", False):
            return "worker_2_data"
        return "worker_3_response"

    def route_after_policy(state: ShoppingState) -> str:
        route = state.get("route", {})
        if route.get("needs_data", False):
            return "worker_2_data"
        return "worker_3_response"

    # Build graph

    graph = StateGraph(ShoppingState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("worker_1_policy", worker_1_policy_node)
    graph.add_node("worker_2_data", worker_2_data_node)
    graph.add_node("worker_3_response", worker_3_response_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "worker_1_policy": "worker_1_policy",
            "worker_2_data": "worker_2_data",
            "worker_3_response": "worker_3_response",
        },
    )
    graph.add_conditional_edges(
        "worker_1_policy",
        route_after_policy,
        {
            "worker_2_data": "worker_2_data",
            "worker_3_response": "worker_3_response",
        },
    )
    graph.add_edge("worker_2_data", "worker_3_response")
    graph.add_edge("worker_3_response", END)

    return graph.compile()


# Standalone stubs (kept for backward compatibility with scaffold imports)


def supervisor_node(state: ShoppingState) -> ShoppingState:  # type: ignore[return]
    raise NotImplementedError("Use build_graph() to get a compiled graph.")


def worker_1_policy_node(state: ShoppingState) -> ShoppingState:  # type: ignore[return]
    raise NotImplementedError("Use build_graph() to get a compiled graph.")


def worker_2_data_node(state: ShoppingState) -> ShoppingState:  # type: ignore[return]
    raise NotImplementedError("Use build_graph() to get a compiled graph.")


def worker_3_response_node(state: ShoppingState) -> ShoppingState:  # type: ignore[return]
    raise NotImplementedError("Use build_graph() to get a compiled graph.")

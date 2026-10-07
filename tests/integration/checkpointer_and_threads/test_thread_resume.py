"""
Tests de integración para persistencia, checkpointer y reanudación (Time-Travel / HITL).

Verifica la integración con LangGraph MemorySaver, puntos de interrupción (`interrupt`)
y reanudación controlada con `Command(resume=...)` comprobando la idempotencia y la no
duplicación de trabajo de nodos previos.
"""

from __future__ import annotations

from typing import Any, TypedDict
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


# ---------------------------------------------------------------------------
# Estado tipado representativo del pipeline
# ---------------------------------------------------------------------------

class PipelineTestState(TypedDict, total=False):
    thread_name: str
    items_ingested: int
    raw_data: list[str]
    curation_approved: bool
    curator_notes: str
    export_finished: bool
    nodes_executed: list[str]


# ---------------------------------------------------------------------------
# Definición de la suite de tests
# ---------------------------------------------------------------------------

class TestCheckpointerAndThreadResume:
    """Valida la pausa por interrupción HITL y la reanudación idempotente."""

    @pytest.fixture
    def test_workflow(self):
        """
        Construye un StateGraph con un nodo previo (ingest), un nodo de revisión
        humana (hitl_review con interrupt) y un nodo posterior (export).
        Registra contadores de ejecución para verificar idempotencia.
        """
        execution_counts: dict[str, int] = {
            "ingest_node": 0,
            "hitl_review_node": 0,
            "export_node": 0,
        }

        def ingest_node(state: PipelineTestState) -> dict[str, Any]:
            execution_counts["ingest_node"] += 1
            executed = list(state.get("nodes_executed", []))
            executed.append("ingest_node")
            return {
                "items_ingested": 10,
                "raw_data": [f"item_{i}" for i in range(10)],
                "nodes_executed": executed,
            }

        def hitl_review_node(state: PipelineTestState) -> dict[str, Any]:
            execution_counts["hitl_review_node"] += 1
            executed = list(state.get("nodes_executed", []))
            executed.append("hitl_review_node")

            # Pausa interactiva HITL
            decision = interrupt({
                "prompt": "¿Aprobar metadatos para importación a SEDICI?",
                "items_count": state.get("items_ingested", 0),
            })

            # Al reanudar con Command(resume=...), decision contendrá el payload del usuario
            approved = decision.get("approved", False) if isinstance(decision, dict) else bool(decision)
            notes = decision.get("notes", "") if isinstance(decision, dict) else str(decision)

            return {
                "curation_approved": approved,
                "curator_notes": notes,
                "nodes_executed": executed,
            }

        def export_node(state: PipelineTestState) -> dict[str, Any]:
            execution_counts["export_node"] += 1
            executed = list(state.get("nodes_executed", []))
            executed.append("export_node")
            return {
                "export_finished": True,
                "nodes_executed": executed,
            }

        builder = StateGraph(PipelineTestState)
        builder.add_node("ingest_node", ingest_node)
        builder.add_node("hitl_review_node", hitl_review_node)
        builder.add_node("export_node", export_node)

        builder.add_edge(START, "ingest_node")
        builder.add_edge("ingest_node", "hitl_review_node")
        builder.add_edge("hitl_review_node", "export_node")
        builder.add_edge("export_node", END)

        checkpointer = MemorySaver()
        compiled_graph = builder.compile(checkpointer=checkpointer)

        return compiled_graph, execution_counts, checkpointer

    def test_hitl_interrupt_pauses_execution_and_persists_checkpoint(self, test_workflow):
        """Verifica que el flujo se detiene en interrupt y persiste el snapshot en el checkpointer."""
        graph, execution_counts, checkpointer = test_workflow
        thread_id = "thread_import_hitl_001"
        config = {"configurable": {"thread_id": thread_id}}

        initial_state: PipelineTestState = {
            "thread_name": "Lote_Springer_2026",
            "items_ingested": 0,
            "nodes_executed": [],
        }

        # Ejecución inicial: debe llegar hasta interrupt y pausarse
        graph.invoke(initial_state, config=config)

        # Verificar que ingest se ejecutó y hitl_review pausó
        assert execution_counts["ingest_node"] == 1
        assert execution_counts["hitl_review_node"] == 1
        assert execution_counts["export_node"] == 0

        # Inspeccionar el estado persistido
        snapshot = graph.get_state(config)
        assert snapshot is not None
        assert snapshot.next == ("hitl_review_node",)
        assert snapshot.values["items_ingested"] == 10
        assert len(snapshot.values["raw_data"]) == 10

        # Verificar que el payload de la interrupción fue capturado correctamente
        assert len(snapshot.tasks) > 0
        interrupts = snapshot.tasks[0].interrupts
        assert len(interrupts) > 0
        assert interrupts[0].value["prompt"] == "¿Aprobar metadatos para importación a SEDICI?"
        assert interrupts[0].value["items_count"] == 10

    def test_resume_with_command_is_idempotent_and_completes_pipeline(self, test_workflow):
        """
        Verifica que al reanudar con Command(resume=...):
          1. El nodo previo (ingest_node) NO se vuelve a ejecutar (idempotencia garantizada).
          2. El flujo procesa la decisión humana y finaliza exitosamente en END.
        """
        graph, execution_counts, checkpointer = test_workflow
        thread_id = "thread_import_hitl_002"
        config = {"configurable": {"thread_id": thread_id}}

        initial_state: PipelineTestState = {
            "thread_name": "Lote_Jaio_PDFs",
            "items_ingested": 0,
            "nodes_executed": [],
        }

        # Paso 1: Ejecutar hasta la interrupción
        graph.invoke(initial_state, config=config)
        assert execution_counts["ingest_node"] == 1
        assert execution_counts["export_node"] == 0

        # Paso 2: Reanudar con Command(resume=...) simulando aprobación de curador
        resume_payload = {
            "approved": True,
            "notes": "Metadatos validados y aceptados sin reparos por el operador.",
        }
        graph.invoke(Command(resume=resume_payload), config=config)

        # Verificación de idempotencia estricta:
        # ingest_node NO debe ejecutarse por segunda vez
        assert execution_counts["ingest_node"] == 1, (
            f"Fallo de idempotencia: ingest_node se ejecutó {execution_counts['ingest_node']} veces."
        )

        # export_node ahora sí debió ejecutarse
        assert execution_counts["export_node"] == 1

        # Verificación del estado final
        final_snapshot = graph.get_state(config)
        assert final_snapshot.next == (), "El grafo debió alcanzar el nodo terminal END."
        assert final_snapshot.values["curation_approved"] is True
        assert "validados y aceptados" in final_snapshot.values["curator_notes"]
        assert final_snapshot.values["export_finished"] is True

    def test_multiple_threads_isolation(self, test_workflow):
        """Verifica que dos hilos independientes no interfieren en sus checkpoints ni reanudaciones."""
        graph, execution_counts, _ = test_workflow

        config_a = {"configurable": {"thread_id": "thread_A"}}
        config_b = {"configurable": {"thread_id": "thread_B"}}

        # Iniciar thread A
        graph.invoke({"thread_name": "Lote A"}, config=config_a)
        # Iniciar thread B
        graph.invoke({"thread_name": "Lote B"}, config=config_b)

        assert graph.get_state(config_a).values["thread_name"] == "Lote A"
        assert graph.get_state(config_b).values["thread_name"] == "Lote B"

        # Reanudar solo el thread A con aprobación
        graph.invoke(Command(resume={"approved": True, "notes": "Aprobado A"}), config=config_a)

        # Thread A terminó, pero Thread B sigue pausado en HITL
        assert graph.get_state(config_a).next == ()
        assert graph.get_state(config_b).next == ("hitl_review_node",)

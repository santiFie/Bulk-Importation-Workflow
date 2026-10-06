"""
Suite de Evaluación Semántica del Agente de Recuperación Reactiva con LLM Real.

Diseñada para inspección visual y análisis experimental de tesis:
  - Compara la salida generada por el LLM en tiempo real contra el Ground Truth esperado.
  - Permite evaluar:
      1. Capacidad de deducción autónoma (from scratch / sin memoria previa).
      2. Transferencia positiva ante casos análogos (memoria episódica relevante).
      3. Robustez y discriminación ante casos no aplicables (memoria episódica distractora).
      4. Calibración de certeza y abstención ante ambigüedad intrínseca (solicitud de HITL).

Modo de uso:
  - Vía pytest mostrando stdout en tiempo real:
      pytest -s tests/evals/regression/test_dedup_recovery_llm_eval.py
  - Como script ejecutable directo:
      python -m tests.evals.regression.test_dedup_recovery_llm_eval
"""

from __future__ import annotations

import os
import sys
from typing import Any
import pytest

# Asegurar importación de core desde la raíz
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.agent.dedup_recovery.agent import invoke_recovery_agent
from core.agent.dedup_recovery.models import RecoveryDecision
from core.memory.episodic_memory.models import (
    Episode,
    EpisodeMetadata,
    EpisodePayload,
    EpisodeQueryResult,
)


# ---------------------------------------------------------------------------
# Utilidad de Visualización Semántica (Ground Truth vs Modelo)
# ---------------------------------------------------------------------------

def render_comparison_report(
    scenario_title: str,
    error_msg: str,
    source_name: str,
    episodes: list[EpisodeQueryResult],
    ground_truth: dict[str, Any],
    decision: RecoveryDecision,
) -> None:
    """Muestra en consola un reporte visual detallado y estructurado."""
    border = "═" * 84
    sub_border = "─" * 84

    print(f"\n{border}")
    print(f"║ EVALUACIÓN EXPERIMENTAL: {scenario_title.upper()}")
    print(f"{border}")

    # 1. Entrada
    print("\n► [1. ENTRADA Y CONTEXTO DEL ERROR]:")
    print(f"  • Mensaje de error  : {error_msg}")
    print(f"  • Fuente / Origen   : {source_name}")
    if episodes:
        print(f"  • Memoria Inyectada : {len(episodes)} caso(s) previo(s):")
        for i, ep_res in enumerate(episodes, 1):
            ep = ep_res.episode
            print(f"    - Caso #{i} (Similitud: {ep_res.similarity_score * 100:.1f}%):")
            print(f"        Problema       : {ep.payload.error_summary}")
            print(f"        Muestra        : '{ep.payload.culprit_sample}'")
            print(f"        Estrategia     : {ep.payload.transformation_strategy}")
            print(f"        Solución previa: {ep.payload.solution_applied}")
    else:
        print("  • Memoria Inyectada : (Ninguna - Resolución from scratch)")

    # 2. Ground Truth
    print("\n► [2. GROUND TRUTH ESPERADO (REFERENCIA HUMANA)]:")
    print(f"  • Estrategias válidas : {ground_truth.get('valid_strategies')}")
    print(f"  • Columna objetivo    : {ground_truth.get('target_column')}")
    print(f"  • Reglas esperadas    : {ground_truth.get('expected_rules')}")
    print(f"  • Confianza esperada  : {ground_truth.get('expected_confidence')}")
    print(f"  • Ambigüedad esperada : {ground_truth.get('expected_ambiguous')}")
    print(f"  • Criterio semántico  : {ground_truth.get('semantic_rationale')}")

    # 3. Salida Real del Modelo
    print("\n► [3. SALIDA REAL GENERADA POR EL LLM]:")
    print(f"  • Estrategia elegida  : {decision.strategy}")
    print(f"  • Columna objetivo    : {decision.target_column}")
    print(f"  • Reglas de mapeo     : {decision.mapping_rules}")
    print(f"  • Patrón regex        : {decision.regex_pattern or '(vacío)'}")
    print(f"  • Nivel de confianza  : {decision.confidence}")
    print(f"  • Es ambiguo          : {decision.is_ambiguous}")
    print(f"  • Es autorreparable   : {decision.is_auto_repairable}")
    print(f"  • Diagnóstico emitido : {decision.diagnosis}")
    print(f"  • Razonamiento        : {decision.reasoning}")

    # 4. Resumen Visual de Coincidencias
    strat_match = decision.strategy in ground_truth.get("valid_strategies", [])
    col_match = decision.target_column == ground_truth.get("target_column")
    conf_match = decision.confidence in ground_truth.get("expected_confidence", [])
    amb_match = decision.is_ambiguous == ground_truth.get("expected_ambiguous")

    print("\n► [4. RESUMEN VISUAL DE COINCIDENCIAS]:")
    print(f"  [{'✓' if strat_match else '✗'}] Estrategia  : {decision.strategy} ∈ {ground_truth.get('valid_strategies')}")
    print(f"  [{'✓' if col_match else '✗'}] Columna     : '{decision.target_column}' == '{ground_truth.get('target_column')}'")
    print(f"  [{'✓' if conf_match else '✗'}] Confianza   : '{decision.confidence}' ∈ {ground_truth.get('expected_confidence')}")
    print(f"  [{'✓' if amb_match else '✗'}] Ambigüedad   : {decision.is_ambiguous} == {ground_truth.get('expected_ambiguous')}")
    print(f"{border}\n")


# ---------------------------------------------------------------------------
# Fixtures de Episodios para Evaluación
# ---------------------------------------------------------------------------

@pytest.fixture
def relevant_episode() -> EpisodeQueryResult:
    """Caso previo análogo relevante: mapeo de mes en inglés."""
    ep = Episode(
        id="ep_date_english_relevant",
        metadata=EpisodeMetadata(
            tool="deduplicator",
            source_name="unlp_doaj",
            error_class="ValueError",
            affected_column="date",
            resolved_by="autonomous",
        ),
        embedding_content="Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'",
        payload=EpisodePayload(
            error_summary="Columna date con nombre de mes en texto en inglés.",
            culprit_sample="1999, June",
            transformation_strategy="value_mapping",
            solution_applied={"June": "06", "July": "07"},
            reasoning="Se extrae el año y se mapea el mes textual a su representación numérica ISO.",
        ),
    )
    return EpisodeQueryResult(episode=ep, similarity_score=0.92)


@pytest.fixture
def distractor_episode() -> EpisodeQueryResult:
    """Caso previo distractivo / no aplicable: mojibake en nombres de autor."""
    ep = Episode(
        id="ep_author_encoding_distractor",
        metadata=EpisodeMetadata(
            tool="deduplicator",
            source_name="scopus",
            error_class="UnicodeDecodeError",
            affected_column="author",
            resolved_by="hitl",
        ),
        embedding_content="Error de decodificación en autores: Caracteres corruptos UTF-8",
        payload=EpisodePayload(
            error_summary="Caracteres especiales corruptos en columna author.",
            culprit_sample="PÃ©rez",
            transformation_strategy="value_mapping",
            solution_applied={"PÃ©rez": "Pérez"},
            reasoning="Reemplazo de artefactos mojibake en nombres de autores.",
        ),
    )
    return EpisodeQueryResult(episode=ep, similarity_score=0.48)


# ---------------------------------------------------------------------------
# Escenarios de Evaluación con LLM Real
# ---------------------------------------------------------------------------

@pytest.mark.llm_eval
def test_eval_llm_scenario_1_without_memory():
    """
    Escenario 1: Resolución from scratch (Zero-Shot).
    El LLM debe deducir la corrección de fechas sin asistencia histórica.
    """
    error_msg = "Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'"
    source_name = "unlp_doaj"
    csv_path = "/tmp/eval_scenario_1.csv"

    ground_truth = {
        "valid_strategies": ["date_normalization", "value_mapping"],
        "target_column": "date",
        "expected_rules": "{'June': '06'} o normalización estándar de fechas",
        "expected_confidence": ["high", "medium"],
        "expected_ambiguous": False,
        "semantic_rationale": (
            "El modelo debe deducir que 'June' es el nombre textual del mes que bloquea el "
            "parseo numérico y proponer normalizarlo a formato fecha canónico o mapearlo numéricamente."
        ),
    }

    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        episodes=[],
        llm=None,
    )

    render_comparison_report(
        scenario_title="Escenario 1: Sin Memoria (Resolución From Scratch)",
        error_msg=error_msg,
        source_name=source_name,
        episodes=[],
        ground_truth=ground_truth,
        decision=decision,
    )

    # Validaciones semánticas mínimas para asegurar salud de la respuesta
    assert decision.target_column == "date"
    assert decision.strategy in ground_truth["valid_strategies"]


@pytest.mark.llm_eval
def test_eval_llm_scenario_2_with_relevant_memory(relevant_episode: EpisodeQueryResult):
    """
    Escenario 2: Transferencia Positiva con Memoria Relevante.
    El LLM debe aprovechar la regla exitosa documentada en el caso análogo.
    """
    error_msg = "Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'"
    source_name = "unlp_doaj"
    csv_path = "/tmp/eval_scenario_2.csv"
    episodes = [relevant_episode]

    ground_truth = {
        "valid_strategies": ["value_mapping", "date_normalization"],
        "target_column": "date",
        "expected_rules": "Mapeo que contenga 'June' -> '06'",
        "expected_confidence": ["high"],
        "expected_ambiguous": False,
        "semantic_rationale": (
            "El modelo debe reconocer la analogía con el Caso Previo #1 y adoptar "
            "la solución probada ('value_mapping' con 'June' -> '06') con alta certeza."
        ),
    }

    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        episodes=episodes,
        llm=None,
    )

    render_comparison_report(
        scenario_title="Escenario 2: Memoria Relevante (Transferencia Positiva)",
        error_msg=error_msg,
        source_name=source_name,
        episodes=episodes,
        ground_truth=ground_truth,
        decision=decision,
    )

    assert decision.target_column == "date"
    assert decision.confidence == "high"
    if decision.strategy == "value_mapping":
        assert decision.mapping_rules.get("June") == "06" or "06" in str(decision.mapping_rules)


@pytest.mark.llm_eval
def test_eval_llm_scenario_3_with_distractor_memory(distractor_episode: EpisodeQueryResult):
    """
    Escenario 3: Robustez ante Memoria Distractora.
    El LLM debe ignorar el caso irrelevante (mojibake en autor) y concentrarse en la fecha.
    """
    error_msg = "Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'"
    source_name = "unlp_doaj"
    csv_path = "/tmp/eval_scenario_3.csv"
    episodes = [distractor_episode]

    ground_truth = {
        "valid_strategies": ["date_normalization", "value_mapping"],
        "target_column": "date",
        "expected_rules": "Reglas para fecha (NO aplicar cambios sobre la columna 'author')",
        "expected_confidence": ["high", "medium"],
        "expected_ambiguous": False,
        "semantic_rationale": (
            "El modelo debe discernir que el Caso Previo #1 (encoding en autores) es irrelevante "
            "para una falla de fechas, descartarlo y enfocarse en normalizar 'date'."
        ),
    }

    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        episodes=episodes,
        llm=None,
    )

    render_comparison_report(
        scenario_title="Escenario 3: Memoria Distractora (Evaluación de Robustez)",
        error_msg=error_msg,
        source_name=source_name,
        episodes=episodes,
        ground_truth=ground_truth,
        decision=decision,
    )

    assert decision.target_column == "date"
    assert decision.target_column != "author"
    assert "author" not in decision.mapping_rules


@pytest.mark.llm_eval
def test_eval_llm_scenario_4_ambiguous_case():
    """
    Escenario 4: Calibración ante Ambigüedad Intrínseca.
    El LLM debe reconocer la falta de certeza y no forzar una autorreparación arbitraria.
    """
    error_msg = "Error al normalizar fecha: valor '1999-2005' no corresponde a un único año de publicación."
    source_name = "crossref"
    csv_path = "/tmp/eval_scenario_4.csv"

    ground_truth = {
        "valid_strategies": ["manual_review", "regex_extraction"],
        "target_column": "date",
        "expected_rules": "Si extrae regex, debe marcar ambigüedad por pérdida de rango temporal",
        "expected_confidence": ["low", "medium"],
        "expected_ambiguous": True,
        "semantic_rationale": (
            "Al haber un rango de años ('1999-2005'), elegir uno arbitrariamente causaría "
            "pérdida de datos. El modelo debe marcar is_ambiguous=True o delegar a manual_review."
        ),
    }

    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        episodes=[],
        llm=None,
    )

    render_comparison_report(
        scenario_title="Escenario 4: Caso Ambiguo (Calibración y Solicitud HITL)",
        error_msg=error_msg,
        source_name=source_name,
        episodes=[],
        ground_truth=ground_truth,
        decision=decision,
    )

    assert decision.target_column == "date"
    # Debe requerir revisión humana o admitir ambigüedad
    assert decision.is_ambiguous is True or decision.strategy == "manual_review" or decision.confidence != "high"


# ---------------------------------------------------------------------------
# Ejecución directa como script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "#" * 84)
    print("# INICIANDO SUITE DE EVALUACIÓN SEMÁNTICA CON LLM REAL")
    print("#" * 84)

    ep_rel = EpisodeQueryResult(
        episode=Episode(
            id="ep_rel",
            metadata=EpisodeMetadata(
                tool="deduplicator",
                source_name="unlp_doaj",
                error_class="ValueError",
                affected_column="date",
                resolved_by="autonomous",
            ),
            embedding_content="Error en fechas",
            payload=EpisodePayload(
                error_summary="Columna date con nombre de mes en texto en inglés.",
                culprit_sample="1999, June",
                transformation_strategy="value_mapping",
                solution_applied={"June": "06", "July": "07"},
                reasoning="Se mapea el mes textual a su formato ISO.",
            ),
        ),
        similarity_score=0.92,
    )

    ep_dist = EpisodeQueryResult(
        episode=Episode(
            id="ep_dist",
            metadata=EpisodeMetadata(
                tool="deduplicator",
                source_name="scopus",
                error_class="UnicodeDecodeError",
                affected_column="author",
                resolved_by="hitl",
            ),
            embedding_content="Error de decodificación en autores",
            payload=EpisodePayload(
                error_summary="Caracteres especiales corruptos en columna author.",
                culprit_sample="PÃ©rez",
                transformation_strategy="value_mapping",
                solution_applied={"PÃ©rez": "Pérez"},
                reasoning="Reemplazo de artefactos mojibake.",
            ),
        ),
        similarity_score=0.48,
    )

    try:
        test_eval_llm_scenario_1_without_memory()
        test_eval_llm_scenario_2_with_relevant_memory(ep_rel)
        test_eval_llm_scenario_3_with_distractor_memory(ep_dist)
        test_eval_llm_scenario_4_ambiguous_case()
        print("\n[OK] Evaluación completada exitosamente.")
    except Exception as exc:
        print(f"\n[ERROR] Falló la evaluación con el LLM: {exc}")

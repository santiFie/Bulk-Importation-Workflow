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
from typing import Any, Callable
import pytest
from langsmith import testing as t

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
from core.utils.config import config


# ---------------------------------------------------------------------------
# Helpers de Registro Seguro en LangSmith
# ---------------------------------------------------------------------------

def _safe_log_inputs(inputs: dict[str, Any]) -> None:
    """Registra entradas en LangSmith de forma segura si el contexto de prueba está activo."""
    try:
        t.log_inputs(inputs)
    except Exception:
        pass


def _safe_log_reference_outputs(reference: dict[str, Any]) -> None:
    """Registra salidas de referencia esperadas en LangSmith de forma segura."""
    try:
        t.log_reference_outputs(reference)
    except Exception:
        pass


def _safe_log_outputs(outputs: dict[str, Any]) -> None:
    """Registra las salidas reales del modelo en LangSmith de forma segura."""
    try:
        t.log_outputs(outputs)
    except Exception:
        pass


def _safe_log_feedback(
    key: str,
    score: float | None = None,
    value: Any = None,
    comment: str | None = None,
) -> None:
    """Registra métricas de feedback en LangSmith de forma segura."""
    try:
        t.log_feedback(key=key, score=score, value=value, comment=comment)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fixture de Verificación de LLM Real
# ---------------------------------------------------------------------------

@pytest.fixture
def require_real_llm():
    """Exige un LLM real configurado; sin credenciales se omite el test (sin mocks)."""
    if not (
        config.GROQ_API_KEY
        or config.NVIDIA_API_KEY
        or config.OPEN_ROUTER_API_KEY
        or config.GEMINI_API_KEY
    ):
        pytest.skip("No hay API key de LLM configurada para ejecutar la evaluación semántica.")


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
    expected_auto = ground_truth.get("expected_auto_repairable", not ground_truth.get("expected_hitl", False))
    auto_match = decision.is_auto_repairable == expected_auto

    print("\n► [4. RESUMEN VISUAL DE COINCIDENCIAS]:")
    print(f"  [{'✓' if strat_match else '✗'}] Estrategia     : {decision.strategy} ∈ {ground_truth.get('valid_strategies')}")
    print(f"  [{'✓' if col_match else '✗'}] Columna        : '{decision.target_column}' == '{ground_truth.get('target_column')}'")
    print(f"  [{'✓' if conf_match else '✗'}] Confianza      : '{decision.confidence}' ∈ {ground_truth.get('expected_confidence')}")
    print(f"  [{'✓' if amb_match else '✗'}] Ambigüedad      : {decision.is_ambiguous} == {ground_truth.get('expected_ambiguous')}")
    print(f"  [{'✓' if auto_match else '✗'}] Autorreparable : {decision.is_auto_repairable} == {expected_auto}")
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
# Runner común para la evaluación de escenarios (DRY)
# ---------------------------------------------------------------------------

def _execute_and_evaluate_scenario(
    scenario_id: str,
    scenario_title: str,
    description: str,
    error_msg: str,
    source_name: str,
    csv_path: str,
    tags: list[str],
    ground_truth: dict[str, Any],
    episodes: list[EpisodeQueryResult] | None = None,
    extra_feedback_fn: Callable[[Any], list[dict[str, Any]]] | None = None,
    extra_assertions: Callable[[Any], None] | None = None,
) -> None:
    """
    Ejecuta el agente de recuperación y consolida el registro de entradas,
    salidas, métricas y aserciones en LangSmith para evitar duplicación de código.
    """
    active_episodes = episodes or []
    memory_type = (
        "none"
        if not active_episodes
        else ("relevant" if len(active_episodes) == 1 and active_episodes[0].similarity_score > 0.7 else "distractor")
    )

    # 1. Registrar inputs del test en LangSmith
    _safe_log_inputs({
        "scenario": scenario_id,
        "description": description,
        "error_msg": error_msg,
        "source_name": source_name,
        "csv_path": csv_path,
        "memory_type": memory_type,
        "episodes_count": len(active_episodes),
        "tags": tags,
    })

    # 2. Registrar salidas esperadas de referencia (Ground Truth) en LangSmith
    ref_outputs = dict(ground_truth)
    ref_outputs["description"] = description
    _safe_log_reference_outputs(ref_outputs)

    # 3. Invocar al agente bajo prueba
    decision = invoke_recovery_agent(
        error_msg=error_msg,
        source_name=source_name,
        csv_path=csv_path,
        episodes=active_episodes,
        llm=None,
    )

    # 4. Mostrar reporte visual detallado en stdout
    render_comparison_report(
        scenario_title=scenario_title,
        error_msg=error_msg,
        source_name=source_name,
        episodes=active_episodes,
        ground_truth=ground_truth,
        decision=decision,
    )

    # 5. Registrar outputs producidos por el modelo en LangSmith
    _safe_log_outputs({
        "description": description,
        "actual_decision": {
            "strategy": decision.strategy,
            "target_column": decision.target_column,
            "confidence": decision.confidence,
            "is_ambiguous": decision.is_ambiguous,
            "is_auto_repairable": decision.is_auto_repairable,
            "mapping_rules": decision.mapping_rules,
            "regex_pattern": decision.regex_pattern,
        },
        "ground_truth": ground_truth,
        "diagnosis": decision.diagnosis,
        "reasoning": decision.reasoning,
        "decision": decision.model_dump(),
    })

    # 6. Calcular métricas estándar
    strategy_match = decision.strategy in ground_truth.get("valid_strategies", [])
    conf_match = decision.confidence in ground_truth.get("expected_confidence", [])
    col_match = decision.target_column == ground_truth.get("target_column")
    amb_match = decision.is_ambiguous == ground_truth.get("expected_ambiguous")

    expected_auto = ground_truth.get("expected_auto_repairable", not ground_truth.get("expected_hitl", False))
    auto_match = bool(decision.is_auto_repairable == expected_auto)

    is_hitl = (
        decision.strategy == "manual_review"
        or decision.confidence == "low"
        or decision.is_ambiguous
        or not decision.is_auto_repairable
    )
    expected_hitl = ground_truth.get("expected_hitl", False)
    hitl_score = bool(is_hitl == expected_hitl)
    hitl_label = "correct_autonomous_resolution" if (not expected_hitl and not is_hitl) else (
        "correct_hitl_escalation" if (expected_hitl and is_hitl) else (
            "unnecessary_hitl_escalation" if is_hitl else "missed_hitl_escalation"
        )
    )

    # 7. Registrar métricas estándar en LangSmith
    _safe_log_feedback(
        key="strategy_accuracy",
        score=strategy_match,
        value=decision.strategy,
        comment=f"Obtenida: '{decision.strategy}' | Válidas: {ground_truth.get('valid_strategies')}",
    )
    _safe_log_feedback(
        key="confidence_accuracy",
        score=conf_match,
        value=decision.confidence,
        comment=f"Obtenida: '{decision.confidence}' | Esperada: {ground_truth.get('expected_confidence')}",
    )
    _safe_log_feedback(
        key="target_column_accuracy",
        score=col_match,
        value=decision.target_column,
        comment=f"Obtenida: '{decision.target_column}' | Esperada: '{ground_truth.get('target_column')}'",
    )
    _safe_log_feedback(
        key="auto_repairable_accuracy",
        score=auto_match,
        value=str(decision.is_auto_repairable),
        comment=f"Obtenida: {decision.is_auto_repairable} | Esperada: {expected_auto}",
    )
    _safe_log_feedback(
        key="hitl_escalation_accuracy",
        score=hitl_score,
        value=hitl_label,
        comment=f"Esperaba HITL: {expected_hitl} | Solicitó HITL: {is_hitl} ({hitl_label})",
    )
    _safe_log_feedback(
        key="ambiguity_detection_accuracy",
        score=amb_match,
        value=decision.is_ambiguous,
        comment=f"Obtenida: {decision.is_ambiguous} | Esperada: {ground_truth.get('expected_ambiguous')}",
    )

    # 8. Registrar métricas específicas del escenario si fueron provistas
    if extra_feedback_fn:
        extra_feedbacks = extra_feedback_fn(decision)
        for fb in extra_feedbacks:
            _safe_log_feedback(**fb)

    # 9. Validaciones y aserciones comunes
    assert decision.target_column == ground_truth["target_column"]
    assert decision.strategy in ground_truth["valid_strategies"]

    # 10. Validaciones y aserciones específicas del escenario
    if extra_assertions:
        extra_assertions(decision)


# ---------------------------------------------------------------------------
# Escenarios de Evaluación con LLM Real
# ---------------------------------------------------------------------------

@pytest.mark.eval
@pytest.mark.llm_eval
@pytest.mark.langsmith(test_suite_name="Dedup_Recovery_LLM_Evaluation")
def test_eval_llm_scenario_1_without_memory(require_real_llm):
    """
    Escenario 1: Resolución from scratch (Zero-Shot).
    El LLM debe deducir la corrección de fechas sin asistencia histórica.
    """
    description = (
        "Evaluación Zero-Shot: Deducir autónomamente corrección de mes en texto inglés "
        "('June' en fecha) sin memoria episódica previa."
    )
    ground_truth = {
        "valid_strategies": ["date_normalization", "value_mapping"],
        "target_column": "date",
        "expected_rules": "{'June': '06'} o normalización estándar de fechas",
        "expected_confidence": ["high", "medium"],
        "expected_auto_repairable": True,
        "expected_ambiguous": False,
        "expected_hitl": False,
        "semantic_rationale": (
            "El modelo debe deducir que 'June' es el nombre textual del mes que bloquea el "
            "parseo numérico y proponer normalizarlo a formato fecha canónico o mapearlo numéricamente."
        ),
    }

    def _scenario_1_feedback(decision: Any) -> list[dict[str, Any]]:
        return [
            {
                "key": "episodic_memory_handling",
                "score": True,
                "value": "zero_shot_autonomous",
                "comment": "Resolución autónoma from-scratch sin asistencia de memoria previa",
            }
        ]

    _execute_and_evaluate_scenario(
        scenario_id="scenario_1_without_memory",
        scenario_title="Escenario 1: Sin Memoria (Resolución From Scratch)",
        description=description,
        error_msg="Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'",
        source_name="unlp_doaj",
        csv_path="/tmp/eval_scenario_1.csv",
        tags=["zero_shot", "date_normalization", "autonomous_resolution"],
        ground_truth=ground_truth,
        episodes=[],
        extra_feedback_fn=_scenario_1_feedback,
    )


@pytest.mark.eval
@pytest.mark.llm_eval
@pytest.mark.langsmith(test_suite_name="Dedup_Recovery_LLM_Evaluation")
def test_eval_llm_scenario_2_with_relevant_memory(relevant_episode: EpisodeQueryResult, require_real_llm):
    """
    Escenario 2: Transferencia Positiva con Memoria Relevante.
    El LLM debe aprovechar la regla exitosa documentada en el caso análogo.
    """
    description = (
        "Evaluación de Transferencia Positiva: Reutilizar regla exitosa documentada en caso "
        "análogo ('June' -> '06') con alta certeza."
    )
    ground_truth = {
        "valid_strategies": ["value_mapping", "date_normalization"],
        "target_column": "date",
        "expected_rules": "Mapeo que contenga 'June' -> '06'",
        "expected_confidence": ["high"],
        "expected_auto_repairable": True,
        "expected_ambiguous": False,
        "expected_hitl": False,
        "semantic_rationale": (
            "El modelo debe reconocer la analogía con el Caso Previo #1 y adoptar "
            "la solución probada ('value_mapping' con 'June' -> '06') con alta certeza."
        ),
    }

    def _scenario_2_feedback(decision: Any) -> list[dict[str, Any]]:
        rule_match = (
            decision.mapping_rules.get("June") == "06" or "06" in str(decision.mapping_rules)
            if decision.strategy == "value_mapping"
            else True
        )
        return [
            {
                "key": "episodic_memory_handling",
                "score": rule_match,
                "value": "positive_transfer_applied" if rule_match else "transfer_failed",
                "comment": f"Regla transferida con éxito: {rule_match}",
            },
            {
                "key": "positive_transfer_rule_accuracy",
                "score": rule_match,
                "value": str(decision.mapping_rules),
                "comment": f"Mapeo 'June' -> '06' adoptado correctamente: {rule_match}",
            },
        ]

    def _scenario_2_assertions(decision: Any) -> None:
        assert decision.confidence == "high"
        if decision.strategy == "value_mapping":
            assert decision.mapping_rules.get("June") == "06" or "06" in str(decision.mapping_rules)

    _execute_and_evaluate_scenario(
        scenario_id="scenario_2_with_relevant_memory",
        scenario_title="Escenario 2: Memoria Relevante (Transferencia Positiva)",
        description=description,
        error_msg="Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'",
        source_name="unlp_doaj",
        csv_path="/tmp/eval_scenario_2.csv",
        tags=["positive_transfer", "analogous_memory", "autonomous_resolution"],
        ground_truth=ground_truth,
        episodes=[relevant_episode],
        extra_feedback_fn=_scenario_2_feedback,
        extra_assertions=_scenario_2_assertions,
    )


@pytest.mark.eval
@pytest.mark.llm_eval
@pytest.mark.langsmith(test_suite_name="Dedup_Recovery_LLM_Evaluation")
def test_eval_llm_scenario_3_with_distractor_memory(distractor_episode: EpisodeQueryResult, require_real_llm):
    """
    Escenario 3: Robustez ante Memoria Distractora.
    El LLM debe ignorar el caso irrelevante (mojibake en autor) y concentrarse en la fecha.
    """
    description = (
        "Evaluación de Robustez ante Distractor: Discernir e ignorar caso distractivo "
        "de codificación en autor y concentrarse en la fecha."
    )
    ground_truth = {
        "valid_strategies": ["date_normalization", "value_mapping"],
        "target_column": "date",
        "expected_rules": "Reglas para fecha (NO aplicar cambios sobre la columna 'author')",
        "expected_confidence": ["high", "medium"],
        "expected_auto_repairable": True,
        "expected_ambiguous": False,
        "expected_hitl": False,
        "semantic_rationale": (
            "El modelo debe discernir que el Caso Previo #1 (encoding en autores) es irrelevante "
            "para una falla de fechas, descartarlo y enfocarse en normalizar 'date'."
        ),
    }

    def _scenario_3_feedback(decision: Any) -> list[dict[str, Any]]:
        distractor_ignored = decision.target_column != "author" and "author" not in decision.mapping_rules
        return [
            {
                "key": "episodic_memory_handling",
                "score": distractor_ignored,
                "value": "distractor_discriminated" if distractor_ignored else "distractor_contamination",
                "comment": f"Distractor ignorado (author no contaminado): {distractor_ignored}",
            },
            {
                "key": "distractor_discrimination_accuracy",
                "score": distractor_ignored,
                "value": decision.target_column,
                "comment": f"Columna 'author' no alterada por distractor: {distractor_ignored}",
            },
        ]

    def _scenario_3_assertions(decision: Any) -> None:
        assert decision.target_column != "author"
        assert "author" not in decision.mapping_rules

    _execute_and_evaluate_scenario(
        scenario_id="scenario_3_with_distractor_memory",
        scenario_title="Escenario 3: Memoria Distractora (Evaluación de Robustez)",
        description=description,
        error_msg="Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'",
        source_name="unlp_doaj",
        csv_path="/tmp/eval_scenario_3.csv",
        tags=["distractor_robustness", "negative_memory", "autonomous_resolution"],
        ground_truth=ground_truth,
        episodes=[distractor_episode],
        extra_feedback_fn=_scenario_3_feedback,
        extra_assertions=_scenario_3_assertions,
    )


@pytest.mark.eval
@pytest.mark.llm_eval
@pytest.mark.langsmith(test_suite_name="Dedup_Recovery_LLM_Evaluation")
def test_eval_llm_scenario_4_ambiguous_case(require_real_llm):
    """
    Escenario 4: Calibración ante Ambigüedad Intrínseca.
    El LLM debe reconocer la falta de certeza y no forzar una autorreparación arbitraria.
    """
    description = (
        "Evaluación de Calibración ante Ambigüedad: Reconocer ambigüedad intrínseca en rango de "
        "años ('1999-2005') y solicitar escalamiento HITL / manual_review."
    )
    ground_truth = {
        "valid_strategies": ["manual_review"],
        "target_column": "date",
        "expected_rules": "Debe marcar ambigüedad por pérdida de rango temporal",
        "expected_confidence": ["low", "medium"],
        "expected_auto_repairable": False,
        "expected_ambiguous": True,
        "expected_hitl": True,
        "semantic_rationale": (
            "Al haber un rango de años ('1999-2005'), elegir uno arbitrariamente causaría "
            "pérdida de datos. El modelo debe marcar is_ambiguous=True y delegar a manual_review."
        ),
    }

    def _scenario_4_feedback(decision: Any) -> list[dict[str, Any]]:
        return [
            {
                "key": "episodic_memory_handling",
                "score": True,
                "value": "zero_shot_abstention",
                "comment": "Abstención ante ambigüedad intrínseca sin interferencia de memoria",
            }
        ]

    def _scenario_4_assertions(decision: Any) -> None:
        # Debe requerir revisión humana o admitir ambigüedad
        assert decision.is_ambiguous is True or decision.strategy == "manual_review" or decision.confidence != "high"

    _execute_and_evaluate_scenario(
        scenario_id="scenario_4_ambiguous_case",
        scenario_title="Escenario 4: Caso Ambiguo (Calibración y Solicitud HITL)",
        description=description,
        error_msg="Error al normalizar fecha: valor '1999-2005' no corresponde a un único año de publicación.",
        source_name="crossref",
        csv_path="/tmp/eval_scenario_4.csv",
        tags=["ambiguity_calibration", "hitl_escalation", "manual_review"],
        ground_truth=ground_truth,
        episodes=[],
        extra_feedback_fn=_scenario_4_feedback,
        extra_assertions=_scenario_4_assertions,
    )


# ---------------------------------------------------------------------------
# Ejecución directa como script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "#" * 84)
    print("# INICIANDO SUITE DE EVALUACIÓN SEMÁNTICA CON LLM REAL (LANGSMITH)")
    print("#" * 84)

    # Delegar la ejecución a pytest para activar el plugin y hooks de LangSmith
    exit_code = pytest.main(["-s", "-v", __file__])
    sys.exit(exit_code)

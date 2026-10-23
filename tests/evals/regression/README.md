# Regresión Semántica y Reubicación Canónica — `tests/evals/regression/`

[← Volver a tests/evals/](../README.md)

---

## 🎯 Estado y Reubicación Arquitectónica

Siguiendo la estandarización metodológica de `agent-eval-creator` (Issue #47), las pruebas originalmente ubicadas en `tests/evals/regression/` fueron consolidadas en sus ubicaciones canónicas correspondientes:

1. **Detección Determinista de Schema Drift (`test_crosswalk_drift.py`):**
   - **Nueva ubicación:** [`tests/unit/nodes/test_crosswalk_drift.py`](../../unit/nodes/test_crosswalk_drift.py)
   - **Motivo:** Evalúa funciones deterministas (`validate_config_against_csv`, `get_existing_config`) mediante mocks sin invocar modelos de lenguaje reales.

2. **Evaluación Semántica de Recuperación Reactiva (`DedupRecoveryAgent`):**
   - **Nueva ubicación:** [`tests/evals/agents/dedup_recovery/`](../agents/dedup_recovery/)
   - **Componentes:**
     - Dataset declarativo (8 casos): [`tests/evals/datasets/dedup_recovery/dedup_recovery_cases.json`](../datasets/dedup_recovery/dedup_recovery_cases.json)
     - Evaluadores homogéneos y condicionales: [`tests/evals/metrics/dedup_recovery_metrics.py`](../metrics/dedup_recovery_metrics.py)
     - Suite Pytest + LangSmith (métricas específicas de caso y ejecución transaccional en CSV): [`test_dedup_recovery_decisions.py`](../agents/dedup_recovery/test_dedup_recovery_decisions.py)
     - Suite LangSmith Nativo (`aevaluate` con métricas 100% homogéneas sin `"No Feedback"`): [`evaluate_dedup_recovery.py`](../agents/dedup_recovery/evaluate_dedup_recovery.py)

---

## 🚀 Comandos Actualizados

```bash
# 1. Ejecutar tests unitarios de detección de drift de esquemas
.venv/bin/pytest tests/unit/nodes/test_crosswalk_drift.py -v

# 2. Ejecutar suite Pytest + LangSmith del Agente de Recuperación Reactiva
.venv/bin/pytest tests/evals/agents/dedup_recovery/test_dedup_recovery_decisions.py -v

# 3. Ejecutar benchmark homogéneo nativo en LangSmith (aevaluate)
.venv/bin/python tests/evals/agents/dedup_recovery/evaluate_dedup_recovery.py
```

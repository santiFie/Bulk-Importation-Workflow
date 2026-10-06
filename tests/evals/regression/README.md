# Regresión Semántica y Drift de Esquemas — `tests/evals/regression/`

[← Volver a tests/evals/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/evals/regression/` aloja las pruebas especializadas de regresión semántica y detección de variación de esquemas (*Schema Drift*).

Su objetivo en el proyecto de tesis es doble:
1. **Estabilidad Operativa:** Garantizar que las configuraciones de crosswalk cacheadas sigan siendo válidas ante modificaciones en las columnas de los archivos de entrada o, en su defecto, que el sistema detecte la discrepancia y regenere la configuración automáticamente.
2. **Experimentación Científica de Tesis:** Evaluar cuantitativa y cualitativamente el impacto del sistema de Memoria Episódica (CBR) en el rendimiento del Agente de Recuperación Reactiva ante incidentes reales de deduplicación.

---

## 🔍 Qué se Evalúa

La suite incluye dos componentes de referencia:

### 1. Detección de Schema Drift (`test_crosswalk_drift.py`)
Evalúa los componentes de validación en `core/nodes/source_to_generic/helpers.py`:
- `validate_config_against_csv`: Comparación estricta entre las columnas esperadas por una configuración JSON de crosswalk existente y las cabeceras reales del nuevo CSV.
- `DriftReport`: Generación de un reporte estructurado de divergencias:
  - Columnas faltantes (potencial fallo de importación).
  - Columnas nuevas no mapeadas.
  - Discrepancias en delimitadores detectados.
- **Lógica de Decisión de Reutilización:**
  - Si no hay drift crítico, el nodo reutiliza la configuración previa sin costo de LLM ni latencia.
  - Si se detecta drift crítico, el nodo invalida la caché e invoca al agente de generación de crosswalk.

### 2. Evaluación Semántica de Recuperación con LLM Real (`test_dedup_recovery_llm_eval.py`)
Ejecuta la suite experimental de evaluación sobre el `DedupRecoveryAgent` contrastando la inferencia en tiempo real contra un Ground Truth riguroso bajo 4 escenarios experimentales:
1. **Deducción Autónoma (*From Scratch*):** Evaluación de la capacidad del modelo para inferir la estrategia correcta sin episodios previos en memoria.
2. **Transferencia Positiva (*Analogous Episodes*):** Medición de la mejora en velocidad y certeza cuando se inyectan en el prompt episodios previos con errores análogos resueltos.
3. **Discriminación y Robustez (*Distractor Episodes*):** Comprobación de que el modelo no aplique estrategias erróneas sugeridas por episodios en memoria que pertenecen a herramientas o contextos dispares.
4. **Calibración de Abstención (*HITL Escalation*):** Validación de que el modelo reconozca la ambigüedad irreducible y elija deliberadamente solicitar intervención humana (`RecoveryDecision(action="request_hitl")`).

---

## 🚀 Cómo Ejecutar los Tests

```bash
# 1. Ejecutar la detección de drift de esquemas (rápido, determinista con mocks)
pytest tests/evals/regression/test_crosswalk_drift.py -v

# 2. Ejecutar la suite de evaluación de LLM en tiempo real mostrando logs y salida formateada
pytest tests/evals/regression/test_dedup_recovery_llm_eval.py -s -v

# 3. Ejecutar como script independiente de terminal para recolección de resultados de tesis
python -m tests.evals.regression.test_dedup_recovery_llm_eval
```

---

## 📦 Dependencias y Costos

- **Marcadores de Pytest:** Los tests con inferencia real están etiquetados con `@pytest.mark.llm_eval` y `@pytest.mark.eval`.
- **Variables de Entorno:** Requiere `GROQ_API_KEY` o `OPENROUTER_API_KEY` configuradas.
- **Tiempo Estimado:** ~1 a 3 minutos para la suite completa de inferencia.
- **Costo en Tokens:** ~$0.02 a $0.06 por corrida de evaluación.

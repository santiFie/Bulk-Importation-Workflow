# Evaluación de Agentes Individuales — `tests/evals/single_agent/`

[← Volver a tests/evals/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/evals/single_agent/` contiene las suites de evaluación atómica diseñadas para medir las capacidades intrínsecas de cada agente de IA del sistema de forma aislada, sin acoplamiento con la orquestación completa de LangGraph:
- `MetadataCuratorAgent` (`core/agent/metadata_curator_agent.py`): Agente de curación semántica de anomalías en PDFs.
- `SourceCrosswalkAgent` (`core/nodes/source_to_generic/`): Agente de inferencia de configuraciones de crosswalk a partir del encabezado de fuentes externas.
- `DedupRecoveryAgent` (`core/agent/dedup_recovery/agent.py`): Agente de resolución reactiva ante errores de formato en deduplicación.

El propósito fundamental es medir la **precisión semántica**, la **estabilidad de formato** y la **resistencia a alucinaciones** bajo condiciones controladas de inferencia.

---

## 🔍 Qué se Evalúa

1. **Adherencia a Esquemas Estructurados (Schema Compliance):**
   - Garantía de que el 100% de las respuestas generadas por los LLMs respeten los contratos Pydantic (`RecoveryDecision`, `CrosswalkConfig`) y no emitan Markdown conversational o JSON truncado.
2. **Control de Falsos Positivos de Curación:**
   - Se alimenta al `MetadataCuratorAgent` con registros deliberadamente limpios para verificar que **se abstenga de modificar texto válido** (tasa de sobrecorrección o *over-curation* < 1%).
3. **Inferencia de Mapeo sin Alucinaciones:**
   - Evaluación del `SourceCrosswalkAgent` sobre tablas con columnas ambiguas (e.g., `"vol"`, `"no"`, `"yr"`):
     - Mapeo correcto a campos canónicos (`volume`, `issue`, `date`).
     - No invención de campos requeridos que no existen en el archivo fuente.
4. **Calibración de Certeza y Abstención:**
   - Comprobación de que el `DedupRecoveryAgent` solicite escalamiento humano (HITL) en lugar de inventar valores cuando los datos están irremediablemente truncados o corruptos.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las evaluaciones de agentes individuales
pytest tests/evals/single_agent/ -v

# Ejecutar con medición de tiempos de latencia por inferencia
pytest tests/evals/single_agent/ -v -s --durations=10
```

---

## 📦 Dependencias y Costos

- **Modelos Evaluados:** Llama-3.1-8B-Instant (Groq) para pruebas rápidas; DeepSeek-V3 / Llama-3.3-70B (OpenRouter) para validación de alta precisión.
- **Tiempo Estimado:** ~30 a 90 segundos por corrida de suite.
- **Costo en Tokens:** ~$0.02 a $0.05 por sesión de prueba.

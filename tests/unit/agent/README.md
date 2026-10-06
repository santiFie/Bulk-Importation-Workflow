# Pruebas Unitarias del Agente de Recuperación Reactiva — `tests/unit/agent/`

[← Volver a tests/unit/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/agent/` alberga las pruebas unitarias del subsistema de autorreparación reactiva del pipeline (`core/agent/dedup_recovery/`). 

Cuando la etapa de deduplicación o transformación encuentra errores de validación de datos (por ejemplo, discrepancias de formato en fechas, columnas no coincidentes o valores corruptos), este agente entra en acción de forma reactiva para:
1. Analizar el error contextual apoyado en memoria episódica.
2. Formular un plan de acción tipado (`RecoveryDecision`).
3. Ejecutar estrategias de corrección mediante herramientas atómicas controladas.
4. Si la solución falla o es ambigua, abortar con rollback automático y escalar al operador humano (HITL - *Human-in-the-Loop*).

---

## 🔍 Qué se Evalúa

La suite unitaria se divide en cuatro frentes técnicos:

### 1. Orquestación y Decisión del Agente (`test_dedup_recovery_agent_eval.py`)
- **Composición de Prompts:** Formateo determinista de episodios previos para inyección *Few-Shot* en `format_episodes_for_prompt` y construcción del System Prompt.
- **Invocación Estructurada:** Parseo y validación del esquema Pydantic de salida (`RecoveryDecision`, `StrategyResult`).
- **Nodo LangGraph (`dedup_recovery_node`):**
  - Camino de reparación autónoma exitosa (commit transaccional y re-enrutamiento al deduplicador).
  - Camino de fallo de validación post-reparación: ejecución de rollback atómico y desvío a interrupción humana.
  - Camino de ambigüedad intrínseca: derivación directa a `interrupt()` de LangGraph para intervención de curador.
  - Registro y despacho de acciones de usuario a través del `ActionHandlerRegistry`.

### 2. Estrategias Polimórficas de Recuperación (`test_dedup_recovery_strategies.py`)
- Implementación del patrón *Strategy* y *Null Object*:
  - `DateNormalizationStrategy`: Normalización de fechas heterogéneas a formato ISO (`YYYY-MM-DD` o `YYYY`).
  - `ValueMappingStrategy`: Mapeo explícito de valores mediante diccionarios.
  - `RegexExtractionStrategy`: Extracción de patrones mediante regex.
  - `NullRecoveryStrategy`: Estrategia neutral para operaciones no soportadas.
  - `StrategyRegistry`: Registro centralizado y resolución dinâmica de estrategias por identificador.

### 3. Herramientas Seguras de Transformación (`test_dedup_recovery_tools.py`)
- Funciones atómicas de modificación sobre DataFrames/CSVs:
  - `apply_value_mapping`: Reemplazo seguro de valores por diccionario.
  - `apply_regex_extraction`: Aplicación de grupos de captura regex sobre columnas objetivo.
  - `apply_date_normalization`: Normalización masiva de series temporales.
  - `validate_csv_structure`: Verificación de consistencia del CSV resultante tras aplicar modificaciones.

### 4. Transaccionalidad Atómica de Archivos (`test_dedup_recovery_transaction.py`)
- Gestor de contexto `CsvTransaction`:
  - Creación inmediata de backup temporal en disco antes de cualquier mutación.
  - Confirmación (`commit`): Eliminación segura del backup tras verificación exitosa.
  - Reversión (`rollback`): Restauración inmediata del CSV original si ocurre cualquier error durante la modificación, impidiendo la corrupción de datasets de trabajo.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas unitarias del agente de recuperación
pytest tests/unit/agent/ -v

# Ejecutar únicamente las pruebas del gestor transaccional
pytest tests/unit/agent/test_dedup_recovery_transaction.py -v

# Ejecutar las pruebas de estrategias polimórficas
pytest tests/unit/agent/test_dedup_recovery_strategies.py -v
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (todas las interacciones de modelo están interceptadas con mocks).
- **Entorno de Trabajo:** Utiliza directorios temporales `tmp_path` de pytest para aislar escrituras CSV.
- **Tiempo Estimado de Ejecución:** < 3 segundos.
- **Costo en Tokens:** **$0.00**.

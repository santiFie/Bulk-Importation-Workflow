# Inventario de Tests — `tests/`

Análisis de todos los archivos de test del proyecto para ordenarlos y decidir cuáles conservar, unificar o eliminar.

---

## Mapa de la carpeta `tests/`

```
tests/
├── data/                          ← Datos de prueba (CSVs, configs)
├── docs/                          ← Documentación (este archivo)
├── studio/                        ← Scripts de ejecución manual (NO son pytest)
│   ├── run_crosswalk_agent.py
│   └── run_pipeline.py
├── test_deduplicator.py           ← Tests unitarios del motor de crosswalk
├── test_graph_steps.py            ← Tests de integración de cada nodo del grafo
├── test_pipeline_evaluation.py    ← Evaluador del pipeline integrado con LangSmith
└── test_source_config_generator.py ← Evaluador del agente de crosswalk con LangSmith
```

---

## 1. Scripts de ejecución manual (`studio/`)

> **No son tests de pytest.** Son scripts que se ejecutan con `python` directamente para hacer smoke tests manuales y observar trazas en LangSmith.

### `studio/run_crosswalk_agent.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Script manual (no pytest) |
| **Cómo ejecutar** | `python tests/studio/run_crosswalk_agent.py` |
| **Qué hace** | Invoca únicamente `generate_source_crosswalk_config` (Paso 1) de forma aislada sobre `SearchResults.csv` (Springer). Imprime en consola los mapeos generados y la configuración del separador. Registra la traza en LangSmith. |
| **Qué testea** | Smoke test del agente LLM de generación de crosswalk config. Útil para iterar sobre el prompt o la lógica del nodo sin ejecutar el pipeline entero. |
| **Dependencias externas** | Groq API (LLM), LangSmith (tracing). |
| **Veredicto** | ✅ **Conservar** — es el único punto de entrada rápido para depurar el Paso 1. |

---

### `studio/run_pipeline.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Script manual (no pytest) |
| **Cómo ejecutar** | `python tests/studio/run_pipeline.py [--stop-after PASO]` |
| **Qué hace** | Ejecuta el pipeline completo (o hasta un paso indicado) sobre `SearchResults.csv` + `export_10915_all.csv`. Usa `_FakeDeduplicatorClient` (mock sin red). Muestra en consola un resumen por paso con tiempo, archivos generados y errores. |
| **Qué testea** | Integración end-to-end del pipeline con datos reales. Permite verificar el comportamiento tras cambios en cualquier nodo. Soporta `--stop-after` para cortar en cualquier paso. |
| **Dependencias externas** | Groq API (para Paso 1), servicio crosswalk (para Pasos 2a, 2b, 5). |
| **Veredicto** | ✅ **Conservar** — es la herramienta principal de debugging interactivo. |

---

## 2. Tests de pytest

### `test_deduplicator.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests unitarios (pytest) |
| **Clases / grupos** | `TestGetConfigPathForSource`, `TestTransformMetadataCsvWithSedici`, `TestTransformMetadataCsvWithOaidc`, `TestTransformMetadataCsvEdgeCases`, `TestRomeroToSediciCrosswalk` |
| **Nodo cubierto** | Motor de crosswalk (`Crosswalk`, `CsvHandler`, `CrosswalkContext`) — **independiente del grafo LangGraph** |
| **Qué testea** | |
| → `TestGetConfigPathForSource` | Helper que mapea nombre de fuente (`"sedici"`, `"oaidc"`, etc.) a su archivo JSON. Verifica case-insensitivity, trim de espacios, error para fuente desconocida, y que todos los archivos existan en disco. |
| → `TestTransformMetadataCsvWithSedici` | Aplica crosswalk `sedici` sobre `sedici_input.csv`. Verifica: mensaje de éxito, creación del archivo, columnas genéricas (`title`, `date`, `author`, `id`), filas con datos, campo `title` no vacío. |
| → `TestTransformMetadataCsvWithOaidc` | Aplica crosswalk `oaidc` sobre `oaidc_input.csv`. Verifica columnas específicas (`dc.title`, `dc.type`, `sedici.contributor.director`) y que el filtro `lowercase` se aplique al autor. |
| → `TestTransformMetadataCsvEdgeCases` | CSV vacío → error, CSV inexistente → error, config inexistente → error, directorio de salida inexistente → se crea, mensaje de éxito contiene la ruta. |
| → `TestRomeroToSediciCrosswalk` | Aplica crosswalk `romero_to_sedici` (Paso 5) sobre `result-14531-Romero.csv`. Verifica existencia del config, éxito, columnas SEDICI (`dc.title[es]`, `sedici.creator.person[es]`, `dc.date.issued`). |
| **Dependencias externas** | Ninguna (todo local). |
| **Problema detectado** | ⚠️ `test_sedici_transform_returns_success_message` usa una ruta absoluta hardcodeada (`/home/santi/...`) para el output, no `tmp_path`. Es el único test con side effect en disco. |
| **Veredicto** | ✅ **Conservar** — son los tests más rápidos y fundamentales. Cubren el motor de crosswalk sin red ni LLM. Arreglar el test con ruta hardcodeada. |

---

### `test_graph_steps.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests de integración (pytest) |
| **Clases / grupos** | `TestGenerateSourceCrosswalkConfig`, `TestMapSourceToGeneric`, `TestMapSediciToGeneric`, `TestDeduplicate`, `TestMetadataReconciliation`, `TestMapToSediciFormat`, `TestMetadataCorrections`, `TestGenerateSafToImport`, `TestImportToDspace`, `TestPipelineCompleto` |
| **Nodos cubiertos** | Pasos 1, 2a, 2b, 3, 4, 5, 6, 8, 9 — el pipeline **completo** vía los nodos de `core/graph.py` |
| **Mock del Deduplicador** | `FakeDeduplicatorClient` — más sofisticado que el de `run_pipeline.py`: usa `sedici.identifier.other` como ID para que el JOIN del Paso 4 sea correcto. |
| **Qué testea por clase** | |
| → `TestGenerateSourceCrosswalkConfig` | Cache del config (no sobreescribe si existe), fallback 1:1 cuando el LLM lanza error, estructura del config de fallback, error con CSV vacío, retorno correcto de la clave `source_crosswalk_config`. Incluye test E2E fallback → crosswalk → CSV procesable. |
| → `TestMapSourceToGeneric` | Genera archivo, no está vacío, contiene columna `title`, retorna `{}`. |
| → `TestMapSediciToGeneric` | Genera archivo, no está vacío, contiene `{title, date, author}`, retorna `{}`. |
| → `TestDeduplicate` | Genera archivo, no está vacío, contiene columna `total`, retorna `{}`. (con mock) |
| → `TestMetadataReconciliation` | Genera CSV reconciliado, mantiene columnas originales del fuente, contiene ítems, retorna `{}`. |
| → `TestMapToSediciFormat` | Genera CSV SEDICI-ready, contiene `{dc.title[es], sedici.creator.person[es], dc.date.issued}`, tiene datos, `dc.title[es]` no vacío, retorna `{}`. |
| → `TestMetadataCorrections` | CSV sigue existiendo, columnas no cambian, filas no cambian, no quedan separadores `|||`, retorna `{}`. |
| → `TestGenerateSafToImport` | Crea directorio SAF, crea subdirectorios `item_*`, `dublin_core.xml` existe en primer ítem, `contents` existe, XML válido con tag `<dublin_core>`, retorna `{}`. |
| → `TestImportToDspace` | Sin credenciales → retorna `{}` sin excepción. Sin SAF → retorna `{}`. Con cliente completamente mockeado → guarda mapfile correcto. |
| → `TestPipelineCompleto` | E2E de todos los pasos en secuencia, verificando que cada archivo intermedio se genere. |
| **Dependencias externas** | Servicio crosswalk (REST) para pasos 2a, 2b, 5. DSpace MCP mockeado en Paso 9. |
| **Veredicto** | ✅ **Conservar** — es la suite de integración principal. La más completa y valiosa del proyecto. |

---

### `test_pipeline_evaluation.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Evaluador integrado con LangSmith (no se ejecuta como pytest normal) |
| **Cómo ejecutar** | `python tests/test_pipeline_evaluation.py` |
| **Qué hace** | Crea un dataset `Pipeline_Integration_Tests` en LangSmith, agrega los casos de `TEST_CASES` y ejecuta el pipeline completo hasta `STOP_AFTER_STEP`. Registra los resultados como un experimento. |
| **Evaluadores heurísticos** | |
| → `no_errors_evaluator` | Score 1.0 si ningún paso lanzó excepción. |
| → `all_steps_executed_evaluator` | Score = pasos ejecutados / pasos esperados. |
| → `files_generated_evaluator` | Score = archivos generados / archivos esperados. |
| → `row_count_evaluator` | Score = CSVs con al menos 1 fila / total de CSVs generados. |
| → `generic_columns_evaluator` | Score = columnas genéricas presentes / columnas esperadas (configurable por caso). |
| → `sedici_columns_evaluator` | Score = columnas SEDICI presentes / columnas esperadas. |
| → `data_integrity_evaluator` | Score 1.0 si el conteo de filas no crece entre pasos (el pipeline solo filtra). |
| **Configuración** | `TEST_CASES`, `STOP_AFTER_STEP`, `DATASET_NAME`, `EXPERIMENT_PREFIX` — todo editable en el archivo. |
| **Dependencias externas** | LangSmith API, Groq API (Paso 1), servicio crosswalk. |
| **Veredicto** | ✅ **Conservar** — es la herramienta de evaluación formal del pipeline. Complementa a `test_graph_steps.py` con visibilidad en LangSmith y métricas comparables entre experimentos. |

---

### `test_source_config_generator.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Evaluador integrado con LangSmith (no se ejecuta como pytest normal) |
| **Cómo ejecutar** | `python tests/test_source_config_generator.py` |
| **Qué hace** | Crea un dataset `Crosswalk_Module_Tests` en LangSmith, agrega un ejemplo con `SearchResults.csv` (Springer) y ejecuta `generate_source_crosswalk_config`. Registra los resultados como experimento. |
| **Evaluadores heurísticos** | |
| → `valid_json_structure_evaluator` | Verifica que el JSON generado tenga la estructura `[mappings, settings]`. Score 0.0 o 1.0. |
| → `critical_columns_mapped_evaluator` | Score = columnas críticas mapeadas / columnas esperadas. Columnas esperadas: `{id, title, author, date, doi, citation, type}`. |
| → `separators_evaluator` | Verifica `file_delimiter`, `replace_separator` y que haya un `separator_regex` válido (Springer usa regex porque los autores no tienen separador explícito). |
| → `regex_splits_correctly_evaluator` | Aplica el regex generado contra 20 filas reales de `Authors`. Score 1.0 si ≥ 60% de filas se dividen en > 1 token. |
| **Dependencias externas** | LangSmith API, Groq API, `SearchResults.csv` en `tests/data/`. |
| **Veredicto** | ✅ **Conservar** — es el único evaluador formal del Paso 1 (agente LLM). Permite comparar experimentos entre modelos y prompts. Muy útil dado el cambio reciente de `llama-3.3-70b-openai/gpt-oss-120b` a `llama-3.1-8b-instant`. |

---

## 3. Datos de prueba (`data/`)

| Archivo | Tamaño | Usado en |
|---------|--------|----------|
| `SearchResults.csv` | 2.3 MB | Prácticamente todos los tests (CSV fuente Springer) |
| `export_10915_all.csv` | 1.2 MB | `run_pipeline.py`, `test_graph_steps.py`, `test_pipeline_evaluation.py` (CSV de SEDICI) |
| `result-14531-Romero.csv` | 32 KB | `test_deduplicator.py` (CSV fuente Romero para crosswalk) |
| `sedici_input.csv` | 1.9 KB | `test_deduplicator.py` (CSV minimal de SEDICI para tests unitarios) |
| `oaidc_input.csv` | 421 B | `test_deduplicator.py` (CSV minimal OAIDC) |
| `empty.csv` | 54 B | `test_deduplicator.py` (caso borde: CSV vacío) |
| `export.csv` | 30 KB | `test_deduplicator.py::test_sedici_transform_returns_success_message` (ruta hardcodeada — ⚠️) |
| `crosswalk_config_Romero.json` | 1.6 KB | No referenciado en ningún test activo |

---

## 4. Resumen de superposiciones y problemas

### Superposiciones entre archivos

| Tema | Archivos con overlap |
|------|---------------------|
| Mock del DeduplicatorClient | `run_pipeline.py`, `test_pipeline_evaluation.py`, `test_graph_steps.py` — cada uno tiene su propia copia del mock `_FakeDeduplicatorClient`. |
| Estado inicial del pipeline | `run_pipeline.py`, `test_pipeline_evaluation.py` — construyen el `state` de forma casi idéntica. |
| Import de `run_pipeline_until_step` | `run_pipeline.py`, `test_pipeline_evaluation.py` — ambos usan esta función pero con propósitos distintos. |

### Problemas detectados

| # | Archivo | Problema | Severidad |
|---|---------|----------|-----------|
| P1 | `test_deduplicator.py` L170 | Ruta de output hardcodeada (`/home/santi/...`) en `test_sedici_transform_returns_success_message` → escribe en disco real, no usa `tmp_path`. | 🟡 Media |
| P2 | `test_graph_steps.py` L37–38 | `SOURCE_CROSSWALK` y `SEDICI_CROSSWALK` apuntan al mismo archivo (`sedicicrosswalkconfig.json`), pero el Paso 2a debería usar el config del origen (Romero), no el de SEDICI. Puede enmascarar errores del crosswalk del origen. | 🔴 Alta |
| P3 | `test_source_config_generator.py` L11 | `MCP_SRC_PATH` apunta a `MCPs/Deduplicator MCP/src` (directorio con espacios, no coincide con la estructura actual `mcps/deduplicator_mcp/src`). | 🟡 Media |
| P4 | Mock del deduplicador | Tres copias del mismo mock en tres archivos distintos. Si cambia el formato del CSV de salida del Deduplicador, hay que actualizar en tres lugares. | 🟡 Media |

---

## 5. Veredicto final — qué conservar, unificar o eliminar

### Conservar sin cambios
- `studio/run_crosswalk_agent.py` ✅
- `studio/run_pipeline.py` ✅
- `test_graph_steps.py` ✅ (suite de integración principal)
- `test_pipeline_evaluation.py` ✅ (evaluación LangSmith del pipeline)
- `test_source_config_generator.py` ✅ (evaluación LangSmith del Paso 1)

### Conservar con correcciones
- `test_deduplicator.py` — corregir **P1**: reemplazar la ruta hardcodeada por `tmp_path` en `test_sedici_transform_returns_success_message`.

### Acciones recomendadas adicionales
1. **Corregir P2 en `test_graph_steps.py`**: `SOURCE_CROSSWALK` debería apuntar al config del repositorio origen (Romero), no al de SEDICI.
2. **Centralizar el mock del Deduplicador**: extraer `FakeDeduplicatorClient` a un archivo `tests/conftest.py` o `tests/fixtures.py` y eliminarlo de los tres archivos que lo duplican.
3. **Eliminar `data/crosswalk_config_Romero.json`** si no está referenciado en ningún test activo (o documentar su propósito).

### No hay archivos para eliminar
Todos los archivos existentes tienen un propósito diferenciado. El problema no es redundancia de archivos sino redundancia de código interno (el mock del deduplicador).

---

*Generado el 2026-07-29 analizando `tests/` del repositorio `santiFie/LangGraph`.*

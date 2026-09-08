# Inventario de Tests — `tests/`

Inventario y análisis de todos los archivos de test del proyecto, organizados en subcarpetas según su alcance (`unit/`, `integration/`, `studio/`).

---

## Mapa de la carpeta `tests/`

```text
tests/
├── data/                                 ← Datos de prueba organizados por subgrafo
│   ├── ingest/                           ← CSVs crudos (raw/) y curación de PDFs (curation/)
│   ├── crosswalk_dedup/                  ← Esquema genérico (generic_inputs/), fuentes externas (source_inputs/), SEDICI y benchmarks
│   ├── enrichment/                       ← Datasets para enriquecimiento (OpenAlex, Crossref)
│   └── export/                           ← Tablas de licencias y metadatos para exportación SAF
├── docs/                                 ← Documentación técnica de testing
├── studio/                               ← Scripts de ejecución manual interactiva (NO son pytest)
│   ├── run_crosswalk_agent.py            (opcional)
│   └── run_pipeline.py                   ← Ejecución paso a paso del pipeline
├── unit/                                 ← Tests unitarios (aislados, rápidos, sin dependencias de red obligatorias)
│   ├── enrichers/                        ← Tests unitarios de clientes de enriquecimiento (con mocks)
│   │   ├── test_crossref_enricher.py     ← Cliente Crossref
│   │   ├── test_openalex_enricher.py     ← Cliente OpenAlex
│   │   └── test_openlibrary_enricher.py  ← Cliente OpenLibrary
│   ├── test_deduplicator.py              ← Motor de crosswalk (Crosswalk, CsvHandler)
│   ├── test_heuristic_detectors.py       ← Detección heurística de anomalías en PDFs
│   └── test_text_fixers.py               ← Correctores deterministas de texto
└── integration/                          ← Tests de integración (subgrafos, nodos y suites E2E)
    ├── subgraphs/                        ← Tests de integración para subgrafos individuales
    │   ├── test_ingest_subgraph.py          ← Subgrafo IngestSubgraph (bifurcación CSV y buckets MinIO)
    │   └── test_crosswalk_dedup_subgraph.py ← Subgrafo CrosswalkDedupSubgraph (LLM y backend reales)
    ├── nodes/                            ← Tests de integración para nodos individuales
    │   ├── test_curation_node.py         ← Nodo curate_metadata_node (PDF curation)
    │   ├── test_pdf_ingest_node.py       ← Nodo pdf_ingest_node (MinIO + Metadata Extractor)
    │   └── test_enrichment_node.py       ← Nodo enrich_metadata_node y route_enrichment
    ├── test_graph_steps.py               ← Integración secuencial de nodos legacy (pendiente actualización)
    ├── test_metadata_curator_agent.py    ← Suite de 3 niveles del Agente Curador (Sintético, Dataset Eval, E2E MinIO/OCR)
    ├── test_pipeline_evaluation.py       ← Evaluación del pipeline completo en LangSmith
    └── test_source_config_generator.py   ← Evaluación del agente de crosswalk en LangSmith
```

---

## Cómo ejecutar la suite

Con el entorno virtual `.venv` activado (`source .venv/bin/activate`):

```bash
# 1. Ejecutar solo tests unitarios (rápidos, sin servicios externos)
pytest tests/unit/ -v

# 2. Ejecutar solo tests unitarios excluyendo tests de API real en enrichers
pytest tests/unit/ -v -k "not integration"

# 3. Ejecutar tests de subgrafos individuales
pytest tests/integration/subgraphs/ -v

# 4. Ejecutar tests de nodos individuales
pytest tests/integration/nodes/ -v

# 5. Ejecutar toda la suite de integración
pytest tests/integration/ -v

# 6. Ejecutar toda la suite completa
pytest tests/ -v
```

---

## 1. Scripts de ejecución manual (`studio/`)

> **No son tests de pytest.** Son scripts que se ejecutan con `python` directamente para hacer smoke tests manuales, interactivos y observar trazas en LangSmith.

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

## 2. Tests Unitarios (`unit/`)

### `unit/test_text_fixers.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests unitarios puros (pytest) |
| **Módulo testeado** | `core/utils/text_fixers.py` |
| **Clases / grupos** | `TestFixSpacedChars`, `TestRemoveCidArtifacts`, `TestDeduplicateCyclicText`, `TestFixGluedWords`, `TestNormalizarAutores`, `TestAplicarCorrectoresProgramaticos` |
| **Qué testea** | Correcciones deterministas de texto sobre cadenas y diccionarios de metadatos (espacios espurios, artefactos CID, repetición cíclica, palabras pegadas, autores). |
| **Dependencias externas** | Ninguna (local y ultra rápido). |
| **Veredicto** | ✅ **Conservar** |

---

### `unit/test_heuristic_detectors.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests unitarios puros (pytest) |
| **Módulo testeado** | `core/utils/heuristic_detectors.py` |
| **Clases / grupos** | `TestDetectarCharsDispersos`, `TestDetectarArtefactosCid`, `TestDetectarRepeticionCiclica`, `TestDetectarTextoPegado`, `TestDetectarCharsControl`, `TestDetectarLongitudAnomala`, `TestCalcularScoreAnomalia`, `TestAnalizarFila`, `TestTriarRegistros`, `TestCalcularEstadisticasLote` |
| **Qué testea** | Detección aislada de anomalías en campos de texto, cálculo de score normalizado y triaje de registros en lotes. |
| **Dependencias externas** | Ninguna. |
| **Veredicto** | ✅ **Conservar** |

---

### `unit/test_deduplicator.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests unitarios (pytest) |
| **Módulo testeado** | `core/scripts/crosswalk/` (`Crosswalk`, `CsvHandler`, `CrosswalkContext`) |
| **Clases / grupos** | `TestGetConfigPathForSource`, `TestTransformMetadataCsvWithSedici`, `TestTransformMetadataCsvWithOaidc`, `TestTransformMetadataCsvEdgeCases`, `TestRomeroToSediciCrosswalk` |
| **Qué testea** | Mapeo y transformación de CSVs de metadatos locales mediante configuraciones JSON de crosswalk sin levantar servicios web. |
| **Dependencias externas** | Ninguna (usa CSVs de `tests/data/`). |
| **Veredicto** | ✅ **Conservar** |

---

### `unit/enrichers/`

| Archivo | Módulo testeado | Qué testea | Dependencias |
|---------|-----------------|------------|--------------|
| `test_crossref_enricher.py` | `core/clients/enrichers/crossref_enricher.py` | Consulta por DOI, normalización de autores/fechas/títulos, manejo de errores y reintentos. | Mocks (tests rápidos); `@pytest.mark.integration` opcional con red. |
| `test_openalex_enricher.py` | `core/clients/enrichers/openalex_enricher.py` | Consulta por título e ISSN, extracción de metadatos primarios, parsing de respuesta JSON. | Mocks (tests rápidos); `@pytest.mark.integration` opcional con red. |
| `test_openlibrary_enricher.py` | `core/clients/enrichers/openlibrary_enricher.py` | Normalización de ISBN (ISBN-10 / ISBN-13), consulta de libros y parseo. | Mocks (tests rápidos); `@pytest.mark.integration` opcional con red. |

---

## 3. Tests de Integración (`integration/`)

### 3.1 Tests de Subgrafos (`integration/subgraphs/`)

#### `integration/subgraphs/test_crosswalk_dedup_subgraph.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests de integración de subgrafo con LLM y servicios reales (pytest) |
| **Subgrafo cubierto** | `CrosswalkDedupSubgraph` (`core/subgraphs/crosswalk_dedup.py`) |
| **Clases / grupos** | `TestCrosswalkDedupSubgraphTopology`, `TestCrosswalkDedupSubgraphE2E` |
| **Qué testea** | |
| → `TestCrosswalkDedupSubgraphTopology` | Compilación del subgrafo, presencia de todos los nodos (`GenerateSourceCrosswalkConfig`, `MapSourceToGeneric`, `BypassSourceCrosswalk`, `EnrichmentSubgraph`, `MapSediciToGeneric`, `Deduplicate`, `MetadataReconciliation`), verificación del router condicional `route_source_crosswalk` y del nodo puente `bypass_source_crosswalk`. |
| → `test_flujo_completo_rama_csv_con_llm_y_servicios_reales` | Ejecución E2E del subgrafo en rama CSV usando el **agente LLM real** (`FallbackLLM`), la API REST de **Crosswalk** en Docker, la API REST del **Deduplicador** y reconciliación final. |
| → `test_flujo_rama_pdf_minio_bypass_crosswalk` | Ejecución del subgrafo en rama PDF/MinIO con `BypassSourceCrosswalk`, MapSedici, Deduplicador real y Reconciliación. |
| **Dependencias externas** | API Key LLM (Groq / Nvidia), backend Docker `deduplicator_crosswalk_web` (`http://localhost:8000`). |
| **Veredicto** | ✅ **Conservar** — suite principal de validación del subgrafo de crosswalk y deduplicación. |

---

### 3.2 Tests de Nodos Individuales (`integration/nodes/`)

#### `integration/nodes/test_curation_node.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Test de integración y funcionalidad de nodo |
| **Nodo cubierto** | `curate_metadata_node` (`core/nodes/curation_nodes.py`) |
| **Qué testea** | Flujo de curación sobre CSV extraído de PDFs (`/tmp/source_from_pdfs.csv`), aplicación de correctores en lote, triaje de anomalías, integración con agente LLM (mockeado o real) y flujo E2E Ingest → Curation. |
| **Dependencias externas** | Requiere CSV de PDFs generado por `test_pdf_ingest_node.py` o servicios MinIO/Extractor para la prueba E2E completa (se salta si no están). |
| **Veredicto** | ✅ **Conservar** |

---

#### `integration/nodes/test_pdf_ingest_node.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Test de integración real con servicios |
| **Nodo cubierto** | `pdf_ingest_node` (`core/nodes/ingest_nodes.py`) |
| **Qué testea** | Conexión e ingesta desde MinIO (bucket `importacion`), invocación del MCP Metadata Extractor, extracción de textos/metadatos y generación de `source_from_pdfs.csv`. |
| **Dependencias externas** | MinIO (`localhost:9003`), Metadata Extractor MCP (`http://localhost:9604/mcp`), Docker (`aistor`), Groq API Key. Se salta automáticamente si la infra no está disponible. |
| **Veredicto** | ✅ **Conservar** |

---

#### `integration/nodes/test_enrichment_node.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Test de integración / funcionalidad de nodo |
| **Nodo cubierto** | `enrich_metadata_node` y `route_enrichment` (`core/nodes/enrichment_nodes.py`) |
| **Qué testea** | Enrutador condicional de enriquecimiento y completado de metadatos in-place sobre el CSV genérico (`generic_source_csv_path`). |
| **Dependencias externas** | Mocks de clientes de enriquecimiento (aislado, rápido). |
| **Veredicto** | ✅ **Conservar** |

---

### 3.3 Evaluaciones y Suites E2E / LangSmith (`integration/`)

#### `integration/test_graph_steps.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests de integración secuencial (pytest) |
| **Nodos cubiertos** | Pasos 1, 2a, 2b, 3, 4, 5, 6, 8, 9 — pipeline completo vía los nodos de `core/graph.py` |
| **Mock del Deduplicador** | `FakeDeduplicatorClient` (mock con join por ID). |
| **Estado actual** | ⚠️ **Pendiente de actualización:** Suite legacy previa a la modularización en subgrafos. Se conserva para refactorización futura. |
| **Veredicto** | 🔄 **Conservar (Pendiente de refactor)** |

---

#### `integration/test_metadata_curator_agent.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Suite híbrida de 3 niveles: Tests programáticos sintéticos, evaluación LLM basada en dataset y tests E2E |
| **Módulo/Nodo cubierto** | `MetadataCuratorAgent` (`core/agent/metadata_curator_agent.py`), correctores de texto (`core/utils/text_fixers.py`) y nodo `curate_metadata_node` |
| **Estructura por Niveles** | Nivel 1 (sintético), Nivel 2 (evaluación con dataset y mocks de tools), Nivel 3 (E2E con MinIO y Tesseract OCR). |
| **Dependencias externas** | API key de LLM (Groq / Nvidia / OpenRouter); Nivel 3: MinIO local (`localhost:9003`). |
| **Veredicto** | ✅ **Conservar** |

---

#### `integration/test_pipeline_evaluation.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Evaluador integrado con LangSmith |
| **Cómo ejecutar** | `python tests/integration/test_pipeline_evaluation.py` |
| **Qué hace** | Crea dataset `Pipeline_Integration_Tests` en LangSmith, ejecuta el pipeline completo con casos de prueba configurables y registra métricas cuantitativas. |
| **Dependencias externas** | LangSmith API, Groq API, servicio crosswalk. |
| **Veredicto** | ✅ **Conservar** |

---

#### `integration/test_source_config_generator.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Evaluador integrado con LangSmith |
| **Cómo ejecutar** | `python tests/integration/test_source_config_generator.py` |
| **Qué hace** | Evalúa el agente de generación de crosswalk config contra `SearchResults.csv`, midiendo validez de JSON y separadores. |
| **Dependencias externas** | LangSmith API, Groq API, datos en `tests/data/`. |
| **Veredicto** | ✅ **Conservar** |

---

## 4. Datos de prueba (`data/`)

| Archivo | Descripción / Uso principal |
|---------|-----------------------------|
| `curation_dataset.json` | Dataset curado de casos de prueba para `MetadataCuratorAgent` (entradas, mocks de tools y salidas esperadas). |
| `SearchResults.csv` | CSV fuente de Springer para pruebas de crosswalk y pipeline. |
| `export_10915_all.csv` | Export de SEDICI utilizado para deduplicación y reconciliación. |
| `result-14531-Romero.csv` | CSV fuente Romero para validación de crosswalk a SEDICI. |
| `sedici_input.csv` | CSV minimal de SEDICI para tests unitarios del crosswalk. |
| `oaidc_input.csv` | CSV minimal OAIDC para tests unitarios. |
| `empty.csv` | CSV vacío para prueba de casos borde. |

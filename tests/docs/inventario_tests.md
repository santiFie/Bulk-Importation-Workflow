# Inventario de Tests — `tests/`

Inventario y análisis de todos los archivos de test del proyecto, organizados en subcarpetas según su alcance (`unit/`, `integration/`, `studio/`).

---

## Mapa de la carpeta `tests/`

```
tests/
├── data/                                 ← Datos de prueba (CSVs, configs)
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
└── integration/                          ← Tests de integración (nodos de LangGraph, servicios externos y LangSmith)
    ├── test_curation_node.py             ← Nodo de curación de metadatos (PDF curation)
    ├── test_graph_steps.py               ← Integración secuencial de nodos del pipeline
    ├── test_pdf_ingest_node.py           ← Ingesta real MinIO + MCP Metadata Extractor
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

# 3. Ejecutar tests de integración
pytest tests/integration/ -v

# 4. Ejecutar toda la suite completa
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
| **Qué testea** | |
| → `TestFixSpacedChars` | Colapso de caracteres separados por espacios espurios (típico en OCR/PDFs). |
| → `TestRemoveCidArtifacts` | Eliminación de artefactos `(cid:XX)` y reemplazo de ligaduras comunes (`(cid:27)` → `fi`). |
| → `TestDeduplicateCyclicText` | Detección y recorte de texto duplicado cíclicamente (ej. título repetido 2 o 3 veces). |
| → `TestFixGluedWords` | Separación de palabras pegadas por mayúsculas intermedias o puntuación sin espacio. |
| → `TestNormalizarAutores` | Formateo consistente de listas de autores (`Apellido, Nombre ||| ...`). |
| → `TestAplicarCorrectoresProgramaticos` | Pipeline secuencial completo de correctores sobre un diccionario de metadatos. |
| **Dependencias externas** | Ninguna (local y ultra rápido). |
| **Veredicto** | ✅ **Conservar** |

---

### `unit/test_heuristic_detectors.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests unitarios puros (pytest) |
| **Módulo testeado** | `core/utils/heuristic_detectors.py` |
| **Clases / grupos** | `TestDetectarCharsDispersos`, `TestDetectarArtefactosCid`, `TestDetectarRepeticionCiclica`, `TestDetectarTextoPegado`, `TestDetectarCharsControl`, `TestDetectarLongitudAnomala`, `TestCalcularScoreAnomalia`, `TestAnalizarFila`, `TestTriarRegistros`, `TestCalcularEstadisticasLote` |
| **Qué testea** | |
| → Detectores individuales | Detección aislada de cada tipología de anomalía en campos de texto (título, abstract, autores). |
| → `TestCalcularScoreAnomalia` | Ponderación de anomalías y cálculo de score normalizado `[0.0, 1.0]`. |
| → `TestTriarRegistros` | Clasificación de registros en `limpios` vs `anomalos` según umbral. |
| → `TestCalcularEstadisticasLote` | Métricas agregadas del lote bajo análisis. |
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

### `integration/test_graph_steps.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Tests de integración (pytest) |
| **Nodos cubiertos** | Pasos 1, 2a, 2b, 3, 4, 5, 6, 8, 9 — pipeline completo vía los nodos de `core/graph.py` |
| **Mock del Deduplicador** | `FakeDeduplicatorClient` (mock con join por ID). |
| **Qué testea** | Generación de config LLM (fallback y estructura), mapeos a genérico, deduplicación simulada, reconciliación de metadatos, formato SEDICI, correcciones finales, armado de estructura SAF y preparación de importación DSpace. |
| **Dependencias externas** | Servicio crosswalk local (REST) para pasos 2a, 2b, 5. |
| **Veredicto** | ✅ **Conservar** — suite principal de validación funcional. |

---

### `integration/test_pdf_ingest_node.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Test de integración real con servicios |
| **Nodo cubierto** | `pdf_ingest_node` (`core/nodes/ingest_nodes.py`) |
| **Qué testea** | Conexión e ingesta desde MinIO (bucket `importacion`), invocación del MCP Metadata Extractor, extracción de textos/metadatos y generación de `source_from_pdfs.csv`. |
| **Dependencias externas** | MinIO (`localhost:9003`), Metadata Extractor MCP (`http://localhost:9604/mcp`), Docker (`aistor`), Groq API Key. Se salta automáticamente si la infra no está disponible. |
| **Veredicto** | ✅ **Conservar** |

---

### `integration/test_curation_node.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Test de integración y funcionalidad de nodo |
| **Nodo cubierto** | `curate_metadata_node` (`core/nodes/curation_nodes.py`) |
| **Qué testea** | Flujo de curación sobre CSV extraído de PDFs (`/tmp/source_from_pdfs.csv`), aplicación de correctores en lote, triaje de anomalías, integración con agente LLM (mockeado o real) y flujo E2E Ingest → Curation. |
| **Dependencias externas** | Requiere CSV de PDFs generado por `test_pdf_ingest_node.py` o servicios MinIO/Extractor para la prueba E2E completa (se salta si no están). |
| **Veredicto** | ✅ **Conservar** |

---

### `integration/test_pipeline_evaluation.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Evaluador integrado con LangSmith |
| **Cómo ejecutar** | `python tests/integration/test_pipeline_evaluation.py` |
| **Qué hace** | Crea dataset `Pipeline_Integration_Tests` en LangSmith, ejecuta el pipeline completo con casos de prueba configurables y registra métricas cuantitativas (errores, integridad de datos, retención de filas, presencia de columnas SEDICI). |
| **Dependencias externas** | LangSmith API, Groq API, servicio crosswalk. |
| **Veredicto** | ✅ **Conservar** |

---

### `integration/test_source_config_generator.py`

| Campo | Detalle |
|-------|---------|
| **Tipo** | Evaluador integrado con LangSmith |
| **Cómo ejecutar** | `python tests/integration/test_source_config_generator.py` |
| **Qué hace** | Evalúa el agente de generación de crosswalk config contra `SearchResults.csv`, midiendo validez de JSON, mapeo de columnas críticas y exactitud de expresiones regulares de separadores. |
| **Dependencias externas** | LangSmith API, Groq API, datos en `tests/data/`. |
| **Veredicto** | ✅ **Conservar** |

---

## 4. Datos de prueba (`data/`)

| Archivo | Descripción / Uso principal |
|---------|-----------------------------|
| `SearchResults.csv` | CSV fuente de Springer para pruebas de crosswalk y pipeline. |
| `export_10915_all.csv` | Export de SEDICI utilizado para deduplicación y reconciliación. |
| `result-14531-Romero.csv` | CSV fuente Romero para validación de crosswalk a SEDICI. |
| `sedici_input.csv` | CSV minimal de SEDICI para tests unitarios del crosswalk. |
| `oaidc_input.csv` | CSV minimal OAIDC para tests unitarios. |
| `empty.csv` | CSV vacío para prueba de casos borde. |

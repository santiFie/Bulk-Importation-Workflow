# Pruebas Unitarias de Nodos LangGraph — `tests/unit/nodes/`

[← Volver a tests/unit/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/nodes/` contiene las pruebas de aislamiento unitario para cada uno de los nodos individuales que componen el grafo principal y los subgrafos de LangGraph (`core/nodes/`).

En la arquitectura de LangGraph, un nodo es una función o ejecutable que recibe el estado del pipeline (`State`) y devuelve un diccionario con las mutaciones que deben aplicarse a dicho estado. Estas pruebas garantizan que cada nodo:
1. Realice sus operaciones de procesamiento de manera aislada.
2. Maneje adecuadamente los estados de entrada incompletos o atípicos.
3. No mute directamente el estado recibido, retornando en su lugar el diccionario de actualización correspondiente.
4. Capture excepciones y registre errores en `node_errors` sin interrumpir descontroladamente el runtime salvo ante condiciones fatales.

---

## 🔍 Qué se Evalúa

Se prueban de forma exhaustiva los nodos de los 4 subgrafos del pipeline a través de los siguientes archivos de test:

| Archivo de Test | Módulo Evaluado | Responsabilidad del Nodo | Invariantes Clave Verificadas |
|:---|:---|:---|:---|
| `test_setup_node.py` | `setup_nodes.py` | Inicialización del espacio de trabajo y directorios temporales | Creación de paths en `workspace_dir`, validación de parámetros iniciales |
| `test_ingest_nodes.py` | `ingest_nodes.py` | Bifurcación entre entrada directa por CSV o ingesta de PDFs desde MinIO | Enrutamiento condicional según `input_source_type` |
| `test_curation_nodes.py` | `curation_nodes.py` | Curación de metadatos extraídos de PDFs con anomalías | Aplicación de correctores programáticos y triaje de anomalías |
| `test_crosswalk_nodes.py` | `pipeline_nodes.py` | Mapeo conceptual de fuente a esquema genérico | Validación de configs JSON e invocación de `CrosswalkClient` mockeado |
| `test_dedup_node.py` | `pipeline_nodes.py` | Invocación del servicio de deduplicación de registros | Manejo de IDs duplicados, umbrales de similitud y registro de métricas |
| `test_reconciliation_node.py` | `pipeline_nodes.py` | Reconciliación de ítems duplicados y merge de campos | Priorización de metadatos locales de SEDICI sobre la fuente externa |
| `test_enrichment_nodes.py` | `enrichment_nodes.py` | Enriquecimiento con APIs bibliográficas (Crossref, OpenAlex) | Enrutamiento condicional y actualización in-place del CSV genérico |
| `test_map_to_sedici_format_node.py` | `export_nodes.py` | Transformación del esquema genérico al esquema SEDICI | Validación de columnas destino y dialectos de salida |
| `test_correction_node.py` | `export_nodes.py` | Correcciones finales de metadatos institucionales | Normalización de fechas, mapeo de tipologías y licencias Creative Commons |
| `test_saf_node.py` | `export_nodes.py` | Empaquetado en estructura Simple Archive Format (SAF) | Generación de `dublin_core.xml`, `metadata_sedici.xml` y `contents` |
| `test_import_node.py` | `export_nodes.py` | Invocación del importador de DSpace (`dspace import`) | Dry-run validation vs importación física real y lectura de `mapfile` |
| `test_sanitizer_node.py` | `sanitizer_nodes.py` | Limpieza de caracteres de control y secuencias inválidas | Eliminación de bytes nulos, secuencias de escape no permitidas |
| `test_target_crosswalk_agent.py` | `target_crosswalk_agent.py` | Agente generador de configuraciones de crosswalk hacia SEDICI | Inferencia mockeada del esquema destino y validación de reglas |

### Fixtures Reutilizables (`conftest.py`):
- `base_state`: Diccionario de estado mínimo con campos estándar (`source_name`, `workspace_dir`, etc.).
- `make_csv`: Helper para sintetizar archivos CSV en memoria/disco temporal (`tmp_path`).
- `mock_crosswalk_client`: Mock preconfigurado que devuelve bytes CSV transformados.
- `mock_deduplicator_client`: Mock que emula la respuesta del microservicio de deduplicación.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas unitarias de nodos
pytest tests/unit/nodes/ -v

# Ejecutar pruebas de un nodo en particular
pytest tests/unit/nodes/test_saf_node.py -v
pytest tests/unit/nodes/test_reconciliation_node.py -v

# Ejecutar mostrando salidas de logs capturadas
pytest tests/unit/nodes/ -v --log-cli-level=INFO
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 llamadas de red (aislamiento total con mocks).
- **Contenedores Docker:** No se requiere ningún contenedor levantado.
- **Tiempo Estimado de Ejecución:** ~3 a 5 segundos.
- **Costo en Tokens:** **$0.00**.

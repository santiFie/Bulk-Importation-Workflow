# Documentación Técnica: Tests de Integración de `ExportSubgraph`

## 1. Propósito y Alcance

Este documento describe la suite de pruebas de integración para el **subgrafo de exportación e importación** ([core/subgraphs/export.py](file:///home/santi/Documentos/LangGraph/Modulo-Marta/core/subgraphs/export.py)), implementada en [tests/integration/subgraphs/test_export_subgraph.py](file:///home/santi/Documentos/LangGraph/Modulo-Marta/tests/integration/subgraphs/test_export_subgraph.py).

El subgrafo encapsula la fase final del pipeline de importación masiva hacia **SEDICI / DSpace 7+**. Recibe como entrada el CSV reconciliado (salida de `CrosswalkDedupSubgraph`, potencialmente enriquecido por `EnrichmentSubgraph`) y ejecuta una secuencia lineal de 5 nodos:

```mermaid
graph LR
    START([START]) --> N1[GenerateSediciTargetConfig]
    N1 --> N2[MapToSediciFormat]
    N2 --> N3[MetadataCorrections]
    N3 --> N4[GenerateSafToImport]
    N4 --> N5[ImportToDspace]
    N5 --> END([END])
```

A diferencia de las pruebas unitarias aisladas por nodo, esta suite evalúa la **integración de extremo a extremo conectándose a servicios reales**:
- **Agente LLM:** `TargetSediciCrosswalkGenerator` (modelo configurado vía OpenRouter, e.g. `openai/gpt-oss-120b`).
- **Servicio REST de Crosswalk:** Backend en `http://localhost:8000` con autenticación JWT.
- **Generador de Simple Archive Format (SAF):** Script programático `dspace-csv-archive`.
- **DSpace 7+ Scripts API:** Instancia real de DSpace (`http://localhost:8080/server`), ejecutando el script `/api/system/scripts/import/processes` sobre la colección de pruebas designada.

---

## 2. Análisis de Medición y Dimensiones de Evaluación End-to-End

El diseño de las pruebas responde a la necesidad de validar la coherencia integral del lote y su admisibilidad en el repositorio institucional:

### A. Invariante de Conservación de Datos ($N_{\text{in}} = N_{\text{out}}$)
- **Cero pérdida de registros:** Si el CSV reconciliado contiene $N$ ítems, se verifica que:
  $$\text{filas}(reconciled.csv) = \text{filas}(sedici\_ready.csv) = \text{carpetas}(saf/item\_*) = \text{líneas}(import\_mapfile.txt)$$
- **Trazabilidad de artefactos:** En cada ejecución deben coexistir en el `workspace_dir`:
  - `config_{source_name}_to_sedici.json`
  - `sedici_ready.csv`
  - Directorio `saf_archive/` con subcarpetas `item_001` a `item_00N`
  - `import_mapfile.txt` (en modo de importación real)

### B. Calidad y Conformidad con el Catálogo de SEDICI
- **Presencia de Metadatos Núcleo:** Todo registro exportado debe contar con:
  - `dc.title` (o calificado `dc.title[es]`) no vacío.
  - Autoría (`sedici.creator.person[es]` o institucional).
  - Fecha de emisión (`dc.date.issued`).
  - Tipología documental (`dc.type`).
- **Cero Alucinaciones de Esquema:** Todo atributo mapeado debe pertenecer estrictamente al catálogo oficial (`core/schemas/sedici_metadata_schema.json`), asegurando que el guardrail de Nivel 3 haya purgado cualquier término no reconocido por el `MetadataRegistry` de DSpace.

### C. Normalización de Formato y Post-Procesamiento
- **Separadores Multivaluados:** Normalización forzada a `||`, eliminando delimitadores legacy (como `|||` de Scopus) o ambiguos.
- **Estandarización de Idiomas:** Validación de códigos ISO (`es`, `en`, `pt`) en columnas `dc.language`.

### D. Estructura y Conformidad del Simple Archive Format (SAF)
- Cada ítem $i$ cuenta con su directorio `saf_archive/item_00N/`.
- Cada paquete contiene `dublin_core.xml` (y esquemas adicionales como `metadata_sedici.xml`), válidos sintácticamente según `xml.etree.ElementTree`.
- Presencia del archivo `contents` por ítem listando los bitstreams correspondientes.

### E. Interacción con DSpace Scripts API
- **Autenticación:** Negociación exitosa de cookies CSRF y tokens JWT.
- **Monitoreo:** Polling asíncrono sobre `/api/system/processes/{id}` hasta alcanzar `processStatus == "COMPLETED"`.
- **Modo Dry-Run (`import_validate_only=True`):** DSpace valida los esquemas y consistencia sin persistir datos en la base de datos (ideal para CI y corridas periódicas).
- **Modo Real (`import_validate_only=False`):** Persistencia física en la colección destino, emisión del `mapfile.txt` y validación de existencia vía API REST de búsqueda (`/api/discover/search/objects?query=handle:"..."`).

---

## 3. Datos de Entrada y Fixtures (`tests/data/export/`)

Los tests consumen los CSVs generados por `crosswalk_dedup`:

```text
tests/data/export/
├── licencias-sedici.csv
└── reconciled_inputs/
    ├── README.md                           <- Especificación de formato
    ├── reconciled_sample_generic.csv       <- Caso canónico (salida PDF/dedup)
    └── reconciled_sample_with_remnants.csv <- Caso heterogéneo (salida PubMed/Scopus con remanentes)
```

### Manejo de Bitstreams Faltantes en Tests
Para evitar errores de `FileNotFoundError` cuando el CSV referencia nombres de PDFs (e.g. `39-jaiio-ast-04.pdf-PDFA.pdf`) que no residen en el workspace temporal del test, se implementó la fixture `mock_saf_missing_bitstreams`:
- Si el archivo fuente existe en disco, se copia normalmente.
- Si no existe localmente, genera automáticamente un PDF dummy válido en el paquete SAF (`%PDF-1.4 dummy test bitstream`), permitiendo que el generador SAF y DSpace completen la ingesta sin interrupciones.

---

## 4. Casos de Prueba Implementados

La suite [tests/integration/subgraphs/test_export_subgraph.py](file:///home/santi/Documentos/LangGraph/Modulo-Marta/tests/integration/subgraphs/test_export_subgraph.py) organiza los tests en clases ordenadas progresivamente:

| Clase / Test | Descripción | Modo DSpace | Estado |
| :--- | :--- | :--- | :--- |
| **`TestExportSubgraphTopology`**<br>`test_compilacion_y_nodos_presentes` | Verifica compilación del grafo y presencia de los 5 nodos en orden. | N/A | ✅ **PASSED** |
| **`TestExportServicesHealth`**<br>`test_servicio_crosswalk_disponible`<br>`test_servicio_dspace_disponible_y_coleccion_accesible` | Health check previo: autenticación JWT en Crosswalk y verificación de la colección `a74e21b7-8ba8-4751-ba40-58051a055fd2` en DSpace. | Consulta REST | ✅ **PASSED** |
| **`TestExportSubgraphIdempotency`**<br>`test_reutilizacion_config_existente_omite_agente` | Verifica que si el estado ya contiene `sedici_target_crosswalk_config`, se respeta el archivo y se omite la llamada al LLM. | Validación (`-v`) | ✅ **PASSED** (6.82s) |
| **`TestExportSubgraphBypassLLM`**<br>`test_flujo_esquema_generico_resuelve_nivel_1_sin_llm` | Ejecuta el flujo con esquema canónico (PDF-MinIO). Nivel 1 determinista cubre las columnas y el LLM mapea `id -> files`. | Validación (`-v`) | ✅ **PASSED** (8.03s) |
| **`TestExportSubgraphDryRun`**<br>`test_flujo_completo_validacion_dry_run_con_llm_y_servicios_reales` | Ejecuta el flujo sobre datos PubMed con 8 columnas remanentes: LLM + Guardrail + Crosswalk API + Correcciones + SAF + DSpace dry-run. | Validación (`-v`) | ✅ **PASSED** (9.52s) |
| **`TestExportSubgraphRealImport`**<br>`test_flujo_completo_ingesta_real_en_coleccion_dspace` | Ingesta física y persistente en DSpace: valida generación de `mapfile.txt`, asignación de handles y búsqueda REST del ítem indexado. | Ingesta Real (`-a`) | ⚠️ *Ver hallazgo crítico* |

---

## 5. Hallazgo Crítico del Test de Integración: Causa Raíz y Solución

Durante la ejecución del test de importación real (`TestExportSubgraphRealImport`), se detectó un fallo en la fase de commit de base de datos en DSpace:

```text
2026-09-19T00:00:46.962729233Z ERROR import - 5 @ Error committing changes to database: bad_dublin_core schema=Unnamed:#lang#0..null. Metadata field does not exist!, aborting most recent changes
Caused by: java.sql.SQLException: bad_dublin_core schema=Unnamed:#lang#0..null. Metadata field does not exist!
	at org.dspace.content.DSpaceObjectServiceImpl.addMetadata(DSpaceObjectServiceImpl.java:207)
	at org.dspace.app.itemimport.ItemImportServiceImpl.loadDublinCore(ItemImportServiceImpl.java:951)
```

### Diagnóstico de Causa Raíz
1. **Origen:** El backend del servicio de Crosswalk (`Backend-Modulo-Nacho`) serializa el DataFrame resultante de pandas conservando el índice numérico, introduciendo una columna sin encabezado: `Unnamed: 0`.
2. **Propagación:** Ni `MetadataCorrections` ni `GenerateSafToImport` ([core/nodes/saf_node.py](file:///home/santi/Documentos/LangGraph/Modulo-Marta/core/nodes/saf_node.py)) filtraban columnas espurias de índice.
3. **Conversión SAF:** El script `dspace-csv-archive` procesa todas las columnas del CSV, interpretando `Unnamed: 0` como un campo de metadatos con prefijo `Unnamed`, generando un XML con `<dcvalue schema="Unnamed" element=" 0">`.
4. **Comportamiento en DSpace:**
   - En modo dry-run (`-v` / validación), DSpace no abre la transacción de persistencia en PostgreSQL, por lo que el error pasa inadvertido.
   - En **modo real persistente**, DSpace valida contra el `MetadataRegistry` de la base de datos. Al no existir el esquema `Unnamed`, la base de datos lanza `SQLException`, aborta la transacción y no genera el mapfile.

### Solución Verificada para [core/nodes/saf_node.py](file:///home/santi/Documentos/LangGraph/Modulo-Marta/core/nodes/saf_node.py)
En la función `_prepare_dataframe`:
```python
def _prepare_dataframe(csv_path: str) -> pd.DataFrame:
    """
    Lee el CSV y asegura que exista la columna 'files' requerida por DspaceArchive.
    Descarta cualquier columna de índice residual generada por pandas o por el backend de Crosswalk.
    """
    df = pd.read_csv(csv_path)
    
    # Descartar columnas de índice residuales (e.g. 'Unnamed: 0')
    unnamed_cols = [c for c in df.columns if str(c).startswith("Unnamed:")]
    if unnamed_cols:
        df = df.drop(columns=unnamed_cols)
        
    if _SAF_FILES_COLUMN not in df.columns:
        df.insert(0, _SAF_FILES_COLUMN, "")
    return df
```

### Resultados de la Verificación con el Ajuste
Al descartar las columnas `Unnamed:`, DSpace realizó exitosamente el commit persistente:
- **Mapfile generado:**
  ```text
  item_001 10915/193129
  item_002 10915/193130
  item_003 10915/193131
  ```
- **Consulta REST:** Búsqueda en `/api/discover/search/objects?query=handle:"10915/193129"` retornó el ítem creado e indexado con su título y metadatos correspondientes.

---

## 6. Guía de Ejecución de los Tests

Con el entorno virtual `.venv` activado:

```bash
# 1. Ejecutar verificación de compilación y salud de servicios
.venv/bin/pytest tests/integration/subgraphs/test_export_subgraph.py -k "TestExportSubgraphTopology or TestExportServicesHealth" -v

# 2. Ejecutar test de validación Dry-Run (recomendado para CI/CD)
.venv/bin/pytest tests/integration/subgraphs/test_export_subgraph.py -k "TestExportSubgraphDryRun" -v -s

# 3. Ejecutar test de esquema genérico / bypass LLM
.venv/bin/pytest tests/integration/subgraphs/test_export_subgraph.py -k "TestExportSubgraphBypassLLM" -v -s

# 4. Ejecutar test de idempotencia (reutilización de config previa)
.venv/bin/pytest tests/integration/subgraphs/test_export_subgraph.py -k "TestExportSubgraphIdempotency" -v -s

# 5. Ejecutar suite completa del subgrafo
.venv/bin/pytest tests/integration/subgraphs/test_export_subgraph.py -v
```

### Variables de Entorno Relevantes:
- `DSPACE_BASE_URL`: URL del servidor DSpace (por defecto `http://localhost:8080/server`).
- `DSPACE_EMAIL` / `DSPACE_PASSWORD`: Credenciales de administrador de DSpace.
- `TEST_DSPACE_COLLECTION`: Colección destino (por defecto `a74e21b7-8ba8-4751-ba40-58051a055fd2`).
- `CROSSWALK_API_URL`: URL del backend de Crosswalk (por defecto `http://localhost:8000`).
- `OPEN_ROUTER_API_KEY`: Clave de API para las invocaciones LLM del agente de crosswalk.

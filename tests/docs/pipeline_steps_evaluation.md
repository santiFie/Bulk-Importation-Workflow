# Evaluación del Pipeline de Importación a SEDICI

Documento de referencia para profundizar en la evaluación del pipeline antes de continuar con el desarrollo.
Para cada paso se describe: **qué hace**, **qué produce**, **qué medir** y **qué se considera aceptable**.

---

## Visión general del pipeline

```
START
  → Paso 1  — GenerateSourceCrosswalkConfig   (Agente LLM)
  → Paso 2a — MapSourceToGeneric              (Crosswalk: fuente → genérico)
  → Paso 2b — MapSediciToGeneric              (Crosswalk: SEDICI → genérico, paralelo)
  → Paso 3  — Deduplicate                     (Detección de duplicados)
  → Paso 4  — MetadataReconciliation          (Join con metadatos originales)
  → Paso 5  — MapToSediciFormat               (Crosswalk: fuente → formato SEDICI)
  → Paso 6  — MetadataCorrections             (Correcciones programáticas)
  → Paso 8  — GenerateSafToImport             (Generación del SAF)
  → Paso 9  — ImportToDspace                  (Importación REST a DSpace)
END
```

> **Nota:** el Paso 7 (`get_pdfs`) está pendiente de implementación.

---

## Paso 1 — `GenerateSourceCrosswalkConfig`

### Qué hace

Genera automáticamente el archivo de configuración JSON que describe cómo mapear las columnas del CSV origen al **formato genérico** requerido por el Deduplicador. Consta de cuatro fases internas:

| Fase | Método | Descripción |
|------|--------|-------------|
| **1 — Mapeo de columnas** | LLM (Groq) + tool `save_column_mappings` | El LLM analiza las cabeceras y 5 filas de muestra del CSV. Llama a la herramienta con el array de mappings (`left`, `replace`, `default`, `required`, `filter`). Máximo 3 iteraciones. |
| **2a — Detección del separador** | Python-first (`detect_separator`) | Analiza los valores reales de las columnas mapeadas a `author` / `subject` y clasifica el separador (literal o regex) sin LLM. |
| **2b — Inferencia de regex** | Agente ReAct (`test_regex_on_samples`) | Solo si la Fase 2a no pudo clasificar. El agente prueba patrones regex contra muestras reales. Máximo 5 iteraciones. |
| **3 — Construcción del config** | Python | Combina mappings + separador y escribe el JSON final en disco. |
| **4 — Validación determinista** | Python (sin LLM) | Ejecuta el crosswalk sobre 3 filas reales. Verifica columnas críticas y splits de `author`. Si falla, reintenta la Fase 1 con feedback (máx. 1 reintento). |

**Salida:** archivo `crosswalk_config_<source_name>.json` con estructura `[mappings, settings]`.

**Comportamiento de caché:** si el archivo ya existe en disco, se reutiliza sin invocar el LLM.

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Cobertura de columnas críticas** | Porcentaje de columnas obligatorias (`id`, `title`, `author`, `date`) presentes en el CSV de salida genérico | **100%** (las 4 deben estar presentes) |
| M2 | **Tasa de splits correctos en `author`** | Porcentaje de filas no vacías donde el separador detectado produce ≥ 2 tokens | **≥ 70%** de filas con múltiples autores |
| M3 | **Iteraciones de LLM usadas** | Cuántas iteraciones de la Fase 1 se necesitaron (1–3) | ≤ 2 iteraciones (sin reintento) |
| M4 | **¿Se activó el fallback 1:1?** | Indica si el LLM no produjo resultado y se usó mapeo directo | Nunca en producción |
| M5 | **Validación determinista superada** | `validation["ok"] == True` en la Fase 4 | **True** en primera pasada; si requiere reintento → degradado |
| M6 | **Tiempo de ejecución** | Tiempo total del nodo en segundos | < 30s (sin ReAct); < 60s (con ReAct) |

#### Considerado "bueno"
- Las 4 columnas críticas presentes sin reintento.
- Separador detectado como `literal` o `regex` válido (no `unknown` con fallback `||`).
- `validation["ok"] == True` en la primera Fase 4.

#### Considerado "malo"
- Alguna columna crítica ausente después del reintento.
- `separator_info["type"] == "unknown"` → fallback a `||` (puede perder multivaluados).
- Uso del fallback 1:1 (`_build_fallback_mappings`).

---

## Paso 2a — `MapSourceToGeneric`

### Qué hace

Aplica el crosswalk config generado en el Paso 1 al CSV del repositorio **origen** via la API REST del backend `crosswalk`. Transforma las columnas propietarias (ej. columnas de Springer, Scopus) al **formato genérico** con las columnas estándar que entiende el Deduplicador.

**Entrada:**
- `state["source_csv_path"]` — CSV original del repositorio externo
- `state["source_crosswalk_config"]` — JSON de configuración generado en Paso 1

**Salida:** `state["generic_source_csv_path"]` — CSV con columnas genéricas (`id`, `title`, `author`, `date`, `subject`, etc.)

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Tasa de retención de filas** | `filas_output / filas_input` | ≥ 95% (salvo ítems filtrados explícitamente) |
| M2 | **Cobertura de columnas genéricas** | Columnas del formato genérico presentes en el output | Mínimo: `id`, `title`, `author`, `date` presentes |
| M3 | **Valores no nulos en `title`** | Porcentaje de filas con `title` no vacío | ≥ 98% |
| M4 | **Valores no nulos en `id`** | Porcentaje de filas con `id` no vacío | **100%** |
| M5 | **Autores multivaluados correctos** | Porcentaje de filas con `author` conteniendo `|` cuando se esperan múltiples autores | Varía por fuente; validar manualmente 10 filas de muestra |
| M6 | **HTTP status de la API** | El endpoint `/run_crosswalk` debe responder `200 OK` | **Siempre 200** |

#### Considerado "bueno"
- Retención ≥ 95% de filas.
- Todas las columnas críticas presentes con ≥ 98% de valores no nulos.
- Separador `|` aplicado correctamente en campos multivaluados.

#### Considerado "malo"
- Error de API (`CrosswalkApiError`) → paso fallido.
- Pérdida de ≥ 5% de filas sin motivo.
- Columna `id` con valores vacíos (rompe el Paso 4).

---

## Paso 2b — `MapSediciToGeneric`

### Qué hace

Idéntico al Paso 2a pero para el **CSV exportado de SEDICI**. Usa el config fijo `sedicicrosswalkconfig.json` (no generado por el agente) para transformar los metadatos de SEDICI al mismo formato genérico.

**Entrada:**
- `state["repository_csv_path"]` — exportación de SEDICI
- `state["sedici_crosswalk_config"]` — `configs/sedicicrosswalkconfig.json`

**Salida:** `state["generic_sedici_csv_path"]`

> Este paso corre **en paralelo** con el Paso 2a en el grafo real (pero secuencialmente en el script de test).

---

### Cómo evaluarlo

Las métricas son análogas a las del Paso 2a, pero con el universo de SEDICI:

| # | Métrica | Umbral aceptable |
|---|---------|-----------------|
| M1 | Tasa de retención de filas | ≥ 99% (el config de SEDICI es estático y testeado) |
| M2 | Columnas genéricas presentes | `id`, `title`, `author`, `date` **siempre** |
| M3 | `id` no nulo | **100%** |
| M4 | HTTP status | **Siempre 200** |

#### Considerado "bueno"
- El config estático de SEDICI nunca debería fallar. Una tasa < 99% indica un problema en el CSV exportado o un cambio en el formato de SEDICI.

#### Considerado "malo"
- Cualquier error de API.
- Pérdida de filas: el repositorio es la fuente de verdad para deduplicación.

---

## Paso 3 — `Deduplicate`

### Qué hace

Envía los dos CSVs en formato genérico al servicio **Deduplicador** para detectar si algún ítem del repositorio origen ya existe en SEDICI.

El Deduplicador devuelve un CSV con las columnas:

| Columna | Descripción |
|---------|-------------|
| `id` | Identificador del ítem en la fuente |
| `title` | Título del ítem |
| `match_id` | ID del posible duplicado en SEDICI |
| `match_title` | Título del posible duplicado |
| `total` | Puntuación de similitud (0–100) |

> En el script de test (`run_pipeline.py`) se usa un **mock** (`_FakeDeduplicatorClient`) que devuelve todos los ítems con `total=0` (sin duplicados) para no depender del servicio externo.

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Tasa de duplicados detectados** | `filas con total >= umbral_seguro / total_filas` | Depende del dataset; validar manualmente |
| M2 | **Precisión (Precision@umbral)** | De los marcados como duplicado, ¿cuántos lo son realmente? | ≥ 80% |
| M3 | **Recall (Recall@umbral)** | De los duplicados reales, ¿cuántos fueron detectados? | ≥ 70% |
| M4 | **F1-score** | Media armónica de precisión y recall | ≥ 0.75 |
| M5 | **Cobertura del output** | Todos los ítems del origen deben aparecer en el CSV resultado | **100%** |
| M6 | **HTTP status del servicio** | El endpoint del Deduplicador debe responder sin error | **Siempre exitoso** |

> **Nota sobre los umbrales de negocio** (definidos en `state`):
> - `umbral_seguro = 10` → ítems con `total < 10` se consideran **no duplicados** y pasan al Paso 4.
> - `umbral_revision = 30` → ítems con `10 ≤ total < 30` podrían requerir revisión manual.
> - Ítems con `total ≥ 30` se descartan como duplicados.

#### Considerado "bueno"
- Precision ≥ 80% y Recall ≥ 70% con los umbrales actuales.
- El 100% de los ítems del origen aparece en el output.

#### Considerado "malo"
- Recall < 70% → duplicados reales pasan al repositorio.
- Precision < 80% → ítems legítimos son descartados por error.
- Output con menos filas que el input → pérdida silenciosa de ítems.

---

## Paso 4 — `MetadataReconciliation`

### Qué hace

Filtra los ítems que el Deduplicador marcó como **no duplicados** (con `total < umbral_seguro`) y realiza un **JOIN** con el CSV original (metadatos completos sin mapear) usando el identificador de cada documento.

**Lógica de join:**
1. Filtra `df_dedup` para quedarse con filas donde `total < umbral_seguro`.
2. Busca la columna `id` en el CSV original (candidatos: `id`, `sedici.identifier.other`, `dc.identifier.uri`).
3. Si no encuentra columna de id → usa fallback por índice de fila.
4. Guarda el resultado en `state["reconciled_csv_path"]`.

**Salida:** CSV con los **metadatos originales completos** (todas las columnas del CSV fuente) de los ítems seleccionados para importar.

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Tasa de retención** | `filas_reconciled / filas_no_duplicadas` | **100%** — todos los no-duplicados deben estar |
| M2 | **Se usó join por ID (no por índice)** | Indica si se encontró columna de identificador | Siempre `True` en producción |
| M3 | **Cobertura de columnas** | El CSV reconciliado debe tener todas las columnas del CSV original | **100%** |
| M4 | **Porcentaje de descarte** | `filas_descartadas / filas_totales` | Depende del dataset; documentar por ejecución |
| M5 | **Consistencia del id** | Todos los ids en el reconciliado existen en el CSV original | **100%** |

#### Considerado "bueno"
- Join exitoso por columna de id (no por fallback de índice).
- Retención del 100% de los ítems no-duplicados.
- CSV reconciliado con todas las columnas del CSV fuente.

#### Considerado "malo"
- Se usó el fallback por índice → riesgo de join incorrecto si los CSVs tienen distinto orden.
- Pérdida de ítems no-duplicados en el join.
- `umbral_seguro` mal calibrado: demasiado alto descarta ítems legítimos, demasiado bajo deja pasar duplicados.

---

## Paso 5 — `MapToSediciFormat`

### Qué hace

Aplica el crosswalk **de la fuente al formato de metadatos de SEDICI** sobre el CSV reconciliado (que contiene los metadatos originales completos). Usa `state["sedici_target_crosswalk_config"]` (ej. `config_romero_to_sedici.json`).

**Entrada:** `state["reconciled_csv_path"]` (metadatos originales de ítems a importar)  
**Salida:** `state["sedici_ready_csv_path"]` — CSV con columnas en el esquema de SEDICI (`dc.title`, `dc.contributor.author`, `dc.date.issued`, etc.)

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Tasa de retención de filas** | `filas_output / filas_input` | ≥ 95% |
| M2 | **Columnas DSpace obligatorias presentes** | `dc.title`, `dc.contributor.author`, `dc.date.issued`, `dc.type` | **Las 4 presentes** |
| M3 | **Valores no nulos en `dc.title`** | % de filas con título | ≥ 98% |
| M4 | **Valores no nulos en `dc.contributor.author`** | % de filas con al menos un autor | ≥ 90% |
| M5 | **Formato de fecha en `dc.date.issued`** | Valores con formato válido (YYYY o YYYY-MM o YYYY-MM-DD) | ≥ 95% |
| M6 | **HTTP status de la API** | | **Siempre 200** |

#### Considerado "bueno"
- Las 4 columnas DSpace obligatorias presentes con alta cobertura.
- Fechas en formato reconocible por DSpace.
- Sin pérdida de filas.

#### Considerado "malo"
- `dc.title` vacío en alguna fila → DSpace rechaza el ítem en la importación.
- Pérdida ≥ 5% de filas.
- Columnas requeridas ausentes en el CSV de salida.

---

## Paso 6 — `MetadataCorrections`

### Qué hace

Aplica correcciones **programáticas** al CSV en formato SEDICI generado en el Paso 5. Las correcciones son específicas por `source_name`. El archivo se **sobreescribe en su lugar** (no genera un archivo nuevo).

**Correcciones genéricas (todos los repositorios):**
- Normalización de separadores a `||`
- Normalización de `dc.language` a códigos de dos letras (`es`, `en`, `pt`)

**Correcciones específicas de SCOPUS:**
- Generación de `mods.originInfo.place[es]` desde `autores_unlp_nombre`
- Filtrado de autores: si hay más de 30, conservar solo los de la UNLP

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Tasa de retención de filas** | Correcciones no deben eliminar filas | **100%** |
| M2 | **Cobertura de `dc.language` normalizado** | % de filas con código de 2 letras válido (`es`, `en`, `pt`, etc.) | ≥ 95% |
| M3 | **Separadores normalizados** | No debe quedar ningún separador distinto de `||` en campos multivaluados | **100%** |
| M4 | **Autores filtrados (SCOPUS)** | Si > 30 autores, solo quedan autores UNLP | Validar en dataset Scopus |
| M5 | **El archivo de salida existe** | `os.path.isfile(sedici_ready_csv_path)` | **True** |

#### Considerado "bueno"
- Ninguna fila eliminada.
- `dc.language` normalizado en todas las filas.
- Sin separadores residuales distintos de `||`.

#### Considerado "malo"
- `FileNotFoundError` → el Paso 5 no generó el archivo.
- Filas con idioma no reconocido (quedan como string libre → DSpace puede rechazarlas).
- Pérdida de autores UNLP en el filtrado de SCOPUS.

---

## Paso 8 — `GenerateSafToImport`

### Qué hace

Genera el **Simple Archive Format (SAF)** de DSpace a partir del CSV SEDICI-ready. Usa el script `dspace-csv-archive` para crear un directorio con subdirectorios `item_001/`, `item_002/`, etc., cada uno con:
- `dublin_core.xml` — metadatos en XML
- `contents` — lista de bitstreams

**Lógica interna:**
1. Agrega columna `files` (vacía) si no existe en el CSV.
2. Escribe un CSV temporal en el directorio de salida.
3. Instancia `DspaceArchive` y llama a `write()`.
4. Elimina el CSV temporal.

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Directorio SAF generado** | `os.path.isdir(saf_output_path)` | **True** |
| M2 | **Cantidad de ítems en el SAF** | Número de subdirectorios `item_*/` creados | `== filas del CSV input` |
| M3 | **XML válido** | `dublin_core.xml` parseable sin errores en cada ítem | **100%** |
| M4 | **Cobertura de `dc.title` en XML** | Cada `dublin_core.xml` tiene al menos un elemento `<dcvalue element="title">` | **100%** |
| M5 | **Archivo `contents` presente** | Cada ítem tiene el archivo `contents` (puede estar vacío si no hay PDFs) | **100%** |
| M6 | **Sin errores de ejecución** | No se lanza excepción durante `archive.write()` | **True** |

#### Considerado "bueno"
- N ítems en el SAF = N filas en el CSV.
- Todos los `dublin_core.xml` válidos y con `dc.title`.
- Sin errores de ejecución.

#### Considerado "malo"
- Excepción en `DspaceArchive.write()` → SAF no generado o incompleto.
- `item_*` count < filas del CSV → pérdida silenciosa de ítems.
- `dublin_core.xml` mal formado → DSpace rechaza el ítem.

---

## Paso 9 — `ImportToDspace`

### Qué hace

Importa el SAF generado en el Paso 8 directamente a SEDICI/DSpace usando la **Scripts API REST** (`/api/system/scripts/import/processes`).

**Flujo interno:**
1. Zipea el directorio SAF en memoria.
2. Lanza un proceso de importación (`POST` multipart con el ZIP).
3. Hace **polling** hasta `COMPLETED` o `FAILED` (timeout: 300s).
4. Descarga el `mapfile` generado y lo guarda en `import_mapfile_path`.

**Parámetros DSpace relevantes:**
- `-a` → add items (modo agregar)
- `-z` → nombre del ZIP
- `-c <collection>` → handle/ID de colección destino
- `-v` → validate-only (dry-run, no importa realmente) — **activo en los tests**
- `-x` → excluir bitstreams

---

### Cómo evaluarlo

#### Métricas principales

| # | Métrica | Descripción | Umbral aceptable |
|---|---------|-------------|-----------------|
| M1 | **Estado final del proceso** | `processStatus` devuelto por DSpace | **`COMPLETED`** (nunca `FAILED`) |
| M2 | **Ítems importados** | Líneas en el `mapfile` generado | `== ítems en el SAF` |
| M3 | **Mapfile descargado** | `os.path.isfile(import_mapfile_path)` | **True** |
| M4 | **Tiempo de polling** | Segundos hasta `COMPLETED` o `FAILED` | < 300s (timeout actual) |
| M5 | **Autenticación exitosa** | `dspace.login()` sin excepción | **True** |
| M6 | **Errores en el log de DSpace** | Líneas de ERROR en el log de importación | **0 errores** |

> **En modo validate-only (`-v`):** DSpace valida la estructura sin crear ítems. El `mapfile` puede estar vacío. Se evalúa que `processStatus == COMPLETED` y que no haya errores en el log.

#### Considerado "bueno"
- `processStatus == COMPLETED`.
- Mapfile con N líneas = N ítems del SAF.
- Sin errores en el log de DSpace.
- Tiempo de polling < 60s para lotes pequeños (< 100 ítems).

#### Considerado "malo"
- `processStatus == FAILED` → revisar las últimas 20 líneas del log (se imprimen automáticamente).
- Ítems en mapfile < ítems en SAF → importación parcial.
- Timeout (> 300s) → lote demasiado grande o DSpace con sobrecarga.
- Error de autenticación → credenciales o URL incorrectas.

---

## Resumen de umbrales por paso

| Paso | Métrica clave | Umbral mínimo | Umbral óptimo |
|------|--------------|---------------|---------------|
| 1 — GenerateSourceCrosswalkConfig | Columnas críticas presentes | 100% | 100% + sin reintento |
| 1 — GenerateSourceCrosswalkConfig | Validación determinista OK | Primera pasada | Primera pasada |
| 2a — MapSourceToGeneric | Retención de filas | ≥ 95% | ≥ 99% |
| 2a — MapSourceToGeneric | `id` no nulo | 100% | 100% |
| 2b — MapSediciToGeneric | Retención de filas | ≥ 99% | 100% |
| 3 — Deduplicate | Precision | ≥ 80% | ≥ 90% |
| 3 — Deduplicate | Recall | ≥ 70% | ≥ 85% |
| 3 — Deduplicate | F1-score | ≥ 0.75 | ≥ 0.87 |
| 4 — MetadataReconciliation | Retención de no-duplicados | 100% | 100% + join por ID |
| 5 — MapToSediciFormat | `dc.title` no nulo | ≥ 98% | 100% |
| 5 — MapToSediciFormat | Columnas DSpace obligatorias | Las 4 presentes | Las 4 + cobertura ≥ 99% |
| 6 — MetadataCorrections | Retención de filas | 100% | 100% |
| 6 — MetadataCorrections | `dc.language` normalizado | ≥ 95% | 100% |
| 8 — GenerateSafToImport | Ítems en SAF | == filas CSV | == filas CSV |
| 8 — GenerateSafToImport | XML válido | 100% | 100% |
| 9 — ImportToDspace | `processStatus` | COMPLETED | COMPLETED + 0 errores en log |
| 9 — ImportToDspace | Ítems en mapfile | == ítems SAF | == ítems SAF |

---

## Variables de estado relevantes para la evaluación

```python
state = {
    # Umbrales del Paso 3/4
    "umbral_seguro":   10,   # total < 10  → no duplicado → pasa al Paso 4
    "umbral_revision": 30,   # total < 30  → revisión manual
    # total >= 30             → descartado como duplicado

    # Modo dry-run en Paso 9
    "import_validate_only": True,   # True en tests; False en producción
}
```

---

*Documento generado para el pipeline en `core/graph.py` y `core/nodes/pipeline_nodes.py`.*

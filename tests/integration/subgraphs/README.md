# Pruebas de Integración de Subgrafos — `tests/integration/subgraphs/`

[← Volver a tests/integration/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/integration/subgraphs/` contiene las pruebas de integración para los tres subgrafos modulares que componen la arquitectura de LangGraph en `core/subgraphs/`:
1. **`IngestSubgraph` (`test_ingest_subgraph.py`):** Normalización de la fuente de entrada y bifurcación entre CSVs planos e ingesta de documentos PDF desde MinIO.
2. **`CrosswalkDedupSubgraph` (`test_crosswalk_dedup_subgraph.py` y `test_crosswalk_dedup_error_recovery.py`):** Mapeo conceptual al esquema genérico, deduplicación contra el repositorio local, reconciliación de atributos y circuito reactivo de autorreparación ante anomalías.
3. **`ExportSubgraph` (`test_export_subgraph.py`):** Mapeo al esquema SEDICI, saneamiento institucional, empaquetado en Simple Archive Format (SAF) e importación en DSpace.

Estas pruebas aseguran que la **topología de grafos compilada**, los **enrutadores condicionales** y los **efectos de estado** entre nodos funcionen de extremo a extremo dentro de cada subgrafo.

---

## 🔍 Qué se Evalúa

### 1. IngestSubgraph (`test_ingest_subgraph.py`)
- **Topología del Grafo:** Verificación de aristas entre `SetupWorkspace`, bifurcación condicional y nodos de ingesta.
- **Rama CSV:** Verificación de paso directo y validación de columnas obligatorias cuando `input_source_type="csv"`.
- **Rama PDF/MinIO:** Verificación del flujo de descarga desde buckets de MinIO, extracción de metadatos vía MCP y normalización a `source_from_pdfs.csv`.

### 2. CrosswalkDedupSubgraph (`test_crosswalk_dedup_subgraph.py`)
- **Topología y Routers:** Presencia de nodos clave (`GenerateSourceCrosswalkConfig`, `MapSourceToGeneric`, `BypassSourceCrosswalk`, `Deduplicate`, `MetadataReconciliation`) y enrutador condicional `route_source_crosswalk`.
- **Flujo Completo Rama CSV con Servicios Reales:** Invocación del agente LLM (`FallbackLLM`), generación de configuración JSON, llamada a la API REST de Crosswalk (`http://localhost:8000`), llamada al servicio asíncrono de Deduplicación y reconciliación de duplicados.
- **Bypass de Crosswalk para PDFs:** Verificación de que la salida curada de PDFs salte la inferencia de crosswalk y se dirija directamente al esquema genérico.

### 3. Circuito de Recuperación y Memoria Episódica (`test_crosswalk_dedup_error_recovery.py`)
- **Enrutadores Post-Deduplicación:** Evaluación de `route_post_deduplicate` ante excepciones del deduplicador (`DeduplicatorApiError`).
- **Autocorrección Reactiva:** Invocación del nodo `dedup_recovery_node`, consulta de memoria episódica, aplicación transaccional de estrategias de reparación sobre fechas/valores, y re-enrutamiento exitoso vía `route_post_recovery`.

### 4. ExportSubgraph (`test_export_subgraph.py`)
- **Topología de Exportación:** 5 nodos en pipeline lineal (`GenerateSediciTargetConfig` → `MapToSediciFormat` → `MetadataCorrections` → `GenerateSafToImport` → `ImportToDspace`).
- **Salud de Servicios y JWT:** Autenticación automática contra Crosswalk API y endpoints REST de DSpace.
- **Dry-Run:** Generación completa del paquete SAF y validación sintáctica contra DSpace con flag `-v` (sin escribir en base de datos).
- **Importación Real:** Ingesta persistente en colección de prueba, captura de identificadores en `mapfile.txt` y verificación vía consulta REST.
- **Bypass Determinista e Idempotencia:** Reutilización de configuraciones de destino sin invocar al LLM si el esquema ya es canónico o fue computado previamente.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas de integración de subgrafos
pytest tests/integration/subgraphs/ -v

# Ejecutar el subgrafo de Exportación
pytest tests/integration/subgraphs/test_export_subgraph.py -v

# Ejecutar el circuito de recuperación reactiva de deduplicación
pytest tests/integration/subgraphs/test_crosswalk_dedup_error_recovery.py -v

# Ejecutar únicamente validación de topología sin llamar servicios
pytest tests/integration/subgraphs/ -k "Topology" -v
```

---

## 📦 Dependencias y Costos

- **Servicios Docker Requeridos:**
  - Microservicio de Crosswalk y Deduplicación en `http://localhost:8000`.
  - Instancia DSpace 7+ Sandbox en `http://localhost:8080/server` (para tests de exportación real).
  - Instancia MinIO en `http://localhost:9003` (para rama PDF de ingesta).
- **Consumo de Tokens:** ~$0.01 a $0.03 si se ejecutan las pruebas completas con LLM real activado.
- **Tiempo Estimado de Ejecución:** ~30 a 70 segundos.

# Pruebas de Integración de Nodos — `tests/integration/nodes/`

[← Volver a tests/integration/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/integration/nodes/` contiene las pruebas de integración para nodos individuales de LangGraph cuyas responsabilidades demandan interacción con servicios del entorno operativo local (MinIO Object Storage, servidores de protocolo MCP, herramientas de OCR y clientes de enriquecimiento bibliográfico).

A diferencia de las pruebas unitarias de nodos en `tests/unit/nodes/`, estas pruebas ejecutan la lógica interna del nodo permitiendo la comunicación real con sockets, contenedores o subsistemas cuando estos se encuentran disponibles en el entorno de pruebas, verificando la robustez de los mecanismos de conexión, parsing de streams y persistencia en disco.

---

## 🔍 Qué se Evalúa

Se evalúan tres nodos críticos de preprocesamiento e ingesta:

### 1. Ingesta de Documentos PDF (`test_pdf_ingest_node.py`)
- **Nodo Evaluado:** `pdf_ingest_node` (`core/nodes/ingest_nodes.py`).
- **Conexión a MinIO:** Autenticación y listado de objetos binarios (PDFs) en el bucket `importacion` de MinIO (`localhost:9003`).
- **Integración con MCP Metadata Extractor:** Invocación del servidor MCP vía HTTP (`http://localhost:9604/mcp`) para delegar el parsing y extracción de texto/metadatos.
- **Normalización de Salida:** Generación del archivo unificado `source_from_pdfs.csv` en el directorio de trabajo temporal.
- **Tolerancia a Desconexión:** Salto automático (*skip*) mediante fixtures condicionales si el bucket o el servidor MCP no están levantados.

### 2. Curación de Metadatos de PDFs (`test_curation_node.py`)
- **Nodo Evaluado:** `curate_metadata_node` (`core/nodes/curation_nodes.py`).
- **Pipeline de Curación en Lote:** Carga del CSV extraído de PDFs, análisis heurístico de anomalías OCR fila por fila y aplicación encadenada de correctores programáticos.
- **Triaje y Delegación:** Identificación de registros severamente degradados y derivación al agente curador basado en LLM (`MetadataCuratorAgent`).
- **Integración E2E Ingest → Curation:** Verificación del pasaje de estado entre la salida del nodo de ingesta y la entrada del nodo de curación.

### 3. Enriquecimiento Bibliográfico (`test_enrichment_node.py`)
- **Nodo Evaluado:** `enrich_metadata_node` y `route_enrichment` (`core/nodes/enrichment_nodes.py`).
- **Enrutamiento Condicional:** Verificación de `route_enrichment` para decidir si el lote requiere paso por enriquecedores o debe avanzar directamente a deduplicación.
- **Actualización In-Place:** Lectura del archivo `generic_source_csv_path`, consulta a los adaptadores de enriquecimiento y escritura atómica de campos completados.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas de integración de nodos
pytest tests/integration/nodes/ -v

# Ejecutar únicamente la prueba de curación de metadatos
pytest tests/integration/nodes/test_curation_node.py -v

# Ejecutar la prueba de ingesta de PDFs capturando mensajes de log
pytest tests/integration/nodes/test_pdf_ingest_node.py -v -s --log-cli-level=INFO
```

---

## 📦 Dependencias y Costos

- **Servicios de Entorno:**
  - MinIO S3 (`http://localhost:9003`) con credenciales estándar de testing (`minioadmin` / `minioadmin`).
  - Servidor MCP Metadata Extractor en puerto `9604`.
  - Herramientas Tesseract OCR instaladas a nivel sistema si se ejecutan tests de extracción local.
- **Políticas de Resiliencia en Test:** Si MinIO o el servidor MCP no responden en el puerto correspondiente, las pruebas no fallan con error espurio sino que emiten un aviso y se marcan como *skipped*.
- **Tiempo Estimado de Ejecución:** ~20 a 40 segundos.
- **Costo en Tokens:** **$0.00** (a menos que se active la curación LLM real en el test de curación).

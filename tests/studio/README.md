# Herramientas de Depuración e Interacción — `tests/studio/`

[← Volver a tests/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/studio/` contiene scripts de ejecución manual, utilidades de inspección en tiempo de ejecución y puntos de entrada para la depuración interactiva del pipeline con **LangGraph Studio** y trazabilidad en **LangSmith**.

> [!IMPORTANT]
> Los archivos en esta carpeta **NO son tests de Pytest**. Son scripts ejecutables independientes de Python pensados para que el desarrollador o evaluador pueda reproducir manualmente corridas completas o parciales del grafo, observar trazas de razonamiento en vivo y generar artefactos intermedios en disco para auditoría visual.

---

## 🔍 Scripts Disponibles

### `studio/run_pipeline.py`
Es el orquestador interactivo principal para pruebas de humo (*smoke testing*) y depuración paso a paso.

- **Datos de Entrada Predeterminados:** Procesa automáticamente una muestra de Springer (`SearchResults.csv`) y la contrasta contra el catálogo local de SEDICI (`export_10915_all.csv`).
- **Control de Ejecución con `--stop-after`:** Permite cortar deliberadamente el flujo tras completar una etapa específica, facilitando la depuración aislada de un nodo sin esperar la finalización de todo el pipeline:
  - `paso1` / `crosswalk_config`: Se detiene tras generar la configuración de mapeo con el LLM.
  - `paso2a` / `map_source`: Se detiene tras aplicar el crosswalk a la fuente.
  - `paso2b` / `map_sedici`: Se detiene tras mapear el catálogo SEDICI al esquema genérico.
  - `paso3` / `deduplicate`: Se detiene tras obtener la matriz de duplicados.
  - `paso4` / `reconciliation`: Se detiene tras la reconciliación de metadatos.
  - `paso5` / `map_sedici_format`: Se detiene tras el formateo a esquema SEDICI.
  - `paso8` / `saf`: Se detiene tras generar la estructura de carpetas SAF.
  - `paso9` / `import`: Ejecuta hasta el depósito final en DSpace.
- **Reporte en Consola:** Emite una tabla formateada que detalla para cada paso:
  - Tiempo transcurrido en segundos.
  - Rutas absolutas a los archivos CSV o paquetes generados.
  - Advertencias y errores detectados en el estado del grafo.

---

## 🚀 Cómo Ejecutar los Scripts

```bash
# 1. Ejecutar el pipeline completo con reportería en consola
python tests/studio/run_pipeline.py

# 2. Ejecutar solo hasta la generación de la configuración de crosswalk
python tests/studio/run_pipeline.py --stop-after 1

# 3. Ejecutar hasta la deduplicación inspeccionando el CSV reconciliado
python tests/studio/run_pipeline.py --stop-after 4
```

---

## 📦 Dependencias y Costos

- **Servicios Externos:** Depende de los servicios configurados en el archivo `.env`:
  - Si se utiliza con clientes reales de Docker y LLM, consume tokens de API y requiere el contenedor `deduplicator_crosswalk_web`.
  - Si se utiliza en modo de prueba sin red, conmuta internamente a clientes emulados (`_FakeDeduplicatorClient`).
- **Trazabilidad en LangSmith:** Si están configuradas las variables `LANGCHAIN_TRACING_V2=true` y `LANGCHAIN_API_KEY`, cada ejecución registra el grafo visual interactivo en la plataforma de observabilidad de LangChain.

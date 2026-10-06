# Pruebas Unitarias de Memoria Episódica — `tests/unit/memory/`

[← Volver a tests/unit/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/memory/` concentra las pruebas unitarias para el sistema de Memoria Episódica basado en Razonamiento Basado en Casos (CBR - *Case-Based Reasoning*) del pipeline (`core/memory/episodic_memory/`).

Este subsistema permite que el agente de recuperación reactiva aprenda de fallos pasados y resoluciones humanas previas: almacena cada incidente de datos junto con su solución validada, permitiendo recuperar precedentes idénticos o análogos para resolver anomalías de forma autónoma sin repetir errores ni consultar al operador innecesariamente.

---

## 🔍 Qué se Evalúa

Las pruebas (implementadas en `test_episodic_memory.py`) evalúan:

1. **Modelos de Datos y Tipado Estricto:**
   - `Episode`: Estructura raíz que encapsula el identificador único, timestamps y relaciones.
   - `EpisodeMetadata`: Metadatos indexables (`tool`, `source_name`, `error_class`, `affected_column`, `resolved_by`, `resolution_strategy`).
   - `EpisodePayload`: Carga útil del caso (fragmento de error textual, fragmento de CSV original, transformación aplicada y diff de metadatos).
   - `EpisodeQueryResult`: Estructura de resultado de búsqueda con score de relevancia.
2. **Almacén de Memoria (`EpisodicMemoryStore`):**
   - Inserción y persistencia de nuevos episodios en directorios temporales aislados (`tmp_path`).
   - Búsqueda por similitud vectorial combinada con filtros booleanos de metadatos.
   - Verificación de consistencia ante reinicios y recarga de la base de datos embebida.
3. **Mecanismo de Recuperación Jerárquica (`retriever`):**
   - `extract_error_context`: Extracción de firmas de error a partir de excepciones y logs de traceback.
   - `build_search_query`: Construcción de consultas enriquecidas que combinan la excepción de Python con el contexto de la columna del CSV.
   - `retrieve_relevant_episodes`: Estrategia de recuperación en cascada:
     - *Nivel 1 (Filtro Duro):* Coincidencia exacta de herramienta y clase de error.
     - *Nivel 2 (Similitud Semántica):* Ordenamiento por distancia de embedding del snippet problemático.
     - *Nivel 3 (Filtro de Relevancia):* Descarte de episodios distractores o con baja afinidad de esquema.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas de memoria episódica
pytest tests/unit/memory/ -v

# Ejecutar con salida detallada
pytest tests/unit/memory/test_episodic_memory.py -vv
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (utiliza vector store local/embebido y embeddings deterministas locales o mockeados).
- **Almacenamiento:** Carpetas efímeras creadas en `tempfile.TemporaryDirectory()`.
- **Tiempo Estimado de Ejecución:** < 4 segundos.
- **Costo en Tokens:** **$0.00**.

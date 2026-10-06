# Persistencia, Checkpointers e Hilos — `tests/integration/checkpointer_and_threads/`

[← Volver a tests/integration/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/integration/checkpointer_and_threads/` está dedicado a las pruebas de integración de los mecanismos de persistencia de estado de LangGraph (`MemorySaver`, `SqliteSaver` o adaptadores PostgreSQL).

En un pipeline de importación masiva de larga duración, la persistencia por hilos (`thread_id`) es un requisito fundamental de diseño para soportar:
1. **Tolerancia a Fallos:** Capacidad de recuperar la ejecución exactamente desde el último nodo exitoso ante una caída imprevista del proceso o del servidor.
2. **Pausa y Reanudación HITL (*Human-in-the-Loop*):** Detención deliberada del pipeline mediante `interrupt()` cuando se requiere decisión humana (validación de mapeos ambiguos o autorización de importación), permitiendo que el sistema guarde el snapshot del estado y espere la respuesta del usuario.
3. **Reanudación Idempotente:** Garantizar que al llamar a `resume()` no se repitan efectos colaterales costosos (transformaciones previas, subidas de archivos o importaciones ya realizadas).
4. **Capacidad de Time-Travel:** Capacidad de inspeccionar estados históricos de un hilo e iniciar una ejecución alternativa modificando variables de entrada sin alterar la corrida original.

---

## 🔍 Qué se Evalúa

1. **Serialización y Deserialización del Estado (`State`):**
   - Verificación de que todos los tipos presentes en `State` (rutas a archivos CSV en disco, diccionarios de configuración de crosswalk, listas de errores y metadatos) sean serializables por el checkpointer sin pérdida de fidelidad.
2. **Ciclo de Vida de Interrupción y Reanudación:**
   - Invocación de un subgrafo con un `thread_id` específico que se suspende al alcanzar un nodo con `interrupt()`.
   - Inspección del snapshot guardado en la base de datos de checkpoints.
   - Envío del comando de reanudación inyectando el valor de decisión humana (`Command(resume=...)`) y validación de que el pipeline avance fluidamente hacia el nodo sucesor.
3. **Idempotencia de Ejecución:**
   - Verificación de que los nodos anteriores al punto de interrupción no se vuelvan a invocar al reanudar.
   - Validación de que los archivos CSV generados en pasos previos permanezcan inalterados y no sufran duplicación de filas.
4. **Aislamiento Multi-Hilo:**
   - Ejecución concurrente o secuencial de dos importaciones independientes con distintos `thread_id` asegurando que no exista contaminación cruzada de variables en el estado compartido.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas de persistencia y checkpointers
pytest tests/integration/checkpointer_and_threads/ -v

# Ejecutar con trazabilidad de checkpoints
pytest tests/integration/checkpointer_and_threads/ -vv -s
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (utiliza `MemorySaver` en memoria o bases de datos SQLite locales en `tmp_path`).
- **Contenedores Docker:** No requiere contenedores externos para tests con checkpointers locales.
- **Tiempo Estimado de Ejecución:** < 10 segundos.
- **Costo en Tokens:** **$0.00**.

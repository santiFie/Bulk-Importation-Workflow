# Fixtures y Mocks Compartidos — `tests/fixtures/`

[← Volver a tests/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/fixtures/` centraliza los generadores de datos sintéticos, módulos de emulación compartidos, grabaciones de red (VCR/cassettes) y definiciones globales de fixtures para la suite de pruebas de Pytest.

Su propósito arquitectónico es:
1. **Evitar la Duplicación de Código:** Proveer helpers estandarizados para generar estructuras complejas (estados de LangGraph, respuestas multipart de microservicios, paquetes SAF simulados) accesibles desde cualquier nivel de la suite.
2. **Garantizar el Aislamiento:** Gestionar el ciclo de vida de creación y destrucción de recursos efímeros (`teardown`), previniendo colisiones en el sistema de archivos cuando los tests se ejecutan en paralelo o en entornos de CI.
3. **Reproducibilidad:** Ofrecer grabaciones estáticas de respuestas de APIs externas para asegurar que los cambios en servicios de terceros no rompan las pruebas sin previo aviso.

---

## 🔍 Qué Proporciona este Módulo

1. **Generadores Sintéticos de Datos Tabulares:**
   - Creación bajo demanda de archivos CSV con delimitadores arbitrarios, encodings dispares y combinaciones específicas de columnas (faltantes, corruptas, válidas).
   - Simulación de lotes de metadatos de gran volumen para pruebas de estrés y rendimiento.
2. **Mocks de Clientes de Servicios:**
   - Emulación de `CrosswalkClient`: Respuestas prefabricadas de transformaciones exitosas, errores de parseo de sintaxis JSON y caídas con códigos HTTP 500.
   - Emulación de `DeduplicatorClient`: Respuestas inmediatas o demoradas de tareas de deduplicación con matrices de similitud sintéticas.
3. **Estados Sintéticos de LangGraph (`State`):**
   - Diccionarios base con valores por defecto validados según la definición tipada en `core/state.py`.
   - Modificadores fluidos para inyectar errores controlados en campos como `node_errors`, `input_source_type` o `dspace_collection`.
4. **Emulación de Respuestas de Enriquecimiento:**
   - Payloads JSON congelados de las APIs de Crossref, OpenAlex y OpenLibrary para casos canónicos de prueba (artículos con DOI, libros con ISBN).

---

## 🚀 Cómo se Utilizan las Fixtures

Las fixtures declaradas en este módulo y en los archivos `conftest.py` son descubiertas y resueltas automáticamente por Pytest:

```python
# Ejemplo de consumo en un test:
def test_mi_nodo(base_state, make_csv, mock_crosswalk_client):
    csv_path = make_csv("datos.csv", [{"id": "1", "title": "Paper A"}])
    base_state["source_csv_path"] = csv_path
    
    # Ejecución con mocks listos...
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (totalmente local).
- **Consumo de Memoria:** Efímero, liberado al finalizar cada sesión de prueba.
- **Costo en Tokens:** **$0.00**.

# Pruebas Unitarias — `tests/unit/`

[← Volver a tests/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/` aloja las pruebas unitarias del pipeline de importación masiva. Su objetivo primordial es garantizar la **corrección lógica interna**, la **robustez ante casos borde** y la **integridad de las transformaciones de datos** a nivel de componentes individuales y funciones puras, sin depender de servicios externos.

### Principios Rectores:
1. **Determinismo Absoluto:** Las pruebas deben arrojar el mismo resultado idéntico en cada ejecución, sin depender de estados compartidos, latencia de red ni variación estocástica de LLMs.
2. **Alta Velocidad (< 10 segundos):** La totalidad de la suite unitaria debe ejecutarse en menos de 10 segundos para habilitar ciclos rápidos de desarrollo y TDD (Test-Driven Development).
3. **Cero Red (Offline):** Ningún test unitario puede establecer conexiones HTTP/TCP externas. Toda comunicación externa debe ser interceptada con mocks (`unittest.mock.patch`, MagicMock).
4. **Cero LLM Real:** Los agentes y nodos con componentes de lenguaje deben ser evaluados con respuestas estructuradas sintéticas o invocaciones mockeadas.

---

## 🔍 Qué se Evalúa

La suite unitaria abarca los siguientes dominios arquitectónicos:

- **Estructura y Topología de Grafos (`test_graph_structure.py`):** Validación de la compilación de `StateGraph`, presencia de nodos obligatorios y consistencia de las aristas.
- **Transformaciones de Metadatos (`test_source_to_generic.py`, `test_target_crosswalk_agent.py`):** Mapeo de columnas fuente al esquema canónico y al formato SEDICI mediante funciones locales y heurísticas.
- **Componentes Especializados (Subdirectorios):**
  - **[clients/](clients/README.md):** Manejo de resiliencia HTTP, parsing de respuestas, manejo de JWT y expiración de tokens.
  - **[nodes/](nodes/README.md):** Aislamiento de cada uno de los 13+ nodos LangGraph mediante inyección de mocks sobre el estado `State`.
  - **[tools/](tools/README.md):** Motores locales de crosswalk y transformaciones CSV (`CsvHandler`, `Crosswalk`).
  - **[utils/](utils/README.md):** Text fixers deterministas, detectores heurísticos de anomalías OCR y validación temprana Fail-Fast de CSVs.
  - **[agent/](agent/README.md):** Agente reactivo de recuperación de errores, despacho de estrategias polimórficas y transacciones atómicas `CsvTransaction`.
  - **[enrichers/](enrichers/README.md):** Clientes de consulta a APIs bibliográficas con payloads emulados (Crossref, OpenAlex, OpenLibrary).
  - **[memory/](memory/README.md):** Almacenamiento y recuperación jerárquica de episodios de error (CBR - Case-Based Reasoning).

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas unitarias
pytest tests/unit/ -v

# Ejecutar con reporte de cobertura sobre el paquete core/
pytest tests/unit/ --cov=core --cov-report=term-missing

# Ejecutar una subcarpeta específica (ej. nodos)
pytest tests/unit/nodes/ -v

# Ejecutar un archivo individual
pytest tests/unit/utils/test_text_fixers.py -v
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** Ninguna (100% aisladas del entorno de red).
- **Contenedores Docker:** No requiere Docker ni bases de datos activas.
- **Tiempo Estimado de Ejecución:** ~3 a 6 segundos para toda la carpeta.
- **Costo en Tokens de LLM:** **$0.00** (sin consumo de API de inferencia).

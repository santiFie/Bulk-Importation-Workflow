# Métricas Cuantitativas y Jueces LLM — `tests/evals/metrics/`

[← Volver a tests/evals/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/evals/metrics/` proporciona los algoritmos de puntuación, evaluadores cuantitativos y jueces automatizados (*LLM-as-a-Judge*) utilizados para medir de forma estandarizada la calidad de los procesos agénticos en el pipeline.

Permite transformar observaciones cualitativas de texto en **métricas estadísticas reproducibles** para la tesis, permitiendo graficar curvas de precisión, tasas de recuperación y costos de inferencia a través de diferentes versiones del sistema.

---

## 🔍 Qué se Evalúa

Las métricas se dividen en dos categorías complementarias:

### 1. Métricas Deterministas y Heurísticas
- **Exact Match Ratio (EMR):** Porcentaje de campos donde la salida saneada coincide carácter por carácter con el Ground Truth.
- **Distancia de Levenshtein Normalizada:** Medida de divergencia sintáctica entre cadenas textuales (títulos, autores, nombres de revistas).
- **Métricas de Clasificación de Deduplicación:**
  - *Precision:* Proporción de duplicados detectados que efectivamente son idénticos.
  - *Recall:* Proporción de duplicados reales presentes en el lote que fueron detectados por el sistema.
  - *F1-Score:* Media armónica balanceada entre precisión y exhaustividad.
- **Validez Estructural:** Conformidad estricta contra esquemas JSON de crosswalk y especificaciones XML de SEDICI / DSpace.

### 2. Jueces LLM (LLM-as-a-Judge) y Similitud Semántica
- **Juez de Fidelidad de Metadatos:** Un modelo evaluador independiente (e.g. GPT-4o o Claude 3.5 Sonnet) califica de 1 a 5 si la corrección realizada preservó el significado original del documento sin añadir información espuria.
- **Similitud Coseno de Embeddings:** Medición de distancia semántica entre títulos curados y títulos originales para detectar alucinaciones destructivas.

---

## 📊 Integración con LangSmith y Observabilidad

Las métricas calculadas se integran con **LangSmith** para:
- Registrar ejecuciones de evaluación en datasets remotos (`Pipeline_Integration_Tests`).
- Comparar ejecuciones entre diferentes versiones de prompts.
- Visualizar trazas completas de razonamiento (*reasoning traces*) de cada agente durante las sesiones de prueba.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar pruebas unitarias de funciones de cálculo de métricas
pytest tests/evals/metrics/ -v

# Ejecutar evaluadores de métricas con salida detallada
pytest tests/evals/metrics/ -vv
```

---

## 📦 Dependencias y Costos

- **Métricas Heurísticas:** 0 red, tiempo < 1 segundo, **$0.00**.
- **Jueces LLM (si se invocan):** Requiere conexión a API y genera costo proporcional al número de pares evaluados (~$0.01 por 100 evaluaciones de texto).

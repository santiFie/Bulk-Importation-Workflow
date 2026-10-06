# Datasets de Evaluación y Ground Truth — `tests/evals/datasets/`

[← Volver a tests/evals/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/evals/datasets/` define y gestiona los conjuntos de datos de evaluación de referencia (*Golden Sets*) y las anotaciones de verdad fundamental (*Ground Truth*) empleados para medir rigurosamente el rendimiento de los agentes y modelos del sistema.

En el marco de la tesis, estos datasets proporcionan la **línea base inmutable (benchmark)** que permite contrastar empíricamente distintas estrategias de prompting, modelos de lenguaje y mecanismos de memoria episódica a lo largo del tiempo.

---

## 🔍 Qué se Evalúa

1. **Casos Canónicos de Curación de Metadatos:**
   - Datasets JSON estructurados (como `curation_dataset.json` en `tests/data/ingest/curation/`) que registran:
     - *Entrada bruta:* Metadatos con artefactos OCR reales (palabras desarticuladas, códigos CID, ausencia de autores).
     - *Decisión esperada:* Si el registro debe resolverse mediante correctores deterministas, derivarse al LLM o requerir escalamiento HITL.
     - *Salida esperada (Ground Truth):* Diccionario de metadatos completamente saneado.
2. **Pares de Deduplicación y Reconciliación:**
   - Pares de registros sintéticos y reales clasificados con etiqueta manual:
     - *Duplicados Ciertos (True Positives):* Similitud esperada > 90%.
     - *No Duplicados (True Negatives):* Ítems con títulos similares pero autores o años distintos.
     - *Casos Frontera (Edge Cases):* Registros con similitud en el rango de incertidumbre [80%, 90%].
3. **Muestras de Esquemas Heterogéneos:**
   - Muestras representativas de fuentes científicas indexadas (Springer Nature, artículos UNLP, metadatos OAI-PMH) con configuraciones de crosswalk esperadas validadas por bibliotecarios de SEDICI.

---

## 📋 Políticas de Versionado e Integridad

- **Inmutabilidad:** Ningún archivo de Ground Truth debe ser alterado una vez establecida la línea base experimental de la tesis, a menos que se documente explícitamente una corrección de etiquetado erróneo.
- **Anonimización:** Todo dataset que incluya datos personales debe cumplir las políticas de privacidad institucionales, limitándose a publicaciones de acceso abierto.
- **Tamaño Acotado:** Los archivos deben ser lo suficientemente compactos (< 5 MB) para permitir una rápida carga en memoria durante las sesiones de evaluación.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Validar la integridad sintáctica y esquemas de los datasets de evaluación
pytest tests/evals/datasets/ -v

# Inspeccionar dataset de curación en consola
python -c "import json; data=json.load(open('tests/data/ingest/curation/curation_dataset.json')); print(f'Casos cargados: {len(data)}')"
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (archivos locales en `tests/data/`).
- **Tiempo Estimado:** < 5 segundos.
- **Costo en Tokens:** **$0.00**.

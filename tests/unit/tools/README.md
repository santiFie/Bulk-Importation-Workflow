# Pruebas Unitarias de Herramientas y Motores Locales — `tests/unit/tools/`

[← Volver a tests/unit/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/tools/` contiene las pruebas unitarias para las herramientas y motores locales de transformación de datos (`core/scripts/crosswalk/`), incluyendo:
- `Crosswalk`: Motor determinista de aplicación de reglas de mapeo semántico basadas en especificaciones JSON.
- `CsvHandler`: Gestor de bajo nivel para la manipulación, lectura y serialización de archivos CSV con soporte de dialectos heterogéneos.
- `CrosswalkContext`: Objeto de contexto que transporta el estado y las reglas de transformación en memoria.

Estas pruebas aseguran que las transformaciones tabulares locales funcionen con absoluta precisión matemática y consistencia sintáctica sin necesidad de instanciar microservicios web ni interactuar con APIs remotas.

---

## 🔍 Qué se Evalúa

Las pruebas (implementadas en `test_deduplicator.py`) evalúan:

1. **Lectura y Escritura Robusta de CSVs:**
   - Detección automática de dialectos (delimitadores `,`, `;`, tabuladores).
   - Manejo de encodings complejos (`utf-8`, `latin-1`) y cadenas con saltos de línea y caracteres especiales entrecomillados.
2. **Motor de Reglas de Crosswalk:**
   - Mapeo directo de columnas origen a destinos canónicos.
   - Aplicación de transformaciones encadenadas: expresiones regulares (`replace`), filtros de inclusión/exclusión (`filter`) y valores por defecto (`default`).
   - Gestión de campos marcados como `required` (emisión de advertencias o exclusión de registros).
3. **Compatibilidad con Esquemas Institucionales:**
   - Validación de conversiones con perfiles de metadatos reales de SEDICI (`sedici_input.csv`, `export_10915_all.csv`).
   - Validación de conversiones al estándar OAI-DC (`oaidc_input.csv`).
   - Transformaciones complejas de fuentes históricas (e.g., `result-14531-Romero.csv`).
4. **Casos Borde y Degradación:**
   - Archivos CSV vacíos (`empty.csv`).
   - Columnas con nombres duplicados o valores nulos persistentes.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas de herramientas locales
pytest tests/unit/tools/ -v

# Ejecutar con detalle de salidas de assert
pytest tests/unit/tools/test_deduplicator.py -vv
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (totalmente local).
- **Archivos de Entrada:** Utiliza datasets estáticos de muestra ubicados en `tests/data/crosswalk_dedup/`.
- **Tiempo Estimado de Ejecución:** < 2 segundos.
- **Costo en Tokens:** **$0.00**.

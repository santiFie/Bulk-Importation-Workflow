# Pruebas Unitarias de Utilidades y Heurísticas — `tests/unit/utils/`

[← Volver a tests/unit/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/utils/` aloja las pruebas para las funciones auxiliares de bajo nivel, correctores programáticos deterministas, detectores heurísticos de anomalías textuales y validadores tempranos de integridad estructural (`core/utils/` y `core/nodes/validation_node.py`).

Garantiza que el sistema detecte inconsistencias de entrada lo antes posible (principio *Fail-Fast*) y corrija automáticamente defectos sistemáticos producidos durante la extracción óptica de caracteres (OCR) y parsing de PDFs sin requerir costosas llamadas a modelos LLM.

---

## 🔍 Qué se Evalúa

Se organizan en tres suites principales de testeo:

### 1. Correctores Deterministas de Texto (`test_text_fixers.py`)
Evalúa los algoritmos de saneamiento textual en `core/utils/text_fixers.py`:
- `fix_spaced_chars`: Reparación de palabras desarticuladas por exceso de espacios inter-carácter (e.g. `"U N L P"` → `"UNLP"`).
- `remove_cid_artifacts`: Eliminación de caracteres CID corruptos generados por fuentes no mapeadas en PDFs (e.g. `"(cid:123)"`).
- `deduplicate_cyclic_text`: Detección y supresión de frases duplicadas de forma cíclica por errores de layout.
- `fix_glued_words`: Separación de términos fusionados por ausencia de espacios en el stream de texto.
- `normalizar_autores`: Conversión determinista a formato estándar `"Apellido, Nombre"` y normalización de separadores.
- `aplicar_correctores_programaticos`: Pipeline encadenado de limpieza sobre diccionarios y filas de metadatos.

### 2. Detectores Heurísticos de Anomalías (`test_heuristic_detectors.py`)
Evalúa los componentes de análisis cuantitativo en `core/utils/heuristic_detectors.py`:
- Métricas individuales de anomalía: ratio de caracteres dispersos, frecuencia de artefactos CID, repetición de n-gramas, caracteres de control no imprimibles y longitudes desproporcionadas.
- `calcular_score_anomalia`: Fusión ponderada de métricas en un score normalizado \([0.0, 1.0]\).
- `triar_registros`: Clasificación de registros en tres grupos operativos:
  - *Registros Limpios* (pasan directo).
  - *Reparables Programáticamente* (reparados por fixers deterministas).
  - *Anomalías Severas* (enviados a curación agéntica con LLM o revisión humana).

### 3. Validación Temprana de CSVs (`test_input_csv_validation.py`)
Evalúa el nodo `validate_input_csvs_node`:
- **Fail-Fast:** Rechazo inmediato ante archivos inexistentes, archivos de 0 bytes o CSVs sin registros útiles.
- **Campos Críticos:** Exigencia de al menos `title` o `doi` en el CSV fuente; rechazo si ambos faltan.
- **Autoincremento de IDs:** Generación transparente y determinista de columna `id` autoincremental si la fuente no proporciona identificador nativo.
- **Esquema SEDICI:** Validación estricta de columnas requeridas en el archivo exportado del repositorio local.

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar toda la suite de utilidades
pytest tests/unit/utils/ -v

# Ejecutar únicamente los detectores heurísticos
pytest tests/unit/utils/test_heuristic_detectors.py -v

# Ejecutar validaciones de entrada de CSVs
pytest tests/unit/utils/test_input_csv_validation.py -v
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (totalmente local y determinista).
- **Contenedores Docker:** Ninguno.
- **Tiempo Estimado de Ejecución:** < 2 segundos.
- **Costo en Tokens:** **$0.00**.

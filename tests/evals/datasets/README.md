# Datasets de Evaluación y Ground Truth — `tests/evals/datasets/`

[← Volver a tests/evals/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/evals/datasets/` gestiona los conjuntos de datos declarativos de evaluación de referencia (*Golden Datasets*) y las verdades fundamentales (*Ground Truth*) empleados para medir rigurosamente el rendimiento de los agentes LLM del pipeline.

En el marco de la tesis de grado, estos datasets constituyen la **línea base inmutable y reproducible (benchmark)** que permite contrastar empíricamente distintas estrategias de prompting, modelos de lenguaje (Llama-3, DeepSeek, Nvidia NIM) y arquitecturas de agentes a lo largo del tiempo.

---

## 📐 Estructura y Esquema Declarativo

Siguiendo las directrices metodológicas de `agent-eval-creator`, los datasets de evaluación se definen en formato JSON declarativo con un esquema estandarizado:

```json
{
  "id_caso": "identificador_unico_del_caso",
  "descripcion": "Explicación del contexto, anomalía evaluada o desafío semántico",
  "inputs": {
    "campo_entrada_1": "...",
    "headers": ["..."],
    "sample_rows": [ ... ]
  },
  "reference_outputs": {
    "resultado_esperado": "...",
    "decision_esperada": "...",
    "tools_esperadas": ["..."]
  },
  "tags": ["agente", "categoria", "dificultad"]
}
```

### Principios de Construcción de Casos:
1. **Datos Reales y Válidos:** No se emplean cadenas ficticias o incoherentes (`"foo"`, `"bar"`) que causen fallos colaterales en parsers o APIs de validación. Los casos provienen de extracciones reales de PDFs o fuentes científicas indexadas.
2. **Exhaustividad y Exclusión:** Cada caso declara de forma unívoca qué resultado es el correcto. Está prohibido el "verde por casualidad" (no se admite `A or B` si el contrato espera uno específico).
3. **Casos Límite y Negativos:** Se incorporan deliberadamente casos limpios (para evaluar no-sobrecorrección), casos truncados y casos con instrucciones ambiguas para verificar la abstención y el pedido de intervención humana (HITL).

---

## 📂 Inventario y Objetivos de los Datasets

La suite se organiza en subdirectorios correspondientes a cada agente evaluado:

```text
tests/evals/datasets/
├── curator/
│   ├── curator_clean_baseline.json       (15 casos) ← Test de sobrecorrección (over-correction)
│   └── curator_decision_cases.json       (8 casos)  ← Ruteo de decisiones y preservación de campos
├── source_to_generic/
│   ├── source_mapping_cases.json         (10 casos) ← Mapeo conceptual de fuentes heterogéneas
│   ├── source_doi_enrichment_cases.json  (7 casos)  ← Trayectoria de tools (DOI Crossref/OpenAlex)
│   ├── source_feedback_recovery_cases.json (5 casos) ← Resiliencia y autorreparación ante feedback
│   └── source_regex_react_cases.json     (6 casos)  ← Inferencia y aplicación de Regex con ReAct
└── target_crosswalk/
    └── target_crosswalk_cases.json       (7 casos)  ← Mapeo a SEDICI/DSpace y reparación de usuario
```

---

### 1. `curator/` — Agente de Curación de Metadatos

#### 📄 `curator_clean_baseline.json` (15 casos)
- **Objetivo Central:** Evaluar la **resistencia a la sobrecorrección (*over-correction*)**. Cuando un registro ingresa con metadatos limpios, completos y válidos, el agente debe abstenerse de modificar los valores o inventar datos ausentes.
- **Campos Estructurados:**
  - `id`: Identificador del benchmark (`baseline_clean_01` a `baseline_clean_15`).
  - `case_type`: Tipo de documento (artículo científico, tesis doctoral, informe técnico).
  - `metadata`: Diccionario con metadatos sanos (`title`, `authors`, `abstract`, `publication_date`, etc.).
  - `expected_category`: Categoría asignada (`clean`).
  - `has_persistent_identifier`: Booleano que indica presencia de DOI o Handle.
- **Métrica Clave:** Tasa de invariancia (los metadatos de salida deben ser idénticos a los de entrada).

#### 📄 `curator_decision_cases.json` (8 casos)
- **Objetivo Central:** Evaluar la **toma de decisiones y ruteo semántico** ante anomalías reales de extracción OCR (palabras partidas por guiones, pérdida de autores, códigos CID corruptos).
- **Campos Estructurados:**
  - `id_caso`: Identificador unívoco (`curator_decision_01`, etc.).
  - `input_fila`: Fila con anomalías extraídas del PDF.
  - `campo_afectado`: Nombre de la columna con error (e.g., `dc.title`).
  - `campo_intacto`: Columna sana que no debe sufrir modificaciones colaterales.
  - `valor_intacto_esperado`: Valor exacto que debe mantenerse inalterado.
  - `espera_reparacion`: Booleano que indica si el campo requiere reparación LLM.
  - `espera_curation_needed`: Booleano que indica si el flag de curación se activa.
  - `expected_tools` / `forbidden_tools`: Listas explícitas de herramientas autorizadas e indebidas.

---

### 2. `source_to_generic/` — Crosswalk Fuente a Genérico

#### 📄 `source_mapping_cases.json` (10 casos)
- **Objetivo Central:** Evaluar el **mapeo conceptual directo** entre columnas heterogéneas de fuentes externas (Springer Nature, SciELO, revistas UNLP, repositorios OAI-PMH) y los atributos normalizados del esquema intermedio.
- **Campos Estructurados:**
  - `inputs`: Encabezados fuente (`headers`) y filas de muestra representativas (`sample_rows`).
  - `reference_outputs`: Mapeo esperado `{ "columna_fuente": "campo_generico" }` y nivel de confianza esperado.
- **Métrica Clave:** Exactitud campo a campo (`field_mapping_accuracy`).

#### 📄 `source_doi_enrichment_cases.json` (7 casos)
- **Objetivo Central:** Evaluar la **trayectoria y despacho de herramientas de enriquecimiento**. Verifica que cuando el esquema fuente incluye identificadores persistentes (DOI) pero carece de metadatos bibliográficos clave (autores, afiliación, ISSN), el agente invoque las tools de consulta externa (`doi_lookup` en Crossref / OpenAlex) para completar el registro.
- **Validaciones:** Herramientas requeridas presentes, sin herramientas redundantes.

#### 📄 `source_feedback_recovery_cases.json` (5 casos)
- **Objetivo Central:** Evaluar la **capacidad de autorreparación ante feedback**. Simula fallas en la validación sintáctica del mapeo preliminar (e.g., ausencia de campo obligatorio `title`, tipo de dato erróneo) e inyecta el error como mensaje de feedback para que el agente rectifique su propuesta sin repetir el error.
- **Validaciones:** Resolución exitosa de la inconsistencia en el siguiente paso de razonamiento.

#### 📄 `source_regex_react_cases.json` (6 casos)
- **Objetivo Central:** Evaluar el **razonamiento ReAct para síntesis y aplicación de expresiones regulares**. Se presenta texto amalgamado en columnas compuestas (e.g., `"Volumen 12, Nro 3, Págs 100-120 (2021)"`) y se evalúa la formulación de regex precisas para separar campos individuales.
- **Validaciones:** Regex sintácticamente válida, sin capturas vacías y concordancia exacta con los campos destino.

---

### 3. `target_crosswalk/` — Crosswalk Genérico a Destino

#### 📄 `target_crosswalk_cases.json` (7 casos)
- **Objetivo Central:** Evaluar la **proyección semántica hacia esquemas institucionales (SEDICI y DSpace)** y la **autorreparación de configuraciones erróneas provistas por el usuario**.
- **Aspectos Evaluados:**
  1. Mapeo a esquema institucional SEDICI (`sedici.*`).
  2. Mapeo a esquema estándar DSpace (`dc.*`).
  3. Generación dinámica de esquema ante vocabularios no documentados.
  4. Detección y reparación de configuración desactualizada provista por el operador humano (`user-provided config repair`), corrigiendo tipos o calificadores obsoletos sin alterar las claves correctas.

---

## 🚀 Inspección y Validación de los Datasets

```bash
# Validar la sintaxis JSON de todos los datasets
.venv/bin/python -c "
import json, glob
for path in glob.glob('tests/evals/datasets/**/*.json', recursive=True):
    with open(path) as f:
        data = json.load(f)
    print(f'OK: {path} ({len(data)} casos)')
"
```

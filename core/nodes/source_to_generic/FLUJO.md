# Crosswalk Agent — Documentación del flujo

## Resumen

El nodo `generate_source_crosswalk_config` genera el archivo JSON de configuración
que le indica al motor de crosswalk cómo transformar el CSV de un repositorio fuente
al formato genérico del deduplicador.

El nodo opera en **cuatro fases independientes** que separan responsabilidades:
mapeo semántico (LLM), detección de separador (Python), construcción del config (código)
y validación (determinista, sin LLM secundario).

---

## Flujo completo

```
CSV fuente
   │
   ▼
┌─────────────────────────────────────────────────┐
│ FASE 1 — Mapeo de columnas (LLM, 1 herramienta) │
│                                                  │
│ Prompt: crosswalk_agent.md                       │
│ Tool:   save_column_mappings(mappings)            │
│                                                  │
│ El LLM analiza cabeceras + 3 filas de muestra   │
│ y propone el array de mappings hacia el formato  │
│ genérico. NO recibe información de separadores.  │
│                                                  │
│ Máx. 3 iteraciones. Si no produce resultado,     │
│ fallback a mapeo 1:1 automático.                 │
└───────────────────┬─────────────────────────────┘
                    │ mappings[]
                    ▼
┌─────────────────────────────────────────────────┐
│ FASE 2a — Detección Python del separador        │
│                                                  │
│ Función: detect_separator(values)  [helpers.py] │
│                                                  │
│ Lee los valores reales de las columnas mapeadas  │
│ a 'author' / 'subject' y clasifica el separador │
│ sin invocar ningún LLM:                          │
│                                                  │
│  1. Prueba literales: ||, |, ;;, ;, ,,           │
│     → si >50% de filas contienen el literal:    │
│       type="literal", value="<sep>"              │
│                                                  │
│  2. Prueba patrones del catálogo REGEX_STRATEGIES│
│     - r"(?<=[a-z])(?=[A-Z])"  (autores concat.) │
│     - r"\.\s+(?=[A-Z])"       (punto+esp+may.)  │
│     → si >50% de filas producen >1 token:       │
│       type="regex", value="<patrón>"             │
│                                                  │
│  3. Si ninguno matchea → type="unknown"          │
└───────────────────┬─────────────────────────────┘
                    │
          ┌─────────┴──────────┐
          │ ¿type == "unknown"?│
          └─────────┬──────────┘
                    │ Sí
                    ▼
┌─────────────────────────────────────────────────┐
│ FASE 2b — ReAct focalizado (solo si "unknown")  │
│                                                  │
│ Agente mínimo: solo tiene test_regex_on_samples  │
│ Máx. 5 iteraciones. Criterio de aceptación:     │
│   >50% de filas con >1 token en el split        │
│                                                  │
│ Si no converge → fallback a literal="||"         │
└───────────────────┬─────────────────────────────┘
                    │ separator_info
                    ▼
┌─────────────────────────────────────────────────┐
│ FASE 3 — Construcción del config JSON           │
│                                                  │
│ Combina mappings[] + separator_info + delimiter  │
│ y escribe crosswalk_config_<source>.json         │
│                                                  │
│ Si type="literal" → original_separator=<value>  │
│ Si type="regex"   → separator_regex=<pattern>   │
│                      original_separator="||"     │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│ FASE 4 — Validación determinista (sin LLM)      │
│                                                  │
│ Función: _validate_config_deterministic()        │
│                                                  │
│ Ejecuta el crosswalk sobre 3 filas reales.       │
│ Verifica:                                        │
│   ✓ Columnas críticas presentes: id, title,      │
│     author, date                                 │
│   ✓ Si 'author' tiene valores largos, deben      │
│     contener '|' (separador aplicado)            │
│                                                  │
│ Si faltan columnas críticas: reintenta Fase 1   │
│ con feedback (1 sola vez).                       │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
           Config guardado ✓
     state["source_crosswalk_config"]
```

---

## Archivos involucrados

| Archivo | Rol |
|---|---|
| `node.py` | Nodo principal; orquesta las cuatro fases |
| `helpers.py` | Funciones de soporte: `detect_separator()`, `_validate_config_deterministic()`, `_read_csv_head()`, etc. |
| `agent_prompts/crosswalk_agent.md` | Prompt de Fase 1: solo mapeo de columnas |
| `agent_prompts/separator_validator.md` | [Legado] Ya no se usa en el flujo principal |

---

## Catálogo de separadores (`_REGEX_STRATEGIES`)

Definido en `helpers.py`. Para agregar una nueva estrategia conocida, añadir una tupla
`(re.compile(patrón), "regex", patrón_canónico)` a la lista `_REGEX_STRATEGIES`:

```python
_REGEX_STRATEGIES: list[tuple] = [
    # Autores concatenados: "G. AadE. AakvaagB. Abbott"
    (re.compile(r"(?<=[a-z])(?=[A-Z])"), "regex", r"(?<=[a-z])(?=[A-Z])"),
    # Autores con punto+espacio: "Doe J. Smith A."
    (re.compile(r"\.\s+(?=[A-Z])"), "regex", r"\.\s+(?=[A-Z])"),
    # Agregar nuevas estrategias aquí ↓
]
```

---

## Decisiones de diseño

### ¿Por qué separar el mapeo del separador?

El LLM tiene dificultades cuando se le pide resolver simultáneamente:
- Un problema **semántico** (qué columna corresponde a qué campo)
- Un problema **técnico/simbólico** (construir un regex correcto)

Dividirlos en fases con responsabilidades únicas reduce la interferencia cognitiva y
mejora la tasa de éxito de ambas tareas.

### ¿Por qué Python-first para el separador?

Los separadores más comunes (`||`, `;`, regex de autores concatenados) son detectables
con reglas deterministas simples. Invocar un LLM para esto agrega latencia, costo y
variabilidad innecesaria. El LLM (ReAct) entra solo cuando Python no alcanza.

### ¿Por qué validación determinista?

El validador LLM secundario (`_validate_separator_with_llm`) fallaba porque:
1. No entendía la mecánica de `re.sub()` sobre zero-width assertions.
2. Producía falsos negativos que el agente principal no sabía cómo corregir.

La validación determinista verifica directamente si el output del crosswalk tiene
la estructura esperada, sin depender del razonamiento del LLM sobre regexes.

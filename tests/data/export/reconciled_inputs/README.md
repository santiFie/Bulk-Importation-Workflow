# CSVs Reconciliados de Entrada para Tests de Exportación

Este directorio almacena los archivos CSV reconciliados provenientes del subgrafo de Crosswalk y Deduplicación (`core/subgraphs/crosswalk_dedup.py`) o del subgrafo de Enriquecimiento (`core/subgraphs/enrichment.py`).

## Archivos Esperados:

1. **`reconciled_sample_generic.csv`**:
   - Representa el caso de entrada normalizada/curada (ej. ingesta de PDFs desde MinIO).
   - Columnas canónicas esperadas: `id`, `title`, `author`, `date`, `type`, `doi`, `issn`, `citation`.
   - Permite verificar la resolución determinista (Nivel 1) sin requerir llamadas al LLM.

2. **`reconciled_sample_with_remnants.csv`** (ej. `reconciled_pubmed_sample.csv`):
   - Representa el caso de entrada heterogénea proveniente de un CSV de repositorio externo (ej. PubMed, DOAJ, Scopus).
   - Columnas esperadas: Nombre de columnas original del dataset fuente (ej. `PMID`, `Title`, `Authors`, `Journal/Book`, `DOI`, etc.).
   - Permite verificar la intervención del agente LLM para mapear columnas remanentes hacia el catálogo oficial de SEDICI, validadas mediante el guardrail de Nivel 3.

"""
Subgrafos especializados del pipeline de importación en masa.

Cada subgrafo encapsula una fase del pipeline y puede ser testeado
e invocado de forma independiente:

  - IngestSubgraph:         normaliza la fuente de entrada (CSV o PDFs en MinIO).
  - CrosswalkDedupSubgraph: aplica crosswalk y detección de duplicados.
  - EnrichmentSubgraph:     enriquece metadatos vía Crossref u OpenAlex.
  - ExportSubgraph:         genera el SAF e importa a DSpace.
"""

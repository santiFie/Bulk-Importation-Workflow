"""
Façade de re-exportaciones del pipeline de importación a SEDICI.

Este módulo centraliza todas las re-exportaciones de los nodos del pipeline
para mantener compatibilidad con:
  - Los tests que importan directamente desde `core.nodes.pipeline_nodes`.
  - El archivo `core/graph.py` que re-exporta hacia `core.graph`.
  - Scripts externos que dependen de este módulo.

El código de implementación de cada nodo vive en módulos separados:
  - core/nodes/setup_node.py         → setup_workspace
  - core/nodes/crosswalk_nodes.py    → map_source_to_generic, map_sedici_to_generic,
                                       map_to_sedici_format, _run_crosswalk, _save_csv
  - core/nodes/dedup_node.py         → deduplicate
  - core/nodes/reconciliation_node.py → metadata_reconciliation,
                                        _load_crosswalk_mappings, _resolve_source_id_column
  - core/nodes/correction_node.py    → metadata_corrections
  - core/nodes/saf_node.py           → generate_saf_to_import
  - core/nodes/import_node.py        → import_to_dspace

Para extender o modificar un nodo, editar el módulo correspondiente,
NO este archivo.
"""

# ---------------------------------------------------------------------------
# Re-exportaciones — mantienen la interfaz pública del módulo original
# ---------------------------------------------------------------------------

from core.nodes.setup_node import (  # noqa: F401
    setup_workspace,
)

from core.nodes.crosswalk_nodes import (  # noqa: F401
    map_source_to_generic,
    map_sedici_to_generic,
    map_to_sedici_format,
    _run_crosswalk,
    _save_csv,
)

from core.nodes.dedup_node import (  # noqa: F401
    deduplicate,
)

from core.nodes.reconciliation_node import (  # noqa: F401
    metadata_reconciliation,
    _load_crosswalk_mappings,
    _resolve_source_id_column,
)

from core.nodes.correction_node import (  # noqa: F401
    metadata_corrections,
)

from core.nodes.saf_node import (  # noqa: F401
    generate_saf_to_import,
)

from core.nodes.import_node import (  # noqa: F401
    import_to_dspace,
)


# ---------------------------------------------------------------------------
# Stub para compatibilidad con paso pendiente de implementación
# ---------------------------------------------------------------------------

def get_pdfs(state: dict) -> None:
    """Paso 7 — Obtención de los PDFs. (Pendiente de implementación)"""
    pass

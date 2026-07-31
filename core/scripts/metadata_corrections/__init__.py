"""
metadata_corrections — Paso 6 del pipeline de importación a SEDICI.

Importar este paquete es suficiente para activar el registro de todos los
correctores específicos por repositorio. El grafo usa `get_corrector()` para
obtener la instancia adecuada según `source_name`.

Uso
---
    from core.scripts.metadata_corrections import get_corrector

    corrector = get_corrector("scopus")
    df_corregido = corrector.correct(df)
"""

# Importar correctores concretos para activar el mecanismo @register_corrector
from .scopus_corrector import ScopusCorrector  # noqa: F401

# Re-exportar la API pública
from .base import get_corrector, MetadataCorrector  # noqa: F401

__all__ = [
    "get_corrector",
    "MetadataCorrector",
    "ScopusCorrector",
]
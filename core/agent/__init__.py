"""
Paquete de agentes del pipeline de importación.

Expone los constructores de workflows de los agentes disponibles.
"""

from .dspace_agent import build_dspace_agent_workflow
from .github_agent import build_github_workflow
from .metadata_extractor_agent import build_metadata_extractor_workflow
from .minio_agent import build_minio_workflow
from .openalex_agent import build_openalex_workflow
from .researcher_agent import build_searcher_graph

__all__ = [
    "build_dspace_agent_workflow",
    "build_github_workflow",
    "build_metadata_extractor_workflow",
    "build_minio_workflow",
    "build_openalex_workflow",
    "build_searcher_graph",
]

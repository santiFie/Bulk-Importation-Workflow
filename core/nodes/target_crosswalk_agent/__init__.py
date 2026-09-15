"""
Módulo target_crosswalk_agent.

Generador del crosswalk de la fuente reconciliada al formato SEDICI/DSpace.
Implementa el mapeo en dos niveles (composición determinista + agente LLM para columnas remanentes).
"""

from core.nodes.target_crosswalk_agent.node import generate_sedici_target_crosswalk_config

__all__ = ["generate_sedici_target_crosswalk_config"]

"""
Paquete del agente generador de crosswalk config hacia formato genérico.
"""

from core.nodes.source_to_generic.node import (
    generate_source_crosswalk_config,
    SourceToGenericCrosswalkGenerator,
)

__all__ = [
    "generate_source_crosswalk_config",
    "SourceToGenericCrosswalkGenerator",
]

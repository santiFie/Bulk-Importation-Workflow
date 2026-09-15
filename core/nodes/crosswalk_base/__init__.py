"""
Módulo base para la generación de configuraciones de crosswalk.
Define abstracciones, esquemas comunes y el patrón Template Method.
"""

from core.nodes.crosswalk_base.models import CrosswalkColumnMapping
from core.nodes.crosswalk_base.base import BaseCrosswalkGenerator

__all__ = [
    "CrosswalkColumnMapping",
    "BaseCrosswalkGenerator",
]

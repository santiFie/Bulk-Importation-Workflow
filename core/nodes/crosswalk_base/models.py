"""
Modelos de datos comunes para el mapeo de columnas de crosswalk.
"""

from typing import Optional
from pydantic import BaseModel, Field as PydanticField


class CrosswalkColumnMapping(BaseModel):
    """Mapeo de una columna CSV origen a un campo destino (genérico o SEDICI/DSpace)."""

    left: str = PydanticField(
        description=(
            "Nombre exacto de la cabecera CSV origen. "
            "Soporta concatenación ('ColA+ColB') y wildcard ('author*')."
        )
    )
    replace: str = PydanticField(
        description="Nombre del campo destino (ej: 'title', 'sedici.creator.person[es]')."
    )
    required: bool = PydanticField(
        default=False,
        description="True descarta la fila completa si este campo está vacío.",
    )
    default: Optional[str] = PydanticField(
        default="",
        description="Valor por defecto cuando el campo está vacío. Cadena vacía si no aplica.",
    )
    filter: str = PydanticField(
        default="trim",
        description="Filtro a aplicar al valor: 'trim', 'lowercase', 'trim|lowercase' o ''.",
    )

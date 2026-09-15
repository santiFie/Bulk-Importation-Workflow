"""
Nodo agente para la generación del crosswalk hacia el formato SEDICI/DSpace.

Implementa la Solución B (Mapeo en 2 Niveles):
  - Nivel 1: Composición determinista de campos genéricos conocidos y enriquecidos.
  - Nivel 2: Agente LLM focalizado para mapear columnas remanentes (guiado por el catálogo de SEDICI).
  - Nivel 3: Guardrail determinista que valida contra la lista blanca oficial de DSpace/SEDICI.
"""

import csv
import logging
import os
from typing import Any, Optional

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langsmith import traceable
from pydantic import BaseModel, Field as PydanticField

from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from core.utils.prompt_loader import load_agent_prompt
from core.nodes.target_crosswalk_agent.helpers import (
    build_and_save_target_config,
    format_sedici_catalog_for_prompt,
    identify_remnant_columns,
    load_sedici_catalog,
    map_generic_to_sedici_level1,
    read_remnant_samples,
    validate_and_filter_target_mappings,
)

logger = logging.getLogger(__name__)


class TargetColumnMapping(BaseModel):
    """Mapeo de una columna CSV remanente hacia un metadato de SEDICI/DSpace."""

    left: str = PydanticField(
        description="Nombre exacto de la cabecera remanente del CSV reconciliado (ej: 'PMID', 'inferred_type')."
    )
    replace: str = PydanticField(
        description="Nombre del metadato SEDICI según el catálogo (ej: 'sedici.identifier.other', 'sedici.subtype[es]')."
    )
    required: bool = PydanticField(
        default=False,
        description="True descarta la fila si este metadato está vacío.",
    )
    default: Optional[str] = PydanticField(
        default="",
        description="Valor por defecto si la celda está vacía.",
    )
    filter: str = PydanticField(
        default="trim",
        description="Filtro a aplicar al valor (ej. 'trim', 'lowercase').",
    )


@traceable(name="GenerateSediciTargetCrosswalkConfig", run_type="chain")
def generate_sedici_target_crosswalk_config(state: dict[str, Any]) -> dict[str, Any]:
    """
    Paso 4b — Generación de configuración de crosswalk al formato SEDICI/DSpace.

    Analiza el CSV reconciliado (`state["reconciled_csv_path"]`) y genera
    dinámicamente el archivo JSON de crosswalk hacia los metadatos de SEDICI.
    Combina composición determinista para los campos troncales y un agente LLM
    con guardrail para los campos específicos/remanentes de la fuente.

    Returns:
        Dict con la actualización de ``sedici_target_crosswalk_config``.
    """
    source_name = state.get("source_name", "default_source")
    workspace_dir = state.get("workspace_dir") or "runs"
    reconciled_csv_path = state.get("reconciled_csv_path")
    existing_target_config = state.get("sedici_target_crosswalk_config")

    # Si ya existe un config destino explícito y es el caso de Romero o fue configurado
    # manualmente por el usuario (y no es el default genérico en una fuente no-Romero),
    # se respeta para retrocompatibilidad total.
    if existing_target_config and os.path.isfile(existing_target_config):
        is_default_romero = existing_target_config.endswith("config_romero_to_sedici.json")
        if source_name == "romero" or not is_default_romero:
            print(f"[GenerateSediciTargetConfig] Utilizando config existente: '{existing_target_config}'")
            return {"sedici_target_crosswalk_config": existing_target_config}

    if not reconciled_csv_path or not os.path.isfile(reconciled_csv_path):
        raise FileNotFoundError(
            f"[GenerateSediciTargetConfig] Error: CSV reconciliado no encontrado en '{reconciled_csv_path}'"
        )

    # 1. Leer cabeceras del CSV reconciliado
    with open(reconciled_csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            reconciled_columns = next(reader)
        except StopIteration:
            raise ValueError(f"[GenerateSediciTargetConfig] CSV reconciliado vacío en '{reconciled_csv_path}'")

    print(f"[GenerateSediciTargetConfig] Analizando {len(reconciled_columns)} columnas de '{reconciled_csv_path}'")

    # 2. Cargar catálogo formal de metadatos de SEDICI
    catalog = load_sedici_catalog()

    # =========================================================================
    # NIVEL 1 — Composición determinista (Campos genéricos conocidos)
    # =========================================================================
    source_cfg = state.get("source_crosswalk_config")
    level1_mappings, covered_cols = map_generic_to_sedici_level1(
        reconciled_columns=reconciled_columns,
        source_crosswalk_cfg=source_cfg,
    )
    print(
        f"[GenerateSediciTargetConfig] Nivel 1 cubrió {len(level1_mappings)} mapeos "
        f"({len(covered_cols)} columnas cubiertas)"
    )

    # =========================================================================
    # NIVEL 2 — Agente LLM para columnas remanentes
    # =========================================================================
    remnant_cols = identify_remnant_columns(reconciled_columns, covered_cols)
    level2_mappings: list[dict[str, Any]] = []

    if remnant_cols:
        print(f"[GenerateSediciTargetConfig] Nivel 2: columnas remanentes a evaluar: {remnant_cols}")
        samples_text = read_remnant_samples(reconciled_csv_path, remnant_cols, max_rows=3)
        catalog_text = format_sedici_catalog_for_prompt(catalog)

        try:
            system_prompt = load_agent_prompt(
                "target_sedici_agent",
                sedici_catalog=catalog_text,
                remnant_columns_with_samples=samples_text,
            )
        except Exception as exc:
            logger.warning("[GenerateSediciTargetConfig] Error al cargar prompt target_sedici_agent: %s", exc)
            system_prompt = (
                "Sos un experto en catalogación bibliográfica para SEDICI/DSpace.\n"
                f"Catálogo de metadatos:\n{catalog_text}\n\n"
                f"Columnas remanentes:\n{samples_text}\n"
            )

        mappings_draft: list[dict[str, Any]] = []

        @tool
        def save_target_mappings(mappings: list[TargetColumnMapping], thought: str = "") -> str:
            """
            Guarda la lista de mapeos para las columnas remanentes hacia metadatos de SEDICI.

            Args:
                mappings: Lista de objetos TargetColumnMapping con left y replace.
                thought: Razonamiento sobre la selección de metadatos o descarte de redundancias.
            """
            nonlocal mappings_draft
            print(f"[GenerateSediciTargetConfig - LLM thought]: {thought}")
            mappings_draft = [m.model_dump() for m in mappings]
            return f"OK: {len(mappings)} mapeos remanentes guardados."

        try:
            llm = FallbackLLM(groq_model=config.CROSSWALK_MODEL, openrouter_model=config.CROSSWALK_MODEL).resolve()
            bound_llm = llm.bind_tools([save_target_mappings])
            messages = [SystemMessage(system_prompt)]

            for iteration in range(3):
                response = bound_llm.invoke(messages)
                messages.append(response)

                if response.tool_calls:
                    for tc in response.tool_calls:
                        if tc["name"] == "save_target_mappings":
                            res = save_target_mappings.invoke(tc["args"])
                            messages.append(ToolMessage(content=res, tool_call_id=tc["id"]))
                    if mappings_draft:
                        break
                else:
                    break

        except Exception as exc:
            print(f"[GenerateSediciTargetConfig] Advertencia en llamada LLM de Nivel 2: {exc}")
            mappings_draft = []

        # =====================================================================
        # NIVEL 3 — Guardrail determinista sobre la salida del LLM
        # =====================================================================
        level2_mappings = validate_and_filter_target_mappings(
            raw_mappings=mappings_draft,
            catalog=catalog,
            reconciled_columns=reconciled_columns,
        )
        print(f"[GenerateSediciTargetConfig] Nivel 3 guardrail validó {len(level2_mappings)} mapeos del LLM")

    else:
        print("[GenerateSediciTargetConfig] Todas las columnas fueron cubiertas en Nivel 1. Se omite LLM.")

    # =========================================================================
    # Fusión y guardado de la configuración final
    # =========================================================================
    target_config_path = os.path.join(workspace_dir, f"config_{source_name}_to_sedici.json")
    build_and_save_target_config(
        level1_mappings=level1_mappings,
        level2_mappings=level2_mappings,
        source_config_path_or_dict=source_cfg,
        output_path=target_config_path,
    )

    print(f"[GenerateSediciTargetConfig] Configuración generada exitosamente en '{target_config_path}'")
    return {"sedici_target_crosswalk_config": target_config_path}

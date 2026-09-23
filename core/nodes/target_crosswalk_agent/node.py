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

from langsmith import traceable

from core.utils.prompt_loader import load_agent_prompt
from core.nodes.crosswalk_base.models import CrosswalkColumnMapping
from core.nodes.crosswalk_base.base import BaseCrosswalkGenerator
from core.nodes.target_crosswalk_agent.helpers import (
    _read_source_crosswalk_config,
    format_sedici_catalog_for_prompt,
    identify_remnant_columns,
    load_sedici_catalog,
    map_generic_to_sedici_level1,
    read_remnant_samples,
    validate_and_filter_target_mappings,
)

logger = logging.getLogger(__name__)

# Alias para mantener retrocompatibilidad total con posibles imports externos
TargetColumnMapping = CrosswalkColumnMapping


class TargetSediciCrosswalkGenerator(BaseCrosswalkGenerator):
    """
    Generador de crosswalk hacia el esquema formal de SEDICI/DSpace.
    Especializa BaseCrosswalkGenerator con las reglas y catálogo de SEDICI.
    """

    max_llm_iterations = 3

    def get_existing_config(self, state: dict[str, Any]) -> Optional[dict[str, Any]]:
        source_name = state.get("source_name", "default_source")
        existing_target_config = state.get("sedici_target_crosswalk_config")

        if existing_target_config and os.path.isfile(existing_target_config):
            is_default_romero = existing_target_config.endswith("config_romero_to_sedici.json")
            if source_name == "romero" or not is_default_romero:
                print(f"[GenerateSediciTargetConfig] Utilizando config existente: '{existing_target_config}'")
                return {"sedici_target_crosswalk_config": existing_target_config}
        return None

    def prepare_context(self, state: dict[str, Any]) -> dict[str, Any]:
        source_name = state.get("source_name", "default_source")
        workspace_dir = state.get("workspace_dir") or "runs"
        reconciled_csv_path = state.get("reconciled_csv_path")

        if not reconciled_csv_path or not os.path.isfile(reconciled_csv_path):
            raise FileNotFoundError(
                f"[GenerateSediciTargetConfig] Error: CSV reconciliado no encontrado en '{reconciled_csv_path}'"
            )

        with open(reconciled_csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            try:
                reconciled_columns = next(reader)
            except StopIteration:
                raise ValueError(f"[GenerateSediciTargetConfig] CSV reconciliado vacío en '{reconciled_csv_path}'")

        print(f"[GenerateSediciTargetConfig] Analizando {len(reconciled_columns)} columnas de '{reconciled_csv_path}'")
        catalog = load_sedici_catalog()
        output_path = os.path.join(workspace_dir, f"config_{source_name}_to_sedici.json")

        return {
            "source_name": source_name,
            "workspace_dir": workspace_dir,
            "csv_path": reconciled_csv_path,
            "columns": reconciled_columns,
            "catalog": catalog,
            "output_config_path": output_path,
            "source_crosswalk_config": state.get("source_crosswalk_config"),
        }

    def map_deterministic(
        self,
        context: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        reconciled_columns = context["columns"]
        source_cfg = context.get("source_crosswalk_config")
        level1_mappings, covered_cols = map_generic_to_sedici_level1(
            reconciled_columns=reconciled_columns,
            source_crosswalk_cfg=source_cfg,
        )
        print(
            f"[GenerateSediciTargetConfig] Nivel 1 cubrió {len(level1_mappings)} mapeos "
            f"({len(covered_cols)} columnas cubiertas)"
        )
        remnant_cols = identify_remnant_columns(reconciled_columns, covered_cols)
        if remnant_cols:
            print(f"[GenerateSediciTargetConfig] Nivel 2: columnas remanentes a evaluar: {remnant_cols}")
        else:
            print("[GenerateSediciTargetConfig] Todas las columnas fueron cubiertas en Nivel 1. Se omite LLM.")
        return level1_mappings, remnant_cols

    def build_prompt(
        self,
        context: dict[str, Any],
        remaining_columns: list[str],
        feedback: Optional[str] = None,
    ) -> str:
        samples_text = read_remnant_samples(context["csv_path"], remaining_columns, max_rows=3)
        catalog_text = format_sedici_catalog_for_prompt(context["catalog"])

        try:
            return load_agent_prompt(
                "target_sedici_agent",
                sedici_catalog=catalog_text,
                remnant_columns_with_samples=samples_text,
            )
        except Exception as exc:
            logger.warning("[GenerateSediciTargetConfig] Error al cargar prompt target_sedici_agent: %s", exc)
            return (
                "Sos un experto en catalogación bibliográfica para SEDICI/DSpace.\n"
                f"Catálogo de metadatos:\n{catalog_text}\n\n"
                f"Columnas remanentes:\n{samples_text}\n"
            )

    def validate_mappings(
        self,
        raw_mappings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        level2_mappings = validate_and_filter_target_mappings(
            raw_mappings=raw_mappings,
            catalog=context["catalog"],
            reconciled_columns=context["columns"],
        )
        print(f"[GenerateSediciTargetConfig] Nivel 3 guardrail validó {len(level2_mappings)} mapeos del LLM")
        return level2_mappings

    def resolve_separator_settings(
        self,
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        source_cfg = context.get("source_crosswalk_config")
        _, source_opts = _read_source_crosswalk_config(source_cfg)
        return {
            "original_separator": source_opts.get("original_separator", "||"),
            "replace_separator": source_opts.get("replace_separator", "||"),
            "file_delimiter": source_opts.get("file_delimiter", ","),
        }

    def build_result(self, config_path: str, context: dict[str, Any]) -> dict[str, Any]:
        print(f"[GenerateSediciTargetConfig] Configuración generada exitosamente en '{config_path}'")
        return {"sedici_target_crosswalk_config": config_path}


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
    generator = TargetSediciCrosswalkGenerator()
    return generator.generate_config(state)

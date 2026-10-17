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
from core.nodes.crosswalk_base.helpers import read_crosswalk_config
from core.nodes.target_crosswalk_agent.helpers import (
    format_repair_prompt,
    format_sedici_catalog_for_prompt,
    identify_remnant_columns,
    load_sedici_catalog,
    map_generic_to_sedici_level1,
    read_remnant_samples,
    validate_and_filter_target_mappings,
    validate_target_config_compatibility,
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

    def __init__(self) -> None:
        super().__init__()
        self.last_omitted_columns: list[str] = []

    def get_existing_config(self, state: dict[str, Any]) -> Optional[dict[str, Any]]:
        existing_target_config = state.get("sedici_target_crosswalk_config")
        if not existing_target_config:
            return None

        if not os.path.isfile(existing_target_config):
            logger.warning(
                "[GenerateSediciTargetConfig] 'sedici_target_crosswalk_config' provisto no existe en disco: '%s'. "
                "Se procederá con la generación dinámica.",
                existing_target_config,
            )
            return None

        # El usuario proveyó explícitamente una ruta de configuración existente.
        reconciled_csv_path = state.get("reconciled_csv_path")
        if not reconciled_csv_path or not os.path.isfile(reconciled_csv_path):
            print(
                f"[GenerateSediciTargetConfig] No hay reconciled_csv_path accesible para validar. "
                f"Utilizando config existente provista: '{existing_target_config}'"
            )
            return {"sedici_target_crosswalk_config": existing_target_config}

        # Leer cabeceras del CSV reconciliado
        with open(reconciled_csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            try:
                reconciled_columns = next(reader)
            except StopIteration:
                reconciled_columns = []

        catalog = load_sedici_catalog()
        compat_result = validate_target_config_compatibility(
            config_source=existing_target_config,
            reconciled_columns=reconciled_columns,
            catalog=catalog,
        )

        if compat_result.get("is_valid"):
            print(f"[GenerateSediciTargetConfig] Configuración del usuario 100% compatible: '{existing_target_config}'")
            return {"sedici_target_crosswalk_config": existing_target_config}

        # Incompatibilidad detectada: resiliencia con LLM
        print(
            f"[GenerateSediciTargetConfig] Incompatibilidad en config del usuario: {compat_result.get('message')}. "
            "Activando agente LLM para autorreparación..."
        )
        return self._repair_user_config(
            state=state,
            user_config_path=existing_target_config,
            compat_result=compat_result,
            reconciled_columns=reconciled_columns,
            catalog=catalog,
        )

    def _repair_user_config(
        self,
        state: dict[str, Any],
        user_config_path: str,
        compat_result: dict[str, Any],
        reconciled_columns: list[str],
        catalog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        source_name = state.get("source_name", "repaired_source")
        workspace_dir = state.get("workspace_dir") or "runs"
        reconciled_csv_path = state.get("reconciled_csv_path")

        # Columnas disponibles en el CSV que no están en valid_mappings
        valid_mappings = compat_result.get("valid_mappings", [])
        mapped_lefts = set()
        for m in valid_mappings:
            for p in m.get("left", "").split("+"):
                mapped_lefts.add(p.strip())

        available_columns = [c for c in reconciled_columns if c not in mapped_lefts]

        repair_prompt = format_repair_prompt(
            catalog=catalog,
            incompatible_mappings=compat_result.get("incompatible_mappings", []),
            invalid_target_fields=compat_result.get("invalid_target_fields", []),
            valid_mappings=valid_mappings,
            csv_path=reconciled_csv_path,
            available_columns=available_columns,
        )

        repair_context = {
            "source_name": source_name,
            "workspace_dir": workspace_dir,
            "csv_path": reconciled_csv_path,
            "columns": reconciled_columns,
            "catalog": catalog,
            "_override_prompt": repair_prompt,
        }

        # Invocamos al LLM para resolver los mapeos incompatibles
        remaining_cols_to_repair = [
            item.get("mapping", {}).get("left", "")
            for item in compat_result.get("incompatible_mappings", [])
        ]
        raw_repaired = self.execute_llm_mapping(repair_context, remaining_cols_to_repair)

        # Nivel 3: Guardrail determinista sobre los mappings reparados
        repaired_mappings = self.validate_mappings(raw_repaired, repair_context)
        print(f"[GenerateSediciTargetConfig] Guardrail validó {len(repaired_mappings)} mapeos reparados por el LLM")

        # Detectar y loguear columnas conflictivas descartadas por omisión
        repaired_lefts = set()
        for m in repaired_mappings:
            for p in m.get("left", "").split("+"):
                repaired_lefts.add(p.strip())

        omitted_columns = [col for col in remaining_cols_to_repair if col not in repaired_lefts]
        if omitted_columns:
            logger.info("[GenerateSediciTargetConfig] Columnas descartadas por omisión del LLM: %s", omitted_columns)
            print(f"[GenerateSediciTargetConfig] Columnas descartadas por omisión: {omitted_columns}")
        self.last_omitted_columns = omitted_columns

        # Fusionar mappings válidos originales + reparados
        all_mappings = self.merge_mappings(valid_mappings, repaired_mappings, repair_context)

        # Settings originales provistos por el usuario o defaults 
        settings = compat_result.get("settings") or {
            "original_separator": "||",
            "replace_separator": "||",
            "file_delimiter": ",",
        }

        output_path = os.path.join(workspace_dir, f"config_{source_name}_to_sedici.json")
        saved_config = self.save_config_file(all_mappings, settings, output_path)
        print(f"[GenerateSediciTargetConfig] Configuración reparada guardada exitosamente en '{saved_config}'")

        return {"sedici_target_crosswalk_config": saved_config}

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
        if context.get("_override_prompt"):
            return context["_override_prompt"]

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
        _, source_opts = read_crosswalk_config(source_cfg)
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

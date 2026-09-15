"""
Clase base abstracta con el patrón Template Method para generadores de crosswalk.
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Callable, Optional

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool, BaseTool

from core.utils.config import config
from core.utils.get_local_model import FallbackLLM
from core.nodes.crosswalk_base.models import CrosswalkColumnMapping
from core.nodes.crosswalk_base.helpers import (
    save_crosswalk_config_json,
    validate_column_mappings_base,
)

logger = logging.getLogger(__name__)


class BaseCrosswalkGenerator(ABC):
    """
    Template Method para la generación de archivos de configuración de crosswalk.

    Define el flujo algorítmico coordinando:
      1. Verificación de caché/reuso de configuraciones preexistentes.
      2. Preparación del contexto (cabeceras, muestras, paths).
      3. Mapeo determinista inicial (Nivel 1).
      4. Mapeo semántico con LLM para columnas remanentes (Nivel 2).
      5. Guardrail estático en memoria sobre las propuestas del LLM (Nivel 3).
      6. Fusión ordenada de mapeos.
      7. Resolución de separadores y opciones del archivo.
      8. Persistencia de la configuración en formato JSON.
      9. Validación funcional post-guardado con soporte de reintento con feedback (Hook).
      10. Retorno del estado actualizado para LangGraph.
    """

    max_llm_iterations: int = 4

    def generate_config(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Método plantilla que orquesta la generación de la configuración.
        """
        # 1. Comprobar si ya existe un config utilizable
        existing = self.get_existing_config(state)
        if existing:
            return existing

        # 2. Preparar el contexto de ejecución
        context = self.prepare_context(state)

        # 3. Mapeo determinista (Nivel 1)
        det_mappings, remaining_cols = self.map_deterministic(context)

        # 4. Mapeo con LLM (Nivel 2) si hay columnas remanentes
        llm_mappings: list[dict[str, Any]] = []
        if remaining_cols:
            raw_llm_mappings = self.execute_llm_mapping(context, remaining_cols)
            # 5. Guardrail estático en memoria (Nivel 3)
            llm_mappings = self.validate_mappings(raw_llm_mappings, context)

        # 6. Fusión de mapeos
        all_mappings = self.merge_mappings(det_mappings, llm_mappings, context)

        # 7. Resolución de separadores y delimitadores
        settings = self.resolve_separator_settings(all_mappings, context)

        # 8. Guardado en disco
        output_path = context["output_config_path"]
        config_path = self.save_config_file(all_mappings, settings, output_path)

        # 9. Validación funcional post-guardado (Hook)
        val_result = self.post_validate(config_path, context)
        if val_result and not val_result.get("ok", True) and val_result.get("retry_feedback") and remaining_cols:
            print(f"[{self.__class__.__name__}] Reintentando mapeo con feedback de validación...")
            retry_raw = self.execute_llm_mapping(
                context, remaining_cols, feedback=val_result["retry_feedback"]
            )
            retry_mappings = self.validate_mappings(retry_raw, context)
            all_mappings = self.merge_mappings(det_mappings, retry_mappings, context)
            config_path = self.save_config_file(all_mappings, settings, output_path)
            self.post_validate(config_path, context)

        # 10. Construcción del resultado final
        return self.build_result(config_path, context)

    # -----------------------------------------------------------------------
    # Pasos comunes implementados en la clase base
    # -----------------------------------------------------------------------

    def save_config_file(
        self,
        mappings: list[dict[str, Any]],
        settings: dict[str, Any],
        output_path: str,
    ) -> str:
        """Serializa la configuración a formato [mappings, settings]."""
        return save_crosswalk_config_json(mappings, settings, output_path)

    def merge_mappings(
        self,
        deterministic_mappings: list[dict[str, Any]],
        llm_mappings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Fusiona mapeos deterministas y del LLM dando precedencia a los deterministas.
        Evita duplicación por columna 'left'.
        """
        merged: list[dict[str, Any]] = []
        seen_lefts: set[str] = set()

        for m in deterministic_mappings:
            left = m.get("left", "")
            if left and left not in seen_lefts:
                merged.append(m)
                seen_lefts.add(left)

        for m in llm_mappings:
            left = m.get("left", "")
            if left and left not in seen_lefts:
                merged.append(m)
                seen_lefts.add(left)

        return merged

    def execute_llm_mapping(
        self,
        context: dict[str, Any],
        remaining_columns: list[str],
        feedback: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Bucle de ejecución ReAct estándar para mapeo asistido por LLM.
        """
        prompt = self.build_prompt(context, remaining_columns, feedback=feedback)
        extra_tools = self.get_extra_tools(context)

        mappings_draft: list[dict[str, Any]] = []

        @tool
        def save_column_mappings(mappings: list[CrosswalkColumnMapping], thought: str = "") -> str:
            """
            Guarda la lista de mapeos de columnas propuestos.

            Args:
                mappings: Lista de objetos de mapeo con 'left' y 'replace'.
                thought: Razonamiento sobre la selección de metadatos o descarte.
            """
            nonlocal mappings_draft
            print(f"[{self.__class__.__name__} - LLM thought]: {thought}")
            mappings_draft = [m.model_dump() for m in mappings]
            return f"OK: {len(mappings)} mapeos guardados."

        tools_list: list[BaseTool] = [save_column_mappings] + extra_tools

        try:
            llm = FallbackLLM(
                groq_model=config.CROSSWALK_MODEL,
                openrouter_model=config.CROSSWALK_MODEL,
            ).resolve()
            bound_llm = llm.bind_tools(tools_list)
            messages = [SystemMessage(content=prompt)]

            tool_map: dict[str, Callable] = {t.name: t for t in tools_list}

            for iteration in range(self.max_llm_iterations):
                response = bound_llm.invoke(messages)
                messages.append(response)

                if response.tool_calls:
                    for tc in response.tool_calls:
                        name = tc["name"]
                        if name in tool_map:
                            res = tool_map[name].invoke(tc["args"])
                        else:
                            res = f"Tool desconocida: {name}"
                        messages.append(ToolMessage(content=str(res), tool_call_id=tc["id"]))

                    if mappings_draft:
                        break
                else:
                    break

        except Exception as exc:
            logger.warning("[%s] Excepción durante la invocación del LLM: %s", self.__class__.__name__, exc)

        return mappings_draft

    def validate_mappings(
        self,
        raw_mappings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Guardrail estático por defecto en memoria.
        Valida que 'left' exista en las columnas disponibles del CSV.
        """
        return validate_column_mappings_base(raw_mappings, context.get("columns", []))

    # -----------------------------------------------------------------------
    # Métodos abstractos y Hooks a especializar por las subclases
    # -----------------------------------------------------------------------

    @abstractmethod
    def prepare_context(self, state: dict[str, Any]) -> dict[str, Any]:
        """Extrae y estructura la información de entrada (archivos, columnas, paths)."""
        pass

    @abstractmethod
    def build_prompt(
        self,
        context: dict[str, Any],
        remaining_columns: list[str],
        feedback: Optional[str] = None,
    ) -> str:
        """Construye el prompt del sistema para el LLM."""
        pass

    @abstractmethod
    def resolve_separator_settings(
        self,
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Resuelve los separadores de campos multivalor y delimitadores de archivo."""
        pass

    @abstractmethod
    def build_result(self, config_path: str, context: dict[str, Any]) -> dict[str, Any]:
        """Construye el payload devuelto al estado de LangGraph."""
        pass

    # Hooks opcionales

    def get_existing_config(self, state: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Hook: Comprueba si ya existe un config reutilizable."""
        return None

    def map_deterministic(
        self,
        context: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Hook Nivel 1: Mapeo determinista previo al LLM.
        Devuelve (mapeos_resueltos, columnas_remanentes).
        """
        return [], context.get("columns", [])

    def get_extra_tools(self, context: dict[str, Any]) -> list[BaseTool]:
        """Hook: Devuelve herramientas adicionales para el LLM (ej. Crossref)."""
        return []

    def post_validate(self, config_path: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Hook Nivel 4: Validación funcional post-guardado.
        Si devuelve dict con ok=False y retry_feedback, el template method reintenta el LLM.
        """
        return None

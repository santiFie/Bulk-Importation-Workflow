"""
Nodos de Crosswalk — Pasos 2a, 2b y 5 del pipeline.

Encapsula los tres nodos que ejecutan un crosswalk sobre CSVs:
  - map_source_to_generic:  CSV origen → formato genérico (Paso 2a)
  - map_sedici_to_generic:  CSV SEDICI → formato genérico (Paso 2b)
  - map_to_sedici_format:   CSV reconciliado → formato SEDICI (Paso 5)

La lógica de invocación a la API REST del backend está encapsulada en
`CrosswalkRunner`, un servicio sin estado que puede testearse de forma
independiente al grafo LangGraph.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langsmith import traceable

from core.clients.crosswalk_client import CrosswalkClient, CrosswalkApiError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Servicio de crosswalk (lógica pura, sin dependencia del State de LangGraph)
# ---------------------------------------------------------------------------

class CrosswalkRunner:
    """
    Servicio sin estado que ejecuta un crosswalk via la API REST del backend.

    Desacopla la lógica de invocación al cliente HTTP de los nodos del grafo,
    lo que permite testear el crosswalk de forma independiente.
    """

    def run(self, csv_input_path: str, config_json_path: str, csv_output_path: str) -> str:
        """
        Aplica el crosswalk definido en config_json_path al CSV de entrada
        via la API REST del backend, y guarda el resultado en csv_output_path.

        Args:
            csv_input_path:  Path al CSV fuente a transformar.
            config_json_path: Path al JSON de configuración del crosswalk.
            csv_output_path: Path donde se escribirá el CSV resultado.

        Returns:
            Mensaje de éxito con la ruta del archivo guardado.

        Raises:
            RuntimeError: Si la API retorna un error o el job falla.
        """
        try:
            client = CrosswalkClient()
            result_bytes = client.run_crosswalk(
                csv_path=csv_input_path,
                config_path=config_json_path,
            )
            self._write_output(result_bytes, csv_output_path)
            return f"Éxito: Mapeo realizado correctamente. Archivo guardado en '{csv_output_path}'"

        except CrosswalkApiError as exc:
            raise RuntimeError(f"Error de API durante el crosswalk: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Error durante la ejecución del crosswalk: {repr(exc)}") from exc

    @staticmethod
    def _write_output(content: bytes, output_path: str) -> None:
        """Escribe los bytes del resultado en el path de salida."""
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(content)


# Instancia compartida del runner (sin estado, segura para reusar)
_runner = CrosswalkRunner()


# ---------------------------------------------------------------------------
# Helpers públicos (re-exportados desde pipeline_nodes para compatibilidad)
# ---------------------------------------------------------------------------

def _run_crosswalk(csv_input_path: str, config_json_path: str, csv_output_path: str) -> str:
    """
    Función helper de compatibilidad. Delega en CrosswalkRunner.run().

    Mantenida para no romper imports en tests y graph.py.
    """
    return _runner.run(csv_input_path, config_json_path, csv_output_path)


def _save_csv(csv_bytes: bytes, output_path: str) -> dict[str, Any]:
    """
    Escribe csv_bytes en output_path y devuelve un dict de estado.

    Helper utilitario usado por el nodo de deduplicación.
    """
    try:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(csv_bytes)
        row_count = max(0, len(csv_bytes.split(b"\n")) - 2)
        return {"status": "ok", "csv_path": output_path, "row_count": row_count}
    except OSError as exc:
        return {"error": f"No se pudo escribir el archivo de salida: {exc}"}


# ---------------------------------------------------------------------------
# Nodos del grafo
# ---------------------------------------------------------------------------

@traceable(name="MapSourceToGeneric", run_type="chain")
def map_source_to_generic(state: dict) -> dict[str, Any]:
    """
    Paso 2a (crosswalk) — Mapea el CSV del repositorio origen al formato
    genérico entendido por el Deduplicador.
    """
    result = _runner.run(
        csv_input_path=state["source_csv_path"],
        config_json_path=state["source_crosswalk_config"],
        csv_output_path=state["generic_source_csv_path"],
    )
    logger.info("[MapSourceToGeneric] %s", result)
    return {}


@traceable(name="MapSediciToGeneric", run_type="chain")
def map_sedici_to_generic(state: dict) -> dict[str, Any]:
    """
    Paso 2b — Mapea el CSV exportado de SEDICI al formato genérico
    entendido por el Deduplicador.
    """
    result = _runner.run(
        csv_input_path=state["repository_csv_path"],
        config_json_path=state["sedici_crosswalk_config"],
        csv_output_path=state["generic_sedici_csv_path"],
    )
    logger.info("[MapSediciToGeneric] %s", result)
    return {}


@traceable(name="MapToSediciFormat", run_type="chain")
def map_to_sedici_format(state: dict) -> dict[str, Any]:
    """
    Paso 5 — Mapeo final al formato esperado por SEDICI.

    Aplica el crosswalk del repositorio origen al formato de metadatos
    de SEDICI sobre el CSV reconciliado (metadatos originales completos).
    """
    result = _runner.run(
        csv_input_path=state["reconciled_csv_path"],
        config_json_path=state["sedici_target_crosswalk_config"],
        csv_output_path=state["sedici_ready_csv_path"],
    )
    logger.info("[MapToSediciFormat] %s", result)
    return {}

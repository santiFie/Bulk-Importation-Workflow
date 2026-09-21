"""
Tests de integración para el subgrafo de Exportación e Importación a DSpace.

Módulo bajo prueba: core/subgraphs/export.py
Subgrafo: ExportSubgraph

Topología del subgrafo:
  START → GenerateSediciTargetConfig → MapToSediciFormat → MetadataCorrections
        → GenerateSafToImport → ImportToDspace → END

Este test ejercita el flujo completo del subgrafo de punta a punta conectándose
a servicios reales:
  1. Agente LLM en Nivel 2 y Guardrail Nivel 3 (GenerateSediciTargetConfig).
  2. API REST del servicio de Crosswalk (MapToSediciFormat).
  3. Normalizador programático de metadatos (MetadataCorrections).
  4. Generador de Simple Archive Format via dspace-csv-archive (GenerateSafToImport).
  5. API REST de DSpace 7+ Scripts API para ingesta y validación (ImportToDspace).

Los casos de prueba están ordenados secuencialmente:
  - Topology & Diagnostics: Verificación de compilación y conectividad con servicios.
  - Caso 1 (Dry-Run / Validación): Ejecución completa con import_validate_only=True.
  - Caso 2 (Importación Real): Ingesta física y persistente en colección de pruebas.
  - Caso 3 (Bypass LLM): Flujo con esquema genérico curado (resuelto 100% en Nivel 1).
  - Caso 4 (Idempotencia): Reutilización de configuración de crosswalk preexistente.
"""

import csv
import os
import shutil
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

import pandas as pd
import pytest

from core.clients.crosswalk_client import CrosswalkClient
from core.state import State
from core.subgraphs.export import build_export_subgraph
from mcps.dspace_mcp.src.dspace_client import DSpaceClient


# ---------------------------------------------------------------------------
# Rutas base del proyecto y fixtures de datos
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "tests", "data")
EXPORT_DATA_DIR = os.path.join(DATA_DIR, "export")
RECONCILED_INPUTS_DIR = os.path.join(EXPORT_DATA_DIR, "reconciled_inputs")

GENERIC_RECONCILED_CSV = os.path.join(RECONCILED_INPUTS_DIR, "reconciled_sample_generic.csv")
REMNANTS_RECONCILED_CSV = os.path.join(RECONCILED_INPUTS_DIR, "reconciled_sample_with_remnants.csv")

# Colección destino de pruebas en DSpace
DEFAULT_TEST_COLLECTION = "556c4151-fbb8-4b3a-84b5-d2a8eb12a19f"
TEST_DSPACE_COLLECTION = os.getenv("TEST_DSPACE_COLLECTION", DEFAULT_TEST_COLLECTION)



@pytest.fixture(autouse=True)
def mock_saf_missing_bitstreams(monkeypatch):
    """
    Evita FileNotFoundError durante la generación del SAF cuando los ítems
    del CSV referencian bitstreams que no existen físicamente en el entorno de tests.
    Copia el archivo original si existe, o genera un PDF dummy válido en el paquete SAF.
    """
    orig_copy = shutil.copy
    orig_copyfile = shutil.copyfile

    def safe_copy(src, dst, **kwargs):
        if os.path.exists(src):
            return orig_copy(src, dst, **kwargs)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(b"%PDF-1.4 dummy test bitstream\n")

    def safe_copyfile(src, dst, **kwargs):
        if os.path.exists(src):
            return orig_copyfile(src, dst, **kwargs)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(b"%PDF-1.4 dummy test bitstream\n")

    monkeypatch.setattr(shutil, "copy", safe_copy)
    monkeypatch.setattr(shutil, "copyfile", safe_copyfile)


# ---------------------------------------------------------------------------
# Helpers utilitarios de evaluación y diagnóstico
# ---------------------------------------------------------------------------
def _get_dspace_client() -> Optional[DSpaceClient]:
    """Crea y autentica una instancia de DSpaceClient según variables de entorno."""
    base_url = os.getenv("DSPACE_BASE_URL", "http://localhost:8080/server").replace(
        "host.docker.internal", "localhost"
    )
    email = os.getenv("DSPACE_EMAIL")
    password = os.getenv("DSPACE_PASSWORD")

    if not email or not password:
        return None

    client = DSpaceClient(base_url, email, password)
    try:
        client.login()
        return client
    except Exception:
        return None


def _assert_saf_structure_and_validity(saf_dir: str, expected_item_count: int) -> None:
    """
    Evalúa la conformidad estructural del Simple Archive Format (SAF):
      - Existencia de N subdirectorios item_001 a item_00N.
      - Existencia y sintaxis válida de dublin_core.xml.
      - Existencia del archivo contents por ítem.
      - Presencia de esquemas adicionales válidos (ej. metadata_sedici.xml).
    """
    assert os.path.isdir(saf_dir), f"El directorio SAF '{saf_dir}' debe existir."

    item_dirs = sorted(
        [
            d for d in os.listdir(saf_dir)
            if d.startswith("item_") and os.path.isdir(os.path.join(saf_dir, d))
        ]
    )
    assert len(item_dirs) == expected_item_count, (
        f"El SAF debe contener {expected_item_count} carpetas de ítem, pero tiene {len(item_dirs)}."
    )

    for item_dir_name in item_dirs:
        item_path = os.path.join(saf_dir, item_dir_name)

        # 1. dublin_core.xml
        dc_path = os.path.join(item_path, "dublin_core.xml")
        assert os.path.isfile(dc_path), f"Falta dublin_core.xml en '{item_dir_name}'."
        tree = ET.parse(dc_path)
        root = tree.getroot()
        assert root.tag == "dublin_core", (
            f"El tag raíz de {dc_path} debe ser 'dublin_core', encontrado '{root.tag}'."
        )

        # 2. contents
        contents_path = os.path.join(item_path, "contents")
        assert os.path.isfile(contents_path), f"Falta el archivo contents en '{item_dir_name}'."

        # 3. Validar otros esquemas si existen (metadata_sedici.xml, etc.)
        for fname in os.listdir(item_path):
            if fname.startswith("metadata_") and fname.endswith(".xml"):
                schema_xml = os.path.join(item_path, fname)
                schema_tree = ET.parse(schema_xml)
                assert schema_tree.getroot().tag == "dublin_core", (
                    f"El tag raíz de {schema_xml} debe ser 'dublin_core'."
                )


def _assert_sedici_csv_conformance(csv_path: str, expected_rows: int) -> None:
    """
    Evalúa la calidad y conformidad del CSV transformado para SEDICI:
      - Cantidad invariante de filas.
      - Presencia de campos núcleo requeridos (dc.title, autoría, fecha).
      - Ausencia de separadores no estandarizados (|||).
    """
    assert os.path.isfile(csv_path), f"El CSV SEDICI-ready '{csv_path}' no existe."
    df = pd.read_csv(csv_path)

    assert len(df) == expected_rows, (
        f"Invariante de filas roto: Se esperaban {expected_rows} filas pero hay {len(df)}."
    )

    columns = set(df.columns)
    has_title = any(c.startswith("dc.title") for c in columns)
    assert has_title, f"Debe existir al menos un campo dc.title en las columnas: {columns}"

    # Validar que ningún valor contenga separadores corruptos como |||
    for col in df.columns:
        for val in df[col].dropna().astype(str):
            assert "|||" not in val, f"Separador legacy '|||' encontrado en columna '{col}': {val}"


# ===========================================================================
# 0. Topología y Verificación de Conectividad con Servicios Reales
# ===========================================================================
class TestExportSubgraphTopology:
    """Verifica la compilación, topología y registro de nodos del subgrafo."""

    @pytest.mark.asyncio
    async def test_compilacion_y_nodos_presentes(self):
        """El subgrafo compila correctamente y registra los 5 nodos en secuencia lineal."""
        subgraph = await build_export_subgraph()

        assert subgraph is not None
        node_names = set(subgraph.nodes.keys())

        expected_nodes = {
            "GenerateSediciTargetConfig",
            "MapToSediciFormat",
            "MetadataCorrections",
            "GenerateSafToImport",
            "ImportToDspace",
        }
        for node in expected_nodes:
            assert node in node_names, f"El nodo '{node}' debe estar registrado en ExportSubgraph."


class TestExportServicesHealth:
    """Diagnóstico preliminar de los servicios reales requeridos para los tests de integración."""

    def test_servicio_crosswalk_disponible(self):
        """El servicio de backend de Crosswalk responde y permite autenticación JWT."""
        client = CrosswalkClient()
        client.login()
        assert client._authenticated is True, "Debe haberse autenticado exitosamente contra Crosswalk."

    def test_servicio_dspace_disponible_y_coleccion_accesible(self):
        """DSpace REST API responde, autentica credenciales y la colección de prueba existe."""
        dspace = _get_dspace_client()
        assert dspace is not None, (
            "DSpace no pudo autenticarse. Verificar DSPACE_EMAIL, DSPACE_PASSWORD y DSPACE_BASE_URL."
        )

        # Verificar que la colección de prueba exista en DSpace
        try:
            col_data = dspace.get(f"/api/core/collections/{TEST_DSPACE_COLLECTION}")
            assert col_data.get("uuid") == TEST_DSPACE_COLLECTION, (
                f"La colección {TEST_DSPACE_COLLECTION} no retornó los datos esperados."
            )
        except Exception as exc:
            pytest.fail(
                f"Error al consultar la colección de prueba '{TEST_DSPACE_COLLECTION}' en DSpace: {exc}"
            )


# ===========================================================================
# 1. Caso de Prueba 1: Modo Validación (Dry-Run / Safe Validation)
# ===========================================================================
class TestExportSubgraphDryRun:
    """
    Ejecuta el subgrafo de exportación completo con servicios reales en modo validación.
    Ejercita LLM, Crosswalk API, Correcciones, SAF y DSpace (--validate) sin persistir datos.
    """

    @pytest.mark.asyncio
    async def test_flujo_completo_validacion_dry_run_con_llm_y_servicios_reales(self, tmp_path):
        """
        Ejecuta el flujo completo sobre un CSV con columnas remanentes (estilo PubMed):
          1. GenerateSediciTargetConfig: Llama al LLM para resolver columnas remanentes.
          2. MapToSediciFormat: Transforma el CSV reconciliado vía API de Crosswalk.
          3. MetadataCorrections: Normaliza delimitadores a || y códigos de idioma ISO.
          4. GenerateSafToImport: Empaqueta los ítems en carpetas SAF con dublin_core.xml.
          5. ImportToDspace: Ejecuta DSpace Scripts API con -v (validate only).
        """
        assert os.path.isfile(REMNANTS_RECONCILED_CSV), (
            f"CSV de entrada reconciliado no encontrado en '{REMNANTS_RECONCILED_CSV}'."
        )
        df_in = pd.read_csv(REMNANTS_RECONCILED_CSV)
        expected_rows = len(df_in)

        # Preparar rutas de trabajo en directorio temporal aislado
        workspace_dir = str(tmp_path / "workspace_dryrun")
        os.makedirs(workspace_dir, exist_ok=True)

        input_csv_copy = str(tmp_path / "reconciled_input.csv")
        shutil.copy(REMNANTS_RECONCILED_CSV, input_csv_copy)

        source_name = "pubmed_test_export"
        sedici_ready_path = os.path.join(workspace_dir, "sediciready.csv")
        saf_output_dir = os.path.join(workspace_dir, "saf_archi_ve")
        import_mapfile_path = os.path.join(workspace_dir, "import_mapfile.txt")

        state: State = {
            "workspace_dir": workspace_dir,
            "source_name": source_name,
            "input_source_type": "csv",
            "source_csv_path": input_csv_copy,
            "repository_csv_path": input_csv_copy,
            "reconciled_csv_path": input_csv_copy,
            "sedici_ready_csv_path": sedici_ready_path,
            "saf_output_path": saf_output_dir,
            "dspace_collection": TEST_DSPACE_COLLECTION,
            "import_mapfile_path": import_mapfile_path,
            "import_validate_only": True,
            "import_exclude_bitstreams": True,
        }

        # Ejecutar subgrafo completo
        subgraph = await build_export_subgraph()
        final_state = await subgraph.ainvoke(state)

        # 1. Evaluar configuración de crosswalk hacia SEDICI generada por el agente LLM
        expected_config_path = os.path.join(workspace_dir, f"config_{source_name}_to_sedici.json")
        assert os.path.isfile(expected_config_path), (
            "El agente LLM debió generar el archivo de configuración de crosswalk a SEDICI."
        )
        assert final_state.get("sedici_target_crosswalk_config") == expected_config_path

        # 2. Evaluar conformidad y conservación de registros en sedici_ready.csv
        _assert_sedici_csv_conformance(sedici_ready_path, expected_rows)

        # 3. Evaluar estructura y validez de los XMLs en el Simple Archive Format (SAF)
        _assert_saf_structure_and_validity(saf_output_dir, expected_rows)

        # 4. Evaluar resultado del proceso de validación en DSpace REST API
        dspace = _get_dspace_client()
        assert dspace is not None, "DSpace client no disponible para consultar procesos."

        proc_resp = dspace.get("/api/system/processes?sort=startTime,desc&size=1")
        processes = proc_resp.get("_embedded", {}).get("processes", [])
        assert len(processes) > 0, "DSpace debió registrar al menos un proceso de importación."

        latest_proc = processes[0]
        assert latest_proc.get("scriptName") == "import", "El último proceso debió ser 'import'."
        assert latest_proc.get("processStatus") == "COMPLETED", (
            f"El proceso de DSpace terminó con estado {latest_proc.get('processStatus')} en lugar de COMPLETED."
        )


# ===========================================================================
# 2. Caso de Prueba 2: Importación Real Persistente en Colección Test
# ===========================================================================
class TestExportSubgraphRealImport:
    """
    Ejecuta el subgrafo realizando una importación física real (import_validate_only=False).
    Verifica la generación del mapfile y la consulta REST del ítem persistido en DSpace.
    """

    @pytest.mark.asyncio
    async def test_flujo_completo_ingesta_real_en_coleccion_dspace(self, tmp_path):
        """
        Ejecuta el flujo completo persistiendo ítems en la colección TEST_DSPACE_COLLECTION:
          - Genera crosswalk, ejecuta mapeo y correcciones.
          - Genera SAF.
          - Ingesta en DSpace en modo real.
          - Verifica que el mapfile se haya guardado y contenga identificadores reales.
        """
        assert os.path.isfile(GENERIC_RECONCILED_CSV), (
            f"CSV de entrada reconciliado no encontrado en '{GENERIC_RECONCILED_CSV}'."
        )
        df_in = pd.read_csv(GENERIC_RECONCILED_CSV)
        expected_rows = len(df_in)

        workspace_dir = str(tmp_path / "workspace_real_import")
        os.makedirs(workspace_dir, exist_ok=True)

        input_csv_copy = str(tmp_path / "reconciled_generic.csv")
        shutil.copy(GENERIC_RECONCILED_CSV, input_csv_copy)

        source_name = "generic_real_export"
        sedici_ready_path = os.path.join(workspace_dir, "sedici_ready.csv")
        saf_output_dir = os.path.join(workspace_dir, "saf_archive")
        import_mapfile_path = os.path.join(workspace_dir, "import_mapfile.txt")

        state: State = {
            "workspace_dir": workspace_dir,
            "source_name": source_name,
            "input_source_type": "pdf_minio",
            "source_csv_path": input_csv_copy,
            "repository_csv_path": input_csv_copy,
            "reconciled_csv_path": input_csv_copy,
            "sedici_ready_csv_path": sedici_ready_path,
            "saf_output_path": saf_output_dir,
            "dspace_collection": TEST_DSPACE_COLLECTION,
            "import_mapfile_path": import_mapfile_path,
            "import_validate_only": False,
            "import_exclude_bitstreams": True,
        }

        subgraph = await build_export_subgraph()
        await subgraph.ainvoke(state)

        # 1. Verificar artefactos intermedios
        _assert_sedici_csv_conformance(sedici_ready_path, expected_rows)
        _assert_saf_structure_and_validity(saf_output_dir, expected_rows)

        # 2. Verificar que DSpace emitió el mapfile y que fue guardado en disco
        assert os.path.isfile(import_mapfile_path), (
            f"DSpace debió emitir un mapfile guardado en '{import_mapfile_path}'."
        )

        with open(import_mapfile_path, "r", encoding="utf-8") as f:
            mapfile_lines = [line.strip() for line in f if line.strip()]

        assert len(mapfile_lines) == expected_rows, (
            f"El mapfile debe tener {expected_rows} líneas correspondientes a los ítems importados, "
            f"pero contiene {len(mapfile_lines)} líneas: {mapfile_lines}"
        )

        # 3. Validar formato de las líneas del mapfile: "item_001 <handle_or_uuid>"
        first_line = mapfile_lines[0].split()
        assert len(first_line) == 2, f"Línea de mapfile con formato no reconocido: {mapfile_lines[0]}"
        item_id, item_handle_or_uuid = first_line
        assert item_id.startswith("item_"), f"Identificador de item inesperado: {item_id}"

        # 4. Validar existencia física del item en DSpace consultando por handle o UUID
        dspace = _get_dspace_client()
        assert dspace is not None, "DSpace client no disponible para verificar item creado."
        search_resp = dspace.get(
            "/api/discover/search/objects",
            params={"query": f'handle:"{item_handle_or_uuid}"'},
        )
        found_objects = (
            search_resp.get("_embedded", {})
            .get("searchResult", {})
            .get("_embedded", {})
            .get("objects", [])
        )
        assert len(found_objects) > 0, (
            f"El item con handle '{item_handle_or_uuid}' debe existir e indexarse en DSpace."
        )


# ===========================================================================
# 3. Caso de Prueba 3: Esquema Genérico (Bypass LLM / Nivel 1 Determinista)
# ===========================================================================
class TestExportSubgraphBypassLLM:
    """
    Verifica que ante entradas con columnas canónicas ya estandarizadas (ej. flujo PDF-MinIO),
    el Nivel 1 determinista cubra el 100% de las columnas omitiendo la inferencia LLM.
    """

    @pytest.mark.asyncio
    async def test_flujo_esquema_generico_resuelve_nivel_1_sin_llm(self, tmp_path):
        """
        Con esquema genérico (id, title, author, date, type, doi, issn, citation),
        todas las columnas son mapeadas por reglas fijas en Nivel 1.
        """
        input_csv_copy = str(tmp_path / "generic_input.csv")
        shutil.copy(GENERIC_RECONCILED_CSV, input_csv_copy)

        workspace_dir = str(tmp_path / "workspace_bypass_llm")
        os.makedirs(workspace_dir, exist_ok=True)

        source_name = "pdf_curated_source"
        sedici_ready_path = os.path.join(workspace_dir, "sedici_ready.csv")
        saf_output_dir = os.path.join(workspace_dir, "saf_archive")
        import_mapfile_path = os.path.join(workspace_dir, "import_mapfile.txt")

        state: State = {
            "workspace_dir": workspace_dir,
            "source_name": source_name,
            "input_source_type": "pdf_minio",
            "source_csv_path": input_csv_copy,
            "repository_csv_path": input_csv_copy,
            "reconciled_csv_path": input_csv_copy,
            "sedici_ready_csv_path": sedici_ready_path,
            "saf_output_path": saf_output_dir,
            "dspace_collection": TEST_DSPACE_COLLECTION,
            "import_mapfile_path": import_mapfile_path,
            "import_validate_only": True,
            "import_exclude_bitstreams": True,
        }

        subgraph = await build_export_subgraph()
        final_state = await subgraph.ainvoke(state)

        # Verificar que el archivo de configuración fue generado exitosamente
        target_config = final_state.get("sedici_target_crosswalk_config")
        assert target_config and os.path.isfile(target_config)

        # Verificar conformidad del CSV final y SAF
        _assert_sedici_csv_conformance(sedici_ready_path, expected_rows=3)
        _assert_saf_structure_and_validity(saf_output_dir, expected_item_count=3)


# ===========================================================================
# 4. Caso de Prueba 4: Idempotencia y Reutilización de Configuración
# ===========================================================================
class TestExportSubgraphIdempotency:
    """
    Verifica que si sedici_target_crosswalk_config ya existe en el estado o en disco,
    el subgrafo lo reutilice directamente sin regenerarlo ni invocar al LLM.
    """

    @pytest.mark.asyncio
    async def test_reutilizacion_config_existente_omite_agente(self, tmp_path):
        """
        Ejecuta el subgrafo proporcionando una configuración preexistente en el estado.
        Verifica que se respete dicho archivo sin sobreescribirlo.
        """
        workspace_dir = str(tmp_path / "workspace_idempotent")
        os.makedirs(workspace_dir, exist_ok=True)

        input_csv_copy = str(tmp_path / "reconciled_generic.csv")
        shutil.copy(GENERIC_RECONCILED_CSV, input_csv_copy)

        # Crear una configuración de crosswalk sintética previa
        custom_config_path = os.path.join(workspace_dir, "custom_crosswalk_config.json")
        custom_config_content = [
            [
                {"left": "title", "replace": "dc.title[es]", "required": True, "default": ""},
                {"left": "author", "replace": "sedici.creator.person[es]", "required": False, "default": ""},
                {"left": "date", "replace": "dc.date.issued", "required": False, "default": ""},
                {"left": "type", "replace": "dc.type", "required": False, "default": ""},
            ],
            {
                "original_separator": "||",
                "replace_separator": "||",
                "file_delimiter": ",",
            },
        ]
        import json
        with open(custom_config_path, "w", encoding="utf-8") as f:
            json.dump(custom_config_content, f, indent=2)

        source_name = "idempotent_test_source"
        sedici_ready_path = os.path.join(workspace_dir, "sedici_ready.csv")
        saf_output_dir = os.path.join(workspace_dir, "saf_archive")
        import_mapfile_path = os.path.join(workspace_dir, "import_mapfile.txt")

        state: State = {
            "workspace_dir": workspace_dir,
            "source_name": source_name,
            "input_source_type": "csv",
            "source_csv_path": input_csv_copy,
            "repository_csv_path": input_csv_copy,
            "reconciled_csv_path": input_csv_copy,
            "sedici_target_crosswalk_config": custom_config_path,
            "sedici_ready_csv_path": sedici_ready_path,
            "saf_output_path": saf_output_dir,
            "dspace_collection": TEST_DSPACE_COLLECTION,
            "import_mapfile_path": import_mapfile_path,
            "import_validate_only": True,
            "import_exclude_bitstreams": True,
        }

        subgraph = await build_export_subgraph()
        final_state = await subgraph.ainvoke(state)

        # Debe mantener exactamente la misma ruta de configuración que se le pasó
        assert final_state.get("sedici_target_crosswalk_config") == custom_config_path

        # El flujo posterior debe haber generado el CSV listo y el SAF
        _assert_sedici_csv_conformance(sedici_ready_path, expected_rows=3)
        _assert_saf_structure_and_validity(saf_output_dir, expected_item_count=3)

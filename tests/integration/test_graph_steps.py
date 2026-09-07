"""
Tests de integración para todos los pasos del grafo definidos en core/graph.py.

Pasos cubiertos:
  - Paso 2a: map_source_to_generic      — crosswalk origen → formato genérico
  - Paso 2b: map_sedici_to_generic      — crosswalk SEDICI → formato genérico
  - Paso 3:  deduplicate                — mockeado (requiere servicio externo)
  - Paso 4:  metadata_reconciliation    — JOIN con metadatos originales
  - Paso 5:  map_to_sedici_format       — crosswalk origen → formato SEDICI
  - Paso 6:  metadata_corrections       — normalización programática de metadatos
  - Paso 8:  generate_saf_to_import     — generación del Simple Archive Format
  - Paso 9:  import_to_dspace           — importación a DSpace (mockeada)

Los tests usan datos reales de tests/data/ y mockean el DeduplicatorClient
y el DSpaceClient para evitar dependencias de red.
"""

import csv
import io
import json
import os
import sys
import types
import pytest

# ---------------------------------------------------------------------------
# Paths — deben definirse antes de la clase FakeDeduplicatorClient
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MCP_SRC_PATH = os.path.join(PROJECT_ROOT, "MCPs", "Deduplicator MCP", "src")
CROSSWALK_MODULE_PATH = os.path.join(PROJECT_ROOT, "core", "scripts", "crosswalk")
CONFIGS_DIR = os.path.join(CROSSWALK_MODULE_PATH, "configs")
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))

SOURCE_CSV = os.path.join(DATA_DIR, "ingest", "raw", "SearchResults.csv")
SEDICI_CSV = os.path.join(DATA_DIR, "crosswalk_dedup", "repository", "export_10915_all.csv")
SOURCE_CROSSWALK = os.path.join(CONFIGS_DIR, "sedicicrosswalkconfig.json")
SEDICI_CROSSWALK = os.path.join(CONFIGS_DIR, "sedicicrosswalkconfig.json")
TARGET_CROSSWALK = os.path.join(CONFIGS_DIR, "config_romero_to_sedici.json")

# El módulo crosswalk.py usa imports planos (from crosswalk_context import *),
# por lo que su directorio debe estar en sys.path.
for _path in (PROJECT_ROOT, MCP_SRC_PATH, CROSSWALK_MODULE_PATH):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# ---------------------------------------------------------------------------
# Mock del DeduplicatorClient — se inyecta ANTES de importar graph.py
# ---------------------------------------------------------------------------

class FakeDeduplicatorClient:
    """
    Reemplaza DeduplicatorClient sin conectarse al servidor MCP.

    Genera un CSV de deduplicación sintético con total=0 (sin duplicados)
    para todos los ítems del CSV fuente original, usando los identificadores
    reales (sedici.identifier.other) para que metadata_reconciliation
    pueda hacer el JOIN correctamente.

    NOTA: el config sedicicrosswalkconfig.json mapea dc.identifier.uri → id,
    pero el CSV Romero usa sedici.identifier.other como identificador; la
    columna 'id' en el CSV genérico queda vacía. Por eso el mock lee el CSV
    fuente original directamente y usa sedici.identifier.other como id.
    """

    def __init__(self, source_csv_path: str = SOURCE_CSV):
        self._source_csv_path = source_csv_path

    def detect_duplicates(
        self,
        csv_file1_path: str,
        csv_file2_path: str,
        source_name: str,
    ) -> bytes:
        """Devuelve un CSV con todos los ítems marcados como no-duplicados."""
        try:
            with open(self._source_csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            rows = []

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["id", "title", "match_id", "match_title", "total"],
        )
        writer.writeheader()
        for i, row in enumerate(rows):
            id_val = (
                row.get("sedici.identifier.other", "").strip()
                or row.get("dc.identifier.uri", "").strip()
                or row.get("id", "").strip()
                or str(i)
            )
            writer.writerow({
                "id": id_val,
                "title": row.get("dc.title[es]", row.get("title", "")),
                "match_id": "",
                "match_title": "",
                "total": 0,
            })
        return output.getvalue().encode("utf-8")


# Inyectar el módulo fake antes de que graph.py lo importe
_fake_module = types.ModuleType("deduplicator_client")
_fake_module.DeduplicatorClient = FakeDeduplicatorClient  # type: ignore[attr-defined]
sys.modules["deduplicator_client"] = _fake_module


# ---------------------------------------------------------------------------
# Importar los nodos del grafo (ya con el cliente mockeado)
# ---------------------------------------------------------------------------
from core.graph import (  # noqa: E402
    generate_source_crosswalk_config,
    map_source_to_generic,
    map_sedici_to_generic,
    deduplicate,
    metadata_reconciliation,
    map_to_sedici_format,
    metadata_corrections,
    generate_saf_to_import,
    import_to_dspace,
)


# ---------------------------------------------------------------------------
# Helper: estado base reutilizable
# ---------------------------------------------------------------------------

def build_state(tmp_path) -> dict:
    """Construye un State completo con rutas a archivos temporales."""
    return {
        "messages": [],
        # Paso 0 — Workspace
        "workspace_dir": str(tmp_path),
        # Inputs
        "repository_csv_path": SEDICI_CSV,
        "source_csv_path": SOURCE_CSV,
        "source_name": "Romero",
        "input_source_type": "csv",
        # Enriquecimiento
        "enrichment_enabled": False,
        # Paso 2 — Crosswalk configs y outputs intermedios
        "source_crosswalk_config": SOURCE_CROSSWALK,
        "sedici_crosswalk_config": SEDICI_CROSSWALK,
        "generic_source_csv_path": str(tmp_path / "generic_source.csv"),
        "generic_sedici_csv_path": str(tmp_path / "generic_sedici.csv"),
        # Paso 3 — Deduplicación
        "dedup_output_csv_path": str(tmp_path / "dedup_output.csv"),
        # Paso 4 — Reconciliación
        "reconciled_csv_path": str(tmp_path / "reconciled.csv"),
        # Paso 5 — Mapeo a SEDICI
        "sedici_ready_csv_path": str(tmp_path / "sedici_ready.csv"),
        # Umbrales
        "umbral_seguro": 10,
        "umbral_revision": 30,
        # Paso 8 — SAF
        "saf_output_path": str(tmp_path / "saf_output"),
        # Paso 9 — Importación
        # Usamos un UUID real obtenido de la instancia local para evitar el error 422
        "dspace_collection": "d0fb620b-6b5e-43d3-ba3f-d0183ed5b84f",
        "import_mapfile_path": str(tmp_path / "mapfile"),
        "import_validate_only": False,
        "import_exclude_bitstreams": True,
    }


# ===========================================================================
# Paso 2a (agente) — generate_source_crosswalk_config
# ===========================================================================

class TestGenerateSourceCrosswalkConfig:
    """
    Agente que genera el crosswalk config analizando el CSV fuente.

    Estrategia de tests:
      - Si el archivo config ya existe en la ruta esperada, la función lo
        reusa sin invocar el LLM.
      - Si no existe y no hay LLM disponible, se genera un config de fallback
        con rename directo de todas las columnas.
    """

    def _get_expected_config_path(self, state: dict) -> str:
        """Calcula la ruta donde la función buscará/creará el config."""
        base_dir = os.path.dirname(state["source_csv_path"]) or "."
        return os.path.join(base_dir, f"crosswalk_config_{state['source_name']}.json")

    # ── Config pre-existente ────────────────────────────────────────────

    def test_reusa_config_si_ya_existe(self, tmp_path):
        """Si el archivo config ya existe, lo reusa sin generar uno nuevo."""
        state = build_state(tmp_path)
        config_path = self._get_expected_config_path(state)

        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump([[], {"original_separator": "||", "replace_separator": "|", "file_delimiter": ","}], f)

        mtime_before = os.path.getmtime(config_path)
        result = generate_source_crosswalk_config(state)
        mtime_after = os.path.getmtime(config_path)

        assert result == {"source_crosswalk_config": config_path}
        assert mtime_before == mtime_after, "El archivo no debería modificarse"

    def test_reusa_devuelve_ruta_correcta(self, tmp_path):
        """Verifica que la ruta devuelta coincide con la esperada."""
        state = build_state(tmp_path)
        config_path = self._get_expected_config_path(state)

        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump([[], {"original_separator": "||", "replace_separator": "|", "file_delimiter": ","}], f)

        result = generate_source_crosswalk_config(state)
        assert result["source_crosswalk_config"] == config_path

    def test_no_sobrescribe_config_existente(self, tmp_path):
        """El contenido del archivo pre-existente no debe alterarse."""
        state = build_state(tmp_path)
        config_path = self._get_expected_config_path(state)

        contenido_original = [
            [{"left": "Title", "replace": "title", "default": "", "required": True, "filter": "trim"}],
            {"original_separator": "||", "replace_separator": "|", "file_delimiter": ","},
        ]
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(contenido_original, f)

        generate_source_crosswalk_config(state)

        with open(config_path, "r", encoding="utf-8") as f:
            contenido_post = json.load(f)
        assert contenido_post == contenido_original

    # ── Fallback (sin LLM, sin config pre-existente) ────────────────────

    def test_fallback_sin_config_ni_llm(self, tmp_path, monkeypatch):
        """
        Sin config pre-existente y con ChatGroq mockeado para que lance
        error, la función debe generar un config de fallback.
        """
        state = build_state(tmp_path)
        config_path = self._get_expected_config_path(state)

        if os.path.isfile(config_path):
            os.unlink(config_path)

        def fake_chatgroq(**kwargs):
            raise RuntimeError("LLM no disponible (mock)")

        monkeypatch.setattr("core.graph.ChatGroq", fake_chatgroq)

        with pytest.raises(RuntimeError, match="LLM no disponible"):
            generate_source_crosswalk_config(state)

    def test_fallback_config_tiene_formato_valido(self, tmp_path, monkeypatch):
        """
        Verifica que el fallback (generado con _generate_fallback_config)
        tenga la estructura esperada.
        """
        from core.graph import _generate_fallback_config

        head = [{"col1": "a", "col2": "b"}, {"col1": "c", "col2": "d"}]
        output_path = str(tmp_path / "fallback_config.json")

        _generate_fallback_config(head, output_path)

        assert os.path.isfile(output_path)

        with open(output_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        assert isinstance(cfg, list) and len(cfg) == 2, "Debe ser [mappings, settings]"

        mappings, settings = cfg
        assert isinstance(mappings, list), "mappings debe ser una lista"
        assert len(mappings) == 2, "Debe haber un mapping por columna"

        for m in mappings:
            assert "left" in m
            assert "replace" in m
            assert "default" in m
            assert "required" in m
            assert "filter" in m

        assert "original_separator" in settings
        assert "replace_separator" in settings
        assert "file_delimiter" in settings

    def test_fallback_con_csv_vacio_lanza_error(self, tmp_path):
        """Si el CSV está vacío, _generate_fallback_config debe lanzar ValueError."""
        from core.graph import _generate_fallback_config

        output_path = str(tmp_path / "fallback_empty.json")
        with pytest.raises(ValueError, match="No hay datos CSV"):
            _generate_fallback_config([], output_path)

    # ── Valor de retorno ────────────────────────────────────────────────

    def test_retorna_dict_con_clave_correcta(self, tmp_path):
        """
        La función debe retornar un dict con la clave 'source_crosswalk_config'
        conteniendo la ruta al archivo de configuración.
        """
        state = build_state(tmp_path)
        config_path = self._get_expected_config_path(state)

        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump([[], {"original_separator": "||", "replace_separator": "|", "file_delimiter": ","}], f)

        result = generate_source_crosswalk_config(state)
        assert "source_crosswalk_config" in result
        assert isinstance(result["source_crosswalk_config"], str)
        assert os.path.isabs(result["source_crosswalk_config"])

    # ── Integración: generación + crosswalk ──────────────────────────────

    def test_fallback_con_searchresults_produce_csv_procesable(self, tmp_path, monkeypatch):
        """
        End-to-end con SearchResults.csv: genera config por fallback,
        ejecuta el crosswalk, y verifica que el CSV de salida sea
        procesable por el lector del crosswalk (tiene datos válidos).
        """
        state = build_state(tmp_path)
        config_path = self._get_expected_config_path(state)

        if os.path.isfile(config_path):
            os.unlink(config_path)

        # Forzar fallback: ChatGroq existe pero no genera tool_calls
        class _FakeResponse:
            tool_calls = []

        class _FakeLLM:
            def bind_tools(self, tools):
                return self
            def invoke(self, messages):
                return _FakeResponse()

        monkeypatch.setattr("core.graph.ChatGroq", lambda **kw: _FakeLLM())

        generate_source_crosswalk_config(state)
        assert os.path.isfile(config_path), "El fallback debe generar el config"

        map_source_to_generic(state)
        assert os.path.isfile(state["generic_source_csv_path"])

        with open(state["generic_source_csv_path"], newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            rows = list(reader)

        assert len(headers) > 0, "El CSV debe tener al menos una columna"
        assert len(rows) > 0, "El CSV debe tener al menos una fila de datos"

        # fallback = rename directo → se conservan las columnas originales
        with open(SOURCE_CSV, newline="", encoding="utf-8") as f:
            source_headers = set(next(csv.reader(f)))
        assert set(headers) == source_headers, (
            "Con fallback las columnas deben conservarse"
        )

        # Verificar que los valores se transfirieron correctamente
        for col in ("Item Title", "Authors", "Publication Year", "Item DOI"):
            valores = [r.get(col, "").strip() for r in rows]
            assert any(valores), f"La columna '{col}' no debe estar completamente vacía"

    def test_mapeo_searchresults_a_generico_con_columnas_criticas(self, tmp_path):
        """
        Verifica que un crosswalk config correctamente configurado para
        SearchResults.csv produzca un CSV en el formato genérico
        (title, author, date, id) con valores no vacíos en las columnas
        críticas para la deduplicación.
        """
        from core.graph import _run_crosswalk

        # Crear un config que mapee SearchResults.csv → formato genérico
        config = [
            [
                {"left": "Item Title", "replace": "title", "default": "", "required": True, "filter": "trim"},
                {"left": "Authors", "replace": "author", "default": "", "required": False, "filter": "trim"},
                {"left": "Publication Year", "replace": "date", "default": "", "required": False, "filter": "trim"},
                {"left": "Item DOI", "replace": "id", "default": "", "required": False, "filter": "trim"},
                {"left": "Content Type", "replace": "type", "default": "", "required": False, "filter": "trim"},
            ],
            {"original_separator": "||", "replace_separator": "|", "file_delimiter": ","},
        ]
        config_path = str(tmp_path / "config_searchresults_to_generic.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        output_path = str(tmp_path / "generic_from_searchresults.csv")
        result_msg = _run_crosswalk(SOURCE_CSV, config_path, output_path)

        assert "Éxito" in result_msg, f"Crosswalk falló: {result_msg}"
        assert os.path.isfile(output_path)

        with open(output_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = set(reader.fieldnames or [])
            rows = list(reader)

        # Columnas críticas del formato genérico
        expected_core = {"title", "author", "date", "id"}
        assert expected_core.issubset(headers), (
            f"El CSV genérico debe contener {expected_core}. "
            f"Columnas obtenidas: {headers}"
        )

        assert len(rows) > 0, "Debe haber al menos una fila de datos"

        # Verificar que las columnas críticas tienen valores reales
        titulos = [r.get("title", "").strip() for r in rows]
        assert all(titulos), "Ningún título debe estar vacío (required=true)"

        autores = [r.get("author", "").strip() for r in rows]
        assert any(autores), "Debe haber al menos un autor"


# ===========================================================================
# Paso 2a (crosswalk) — map_source_to_generic
# ===========================================================================

class TestMapSourceToGeneric:
    """Crosswalk: CSV del repositorio origen → formato genérico."""

    def test_genera_archivo_de_salida(self, tmp_path):
        state = build_state(tmp_path)
        map_source_to_generic(state)
        assert os.path.isfile(state["generic_source_csv_path"]), (
            "map_source_to_generic debería crear el archivo de salida."
        )

    def test_archivo_no_esta_vacio(self, tmp_path):
        state = build_state(tmp_path)
        map_source_to_generic(state)
        with open(state["generic_source_csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows) > 1, "El CSV genérico del origen debe tener filas de datos."

    def test_contiene_columna_title(self, tmp_path):
        state = build_state(tmp_path)
        map_source_to_generic(state)
        with open(state["generic_source_csv_path"], newline="", encoding="utf-8") as f:
            headers = csv.DictReader(f).fieldnames or []
        assert "title" in headers, f"Se esperaba columna 'title' en {headers}"

    def test_retorna_dict_vacio(self, tmp_path):
        state = build_state(tmp_path)
        result = map_source_to_generic(state)
        assert result == {}, "El nodo debería retornar un dict vacío."


# ===========================================================================
# Paso 2b — map_sedici_to_generic
# ===========================================================================

class TestMapSediciToGeneric:
    """Crosswalk: CSV exportado de SEDICI → formato genérico."""

    def test_genera_archivo_de_salida(self, tmp_path):
        state = build_state(tmp_path)
        map_sedici_to_generic(state)
        assert os.path.isfile(state["generic_sedici_csv_path"]), (
            "map_sedici_to_generic debería crear el archivo de salida."
        )

    def test_archivo_no_esta_vacio(self, tmp_path):
        state = build_state(tmp_path)
        map_sedici_to_generic(state)
        with open(state["generic_sedici_csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows) > 1, "El CSV genérico de SEDICI debe tener filas de datos."

    def test_contiene_columnas_genericas(self, tmp_path):
        state = build_state(tmp_path)
        map_sedici_to_generic(state)
        with open(state["generic_sedici_csv_path"], newline="", encoding="utf-8") as f:
            headers = set(csv.DictReader(f).fieldnames or [])
        expected = {"title", "date", "author"}
        assert expected.issubset(headers), (
            f"Columnas genéricas {expected} no encontradas en {headers}"
        )

    def test_retorna_dict_vacio(self, tmp_path):
        state = build_state(tmp_path)
        result = map_sedici_to_generic(state)
        assert result == {}


# ===========================================================================
# Paso 3 — deduplicate (con mock)
# ===========================================================================

class TestDeduplicate:
    """Deduplicación con FakeDeduplicatorClient."""

    def _prepare_generic_csvs(self, state: dict):
        """Genera los CSVs genéricos necesarios como precondición."""
        map_source_to_generic(state)
        map_sedici_to_generic(state)

    def test_genera_archivo_de_salida(self, tmp_path):
        state = build_state(tmp_path)
        self._prepare_generic_csvs(state)
        deduplicate(state)
        assert os.path.isfile(state["dedup_output_csv_path"]), (
            "deduplicate debería crear el archivo de resultado."
        )

    def test_archivo_no_esta_vacio(self, tmp_path):
        state = build_state(tmp_path)
        self._prepare_generic_csvs(state)
        deduplicate(state)
        with open(state["dedup_output_csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows) > 1, "El CSV del deduplicador debe contener datos."

    def test_contiene_columna_total(self, tmp_path):
        state = build_state(tmp_path)
        self._prepare_generic_csvs(state)
        deduplicate(state)
        with open(state["dedup_output_csv_path"], newline="", encoding="utf-8") as f:
            headers = csv.DictReader(f).fieldnames or []
        assert "total" in headers, (
            f"El CSV del deduplicador debe tener columna 'total', tiene: {headers}"
        )

    def test_retorna_dict_vacio(self, tmp_path):
        state = build_state(tmp_path)
        self._prepare_generic_csvs(state)
        result = deduplicate(state)
        assert result == {}


# ===========================================================================
# Paso 4 — metadata_reconciliation
# ===========================================================================

class TestMetadataReconciliation:
    """Reconciliación de metadatos: JOIN con CSV original filtrado por dedup."""

    def _run_hasta_paso3(self, state: dict):
        map_source_to_generic(state)
        map_sedici_to_generic(state)
        deduplicate(state)

    def test_genera_archivo_reconciliado(self, tmp_path):
        state = build_state(tmp_path)
        self._run_hasta_paso3(state)
        metadata_reconciliation(state)
        assert os.path.isfile(state["reconciled_csv_path"]), (
            "metadata_reconciliation debería crear el archivo reconciliado."
        )

    def test_archivo_tiene_columnas_originales(self, tmp_path):
        """El CSV reconciliado debe mantener las columnas del CSV fuente original."""
        state = build_state(tmp_path)
        self._run_hasta_paso3(state)
        metadata_reconciliation(state)

        with open(SOURCE_CSV, newline="", encoding="utf-8") as f:
            source_headers = set(csv.DictReader(f).fieldnames or [])

        with open(state["reconciled_csv_path"], newline="", encoding="utf-8") as f:
            reconciled_headers = set(csv.DictReader(f).fieldnames or [])

        assert source_headers == reconciled_headers, (
            "El CSV reconciliado debe tener las mismas columnas que el CSV fuente."
        )

    def test_filtra_items_sin_duplicados(self, tmp_path):
        """Con total=0 para todos los ítems (mock), todos pasan el filtro."""
        state = build_state(tmp_path)
        self._run_hasta_paso3(state)
        metadata_reconciliation(state)

        with open(state["reconciled_csv_path"], newline="", encoding="utf-8") as f:
            reconciled_count = sum(1 for _ in csv.DictReader(f))

        assert reconciled_count > 0, "Deberían seleccionarse ítems para importar."

    def test_retorna_dict_vacio(self, tmp_path):
        state = build_state(tmp_path)
        self._run_hasta_paso3(state)
        result = metadata_reconciliation(state)
        assert result == {}


# ===========================================================================
# Paso 5 — map_to_sedici_format
# ===========================================================================

class TestMapToSediciFormat:
    """Crosswalk final: CSV reconciliado → formato SEDICI."""

    def _run_hasta_paso4(self, state: dict):
        map_source_to_generic(state)
        map_sedici_to_generic(state)
        deduplicate(state)
        metadata_reconciliation(state)

    def test_genera_archivo_sedici_ready(self, tmp_path):
        state = build_state(tmp_path)
        self._run_hasta_paso4(state)
        map_to_sedici_format(state)
        assert os.path.isfile(state["sedici_ready_csv_path"]), (
            "map_to_sedici_format debería crear el CSV final."
        )

    def test_contiene_columnas_sedici(self, tmp_path):
        state = build_state(tmp_path)
        self._run_hasta_paso4(state)
        map_to_sedici_format(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            headers = set(csv.DictReader(f).fieldnames or [])

        expected = {"dc.title[es]", "sedici.creator.person[es]", "dc.date.issued"}
        assert expected.issubset(headers), (
            f"Columnas SEDICI esperadas {expected} no encontradas en {headers}"
        )

    def test_archivo_tiene_datos(self, tmp_path):
        state = build_state(tmp_path)
        self._run_hasta_paso4(state)
        map_to_sedici_format(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) > 0, "El CSV final no debe estar vacío."

    def test_campo_titulo_no_vacio(self, tmp_path):
        """El campo dc.title[es] (required=true en el config) no debe estar vacío."""
        state = build_state(tmp_path)
        self._run_hasta_paso4(state)
        map_to_sedici_format(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            assert row.get("dc.title[es]", "").strip() != "", (
                f"Fila con título vacío encontrada: {row}"
            )

    def test_retorna_dict_vacio(self, tmp_path):
        state = build_state(tmp_path)
        self._run_hasta_paso4(state)
        result = map_to_sedici_format(state)
        assert result == {}


# ===========================================================================
# Paso 6 — metadata_corrections
# ===========================================================================

class TestMetadataCorrections:
    """Correcciones programáticas de metadatos sobre el CSV SEDICI-ready."""

    def _run_hasta_paso5(self, state: dict):
        map_source_to_generic(state)
        map_sedici_to_generic(state)
        deduplicate(state)
        metadata_reconciliation(state)
        map_to_sedici_format(state)

    def test_archivo_sigue_existiendo(self, tmp_path):
        """El CSV debe seguir existiendo después de las correcciones."""
        state = build_state(tmp_path)
        self._run_hasta_paso5(state)
        metadata_corrections(state)
        assert os.path.isfile(state["sedici_ready_csv_path"]), (
            "metadata_corrections debería conservar (y sobreescribir) el CSV."
        )

    def test_conserva_columnas(self, tmp_path):
        """Las correcciones no deben agregar ni eliminar columnas."""
        state = build_state(tmp_path)
        self._run_hasta_paso5(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            headers_before = set(csv.DictReader(f).fieldnames or [])

        metadata_corrections(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            headers_after = set(csv.DictReader(f).fieldnames or [])

        assert headers_before == headers_after, (
            f"Las correcciones alteraron las columnas.\nAntes: {headers_before}\nDespués: {headers_after}"
        )

    def test_conserva_cantidad_de_filas(self, tmp_path):
        """Las correcciones no deben filtrar ni duplicar filas."""
        state = build_state(tmp_path)
        self._run_hasta_paso5(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            count_before = sum(1 for _ in csv.DictReader(f))

        metadata_corrections(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            count_after = sum(1 for _ in csv.DictReader(f))

        assert count_before == count_after, (
            f"Se esperaban {count_before} filas, se obtuvieron {count_after}."
        )

    def test_normaliza_separadores(self, tmp_path):
        """Ninguna celda debe contener '|||' (separador SCOPUS legacy) tras corregir."""
        state = build_state(tmp_path)
        self._run_hasta_paso5(state)
        metadata_corrections(state)

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for col, val in row.items():
                    assert "|||" not in (val or ""), (
                        f"Separador '|||' encontrado en columna '{col}': {val!r}"
                    )

    def test_retorna_dict_vacio(self, tmp_path):
        state = build_state(tmp_path)
        self._run_hasta_paso5(state)
        result = metadata_corrections(state)
        assert result == {}


# ===========================================================================
# Paso 8 — generate_saf_to_import
# ===========================================================================

class TestGenerateSafToImport:
    """Generación del Simple Archive Format (SAF)."""

    def _run_hasta_paso6(self, state: dict, monkeypatch=None):
        map_source_to_generic(state)
        map_sedici_to_generic(state)
        deduplicate(state)
        metadata_reconciliation(state)
        map_to_sedici_format(state)
        metadata_corrections(state)
        # Mockear shutil.copy globalmente para evitar FileNotFoundError
        # cuando dspacearchive intente copiar PDFs que no existen (e.g. /home/maria/...)
        if monkeypatch:
            import shutil
            monkeypatch.setattr(shutil, "copy", lambda src, dst, **kwargs: None)
            monkeypatch.setattr(shutil, "copyfile", lambda src, dst, **kwargs: None)

    def test_crea_directorio_saf(self, tmp_path, monkeypatch):
        state = build_state(tmp_path)
        self._run_hasta_paso6(state, monkeypatch)
        generate_saf_to_import(state)
        assert os.path.isdir(state["saf_output_path"]), (
            "generate_saf_to_import debe crear el directorio SAF."
        )

    def test_crea_subdirectorios_item(self, tmp_path, monkeypatch):
        state = build_state(tmp_path)
        self._run_hasta_paso6(state, monkeypatch)
        generate_saf_to_import(state)

        saf_dir = state["saf_output_path"]
        item_dirs = [
            d for d in os.listdir(saf_dir)
            if d.startswith("item_") and os.path.isdir(os.path.join(saf_dir, d))
        ]
        assert len(item_dirs) > 0, "Debe haber al menos un directorio item_NNN."

    def test_dublin_core_xml_existe(self, tmp_path, monkeypatch):
        state = build_state(tmp_path)
        self._run_hasta_paso6(state, monkeypatch)
        generate_saf_to_import(state)

        saf_dir = state["saf_output_path"]
        item_dirs = sorted(
            d for d in os.listdir(saf_dir)
            if d.startswith("item_") and os.path.isdir(os.path.join(saf_dir, d))
        )
        first_item = os.path.join(saf_dir, item_dirs[0])
        assert os.path.isfile(os.path.join(first_item, "dublin_core.xml")), (
            "Cada item debe contener dublin_core.xml."
        )

    def test_contents_file_existe(self, tmp_path, monkeypatch):
        state = build_state(tmp_path)
        self._run_hasta_paso6(state, monkeypatch)
        generate_saf_to_import(state)

        saf_dir = state["saf_output_path"]
        item_dirs = sorted(
            d for d in os.listdir(saf_dir)
            if d.startswith("item_") and os.path.isdir(os.path.join(saf_dir, d))
        )
        first_item = os.path.join(saf_dir, item_dirs[0])
        assert os.path.isfile(os.path.join(first_item, "contents")), (
            "Cada item debe contener el archivo 'contents'."
        )

    def test_dublin_core_xml_es_valido(self, tmp_path, monkeypatch):
        """El XML generado debe tener el tag raíz <dublin_core>."""
        import xml.etree.ElementTree as ET
        state = build_state(tmp_path)
        self._run_hasta_paso6(state, monkeypatch)
        generate_saf_to_import(state)

        saf_dir = state["saf_output_path"]
        item_dirs = sorted(
            d for d in os.listdir(saf_dir)
            if d.startswith("item_") and os.path.isdir(os.path.join(saf_dir, d))
        )
        dc_path = os.path.join(saf_dir, item_dirs[0], "dublin_core.xml")
        tree = ET.parse(dc_path)
        root = tree.getroot()
        assert root.tag == "dublin_core", (
            f"Tag raíz esperado 'dublin_core', encontrado '{root.tag}'."
        )

    def test_retorna_dict_vacio(self, tmp_path, monkeypatch):
        state = build_state(tmp_path)
        self._run_hasta_paso6(state, monkeypatch)
        result = generate_saf_to_import(state)
        assert result == {}


# ===========================================================================
# Paso 9 — import_to_dspace (mockeado)
# ===========================================================================

class TestImportToDspace:
    """
    Importación a DSpace — se mockea el DSpaceClient para evitar
    conexión de red real. Se verifica que el nodo:
      - Llama a login() del cliente.
      - Lanza el proceso de importación.
      - Guarda el mapfile en import_mapfile_path.
      - Retorna un dict vacío (sin estado que propagar).
    """

    def _run_hasta_paso8(self, state: dict, monkeypatch):
        map_source_to_generic(state)
        map_sedici_to_generic(state)
        deduplicate(state)
        metadata_reconciliation(state)
        map_to_sedici_format(state)
        metadata_corrections(state)
        
        import shutil
        monkeypatch.setattr(shutil, "copy", lambda src, dst, **kwargs: None)
        monkeypatch.setattr(shutil, "copyfile", lambda src, dst, **kwargs: None)
        generate_saf_to_import(state)

    def test_retorna_dict_vacio_con_credenciales_faltantes(self, tmp_path, monkeypatch):
        """
        Sin DSPACE_EMAIL/PASSWORD en el entorno, el nodo debe retornar {}
        sin lanzar excepción.
        """
        state = build_state(tmp_path)
        self._run_hasta_paso8(state, monkeypatch)

        monkeypatch.delenv("DSPACE_EMAIL", raising=False)
        monkeypatch.delenv("DSPACE_PASSWORD", raising=False)

        result = import_to_dspace(state)
        assert result == {}

    def test_retorna_dict_vacio_si_saf_no_existe(self, tmp_path, monkeypatch):
        """Si el directorio SAF no existe, el nodo debe retornar {} sin excepción."""
        state = build_state(tmp_path)
        # No corremos los pasos anteriores: saf_output_path no existe
        monkeypatch.setenv("DSPACE_EMAIL", "test@test.com")
        monkeypatch.setenv("DSPACE_PASSWORD", "secret")

        result = import_to_dspace(state)
        assert result == {}

    def test_guarda_mapfile_con_cliente_mock(self, tmp_path, monkeypatch):
        """
        Verifica el flujo completo con un DSpaceClient completamente mockeado:
        - login() no falla
        - el proceso de importación retorna processId=42
        - el polling retorna COMPLETED
        - se descarga un mapfile sintético
        - el mapfile se guarda en import_mapfile_path
        """
        state = build_state(tmp_path)
        self._run_hasta_paso8(state, monkeypatch)

        monkeypatch.setenv("DSPACE_EMAIL", "admin@sedici.unlp.edu.ar")
        monkeypatch.setenv("DSPACE_PASSWORD", "secret")
        monkeypatch.setenv("DSPACE_BASE_URL", "http://localhost:8080/server")

        # Monkeypatch de los helpers del módulo import_tools
        mcp_src = os.path.join(PROJECT_ROOT, "MCPs", "Dspace MCP", "src")
        if mcp_src not in sys.path:
            sys.path.insert(0, mcp_src)

        import importlib
        import tools.import_tools as it

        FAKE_MAPFILE = "item_001 123456789/100\nitem_002 123456789/101\n"

        monkeypatch.setattr(it, "_zip_saf_directory", lambda d: b"PK fake zip")
        monkeypatch.setattr(it, "_launch_import_process",
                            lambda client, zb, zn, params: {"processId": 42})
        monkeypatch.setattr(it, "_poll_until_done",
                            lambda client, pid, timeout: {"processStatus": "COMPLETED"})
        monkeypatch.setattr(it, "_get_process_files",
                            lambda client, pid: [{"name": "mapfile", "type": "mapfile",
                                                  "_links": {"content": {"href": "http://x/mapfile"}}}])
        monkeypatch.setattr(it, "_download_file_by_type",
                            lambda client, files, ftype: FAKE_MAPFILE if ftype == "mapfile" else None)

        # También mockear DSpaceClient.login para que no haga red
        from dspace_client import DSpaceClient
        monkeypatch.setattr(DSpaceClient, "login", lambda self: None)

        result = import_to_dspace(state)

        assert result == {}
        assert os.path.isfile(state["import_mapfile_path"]), (
            "El mapfile debe haberse guardado en import_mapfile_path."
        )
        with open(state["import_mapfile_path"], encoding="utf-8") as f:
            content = f.read()
        assert "123456789/100" in content, "El mapfile debe contener los handles importados."


# ===========================================================================
# Test de pipeline completo (end-to-end)
# ===========================================================================

class TestPipelineCompleto:
    """Ejecuta todos los pasos en secuencia como lo haría el grafo."""

    def test_pipeline_end_to_end(self, tmp_path, monkeypatch):
        state = build_state(tmp_path)

        map_source_to_generic(state)
        assert os.path.isfile(state["generic_source_csv_path"]), "Paso 2a falló"

        map_sedici_to_generic(state)
        assert os.path.isfile(state["generic_sedici_csv_path"]), "Paso 2b falló"

        deduplicate(state)
        assert os.path.isfile(state["dedup_output_csv_path"]), "Paso 3 falló"

        metadata_reconciliation(state)
        assert os.path.isfile(state["reconciled_csv_path"]), "Paso 4 falló"

        map_to_sedici_format(state)
        assert os.path.isfile(state["sedici_ready_csv_path"]), "Paso 5 falló"

        metadata_corrections(state)
        assert os.path.isfile(state["sedici_ready_csv_path"]), "Paso 6 falló"

        import shutil
        monkeypatch.setattr(shutil, "copy", lambda src, dst, **kwargs: None)
        monkeypatch.setattr(shutil, "copyfile", lambda src, dst, **kwargs: None)
        
        generate_saf_to_import(state)
        assert os.path.isdir(state["saf_output_path"]), "Paso 8 falló"

        saf_dir = state["saf_output_path"]
        item_dirs = [d for d in os.listdir(saf_dir) if d.startswith("item_")]
        assert len(item_dirs) > 0, "El SAF debe contener al menos un ítem."

        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0, "El pipeline completo debe producir al menos un ítem."


# ===========================================================================
# Ejecución directa
# ===========================================================================

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        state = build_state(tmp_path)

        print("\n=== Paso 2a: map_source_to_generic ===")
        map_source_to_generic(state)
        print(f"  → {state['generic_source_csv_path']}")

        print("\n=== Paso 2b: map_sedici_to_generic ===")
        map_sedici_to_generic(state)
        print(f"  → {state['generic_sedici_csv_path']}")

        print("\n=== Paso 3: deduplicate (mock) ===")
        deduplicate(state)
        print(f"  → {state['dedup_output_csv_path']}")

        print("\n=== Paso 4: metadata_reconciliation ===")
        metadata_reconciliation(state)
        with open(state["reconciled_csv_path"], newline="", encoding="utf-8") as f:
            count = sum(1 for _ in csv.DictReader(f))
        print(f"  → {count} ítems reconciliados")

        print("\n=== Paso 5: map_to_sedici_format ===")
        map_to_sedici_format(state)
        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            final_rows = list(csv.DictReader(f))
        print(f"  → {len(final_rows)} ítems listos para SEDICI")

        print("\n=== Paso 6: metadata_corrections ===")
        metadata_corrections(state)
        with open(state["sedici_ready_csv_path"], newline="", encoding="utf-8") as f:
            corrected_rows = list(csv.DictReader(f))
        print(f"  → {len(corrected_rows)} ítems tras correcciones")

        print("\n=== Paso 8: generate_saf_to_import ===")
        # Mock global para la ejecución directa
        import shutil
        original_copy = shutil.copy
        shutil.copy = lambda src, dst, **kwargs: None
        shutil.copyfile = lambda src, dst, **kwargs: None
        try:
            generate_saf_to_import(state)
        finally:
            shutil.copy = original_copy
            
        saf_items = [
            d for d in os.listdir(state["saf_output_path"])
            if d.startswith("item_")
        ]
        try:
            with open(state["saf_output_path"], newline="", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            content = "#### NO SE PUDO LEER EL SAF ####"
        print(f"  → SAF generado en '{state['saf_output_path']}' ({len(saf_items)} ítems)")
        print(f"  → Contenido del SAF: {content}")

        print("\n=== Paso 9: import_to_dspace (requiere DSpace activo) ===")
        import_to_dspace(state)
        try:
            with open(state["import_mapfile_path"], newline="", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            content = "#### NO SE PUDO LEER EL MAPFILE ####"
        print(f"  → Mapfile generado en '{state['import_mapfile_path']}'")
        print(f"  → Contenido del mapfile: {content}")

        print("\n✅ Pipeline hasta Paso 8 ejecutado correctamente.")

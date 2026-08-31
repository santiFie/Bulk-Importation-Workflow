"""
Tests para el nodo `transform_metadata_csv` definido en core/graph.py.

La función toma un `State` con:
  - source_csv_path   : ruta al CSV de entrada
  - config_json_path  : ruta al JSON de crosswalk
  - target_csv_path   : ruta donde se guardará el CSV transformado

Los CSVs de prueba se encuentran en tests/data/.
"""

import csv
import json
import os
import sys
import tempfile
import pytest

# ---------------------------------------------------------------------------
# Configuración de paths para importar los módulos del proyecto sin instalarlo
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CROSSWALK_MODULE_PATH = os.path.join(PROJECT_ROOT, "core", "scripts", "crosswalk")
CONFIGS_DIR = os.path.join(CROSSWALK_MODULE_PATH, "configs")
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))

for path in (PROJECT_ROOT, CROSSWALK_MODULE_PATH):
    if path not in sys.path:
        sys.path.insert(0, path)

# ---------------------------------------------------------------------------
# Importar las clases del crosswalk directamente (sin depender de graph.py
# para evitar la inicialización costosa del grafo completo en cada test)
# ---------------------------------------------------------------------------
from core.scripts.crosswalk.crosswalk import Crosswalk
from core.scripts.crosswalk.csv_handler import CsvHandler
from core.scripts.crosswalk.crosswalk_context import CrosswalkContext


# ---------------------------------------------------------------------------
# Mapa fuente → archivo de configuración de crosswalk
# ---------------------------------------------------------------------------
SOURCE_TO_CONFIG: dict[str, str] = {
    "sedici": "sedicicrosswalkconfig.json",
    "oaidc": "configoaidc.json",
    "conicet": "config_conicet.json",
    "conicet_generic": "config_conicet_generic.json",
    "memoria": "config_memoria.json",
    "memoria_to_sedici": "config_memoria_to_sedici.json",
    "scopus": "config_scopus_51.json",
    "scopus_busqueda": "config_scopus_busqueda.json",
    "limpiar": "config_limpiar.json",
    "wildcard": "configwildcard.json",
    "romero_to_sedici": "config_romero_to_sedici.json",  # Paso 5: origen Romero → formato SEDICI
}


def get_config_path_for_source(source_name: str) -> str:
    """
    Dado el nombre de la fuente del CSV (por ejemplo 'sedici'), retorna la
    ruta absoluta al archivo JSON de configuración del crosswalk correspondiente.

    Args:
        source_name: Identificador de la fuente en minúsculas
                     (p. ej. 'sedici', 'oaidc', 'conicet').

    Returns:
        Ruta absoluta al JSON de configuración.

    Raises:
        KeyError: Si no existe un mapeo para la fuente indicada.
    """
    source_key = source_name.strip().lower()
    if source_key not in SOURCE_TO_CONFIG:
        raise KeyError(
            f"Fuente desconocida: '{source_name}'. "
            f"Fuentes disponibles: {sorted(SOURCE_TO_CONFIG.keys())}"
        )
    filename = SOURCE_TO_CONFIG[source_key]
    return os.path.join(CONFIGS_DIR, filename)


# ---------------------------------------------------------------------------
# Función bajo test: réplica del nodo transform_metadata_csv sin depender
# del StateGraph completo de LangGraph.
#
# NOTA: CrosswalkContext define `setting_list` a nivel de clase (class-level
# mutable list), lo que hace que los settings se acumulen entre instancias.
# Reseteamos ese atributo antes de cada instanciación para aislar los tests.
# ---------------------------------------------------------------------------
def transform_metadata_csv(state: dict) -> str:
    """
    Réplica standalone del nodo de grafo para facilitar los tests unitarios.
    Misma lógica que core/graph.py::transform_metadata_csv.
    """
    csv_input_path = state["source_csv_path"]
    config_json_path = state["config_json_path"]
    csv_output_path = state["target_csv_path"]

    csv.field_size_limit(sys.maxsize)

    try:
        with open(config_json_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Resetear la lista de clase antes de crear la instancia para evitar
        # que los settings de tests anteriores contaminen el contexto actual.
        CrosswalkContext.setting_list = []
        context = CrosswalkContext(config)
        handler = CsvHandler()
        csv_list = handler.csv_to_list(csv_input_path, context.file_delimiter)

        if not csv_list:
            return "Error: El archivo CSV de entrada está vacío o no se pudo leer."

        crosswalk = Crosswalk(context)
        new_csv_list = crosswalk.transform(csv_list)

        handler.list_to_csv(new_csv_list, csv_output_path)
        return f"Éxito: El mapeo se realizó correctamente. Archivo guardado en {csv_output_path}"

    except Exception as e:
        return f"Error durante la ejecución del crosswalk: {repr(e)}"


# ===========================================================================
# Tests
# ===========================================================================


class TestGetConfigPathForSource:
    """Tests del helper que mapea nombre de fuente → archivo JSON."""

    def test_sedici_maps_to_correct_config(self):
        path = get_config_path_for_source("sedici")
        assert path.endswith("sedicicrosswalkconfig.json")

    def test_oaidc_maps_to_correct_config(self):
        path = get_config_path_for_source("oaidc")
        assert path.endswith("configoaidc.json")

    def test_conicet_maps_to_correct_config(self):
        path = get_config_path_for_source("conicet")
        assert path.endswith("config_conicet.json")

    def test_source_name_is_case_insensitive(self):
        assert get_config_path_for_source("SEDICI") == get_config_path_for_source("sedici")
        assert get_config_path_for_source("Oaidc") == get_config_path_for_source("oaidc")

    def test_source_name_strips_whitespace(self):
        assert get_config_path_for_source("  sedici  ") == get_config_path_for_source("sedici")

    def test_unknown_source_raises_key_error(self):
        with pytest.raises(KeyError, match="fuente_inexistente"):
            get_config_path_for_source("fuente_inexistente")

    def test_returned_config_file_exists_for_all_sources(self):
        """Todos los configs del mapa deben existir en el sistema de archivos."""
        for source in SOURCE_TO_CONFIG:
            path = get_config_path_for_source(source)
            assert os.path.isfile(path), (
                f"El archivo de configuración para '{source}' no existe: {path}"
            )


class TestTransformMetadataCsvWithSedici:
    """Tests de transformación usando el CSV de SEDICI como fuente."""

    def test_sedici_transform_returns_success_message(self, tmp_path):
        output_csv = "/home/santi/Documentos/LangGraph/Modulo-Marta/tests/data/export.csv"
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "result-14531-Romero.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert result.startswith("Éxito"), f"Se esperaba éxito pero se obtuvo: {result}"

    def test_sedici_output_file_is_created(self, tmp_path):
        output_csv = str(tmp_path / "sedici_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "sedici_input.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        transform_metadata_csv(state)
        assert os.path.isfile(output_csv), "El archivo de salida no fue creado."

    def test_sedici_output_has_expected_columns(self, tmp_path):
        """Las columnas de salida deben coincidir con los campos 'replace' del config."""
        output_csv = str(tmp_path / "sedici_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "sedici_input.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        transform_metadata_csv(state)

        with open(output_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

        expected_cols = {"title", "date", "description", "author", "id"}
        assert expected_cols.issubset(set(headers)), (
            f"Columnas esperadas {expected_cols} no están en {headers}"
        )

    def test_sedici_output_has_data_rows(self, tmp_path):
        output_csv = str(tmp_path / "sedici_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "sedici_input.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        transform_metadata_csv(state)

        with open(output_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) > 0, "El CSV de salida no tiene filas de datos."

    def test_sedici_title_column_is_not_empty(self, tmp_path):
        """El campo 'title' (required=true en el config) no debe estar vacío."""
        output_csv = str(tmp_path / "sedici_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "sedici_input.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        transform_metadata_csv(state)

        with open(output_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            assert row.get("title", "").strip() != "", (
                f"Se encontró una fila con 'title' vacío: {row}"
            )


class TestTransformMetadataCsvWithOaidc:
    """Tests de transformación usando el CSV OAIDC como fuente."""

    def test_oaidc_transform_returns_success_message(self, tmp_path):
        output_csv = str(tmp_path / "oaidc_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "oaidc_input.csv"),
            "config_json_path": get_config_path_for_source("oaidc"),
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert result.startswith("Éxito"), f"Se esperaba éxito pero se obtuvo: {result}"

    def test_oaidc_output_has_expected_columns(self, tmp_path):
        output_csv = str(tmp_path / "oaidc_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "oaidc_input.csv"),
            "config_json_path": get_config_path_for_source("oaidc"),
            "target_csv_path": output_csv,
        }
        transform_metadata_csv(state)

        with open(output_csv, "r", encoding="utf-8") as f:
            headers = csv.DictReader(f).fieldnames or []

        expected_cols = {"dc.title", "dc.type", "sedici.contributor.director"}
        assert expected_cols.issubset(set(headers)), (
            f"Columnas esperadas {expected_cols} no están en {headers}"
        )

    def test_oaidc_author_is_lowercased(self, tmp_path):
        """El config de oaidc aplica el filtro 'lowercase' al campo author."""
        output_csv = str(tmp_path / "oaidc_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "oaidc_input.csv"),
            "config_json_path": get_config_path_for_source("oaidc"),
            "target_csv_path": output_csv,
        }
        transform_metadata_csv(state)

        with open(output_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            author_val = row.get("sedici.contributor.director", "")
            assert author_val == author_val.lower(), (
                f"Se esperaba autor en minúsculas pero se obtuvo: '{author_val}'"
            )


class TestTransformMetadataCsvEdgeCases:
    """Tests de casos límite y manejo de errores."""

    def test_empty_csv_returns_error_message(self, tmp_path):
        output_csv = str(tmp_path / "empty_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "empty.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert "Error" in result, (
            f"Se esperaba un mensaje de error para CSV vacío, pero se obtuvo: {result}"
        )

    def test_nonexistent_input_csv_returns_error(self, tmp_path):
        output_csv = str(tmp_path / "output.csv")
        state = {
            "source_csv_path": "/ruta/que/no/existe/archivo.csv",
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert "Error" in result

    def test_nonexistent_config_json_returns_error(self, tmp_path):
        output_csv = str(tmp_path / "output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "sedici_input.csv"),
            "config_json_path": "/ruta/que/no/existe/config.json",
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert "Error" in result

    def test_output_path_in_new_directory_is_created(self, tmp_path):
        """El archivo de salida se puede escribir aunque el directorio no exista previamente."""
        new_dir = tmp_path / "subdir" / "nested"
        new_dir.mkdir(parents=True)
        output_csv = str(new_dir / "output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "sedici_input.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert result.startswith("Éxito")
        assert os.path.isfile(output_csv)

    def test_success_message_contains_output_path(self, tmp_path):
        output_csv = str(tmp_path / "result.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "sedici_input.csv"),
            "config_json_path": get_config_path_for_source("sedici"),
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert output_csv in result, (
            f"El mensaje de éxito debería contener la ruta de salida '{output_csv}', "
            f"pero se obtuvo: '{result}'"
        )


# ===========================================================================
# Tests del config romero_to_sedici (Paso 5 del pipeline)
# ===========================================================================


class TestRomeroToSediciCrosswalk:
    """Tests del crosswalk Paso 5: origen Romero → formato SEDICI."""

    def test_romero_config_exists(self):
        path = get_config_path_for_source("romero_to_sedici")
        assert os.path.isfile(path), f"Config no encontrado: {path}"

    def test_romero_to_sedici_transform_returns_success(self, tmp_path):
        output_csv = str(tmp_path / "romero_sedici_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "result-14531-Romero.csv"),
            "config_json_path": get_config_path_for_source("romero_to_sedici"),
            "target_csv_path": output_csv,
        }
        result = transform_metadata_csv(state)
        assert result.startswith("Éxito"), f"Se esperaba éxito pero se obtuvo: {result}"

    def test_romero_to_sedici_output_has_sedici_columns(self, tmp_path):
        output_csv = str(tmp_path / "romero_sedici_output.csv")
        state = {
            "source_csv_path": os.path.join(DATA_DIR, "result-14531-Romero.csv"),
            "config_json_path": get_config_path_for_source("romero_to_sedici"),
            "target_csv_path": output_csv,
        }
        transform_metadata_csv(state)
        with open(output_csv, "r", encoding="utf-8") as f:
            headers = csv.DictReader(f).fieldnames or []
        expected = {"dc.title[es]", "sedici.creator.person[es]", "dc.date.issued"}
        assert expected.issubset(set(headers)), (
            f"Columnas SEDICI esperadas {expected} no encontradas en {headers}"
        )


# ===========================================================================
# Ejecución directa
# ===========================================================================

if __name__ == "__main__":
    import tempfile

    print("\n=== Test rápido: Paso 2 (crosswalk sedici → genérico) ===")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name

    state_p2 = {
        "source_csv_path": os.path.join(DATA_DIR, "result-14531-Romero.csv"),
        "config_json_path": get_config_path_for_source("sedici"),
        "target_csv_path": tmp_path,
    }
    result_p2 = transform_metadata_csv(state_p2)
    print(result_p2)

    print("\n=== Test rápido: Paso 5 (crosswalk romero → sedici) ===")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp5:
        tmp_path5 = tmp5.name

    state_p5 = {
        "source_csv_path": os.path.join(DATA_DIR, "result-14531-Romero.csv"),
        "config_json_path": get_config_path_for_source("romero_to_sedici"),
        "target_csv_path": tmp_path5,
    }
    result_p5 = transform_metadata_csv(state_p5)
    print(result_p5)

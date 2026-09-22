"""
Tests unitarios para el nodo MapToSediciFormat (Paso 5) y CrosswalkRunner.

Módulo bajo prueba: core/nodes/crosswalk_nodes.py
Función principal: map_to_sedici_format

Verifica:
  - Invocación exitosa de map_to_sedici_format delegando en CrosswalkRunner.
  - Creación automática de directorios de salida.
  - Captura y transformación de excepciones (CrosswalkApiError -> RuntimeError).
  - Manejo de columnas espurias ('Unnamed: 0') junto con el filtro de saf_node.
  - Resiliencia del filtrado en Crosswalk ante claves None (campos no delimitados).
"""

import io
import os
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.clients.crosswalk_client import CrosswalkApiError
from core.nodes.crosswalk_nodes import (
    CrosswalkRunner,
    _run_crosswalk,
    _save_csv,
    map_to_sedici_format,
)
from core.nodes.saf_node import _prepare_dataframe


@pytest.fixture
def mock_crosswalk_state(tmp_path):
    """Genera un estado mínimo con rutas temporales para el nodo map_to_sedici_format."""
    reconciled_path = tmp_path / "reconciled.csv"
    target_config_path = tmp_path / "config_sedici.json"
    sedici_ready_path = tmp_path / "output" / "sedici_ready.csv"

    # CSV reconciliado ficticio
    reconciled_path.write_text(
        "id,title,authors,date\n"
        "1,Mi Articulo,Perez Juan,2024\n",
        encoding="utf-8",
    )

    # Config JSON ficticio
    target_config_path.write_text(
        '[[], {"original_separator": "||", "replace_separator": "||", "file_delimiter": ","}]',
        encoding="utf-8",
    )

    return {
        "reconciled_csv_path": str(reconciled_path),
        "sedici_target_crosswalk_config": str(target_config_path),
        "sedici_ready_csv_path": str(sedici_ready_path),
    }


# ===========================================================================
# 1. Tests de ejecución exitosa
# ===========================================================================

def test_map_to_sedici_format_success(mock_crosswalk_state):
    """
    Verifica que map_to_sedici_format invoque a CrosswalkRunner,
    escriba el archivo resultante en disco y retorne un dict vacío.
    """
    expected_csv_content = (
        b"dc.title[es],sedici.creator.person[es],dc.date.issued\n"
        b"Mi Articulo,Perez Juan,2024\n"
    )

    with patch("core.nodes.crosswalk_nodes.CrosswalkClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.run_crosswalk.return_value = expected_csv_content
        mock_client_cls.return_value = mock_client

        state = mock_crosswalk_state
        result = map_to_sedici_format(state)

        # La convención del nodo en LangGraph es retornar {}
        assert result == {}

        # Verificar que el cliente fue llamado con los argumentos correctos
        mock_client.run_crosswalk.assert_called_once_with(
            csv_path=state["reconciled_csv_path"],
            config_path=state["sedici_target_crosswalk_config"],
        )

        # Verificar que el archivo de salida fue creado con el contenido esperado
        out_path = state["sedici_ready_csv_path"]
        assert os.path.isfile(out_path)
        with open(out_path, "rb") as fh:
            assert fh.read() == expected_csv_content


def test_crosswalk_runner_creates_parent_directories(tmp_path):
    """
    Verifica que CrosswalkRunner._write_output cree automáticamente las carpetas intermedias
    si la ruta de salida apunta a un subdirectorio inexistente.
    """
    nested_output = tmp_path / "deeply" / "nested" / "dir" / "result.csv"
    runner = CrosswalkRunner()

    with patch("core.nodes.crosswalk_nodes.CrosswalkClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.run_crosswalk.return_value = b"col1,col2\nval1,val2\n"
        mock_client_cls.return_value = mock_client

        msg = runner.run("dummy_in.csv", "dummy_cfg.json", str(nested_output))

        assert os.path.isfile(str(nested_output))
        assert "Éxito: Mapeo realizado correctamente" in msg


# ===========================================================================
# 2. Tests de manejo de errores
# ===========================================================================

def test_map_to_sedici_format_raises_runtime_error_on_api_error(mock_crosswalk_state):
    """
    Verifica que cuando la API de crosswalk falla arrojando CrosswalkApiError,
    el nodo map_to_sedici_format propague RuntimeError con mensaje explicativo.
    """
    with patch("core.nodes.crosswalk_nodes.CrosswalkClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.run_crosswalk.side_effect = CrosswalkApiError("400 Bad Request: Config inválido")
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Error de API durante el crosswalk"):
            map_to_sedici_format(mock_crosswalk_state)


def test_map_to_sedici_format_raises_runtime_error_on_unexpected_exception(mock_crosswalk_state):
    """
    Verifica que ante errores inesperados (ej. problemas de I/O o red),
    el nodo map_to_sedici_format lance RuntimeError.
    """
    with patch("core.nodes.crosswalk_nodes.CrosswalkClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.run_crosswalk.side_effect = IOError("Error de disco simulado")
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Error durante la ejecución del crosswalk"):
            map_to_sedici_format(mock_crosswalk_state)


# ===========================================================================
# 3. Tests de helpers de compatibilidad
# ===========================================================================

def test_run_crosswalk_helper_delegates_to_runner():
    """Verifica que _run_crosswalk delegue en la instancia singleton _runner."""
    with patch("core.nodes.crosswalk_nodes._runner.run") as mock_run:
        mock_run.return_value = "Éxito"
        res = _run_crosswalk("in.csv", "cfg.json", "out.csv")
        mock_run.assert_called_once_with("in.csv", "cfg.json", "out.csv")
        assert res == "Éxito"


def test_save_csv_helper(tmp_path):
    """Verifica que _save_csv escriba correctamente y cuente filas."""
    out_file = str(tmp_path / "saved.csv")
    csv_bytes = b"colA,colB\n1,2\n3,4\n"
    res = _save_csv(csv_bytes, out_file)

    assert res["status"] == "ok"
    assert res["csv_path"] == out_file
    assert res["row_count"] == 2
    assert os.path.isfile(out_file)


# ===========================================================================
# 4. Tests de detección y neutralización de columnas espurias ('Unnamed: 0')
# ===========================================================================

def test_sedici_output_with_unnamed_cleaned_by_saf_preparation(tmp_path):
    """
    Verifica que si el output del crosswalk contiene una columna 'Unnamed: 0'
    (producto de serializaciones con índice o de comas excedentes),
    la función _prepare_dataframe del nodo SAF la descarte exitosamente.
    """
    csv_with_unnamed = tmp_path / "sedici_with_unnamed.csv"
    # Simula un CSV generado con índice residual y columna válida
    csv_with_unnamed.write_text(
        "Unnamed: 0,dc.title[es],sedici.creator.person[es]\n"
        "0,Titulo de prueba,Autor de prueba\n",
        encoding="utf-8",
    )

    df_prepared = _prepare_dataframe(str(csv_with_unnamed))

    # 'Unnamed: 0' debe haber sido descartada
    assert "Unnamed: 0" not in df_prepared.columns
    # La columna 'files' debe estar presente al inicio
    assert df_prepared.columns[0] == "files"
    # Las columnas legítimas deben mantenerse
    assert "dc.title[es]" in df_prepared.columns
    assert "sedici.creator.person[es]" in df_prepared.columns
    assert len(df_prepared) == 1


def test_crosswalk_script_delete_absent_cols_handles_none_key():
    """
    Verifica que la función deleteAbsentCols del script crosswalk.py
    elimine de forma segura la clave None (generada por DictReader ante
    comas no escapadas) sin lanzar TypeError por fnmatch.
    """
    import sys
    script_dir = os.path.join(os.path.dirname(__file__), "..", "..", "core", "scripts", "crosswalk")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    from crosswalk import Crosswalk

    class DummyContext:
        pass

    cw = Crosswalk(DummyContext())

    # Diccionario que simula la salida de csv.DictReader cuando hay comas sin entrecomillar:
    # row[None] = ['valor_extra_1', 'valor_extra_2']
    row_with_none_key = {
        "title": "Mi Titulo",
        None: ["valor_extra_1", "valor_extra_2"],
        "": "campo_vacio",
    }
    cols_to_maintain = ["title"]

    # No debe arrojar excepción TypeError
    cw.deleteAbsentCols(row_with_none_key, cols_to_maintain)

    assert None not in row_with_none_key
    assert "" not in row_with_none_key
    assert "title" in row_with_none_key

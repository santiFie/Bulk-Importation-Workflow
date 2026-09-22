"""
Tests unitarios para el nodo ImportToDspace (core/nodes/import_node.py).
"""

import os
from unittest.mock import MagicMock, patch
import pytest

from core.nodes.import_node import (
    DSpaceImportConfig,
    import_to_dspace,
    _validate_preconditions,
    _save_mapfile,
)


class TestDSpaceImportConfig:
    """Verifica la construcción de parámetros del script de importación."""

    def test_build_parameters_standard(self):
        cfg = DSpaceImportConfig(
            saf_dir="/tmp/saf",
            collection="12345/10",
            mapfile_path="/tmp/mapfile.txt",
            validate_only=False,
            exclude_bitstreams=True,
        )
        params = cfg.build_parameters()
        param_names = [p["name"] for p in params]

        assert "-a" in param_names
        assert "-z" in param_names
        assert "-c" in param_names
        assert "-x" in param_names
        assert "-v" not in param_names

    def test_build_parameters_validate_only_and_include_bitstreams(self):
        cfg = DSpaceImportConfig(
            saf_dir="/tmp/saf",
            collection="12345/10",
            mapfile_path="/tmp/mapfile.txt",
            validate_only=True,
            exclude_bitstreams=False,
        )
        params = cfg.build_parameters()
        param_names = [p["name"] for p in params]

        assert "-v" in param_names
        assert "-x" not in param_names


class TestImportToDSpaceNode:
    """Verifica el flujo del nodo import_to_dspace aislando la API REST de DSpace."""

    def test_preconditions_validation_fails_on_missing_dir(self, tmp_path):
        cfg = DSpaceImportConfig(
            saf_dir=str(tmp_path / "inexistente"),
            collection="12345/10",
            mapfile_path="",
        )
        assert _validate_preconditions(cfg) is False

    def test_preconditions_validation_fails_on_missing_collection(self, tmp_path):
        saf_dir = tmp_path / "saf_exists"
        saf_dir.mkdir()
        cfg = DSpaceImportConfig(
            saf_dir=str(saf_dir),
            collection="",
            mapfile_path="",
        )
        assert _validate_preconditions(cfg) is False

    def test_save_mapfile(self, tmp_path):
        mapfile_dest = tmp_path / "output_mapfile.txt"
        _save_mapfile("item_001 1234/567\nitem_002 1234/568\n", str(mapfile_dest))

        assert os.path.isfile(mapfile_dest)
        with open(mapfile_dest, "r") as f:
            content = f.read()
        assert "item_001 1234/567" in content

    def test_import_to_dspace_full_mocked_success(self, tmp_path):
        saf_dir = tmp_path / "saf_folder"
        saf_dir.mkdir()
        mapfile_path = tmp_path / "mapfile.txt"

        state = {
            "saf_output_path": str(saf_dir),
            "dspace_collection": "12345/10",
            "import_mapfile_path": str(mapfile_path),
            "import_validate_only": False,
            "import_exclude_bitstreams": True,
        }

        mock_dspace = MagicMock()
        mock_proc_data = {"processId": 42}
        mock_final_proc = {"processStatus": "COMPLETED"}

        with patch("core.nodes.import_node._authenticate_dspace", return_value=mock_dspace), \
             patch("core.nodes.import_node._zip_saf", return_value=b"PK fake zip bytes"), \
             patch("core.nodes.import_node._launch_import", return_value=mock_proc_data), \
             patch("mcps.dspace_mcp.src.tools.import_tools._poll_until_done", return_value=mock_final_proc), \
             patch("mcps.dspace_mcp.src.tools.import_tools._get_process_files", return_value=["mapfile", "log"]), \
             patch("mcps.dspace_mcp.src.tools.import_tools._download_file_by_type") as mock_download:

            mock_download.side_effect = lambda client, files, file_type: (
                "item_000 12345/999\n" if file_type == "mapfile" else "Import log: OK"
            )

            res = import_to_dspace(state)

            assert res == {}
            assert os.path.isfile(mapfile_path)
            with open(mapfile_path, "r") as fh:
                assert "item_000 12345/999" in fh.read()

    def test_import_to_dspace_auth_failure_handles_cleanly(self, tmp_path):
        saf_dir = tmp_path / "saf_folder"
        saf_dir.mkdir()

        state = {
            "saf_output_path": str(saf_dir),
            "dspace_collection": "12345/10",
        }

        with patch("core.nodes.import_node._authenticate_dspace", return_value=None):
            res = import_to_dspace(state)
            assert res == {}

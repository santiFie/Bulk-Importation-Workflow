"""
Tests unitarios para los nodos de Crosswalk (core/nodes/crosswalk_nodes.py y subgrafos).
"""

import os
import shutil
from unittest.mock import MagicMock, patch
import pytest

from core.clients.crosswalk_client import CrosswalkApiError
from core.nodes.crosswalk_nodes import (
    CrosswalkRunner,
    _run_crosswalk,
    _save_csv,
    map_source_to_generic,
    map_sedici_to_generic,
    map_to_sedici_format,
)
from core.subgraphs.crosswalk_dedup import bypass_source_crosswalk
from core.nodes.source_to_generic.node import (
    generate_source_crosswalk_config,
    SourceToGenericCrosswalkGenerator,
)


class TestCrosswalkRunner:
    """Verifica el servicio CrosswalkRunner que invoca la API del microservicio."""

    def test_run_success(self, tmp_path):
        in_csv = str(tmp_path / "in.csv")
        cfg_json = str(tmp_path / "cfg.json")
        out_csv = str(tmp_path / "out.csv")

        with open(in_csv, "w") as f:
            f.write("a,b\n1,2\n")
        with open(cfg_json, "w") as f:
            f.write("[]")

        expected_bytes = b"id,title\n1,Test"

        with patch("core.nodes.crosswalk_nodes.CrosswalkClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.run_crosswalk.return_value = expected_bytes
            mock_client_cls.return_value = mock_client

            runner = CrosswalkRunner()
            msg = runner.run(in_csv, cfg_json, out_csv)

            assert "Éxito" in msg
            assert os.path.isfile(out_csv)
            with open(out_csv, "rb") as fh:
                assert fh.read() == expected_bytes

    def test_run_creates_parent_directories(self, tmp_path):
        nested_out = str(tmp_path / "nested" / "subfolder" / "out.csv")
        runner = CrosswalkRunner()
        runner._write_output(b"dummy,content\n", nested_out)

        assert os.path.isfile(nested_out)

    def test_run_raises_runtime_error_on_api_error(self, tmp_path):
        with patch("core.nodes.crosswalk_nodes.CrosswalkClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.run_crosswalk.side_effect = CrosswalkApiError("Job 42 failed")
            mock_client_cls.return_value = mock_client

            runner = CrosswalkRunner()
            with pytest.raises(RuntimeError, match="Error de API durante el crosswalk"):
                runner.run("in.csv", "cfg.json", "out.csv")


class TestCrosswalkNodes:
    """Verifica las funciones de nodo de LangGraph para crosswalk."""

    def test_map_source_to_generic(self, tmp_path):
        state = {
            "source_csv_path": str(tmp_path / "src.csv"),
            "source_crosswalk_config": str(tmp_path / "src_cfg.json"),
            "generic_source_csv_path": str(tmp_path / "gen_src.csv"),
        }

        with patch("core.nodes.crosswalk_nodes._runner.run") as mock_run:
            mock_run.return_value = "OK"
            res = map_source_to_generic(state)

            assert res == {}
            mock_run.assert_called_once_with(
                csv_input_path=state["source_csv_path"],
                config_json_path=state["source_crosswalk_config"],
                csv_output_path=state["generic_source_csv_path"],
            )

    def test_map_sedici_to_generic(self, tmp_path):
        state = {
            "repository_csv_path": str(tmp_path / "repo.csv"),
            "sedici_crosswalk_config": str(tmp_path / "sedici_cfg.json"),
            "generic_sedici_csv_path": str(tmp_path / "gen_sedici.csv"),
        }

        with patch("core.nodes.crosswalk_nodes._runner.run") as mock_run:
            mock_run.return_value = "OK"
            res = map_sedici_to_generic(state)

            assert res == {}
            mock_run.assert_called_once_with(
                csv_input_path=state["repository_csv_path"],
                config_json_path=state["sedici_crosswalk_config"],
                csv_output_path=state["generic_sedici_csv_path"],
            )

    def test_map_to_sedici_format(self, tmp_path):
        state = {
            "reconciled_csv_path": str(tmp_path / "reconciled.csv"),
            "sedici_target_crosswalk_config": str(tmp_path / "target_cfg.json"),
            "sedici_ready_csv_path": str(tmp_path / "sedici_ready.csv"),
        }

        with patch("core.nodes.crosswalk_nodes._runner.run") as mock_run:
            mock_run.return_value = "OK"
            res = map_to_sedici_format(state)

            assert res == {}
            mock_run.assert_called_once_with(
                csv_input_path=state["reconciled_csv_path"],
                config_json_path=state["sedici_target_crosswalk_config"],
                csv_output_path=state["sedici_ready_csv_path"],
            )

    @pytest.mark.asyncio
    async def test_bypass_source_crosswalk(self, tmp_path):
        curated_csv = tmp_path / "curated.csv"
        curated_csv.write_text("id,title\n1,Curado")
        target_csv = tmp_path / "generic_source.csv"

        state = {
            "curated_csv_path": str(curated_csv),
            "generic_source_csv_path": str(target_csv),
        }

        res = await bypass_source_crosswalk(state)
        assert res == {}
        assert os.path.isfile(target_csv)
        assert target_csv.read_text() == "id,title\n1,Curado"

    def test_generate_source_crosswalk_config_existing_cache(self, tmp_path):
        cfg_file = tmp_path / "crosswalk_config_springer.json"
        cfg_file.write_text("[]")
        src_csv = tmp_path / "source.csv"
        src_csv.write_text("Title,Authors\nArticulo,Juan")

        state = {
            "source_name": "springer",
            "source_csv_path": str(src_csv),
            "workspace_dir": str(tmp_path),
        }

        with patch.object(
            SourceToGenericCrosswalkGenerator,
            "get_existing_config",
            return_value={"source_crosswalk_config": str(cfg_file)},
        ):
            res = generate_source_crosswalk_config(state)
            assert res["source_crosswalk_config"] == str(cfg_file)

"""
Tests unitarios para el nodo SetupWorkspace (core/nodes/setup_node.py).
"""

import os
from datetime import datetime
from unittest.mock import patch
import pytest

from core.nodes.setup_node import setup_workspace, _build_workspace_dir


class TestSetupWorkspaceNode:
    """Verifica la inicialización de workspace y valores por defecto."""

    def test_setup_workspace_with_explicit_dir(self, tmp_path):
        """Si workspace_dir es provisto, debe crearlo y mapear todos los paths relativos a él."""
        custom_dir = str(tmp_path / "custom_lot_123")
        state = {
            "source_name": "mi_fuente",
            "workspace_dir": custom_dir,
        }

        updates = setup_workspace(state)

        assert os.path.isdir(custom_dir)
        assert updates["workspace_dir"] == custom_dir
        assert updates["generic_source_csv_path"] == os.path.join(custom_dir, "generic_source.csv")
        assert updates["generic_sedici_csv_path"] == os.path.join(custom_dir, "generic_sedici.csv")
        assert updates["dedup_output_csv_path"] == os.path.join(custom_dir, "dedup.csv")
        assert updates["reconciled_csv_path"] == os.path.join(custom_dir, "reconciled.csv")
        assert updates["sedici_ready_csv_path"] == os.path.join(custom_dir, "sedici_ready.csv")
        assert updates["saf_output_path"] == os.path.join(custom_dir, "saf_output")
        assert updates["import_mapfile_path"] == os.path.join(custom_dir, "mapfile.txt")
        assert updates["umbral_seguro"] == 10
        assert updates["umbral_revision"] == 30
        assert updates["sedici_crosswalk_config"].endswith(".json")
        assert updates["sedici_target_crosswalk_config"].endswith(".json")

    def test_setup_workspace_generates_default_dir(self, tmp_path, monkeypatch):
        """Si workspace_dir no se provee, debe crearse bajo runs/{source}_{fecha}_1."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr("os.path.abspath", lambda p: str(runs_dir) if p == "runs" else p)

        fecha_esperada = datetime.now().strftime("%Y%m%d")
        state = {"source_name": "scopus"}

        updates = setup_workspace(state)

        expected_dir = str(runs_dir / f"scopus_{fecha_esperada}_1")
        assert updates["workspace_dir"] == expected_dir
        assert os.path.isdir(expected_dir)

    def test_setup_workspace_increments_run_number(self, tmp_path, monkeypatch):
        """Verifica que incremente el sufijo si ya existen carpetas previas del mismo día."""
        runs_dir = tmp_path / "runs"
        fecha_actual = datetime.now().strftime("%Y%m%d")
        os.makedirs(runs_dir / f"springer_{fecha_actual}_1", exist_ok=True)
        os.makedirs(runs_dir / f"springer_{fecha_actual}_2", exist_ok=True)

        monkeypatch.setattr("os.path.abspath", lambda p: str(runs_dir) if p == "runs" else p)

        state = {"source_name": "springer"}
        updates = setup_workspace(state)

        expected_dir = str(runs_dir / f"springer_{fecha_actual}_3")
        assert updates["workspace_dir"] == expected_dir
        assert os.path.isdir(expected_dir)

    def test_setup_workspace_preserves_overrides(self, tmp_path):
        """Verifica que no sobreescriba configuraciones personalizadas provistas en el state."""
        custom_csv = str(tmp_path / "my_custom_generic.csv")
        state = {
            "source_name": "custom",
            "workspace_dir": str(tmp_path),
            "generic_source_csv_path": custom_csv,
            "umbral_seguro": 18,
            "umbral_revision": 45,
            "sedici_crosswalk_config": "/opt/custom_sedici.json",
        }

        updates = setup_workspace(state)

        assert updates["generic_source_csv_path"] == custom_csv
        assert updates["umbral_seguro"] == 18
        assert updates["umbral_revision"] == 45
        assert updates["sedici_crosswalk_config"] == "/opt/custom_sedici.json"

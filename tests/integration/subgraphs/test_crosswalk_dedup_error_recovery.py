"""
Tests de integración para el circuito de recuperación reactiva y memoria episódica
en CrosswalkDedupSubgraph.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from core.subgraphs.crosswalk_dedup import (
    build_crosswalk_dedup_subgraph,
    route_post_deduplicate,
    route_post_recovery,
)
from core.clients.deduplicator_client import DeduplicatorApiError
from core.agent.dedup_recovery.agent import RecoveryDecision


@pytest.fixture
def recovery_workspace(tmp_path: Path):
    """Genera los archivos y CSVs mínimos necesarios para ejecutar el subgrafo."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    generic_source = ws / "generic_source.csv"
    generic_sedici = ws / "generic_sedici.csv"
    dedup_output = ws / "dedup_output.csv"
    reconciled = ws / "reconciled.csv"
    source_csv = ws / "source.csv"

    # CSV fuente con fecha que provocará el fallo
    source_data = {
        "id": ["14531"],
        "title": ["Prueba de Recuperacion Reactiva"],
        "date": ["1999, June"],
        "author": ["Romero, A."],
    }
    pd.DataFrame(source_data).to_csv(generic_source, index=False)
    pd.DataFrame(source_data).to_csv(source_csv, index=False)

    sedici_data = {
        "id": ["10915"],
        "title": ["Prueba en SEDICI"],
        "date": ["1999"],
        "author": ["Romero, A."],
    }
    pd.DataFrame(sedici_data).to_csv(generic_sedici, index=False)

    return {
        "workspace_dir": str(ws),
        "source_name": "unlp_doaj",
        "input_source_type": "pdf_minio",  # Para usar bypass y no requerir crosswalk config real
        "source_csv_path": str(source_csv),
        "generic_source_csv_path": str(generic_source),
        "repository_csv_path": str(generic_sedici),
        "generic_sedici_csv_path": str(generic_sedici),
        "sedici_crosswalk_config": "",
        "dedup_output_csv_path": str(dedup_output),
        "reconciled_csv_path": str(reconciled),
        "enrichment_enabled": False,
        "umbral_seguro": 10,
        "applied_corrections": [],
        "node_errors": {},
        "dedup_retry_count": 0,
    }


def test_routing_logic():
    """Verifica las funciones de enrutamiento condicional post-deduplicate y post-recovery."""
    # 1. Caso sin error
    assert route_post_deduplicate({"node_errors": {}}) == "MetadataReconciliation"

    # 2. Caso con error en intento 0 (debe ir a recuperación)
    state_err_0 = {
        "node_errors": {"Deduplicate": "invalid literal for int()"},
        "dedup_retry_count": 0,
    }
    assert route_post_deduplicate(state_err_0) == "DedupRecoveryNode"

    # 3. Caso con error en intento 1 (límite superado, debe finalizar)
    state_err_1 = {
        "node_errors": {"Deduplicate": "invalid literal for int()"},
        "dedup_retry_count": 1,
    }
    assert route_post_deduplicate(state_err_1) == "__end__"

    # 4. Caso post-recuperación exitosa (error resuelto -> reintenta Deduplicate)
    state_recovered = {"node_errors": {}, "pipeline_status": "running"}
    assert route_post_recovery(state_recovered) == "Deduplicate"

    # 5. Caso post-recuperación fallida/abortada
    state_aborted = {"pipeline_status": "failed"}
    assert route_post_recovery(state_aborted) == "__end__"


@pytest.mark.asyncio
async def test_crosswalk_dedup_recovery_integration(recovery_workspace: dict):
    """
    Test de integración completo:
      1. MapSediciToGeneric / BypassSourceCrosswalk preparan los CSVs.
      2. Deduplicate falla en intento 0 con DeduplicatorApiError.
      3. DedupRecoveryNode se activa, consulta la memoria episódica y repara generic_source.csv.
      4. Deduplicate reintenta (intento 1) y tiene éxito.
      5. MetadataReconciliation finaliza con el campo date normalizado a '1999-06'.
    """
    mock_subgraph = await build_crosswalk_dedup_subgraph()

    call_count = 0
    fake_dedup_csv_bytes = b"id_document1,id_document2,similarity\n10915,14531,0.05\n"

    def mock_detect_duplicates(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise DeduplicatorApiError(
                "El proceso de deduplicación falló en el servidor: "
                "Error al comparar fechas: 1999, June — invalid literal for int() with base 10: 'June'"
            )
        return fake_dedup_csv_bytes

    # Mock del LLM del agente de recuperación devolviendo decisión estructurada
    mock_decision = RecoveryDecision(
        diagnosis="Mes textual 'June' en columna date impide cálculo numérico.",
        confidence="high",
        is_ambiguous=False,
        strategy="value_mapping",
        target_column="date",
        mapping_rules={"June": "06"},
        reasoning="Normalización mediante mapeo de mes a formato de dos dígitos.",
    )

    with patch("core.nodes.dedup_node.DeduplicatorClient") as MockClient, \
         patch("core.agent.dedup_recovery.agent.invoke_recovery_agent", return_value=mock_decision), \
         patch("core.nodes.crosswalk_nodes._runner.run", return_value="ok"):

        client_inst = MockClient.return_value
        client_inst.detect_duplicates.side_effect = mock_detect_duplicates

        final_state = await mock_subgraph.ainvoke(recovery_workspace)

        # 1. Verificar que Deduplicate se ejecutó 2 veces (falla inicial + 1 reintento)
        assert call_count == 2

        # 2. Verificar que el reintento quedó registrado
        assert final_state.get("dedup_retry_count") == 1

        # 3. Verificar que generic_source.csv fue reparado en disco
        df_generic = pd.read_csv(final_state["generic_source_csv_path"], dtype=str)
        assert "1999, 06" in df_generic["date"].iloc[0] or "1999-06" in df_generic["date"].iloc[0]

        # 4. Verificar que las correcciones se propagaron al reconciliado
        reconciled_path = final_state["reconciled_csv_path"]
        assert os.path.isfile(reconciled_path)
        df_rec = pd.read_csv(reconciled_path, dtype=str)
        assert len(df_rec) == 1
        assert "1999, 06" in df_rec["date"].iloc[0] or "1999-06" in df_rec["date"].iloc[0]

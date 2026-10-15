"""
Evaluación de Falsos Positivos y Sobre-Curación (Over-Correction Rate).

Módulo: tests/evals/agents/curator/test_curator_over_correction.py
Suite de LangSmith: Metadata_Curator_Heuristic_OverCorrection

Evalúa la tasa de sobre-curación sobre un conjunto de metadatos limpios y normalizados
(curator_clean_baseline.json). Un pipeline conservador debe:
  1. Clasificar el 100% de los registros limpios como válidos en la Capa 1 heurística.
  2. No invocar al agente LLM de forma redundante (0 tokens consumidos).
  3. No alterar ningún campo original ni enviar ítems a cuarentena.
  4. Garantizar over_correction_rate == 0.0%.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from langsmith import testing as t

from core.nodes.curation_nodes import (
    CurationOrchestrator,
    _calcular_anomalias_restantes,
    curate_metadata_node,
)
from core.state import State
from core.utils.config import config
from core.utils.heuristic_detectors import (
    analizar_fila,
    calcular_estadisticas_lote,
)
from core.utils.text_fixers import aplicar_correctores_programaticos

BASELINE_DATASET_PATH = (
    Path(__file__).parent.parent.parent / "datasets" / "curator" / "curator_clean_baseline.json"
)


def _cargar_registros_limpios_de_control() -> list[dict[str, Any]]:
    """
    Carga registros de metadatos limpios y normalizados para la prueba de falsos positivos.
    """
    if BASELINE_DATASET_PATH.exists():
        with open(BASELINE_DATASET_PATH, "r", encoding="utf-8") as f:
            cases = json.load(f)
        registros = []
        for c in cases:
            meta = dict(c.get("metadata", {}))
            meta["id"] = f"{c.get('id', 'item')}.pdf"
            registros.append(meta)
        return registros
    else:
        raise FileNotFoundError(
            f"No se encontró el dataset de baseline limpio en: {BASELINE_DATASET_PATH}"
        )


@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.langsmith(test_suite_name="Metadata_Curator_Heuristic_OverCorrection")
async def test_curator_zero_over_correction(tmp_path: Path):
    """
    Evalúa la tasa de sobre-curación (over-correction rate) sobre metadatos limpios.

    Un lote de control limpio:
      1. Debe ser clasificado íntegramente como 'limpio' en la Capa 1 heurística.
      2. No debe invocar al agente LLM de forma redundante.
      3. No debe alterar ningún campo original ni enviar ítems a cuarentena.
      4. over_correction_rate == 0.0%.
    """
    clean_records = _cargar_registros_limpios_de_control()
    assert len(clean_records) >= 2, "El conjunto de control debe tener registros limpios."

    t.log_inputs({
        "dataset": "curator_clean_baseline.json",
        "total_records": len(clean_records),
    })
    t.log_reference_outputs({
        "expected_over_correction_rate": 0.0,
        "expected_suspicious": 0,
        "expected_curated_by_agent": 0,
    })

    # Guardar registros de control en un CSV temporal
    csv_control_path = tmp_path / "clean_control_metadata.csv"
    df_clean = pd.DataFrame(clean_records)
    df_clean.to_csv(csv_control_path, index=False)

    campos_analisis = [
        c for c in clean_records[0].keys()
        if c not in ("id", "_curation", "_corrections_applied")
    ]
    stats_lote = calcular_estadisticas_lote(clean_records, campos_analisis)

    # 1. Capa 1: Detectores heurísticos
    sospechosos_capa1 = []
    for fila in clean_records:
        anomalias = analizar_fila(fila, stats_lote)
        if anomalias:
            sospechosos_capa1.append({"fila": fila, "anomalias": anomalias})

    # 2. Capa 2a: Correctores programáticos
    van_al_agente = []
    for item in sospechosos_capa1:
        fila_corregida, _ = aplicar_correctores_programaticos(
            item["fila"], item["anomalias"]
        )
        anomalias_restantes = _calcular_anomalias_restantes(
            fila_corregida, item["anomalias"], stats_lote
        )
        if anomalias_restantes:
            van_al_agente.append(item)

    # 3. Ejecutar nodo de curación sobre el CSV completo
    state: State = {
        "source_csv_path": str(csv_control_path),
        "workspace_dir": str(tmp_path),
        "current_step": "curate_metadata",
    }

    nuevo_state = await curate_metadata_node(state)

    output_csv_path = nuevo_state.get("curated_csv_path", "")
    df_resultado = pd.read_csv(output_csv_path)

    # Verificar ítems en cuarentena
    items_cuarentena = 0
    if "curation_needed" in df_resultado.columns:
        items_cuarentena = int(df_resultado["curation_needed"].fillna(False).sum())

    total = len(clean_records)
    sobre_curados = len(van_al_agente) + items_cuarentena
    over_correction_rate = sobre_curados / total if total > 0 else 0.0

    # Registrar outputs y feedback en LangSmith
    t.log_outputs({
        "total_records": total,
        "suspicious_detected": len(sospechosos_capa1),
        "routed_to_agent": len(van_al_agente),
        "quarantined_items": items_cuarentena,
        "over_correction_rate": over_correction_rate,
    })
    t.log_feedback(key="over_correction_rate", score=over_correction_rate)
    t.log_feedback(key="zero_over_correction_pass", score=1.0 if over_correction_rate == 0.0 else 0.0)

    # Aserciones deterministas de control
    assert len(sospechosos_capa1) == 0, (
        f"Detectores heurísticos generaron {len(sospechosos_capa1)} falsos positivos en registros limpios: "
        f"{[s['anomalias'] for s in sospechosos_capa1]}"
    )
    assert len(van_al_agente) == 0, (
        f"Se enviaron indebidamente {len(van_al_agente)} filas limpias al agente LLM."
    )
    assert items_cuarentena == 0, (
        f"Se marcaron {items_cuarentena} ítems limpios para cuarentena indebida."
    )
    assert over_correction_rate == 0.0, (
        f"Over-correction rate esperado: 0.0%, obtenido: {over_correction_rate:.2%}"
    )

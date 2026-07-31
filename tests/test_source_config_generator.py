import json
import os
import sys
import tempfile
import pytest
from langgraph_sdk import get_client
from langsmith import Client, evaluate
from langsmith.evaluation import EvaluationResult

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MCP_SRC_PATH = os.path.join(PROJECT_ROOT, "MCPs", "Deduplicator MCP", "src")
CORE_MODULE_PATH = os.path.join(PROJECT_ROOT, "core")
CROSSWALK_MODULE_PATH = os.path.join(PROJECT_ROOT, "core", "scripts", "crosswalk")
CONFIGS_DIR = os.path.join(CROSSWALK_MODULE_PATH, "configs")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# The crosswalk.py module uses flat imports (from crosswalk_context import *),
# so its directory must be in sys.path.
for _path in (PROJECT_ROOT, MCP_SRC_PATH, CORE_MODULE_PATH, CROSSWALK_MODULE_PATH):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.graph import generate_source_crosswalk_config

SOURCE_NAME = "springer"
SOURCE_DATA_PATH = "./data/"
SOURCE_CSV_NAME = "SearchResults.csv"


# 1. Wrapper de la función objetivo para el
def predict_crosswalk(inputs: dict) -> dict:
    """
    Envuelve la función del nodo para inyectar datos del dataset de LangSmith
    y devolver el contenido real del JSON para poder evaluarlo.
    """
    # Usamos un directorio temporal para no sobreescribir archivos reales
    with tempfile.TemporaryDirectory() as temp_dir:

        real_csv_path = inputs["source_csv_path"]

        filename = os.path.basename(real_csv_path)
        test_csv_path = os.path.join(temp_dir, filename)

        import shutil
        shutil.copy(real_csv_path, test_csv_path)

        state = {
            "source_csv_path": test_csv_path,
            "source_name": inputs.get("source_name", "test_source")
        }

        # Ejecutar el nodo de LangGraph
        result_state = generate_source_crosswalk_config(state)
        
        # Leer el resultado generado para que el evaluador lo analice
        config_path = result_state["source_crosswalk_config"]
        
        # Si cayó en el fallback, devolvemos eso
        if not os.path.exists(config_path):
            return {"error": "Archivo no generado"}

        with open(config_path, "r", encoding="utf-8") as f:
            generated_json = json.load(f)
            
        return {"config_data": generated_json}

# 2. Definición de Evaluadores Heurísticos
def valid_json_structure_evaluator(run, example) -> EvaluationResult:
    """Evalúa si la estructura devuelta tiene el formato esperado [mappings, config]"""
    output = run.outputs.get("config_data")
    
    if not output or not isinstance(output, list) or len(output) != 2:
        return EvaluationResult(key="valid_structure", score=0.0)
    
    mappings, config = output
    score = 1.0 if isinstance(mappings, list) and isinstance(config, dict) else 0.0
    return EvaluationResult(key="valid_structure", score=score)

def critical_columns_mapped_evaluator(run, example) -> EvaluationResult:
    """Evalúa si el LLM logró mapear las columnas obligatorias definidas en el dataset"""
    output = run.outputs.get("config_data")
    if not output:
        return EvaluationResult(key="critical_columns_mapped", score=0.0)

    generated_mappings = output[0]
    expected_core_columns = example.outputs.get("expected_core_columns", [])

    mapped_targets = [m.get("replace") for m in generated_mappings if "replace" in m]
    hits = sum(1 for col in expected_core_columns if col in mapped_targets)
    score = hits / len(expected_core_columns) if expected_core_columns else 1.0

    return EvaluationResult(key="critical_columns_mapped", score=score)


def separators_evaluator(run, example) -> EvaluationResult:
    """
    Evaluates that the separator configuration in the generated config matches
    the expected values from the dataset.

    Supports two modes depending on what the example outputs declare:
      - If `expected_separator_regex` is set, checks that the config contains
        a `separator_regex` field with a valid, non-empty regex pattern.
      - Otherwise, falls back to checking `expected_original_separator` (literal).

    Always checks `file_delimiter` and `replace_separator` when expected.
    """
    import re as _re

    output = run.outputs.get("config_data")
    if not output:
        return EvaluationResult(key="separators", score=0.0)

    generated_config = output[1]
    checks = []

    expected_file_delimiter = example.outputs.get("expected_file_delimiter")
    if expected_file_delimiter is not None:
        match = generated_config.get("file_delimiter") == expected_file_delimiter
        print(f"file_delimiter match: {match}")
        checks.append(match)

    expected_replace_separator = example.outputs.get("expected_replace_separator")
    if expected_replace_separator is not None:
        match = generated_config.get("replace_separator") == expected_replace_separator
        print(f"replace_separator match: {match}")
        checks.append(match)

    # --- Separator mode: regex vs literal ---
    expected_separator_regex = example.outputs.get("expected_separator_regex")
    if expected_separator_regex:
        # Expect the config to contain a non-empty separator_regex field.
        # We do NOT require an exact string match (the LLM may generate a
        # semantically equivalent but textually different pattern), so we
        # only check that a regex is present and syntactically valid.
        actual_regex = generated_config.get("separator_regex", "")
        has_regex = bool(actual_regex)
        try:
            _re.compile(actual_regex) if actual_regex else None
            valid_syntax = True
        except _re.error:
            valid_syntax = False
        print(f"separator_regex present: {has_regex}, valid syntax: {valid_syntax}")
        checks.append(has_regex and valid_syntax)
    else:
        expected_original_separator = example.outputs.get("expected_original_separator")
        if expected_original_separator is not None:
            match = generated_config.get("original_separator") == expected_original_separator
            print(f"original_separator match: {match}")
            checks.append(match)

    score = sum(checks) / len(checks) if checks else 1.0
    return EvaluationResult(key="separators", score=score)


def regex_splits_correctly_evaluator(run, example) -> EvaluationResult:
    """
    Evaluates whether the generated `separator_regex` actually splits the author
    field into multiple clean tokens on the real test CSV data.

    Only runs when `expected_separator_regex` and `author_column` are declared in
    the example outputs. Reads up to 20 rows from the source CSV, applies
    re.split() with the generated regex, and checks that ≥ 60% of non-empty
    author rows yield more than one token.
    """
    import re as _re
    import csv as _csv

    output = run.outputs.get("config_data")
    if not output:
        return EvaluationResult(key="regex_splits_correctly", score=0.0)

    # Only run this evaluator when the example declares a regex is expected
    if not example.outputs.get("expected_separator_regex"):
        return EvaluationResult(key="regex_splits_correctly", score=1.0, comment="Not applicable (no regex expected)")

    generated_config = output[1]
    actual_regex = generated_config.get("separator_regex", "")
    if not actual_regex:
        return EvaluationResult(key="regex_splits_correctly", score=0.0, comment="No separator_regex in generated config")

    try:
        compiled = _re.compile(actual_regex)
    except _re.error as exc:
        return EvaluationResult(key="regex_splits_correctly", score=0.0, comment=f"Invalid regex: {exc}")

    # Read sample rows from the real CSV
    source_csv = example.inputs.get("source_csv_path", "")
    author_column = example.outputs.get("author_column", "")

    if not source_csv or not os.path.exists(source_csv):
        return EvaluationResult(key="regex_splits_correctly", score=0.0, comment="Source CSV not accessible")

    try:
        with open(source_csv, "r", encoding="utf-8") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = _csv.Sniffer().sniff(sample)
            except _csv.Error:
                dialect = _csv.excel()
            reader = _csv.DictReader(fh, delimiter=dialect.delimiter)
            rows = [dict(r) for i, r in enumerate(reader) if i < 20]
    except Exception as exc:
        return EvaluationResult(key="regex_splits_correctly", score=0.0, comment=f"Could not read CSV: {exc}")

    if not rows:
        return EvaluationResult(key="regex_splits_correctly", score=0.0, comment="Empty CSV")

    # Case-insensitive column lookup
    matched_col = None
    for col in rows[0].keys():
        if col == author_column or col.lower() == author_column.lower():
            matched_col = col
            break

    if not matched_col:
        available = list(rows[0].keys())
        return EvaluationResult(
            key="regex_splits_correctly",
            score=0.0,
            comment=f"Author column '{author_column}' not found. Available: {available}"
        )

    non_empty = [r[matched_col].strip() for r in rows if r.get(matched_col, "").strip()]
    if not non_empty:
        return EvaluationResult(key="regex_splits_correctly", score=0.0, comment="No non-empty author values in sample")

    multi_token_rows = sum(
        1 for val in non_empty
        if len([t for t in compiled.split(val) if t and t.strip()]) > 1
    )
    ratio = multi_token_rows / len(non_empty)
    print(f"regex_splits_correctly: {multi_token_rows}/{len(non_empty)} rows split into >1 token (ratio={ratio:.2f})")
    # Score 1.0 if ≥60% of rows split into multiple tokens
    score = 1.0 if ratio >= 0.6 else round(ratio, 2)
    return EvaluationResult(
        key="regex_splits_correctly",
        score=score,
        comment=f"{multi_token_rows}/{len(non_empty)} author rows split into >1 token"
    )

# 3. Script para ejecutar el pipeline de evaluación
def run_evaluation():
    import pandas as pd

    client = Client()
    dataset_name = "Crosswalk_Module_Tests"

    if not client.has_dataset(dataset_name=dataset_name):
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="Casos de prueba para el agente de generación de crosswalk configs."
        )
        print(f"Dataset '{dataset_name}' creado con éxito.")
    else:
        dataset = client.read_dataset(dataset_name=dataset_name)
        print(f"El dataset '{dataset_name}' ya existe. Añadiendo nuevos ejemplos...")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    data_folder = os.path.join(current_dir, "data") 
    csv_filename = "SearchResults.csv"
    csv_path = os.path.join(data_folder, csv_filename)
    
    if not os.path.exists(csv_path):
        print(f"Error: No se encontró el archivo en {csv_path}")
        return

    # 4. Inspeccionar el CSV localmente para extraer la "verdad de campo" (Outputs esperados)
    # Leemos solo la cabecera para saber qué columnas tiene y definir qué esperamos que mapee el LLM
    df_sample = pd.read_csv(csv_path, nrows=1)
    original_columns = list(df_sample.columns)
    print(f"Columnas detectadas en el archivo local: {original_columns}")

    # NOTE: the key MUST match what predict_crosswalk reads at L23 ('source_csv_path').
    inputs={
        "source_csv_path": csv_path,
        "source_name": "springer"
    }

    # Only columns that can be derived from this Springer export:
    #   Item Title       -> title
    #   Authors          -> author
    #   Publication Year -> date
    #   Item DOI         -> doi
    #   Publication Title -> citation
    #   Content Type     -> type
    # Missing in source: description, subject, isbn, subtitle, issn
    #
    # NOTE on separators:
    # Springer authors are concatenated without a conventional delimiter:
    #   "G. AadE. AakvaagB. AbbottS. Abdelhameed"
    # The agent is expected to detect this and produce a `separator_regex`
    # instead of a plain `original_separator`. We therefore declare
    # `expected_separator_regex=True` (presence check) rather than an exact
    # string match, and provide `author_column` so the regex evaluator can
    # verify the pattern against the real data.
    outputs={
        "original_columns": original_columns,
        "expected_core_columns": ["id", "title", "author", "date", "doi", "citation", "type"],
        "expected_file_delimiter": ",",
        "expected_separator_regex": True,
        "author_column": "Authors",
        "expected_replace_separator": "|"
    }

    # Crear el dataset con los ejemplos
    client.create_example(
        inputs=inputs,
        outputs=outputs,
        dataset_id=dataset.id
    )

    # Ejecutar la evaluación masiva
    evaluate(
        predict_crosswalk,
        data=dataset_name,
        evaluators=[
            valid_json_structure_evaluator,
            critical_columns_mapped_evaluator,
            separators_evaluator,
            regex_splits_correctly_evaluator,
        ],
        experiment_prefix="crosswalk-agent-iter1",
    )

if __name__ == "__main__":
    run_evaluation()
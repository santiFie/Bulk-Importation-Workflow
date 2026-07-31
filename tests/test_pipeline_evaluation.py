"""
Test de evaluación del pipeline completo con trazabilidad en LangGraph Platform.

Propósito:
    Ejecuta el pipeline de importación a SEDICI (o una porción de él) sobre
    uno o varios CSVs de prueba, registra los resultados como un experimento
    en LangSmith (sección "Datasets & Experiments") y aplica evaluadores
    heurísticos por cada paso del pipeline.

Características:
    - source_csv configurable (soporta varios CSVs para pruebas sucesivas).
    - Ejecución hasta un paso determinado (STOP_AFTER_STEP).
    - Métricas por paso: existencia de archivos, conteo de filas, columnas
      esperadas, errores capturados.
    - Visualización clara de errores en LangSmith y en consola.

Modo de uso:
    Desde la raíz del proyecto, con el .venv activado:

        python tests/test_pipeline_evaluation.py

    Variables de entorno requeridas:
        LANGCHAIN_API_KEY  — clave de LangSmith
        LANGCHAIN_TRACING_V2=true

    Para ejecutar hasta un paso específico, editar STOP_AFTER_STEP más abajo.
"""

import csv
import io
import json
import os
import subprocess
import sys
import shutil
import tempfile
import types

from langsmith import Client, evaluate
from langsmith.evaluation import EvaluationResult

# ---------------------------------------------------------------------------
# Paths y sys.path
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CROSSWALK_MODULE_PATH = os.path.join(PROJECT_ROOT, "core", "scripts", "crosswalk")
CONFIGS_DIR = os.path.join(CROSSWALK_MODULE_PATH, "configs")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

for _path in (PROJECT_ROOT, CROSSWALK_MODULE_PATH):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# ---------------------------------------------------------------------------
# Mock del DeduplicatorClient (evita dependencia de red)
# ---------------------------------------------------------------------------


class _FakeDeduplicatorClient:
    """Genera CSV de deduplicación sintético con total=0 (sin duplicados)."""

    def __init__(self, source_csv_path: str = ""):
        self._source_csv_path = source_csv_path

    def detect_duplicates(self, csv_file1_path, csv_file2_path, source_name):
        try:
            with open(csv_file2_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            rows = []

        output = io.StringIO()
        writer = csv.DictWriter(
            output, fieldnames=["id", "title", "match_id", "match_title", "total"]
        )
        writer.writeheader()
        for i, row in enumerate(rows):
            writer.writerow({
                "id": row.get("id", str(i)),
                "title": row.get("title", ""),
                "match_id": "",
                "match_title": "",
                "total": 0,
            })
        return output.getvalue().encode("utf-8")


_fake_mod = types.ModuleType("deduplicator_client")
_fake_mod.DeduplicatorClient = _FakeDeduplicatorClient  # type: ignore
sys.modules["deduplicator_client"] = _fake_mod

# Mock de shutil.copy para evitar FileNotFoundError con PDFs
_original_copy = shutil.copy
_original_copyfile = shutil.copyfile
shutil.copy = lambda src, dst, **kw: None  # type: ignore
shutil.copyfile = lambda src, dst, **kw: None  # type: ignore

from core.graph import (  # noqa: E402
    run_pipeline_until_step,
    get_step_node_names,
    get_step_label,
    PIPELINE_STEPS,
)
from core.utils.config import config  # noqa: E402

# Restaurar shutil después de importar
shutil.copy = _original_copy
shutil.copyfile = _original_copyfile


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN — EDITAR ESTAS CONSTANTES SEGÚN EL CASO DE PRUEBA
# ═══════════════════════════════════════════════════════════════════════════

# Lista de CSVs a evaluar. Cada entrada es un dict con los datos del caso.
# Agregar más entradas para realizar pruebas sucesivas con distintos CSVs.
TEST_CASES: list[dict] = [
    {
        "source_csv_path": os.path.join(DATA_DIR, "SearchResults.csv"),
        "source_name": "springer",
        "repository_csv_path": os.path.join(DATA_DIR, "export_10915_all.csv"),
        # Columnas esperadas en el CSV genérico (para métricas del Paso 2a)
        "expected_generic_columns": ["title", "author", "date", "doi"],
        # Columnas esperadas en el CSV SEDICI-ready (para métricas del Paso 5)
        "expected_sedici_columns": ["dc.title[es]", "sedici.creator.person[es]"],
        # Cantidad exacta de autores esperados por fila tras la separación multivalor.
        # Cada valor corresponde a una fila del CSV genérico (en orden).
        "expected_author_counts": [6, 6, 6],
    },
]

# Paso en el que se detiene la ejecución (inclusive).
# Valores válidos: ver PIPELINE_STEPS en core/graph.py
# Ejemplos: "GenerateSourceCrosswalkConfig", "Deduplicate", "MetadataCorrections"
STOP_AFTER_STEP = "MetadataCorrections"

# ---------------------------------------------------------------------------
# Helpers para metadata del experimento
# ---------------------------------------------------------------------------

def _git_hash(relative_path: str) -> str:
    """
    Devuelve el hash corto del último commit que modificó el archivo indicado.
    Si el archivo tiene cambios sin commitear devuelve "uncommitted".
    Útil para registrar con precisión qué versión del prompt se usó.
    """
    try:
        # Verificar si hay cambios sin commitear en el archivo
        dirty = subprocess.run(
            ["git", "status", "--porcelain", relative_path],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        ).stdout.strip()
        if dirty:
            return "uncommitted"
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", relative_path],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        return result.stdout.strip() or "no-commits"
    except Exception:
        return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DEL EXPERIMENTO — editar antes de cada corrida
# ═══════════════════════════════════════════════════════════════════════════

# Nombre del dataset y prefijo del experimento en LangSmith
DATASET_NAME      = "Pipeline_Integration_Tests"
EXPERIMENT_PREFIX = "pipeline-eval"

# Metadata adjunta al experimento en LangSmith.
# Aparece en la cabecera de cada corrida y permite comparar experimentos
# con distintos modelos o prompts en la tabla de la UI.
# Actualizar 'notes' con una descripción breve del cambio antes de cada corrida.
EXPERIMENT_METADATA: dict = {
    # Modelo que usa el nodo de crosswalk (leído directamente de config.py)
    "crosswalk_model": config.CROSSWALK_MODEL,
    # Hash git del .md del prompt — se calcula automáticamente
    "prompt_crosswalk_agent": _git_hash("agent_prompts/crosswalk_agent.md"),
    "prompt_separator_validator": _git_hash("agent_prompts/separator_validator.md"),
    # Observaciones libres sobre esta corrida (modificar antes de ejecutar)
    "notes": "",
}


# ═══════════════════════════════════════════════════════════════════════════
# Ejecución del pipeline
# ═══════════════════════════════════════════════════════════════════════════

def predict_pipeline(inputs: dict) -> dict:
    """
    Ejecuta el pipeline hasta STOP_AFTER_STEP y devuelve información
    detallada de cada paso para que los evaluadores la analicen.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Copiar CSV fuente a directorio temporal
        src_csv = inputs["source_csv_path"]
        tmp_csv = os.path.join(tmp_dir, os.path.basename(src_csv))
        import shutil as _shutil
        _shutil.copy(src_csv, tmp_csv)

        # Copiar CSV de repositorio SEDICI
        repo_csv = inputs.get("repository_csv_path", "")
        tmp_repo = ""
        if repo_csv and os.path.isfile(repo_csv):
            tmp_repo = os.path.join(tmp_dir, os.path.basename(repo_csv))
            _shutil.copy(repo_csv, tmp_repo)

        stop_after = inputs.get("stop_after_step", STOP_AFTER_STEP)

        # Armar estado inicial
        state = {
            "messages": [],
            "source_csv_path": tmp_csv,
            "source_name": inputs.get("source_name", "test_source"),
            "repository_csv_path": tmp_repo,
            "source_crosswalk_config": "",
            "sedici_crosswalk_config": os.path.join(CONFIGS_DIR, "sedicicrosswalkconfig.json"),
            "generic_source_csv_path": os.path.join(tmp_dir, "generic_source.csv"),
            "generic_sedici_csv_path": os.path.join(tmp_dir, "generic_sedici.csv"),
            "dedup_output_csv_path": os.path.join(tmp_dir, "dedup_output.csv"),
            "reconciled_csv_path": os.path.join(tmp_dir, "reconciled.csv"),
            "sedici_target_crosswalk_config": os.path.join(
                CONFIGS_DIR, "config_romero_to_sedici.json"
            ),
            "sedici_ready_csv_path": os.path.join(tmp_dir, "sedici_ready.csv"),
            "umbral_seguro": 10,
            "umbral_revision": 30,
            "saf_output_path": os.path.join(tmp_dir, "saf_output"),
            "dspace_collection": "test-collection",
            "import_mapfile_path": os.path.join(tmp_dir, "mapfile"),
            "import_validate_only": True,
        }

        # Mock shutil para SAF
        orig_copy = _shutil.copy
        orig_copyfile = _shutil.copyfile
        _shutil.copy = lambda s, d, **kw: None
        _shutil.copyfile = lambda s, d, **kw: None

        try:
            step_results = run_pipeline_until_step(state, stop_after)
        finally:
            _shutil.copy = orig_copy
            _shutil.copyfile = orig_copyfile

        # Recopilar información de archivos generados
        file_info = {}
        file_checks = {
            "source_crosswalk_config": state.get("source_crosswalk_config", ""),
            "generic_source_csv_path": state.get("generic_source_csv_path", ""),
            "generic_sedici_csv_path": state.get("generic_sedici_csv_path", ""),
            "dedup_output_csv_path": state.get("dedup_output_csv_path", ""),
            "reconciled_csv_path": state.get("reconciled_csv_path", ""),
            "sedici_ready_csv_path": state.get("sedici_ready_csv_path", ""),
        }

        for key, path in file_checks.items():
            if path and os.path.isfile(path):
                try:
                    with open(path, newline="", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        headers = list(reader.fieldnames or [])
                        rows = list(reader)
                    file_info[key] = {
                        "exists": True,
                        "row_count": len(rows),
                        "columns": headers,
                        "rows": rows[:10],
                    }
                except Exception:
                    # Puede ser JSON (config) u otro formato
                    file_info[key] = {
                        "exists": True,
                        "row_count": -1,
                        "columns": [],
                        "rows": [],
                    }
            else:
                file_info[key] = {
                    "exists": False,
                    "row_count": 0,
                    "columns": [],
                    "rows": [],
                }

        # Detectar errores por paso
        errors = {}
        for node_name, result in step_results.items():
            if "__error__" in result:
                errors[node_name] = result["__error__"]

        return {
            "step_results": step_results,
            "file_info": file_info,
            "errors": errors,
            "steps_executed": list(step_results.keys()),
            "stop_after": stop_after,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Evaluadores heurísticos
# ═══════════════════════════════════════════════════════════════════════════

def no_errors_evaluator(run, example) -> EvaluationResult:
    """Evalúa que ningún paso haya lanzado una excepción."""
    errors = run.outputs.get("errors", {})
    if errors:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
        return EvaluationResult(
            key="no_errors", score=0.0, comment=f"Errores: {detail}"
        )
    return EvaluationResult(key="no_errors", score=1.0)


def all_steps_executed_evaluator(run, example) -> EvaluationResult:
    """Evalúa que se hayan ejecutado todos los pasos esperados."""
    stop_after = run.outputs.get("stop_after", STOP_AFTER_STEP)
    executed = run.outputs.get("steps_executed", [])
    all_nodes = get_step_node_names()

    expected = []
    for n in all_nodes:
        expected.append(n)
        if n == stop_after:
            break

    if set(expected) == set(executed):
        return EvaluationResult(key="all_steps_executed", score=1.0)

    missing = set(expected) - set(executed)
    return EvaluationResult(
        key="all_steps_executed",
        score=len(executed) / len(expected) if expected else 0.0,
        comment=f"Pasos faltantes: {missing}",
    )


def files_generated_evaluator(run, example) -> EvaluationResult:
    """Evalúa que los archivos intermedios esperados se hayan generado."""
    file_info = run.outputs.get("file_info", {})
    executed = run.outputs.get("steps_executed", [])

    # Mapeo: si el paso fue ejecutado, qué archivo debería existir
    step_to_file = {
        "GenerateSourceCrosswalkConfig": "source_crosswalk_config",
        "MapSourceToGeneric": "generic_source_csv_path",
        "MapSediciToGeneric": "generic_sedici_csv_path",
        "Deduplicate": "dedup_output_csv_path",
        "MetadataReconciliation": "reconciled_csv_path",
        "MapToSediciFormat": "sedici_ready_csv_path",
        "MetadataCorrections": "sedici_ready_csv_path",
    }

    checks = []
    missing = []
    for step in executed:
        file_key = step_to_file.get(step)
        if file_key:
            info = file_info.get(file_key, {})
            exists = info.get("exists", False)
            checks.append(exists)
            if not exists:
                missing.append(f"{step} → {file_key}")

    score = sum(checks) / len(checks) if checks else 1.0
    comment = f"Archivos faltantes: {missing}" if missing else ""
    return EvaluationResult(key="files_generated", score=score, comment=comment)


def row_count_evaluator(run, example) -> EvaluationResult:
    """Evalúa que los CSVs generados tengan al menos 1 fila de datos."""
    file_info = run.outputs.get("file_info", {})
    checks = []
    empty_files = []

    for key, info in file_info.items():
        if info.get("exists") and info.get("row_count", 0) != -1:
            has_rows = info["row_count"] > 0
            checks.append(has_rows)
            if not has_rows:
                empty_files.append(key)

    score = sum(checks) / len(checks) if checks else 1.0
    comment = f"Archivos vacíos: {empty_files}" if empty_files else ""
    return EvaluationResult(key="row_count", score=score, comment=comment)


def generic_columns_evaluator(run, example) -> EvaluationResult:
    """Evalúa que el CSV genérico contenga las columnas esperadas."""
    file_info = run.outputs.get("file_info", {})
    expected = example.outputs.get("expected_generic_columns", [])

    if not expected:
        return EvaluationResult(
            key="generic_columns", score=1.0, comment="Sin columnas esperadas definidas"
        )

    generic_info = file_info.get("generic_source_csv_path", {})
    if not generic_info.get("exists"):
        return EvaluationResult(
            key="generic_columns", score=0.0, comment="CSV genérico no generado"
        )

    actual_cols = set(generic_info.get("columns", []))
    hits = sum(1 for col in expected if col in actual_cols)
    score = hits / len(expected)
    missing = [c for c in expected if c not in actual_cols]
    comment = f"Columnas faltantes: {missing}" if missing else ""
    return EvaluationResult(key="generic_columns", score=score, comment=comment)


def crosswalk_validation_evaluator(run, example) -> EvaluationResult:
    """
    Evalúa determinísticamente la calidad del crosswalk y la separación multivalor,
    replicando las reglas de `_validate_config_deterministic` de `helpers.py`.

    Verifica:
    1. Presencia de columnas críticas: ["id", "title", "author", "date"].
    2. Correcta separación de valores en campos multivalor ('author' y 'subject'):
       - Si el ejemplo define `expected_author_counts`, se verifica el conteo exacto
         de autores para cada fila correspondiente del CSV genérico.
       - Si no hay conteos esperados, se aplica una heurística general:
         detección de valores muy largos (>40 caracteres) sin separación (<3 partes).
    """
    executed = run.outputs.get("steps_executed", [])
    if "MapSourceToGeneric" not in executed:
        return EvaluationResult(
            key="crosswalk_validation",
            score=1.0,
            comment="Paso MapSourceToGeneric no ejecutado",
        )

    file_info = run.outputs.get("file_info", {})
    generic_info = file_info.get("generic_source_csv_path", {})

    if not generic_info.get("exists"):
        return EvaluationResult(
            key="crosswalk_validation",
            score=0.0,
            comment="CSV genérico no generado",
        )

    cols = generic_info.get("columns", [])
    rows = generic_info.get("rows", [])

    # 1. Columnas críticas definidas en _validate_config_deterministic
    CRITICAL = ["id", "title", "author", "date"]
    missing = [c for c in CRITICAL if c not in cols]

    # 2. Validación de separador en author y subject.
    # Los conteos exactos se leen del ejemplo para que sea configurable por CSV.
    expected_author_counts: list[int] = example.outputs.get("expected_author_counts", [])
    separator_ok = True
    separation_issues = []

    for field in ["author", "subject"]:
        if field not in cols:
            continue
        for i, row in enumerate(rows[:len(expected_author_counts) or 3]):
            raw_val = row.get(field, "") or ""
            values = [v.strip() for v in raw_val.split("|") if v.strip()]
            count = len(values)

            if field == "author" and i < len(expected_author_counts):
                # Validación determinista: conteo exacto definido en el caso de prueba
                expected = expected_author_counts[i]
                if count != expected:
                    separator_ok = False
                    separation_issues.append(
                        f"Fila {i+1} ({field}): se esperaban {expected} valores, pero hay {count}."
                    )
            else:
                # Heurística general para strings largos sin separar
                long_values = [v for v in values if len(v) > 40]
                if long_values and count < 3:
                    separator_ok = False
                    separation_issues.append(
                        f"Fila {i+1} ({field}): posible falla, valor muy largo sin separar ('{long_values[0][:30]}...')."
                    )

    critical_score = (len(CRITICAL) - len(missing)) / len(CRITICAL) if CRITICAL else 1.0
    sep_score = 1.0 if separator_ok else 0.0
    score = (critical_score + sep_score) / 2.0

    comments = []
    if missing:
        comments.append(f"Faltan columnas críticas: {missing}")
    if separation_issues:
        comments.append(f"Fallas de separación: {'; '.join(separation_issues)}")
    if score == 1.0:
        comments.append("Validación OK")

    return EvaluationResult(
        key="crosswalk_validation",
        score=score,
        comment=" | ".join(comments),
    )


def sedici_columns_evaluator(run, example) -> EvaluationResult:
    """Evalúa que el CSV SEDICI-ready contenga las columnas esperadas."""
    file_info = run.outputs.get("file_info", {})
    expected = example.outputs.get("expected_sedici_columns", [])

    if not expected:
        return EvaluationResult(
            key="sedici_columns", score=1.0, comment="Sin columnas SEDICI esperadas"
        )

    sedici_info = file_info.get("sedici_ready_csv_path", {})
    if not sedici_info.get("exists"):
        return EvaluationResult(
            key="sedici_columns", score=0.0, comment="CSV SEDICI-ready no generado"
        )

    actual_cols = set(sedici_info.get("columns", []))
    hits = sum(1 for col in expected if col in actual_cols)
    score = hits / len(expected)
    missing = [c for c in expected if c not in actual_cols]
    comment = f"Columnas SEDICI faltantes: {missing}" if missing else ""
    return EvaluationResult(key="sedici_columns", score=score, comment=comment)


def data_integrity_evaluator(run, example) -> EvaluationResult:
    """
    Evalúa la integridad de datos: que el conteo de filas no crezca
    inesperadamente entre pasos sucesivos.
    """
    file_info = run.outputs.get("file_info", {})
    ordered_csvs = [
        "generic_source_csv_path",
        "reconciled_csv_path",
        "sedici_ready_csv_path",
    ]

    counts = []
    for key in ordered_csvs:
        info = file_info.get(key, {})
        if info.get("exists") and info.get("row_count", -1) >= 0:
            counts.append((key, info["row_count"]))

    if len(counts) < 2:
        return EvaluationResult(
            key="data_integrity", score=1.0, comment="Insuficientes archivos para comparar"
        )

    # Las filas no deberían crecer (el pipeline filtra, no agrega)
    issues = []
    for i in range(1, len(counts)):
        prev_name, prev_count = counts[i - 1]
        curr_name, curr_count = counts[i]
        if curr_count > prev_count:
            issues.append(
                f"{curr_name} ({curr_count}) > {prev_name} ({prev_count})"
            )

    score = 1.0 if not issues else 0.0
    comment = f"Crecimiento inesperado: {'; '.join(issues)}" if issues else ""
    return EvaluationResult(key="data_integrity", score=score, comment=comment)


# ═══════════════════════════════════════════════════════════════════════════
# Ejecución del pipeline de evaluación
# ═══════════════════════════════════════════════════════════════════════════

def run_evaluation():
    """
    Crea (o reutiliza) un dataset en LangSmith, agrega los casos de prueba
    definidos en TEST_CASES, y ejecuta la evaluación con todos los evaluadores.
    """
    client = Client()

    if not client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description=(
                "Casos de prueba para el pipeline completo de importación a SEDICI. "
                "Cada ejemplo representa un CSV de entrada con sus outputs esperados."
            ),
        )
        print(f"✅ Dataset '{DATASET_NAME}' creado con éxito.")
    else:
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        print(f"ℹ️  El dataset '{DATASET_NAME}' ya existe. Añadiendo ejemplos...")

    # Agregar cada caso de prueba al dataset
    for i, case in enumerate(TEST_CASES):
        csv_path = case["source_csv_path"]
        if not os.path.isfile(csv_path):
            print(f"⚠️  Caso {i}: CSV no encontrado en {csv_path}. Saltando.")
            continue

        inputs = {
            "source_csv_path": csv_path,
            "source_name": case.get("source_name", "test_source"),
            "repository_csv_path": case.get("repository_csv_path", ""),
            "stop_after_step": STOP_AFTER_STEP,
        }

        outputs = {
            "expected_generic_columns": case.get("expected_generic_columns", []),
            "expected_sedici_columns": case.get("expected_sedici_columns", []),
            # Conteos exactos de autores por fila para crosswalk_validation_evaluator
            "expected_author_counts": case.get("expected_author_counts", []),
        }

        client.create_example(
            inputs=inputs,
            outputs=outputs,
            dataset_id=dataset.id,
        )
        print(f"  📄 Caso {i} añadido: {os.path.basename(csv_path)} ({case.get('source_name')})")

    # Mostrar configuración
    print(f"\n{'═' * 60}")
    print(f"  Ejecutando hasta: {get_step_label(STOP_AFTER_STEP)}")
    print(f"  Casos de prueba:  {len(TEST_CASES)}")
    print(f"  Evaluadores:      8")
    print(f"{'═' * 60}\n")

    # Mostrar metadata del experimento antes de ejecutar
    print("  Metadata del experimento:")
    for k, v in EXPERIMENT_METADATA.items():
        print(f"    {k}: {v}")
    print(f"{'═' * 60}\n")

    # Ejecutar evaluación
    evaluate(
        predict_pipeline,
        data=DATASET_NAME,
        evaluators=[
            no_errors_evaluator,
            all_steps_executed_evaluator,
            files_generated_evaluator,
            row_count_evaluator,
            generic_columns_evaluator,
            crosswalk_validation_evaluator,
            sedici_columns_evaluator,
            data_integrity_evaluator,
        ],
        experiment_prefix=EXPERIMENT_PREFIX,
        metadata=EXPERIMENT_METADATA,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Evaluar el pipeline.")
    parser.add_argument("--stop-after", type=str, default=None, help="Paso en el cual detener la ejecución")
    args = parser.parse_args()
    
    if args.stop_after:
        STOP_AFTER_STEP = args.stop_after
        
    run_evaluation()

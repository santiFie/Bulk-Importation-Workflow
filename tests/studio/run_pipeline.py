"""
Script de testeo manual del pipeline completo en LangGraph Studio.

Propósito:
    Permite ejecutar el pipeline de importación a SEDICI de forma aislada,
    paso a paso, con salida formateada y trazabilidad en LangSmith.
    Útil para:
      - Verificar el comportamiento del pipeline tras cambios en los nodos.
      - Depurar pasos específicos con datos reales.
      - Observar la traza completa en LangSmith.
      - Ejecutar hasta un paso determinado sin correr todo el pipeline.

Modo de uso:
    Desde la raíz del proyecto, con el .venv activado:

        python tests/studio/run_pipeline.py

    Para ejecutar hasta un paso específico:

        python tests/studio/run_pipeline.py --stop-after Deduplicate

    Pasos disponibles:
        GenerateSourceCrosswalkConfig, MapSourceToGeneric,
        MapSediciToGeneric, Deduplicate, MetadataReconciliation,
        MapToSediciFormat, MetadataCorrections, GenerateSafToImport,
        ImportToDspace

Configuración:
    Editá las constantes SOURCE_CSV_PATH, REPOSITORY_CSV_PATH y SOURCE_NAME
    para apuntar a los archivos que querés procesar.
"""

import argparse
import csv
import io
import os
import sys
import types
import shutil
import tempfile
import time
import traceback

# Agregar la raíz del proyecto al sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CROSSWALK_MODULE_PATH = os.path.join(_PROJECT_ROOT, "core", "scripts", "crosswalk")
_CONFIGS_DIR = os.path.join(_CROSSWALK_MODULE_PATH, "configs")

for _path in (_PROJECT_ROOT, _CROSSWALK_MODULE_PATH):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# ---------------------------------------------------------------------------
# Mock del DeduplicatorClient
# ---------------------------------------------------------------------------

class _FakeDeduplicatorClient:
    """Mock que devuelve todos los ítems como no-duplicados."""

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
                "match_id": "", "match_title": "", "total": 0,
            })
        return output.getvalue().encode("utf-8")


_fake_mod = types.ModuleType("deduplicator_client")
_fake_mod.DeduplicatorClient = _FakeDeduplicatorClient  # type: ignore
sys.modules["deduplicator_client"] = _fake_mod

# Mock de shutil para evitar errores con PDFs
_orig_copy = shutil.copy
_orig_copyfile = shutil.copyfile
shutil.copy = lambda src, dst, **kw: None  # type: ignore
shutil.copyfile = lambda src, dst, **kw: None  # type: ignore

from core.graph import (  # noqa: E402
    run_pipeline_until_step,
    get_step_node_names,
    get_step_label,
    PIPELINE_STEPS,
)

# Restaurar shutil
shutil.copy = _orig_copy
shutil.copyfile = _orig_copyfile


# ---------------------------------------------------------------------------
# Configuración — EDITÁ ESTAS CONSTANTES según los archivos a procesar
# ---------------------------------------------------------------------------

SOURCE_CSV_PATH = os.path.join(_PROJECT_ROOT, "tests", "data", "SearchResults.csv")
REPOSITORY_CSV_PATH = os.path.join(_PROJECT_ROOT, "tests", "data", "export_10915_all.csv")
SOURCE_NAME = "springer"

# Paso por defecto en el que se detiene (se puede sobreescribir con --stop-after)
DEFAULT_STOP_AFTER = "MetadataCorrections"


# ---------------------------------------------------------------------------
# Formateo de salida
# ---------------------------------------------------------------------------

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _print_header(text: str):
    print(f"\n{_BOLD}{_CYAN}{'═' * 60}{_RESET}")
    print(f"{_BOLD}{_CYAN}  {text}{_RESET}")
    print(f"{_BOLD}{_CYAN}{'═' * 60}{_RESET}")


def _print_step_result(step: str, result: dict, elapsed: float):
    label = get_step_label(step)
    if "__error__" in result:
        status = f"{_RED}✗ ERROR{_RESET}"
        print(f"\n  {status} {_BOLD}{label}{_RESET} ({elapsed:.2f}s)")
        print(f"    {_RED}{result['__error__']}{_RESET}")
    else:
        status = f"{_GREEN}✓ OK{_RESET}"
        print(f"\n  {status} {_BOLD}{label}{_RESET} ({elapsed:.2f}s)")


def _print_file_summary(label: str, path: str):
    if not path or not os.path.isfile(path):
        print(f"    {_YELLOW}⚠ {label}: no generado{_RESET}")
        return

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            row_count = sum(1 for _ in reader)
        print(f"    📄 {label}: {row_count} filas, {len(headers)} columnas")
        if headers:
            cols_str = ", ".join(headers[:8])
            if len(headers) > 8:
                cols_str += f" ... (+{len(headers) - 8} más)"
            print(f"       Columnas: {cols_str}")
    except Exception:
        print(f"    📄 {label}: archivo presente (no CSV)")


# ---------------------------------------------------------------------------
# Ejecución principal
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Ejecutar el pipeline de importación paso a paso."
    )
    parser.add_argument(
        "--stop-after",
        default=DEFAULT_STOP_AFTER,
        choices=get_step_node_names(),
        help=f"Nombre del nodo en el que se detiene (default: {DEFAULT_STOP_AFTER})",
    )
    args = parser.parse_args()
    stop_after = args.stop_after

    _print_header("Pipeline de importación a SEDICI — Ejecución manual")
    print(f"  CSV fuente:       {SOURCE_CSV_PATH}")
    print(f"  CSV SEDICI:       {REPOSITORY_CSV_PATH}")
    print(f"  Repositorio:      {SOURCE_NAME}")
    print(f"  Ejecutar hasta:   {get_step_label(stop_after)}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Copiar archivos al directorio temporal
        tmp_csv = os.path.join(tmp_dir, os.path.basename(SOURCE_CSV_PATH))
        shutil.copy(SOURCE_CSV_PATH, tmp_csv)

        tmp_repo = ""
        if os.path.isfile(REPOSITORY_CSV_PATH):
            tmp_repo = os.path.join(tmp_dir, os.path.basename(REPOSITORY_CSV_PATH))
            shutil.copy(REPOSITORY_CSV_PATH, tmp_repo)

        state = {
            "messages": [],
            "source_csv_path": tmp_csv,
            "source_name": SOURCE_NAME,
            "repository_csv_path": tmp_repo,
            "source_crosswalk_config": "",
            "sedici_crosswalk_config": os.path.join(_CONFIGS_DIR, "sedicicrosswalkconfig.json"),
            "generic_source_csv_path": os.path.join(tmp_dir, "generic_source.csv"),
            "generic_sedici_csv_path": os.path.join(tmp_dir, "generic_sedici.csv"),
            "dedup_output_csv_path": os.path.join(tmp_dir, "dedup_output.csv"),
            "reconciled_csv_path": os.path.join(tmp_dir, "reconciled.csv"),
            "sedici_target_crosswalk_config": os.path.join(
                _CONFIGS_DIR, "config_romero_to_sedici.json"
            ),
            "sedici_ready_csv_path": os.path.join(tmp_dir, "sedici_ready.csv"),
            "umbral_seguro": 10,
            "umbral_revision": 30,
            "saf_output_path": os.path.join(tmp_dir, "saf_output"),
            "dspace_collection": "test-collection",
            "import_mapfile_path": os.path.join(tmp_dir, "mapfile"),
            "import_validate_only": True,
        }

        _print_header("Ejecución paso a paso")

        # Mock shutil para SAF
        orig_copy = shutil.copy
        orig_copyfile = shutil.copyfile
        shutil.copy = lambda src, dst, **kw: None
        shutil.copyfile = lambda src, dst, **kw: None

        total_start = time.time()
        try:
            results = run_pipeline_until_step(state, stop_after)
        finally:
            shutil.copy = orig_copy
            shutil.copyfile = orig_copyfile

        total_elapsed = time.time() - total_start

        # Mostrar resultados por paso
        for step, result in results.items():
            _print_step_result(step, result, 0.0)

        # Resumen de archivos generados
        _print_header("Archivos generados")
        _print_file_summary("Config crosswalk", state.get("source_crosswalk_config", ""))
        _print_file_summary("CSV genérico (fuente)", state.get("generic_source_csv_path", ""))
        _print_file_summary("CSV genérico (SEDICI)", state.get("generic_sedici_csv_path", ""))
        _print_file_summary("CSV deduplicación", state.get("dedup_output_csv_path", ""))
        _print_file_summary("CSV reconciliado", state.get("reconciled_csv_path", ""))
        _print_file_summary("CSV SEDICI-ready", state.get("sedici_ready_csv_path", ""))

        # Resumen de errores
        errors = {k: v for k, v in results.items() if "__error__" in v}
        if errors:
            _print_header("ERRORES DETECTADOS")
            for step, result in errors.items():
                print(f"  {_RED}✗ {get_step_label(step)}{_RESET}")
                print(f"    {result['__error__']}")
        else:
            print(f"\n{_GREEN}{_BOLD}  ✓ Pipeline ejecutado sin errores hasta "
                  f"{get_step_label(stop_after)}{_RESET}")

        print(f"\n  ⏱  Tiempo total: {total_elapsed:.2f}s")
        print(f"  📊 Pasos ejecutados: {len(results)}/{len(get_step_node_names())}")


if __name__ == "__main__":
    main()

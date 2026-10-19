"""
Evaluadores y métricas semánticas para el agente `source_to_generic`.

Diseñados tanto para LangSmith Nativo (`langsmith.evaluate` / `aevaluate`) como para Pytest.
Cada evaluador cumple la firma estándar de LangSmith: `(run: Run, example: Example) -> dict[str, Any]`.
"""

from __future__ import annotations

import re
from typing import Any
from langsmith.schemas import Example, Run

from core.nodes.source_to_generic.helpers import GENERIC_COLUMNS

ALLOWED_GENERIC_FIELDS = set(GENERIC_COLUMNS.keys())


def _normalize_left(left_str: str) -> str:
    """Normaliza una expresión 'left' (simple o compuesta con '+') para comparación."""
    parts = [p.strip() for p in str(left_str).split("+") if p.strip()]
    return "+".join(parts)


def eval_header_fidelity(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la fidelidad de las cabeceras mapeadas (*Header Fidelity*).

    Invariante: Ningún valor 'left' propuesto por el LLM puede ser un valor de celda
    ni un nombre inventado; debe existir exactamente en las cabeceras del CSV de entrada
    (o ser una columna inyectada válidamente por pre-enriquecimiento como 'inferred_*').
    """
    outputs = run.outputs or {}
    inputs = example.inputs or {}

    available_cols = set(inputs.get("columns", []))
    mappings = outputs.get("mappings", [])

    if not mappings:
        return {
            "key": "header_fidelity",
            "score": 0.0,
            "comment": "No se generaron mapeos.",
        }

    invalid_lefts = []
    for m in mappings:
        left = m.get("left", "").strip()
        # Soporta combinación con '+' (ej. ColA+ColB) o wildcard '*'
        parts = [p.strip().rstrip("*") for p in left.split("+")]
        for part in parts:
            if not part:
                continue
            is_inferred = part.startswith("inferred_")
            is_available = any(
                part.lower() == col.lower() or col.lower().startswith(part.lower())
                for col in available_cols
            )
            if not is_available and not is_inferred:
                invalid_lefts.append(part)

    score = 1.0 if not invalid_lefts else 0.0
    comment = (
        "Todas las cabeceras 'left' existen en el CSV."
        if score == 1.0
        else f"Cabeceras inválidas o valores de celda detectados: {invalid_lefts}"
    )

    return {
        "key": "header_fidelity",
        "score": score,
        "comment": comment,
    }


def eval_critical_fields_coverage(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la cobertura de campos críticos (*Critical Fields Coverage*).

    Calcula la proporción [0.0, 1.0] de campos obligatorios para la deduplicación
    (por defecto 'id', 'title', 'author', 'date', 'type') que fueron asignados en 'replace'.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    critical_fields = set(
        reference.get("critical_fields", ["id", "title", "author", "date", "type"])
    )
    mappings = outputs.get("mappings", [])

    covered_fields = {m.get("replace", "").strip() for m in mappings if m.get("replace")}
    missing_critical = [cf for cf in critical_fields if cf not in covered_fields]

    if not critical_fields:
        return {
            "key": "critical_fields_coverage",
            "score": None,
            "comment": "N/A: Sin campos críticos declarados en el caso.",
        }

    coverage = (len(critical_fields) - len(missing_critical)) / len(critical_fields)

    return {
        "key": "critical_fields_coverage",
        "score": float(coverage),
        "comment": (
            f"Cobertura completa ({len(critical_fields)}/{len(critical_fields)})"
            if coverage == 1.0
            else f"Faltan campos críticos: {missing_critical}"
        ),
    }


def eval_target_schema_adherence(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la adherencia estricta al esquema genérico de 12 campos (*Target Schema Adherence*).

    Valida que ningún 'replace' apunte a un campo inexistente o inventado.
    """
    outputs = run.outputs or {}
    mappings = outputs.get("mappings", [])

    if not mappings:
        return {
            "key": "target_schema_adherence",
            "score": 0.0,
            "comment": "No se produjeron mapeos.",
        }

    invalid_replaces = [
        m.get("replace", "")
        for m in mappings
        if m.get("replace", "").strip() not in ALLOWED_GENERIC_FIELDS
    ]

    score = 1.0 if not invalid_replaces else 0.0
    comment = (
        "Todos los campos 'replace' pertenecen al esquema genérico permitido."
        if score == 1.0
        else f"Campos no permitidos en replace: {invalid_replaces}"
    )

    return {
        "key": "target_schema_adherence",
        "score": score,
        "comment": comment,
    }


def eval_expected_mappings_accuracy(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la exactitud semántica de los mapeos contra el oráculo del caso (*Expected Mappings Accuracy*).

    Verifica:
      1. `expected_mappings`: pares {left_esperado: replace_esperado} presentes y correctos.
      2. `forbidden_fields`: columnas origen ruidosas o vacías que NO deben aparecer en ningún 'left'.
      3. `forbidden_mappings`: pares {left: replace_prohibido} que NO deben ocurrir.
      4. `unmappable_fields`: campos destino ('replace') que NO deben ser inventados ni mapeados en el caso.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    expected_mappings: dict[str, str] = reference.get("expected_mappings", {})
    forbidden_fields: list[str] = reference.get("forbidden_fields", [])
    forbidden_mappings: dict[str, str] = reference.get("forbidden_mappings", {})
    unmappable_fields: list[str] = reference.get("unmappable_fields", [])

    if not (expected_mappings or forbidden_fields or forbidden_mappings or unmappable_fields):
        return {
            "key": "expected_mappings_accuracy",
            "score": None,
            "comment": "N/A: El caso no especifica oráculo de mapeos esperados o prohibidos.",
        }

    mappings = outputs.get("mappings", [])
    if not mappings:
        return {
            "key": "expected_mappings_accuracy",
            "score": 0.0,
            "comment": "No se produjeron mapeos.",
        }

    # Construir índices de lo devuelto por el agente (sensible a mayúsculas para distinguir 'title' vs 'TITLE')
    exact_left_to_replace: dict[str, str] = {}
    lower_left_to_replace: dict[str, str] = {}
    replace_to_left: dict[str, str] = {}
    used_atomic_lefts: set[str] = set()

    for m in mappings:
        left = _normalize_left(m.get("left", ""))
        replace = str(m.get("replace", "")).strip()
        if not left or not replace:
            continue
        exact_left_to_replace[left] = replace
        lower_left_to_replace[left.lower()] = replace
        replace_to_left[replace] = left
        for part in left.split("+"):
            used_atomic_lefts.add(part.strip())

    errors: list[str] = []
    matched_count = 0

    # 1. Verificar expected_mappings
    for exp_left, exp_replace in expected_mappings.items():
        norm_exp_left = _normalize_left(exp_left)
        actual_replace = exact_left_to_replace.get(norm_exp_left)
        if actual_replace is None:
            actual_replace = lower_left_to_replace.get(norm_exp_left.lower())

        if actual_replace == exp_replace:
            matched_count += 1
        else:
            actual_left_for_target = replace_to_left.get(exp_replace)
            errors.append(
                f"Esperado '{norm_exp_left}'->'{exp_replace}', obtenido left='{actual_left_for_target}' / replace='{actual_replace}'"
            )

    # 2. Verificar forbidden_fields (columnas que deben ignorarse)
    for forb_col in forbidden_fields:
        if forb_col in used_atomic_lefts or forb_col in exact_left_to_replace:
            errors.append(f"Columna prohibida '{forb_col}' fue mapeada indebidamente.")

    # 3. Verificar forbidden_mappings
    for forb_left, forb_replace in forbidden_mappings.items():
        norm_forb_left = _normalize_left(forb_left)
        actual_rep = exact_left_to_replace.get(norm_forb_left)
        if actual_rep is None:
            actual_rep = lower_left_to_replace.get(norm_forb_left.lower())
        if actual_rep == forb_replace:
            errors.append(
                f"Mapeo prohibido detectado: '{forb_left}' -> '{forb_replace}'."
            )

    # 4. Verificar unmappable_fields (abstención obligatoria sobre campos ausentes)
    for unmappable in unmappable_fields:
        if unmappable in replace_to_left:
            errors.append(
                f"Campo inexistente '{unmappable}' fue mapeado desde '{replace_to_left[unmappable]}'."
            )

    if errors:
        total_checks = (
            len(expected_mappings)
            + len(forbidden_fields)
            + len(forbidden_mappings)
            + len(unmappable_fields)
        )
        passed_checks = max(0, total_checks - len(errors))
        score = passed_checks / total_checks if total_checks > 0 else 0.0
        return {
            "key": "expected_mappings_accuracy",
            "score": float(score),
            "comment": "; ".join(errors),
        }

    return {
        "key": "expected_mappings_accuracy",
        "score": 1.0,
        "comment": f"Todos los mapeos esperados ({matched_count}) y restricciones se cumplieron exactamente.",
    }


def eval_doi_handling_correctness(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa el manejo correcto de identificadores DOI (*DOI Handling Correctness*).

    Regla crítica:
      1. La columna 'doi' debe mapearse a su campo propio 'doi'.
      2. NUNCA debe usarse la columna 'doi' para cubrir el campo 'id'.
    """
    outputs = run.outputs or {}
    inputs = example.inputs or {}

    available_cols = [c.lower() for c in inputs.get("columns", [])]
    has_doi_column = any("doi" in c or "digital_object_identifier" in c for c in available_cols)

    if not has_doi_column:
        # Si el CSV no tiene DOI, la métrica no aplica (evita aprobaciones vacías)
        return {
            "key": "doi_handling_correctness",
            "score": None,
            "comment": "N/A: El caso no contiene columna de DOI.",
        }

    mappings = outputs.get("mappings", [])
    doi_mapped_to_id = False
    doi_mapped_to_doi = False

    for m in mappings:
        left = m.get("left", "").lower()
        replace = m.get("replace", "").lower()
        if "doi" in left or "digital_object_identifier" in left:
            if replace == "id":
                doi_mapped_to_id = True
            elif replace == "doi":
                doi_mapped_to_doi = True

    if doi_mapped_to_id:
        return {
            "key": "doi_handling_correctness",
            "score": 0.0,
            "comment": "Violación de regla: La columna DOI fue indebidamente mapeada a 'id'.",
        }

    score = 1.0 if doi_mapped_to_doi else 0.5
    comment = (
        "DOI mapeado correctamente a replace='doi' sin interferir con 'id'."
        if score == 1.0
        else "DOI no se mapeó a 'id', pero tampoco se preservó en replace='doi'."
    )

    return {
        "key": "doi_handling_correctness",
        "score": score,
        "comment": comment,
    }


def eval_tool_selection(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la selección y trayectoria de herramientas en pre-enriquecimiento.

    Valida que:
      1. Se llamen todas las herramientas en 'expected_tools'.
      2. No se ejecute ninguna de 'forbidden_tools'.
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    expected_tools = reference.get("expected_tools", [])
    forbidden_tools = reference.get("forbidden_tools", [])
    tools_executed = outputs.get("tools_executed", [])

    has_expected = all(t in tools_executed for t in expected_tools)
    has_no_forbidden = all(f not in tools_executed for f in forbidden_tools)

    score = 1.0 if (has_expected and has_no_forbidden) else 0.0
    comment = f"Esperadas: {expected_tools}, Prohibidas: {forbidden_tools}, Ejecutadas: {tools_executed}"

    return {
        "key": "tool_selection_accuracy",
        "score": score,
        "comment": comment,
    }


def eval_feedback_recovery(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa si el agente corrigió el error reportado en el feedback de validación determinista.

    Verifica:
      1. Presencia de todos los `fixed_fields` y `must_contain_fields`.
      2. Correspondencia exacta de `expected_left_mappings` ({replace: expected_left}).
      3. Ausencia de `forbidden_lefts` (ej. valores de celda) y `forbidden_replaces` (ej. campos inexistentes).
      4. Configuración `required=False` en `expected_non_required_fields` (recuperación de filtrado agresivo).
    """
    outputs = run.outputs or {}
    reference = example.outputs or {}

    fixed_field = reference.get("fixed_field")
    fixed_fields = list(reference.get("fixed_fields", [fixed_field] if fixed_field else []))
    must_contain = list(reference.get("must_contain_fields", []))
    expected_left = reference.get("expected_left_for_fixed_field")
    expected_left_mappings: dict[str, str] = dict(reference.get("expected_left_mappings", {}))
    if expected_left and fixed_field and fixed_field not in expected_left_mappings:
        expected_left_mappings[fixed_field] = expected_left

    forbidden_lefts = reference.get("forbidden_lefts", [])
    forbidden_replaces = reference.get("forbidden_replaces", [])
    expected_non_required = reference.get("expected_non_required_fields", [])

    if not (
        fixed_fields
        or must_contain
        or expected_left_mappings
        or forbidden_lefts
        or forbidden_replaces
        or expected_non_required
    ):
        return {
            "key": "feedback_recovery_accuracy",
            "score": None,
            "comment": "N/A: Sin criterios de recuperación declarados en el caso.",
        }

    mappings = outputs.get("mappings", [])
    mapping_dict = {m.get("replace"): m.get("left") for m in mappings if m.get("replace")}
    required_by_replace = {
        m.get("replace"): bool(m.get("required", False))
        for m in mappings
        if m.get("replace")
    }

    # 1. Verificar presencia de campos corregidos y obligatorios
    required_targets = set(fixed_fields) | set(must_contain)
    all_required_present = all(f in mapping_dict for f in required_targets)

    # 2. Verificar que 'left' esperado coincida para cada campo en expected_left_mappings
    left_ok = True
    left_mismatches = []
    for key_a, val_b in expected_left_mappings.items():
        # Soporta tanto {replace: expected_left} como {expected_left: replace}
        if key_a in ALLOWED_GENERIC_FIELDS:
            target_rep, exp_l = key_a, val_b
        else:
            exp_l, target_rep = key_a, val_b
        actual_l = str(mapping_dict.get(target_rep, ""))
        if actual_l.lower() != exp_l.lower():
            left_ok = False
            left_mismatches.append(f"{target_rep}: esperado='{exp_l}', obtenido='{actual_l}'")

    # 3. Verificar que no se usen cabeceras prohibidas (ej. valores de celda) ni replaces prohibidos
    no_forbidden_lefts = all(
        all(fl.lower() != str(m.get("left", "")).lower() for m in mappings)
        for fl in forbidden_lefts
    )
    no_forbidden_replaces = all(fr not in mapping_dict for fr in forbidden_replaces)

    # 4. Verificar campos que no deben tener required=True
    non_required_ok = all(
        not required_by_replace.get(f, False) for f in expected_non_required
    )

    all_ok = (
        all_required_present
        and left_ok
        and no_forbidden_lefts
        and no_forbidden_replaces
        and non_required_ok
    )
    score = 1.0 if all_ok else 0.0
    comment = (
        f"Recuperación exitosa. Campos verificados: {sorted(required_targets)}"
        if score == 1.0
        else (
            f"Fallo en recuperación: presentes={all_required_present}, "
            f"left_ok={left_ok} ({left_mismatches}), "
            f"no_forbidden_lefts={no_forbidden_lefts}, "
            f"no_forbidden_replaces={no_forbidden_replaces}, "
            f"non_required_ok={non_required_ok}"
        )
    )

    return {
        "key": "feedback_recovery_accuracy",
        "score": score,
        "comment": comment,
    }


def _normalize_token(token: str) -> str:
    """Normaliza un token de autor/materia quitando espacios extremos y punto final opcional."""
    return token.strip().rstrip(".").strip()


def eval_regex_react_quality(run: Run, example: Example) -> dict[str, Any]:
    """
    Evalúa la calidad sintáctica, efectividad de división y exactitud de tokens
    del regex propuesto por el agente ReAct.
    """
    outputs = run.outputs or {}
    inputs = example.inputs or {}
    reference = example.outputs or {}

    pattern = outputs.get("proposed_regex", "").strip()
    samples = inputs.get("sample_values", [])
    min_efficiency = reference.get("min_split_efficiency", 0.5)
    expected_action = reference.get("expected_action")
    expected_tokens_per_sample: list[list[str]] = reference.get("expected_tokens_per_sample", [])

    if expected_action == "no_split_or_sin_patron":
        if pattern in ("SIN_PATRON", ""):
            return {
                "key": "regex_split_efficiency",
                "score": 1.0,
                "comment": "Abstención correcta: El agente identificó que no correspondía dividir.",
            }
        try:
            compiled = re.compile(pattern)
            splits = [len(compiled.split(s)) for s in samples]
            spurious_splits = sum(s > 1 for s in splits)
            if spurious_splits == 0:
                return {
                    "key": "regex_split_efficiency",
                    "score": 1.0,
                    "comment": "Patrón inocuo que no partió incorrectamente los valores únicos.",
                }
            return {
                "key": "regex_split_efficiency",
                "score": 0.0,
                "comment": f"División espuria: partió {spurious_splits}/{len(samples)} valores únicos.",
            }
        except re.error as exc:
            return {
                "key": "regex_split_efficiency",
                "score": 0.0,
                "comment": f"Regex sintácticamente inválido: {exc}",
            }

    if not pattern or pattern == "SIN_PATRON":
        return {
            "key": "regex_split_efficiency",
            "score": 0.0,
            "comment": "Fallo: El agente no produjo ningún patrón para un caso positivo.",
        }

    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return {
            "key": "regex_split_efficiency",
            "score": 0.0,
            "comment": f"Regex sintácticamente inválido '{pattern}': {exc}",
        }

    if not samples:
        return {
            "key": "regex_split_efficiency",
            "score": None,
            "comment": "N/A: Sin muestras para verificar división.",
        }

    split_results = [
        [t.strip() for t in compiled.split(s) if t and t.strip()]
        for s in samples
    ]
    multi = sum(len(tokens) > 1 for tokens in split_results)
    efficiency = multi / len(samples)

    # Si el caso declara los tokens esperados por muestra, verificar que no haya destruido los nombres
    if expected_tokens_per_sample and len(expected_tokens_per_sample) == len(samples):
        exact_sample_matches = 0
        mismatches = []
        for idx, (got_tokens, exp_tokens) in enumerate(zip(split_results, expected_tokens_per_sample)):
            norm_got = [_normalize_token(t) for t in got_tokens]
            norm_exp = [_normalize_token(t) for t in exp_tokens]
            if norm_got == norm_exp:
                exact_sample_matches += 1
            else:
                mismatches.append(f"Muestra {idx+1}: obtenido={got_tokens} vs esperado={exp_tokens}")

        token_accuracy = exact_sample_matches / len(samples)
        score = 1.0 if token_accuracy >= min_efficiency else float(token_accuracy)
        comment = (
            f"Patrón: '{pattern}' — {exact_sample_matches}/{len(samples)} muestras divididas con tokens exactos ({token_accuracy:.1%})."
            if not mismatches
            else f"Patrón: '{pattern}' — Exactitud de tokens {token_accuracy:.1%}. Diferencias: {'; '.join(mismatches[:2])}"
        )
        return {
            "key": "regex_split_efficiency",
            "score": score,
            "comment": comment,
        }

    score = 1.0 if efficiency >= min_efficiency else float(efficiency)
    comment = f"Patrón: '{pattern}' — Dividió {multi}/{len(samples)} muestras ({efficiency:.1%}). Min esperado: {min_efficiency:.1%}"

    return {
        "key": "regex_split_efficiency",
        "score": score,
        "comment": comment,
    }


# Evaluadores homogéneos para la suite nativa de benchmarking de mapeos
SOURCE_MAPPING_EVALUATORS = [
    eval_header_fidelity,
    eval_critical_fields_coverage,
    eval_target_schema_adherence,
    eval_expected_mappings_accuracy,
    eval_doi_handling_correctness,
]

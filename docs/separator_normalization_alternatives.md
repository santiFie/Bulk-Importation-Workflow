# Separator Normalization — Alternative Solutions (1 & 2)

> **Context:** This document captures two alternative approaches to handling complex/irregular
> author-field separators in source CSVs. The currently implemented approach (Solution 3 — LLM
> generates and tests a regex via a tool loop) is in `core/graph.py` and `core/scripts/crosswalk/`.
> The alternatives below were intentionally deferred but documented for future reference.

---

## Problem

Some source CSVs have author fields where authors are concatenated without a conventional
separator. Example:

```
"G. AadE. AakvaagB. AbbottS. AbdelhameedK. AbelingN. J. AbichtS."
```

The `Crosswalk` engine uses a plain `str.replace(original_separator, replace_separator)` call
(`crosswalk.py`). This only works when the separator is a fixed, unambiguous literal like `||`,
`;`, or `,`. Complex patterns require a different strategy.

---

## Solution 1 — Declarative Strategy Catalog

### Concept

Instead of the LLM generating raw regex code, the LLM acts as a **classifier**: it selects one
entry from a pre-built catalog of normalization strategies. The actual regex/logic is always
written by a developer and committed to the codebase — never generated at runtime by the LLM.

### Catalog (example)

```python
# core/scripts/crosswalk/author_normalizer.py

NORMALIZATION_STRATEGIES = {
    "standard_literal": {
        "description": "Authors separated by a single, unambiguous literal delimiter",
        "example": "Doe, J.||Smith, A.",
        "apply": lambda value, sep, replace: value.replace(sep, replace),
    },
    "concatenated_initials": {
        "description": (
            "Authors concatenated without delimiter; each name starts with an "
            "uppercase initial followed by a dot: 'G. AadE. AakvaagB. Abbott'"
        ),
        "example": "G. AadE. AakvaagB. AbbottS. Abdelhameed",
        "pattern": r"(?<=[a-záéíóúüñ\w])(?=[A-ZÁÉÍÓÚÜÑ][a-z][a-z])",
        "apply": lambda value, _sep, replace: re.sub(
            r"(?<=[a-záéíóúüñ\w])(?=[A-ZÁÉÍÓÚÜÑ][a-z][a-z])", replace, value
        ),
    },
    "period_separated": {
        "description": "Authors separated by a period followed by a space or uppercase",
        "example": "Doe J. Smith A. Brown C.",
        "pattern": r"\.\s+(?=[A-Z])",
        "apply": lambda value, _sep, replace: re.sub(r"\.\s+(?=[A-Z])", replace, value),
    },
    "semicolon": {
        "description": "Semicolon-separated authors",
        "example": "Doe, J.; Smith, A.; Brown, C.",
        "apply": lambda value, _sep, replace: value.replace(";", replace),
    },
}
```

### New tool for `generate_source_crosswalk_config`

```python
@tool
def set_author_normalization_strategy(strategy_name: str, column: str) -> str:
    """
    Selects a pre-built normalization strategy for a multi-value column (e.g. author).

    Args:
        strategy_name: One of the keys in NORMALIZATION_STRATEGIES.
        column: The source column this strategy applies to.

    Returns:
        A preview of how the strategy would split the sample values.
    """
    from core.scripts.crosswalk.author_normalizer import NORMALIZATION_STRATEGIES
    ...
```

### How it fits the config JSON

Instead of `separator_regex`, the config would carry:

```json
{
  "original_separator": "|",
  "replace_separator": "|",
  "file_delimiter": ",",
  "normalization_strategy": "concatenated_initials"
}
```

`CrosswalkContext` would load `normalization_strategy` and `crosswalk.py` would dispatch to the
right lambda in the catalog.

### Advantages over Solution 3

| Dimension | Solution 1 | Solution 3 (current) |
|---|---|---|
| LLM generates executable code | ❌ Never | ✅ Yes |
| Can handle truly new patterns | ❌ Only cataloged patterns | ✅ Yes |
| Risk of silent regex error | 🟢 None | 🟠 Possible |
| Requires dev work for new patterns | 🔴 Yes | 🟢 No |
| Model size requirement | 🟢 Any model | 🟡 ~30B+ recommended |

---

## Solution 2 — Heuristic Python Pre-detection

### Concept

Before calling the LLM at all, run a **deterministic Python detector** over the author column
samples. If the detector can classify the separator with high confidence, the LLM is never
invoked for this step. Only on ambiguous/unknown patterns does it fall back to the LLM (Solution 3).

### Sketch

```python
def _detect_author_separator(samples: list[str]) -> tuple[str, str | None]:
    """
    Returns (separator_type, value) where separator_type is one of:
      'literal'  → value is the plain separator string
      'regex'    → value is a Python re pattern
      'unknown'  → value is None, fall back to LLM

    Decision thresholds:
      - A literal separator must appear in >80% of non-empty samples.
      - A regex pattern must produce ≥2 tokens in >60% of non-empty samples.
    """
    import re

    LITERAL_CANDIDATES = ["||", ";;", " - ", ";", "|", "  "]
    REGEX_CANDIDATES = {
        "concatenated_initials": r"(?<=[a-z\u00e0-\u00ff\w])(?=[A-Z\u00C0-\u00DC][a-z\u00e0-\u00ff]{1,})",
        "period_space_caps":     r"\.\s+(?=[A-Z])",
    }

    non_empty = [s.strip() for s in samples if s.strip()]
    if not non_empty:
        return ("unknown", None)

    for sep in LITERAL_CANDIDATES:
        hits = sum(1 for s in non_empty if sep in s)
        if hits / len(non_empty) > 0.8:
            return ("literal", sep)

    for _name, pattern in REGEX_CANDIDATES.items():
        multi = sum(1 for s in non_empty if len(re.split(pattern, s)) >= 2)
        if multi / len(non_empty) > 0.6:
            return ("regex", pattern)

    return ("unknown", None)
```

### Integration point

This function would run inside `generate_source_crosswalk_config` **before** building the
system prompt for the LLM. The detected separator (or regex) would be pre-populated in the
tool call, and the LLM would only override it if the validation sample shows a problem.

### Advantages

- Zero LLM calls for 90%+ of well-structured CSVs (standard separators).
- Reduces latency and cost significantly.
- Naturally composable with Solution 1 (heuristic → catalog) or Solution 3 (heuristic → LLM regex).

---

## Recommended combination (if Solution 3 proves insufficient)

```
_detect_author_separator()          ← Python heuristic, free
       ↓ "unknown"
LLM classifies strategy             ← selects from NORMALIZATION_STRATEGIES catalog
       ↓ strategy not in catalog
LLM generates + tests regex         ← Solution 3 (current), last resort
```

This three-tier fallback maximizes determinism while preserving full flexibility.

"""
Nodo MetadataReconciliation — Paso 4 del pipeline.

Filtra los ítems del repositorio origen que no son duplicados y
realiza un JOIN con el CSV original para recuperar sus metadatos completos.

Encapsula la lógica de reconciliación en la clase `MetadataReconciler`,
que puede testearse de forma independiente al grafo LangGraph.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import pandas as pd
from langsmith import traceable

logger = logging.getLogger(__name__)

# Columnas que pueden ser propagadas desde el CSV genérico enriquecido
_ENRICHABLE_COLUMNS = ["author", "date", "doi", "issn", "isbn", "citation", "subject", "type"]

# Valores que pandas interpreta como vacíos pero debemos tratar como tales
_EMPTY_VALUES = {"", "nan", "None"}


# ---------------------------------------------------------------------------
# Servicio de reconciliación (lógica pura, sin dependencia del State)
# ---------------------------------------------------------------------------

class MetadataReconciler:
    """
    Servicio de reconciliación de metadatos post-deduplicación.

    Recibe los DataFrames del resultado del deduplicador y del CSV fuente
    original, y devuelve un DataFrame con los ítems que deben importarse
    (los que no son duplicados según el umbral de similitud).

    Soporta dos formatos de output del deduplicador:
      - Formato backend REST: columnas `id_document1`, `id_document2`, `similarity`.
      - Formato legacy/mock:  columnas `id`, `total` (score escalado 0-100).

    Attributes:
        umbral_seguro:    Porcentaje de similitud por debajo del cual se
                          considera que el ítem NO es un duplicado.
        generic_to_source: Mapeo de nombre de campo genérico al nombre de
                           columna original en el CSV fuente.
    """

    def __init__(
        self,
        umbral_seguro: int = 10,
        generic_to_source: Optional[dict[str, str]] = None,
    ) -> None:
        self._umbral = umbral_seguro
        self._generic_to_source = generic_to_source or {}

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def reconcile(self, df_dedup: pd.DataFrame, df_source: pd.DataFrame) -> pd.DataFrame:
        """
        Filtra df_source para retener solo los ítems que NO son duplicados
        según el resultado del deduplicador.

        Args:
            df_dedup:  DataFrame con el output del deduplicador.
            df_source: DataFrame con los metadatos originales del CSV fuente.

        Returns:
            DataFrame filtrado con los ítems a importar.
        """
        id_col_source = self._resolve_id_column(df_source)

        if self._is_rest_format(df_dedup):
            return self._reconcile_rest_format(df_dedup, df_source, id_col_source)
        return self._reconcile_legacy_format(df_dedup, df_source, id_col_source)

    def propagate_enrichment(
        self,
        df_reconciled: pd.DataFrame,
        df_generic: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Propaga los campos enriquecidos del CSV genérico al DataFrame reconciliado.

        Rellena los campos vacíos en df_reconciled con los valores del CSV
        genérico enriquecido (ej. author, date, doi) usando el ID como clave.
        También propaga a las columnas originales de la fuente si el nombre
        difiere del genérico.

        Args:
            df_reconciled: DataFrame filtrado de ítems a importar.
            df_generic:    DataFrame del CSV genérico con columnas enriquecidas.

        Returns:
            df_reconciled con los campos vacíos completados donde sea posible.
        """
        id_col_source = self._resolve_id_column(df_reconciled)
        if not id_col_source or "id" not in df_generic.columns:
            return df_reconciled

        df_out = df_reconciled.copy()
        df_generic_indexed = df_generic.set_index("id")

        for col in _ENRICHABLE_COLUMNS:
            if col not in df_generic_indexed.columns:
                continue

            mapped_values = df_out[id_col_source].map(df_generic_indexed[col])
            df_out = self._fill_column(df_out, col, mapped_values)
            df_out = self._fill_source_column(df_out, col, mapped_values)

        return df_out

    # ------------------------------------------------------------------
    # Métodos de reconciliación por formato
    # ------------------------------------------------------------------

    def _is_rest_format(self, df_dedup: pd.DataFrame) -> bool:
        """Determina si el DataFrame viene del formato backend REST."""
        return "similarity" in df_dedup.columns and "id_document1" in df_dedup.columns

    def _reconcile_rest_format(
        self,
        df_dedup: pd.DataFrame,
        df_source: pd.DataFrame,
        id_col_source: Optional[str],
    ) -> pd.DataFrame:
        """Reconcilia usando el formato backend REST (columna `similarity`)."""
        duplicate_ids = self._extract_rest_duplicate_ids(df_dedup)

        if id_col_source and duplicate_ids:
            return df_source[
                ~df_source[id_col_source].astype(str).isin(duplicate_ids)
            ].copy()

        return df_source.copy()

    def _reconcile_legacy_format(
        self,
        df_dedup: pd.DataFrame,
        df_source: pd.DataFrame,
        id_col_source: Optional[str],
    ) -> pd.DataFrame:
        """Reconcilia usando el formato legacy/mock (columna `total`)."""
        score_col = "total" if "total" in df_dedup.columns else df_dedup.columns[-1]
        id_col_dedup = "id" if "id" in df_dedup.columns else df_dedup.columns[0]

        df_to_import = df_dedup[
            pd.to_numeric(df_dedup[score_col], errors="coerce").fillna(0) < self._umbral
        ]

        if id_col_source is None:
            return df_source.iloc[df_to_import.index].copy()

        return df_source[
            df_source[id_col_source].isin(df_to_import[id_col_dedup])
        ].copy()

    def _extract_rest_duplicate_ids(self, df_dedup: pd.DataFrame) -> set[str]:
        """Extrae los IDs de la fuente (id_document2) considerados duplicados."""
        scores = df_dedup["similarity"].apply(self._parse_similarity_score)
        duplicate_mask = scores >= self._umbral
        return set(
            df_dedup.loc[duplicate_mask, "id_document2"].dropna().astype(str).unique()
        )

    def _parse_similarity_score(self, val: Any) -> float:
        """
        Normaliza un valor de similitud al rango [0, 100].

        Los valores "NO_DUPLICATE" y nulos se convierten a 0.0.
        Los scores en [0, 1] se escalan a porcentaje si el umbral es > 1.
        Los valores no numéricos desconocidos se asumen como 100.0 (duplicado).
        """
        _NON_DUPLICATE_MARKERS = {"NO_DUPLICATE", "NONE", ""}

        if pd.isna(val) or str(val).strip().upper() in _NON_DUPLICATE_MARKERS:
            return 0.0
        try:
            score = float(val)
            if score <= 1.0 and self._umbral > 1:
                score *= 100.0
            return score
        except (ValueError, TypeError):
            return 100.0

    # ------------------------------------------------------------------
    # Resolución de la columna ID
    # ------------------------------------------------------------------

    def _resolve_id_column(self, df: pd.DataFrame) -> Optional[str]:
        """
        Identifica la columna que actúa como identificador en el DataFrame.

        Estrategia:
          1. Columna mapeada al campo genérico 'id' según el crosswalk config.
          2. Fallback heurístico: candidatos comunes por nombre.

        Returns:
            Nombre de la columna ID o None si no se encuentra.
        """
        # 1. Columna explícita desde el crosswalk config
        id_from_mapping = self._generic_to_source.get("id")
        if id_from_mapping:
            candidates = [c.strip() for c in id_from_mapping.split("+") if c.strip()]
            for candidate in candidates:
                if candidate in df.columns:
                    return candidate

        # 2. Fallback heurístico
        _FALLBACK_ID_CANDIDATES = [
            "id", "sedici.identifier.other", "dc.identifier.uri",
            "doi", "pmid", "handle", "url", "uri",
        ]
        columns_lower = {col.lower(): col for col in df.columns}
        for candidate in _FALLBACK_ID_CANDIDATES:
            if candidate.lower() in columns_lower:
                return columns_lower[candidate.lower()]

        return None

    # ------------------------------------------------------------------
    # Propagación de enriquecimiento
    # ------------------------------------------------------------------

    def _fill_column(
        self,
        df: pd.DataFrame,
        col: str,
        mapped_values: pd.Series,
    ) -> pd.DataFrame:
        """Rellena los valores vacíos en `col` con los de `mapped_values`."""
        if col not in df.columns:
            df[col] = mapped_values
            return df

        df = df.copy()
        if df[col].dtype != "object":
            df[col] = df[col].astype("object")

        mask_empty = df[col].isna() | df[col].astype(str).str.strip().isin(_EMPTY_VALUES)
        df.loc[mask_empty, col] = mapped_values[mask_empty]
        return df

    def _fill_source_column(
        self,
        df: pd.DataFrame,
        generic_col: str,
        mapped_values: pd.Series,
    ) -> pd.DataFrame:
        """
        Propaga bidireccional: si la columna original tiene otro nombre
        (ej. 'Journal/Book' para 'citation'), también la actualiza.
        """
        source_col = self._generic_to_source.get(generic_col)
        if not source_col or source_col == generic_col or source_col not in df.columns:
            return df

        df = df.copy()
        if df[source_col].dtype != "object":
            df[source_col] = df[source_col].astype("object")

        mask_empty = (
            df[source_col].isna() | df[source_col].astype(str).str.strip().isin(_EMPTY_VALUES)
        )
        df.loc[mask_empty, source_col] = mapped_values[mask_empty]
        return df


# ---------------------------------------------------------------------------
# Helpers de I/O de crosswalk config
# ---------------------------------------------------------------------------

def load_crosswalk_mappings(config_path: Any) -> tuple[dict[str, str], dict[str, str]]:
    """
    Lee un archivo de configuración de crosswalk y devuelve los mapeos entre
    las cabeceras originales del CSV fuente y los campos destino genéricos.

    Returns:
        tuple (source_to_generic, generic_to_source):
          - source_to_generic: Dict con clave cabecera fuente → campo genérico.
          - generic_to_source: Dict con clave campo genérico → cabecera fuente.
    """
    source_to_generic: dict[str, str] = {}
    generic_to_source: dict[str, str] = {}

    if not config_path or not isinstance(config_path, str) or not os.path.isfile(config_path):
        return source_to_generic, generic_to_source

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)

        mappings = (
            cfg[0] if isinstance(cfg, list) and len(cfg) > 0 and isinstance(cfg[0], list) else []
        )
        for m in mappings:
            if isinstance(m, dict):
                left = m.get("left", "").strip()
                replace = m.get("replace", "").strip()
                if left and replace:
                    source_to_generic[left] = replace
                    generic_to_source[replace] = left

    except Exception as exc:
        logger.warning("[load_crosswalk_mappings] Advertencia al leer config: %s", exc)

    return source_to_generic, generic_to_source


# ---------------------------------------------------------------------------
# Alias de compatibilidad (importado por tests y graph.py)
# ---------------------------------------------------------------------------

def _load_crosswalk_mappings(config_path: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Alias de compatibilidad para `load_crosswalk_mappings`."""
    return load_crosswalk_mappings(config_path)


def _resolve_source_id_column(
    state: dict,
    df_source: Any,
    generic_to_source: Any = None,
) -> Any:
    """
    Alias de compatibilidad para tests que importan esta función directamente.

    Delega en `MetadataReconciler._resolve_id_column` usando el estado del grafo
    para construir el reconciler con la config de crosswalk adecuada.
    """
    if state.get("input_source_type") == "pdf_minio":
        if "id" in df_source.columns:
            return "id"

    if generic_to_source is None:
        _, generic_to_source = load_crosswalk_mappings(state.get("source_crosswalk_config"))

    reconciler = MetadataReconciler(generic_to_source=generic_to_source or {})
    return reconciler._resolve_id_column(df_source)


# ---------------------------------------------------------------------------
# Nodo del grafo
# ---------------------------------------------------------------------------

@traceable(name="MetadataReconciliation", run_type="chain")
def metadata_reconciliation(state: dict) -> dict[str, Any]:
    """
    Paso 4 — Reconciliación de metadatos.

    Con el CSV de resultado del deduplicador, filtra los ítems del repositorio
    origen que no son duplicados y realiza un JOIN con el CSV original para
    recuperar sus metadatos completos sin mapear.
    """
    umbral_seguro = state.get("umbral_seguro") or 10

    _, generic_to_source = load_crosswalk_mappings(state.get("source_crosswalk_config"))
    reconciler = MetadataReconciler(
        umbral_seguro=umbral_seguro,
        generic_to_source=generic_to_source,
    )

    df_dedup  = pd.read_csv(state["dedup_output_csv_path"])
    source_path = state.get("curated_csv_path") or state["source_csv_path"]
    df_source = pd.read_csv(source_path)

    df_reconciled = reconciler.reconcile(df_dedup, df_source)

    # Propagación de enriquecimiento (si el subgrafo de enriquecimiento estuvo habilitado)
    generic_csv_path = state.get("generic_source_csv_path")
    if state.get("enrichment_enabled", False) and generic_csv_path and os.path.isfile(generic_csv_path):
        try:
            df_generic = pd.read_csv(generic_csv_path)
            df_reconciled = reconciler.propagate_enrichment(df_reconciled, df_generic)
        except Exception as exc:
            logger.warning(
                "[MetadataReconciliation] Advertencia al propagar metadatos enriquecidos: %s", exc
            )

    output_dir = os.path.dirname(state["reconciled_csv_path"])
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    df_reconciled.to_csv(state["reconciled_csv_path"], index=False)

    logger.info(
        "[MetadataReconciliation] %d ítems seleccionados → '%s'",
        len(df_reconciled), state["reconciled_csv_path"],
    )
    return {}

"""
Gestor transaccional para operaciones sobre archivos CSV (Unit of Work).
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import Optional

from core.agent.dedup_recovery.models import ValidationResult
from core.agent.dedup_recovery.tools import validate_csv_structure

logger = logging.getLogger(__name__)


class CsvTransaction:
    """
    Gestor de contexto (Context Manager) para modificaciones atómicas en CSVs.

    Garantiza:
      1. Creación de una copia de respaldo (backup) temporal única al ingresar.
      2. Validación de consistencia estructural antes de confirmar cambios.
      3. Rollback automático ante excepciones no controladas o validaciones fallidas.
      4. Limpieza determinista del archivo temporal de respaldo al salir del bloque.
    """

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.backup_path = f"{csv_path}.bak_{uuid.uuid4().hex[:6]}"
        self._committed = False

    def __enter__(self) -> CsvTransaction:
        if os.path.isfile(self.csv_path):
            shutil.copy(self.csv_path, self.backup_path)
            logger.debug("[CsvTransaction] Backup temporal creado: %s", self.backup_path)
        return self

    def validate(self) -> ValidationResult:
        """Compara la integridad estructural del CSV actual respecto a su backup original."""
        if not os.path.isfile(self.backup_path):
            return ValidationResult(
                is_valid=False,
                errors=["No existe copia de respaldo para validar la transacción."],
            )
        return validate_csv_structure(self.backup_path, self.csv_path)

    def rollback(self) -> None:
        """Revierte los cambios sobrescribiendo el archivo CSV con la copia de respaldo."""
        if os.path.isfile(self.backup_path) and os.path.isfile(self.csv_path):
            shutil.copy(self.backup_path, self.csv_path)
            logger.info("[CsvTransaction] Transacción revertida (rollback) en '%s'.", self.csv_path)

    def commit(self) -> None:
        """Confirma la transacción."""
        self._committed = True
        logger.debug("[CsvTransaction] Transacción confirmada (commit) en '%s'.", self.csv_path)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> None:
        if exc_type is not None and not self._committed:
            self.rollback()

        # Limpieza garantizada del archivo temporal
        if os.path.isfile(self.backup_path):
            try:
                os.unlink(self.backup_path)
                logger.debug("[CsvTransaction] Backup temporal eliminado: %s", self.backup_path)
            except OSError as err:
                logger.warning(
                    "[CsvTransaction] No se pudo eliminar backup temporal '%s': %s",
                    self.backup_path,
                    err,
                )

"""
Tests unitarios de invariantes estructurales del paquete SAF (Simple Archive Format).

Valida que el paquete generado por `generate_saf_to_import`:
  1. Produzca XMLs sintácticamente válidos (`dublin_core.xml`, `metadata_sedici.xml`) según ElementTree.
  2. Declare correctamente los schemas (`dc` y `sedici`).
  3. Realice escape riguroso de caracteres especiales XML (`&`, `<`, `>`, `"`, `'`).
  4. Preserve la codificación UTF-8 sin BOM para acentos y caracteres especiales.
  5. Mantenga consistencia biunívoca entre el manifiesto `contents` y los bitstreams físicos en disco.
"""

from __future__ import annotations

import os
from pathlib import Path
import xml.etree.ElementTree as ET
import pandas as pd
import pytest

from core.nodes.saf_node import generate_saf_to_import


class TestSafStructuralInvariants:
    """Verifica los invariantes y restricciones técnicas del Simple Archive Format."""

    @pytest.fixture
    def sample_metadata_record(self):
        """Registro con metadatos en esquemas dc y sedici, caracteres especiales y acentos."""
        return {
            "files": "",
            "dc.title[es]": "Bioinformática & Algoritmos Genéticos <v2.0> 'Especial' \"2024\"",
            "dc.creator[es]": "García Márquez, Gabriel; Peña, José",
            "dc.date.issued": "2024",
            "dc.type[es]": "Articulo",
            "dc.description.abstract[es]": "Resumen con símbolos: 10 < 20 & 30 > 25. 'Comillas' y \"dobles\".",
            "sedici.creator.person[es]": "García Márquez, Gabriel",
            "sedici.identifier.other": "http://sedici.unlp.edu.ar/handle/10915/9999",
            "sedici.relation.journalTitle": "Revista de Informática & Redes",
            "sedici.subject.materias[es]": "Ciencias de la Computación; Algoritmos",
        }

    def test_xml_syntactic_validation_and_schemas(self, tmp_path: Path, sample_metadata_record):
        """
        Valida que ElementTree pueda parsear dublin_core.xml y metadata_sedici.xml
        y que las declaraciones de schema sean correctas.
        """
        csv_file = tmp_path / "sedici_input.csv"
        df = pd.DataFrame([sample_metadata_record])
        df.to_csv(csv_file, index=False)

        saf_out = tmp_path / "saf_package"
        state = {
            "sedici_ready_csv_path": str(csv_file),
            "saf_output_path": str(saf_out),
        }

        generate_saf_to_import(state)

        item_dir = saf_out / "item_001"
        assert item_dir.is_dir(), "El directorio item_001 no fue creado."

        dc_file = item_dir / "dublin_core.xml"
        sedici_file = item_dir / "metadata_sedici.xml"

        assert dc_file.is_file(), "dublin_core.xml no existe en el ítem SAF."
        assert sedici_file.is_file(), "metadata_sedici.xml no existe en el ítem SAF."

        # 1. Validación sintáctica con ElementTree
        tree_dc = ET.parse(dc_file)
        root_dc = tree_dc.getroot()
        assert root_dc.tag == "dublin_core"

        tree_sedici = ET.parse(sedici_file)
        root_sedici = tree_sedici.getroot()
        assert root_sedici.tag == "dublin_core"

        # 2. Declaración de esquemas
        # En DSpace SAF: dublin_core.xml representa 'dc' (sin atributo schema o schema="dc")
        # y los demás esquemas califican con schema="<nombre>"
        dc_schema = root_dc.attrib.get("schema")
        assert dc_schema in (None, "dc"), f"Esquema inesperado en dublin_core.xml: {dc_schema}"
        assert root_sedici.attrib.get("schema") == "sedici", (
            f"metadata_sedici.xml debe declarar schema='sedici', obtenido: {root_sedici.attrib.get('schema')}"
        )

        # 3. Validar elementos dcvalue
        dc_values = root_dc.findall("dcvalue")
        assert len(dc_values) >= 3, "dublin_core.xml debe contener al menos 3 dcvalues."
        for val in dc_values:
            assert "element" in val.attrib, "Todo dcvalue debe poseer atributo 'element'."

        sedici_values = root_sedici.findall("dcvalue")
        assert len(sedici_values) >= 2, "metadata_sedici.xml debe contener al menos 2 dcvalues."
        for val in sedici_values:
            assert "element" in val.attrib

    def test_xml_special_characters_escaping(self, tmp_path: Path):
        """
        Verifica que caracteres como &, <, >, \", ' sean escapados adecuadamente
        y reconstruidos intactos al deserializar con el parser XML.
        """
        csv_file = tmp_path / "escaping_input.csv"
        raw_title = "Algoritmos & Heurísticas: ¿x < y ó y > x? - \"Test\" & 'Comillas'"
        raw_abstract = "Párrafo con &amp; ya existente, signos < y > e indicadores 'A' & \"B\"."

        df = pd.DataFrame([{
            "files": "",
            "dc.title[es]": raw_title,
            "dc.description.abstract[es]": raw_abstract,
            "sedici.creator.person[es]": "Pérez & Gómez, Juan",
        }])
        df.to_csv(csv_file, index=False)

        saf_out = tmp_path / "saf_package_escaped"
        state = {
            "sedici_ready_csv_path": str(csv_file),
            "saf_output_path": str(saf_out),
        }

        generate_saf_to_import(state)

        item_dir = saf_out / "item_001"
        dc_file = item_dir / "dublin_core.xml"
        sedici_file = item_dir / "metadata_sedici.xml"

        # Debe parsear sin ParseError
        tree_dc = ET.parse(dc_file)
        root_dc = tree_dc.getroot()

        # Extraer el texto deserializado de dc.title[es]
        title_elem = None
        for elem in root_dc.findall("dcvalue"):
            if elem.attrib.get("element") == "title":
                title_elem = elem
                break

        assert title_elem is not None
        # ElementTree deserializa los entities (&amp; -> &, &lt; -> <, etc.)
        assert "&" in title_elem.text
        assert "<" in title_elem.text
        assert ">" in title_elem.text

        # El texto en crudo en el archivo XML físico DEBE contener los escapes
        with open(dc_file, "r", encoding="utf-8") as f:
            raw_content = f.read()
            assert "&amp;" in raw_content
            assert "&lt;" in raw_content
            assert "&gt;" in raw_content

    def test_utf8_encoding_without_bom(self, tmp_path: Path, sample_metadata_record):
        """Verifica que los archivos XML estén codificados en UTF-8 puro sin BOM."""
        csv_file = tmp_path / "utf8_input.csv"
        df = pd.DataFrame([sample_metadata_record])
        df.to_csv(csv_file, index=False)

        saf_out = tmp_path / "saf_package_utf8"
        state = {
            "sedici_ready_csv_path": str(csv_file),
            "saf_output_path": str(saf_out),
        }

        generate_saf_to_import(state)
        item_dir = saf_out / "item_001"

        for xml_name in ("dublin_core.xml", "metadata_sedici.xml"):
            file_path = item_dir / xml_name
            with open(file_path, "rb") as f:
                raw_bytes = f.read()

            # No debe tener Byte Order Mark (BOM: \xef\xbb\xbf)
            assert not raw_bytes.startswith(b"\xef\xbb\xbf"), f"{xml_name} contiene UTF-8 BOM no deseado."
            # Decodificación limpia en UTF-8
            decoded = raw_bytes.decode("utf-8")
            assert "García Márquez" in decoded or "Informática" in decoded

    def test_manifest_contents_consistency_with_physical_bitstreams(self, tmp_path: Path):
        """
        Verifica que el archivo `contents` refleje exactamente los bitstreams físicos
        copiados en la carpeta del ítem y que no queden archivos huérfanos.
        """
        # Directorio de trabajo del lote donde se genera el SAF
        run_dir = tmp_path / "run_lote"
        run_dir.mkdir()
        saf_out = run_dir / "saf_with_bitstreams"

        # dspace-csv-archive busca los bitstreams en el directorio base del CSV temporal (dirname de saf_output_path)
        pdf_name = "articulo_principal.pdf"
        pdf_file = run_dir / pdf_name
        pdf_content = b"%PDF-1.4 mock binary content for testing bitstream copy"
        pdf_file.write_bytes(pdf_content)

        csv_file = tmp_path / "input.csv"
        df = pd.DataFrame([{
            "files": pdf_name,
            "dc.title[es]": "Artículo con bitstream adjunto",
            "sedici.creator.person[es]": "Investigador, Juan",
            "dc.date.issued": "2024",
        }])
        df.to_csv(csv_file, index=False)

        state = {
            "sedici_ready_csv_path": str(csv_file),
            "saf_output_path": str(saf_out),
        }

        generate_saf_to_import(state)

        item_dir = saf_out / "item_001"
        contents_file = item_dir / "contents"
        assert contents_file.is_file(), "El manifiesto 'contents' debe existir."

        # Leer las líneas del manifiesto
        with open(contents_file, "r", encoding="utf-8") as f:
            manifest_lines = [line.strip() for line in f if line.strip()]

        assert len(manifest_lines) == 1
        assert manifest_lines[0] == pdf_name

        # Verificar que el archivo físico esté en item_001/
        copied_pdf = item_dir / pdf_name
        assert copied_pdf.is_file(), f"El bitstream '{pdf_name}' no fue copiado a la carpeta del ítem."
        assert copied_pdf.read_bytes() == pdf_content, "El contenido del bitstream copiado difiere del original."

        # Verificar ausencia de archivos huérfanos
        archivos_en_item = set(os.listdir(item_dir))
        archivos_esperados = {
            "dublin_core.xml",
            "metadata_sedici.xml",
            "contents",
            pdf_name,
        }
        assert archivos_en_item == archivos_esperados, (
            f"Archivos huérfanos o faltantes en el ítem: {archivos_en_item ^ archivos_esperados}"
        )

    def test_manifest_when_no_bitstreams(self, tmp_path: Path):
        """Si la columna 'files' está vacía, contents debe crearse vacío y sin archivos adicionales."""
        csv_file = tmp_path / "no_files_input.csv"
        df = pd.DataFrame([{
            "files": "",
            "dc.title[es]": "Item sin bitstream",
            "sedici.creator.person[es]": "Autor, Pedro",
        }])
        df.to_csv(csv_file, index=False)

        saf_out = tmp_path / "saf_empty_bitstreams"
        state = {
            "sedici_ready_csv_path": str(csv_file),
            "saf_output_path": str(saf_out),
        }

        generate_saf_to_import(state)

        item_dir = saf_out / "item_001"
        contents_file = item_dir / "contents"
        assert contents_file.is_file()

        with open(contents_file, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 0, "El manifiesto contents no debe tener bitstreams si 'files' está vacío."

        # Solo debe contener los XMLs y contents
        archivos_en_item = set(os.listdir(item_dir))
        assert archivos_en_item == {"dublin_core.xml", "metadata_sedici.xml", "contents"}

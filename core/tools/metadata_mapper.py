import json
import csv
import sys
from typing import List, Dict, Any
from langchain_core.tools import tool

from core.scripts.crosswalk.crosswalk import Crosswalk
from core.scripts.crosswalk.csv_handler import CsvHandler
from core.scripts.crosswalk.crosswalk_context import CrosswalkContext

# Incrementar el límite de tamaño por si los CSVs son grandes
csv.field_size_limit(sys.maxsize)

@tool
def transform_metadata_csv(csv_input_path: str, config_json_path: str, csv_output_path: str) -> str:
    """
    Transforma un archivo CSV de metadatos de origen a un CSV de destino mapeado 
    utilizando un archivo de configuración JSON (Crosswalk).
    
    Args:
        csv_input_path: Ruta al archivo CSV original (ej. 'input.csv').
        config_json_path: Ruta al archivo de configuración de mapeo JSON (ej. 'crosswalk/configs/configoaidc.json').
        csv_output_path: Ruta donde se guardará el archivo CSV resultante.
        
    Returns:
        Un mensaje de éxito indicando dónde se guardó el resultado o el error ocurrido.
    """
    try:
        # 1. Leer la configuración JSON
        with open(config_json_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 2. Inicializar el contexto del mapeo
        context = CrosswalkContext(config)
        
        # 3. Leer el CSV origen y convertirlo a una lista de diccionarios
        handler = CsvHandler()
        csv_list = handler.csv_to_list(csv_input_path, context.file_delimiter)
        
        if not csv_list:
            return "Error: El archivo CSV de entrada está vacío o no se pudo leer."
        
        # 4. Realizar la transformación
        crosswalk = Crosswalk(context)
        new_csv_list = crosswalk.transform(csv_list)
        
        # 5. Guardar el nuevo CSV en el destino solicitado
        handler.list_to_csv(new_csv_list, csv_output_path)
        
        return f"Éxito: El mapeo se realizó correctamente. Archivo guardado en {csv_output_path}"
        
    except Exception as e:
        return f"Error durante la ejecución del crosswalk: {repr(e)}"   
        
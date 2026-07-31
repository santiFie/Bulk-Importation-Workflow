import sys
from crosswalk import *
from csv_handler import *
from crosswalk_context import *

csv_path = sys.argv[1]
config_json_path = sys.argv[2]
destination_path = sys.argv[3]

print("Subiendo los csv ...")

def read_json(json_path):
        #Read JSON data into the datastore variable
        if json_path:
            with open(json_path, 'r') as f:
                json_file = json.load(f)
        return json_file

csv.field_size_limit(sys.maxsize)

config = read_json(config_json_path)
context = CrosswalkContext(config)
csv_list = CsvHandler().csv_to_list(csv_path,context.file_delimiter)

print("Realizando el mapeo ...")

crosswalk = Crosswalk(context)
new_csv_list = crosswalk.transform(csv_list)

print("Guardando el csv generado ...")

CsvHandler().list_to_csv(new_csv_list,destination_path)

#REVISAR Unidades Academicas Ejecutoras y Coordinadores y participantes etc.
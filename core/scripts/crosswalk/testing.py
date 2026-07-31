from crosswalk_context import *
import json
import sys

config_json_path = sys.argv[1]

print("Subiendo los csv ...")

def read_json(json_path):
        #Read JSON data into the datastore variable
        if json_path:
            with open(json_path, 'r') as f:
                json_file = json.load(f)
        return json_file

config = read_json(config_json_path)
context = CrosswalkContext(config)

print(context.getOriginalSeparator())
print(context.getReplaceSeparator())

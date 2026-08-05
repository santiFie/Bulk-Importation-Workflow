import csv
import json
import html

class CsvHandler():

    def csv_to_list(self,csv_path,file_delimiter):
        doc_list = []
        decoded_file = open(csv_path, 'r', encoding="utf-8-sig")
        reader = csv.DictReader(decoded_file,delimiter=file_delimiter)
        for row in reader:
            doc_list.append(row)
        return doc_list

    def list_to_csv(self,csv_file,destination_path):
        with open(destination_path, 'w') as f:
            # Assuming that all dictionaries in the list have the same keys.
            headers = [k for k, v in csv_file[0].items()]
            csv_data = [headers]

            for d in csv_file:
                csv_data.append([d[h] for h in headers])

            writer = csv.writer(f)
            writer.writerows(csv_data)

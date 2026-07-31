import re

csv_path = "tests/data/SearchResults.csv"

def parse_csv_line(line):
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_quotes:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    current.append('"')
                    i += 1
                else:
                    in_quotes = False
            else:
                current.append(c)
        else:
            if c == '"':
                in_quotes = True
            elif c == ',':
                fields.append("".join(current))
                current = []
            else:
                current.append(c)
        i += 1
    fields.append("".join(current))
    return fields


def write_csv_line(fields):
    out = []
    for f in fields:
        if "," in f or '"' in f or "\n" in f:
            out.append('"' + f.replace('"', '""') + '"')
        else:
            out.append(f)
    return ",".join(out) + "\n"


with open(csv_path, encoding="utf-8") as f:
    raw_lines = f.readlines()

header = parse_csv_line(raw_lines[0].rstrip("\r\n"))
parsed_rows = [parse_csv_line(line.rstrip("\r\n")) for line in raw_lines[1:]]

header_field_count = len(header)

for row in parsed_rows:
    doi_idx = None
    for j, field in enumerate(row):
        if field.startswith("10.") and "/" in field:
            doi_idx = j
            break

    if doi_idx is not None and doi_idx + 1 < len(row):
        authors_idx = doi_idx + 1
        authors_str = row[authors_idx]
        authors = re.split(r"(?<=[a-z])(?=[A-Z]\.)", authors_str)
        authors = [a for a in authors if a]
        row[authors_idx] = "".join(authors[:6])

    # If there's a field mismatch due to unquoted commas in the title,
    # merge extra leading fields back into the first field
    while len(row) > header_field_count:
        row[0] = row[0] + "," + row.pop(1)

with open(csv_path, "w", encoding="utf-8") as f:
    f.write(write_csv_line(header))
    for row in parsed_rows:
        f.write(write_csv_line(row))

print("CSV actualizado correctamente.")

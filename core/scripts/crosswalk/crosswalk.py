from crosswalk_context import * 
import html
import fnmatch
import re

class Crosswalk():
    
    context = None

    def __init__(self,context):
        self.context = context


    def deleteAbsentCols(self,item,cols_to_mantain):
        try:
            keys_to_delete = []
            for key in item.keys():
                ok = False
                for col in cols_to_mantain:
                    if fnmatch.fnmatch(key,col) or key==col:
                        ok = True
                if not ok:
                    keys_to_delete.append(key)
            for key_to_delete in keys_to_delete:
                if key_to_delete in item.keys():
                    del item[key_to_delete]
        except Exception as e:
            print(repr(e))            

    def applyFilters(self,item,setting):
        #Filtros
        filters = setting.getFilters()
        if filters:
            for string_filter in filters:
                original_string = str(item[setting.replace_col])
                if string_filter == 'trim':
                    original_string = original_string.strip(' ').strip(',')
                if string_filter == 'lowercase':
                    original_string = original_string.lower()
                item[setting.replace_col] = original_string

    def changeColName(self,item,setting):
        col_value = item.pop(setting.left_col)
        try:
            separator_regex = self.context.getSeparatorRegex()
            if separator_regex:
                col_value = re.sub(separator_regex, self.context.getReplaceSeparator(), col_value)
            else:
                col_value = col_value.replace(self.context.getOriginalSeparator(),self.context.getReplaceSeparator())
        except:
            pass
        item[setting.replace_col] = html.unescape(col_value)

    def combineColsIntoOne(self,item,setting):
        new_col_value = ""
        replace_separator = self.context.getReplaceSeparator()
        original_separator = self.context.getOriginalSeparator()
        separator_regex = self.context.getSeparatorRegex()
        for col in setting.splitCol():
            if col not in item:
                continue
            new_col_value = new_col_value + replace_separator + str(item[col])
            del item[col]
        if separator_regex:
            new_col_value = re.sub(separator_regex, replace_separator, new_col_value)
        elif original_separator in new_col_value:
            new_col_value = new_col_value.replace(original_separator,replace_separator)
        item[setting.replace_col] = html.unescape(new_col_value.strip(replace_separator))
    
    def combineWildcardCol(self,item,setting):
        new_col_value = ""
        replace_separator = self.context.getReplaceSeparator()
        original_separator = self.context.getOriginalSeparator()
        separator_regex = self.context.getSeparatorRegex()
        for col in list(item.keys()):
            if fnmatch.fnmatch(col,setting.left_col):
                if item[col] != "":
                    new_col_value = new_col_value + replace_separator + str(item[col])
                del item[col]
        if separator_regex:
            new_col_value = re.sub(separator_regex, replace_separator, new_col_value)
        elif original_separator in new_col_value:
            new_col_value = new_col_value.replace(original_separator,replace_separator)
        item[setting.replace_col] = html.unescape(new_col_value.strip(replace_separator))
    
    def combineWildcardCols(self,item,setting):
        new_col_value = ""
        replace_separator = self.context.getReplaceSeparator()
        original_separator = self.context.getOriginalSeparator()
        separator_regex = self.context.getSeparatorRegex()
        for col in setting.splitCol():
            for sub_col in list(item.keys()):
                if fnmatch.fnmatch(sub_col,col):
                    if item[sub_col] != "":
                        new_col_value = new_col_value + replace_separator + str(item[sub_col])
                    del item[sub_col]
        if separator_regex:
            new_col_value = re.sub(separator_regex, replace_separator, new_col_value)
        elif original_separator in new_col_value:
            new_col_value = new_col_value.replace(original_separator,replace_separator)
        item[setting.replace_col] = html.unescape(new_col_value.strip(replace_separator))

    def transform(self,csv_file):
        #Por cada recurso en el csv
        new_csv = []
        cols_to_mantain = self.context.calcColsToMantain()
        for index, item in enumerate(csv_file):
            append = True
            self.deleteAbsentCols(item,cols_to_mantain)
            for setting in self.context.getSettings():
                if setting.isLeftColMultiple() and setting.isLeftColWildcard():
                    self.combineWildcardCols(item,setting)
                elif setting.isLeftColMultiple():
                    self.combineColsIntoOne(item,setting)
                elif setting.isLeftColWildcard():
                    self.combineWildcardCol(item,setting)
                else:
                    if setting.isLeftEmpty() and setting.isRequired():
                        append = False
                        break
                    elif setting.isLeftEmpty():
                        item[setting.left_col] = setting.default()
                    self.changeColName(item,setting)
                self.applyFilters(item,setting)
            if append:
                new_csv.append(item)
            print(index, len(csv_file))
        return new_csv


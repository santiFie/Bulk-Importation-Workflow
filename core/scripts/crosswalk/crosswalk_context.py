from typing import Optional


class Setting():

    left_col = ""
    replace_col = ""
    default = ""
    required = False
    filters = []

    def __init__(self,setting):
        self.left_col = setting['left']
        self.replace_col = setting['replace']
        if "default" in setting.keys():
            self.default = setting['default']
        self.required = setting['required']
        if 'filter' in setting.keys():
            self.filters = setting['filter'].split('|')
    
    def isLeftColWildcard(self):
        return '*' in self.left_col

    def isLeftColMultiple(self):
        return '+' in self.left_col
    
    def splitCol(self):
        return self.left_col.split('+')
    
    def getFilters(self):
        return self.filters
    
    def isLeftEmpty(self):
        return self.left_col == ''

    def isRequired(self):
        return self.required
    
    def getDefault(self):
        return self.default
    

class CrosswalkContext():

    replace_separator = "|"
    original_separator = "|"
    file_delimiter = ","
    separator_regex: Optional[str] = None
    setting_list = []

    def __init__(self,config):

        self.replace_separator = config[1]['replace_separator']
        self.original_separator = config[1]['original_separator']
        self.file_delimiter = config[1]['file_delimiter']
        # Optional: a Python re.sub()-compatible pattern that replaces original_separator
        # when the multi-value delimiter is too complex for a plain string match.
        self.separator_regex = config[1].get('separator_regex', None)
        for setting in config[0]:
            self.setting_list.append(Setting(setting))
    
    def getSettings(self):
        return self.setting_list
    
    def getReplaceSeparator(self):
        return self.replace_separator

    def getOriginalSeparator(self):
        return self.original_separator

    def getSeparatorRegex(self) -> Optional[str]:
        """Returns the regex pattern used to split multi-value fields, or None."""
        return self.separator_regex

    def getFileDelimiter(self):
        return self.file_delimiter
    
    def calcColsToMantain(self):
        mantain_cols = []
        for setting in self.setting_list:
            if '+' in setting.left_col:
                mantain_cols += setting.left_col.split('+')
            else:
                mantain_cols.append(setting.left_col)
        return mantain_cols
            


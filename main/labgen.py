import re


class Variable:

    def __init__(self, filename, table_name, plot_name=None, column_names=(), axis_names=(), pyplot_code="",
                 gnuplot_code=""):
        self.filename = filename
        self.table_name = table_name
        self.plot_name = plot_name
        self.column_names = column_names
        self.axis_names = axis_names
        self.pyplot_code = pyplot_code
        self.gnuplot_code = gnuplot_code


class Command:

    def assemble_output(self, variables)->str:
        pass


class Parser:

    skip_spans = []
    insertion_spans = []
    commands = []
    variables = []
    string = ""

    def __init__(self):
        pass

    def parse_vars(self, string)->list: # fills skip spans
        variables = []
        return variables

    def parse_commands(self, string)->list: # fills skip spans, insertion spans
        commands = []
        return commands

    def parse_string(self, string):
        self.skip_spans = []
        self.insertion_spans = []
        self.variables = parse_variables(string)
        self.commands = parse_commands(string)



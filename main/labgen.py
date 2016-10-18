import numpy as np
import asteval
import re
import time

from matplotlib import pyplot as pp

def create_variable_pattern(initiating_char, closing_char):
    processed = r"\{initiating_char}\s*(\w*)\s*(\\)?\s*(?(2)([^\n\r]*)|)$(.*?)\{closing_char}".format(
        initiating_char=initiating_char,
        closing_char=closing_char
    )
    return re.compile(processed, re.M | re.U | re.S)

def find_all_properties(clazz: type, prefix="_PROP_"):
    return {getattr(clazz, field_name).name: getattr(clazz, field_name)
            for field_name in dir(clazz) if field_name.startswith(prefix)}

def get_method_arg_names(method):
    return method.__code__.co_varnames[:method.__code__.co_argcount]

def str_dict_kv_per_line(d: dict):
    return "{\n\t" + "\n\t".join(["%s: %s" % (key, d[key]) for key in d.keys()]) + "\n}"

def create_invocation_pattern(initiating_char, param_initiating_char, param_closing_char):
    processed = r"\{initiating_char}([\w_]+)(?:\s*\{param_initiating_char}(.*?)\{param_closing_char}|)".format(
        initiating_char=initiating_char,
        param_initiating_char=param_initiating_char,
        param_closing_char=param_closing_char
    )
    return re.compile(processed, re.M | re.U | re.S)


class LabGenError(Exception):

    def __init__(self, message):
        super().__init__(message)


class RangeObject:
    def __init__(self, start_stop: str):
        self.start, self.stop = None, None
        self.auto_scale = start_stop.strip() == "autoscale"
        if not self.auto_scale:
            self.start, self.stop = map(float, start_stop.split(";"))

    def __str__(self):
        r = "auto"
        if not self.auto_scale:
            r = "start=%f; stop=%f" % (self.start, self.stop)
        return "Range<%s>" % (r,)


class Property:

    def __init__(self, name, type_, object_type=None, default=None, single_value=True):
        self.default = default
        self.type = type_
        self.object_type = object_type
        self.name = name
        self.single_value = single_value

    def __str__(self):
        return "Property<name=\"%s\"; type=%s; default=\"%s\"; object_type=\"%s\"; single_value=\"%s\">" % (
            self.name, self.type, self.default, str(self.object_type), self.single_value)


# noinspection PyCallingNonCallable
class Builder:

    def __init__(self, possible_properties: dict, converters: dict):
        self.possible_properties = possible_properties
        self.converters = converters
        self.metadata = {}
        self.object_builder = None

    def process_value(self, type_name, value):
        return self.get_converter_for_type(type_name)(value.strip())

    def get_converter_for_type(self, type_name: str):
        converter = self.converters.get(type_name, None)
        if converter is None:
            raise LabGenError("Converter for type %s not found" % (type_name,))
        return converter

    def put_into_object_builder(self, key: str, value: str):
        if self.object_builder is None:
            raise LabGenError("No object is currently being built: ." + key + "=" + value)
        self.object_builder.put(key, value)

    def flush_object_builder(self):
        if self.object_builder is None:
            return
        self.put_processed(self.object_builder.property, self.object_builder.build())
        self.object_builder = None

    def put(self, key: str, value: str):
        prop = self.possible_properties[key]
        if prop.type == DatafileVariable.METADATA_VALUE_TYPE_BUILDER:
            self.flush_object_builder()
            self.object_builder = ObjectBuilder(self.process_value(DatafileVariable.METADATA_VALUE_TYPE_STR, value),
                                                prop,
                                                self.converters)
        else:
            self.put_processed(prop, self.process_value(prop.type, value))

    def put_processed(self, prop: Property, processed_value: object):
        self.metadata[prop.name] = processed_value if prop.single_value \
            else (self.metadata.get(prop.name, []) + [processed_value, ])

    def build(self):
        # fill in default values
        if self.object_builder:
            self.flush_object_builder()
        for prop in self.possible_properties.values():
            if not (prop.name in self.metadata.keys()) \
                    and prop.type != DatafileVariable.METADATA_VALUE_TYPE_BUILDER \
                    and not prop.sub_property:
                if prop.default is None:
                    raise LabGenError("Value is not presented for required property %s" % (prop.name,))
                self.put(prop, prop.default)
        return self.metadata


class ObjectBuilder(Builder):

    def __init__(self, object_name: str, builder_property: Property, converters: dict):
        super().__init__(find_all_properties(builder_property.object_type), converters)
        self.obj_name = object_name
        self.property = builder_property

    def build(self):
        return self.property.object_type(self.obj_name, super().build())


class DatafileVariable:

    METADATA_VALUE_TYPE_NUMBER = "number"
    METADATA_VALUE_TYPE_LIST = "list"
    METADATA_VALUE_TYPE_BUILDER = "builder"
    METADATA_VALUE_TYPE_STR = "str"
    METADATA_VALUE_TYPE_RANGE = "range"

    CONVERTERS = {
        METADATA_VALUE_TYPE_LIST: lambda s: s.strip().split(";"),
        METADATA_VALUE_TYPE_NUMBER: lambda s: float(s),
        METADATA_VALUE_TYPE_STR: lambda s: s.strip(),
        METADATA_VALUE_TYPE_RANGE: RangeObject,
    }

    METADATA_PATTERN = \
        re.compile(r"^(\.?)(\w+)\s*=(?:$|\s*(.*))",
                   re.M | re.U)

    def __init__(self, name, human_readable_name, metadata, properties):
        self.name = name
        self.human_readable_name = human_readable_name
        self.properties = properties
        self.metadata = None
        self.parse_metadata(metadata)
        self.label = "label_" + self.name

    def parse_metadata(self, string):
        builder, last_were_object = Builder(self.properties, DatafileVariable.CONVERTERS), False
        for match in DatafileVariable.METADATA_PATTERN.finditer(string):
            is_building_object_property, key, value = bool(match.group(1)), match.group(2), match.group(3) or ""
            if is_building_object_property:
                builder.put_into_object_builder(key, value)
            else:
                if last_were_object:
                    builder.flush_object_builder()
                builder.put(key, value)
        self.metadata = builder.build()


class Table(DatafileVariable):

    _PROP_COLS = Property("cols", DatafileVariable.METADATA_VALUE_TYPE_LIST)

    DEFINITION_PATTERN = re.compile(r"\^{2}\s*(\w*)\s*(\\)?\s*(?(2)([^\n\r]*))([^\^]*)\^{2}(?:(.*?)(?:\r*?\n){2}|)",
                                    re.S)

    def __init__(self, name, human_readable_name, metadata, body):
        super().__init__(name, human_readable_name, metadata, find_all_properties(Table))
        self.body = self.parse_table_body(body)

    def parse_table_body(self, body):
        # transpose here is used to provide convenient usage in plot ASTEVAL exprs
        return np.reshape(np.fromstring(body, dtype=np.float, sep=" "),
                          (body.count("\n") + 1, len(self.metadata["cols"]))).transpose()

    def body_as_one_line_string(self):
        return "[" + ", ".join(["[" + " ".join(map(str, arr)) + "]" for arr in self.body]) + "]"

    def __str__(self):
        return "Table<name=\"%s\"; human_readable_name=\"%s\"; metadata=\"%s\"; body=\"%s\"" % (
            self.name, self.human_readable_name, str(self.metadata), self.body_as_one_line_string()
        )


class Curve:

    _PROP_COLOR = Property("color",
                           DatafileVariable.METADATA_VALUE_TYPE_STR,
                           default="black")
    _PROP_STYLE = Property("style",
                           DatafileVariable.METADATA_VALUE_TYPE_STR,
                           default="lines+points")
    _PROP_X = Property("x",
                       DatafileVariable.METADATA_VALUE_TYPE_STR,
                       default="x")
    _PROP_Y = Property("y",
                       DatafileVariable.METADATA_VALUE_TYPE_STR,
                       default="y")
    _PROP_SCOPE = Property("scope",
                           DatafileVariable.METADATA_VALUE_TYPE_STR,
                           default="")

    def __init__(self, name, metadata):
        self.name = name
        self.metadata = metadata
        # TODO: replace with logging facilities?
        print("Built Curve object with name=\"%s\"; metadata=\"%s\"" % (self.name, str(self.metadata)))

    def __str__(self):
        return "Curve<name=\"%s\"; metadata=\"%s\"" % (
            self.name, str(self.metadata)
        )

    def get_expressions(self):
        return self.metadata[self._PROP_X.name], self.metadata[self._PROP_Y.name], self.metadata[self._PROP_SCOPE.name]

    def get_style(self):
        return self.metadata[self._PROP_STYLE.name]


class Image:

    def __init__(self, dpi, filename, tables_hash):
        self.dpi = dpi
        self.filename = filename
        self.tables_hash = tables_hash

    def __eq__(self, other):
        if type(other) == Image:
            return other.dpi == self.dpi \
                   and other.filename == self.filename \
                   and other.tables_hash == self.tables_hash
        return False


class Plot(DatafileVariable):

    AUTOSCALE = "autoscale"

    DEFINITION = "${2}"
    DEFINITION_PATTERN = create_variable_pattern(DEFINITION, DEFINITION)

    _PROP_AXES = Property("axes",
                          DatafileVariable.METADATA_VALUE_TYPE_LIST,
                          default="x;y")
    _PROP_XRANGE = Property("xrange",
                            DatafileVariable.METADATA_VALUE_TYPE_RANGE,
                            default=AUTOSCALE)
    _PROP_YRANGE = Property("yrange",
                            DatafileVariable.METADATA_VALUE_TYPE_RANGE,
                            default=AUTOSCALE)
    _PROP_CURVE = Property("curve",
                           DatafileVariable.METADATA_VALUE_TYPE_BUILDER,
                           object_type=Curve,
                           single_value=False)

    def __init__(self, name, human_readable_name, metadata, labgen_instance: LabGen):
        super().__init__(name, human_readable_name, metadata, find_all_properties(Plot))
        self.figure_name = "figure_" + self.name
        # needed to access tables
        self.labgen_instance = labgen_instance
        self.images = []

    def produce_image(self, dpi=None):
        """
        Produces an image of this plot and saves it to file inside LabGen output_dir

        :param dpi: not used yet
        :return: tuple: (bool - were image redrawn or not, str - path to image
        """
        path = self.labgen_instance.output_dir + self.figure_name
        img = Image(dpi or "default", path, hash(self.labgen_instance.tables))
        if img in self.images:
            return (False, path)
        # TODO: optimize
        interpreter = asteval.Interpreter({table.name: table.body.transpose() for table in self.labgen_instance.tables})
        curves = self.metadata.get(Plot._PROP_CURVE.name, [])
        curves_pp = []
        for curve in curves:
            x_expr, y_expr, scope = curve.get_expressions()
            interpreter.eval(scope)  # prepare scope
            curves_pp.append(interpreter.eval(x_expr))
            curves_pp.append(interpreter.eval(y_expr))
            curves_pp.append(curve.get_style())
        xrange = self.metadata[self._PROP_XRANGE.name]
        if xrange != Plot.AUTOSCALE:
            pp.xlim(*xrange)
        yrange = self.metadata[self._PROP_YRANGE.name]
        if yrange != Plot.AUTOSCALE:
            pp.ylim(*yrange)
        xlabel, ylabel = self.metadata[self._PROP_AXES.name]
        pp.xlabel(xlabel)
        pp.ylabel(ylabel)
        # TODO: use dpi or smt to specify image size
        pp.plot(*curves_pp)
        pp.savefig(img.filename)
        return (True, path)


class Template:

    # patterns and constants
    DEFINITION = "#{2}"
    PARAM_DEFINITION = "++"
    OPT_DEFINITION = "@@"
    DEFINITION_PATTERN = create_variable_pattern(DEFINITION, DEFINITION)
    PARAM_INTERPOLATION_PATTERN = re.compile("%{2}([\w_]*)", re.U | re.M)
    INVOCATION_PATTERN = create_invocation_pattern("#", "|{2}", "|{2}")

    def __init__(self, name: str, body: str):
        self.name = name
        self.body, self.param_map, self.param_positions, self.opts = self.parse_body(body)
        self._apply_options()

    def _apply_options(self):
        # TODO: refine
        if "wrap-newlines" in self.opts:
            self.body = "\n%s\n" % (self.body,)

    def parse_body(self, string):
        params, body_lines, positions, options = {}, [], {}, []
        params_count = 0
        for line in filter(lambda s: bool(s), string.split("\n")):
            if line.startswith(Template.OPT_DEFINITION):
                options.append(line[len(Template.OPT_DEFINITION):])
            elif line.startswith(Template.PARAM_DEFINITION):
                kv_pair = line[len(Template.PARAM_DEFINITION):].split("=", 1)
                key = kv_pair[0].strip()
                value = None if len(kv_pair) <= 1 else (kv_pair[1].strip() or kv_pair[1])
                params[key] = value
                positions[key] = params_count
                params_count += 1
            else:
                body_lines.append(line)
        return "\n".join(body_lines), params, positions, options

    def interpolate_params(self, substitution):
        def interceptor_func(match):
            param = match.group(1)
            position = self.param_positions.get(param, None)
            if position is None:
                # parameter does not exists
                raise LabGenError("parameter \"%s\" is not defined for template \"%s\"" % (param, self.name))
            # search for value
            for possible_location in (substitution, self.param_map):
                for possible_key in (param, position):
                    value = possible_location.get(possible_key, None)
                    if not (value is None):
                        # if we have found a value, return it
                        return value
            raise LabGenError("no value found for param \"%s\"" % (param,))

        return Template.PARAM_INTERPOLATION_PATTERN.sub(interceptor_func, self.body)

    def __str__(self):
        return "Template<name=\"%s\"; param_map=%s; positions: %s" % (
            self.name, str(self.param_map), str(self.param_positions)
        )


class Command:
    COMMAND_DEF_PREFIX = "cmd_"
    INVOCATION_PATTERN = create_invocation_pattern("@", "|{2}", "|{2}")

    def __init__(self, name, exec_method):
        self.name = name
        self.command_exec_method = exec_method

    def __call__(self, parser, args_dict: dict) -> str:
        positional = []
        i = 0
        for arg_name in get_method_arg_names(self.command_exec_method)[1:]:
            pos_arg = args_dict.get(i, None)
            if pos_arg is None:
                pos_arg = args_dict[arg_name]
            positional.append(pos_arg)
            i += 1
        return self.command_exec_method(parser, *positional,
                                        **dict(map(lambda key: (str(key), args_dict[key]), args_dict.keys())))

    def __str__(self):
        return "Command<name=\"%s\"; positional args=%s>" % (self.name, get_method_arg_names(self.command_exec_method))


# Commands are defined as following:
# 1. Command core function name should start with COMMAND_DEF_PREFIX; the rest of the name will be used as command name
# 2. Command should take LabGen instance as first argument, any count of positional arguments and **kwargs

def cmd_date(parser, **kwargs):
    return time.asctime()

def cmd_labgen_dump(parser, **kwargs):
    return "{{\nHere was invoked labgen_dump command. The purpose of this command is " \
           "to perform a dump on LabGen instance.\n" \
           "Defined templates: \n%s\n" \
           "Defined commands: \n%s\n" \
           "Parser object: %s\n" \
           "labgen_dump kwargs were: %s\n" \
           "result of 'date' command: %s\n}}" % (
               str_dict_kv_per_line(parser.templates),
               str_dict_kv_per_line(COMMAND_DEFINITIONS),
               str(parser),
               str(kwargs),
               cmd_date(parser)
           )

"""
Creates a plot image using pyplot
"""
def cmd_plot(parser, plot_var, **kwargs):
    # TODO : implement
    return "(ref to "+ ")"

"""
Returns table label
"""
def cmd_table_label(parser, table_var, **kwargs):
    return parser.tables[table_var].label

"""
Returns table name
"""
def cmd_table_caption(parser, table_var, **kwargs):
    return parser.tables[table_var].human_readable_name

"""
Generates table body

kwargs: split_each=False, cast_to_int=False, precision=3
"""
def cmd_table_body(parser, table_var, **kwargs):
    table = parser.tables[table_var]
    split_each = kwargs.get("split_each", False)
    precision = int(kwargs.get("precision", 3))
    cast_to_int = bool(kwargs.get("cast_to_int", False))
    header = " & ".join(table.metadata["cols"]) + "\\\\\n\\hline\n"
    return header + ("\n\\hline\n" if split_each else "\n").join(
        [np.array2string(row,
                         separator=" & ",
                         precision=precision,
                         formatter={"all": lambda x: str(x if not cast_to_int else int(x))})[1:-1] # esc np default []
         + r" \\" for row in table.body.transpose()])

"""
Generates full table
"""
def cmd_table(parser, table_var, **kwargs):
    table = parser.tables[table_var]
    return r"""\begin{{table}}[{modifiers}]
        \caption{{{caption}}}
        \label{{{label}}}
            \begin{{center}}
                \begin{{tabular}}{{{columns}}}
                \hline
                {table_body}
                \hline
                \end{{tabular}}
            \end{{center}}
        \end{{table}}""".format(
        modifiers=kwargs.get("modifiers", "h!"),
        caption=table.human_readable_name,
        label=table.label,
        columns=("c|"*len(table.metadata["cols"]))[:-1],
        table_body=cmd_table_body(parser, table_var, **kwargs)
    )


COMMAND_DEFINITIONS = {
    cmd_name: Command(cmd_name, globals()[full_name])
    for cmd_name, full_name in \
    map(
        lambda s: (s[len(Command.COMMAND_DEF_PREFIX):], s),
        filter(
            lambda s: s.startswith(Command.COMMAND_DEF_PREFIX),
            dir()))
    }

class LabGen:

    ARGS_ITEM_PATTERN = re.compile(r"(?:\s*(\w*)\s*=\s*([^|]*)|([^|]+))\|?", re.U | re.M | re.S)
    #                                                    put * here ^ in case of troubles with empty arguments

    def __init__(self, output_dir, template_files: list, data_files: list):
        self.output_dir = output_dir
        self.templates = {}
        LabGen.parse_template_files(*template_files)
        self.tables, self.plots = {}, {}
        LabGen.parse_datafiles(*data_files)

    @staticmethod
    def parse_args(string, strip_values=True):
        kwargs = {}
        position = 0
        for match in LabGen.ARGS_ITEM_PATTERN.finditer(string):
            key = match.group(1)
            if key is None:
                kwargs[position] = match.group(3).strip() if strip_values else match.group(3)
            else:
                kwargs[key] = match.group(2).strip() if strip_values else match.group(2)
            position += 1
        return kwargs

    def parse_template_files(self, *filename_list):
        for filename in filename_list:
            with open(filename, encoding="utf-8") as file:
                for match in Template.DEFINITION_PATTERN.finditer(file.read()):
                    template_name = match.group(1)
                    # TODO: replace with logging facilities?
                    print("Defining template \"%s\"" % (template_name,), end="... ")
                    self.templates[template_name] = Template(template_name, match.group(4))
                    template_params = self.templates[ template_name ].param_map
                    # TODO: replace with logging facilities?
                    print("required args: %s; other args: %s" % (
                        list(filter(lambda k: template_params[k] is None, template_params.keys())),
                        {key: template_params[key] for key in filter(lambda a: not (template_params[a] is None), template_params.keys())}
                    ))

    def parse_datafiles(self, filename_list):
        for filename in filename_list:
            with open(filename, encoding="utf-8") as file:
                string = file.read()
                for match in Table.DEFINITION_PATTERN.finditer(string):
                    name, hr_name, metadata, body = match.group(1), match.group(3), match.group(4), match.group(5)
                    new_table = Table(name, hr_name, metadata.strip(), body.strip())
                    # TODO: replace with logging facilities or remove?
                    print("Created new table variable %s" % (self.tables[name],))
                for match in Plot.DEFINITION_PATTERN.finditer(string):
                    name, hr_name, metadata = match.group(1), match.group(3), match.group(4)
                    self.plots[name] = Plot(name, hr_name, metadata, self)
                    # TODO: replace with logging facilities or remove?
                    print("Created new plot variable %s" % (self.plots[name],))

    def _do_resolve_templates(self, string, outer_templates, recursion_level):
        def interceptor_func(match):
            nonlocal outer_templates
            template_name = match.group(1)
            if outer_templates and template_name == outer_templates[-1]:
                raise LabGenError("Recursive template calls are not allowed. Stack: " + str(outer_templates))
            template = self.templates[template_name]
            substitution = LabGen.parse_args(match.group(2) or "")
            # TODO: replace with logging facilities?
            print(recursion_level*"\t" +
                  "Applying substitution %s in %s invocation" % (
                      str(substitution),
                      "[" + "->".join(outer_templates) + ("->" if outer_templates else "") + template_name + "]"
                  ))
            return self._do_resolve_templates(template.interpolate_params(substitution),
                                              outer_templates + [template_name], recursion_level + 1)
        return Template.INVOCATION_PATTERN.sub(interceptor_func, string)

    def resolve_templates(self, string):
        return self._do_resolve_templates(string, [], 0)

    def invoke_commands(self, string):
        def interceptor_func(match):
            command = COMMAND_DEFINITIONS.get(match.group(1))
            if command is None:
                raise LabGenError("No such command: @\"%s\"" % (match.group(1),))
            arg_dict = LabGen.parse_args(match.group(2) or "")
            # TODO: replace with logging facilities?
            print("invoking command %s with args %s" % (str(command), str(arg_dict)))
            return command(self, arg_dict)
        return Command.INVOCATION_PATTERN.sub(interceptor_func, string)

    def render_string(self, string):
        # TODO: replace with logging facilities?
        print("===== RENDER STAGE 1: RESOLVE TEMPLATES =====")
        string = self.resolve_templates(string)
        # TODO: replace with logging facilities?
        print("===== RENDER STAGE 2: INVOKE COMMANDS =====")
        string = self.invoke_commands(string)
        return string

    def render_file(self, filename, encoding="utf-8"):
        # TODO: replace with logging facilities?
        print("===== STARTED RENDERING FILE %s" % (filename,))
        result = None
        with open(filename, encoding=encoding) as file:
            result = self.render_string(file.read())
        # TODO: replace with logging facilities?
        print("===== COMPLETED RENDERING FILE %s" % (filename,))
        return result

    def get_scope(self):
        return {"output_directory": self.output_dir,
                "templates": self.templates,
                "tables": self.tables,
                "plots": self.plots}

if __name__ == '__main__':
    p = LabGen("../test/", ["../table.lgt", "../tamplateLabs2.0.txt"], ["../datafile.txt"])
    print(p.render_file("../test/test_source2.txt"))


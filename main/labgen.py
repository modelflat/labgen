import numpy as np
import asteval
import re
import time
import random
import os
import sys
import argparse
import logging

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


def remove_non_alphanum(string):
    if not string:
        return ""
    return "".join(filter(lambda ch: ch.isalnum() or ch == "_", string))


def random_str(length):
    gen = hex(random.getrandbits(length * 4))[2:]
    return ("0" * (length - len(gen))) + gen


def generate_label(name: str):
    return "label_" + remove_non_alphanum(name) + "_" + random_str(4)


def flatten_2d_np_array(array, lowest_level_types=(np.array, np.ndarray)):
    res = []
    if type(array) != list:
        return [array, ]
    for element in array:
        if type(element) in lowest_level_types:
            res.append(element)
        else:
            res.extend(flatten_2d_np_array(element))
    return res


def split_ext(path):
    f, e = os.path.splitext(path)
    return f, e[1:]


def list_files(dir_path):
    return [dir_path + os.sep + filename for filename in
            filter(lambda s: not os.path.isdir(dir_path + os.sep + s), os.listdir(dir_path))]


def read_file(filename, encoding):
    with open(filename, encoding=encoding) as file:
        return file.read()


def do_for_path(path, action, recursive=False, encoding="utf-8"):
    """
    Perform a particular action on each file and/or directory in path

    :param path: path
    :param action: callable. receives path as the first parameter, file contents as second
    :param recursive: note: if this set to False, and path is directory, files from directory will still be resolved
    :param encoding: encoding for file opening
    """
    path = os.path.normpath(path)
    if os.path.isdir(path):
        for p in (list_files(path) if not recursive else os.listdir(path)):
            do_for_path(p, action, recursive=recursive, encoding=encoding)
    else:
        filename, ext = split_ext(path)
        action(filename, ext, read_file(path, encoding))


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
                    and prop.type != DatafileVariable.METADATA_VALUE_TYPE_BUILDER:
                if prop.default is None:
                    raise LabGenError("Value is not presented for required property %s" % (prop.name,))
                self.put(prop.name, prop.default)
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
    METADATA_VALUE_TYPE_BOOL = "boolean"

    CONVERTERS = {
        METADATA_VALUE_TYPE_LIST: lambda s: [k.strip() for k in filter(bool, s.split(";"))],
        METADATA_VALUE_TYPE_NUMBER: lambda s: float(s),
        METADATA_VALUE_TYPE_STR: lambda s: s.strip(),
        METADATA_VALUE_TYPE_RANGE: RangeObject,
        METADATA_VALUE_TYPE_BOOL: lambda s: s is not None and s.lower() not in ["false", "f", "0"]
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
        self.label = generate_label(self.name)

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

    def __str__(self):
        return "name=\"%s\"; human_readable_name=\"%s\"; label=%s; metadata=\"%s\"" % (
            self.name, self.human_readable_name, self.label, str(self.metadata)
        )


class Table(DatafileVariable):
    _PROP_COLS = Property("cols", DatafileVariable.METADATA_VALUE_TYPE_LIST, default="")
    _PROP_META = Property("meta", DatafileVariable.METADATA_VALUE_TYPE_BOOL, default="0")
    _PROP_STACK = Property("stack", DatafileVariable.METADATA_VALUE_TYPE_LIST, default="")

    DEFINITION_PATTERN = re.compile(r"\^{2}\s*(\w*)\s*(\\)?\s*(?(2)([^\n\r]*))([^\^]*)\^{2}(?:(.*?)(?:\r*?\n){2}|)",
                                    re.S)
    META_STACK_COLS_PATTERN = re.compile(r"(\d+)\s*,?")

    def __init__(self, name, human_readable_name, metadata, body):
        super().__init__(name, human_readable_name, metadata, find_all_properties(Table))
        self.body = self.parse_table_body(body) if body else np.empty((0,))
        self.cols = []

    def process_meta_properties(self, table_pool):
        if not self.metadata.get(Table._PROP_META.name, False):
            # this is not a metatable:
            return
        final_columns = []
        for entry in self.metadata.get(Table._PROP_STACK.name, []):
            e = re.compile("\s*").split(entry, maxsplit=1)
            t = table_pool[e[0]]
            foreign_cols = t.metadata[Table._PROP_COLS.name]
            if len(e) > 1:
                for i in map(lambda m: int(m.group(1)), Table.META_STACK_COLS_PATTERN.finditer(e[1])):
                    self.cols.append(foreign_cols[i])
                    final_columns.append(t.body[i])
            else:
                # take all cols:
                self.cols.extend(foreign_cols)
                final_columns.append(t.body)
        self.body = np.vstack(final_columns)

    def parse_table_body(self, body):
        # transpose here is used to provide convenient usage in plot ASTEVAL exprs
        return np.reshape(np.fromstring(body, dtype=np.float, sep=" "),
                          (body.count("\n") + 1, len(self.metadata["cols"]))).transpose()

    def body_as_one_line_string(self):
        return "[" + ", ".join(["[" + " ".join(map(str, arr)) + "]" for arr in self.body]) + "]"

    def __str__(self):
        return "Table<%s; body=\"%s\">" % (
            super().__str__(), self.body_as_one_line_string()
        )


class Curve:
    _PROP_COLOR = Property("color",
                           DatafileVariable.METADATA_VALUE_TYPE_STR,
                           default="black")
    _PROP_STYLE = Property("style",
                           DatafileVariable.METADATA_VALUE_TYPE_STR,
                           default="-")
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
        # print("Built Curve object with name=\"%s\"; metadata=\"%s\"" % (self.name, str(self.metadata)))

    def __str__(self):
        return "Curve<name=\"%s\"; metadata=\"%s\"" % (
            self.name, str(self.metadata)
        )

    def get_expressions(self):
        return self.metadata[self._PROP_X.name], self.metadata[self._PROP_Y.name], self.metadata[self._PROP_SCOPE.name]

    def get_style(self):
        return self.metadata[self._PROP_STYLE.name]

    def get_color(self):
        return self.metadata[self._PROP_COLOR.name]


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

    def __init__(self, name, human_readable_name, metadata, labgen_instance):
        super().__init__(name, human_readable_name, metadata, find_all_properties(Plot))
        self.figure_name = "figure_" + self.name
        # needed to access tables
        self.labgen_instance = labgen_instance
        self.figures = {}

    def produce_image(self, dpi=None):
        f = self.figures.get(dpi, None)
        if f:
            return f
        path = self.labgen_instance.figures_dir + os.sep + self.figure_name + (dpi or "")
        pp.clf()
        interpreter = asteval.Interpreter({table.name: table.body for table in self.labgen_instance.tables.values()})
        curves = self.metadata.get(Plot._PROP_CURVE.name, [])
        xlabel, ylabel = self.metadata[self._PROP_AXES.name]
        pp.xlabel(xlabel)
        pp.ylabel(ylabel)
        # plot data
        for curve in curves:
            x_expr, y_expr, scope = curve.get_expressions()
            interpreter.eval(scope)  # prepare scope
            for curve_data_x, curve_data_y in zip(flatten_2d_np_array(interpreter.eval(x_expr)),
                                                  flatten_2d_np_array(interpreter.eval(y_expr))):
                pp.plot(curve_data_x, curve_data_y,
                        marker="o", linestyle=curve.get_style(), color=curve.get_color())
        xrange = self.metadata[self._PROP_XRANGE.name]
        do_auto_x = False
        if xrange != Plot.AUTOSCALE:
            pp.xlim(xrange.start, xrange.stop)
            do_auto_x = True
        yrange = self.metadata[self._PROP_YRANGE.name]
        do_auto_y = False
        if yrange != Plot.AUTOSCALE:
            pp.ylim(yrange.start, yrange.stop)
            do_auto_y = True
        if do_auto_x and do_auto_y:
            pp.autoscale()
        pp.savefig(path)
        self.figures[dpi] = fig = Figure(path)
        return fig

    def __str__(self):
        return "Plot<%s; figure_name=%s>" % (
            super().__str__(), self.figure_name
        )


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


class Figure:
    def __init__(self, full_path):
        self.path = full_path
        self.ext = split_ext(full_path)[-1]
        self.name = os.path.basename(full_path)
        self.label = generate_label(self.name)


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


def cmd_ref(parser, var_name, **kwargs):
    var = parser.find_variable(var_name)
    return "\\ref{%s}" % (var.label,)


def cmd_fig_by_path(parser, path, label, hr_name, **kwargs):
    scale = kwargs.get("scale", "1.0")
    return r"""\begin{{figure}}[h!]
            \noindent\centering{{
                \includegraphics[scale={scale}]{{{name}}}
            }}
                \caption{{{caption}}}
            \label{{{label}}}
        \end{{figure}}""".format(
        scale=scale,
        name=path,
        caption=hr_name,
        label=label
    )


def cmd_fig(parser, name, hr_name, **kwargs):
    fig = parser.get_figure(name + os.extsep + kwargs.get("ext", "png"))
    return cmd_fig_by_path(parser, fig.path, fig.label, hr_name, **kwargs)


def cmd_plot(parser, plot_var, **kwargs):
    """
    Creates a plot image using pyplot
    """
    plot = parser.plots[plot_var]
    # dpi = kwargs.get("dpi", None)
    figure = plot.produce_image()
    return cmd_fig_by_path(parser, figure.path, figure.label, plot.human_readable_name, **kwargs)


def cmd_table_caption(parser, table_var, **kwargs):
    """
    Returns table name
    """
    return parser.tables[table_var].human_readable_name


def cmd_table_body(parser, table_var, **kwargs):
    """
    Generates table body

    kwargs: split_each=False, cast_to_int=False, precision=3
    """
    table = parser.tables[table_var]
    split_each = kwargs.get("split_each", False)
    precision = int(kwargs.get("precision", 3))
    cast_to_int = bool(kwargs.get("cast_to_int", False))
    header = " & ".join(table.metadata["cols"]) + "\\\\\n\\hline\n"
    return header + ("\n\\hline\n" if split_each else "\n").join(
        [np.array2string(row,
                         separator=" & ",
                         precision=precision,
                         formatter={"all": lambda x: str(x if not cast_to_int else int(x))})[1:-1]  # esc np default []
         + r" \\" for row in table.body.transpose()])


def cmd_table(parser, table_var, **kwargs):
    """
    Generates full table
    """
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
        columns=("c|" * len(table.metadata["cols"]))[:-1],
        table_body=cmd_table_body(parser, table_var, **kwargs)
    )


COMMAND_DEFINITIONS = {
    cmd_name: Command(cmd_name, globals()[full_name])
    for cmd_name, full_name in
    map(
        lambda s: (s[len(Command.COMMAND_DEF_PREFIX):], s),
        filter(
            lambda s: s.startswith(Command.COMMAND_DEF_PREFIX),
            dir()))
    }


class LabGen:
    ARGS_ITEM_PATTERN = re.compile(r"(?:\s*(\w*)\s*=\s*([^|]*)|([^|]+))\|?", re.U | re.M | re.S)
    #                                                    put * here ^ in case of troubles with empty arguments

    LOGGER_FORMATTER = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s %(funcName)s: %(message)s")

    DEFAULT_FIGURES_DIR = "fig"

    TEMPLATE_FILE_FORMAT = "lgt"
    DATA_FILE_FORMAT = "lgd"
    SOURCE_FILE_FORMAT = "lgs"
    OUTPUT_FILE_FORMAT = "tex"

    ALLOWED_FIGURE_FORMAT = ["png", "jpg", "eps", "svg", "jpeg", "gif"]

    def __init__(self, output_dir, figures_dir=None, log_level="DEBUG"):
        self.output_dir = os.path.normpath(output_dir)
        if not os.path.exists(self.output_dir):
            os.mkdir(self.output_dir)
        self.figures_dir = os.path.normpath(figures_dir) or (output_dir + os.sep + LabGen.DEFAULT_FIGURES_DIR)
        if not os.path.exists(self.figures_dir):
            os.mkdir(self.figures_dir)
        self.templates, self.tables, self.plots, self.constants, self.figures = \
            {}, {}, {}, {}, {}
        self.log = self._prepare_logger(log_level)
        self._load_figures()

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

    def find_variable(self, var_name):
        for pool in (self.tables, self.plots, self.figures):
            v = pool.get(var_name)
            if not (v is None):
                return v
        raise LabGenError("no variable with name %s" % (var_name,))

    def get_figure(self, figure_name):
        figure = self.figures.get(figure_name)
        if not (figure is None):
            return figure
        path = self.figures_dir + os.sep + figure_name
        if not os.path.exists(path):
            raise LabGenError("no such file: " + path)
        self.figures[figure_name] = fig = Figure(path)
        return fig

    def _resolve_templates(self, string, outer_templates, recursion_level):
        def interceptor_func(match):
            nonlocal outer_templates, recursion_level
            template_name = match.group(1)
            if outer_templates and template_name == outer_templates[-1]:
                raise LabGenError("Recursive template calls are not allowed. Stack: " + str(outer_templates))
            template = self.templates[template_name]
            substitution = LabGen.parse_args(match.group(2) or "")
            self.log.info(recursion_level * "\t" +
                          "Applying substitution %s in %s invocation" % (
                              str(substitution),
                              "[" + "->".join(outer_templates) + ("->" if outer_templates else "") + template_name + "]"
                          ))
            return self._resolve_templates(template.interpolate_params(substitution),
                                           outer_templates + [template_name], recursion_level + 1)

        return Template.INVOCATION_PATTERN.sub(interceptor_func, string)

    def resolve_templates(self, string):
        return self._resolve_templates(string, [], 0)

    def invoke_commands(self, string):
        def interceptor_func(match):
            command = COMMAND_DEFINITIONS.get(match.group(1))
            if command is None:
                raise LabGenError("No such command: @\"%s\"" % (match.group(1),))
            arg_dict = LabGen.parse_args(match.group(2) or "")
            self.log.info("invoking command %s with args %s" % (str(command), str(arg_dict)))
            return command(self, arg_dict)

        return Command.INVOCATION_PATTERN.sub(interceptor_func, string)

    def render(self, string):
        self._log_stage("RENDER STAGE 1: RESOLVE TEMPLATES")
        string = self.resolve_templates(string)
        self._log_stage("RENDER STAGE 2: INVOKE COMMANDS")
        return self.invoke_commands(string)

    def parse_templates(self, string):
        for match in Template.DEFINITION_PATTERN.finditer(string):
            template_name = match.group(1)
            self.log.info("Defined template \"%s\"" % (template_name,), end="... ")
            self.templates[template_name] = Template(template_name, match.group(4))
            template_params = self.templates[template_name].param_map
            self.log.info("required args: %s; other args: %s" % (
                list(filter(lambda k: template_params[k] is None, template_params.keys())),
                {key: template_params[key] for key in
                 filter(lambda a: not (template_params[a] is None), template_params.keys())}
            ))

    def parse_data(self, string):
        # we do this in 3 stages
        # 1. parse all tables
        for match in Table.DEFINITION_PATTERN.finditer(string):
            name, hr_name, metadata, body = match.group(1), match.group(3), match.group(4), match.group(5)
            new_table = Table(name, hr_name, metadata.strip(), body.strip())
            self.tables[name] = new_table
            self.log.info("Created new table variable %s" % (new_table,))
        # process metatables and so
        for table in self.tables.values():
            table.process_meta_properties(self.tables)
        # 2. parse all constants
        pass
        # 3. parse all plots
        for match in Plot.DEFINITION_PATTERN.finditer(string):
            name, hr_name, metadata = match.group(1), match.group(3), match.group(4)
            self.plots[name] = Plot(name, hr_name, metadata, self)
            self.log.info("Created new plot variable %s" % (self.plots[name],))

    def process_files(self, filenames, recursive=False, encoding="utf-8"):
        def action(filename, ext, string):
            nonlocal self
            self.log.debug("Looking at: " + filename + "." + ext)
            if ext == LabGen.DATA_FILE_FORMAT:
                self.log.info("Parsing datafile %s.%s" % (filename, ext))
                self.parse_data(string)
            elif ext == LabGen.TEMPLATE_FILE_FORMAT:
                self.log.info("Parsing template file %s.%s" % (filename, ext))
                self.parse_templates(string)
        for name in filenames:
            do_for_path(name, action, recursive=recursive, encoding=encoding)

    def render_files(self, filenames, encoding="utf-8"):
        def action(filename, ext, string):
            nonlocal self
            if ext != LabGen.SOURCE_FILE_FORMAT:
                return
            self.log.info("Processing file %s.%s" % (filename, ext))
            self._write_out_file(
                os.path.basename(filename),
                self.render(string),
                encoding
            )
        for path in filenames:
            do_for_path(path, action, recursive=False, encoding=encoding)

    def _prepare_logger(self, level):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(LabGen.LOGGER_FORMATTER)
        log = logging.getLogger(self.__class__.__name__)
        log.propagate = False
        log.addHandler(handler)
        log.setLevel(level)
        return log

    def _write_out_file(self, filename, contents, encoding="utf-8"):
        path = self.output_dir + os.sep + filename + (
            (os.extsep + LabGen.OUTPUT_FILE_FORMAT) if split_ext(filename)[1] != LabGen.OUTPUT_FILE_FORMAT else
            ""
        )
        self._log_stage("Writing output file %s" % (path,))
        try:
            with open(path, "w", encoding=encoding) as file:
                file.write(contents)
        except Exception as e:
            self._log_stage("Failed to write file %s" % (path,), exception=e)
        else:
            self._log_stage("File written %s" % (path,))

    def _load_figures(self):
        for file in os.listdir(self.figures_dir):
            filename, ext = split_ext(file)
            if not (ext in self.ALLOWED_FIGURE_FORMAT):
                continue
            else:
                fig = Figure(self.figures_dir + os.sep + filename)
                self.log.info("Found image: %s; loaded with label %s" % (fig.name, fig.label))
                self.figures[filename] = fig

    def _log_stage(self, stage, exception=None):
        self.log.info("===== %s =====" % (stage,))
        if not (exception is None):
            self.log.info("===== REASON: " + str(exception))


def prepare_command_line_args_parser():
    parser = argparse.ArgumentParser(description="LabGen")

    parser.add_argument("-o", "--output-dir", help="LabGen output directory")
    parser.add_argument("-f", "--figures-dir", help="directory with usable figures. Following formats are possible: " +
                                                    str(LabGen.ALLOWED_FIGURE_FORMAT))
    parser.add_argument("-H", "--headers", nargs="*", help="files or directories with .%s and .%s files" %
                                                           (LabGen.DATA_FILE_FORMAT, LabGen.TEMPLATE_FILE_FORMAT))
    parser.add_argument("-S", "--source", nargs="*", help="source files and/or dirs with .%s files" %
                                                          (LabGen.SOURCE_FILE_FORMAT,))
    return parser


if __name__ == '__main__':
    namespace = prepare_command_line_args_parser().parse_args(args=sys.argv[1:])

    lg = LabGen(namespace.output_dir, namespace.figures_dir)
    lg.process_files(namespace.headers)

    lg.render_files(namespace.source)

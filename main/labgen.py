import numpy as np
import asteval

from commands import COMMAND_DEFINITIONS, Command, create_invocation_pattern, re

def create_variable_pattern(initiating_char, closing_char):
    processed = r"\{initiating_char}\s*(\w*)\s*(\\)?\s*(?(2)([^\n\r]*)|)$(.*?)\{closing_char}".format(
        initiating_char=initiating_char,
        closing_char=closing_char
    )
    return re.compile(processed, re.M | re.U | re.S)

def find_all_properties(clazz: type, prefix="_PROP_"):
    return {getattr(clazz, field_name).name: getattr(clazz, field_name)
            for field_name in dir(clazz) if field_name.startswith(prefix)}


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

    def __init__(self, name, type_, object_type=None, default=None, single_value=True, subproperty=False):
        self.default = default
        self.type = type_
        self.object_type = object_type
        self.name = name
        self.single_value = single_value
        self.sub_property = subproperty

    def __str__(self):
        return "Property<name=\"%s\">" % (self.name,)


class ObjectBuilder:

    def __init__(self, object_name:str, obj_property: Property):
        self.obj_property = obj_property
        self.build_metadata = {}
        self.name = obj_property.name
        self.object_name = object_name

    def put(self, key, value):
        self.build_metadata[key] = value

    def build(self):
        return self.obj_property.object_type(self.object_name, self.build_metadata)


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
        parsed, builder = {}, None

        def put_value(prop_, processed_value):
            nonlocal parsed
            if prop_.single_value:
                parsed[prop_.name] = processed_value
            else:
                parsed[prop_.name] = parsed.get(prop_.name, []) + [processed_value, ]

        # 1. parse provided string for metadata
        for match in DatafileVariable.METADATA_PATTERN.finditer(string):
            is_building_object_property, key, value = bool(match.group(1)), match.group(2), match.group(3)
            if value is None: value = ""
            prop = self.properties[key]
            if prop.type == DatafileVariable.METADATA_VALUE_TYPE_BUILDER:
                # flush old object if any and start building another
                if builder:
                    put_value(builder.obj_property, builder.build())
                builder = ObjectBuilder(value, prop)
            else:
                # find a converter
                converter = DatafileVariable.CONVERTERS[prop.type]
                if is_building_object_property:
                    if not builder:
                        raise ValueError("No object is currently being built. Property: " + str(prop))
                    builder.put(key, converter(value))
                else:
                    if builder:
                        put_value(builder.obj_property, builder.build())
                        builder = None
                    put_value(prop, converter(value))
        if builder:
            put_value(builder.obj_property, builder.build())
        # 2. locate and try to define defaults
        for prop in self.properties.values():
            if not (prop.name in parsed.keys()) and prop.object_type is None and not prop.sub_property:
                if prop.default is None:
                    raise ValueError("Value is not presented for required property %s" % (prop.name,))
                put_value(prop, DatafileVariable.CONVERTERS[prop.type](prop.default))
        self.metadata = parsed


class Table(DatafileVariable):

    _PROP_COLS = Property("cols", DatafileVariable.METADATA_VALUE_TYPE_LIST)

    DEFINITION_PATTERN = re.compile(r"\^{2}\s*(\w*)\s*(\\)?\s*(?(2)([^\n\r]*))([^\^]*)\^{2}(?:(.*?)(?:\r*?\n){2}|)",
                                    re.S)

    def __init__(self, name, human_readable_name, metadata, body):
        super().__init__(name, human_readable_name, metadata, find_all_properties(Table))
        self.body = self.parse_table_body(body)
        print("Created new table variable \"%s\": metadata: %s; body:\n%s" % (self.name, str(self.metadata), self.body))

    def parse_table_body(self, body):
        # transpose here is used to provide convenient usage in plot ASTEVAL exprs
        return np.reshape(np.fromstring(body, dtype=np.float, sep=" "),
                          (body.count("\n") + 1, len(self.metadata["cols"]))).transpose()


class Curve:

    def __init__(self, name, metadata):
        self.name = name
        self.metadata = metadata
        print("Built Curve object with name=\"%s\"; metadata=\"%s\"" % (self.name, str(self.metadata)))


class Plot(DatafileVariable):

    DEFINITION = "${2}"
    DEFINITION_PATTERN = create_variable_pattern(DEFINITION, DEFINITION)

    # TODO: cleanup property definitions
    _PROP_AXES = Property("axes", DatafileVariable.METADATA_VALUE_TYPE_LIST,
                          default="x;y")
    _PROP_XRANGE = Property("xrange", DatafileVariable.METADATA_VALUE_TYPE_RANGE,
                            default="autoscale")
    _PROP_YRANGE = Property("yrange", DatafileVariable.METADATA_VALUE_TYPE_RANGE,
                            default="autoscale")
    _PROP_CURVE = Property("curve", DatafileVariable.METADATA_VALUE_TYPE_BUILDER,
                           object_type=Curve, single_value=False)
    _PROP_CURVE_COLOR = Property("color", DatafileVariable.METADATA_VALUE_TYPE_STR,
                                 default="black", subproperty=True)
    _PROP_CURVE_STYLE = Property("style", DatafileVariable.METADATA_VALUE_TYPE_STR,
                                 default="lines+points", subproperty=True)
    _PROP_CURVE_X = Property("x", DatafileVariable.METADATA_VALUE_TYPE_STR,
                             default="x", subproperty=True)
    _PROP_CURVE_Y = Property("y", DatafileVariable.METADATA_VALUE_TYPE_STR,
                             default="y", subproperty=True)
    _PROP_CURVE_SCOPE = Property("scope", DatafileVariable.METADATA_VALUE_TYPE_STR,
                                 default="", subproperty=True)

    def __init__(self, name, human_readable_name, metadata):
        super().__init__(name, human_readable_name, metadata, find_all_properties(Plot))
        self.figure_name = "figure_" + self.name
        print("Created new plot variable \"%s\": metadata: %s" % (self.name, str(self.metadata)))


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
        if "wrap-newlines"in self.opts:
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
                        # if we have found value, return it
                        return value
            raise ValueError("no value found for param \"%s\"" % (param,))

        return Template.PARAM_INTERPOLATION_PATTERN.sub(interceptor_func, self.body)

    def __str__(self):
        return "Template<name=\"%s\"; param_map=%s; positions: %s" % (
            self.name, str(self.param_map), str(self.param_positions)
        )


class LabGen:

    ARGS_ITEM_PATTERN = re.compile(r"(?:\s*(\w*)\s*=\s*([^|]*)|([^|]+))\|?", re.U | re.M | re.S)
    #                                                               ^ put * here in case of troubles with
    # empty arguments

    def __init__(self, temp_files_dir, template_files: list, data_files: list):
        self.temp_files_dir = temp_files_dir
        self.templates = LabGen.parse_template_files(template_files)
        self.tables, self.plots = LabGen.parse_datafiles(data_files)
        self.ast_interpreter = asteval.Interpreter(
            {table.name: table.body for table in self.tables.values()}
        )

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

    @staticmethod
    def parse_template_files(filename_list):
        templates = {}
        for filename in filename_list:
            with open(filename, encoding="utf-8") as file:
                for match in Template.DEFINITION_PATTERN.finditer(file.read()):
                    template_name = match.group(1)
                    print("Defining template \"%s\"" % (template_name,), end="... ")
                    templates[template_name] = Template(template_name, match.group(4))
                    template_params = templates[ template_name ].param_map
                    print("required args: %s; other args: %s" % (
                        list(filter(lambda k: template_params[k] is None, template_params.keys())),
                        {key: template_params[key] for key in filter(lambda a: not (template_params[a] is None), template_params.keys())}
                    ))
        return templates

    @staticmethod
    def parse_datafiles(filename_list):
        tables, plots = {}, {}
        for filename in filename_list:
            with open(filename, encoding="utf-8") as file:
                string = file.read()
                for match in Table.DEFINITION_PATTERN.finditer(string):
                    name, hr_name, metadata, body = match.group(1), match.group(3), match.group(4), match.group(5)
                    tables[name] = Table(name, hr_name, metadata.strip(), body.strip())
                for match in Plot.DEFINITION_PATTERN.finditer(string):
                    name, hr_name, metadata = match.group(1), match.group(3), match.group(4)
                    plots[name] = Plot(name, hr_name, metadata)
        return tables, plots

    def _do_resolve_templates(self, string, outer_templates, recursion_level):
        def interceptor_func(match):
            nonlocal outer_templates
            template_name = match.group(1)
            if outer_templates and template_name == outer_templates[-1]:
                raise LabGenError("Recursive template calls are not allowed. Stack: " + str(outer_templates))
            template = self.templates[template_name]
            substitution = LabGen.parse_args(match.group(2) or "")
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
            arg_dict = LabGen.parse_args(match.group(2) or "")
            print("invoking command %s with args %s" % (str(command), str(arg_dict)))
            return command(self, arg_dict)
        return Command.INVOCATION_PATTERN.sub(interceptor_func, string)

    def render_string(self, string):
        print("===== RENDER STAGE 1: RESOLVE TEMPLATES =====")
        string = self.resolve_templates(string)
        print("===== RENDER STAGE 2: INVOKE COMMANDS =====")
        string = self.invoke_commands(string)
        return string

    def render_file(self, filename, encoding="utf-8"):
        print("===== STARTED RENDERING FILE %s" % (filename,))
        result = None
        with open(filename, encoding=encoding) as file:
            result = self.render_string(file.read())
        print("===== COMPLETED RENDERING FILE %s" % (filename,))
        return result


if __name__ == '__main__':
    p = LabGen("../test/", ["../table.lgt", "../tamplateLabs2.0.txt"], ["../datafile.txt"])
    print(p.render_file("../test/test_source2.txt"))


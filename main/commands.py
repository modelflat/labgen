import time
import re
import numpy as np
from matplotlib import pyplot as pp


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
# 1. Command core function name should start with COMMAND_DEF_PREFIX
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
    plot = parser.plots[plot_var]
    intp_ = []
    for curve in plot.metadata["curve"]:
        x_expr, y_expr, scope = curve.metadata["x"], curve.metadata["y"], curve.metadata["scope"]
        parser.ast_interpreter.eval(scope) # prepare scope
        intp_.append(parser.ast_interpreter.eval(x_expr))
        intp_.append(parser.ast_interpreter.eval(y_expr))
        intp_.append("b-")
    pp.plot(*intp_)
    filename = parser.temp_files_dir + plot.figure_name
    pp.savefig(filename)
    return "(ref to " + filename + ")"

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

if __name__ == '__main__':
    for key in COMMAND_DEFINITIONS.keys():
        print(str(COMMAND_DEFINITIONS[key]))

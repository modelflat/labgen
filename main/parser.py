import re

TEMPLATE_PATTERN = re.compile("(^#.*?$)|(\w+?)\s*\{{2}(.*?)\}{2}", re.M | re.S | re.U)


def parse_template_file(filename, encoding="utf-8"):
    result = {}
    with open(filename, "r", encoding=encoding) as f:
        for match in TEMPLATE_PATTERN.finditer(f.read()):
            if match.group(1):
                continue
            result[match.group(2)] = match.group(3).strip()
    return result


LATEX_TABLE_TEMPLATE = \
    """\\begin{{table}}[{modifiers}]
    \\caption{{{caption}}}
    \\label{{{label}}}
        \\begin{{center}}
            \\begin{{tabular}}{{{columns}}}
            \\hline
            {column_names}
            \\hline
            {table_body}
            \\hline
            \\end{{tabular}}
        \\end{{center}}
    \\end{{table}}"""

# "@@stack[Title 1:##filename:ccc:,Title 2]"

COLUMN_JUSTIFICATION = "c"


def generate_latex_table_body(values_list, split_each=False):
    return ("\n\\hline\n" if split_each else "\n").join(
        [" & ".join([str(e) for e in row]) + " \\\\" for row in values_list])


def generate_latex_table(caption, label, column_names, values_list, split_values=False, modifiers="h!",
                         column_format: list = None):
    return LATEX_TABLE_TEMPLATE.format(
        modifiers=modifiers, caption=caption, label=label,
        columns=column_format if not (column_format is None) else "c" * len(column_names),
        column_names=" & ".join(column_names) + "\\\\",
        table_body=generate_latex_table_body(values_list, split_each=split_values))


if __name__ == "__main__":
    print(parse_template_file("D:\\template.txt", encoding="cp1251"))


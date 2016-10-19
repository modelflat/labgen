## **WARNING 1:** This project is only pre-alpha and might stay in this state forever.
## **WARNING 2:** This project is based on python module _re_ in educational purposes only. Yes, I know about _regex_ and _pyparsing_, and probably will move this project on _pyparsing_ in the indefinite future, but for now it remains as is


# LabGen - tool for generating laboratory work reports

* Usage
```
python3 labgen.py [-h] [-o OUTPUT_DIR] [-f FIGURES_DIR]
                 [--template-files [TEMPLATE_FILES [TEMPLATE_FILES ...]]]
                 [--data-files [DATA_FILES [DATA_FILES ...]]]
                 [--source-files [SOURCE_FILES [SOURCE_FILES ...]]]
```

## Concepts

### Source File
A source file is a core file, which can be rendered according to given template and data files.
Source file can contain `@command` and `#template` invocations

Example:
```
Some text..

@date - here will  be inserted current date and time
#t|| first=first parameter |
 second 
 multiline
 parameter
 || - if template with name t is defined, it will be invoked, and parameters will be passed into the template
```

### Commands

A command is an instruction to LabGen to perform a particular action, for example, generate table body for provided variable name.
Commands can be defined from Python code only. 

List of commands (**is incompleted for now, descriptions can be missing**):

* fig
* ref
* table_body
* plo
* table_caption
* table 
* date
* labgen_dump 

### Template File
Template file can contain template definitions only. All text that is not inside template definition brackets will be
simply ignored.

* Template definition

Syntax:
```
##<name>
[++<parameter name>]
...
plain text with command @invocations and parameter %%injections
...
##
```

* Parameters:

_name_ - identifier of this template object

Template parameters define the list of parameters which can be passed to template on its invocation. Parameters can
be passed positionally only, in exact order in which they are defined in the lines in the beginning of a template.
Parameters can then be injected into template text with %% prefix:
```
    %%<parameter name>
```

* Nested templates:

Templates can invoke other templates. Recursive calls are not allowed.

### Data file

Data files can contain tables' and plots' definitions.

#### Table variable definition

Syntax:
```
^^<name>[\<human readable name>]
[metainfo strings...]&
^^
[<table contents>...]
```

* Parameters

_name_ - identifier of this table object. can contain unicode letters and _underscores

_human readable name_ - variable meta name, can contain any character except for newline

_table contents_ - contents of the table in form of row and cols. ! An empty line is treated as end of the table

* Metadata

cols=value1; value2;... - specifies column names for table, separated with `;`

#### Plot variable definition

Syntax:
```
$$<name>[\<human readable name>]
[metainfo strings...]
$$
```

* Parameters

name / human readable name - same as in table definition.

* Metadata

_axes_= value1; value2 - axes names, default to "x" and "y"

_xrange_= value1; value2 - range for plotting x, defaults to auto-scale

_yrange_= value1; value2 - range for plotting y, defaults to auto-scale

_curve_= name - adds new curve with given name.

##### Curve object
Curve objects represent curves on plot. There can be multiple curve objects for plot.
Each curve configured with subsequent lines starting with "."

* Curve configuration

_.color_= value , defaults to black

_.style_= value , defaults to lines with points (_pyplot_'s `marker="o", linestyle="-"`)

_.x_= Python ASTEVAL expression - a single statement, interpreted as lambda function to transform input data for x coordinate

_.y_= Python ASTEVAL expression - a single statement, interpreted as lambda function to transform input data for y coordinate

_.scope_= Python ASTEVAL expression - one or more statements, which are executed just before the computation of y and x occurs.

* Writing expressions for plotting data

Each table parsed from set of data files can be referenced by its name. Inside an interpreter, tables are represented as simple numpy arrays.
Example:
```
.x = log( table1[0] )
.y = log( table2[1] )**2
```
Here _x_ coordinate will consist of logarithm of the first row of table with name `table1`, and _y_ coordinate, in its turn, will be a squared logarithm of the second row of table `table2` 

All functions of Python's `math` and `numpy` modules are available inside the interpreter.
For more information on `asteval`, head on [its site](https://newville.github.io/asteval/).

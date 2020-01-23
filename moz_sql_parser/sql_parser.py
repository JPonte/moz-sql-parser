# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#

from __future__ import absolute_import, division, unicode_literals

import ast
import sys

from mo_future import first
from pyparsing import Combine, Forward, Group, Keyword, Literal, Optional, ParserElement, Regex, Word, ZeroOrMore, alphanums, alphas, delimitedList, infixNotation, opAssoc, restOfLine

from moz_sql_parser.debugs import debug
from moz_sql_parser.keywords import AND, AS, ASC, BETWEEN, CASE, COLLATE_NOCASE, CROSS_JOIN, DESC, ELSE, END, FROM, FULL_JOIN, FULL_OUTER_JOIN, GROUP_BY, HAVING, IN, INNER_JOIN, IS, IS_NOT, JOIN, LEFT_JOIN, LEFT_OUTER_JOIN, LIKE, LIMIT, NOT_BETWEEN, NOT_IN, NOT_LIKE, OFFSET, ON, OR, ORDER_BY, RESERVED, RIGHT_JOIN, RIGHT_OUTER_JOIN, SELECT, THEN, UNION, UNION_ALL, USING, WHEN, WHERE

ParserElement.enablePackrat()

# PYPARSING USES A LOT OF STACK SPACE
sys.setrecursionlimit(2000)

IDENT_FIRST_CHAR = alphas + "_"
IDENT_REST_CHAR = alphanums + "_$"

KNOWN_OPS = [
    # https://www.sqlite.org/lang_expr.html
    Literal("||").setName("concat").setDebugActions(*debug),
    Literal("*").setName("mul").setDebugActions(*debug),
    Literal("/").setName("div").setDebugActions(*debug),
    Literal("%").setName("mod").setDebugActions(*debug),
    Literal("+").setName("add").setDebugActions(*debug),
    Literal("-").setName("sub").setDebugActions(*debug),

    Literal("&").setName("binary_and").setDebugActions(*debug),
    Literal("|").setName("binary_or").setDebugActions(*debug),

    Literal("<").setName("lt").setDebugActions(*debug),
    Literal("<=").setName("lte").setDebugActions(*debug),
    Literal(">").setName("gt").setDebugActions(*debug),
    Literal(">=").setName("gte").setDebugActions(*debug),
    Literal("=").setName("eq").setDebugActions(*debug),
    Literal("==").setName("eq").setDebugActions(*debug),
    Literal("!=").setName("neq").setDebugActions(*debug),
    Literal("<>").setName("neq").setDebugActions(*debug),
    (BETWEEN.setName("between").setDebugActions(*debug), AND),
    (NOT_BETWEEN.setName("not_between").setDebugActions(*debug), AND),
    IN.setName("in").setDebugActions(*debug),
    NOT_IN.setName("nin").setDebugActions(*debug),
    IS_NOT.setName("neq").setDebugActions(*debug),
    IS.setName("is").setDebugActions(*debug),
    LIKE.setName("like").setDebugActions(*debug),
    NOT_LIKE.setName("nlike").setDebugActions(*debug),
    AND.setName("and").setDebugActions(*debug),
    OR.setName("or").setDebugActions(*debug)
]


def to_json_operator(instring, tokensStart, retTokens):
    # ARRANGE INTO {op: params} FORMAT
    tok = retTokens[0]
    for o in KNOWN_OPS:
        if isinstance(o, tuple):
            if o[0].match == tok[1]:
                op = o[0].name
                break
        elif (o.match == tok[1]) != (o.matches(tok[1])):
            raise Exception("not expected")
        elif o.match == tok[1]:
            op = o.name
            break
    else:
        if tok[1] == COLLATE_NOCASE.match:
            op = COLLATE_NOCASE.name
            return {op: tok[0]}
        else:
            raise Exception("not found")

    if op == "eq":
        if tok[2] == "null":
            return {"missing": tok[0]}
        elif tok[0] == "null":
            return {"missing": tok[2]}
    elif op == "neq":
        if tok[2] == "null":
            return {"exists": tok[0]}
        elif tok[0] == "null":
            return {"exists": tok[2]}
    elif op == "is":
        if tok[2] == 'null':
            return {"missing": tok[0]}
        else:
            return {"exists": tok[0]}

    return {op: [tok[i * 2] for i in range(int((len(tok) + 1) / 2))]}


def to_json_call(instring, tokensStart, retTokens):
    # ARRANGE INTO {op: params} FORMAT
    tok = retTokens
    op = tok.op.lower()

    if op == "-":
        op = "neg"

    params = tok.params
    if not params:
        params = None
    elif len(params) == 1:
        params = params[0]
    return {op: params}


def to_case_call(instring, tokensStart, retTokens):
    tok = retTokens
    cases = list(tok.case)
    elze = getattr(tok, "else", None)
    if elze:
        cases.append(elze)
    return {"case": cases}


def to_when_call(instring, tokensStart, retTokens):
    tok = retTokens
    return {"when": tok.when, "then":tok.then}


def to_join_call(instring, tokensStart, retTokens):
    tok = retTokens

    if tok.join.name:
        output = {tok.op: {"name": tok.join.name, "value": tok.join.value}}
    else:
        output = {tok.op: tok.join}

    if tok.on:
        output['on'] = tok.on

    if tok.using:
        output['using'] = tok.using
    return output


def to_select_call(instring, tokensStart, retTokens):
    tok = retTokens[0].asDict()

    if tok.get('value')[0][0] == '*':
        return '*'
    else:
        return tok


def to_union_call(instring, tokensStart, retTokens):
    tok = retTokens[0].asDict()
    operator = first(tok['from'].keys())
    unions = tok['from']['union']
    if len(unions) == 1:
        output = unions[0]
    else:
        if not tok.get('orderby') and not tok.get('limit'):
            return tok['from']
        else:
            output = {"from": {operator: unions}}

    if tok.get('orderby'):
        output["orderby"] = tok.get('orderby')
    if tok.get('limit'):
        output["limit"] = tok.get('limit')
    return output


def unquote(instring, tokensStart, retTokens):
    val = retTokens[0]
    if val.startswith("'") and val.endswith("'"):
        val = "'"+val[1:-1].replace("''", "\\'")+"'"
        # val = val.replace(".", "\\.")
    elif val.startswith('"') and val.endswith('"'):
        val = '"'+val[1:-1].replace('""', '\\"')+'"'
        # val = val.replace(".", "\\.")
    elif val.startswith('`') and val.endswith('`'):
        val = "'" + val[1:-1].replace("``","`") + "'"
    elif val.startswith("+"):
        val = val[1:]
    un = ast.literal_eval(val)
    return un


def to_string(instring, tokensStart, retTokens):
    val = retTokens[0]
    val = "'"+val[1:-1].replace("''", "\\'")+"'"
    return {"literal": ast.literal_eval(val)}

# NUMBERS
realNum = Regex(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?").addParseAction(unquote)
intNum = Regex(r"[+-]?\d+([eE]\+?\d+)?").addParseAction(unquote)

# STRINGS, NUMBERS, VARIABLES
sqlString = Regex(r"\'(\'\'|\\.|[^'])*\'").addParseAction(to_string)
identString = Regex(r'\"(\"\"|\\.|[^"])*\"').addParseAction(unquote)
mysqlidentString = Regex(r'\`(\`\`|\\.|[^`])*\`').addParseAction(unquote)
ident = Combine(~RESERVED + (delimitedList(Literal("*") | Word(IDENT_FIRST_CHAR, IDENT_REST_CHAR) | identString | mysqlidentString, delim=".", combine=True))).setName("identifier")

# EXPRESSIONS
expr = Forward()

# CASE
case = (
    CASE +
    Group(ZeroOrMore((WHEN + expr("when") + THEN + expr("then")).addParseAction(to_when_call)))("case") +
    Optional(ELSE + expr("else")) +
    END
).addParseAction(to_case_call)

selectStmt = Forward()
compound = (
    (Keyword("not", caseless=True)("op").setDebugActions(*debug) + expr("params")).addParseAction(to_json_call) |
    (Keyword("distinct", caseless=True)("op").setDebugActions(*debug) + expr("params")).addParseAction(to_json_call) |
    Keyword("null", caseless=True).setName("null").setDebugActions(*debug) |
    case |
    (Literal("(").setDebugActions(*debug).suppress() + selectStmt + Literal(")").suppress()) |
    (Literal("(").setDebugActions(*debug).suppress() + Group(delimitedList(expr)) + Literal(")").suppress()) |
    realNum.setName("float").setDebugActions(*debug) |
    intNum.setName("int").setDebugActions(*debug) |
    (Keyword("~")("op").setDebugActions(*debug) + expr("params")).addParseAction(to_json_call) |
    (Literal("-")("op").setDebugActions(*debug) + expr("params")).addParseAction(to_json_call) |
    sqlString.setName("string").setDebugActions(*debug) |
    (
        Word(IDENT_FIRST_CHAR, IDENT_REST_CHAR)("op").setName("function name").setDebugActions(*debug) +
        Literal("(").setName("func_param").setDebugActions(*debug) +
        Optional(selectStmt | Group(delimitedList(expr)))("params") +
        ")"
    ).addParseAction(to_json_call).setDebugActions(*debug) |
    ident.copy().setName("variable").setDebugActions(*debug)
)
expr << Group(infixNotation(
    compound,
    [
        (
            o,
            3 if isinstance(o, tuple) else 2,
            opAssoc.LEFT,
            to_json_operator
        )
        for o in KNOWN_OPS
    ]+[
        (
            COLLATE_NOCASE,
            1,
            opAssoc.LEFT,
            to_json_operator
        )
    ]
).setName("expression").setDebugActions(*debug))

# SQL STATEMENT
selectColumn = Group(
    Group(expr).setName("expression1")("value").setDebugActions(*debug) + Optional(Optional(AS) + ident.copy().setName("column_name1")("name").setDebugActions(*debug)) |
    Literal('*')("value").setDebugActions(*debug)
).setName("column").addParseAction(to_select_call)

table_source = (
    (
        (
            Literal("(").setDebugActions(*debug).suppress() +
            selectStmt +
            Literal(")").setDebugActions(*debug).suppress()
        ).setName("table source").setDebugActions(*debug)
    )("value") +
    Optional(
        Optional(AS) +
        ident("name").setName("table alias").setDebugActions(*debug)
    )
    |
    (
        ident("value").setName("table name").setDebugActions(*debug) +
        Optional(AS) +
        ident("name").setName("table alias").setDebugActions(*debug)
    )
    |
    ident.setName("table name").setDebugActions(*debug)
)

join = (
    (CROSS_JOIN | FULL_JOIN | FULL_OUTER_JOIN | INNER_JOIN | JOIN | LEFT_JOIN | LEFT_OUTER_JOIN | RIGHT_JOIN | RIGHT_OUTER_JOIN)("op") +
    Group(table_source)("join") +
    Optional((ON + expr("on")) | (USING + expr("using")))
).addParseAction(to_join_call)

sortColumn = expr("value").setName("sort1").setDebugActions(*debug) + Optional(DESC("sort") | ASC("sort")) | \
             expr("value").setName("sort2").setDebugActions(*debug)

# define SQL tokens
selectStmt << Group(
    Group(Group(
        delimitedList(
            Group(
                SELECT.suppress().setDebugActions(*debug) + delimitedList(selectColumn)("select") +
                Optional(
                    (FROM.suppress().setDebugActions(*debug) + delimitedList(Group(table_source)) + ZeroOrMore(join))("from") +
                    Optional(WHERE.suppress().setDebugActions(*debug) + expr.setName("where"))("where") +
                    Optional(GROUP_BY.suppress().setDebugActions(*debug) + delimitedList(Group(selectColumn))("groupby").setName("groupby")) +
                    Optional(HAVING.suppress().setDebugActions(*debug) + expr("having").setName("having")) +
                    Optional(LIMIT.suppress().setDebugActions(*debug) + expr("limit")) +
                    Optional(OFFSET.suppress().setDebugActions(*debug) + expr("offset"))
                )
            ),
            delim=UNION_ALL | UNION
        )
    )("union"))("from") +
    Optional(ORDER_BY.suppress().setDebugActions(*debug) + delimitedList(Group(sortColumn))("orderby").setName("orderby")) +
    Optional(LIMIT.suppress().setDebugActions(*debug) + expr("limit")) +
    Optional(OFFSET.suppress().setDebugActions(*debug) + expr("offset"))
).addParseAction(to_union_call)


SQLParser = selectStmt

# IGNORE SOME COMMENTS
oracleSqlComment = Literal("--") + restOfLine
mySqlComment = Literal("#") + restOfLine
SQLParser.ignore(oracleSqlComment | mySqlComment)

"""A small Jinja-like template engine.

    {% extends "base.html" %}
    {% block content %}
      <h1>{{ page.title }}</h1>
      {% for post in posts | sort(attribute="date", reverse=true) %}
        <a href="{{ post.url }}">{{ post.title | truncate(60) }}</a>{% if not loop.last %}, {% endif %}
      {% else %}
        No posts yet.
      {% endfor %}
      {% include "footer.html" %}
    {% endblock %}

Supported: ``{{ expression }}``, ``{% if/elif/else %}``, ``{% for x in xs %}`` (with
``loop.index``, ``loop.first``, ... and an ``{% else %}`` for empty lists),
``{% set %}``, ``{% extends %}`` / ``{% block %}`` (with ``{{ super() }}``),
``{% include %}``, ``{# comments #}`` and ``-`` whitespace control (``{%-``, ``-%}``).

Expressions are parsed by a small Pratt parser, never ``eval``-ed. Attribute and
key access work on dicts and objects, but names starting with ``_`` are refused, so
templates can't reach Python internals. Output is HTML-escaped unless a value is
:class:`Markup` (e.g. via the ``safe`` filter).
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, NamedTuple

# ---- Values --------------------------------------------------------------------------------


class Markup(str):
    """A string that is already safe HTML and won't be escaped again."""

    def __html__(self) -> str:
        return str(self)


class Undefined:
    """What a missing variable evaluates to: empty, falsy and iterable, unless strict."""

    def __init__(self, name: str = ""):
        self._name = name

    def __str__(self) -> str:
        return ""

    def __bool__(self) -> bool:
        return False

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Undefined)

    def __hash__(self) -> int:
        return 0

    def __repr__(self) -> str:
        return f"Undefined({self._name!r})"


def escape(value: Any) -> Markup:
    if hasattr(value, "__html__"):
        return Markup(value.__html__())
    if value is None or isinstance(value, Undefined):
        return Markup("")
    return Markup(html.escape(str(value), quote=True))


class TemplateError(Exception):
    def __init__(self, message: str, name: str = "<string>", line: int | None = None):
        where = f"{name}, line {line}" if line else name
        super().__init__(f"{where}: {message}")
        self.message, self.name, self.line = message, name, line


class TemplateNotFound(TemplateError):
    pass


# ---- Lexer ---------------------------------------------------------------------------------

# Comments are matched first so a "}}" inside {# ... #} doesn't end them early.
TAG = re.compile(r"(\{#)(.*?)(#\})|(\{\{-?|\{%-?)(.*?)(-?\}\}|-?%\})", re.DOTALL)
RAW_END = re.compile(r"\{%-?\s*endraw\s*-?%\}")


@dataclass
class Token:
    kind: str  # "text", "var", "block"
    value: str
    line: int


def tokenize(source: str, name: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    line = 1
    trim_next = False
    while (m := TAG.search(source, pos)) is not None:
        text = source[pos:m.start()]
        if trim_next:
            text = text.lstrip()
        if m.group(1):
            opener, body, closer = m.group(1), m.group(2), m.group(3)
        else:
            opener, body, closer = m.group(4), m.group(5), m.group(6)
        if opener.endswith("-"):
            text = text.rstrip()
        if text:
            tokens.append(Token("text", text, line))
        line += source[pos:m.start()].count("\n")
        if opener != "{#":
            expected = "}}" if opener.startswith("{{") else "%}"
            if not closer.endswith(expected):
                raise TemplateError(f"expected {expected!r} to close {opener!r}", name, line)
            if opener.startswith("{%") and body.strip() == "raw":
                # Everything up to {% endraw %} is text, tags included.
                end = RAW_END.search(source, m.end())
                if end is None:
                    raise TemplateError("{% raw %} is never closed with {% endraw %}", name, line)
                raw = source[m.end():end.start()]
                if closer.startswith("-"):
                    raw = raw.lstrip()
                if end.group(0).startswith("{%-"):
                    raw = raw.rstrip()
                if raw:
                    tokens.append(Token("text", raw, line))
                line += source[m.start():end.end()].count("\n")
                trim_next = end.group(0).endswith("-%}")
                pos = end.end()
                continue
            if opener.startswith("{%") and body.strip() == "endraw":
                raise TemplateError("unexpected {% endraw %} (nothing open to close)", name, line)
            tokens.append(Token("var" if opener.startswith("{{") else "block", body.strip(), line))
        trim_next = closer.startswith("-")
        line += m.group(0).count("\n")
        pos = m.end()
    rest = source[pos:]
    if trim_next:
        rest = rest.lstrip()
    if rest:
        tokens.append(Token("text", rest, line))
    if re.search(r"\{\{|\{%|\{#", rest):
        raise TemplateError("tag opened but never closed", name, line + rest[:rest.find("{")].count("\n"))
    return tokens


# ---- Expressions ---------------------------------------------------------------------------

EXPR_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+\.\d+|\d+)|(?P<str>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>==|!=|<=|>=|\*\*|//|[-+*/%~<>()\[\]{}.,|:=]))"
)
KEYWORDS = {"and", "or", "not", "in", "is", "if", "else", "true", "false", "none", "True", "False", "None"}


class Expr:
    line = 0

    def eval(self, ctx: Context) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass
class Const(Expr):
    value: Any

    def eval(self, ctx: Context) -> Any:
        return self.value


@dataclass
class Name(Expr):
    name: str

    def eval(self, ctx: Context) -> Any:
        return ctx.lookup(self.name)


@dataclass
class ListExpr(Expr):
    items: list[Expr]

    def eval(self, ctx: Context) -> Any:
        return [i.eval(ctx) for i in self.items]


@dataclass
class DictExpr(Expr):
    items: list[tuple[Expr, Expr]]

    def eval(self, ctx: Context) -> Any:
        return {k.eval(ctx): v.eval(ctx) for k, v in self.items}


#: Attributes that could reach private data indirectly (e.g. "{0.__class__}".format(x)).
UNSAFE_ATTRIBUTES = {"format", "format_map", "mro", "func_globals", "gi_frame", "gi_code", "cr_frame", "f_globals"}


def get_attr(obj: Any, key: Any) -> Any:
    """Dict key first, then attribute, then index. Private names are never reachable."""
    if isinstance(key, str) and (key.startswith("_") or (key in UNSAFE_ATTRIBUTES and not isinstance(obj, Mapping))):
        raise TemplateError(f"access to {key!r} is not allowed in templates")
    if isinstance(obj, Undefined):
        return Undefined(str(key))
    if isinstance(obj, Mapping):
        if key in obj:
            return obj[key]
    if isinstance(key, str) and not isinstance(obj, Mapping):
        try:
            return getattr(obj, key)
        except AttributeError:
            pass
    if isinstance(key, int) and isinstance(obj, (list, tuple, str)):
        try:
            return obj[key]
        except IndexError:
            return Undefined(str(key))
    return Undefined(str(key))


@dataclass
class Attr(Expr):
    target: Expr
    key: Expr

    def eval(self, ctx: Context) -> Any:
        return get_attr(self.target.eval(ctx), self.key.eval(ctx))


@dataclass
class Call(Expr):
    func: Expr
    args: list[Expr]
    kwargs: dict[str, Expr]

    def eval(self, ctx: Context) -> Any:
        func = self.func.eval(ctx)
        if isinstance(func, Undefined):
            raise TemplateError(f"{_describe(self.func)} is not defined")
        if not callable(func):
            raise TemplateError(f"{_describe(self.func)} is not callable")
        args = [a.eval(ctx) for a in self.args]
        kwargs = {k: v.eval(ctx) for k, v in self.kwargs.items()}
        try:
            return func(*args, **kwargs)
        except TemplateError:
            raise
        except Exception as exc:  # e.g. a plugin global that raises: report where, not a traceback
            raise TemplateError(f"calling {_describe(self.func)} failed: {type(exc).__name__}: {exc}") from exc


@dataclass
class Filter(Expr):
    target: Expr
    name: str
    args: list[Expr]
    kwargs: dict[str, Expr]

    def eval(self, ctx: Context) -> Any:
        func = ctx.env.filters.get(self.name)
        if func is None:
            raise TemplateError(f"unknown filter {self.name!r}")
        value = self.target.eval(ctx)
        args = [a.eval(ctx) for a in self.args]
        kwargs = {k: v.eval(ctx) for k, v in self.kwargs.items()}
        try:
            return func(value, *args, **kwargs)
        except TemplateError:
            raise
        except Exception as exc:  # `none | length`, `[] | max`, a buggy plugin filter...
            shown = "undefined" if isinstance(value, Undefined) else type(value).__name__
            raise TemplateError(f"filter {self.name!r} failed on {shown} value: {type(exc).__name__}: {exc}") from exc


@dataclass
class Test(Expr):
    target: Expr
    name: str
    negate: bool

    def eval(self, ctx: Context) -> Any:
        value = self.target.eval(ctx)
        tests: dict[str, Callable[[Any], bool]] = {
            "defined": lambda v: not isinstance(v, Undefined),
            "undefined": lambda v: isinstance(v, Undefined),
            "none": lambda v: v is None,
            "string": lambda v: isinstance(v, str),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "iterable": lambda v: isinstance(v, Iterable) and not isinstance(v, Undefined),
            "odd": lambda v: isinstance(v, int) and v % 2 == 1,
            "even": lambda v: isinstance(v, int) and v % 2 == 0,
        }
        if self.name not in tests:
            raise TemplateError(f"unknown test {self.name!r}; use one of: {', '.join(sorted(tests))}")
        result = tests[self.name](value)
        return not result if self.negate else result


def _num(v: Any) -> Any:
    return 0 if isinstance(v, Undefined) or v is None else v


BINARY: dict[str, Callable[[Any, Any], Any]] = {
    "+": lambda a, b: _num(a) + _num(b),
    "-": lambda a, b: _num(a) - _num(b),
    "*": lambda a, b: _num(a) * _num(b),
    "/": lambda a, b: _num(a) / _num(b),
    "//": lambda a, b: _num(a) // _num(b),
    "%": lambda a, b: _num(a) % _num(b),
    "**": lambda a, b: _num(a) ** _num(b),
    "~": lambda a, b: f"{'' if a is None else a}{'' if b is None else b}",
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
}


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr

    def eval(self, ctx: Context) -> Any:
        if self.op == "and":
            left = self.left.eval(ctx)
            return self.right.eval(ctx) if left else left
        if self.op == "or":
            left = self.left.eval(ctx)
            return left if left else self.right.eval(ctx)
        try:
            return BINARY[self.op](self.left.eval(ctx), self.right.eval(ctx))
        except TypeError as exc:
            raise TemplateError(f"can't apply {self.op!r}: {exc}") from None
        except ZeroDivisionError:
            raise TemplateError("division by zero") from None


@dataclass
class Unary(Expr):
    op: str
    operand: Expr

    def eval(self, ctx: Context) -> Any:
        value = self.operand.eval(ctx)
        if self.op == "not":
            return not value
        return -_num(value) if self.op == "-" else +_num(value)


@dataclass
class Conditional(Expr):
    then: Expr
    test: Expr
    otherwise: Expr | None

    def eval(self, ctx: Context) -> Any:
        if self.test.eval(ctx):
            return self.then.eval(ctx)
        return self.otherwise.eval(ctx) if self.otherwise else Undefined()


def _describe(expr: Expr) -> str:
    if isinstance(expr, Name):
        return repr(expr.name)
    if isinstance(expr, Attr) and isinstance(expr.key, Const):
        return f"{_describe(expr.target)[:-1]}.{expr.key.value}'"
    return "this value"


class ExprParser:
    """Pratt parser for template expressions."""

    PRECEDENCE = {
        "or": 1, "and": 2,
        "in": 4, "not in": 4, "is": 4, "==": 4, "!=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4,
        "~": 5, "+": 6, "-": 6, "*": 7, "/": 7, "//": 7, "%": 7, "**": 9,
    }

    def __init__(self, source: str, name: str = "<string>", line: int = 0):
        self.source, self.name, self.line = source, name, line
        self.tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(source):
            if source[pos:].strip() == "":
                break
            m = EXPR_TOKEN.match(source, pos)
            if not m or m.end() == pos:
                raise self.error(f"unexpected character {source[pos:].strip()[0]!r}")
            kind = m.lastgroup or ""
            self.tokens.append((kind, m.group(kind)))
            pos = m.end()
        self.pos = 0

    def error(self, message: str) -> TemplateError:
        return TemplateError(f"{message} in {self.source!r}", self.name, self.line)

    def peek(self, offset: int = 0) -> tuple[str, str]:
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else ("end", "")

    def next(self) -> tuple[str, str]:
        tok = self.peek()
        self.pos += 1
        return tok

    def expect(self, value: str) -> None:
        kind, got = self.next()
        if got != value:
            raise self.error(f"expected {value!r} but found {got or 'the end'!r}")

    def at(self, value: str) -> bool:
        return self.peek()[1] == value and self.peek()[0] in ("op", "name")

    def parse(self) -> Expr:
        expr = self.expression()
        if self.peek()[0] != "end":
            raise self.error(f"unexpected {self.peek()[1]!r}")
        return expr

    def expression(self) -> Expr:
        expr = self.binary(0)
        if self.at("if"):
            self.next()
            test = self.binary(0)
            otherwise = None
            if self.at("else"):
                self.next()
                otherwise = self.expression()
            expr = Conditional(expr, test, otherwise)
        return expr

    def binary(self, min_prec: int) -> Expr:
        left = self.unary()
        while True:
            kind, value = self.peek()
            op = value
            if value == "not" and self.peek(1)[1] == "in":
                op = "not in"
            if kind not in ("op", "name") or op not in self.PRECEDENCE:
                return left
            prec = self.PRECEDENCE[op]
            if prec <= min_prec and op != "**":
                return left
            self.next()
            if op == "not in":
                self.next()
            if op == "is":
                negate = self.at("not")
                if negate:
                    self.next()
                kind, test_name = self.next()
                if kind != "name":
                    raise self.error("expected a test name after 'is'")
                left = Test(left, test_name, negate)
                continue
            right = self.binary(prec - 1 if op == "**" else prec)
            left = Binary(op, left, right)

    def unary(self) -> Expr:
        if self.at("not"):
            self.next()
            return Unary("not", self.binary(3))
        if self.at("-") or self.at("+"):
            op = self.next()[1]
            return Unary(op, self.unary())
        return self.postfix(self.primary())

    def primary(self) -> Expr:
        kind, value = self.next()
        if kind == "num":
            return Const(float(value) if "." in value else int(value))
        if kind == "str":
            return Const(bytes(value[1:-1], "utf-8").decode("unicode_escape") if "\\" in value else value[1:-1])
        if kind == "name":
            if value in ("true", "True"):
                return Const(True)
            if value in ("false", "False"):
                return Const(False)
            if value in ("none", "None"):
                return Const(None)
            if value in KEYWORDS:
                raise self.error(f"unexpected keyword {value!r}")
            return Name(value)
        if value == "(":
            expr = self.expression()
            self.expect(")")
            return expr
        if value == "[":
            items = []
            while not self.at("]"):
                items.append(self.expression())
                if not self.at("]"):
                    self.expect(",")
            self.expect("]")
            return ListExpr(items)
        if value == "{":
            pairs = []
            while not self.at("}"):
                key = self.expression()
                self.expect(":")
                pairs.append((key, self.expression()))
                if not self.at("}"):
                    self.expect(",")
            self.expect("}")
            return DictExpr(pairs)
        raise self.error(f"unexpected {value or 'end of expression'!r}")

    def arguments(self) -> tuple[list[Expr], dict[str, Expr]]:
        args: list[Expr] = []
        kwargs: dict[str, Expr] = {}
        self.expect("(")
        while not self.at(")"):
            if self.peek()[0] == "name" and self.peek(1)[1] == "=":
                key = self.next()[1]
                self.next()
                kwargs[key] = self.expression()
            else:
                if kwargs:
                    raise self.error("positional argument after keyword argument")
                args.append(self.expression())
            if not self.at(")"):
                self.expect(",")
        self.expect(")")
        return args, kwargs

    def postfix(self, expr: Expr) -> Expr:
        while True:
            if self.at("."):
                self.next()
                kind, value = self.next()
                if kind not in ("name", "num"):
                    raise self.error("expected a name after '.'")
                expr = Attr(expr, Const(int(value) if kind == "num" else value))
            elif self.at("["):
                self.next()
                key = self.expression()
                self.expect("]")
                expr = Attr(expr, key)
            elif self.at("("):
                args, kwargs = self.arguments()
                expr = Call(expr, args, kwargs)
            elif self.at("|"):
                self.next()
                kind, name = self.next()
                if kind != "name":
                    raise self.error("expected a filter name after '|'")
                args, kwargs = self.arguments() if self.at("(") else ([], {})
                expr = Filter(expr, name, args, kwargs)
            else:
                return expr


# ---- Template nodes ------------------------------------------------------------------------


class Node:
    line = 0

    def render(self, ctx: Context, out: list[str]) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass
class Text(Node):
    text: str

    def render(self, ctx: Context, out: list[str]) -> None:
        out.append(self.text)


@dataclass
class Output(Node):
    expr: Expr
    line: int = 0

    def render(self, ctx: Context, out: list[str]) -> None:
        value = self.expr.eval(ctx)
        if isinstance(value, Undefined) and ctx.env.strict:
            raise TemplateError(f"{_describe(self.expr)} is not defined")
        out.append(escape(value) if ctx.env.autoescape else ("" if value is None else str(value)))


@dataclass
class If(Node):
    branches: list[tuple[Expr | None, list[Node]]]

    def render(self, ctx: Context, out: list[str]) -> None:
        for test, body in self.branches:
            if test is None or test.eval(ctx):
                render_nodes(body, ctx, out)
                return


@dataclass
class Loop:
    index0: int
    length: int
    _items: list[Any] = field(default_factory=list, repr=False)
    _last_changed: Any = field(default=None, repr=False)

    @property
    def index(self) -> int:
        return self.index0 + 1

    @property
    def revindex(self) -> int:
        return self.length - self.index0

    @property
    def revindex0(self) -> int:
        return self.length - self.index0 - 1

    @property
    def previtem(self) -> Any:
        return self._items[self.index0 - 1] if self.index0 > 0 else Undefined("loop.previtem")

    @property
    def nextitem(self) -> Any:
        return self._items[self.index0 + 1] if self.index0 + 1 < self.length else Undefined("loop.nextitem")

    @property
    def first(self) -> bool:
        return self.index0 == 0

    @property
    def last(self) -> bool:
        return self.index0 == self.length - 1

    def cycle(self, *values: Any) -> Any:
        return values[self.index0 % len(values)] if values else ""

    def changed(self, *values: Any) -> bool:
        """True on the first pass and whenever the values differ from the last call, e.g.
        {% if loop.changed(post.date.year) %}<h2>{{ post.date.year }}</h2>{% endif %}."""
        if self._last_changed is None or self._last_changed != values:
            self._last_changed = values
            return True
        return False


@dataclass
class For(Node):
    targets: list[str]
    iterable: Expr
    body: list[Node]
    empty: list[Node]

    def render(self, ctx: Context, out: list[str]) -> None:
        value = self.iterable.eval(ctx)
        if isinstance(value, Mapping):
            value = value.items() if len(self.targets) == 2 else value.keys()
        if value is None:
            value = []
        try:
            items = list(value)
        except TypeError:
            raise TemplateError(f"can't loop over {type(value).__name__}") from None
        if not items:
            render_nodes(self.empty, ctx, out)
            return
        ctx.push()
        loop = Loop(0, len(items), items)
        try:
            for i, item in enumerate(items):
                if len(self.targets) == 1:
                    ctx.set(self.targets[0], item)
                else:
                    try:
                        values = list(item)
                    except TypeError:
                        raise TemplateError(f"can't unpack {type(item).__name__} into {len(self.targets)} names") from None
                    if len(values) != len(self.targets):
                        raise TemplateError(f"expected {len(self.targets)} values to unpack, got {len(values)}")
                    for name, v in zip(self.targets, values):
                        ctx.set(name, v)
                loop.index0 = i
                ctx.set("loop", loop)
                render_nodes(self.body, ctx, out)
        finally:
            ctx.pop()


@dataclass
class Set(Node):
    name: str
    expr: Expr

    def render(self, ctx: Context, out: list[str]) -> None:
        ctx.set(self.name, self.expr.eval(ctx), outer=True)


@dataclass
class Block(Node):
    name: str
    body: list[Node]

    def render(self, ctx: Context, out: list[str]) -> None:
        chain = ctx.blocks.get(self.name, [(ctx.template_name, self.body)])
        _render_block(chain, 0, ctx, out)


def _render_block(chain: list[tuple[str, list[Node]]], level: int, ctx: Context, out: list[str]) -> None:
    """Renders one version of a block. Errors name the template the block was written in
    (page.html), not the one that placed it (base.html)."""
    def super_() -> Markup:
        if level + 1 >= len(chain):
            return Markup("")
        parent: list[str] = []
        _render_block(chain, level + 1, ctx, parent)
        return Markup("".join(parent))

    owner, body = chain[level]
    saved_name = ctx.template_name
    ctx.template_name = owner
    ctx.push()
    ctx.set("super", super_)
    try:
        render_nodes(body, ctx, out)
    finally:
        ctx.pop()
        ctx.template_name = saved_name


@dataclass
class Include(Node):
    template: Expr

    def render(self, ctx: Context, out: list[str]) -> None:
        name = self.template.eval(ctx)
        template = ctx.env.get_template(str(name))
        template.render_into(ctx, out)


def render_nodes(nodes: list[Node], ctx: Context, out: list[str]) -> None:
    for node in nodes:
        try:
            node.render(ctx, out)
        except TemplateError as exc:
            if exc.line is None and node.line:
                raise TemplateError(exc.message, ctx.template_name, node.line) from None
            raise


# ---- Parser --------------------------------------------------------------------------------


class Parser:
    def __init__(self, tokens: list[Token], name: str):
        self.tokens, self.name, self.pos = tokens, name, 0
        self.extends: Expr | None = None
        self.blocks: dict[str, list[Node]] = {}
        self.includes: list[str] = []

    def expr(self, source: str, line: int) -> Expr:
        return ExprParser(source, self.name, line).parse()

    def parse(self) -> list[Node]:
        nodes, end = self.until(())
        if end is not None:
            raise TemplateError(f"unexpected {{% {end.value} %}}", self.name, end.line)
        return nodes

    def until(self, enders: tuple[str, ...]) -> tuple[list[Node], Token | None]:
        nodes: list[Node] = []
        while self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            self.pos += 1
            if tok.kind == "text":
                nodes.append(Text(tok.value))
                continue
            if tok.kind == "var":
                if not tok.value:
                    raise TemplateError("empty {{ }}", self.name, tok.line)
                nodes.append(Output(self.expr(tok.value, tok.line), tok.line))
                continue
            word = tok.value.split(None, 1)[0] if tok.value else ""
            if word in enders:
                return nodes, tok
            node = self.block_tag(tok, word)
            if node is not None:
                node.line = tok.line
                nodes.append(node)
        if enders:
            line = self.tokens[-1].line if self.tokens else 1
            raise TemplateError(f"missing {{% {enders[-1]} %}}", self.name, line)
        return nodes, None

    def block_tag(self, tok: Token, word: str) -> Node | None:
        rest = tok.value[len(word):].strip()
        line = tok.line
        if word == "if":
            branches: list[tuple[Expr | None, list[Node]]] = []
            test: Expr | None = self.expr(rest, line)
            while True:
                body, end = self.until(("elif", "else", "endif"))
                branches.append((test, body))
                assert end is not None
                end_word = end.value.split(None, 1)[0]
                if end_word == "endif":
                    return If(branches)
                if end_word == "elif":
                    test = self.expr(end.value[4:].strip(), end.line)
                else:
                    body, end = self.until(("endif",))
                    branches.append((None, body))
                    return If(branches)
        if word == "for":
            m = re.match(r"^([A-Za-z_]\w*)(?:\s*,\s*([A-Za-z_]\w*))?\s+in\s+(.+)$", rest, re.DOTALL)
            if not m:
                raise TemplateError("use {% for item in items %} or {% for key, value in mapping %}", self.name, line)
            targets = [t for t in (m.group(1), m.group(2)) if t]
            iterable = self.expr(m.group(3), line)
            body, end = self.until(("else", "endfor"))
            empty: list[Node] = []
            assert end is not None
            if end.value.split(None, 1)[0] == "else":
                empty, _ = self.until(("endfor",))
            return For(targets, iterable, body, empty)
        if word == "set":
            m = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", rest, re.DOTALL)
            if not m:
                raise TemplateError("use {% set name = value %}", self.name, line)
            if m.group(1).startswith("_"):
                raise TemplateError("names starting with _ are not allowed", self.name, line)
            return Set(m.group(1), self.expr(m.group(2), line))
        if word == "block":
            if not re.fullmatch(r"[A-Za-z_]\w*", rest):
                raise TemplateError("use {% block name %}", self.name, line)
            if rest in self.blocks:
                raise TemplateError(f"block {rest!r} is defined twice", self.name, line)
            body, end = self.until(("endblock",))
            assert end is not None
            closing = end.value[len("endblock"):].strip()
            if closing and closing != rest:
                raise TemplateError(f"{{% endblock {closing} %}} closes block {rest!r}", self.name, end.line)
            self.blocks[rest] = body
            return Block(rest, body)
        if word == "extends":
            if self.extends is not None:
                raise TemplateError("only one {% extends %} per template", self.name, line)
            self.extends = self.expr(rest, line)
            return None
        if word == "include":
            expr = self.expr(rest, line)
            if isinstance(expr, Const):
                self.includes.append(str(expr.value))
            return Include(expr)
        if word in ("endif", "endfor", "endblock", "elif", "else"):
            raise TemplateError(f"unexpected {{% {word} %}} (nothing open to close)", self.name, line)
        raise TemplateError(f"unknown tag {{% {word} %}}", self.name, line)


# ---- Context and templates -----------------------------------------------------------------


class Context:
    def __init__(self, env: Environment, variables: Mapping[str, Any], template_name: str):
        self.env = env
        self.template_name = template_name
        self.scopes: list[dict[str, Any]] = [dict(env.globals), dict(variables)]
        self.blocks: dict[str, list[tuple[str, list[Node]]]] = {}

    def lookup(self, name: str) -> Any:
        if name.startswith("_"):
            raise TemplateError(f"access to private name {name!r} is not allowed")
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return Undefined(name)

    def set(self, name: str, value: Any, outer: bool = False) -> None:
        # {% set %} inside a loop still assigns in the loop's scope, like Jinja.
        self.scopes[-1][name] = value

    def push(self) -> None:
        self.scopes.append({})

    def pop(self) -> None:
        self.scopes.pop()


class Template:
    def __init__(self, env: Environment, source: str, name: str = "<string>"):
        self.env, self.name = env, name
        parser = Parser(tokenize(source, name), name)
        self.nodes = parser.parse()
        self.extends = parser.extends
        self.blocks = parser.blocks
        self.includes = parser.includes

    def render(self, variables: Mapping[str, Any] | None = None, **kwargs: Any) -> str:
        ctx = Context(self.env, {**(variables or {}), **kwargs}, self.name)
        out: list[str] = []
        self.render_into(ctx, out)
        return "".join(out)

    def render_into(self, ctx: Context, out: list[str]) -> None:
        # Walk up the {% extends %} chain, collecting block overrides child-first.
        chain: list[Template] = [self]
        seen = {self.name}
        current = self
        while current.extends is not None:
            parent_name = str(current.extends.eval(ctx))
            if parent_name in seen:
                raise TemplateError(f"{{% extends %}} loop through {parent_name!r}", current.name)
            seen.add(parent_name)
            current = self.env.get_template(parent_name)
            chain.append(current)
        saved_blocks, saved_name = ctx.blocks, ctx.template_name
        blocks: dict[str, list[tuple[str, list[Node]]]] = {}
        for template in chain:
            for name, body in template.blocks.items():
                blocks.setdefault(name, []).append((template.name, body))
        # An included template's own blocks shouldn't leak into the page that includes it.
        ctx.blocks = {**saved_blocks, **blocks} if self.extends is None and saved_blocks else blocks
        root = chain[-1]
        ctx.template_name = root.name
        try:
            render_nodes(root.nodes, ctx, out)
        finally:
            ctx.blocks, ctx.template_name = saved_blocks, saved_name

    def dependencies(self) -> set[str]:
        """Names of every template this one can load (extends and literal includes), recursively."""
        found: set[str] = set()
        pending = [self]
        while pending:
            template = pending.pop()
            names = list(template.includes)
            if isinstance(template.extends, Const):
                names.append(str(template.extends.value))
            for name in names:
                if name not in found:
                    found.add(name)
                    try:
                        pending.append(self.env.get_template(name))
                    except TemplateNotFound:
                        pass
        return found


class Environment:
    """Loads templates from directories (earlier directories win) and holds filters and globals.

    Put a site's own ``templates/`` before the theme's so the site can override any
    theme template by giving it the same name.
    """

    def __init__(self, search_path: Iterable[str | Path] = (), autoescape: bool = True, strict: bool = False):
        self.search_path = [Path(p) for p in search_path]
        self.autoescape = autoescape
        self.strict = strict
        self.filters: dict[str, Callable[..., Any]] = dict(FILTERS)
        self.globals: dict[str, Any] = {"range": _safe_range}
        self._cache: dict[str, tuple[float, Template]] = {}

    def find(self, name: str) -> Path:
        if not name or name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
            raise TemplateNotFound(f"invalid template name {name!r}", name)
        for directory in self.search_path:
            path = directory / name
            if path.is_file():
                return path
        searched = ", ".join(str(d) for d in self.search_path) or "nowhere"
        raise TemplateNotFound(f"template {name!r} not found (looked in {searched})", name)

    def path_of(self, name: str) -> Path:
        return self.find(name)

    def get_template(self, name: str) -> Template:
        path = self.find(name)
        mtime = path.stat().st_mtime_ns
        cached = self._cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        template = Template(self, path.read_text(encoding="utf-8"), name)
        self._cache[name] = (mtime, template)
        return template

    def from_string(self, source: str, name: str = "<string>") -> Template:
        return Template(self, source, name)

    def render(self, name: str, variables: Mapping[str, Any] | None = None, **kwargs: Any) -> str:
        return self.get_template(name).render(variables, **kwargs)


def _safe_range(*args: int) -> range:
    r = range(*args)
    if len(r) > 100_000:
        raise TemplateError("range() is limited to 100,000 items")
    return r


# ---- Filters -------------------------------------------------------------------------------


ATTR_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b, "not in": lambda a, b: a not in b,
}


def _attr_matches(item: Any, attribute: str, args: tuple[Any, ...]) -> bool:
    """selectattr("draft") is truthiness, selectattr("section", "posts") is equality,
    selectattr("weight", ">", 5) uses an operator."""
    value = item
    for part in attribute.split("."):
        value = get_attr(value, part)
    if not args:
        return bool(value) and not isinstance(value, Undefined)
    if len(args) == 1:
        return value == args[0]
    if len(args) == 2 and args[0] in ATTR_OPERATORS:
        if isinstance(value, Undefined) or value is None:
            return False
        try:
            return ATTR_OPERATORS[args[0]](value, args[1])
        except TypeError:
            raise TemplateError(f"can't compare {attribute}={value!r} {args[0]} {args[1]!r}") from None
    raise TemplateError(f"selectattr/rejectattr take (attribute), (attribute, value) or (attribute, operator, value); "
                        f"operators: {', '.join(ATTR_OPERATORS)}")


def _sort_key(attribute: str | None) -> Callable[[Any], Any]:
    def key(item: Any) -> Any:
        value = item
        if attribute:
            for part in attribute.split("."):
                value = get_attr(value, part)
        if isinstance(value, Undefined) or value is None:
            return (0, "")
        if isinstance(value, datetime):
            return (1, value.replace(tzinfo=None).isoformat())
        if isinstance(value, date):
            return (1, value.isoformat())
        return (1, value)
    return key


def _truncate(value: Any, length: int = 255, end: str = "…", killwords: bool = False) -> str:
    text = str(value)
    if len(text) <= length:
        return text
    cut = text[: max(0, length - len(end))]
    if not killwords and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + end


def _date(value: Any, fmt: str = "%B %-d, %Y") -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, (date, datetime)):
        return "" if isinstance(value, Undefined) or value is None else str(value)
    # %-d isn't portable (Windows); expand it ourselves.
    fmt = fmt.replace("%-d", str(value.day)).replace("%-m", str(value.month))
    return value.strftime(fmt)


def _join(value: Any, separator: str = "", attribute: str | None = None) -> Any:
    items = list(value)
    if attribute:
        items = [get_attr(i, attribute) for i in items]
    if any(hasattr(i, "__html__") for i in items):
        return Markup(escape(separator).join(escape(i) for i in items))
    return separator.join(str(i) for i in items)


def _batch(value: Any, size: int, fill: Any = None) -> list[list[Any]]:
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise TemplateError(f"batch size must be a whole number of at least 1, not {size!r}")
    items = list(value)
    rows = [items[i:i + size] for i in range(0, len(items), size)]
    if fill is not None and rows and len(rows[-1]) < size:
        rows[-1] += [fill] * (size - len(rows[-1]))
    return rows


def _not_mapping(name: str, value: Any) -> Any:
    raise TemplateError(f"{name} needs a mapping (like page.extra), not {type(value).__name__}")


def _dictsort(value: Any, by: str = "key", reverse: bool = False, case_sensitive: bool = False) -> list[tuple[Any, Any]]:
    if isinstance(value, Undefined) or value is None:
        return []
    if not isinstance(value, Mapping):
        _not_mapping("dictsort", value)
    if by not in ("key", "value"):
        raise TemplateError(f"dictsort can sort by 'key' or 'value', not {by!r}")
    position = 0 if by == "key" else 1

    def key(pair: tuple[Any, Any]) -> Any:
        v = pair[position]
        return v.lower() if isinstance(v, str) and not case_sensitive else _sort_key(None)(v)

    try:
        return sorted(value.items(), key=key, reverse=reverse)
    except TypeError:
        raise TemplateError(f"dictsort can't compare these {by}s") from None


class Group(NamedTuple):
    """One groupby result: use g.grouper and g.list, or unpack with {% for key, items in ... %}."""
    grouper: Any
    list: list[Any]


def _groupby(value: Any, attribute: str) -> list[Group]:
    groups: dict[Any, list[Any]] = {}
    for item in value:
        key = item
        for part in attribute.split("."):
            key = get_attr(key, part)
        groups.setdefault(None if isinstance(key, Undefined) else key, []).append(item)
    return [Group(k, v) for k, v in groups.items()]


def _striptags(value: Any) -> str:
    text = re.sub(r"<[^>]*>", " ", str(value))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _slugify(value: Any) -> str:
    from .markdown import slugify

    return slugify(str(value))


def _wordcount(value: Any) -> int:
    return len(re.findall(r"\w+", _striptags(value)))


FILTERS: dict[str, Callable[..., Any]] = {
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "title": lambda v: str(v).title(),
    "capitalize": lambda v: str(v).capitalize(),
    "trim": lambda v: str(v).strip(),
    "length": lambda v: len(v),
    "count": lambda v: len(v),
    "default": lambda v, d="", boolean=False: d if isinstance(v, Undefined) or (boolean and not v) else v,
    "join": _join,
    "first": lambda v: next(iter(v), Undefined("first")),
    "last": lambda v: list(v)[-1] if list(v) else Undefined("last"),
    "reverse": lambda v: v[::-1] if isinstance(v, str) else list(reversed(list(v))),
    "sort": lambda v, reverse=False, attribute=None: sorted(v, key=_sort_key(attribute), reverse=reverse),
    "unique": lambda v: list(dict.fromkeys(v)),
    "list": lambda v: list(v),
    "batch": _batch,
    "dictsort": _dictsort,
    "items": lambda v: list(v.items()) if isinstance(v, Mapping) else [] if isinstance(v, Undefined) or v is None else _not_mapping("items", v),
    "groupby": _groupby,
    "map": lambda v, attribute: [get_attr(i, attribute) for i in v],
    "selectattr": lambda v, attribute, *args: [i for i in v if _attr_matches(i, attribute, args)],
    "rejectattr": lambda v, attribute, *args: [i for i in v if not _attr_matches(i, attribute, args)],
    "slice": lambda v, start=0, stop=None: list(v)[start:stop],
    "truncate": _truncate,
    "wordcount": _wordcount,
    "reading_time": lambda v, wpm=200: max(1, round(_wordcount(v) / wpm)),
    "replace": lambda v, old, new: str(v).replace(old, new),
    "striptags": _striptags,
    "slugify": _slugify,
    "date": _date,
    "safe": lambda v: Markup(v),
    "escape": escape,
    "e": escape,
    "urlencode": lambda v: urllib.parse.quote(str(v), safe=""),
    "tojson": lambda v: Markup(json.dumps(v, default=str).replace("<", "\\u003c").replace(">", "\\u003e")
                               .replace("&", "\\u0026")),
    "int": lambda v, default=0: int(v) if str(v).lstrip("-").isdigit() else default,
    "float": lambda v, default=0.0: float(v) if re.fullmatch(r"-?\d+(\.\d+)?", str(v)) else default,
    "string": lambda v: str(v),
    "round": lambda v, precision=0: round(float(v), precision),
    "abs": lambda v: abs(v),
    "sum": lambda v, attribute=None: sum(get_attr(i, attribute) for i in v) if attribute else sum(v),
    "min": lambda v: min(v),
    "max": lambda v: max(v),
    "indent": lambda v, width=4: str(v).replace("\n", "\n" + " " * width),
}

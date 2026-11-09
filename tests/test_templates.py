from datetime import date

import pytest

from pagewright.templates import Environment, Markup, TemplateError, TemplateNotFound, Undefined


@pytest.fixture
def env():
    return Environment()


def r(env, source, **ctx):
    return env.from_string(source).render(ctx)


# ---- Output and escaping -------------------------------------------------------------------


def test_variables_and_autoescape(env):
    assert r(env, "Hi {{ name }}!", name="<Ana>") == "Hi &lt;Ana&gt;!"
    assert r(env, "{{ html | safe }}", html="<b>x</b>") == "<b>x</b>"
    assert r(env, "{{ m }}", m=Markup("<i>ok</i>")) == "<i>ok</i>"
    assert r(env, "[{{ missing }}]") == "[]"
    assert r(env, "{{ none_value }}", none_value=None) == ""


def test_attribute_and_index_access(env):
    page = {"title": "T", "tags": ["a", "b"], "meta": {"x": 1}}
    assert r(env, "{{ page.title }} {{ page.tags[1] }} {{ page['meta'].x }} {{ page.tags.0 }}", page=page) == "T b 1 a"

    class Obj:
        name = "obj"
    assert r(env, "{{ o.name }} {{ o.nope }}", o=Obj()) == "obj "


def test_expressions(env):
    assert r(env, "{{ 1 + 2 * 3 }} {{ (1 + 2) * 3 }} {{ 7 // 2 }} {{ 7 % 3 }} {{ 2 ** 3 ** 2 }}") == "7 9 3 1 512"
    assert r(env, "{{ 'a' ~ 1 ~ none }}") == "a1"
    assert r(env, "{{ 3 > 2 and 'yes' or 'no' }} {{ not true }}") == "yes False"
    assert r(env, "{{ 'b' in ['a', 'b'] }} {{ 'z' not in 'abc' }}") == "True True"
    assert r(env, "{{ 'big' if n > 10 else 'small' }}", n=3) == "small"
    assert r(env, "{{ -n }} {{ [1, 2][0] }} {{ {'k': 'v'}.k }}", n=5) == "-5 1 v"


def test_tests(env):
    assert r(env, "{{ x is defined }} {{ y is undefined }} {{ 3 is odd }} {{ 4 is not odd }}", x=1) == (
        "True True True True"
    )
    with pytest.raises(TemplateError, match="unknown test"):
        r(env, "{{ x is shiny }}")


# ---- Control flow --------------------------------------------------------------------------


def test_if_elif_else(env):
    src = "{% if n > 10 %}big{% elif n > 5 %}medium{% else %}small{% endif %}"
    assert [r(env, src, n=n) for n in (20, 7, 1)] == ["big", "medium", "small"]


def test_for_loop_variables_and_else(env):
    src = "{% for x in xs %}{{ loop.index }}:{{ x }}{% if not loop.last %}, {% endif %}{% else %}none{% endfor %}"
    assert r(env, src, xs=["a", "b", "c"]) == "1:a, 2:b, 3:c"
    assert r(env, src, xs=[]) == "none"
    assert r(env, src) == "none"  # undefined iterates as empty
    assert r(env, "{% for k, v in d %}{{ k }}={{ v }};{% endfor %}", d={"a": 1, "b": 2}) == "a=1;b=2;"
    assert r(env, "{% for x in xs %}{{ loop.cycle('odd', 'even') }} {% endfor %}", xs=[1, 2, 3]) == "odd even odd "


def test_loop_variables_dont_leak(env):
    assert r(env, "{% for x in [1, 2] %}{% endfor %}[{{ x }}{{ loop }}]") == "[]"


def test_set(env):
    assert r(env, "{% set greeting = 'hi ' ~ name %}{{ greeting }}", name="Bo") == "hi Bo"


def test_whitespace_control(env):
    assert r(env, "a  {%- if true -%}  b  {%- endif -%}  c") == "abc"
    assert r(env, "<ul>\n  {%- for x in [1, 2] %}\n  <li>{{ x }}</li>\n  {%- endfor %}\n</ul>") == (
        "<ul>\n  <li>1</li>\n  <li>2</li>\n</ul>"
    )


def test_comments(env):
    assert r(env, "a{# ignored {{ x }} {% if %} #}b") == "ab"
    assert r(env, "a{#\nmulti\nline\n#}b{{ 1 }}") == "ab1"


# ---- Filters -------------------------------------------------------------------------------


def test_common_filters(env):
    assert r(env, "{{ 'hello' | upper }} {{ 'A' | lower }} {{ xs | length }} {{ xs | join(', ') }}", xs=[1, 2, 3]) == (
        "HELLO a 3 1, 2, 3"
    )
    assert r(env, "{{ missing | default('n/a') }} {{ '' | default('empty', boolean=true) }}") == "n/a empty"
    assert r(env, "{{ 'The quick brown fox jumps' | truncate(15) }}") == "The quick…"
    assert r(env, "{{ 'Hello, World!' | slugify }}") == "hello-world"
    assert r(env, "{{ '<p>Hi <b>there</b></p>' | striptags }}") == "Hi there"
    assert r(env, "{{ d | date('%Y-%m-%d') }} {{ d | date }}", d=date(2026, 3, 7)) == "2026-03-07 March 7, 2026"
    assert r(env, "{{ '2026-01-02' | date('%d/%m') }}") == "02/01"
    assert r(env, "{{ 'a b' | urlencode }} {{ 3.14159 | round(2) }} {{ '42' | int + 1 }}") == "a%20b 3.14 43"


def test_sort_groupby_batch(env):
    posts = [{"t": "b", "d": date(2026, 1, 2), "cat": "x"}, {"t": "a", "d": date(2026, 1, 3), "cat": "y"},
             {"t": "c", "d": None, "cat": "x"}]
    assert r(env, "{% for p in posts | sort(attribute='d', reverse=true) %}{{ p.t }}{% endfor %}", posts=posts) == "abc"
    assert r(env, "{% for c, ps in posts | groupby('cat') %}{{ c }}{{ ps | length }}{% endfor %}", posts=posts) == "x2y1"
    assert r(env, "{% for row in [1,2,3,4,5] | batch(2) %}[{{ row | join }}]{% endfor %}") == "[12][34][5]"
    assert r(env, "{{ posts | map('t') | join }}", posts=posts) == "bac"


def test_tojson_is_safe_inside_script_tags(env):
    assert r(env, "{{ v | tojson }}", v={"a": "</script>"}) == '{"a": "\\u003c/script\\u003e"}'


def test_join_escapes_unsafe_items(env):
    assert r(env, "{{ xs | join(', ') }}", xs=["<a>", "b"]) == "&lt;a&gt;, b"


def test_reading_time(env):
    assert r(env, "{{ text | reading_time }}", text="word " * 450) == "2"


def test_custom_filters_and_globals():
    env = Environment()
    env.filters["shout"] = lambda v: str(v).upper() + "!"
    env.globals["site_name"] = "My Site"
    assert env.from_string("{{ 'hi' | shout }} {{ site_name }}").render() == "HI! My Site"


# ---- Inheritance and includes --------------------------------------------------------------


@pytest.fixture
def files(tmp_path):
    theme, site = tmp_path / "theme", tmp_path / "site"
    theme.mkdir()
    site.mkdir()
    (theme / "base.html").write_text(
        "<title>{% block title %}Site{% endblock %}</title><main>{% block content %}{% endblock %}</main>"
        "{% include 'footer.html' %}"
    )
    (theme / "footer.html").write_text("<footer>theme footer</footer>")
    (theme / "post.html").write_text(
        "{% extends 'base.html' %}{% block title %}{{ page.title }} | {{ super() }}{% endblock %}"
        "{% block content %}<h1>{{ page.title }}</h1>{% endblock %}ignored outside blocks"
    )
    (site / "footer.html").write_text("<footer>site footer {{ year }}</footer>")
    return Environment([site, theme])


def test_extends_blocks_super_and_override_order(files):
    out = files.render("post.html", page={"title": "Hello"}, year=2026)
    assert out == "<title>Hello | Site</title><main><h1>Hello</h1></main><footer>site footer 2026</footer>"


def test_multi_level_inheritance(tmp_path):
    (tmp_path / "a.html").write_text("[{% block x %}A{% endblock %}]")
    (tmp_path / "b.html").write_text("{% extends 'a.html' %}{% block x %}B{{ super() }}{% endblock %}")
    (tmp_path / "c.html").write_text("{% extends 'b.html' %}{% block x %}C{{ super() }}{% endblock %}")
    assert Environment([tmp_path]).render("c.html") == "[CBA]"


def test_dependencies(files):
    assert files.get_template("post.html").dependencies() == {"base.html", "footer.html"}


def test_template_cache_reloads_changed_files(tmp_path):
    import os
    import time

    path = tmp_path / "t.html"
    path.write_text("one")
    env = Environment([tmp_path])
    assert env.render("t.html") == "one"
    path.write_text("two")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert env.render("t.html") == "two"


# ---- Errors and safety ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, message",
    [
        ("{% if x %}no end", "missing {% endif %}"),
        ("{% for x %}{% endfor %}", "{% for item in items %}"),
        ("{% wibble %}", "unknown tag"),
        ("{{ x | nope }}", "unknown filter"),
        ("{{ 1 + }}", "unexpected"),
        ("{{ x ]", "never closed"),
        ("{# unclosed comment", "never closed"),
        ("{% endfor %}", "unexpected {% endfor %}"),
        ("{% block a %}{% endblock b %}", "closes block 'a'"),
        ("{% block a %}{% endblock %}{% block a %}{% endblock %}", "defined twice"),
        ("{{ 1 / 0 }}", "division by zero"),
        ("{{ 'a' + 1 }}", "can't apply"),
        ("{{ notafunc() }}", "is not defined"),
    ],
)
def test_errors_are_explained(env, source, message):
    with pytest.raises(TemplateError, match=message.replace("{", r"\{").replace("(", r"\(").replace("[", r"\[")
                       .replace("]", r"\]").replace("}", r"\}").replace("+", r"\+")):
        r(env, source)


def test_errors_report_the_line(env):
    with pytest.raises(TemplateError) as info:
        env.from_string("line 1\nline 2\n{{ x | nope }}", name="page.html").render()
    assert info.value.line == 3 and "page.html, line 3" in str(info.value)


@pytest.mark.parametrize(
    "source",
    [
        "{{ x.__class__ }}",
        "{{ x._private }}",
        "{{ ''.__class__.__mro__ }}",
        "{{ '{0.__class__}'.format(x) }}",
        "{{ __builtins__ }}",
        "{% set _secret = 1 %}",
        "{{ x['__dict__'] }}",
    ],
)
def test_private_access_is_blocked(env, source):
    with pytest.raises(TemplateError, match="not allowed"):
        r(env, source, x="value")


def test_strict_mode_reports_missing_variables():
    env = Environment(strict=True)
    with pytest.raises(TemplateError, match="'titel' is not defined"):
        env.from_string("{{ titel }}").render(title="x")


def test_extends_loop_is_detected(tmp_path):
    (tmp_path / "a.html").write_text("{% extends 'b.html' %}")
    (tmp_path / "b.html").write_text("{% extends 'a.html' %}")
    with pytest.raises(TemplateError, match="loop"):
        Environment([tmp_path]).render("a.html")


def test_missing_and_unsafe_template_names(tmp_path):
    env = Environment([tmp_path])
    with pytest.raises(TemplateNotFound, match="not found"):
        env.render("nope.html")
    for bad in ("../secret.html", "/etc/passwd", "C:/x.html", ""):
        with pytest.raises(TemplateNotFound, match="invalid template name"):
            env.render(bad)


def test_range_is_limited(env):
    assert r(env, "{% for i in range(3) %}{{ i }}{% endfor %}") == "012"
    with pytest.raises(TemplateError, match="limited"):
        r(env, "{{ range(10 ** 9) | length }}")


def test_undefined_behaves_quietly():
    u = Undefined("x")
    assert str(u) == "" and not u and list(u) == [] and len(u) == 0


def test_selectattr_and_rejectattr_forms(env):
    items = [{"name": "a", "draft": True, "weight": 1, "section": "posts"},
             {"name": "b", "draft": False, "weight": 7, "section": "notes"},
             {"name": "c", "weight": 9, "section": "posts"}]
    names = lambda expr: r(env, "{{ items | " + expr + " | map('name') | join(',') }}", items=items)
    assert names("selectattr('draft')") == "a"
    assert names("rejectattr('draft')") == "b,c"
    assert names("selectattr('section', 'posts')") == "a,c"
    assert names("selectattr('section', '==', 'posts')") == "a,c"
    assert names("selectattr('weight', '>', 5)") == "b,c"
    assert names("selectattr('section', 'in', ['notes'])") == "b"
    assert names("rejectattr('weight', '<=', 1)") == "b,c"
    with pytest.raises(TemplateError, match="operator"):
        names("selectattr('weight', '~', 1, 2)")
    with pytest.raises(TemplateError, match="can't compare"):
        names("selectattr('section', '>', 3)")


def test_groupby_gives_grouper_and_list_and_unpacks(env):
    items = [{"k": "b", "n": 1}, {"k": "a", "n": 2}, {"k": "b", "n": 3}]
    assert r(env, "{% for g in items | groupby('k') %}{{ g.grouper }}={{ g.list | map('n') | join(',') }};{% endfor %}",
             items=items) == "b=1,3;a=2;"
    assert r(env, "{% for key, group in items | groupby('k') %}{{ key }}{{ group | length }}{% endfor %}",
             items=items) == "b2a1"


def test_batch_fills_and_rejects_bad_sizes(env):
    assert r(env, "{{ [1, 2, 3] | batch(2, 0) }}") == "[[1, 2], [3, 0]]"
    with pytest.raises(TemplateError, match="at least 1"):
        r(env, "{{ [1] | batch(0) }}")


def test_errors_inside_filters_and_calls_say_where(env):
    with pytest.raises(TemplateError, match="filter 'length' failed") as exc:
        env.from_string("ok\n{{ none | length }}", name="page.html").render({})
    assert exc.value.line == 2 and exc.value.name == "page.html"
    with pytest.raises(TemplateError, match="filter 'max' failed"):
        r(env, "{{ [] | max }}")

    def boom():
        raise RuntimeError("kaboom")
    env.globals["boom"] = boom
    with pytest.raises(TemplateError, match="calling 'boom' failed: RuntimeError: kaboom"):
        r(env, "{{ boom() }}")


def test_errors_in_a_block_name_the_child_template(tmp_path):
    (tmp_path / "base.html").write_text("<main>\n{% block content %}{% endblock %}\n</main>")
    (tmp_path / "page.html").write_text('{% extends "base.html" %}\n{% block content %}\nfine\n{{ none | length }}\n{% endblock %}')
    env = Environment([tmp_path])
    with pytest.raises(TemplateError) as exc:
        env.render("page.html")
    assert exc.value.name == "page.html" and exc.value.line == 4

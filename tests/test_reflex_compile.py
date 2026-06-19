"""Reflex compile-time smoke test.

Reflex evaluates `rx.foreach` lambdas and `rx.cond` branches only at compile
time, so component-tree bugs slip past import-time checks and only crash when
`reflex run` starts. This test forces evaluation of every registered page.
"""


def test_every_page_compiles():
    from web.web import app

    for _route, page in app._unevaluated_pages.items():
        page.component()

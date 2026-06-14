"""Mermaid rendering for the Reflex UI.

`rx.markdown` (react-markdown) does not render ```mermaid fenced blocks, so we
split section markdown into text/mermaid segments and render mermaid as
`<pre class="mermaid">`. `mermaid_script()` loads the mermaid CDN and uses a
MutationObserver to render any unprocessed nodes, with light/dark re-render.
"""

import reflex as rx


def md_to_segments(md: str) -> list[dict]:
    """Split markdown into ordered text/mermaid segments."""
    if not md:
        return []
    segments: list[dict] = []
    remaining = md
    fence_open = "```mermaid"
    fence_close = "```"
    while remaining:
        start = remaining.find(fence_open)
        if start == -1:
            segments.append({"type": "text", "content": remaining})
            break
        if start > 0:
            segments.append({"type": "text", "content": remaining[:start]})
        rest = remaining[start + len(fence_open) :]
        end = rest.find(fence_close)
        if end == -1:
            segments.append({"type": "text", "content": fence_open + rest})
            break
        segments.append({"type": "mermaid", "content": rest[:end].strip()})
        remaining = rest[end + len(fence_close) :]
    return segments


def render_segment(segment: dict) -> rx.Component:
    """Render a single text or mermaid segment."""
    return rx.cond(
        segment["type"] == "mermaid",
        rx.box(
            rx.el.pre(segment["content"], class_name="mermaid"),
            width="100%",
            overflow_x="auto",
        ),
        rx.markdown(segment["content"]),
    )


def mermaid_script() -> rx.Component:
    """Load mermaid.js and auto-render diagrams with theme support + readability CSS."""
    return rx.fragment(
        rx.script(src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"),
        rx.script(
            """
            (function() {
                (function injectCSS() {
                    var style = document.createElement('style');
                    style.textContent = [
                        '.rx-Markdown { line-height: 1.7; font-size: 15px; }',
                        '.rx-Markdown table { border-collapse: collapse; width: 100%; font-size: 13.5px; display: block; overflow-x: auto; }',
                        '.rx-Markdown th, .rx-Markdown td { border: 1px solid var(--gray-a5); padding: 7px 12px; text-align: left; vertical-align: top; }',
                        '.rx-Markdown th { background: var(--gray-a3); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }',
                        '.rx-Markdown tr:nth-child(even) td { background: var(--gray-a2); }',
                        '.rx-Markdown pre:not(.mermaid) { background: var(--gray-a3); border: 1px solid var(--gray-a5); border-radius: 8px; padding: 14px; overflow-x: auto; font-size: 13px; }',
                        '.rx-Markdown code:not(pre code) { background: var(--gray-a3); border-radius: 4px; padding: 2px 6px; font-size: 0.875em; }',
                        'pre.mermaid { background: var(--gray-a2); border: 1px solid var(--gray-a4); border-radius: 10px; padding: 22px; margin: 14px 0; text-align: center; }',
                        '.rx-Markdown h2 { margin-top: 1.6em; margin-bottom: 0.6em; }',
                        '.rx-Markdown h3 { margin-top: 1.4em; margin-bottom: 0.5em; }',
                        '.rx-Markdown blockquote { border-left: 3px solid var(--grass-8); padding: 6px 14px; margin: 10px 0; background: var(--grass-a2); border-radius: 0 6px 6px 0; }',
                        '.rx-Markdown ul, .rx-Markdown ol { padding-left: 1.4em; margin: 6px 0; }',
                        '@media print { .no-print { display: none !important; } body { background: #fff !important; } [id="report-content"] { break-inside: avoid; } }',
                    ].join('\\n');
                    document.head.appendChild(style);
                })();

                function getTheme() {
                    return document.documentElement.className.indexOf('dark') !== -1 ? 'dark' : 'default';
                }

                function init() {
                    if (typeof mermaid === 'undefined') { setTimeout(init, 100); return; }
                    mermaid.initialize({ startOnLoad: false, theme: getTheme() });
                    var rendering = false;

                    function renderNodes() {
                        if (rendering) return;
                        var els = Array.from(document.querySelectorAll('pre.mermaid:not([data-processed])'));
                        els.forEach(function(el) {
                            if (!el.getAttribute('data-mermaid-source')) {
                                el.setAttribute('data-mermaid-source', el.textContent);
                            }
                        });
                        if (els.length > 0) {
                            rendering = true;
                            mermaid.run({ nodes: els }).then(function() { rendering = false; })
                                .catch(function(err) { console.error('Mermaid:', err); rendering = false; });
                        }
                    }

                    function rerenderAll() {
                        mermaid.initialize({ startOnLoad: false, theme: getTheme() });
                        document.querySelectorAll('pre.mermaid').forEach(function(el) {
                            var src = el.getAttribute('data-mermaid-source');
                            if (src) { el.removeAttribute('data-processed'); el.innerHTML = src; }
                        });
                        renderNodes();
                    }

                    renderNodes();

                    new MutationObserver(function(muts) {
                        for (var i = 0; i < muts.length; i++) {
                            if (muts[i].addedNodes.length > 0) { renderNodes(); break; }
                        }
                    }).observe(document.body, { childList: true, subtree: true });

                    new MutationObserver(function(muts) {
                        muts.forEach(function(m) { if (m.attributeName === 'class') { rerenderAll(); } });
                    }).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
                }
                init();
            })();
            """
        ),
    )

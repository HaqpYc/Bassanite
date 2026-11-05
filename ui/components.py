"""Building blocks shared by every tab: Markdown with math, explanation panels,
and the five-block stage layout (story, controls, visualisations, outputs, nav)."""
from __future__ import annotations

import re
from collections.abc import Callable

from nicegui import ElementFilter, ui

from explain import function_source, load_panel, load_story, panel_names
from stages import STAGES, Stage
from state import REPO_ROOT, state

# `latex` renders $...$ and $$...$$ to MathML (via latex2mathml), so math works
# offline without a JavaScript library.
MD_EXTRAS = ["latex", "fenced-code-blocks", "tables"]

# Build step of docs/UI_PLAN.md that adds each tab's real content.
_BUILD_STEP = {"signal": 2, "acquisition": 4, "tracking": 5, "decode": 6, "position": 6}


EXPLAIN_MARKER = "explain-panel"


def set_all_panels(open_: bool) -> None:
    """Open or close every explanation panel on the current page."""
    for expansion in ElementFilter(kind=ui.expansion, marker=EXPLAIN_MARKER):
        expansion.set_value(open_)


# markdown2's latex extra turns a line break into a real break when the next line
# starts with inline math ($x$). Join such lines so paragraphs flow normally.
_BREAK_BEFORE_INLINE_MATH = re.compile(r"(?<=\S)\n(?=\$(?!\$))")


def md(text: str) -> ui.markdown:
    text = _BREAK_BEFORE_INLINE_MATH.sub(" ", text)
    # sanitize=False: the text comes from the repo's own ui/explain/ files.
    return ui.markdown(text, extras=MD_EXTRAS, sanitize=False).classes("w-full bassanite-md")


def explain_panel(stage_key: str, name: str) -> None:
    """One "How is this computed?" panel with Intuition / Math / Code tabs.

    Flipping the global "Show the math" switch opens or closes every panel (see
    set_all_panels); each panel can also be opened on its own. (A binding would
    not do: it forces the panel back to the switch's value every 0.1 s.)
    """
    panel = load_panel(stage_key, name)
    with ui.expansion(panel.title, caption="How is this computed?", icon="functions",
                      value=state.show_math) \
            .classes("w-full border rounded-lg").mark(EXPLAIN_MARKER):
        with ui.tabs().props("dense align=left no-caps") as tabs:
            ui.tab("intuition", "Intuition", icon="lightbulb")
            ui.tab("math", "Math", icon="functions")
            ui.tab("code", "Code", icon="code")
        with ui.tab_panels(tabs, value="intuition").classes("w-full"):
            with ui.tab_panel("intuition"):
                md(panel.intuition)
            with ui.tab_panel("math"):
                md(panel.math)
            with ui.tab_panel("code"):
                _code_view(panel.code_ref)


def _code_view(code_ref: str | None) -> None:
    if code_ref is None:
        ui.label("No code yet: this stage is planned.").classes("text-grey-7")
        return
    try:
        source = function_source(code_ref)
    except (FileNotFoundError, LookupError) as err:
        ui.label(f"Could not read the source: {err}").classes("text-negative")
        return
    ui.label(f"The function that runs, read live from {code_ref.replace('::', ' → ')}()") \
        .classes("text-xs text-grey-7")
    ui.code(source).classes("w-full")


def story(stage_key: str) -> None:
    """The plain-language story: a strip in Beginner mode, collapsed in Expert mode."""
    text = load_story(stage_key)
    if not text:
        return
    with ui.card().classes("w-full bg-blue-1").props("flat bordered") \
            .bind_visibility_from(state, "mode", value="beginner"):
        md(text)
    with ui.expansion("About this step", icon="info").classes("w-full") \
            .bind_visibility_from(state, "mode", value="expert"):
        md(text)


def _outputs(stage: Stage) -> None:
    with ui.card().classes("w-full").props("flat bordered"):
        ui.label("Hands over to the next step").classes("text-subtitle1 font-bold")
        ui.label(stage.hands_over)
        for pattern in stage.output_files:
            matches = sorted(REPO_ROOT.glob(pattern))
            mark = "✅" if matches else "—"
            count = f" ({len(matches)} files)" if len(matches) > 1 else ""
            ui.label(f"{mark} {pattern}{count}").classes("text-sm font-mono text-grey-8")


def _nav(stage: Stage, go_to: Callable[[str], None]) -> None:
    keys = ["overview"] + [s.key for s in STAGES]
    i = keys.index(stage.key)
    with ui.row().classes("w-full justify-between"):
        prev_key = keys[i - 1]
        prev_title = "Overview" if prev_key == "overview" else \
            next(s.title for s in STAGES if s.key == prev_key)
        ui.button(f"← {prev_title}", on_click=lambda: go_to(prev_key)).props("flat no-caps")
        if i + 1 < len(keys):
            nxt = STAGES[stage.number]   # stage numbers start at 1
            ui.button(f"Next step: {nxt.title} →", on_click=lambda: go_to(nxt.key)) \
                .props("no-caps")


def _placeholder(stage: Stage) -> None:
    step = _BUILD_STEP[stage.key]
    with ui.row().classes("w-full items-stretch gap-4"):
        with ui.card().classes("w-full md:w-72").props("flat bordered") \
                .bind_visibility_from(state, "mode", value="expert"):
            ui.label("Controls").classes("text-subtitle1 font-bold")
            ui.label(f"The settings for this step arrive with its tab "
                     f"(build step {step} of docs/UI_PLAN.md).").classes("text-grey-7")
        with ui.card().classes("flex-1 min-w-[16rem] min-h-[12rem]").props("flat bordered"):
            ui.label("Visualisations").classes("text-subtitle1 font-bold")
            ui.label(f"Plots for this step arrive in build step {step} of "
                     f"docs/UI_PLAN.md.").classes("text-grey-7")


# The stage-level panel ("how.md") goes at the top of the tab. Every other panel of
# a stage belongs to one visualisation and is placed by that tab's own code.
STAGE_PANEL = "how"


def stage_page(stage: Stage, go_to: Callable[[str], None],
               content: Callable[[], None] | None = None) -> None:
    """The five-block layout every stage tab shares.

    `content` builds the tab's controls and visualisations; without it, the tab
    shows placeholders.
    """
    with ui.column().classes("w-full gap-4"):
        ui.label(f"Step {stage.number} · {stage.title}").classes("text-h5")
        story(stage.key)

        if not stage.built:
            ui.label("🔒 This stage is not built yet. The explanation below already "
                     "describes how it will work.").classes("text-grey-8")

        if STAGE_PANEL in panel_names(stage.key):
            explain_panel(stage.key, STAGE_PANEL)

        if content is None:
            _placeholder(stage)
        else:
            content()

        _outputs(stage)
        _nav(stage, go_to)

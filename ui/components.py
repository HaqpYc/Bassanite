"""Building blocks shared by every tab: Markdown with math, explanation panels,
and the five-block stage layout (story, controls, visualisations, outputs, nav)."""
from __future__ import annotations

import re
from collections.abc import Callable

from nicegui import ElementFilter, ui

from explain import fill, function_source, load_panel, load_story, panel_names
from explain_values import current_values
from stages import STAGES, Stage, rel_path
from state import state

from bassanite.results import results_dir  # noqa: E402,I001  (state set sys.path)

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


INTUITION_MARKER = "explain-intuition"


@ui.refreshable
def _live_md(template: str) -> None:
    """Markdown whose {{placeholders}} follow the loaded recording and its scan."""
    md(fill(template, current_values()))


def refresh_explanations() -> None:
    """Re-fill every explanation text (after a new source or a new scan)."""
    _live_md.refresh()


def explain_panel(stage_key: str, name: str) -> None:
    """One explanation panel: Intuition, Math and Code.

    Beginner mode shows the Intuition text on the page, next to its plot, with the
    Math and Code tabs in a "How is this computed?" expansion below it. Expert mode
    folds all three into the expansion. Flipping the global "Show the math" switch
    opens or closes every expansion (see set_all_panels); each can also be opened
    on its own. (A binding would not do: it forces the panel back to the switch's
    value every 0.1 s.)
    """
    panel = load_panel(stage_key, name)
    with ui.column().classes("w-full gap-0") \
            .bind_visibility_from(state, "mode", value="beginner"):
        if panel.intuition:
            with ui.card().classes("w-full bg-amber-1").props("flat bordered") \
                    .mark(INTUITION_MARKER):
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.icon("lightbulb", color="amber-9")
                    ui.label(panel.title).classes("font-bold")
                _live_md(panel.intuition)
        _expansion(panel, "The math and the code", ("math", "code"))
    with ui.column().classes("w-full gap-0") \
            .bind_visibility_from(state, "mode", value="expert"):
        _expansion(panel, panel.title, ("intuition", "math", "code"))


_TABS = {"intuition": ("Intuition", "lightbulb"), "math": ("Math", "functions"),
         "code": ("Code", "code")}


def _expansion(panel, title: str, tabs_shown: tuple[str, ...]) -> None:
    with ui.expansion(title, caption="How is this computed?", icon="functions",
                      value=state.show_math) \
            .classes("w-full border rounded-lg").mark(EXPLAIN_MARKER):
        with ui.tabs().props("dense align=left no-caps") as tabs:
            for key in tabs_shown:
                label, icon = _TABS[key]
                ui.tab(key, label, icon=icon)
        with ui.tab_panels(tabs, value=tabs_shown[0]).classes("w-full"):
            for key in tabs_shown:
                with ui.tab_panel(key):
                    if key == "code":
                        _code_view(panel.code_refs)
                    else:
                        _live_md(panel.intuition if key == "intuition" else panel.math)


def _code_view(code_refs: tuple[str, ...]) -> None:
    if not code_refs:
        ui.label("No code yet: this stage is planned.").classes("text-grey-7")
        return
    for code_ref in code_refs:
        try:
            source = function_source(code_ref)
        except (FileNotFoundError, LookupError) as err:
            ui.label(f"Could not read the source: {err}").classes("text-negative")
            continue
        ui.label(f"The function that runs, read live from "
                 f"{code_ref.replace('::', ' → ')}()").classes("text-xs text-grey-7")
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


@ui.refreshable
def _output_files(stage: Stage) -> None:
    if state.source is None:
        return
    folder = results_dir(state.source.path)
    for pattern in stage.output_files:
        matches = sorted(folder.glob(pattern))
        mark = "✅" if matches else "—"
        count = f" ({len(matches)} files)" if len(matches) > 1 else ""
        ui.label(f"{mark} {rel_path(folder / pattern)}{count}") \
            .classes("text-sm font-mono text-grey-8")


def refresh_outputs() -> None:
    """Re-list every stage's result files (after a run, or a new source)."""
    _output_files.refresh()


def _outputs(stage: Stage) -> None:
    with ui.card().classes("w-full").props("flat bordered"):
        ui.label("Hands over to the next step").classes("text-subtitle1 font-bold")
        ui.label(stage.hands_over)
        _output_files(stage)


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

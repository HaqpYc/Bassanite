"""Bassanite UI: a local web app that walks through the GPS receiver pipeline.

Run it from the repository root:

    python ui/app.py

and open http://localhost:8080 (your default browser opens on its own; add
--no-browser to stop that).

Editing:
- Explanation texts (ui/explain/**/*.md) are re-read on every page load:
  save the file and refresh the browser.
- After editing Python files, stop the server (Ctrl+C) and start it again.
  (Auto-reload is off: on Windows it kept serving the old code.)
"""
from __future__ import annotations

from nicegui import app, ui

from components import set_all_panels, stage_page
from source_dialog import source_dialog
from stages import STAGES, stage_status
from state import state
from tabs.acquisition import AcquisitionTab
from tabs.overview import overview
from tabs.capture import CapturePanel
from tabs.signal import SignalTab
from tabs.tracking import TrackingTab, stop_all_runs

from common.hackrf import stop_all  # noqa: E402  (repo root is on sys.path via state)

HELP = """
**How to use Bassanite**

- The **tabs** follow the GPS receiver from the raw signal (step 1) to your position
  (step 5). The **Overview** tab summarises where you are.
- **Beginner** mode explains each step in plain words and uses default settings.
  **Expert** mode shows the settings of each step.
- **Show the math** opens every "How is this computed?" panel. Each panel has three
  levels: the intuition, the equations, and the actual code that runs.

**Status icons:** ✅ done · ⚠️ needs attention · ⏳ running · 🔒 not built or not run yet
"""


def root() -> None:
    ui.add_css("""
        .bassanite-md math[display="block"] { margin: 0.75rem 0; font-size: 1.1em; }
        .bassanite-md p { margin: 0.4rem 0; }
    """)

    tab_refs: dict[str, ui.tab] = {}
    signal_tab = SignalTab()
    signal_tab.capture_panel = CapturePanel(signal_tab, on_recorded=lambda: refresh())
    # Tabs that have real content; the others show placeholders.
    acquisition_tab = AcquisitionTab(on_scanned=lambda: refresh())
    tracking_tab = TrackingTab(go_to=lambda key: go_to(key), on_tracked=lambda: refresh())
    tab_content = {"signal": signal_tab.build, "acquisition": acquisition_tab.build,
                   "tracking": tracking_tab.build}

    def go_to(key: str) -> None:
        tabs.set_value(key)
        ui.run_javascript("window.scrollTo(0, 0)")

    def update_labels() -> None:
        source_button.set_text(state.source.describe() if state.source
                               else "No recording loaded")
        for stage in STAGES:
            tab_refs[stage.key].set_label(
                f"{stage.number} {stage.title} {stage_status(stage, state.source).icon}")

    def refresh() -> None:
        """Update everything that depends on the source or on stage results."""
        update_labels()
        overview.refresh()
        signal_tab.request_update()
        acquisition_tab.request_update()
        tracking_tab.request_update()

    def open_source_dialog() -> None:
        source_dialog(on_loaded=refresh).open()

    with ui.dialog() as help_dialog, ui.card().classes("max-w-xl"):
        ui.markdown(HELP)
        ui.button("Close", on_click=help_dialog.close).props("flat no-caps")

    with ui.header(elevated=True).classes("bg-blue-grey-9 text-white column q-pa-none"):
        with ui.row().classes("w-full items-center gap-x-4 gap-y-1 px-4 pt-2"):
            ui.label("BASSANITE").classes("text-h6 tracking-widest")
            source_button = ui.button(icon="graphic_eq", on_click=open_source_dialog) \
                .props("flat dense no-caps color=white").tooltip("Change the input signal")
            ui.space()
            ui.toggle({"beginner": "Beginner", "expert": "Expert"}) \
                .bind_value(state, "mode") \
                .props("dense no-caps unelevated rounded color=blue-grey-7 "
                       "toggle-color=amber-7 text-color=white")
            ui.switch("Show the math").bind_value(state, "show_math") \
                .on_value_change(lambda e: set_all_panels(e.value)) \
                .props("dark color=amber-7")
            ui.button(icon="help_outline", on_click=help_dialog.open) \
                .props("flat round dense color=white").tooltip("How to use Bassanite")
        with ui.tabs(value="overview").classes("w-full") \
                .props("align=left no-caps outside-arrows mobile-arrows "
                       "active-color=amber-5 indicator-color=amber-5") as tabs:
            ui.tab("overview", "Overview", icon="home")
            for stage in STAGES:
                tab_refs[stage.key] = ui.tab(stage.key)

    with ui.tab_panels(tabs, value="overview", animated=False) \
            .classes("w-full max-w-6xl mx-auto"):
        with ui.tab_panel("overview"):
            overview(go_to, open_source_dialog, refresh)
        for stage in STAGES:
            with ui.tab_panel(stage.key):
                stage_page(stage, go_to, content=tab_content.get(stage.key))

    update_labels()


app.on_shutdown(stop_all)        # never leave hackrf_transfer running
app.on_shutdown(stop_all_runs)   # nor a tracking process

if __name__ in {"__main__", "__mp_main__"}:
    import argparse
    parser = argparse.ArgumentParser(description="Bassanite web UI")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't open the default browser on start "
                             "(open http://localhost:8080 yourself)")
    parser.add_argument("--port", type=int, default=8080,
                        help="port to serve on (default 8080); use another one to run "
                             "a second copy")
    args, _ = parser.parse_known_args()   # tolerant: pytest runs this file with its own args
    # reconnect_timeout: how long a page may stay silent before NiceGUI gives up on
    # it and reloads it. The default 3 s is too short when the computer is busy
    # (e.g. memory paging during a tracking run), so allow 10 s.
    ui.run(root, title="Bassanite", port=args.port, show=not args.no_browser, reload=False,
           reconnect_timeout=10.0)

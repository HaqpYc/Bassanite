"""Plot helpers shared by the tabs: Plotly figures are plain dicts (no plotly
package needed), all with the same look."""
from __future__ import annotations

PLOT_CONFIG = {"displaylogo": False, "responsive": True,
               "modeBarButtonsToRemove": ["lasso2d", "select2d"]}

# Colours used for the same meaning across tabs.
DETECTED = "#2e7d32"      # green: a satellite found / locked
NOISE = "#9e9e9e"         # grey: nothing there
LIMIT = "#c62828"         # red: thresholds and limits
HIGHLIGHT = "#ffb300"     # amber: the item the user selected


def _titles_as_objects(axis: dict) -> dict:
    """plotly.js 3+ ignores `title: "text"`; it wants `title: {"text": ...}`."""
    if isinstance(axis.get("title"), str):
        axis = {**axis, "title": {"text": axis["title"]}}
    return axis


def layout(**kwargs) -> dict:
    out = {"margin": {"l": 60, "r": 20, "t": 10, "b": 45}, "height": 300,
           "template": "plotly_white",
           "legend": {"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0}}
    out.update(kwargs)
    for key in ("xaxis", "yaxis"):
        if isinstance(out.get(key), dict):
            out[key] = _titles_as_objects(out[key])
    if isinstance(out.get("scene"), dict):
        out["scene"] = {k: _titles_as_objects(v) if k.endswith("axis") and isinstance(v, dict)
                        else v for k, v in out["scene"].items()}
    return out


def figure(data: list, **layout_kwargs) -> dict:
    return {"data": data, "config": PLOT_CONFIG, "layout": layout(**layout_kwargs)}


def empty_figure(message: str = "") -> dict:
    return figure([], xaxis={"visible": False}, yaxis={"visible": False},
                  annotations=[{"text": message, "showarrow": False, "font": {"size": 14}}]
                  if message else [])

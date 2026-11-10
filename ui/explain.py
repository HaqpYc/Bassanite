"""Load the explanation texts that live in ui/explain/.

Each stage has a folder ui/explain/<stage>/ containing:

* story.md          a few plain sentences shown at the top of the tab
* <panel>.md        one "How is this computed?" panel, in this format:

      ---
      title: How a buried satellite is found
      code: 02_acquisition/scan_prns.py::acquire_prn
      ---
      ## Intuition
      plain-language explanation...
      ## Math
      equations in LaTeX ($inline$ or $$block$$)...

The `code:` line is optional and may list several functions, separated by
commas. The Code tab shows their source, read from the files at runtime, so the
explanation always shows the code that runs.

Figures that depend on the recording or the settings are written as
{{name}} or {{name:format}} and filled from ui/explain_values.py (see fill()).
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from state import REPO_ROOT

EXPLAIN_DIR = Path(__file__).resolve().parent / "explain"

_SECTION_RE = re.compile(r"^##\s+(Intuition|Math)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Panel:
    title: str
    intuition: str
    math: str
    code_refs: tuple[str, ...]   # each "path/to/file.py::function_name"


_PLACEHOLDER = re.compile(r"\{\{(\w+)(?::([^}]*))?\}\}")


def placeholders(text: str) -> set[str]:
    """The value names a text uses ({{name}} or {{name:format}})."""
    return {m.group(1) for m in _PLACEHOLDER.finditer(text)}


def fill(text: str, values: dict) -> str:
    """Replace each {{name}} / {{name:format}} with format(values[name], format).
    A name missing from `values` raises KeyError (the tests check none is)."""
    return _PLACEHOLDER.sub(lambda m: format(values[m.group(1)], m.group(2) or ""), text)


def load_story(stage_key: str) -> str:
    path = EXPLAIN_DIR / stage_key / "story.md"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    _, header, body = text.split("---", 2)
    meta = {}
    for line in header.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body


def load_panel(stage_key: str, name: str) -> Panel:
    text = (EXPLAIN_DIR / stage_key / f"{name}.md").read_text(encoding="utf-8")
    meta, body = _split_front_matter(text)
    parts = _SECTION_RE.split(body)
    # parts = [preamble, "Intuition", text, "Math", text, ...]
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    refs = tuple(r.strip() for r in meta.get("code", "").split(",") if r.strip())
    return Panel(
        title=meta.get("title", name),
        intuition=sections.get("Intuition", ""),
        math=sections.get("Math", ""),
        code_refs=refs,
    )


def panel_names(stage_key: str) -> list[str]:
    """Panel files of a stage, in file-name order (story.md excluded)."""
    folder = EXPLAIN_DIR / stage_key
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.md") if p.stem != "story")


def function_source(code_ref: str) -> str:
    """Source of a function in a file, given "file.py::function" or
    "file.py::Class.method" (or "file.py::Class" for a whole class).

    The file is parsed, never imported or run. Only top-level definitions (and the
    methods of top-level classes) are looked up, so a name can't match a nested
    helper by accident.
    """
    rel_path, _, name = code_ref.partition("::")
    path = REPO_ROOT / rel_path
    if not path.is_file():
        raise FileNotFoundError(f"{rel_path} does not exist")
    source = path.read_text(encoding="utf-8")
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    nodes = ast.parse(source).body
    *outer, last = name.split(".")
    for part in outer:                       # walk into the class(es)
        cls = next((n for n in nodes if isinstance(n, ast.ClassDef) and n.name == part), None)
        if cls is None:
            raise LookupError(f"class {part} not found in {rel_path}")
        nodes = cls.body
    for node in nodes:
        if isinstance(node, defs) and node.name == last:
            return ast.get_source_segment(source, node)
    raise LookupError(f"function {name}() not found in {rel_path}")

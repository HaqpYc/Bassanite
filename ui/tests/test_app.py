"""Build the whole page with NiceGUI's simulated user (no browser needed)."""
import asyncio

from nicegui import ui
from nicegui.testing import User

from components import EXPLAIN_MARKER
from state import state


async def test_page_builds_with_all_tabs(user: User) -> None:
    await user.open("/")
    await user.should_see("BASSANITE")
    await user.should_see("Overview")
    for title in ["Signal", "Find satellites", "Track", "Decode", "Position"]:
        await user.should_see(title)


async def test_overview_shows_pipeline_status(user: User) -> None:
    await user.open("/")
    await user.should_see("The pipeline")
    await user.should_see("Furthest step reached", retries=5)


async def test_explanation_panels_are_present(user: User) -> None:
    await user.open("/")
    for title in ["From radio wave to IQ samples", "How a buried satellite is found",
                  "Two loops that follow a moving satellite",
                  "Reading the navigation message", "From distances to a position"]:
        await user.should_see(title)


async def test_math_switch_drives_global_state(user: User) -> None:
    state.show_math = False
    await user.open("/")
    switch = user.find(kind=ui.switch, content="Show the math")
    switch.click()
    assert state.show_math is True
    switch.click()
    assert state.show_math is False


async def test_panels_open_individually_and_follow_the_switch(user: User) -> None:
    """Regression: a binding forced every panel shut while the switch was off."""
    state.show_math = False
    await user.open("/")
    panels = list(user.find(kind=ui.expansion, marker=EXPLAIN_MARKER).elements)
    assert len(panels) >= 10 and not any(p.value for p in panels)

    panels[0].set_value(True)              # the user opens one panel
    await asyncio.sleep(0.5)               # several binding refresh cycles
    assert panels[0].value is True and not panels[1].value

    user.find(kind=ui.switch, content="Show the math").click()
    assert all(p.value for p in panels)
    user.find(kind=ui.switch, content="Show the math").click()
    assert not any(p.value for p in panels)


async def test_mode_toggle_drives_global_state(user: User) -> None:
    state.mode = "beginner"
    await user.open("/")
    user.find(kind=ui.toggle).elements.pop().set_value("expert")
    assert state.expert
    state.mode = "beginner"

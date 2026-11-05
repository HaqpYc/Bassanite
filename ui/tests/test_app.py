"""Build the whole page with NiceGUI's simulated user (no browser needed)."""
from nicegui import ui
from nicegui.testing import User

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


async def test_mode_toggle_drives_global_state(user: User) -> None:
    state.mode = "beginner"
    await user.open("/")
    user.find(kind=ui.toggle).elements.pop().set_value("expert")
    assert state.expert
    state.mode = "beginner"

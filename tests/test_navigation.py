"""Stage 04 (bassanite/navigation.py): a message is encoded from a known ephemeris,
then read back from its bits alone."""
from datetime import datetime, timezone

import numpy as np
import pytest

from bassanite import navigation as nav
from bassanite.synthetic import SKY_TOW, default_sky

EPH = default_sky()[12]
T_MESSAGE = SKY_TOW - 6                    # the stream starts with a subframe 5


def raw(n_subframes=6, eph=EPH):
    return nav.encode_message(eph, T_MESSAGE, n_subframes)


def message(n_subframes=6, eph=EPH):
    """The stream with the last two bits of the subframe before it (always 00):
    the first word is decoded relative to them, so subframes start at bits 2, 302..."""
    return [0, 0] + raw(n_subframes, eph)


def test_every_field_comes_back_exactly():
    sat = nav.decode_satellite(message())
    assert sat["problems"] == []
    assert [sf["number"] for sf in sat["subframes"]] == [5, 1, 2, 3, 4, 5]
    assert all(nav.complete(sf) for sf in sat["subframes"])
    assert set(sat["ephemeris"]) == set(nav.FIELD_NAMES)
    for name in nav.FIELD_NAMES:
        assert sat["ephemeris"][name] == EPH[name], name


def test_negative_and_extreme_values_survive_the_two_s_complement():
    eph = dict(EPH, a_f0=-2**21 * 2**-31, m_0=-2**31 * 2**-31 * nav.PI_GPS,
               e=(2**32 - 1) * 2**-33, i_dot=-1 * 2**-43 * nav.PI_GPS, c_rs=2**15 * -2**-5)
    got = nav.decode_satellite(message(eph=eph))["ephemeris"]
    for name in ("a_f0", "m_0", "e", "i_dot", "c_rs"):
        assert got[name] == eph[name], name


def test_an_inverted_stream_decodes_the_same():
    """The Costas loop may lock upside down; the parity rule makes it not matter."""
    upright = nav.decode_satellite(message())
    inverted = nav.decode_satellite([1 - b for b in message()])
    assert upright["inverted"] is False and inverted["inverted"] is True
    assert inverted["ephemeris"] == upright["ephemeris"]
    assert inverted["period0_time"] == upright["period0_time"]


def test_subframes_are_found_at_any_offset_in_the_stream():
    bits = raw(7)
    rng = np.random.default_rng(3)
    for cut in (1, 17, 123, 298):       # 298: only the two bits before a start remain
        stream = list(rng.integers(0, 2, 40)) + bits[cut:]
        sat = nav.decode_satellite(stream)
        first = sat["subframes"][0]
        assert first["start_bit"] == 40 + 300 - cut
        assert first["tow"] == T_MESSAGE + 6
        assert sat["ephemeris"] == nav.decode_satellite(message(7))["ephemeris"]


def test_period0_time_labels_tracking_s_code_periods():
    """Bit 0 of the stream starts at code period `bit_offset`; subframe at bit s
    starts 20 s periods later, at its time of week."""
    bits = message()[130:]
    sat = nav.decode_satellite(bits, bit_offset=13)
    start = sat["subframes"][0]
    assert sat["period0_time"] == pytest.approx(
        start["tow"] - (13 + 20 * start["start_bit"]) * 1e-3, abs=1e-9)


def test_one_wrong_bit_fails_the_parity_of_its_word_only():
    bits = message()
    bits[2 + 300 + 30 * 4 + 7] ^= 1                # subframe 1, word 5
    sat = nav.decode_satellite(bits)
    sf1 = sat["subframes"][1]
    assert sf1["number"] == 1 and sf1["parity_ok"] == [True] * 4 + [False] + [True] * 5
    assert sat["ephemeris"] is None and "subframe 1" in sat["problems"][0]


def test_a_second_clean_subframe_replaces_a_damaged_one():
    bits = message(11)                             # 5 1 2 3 4 5 1 2 3 4 5
    bits[2 + 300 + 30 * 4 + 7] ^= 1                # damage the first subframe 1
    sat = nav.decode_satellite(bits)
    assert sat["problems"] == [] and sat["ephemeris"]["a_f0"] == EPH["a_f0"]


def test_noise_holds_no_subframe():
    bits = np.random.default_rng(0).integers(0, 2, 3000)
    sat = nav.decode_satellite(bits)
    assert sat["subframes"] == [] and sat["period0_time"] is None
    assert not nav.usable(sat)


def test_a_chance_preamble_off_the_grid_is_not_a_start():
    """A false start would need the preamble and two good parities: build one on
    purpose, off the 300-bit grid of the real subframes, and see it dropped."""
    bits = message(8)
    fake = nav.add_parity(nav.subframe_data(2, 12, {}), bits[448], bits[449])[:60]
    bits[450:510] = fake
    starts, lookalikes = nav.find_subframes(bits)
    assert 450 in lookalikes and starts == [2 + 300 * k for k in range(8)]


def test_mixed_issues_are_refused():
    """Subframe 1 of one data set with subframes 2 and 3 of another is no orbit."""
    other = dict(EPH, iode=EPH["iode"] + 1, iode_3=EPH["iode"] + 1)
    bits = [0, 0] + nav.encode_message(EPH, T_MESSAGE, 2) \
        + nav.encode_message(other, T_MESSAGE + 12, 2)
    sat = nav.decode_satellite(bits)
    assert sat["ephemeris"] is None and "issue" in sat["problems"][-1]


def test_an_unhealthy_satellite_is_flagged():
    sat = nav.decode_satellite(message(eph=dict(EPH, health=32)))
    assert sat["ephemeris"] is not None and not nav.usable(sat)


def test_week_number_and_date():
    near = datetime(2026, 11, 10, tzinfo=timezone.utc)
    assert nav.full_week(2444 % 1024, near) == 2444
    assert nav.full_week(1023, datetime(2019, 4, 1, tzinfo=timezone.utc)) == 2047
    assert nav.full_week(0, datetime(2019, 4, 8, tzinfo=timezone.utc)) == 2048
    # 2026-11-10 is a Tuesday: 2 days and 13.5 h into the week, minus 18 leap seconds
    assert nav.utc_from_gps(2444, 221400) == datetime(2026, 11, 10, 13, 29, 42,
                                                      tzinfo=timezone.utc)


def test_navigation_file_round_trip(tmp_path):
    sat = nav.decode_satellite(message(), bit_offset=7)
    sat.update(bit_offset=7, nbits=1800, cn0_dbhz=44.0, locked=True)
    noise = nav.decode_satellite(np.random.default_rng(1).integers(0, 2, 600))
    path = nav.save_navigation(tmp_path / nav.NAVIGATION_JSON, {12: sat, 3: noise})
    back = nav.load_navigation(path)
    assert sorted(back) == [3, 12]
    assert back[12]["usable"] and not back[3]["usable"]
    assert back[12]["ephemeris"] == sat["ephemeris"]
    assert back[12]["period0_time"] == sat["period0_time"]
    assert [sf["number"] for sf in back[12]["subframes"]] == [5, 1, 2, 3, 4, 5]


def test_fields_of_gives_the_table_rows():
    sf = nav.decode_satellite(message())["subframes"][1]         # subframe 1
    rows = {r["name"]: r for r in nav.fields_of(sf)}
    assert rows["a_f0"]["value"] == EPH["a_f0"]
    assert len(rows["a_f0"]["bits"]) == 22 and len(rows["iodc"]["bits"]) == 10
    assert rows["a_f0"]["raw"] * rows["a_f0"]["unit_value"] == EPH["a_f0"]

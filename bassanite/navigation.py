"""
navigation.py -- stage 04: read the navigation message out of the tracked bits.

Tracking hands over a stream of 50 bit/s bits per satellite, with no marker of
where anything starts and an unknown polarity (the Costas loop may have locked
upside down, which inverts every bit). The message is built so that both can be
recovered from the bits alone (IS-GPS-200, section 20.3):

    word       30 bits: 24 of data, then 6 of parity                    0.6 s
    subframe   10 words; word 1 starts with the preamble 10001011,
               word 2 holds the time of week and the subframe number      6 s
    frame      5 subframes: 1 = satellite clock, 2 and 3 = orbit
               (ephemeris), 4 and 5 = slow data a position does not need  30 s

  * FRAME SYNC: a subframe starts where the preamble is followed by two words
    whose parity is right. Eight bits turn up by chance; parity does not.
  * POLARITY: each word is decoded relative to the last bit of the word before
    it (check_word), which comes out the same whether or not the stream is
    inverted. Nothing has to be flipped.
  * FIELDS: each number is an integer of a fixed number of bits (FIELDS) times
    the value of one unit; negative numbers use two's complement.

What stage 05 needs from here, per satellite: the EPHEMERIS (orbit and clock
terms, from subframes 1, 2 and 3) and the satellite time of one known instant of
the recording (`period0_time`: the time on the satellite's clock when the code
period numbered 0 by tracking left it). See 04_decoding/NOTES.md.

The encoder (subframe_data, add_parity, encode_message) builds a message from an
ephemeris: the synthetic recordings and the tests use it.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bassanite.tracking import SUMMARY_CSV, load_track, read_summary, track_file_name

PI_GPS = 3.1415926535898     # the value of pi fixed by the specification
PREAMBLE = [1, 0, 0, 0, 1, 0, 1, 1]
BITS_PER_WORD = 30
WORDS_PER_SUBFRAME = 10
BITS_PER_SUBFRAME = BITS_PER_WORD * WORDS_PER_SUBFRAME      # 300 bits = 6 s
SUBFRAME_S = 6
WEEK_S = 604800
BIT_S = 0.02                  # one navigation bit
CODE_PERIOD_S = 1e-3          # one C/A code period
EPHEMERIS_SUBFRAMES = (1, 2, 3)

# name, subframe, [(first bit, number of bits), ...], signed, value of one unit, unit,
# meaning. Bits are numbered 1 to 300 in the subframe (IS-GPS-200, figure 20-1).
# Angles are sent in half-turns: one unit below is already converted to radians.
FIELDS = [
    ("week",      1, [(61, 10)],            False, 1,               "weeks", "week number, modulo 1024"),
    ("ura",       1, [(73, 4)],             False, 1,               "",      "accuracy index of the satellite's data"),
    ("health",    1, [(77, 6)],             False, 1,               "",      "0 = the satellite is healthy"),
    ("iodc",      1, [(83, 2), (211, 8)],   False, 1,               "",      "issue number of the clock data"),
    ("t_gd",      1, [(197, 8)],            True,  2**-31,          "s",     "delay of L1 inside the satellite"),
    ("t_oc",      1, [(219, 16)],           False, 2**4,            "s",     "reference time of the clock correction"),
    ("a_f2",      1, [(241, 8)],            True,  2**-55,          "s/s²",  "clock drift rate"),
    ("a_f1",      1, [(249, 16)],           True,  2**-43,          "s/s",   "clock drift"),
    ("a_f0",      1, [(271, 22)],           True,  2**-31,          "s",     "clock offset"),
    ("iode",      2, [(61, 8)],             False, 1,               "",      "issue number of the ephemeris"),
    ("c_rs",      2, [(69, 16)],            True,  2**-5,           "m",     "radius correction (sine)"),
    ("delta_n",   2, [(91, 16)],            True,  2**-43 * PI_GPS, "rad/s", "correction to the mean motion"),
    ("m_0",       2, [(107, 8), (121, 24)], True,  2**-31 * PI_GPS, "rad",   "mean anomaly at the reference time"),
    ("c_uc",      2, [(151, 16)],           True,  2**-29,          "rad",   "along-track correction (cosine)"),
    ("e",         2, [(167, 8), (181, 24)], False, 2**-33,          "",      "eccentricity"),
    ("c_us",      2, [(211, 16)],           True,  2**-29,          "rad",   "along-track correction (sine)"),
    ("sqrt_a",    2, [(227, 8), (241, 24)], False, 2**-19,          "√m",    "square root of the semi-major axis"),
    ("t_oe",      2, [(271, 16)],           False, 2**4,            "s",     "reference time of the ephemeris"),
    ("c_ic",      3, [(61, 16)],            True,  2**-29,          "rad",   "inclination correction (cosine)"),
    ("omega_0",   3, [(77, 8), (91, 24)],   True,  2**-31 * PI_GPS, "rad",   "longitude of the ascending node"),
    ("c_is",      3, [(121, 16)],           True,  2**-29,          "rad",   "inclination correction (sine)"),
    ("i_0",       3, [(137, 8), (151, 24)], True,  2**-31 * PI_GPS, "rad",   "inclination"),
    ("c_rc",      3, [(181, 16)],           True,  2**-5,           "m",     "radius correction (cosine)"),
    ("omega",     3, [(197, 8), (211, 24)], True,  2**-31 * PI_GPS, "rad",   "argument of perigee"),
    ("omega_dot", 3, [(241, 24)],           True,  2**-43 * PI_GPS, "rad/s", "rate of the node longitude"),
    ("iode_3",    3, [(271, 8)],            False, 1,               "",      "issue number again (must match)"),
    ("i_dot",     3, [(279, 14)],           True,  2**-43 * PI_GPS, "rad/s", "rate of the inclination"),
]
FIELD_NAMES = tuple(f[0] for f in FIELDS)

# Which data bits (1 to 24) enter each of the six parity bits, and which of the
# previous word's last two bits (29 or 30) is added to it (IS-GPS-200, table 20-XIV).
PARITY_RULES = [(29, [1, 2, 3, 5, 6, 10, 11, 12, 13, 14, 17, 18, 20, 23]),
                (30, [2, 3, 4, 6, 7, 11, 12, 13, 14, 15, 18, 19, 21, 24]),
                (29, [1, 3, 4, 5, 7, 8, 12, 13, 14, 15, 16, 19, 20, 22]),
                (30, [2, 4, 5, 6, 8, 9, 13, 14, 15, 16, 17, 20, 21, 23]),
                (30, [1, 3, 5, 6, 7, 9, 10, 14, 15, 16, 17, 18, 21, 22, 24]),
                (29, [3, 5, 6, 8, 9, 10, 11, 13, 15, 19, 22, 23, 24])]


# --- bits and words -------------------------------------------------------------------

def parity_bits(data, prev29, prev30):
    """The six parity bits of a word, from its 24 data bits and the last two bits
    of the previous word (as sent)."""
    prev = {29: prev29, 30: prev30}
    return [prev[which] ^ (sum(data[i - 1] for i in bits) % 2)
            for which, bits in PARITY_RULES]


def to_bits(value, n):
    """An integer as n bits, most significant first (two's complement if negative)."""
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def from_bits(bits, signed=False):
    """The integer a list of bits spells, most significant first."""
    value = 0
    for b in bits:
        value = (value << 1) | b
    return value - (1 << len(bits)) if signed and bits[0] else value


def check_word(word, prev29, prev30):
    """Decode one received word of 30 bits. Returns its 24 data bits and whether
    the parity is right.

    The satellite sends each data bit XORed with the last bit of the previous
    word (prev30), so the receiver XORs it again. If the whole stream is inverted
    (Costas loop locked upside down), word and prev30 are both inverted, their
    XOR is unchanged, and the parity still passes: no polarity to find out.
    """
    data = [bit ^ prev30 for bit in word[:24]]
    return data, parity_bits(data, prev29, prev30) == list(word[24:])


# --- frame synchronisation ----------------------------------------------------------------

def find_subframes(bits):
    """Where subframes start in the bit stream.

    A start is a position where the decoded word begins with the preamble, its
    parity and the next word's are right, and the next word names a subframe 1
    to 5. Returns (starts, lookalikes): lookalikes are positions that show the
    preamble but fail the rest (chance matches, or a real start hit by bit
    errors). Of the starts, only those on the most common 300-bit grid are kept:
    real subframes follow each other exactly 300 bits apart.
    """
    bits = [int(b) for b in bits]
    starts, lookalikes = [], []
    for s in range(2, len(bits) - 2 * BITS_PER_WORD + 1):
        word1, ok1 = check_word(bits[s:s + 30], bits[s - 2], bits[s - 1])
        if word1[:8] != PREAMBLE:
            continue
        word2, ok2 = check_word(bits[s + 30:s + 60], bits[s + 28], bits[s + 29])
        if ok1 and ok2 and 1 <= from_bits(word2[19:22]) <= 5:
            starts.append(s)
        else:
            lookalikes.append(s)
    if starts:
        grids = [s % BITS_PER_SUBFRAME for s in starts]
        grid = max(set(grids), key=lambda g: (grids.count(g), -g))
        lookalikes = sorted(lookalikes + [s for s in starts if s % BITS_PER_SUBFRAME != grid])
        starts = [s for s in starts if s % BITS_PER_SUBFRAME == grid]
    return starts, lookalikes


def read_subframe(bits, s):
    """Decode the ten words of the subframe that starts at bit `s`.

    Returns a dict: start_bit; number (1 to 5); tow, the satellite time (seconds
    of the week) at which this subframe started; parity_ok, one flag per word;
    data, the 300 bits with data bits decoded and parity positions left at 0
    (so FIELDS' numbering applies); alert and anti_spoof (flags of word 2);
    inverted, True if the stream arrived upside down.
    """
    bits = [int(b) for b in bits]
    data, ok = [], []
    for w in range(WORDS_PER_SUBFRAME):
        a = s + BITS_PER_WORD * w
        d, good = check_word(bits[a:a + BITS_PER_WORD], bits[a - 2], bits[a - 1])
        data += d + [0] * 6
        ok.append(good)
    # Word 2 gives the time of week at which the NEXT subframe starts, in units of 6 s.
    tow_next = SUBFRAME_S * from_bits(data[30:47])
    return {"start_bit": s, "number": from_bits(data[49:52]),
            "tow": (tow_next - SUBFRAME_S) % WEEK_S, "parity_ok": ok, "data": data,
            "alert": bool(data[47]), "anti_spoof": bool(data[48]),
            # Every subframe ends with the bits 00, so the bit before a preamble is
            # 0 as sent: a 1 there means every bit of the stream is inverted.
            "inverted": bool(bits[s - 1])}


def complete(subframe):
    """Whether all ten words of a decoded subframe passed parity."""
    return all(subframe["parity_ok"])


def fields_of(subframe):
    """The fields a decoded subframe carries, as rows for a table: name, bits (the
    raw bits as a string), raw (the integer), unit_value (the value of one unit),
    value (raw x unit_value), unit, meaning. Empty for subframes 4 and 5."""
    rows = []
    for name, number, parts, signed, unit_value, unit, meaning in FIELDS:
        if number != subframe["number"]:
            continue
        bits = [b for first, n in parts for b in subframe["data"][first - 1:first - 1 + n]]
        raw = from_bits(bits, signed)
        rows.append({"name": name, "subframe": number, "bits": "".join(map(str, bits)),
                     "raw": raw, "unit_value": unit_value, "value": raw * unit_value,
                     "unit": unit, "meaning": meaning, "signed": signed,
                     "parts": parts})
    return rows


def unpack(subframe):
    """The fields of a decoded subframe as numbers: {name: value}."""
    return {r["name"]: r["value"] for r in fields_of(subframe)}


# --- from the bits of one satellite to what stage 05 needs --------------------------------

def assemble_ephemeris(subframes):
    """Put subframes 1, 2 and 3 together. Returns (ephemeris, problems).

    The ephemeris is {field name: value} for every entry of FIELDS, or None with
    the reasons in `problems`. Only subframes whose ten words all passed parity
    are used, and the three must belong to the same data set: the issue number
    is repeated in each (IODE in 2 and 3, the low 8 bits of IODC in 1), because
    satellites switch to a new data set every two hours and a mix of two sets
    describes no orbit at all.
    """
    good = [sf for sf in subframes if complete(sf)]
    by_number = {n: [unpack(sf) for sf in good if sf["number"] == n]
                 for n in EPHEMERIS_SUBFRAMES}
    missing = [n for n in EPHEMERIS_SUBFRAMES if not by_number[n]]
    if missing:
        return None, ["no complete subframe " + ", ".join(map(str, missing))
                      + " (each is sent once every 30 s and lasts 6 s)"]
    # The most recent subframes 2 and 3 with the same issue number, and a subframe 1
    # of that issue.
    for sf2 in reversed(by_number[2]):
        for sf3 in reversed(by_number[3]):
            for sf1 in reversed(by_number[1]):
                if sf2["iode"] == sf3["iode_3"] == sf1["iodc"] % 256:
                    eph = {**sf1, **sf2, **sf3}
                    problems = []
                    if eph["health"] != 0:
                        problems.append(f"the satellite says it is unhealthy "
                                        f"(health = {eph['health']:.0f})")
                    return eph, problems
    return None, ["subframes 1, 2 and 3 carry different issue numbers: the satellite "
                  "changed its data during the recording"]


def decode_satellite(bits, bit_offset=0):
    """Everything stage 04 reads from one satellite's bits.

    `bits` are tracking's nav_bits (0/1, either polarity) and `bit_offset` the
    code period at which bit 0 starts (tracking's bit sync). Returns a dict:

        subframes     read_subframe() results, in order (complete or not)
        lookalikes    bit positions that show the preamble but are not a start
        ephemeris     {field: value} from subframes 1 to 3, or None
        period0_time  satellite time (s of the week) when the code period that
                      tracking numbers 0 left the satellite, or None. Code period
                      k then left at period0_time + k ms: this is what turns the
                      code timing into a clock reading in stage 05.
        inverted      True if the bits arrived inverted, None if unknown
        problems      why the satellite cannot be used (empty if it can)
    """
    bits = [int(b) for b in bits]
    starts, lookalikes = find_subframes(bits)
    subframes = [read_subframe(bits, s) for s in starts
                 if s + BITS_PER_SUBFRAME <= len(bits)]
    problems = []
    period0_time = None
    if not starts:
        problems.append("no subframe start found (preamble + parity): bit errors, or "
                        "fewer than 6 s of bits")
    else:
        # Subframe at bit s starts at code period bit_offset + 20 s, at satellite time
        # tow. Every start must tell the same story, or the time is not trustworthy.
        times_ms = {(1000 * h["tow"] - (bit_offset + 20 * h["start_bit"])) % (1000 * WEEK_S)
                    for h in (_read_how(bits, s) for s in starts)}
        if len(times_ms) == 1:
            period0_time = times_ms.pop() / 1000.0
        else:
            problems.append("the subframes disagree about the time of week")
    ephemeris, eph_problems = assemble_ephemeris(subframes)
    problems += eph_problems
    return {"subframes": subframes, "lookalikes": lookalikes, "ephemeris": ephemeris,
            "period0_time": period0_time,
            "inverted": subframes[0]["inverted"] if subframes else None,
            "problems": problems}


def _read_how(bits, s):
    """The time of week of a subframe cut by the end of the recording (its first
    two words are there and passed parity, or find_subframes would not list it)."""
    word2, _ = check_word(bits[s + 30:s + 60], bits[s + 28], bits[s + 29])
    return {"start_bit": s, "tow": (SUBFRAME_S * from_bits(word2[0:17]) - SUBFRAME_S) % WEEK_S}


def usable(sat):
    """Whether a decode_satellite() result gives stage 05 all it needs."""
    return sat["ephemeris"] is not None and sat["period0_time"] is not None \
        and not sat["problems"]


def decode_tracks(tracking_folder):
    """Decode every satellite of a tracking results folder (stage 03's files).

    Returns {prn: decode_satellite() result}, each with five more entries taken
    from tracking: bits (the stream itself, a list of 0/1), bit_offset, nbits,
    cn0_dbhz and locked. Satellites that tracking did not lock are decoded too:
    their bits are noise, no subframe is found in them, and the result says so.
    """
    folder = Path(tracking_folder)
    out = {}
    for row in read_summary(folder / SUMMARY_CSV):
        path = folder / track_file_name(row["prn"])
        if not path.is_file():
            continue
        track = load_track(path)
        sat = decode_satellite(track["nav_bits"], track["bit_offset"])
        sat.update(bits=[int(b) for b in track["nav_bits"]],
                   bit_offset=track["bit_offset"], nbits=int(len(track["nav_bits"])),
                   cn0_dbhz=row["mean_cn0_dbhz"], locked=row["locked"])
        out[row["prn"]] = sat
    return out


# --- GPS time ---------------------------------------------------------------------------------

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)      # week 0 started here
LEAP_SECONDS = 18      # GPS time is ahead of UTC by this much since 2017-01-01


def full_week(week_mod_1024, near):
    """The week number since 1980, from the 10 bits the message carries.

    The message counts weeks modulo 1024 (19.6 years), so it rolled over in 1999
    and 2019. `near` is any date within about 9 years of the truth (the date the
    recording was made, or today): the candidate closest to it is taken.
    """
    if near.tzinfo is None:
        near = near.astimezone()
    weeks_near = (near - GPS_EPOCH) / timedelta(weeks=1)
    rollovers = round((weeks_near - week_mod_1024) / 1024)
    return int(week_mod_1024 + 1024 * rollovers)


def recording_date(iq_path):
    """Roughly when a recording was made, to resolve the week number: the date in
    its sidecar file (written by the capture), else the file's own date, else now."""
    path = Path(iq_path)
    try:
        sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        return datetime.fromisoformat(sidecar["recorded"]).astimezone()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return datetime.now().astimezone()


def utc_from_gps(week, tow):
    """UTC date and time of GPS time (full week number, seconds of the week)."""
    return GPS_EPOCH + timedelta(weeks=week, seconds=tow - LEAP_SECONDS)


# --- result file: the hand-over to stage 05 (written and read only here) ------------------
#
#   navigation.json   {"satellites": {"<prn>": {...}}}: per satellite the ephemeris,
#                     period0_time, the subframes found and the problems

NAVIGATION_JSON = "navigation.json"


def save_navigation(path, satellites):
    """Write the decode_satellite() result of each satellite ({prn: result}).
    The 300 decoded bits of each subframe are left out: they are re-read from the
    track file's bits when needed (read_subframe)."""
    out = {}
    for prn, sat in sorted(satellites.items()):
        out[str(prn)] = {
            "prn": int(prn), "usable": usable(sat), "problems": sat["problems"],
            "inverted": sat["inverted"], "period0_time": sat["period0_time"],
            "ephemeris": sat["ephemeris"], "lookalikes": sat["lookalikes"],
            "subframes": [{k: sf[k] for k in ("start_bit", "number", "tow", "parity_ok",
                                              "alert", "anti_spoof")}
                          for sf in sat["subframes"]],
            **{k: sat[k] for k in ("bit_offset", "nbits", "cn0_dbhz", "locked")
               if k in sat},
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"satellites": out}, f, indent=1)
    return path


def load_navigation(path):
    """Read navigation.json back: {prn: dict}, with integer PRN keys."""
    with open(path, encoding="utf-8") as f:
        return {int(prn): sat for prn, sat in json.load(f)["satellites"].items()}


# --- encoder: build a message from an ephemeris (synthetic recordings, tests) ------------

def subframe_data(number, tow_next, eph):
    """The 300 bits of a subframe before parity (the parity positions are left at
    0). `tow_next` is the time of week at which the next subframe starts; fields
    missing from `eph` are sent as 0."""
    d = [0] * BITS_PER_SUBFRAME
    d[0:8] = PREAMBLE
    d[30:47] = to_bits((int(tow_next) % WEEK_S) // SUBFRAME_S, 17)
    d[49:52] = to_bits(number, 3)
    for name, subframe, parts, _signed, unit_value, *_ in FIELDS:
        if subframe == number:
            bits = to_bits(int(round(eph.get(name, 0) / unit_value)), sum(n for _, n in parts))
            for first, n in parts:
                d[first - 1:first - 1 + n], bits = bits[:n], bits[n:]
    if number > 3:                      # subframes 4 and 5: filler, as in unused fields
        for word in range(2, 10):
            d[30 * word:30 * word + 24] = [1, 0] * 12
    return d


def add_parity(d, prev29=0, prev30=0):
    """What the satellite sends for a subframe: data bits (inverted if the previous
    bit was 1) and parity. Words 2 and 10 end in 00, by choosing their last two
    data bits, so that the next word, and the next preamble, are sent upright."""
    sent = []
    for word in range(WORDS_PER_SUBFRAME):
        data = list(d[30 * word:30 * word + 24])
        if word in (1, 9):
            for trial in range(4):
                data[22:24] = [trial >> 1, trial & 1]
                if parity_bits(data, prev29, prev30)[4:] == [0, 0]:
                    break
        parity = parity_bits(data, prev29, prev30)
        sent += [bit ^ prev30 for bit in data] + parity
        prev29, prev30 = parity[4], parity[5]
    return sent


def encode_message(eph, tow_start, n_subframes):
    """The bits a satellite sends from time of week `tow_start` (a multiple of 6 s)
    on, for `n_subframes` subframes. The subframe number follows from the time:
    subframe 1 starts whenever the time of week is a multiple of 30 s."""
    bits, prev29, prev30 = [], 0, 0
    for j in range(n_subframes):
        start = int(tow_start) + SUBFRAME_S * j
        number = (start % 30) // SUBFRAME_S + 1
        sent = add_parity(subframe_data(number, start + SUBFRAME_S, eph), prev29, prev30)
        bits += sent
        prev29, prev30 = sent[-2:]
    return bits

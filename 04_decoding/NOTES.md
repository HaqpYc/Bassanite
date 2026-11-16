# Stage 04 — Decoding (reading the navigation message)

**Goal of this stage:** take the 50 bit/s stream that stage 03 produced for each
satellite and read what the satellite says: its **orbit** (ephemeris), its **clock
correction** and the **time**. Stage 05 needs exactly these three things, per
satellite, to compute a position.

Code: [`../bassanite/navigation.py`](../bassanite/navigation.py). Command line:
[`decode.py`](decode.py).

---

## The concepts (short version)

### 1. How the message is organised
| unit | made of | lasts |
|---|---|---|
| bit | 20 code periods | 20 ms |
| word | 30 bits: 24 of data, then 6 of parity | 0.6 s |
| subframe | 10 words (300 bits) | 6 s |
| frame | 5 subframes | 30 s |

Every subframe starts with the **preamble** `10001011`; its second word holds the
**time of week** and the **subframe number**. Subframe 1 carries the week number
and the satellite's clock correction, subframes 2 and 3 the ephemeris. Subframes 4
and 5 carry slow data (almanac, ionosphere model) spread over 12.5 minutes, which a
position does not need.

### 2. Frame synchronisation (`find_subframes`)
The stream has no markers. A subframe starts where:
1. the decoded word begins with the preamble,
2. its parity and the next word's parity are right,
3. the next word names a subframe 1 to 5,
4. and the position sits on the same 300-bit grid as the other starts.

The preamble alone is not enough: 8 bits turn up by chance about 12 times in 3000
bits (the UI marks them with red crosses). Parity makes a false start a
`6e-7`-per-position event, and the grid test removes what is left.

### 3. Parity, and the polarity that never has to be found (`check_word`)
The Costas loop of stage 03 locks with a 180° ambiguity, so about half of the
satellites arrive with every bit inverted. The message is built for this: the
satellite XORs the 24 data bits of each word with the last bit of the previous
word, and the receiver XORs them again with that bit *as received*. If the stream
is inverted, both are inverted and the XOR is unchanged. The data come out right
and the parity check passes, with no polarity decision anywhere in the code.

### 4. Fields (`FIELDS`, `fields_of`)
Each number is an integer of a fixed number of bits at a fixed place, times the
value of one unit (`e` is 32 bits in units of 2⁻³³, `√A` in units of 2⁻¹⁹ √m,
angles in half-turns). Negative values use two's complement. Fields longer than a
word's 24 data bits are split over two words. The table `FIELDS` holds all of it,
and the same table drives the encoder used by the synthetic recordings.

### 5. The ephemeris must be one data set (`assemble_ephemeris`)
Only subframes whose ten words all pass parity are used. Subframes 1, 2 and 3 must
carry the same issue number (IODE in 2 and 3, the low 8 bits of IODC in 1):
satellites switch to new data every two hours, and a mix describes no orbit. A
satellite that flags itself unhealthy is reported and not handed over.

### 6. Time: `period0_time`
A subframe that starts at bit `s` of the stream starts at tracking's code period
`bit_offset + 20·s`, and word 2 says at what satellite time it was sent. So every
code period of the track gets a satellite time, 1 ms apart. The stage hands over
the time of code period 0. Every subframe found must give the same value, or the
satellite is rejected.

The week number is sent modulo 1024 (it wrapped in 1999 and 2019): `full_week`
takes the candidate closest to the date of the recording (its sidecar file, else
the file's date).

---

## How to run

```bash
python 04_decoding/decode.py --iq recordings/<recording>.bin
```

It reads `results/<recording>/tracking/` and writes
`results/<recording>/decode/navigation.json` (+ `navigation.meta.json`). In the web
UI: the **Decode** tab.

---

## Results

**`recordings/capture_20261110_143115.bin` (60 s):** the 4 locked satellites each
give 9 complete subframes (`2 3 4 5 1 2 3 4 5`), parity 90/90 words, and a complete
ephemeris. Two arrive inverted, two upright. The decoded week is 2444 and the time
of the first sample agrees with the recording's own timestamp to within the second.
The fifth "detection" of stage 02 (a false alarm that never locked) gives no
subframe, as it should.

**`recordings/capture.bin` (30 s):** all 8 satellites give subframes `2 3 4 5`
complete, 40/40 words, but **no subframe 1**: the recording ends inside it. The
orbits are there and the clock corrections are not, so no position. A 30 s
recording (1499 bits) can never hold five complete subframes; subframes 1 to 3 are
all complete at least once only from **36 s**.

**Synthetic:** a message encoded from a known ephemeris comes back exactly, field
for field, upright or inverted and from any starting bit
(`tests/test_navigation.py`); and from samples, through tracking
(`tests/test_end_to_end.py`).

---

## Implementation notes

- **The first word needs two bits before it.** `check_word` uses the last two bits
  of the previous word, so a stream that starts exactly on a subframe loses that
  subframe. A real recording almost never does.
- **A subframe cut by the end of the file still gives the time** (its first two
  words are enough for `period0_time`), but not its fields.
- **Not decoded:** subframes 4 and 5 (almanac, ionosphere model, UTC offset). The
  leap-second count (18 s since 2017) is a constant, `LEAP_SECONDS`, because the
  page that carries it comes once per 12.5 minutes.

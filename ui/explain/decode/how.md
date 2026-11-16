---
title: Reading the navigation message
code: bassanite/navigation.py::decode_satellite, bassanite/navigation.py::assemble_ephemeris
---
## Intuition

The bits from tracking are a continuous stream with no markers. To read it we must
find where each subframe starts. Every subframe begins with the same pattern,
`10001011`. A pattern that short also appears by chance, so we accept it only where
the parity checks that follow pass, and where it repeats exactly every 300 bits.

The polarity of the stream is unknown: the carrier loop can lock with a 180°
ambiguity, which inverts every bit. The message is built so that this does not
matter: each word is decoded relative to the last bit of the word before it.

Once aligned, each field is a fixed group of bits, multiplied by the value of one
unit to get a physical number, for example the square root of the orbit's semi-major
axis. Subframes 1, 2 and 3 together hold everything a position needs from one
satellite: its clock correction and its orbit. The time written in every subframe
turns tracking's code timing into a clock.

## Math

**Structure.** One subframe is 300 bits = 10 words × 30 bits, and lasts 6 s. Five
subframes make a 30 s frame:

- Subframe 1: the week number and the satellite's clock correction.
- Subframes 2 and 3: the ephemeris, the satellite's orbit.
- Subframes 4 and 5: slow data (the other satellites' rough orbits, the ionosphere
  model), spread over 12.5 minutes. A position does not need it.
- Word 1 of every subframe starts with the preamble.
- Word 2 of every subframe holds the time of week and the subframe number.

**How long a recording must be.** Subframes 1, 2 and 3 take 18 s in a row, once every
30 s. A recording that starts just after subframe 1 began must wait for the next one:

$$T_{\min} = 30\ \mathrm{s} + 6\ \mathrm{s} = 36\ \mathrm{s}$$

plus the second or two the tracking loops take to settle.

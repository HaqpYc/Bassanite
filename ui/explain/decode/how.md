---
title: Reading the navigation message
---
## Intuition

The bits from tracking are a continuous stream with no markers. To read it we must
find where each subframe starts. Every subframe begins with the same pattern,
`10001011`. A pattern that short could appear by chance, so we accept it only if it
repeats exactly every 300 bits and the parity checks pass.

The polarity of the stream is unknown: the carrier loop can lock with a 180°
ambiguity, which inverts every bit. Finding the preamble inverted (`01110100`) tells
us to flip the whole stream.

Once aligned, each field is a fixed group of bits, multiplied by a scale factor to
get a physical value, for example the square root of the orbit's semi-major axis.

## Math

**Structure.** One subframe is 300 bits = 10 words × 30 bits, and lasts 6 s. Five
subframes make a 30 s frame:

- Subframe 1: satellite clock correction terms.
- Subframes 2 and 3: ephemeris, the satellite's orbit.
- Word 1 (TLM) of every subframe holds the preamble.
- Word 2 (HOW) of every subframe holds the time of week.

**Parity.** Each 30-bit word is 24 data bits plus 6 parity bits. The parity is
computed from the word's data bits and the last two bits of the previous word,
$D_{29}^*$ and $D_{30}^*$. If $D_{30}^* = 1$, the 24 data bits were transmitted
inverted.

**Scaling.** A field stored as the integer $k$ (two's complement where signed),
with scale factor $s$, has the value

$$v = k \cdot s, \qquad \text{e.g. } \sqrt{A} = k \cdot 2^{-19}\ \sqrt{\mathrm{m}}$$

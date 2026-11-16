---
title: From bits to numbers
code: bassanite/navigation.py::fields_of, bassanite/navigation.py::from_bits
---
## Intuition

The message has no decimal points and no units. Every number is a whole number,
written in a fixed group of bits at a fixed place of a fixed subframe. The
specification says where each one is and what one unit of it is worth.

Take the eccentricity of the orbit (how far the ellipse is from a circle). It is sent
as a 32-bit whole number, in units of $2^{-33}$. A received value of 100,000,000
means an eccentricity of 100,000,000 × 2⁻³³ = 0.0116.

Three details:

- **Negative numbers** use two's complement, as in a computer: if the first bit is
  1, subtract $2^n$ from the value read from the $n$ bits.
- **Long fields are split.** A word holds only 24 data bits, so a 32-bit number is
  sent as 8 bits at the end of one word and 24 bits in the next. The picture above
  shows such a field in two pieces of the same colour.
- **Angles are sent in half-turns** (units of π radians). The specification fixes
  the value of π to use, 3.1415926535898, so that every receiver computes the same
  orbit.

The table lists each field of the selected subframe: its bits, the whole number they
spell, the value of one unit, and the result.

## Math

A field of $n$ bits $b_1 \dots b_n$ (most significant first) spells the integer

$$k = \sum_{i=1}^{n} b_i\, 2^{\,n-i} \qquad \text{or, if signed,} \qquad
k = -b_1\, 2^{\,n-1} + \sum_{i=2}^{n} b_i\, 2^{\,n-i}$$

and stands for the value $v = k \cdot s$, with $s$ the value of one unit. For example

$$\sqrt{A} = k \cdot 2^{-19}\ \sqrt{\mathrm{m}}, \qquad
e = k \cdot 2^{-33}, \qquad
M_0 = k \cdot 2^{-31} \cdot \pi\ \mathrm{rad}, \qquad
a_{f0} = k \cdot 2^{-31}\ \mathrm{s}$$

The unit sets the resolution. For the satellite clock offset $a_{f0}$, one unit is
$2^{-31}$ s = 0.47 ns, which light covers in 14 cm. For $\sqrt{A}$, one unit changes
the orbit radius of 26,560 km by about 2 cm.

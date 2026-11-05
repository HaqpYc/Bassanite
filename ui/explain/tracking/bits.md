---
title: From the prompt to data bits
code: bassanite/tracking.py::bit_sync_scores, bassanite/tracking.py::bit_sync
---
## Intuition

Once the carrier is locked, the prompt I is the satellite's message, one value per
millisecond. Each navigation bit lasts exactly **20 ms** (50 bits per second), so I
keeps the same sign for 20 ms and may flip at the next bit edge.

But where does a bit start? There are 20 possible answers (0 to 19 ms). For each
one, cut the stream into 20 ms pieces and add up each piece. Pieces that line up with
the real bits never contain a flip, so their sums are large. Misaligned pieces
straddle flips, half cancels, and the sums are smaller. The bar chart shows this
"energy" for the 20 choices; the tallest one (green) is used.

Each bit is then the sign of its 20 ms sum: 1 if positive, 0 if negative. Because
the carrier loop cannot tell up from down (see the carrier panel), all bits may come
out inverted. Stage 04 checks that with the preamble.

The second plot ("What the loops measure") shows the two loops' raw errors each
millisecond: noisy, but centred on zero when locked.

## Math

For an offset $o \in \{0, \dots, 19\}$, the bit sums and their energy are

$$S_k(o) = \sum_{m=0}^{19} I_P[20k + m + o], \qquad
\text{score}(o) = \frac{1}{K}\sum_{k} \left|S_k(o)\right|$$

The bit edge is $\hat o = \arg\max_o \text{score}(o)$, and each bit is

$$b_k = \begin{cases} 1 & S_k(\hat o) > 0 \\ 0 & \text{otherwise} \end{cases}$$

The soft sums $S_k$ are also saved (`nav_symbols`). Summing 20 ms raises the
signal-to-noise ratio 20 times (13 dB) over one millisecond, which is why bits are
reliable even when individual milliseconds are noisy.

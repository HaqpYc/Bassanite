---
title: Reading every satellite's clock at the same instant
code: bassanite/position.py::satellite_time, bassanite/position.py::receiver_clock_at_0
---
## Intuition

Step 4 turned each satellite's code timing into a clock: we know which sample of the
recording each code period starts at, and what time the satellite's clock showed when
that code period left.

Now pick one instant of the recording, one sample, and ask each satellite: what does
your clock show in the signal arriving right now? The answers differ by up to 20 ms.
The satellites' clocks are (almost) synchronised, so the differences come from the
journey: the signal showing the latest time comes from the nearest satellite, and one
showing a time 10 ms earlier has come from 3000 km further away.

An instant usually falls inside a code period, not at its start. We interpolate: a
third of the way through a code period is a third of a millisecond later.

To turn these readings into distances we would need to know what time it really is at
that instant. We don't: the receiver's only clock is its counter of samples, and
nobody told it when sample 0 was taken. So we set it roughly, by assuming the nearest
satellite is 70 ms away, and let the solver find how wrong that was.

## Math

Code period $k$ of a satellite starts at sample $n_k$ of the recording (tracking's
`code_phase_samp`) and left the satellite at $t^{sv}_0 + k \cdot 1\ \mathrm{ms}$
(step 4). For a sample $n$ with $n_k \le n < n_{k+1}$:

$$t^{sv}(n) = t^{sv}_0 + \left(k + \frac{n - n_k}{n_{k+1} - n_k}\right) \cdot 1\ \mathrm{ms}$$

The receiver clock is $t_{rx}(n) = t_{rx}(0) + n / f_s$, and the **pseudorange** is

$$\rho_i(n) = c\,\big(t_{rx}(n) - t^{sv}_i(n)\big)$$

One sample lasts $1/f_s$, during which light travels {{m_per_sample:.0f}} m. The
code loop places $n_k$ to a few hundredths of a sample (about 2 m, measured on
`capture_20261110_143115.bin`), which is why a position can be good to metres.

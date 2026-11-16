---
title: Turning the code timing into a clock
code: bassanite/navigation.py::read_subframe, bassanite/navigation.py::full_week
---
## Intuition

A satellite's signal is a clock, and by now we can read all of its hands:

- **The message** says, in word 2 of each subframe, at what time of the week the
  next subframe starts. This is the coarse hand: it counts in steps of 6 s.
- **Frame synchronisation** found the bit at which each subframe starts. Bits are
  20 ms long.
- **Bit synchronisation** (step 3) found the code period at which each bit starts.
  Code periods are 1 ms long.
- **The code loop** (step 3) knows where each code period starts in the recording,
  to a small fraction of a sample.

Put together: we know the sample of the recording at which a subframe starts, and
the satellite time at which that subframe was sent. From there, every code period is
1 ms of satellite time later than the one before. Step 5 uses nothing else: it reads
this clock for several satellites at the same instant.

The first three hands are whole numbers. They are either right or wrong, and parity
makes sure they are right. All the precision comes from the last one.

**The week.** The time of week restarts at 0 every Sunday at midnight. The week
itself is in subframe 1, on 10 bits only: it wraps around every 1024 weeks (19.6
years), as it did in 1999 and 2019. We pick the week closest to the date of the
recording.

## Math

Word 2 carries the count $Z$ of 6 s steps at the start of the **next** subframe, so
the subframe that holds it started at

$$t_{\text{start}} = 6\,Z - 6 \ \ \mathrm{s\ of\ the\ week}$$

If this subframe starts at bit $s$ of the stream, and bit 0 starts at code period
$k_0$ (tracking's bit offset), it starts at code period $k_0 + 20\,s$. So code period
$k$ left the satellite at

$$t^{sv}(k) = t_{\text{start}} - (k_0 + 20\,s)\cdot 1\ \mathrm{ms} + k \cdot 1\ \mathrm{ms}$$

The value for $k = 0$ is what this step hands over (`period0_time`). Every subframe
of the recording must give the same value, and the code checks that they do.

The full week number is $W = W_{10} + 1024\,n$, with $W_{10}$ the 10 bits received
and $n$ chosen so that $W$ is the closest to the week of the recording's date. GPS
time does not have leap seconds, so UTC is behind it, by 18 s since 2017:

$$t_{\text{UTC}} = \text{6 January 1980} + W \text{ weeks} + t - 18\ \mathrm{s}$$

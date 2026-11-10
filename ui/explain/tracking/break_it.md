---
title: Two bugs that kept the loops from locking
code: bassanite/tracking.py::atan2_error, bassanite/tracking.py::costas_error, bassanite/tracking.py::ChannelTracker.step
---
## Intuition

Both switches undo a decision that took real debugging to find while this receiver
was written (see `03_tracking/NOTES.md`). Each one breaks a different block of the
map above.

**atan2 instead of atan.** The navigation data flips the carrier by half a turn at
every bit edge, up to 50 times a second. The Costas discriminator, `atan(Q/I)`, cannot
tell a flipped signal from an unflipped one, so it ignores the data. `atan2(Q, I)` sees
each flip as a sudden phase error of half a cycle, and the loop jerks towards it. On
PRN 20 of `capture.bin` it never locks: |Q|/|I| stays between 0.4 and 0.7, and the
Doppler wanders over more than 100 Hz.

**Rounding the code period.** A code period is 1023 chips. With Doppler, the code
arrives slightly compressed or stretched, so a period lasts a fraction of a sample
more or less than 4000 samples. The receiver therefore processes exactly enough
samples to finish each period and carries the leftover fraction of a chip into the
next one. If it cuts every period at 4000 samples instead, the replica slides off the
code a little more every millisecond. PRN 20 (2425 Hz) locks, then loses everything
after about half a second. PRN 5 (about −90 Hz) keeps going, because its period is
almost exactly 4000 samples. That is why, with this bug, "only PRN 5 worked".

Two other decisions of NOTES.md can be switched off in the code (`TrackOptions`) but
are not offered here, because they change nothing you could see in these plots:
without carrier aiding, the slow code loop takes about 2 s to learn the code's drift
by itself, then follows it; and advancing the carrier phase with the updated rather
than the used frequency makes no measurable difference with this loop.

## Math

**Costas vs atan2.** A data flip turns the prompt $(I, Q)$ into $(-I, -Q)$:

$$\frac{1}{2\pi}\arctan\frac{-Q}{-I} = \frac{1}{2\pi}\arctan\frac{Q}{I},
\qquad \frac{1}{2\pi}\,\mathrm{atan2}(-Q, -I) = \frac{1}{2\pi}\,\mathrm{atan2}(Q, I) \pm \frac{1}{2}$$

The first is unchanged; the second jumps by half a cycle, which the loop filter turns
into a frequency kick at every bit edge.

**The code period in samples.** The code NCO runs at
$f_{\text{code}} = 1.023\ \text{MHz}\,(1 + f_D / f_{L1})$, so one period of 1023 chips
lasts

$$N = \frac{1023\, f_s}{f_{\text{code}}} = \frac{4000}{1 + f_D / f_{L1}} \ \text{samples at } f_s = 4\ \text{MHz}.$$

For $f_D = 2425$ Hz, $N = 3999.9938$. Stepping exactly 4000 samples each period
puts the replica $0.0062$ samples behind per millisecond: 6.2 samples/s, or
$6.2 \times 1023 / 4000 = 1.6$ chips/s. After about 0.3 s the replica is half a chip
off, where the Late correlator was, and the prompt has lost half its correlation.
After 0.6 s, a whole chip, nothing is left. The code loop cannot catch it: in this
broken version every period restarts the replica at chip 0, so the DLL's corrections
never move where the period starts. For $f_D = -90$ Hz the drift is only
0.06 chips/s, so the replica takes about 8 s to slide half a chip off.

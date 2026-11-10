---
title: Loop bandwidth: too narrow, too wide
code: bassanite/tracking.py::calc_loop_coef, bassanite/acquisition.py::refine_doppler
---
## Intuition

The carrier loop's bandwidth is a trade-off.

- **Too narrow, and it cannot catch the signal.** Before the loop closes, a fine
  search narrows the frequency error to a few Hz, sometimes a few tens of Hz. A narrow
  loop reacts so slowly that the phase runs away before it can correct that error.
- **Too wide, and it follows the noise.** It reacts to every noisy measurement. Past
  a point, with one update per millisecond, it overcorrects and goes unstable: the
  frequency swings wildly.

Measured on the first 1.5 s of PRN 20 in `capture.bin`: no lock at 2 Hz; lock from
5 to 100 Hz (|Q|/|I| between 0.1 and 0.16); unstable from 150 Hz, with the Doppler
swinging by hundreds of Hz. The default, 12 Hz, sits comfortably inside. Weaker
satellites have a narrower good range: try PRN 15.

How far off the loop starts matters as much as its bandwidth. This try-it starts from
step 2's 250 Hz search bin and runs the pull-in search first, as tracking does when it
has no refined Doppler: that leaves PRN 20 9 Hz from the frequency the loop settles
on. An earlier version of that search left it 34 Hz off, and then 5 Hz did not lock
either. (The real tracking run starts from step 2's refined Doppler, 4 Hz off.)

## Math

**Noise.** For a Costas loop of noise bandwidth $B_L$, the thermal phase jitter is

$$\sigma_\phi = \frac{180°}{\pi}\sqrt{\frac{B_L}{C/N_0}\left(1 + \frac{1}{2\,T\,C/N_0}\right)}$$

At 43 dB-Hz: 1.4° for 12 Hz, 4.0° for 100 Hz. At 36.6 dB-Hz (PRN 15): 3.1° and
8.9°. A common rule keeps $3\sigma_\phi$ below the Costas limit of 45°, so
$\sigma_\phi < 15°$.

**Stability.** The filter of "Doppler and code drift" is a continuous-time design
applied once per $T = 1$ ms. That holds while $B_L T \ll 1$. Here it breaks between
$B_L$ = 100 Hz ($B_L T = 0.1$) and 150 Hz ($0.15$).

**Pull-in.** `refine_doppler` tests the seed frequency ±200 Hz in 25 Hz steps. The
grid alone leaves up to 12.5 Hz of error. Noise adds more, because the search sums
1 ms correlations, and a few tens of Hz cost them almost nothing ($\mathrm{sinc}^2$
of 0.03 is 0.997). The loop's natural frequency
$\omega_n = 8\zeta B_L / (4\zeta^2 + 1)$ is 3.8 rad/s for 2 Hz, 9.5 rad/s for 5 Hz
and 15 rad/s for 8 Hz. The slower the loop, the smaller the starting error it catches
before the phase slips: 5 Hz catches PRN 20's 9 Hz but not 34 Hz, and 2 Hz catches
neither.

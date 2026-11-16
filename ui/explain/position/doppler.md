---
title: Checking the answer with the Doppler
code: bassanite/position.py::doppler_check, bassanite/position.py::predicted_doppler, bassanite/position.py::signal_arriving
---
## Intuition

How do we know the position is right? With five or more satellites, the leftover
differences (residuals) tell: a wrong orbit or a wrong time makes the equations
disagree. With exactly four, the equations always have an exact solution, right or
wrong.

There is a second measurement that the position never used: the **carrier
frequency** of each satellite, which the carrier loop of step 3 followed. A satellite
that approaches is received at a higher frequency, one that moves away at a lower
one. This Doppler shift depends on the satellite's velocity along the line from it
to us.

So we can turn the question around. Given the position we found, the time we found
and the orbits we decoded, what Doppler should each satellite have had? If
everything is right, the predictions match the measurements, up to one common
offset: the receiver's own oscillator is slightly off, which shifts every satellite
by the same amount.

The test is sensitive. A position wrong by one kilometre changes the predicted
Doppler by up to about 1 Hz, differently for each satellite. A time wrong by one
second changes it by up to about 1 Hz too. The table shows what is left once the
common offset is removed.

## Math

The signal arriving at time $t$ left the satellite when its clock showed
$t - L(t)$, with $L$ the lag: flight time minus the satellite's clock error.
`signal_arriving` computes $L$ from the orbit and the position. When $L$ shrinks,
the satellite's signal is received in fast motion, and its carrier of frequency
$f_{L1}$ = 1575.42 MHz is shifted by

$$f_D = -f_{L1}\, \frac{dL}{dt}$$

A receiver whose oscillator runs fast by the fraction $\epsilon$ tunes too high by
$\epsilon f_{L1}$, so it measures

$$f_{\text{measured}} = f_D - \epsilon\, f_{L1}$$

for every satellite. The same $\epsilon$ makes its sample clock gain on GPS time at
the rate $\epsilon$ seconds per second, which the position solver sees as a drift of
the clock error $\delta t$. The two estimates of $\epsilon$ can be compared. On
`capture_20261110_143115.bin` they differ by 22 Hz (310 Hz measured, 332 Hz
predicted from the clock drift). A likely cause, not verified here, is that the
HackRF's tuner sets its frequency in steps of a few tens of hertz.

A horizontal position error $\Delta x$ changes the direction to the satellite by
$\Delta x / d$, and the predicted Doppler by at most

$$\Delta f_D \approx \frac{f_{L1}}{c} \cdot v_\perp \cdot \frac{\Delta x}{d}
\approx \frac{1575.42\ \mathrm{MHz}}{c} \cdot 3.9\ \mathrm{km/s} \cdot \frac{1\ \mathrm{km}}{20{,}200\ \mathrm{km}}
\approx 1\ \mathrm{Hz}$$

---
title: From clock readings to distances: the corrections
code: bassanite/position.py::transmit_time, bassanite/position.py::sat_clock_error, bassanite/position.py::sat_position, bassanite/position.py::troposphere_delay
---
## Intuition

A pseudorange is a raw reading. Before it can be used as a distance, five things
must be accounted for. The Try-it below switches each one off, to show what it is
worth. The figures in bold were measured that way on `capture_20261110_143115.bin`,
whose four satellites sit in the same part of the sky: a better geometry would give
smaller ones.

- **The satellite's clock is not exactly on GPS time.** Each satellite's atomic clock
  is left to drift, and subframe 1 says by how much: an offset, a drift and a drift
  rate. The offsets reach hundreds of microseconds, different for each satellite.
  Without this correction the position moves by **3,400 km**.
- **Relativity.** A GPS orbit is slightly elliptical, so the satellite's speed and
  height vary along it, and with them the rate of its clock. The effect is up to a
  few tens of nanoseconds, computed from the orbit. Worth **37 m**.
- **The delay inside the satellite.** The broadcast clock correction is meant for
  receivers that use two frequencies. A receiver that uses L1 alone must subtract a
  small delay given in subframe 1. Worth **8 m**.
- **The Earth turns while the signal flies.** The orbit gives the satellite's
  position in axes fixed to the Earth at the moment of transmission. During the
  70 ms flight the Earth, and those axes, turn by 5 millionths of a radian, which
  moves the satellite's coordinates by up to 130 m. Worth **19 m**.
- **The air slows the signal.** The lower atmosphere adds about 2.4 m of path
  straight up, and several times more for a satellite near the horizon. Worth
  **11 m**, mostly on the height.

One delay is left uncorrected: the **ionosphere** (charged upper atmosphere),
typically 2 to 10 m by day and more near the horizon. The satellites broadcast a
model for it, but only once every 12.5 minutes, longer than our recordings.

## Math

**Time of transmission.** With $t^{sv}$ the time read in the signal:

$$t^{tx} = t^{sv} - \Delta t^{sv}, \qquad
\Delta t^{sv} = a_{f0} + a_{f1}\,(t - t_{oc}) + a_{f2}\,(t - t_{oc})^2
+ F\, e\, \sqrt{A}\, \sin E - T_{GD}$$

with $F = -4.442807633 \times 10^{-10}\ \mathrm{s}/\sqrt{\mathrm{m}}$, $e$ the
eccentricity, $A$ the semi-major axis and $E$ the eccentric anomaly of the satellite
at that time.

**Where the satellite was.** `sat_position` follows the specification: solve
Kepler's equation $M = E - e \sin E$ for $E$, get the angle and radius in the
orbit's plane, apply the six small corrections ($C_{uc}, C_{us}, C_{rc}, C_{rs},
C_{ic}, C_{is}$), and turn the plane by its inclination and by the longitude of its
node.

**Earth rotation.** With $\tau$ the flight time and
$\Omega_e = 7.2921151467 \times 10^{-5}$ rad/s, the satellite's coordinates are
turned about the Earth's axis by the angle $\Omega_e \tau$:

$$\mathbf{s}' = R_z(\Omega_e \tau)\, \mathbf{s}, \qquad
\Omega_e \tau \approx 5 \times 10^{-6}\ \mathrm{rad}$$

**Troposphere.** A simple model, for a satellite at elevation $\theta$ and a
receiver at height $h$:

$$T(\theta) \approx \frac{2.47\ \mathrm{m}}{\sin\theta + 0.0121} \cdot e^{-h / 8000\ \mathrm{m}}$$

The corrected equation the solver uses is then

$$c\,\big(t_{rx} - t^{tx}_i\big) = \lVert \mathbf{s}'_i - \mathbf{r} \rVert + T(\theta_i) + c\,\delta t$$

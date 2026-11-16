---
title: Why the place of the satellites in the sky matters
code: bassanite/position.py::dilution_of_precision, bassanite/position.py::azimuth_elevation
---
## Intuition

Every distance is measured with a small error, a metre or two. How much error that
puts on the position depends on where the satellites are.

Think of two people each telling you how far you are from them. If they stand in
directions at right angles, the two circles cross cleanly and you know where you
are. If they stand in almost the same direction, the two circles are almost
parallel where they cross, and a small error on either distance moves the crossing
point a long way.

Satellites are the same. Spread around the sky, they pin the position down. Bunched
in one part of the sky, they leave it loose in the directions they do not cover.
The number that says how loose is the **dilution of precision** (DOP): the position
error is the ranging error multiplied by it. Below 2 is good, above 10 is poor.

Height is always worse than the horizontal position: satellites are only ever above
us, never below, so the vertical direction is covered from one side only. And the
receiver's clock error looks, to the solver, much like a change of height (both
change all the distances together), which makes the two hard to separate.

## Math

The solver's matrix $H$ (one row $[-\mathbf{u}_i^{\mathsf{T}},\ 1]$ per satellite)
turns errors on the pseudoranges into errors on the solution. If each pseudorange has
an independent error of standard deviation $\sigma$, the covariance of the solution
is

$$\mathrm{cov}\begin{bmatrix} \mathbf{r} \\ c\,\delta t \end{bmatrix}
= \sigma^2 \left( H^{\mathsf{T}} H \right)^{-1} = \sigma^2\, Q$$

With $Q$ expressed in east, north, up at the receiver:

$$\mathrm{HDOP} = \sqrt{Q_{ee} + Q_{nn}}, \qquad \mathrm{VDOP} = \sqrt{Q_{uu}}, \qquad
\mathrm{TDOP} = \sqrt{Q_{tt}}$$

and the expected errors are $\sigma \cdot \mathrm{HDOP}$ horizontally and
$\sigma \cdot \mathrm{VDOP}$ in height. DOP depends only on the directions
$\mathbf{u}_i$ of the satellites, not on the signals.

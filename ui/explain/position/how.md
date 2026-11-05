---
title: From distances to a position
---
## Intuition

Each satellite tells us where it was and when it sent its signal. We know when the
signal arrived, so the travel time multiplied by the speed of light gives a distance.
You are somewhere on a sphere of that radius around the satellite. Three spheres
intersect at a point.

The catch: the HackRF's clock is not synchronised with the satellites' atomic clocks.
A clock error of just 1 µs makes every distance wrong by 300 m. So the receiver's
clock error becomes a fourth unknown, and we need a fourth satellite. These
"distances with a common clock error" are called **pseudoranges**.

The equations aren't linear, so the solver starts from a guess (even the centre of
the Earth works) and improves it a few times until it stops moving.

## Math

**Pseudorange** to satellite $i$ at position $\mathbf{s}_i$, for receiver position
$\mathbf{r}$ and receiver clock error $\delta t$:

$$\rho_i = \lVert \mathbf{s}_i - \mathbf{r} \rVert + c\,\delta t + \varepsilon_i$$

There are four unknowns $(x, y, z, c\,\delta t)$, so at least four satellites are
needed.

**Linearisation** around the current estimate, with $\mathbf{u}_i$ the unit vector
from the receiver to satellite $i$:

$$\Delta \rho_i \approx -\mathbf{u}_i^{\mathsf{T}} \Delta \mathbf{r} + c\,\Delta \delta t, \qquad H_i = \begin{bmatrix} -\mathbf{u}_i^{\mathsf{T}} & 1 \end{bmatrix}$$

**Least-squares update**, repeated until it converges:

$$\Delta \mathbf{x} = \left( H^{\mathsf{T}} H \right)^{-1} H^{\mathsf{T}}\, \Delta \boldsymbol{\rho}$$

**Dilution of precision (DOP)**: the diagonal of $(H^{\mathsf{T}} H)^{-1}$ shows
how much the satellite geometry amplifies range errors. Satellites spread across the
sky give a small DOP.

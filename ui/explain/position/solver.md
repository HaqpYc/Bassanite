---
title: Four unknowns, solved by least squares
code: bassanite/position.py::solve_position
---
## Intuition

Each satellite gives one equation: "my distance to the receiver, plus the receiver's
clock error, equals the pseudorange". The unknowns are the receiver's three
coordinates and its clock error.

The equations cannot be solved directly, because a distance is not a linear function
of the coordinates (there is a square root in it). So the solver works by successive
guesses:

1. Start anywhere. We start at the centre of the Earth, with a clock error of zero.
2. Compute what the pseudoranges would be from there, and compare with the measured
   ones.
3. Ask: which small move, and which change of clock, would reduce these differences
   the most? Near the current guess, moving 1 m towards a satellite shortens its
   distance by 1 m: this makes the question a set of linear equations, which is
   solved at once.
4. Apply the move and start again from 2.

The plot shows the distance between each guess and the final answer. It starts
6,370 km away and collapses in four or five steps (on
`capture_20261110_143115.bin`: 1,200 km, 29 km, 25 m, 1.5 cm).
With more than four satellites there are more equations than unknowns and no
position satisfies them all exactly: the solver takes the one with the smallest sum
of squared differences. What is left over (the residuals) then measures how well the
satellites agree.

## Math

For satellite $i$ at $\mathbf{s}_i$, the current estimate $(\mathbf{r}, \delta t)$
predicts the pseudorange $\lVert \mathbf{s}_i - \mathbf{r} \rVert + c\,\delta t$. The
difference with the measured one is the **misfit**

$$\Delta\rho_i = \rho_i - \big( \lVert \mathbf{s}_i - \mathbf{r} \rVert + c\,\delta t \big)$$

A small change $(\Delta\mathbf{r}, c\,\Delta\delta t)$ of the estimate changes the
prediction by $-\mathbf{u}_i^{\mathsf{T}} \Delta\mathbf{r} + c\,\Delta\delta t$, with
$\mathbf{u}_i = (\mathbf{s}_i - \mathbf{r}) / \lVert \mathbf{s}_i - \mathbf{r} \rVert$
the unit vector towards the satellite. Writing one row per satellite:

$$H \begin{bmatrix} \Delta\mathbf{r} \\ c\,\Delta\delta t \end{bmatrix} = \Delta\boldsymbol{\rho},
\qquad H = \begin{bmatrix} -\mathbf{u}_1^{\mathsf{T}} & 1 \\ \vdots & \vdots \\ -\mathbf{u}_n^{\mathsf{T}} & 1 \end{bmatrix}$$

solved in the least-squares sense:

$$\begin{bmatrix} \Delta\mathbf{r} \\ c\,\Delta\delta t \end{bmatrix}
= \left( H^{\mathsf{T}} H \right)^{-1} H^{\mathsf{T}}\, \Delta\boldsymbol{\rho}$$

This is Newton's method. With exactly four satellites $H$ is square, the solution is
exact, and the residuals are zero whatever the measurements: four satellites give a
position but no way to tell whether it is right. That is what the Doppler check
below is for.

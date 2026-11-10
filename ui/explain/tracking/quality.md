---
title: Signal strength (C/N0) and the lock indicator
code: bassanite/tracking.py::estimate_cn0, bassanite/tracking.py::summarize
---
## Intuition

**C/N0** (green) measures how strong the satellite is compared with the noise, in a
way that does not depend on the receiver's bandwidth. Rough guide: above 40 dB-Hz is
strong, 35–40 is fine, below about 30 tracking struggles. The tracked satellites of
`capture.bin` sit between 37 and 43 dB-Hz.

It is measured by comparing two ways of adding up 20 ms of prompt values. Adding the
complex values first (narrowband) keeps the signal and averages the noise away.
Adding their powers (wideband) keeps everything. The stronger the signal, the
larger the ratio between the two.

**|Q| / |I|** (purple) is the lock indicator. When the carrier is locked, the energy
is on I and Q is just noise, so the ratio is small. Below 0.3 (dashed line) counts
as locked. A satellite whose ratio stays near 1 never locked: its energy is spread
around the circle. PRN 7 in `capture.bin` did that until the tracking started from a
better Doppler estimate; now it locks at about 37 dB-Hz.

## Math

For each 20 ms block (aligned with the bits, so no data flip inside), with $M = 20$:

$$\text{NBP} = \Big(\sum_{m} I_m\Big)^2 + \Big(\sum_{m} Q_m\Big)^2, \qquad
\text{WBP} = \sum_{m} \left(I_m^2 + Q_m^2\right), \qquad
\text{NP} = \frac{\text{NBP}}{\text{WBP}}$$

$$\frac{C}{N_0} = 10\log_{10}\left(\frac{1}{T}\cdot\frac{\text{NP} - 1}{M - \text{NP}}\right)\ \text{dB-Hz},
\qquad T = 1\ \text{ms}$$

NP goes from 1 (pure noise) to $M$ (pure signal). The value shown on the cards is the
median over all blocks.

**Lock indicator**, per bit here and over the whole track for the cards:

$$q = \frac{\operatorname{median}|Q_P|}{\operatorname{median}|I_P|}, \qquad
\text{locked if } q < 0.3$$

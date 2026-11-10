---
title: Gains and antenna power: the receive chain
code: bassanite/hackrf.py::bias_tee_check
---
## Intuition

Before reaching the ADC, the signal goes through a chain of amplifiers:

1. **The antenna's own amplifier (LNA)**, inside the active patch antenna. It has no
   battery: the HackRF sends it DC power through the coax cable. This is the
   **bias-tee** ("antenna power").
2. **The HackRF's RF amplifier**, on or off.
3. **The HackRF's LNA gain**, 0 to 40 dB in 8 dB steps.
4. **The HackRF's VGA gain**, 0 to 62 dB in 2 dB steps.

**The first amplifier matters most.** Every stage adds its own noise, but noise added
after a strong amplifier is small next to the signal-plus-noise already amplified.
That's why GPS antennas put an amplifier right at the antenna, before the cable's
losses.

The HackRF's gains can't improve the signal-to-noise ratio any more. They only scale
the samples to fit the ADC: enough to use several levels, not so much that they
clip. Watch the "ADC levels used" plot during the live preview while changing them.

**Bias-tee check.** It records twice with the same gains, antenna power off then on.
A powered antenna amplifier lifts the noise floor, because it amplifies the noise of
the sky and its own. If the floor doesn't move, the amplifier isn't working.

**Live preview** records 0.1 s again and again and analyses each piece, so every
plot follows the antenna live.

## Math

**Friis formula.** For stages with noise factors $F_k$ and gains $G_k$ (linear
units), the noise factor of the whole chain is

$$F = F_1 + \frac{F_2 - 1}{G_1} + \frac{F_3 - 1}{G_1 G_2} + \cdots$$

For example, an antenna LNA with $G_1 = 25$ dB ($\times 316$) and a 2 dB noise
figure ($F_1 = 1.58$), ahead of a receiver with a 10 dB noise figure ($F_2 = 10$):

$$F = 1.58 + \frac{10 - 1}{316} \approx 1.61 \quad (2.1\ \text{dB})$$

The receiver's 10 dB almost disappears: the first stage sets the noise.

**Bias-tee check.** Each recording is cut into blocks of about 1 ms. For each
block, the noise power is averaged over the bins within ±1 MHz of the centre,
skipping the DC bins. The floor $P$ is the **median** over blocks, so short
interference bursts (which only a powered antenna picks up) don't inflate it:

$$\Delta = 10 \log_{10} \frac{P_{\mathrm{on}}}{P_{\mathrm{off}}}$$

A clear, repeatable rise (3 dB or more) means the amplifier is powered. Its size
depends on the antenna's gain and on how much noise the HackRF itself adds.

**Bursts.** Steady noise varies very little from one 1 ms block to the next (about
0.1 dB, since each block averages 4000 samples). A block more than 2 dB above the
median is therefore not noise but something intermittent: interference.

**Recording size.** $T$ seconds at $f_s$ samples per second, 2 bytes per sample
(int8 I and Q): 60 s at 4 MSPS is $60 \times 4 \times 10^6 \times 2 = 480$ MB.
Why 60 s: a satellite's orbit and clock are spread over subframes 1 to 3 of its 30 s
frame, and a recording holds each of them complete only from about 36 s on.

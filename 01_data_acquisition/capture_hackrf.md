# Capturing live IQ from the HackRF

How to record a raw GPS L1 IQ file with `hackrf_transfer` for the rest of the
pipeline. The reference recording `recordings/capture.bin` was made this way.

## Prerequisites
- HackRF connected, active GPS L1 patch antenna attached, **antenna outdoors with a
  clear sky view** (this is the single biggest factor in signal quality).
- `hackrf_transfer` available. On this machine it ships with **radioconda** as
  `hackrf_transfer.exe` and is **not on the default PATH** — run it from the
  radioconda prompt, or use its full path.
- Verify the device first: `hackrf_info`.

## Capture command (native Windows / radioconda)

```powershell
# 1575.42 MHz, 4 MSPS, bias-tee ON, 30 seconds, int8 interleaved output
# (run from the project root so `recordings\` resolves, or give an absolute path)
hackrf_transfer -r recordings\capture.bin `
  -f 1575420000 `
  -s 4000000 `
  -p 1 `
  -l 32 `
  -g 30 `
  -n 120000000
```

| Flag | Meaning |
|---|---|
| `-r <file>` | receive (record) to this file (int8 interleaved I/Q) |
| `-f 1575420000` | center frequency = GPS L1, 1575.42 MHz |
| `-s 4000000` | sample rate = 4 MSPS |
| `-p 1` | **bias-tee on** — powers the active antenna's LNA |
| `-l 32` | LNA (IF) gain, dB (0–40, step 8) |
| `-g 30` | VGA (baseband) gain, dB (0–62, step 2) |
| `-n 120000000` | number of samples to capture (4 MSPS × 30 s = 120 M) |

Result: 120 M samples × 2 bytes = **240 MB** for 30 s.

## Notes
- **Confirm the bias-tee actually powers the LNA.** Clone HackRFs sometimes have a
  weak/ineffective bias-tee; without LNA gain, satellites stay buried. Check antenna
  current draw or test the antenna on a known-good GPS receiver.
- Keep gains moderate to avoid ADC compression (the project tested 8/16 up to 32/30
  with no saturation issues on this antenna; the reference capture used ~32/30).
- After capturing, **always verify the file with stage 02** before trusting it:
  `python 02_acquisition/scan_prns.py --iq <file>` — look for several PRNs whose
  detections persist across `--skip-seconds`.
- WSL2 alternative: capture with the Linux `hackrf_transfer` after
  `usbipd attach`; the file format is identical.

"""Bassanite: the GPS receiver's stages as importable modules.

    acquisition   stage 02: which satellites are present (code phase + Doppler)
    tracking      stage 03: follow each satellite (PLL + DLL) -> 50 bps bits
    provenance    which recording and settings produced a stage's result files

The numbered folders (02_acquisition/, 03_tracking/, ...) keep the command-line
scripts, notes and result files; they call into this package. Shared low-level
pieces (Gold codes, IQ loading, spectra, HackRF control) live in common/.
"""

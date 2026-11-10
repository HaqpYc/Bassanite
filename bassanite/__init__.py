"""Bassanite: the GPS receiver's stages as an importable package.

    acquisition   stage 02: which satellites are present (code phase + Doppler)
    tracking      stage 03: follow each satellite (PLL + DLL) -> 50 bps bits
    results       where each stage writes its files: one folder per recording
    provenance    which recording and settings produced a stage's result files
    synthetic     recordings made up from known satellites, to test against

    ca_code       the C/A Gold codes
    iq_loader     reading raw IQ recordings (whole windows, or block by block)
    signal_view   spectra, waterfall and ADC statistics of a recording
    hackrf        driving a HackRF One (hackrf_transfer), captures, bias-tee check
    constants     the GPS L1 C/A numbers every module shares

Install it once into the project's environment (pip install -e .); the
numbered folders (02_acquisition/, 03_tracking/, ...) keep the command-line
scripts and notes, and call into this package.
"""

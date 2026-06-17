"""Shared, reusable helpers for the educational GPS pipeline.

Modules here are imported by the numbered pipeline stages (01_data_acquisition,
02_acquisition, ...). They hold the pieces that more than one stage needs:

- ca_code   : the GPS L1 C/A Gold-code (PRN) generator
- iq_loader : load raw IQ recordings (HackRF int8 or gr_complex) into numpy
"""

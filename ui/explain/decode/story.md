The 50 bit/s stream is organised in **subframes of 300 bits** (6 seconds), each
starting with the same 8-bit **preamble**. Decoding finds those frames and reads what
each satellite says about itself: its precise **orbit (ephemeris)**, its **clock
corrections** and the **time** each subframe was sent.

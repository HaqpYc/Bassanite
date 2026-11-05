Knowing **when** each signal left its satellite and **where** that satellite was, we
turn travel times into distances (**pseudoranges**). There are four unknowns, your
latitude, longitude and altitude plus the error of the receiver's own clock, so we
need at least **four satellites**. A least-squares solver finds the point that best
fits all the distances.

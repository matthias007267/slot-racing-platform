"""Camera timing.

Detection turns a grayscale frame into lane crossings. A capture thread can
read a camera into a bounded queue; the timing provider translates crossings
into sensor events when the host polls. The device hint and the detection
zones are one global camera configuration, saved apart from any track.
"""

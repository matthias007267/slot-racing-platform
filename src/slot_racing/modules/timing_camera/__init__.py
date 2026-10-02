"""Camera timing.

Detection turns a grayscale frame into lane crossings. A capture thread can
read a camera into a bounded queue; the timing provider translates crossings
into sensor events when the host polls.
"""

"""Camera timing.

Detection turns a grayscale frame into lane crossings. A capture thread can
read a camera into a bounded queue; the timing provider translates crossings
into sensor events when the host polls. The device hint and the detection
zones are one global camera configuration, saved apart from any track.
Detection turns a grayscale frame into lane crossings. The timing provider
translates those crossings into sensor events. The package does not open a camera.
"""

"""Device-neutral sensor timing.

The module turns captured sensor frames into ``SensorTriggered`` events. USB,
a Raspberry Pi or another reader is a transport behind this module. None of
those devices is implemented here. The text frame ``PSF/1`` is provisional and
is not the final wire protocol.
"""

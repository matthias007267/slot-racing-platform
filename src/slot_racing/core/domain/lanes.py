"""How many lanes a track may have.

New tracks use :data:`DEFAULT_LANE_COUNT`. The choices offered for a new or edited
configuration are :data:`MIN_LANE_COUNT` through :data:`MAX_LANE_COUNT`. A lane count
already stored on a track is left as it is until someone saves a different choice.
"""

DEFAULT_LANE_COUNT = 2
MIN_LANE_COUNT = 2
MAX_LANE_COUNT = 4

"""Cold Snap character snow: the game's timed accumulation, as a function of exposure time.

Game system (static data, 2.3.0): the Cold Snap map condition (BP_ColdSnap, config DA_ColdSnap_Default,
class ColdSnapDataAsset) puts a ColdSnapCharacterComponent on each character. Outdoors the component
raises the character's Snow value (CustomPrimitiveData 1) to full over CoveredInSnowDuration; indoors
(spatial definition and override volumes) the snow melts over SnowMeltDuration and the character warms
up over WarmupDuration. The material turns the Snow value into coverage (see materials/user_controls.py).

Values read from DA_ColdSnap_Default: CoveredInSnowDuration 45 s, SnowMeltDuration 30 s, WarmupDuration 30 s.
SnowWetnessReductionPoint and CoolDuration keep their script class defaults, which are not in the game
data; the wetness curve below is therefore an assumption: melted snow becomes wetness, and the wetness
dries over WarmupDuration once the snow is gone.
"""

COVERED_IN_SNOW_DURATION = 45.0
SNOW_MELT_DURATION = 30.0
WARMUP_DURATION = 30.0


def _sat(x):
    return min(max(float(x), 0.0), 1.0)


def state(outdoors_s, sheltered_s=0.0):
    """(snow, wetness) in 0..1 after *outdoors_s* seconds in the cold snap, then *sheltered_s* seconds indoors."""
    peak = _sat(outdoors_s / COVERED_IN_SNOW_DURATION)
    melt = _sat(sheltered_s / SNOW_MELT_DURATION)
    dry = _sat((sheltered_s - SNOW_MELT_DURATION) / WARMUP_DURATION)
    return peak * (1.0 - melt), peak * melt * (1.0 - dry)

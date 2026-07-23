"""Tunable constants: coordinate transform + detection thresholds.

CALIBRATE `game_to_pixel` before trusting any minimap output. The safest way
(devilesk method): dump one real STRATZ response, find two landmarks whose game
(x, y) you know AND whose minimap pixel you want (e.g. the two fountains), then
solve the linear scale/offset per axis. Placeholder values below are a starting
guess, NOT verified — STRATZ position coords have historically lived in a small
grid space (~64-192), so print a few real events first.
"""

# --- Minimap render size (pixels) ---
MAP_PX = 500

# --- Coordinate transform (CALIBRATED from MangoByte place_icon_on_map) ---
# The classic grid was 64..192; the 7.33 map expansion added 10 grid units on
# every side, and assets/dota_map.png covers that expanded area: 54..202.
GAME_MIN = 54.0
GAME_MAX = 202.0


def game_to_pixel(x: float, y: float) -> tuple[float, float]:
    """Game grid coord -> minimap pixel. Y is flipped (screen y grows downward)."""
    span = GAME_MAX - GAME_MIN
    px = (x - GAME_MIN) / span * MAP_PX
    py = (1.0 - (y - GAME_MIN) / span) * MAP_PX  # flip so Radiant is bottom-left
    return px, py


# --- The river diagonal separates the two halves. In grid space the anti-diagonal
#     x + y = GAME_MIN + GAME_MAX runs from top-left to bottom-right. ---
_DIAG_SUM = GAME_MIN + GAME_MAX


def on_enemy_half(x: float, y: float, is_radiant: bool) -> bool:
    """True if this point is on the OPPONENT's side of the river."""
    if is_radiant:                 # Radiant lives at low x+y (bottom-left)
        return (x + y) > _DIAG_SUM
    return (x + y) < _DIAG_SUM     # Dire lives at high x+y


# --- Distances are in the SAME units as the incoming coords. If STRATZ turns out
#     to give world units (~17664 wide) instead of grid units, scale these up. ---
NEAR_ENEMY      = 12.0   # "collapsed on me" radius
ISOLATED_ALLY   = 20.0   # nearest ally beyond this = you were alone
WARD_COVER      = 13.0   # allied ward within this = death spot was lit
FAR_LASTSEEN    = 32.0   # enemy last-known this far away = you had no recent info
ROTATE_LOOKBACK = 8.0    # seconds: how far back "no enemies visible" looks
TOWER_RANGE     = 9.0    # died within this of an enemy tower = a dive
TOWER_VISION    = 14.8   # ~1900 world units: your towers light this radius
DISPLACE_WINDOW  = 6.0   # seconds before death a hook/skewer can still explain it
DISPLACE_MIN_LEAD = 0.5  # must land at least this long before the killing blow
DISPLACE_MIN_MOVE = 3.0  # grid units (~380) you must actually be moved
DISPLACE_MIN_CLOSE = 2.0 # grid units (~250) the gap to the caster must close

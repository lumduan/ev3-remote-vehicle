"""HOST CODE. The arithmetic between held keys and two motor duties.

Pure functions, no I/O and no state, so the part of the vehicle that is
easiest to get subtly wrong is also the part that can be read in one
sitting and checked without a brick attached.

Everything here works in two stages. The mixing is done in normalised
units, -1.0 to 1.0, where the geometry is obvious. Only at the very end
is a percentage applied. Keeping the scale out of the mixing is what
makes `--speed` a single honest knob rather than something that
interacts with the steering.
"""

MOVEMENT_KEYS = ("w", "a", "s", "d")


def axes(held):
    """Throttle and turn, each -1.0 to 1.0, from the held key set.

    Opposing keys cancel: w and s together is zero throttle, a and d
    together is zero turn. That is deliberate rather than an accident of
    the arithmetic. Someone mashing both should come to a stop, not have
    the program pick a winner on their behalf.
    """
    throttle = (1.0 if "w" in held else 0.0) - (1.0 if "s" in held else 0.0)
    turn = (1.0 if "d" in held else 0.0) - (1.0 if "a" in held else 0.0)
    return throttle, turn


def tank(throttle, turn):
    """Mix to two sides, preserving the ratio between them.

    `left = throttle + turn` reaches 2.0 when both are at full. Clamping
    each side independently at that point would quietly change the
    turn-to-throttle ratio, and full-forward-plus-full-left would come
    out as straight ahead. Dividing both by the larger magnitude keeps
    the ratio and gives up only absolute speed, which is the one of the
    two that the driver can see and correct for.

    A consequence worth knowing: at full deflection, w and a together
    give left 0.0 and right 1.0. That is a pivot about the stopped
    wheel, not a gentle arc. An arc needs the turn axis scaled below 1.0
    before it gets here.
    """
    left = throttle + turn
    right = throttle - turn
    largest = max(abs(left), abs(right))
    if largest > 1.0:
        left /= largest
        right /= largest
    return left, right


def slew(current, target, limit):
    """Step `current` toward `target` by at most `limit`.

    Keyboard input is a step function: a key is either held or it is
    not, so without this every key change would hand the motors a
    full-scale step. The limit is per loop iteration, so the time to
    reach full is (speed / limit) loops.
    """
    delta = target - current
    if delta > limit:
        return current + limit
    if delta < -limit:
        return current - limit
    return target


def duties(held, speed):
    """The whole chain: held keys to a target duty per side, in percent.

    Scale is applied last, once, to both sides equally.
    """
    throttle, turn = axes(held)
    left, right = tank(throttle, turn)
    return left * speed, right * speed

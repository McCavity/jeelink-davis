"""Third-party code kept in-tree, with the patches it needs to run here.

Vendored rather than pip-installed because the upstream package is not on PyPI
and the two changes below are required on Debian 13 (trixie) — a `pip install`
of the GitHub source would put the *unpatched* file on the Pi and the reader
would fail at import.

``DFRobot_AS3935_Lib.py`` — DFRobot, MIT, v1.0.2 (2021-09-28),
https://github.com/DFRobot/DFRobot_AS3935. Taken from the calibration bench on
2026-08-06 with exactly three changes, each marked at the line it affects:

  * ``import smbus`` → ``import smbus2 as smbus`` — python3-smbus no longer
    exists on trixie; smbus2 is API-compatible for what this file uses.
  * ``read_i2c_block_data(addr, reg)`` → the same call with an explicit length
    of 1. The old smbus defaulted to 32; smbus2 requires the argument, and the
    AS3935 has nine registers, not 32.
  * ``get_interrupt_src``: ``time.sleep(0.03)`` → ``0.005``. See below — this
    is the only one of the three that changes behaviour rather than making the
    file run at all.

Nothing else is modified, so a diff against upstream stays readable.

WHY THE INTERRUPT SLEEP WAS SHORTENED
-------------------------------------
Upstream sleeps 30 ms while the comment on the same line says "wait 3ms" and
the datasheet (p. 22) asks for a minimum of 2 ms. A tenfold gap between a
comment and the code it annotates is a typo, not a decision.

It is not cosmetic, because that sleep sets the whole IRQ handler's period, and
the handler's period turned out to be what the event counter measures. Two
single-stimulus measurements on 2026-08-06, one piezo click at 3–5 cm each:

    sleep    handler period      events     burst duration
    30 ms    36.3 ms (median)    28         972 ms
     5 ms    12.2 ms (median)    11         249 ms

The spacing follows the handler in both runs, to the millisecond — so the count
is paced by this loop and not by the sensor. What it does *not* do is scale the
way a fixed-duration signal sampled faster would: the faster handler produced
*fewer* events over a *shorter* burst, which falsifies the obvious model.

The hypothesis that fits, and which is NOT yet verified: the handler's own I²C
reads keep the disturbance alive. This sensor is documented to fire on I²C
traffic on its own bus — that is why the neighbouring BME280's 60-second poll
was once logged as lightning — and a slower handler spreads its own bus traffic
over a longer window, feeding a detector that is already excited.

Until that is measured, **do not build anything on the event count**. How many
stored events constitute one physical detection is an open question; see
"Lightning sensor" in CLAUDE.md.
"""

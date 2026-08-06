"""Third-party code kept in-tree, with the patches it needs to run here.

Vendored rather than pip-installed because the upstream package is not on PyPI
and the two changes below are required on Debian 13 (trixie) — a `pip install`
of the GitHub source would put the *unpatched* file on the Pi and the reader
would fail at import.

``DFRobot_AS3935_Lib.py`` — DFRobot, MIT, v1.0.2 (2021-09-28),
https://github.com/DFRobot/DFRobot_AS3935. Taken from the calibration bench on
2026-08-06 with exactly two changes, both marked at the line they affect:

  * ``import smbus`` → ``import smbus2 as smbus`` — python3-smbus no longer
    exists on trixie; smbus2 is API-compatible for what this file uses.
  * ``read_i2c_block_data(addr, reg)`` → the same call with an explicit length
    of 1. The old smbus defaulted to 32; smbus2 requires the argument, and the
    AS3935 has nine registers, not 32.

Nothing else is modified, so a diff against upstream stays readable.
"""

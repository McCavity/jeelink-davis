# AS3935 lightning sensor — integration bootstrap

> Written 2026-08-06, immediately after the sensor was mounted and calibrated.
> Self-contained: a session can start from this file alone.
> Prompt for that session: *"Read `docs/plans/2026-08-06-as3935-integration.md` and implement it."*

## Why this file exists

The sensor is mounted, wired and calibrated. **The calibration cost most of a day**, and almost all of it went into finding out how the thing lies to you. A session that starts from scratch will not know the values below, will not know the traps, and will re-derive them — or worse, silently undo them.

Everything marked **FROZEN** was measured. Do not change it without measuring again.

---

## 1. Required reading before touching code

| File | Why |
|---|---|
| `web/mqtt_publisher.py` | `SOURCE_FIELDS` already reserves `davis/lightning/…`. The topic contract exists. |
| `web/plausibility.py` | Range checks live here. Lightning gets its own ranges (see §6). |
| `web/bme280_reader.py` | The pattern to copy: a daemon thread with a testable `_handle_sample()`. |
| `web/influxdb_writer.py` | `push(payload, measurement)` — the lightning measurement goes next to `indoor`/`outdoor`. |
| `~/as3935-test/` on the weather host | Working scripts from the calibration: `probe.py`, `tune.py`, `watch.py`, `diag.py`, plus the CSV logs of every measurement. **`watch.py` is essentially the reader** — the integration is largely a matter of moving it in. |

## 2. Hardware, as built — FROZEN

```
AS3935   I²C address 0x03   (DIP A0=1 A1=1, factory default)
IRQ      physical pin 7  ==  GPIO4
VCC      physical pin 1  ==  3.3 V     <- NOT 5 V, the IRQ swings to VCC level
Bus      shared with BME280 0x76 (old) and 0x77 (new)
```

Wiring runs Pi → 30 cm → I²C hub → 50 cm → sensor. The IRQ has its own lead straight to pin 7; **it does not go through the hub** (the Gravity connector carries only VCC/GND/SDA/SCL).

## 3. Calibration values — FROZEN

```
Antenna tuning capacitance   96 pF
Watchdog threshold (WDTH)     6
Spike rejection (SREJ)        2   (unchanged default)
Noise floor (NF)              2   (unchanged default)
Mode                          indoor
```

**96 pF** was measured with an oscilloscope on the IRQ pin in LCO mode. Target is 500 kHz / 16 = 31 250 Hz ± 3.5 %. Measured: 88 pF → 31.38 kHz, **96 pF → 31.29 kHz**, 104 pF → 31.19 kHz. The minimum is bracketed on both sides, not merely "within tolerance".

⚠️ `manual_cal(96, …)` in the vendor example **does not calibrate**. It writes the number it is given into register `0x08` and measures nothing. The 96 pF happen to be right for this unit; on another board they would be a guess.

**WDTH = 6** is the smallest value that rejects the local interferer while still passing a real stimulus. Measured with 3 bursts per step and a control at WDTH=2 between every step:

| WDTH | interferer detected | control after |
|---|---|---|
| 2 | 3/3 | — |
| 4 | 3/3 | 3/3 ✓ |
| **6** | **0/3** | 3/3 ✓ |
| 8 … 15 | 0/3 | 3/3 ✓ |

Fifteen control bursts, fifteen hits — so the zeros are real zeros and not a dead listener.

## 4. What the sensor actually reacts to — FROZEN

**I²C traffic on its own bus triggers it.** Five bursts of 40 reads to the neighbouring BME280 — no network traffic at all — produced five events, to the second. The weather service's 60-second BME280 poll was reported as `lightning` at 8 km and 12 km before WDTH was raised.

At WDTH=6 this is gone: 45 minutes in an empty room produced **4 events, all `unknown`, zero disturber, zero lightning**, with all 45 BME280 polls suppressed. That is the reference baseline for any later "is it noisier now?" question.

## 5. Traps — every one of these cost time

1. **`reset()` wipes the calibration.** WDTH returns to 2. The reader must therefore either not call `reset()`, or set cap/WDTH/SREJ *after* it — and read them back. A test measurement was invalidated exactly this way: the value was set, verified, and then the listener reset it in the background.
2. **The settings are volatile.** A reboot loses them. They must be applied at reader start from config, not assumed.
3. **The vendor library does not run as shipped** on Debian 13:
   - `import smbus` → `smbus2` (the old package is gone)
   - `read_i2c_block_data(addr, reg)` needs an explicit length; the old `smbus` defaulted to 32
4. **`RPi.GPIO` is the `rpi-lgpio` shim** here. Do not install the real `RPi.GPIO` alongside it.
5. **Rising-edge detection loses events** while the callback runs. Keep the handler short; do heavy work elsewhere.
6. **A quiet log is indistinguishable from a broken IRQ wire.** Never read "no events" as "no lightning" without having proved the chain fires. See §7.
7. **The piezo test stimulus only works at 3–5 cm.** At 10–20 cm it produces nothing even at maximum sensitivity. A "failed" test at the wrong distance measures the tester, not the sensor.
8. **Classification is unreliable for interference.** The same stimulus arrived as `disturber` and as `lightning` in the same run. Do not build logic that trusts the class of a single event.

## 6. What to build

**Reader** — `web/lightning_reader.py`, modelled on `bme280_reader.py`:
- config section `[lightning]` with `address`, `capacitance`, `watchdog`, `spike_rejection`, `noise_floor`, `indoor`, `irq_pin`
- applies the settings at start **and reads them back**, refusing to run on mismatch
- IRQ-driven, short handler, work queued out of the callback
- exposes a testable `_handle_event()` like `_handle_sample()`

**Storage** — new SQLite table (`lightning_events`: timestamp, kind, distance_km, energy), `influxdb_writer.push(payload, "lightning")`, `mqtt_publisher.push(payload, "lightning")`. The MQTT source is already reserved.

**Plausibility** — `distance_km` 1…40 (AS3935 datasheet), `energy` unbounded (no documented scale — leave it unchecked rather than invent a range, as done for `rssi` in `plausibility.py`).

**API + display** — a `/api/lightning` endpoint and a tile: last event, distance, count today. Header warning and beacon are **out of scope**, see §8.

## 7. Acceptance criteria

- [ ] Settings survive a **service restart** — read back from the sensor, not from config
- [ ] Settings survive a **host reboot** — same check, after a real reboot
- [ ] A piezo spark at 3–5 cm reaches the database, the API and MQTT
- [ ] The 60-second BME280 poll does **not** produce events (WDTH still doing its job)
- [ ] A test asserts an implausible distance is rejected, and a plausible one is stored
- [ ] The chain has been proved to fire **before** any quiet period is reported as "no lightning"

## 8. Explicitly out of scope, and why

**It is unproven that this sensor detects real lightning.** Everything it has ever reacted to is interference or a spark a few centimetres away. Distance and energy readings have never been checked against a real strike.

Therefore: **no header warning on the public site, no beacon, no threshold logic** until at least one real thunderstorm has been recorded and compared against an independent source. Building an alarm on an unvalidated sensor produces exactly the artefact that is worst — one that looks authoritative. The false "lightning at 8 km" events came with a distance, and on a dashboard they would have looked like a storm approaching.

The data-collection layer is what makes that decision possible later. Build that, and wait for weather.

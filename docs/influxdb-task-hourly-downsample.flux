// InfluxDB v2 Task — stündliches Downsampling der Davis-Wetterdaten
//
// Bucket-Voraussetzungen:
//   "weather"        — Rohdaten (Quelle, geschrieben vom jeelink-davis Service)
//   "weather_hourly" — Aggregate (Ziel, Retention: forever)
//
// Setup: InfluxDB UI → Tasks → Create Task → New Task → Script Editor
// Name, every (1h) und offset (5m) in den UI-Feldern eintragen, dann diesen
// Code einfügen und speichern. task.every wird bewusst nicht referenziert
// (Kompatibilitätsproblem mit älteren InfluxDB-Versionen).

option task = {
  name:   "davis_hourly_downsample",
  every:  1h,
  offset: 5m,
}

// ── Außen: Mittelwerte ────────────────────────────────────────────────────────
// temperature, humidity, wind_speed, wind_direction, solar_radiation,
// uv_index, rssi, pressure werden als Mittelwert der Stunde geschrieben.
// Die Feldnamen bleiben gleich → Grafana-Queries funktionieren für beide Buckets.

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "outdoor")
  |> filter(fn: (r) =>
      r._field == "temperature"     or
      r._field == "humidity"        or
      r._field == "wind_speed"      or
      r._field == "wind_direction"  or
      r._field == "solar_radiation" or
      r._field == "uv_index"        or
      r._field == "rssi"            or
      r._field == "pressure"
  )
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> to(bucket: "weather_hourly")

// ── Außen: Maximalwerte ───────────────────────────────────────────────────────
// wind_gust: maximale Böe der Stunde

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "outdoor" and r._field == "wind_gust")
  |> aggregateWindow(every: 1h, fn: max, createEmpty: false)
  |> to(bucket: "weather_hourly")

// ── Außen: Temperatur Min/Max ─────────────────────────────────────────────────
// Als eigene Felder temperature_min / temperature_max gespeichert,
// damit in Grafana Tagesgang-Balken einfach darstellbar sind.

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "outdoor" and r._field == "temperature")
  |> aggregateWindow(every: 1h, fn: min, createEmpty: false)
  |> set(key: "_field", value: "temperature_min")
  |> to(bucket: "weather_hourly")

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "outdoor" and r._field == "temperature")
  |> aggregateWindow(every: 1h, fn: max, createEmpty: false)
  |> set(key: "_field", value: "temperature_max")
  |> to(bucket: "weather_hourly")

// ── Außen: Regen ──────────────────────────────────────────────────────────────
// rain_tip_count ist ein kumulativer 7-Bit-Zähler (0–127, Wrap bei 127→0).
// Wir speichern den letzten Wert der Stunde — die Differenz (= mm) kann
// Grafana via difference() berechnen, oder du nutzt die SQLite-API-Endpunkte
// für exakte Tages-/Monats-Summen (die den Wrap korrekt behandeln).

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "outdoor" and r._field == "rain_tip_count")
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false)
  |> to(bucket: "weather_hourly")

// ── Innen (BME280): Mittelwerte ───────────────────────────────────────────────

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "indoor")
  |> filter(fn: (r) =>
      r._field == "temperature" or
      r._field == "humidity"    or
      r._field == "pressure"
  )
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> to(bucket: "weather_hourly")

// ── Innen: Luftdruck Min/Max ──────────────────────────────────────────────────

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "indoor" and r._field == "pressure")
  |> aggregateWindow(every: 1h, fn: min, createEmpty: false)
  |> set(key: "_field", value: "pressure_min")
  |> to(bucket: "weather_hourly")

from(bucket: "weather")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "indoor" and r._field == "pressure")
  |> aggregateWindow(every: 1h, fn: max, createEmpty: false)
  |> set(key: "_field", value: "pressure_max")
  |> to(bucket: "weather_hourly")

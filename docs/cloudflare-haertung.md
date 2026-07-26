# Cloudflare-Härtung für `wetter.halfpap.io`

> Stand: 2026-07-26

Die Station läuft auf einem Raspberry Pi 4 mit 2 GB und ist über einen
Cloudflare-Tunnel öffentlich erreichbar. Diese Datei beschreibt die
Konfiguration auf der Cloudflare-Seite. Der Code-Anteil der Härtung ist bereits
umgesetzt (PR #11 und #12) und wird hier nur so weit erklärt, wie es für die
Regeln nötig ist.

## Ausgangslage

Alle `/api/*`-Endpunkte sind ohne Authentifizierung aus dem Internet erreichbar
— nachgeprüft am 26.07.: `GET https://wetter.halfpap.io/api/latest` liefert
HTTP 200. Das ist so gewollt, denn das Dashboard ruft genau diese Endpunkte aus
dem Browser des Besuchers auf.

**Daraus folgt die wichtigste Randbedingung: `/api/*` darf nicht gesperrt
werden.** Eine Block- oder Access-Regel auf diesem Pfad schaltet die eigene
Seite ab. Es geht um Zwischenspeichern und Begrenzen, nicht um Sperren.

Vor der Härtung kostete ein einziger GET bis zu 50 s Rechenzeit:

| Endpunkt | vorher |
|---|---|
| `/api/stats/daily` | 50,3 s |
| `/api/stats/monthly` | 38,9 s |
| `/api/stats/yearly` | 33,3 s |
| `/api/rain/totals` | 24,2 s |

## Was der Code schon leistet

* **Tages- und Bereichsabfragen** benutzen seit PR #11 indizierbare
  UTC-Bereiche statt `date(timestamp,'localtime')`. Sie sind damit 40- bis
  200-mal schneller und spielen für die Regeln unten keine Rolle mehr.
* **Die Aggregat-Endpunkte** (`/api/stats/*`, `/api/rain/totals`) aggregieren
  die ganze Tabelle und lassen sich **nicht** beschleunigen. Sie liegen hinter
  einem TTL-Cache mit **Single-Flight**: zehn gleichzeitige kalte Anfragen
  kosteten am laufenden Dienst 31,5 s statt 315 s.
* Seit PR #12 senden genau diese vier Endpunkte
  `Cache-Control: public, max-age=<rest>`, wobei `<rest>` die **Restlaufzeit**
  des Origin-Eintrags ist, nicht die volle TTL. Nachgeprüft: 300 → 299 → 287
  nach zwölf Sekunden.

Der verbleibende Hebel liegt darin, daß trotzdem **jede** Besucheranfrage den
Pi erreicht. Genau das nimmt die Cache Rule weg.

## 1. Cache Rule

**Caching → Cache Rules → Create rule**, Name `api-aggregate-cache`.

Ausdruck:

```
(http.host eq "wetter.halfpap.io" and (starts_with(http.request.uri.path, "/api/stats/") or http.request.uri.path eq "/api/rain/totals"))
```

Einstellungen:

| Feld | Wert |
|---|---|
| Cache eligibility | **Eligible for cache** |
| Edge TTL | **Use cache-control header if present** (`respect_origin`) |
| Browser TTL | **Respect origin** |

> [!warning] Nicht „Override origin"
> Ein fester Edge-TTL-Wert überschreibt den Header aus PR #12. Der Origin
> schickt bewußt die Restlaufzeit, damit Edge und Origin **im selben Moment**
> ablaufen. Mit einem Override wäre das Höchstalter wieder zwei TTLs statt
> einer — genau der Fehler, den der Countdown vermeidet.

Cloudflares **Cache Lock** sorgt zusätzlich dafür, daß pro Rechenzentrum immer
nur eine Anfrage gleichzeitig zum Origin geht. Das ist dasselbe
Single-Flight-Prinzip wie im Code, eine Ebene höher.

### Tiered Cache empfohlen

**Caching → Tiered Cache → Smart Tiered Cache** einschalten (auch im Free-Plan
verfügbar). Ohne das ist der Cache **pro Rechenzentrum** getrennt, und ein
über viele Standorte verteilter Abruf könnte den Pi trotz Cache Rule
vervielfacht treffen. Tiered Cache bündelt das über einen oberen Knoten.

## 2. Rate Limiting

**Security → WAF → Rate limiting rules**

Was hier geht, hängt stark vom Plan ab:

| | Free | Pro | Business |
|---|---|---|---|
| Regeln | 1 | 2 | 5 |
| Felder im Ausdruck | nur Path, Verified Bot | Host, URI, Path, Full URI, Query | + Method, Source IP, User Agent |
| Zählperiode | nur 10 s | bis 1 min | bis 10 min |
| Sperrdauer | nur 10 s | bis 1 h | bis 1 Tag |
| Gecachte Treffer ausnehmen | nein | nein | ja |
| Zähl-Merkmal | IP | IP | IP, IP mit NAT-Unterstützung |
| Eigene Fehlerantwort | **nein** | ja | ja |

Regel für den Free-Plan:

```
starts_with(http.request.uri.path, "/api/")
```

| Feld | Wert |
|---|---|
| Characteristics | IP (im Free-Plan fest, das Feld ist ausgegraut) |
| When rate exceeds | 30 requests / 10 seconds |
| Action | **Block** |
| Duration | 10 s |

### Warum Block und nicht Managed Challenge

Naheliegend wäre eine Challenge: ein Fehlalarm kostet dann einen Klick statt
den Zugang. Auf diesem Pfad ist das aber falsch.

`/api/*` wird **nicht von einem Menschen aufgerufen**, sondern vom eigenen
Dashboard per `fetch` aus JavaScript. Eine Challenge antwortet mit einer
HTML-Zwischenseite. Code, der JSON erwartet, kann damit nichts anfangen — die
Kacheln blieben leer, ohne verwertbare Fehlermeldung. Ein Statuscode ist für
einen API-Pfad das ehrliche Signal.

### Was im Free-Plan dabei fehlt

Zu einer Block-Aktion läßt sich normalerweise eine **eigene Antwort**
konfigurieren (Typ, Statuscode, Rumpf) — für einen API-Pfad wäre das ein
sauberes `429` mit JSON-Rumpf. Diese Einstellung gibt es laut Cloudflare erst
**ab Pro**; im Free-Plan erscheint der Abschnitt im Dialog gar nicht erst.

Die Standardantwort ist aber besser als erwartet. **Am 26.07. gemessen**, indem
die Regel absichtlich ausgelöst wurde:

```
HTTP/2 429
content-type: text/plain; charset=UTF-8
retry-after: 10

error code: 1015
```

17 Bytes Klartext, kein HTML-Interstitial — und mit `Retry-After`. Für eigenen
Code ist das auswertbar: `response.ok` ist falsch, der Status ist der
standardkonforme `429`, und die Wartezeit steht im Header. Eine eigene
JSON-Antwort wäre schöner, aber nichts geht dabei verloren.

**Die Schwelle trotzdem großzügig lassen** — 30 Anfragen pro 10 Sekunden,
nicht weniger. Der Grund sind die beiden Punkte unten, nicht das Antwortformat.

Zwei weitere Punkte, die man kennen muß:

* Auf Free und Pro zählen **auch gecachte Treffer** mit. Die eigene
  Dashboard-Nutzung schlägt also ebenfalls auf das Zählwerk.
* Gezählt wird **pro IP**. Hinter einem Anschluß-NAT teilen sich alle Geräte
  eines Haushalts ein Zählwerk.

Beides spricht ebenfalls für 30 statt 10.

## 3. cloudflared aktuell halten

Der Tunnel läuft als Container auf `nasapp01` (Synology DS920+, DSM 7.3,
SSH auf Port 2022). `/volume1/docker` ist leer, es gibt also keinen
Bind-Mount — der Tunnel ist mit hoher Wahrscheinlichkeit token-basiert und
seine Ingress-Regeln liegen im Cloudflare-Dashboard, nicht auf dem NAS.

**Zuerst den Ist-Zustand sichern**, sonst ist der Tunnel-Token nach einem
Neuanlegen verloren:

```bash
ssh -p 2022 nasapp01 'sudo docker inspect cloudflared > ~/cloudflared-inspect-$(date +%F).json && echo gesichert'
```

Danach im Container Manager das Image `cloudflare/cloudflared:latest` laden und
den Container darauf neu erzeugen. Bei einem Container-Manager-*Projekt*
(Compose) macht „Projekt → Neu erstellen" Pull und Neustart in einem.

> [!note] Nicht selbst gesehen
> Der DSM-Dialog ist hier ungeprüft beschrieben: `docker` verlangt auf der
> Synology Root, und `sudo` ein Paßwort. Die Beschriftungen in DSM 7.3.2 können
> abweichen — im Zweifel gilt der Bildschirm, nicht dieser Absatz.

## Verifikation

Nach dem Setzen der Regeln von außen prüfen:

```bash
curl -sI https://wetter.halfpap.io/api/stats/yearly | grep -iE 'cache-control|cf-cache-status|age'
```

Erwartung:

* `cache-control: public, max-age=<n>` mit `n` ≤ 300
* `cf-cache-status: MISS` beim ersten Aufruf, danach `HIT`
* `age:` wächst bei aufeinanderfolgenden Aufrufen

Bleibt `cf-cache-status` dauerhaft `DYNAMIC`, greift die Cache Rule nicht —
dann stimmt der Ausdruck nicht oder „Eligible for cache" fehlt.

### Gemessenes Ergebnis (2026-07-26)

Beides von außen nachgeprüft, nachdem die Regeln gesetzt waren:

**Cache Rule** — `/api/stats/yearly`, drei Abrufe hintereinander:

| Abruf | `cf-cache-status` | `age` |
|---|---|---|
| 1 | `MISS` | — |
| 2 | `HIT` | 0 |
| 3 (nach 8 s) | `HIT` | 8 |

Der Origin sieht den Endpunkt damit einmal statt bei jedem Besucher.

**Rate Limiting** — 45 Abrufe auf `/api/latest` in 2,8 Sekunden:

```
30 × 200
15 × 429
```

Die Regel feuert exakt an der eingestellten Schwelle. Nach 15 Sekunden war der
Zugang wieder frei — der Rückweg gehört zur Prüfung, eine Sperre, die man nur
auslöst und nie aufgehen sieht, ist halb geprüft.

> [!warning] Nicht die teuren Endpunkte zum Testen hämmern
> Ein `MISS` kostet den Pi 30–50 s. Für Erreichbarkeitstests `/api/latest`
> nehmen, das ist billig. Die Aggregat-Endpunkte höchstens einzeln und mit
> Abstand prüfen.

## Bewußt nicht gemacht

* **`/api/*` hinter Cloudflare Access legen** — würde das eigene Dashboard
  abschalten (siehe Ausgangslage).
* **Feste Edge-TTL** — hebelt den Countdown aus PR #12 aus.
* **Origin-Port 8000 aus dem LAN schließen** — der Dienst horcht auf
  `0.0.0.0:8000` und ist damit im Heimnetz offen erreichbar. Das ist ein
  eigener Punkt und hier nicht behandelt.

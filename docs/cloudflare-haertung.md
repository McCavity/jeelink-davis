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

Der Tunnel läuft als Container auf `nasapp01` (Synology DS920+, DSM 7.3.2).
Ist-Stand am 2026-07-26 **im Container Manager nachgesehen**:

| | |
|---|---|
| Container | `hennings-netzwerk-cloudflare`, angelegt 01.03.2025 |
| Image | `cloudflare/cloudflared:` **`<none>`** — 57 MB, vom **27.02.2025** |
| Netzwerk | `host` · Auto-Neustart aktiv |
| Entrypoint | `cloudflared --no-autoupdate` |
| Projekte | **keine** — es sind einfache Container, kein Compose |
| Neues Image | `cloudflare/cloudflared:latest`, 60 MB, vom 23.07.2026, noch ungenutzt |

Das laufende Image ist also rund **17 Monate alt**. Daß sein Tag `<none>` ist,
ist kein Defekt: Beim Ziehen von `:latest` wandert das Tag auf das neue Image,
und das alte behält nur seine ID.

> [!caution] Der Tunnel-Token steht im Klartext auf der Detailseite
> Unter *Container → \<name\> → Allgemein* zeigt das Feld **Ausführungsbefehl**
> `tunnel run --token …` mit dem vollständigen Token. Von dieser Seite keine
> Screenshots teilen, sie nicht in Tickets kleben und sie nicht an Werkzeuge
> weitergeben, die Bilder speichern. `Aktion → Exportieren` erzeugt aus
> demselben Grund eine Datei, die wie ein Secret zu behandeln ist.

### Der Weg, der ohne Ausfall auskommt

`Aktion → Zurücksetzen` und `Aktion → Einstellungen` sind **ausgegraut, solange
der Container läuft**. Man müßte also erst stoppen — und damit wäre der Tunnel
und alles dahinter offline, bevor man weiß, ob der neue Container überhaupt
hochkommt. Zudem ist unklar, ob „Zurücksetzen" bei einem Container auf einem
**tag-losen** Image das neue `latest` zieht oder dieselbe ID wiederverwendet.

**`Duplizieren` löst das.** Der Knopf ist auch im laufenden Betrieb aktiv, und
der Dialog zeigt oben ausdrücklich:

```
Image: cloudflare/cloudflared:latest
```

Er löst also über das **Tag** auf, nicht über die alte ID. Vorgeschlagen werden
`hennings-netzwerk-cloudflare-1` als Name, die übernommenen Einstellungen und
„Diesen Container nach Abschluß des Assistenten ausführen".

Ablauf:

1. Im Duplizieren-Dialog **nach unten scrollen** und prüfen, daß Netzwerkmodus
   `host` und der `tunnel run --token …`-Befehl übernommen wurden. (Diesen
   Schritt macht man selbst — er zeigt den Token.)
2. Duplizieren. Cloudflare verträgt zwei Replikate desselben Tunnels, der alte
   Container kann also zunächst weiterlaufen.
3. Prüfen, daß die Seiten weiter antworten und im Cloudflare-Dashboard zwei
   gesunde Verbindungen erscheinen.
4. Erst dann den alten Container stoppen und erneut prüfen.
5. Läuft alles, den alten Container löschen und über *Image → Nicht verwendete
   Images entfernen* das 57-MB-Altimage aufräumen.

Der alte Container ist bis Schritt 5 der Rückweg: Neuen stoppen, alten starten.

Als Sicherung vorweg, falls man den SSH-Weg bevorzugt (verlangt Root, und
`sudo` will auf der Synology ein Paßwort):

```bash
ssh -p 2022 nasapp01 'sudo docker inspect hennings-netzwerk-cloudflare > ~/cloudflared-inspect-$(date +%F).json'
```

Die Datei enthält den Token — entsprechend behandeln.

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

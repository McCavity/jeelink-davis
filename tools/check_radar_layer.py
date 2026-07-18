#!/usr/bin/env python3
"""Prüft, ob der im Dashboard verdrahtete DWD-Radar-Layer noch existiert.

Hintergrund: Das Regenradar hing monatelang leer im Dashboard, weil der DWD den
Layer ``dwd:RX-Produkt`` abgeschaltet hat. Der Ausfall war lautlos — die Kachel
kam einfach nicht, und niemand merkt das, solange es nicht regnet.

Dieses Skript liest den Layer-Namen aus ``web/static/index.html`` (also genau
den, der wirklich ausgeliefert wird — keine zweite Wahrheit) und prüft ihn gegen
den echten WMS:

1. Steht der Layer in den GetCapabilities?
2. Liefert ein GetMap für einen Zeitstempel, wie ihn das Dashboard baut,
   tatsächlich ein PNG?

Aufruf (keine Abhängigkeiten außer der Standardbibliothek)::

    python3 tools/check_radar_layer.py

Exit-Code 0 = in Ordnung, 1 = der Layer trägt nicht mehr.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

WMS = "https://maps.dwd.de/geoserver/dwd/wms"
INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"

# Der DWD-WMS sitzt hinter einer WAF, die Requests ohne Browser-Kennung mit 403
# abweist. Ohne diesen Header misst man die WAF statt des Layers.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def fetch(url: str, timeout: int = 60) -> tuple[int, str, bytes]:
    """GET, das HTTP-Fehler als Wert zurückgibt statt zu werfen."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.headers.get("Content-Type", ""), res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Content-Type", ""), err.read()


def layer_from_dashboard() -> str:
    """Zieht den Radar-Layer aus dem ausgelieferten HTML."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r"layers:\s*'([^']+)'", html)
    if not match:
        sys.exit(f"FEHLER: kein 'layers:'-Eintrag in {INDEX_HTML} gefunden")
    return match.group(1)


def dashboard_timestamp() -> str:
    """Baut denselben Zeitstempel wie radarBuildFrames() im Dashboard."""
    now = datetime.now(timezone.utc)
    base = now - timedelta(minutes=10)
    base = base.replace(minute=base.minute - base.minute % 5, second=0, microsecond=0)
    return base.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    layer = layer_from_dashboard()
    stamp = dashboard_timestamp()
    print(f"Layer laut Dashboard : {layer}")
    print(f"Prüf-Zeitstempel     : {stamp}")

    status, _, body = fetch(f"{WMS}?service=WMS&version=1.3.0&request=GetCapabilities")
    if status != 200:
        print(f"WARNUNG: GetCapabilities nicht erreichbar (HTTP {status}) — Prüfung unvollständig")
        return 1
    bare = layer.split(":", 1)[-1]
    in_caps = f"<Name>{bare}</Name>" in body.decode("utf-8", "replace")
    print(f"In GetCapabilities   : {'ja' if in_caps else 'NEIN'}")

    getmap = (
        f"{WMS}?service=WMS&version=1.1.1&request=GetMap&layers={layer}"
        "&format=image/png&transparent=true&srs=EPSG:3857"
        "&bbox=626172,6261722,1252344,6887895&width=256&height=256"
        f"&time={stamp}"
    )
    status, ctype, body = fetch(getmap)
    is_png = body[:8] == b"\x89PNG\r\n\x1a\n"
    print(f"GetMap               : HTTP {status}, {ctype}, {len(body)} Bytes, PNG={is_png}")

    if in_caps and status == 200 and is_png:
        print("\n=> OK — der Radar-Layer trägt.")
        return 0

    print(
        "\n=> FEHLER — das Regenradar bleibt leer.\n"
        "   Aktuell verfügbare Radar-Layer beim DWD abfragen mit:\n"
        f"   curl -s -A '<browser-ua>' '{WMS}?service=WMS&version=1.3.0&request=GetCapabilities'"
        " | grep -o '<Name>[^<]*adar[^<]*</Name>'"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

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


def const_from_dashboard(name: str) -> int:
    """Liest eine RADAR_*-Konstante aus dem ausgelieferten HTML."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(rf"const\s+{name}\s*=\s*(\d+)", html)
    if not match:
        sys.exit(f"FEHLER: Konstante {name} nicht in {INDEX_HTML} gefunden")
    return int(match.group(1))


def dashboard_base() -> datetime:
    """Baut denselben Anker wie radarBuildFrames() im Dashboard."""
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    return base.replace(minute=base.minute - base.minute % 5, second=0, microsecond=0)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def time_extent_end(caps: str, layer: str) -> datetime | None:
    """Liest das Ende der time-Dimension des Layers aus den GetCapabilities.

    Der Horizont muss aus den Capabilities kommen, nicht aus dem Bildinhalt:
    Jenseits des Horizonts liefert der DWD ein leeres PNG mit HTTP 200 — und
    das ist von "es regnet gerade nirgends" per Pixel nicht zu unterscheiden.
    """
    import xml.etree.ElementTree as ET

    wms = "{http://www.opengis.net/wms}"
    bare = layer.split(":", 1)[-1]
    for node in ET.fromstring(caps).iter(f"{wms}Layer"):
        name = node.find(f"{wms}Name")
        if name is None or name.text != bare:
            continue
        for dim in node.findall(f"{wms}Dimension"):
            if dim.get("name") != "time" or not (dim.text or "").strip():
                continue
            end = (dim.text or "").strip().split("/")[1]
            return datetime.fromisoformat(end.replace("Z", "+00:00"))
    return None


def main() -> int:
    layer = layer_from_dashboard()
    base = dashboard_base()
    horizon = base + timedelta(minutes=5 * const_from_dashboard("RADAR_FUTURE"))
    print(f"Layer laut Dashboard : {layer}")
    print(f"Jetzt-Frame          : {stamp(base)}")
    print(f"Fernster Frame       : {stamp(horizon)}")

    status, _, body = fetch(f"{WMS}?service=WMS&version=1.3.0&request=GetCapabilities")
    if status != 200:
        print(f"WARNUNG: GetCapabilities nicht erreichbar (HTTP {status}) — Prüfung unvollständig")
        return 1
    caps = body.decode("utf-8", "replace")
    bare = layer.split(":", 1)[-1]
    in_caps = f"<Name>{bare}</Name>" in caps
    print(f"In GetCapabilities   : {'ja' if in_caps else 'NEIN'}")

    end = time_extent_end(caps, layer) if in_caps else None
    reaches = end is not None and end >= horizon
    if end is None:
        print("DWD-Horizont         : keine time-Dimension gefunden")
    else:
        print(f"DWD-Horizont         : bis {stamp(end)} — "
              f"{'deckt den fernsten Frame' if reaches else 'ZU KURZ für den fernsten Frame'}")

    getmap = (
        f"{WMS}?service=WMS&version=1.1.1&request=GetMap&layers={layer}"
        "&format=image/png&transparent=true&srs=EPSG:3857"
        "&bbox=626172,6261722,1252344,6887895&width=256&height=256"
        f"&time={stamp(base)}"
    )
    status, ctype, body = fetch(getmap)
    is_png = body[:8] == b"\x89PNG\r\n\x1a\n"
    print(f"GetMap (Jetzt-Frame) : HTTP {status}, {ctype}, {len(body)} Bytes, PNG={is_png}")

    if in_caps and reaches and status == 200 and is_png:
        print("\n=> OK — Layer und Vorhersage-Horizont tragen.")
        return 0

    print(
        "\n=> FEHLER — das Regenradar bleibt leer oder die Vorhersage reicht nicht.\n"
        "   Aktuell verfügbare Radar-Layer beim DWD abfragen mit:\n"
        f"   curl -s -A '<browser-ua>' '{WMS}?service=WMS&version=1.3.0&request=GetCapabilities'"
        " | grep -o '<Name>[^<]*adar[^<]*</Name>'"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Tests for the TTL + single-flight cache in front of the whole-table aggregates,
and for the Cache-Control header derived from it.

The cache exists for one reason: /api/stats/* cannot be made fast (they
aggregate the entire table by design, 33-50 s measured) and they are publicly
reachable. So the property that matters is not "a second call is fast" — it is
that N *simultaneous* callers still cause only one run. A cache without that
guarantee is defeated by simply requesting in parallel.
"""

import asyncio
import time

import pytest

from web.app import _agg_cache, _agg_locks, _cacheable, _cached_aggregate


@pytest.fixture(autouse=True)
def leerer_cache():
    _agg_cache.clear()
    _agg_locks.clear()
    yield
    _agg_cache.clear()
    _agg_locks.clear()


async def test_zweiter_aufruf_rechnet_nicht_neu():
    laeufe = []

    def produce():
        laeufe.append(1)
        return {"wert": 42}

    a, _ = await _cached_aggregate("k", 300.0, produce)
    b, _ = await _cached_aggregate("k", 300.0, produce)
    assert a == b == {"wert": 42}
    assert len(laeufe) == 1


async def test_gleichzeitige_aufrufe_rechnen_nur_einmal():
    """Der eigentliche Schutz: zwanzig Anfragen auf einen kalten Schluessel
    duerfen die teure Abfrage genau einmal ausloesen, nicht zwanzigmal."""
    laeufe = []

    def produce():
        laeufe.append(1)
        time.sleep(0.25)   # langsam genug, dass die uebrigen am Lock warten
        return {"wert": "teuer"}

    ergebnisse = await asyncio.gather(
        *(_cached_aggregate("kalt", 300.0, produce) for _ in range(20))
    )
    assert all(e[0] == {"wert": "teuer"} for e in ergebnisse)
    assert len(laeufe) == 1, f"{len(laeufe)} Laeufe statt 1 — Single-Flight greift nicht"


async def test_abgelaufener_eintrag_wird_neu_gerechnet():
    laeufe = []

    def produce():
        laeufe.append(1)
        return len(laeufe)

    assert (await _cached_aggregate("k", 0.0, produce))[0] == 1
    assert (await _cached_aggregate("k", 0.0, produce))[0] == 2
    assert len(laeufe) == 2


async def test_schluessel_sind_getrennt():
    def mach(v):
        return lambda: v

    assert (await _cached_aggregate("a", 300.0, mach("A")))[0] == "A"
    assert (await _cached_aggregate("b", 300.0, mach("B")))[0] == "B"
    assert (await _cached_aggregate("a", 300.0, mach("X")))[0] == "A"


async def test_fehler_wird_nicht_zwischengespeichert():
    """Ein Fehlschlag darf nicht fuer die ganze TTL festgeschrieben werden —
    sonst macht ein einzelner Ausrutscher den Endpunkt fuer 5 Minuten kaputt."""
    zustand = {"n": 0}

    def produce():
        zustand["n"] += 1
        if zustand["n"] == 1:
            raise RuntimeError("erster Versuch scheitert")
        return "gut"

    with pytest.raises(RuntimeError):
        await _cached_aggregate("k", 300.0, produce)
    assert (await _cached_aggregate("k", 300.0, produce))[0] == "gut"


class TestMaxAge:
    async def test_erster_aufruf_meldet_die_volle_ttl(self):
        _, max_age = await _cached_aggregate("k", 300.0, lambda: "x")
        assert max_age == 300

    async def test_max_age_zaehlt_herunter_statt_sich_zu_erneuern(self):
        """Der Punkt der ganzen Uebung: ein Treffer darf NICHT wieder die volle
        TTL melden. Sonst haelt ein nachgelagerter Cache den Wert bis zu eine
        weitere TTL lang, und das Gesamtalter waere doppelt so hoch wie gedacht."""
        _, zuerst = await _cached_aggregate("k", 3.0, lambda: "x")
        await asyncio.sleep(1.2)
        _, danach = await _cached_aggregate("k", 3.0, lambda: "x")
        assert zuerst == 3
        assert danach < zuerst, f"max_age blieb bei {danach} — zaehlt nicht herunter"

    async def test_max_age_wird_nie_null(self):
        """max-age=0 waere fuer einen gerade erst gerechneten Wert irrefuehrend
        und wuerde jedes Edge-Caching aushebeln."""
        _, max_age = await _cached_aggregate("k", 0.4, lambda: "x")
        assert max_age >= 1


class TestCacheableResponse:
    def test_setzt_public_und_max_age(self):
        r = _cacheable({"a": 1}, 42)
        assert r.headers["cache-control"] == "public, max-age=42"

    def test_liefert_den_inhalt_unveraendert(self):
        import json
        r = _cacheable([{"period": "2026-07", "temp_avg": 22.1, "leer": None}], 10)
        assert json.loads(bytes(r.body)) == [
            {"period": "2026-07", "temp_avg": 22.1, "leer": None}
        ]

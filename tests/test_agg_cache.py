"""
Tests for the TTL + single-flight cache in front of the whole-table aggregates.

The cache exists for one reason: /api/stats/* cannot be made fast (they
aggregate the entire table by design, 33-50 s measured) and they are publicly
reachable. So the property that matters is not "a second call is fast" — it is
that N *simultaneous* callers still cause only one run. A cache without that
guarantee is defeated by simply requesting in parallel.
"""

import asyncio

import pytest

from web.app import _agg_cache, _agg_locks, _cached_aggregate


@pytest.fixture(autouse=True)
def leerer_cache():
    _agg_cache.clear()
    _agg_locks.clear()
    yield
    _agg_cache.clear()
    _agg_locks.clear()


@pytest.mark.asyncio
async def test_zweiter_aufruf_rechnet_nicht_neu():
    laeufe = []

    def produce():
        laeufe.append(1)
        return {"wert": 42}

    a = await _cached_aggregate("k", 300.0, produce)
    b = await _cached_aggregate("k", 300.0, produce)
    assert a == b == {"wert": 42}
    assert len(laeufe) == 1


@pytest.mark.asyncio
async def test_gleichzeitige_aufrufe_rechnen_nur_einmal():
    """Der eigentliche Schutz: zwanzig Anfragen auf einen kalten Schluessel
    duerfen die teure Abfrage genau einmal ausloesen, nicht zwanzigmal."""
    laeufe = []
    gestartet = asyncio.Event()

    def produce():
        laeufe.append(1)
        # Langsam genug, dass die uebrigen Aufrufer sicher am Lock warten.
        import time as _t
        _t.sleep(0.25)
        return {"wert": "teuer"}

    async def anfrage():
        gestartet.set()
        return await _cached_aggregate("kalt", 300.0, produce)

    ergebnisse = await asyncio.gather(*(anfrage() for _ in range(20)))
    assert all(e == {"wert": "teuer"} for e in ergebnisse)
    assert len(laeufe) == 1, f"{len(laeufe)} Laeufe statt 1 — Single-Flight greift nicht"


@pytest.mark.asyncio
async def test_abgelaufener_eintrag_wird_neu_gerechnet():
    laeufe = []

    def produce():
        laeufe.append(1)
        return len(laeufe)

    assert await _cached_aggregate("k", 0.0, produce) == 1
    assert await _cached_aggregate("k", 0.0, produce) == 2
    assert len(laeufe) == 2


@pytest.mark.asyncio
async def test_schluessel_sind_getrennt():
    def mach(v):
        return lambda: v

    assert await _cached_aggregate("a", 300.0, mach("A")) == "A"
    assert await _cached_aggregate("b", 300.0, mach("B")) == "B"
    assert await _cached_aggregate("a", 300.0, mach("X")) == "A"


@pytest.mark.asyncio
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
    assert await _cached_aggregate("k", 300.0, produce) == "gut"

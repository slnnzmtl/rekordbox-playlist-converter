#!/usr/bin/env python3
"""Optional 5k-assignment timing bench for Manifest V2 (not run in CI).

Example:
  PYTHONPATH=src python3 scripts/manifest_bench.py
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import converter_manifest as cm


def _bench_unique(n: int) -> dict[str, float]:
    with tempfile.TemporaryDirectory() as tmp:
        wav_dir = Path(tmp) / "lib"
        wav_dir.mkdir()
        (wav_dir / "WAV").mkdir()
        manifest = cm.empty_manifest()
        t0 = time.perf_counter()
        ctx = cm.ReservationContext.scanned(wav_dir)
        t_scan = time.perf_counter() - t0
        t1 = time.perf_counter()
        for i in range(n):
            cm.reserve_relative_dest(
                manifest,
                source_key=f"/music/track-{i}.flac",
                output_format="wav",
                preferred=f"WAV/Artist - Track {i}.wav",
                wav_dir=wav_dir,
                reservation=ctx,
            )
        t_reserve = time.perf_counter() - t1
        t2 = time.perf_counter()
        cm.save_manifest(manifest, wav_dir)
        t_save = time.perf_counter() - t2
        t3 = time.perf_counter()
        loaded = cm.load_manifest(wav_dir)
        t_load = time.perf_counter() - t3
        assert len(loaded.tracks) == n
        return {
            "scan_s": t_scan,
            "reserve_s": t_reserve,
            "save_s": t_save,
            "load_s": t_load,
        }


def _bench_collisions(n: int) -> dict[str, float]:
    with tempfile.TemporaryDirectory() as tmp:
        wav_dir = Path(tmp) / "lib"
        wav_dir.mkdir()
        (wav_dir / "WAV").mkdir()
        manifest = cm.empty_manifest()
        ctx = cm.ReservationContext.scanned(wav_dir)
        preferred = "WAV/Same - Song.wav"
        t0 = time.perf_counter()
        for i in range(n):
            cm.reserve_relative_dest(
                manifest,
                source_key=f"/music/track-{i}.flac",
                output_format="wav",
                preferred=preferred,
                wav_dir=wav_dir,
                reservation=ctx,
            )
        return {"collision_reserve_s": time.perf_counter() - t0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", type=int, default=5000, help="assignment count")
    args = parser.parse_args()
    n = args.n
    unique = _bench_unique(n)
    collisions = _bench_collisions(n)
    print(f"assignments={n}")
    for key, value in {**unique, **collisions}.items():
        print(f"{key}={value:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

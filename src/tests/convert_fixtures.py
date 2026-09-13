"""Shared fixtures for convert / CLI playlist tests."""

from __future__ import annotations

import io
import struct
from pathlib import Path


class HangProc:
    def __init__(self, *_a: object, **_k: object) -> None:
        self.returncode: int | None = None
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else -9

    def communicate(self) -> tuple[str, str]:
        return "", ""


# Back-compat alias used by older test modules.
_HangProc = HangProc


def write_pcm_wav(
    path: Path,
    *,
    sample_rate: int = 44100,
    channels: int = 2,
    bits: int = 16,
    frames: int = 8,
) -> None:
    """Minimal stereo WAVE_FORMAT_PCM for skip/rebuild tests."""
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    data = b"\x00" * (frames * block_align)
    fmt_payload = struct.pack(
        "<HHIIHH",
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
    )
    body = b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload
    body += b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="219211420" Name="Bestial" Artist="ABSL" Composer=""
           Album="It's just a bad dream" Grouping="" Genre="Electronic"
           Kind="FLAC File" Size="40888414" TotalTime="328" DiscNumber="0"
           TrackNumber="7" Year="2023" AverageBpm="148.00" DateAdded="2024-06-14"
           BitRate="0" SampleRate="44100" Comments="exystence.net" PlayCount="19"
           Rating="51" Location="{loc_a}" Remixer="" Tonality="Bbm"
           Label="Mama told ya" Mix="" Colour="0xFF0000">
      <TEMPO Inizio="0.027" Bpm="148.00" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="cue" Type="0" Start="0.027" Num="-1" Red="255" Green="0" Blue="0"/>
      <EXTRA Foo="bar"/>
    </TRACK>
    <TRACK TrackID="58834508" Name="Revelation" Artist="Shogan" Composer=""
           Album="Hits" Grouping="" Genre="Trance" Kind="FLAC File" Size="1"
           TotalTime="100" DiscNumber="0" TrackNumber="1" Year="2020"
           AverageBpm="140.00" DateAdded="2024-12-31" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_b}" Remixer="" Tonality="Bm" Label="" Mix="">
      <TEMPO Inizio="0.000" Bpm="140.00" Metro="4/4" Battito="1"/>
    </TRACK>
    <TRACK TrackID="115068759" Name="Movement" Artist="Quantum" Composer=""
           Album="Hits" Grouping="" Genre="Trance" Kind="FLAC File" Size="1"
           TotalTime="100" DiscNumber="0" TrackNumber="18" Year="2020"
           AverageBpm="145.00" DateAdded="2024-12-31" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_c}" Remixer="" Tonality="G" Label="" Mix="">
      <TEMPO Inizio="0.413" Bpm="145.00" Metro="4/4" Battito="2"/>
    </TRACK>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Intelligent playlists" Type="0" Count="1">
        <NODE Name="Untitled Intelligent List" Type="1" KeyType="0" Entries="3">
          <TRACK Key="219211420"/>
          <TRACK Key="58834508"/>
          <TRACK Key="115068759"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


def flac_probe(bits: int = 24) -> dict:
    fmt = "s32" if bits == 24 else "s16"
    return {
        "format": {"format_name": "flac"},
        "streams": [
            {
                "codec_name": "flac",
                "sample_fmt": fmt,
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": str(bits),
            }
        ],
    }


def wav_probe(bits: int = 24) -> dict:
    codec = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}[bits]
    fmt = "s16" if bits == 16 else "s32"
    return {
        "format": {"format_name": "wav"},
        "streams": [
            {
                "codec_name": codec,
                "sample_fmt": fmt,
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": str(bits),
            }
        ],
    }


def write_flac(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC")

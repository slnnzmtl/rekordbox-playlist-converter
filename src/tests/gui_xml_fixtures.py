"""Shared XML fixtures for GUI playlist / tracklist tests."""

from __future__ import annotations

from pathlib import Path

TRACKLIST_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="Bestial" Artist="ABSL"
           Location="file://localhost/Users/me/music/Bestial.flac"
           Kind="FLAC File" SampleRate="44100"/>
    <TRACK TrackID="2" Name="Revelation" Artist="Shogan"
           Location="file://localhost/Users/me/music/Revelation.aiff"
           Kind="AIFF File" SampleRate="48000"/>
    <TRACK TrackID="3" Name="NoLoc" Artist="Ghost" Kind="WAV File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Dark forest" Type="1" KeyType="0" Entries="3">
        <TRACK Key="1"/>
        <TRACK Key="2"/>
        <TRACK Key="999"/>
      </NODE>
      <NODE Name="Morning" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/>
        <TRACK Key="3"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


def write_xml(directory: Path, text: str) -> Path:
    path = directory / "rekordbox.xml"
    path.write_text(text, encoding="utf-8")
    return path

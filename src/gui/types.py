"""Typed GUI row references for playlists and tracklist leaves."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum


class PlaylistNodeKind(str, Enum):
    FOLDER = "folder"
    PLAYLIST = "playlist"


@dataclass(frozen=True)
class PlaylistEntry:
    kind: PlaylistNodeKind
    folder: str
    name: str
    count: int
    node: ET.Element

    @classmethod
    def from_walk(
        cls,
        kind: str,
        folder: str,
        name: str,
        count: int,
        node: ET.Element,
    ) -> PlaylistEntry:
        return cls(
            kind=PlaylistNodeKind(kind),
            folder=folder,
            name=name,
            count=count,
            node=node,
        )


@dataclass(frozen=True)
class PlaylistRef:
    folder: str
    name: str

    @classmethod
    def from_tuple(cls, folder: str | None, name: str) -> PlaylistRef:
        return cls(folder=folder or "", name=name)


@dataclass(frozen=True)
class TrackLeafRef:
    folder: str
    name: str
    key: str

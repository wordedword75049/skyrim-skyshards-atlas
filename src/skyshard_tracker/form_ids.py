"""Plugin-relative FormIDs and save-specific Skyrim runtime FormIDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


PluginKind = Literal["full", "light"]


def plugin_local_id(form_id: int, *, self_index: int, kind: PluginKind) -> int:
    """A plugin record ID has a master-table byte, not a save load-order byte."""
    if form_id >> 24 != self_index:
        raise ValueError(f"FormID {form_id:08X} is not owned by Skyshards.esp")
    local = form_id & 0xFFFFFF
    if kind == "light" and local > 0xFFF:
        raise ValueError(f"ESL FormID {form_id:08X} exceeds the 12-bit object range")
    return local


@dataclass(frozen=True, slots=True)
class RuntimeFormIds:
    kind: PluginKind
    index: int

    def resolve(self, local_id: int) -> int:
        if self.kind == "light":
            if not 0 <= self.index < 4096 or not 0 <= local_id <= 0xFFF:
                raise ValueError("Invalid light-plugin index or local FormID")
            return 0xFE000000 | (self.index << 12) | local_id
        if not 0 <= self.index < 0xFE or not 0 <= local_id <= 0xFFFFFF:
            raise ValueError("Invalid full-plugin index or local FormID")
        return (self.index << 24) | local_id


def from_save_tables(kind: PluginKind, full_names: Sequence[str],
                     light_names: Sequence[str]) -> tuple[RuntimeFormIds | None, str | None]:
    full = next((index for index, name in enumerate(full_names)
                 if name.casefold() == "skyshards.esp"), None)
    light = next((index for index, name in enumerate(light_names)
                  if name.casefold() == "skyshards.esp"), None)
    if kind == "full" and full is not None and full < 0xFE:
        return RuntimeFormIds("full", full), None
    if kind == "light" and light is not None:
        return RuntimeFormIds("light", light), None
    if full is not None or light is not None:
        return None, f"Skyshards.esp is {('full' if full is not None else 'light')} in this save, but the imported plugin is {kind}"
    return None, f"Skyshards.esp is missing from this save's {kind}-plugin table"

"""Conservative tri-state classification from a decoded Skyshards FormList."""

from __future__ import annotations

from datetime import datetime, timezone

from .ess import ParsedSave, unavailable_player_location
from .models import Catalog, ProgressSnapshot, SaveIdentity, ShardProgress


def empty_snapshot(catalog: Catalog, warning: str = "No save has been parsed") -> ProgressSnapshot:
    return ProgressSnapshot(
        catalog_version=catalog.version, total=108, collected_count=0, uncollected_count=0,
        unknown_count=108,
        shards=tuple(ShardProgress(id=s.id, hold=s.hold, worldspace=s.worldspace, cell=s.cell,
                                   coordinates=s.coordinates, status="unknown", save_form_id=None,
                                   confidence="insufficient", evidence=()) for s in catalog.shards),
        save=None, player_location=unavailable_player_location("No save has been parsed"),
        is_newest=False, newest_candidate=None, newer_save_error=None,
        warnings=(warning,), last_successful_refresh_at=None,
    )


def classify(parsed: ParsedSave, catalog: Catalog, *, modified_at: datetime | None = None,
             co_save_status: str = "missing") -> ProgressSnapshot:
    now = datetime.now(timezone.utc)
    warnings = list(parsed.warnings)
    resolver = parsed.runtime_forms if parsed.skyshards_kind == catalog.plugin_kind else None
    expected = {resolver.resolve(local) for local in catalog.by_local_id} if resolver else set()
    members = parsed.absorbed_members
    list_valid = members is not None and parsed.absorbed_list_error is None
    if list_valid and len(members) != len(set(members)):
        warnings.append("duplicate absorbed FormList members")
        list_valid = False
    if list_valid and not set(members) <= expected:
        warnings.append("foreign absorbed FormList member")
        list_valid = False
    counters = parsed.counters
    count = len(members) if members is not None else None
    activated = ("DMN_SkyshardsCountActivated", "DMN_SkyshardsSkyrimCountActivated")
    totals = ("DMN_SkyshardsCountTotal", "DMN_SkyshardsSkyrimCountTotal")
    counter_valid = count is not None and all(counters.get(key) == count for key in activated) and all(
        counters.get(key) == 108 for key in totals)
    if not counter_valid:
        warnings.append("Skyshards activated/total counters disagree with absorbed FormList or 108 total")
    identity_valid = resolver is not None and parsed.resolved_shard_count == 108
    if not identity_valid:
        warnings.append("loaded Skyshards identity or 108 FormID mappings could not be verified")
    negatives_valid = bool(list_valid and counter_valid and identity_valid and not any(
        warning.startswith(("duplicate global", "non-finite global")) for warning in warnings))
    positive = set(members) if list_valid and identity_valid else set()
    shards = []
    for shard in catalog.shards:
        save_id = resolver.resolve(shard.loaded_local_id) if resolver else None
        if save_id in positive:
            status, confidence, evidence = "collected", "validated" if negatives_valid else "positive", (
                "DMN_SkyshardsAbsorbedList membership",)
        elif negatives_valid:
            status, confidence, evidence = "uncollected", "validated", (
                "complete absorbed FormList and matching counters",)
        else:
            status, confidence, evidence = "unknown", "insufficient", ()
        shards.append(ShardProgress(id=shard.id, hold=shard.hold, worldspace=shard.worldspace,
                                    cell=shard.cell, coordinates=shard.coordinates, status=status,
                                    save_form_id=f"{save_id:08X}" if save_id is not None else None,
                                    confidence=confidence, evidence=evidence))
    collected = sum(shard.status == "collected" for shard in shards)
    uncollected = sum(shard.status == "uncollected" for shard in shards)
    if modified_at is None:
        modified_at = datetime.fromtimestamp(parsed.path.stat().st_mtime, timezone.utc)
    identity = SaveIdentity(filename=parsed.path.name, character=parsed.character,
                            save_number=parsed.save_number, modified_at=modified_at, parsed_at=now,
                            co_save_status=co_save_status)
    return ProgressSnapshot(catalog_version=catalog.version, total=108, collected_count=collected,
                            uncollected_count=uncollected, unknown_count=108 - collected - uncollected,
                            shards=tuple(shards), save=identity, player_location=parsed.player_location,
                            is_newest=True,
                            newest_candidate=parsed.path.name, newer_save_error=None,
                            warnings=tuple(dict.fromkeys(warnings)), last_successful_refresh_at=now)

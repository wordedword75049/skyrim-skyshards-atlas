export function defaultFilters() {
  return {uncollected: true, collected: false, unknown: true, hold: 'all'};
}

export function resetFilters() {
  return defaultFilters();
}

export function visibleShard(shard, filters) {
  return Boolean(filters[shard.status]) && (filters.hold === 'all' || shard.hold === filters.hold);
}

export function counts(shards) {
  const result = {collected: 0, uncollected: 0, unknown: 0, total: shards.length, percent: 0};
  for (const shard of shards) {
    if (Object.hasOwn(result, shard.status)) result[shard.status] += 1;
  }
  result.percent = result.total ? Math.round(result.collected * 100 / result.total) : 0;
  return result;
}

export function markerClass(shard) {
  return `shard-pin ${['collected', 'uncollected', 'unknown'].includes(shard.status) ? shard.status : 'unknown'}`;
}

export function readableName(id, hold) {
  const number = /([0-9]{2})$/.exec(id)?.[1] ?? '??';
  return `${hold} · Skyshard ${number}`;
}

function characterToken(filename) {
  return /^[^_]+_([0-9A-Fa-f]{8})_/.exec(filename)?.[1]?.toUpperCase() ?? null;
}

export function collectionTransition(previous, current) {
  if (!previous?.save || !current?.save || previous.catalog_version !== current.catalog_version) return null;
  if (!previous.is_newest || !current.is_newest || previous.newer_save_error || current.newer_save_error) return null;
  if (previous.unknown_count !== 0 || current.unknown_count !== 0) return null;
  const oldToken = characterToken(previous.save.filename);
  if (!oldToken || oldToken !== characterToken(current.save.filename)) return null;
  if (!previous.save.character || previous.save.character !== current.save.character) return null;
  if (current.save.save_number < previous.save.save_number) return null;
  if (Date.parse(current.save.modified_at) <= Date.parse(previous.save.modified_at)) return null;
  if (current.collected_count !== previous.collected_count + 1 ||
      current.uncollected_count !== previous.uncollected_count - 1) return null;
  const oldStatuses = new Map(previous.shards.map(shard => [shard.id, shard.status]));
  const newlyCollected = current.shards.filter(shard =>
    shard.status === 'collected' && oldStatuses.get(shard.id) === 'uncollected');
  if (newlyCollected.length !== 1) return null;
  if (previous.shards.some(shard => shard.status === 'collected' &&
      current.shards.find(next => next.id === shard.id)?.status !== 'collected')) return null;
  return newlyCollected[0].id;
}

export function projectWorld(projection, x, y) {
  const [a, b, c, d, e, f] = projection;
  return [a * x + b * y + c, d * x + e * y + f];
}

export function visiblePlayerLocation(snapshot) {
  const place = snapshot?.player_location;
  if (!snapshot?.save || !snapshot.is_newest || snapshot.newer_save_error ||
      place?.status !== 'available' || place.worldspace !== 'Tamriel' ||
      place.is_exterior !== true || !Number.isFinite(place.x) || !Number.isFinite(place.y)) return null;
  return place;
}

export function saveIndicator(snapshot) {
  if (!snapshot?.save) return {state: '', text: 'WAITING'};
  if (!snapshot.is_newest || snapshot.newer_save_error || snapshot.unknown_count > 0) {
    return {state: 'stale', text: 'STALE / CHECK'};
  }
  return {state: 'live', text: 'LATEST SAVE'};
}

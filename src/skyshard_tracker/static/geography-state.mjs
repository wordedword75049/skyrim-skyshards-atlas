const priority = {city: 0, settlement: 1, landmark: 2};

export function featureVisible(feature, zoom, showLandmarks) {
  if (feature.kind === 'city') return true;
  if (feature.kind === 'settlement') return zoom >= -1.75;
  return showLandmarks && zoom >= -1.25;
}

export function pickLabels(rows, viewportWidth, viewportHeight, enabled = true) {
  if (!enabled) return [];
  const occupied = [];
  const selected = [];
  for (const row of [...rows].sort((a, b) =>
    (priority[a.kind] ?? 9) - (priority[b.kind] ?? 9) || a.name.localeCompare(b.name))) {
    const width = Math.max(42, row.name.length * 7 + 14);
    const box = {left: row.x + 17, right: row.x + 17 + width,
      top: row.y - 11, bottom: row.y + 9};
    if (box.right < 0 || box.left > viewportWidth || box.bottom < 0 || box.top > viewportHeight) continue;
    if (occupied.some(other => box.left < other.right && box.right > other.left &&
        box.top < other.bottom && box.bottom > other.top)) continue;
    occupied.push(box);
    selected.push(row.id);
  }
  return selected;
}

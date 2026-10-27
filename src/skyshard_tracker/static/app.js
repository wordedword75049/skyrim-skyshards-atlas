import {defaultFilters, resetFilters, visibleShard, markerClass, readableName, collectionTransition,
  projectWorld, visiblePlayerLocation, saveIndicator} from './tracker-state.mjs';
import {featureVisible, pickLabels} from './geography-state.mjs';

const el = id => document.getElementById(id);
let filters = defaultFilters();
let map = null;
let mapBounds = null;
let viewBounds = null;
let mapMetadata = null;
let overlay = null;
let waterOverlay = null;
let detailTerrain = null;
let detailWater = null;
let mapLoading = false;
let snapshot = null;
let playerMarker = null;
let polling = false;
let toastTimer = null;
const markers = new Map();
const geographyMarkers = new Map();
let geography = [];
let layers = {water: true, labels: true, landmarks: true};

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, {cache: 'no-store', ...options});
  let payload;
  try { payload = await response.json(); } catch { throw new Error(`${url}: invalid server response`); }
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `${url}: HTTP ${response.status}`);
  return payload;
}

function localTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
}

function showToast(message) {
  const toast = el('toast');
  toast.textContent = message;
  toast.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add('hidden'), 4800);
}

function setMapStatus(title, detail, error = false) {
  const status = el('map-status');
  status.querySelector('strong').textContent = title;
  status.querySelector('span').textContent = detail;
  status.classList.toggle('error', error);
  status.classList.remove('hidden');
}

function popupContent(shard, save) {
  const root = document.createElement('div');
  const add = (className, text) => {
    const node = document.createElement('div');
    node.className = className;
    node.textContent = text;
    root.appendChild(node);
  };
  add('popup-hold', shard.hold);
  add('popup-title', readableName(shard.id, shard.hold));
  add('popup-status', shard.status);
  const {x, y, z} = shard.coordinates;
  add('popup-meta', `Tamriel · ${Math.round(x).toLocaleString()}, ${Math.round(y).toLocaleString()}, ${Math.round(z).toLocaleString()} (X, Y, Z)`);
  add('popup-tech', `${shard.id}${shard.save_form_id ? ` · ${shard.save_form_id}` : ''}`);
  add('popup-evidence', `Confidence: ${shard.confidence}. ${shard.evidence.join(' · ') || 'Insufficient save evidence.'}`);
  if (save) add('popup-evidence', `Source save: ${save.filename}`);
  return root;
}

function iconFor(shard) {
  return L.divIcon({className: 'shard-marker', html: `<span class="${markerClass(shard)}" aria-hidden="true"></span>`,
    iconSize: [20, 20], iconAnchor: [10, 10], popupAnchor: [0, -10]});
}

function syncMarkerLayers() {
  if (!map || !snapshot) return;
  let visible = 0;
  const positions = new Map(mapMetadata.markers.map(item => [item.id, item]));
  for (const shard of snapshot.shards) {
    const point = positions.get(shard.id);
    if (!point) continue;
    let entry = markers.get(shard.id);
    if (!entry) {
      const layer = L.marker([-point.pixel_y, point.pixel_x], {icon: iconFor(shard),
        title: readableName(shard.id, shard.hold), riseOnHover: true});
      layer.bindPopup(popupContent(shard, snapshot.save), {maxWidth: 310});
      entry = {layer, status: shard.status, pixelX: point.pixel_x, pixelY: point.pixel_y};
      markers.set(shard.id, entry);
    } else {
      if (entry.pixelX !== point.pixel_x || entry.pixelY !== point.pixel_y) {
        entry.layer.setLatLng([-point.pixel_y, point.pixel_x]);
        entry.pixelX = point.pixel_x;
        entry.pixelY = point.pixel_y;
      }
      if (entry.status !== shard.status) {
        entry.layer.setIcon(iconFor(shard));
        entry.status = shard.status;
      }
      entry.layer.setPopupContent(popupContent(shard, snapshot.save));
    }
    if (visibleShard(shard, filters)) {
      if (!map.hasLayer(entry.layer)) entry.layer.addTo(map);
      visible++;
    } else if (map.hasLayer(entry.layer)) {
      map.removeLayer(entry.layer);
    }
  }
  el('visible-count').textContent = `${visible} of 108 markers visible`;
}

function playerPopup(location, save) {
  const root = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = `${save.character || 'Player'} · Saved position`;
  const place = document.createElement('div');
  place.className = 'popup-meta';
  place.textContent = `${location.saved_location || 'Tamriel'} · ${Math.round(location.x).toLocaleString()}, ${Math.round(location.y).toLocaleString()}, ${Math.round(location.z).toLocaleString()} (X, Y, Z)`;
  const source = document.createElement('div');
  source.className = 'popup-evidence';
  source.textContent = `${save.filename} · ${localTime(save.modified_at)}`;
  root.append(title, place, source);
  return root;
}

function syncPlayerMarker() {
  const location = visiblePlayerLocation(snapshot);
  el('center-player').disabled = !location || !map || !mapMetadata;
  if (!location || !map || !mapMetadata) {
    if (playerMarker) { playerMarker.remove(); playerMarker = null; }
    return;
  }
  const [px, py] = projectWorld(mapMetadata.projection, location.x, location.y);
  if (!playerMarker) {
    playerMarker = L.marker([-py, px], {pane: 'playerPane', title: 'Saved player position',
      zIndexOffset: 1000, icon: L.divIcon({className: 'player-marker',
        html: '<span class="player-pin" aria-hidden="true">◆</span>',
        iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -14]})}).addTo(map);
    playerMarker.bindPopup(playerPopup(location, snapshot.save));
  } else {
    playerMarker.setLatLng([-py, px]);
    playerMarker.setPopupContent(playerPopup(location, snapshot.save));
  }
}

function syncFilters() {
  el('filter-uncollected').checked = filters.uncollected;
  el('filter-collected').checked = filters.collected;
  el('filter-unknown').checked = filters.unknown;
  el('hold-filter').value = filters.hold;
  syncMarkerLayers();
}

function populateHolds(shards) {
  const select = el('hold-filter');
  if (select.options.length > 1) return;
  for (const hold of [...new Set(shards.map(shard => shard.hold))].sort()) {
    const option = document.createElement('option');
    option.value = hold;
    option.textContent = hold;
    select.appendChild(option);
  }
}

function showSaveState(data) {
  el('progress-collected').textContent = data.collected_count;
  el('count-collected').textContent = data.collected_count;
  el('count-uncollected').textContent = data.uncollected_count;
  el('count-unknown').textContent = data.unknown_count;
  const percent = Math.round(data.collected_count / 108 * 100);
  el('progress-percent').textContent = `${percent}%`;
  el('progress-ring').style.setProperty('--progress', `${percent}%`);
  const save = data.save;
  el('save-character').textContent = save?.character || (save ? 'Unknown character' : 'Awaiting a verified save');
  el('save-number').textContent = save?.save_number ?? '—';
  el('save-modified').textContent = localTime(save?.modified_at);
  el('save-parsed').textContent = localTime(data.last_successful_refresh_at);
  el('save-file').textContent = save?.filename ?? 'No save loaded';
  const location = data.player_location;
  el('player-location-state').textContent = visiblePlayerLocation(data) ?
    `Player position: ${location.saved_location || 'Tamriel'} · X ${Math.round(location.x).toLocaleString()}, Y ${Math.round(location.y).toLocaleString()}` :
    `Player position unavailable: ${data.newer_save_error ? 'newer save could not be read' :
      location?.reason || 'no verified outdoor Tamriel position'}${location?.saved_location ? ` (${location.saved_location})` : ''}`;
  const warnings = [];
  if (data.newer_save_error) warnings.push(`Newer save problem: ${data.newer_save_error}`);
  if (save && !data.is_newest && !data.newer_save_error) warnings.push('Displayed progress is not from the newest save.');
  warnings.push(...(data.warnings || []));
  const warning = el('save-warning');
  warning.textContent = warnings.join(' ');
  warning.classList.toggle('hidden', warnings.length === 0);
  const indicator = el('live-indicator');
  const indicatorState = saveIndicator(data);
  indicator.className = `live-indicator ${indicatorState.state}`;
  indicator.textContent = indicatorState.text;
}

function applySnapshot(next) {
  if (next.total !== 108 || next.shards?.length !== 108) throw new Error('Backend did not return the validated 108-shard catalog.');
  const transition = collectionTransition(snapshot, next);
  snapshot = next;
  populateHolds(next.shards);
  showSaveState(next);
  syncMarkerLayers();
  syncPlayerMarker();
  if (transition) {
    const shard = next.shards.find(item => item.id === transition);
    showToast(`Skyshard absorbed · ${readableName(shard.id, shard.hold)}`);
    const entry = markers.get(transition);
    if (entry && map?.hasLayer(entry.layer)) {
      const pin = entry.layer.getElement()?.querySelector('.shard-pin');
      pin?.classList.add('just-collected');
      setTimeout(() => pin?.classList.remove('just-collected'), 3500);
    }
  }
}

async function pollProgress(manual = false) {
  if (polling) return;
  polling = true;
  if (manual) {
    el('refresh-button').disabled = true;
    el('refresh-status').textContent = 'Checking the newest save…';
  }
  try {
    const next = await jsonRequest(manual ? '/api/refresh' : '/api/progress',
      manual ? {method: 'POST'} : {});
    applySnapshot(next);
    el('refresh-status').textContent = manual ? 'Refresh complete.' : 'Save progress updates automatically.';
  } catch (error) {
    if (playerMarker) { playerMarker.remove(); playerMarker = null; }
    el('center-player').disabled = true;
    el('player-location-state').textContent = 'Player position unavailable: backend connection lost';
    el('refresh-status').textContent = `Connection problem: ${error.message}`;
    const warning = el('save-warning');
    warning.textContent = snapshot ? `Backend unavailable. Displaying last received progress. ${error.message}` :
      `Backend unavailable. No progress is displayed. ${error.message}`;
    warning.classList.remove('hidden');
    el('live-indicator').textContent = 'OFFLINE';
    el('live-indicator').className = 'live-indicator stale';
  } finally {
    polling = false;
    el('refresh-button').disabled = false;
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
}

function geographyIcon(feature) {
  const symbol = feature.kind === 'city' ? '◆' : feature.kind === 'settlement' ? '●' : '▲';
  return L.divIcon({className: `geography-marker geography-${feature.kind}`,
    html: `<span class="geo-pin" aria-hidden="true">${symbol}</span><span class="geo-label">${escapeHtml(feature.name)}</span>`,
    iconSize: [18, 18], iconAnchor: [9, 9]});
}

function refreshGeography() {
  if (!map || !mapMetadata || !geography.length) return;
  const candidates = [];
  for (const feature of geography) {
    const [px, py] = projectWorld(mapMetadata.projection, feature.x, feature.y);
    let layer = geographyMarkers.get(feature.form_id);
    if (!layer) {
      layer = L.marker([-py, px], {icon: geographyIcon(feature), pane: 'geographyPane',
        title: feature.name, keyboard: true});
      const popup = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = feature.name;
      const source = document.createElement('div');
      source.className = 'popup-evidence';
      source.textContent = `${feature.editor_id} · ${feature.form_id} · ${feature.winning_plugin}`;
      popup.append(title, source);
      layer.bindPopup(popup);
      geographyMarkers.set(feature.form_id, layer);
    } else {
      layer.setLatLng([-py, px]);
    }
    if (featureVisible(feature, map.getZoom(), layers.landmarks)) {
      if (!map.hasLayer(layer)) layer.addTo(map);
      const point = map.latLngToContainerPoint(layer.getLatLng());
      candidates.push({id: feature.form_id, name: feature.name, kind: feature.kind,
        x: point.x, y: point.y});
    } else if (map.hasLayer(layer)) {
      map.removeLayer(layer);
    }
  }
  const labels = new Set(pickLabels(candidates, map.getSize().x, map.getSize().y, layers.labels));
  for (const [id, layer] of geographyMarkers) {
    layer.getElement()?.querySelector('.geo-label')?.classList.toggle('hidden-label', !labels.has(id));
  }
}

async function loadGeography() {
  try {
    const payload = await jsonRequest('/api/landmarks');
    if (!Array.isArray(payload.features) || payload.features.length !== 24) {
      throw new Error('The validated 24-place catalog is incomplete.');
    }
    geography = payload.features;
    refreshGeography();
  } catch (error) {
    const warning = el('geography-warning');
    warning.textContent = `Place names unavailable: ${error.message}`;
    warning.classList.remove('hidden');
  }
}

function syncWaterLayer() {
  if (!map || !waterOverlay) return;
  for (const layer of [waterOverlay, detailWater]) {
    if (!layer) continue;
    if (layers.water && !map.hasLayer(layer)) layer.addTo(map);
    if (!layers.water && map.hasLayer(layer)) map.removeLayer(layer);
  }
}

async function loadMap() {
  if (mapLoading) return;
  mapLoading = true;
  setMapStatus('Loading the local map', 'Reading generated terrain and water tiles…');
  try {
    const metadata = await jsonRequest('/api/map');
    if (metadata.state === 'error') throw new Error(metadata.error);
    if (metadata.state !== 'ready' || metadata.markers?.length !== 108 ||
        metadata.projection?.length !== 6) throw new Error('The local map metadata is incomplete.');
    mapMetadata = metadata;
    mapBounds = [[-metadata.height, 0], [0, metadata.width]];
    const [left, top, right, bottom] = metadata.view_bbox;
    viewBounds = [[-bottom, left], [-top, right]];
    if (!map) {
      map = L.map('map', {crs: L.CRS.Simple, zoomControl: false, attributionControl: false,
        minZoom: -4, maxZoom: 3, zoomSnap: 0.25, zoomDelta: 0.5, maxBoundsViscosity: 0.7});
      map.createPane('detailTerrainPane').style.zIndex = '430';
      map.createPane('detailWaterPane').style.zIndex = '440';
      map.createPane('geographyPane').style.zIndex = '650';
      map.createPane('playerPane').style.zIndex = '720';
      map.on('zoomend moveend', refreshGeography);
      L.control.zoom({position: 'bottomright'}).addTo(map);
    }
    if (overlay) map.removeLayer(overlay);
    if (waterOverlay) map.removeLayer(waterOverlay);
    if (detailTerrain) map.removeLayer(detailTerrain);
    if (detailWater) map.removeLayer(detailWater);
    waterOverlay = null;
    detailTerrain = null;
    detailWater = null;
    overlay = L.imageOverlay(metadata.image_url, mapBounds, {alt: metadata.attribution});
    overlay.on('load', () => el('map-status').classList.add('hidden'));
    overlay.on('error', () => setMapStatus('Local map image failed to load',
      'The local WebP could not be read. Run build-map --force to rebuild it.', true));
    overlay.addTo(map);
    waterOverlay = L.imageOverlay(metadata.water_url, mapBounds,
      {alt: 'Mesh-derived water and shorelines', interactive: false});
    detailTerrain = L.tileLayer(metadata.terrain_tile_url, {tileSize: 512, minZoom: 1,
      maxNativeZoom: 1, maxZoom: 3, noWrap: true, bounds: mapBounds,
      pane: 'detailTerrainPane', attribution: metadata.attribution});
    detailWater = L.tileLayer(metadata.water_tile_url, {tileSize: 512, minZoom: 1,
      maxNativeZoom: 1, maxZoom: 3, noWrap: true, bounds: mapBounds,
      pane: 'detailWaterPane'});
    detailTerrain.addTo(map);
    syncWaterLayer();
    map.setMaxBounds([[-metadata.height - 900, -900], [900, metadata.width + 900]]);
    map.fitBounds(viewBounds);
    refreshGeography();
    syncMarkerLayers();
    syncPlayerMarker();
  } catch (error) {
    setMapStatus('Local map unavailable', error.message, true);
  } finally {
    mapLoading = false;
  }
}

for (const status of ['uncollected', 'collected', 'unknown']) {
  el(`filter-${status}`).addEventListener('change', event => {
    filters = {...filters, [status]: event.target.checked};
    syncMarkerLayers();
  });
}
el('hold-filter').addEventListener('change', event => {filters = {...filters, hold: event.target.value}; syncMarkerLayers();});
el('reset-filters').addEventListener('click', () => {filters = resetFilters(); syncFilters();});
el('refresh-button').addEventListener('click', () => pollProgress(true));
el('reset-view').addEventListener('click', () => {if (map && viewBounds) map.fitBounds(viewBounds);});
el('center-player').addEventListener('click', () => {
  const location = visiblePlayerLocation(snapshot);
  if (!location || !map || !mapMetadata) return;
  const [px, py] = projectWorld(mapMetadata.projection, location.x, location.y);
  map.setView([-py, px], Math.max(map.getZoom(), 0), {animate: true});
});
el('layer-water').addEventListener('change', event => {
  layers = {...layers, water: event.target.checked};
  syncWaterLayer();
});
el('layer-labels').addEventListener('change', event => {
  layers = {...layers, labels: event.target.checked};
  refreshGeography();
});
el('layer-landmarks').addEventListener('change', event => {
  layers = {...layers, landmarks: event.target.checked};
  refreshGeography();
});

void loadMap();
void loadGeography();
void pollProgress();
setInterval(() => {void pollProgress();}, 10000);

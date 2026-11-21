<script setup lang="ts">
// A full-page 3D terrain map (MapTiler Outdoor, or satellite imagery, on a terrain-RGB DEM), centered on the
// Washington Cascades, with the athlete's activity tracks (their PMTiles archive) colored by sport. Clicking a track
// opens its details; the filter panel narrows tracks by sport and date; the area button draws a box whose tracks
// download for CalTopo.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre looks for its worker next to its own module, which the bundle doesn't keep; emit it as an asset
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { Protocol } from 'pmtiles'
import { api, SignedOutError, TRACKS_URL, useSignedIn, type ActivitySummary, type BBox, type MapActivities } from '@/api'
import { AreaSelect } from '@/areaSelect'
import { filterExpression, matches } from '@/mapFilters'
import { settings } from '@/settings'
import { SPORT_COLOR_EXPRESSION } from '@/sports'
import ActivityPopup from '@/components/ActivityPopup.vue'
import ExportDialog from '@/components/ExportDialog.vue'
import FloatingButton from '@/components/FloatingButton.vue'
import MapFilters from '@/components/MapFilters.vue'

const MAPTILER_KEY = import.meta.env.VITE_MAPTILER_KEY ?? ''
const CASCADES: [number, number] = [-121.4, 47.9]
// the archive's layer (tracks.LAYER on the server)
const LAYER = 'tracks'
const HIT_LAYER = 'tracks-hit'
// pixels from a click that count as on a track, measured on screen
const HIT_RADIUS = 10
// half-size of the box that finds candidate tracks: on a steeply tilted map MapLibre measures line widths at the
// center's scale, so distant tracks only match boxes much larger than they look; `tracksAt` checks them on screen
const CANDIDATE_SLOP = 96
// the style's hiking and cycling routes (e.g. the PCT), drawn in bright colors that clash with the tracks; they
// get the style's own colors for paths
const ROUTES_LAYER = 'trail'
const MUTED_ROUTE = 'hsl(24, 10%, 60%)'
const MUTED_ROUTE_LABEL = 'hsl(24, 10%, 40%)'
// MapTiler satellite imagery, drawn over the topo map's base (landcover and hillshade) but under its lines (contours,
// roads, trails), water, labels and the tracks
const SATELLITE = 'satellite'

maplibregl.setWorkerUrl(workerUrl)
const pmtiles = new Protocol()
maplibregl.addProtocol('pmtiles', pmtiles.tile)

const signedIn = useSignedIn()
const container = ref<HTMLDivElement | null>(null)
const data = ref<MapActivities | null>(null)
const loadError = ref('')
// the activities under the last click (newest first), and the one shown
const candidates = ref<ActivitySummary[]>([])
const selectedId = ref('')
// the popup's content is rendered by Vue, teleported into MapLibre's popup
const popupContent = document.createElement('div')
// area mode (drawing an area, then its export dialog): the area drawn, and the tracks the export would hold
const areaMode = ref(false)
const area = ref<BBox | null>(null)
const exportIds = ref<string[]>([])
const filtersOpen = ref(false)
// after a download: how many tracks, for the import instructions shown briefly
const downloaded = ref(0)
let downloadedTimer: ReturnType<typeof setTimeout> | undefined
let map: maplibregl.Map | null = null
let popup: maplibregl.Popup | null = null
let areaSelect: AreaSelect | null = null

const byId = computed(() => new Map(data.value?.activities.map((a) => [a.id, a]) ?? []))
const onMap = computed(() =>
  (data.value?.on_map ?? []).flatMap((id) => {
    const activity = byId.value.get(id)
    return activity ? [activity] : []
  }),
)
const mapTypes = computed(() => [...new Set(onMap.value.map((activity) => activity.type))])

let resolveMapLoaded: () => void = () => {}
const mapLoaded = new Promise<void>((resolve) => (resolveMapLoaded = resolve))

onMounted(() => {
  if (!MAPTILER_KEY || !container.value) return
  map = new maplibregl.Map({
    container: container.value,
    style: `https://api.maptiler.com/maps/outdoor-v2/style.json?key=${MAPTILER_KEY}`,
    center: CASCADES,
    zoom: 8.5,
    pitch: 0,
    maxPitch: 85,
  })
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right')
  map.addControl(new maplibregl.ScaleControl({ unit: settings.units === 'ft' ? 'imperial' : 'metric' }))
  map.on('load', () => {
    map?.addSource('terrain', {
      type: 'raster-dem',
      url: `https://api.maptiler.com/tiles/terrain-rgb-v2/tiles.json?key=${MAPTILER_KEY}`,
      tileSize: 256,
    })
    map?.setTerrain({ source: 'terrain', exaggeration: 1 })
    map?.addControl(new maplibregl.TerrainControl({ source: 'terrain' }), 'bottom-right')
    // bottom controls stack upwards, so this sits above the terrain button
    map?.addControl(satelliteControl, 'bottom-right')
    if (map) {
      muteRoutes(map)
      addSatellite(map)
    }
    resolveMapLoaded()
  })
  window.addEventListener('keydown', closeOnEscape)
})

function closeOnEscape(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (areaMode.value) endArea()
  else popup?.remove()
}

// a button in MapLibre's own control style (like the terrain button) that toggles satellite imagery
const satelliteButton = document.createElement('button')
satelliteButton.type = 'button'
satelliteButton.className = 'satellite-toggle'
satelliteButton.innerHTML = '<span class="maplibregl-ctrl-icon" aria-hidden="true"></span>'
satelliteButton.addEventListener('click', () => (settings.satellite = !settings.satellite))
const satelliteControl: maplibregl.IControl = {
  onAdd: () => {
    const group = document.createElement('div')
    group.className = 'maplibregl-ctrl maplibregl-ctrl-group'
    group.append(satelliteButton)
    return group
  },
  onRemove: () => satelliteButton.parentElement?.remove(),
}

function startArea(): void {
  popup?.remove()
  filtersOpen.value = false
  downloaded.value = 0
  area.value = null
  exportIds.value = []
  areaMode.value = true
  areaSelect?.start()
}

function endArea(): void {
  areaSelect?.cancel()
  areaMode.value = false
  area.value = null
  exportIds.value = []
}

function onDownloaded(count: number): void {
  endArea()
  downloaded.value = count
  clearTimeout(downloadedTimer)
  downloadedTimer = setTimeout(() => (downloaded.value = 0), 12_000)
}

watch(exportIds, applyFilters)

function addSatellite(m: maplibregl.Map): void {
  m.addSource(SATELLITE, {
    type: 'raster',
    url: `https://api.maptiler.com/tiles/satellite-v2/tiles.json?key=${MAPTILER_KEY}`,
    tileSize: 512,
  })
  const base = ['background', 'fill', 'hillshade']
  const firstOverlay = m.getStyle().layers.find((layer) => !base.includes(layer.type))?.id
  m.addLayer({ id: SATELLITE, type: 'raster', source: SATELLITE }, firstOverlay)
  showBaseLayer()
}

function showBaseLayer(): void {
  const on = settings.satellite
  satelliteButton.classList.toggle('satellite-enabled', on)
  satelliteButton.title = on ? 'Show topo map' : 'Show satellite imagery'
  satelliteButton.setAttribute('aria-pressed', String(on))
  // a hidden layer's tiles aren't fetched
  if (map?.getLayer(SATELLITE)) map.setLayoutProperty(SATELLITE, 'visibility', on ? 'visible' : 'none')
}

watch(() => settings.satellite, showBaseLayer)

function muteRoutes(m: maplibregl.Map): void {
  for (const layer of m.getStyle().layers) {
    if (!('source-layer' in layer) || layer['source-layer'] !== ROUTES_LAYER || layer.id.endsWith('outline')) continue
    if (layer.type === 'line') m.setPaintProperty(layer.id, 'line-color', MUTED_ROUTE)
    else if (layer.type === 'symbol') m.setPaintProperty(layer.id, 'text-color', MUTED_ROUTE_LABEL)
  }
}

/** Pixels from `point` to the nearest segment of a screen-space polyline. */
function distanceToLine(point: maplibregl.Point, line: maplibregl.Point[]): number {
  if (line.length === 1) return point.dist(line[0]!)
  let nearest = Infinity
  for (let i = 1; i < line.length; i++) {
    const a = line[i - 1]!
    const b = line[i]!
    const dx = b.x - a.x
    const dy = b.y - a.y
    const length2 = dx * dx + dy * dy
    const t = length2 ? Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / length2)) : 0
    nearest = Math.min(nearest, Math.hypot(point.x - a.x - t * dx, point.y - a.y - t * dy))
  }
  return nearest
}

/** Ids of the tracks within `HIT_RADIUS` pixels of `point`, measured on screen (so tilt and terrain don't skew it). */
function tracksAt(m: maplibregl.Map, point: maplibregl.Point): Set<string> {
  const { x, y } = point
  const candidates = m.queryRenderedFeatures(
    [
      [x - CANDIDATE_SLOP, y - CANDIDATE_SLOP],
      [x + CANDIDATE_SLOP, y + CANDIDATE_SLOP],
    ],
    { layers: [HIT_LAYER] },
  )
  const onScreen = (line: number[][]) => line.map(([lon, lat]) => m.project([lon!, lat!]))
  const ids = new Set<string>()
  for (const feature of candidates) {
    const id = String(feature.properties.id)
    const { geometry } = feature
    const lines: number[][][] =
      geometry.type === 'LineString' ? [geometry.coordinates] : geometry.type === 'MultiLineString' ? geometry.coordinates : []
    if (!ids.has(id) && lines.some((line) => distanceToLine(point, onScreen(line)) <= HIT_RADIUS)) ids.add(id)
  }
  return ids
}

function addTracks(m: maplibregl.Map): void {
  m.addSource(LAYER, { type: 'vector', url: `pmtiles://${TRACKS_URL}` })
  const width = (base: number): maplibregl.ExpressionSpecification => [
    'interpolate',
    ['linear'],
    ['zoom'],
    6,
    base,
    14,
    base * 2.5,
  ]
  const line = (
    id: string,
    paint: NonNullable<maplibregl.LineLayerSpecification['paint']>,
  ): maplibregl.LineLayerSpecification => ({
    id,
    type: 'line',
    source: LAYER,
    'source-layer': LAYER,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint,
  })
  // a wide invisible line, so thin tracks are easy to tap
  m.addLayer(line(HIT_LAYER, { 'line-color': '#000', 'line-opacity': 0, 'line-width': 16 }))
  m.addLayer(line('tracks-casing', { 'line-color': '#fff', 'line-opacity': 0.7, 'line-width': width(2.6) }))
  m.addLayer(line('tracks', { 'line-color': SPORT_COLOR_EXPRESSION, 'line-width': width(1.4) }))
  m.addLayer(line('tracks-selected-casing', { 'line-color': '#facc15', 'line-width': width(6) }))
  m.addLayer(line('tracks-selected', { 'line-color': SPORT_COLOR_EXPRESSION, 'line-width': width(2.8) }))

  m.on('mouseenter', HIT_LAYER, () => {
    if (!areaSelect?.drawing) m.getCanvas().style.cursor = 'pointer'
  })
  m.on('mouseleave', HIT_LAYER, () => {
    if (!areaSelect?.drawing) m.getCanvas().style.cursor = ''
  })
  m.on('click', (event) => {
    if (areaMode.value) return // the clicks draw an area
    const found = [...tracksAt(m, event.point)]
      .flatMap((id) => byId.value.get(id) ?? [])
      .sort((a, b) => b.start_date_local.localeCompare(a.start_date_local))
    if (!found.length) {
      popup?.remove()
      return
    }
    if (!popup) {
      // closeOnClick: off, since clicks are handled above (another track moves the popup, empty map closes it)
      popup = new maplibregl.Popup({ maxWidth: '300px', focusAfterOpen: false, closeOnClick: false })
      popup.setDOMContent(popupContent)
      popup.on('close', () => {
        candidates.value = []
        selectedId.value = ''
      })
    }
    popup.setLngLat(event.lngLat)
    // addTo() on an open popup closes it first, whose 'close' would clear the selection below
    if (!popup.isOpen()) popup.addTo(m)
    candidates.value = found
    selectedId.value = found[0]!.id
  })
  areaSelect = new AreaSelect(m, (bbox) => (area.value = bbox))
  applyFilters()
}

function applyFilters(): void {
  if (!map?.getLayer(HIT_LAYER)) return
  const filter = filterExpression(settings.mapFilters)
  for (const id of [HIT_LAYER, 'tracks-casing', 'tracks']) map.setFilter(id, filter)
  // highlighted: the export's tracks while its dialog is open, else the popup's
  const ids = area.value ? exportIds.value : [selectedId.value]
  const selected: maplibregl.FilterSpecification = ['all', filter, ['in', ['get', 'id'], ['literal', ids]]]
  for (const id of ['tracks-selected-casing', 'tracks-selected']) map.setFilter(id, selected)
}

watch(
  () => settings.mapFilters,
  () => {
    // the popup keeps only activities that still pass; it closes if none do
    const passing = candidates.value.filter((activity) => matches(activity, settings.mapFilters))
    if (!passing.length) popup?.remove()
    else if (passing.length < candidates.value.length) {
      candidates.value = passing
      if (!passing.some((activity) => activity.id === selectedId.value)) selectedId.value = passing[0]!.id
    }
    applyFilters()
  },
  { deep: true },
)
watch(selectedId, applyFilters)

watch(signedIn, async (value) => {
  if (!value || !MAPTILER_KEY) return
  try {
    data.value = await api.activities()
  } catch (e) {
    if (e instanceof SignedOutError) signedIn.value = false
    else loadError.value = e instanceof Error ? e.message : String(e)
    return
  }
  await mapLoaded
  if (map && data.value.on_map.length) addTracks(map)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', closeOnEscape)
  clearTimeout(downloadedTimer)
  popup?.remove()
  map?.remove()
})
</script>

<template>
  <div ref="container" class="map" />
  <div v-if="!MAPTILER_KEY" class="notice">
    The map needs a MapTiler key: set <code>VITE_MAPTILER_KEY</code> and rebuild the frontend.
  </div>
  <template v-else>
    <div v-if="signedIn === false" class="notice bottom">
      <RouterLink to="/connect">Connect intervals.icu</RouterLink> to see your activities on the map.
    </div>
    <div v-else-if="loadError" class="notice bottom failed">Couldn't load your activities: {{ loadError }}</div>
    <div v-else-if="data && !data.on_map.length" class="notice bottom">
      No tracks yet. <RouterLink to="/">Import your activities</RouterLink> to see them here.
    </div>
    <template v-if="data?.on_map.length">
      <MapFilters v-model:open="filtersOpen" :activities="onMap" :omitted="data.map_tracks_omitted" />
      <FloatingButton
        side="right"
        :offset="1"
        :label="areaMode ? 'Cancel export' : 'Export an area to CalTopo'"
        @click="areaMode ? endArea() : startArea()"
      >
        <span v-if="areaMode" aria-hidden="true">✕</span>
        <svg v-else class="area-icon" viewBox="0 0 22 22" aria-hidden="true">
          <rect x="3" y="4" width="13" height="10" stroke-dasharray="2.2 1.6" />
          <path d="M12 11l1.7 8 1.6-2.9 3.3-.4z" />
        </svg>
      </FloatingButton>
    </template>
    <div v-if="areaMode && !area" class="notice top">Click two corners of the area · Esc to cancel</div>
    <div v-if="downloaded" class="notice top" role="status">
      Downloaded {{ downloaded.toLocaleString() }} {{ downloaded === 1 ? 'track' : 'tracks' }}. In CalTopo, open a map,
      choose <strong>Import</strong> at the top of the sidebar, and pick the file.
      <button type="button" class="dismiss" aria-label="Dismiss" @click="downloaded = 0">✕</button>
    </div>
    <ExportDialog
      v-if="area && data"
      :bbox="area"
      :types="mapTypes"
      :omitted="data.map_tracks_omitted"
      @preview="exportIds = $event"
      @redraw="startArea"
      @close="endArea"
      @downloaded="onDownloaded"
    />
  </template>
  <FloatingButton side="left" to="/" label="Back">←</FloatingButton>
  <Teleport v-if="candidates.length" :to="popupContent">
    <ActivityPopup :activities="candidates" :selected-id="selectedId" @select="selectedId = $event" />
  </Teleport>
</template>

<style scoped>
.map {
  position: fixed;
  inset: 0;
  z-index: 10;
}

/* MapLibre's close button sits flush in the corner; inset it evenly from the top and right */
.map :deep(.maplibregl-popup-close-button) {
  top: 8px;
  right: 8px;
  width: 24px;
  height: 24px;
  padding: 0;
  border-radius: 6px;
  color: #64748b;
  font-size: 18px;
  line-height: 24px;
}

/* the satellite toggle's icon, in the terrain button's colors (grey, blue when on) */
.map :deep(.satellite-toggle .maplibregl-ctrl-icon) {
  background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='22' height='22' viewBox='0 0 22 22' fill='none' stroke='%23333' stroke-width='1.4' stroke-linejoin='round'%3E%3Cg transform='rotate(-45 11 11)'%3E%3Crect x='8.5' y='8.5' width='5' height='5'/%3E%3Crect x='1.5' y='9' width='5' height='4'/%3E%3Crect x='15.5' y='9' width='5' height='4'/%3E%3Cpath d='M6.5 11h2m5 0h2M11 13.5V16m-2.5 1.5a2.5 2.5 0 0 0 5 0'/%3E%3C/g%3E%3C/svg%3E");
}

.map :deep(.satellite-toggle.satellite-enabled .maplibregl-ctrl-icon) {
  background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='22' height='22' viewBox='0 0 22 22' fill='none' stroke='%2333b5e5' stroke-width='1.4' stroke-linejoin='round'%3E%3Cg transform='rotate(-45 11 11)'%3E%3Crect x='8.5' y='8.5' width='5' height='5'/%3E%3Crect x='1.5' y='9' width='5' height='4'/%3E%3Crect x='15.5' y='9' width='5' height='4'/%3E%3Cpath d='M6.5 11h2m5 0h2M11 13.5V16m-2.5 1.5a2.5 2.5 0 0 0 5 0'/%3E%3C/g%3E%3C/svg%3E");
}

/* the export button's icon: a dashed box and a pointer */
.area-icon {
  width: 24px;
  height: 24px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linejoin: round;
}

.area-icon path {
  fill: currentColor;
}

.map :deep(.maplibregl-popup-close-button:hover) {
  background: #f1f5f9;
  color: #0f172a;
}

.notice {
  position: fixed;
  z-index: 11;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  max-width: 360px;
  padding: 20px 24px;
  border-radius: 16px;
  background: white;
  color: #334155;
  font-size: 15px;
  line-height: 1.5;
  text-align: center;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

.notice.bottom {
  top: auto;
  bottom: 40px;
  transform: translateX(-50%);
  width: max-content;
  max-width: calc(100vw - 40px);
  padding: 12px 18px;
  font-size: 14px;
}

.notice.top {
  top: 80px;
  bottom: auto;
  transform: translateX(-50%);
  width: max-content;
  max-width: calc(100vw - 40px);
  padding: 10px 16px;
  font-size: 14px;
}

.notice .dismiss {
  margin-left: 6px;
  padding: 0 4px;
  border: none;
  background: none;
  color: #64748b;
  font: inherit;
  cursor: pointer;
}

.notice a {
  color: #ea580c;
  font-weight: 600;
}

.failed {
  color: #b91c1c;
}
</style>

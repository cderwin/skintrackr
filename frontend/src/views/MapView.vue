<script setup lang="ts">
// A full-page 3D terrain map (MapTiler Outdoor and terrain-RGB DEM), centered on the Washington Cascades, with the
// athlete's activity tracks (their PMTiles archive) colored by sport. Clicking a track opens its details; the
// filter panel narrows tracks by sport and date.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre looks for its worker next to its own module, which the bundle doesn't keep; emit it as an asset
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { Protocol } from 'pmtiles'
import { api, SignedOutError, TRACKS_URL, useSignedIn, type ActivitySummary, type MapActivities } from '@/api'
import { filterExpression, matches } from '@/mapFilters'
import { settings } from '@/settings'
import { SPORT_COLOR_EXPRESSION } from '@/sports'
import ActivityPopup from '@/components/ActivityPopup.vue'
import FloatingButton from '@/components/FloatingButton.vue'
import MapFilters from '@/components/MapFilters.vue'

const MAPTILER_KEY = import.meta.env.VITE_MAPTILER_KEY ?? ''
const CASCADES: [number, number] = [-121.4, 47.9]
// the archive's layer (tracks.LAYER on the server)
const LAYER = 'tracks'
const HIT_LAYER = 'tracks-hit'
// pixels around a click that count as on a track
const HIT_SLOP = 6
// activities listed in a popup where tracks overlap
const POPUP_LIMIT = 5

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
let map: maplibregl.Map | null = null
let popup: maplibregl.Popup | null = null

const byId = computed(() => new Map(data.value?.activities.map((a) => [a.id, a]) ?? []))
const onMap = computed(() =>
  (data.value?.on_map ?? []).flatMap((id) => {
    const activity = byId.value.get(id)
    return activity ? [activity] : []
  }),
)

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
    resolveMapLoaded()
  })
})

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

  m.on('mouseenter', HIT_LAYER, () => (m.getCanvas().style.cursor = 'pointer'))
  m.on('mouseleave', HIT_LAYER, () => (m.getCanvas().style.cursor = ''))
  m.on('click', (event) => {
    const { x, y } = event.point
    const hits = m.queryRenderedFeatures(
      [
        [x - HIT_SLOP, y - HIT_SLOP],
        [x + HIT_SLOP, y + HIT_SLOP],
      ],
      { layers: [HIT_LAYER] },
    )
    const ids = new Set(hits.map((feature) => String(feature.properties.id)))
    const found = [...ids]
      .flatMap((id) => byId.value.get(id) ?? [])
      .sort((a, b) => b.start_date_local.localeCompare(a.start_date_local))
      .slice(0, POPUP_LIMIT)
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
  applyFilters()
}

function applyFilters(): void {
  if (!map?.getLayer(HIT_LAYER)) return
  const filter = filterExpression(settings.mapFilters)
  for (const id of [HIT_LAYER, 'tracks-casing', 'tracks']) map.setFilter(id, filter)
  const selected: maplibregl.FilterSpecification = ['all', filter, ['==', ['get', 'id'], selectedId.value]]
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
    <MapFilters v-if="data?.on_map.length" :activities="onMap" :omitted="data.map_tracks_omitted" />
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

.notice a {
  color: #ea580c;
  font-weight: 600;
}

.failed {
  color: #b91c1c;
}
</style>

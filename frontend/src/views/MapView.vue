<script setup lang="ts">
// A full-page 3D terrain map (MapTiler Outdoor and terrain-RGB DEM), centered on the Washington Cascades.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre looks for its worker next to its own module, which the bundle doesn't keep; emit it as an asset
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { settings } from '@/settings'
import FloatingButton from '@/components/FloatingButton.vue'

const MAPTILER_KEY = import.meta.env.VITE_MAPTILER_KEY ?? ''
const CASCADES: [number, number] = [-121.4, 47.9]

maplibregl.setWorkerUrl(workerUrl)

const container = ref<HTMLDivElement | null>(null)
let map: maplibregl.Map | null = null

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
  })
})

onBeforeUnmount(() => map?.remove())
</script>

<template>
  <div ref="container" class="map" />
  <div v-if="!MAPTILER_KEY" class="missing-key">
    The map needs a MapTiler key: set <code>VITE_MAPTILER_KEY</code> and rebuild the frontend.
  </div>
  <FloatingButton side="left" to="/" label="Back">←</FloatingButton>
</template>

<style scoped>
.map {
  position: fixed;
  inset: 0;
  z-index: 10;
}

.missing-key {
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
</style>

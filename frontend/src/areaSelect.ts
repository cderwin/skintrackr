// Drawing an area on the map with two clicks: the first drops a corner, then (with a pointer) the rectangle follows it
// until the second click finishes it. The rectangle stays drawn, dashed and draped over the terrain, until cleared.
import type { FilterSpecification, GeoJSONSource, LngLat, Map, MapMouseEvent } from 'maplibre-gl'
import type { BBox } from '@/api'

const SOURCE = 'area-select'
const COLOR = '#ea580c'

// GeoJSON, as MapLibre takes it (its types come with MapLibre)
type Data = Parameters<GeoJSONSource['setData']>[0]

const EMPTY: Data = { type: 'FeatureCollection', features: [] }

function box(a: LngLat, b: LngLat): BBox {
  return [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)]
}

function rectangle([west, south, east, north]: BBox): Data {
  const ring = [
    [west, south],
    [east, south],
    [east, north],
    [west, north],
    [west, south],
  ]
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }
}

export class AreaSelect {
  /** Whether the next clicks draw an area. */
  drawing = false
  private first: { lngLat: LngLat; x: number; y: number } | null = null

  constructor(
    private readonly map: Map,
    private readonly onSelect: (bbox: BBox) => void,
  ) {}

  /** Start drawing (clearing any area drawn before). */
  start(): void {
    this.ensureLayers()
    this.stopListening()
    this.show(EMPTY)
    this.first = null
    this.drawing = true
    this.map.getCanvas().style.cursor = 'crosshair'
    this.map.on('click', this.click)
    this.map.on('mousemove', this.move)
  }

  /** Stop drawing, and remove the area from the map. */
  cancel(): void {
    this.stopListening()
    if (this.map.getSource(SOURCE)) this.show(EMPTY)
  }

  private stopListening(): void {
    this.drawing = false
    this.first = null
    this.map.off('click', this.click)
    this.map.off('mousemove', this.move)
    this.map.getCanvas().style.cursor = ''
  }

  private readonly click = (event: MapMouseEvent): void => {
    if (!this.first) {
      this.first = { lngLat: event.lngLat, x: event.point.x, y: event.point.y }
      this.show({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: event.lngLat.toArray() } })
      return
    }
    // a second click on the first corner would make an empty area
    if (Math.hypot(event.point.x - this.first.x, event.point.y - this.first.y) < 4) return
    const bbox = box(this.first.lngLat, event.lngLat)
    this.stopListening()
    this.show(rectangle(bbox))
    this.onSelect(bbox)
  }

  private readonly move = (event: MapMouseEvent): void => {
    if (this.first) this.show(rectangle(box(this.first.lngLat, event.lngLat)))
  }

  private show(data: Data): void {
    ;(this.map.getSource(SOURCE) as GeoJSONSource | undefined)?.setData(data)
  }

  private ensureLayers(): void {
    if (this.map.getSource(SOURCE)) return
    this.map.addSource(SOURCE, { type: 'geojson', data: EMPTY })
    const polygons: FilterSpecification = ['==', ['geometry-type'], 'Polygon']
    this.map.addLayer({
      id: `${SOURCE}-fill`,
      type: 'fill',
      source: SOURCE,
      filter: polygons,
      paint: { 'fill-color': COLOR, 'fill-opacity': 0.08 },
    })
    this.map.addLayer({
      id: `${SOURCE}-line`,
      type: 'line',
      source: SOURCE,
      filter: polygons,
      paint: { 'line-color': COLOR, 'line-width': 2, 'line-dasharray': [3, 2] },
    })
    this.map.addLayer({
      id: `${SOURCE}-corner`,
      type: 'circle',
      source: SOURCE,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: { 'circle-radius': 5, 'circle-color': COLOR, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 },
    })
  }
}

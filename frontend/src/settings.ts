// The viewer's settings (vert goal, units, map filters), kept in localStorage: per browser, and optional.
import { reactive, watch } from 'vue'

export type Units = 'ft' | 'm'

const STORAGE_KEY = 'skintrackr.settings'
const METERS_PER_FOOT = 0.3048
const METERS_PER_MILE = 1609.344

/** Which period the map shows: every activity, the last 12 months, a calendar year ("2025"), or a custom range. */
export type MapPeriod = 'all' | 'last12' | 'custom' | `${number}`

export interface MapFilters {
  /** Activity types shown; empty shows every type. */
  sports: string[]
  period: MapPeriod
  /** The custom range's dates (YYYY-MM-DD); empty is open-ended. */
  from: string
  to: string
}

interface Settings {
  /** Yearly vert goal, in meters (shown in `units`). */
  goalM: number
  units: Units
  mapFilters: MapFilters
  /** How many activities were off the map (over its limit) when its warning was dismissed. */
  mapLimitDismissed: number
}

const DEFAULTS: Settings = {
  goalM: 500_000 * METERS_PER_FOOT,
  units: 'ft',
  mapFilters: { sports: [], period: 'all', from: '', to: '' },
  mapLimitDismissed: 0,
}

const DATE = /^\d{4}-\d{2}-\d{2}$/

function loadMapFilters(saved: Partial<MapFilters> | undefined): MapFilters {
  const period = saved?.period
  const valid = period === 'all' || period === 'last12' || period === 'custom' || /^\d{4}$/.test(String(period))
  const date = (value: unknown) => (typeof value === 'string' && DATE.test(value) ? value : '')
  return {
    sports: Array.isArray(saved?.sports) ? saved.sports.filter((sport) => typeof sport === 'string') : [],
    period: valid ? (period as MapPeriod) : 'all',
    from: date(saved?.from),
    to: date(saved?.to),
  }
}

function load(): Settings {
  try {
    const saved: Partial<Settings> = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    return {
      goalM: typeof saved.goalM === 'number' && saved.goalM > 0 ? saved.goalM : DEFAULTS.goalM,
      units: saved.units === 'm' ? 'm' : DEFAULTS.units,
      mapFilters: loadMapFilters(saved.mapFilters),
      mapLimitDismissed:
        typeof saved.mapLimitDismissed === 'number' && saved.mapLimitDismissed > 0 ? saved.mapLimitDismissed : 0,
    }
  } catch {
    return { ...DEFAULTS, mapFilters: { ...DEFAULTS.mapFilters, sports: [] } }
  }
}

export const settings = reactive<Settings>(load())

watch(settings, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // private mode or blocked storage: settings last for this visit only
  }
})

/** Meters in the chosen units. */
export function toUnits(meters: number): number {
  return settings.units === 'ft' ? meters / METERS_PER_FOOT : meters
}

/** A value in the chosen units, in meters. */
export function fromUnits(value: number): number {
  return settings.units === 'ft' ? value * METERS_PER_FOOT : value
}

/** Vert in the chosen units, e.g. "412,300 ft", or "412K ft" when compact. */
export function formatVert(meters: number, compact = false): string {
  const number = new Intl.NumberFormat(undefined, {
    notation: compact ? 'compact' : 'standard',
    maximumFractionDigits: compact ? 1 : 0,
  })
  return `${number.format(toUnits(meters))} ${settings.units}`
}

/** A distance in the chosen units' scale (km or mi), e.g. "12.4 mi". */
export function formatDistance(meters: number): string {
  const imperial = settings.units === 'ft'
  const value = meters / (imperial ? METERS_PER_MILE : 1000)
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)} ${imperial ? 'mi' : 'km'}`
}

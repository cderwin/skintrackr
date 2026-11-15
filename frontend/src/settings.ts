// The viewer's settings (vert goal and units), kept in localStorage: per browser, and optional.
import { reactive, watch } from 'vue'

export type Units = 'ft' | 'm'

const STORAGE_KEY = 'skintrackr.settings'
const METERS_PER_FOOT = 0.3048

interface Settings {
  /** Yearly vert goal, in meters (shown in `units`). */
  goalM: number
  units: Units
}

const DEFAULTS: Settings = { goalM: 500_000 * METERS_PER_FOOT, units: 'ft' }

function load(): Settings {
  try {
    const saved: Partial<Settings> = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    return {
      goalM: typeof saved.goalM === 'number' && saved.goalM > 0 ? saved.goalM : DEFAULTS.goalM,
      units: saved.units === 'm' ? 'm' : DEFAULTS.units,
    }
  } catch {
    return { ...DEFAULTS }
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

// Sport labels and the map's colors: one color per family of intervals.icu activity types.
import type { ExpressionSpecification } from 'maplibre-gl'

interface Family {
  name: string
  color: string
  types: string[]
}

const FAMILIES: Family[] = [
  { name: 'Ski touring', color: '#e11d48', types: ['BackcountrySki'] },
  { name: 'Nordic', color: '#7c3aed', types: ['NordicSki', 'Rollerski', 'VirtualSki'] },
  { name: 'Alpine', color: '#2563eb', types: ['AlpineSki', 'Snowboard'] },
  { name: 'On foot', color: '#b45309', types: ['Run', 'TrailRun', 'Hike', 'Walk', 'Snowshoe', 'VirtualRun'] },
  {
    name: 'Ride',
    color: '#0d9488',
    types: [
      'Ride',
      'MountainBikeRide',
      'GravelRide',
      'EBikeRide',
      'EMountainBikeRide',
      'Cyclocross',
      'TrackRide',
      'Velomobile',
      'Handcycle',
      'VirtualRide',
    ],
  },
]
const OTHER_COLOR = '#475569'

/** An activity type for people: `BackcountrySki` → "Backcountry ski". */
export function sportLabel(type: string): string {
  if (!type) return 'Activity'
  const words = type.replace(/([a-z])([A-Z])/g, '$1 $2')
  return words.charAt(0) + words.slice(1).toLowerCase()
}

/** The color an activity type is drawn in. */
export function sportColor(type: string): string {
  return FAMILIES.find((family) => family.types.includes(type))?.color ?? OTHER_COLOR
}

/** Each activity type's label and color, for an export to draw tracks as the map does. */
export function sportStyles(types: Iterable<string>): Record<string, { label: string; color: string }> {
  return Object.fromEntries([...new Set(types)].map((type) => [type, { label: sportLabel(type), color: sportColor(type) }]))
}

/** A MapLibre expression coloring features by their `type` property, like `sportColor`. */
export const SPORT_COLOR_EXPRESSION: ExpressionSpecification = [
  'match',
  ['get', 'type'],
  ...FAMILIES.flatMap((family) => [family.types, family.color] as [string[], string]),
  OTHER_COLOR,
] as unknown as ExpressionSpecification

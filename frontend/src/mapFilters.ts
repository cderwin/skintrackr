// The map's filters: one date range and sport test, applied to the tracks (a MapLibre filter) and to the
// activity list (the panel's counts), so the two always agree.
import type { ExpressionSpecification } from 'maplibre-gl'
import type { ActivitySummary, TrackExportRequest } from '@/api'
import type { MapFilters } from '@/settings'
import { sportLabel } from '@/sports'

const OPEN_END = 99_999_999

/** A local date ("YYYY-MM-DD…") as YYYYMMDD, the tracks' `date` property; 0 if it has none. */
export function dateNumber(value: string): number {
  const digits = value.slice(0, 10).replaceAll('-', '')
  return /^\d{8}$/.test(digits) ? Number(digits) : 0
}

function localDate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** The filters' period as an inclusive [from, to] range of YYYYMMDD numbers. */
export function dateRange(filters: MapFilters, today = new Date()): [number, number] {
  switch (filters.period) {
    case 'all':
      return [0, OPEN_END]
    case 'last12': {
      const yearAgo = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate())
      return [dateNumber(localDate(yearAgo)), OPEN_END]
    }
    case 'custom':
      return [dateNumber(filters.from), filters.to ? dateNumber(filters.to) : OPEN_END]
    default:
      return [Number(filters.period) * 10_000 + 101, Number(filters.period) * 10_000 + 1231]
  }
}

/** Whether an activity passes the filters. */
export function matches(activity: ActivitySummary, filters: MapFilters, today = new Date()): boolean {
  const [from, to] = dateRange(filters, today)
  const date = dateNumber(activity.start_date_local)
  return (!filters.sports.length || filters.sports.includes(activity.type)) && date >= from && date <= to
}

/** The filters as a MapLibre expression on the tracks' `type` and `date` properties. */
export function filterExpression(filters: MapFilters, today = new Date()): ExpressionSpecification {
  const [from, to] = dateRange(filters, today)
  const conditions: ExpressionSpecification[] = [
    ['>=', ['get', 'date'], from],
    ['<=', ['get', 'date'], to],
  ]
  if (filters.sports.length) conditions.push(['in', ['get', 'type'], ['literal', filters.sports]])
  return ['all', ...conditions]
}

/** Whether any filter narrows the map. */
export function isFiltered(filters: MapFilters): boolean {
  return filters.sports.length > 0 || filters.period !== 'all'
}

/** The filters as an export request's, so an export takes exactly the tracks the map shows. */
export function exportFilters(
  filters: MapFilters,
  today = new Date(),
): Pick<TrackExportRequest, 'sports' | 'date_from' | 'date_to'> {
  const [from, to] = dateRange(filters, today)
  return { sports: [...filters.sports], date_from: from, date_to: to }
}

/** The filters in words, e.g. "Backcountry ski · 2025"; empty if nothing is filtered. */
export function describeFilters(filters: MapFilters): string {
  const period =
    filters.period === 'all'
      ? ''
      : filters.period === 'last12'
        ? 'Last 12 months'
        : filters.period === 'custom'
          ? `${filters.from || '…'} – ${filters.to || '…'}`
          : filters.period
  return [...filters.sports.map(sportLabel), period].filter(Boolean).join(' · ')
}

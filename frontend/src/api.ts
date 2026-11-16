// The server's API. The browser is signed in by an HttpOnly session cookie, sent with every request.
import { onMounted, ref, type Ref } from 'vue'

export class SignedOutError extends Error {
  constructor() {
    super('Not signed in')
  }
}

export interface YearVert {
  year: number
  vert_m: number
}

export interface VertStats {
  /** The date the totals run to (YYYY-MM-DD), in each year. */
  as_of: string
  /** This year first, then previous years. */
  years: YearVert[]
  /** Activities imported (0: nothing imported yet). */
  activities: number
}

export interface ImportStatus {
  state: 'idle' | 'running' | 'done' | 'failed'
  /** Activities indexed (enough for stats). */
  activities: number
  /** Activities archived in full (details and streams), out of tracks_total. */
  tracks_done: number
  tracks_total: number
  /** Activities intervals.icu doesn't serve through its API (synced from Strava). */
  skipped: number
  /** Activities with a track left off the map when the import finished (beyond MAP_TRACKS_LIMIT). */
  map_tracks_omitted: number
  error: string
}

/** One activity of the athlete's index. */
export interface ActivitySummary {
  id: string
  name: string
  /** intervals.icu's activity type, e.g. `BackcountrySki`. */
  type: string
  /** Local start, YYYY-MM-DDTHH:MM:SS. */
  start_date_local: string
  total_elevation_gain: number
  distance: number
  moving_time: number
}

export interface MapActivities {
  /** Every activity, in no particular order. */
  activities: ActivitySummary[]
  /** The ids on the map (in its tracks), newest first. */
  on_map: string[]
  /** Older activities with a track that are left off the map. */
  map_tracks_omitted: number
}

export interface Athlete {
  athlete_id: string
  name: string
}

/** How far back, by start date, the server syncs automatically (its SYNC_WINDOW_DAYS default). */
export const SYNC_WINDOW_DAYS = 30

/** How many tracks the map holds, the most recent (the server's MAP_TRACKS_LIMIT). */
export const MAP_TRACKS_LIMIT = 3000

/** The athlete's map tracks, for MapLibre through the PMTiles protocol (same origin, so the cookie goes too). */
export const TRACKS_URL = '/api/tracks.pmtiles'

/** Where athletes find their intervals.icu API key. */
export const INTERVALS_SETTINGS_URL = 'https://intervals.icu/settings'

/** An activity's page on intervals.icu. */
export function intervalsActivityUrl(id: string): string {
  return `https://intervals.icu/activities/${encodeURIComponent(id)}`
}

async function call<T>(method: 'GET' | 'POST', path: string, payload?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    ...(payload === undefined
      ? {}
      : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  })
  if (response.status === 401 || response.status === 403) {
    throw new SignedOutError()
  }
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(body.message ?? `Request failed (${response.status})`)
  }
  return body as T
}

/** Today in the browser's timezone, as YYYY-MM-DD. */
function localDate(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export const api = {
  /** Resolves if the browser is signed in. */
  verify: () => call<{ athlete_id: string }>('POST', '/token/verify'),
  /** Connect an intervals.icu account by its API key, and sign the browser in. */
  connect: (apiKey: string) => call<Athlete>('POST', '/auth/connect', { api_key: apiKey }),
  vert: () => call<VertStats>('GET', `/api/stats/vert?today=${localDate()}`),
  importStatus: () => call<ImportStatus>('GET', '/api/import'),
  startImport: () => call<ImportStatus>('POST', '/api/import'),
  activities: () => call<MapActivities>('GET', '/api/activities'),
  logout: () => call<{ ok: boolean }>('POST', '/auth/logout'),
  /** Delete everything stored for the athlete, and sign out. */
  disconnect: () => call<{ ok: boolean }>('POST', '/auth/disconnect'),
}

/** Whether the browser is signed in: null until the server has answered. */
export function useSignedIn(): Ref<boolean | null> {
  const signedIn = ref<boolean | null>(null)
  onMounted(async () => {
    try {
      await api.verify()
      signedIn.value = true
    } catch {
      signedIn.value = false
    }
  })
  return signedIn
}

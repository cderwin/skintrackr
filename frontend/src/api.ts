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

/** How an upload of the athlete's Strava export matched their activities. */
export interface StravaImport {
  /** Activities in the export. */
  strava_activities: number
  /** The athlete's activities. */
  activities: number
  /** Their activities paired with one in the export (they show its name and Strava id). */
  matched: number
}

/** One activity of the athlete's index. */
export interface ActivitySummary {
  id: string
  /** Its Strava name, if it matched one in the athlete's Strava export; else intervals.icu's. */
  name: string
  /** intervals.icu's activity type, e.g. `BackcountrySki`. */
  type: string
  /** Local start, YYYY-MM-DDTHH:MM:SS. */
  start_date_local: string
  total_elevation_gain: number
  distance: number
  moving_time: number
  /** The activity on Strava, once intervals.icu or the athlete's Strava export has matched it with one; else empty. */
  strava_id: string
}

export interface MapActivities {
  /** Every activity, in no particular order. */
  activities: ActivitySummary[]
  /** The ids on the map (in its tracks), newest first. */
  on_map: string[]
  /** Older activities with a track that are left off the map. */
  map_tracks_omitted: number
}

/** An area: [west, south, east, north], in degrees. */
export type BBox = [number, number, number, number]

/** A CalTopo export of the map's tracks in an area (the preview reads only the area and the filters). */
export interface TrackExportRequest {
  bbox: BBox
  /** Activity types; empty is every type. */
  sports: string[]
  /** Inclusive local start dates, as YYYYMMDD. */
  date_from: number
  date_to: number
  /** Whole tracks that meet the area, or only their pieces inside it. */
  mode: 'whole' | 'clip'
  /** The CalTopo folder the tracks go in; null for none. */
  folder: string | null
  units: 'ft' | 'm'
  /** Each activity type's label and color (`sportStyles`). */
  styles: Record<string, { label: string; color: string }>
  stroke_width: number
  /** The tracks to export (ticked in the dialog), in place of the filters' search. */
  activity_ids?: string[]
}

export interface ExportPreview {
  /** The tracks the export would hold, newest first. */
  activity_ids: string[]
  /** The most tracks an export may hold. */
  limit: number
}

export interface ExportFile {
  blob: Blob
  filename: string
  /** Tracks in the file. */
  count: number
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

/** An activity's GPX download (the server's export, authenticated by the session cookie). */
export function gpxExportUrl(id: string): string {
  return `/api/activity/${encodeURIComponent(id)}/export`
}

/** An activity's page on Strava, from its Strava id. */
export function stravaActivityUrl(id: string): string {
  return `https://www.strava.com/activities/${encodeURIComponent(id)}`
}

/** A request to the server, with a JSON payload or a file; signed-out answers and errors (`{"message"}`) are thrown. */
async function send(method: 'GET' | 'POST', path: string, payload?: unknown): Promise<Response> {
  const body =
    payload === undefined
      ? {}
      : payload instanceof Blob
        ? { headers: { 'Content-Type': payload.type || 'application/octet-stream' }, body: payload }
        : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }
  const response = await fetch(path, { method, credentials: 'same-origin', ...body })
  if (response.status === 401 || response.status === 403) {
    throw new SignedOutError()
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.message ?? `Request failed (${response.status})`)
  }
  return response
}

async function call<T>(method: 'GET' | 'POST', path: string, payload?: unknown): Promise<T> {
  const response = await send(method, path, payload)
  return (await response.json().catch(() => ({}))) as T
}

/** A file the server sent for download, with the name its `Content-Disposition` gives. */
async function download(response: Response, fallback: string): Promise<{ blob: Blob; filename: string }> {
  const filename = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? fallback
  return { blob: await response.blob(), filename }
}

/** Hand a file to the browser to save. */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
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
  /** Match the athlete's activities with their Strava export (its `activities.csv`), for their Strava names. */
  stravaImport: (activitiesCsv: Blob) => call<StravaImport>('POST', '/api/strava/import', activitiesCsv),
  /** The tracks an export of an area would hold. */
  exportPreview: (request: TrackExportRequest) => call<ExportPreview>('POST', '/api/activities/export/preview', request),
  /** The tracks in an area, as a CalTopo GeoJSON file. */
  exportTracks: async (request: TrackExportRequest): Promise<ExportFile> => {
    const response = await send('POST', '/api/activities/export', request)
    const count = Number(response.headers.get('X-Track-Count') ?? 0)
    return { ...(await download(response, 'skintrackr-tracks.json')), count }
  },
  /** The map tracks' version (their ETag, which every rebuild changes); null before the athlete has any. */
  tracksVersion: async (): Promise<string | null> => {
    const response = await fetch(TRACKS_URL, { credentials: 'same-origin', headers: { Range: 'bytes=0-0' } }).catch(
      () => null,
    )
    return response?.ok ? response.headers.get('ETag') : null
  },
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

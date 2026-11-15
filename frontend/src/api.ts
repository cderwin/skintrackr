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
}

export interface StravaToken {
  access_token: string
  expires_at: number
}

async function call<T>(method: 'GET' | 'POST', path: string): Promise<T> {
  const response = await fetch(path, { method, credentials: 'same-origin' })
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
  verify: () => call<{ athlete_id: number }>('POST', '/token/verify'),
  vert: () => call<VertStats>('GET', `/api/stats/vert?today=${localDate()}`),
  stravaToken: () => call<StravaToken>('GET', '/api/strava-token'),
  logout: () => call<{ ok: boolean }>('POST', '/oauth2/logout'),
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

// The athlete's bulk import (Re-sync), shared by everything that shows it: one status, polled while an import runs
// and anything showing it is mounted, so starting it in one place updates the others.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, type ImportStatus } from '@/api'
import { settings } from '@/settings'

const POLL_MS = 3000

const status = ref<ImportStatus | null>(null)
const error = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let users = 0

async function refresh(request = api.importStatus): Promise<void> {
  clearTimeout(timer)
  try {
    status.value = await request()
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
  // another refresh may have finished meanwhile: keep a single poll
  clearTimeout(timer)
  if (status.value?.state === 'running' && users) timer = setTimeout(refresh, POLL_MS)
}

/** Start (or resume) an import. */
export function startImport(): Promise<void> {
  return refresh(api.startImport)
}

/** The tracks archived so far, out of all (0 before they're listed). */
export const importProgress = computed(() => {
  const s = status.value
  return s && s.tracks_total ? s.tracks_done / s.tracks_total : 0
})

/** One line on where the import is. */
export const importSummary = computed(() => {
  const s = status.value
  switch (s?.state) {
    case 'idle':
      return 'Import your intervals.icu history to see it on the map and in your stats.'
    case 'running':
      return s.tracks_total
        ? `${s.activities} activities imported · tracks ${s.tracks_done} of ${s.tracks_total}`
        : 'Listing your activities…'
    case 'done':
      return `All ${s.activities} activities imported.`
    case 'failed':
      return `Import stopped: ${s.error}`
  }
  return ''
})

/** After a finished import over the map's limit, until dismissed (it comes back if more are left off): how many. */
export const mapLimitWarning = computed(() => {
  const s = status.value
  return s?.state === 'done' && s.map_tracks_omitted > settings.mapLimitDismissed ? s.map_tracks_omitted : 0
})

export function dismissMapLimit(): void {
  settings.mapLimitDismissed = status.value?.map_tracks_omitted ?? 0
}

/** The import's status and last error, loaded when the component mounts and kept current while it runs. */
export function useImport() {
  onMounted(() => {
    users++
    void refresh()
  })
  onBeforeUnmount(() => {
    if (!--users) clearTimeout(timer)
  })
  return { status, error }
}

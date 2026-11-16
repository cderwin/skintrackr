<script setup lang="ts">
// Bulk import (Re-sync) of the athlete's intervals.icu activities: start it, and follow its progress while it runs.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, MAP_TRACKS_LIMIT, SYNC_WINDOW_DAYS, type ImportStatus } from '@/api'
import { settings } from '@/settings'

const POLL_MS = 3000

const status = ref<ImportStatus | null>(null)
const error = ref('')
let timer: ReturnType<typeof setTimeout> | undefined

async function refresh(request = api.importStatus): Promise<void> {
  clearTimeout(timer)
  try {
    status.value = await request()
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
  if (status.value?.state === 'running') timer = setTimeout(refresh, POLL_MS)
}

onMounted(() => refresh())
onBeforeUnmount(() => clearTimeout(timer))

// after a finished import over the map's limit, until dismissed (it comes back if more are left off)
const mapLimitWarning = computed(() => {
  const s = status.value
  return s?.state === 'done' && s.map_tracks_omitted > settings.mapLimitDismissed ? s.map_tracks_omitted : 0
})

function dismissMapLimit(): void {
  settings.mapLimitDismissed = status.value?.map_tracks_omitted ?? 0
}

const progress = computed(() => {
  const s = status.value
  return s && s.tracks_total ? s.tracks_done / s.tracks_total : 0
})

const summary = computed(() => {
  const s = status.value
  if (!s) return ''
  switch (s.state) {
    case 'idle':
      return 'Import your intervals.icu history to see your season stats.'
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
</script>

<template>
  <section v-if="status" class="import">
    <p class="summary" :class="{ failed: status.state === 'failed' }">{{ summary }}</p>
    <template v-if="status.state === 'running' && status.tracks_total">
      <div class="bar"><span :style="{ width: `${progress * 100}%` }" /></div>
      <small>Stats are ready. Full tracks download in the background.</small>
    </template>
    <small v-if="status.skipped">
      {{ status.skipped }} activities synced from Strava were skipped: intervals.icu doesn't share those through its API.
    </small>
    <button
      v-else
      class="small-button"
      :class="{ 'button-primary': status.state === 'idle' }"
      type="button"
      @click="refresh(api.startImport)"
    >
      {{ status.state === 'idle' ? 'Import my activities' : status.state === 'failed' ? 'Resume import' : 'Re-sync' }}
    </button>
    <small v-if="status.state === 'done'">
      New activities and edits from the last {{ SYNC_WINDOW_DAYS }} days sync automatically. Re-sync to pick up
      changes to anything older.
    </small>
    <div v-if="mapLimitWarning" class="warning" role="alert">
      <p>
        Your map shows your {{ MAP_TRACKS_LIMIT.toLocaleString() }} most recent activities.
        {{ mapLimitWarning.toLocaleString() }} older {{ mapLimitWarning === 1 ? 'activity is' : 'activities are' }}
        still synced and count in your stats, but {{ mapLimitWarning === 1 ? "isn't" : "aren't" }} drawn on the map.
      </p>
      <button type="button" class="dismiss" aria-label="Dismiss" @click="dismissMapLimit">✕</button>
    </div>
    <small v-if="error" class="failed">{{ error }}</small>
  </section>
</template>

<style scoped>
.import {
  margin-top: 16px;
  padding: 16px;
  background: #f1f5f9;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  color: #334155;
}

.summary {
  line-height: 1.5;
}

small {
  font-size: 12px;
  color: #64748b;
}

.failed {
  color: #b91c1c;
}

.warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid #fcd34d;
  border-radius: 10px;
  background: #fffbeb;
  color: #92400e;
  text-align: left;
  line-height: 1.45;
}

.warning p {
  margin: 0;
}

.dismiss {
  flex: none;
  padding: 0 2px;
  border: none;
  background: none;
  color: #92400e;
  font-size: 14px;
  cursor: pointer;
}

.bar {
  width: 100%;
  height: 8px;
  background: #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
}

.bar span {
  display: block;
  height: 100%;
  background: linear-gradient(135deg, #ff6b35 0%, #f7931e 100%);
  transition: width 0.5s ease;
}
</style>

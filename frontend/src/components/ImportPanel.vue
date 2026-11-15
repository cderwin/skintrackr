<script setup lang="ts">
// Bulk import of the athlete's Strava activities: start it, and follow its progress while it runs.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, type ImportStatus } from '@/api'

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

const progress = computed(() => {
  const s = status.value
  return s && s.tracks_total ? s.tracks_done / s.tracks_total : 0
})

const summary = computed(() => {
  const s = status.value
  if (!s) return ''
  switch (s.state) {
    case 'idle':
      return 'Import your Strava history to see your season stats.'
    case 'running':
      return s.tracks_total
        ? `${s.activities} activities imported · tracks ${s.tracks_done} of ${s.tracks_total}`
        : 'Listing your activities…'
    case 'done':
      return `All ${s.activities} activities imported. New ones sync automatically.`
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
      <small>Stats are ready. Full tracks download in the background, within Strava's rate limits.</small>
    </template>
    <button
      v-else
      class="small-button"
      :class="{ 'button-primary': status.state === 'idle' }"
      type="button"
      @click="refresh(api.startImport)"
    >
      {{ status.state === 'idle' ? 'Import my Strava activities' : status.state === 'failed' ? 'Resume import' : 'Re-sync' }}
    </button>
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

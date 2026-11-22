<script setup lang="ts">
// The map's import notice, at its bottom: the first import, progress while one runs (wherever it was started), a
// failed one, and the map's track limit after one. Emits `finished` when an import finishes, so the map reloads.
import { computed, watch } from 'vue'
import { X } from '@lucide/vue'
import { MAP_TRACKS_LIMIT } from '@/api'
import {
  dismissMapLimit,
  importProgress as progress,
  importSummary as summary,
  mapLimitWarning,
  startImport,
  useImport,
} from '@/importStatus'

const props = defineProps<{
  /** Whether the map has tracks to show. */
  hasTracks: boolean
}>()
const emit = defineEmits<{ finished: [] }>()

const { status } = useImport()

watch(
  () => status.value?.state,
  (now, before) => {
    if (before === 'running' && now === 'done') emit('finished')
  },
)

const kind = computed(() => {
  const state = status.value?.state
  if (state === 'running' || state === 'failed') return state
  if (!props.hasTracks) return state === 'idle' ? 'idle' : 'empty'
  return mapLimitWarning.value ? 'limit' : null
})
</script>

<template>
  <div v-if="kind" class="import-notice" :role="kind === 'failed' ? 'alert' : 'status'">
    <template v-if="kind === 'running'">
      <p>{{ summary }}</p>
      <div v-if="status?.tracks_total" class="bar"><span :style="{ width: `${progress * 100}%` }" /></div>
    </template>

    <template v-else-if="kind === 'failed'">
      <p class="failed">{{ summary }}</p>
      <button class="small-button" type="button" @click="startImport">Resume import</button>
    </template>

    <template v-else-if="kind === 'idle'">
      <p><strong>No tracks yet.</strong> {{ summary }}</p>
      <button class="small-button button-primary" type="button" @click="startImport">Import my activities</button>
    </template>

    <p v-else-if="kind === 'empty'">No tracks yet: none of your activities has a GPS track to draw.</p>

    <p v-else class="limit">
      Your map shows your {{ MAP_TRACKS_LIMIT.toLocaleString() }} most recent activities.
      {{ mapLimitWarning.toLocaleString() }} older {{ mapLimitWarning === 1 ? 'activity is' : 'activities are' }}
      still synced and count in your stats, but {{ mapLimitWarning === 1 ? "isn't" : "aren't" }} drawn.
      <button type="button" class="dismiss" aria-label="Dismiss" @click="dismissMapLimit"><X class="icon-em" /></button>
    </p>
  </div>
</template>

<style scoped>
.import-notice {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

.bar {
  width: 240px;
  max-width: 100%;
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

.failed {
  color: #b91c1c;
}

.limit {
  max-width: 420px;
}

.dismiss {
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  margin-left: 4px;
  padding: 0 2px;
  border: none;
  background: none;
  color: #64748b;
  cursor: pointer;
}
</style>

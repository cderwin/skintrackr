<script setup lang="ts">
// The CalTopo export dialog, for an area drawn on the map: counts the tracks in it that pass the map's filters
// (reported with `preview`, so the map highlights them), and downloads them as a GeoJSON file CalTopo imports.
import { computed, ref, watch } from 'vue'
import { api, MAP_TRACKS_LIMIT, saveFile, SignedOutError, type BBox, type TrackExportRequest } from '@/api'
import { describeFilters, exportFilters } from '@/mapFilters'
import { DEFAULT_FOLDER_NAME, settings } from '@/settings'
import { sportStyles } from '@/sports'

// the lines' width in CalTopo, in pixels (its own default is 2)
const STROKE_WIDTH = 3

const props = defineProps<{
  bbox: BBox
  /** Activity types on the map, for the lines' colors. */
  types: string[]
  /** Older activities left off the map (over its limit), so not exported either. */
  omitted: number
}>()
const emit = defineEmits<{ redraw: []; close: []; preview: [ids: string[]] }>()

type State = 'counting' | 'ready' | 'empty' | 'over' | 'preparing' | 'downloaded' | 'error'

const state = ref<State>('counting')
const count = ref(0)
const limit = ref(0)
const downloaded = ref(0)
const error = ref('')
const options = settings.caltopoExport
const filtersText = computed(() => describeFilters(settings.mapFilters))

function request(): TrackExportRequest {
  return {
    bbox: props.bbox,
    ...exportFilters(settings.mapFilters),
    mode: options.mode,
    folder: options.folder ? options.folderName.trim() || DEFAULT_FOLDER_NAME : null,
    units: settings.units,
    styles: sportStyles(props.types),
    stroke_width: STROKE_WIDTH,
  }
}

function fail(e: unknown): void {
  state.value = 'error'
  error.value = e instanceof SignedOutError ? 'You were signed out: reload the page to sign in again.' : String(e instanceof Error ? e.message : e)
}

// each preview supersedes the previous one (the filters can change while one is out)
let previews = 0

async function preview(): Promise<void> {
  const current = ++previews
  state.value = 'counting'
  try {
    const found = await api.exportPreview(request())
    if (current !== previews) return
    count.value = found.activity_ids.length
    limit.value = found.limit
    state.value = !count.value ? 'empty' : count.value > found.limit ? 'over' : 'ready'
    emit('preview', found.activity_ids)
  } catch (e) {
    if (current === previews) fail(e)
  }
}

async function download(): Promise<void> {
  state.value = 'preparing'
  try {
    const file = await api.exportTracks(request())
    saveFile(file.blob, file.filename)
    downloaded.value = file.count
    state.value = 'downloaded'
  } catch (e) {
    fail(e)
  }
}

watch(
  [() => props.bbox, () => settings.mapFilters],
  () => {
    emit('preview', [])
    void preview()
  },
  { deep: true, immediate: true },
)

const tracks = (n: number) => `${n.toLocaleString()} ${n === 1 ? 'track' : 'tracks'}`
</script>

<template>
  <section class="export" role="dialog" aria-labelledby="export-title">
    <h3 id="export-title">Export to CalTopo</h3>

    <p class="summary" aria-live="polite">
      <template v-if="state === 'counting'">Finding tracks in this area…</template>
      <template v-else-if="state === 'empty'">No tracks in this area match the map's filters.</template>
      <template v-else-if="state === 'over'">
        <strong>{{ tracks(count) }}</strong>: exports are limited to {{ limit }}. Narrow the area or the map's filters.
      </template>
      <template v-else-if="state === 'error'"><span class="failed">{{ error }}</span></template>
      <template v-else-if="state === 'downloaded'">
        Downloaded {{ tracks(downloaded) }}. In CalTopo, open a map, choose <strong>Import</strong> at the top of the
        sidebar, and pick this file. Tracks already on the map from an earlier export aren't added twice.
      </template>
      <template v-else>
        <strong>{{ tracks(count) }}</strong><template v-if="filtersText"> · {{ filtersText }}</template>
      </template>
    </p>
    <p v-if="omitted && state !== 'downloaded'" class="note">
      Only the map's {{ MAP_TRACKS_LIMIT.toLocaleString() }} most recent tracks can be exported.
    </p>

    <fieldset :disabled="state === 'preparing'">
      <legend>Tracks</legend>
      <label><input v-model="options.mode" type="radio" value="whole" /> Whole tracks</label>
      <label><input v-model="options.mode" type="radio" value="clip" /> Clip to the area</label>
    </fieldset>

    <fieldset :disabled="state === 'preparing'">
      <legend>Folder</legend>
      <label><input v-model="options.folder" type="checkbox" /> Put tracks in a CalTopo folder</label>
      <input
        v-model="options.folderName"
        class="folder-name"
        type="text"
        maxlength="100"
        :placeholder="DEFAULT_FOLDER_NAME"
        :disabled="!options.folder"
        aria-label="Folder name"
      />
    </fieldset>

    <div class="buttons">
      <button type="button" class="secondary" :disabled="state === 'preparing'" @click="emit('redraw')">Redraw</button>
      <button type="button" class="secondary" @click="emit('close')">Cancel</button>
      <button
        type="button"
        class="primary"
        :disabled="!['ready', 'downloaded'].includes(state)"
        @click="download"
      >
        {{ state === 'preparing' ? 'Preparing…' : 'Download for CalTopo' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.export {
  position: fixed;
  z-index: 20;
  top: 80px;
  left: 20px;
  width: min(360px, calc(100vw - 40px));
  padding: 16px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.97);
  color: #334155;
  font-size: 13px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.35);
}

h3 {
  margin: 0 0 8px;
  font-size: 16px;
  color: #0f172a;
}

.summary {
  margin: 0;
  font-size: 14px;
  line-height: 1.45;
}

.note {
  margin: 6px 0 0;
  color: #64748b;
}

.failed {
  color: #b91c1c;
}

fieldset {
  margin: 14px 0 0;
  padding: 0;
  border: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

legend {
  margin-bottom: 6px;
  padding: 0;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #64748b;
}

label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

input[type='radio'],
input[type='checkbox'] {
  accent-color: #ea580c;
}

.folder-name {
  margin-left: 24px;
  padding: 5px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font: inherit;
}

.folder-name:disabled {
  background: #f1f5f9;
  color: #94a3b8;
}

.buttons {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}

.buttons button {
  padding: 8px 12px;
  white-space: nowrap;
  border-radius: 8px;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.buttons button:disabled {
  cursor: default;
  opacity: 0.5;
}

.secondary {
  border: 1px solid #cbd5e1;
  background: white;
  color: #334155;
}

.primary {
  margin-left: auto;
  border: none;
  background: #ea580c;
  color: white;
}

/* a bottom sheet on phones */
@media (max-width: 600px) {
  .export {
    top: auto;
    bottom: 0;
    left: 0;
    width: 100%;
    box-sizing: border-box;
    border-radius: 16px 16px 0 0;
    padding-bottom: max(16px, env(safe-area-inset-bottom));
  }

  .buttons {
    flex-wrap: wrap;
  }
}
</style>

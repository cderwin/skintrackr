<script setup lang="ts">
// The CalTopo export dialog, for an area drawn on the map: finds the tracks in it that pass the map's filters, lists
// them to tick (all, or the newest `limit` when there are more), and downloads the ticked ones as a GeoJSON file
// CalTopo imports. It reports the ticked tracks with `preview` (the map highlights them), the hovered one with
// `focus`, and a download with `downloaded` (which closes it). It sits just below the map's export button.
import { computed, ref, watch } from 'vue'
import { ChevronRight } from '@lucide/vue'
import {
  api,
  MAP_TRACKS_LIMIT,
  saveFile,
  SignedOutError,
  type ActivitySummary,
  type BBox,
  type TrackExportRequest,
} from '@/api'
import { describeFilters, exportFilters } from '@/mapFilters'
import { DEFAULT_FOLDER_NAME, formatDate, formatDistance, formatVert, settings } from '@/settings'
import { sportColor, sportLabel, sportStyles } from '@/sports'

// the lines' width in CalTopo, in pixels (its own default is 2)
const STROKE_WIDTH = 3

const props = defineProps<{
  bbox: BBox
  /** Activity types on the map, for the lines' colors. */
  types: string[]
  /** Older activities left off the map (over its limit), so not exported either. */
  omitted: number
  /** The athlete's activities by id, to list the tracks found. */
  activities: Map<string, ActivitySummary>
}>()
const emit = defineEmits<{
  redraw: []
  close: []
  preview: [ids: string[]]
  focus: [id: string | null]
  downloaded: [count: number]
}>()

type State = 'counting' | 'ready' | 'empty' | 'preparing' | 'error'

const state = ref<State>('counting')
// the tracks found, newest first, and those unticked: a new search (the filters changed) keeps the athlete's
// choices, and tracks it newly finds arrive ticked
const found = ref<string[]>([])
const excluded = ref(new Set<string>())
const limit = ref(0)
// whether the search found more than the limit, so only the newest were ticked
const trimmed = ref(false)
const listOpen = ref(false)
const error = ref('')
const options = settings.caltopoExport
const filtersText = computed(() => describeFilters(settings.mapFilters))

const selected = computed(() => found.value.filter((id) => !excluded.value.has(id)))
const canDownload = computed(() => state.value === 'ready' && selected.value.length > 0 && selected.value.length <= limit.value)
const rows = computed(() =>
  found.value.map((id) => {
    const activity = props.activities.get(id)
    return {
      id,
      title: activity ? activity.name || sportLabel(activity.type) : `Activity ${id}`,
      color: activity ? sportColor(activity.type) : '#94a3b8',
      details: activity
        ? [formatDate(activity.start_date_local), formatVert(activity.total_elevation_gain), formatDistance(activity.distance)].join(' · ')
        : '',
    }
  }),
)

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

function tick(id: string, on: boolean): void {
  trimmed.value = false
  if (on) excluded.value.delete(id)
  else excluded.value.add(id)
}

function tickAll(on: boolean): void {
  trimmed.value = false
  excluded.value = on ? new Set() : new Set(found.value)
}

// each preview supersedes the previous one (the filters can change while one is out)
let previews = 0

async function preview(): Promise<void> {
  const current = ++previews
  state.value = 'counting'
  try {
    const result = await api.exportPreview(request())
    if (current !== previews) return
    found.value = result.activity_ids
    limit.value = result.limit
    // over the limit: untick the oldest of the ticked ones, and show the list
    const ticked = result.activity_ids.filter((id) => !excluded.value.has(id))
    trimmed.value = ticked.length > result.limit
    if (trimmed.value) {
      for (const id of ticked.slice(result.limit)) excluded.value.add(id)
      listOpen.value = true
    }
    state.value = found.value.length ? 'ready' : 'empty'
  } catch (e) {
    if (current === previews) fail(e)
  }
}

async function download(): Promise<void> {
  state.value = 'preparing'
  try {
    const file = await api.exportTracks({ ...request(), activity_ids: selected.value })
    saveFile(file.blob, file.filename)
    emit('downloaded', file.count)
  } catch (e) {
    fail(e)
  }
}

watch(
  [() => props.bbox, () => settings.mapFilters],
  () => {
    found.value = []
    void preview()
  },
  { deep: true, immediate: true },
)
watch(selected, (ids) => emit('preview', ids))

const tracks = (n: number) => `${n.toLocaleString()} ${n === 1 ? 'track' : 'tracks'}`
</script>

<template>
  <section class="export" role="dialog" aria-labelledby="export-title">
    <h3 id="export-title">Export to CalTopo</h3>

    <p class="summary" aria-live="polite">
      <template v-if="state === 'counting'">Finding tracks in this area…</template>
      <template v-else-if="state === 'empty'">No tracks in this area match the map's filters.</template>
      <template v-else-if="state === 'error'"><span class="failed">{{ error }}</span></template>
      <template v-else>
        <strong v-if="selected.length === found.length">{{ tracks(found.length) }}</strong>
        <template v-else>
          <strong>{{ selected.length.toLocaleString() }}</strong> of {{ tracks(found.length) }} selected
        </template>
        <template v-if="filtersText"> · {{ filtersText }}</template>
      </template>
    </p>
    <p v-if="state === 'ready' && selected.length > limit" class="note warning">
      Exports are limited to {{ limit }} tracks: untick {{ (selected.length - limit).toLocaleString() }} more, or narrow
      the area.
    </p>
    <p v-else-if="state === 'ready' && trimmed" class="note">
      Exports are limited to {{ limit }} tracks, so the newest {{ limit }} are ticked.
    </p>
    <p v-if="omitted" class="note">
      Only the map's {{ MAP_TRACKS_LIMIT.toLocaleString() }} most recent tracks can be exported.
    </p>

    <div v-if="state === 'ready' || state === 'preparing'" class="choose">
      <div class="choose-bar">
        <button type="button" class="link disclosure" :aria-expanded="listOpen" @click="listOpen = !listOpen">
          <ChevronRight class="chevron icon-em" :class="{ open: listOpen }" />Choose tracks
        </button>
        <span v-if="listOpen" class="all-none">
          <button type="button" class="link" :disabled="state === 'preparing'" @click="tickAll(true)">All</button>
          ·
          <button type="button" class="link" :disabled="state === 'preparing'" @click="tickAll(false)">None</button>
        </span>
      </div>
      <ul v-if="listOpen" class="tracks" @mouseleave="emit('focus', null)">
        <li v-for="row in rows" :key="row.id" @mouseenter="emit('focus', row.id)">
          <label>
            <input
              type="checkbox"
              :checked="!excluded.has(row.id)"
              :disabled="state === 'preparing'"
              @change="tick(row.id, ($event.target as HTMLInputElement).checked)"
            />
            <span class="dot" :style="{ background: row.color }" aria-hidden="true" />
            <span class="track">
              <span class="title">{{ row.title }}</span>
              <span class="details">{{ row.details }}</span>
            </span>
          </label>
        </li>
      </ul>
    </div>

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
      <button type="button" class="primary" :disabled="!canDownload" @click="download">
        {{ state === 'preparing' ? 'Preparing…' : 'Download for CalTopo' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
/* just below the export button (48px, 20px from the top), right edges aligned: the button is one step in from the
   right corner (FloatingButton's offset 1) */
.export {
  position: fixed;
  z-index: 20;
  top: 80px;
  right: 80px;
  width: min(360px, calc(100vw - 40px));
  max-height: calc(100vh - 100px);
  overflow-y: auto;
  box-sizing: border-box;
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

.note.warning {
  color: #b45309;
}

.failed {
  color: #b91c1c;
}

.choose {
  margin-top: 12px;
}

.choose-bar {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  color: #94a3b8;
}

.link {
  padding: 0;
  border: none;
  background: none;
  color: #ea580c;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.disclosure {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.chevron {
  transition: transform 0.15s ease;
}

.chevron.open {
  transform: rotate(90deg);
}

.link:disabled {
  cursor: default;
  opacity: 0.5;
}

.tracks {
  max-height: 40vh;
  overflow-y: auto;
  margin: 8px 0 0;
  padding: 4px 0;
  list-style: none;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.tracks li {
  padding: 4px 10px;
}

.tracks li:hover {
  background: #fff7ed;
}

.tracks label {
  align-items: flex-start;
}

.tracks input {
  margin-top: 2px;
}

.dot {
  flex: none;
  width: 10px;
  height: 10px;
  margin-top: 4px;
  border-radius: 50%;
}

.track {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.title {
  overflow: hidden;
  color: #0f172a;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.details {
  color: #64748b;
  font-size: 12px;
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
    right: auto;
    bottom: 0;
    left: 0;
    width: 100%;
    max-height: 75vh;
    border-radius: 16px 16px 0 0;
    padding-bottom: max(16px, env(safe-area-inset-bottom));
  }

  .tracks {
    max-height: 30vh;
  }

  .buttons {
    flex-wrap: wrap;
  }
}
</style>

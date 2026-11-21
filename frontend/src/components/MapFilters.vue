<script setup lang="ts">
// The map's filter panel: sports and a period (calendar years, the last 12 months, or a custom range). Edits
// `settings.mapFilters`, which the map applies; counts the activities on the map that pass.
import { computed } from 'vue'
import { MAP_TRACKS_LIMIT, type ActivitySummary } from '@/api'
import { isFiltered, matches } from '@/mapFilters'
import { settings, type MapPeriod } from '@/settings'
import { sportColor, sportLabel } from '@/sports'
import FloatingButton from '@/components/FloatingButton.vue'

const props = defineProps<{
  /** The activities on the map. */
  activities: ActivitySummary[]
  /** Older activities left off the map (over its limit). */
  omitted: number
}>()

// the panel is open (the map closes it to make room for its export dialog)
const open = defineModel<boolean>('open', { default: false })
const filters = settings.mapFilters

const sports = computed(() => {
  const counts = new Map<string, number>()
  for (const activity of props.activities) counts.set(activity.type, (counts.get(activity.type) ?? 0) + 1)
  // a saved sport with no activities stays visible, so it can be turned off
  for (const sport of filters.sports) if (!counts.has(sport)) counts.set(sport, 0)
  return [...counts].sort(([a, x], [b, y]) => y - x || a.localeCompare(b))
})

const years = computed(() => {
  const found = new Set(props.activities.map((a) => a.start_date_local.slice(0, 4)).filter((y) => /^\d{4}$/.test(y)))
  return [...found].sort().reverse()
})

const shown = computed(() => props.activities.filter((activity) => matches(activity, filters)).length)

function toggleSport(sport: string): void {
  const index = filters.sports.indexOf(sport)
  if (index < 0) filters.sports.push(sport)
  else filters.sports.splice(index, 1)
}

function setPeriod(period: MapPeriod): void {
  filters.period = period
}

function reset(): void {
  filters.sports.splice(0)
  filters.period = 'all'
  filters.from = filters.to = ''
}
</script>

<template>
  <FloatingButton side="right" :label="open ? 'Close filters' : 'Filter activities'" @click="open = !open">
    <span aria-hidden="true">{{ open ? '✕' : '⚲' }}</span>
    <span v-if="!open && isFiltered(filters)" class="badge" aria-hidden="true" />
  </FloatingButton>

  <section v-if="open" class="filters" aria-label="Filter activities">
    <p class="count">
      <strong>{{ shown.toLocaleString() }}</strong> of {{ activities.length.toLocaleString() }} activities shown
      <button v-if="isFiltered(filters)" type="button" class="link" @click="reset">Show all</button>
    </p>

    <h4>Sport</h4>
    <div class="chips">
      <button type="button" class="chip" :aria-pressed="!filters.sports.length" @click="filters.sports.splice(0)">
        All
      </button>
      <button
        v-for="[sport, count] in sports"
        :key="sport"
        type="button"
        class="chip"
        :aria-pressed="filters.sports.includes(sport)"
        @click="toggleSport(sport)"
      >
        <span class="dot" :style="{ background: sportColor(sport) }" />{{ sportLabel(sport) }}
        <small>{{ count }}</small>
      </button>
    </div>

    <h4>Date</h4>
    <div class="chips">
      <button type="button" class="chip" :aria-pressed="filters.period === 'all'" @click="setPeriod('all')">
        All time
      </button>
      <button type="button" class="chip" :aria-pressed="filters.period === 'last12'" @click="setPeriod('last12')">
        Last 12 months
      </button>
      <button
        v-for="year in years"
        :key="year"
        type="button"
        class="chip"
        :aria-pressed="filters.period === year"
        @click="setPeriod(year as MapPeriod)"
      >
        {{ year }}
      </button>
      <button type="button" class="chip" :aria-pressed="filters.period === 'custom'" @click="setPeriod('custom')">
        Custom
      </button>
    </div>
    <div v-if="filters.period === 'custom'" class="range">
      <label>From <input v-model="filters.from" type="date" :max="filters.to || undefined" /></label>
      <label>To <input v-model="filters.to" type="date" :min="filters.from || undefined" /></label>
    </div>

    <p v-if="omitted" class="limit">
      The map shows your {{ MAP_TRACKS_LIMIT.toLocaleString() }} most recent activities;
      {{ omitted.toLocaleString() }} older ones aren't drawn.
    </p>
  </section>
</template>

<style scoped>
.badge {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #ea580c;
  border: 2px solid white;
}

.filters {
  position: fixed;
  z-index: 20;
  top: 80px;
  right: 20px;
  width: min(320px, calc(100vw - 40px));
  max-height: calc(100vh - 180px);
  overflow-y: auto;
  padding: 16px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.97);
  color: #334155;
  font-size: 13px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.35);
}

.count {
  margin: 0 0 4px;
  font-size: 14px;
}

h4 {
  margin: 14px 0 6px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #64748b;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  background: white;
  color: #334155;
  font: inherit;
  cursor: pointer;
}

.chip[aria-pressed='true'] {
  border-color: #ea580c;
  background: #fff7ed;
  color: #9a3412;
  font-weight: 600;
}

.chip small {
  color: #94a3b8;
  font-weight: normal;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.range {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.range label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  color: #64748b;
}

.range input {
  font: inherit;
  padding: 4px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
}

.link {
  margin-left: 6px;
  padding: 0;
  border: none;
  background: none;
  color: #ea580c;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.limit {
  margin: 14px 0 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fffbeb;
  color: #92400e;
}
</style>

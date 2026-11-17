<script setup lang="ts">
// A clicked track's details, inside the map's popup. Where tracks overlap, the others under the click are listed
// below (the newest `LIMIT`, with a count of the rest), to switch between them.
import { computed } from 'vue'
import { intervalsActivityUrl, stravaActivityUrl, type ActivitySummary } from '@/api'
import { formatDistance, formatVert } from '@/settings'
import { sportColor, sportLabel } from '@/sports'

// activities listed where tracks overlap
const LIMIT = 5

const props = defineProps<{
  /** The activities under the click, newest first. */
  activities: ActivitySummary[]
  selectedId: string
}>()
defineEmits<{ select: [id: string] }>()

const activity = computed(() => props.activities.find((a) => a.id === props.selectedId) ?? props.activities[0])
const listed = computed(() => props.activities.slice(0, LIMIT))
const count = computed(() =>
  props.activities.length > LIMIT
    ? `${LIMIT} of ${props.activities.length} activities here · zoom in to see the rest`
    : `${props.activities.length} activities here`,
)

function formatDate(value: string): string {
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  if (!year || !month || !day) return ''
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
</script>

<template>
  <div v-if="activity" class="activity-popup">
    <h3>{{ activity.name || sportLabel(activity.type) }}</h3>
    <dl>
      <dt>Sport</dt>
      <dd><span class="dot" :style="{ background: sportColor(activity.type) }" />{{ sportLabel(activity.type) }}</dd>
      <dt>Date</dt>
      <dd>{{ formatDate(activity.start_date_local) }}</dd>
      <dt>Distance</dt>
      <dd>{{ formatDistance(activity.distance) }}</dd>
      <dt>Elevation gain</dt>
      <dd>{{ formatVert(activity.total_elevation_gain) }}</dd>
    </dl>
    <div class="links">
      <a :href="intervalsActivityUrl(activity.id)" target="_blank" rel="noopener noreferrer">Open in intervals.icu ↗</a>
      <a v-if="activity.strava_id" :href="stravaActivityUrl(activity.strava_id)" target="_blank" rel="noopener noreferrer">
        View on Strava ↗
      </a>
    </div>

    <div v-if="activities.length > 1" class="others">
      <p>{{ count }}</p>
      <button
        v-for="other in listed"
        :key="other.id"
        type="button"
        :class="{ current: other.id === activity.id }"
        :aria-pressed="other.id === activity.id"
        @click="$emit('select', other.id)"
      >
        <span class="dot" :style="{ background: sportColor(other.type) }" />
        {{ formatDate(other.start_date_local) }} · {{ other.name || sportLabel(other.type) }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.activity-popup {
  min-width: 200px;
  font-size: 13px;
  color: #334155;
}

h3 {
  margin: 0 24px 8px 0;
  font-size: 15px;
  line-height: 1.3;
  color: #0f172a;
}

dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0 0 10px;
}

dt {
  color: #64748b;
}

dd {
  margin: 0;
  font-weight: 600;
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  border-radius: 50%;
  vertical-align: middle;
}

.links {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

a {
  color: #ea580c;
  font-weight: 600;
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

.others {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid #e2e8f0;
}

.others p {
  margin: 0 0 4px;
  color: #64748b;
}

.others button {
  display: block;
  width: 100%;
  padding: 4px 6px;
  border: none;
  border-radius: 6px;
  background: none;
  color: #334155;
  font: inherit;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.others button:hover,
.others button.current {
  background: #f1f5f9;
}

.others button.current {
  font-weight: 600;
}
</style>

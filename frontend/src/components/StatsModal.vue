<script setup lang="ts">
// Season vert over the map: progress to the yearly goal (edited here, saved in this browser), pace, and previous
// years.
import { computed, nextTick, onMounted, ref } from 'vue'
import { Mountain } from '@lucide/vue'
import { api, type VertStats } from '@/api'
import { formatVert, fromUnits, settings, toUnits } from '@/settings'
import ImportPanel from '@/components/ImportPanel.vue'
import ModalCard from '@/components/ModalCard.vue'
import PageCard from '@/components/PageCard.vue'

const emit = defineEmits<{ close: [] }>()

const stats = ref<VertStats | null>(null)
const error = ref('')

// loaded on every open, so a re-sync shows straight away
onMounted(async () => {
  try {
    stats.value = await api.vert()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})

const asOf = computed(() => {
  const [year, month, day] = (stats.value?.as_of ?? '').split('-').map(Number)
  return new Date(year ?? 0, (month ?? 1) - 1, day ?? 1)
})
const subtitle = computed(() =>
  stats.value ? `Jan 1 → ${asOf.value.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}` : '',
)
const current = computed(() => stats.value?.years[0]?.vert_m ?? 0)
const progress = computed(() => current.value / settings.goalM)

// how far ahead of (or behind) an even pace to the goal
const pace = computed(() => {
  const year = asOf.value.getFullYear()
  const dayOfYear = (asOf.value.getTime() - new Date(year, 0, 1).getTime()) / 86_400_000 + 1
  const daysInYear = (new Date(year + 1, 0, 1).getTime() - new Date(year, 0, 1).getTime()) / 86_400_000
  return current.value - (settings.goalM * dayOfYear) / daysInYear
})

const maxVert = computed(() => Math.max(1, ...(stats.value?.years ?? []).map((y) => y.vert_m)))

// the goal, in the athlete's units, edited in place
const editingGoal = ref(false)
const goalInput = ref<HTMLInputElement | null>(null)
const goal = computed({
  get: () => Math.round(toUnits(settings.goalM)),
  set: (value: number) => {
    if (value > 0) settings.goalM = fromUnits(value)
  },
})
async function editGoal(): Promise<void> {
  editingGoal.value = true
  await nextTick()
  goalInput.value?.select()
}

// progress ring geometry
const RADIUS = 70
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
</script>

<template>
  <ModalCard label="season stats" @close="emit('close')">
    <PageCard :icon="Mountain" title="Season vert" :subtitle="subtitle || (error ? '' : 'Loading your activities…')">
      <p v-if="error" class="error">{{ error }}</p>

      <template v-else-if="stats && !stats.activities">
        <p class="empty">No activities imported yet. Import them to see your season.</p>
        <ImportPanel />
      </template>

      <template v-else-if="stats">
        <div class="ring">
          <svg viewBox="0 0 160 160" role="img" :aria-label="`${Math.round(progress * 100)}% of goal`">
            <circle class="track" cx="80" cy="80" :r="RADIUS" />
            <circle
              class="fill"
              cx="80"
              cy="80"
              :r="RADIUS"
              :stroke-dasharray="CIRCUMFERENCE"
              :stroke-dashoffset="CIRCUMFERENCE * (1 - Math.min(progress, 1))"
            />
          </svg>
          <div class="ring-label">
            <strong>{{ formatVert(current, true) }}</strong>
            <span>{{ Math.round(progress * 100) }}% of {{ formatVert(settings.goalM, true) }}</span>
          </div>
        </div>

        <form v-if="editingGoal" class="goal" novalidate @submit.prevent="editingGoal = false">
          <label>
            Yearly goal
            <input
              ref="goalInput"
              v-model.lazy.number="goal"
              type="number"
              min="1"
              step="1000"
              inputmode="numeric"
              @blur="editingGoal = false"
            />
            {{ settings.units }}
          </label>
        </form>
        <p v-else class="goal">
          Yearly goal: {{ formatVert(settings.goalM) }} ·
          <button class="link-button" type="button" @click="editGoal">Edit</button>
        </p>

        <p class="pace" :class="pace >= 0 ? 'ahead' : 'behind'">
          {{ pace >= 0 ? 'Ahead of' : 'Behind' }} pace by {{ formatVert(Math.abs(pace), true) }}
        </p>

        <ul class="years">
          <li v-for="(year, index) in stats.years" :key="year.year" :class="{ current: index === 0 }">
            <span class="year">{{ year.year }}</span>
            <span class="bar"><span :style="{ width: `${(year.vert_m / maxVert) * 100}%` }" /></span>
            <span class="value">{{ formatVert(year.vert_m, true) }}</span>
          </li>
        </ul>
      </template>
      </PageCard>
  </ModalCard>
</template>

<style scoped>
.error {
  color: #b91c1c;
  font-size: 15px;
}

.empty {
  color: #475569;
  font-size: 15px;
  line-height: 1.6;
}

.ring {
  position: relative;
  width: 180px;
  height: 180px;
  margin: 0 auto 12px;
}

.ring svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.ring circle {
  fill: none;
  stroke-width: 14;
}

.track {
  stroke: #e2e8f0;
}

.fill {
  stroke: #f7931e;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.8s ease;
}

.ring-label {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.ring-label strong {
  font-size: 28px;
  color: #0f172a;
}

.ring-label span {
  font-size: 13px;
  color: #64748b;
}

.goal {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 12px;
  min-height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.goal label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.goal input {
  width: 110px;
  font-size: 13px;
  padding: 4px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
}

.link-button {
  margin-left: 4px;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-weight: 600;
  color: #2d5a7b;
  cursor: pointer;
}

.link-button:hover {
  text-decoration: underline;
}

.pace {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 28px;
}

.ahead {
  color: #059669;
}

.behind {
  color: #b45309;
}

.years {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.years li {
  display: grid;
  grid-template-columns: 44px 1fr 72px;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  color: #475569;
}

.year {
  text-align: left;
  font-weight: 600;
}

.value {
  text-align: right;
}

.bar {
  height: 14px;
  background: #f1f5f9;
  border-radius: 7px;
  overflow: hidden;
}

.bar span {
  display: block;
  height: 100%;
  background: #94a3b8;
  border-radius: 7px;
}

.current .bar span {
  background: linear-gradient(135deg, #ff6b35 0%, #f7931e 100%);
}

.current {
  color: #0f172a;
}
</style>

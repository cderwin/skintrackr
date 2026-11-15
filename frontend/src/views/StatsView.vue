<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, SignedOutError, type VertStats } from '@/api'
import { formatVert, settings } from '@/settings'
import ConnectButton from '@/components/ConnectButton.vue'
import FloatingButton from '@/components/FloatingButton.vue'
import PageCard from '@/components/PageCard.vue'
import SettingsPanel from '@/components/SettingsPanel.vue'

const stats = ref<VertStats | null>(null)
const signedOut = ref(false)
const error = ref('')
const showSettings = ref(false)

onMounted(async () => {
  try {
    stats.value = await api.vert()
  } catch (e) {
    if (e instanceof SignedOutError) signedOut.value = true
    else error.value = e instanceof Error ? e.message : String(e)
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

// progress ring geometry
const RADIUS = 70
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
</script>

<template>
  <FloatingButton side="left" to="/" label="Back">←</FloatingButton>
  <FloatingButton v-if="!signedOut" side="right" label="Settings" @click="showSettings = true">⚙️</FloatingButton>
  <SettingsPanel v-if="showSettings" @close="showSettings = false" />

  <PageCard v-if="signedOut" icon="⛰️" title="Season vert" subtitle="Connect your Strava account to see your stats">
    <ConnectButton />
  </PageCard>

  <PageCard v-else icon="⛰️" title="Season vert" :subtitle="subtitle || 'Loading your activities…'">
    <p v-if="error" class="error">{{ error }}</p>

    <p v-else-if="stats && !stats.activities" class="empty">
      No activities imported yet. <RouterLink class="brand-link" to="/">Import them from the home page</RouterLink>
      to see your season.
    </p>

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
</template>

<style scoped>
/* room for the floating buttons on narrow screens */
@media (max-width: 600px) {
  :deep(.card) {
    margin-top: 56px;
  }
}

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

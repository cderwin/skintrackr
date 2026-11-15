<script setup lang="ts">
// Settings, over the page: vert goal and units (saved in this browser), the Strava token, and signing out.
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type StravaToken } from '@/api'
import { fromUnits, settings, toUnits, type Units } from '@/settings'
import TokenField from '@/components/TokenField.vue'

const emit = defineEmits<{ close: [] }>()
const router = useRouter()

const goal = computed({
  get: () => Math.round(toUnits(settings.goalM)),
  set: (value: number) => {
    if (value > 0) settings.goalM = fromUnits(value)
  },
})
const unitOptions: Units[] = ['ft', 'm']

const token = ref<StravaToken | null>(null)
const tokenError = ref('')
async function fetchToken(): Promise<void> {
  tokenError.value = ''
  try {
    token.value = await api.stravaToken()
  } catch (e) {
    tokenError.value = e instanceof Error ? e.message : String(e)
  }
}
const tokenExpiry = computed(() =>
  token.value
    ? new Date(token.value.expires_at * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : '',
)

async function signOut(): Promise<void> {
  await api.logout().catch(() => undefined)
  await router.push('/')
}
</script>

<template>
  <div class="backdrop" @click.self="emit('close')" @keydown.esc="emit('close')">
    <section class="panel" role="dialog" aria-label="Settings">
      <header>
        <h2>Settings</h2>
        <button class="close" type="button" aria-label="Close settings" @click="emit('close')">✕</button>
      </header>

      <label class="field">
        <span>Yearly vert goal</span>
        <span class="goal-row">
          <input v-model.lazy.number="goal" type="number" min="1" step="1000" inputmode="numeric" />
          <span class="units" role="radiogroup" aria-label="Units">
            <button
              v-for="option in unitOptions"
              :key="option"
              type="button"
              role="radio"
              :aria-checked="settings.units === option"
              :class="{ active: settings.units === option }"
              @click="settings.units = option"
            >
              {{ option }}
            </button>
          </span>
        </span>
      </label>

      <div class="field">
        <span>Strava access token</span>
        <template v-if="token">
          <TokenField :token="token.access_token" label="Strava access token" />
          <small>Expires {{ tokenExpiry }}. Treat it like a password.</small>
        </template>
        <button v-else class="small-button" type="button" @click="fetchToken">Show my Strava token</button>
        <small v-if="tokenError" class="error">{{ tokenError }}</small>
      </div>

      <button class="small-button sign-out" type="button" @click="signOut">Sign out</button>
    </section>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 30;
  background: rgba(15, 23, 42, 0.55);
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  padding: 20px;
}

.panel {
  background: white;
  border-radius: 20px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  width: 100%;
  max-width: 380px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  text-align: left;
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

h2 {
  font-size: 20px;
  color: #0f172a;
}

.close {
  border: none;
  background: none;
  font-size: 18px;
  color: #64748b;
  cursor: pointer;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #334155;
}

.field small {
  font-weight: 400;
  color: #64748b;
}

.field small.error {
  color: #b91c1c;
}

.goal-row {
  display: flex;
  gap: 8px;
}

.goal-row input {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  padding: 10px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
}

.units {
  display: flex;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  overflow: hidden;
}

.units button {
  border: none;
  background: white;
  padding: 0 14px;
  font-weight: 600;
  color: #334155;
  cursor: pointer;
}

.units button.active {
  background: #1e3a5f;
  color: white;
}

.sign-out {
  align-self: flex-start;
}
</style>

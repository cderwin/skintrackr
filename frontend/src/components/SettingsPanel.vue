<script setup lang="ts">
// Settings, over the page: vert goal and units (saved in this browser), signing out, and disconnecting.
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { X } from '@lucide/vue'
import { api } from '@/api'
import { fromUnits, settings, toUnits, type Units } from '@/settings'

const emit = defineEmits<{ close: [] }>()
const router = useRouter()

const goal = computed({
  get: () => Math.round(toUnits(settings.goalM)),
  set: (value: number) => {
    if (value > 0) settings.goalM = fromUnits(value)
  },
})
const unitOptions: Units[] = ['ft', 'm']

async function signOut(): Promise<void> {
  await api.logout().catch(() => undefined)
  await router.push('/')
}

// disconnecting deletes everything, so it takes a second click to confirm
const confirmingDisconnect = ref(false)
const disconnectError = ref('')
async function disconnect(): Promise<void> {
  if (!confirmingDisconnect.value) {
    confirmingDisconnect.value = true
    return
  }
  disconnectError.value = ''
  try {
    await api.disconnect()
    await router.push('/')
  } catch (e) {
    disconnectError.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <div class="backdrop" @click.self="emit('close')" @keydown.esc="emit('close')">
    <section class="panel" role="dialog" aria-label="Settings">
      <header>
        <h2>Settings</h2>
        <button class="close" type="button" aria-label="Close settings" @click="emit('close')"><X :size="18" /></button>
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

      <button class="small-button align-start" type="button" @click="signOut">Sign out</button>

      <div class="field">
        <span>intervals.icu connection</span>
        <small>
          Disconnecting deletes your stored API key, archived activities and stats from this app. Your intervals.icu
          account isn't changed.
        </small>
        <span class="disconnect-row">
          <button class="small-button align-start danger" type="button" @click="disconnect">
            {{ confirmingDisconnect ? 'Yes, delete my data' : 'Disconnect and delete my data' }}
          </button>
          <button v-if="confirmingDisconnect" class="small-button" type="button" @click="confirmingDisconnect = false">
            Cancel
          </button>
        </span>
        <small v-if="disconnectError" class="error">{{ disconnectError }}</small>
      </div>
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
  display: inline-flex;
  align-items: center;
  padding: 0;
  border: none;
  background: none;
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

.align-start {
  align-self: flex-start;
}

.disconnect-row {
  display: flex;
  gap: 8px;
}

.small-button.danger {
  color: #b91c1c;
  border-color: #fca5a5;
}
</style>

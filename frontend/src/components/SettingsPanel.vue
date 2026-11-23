<script setup lang="ts">
// The map's settings, under its button: units (saved in this browser), re-syncing activities, Strava names (from
// the athlete's Strava export), signing out, and disconnecting.
import { ref } from 'vue'
import { X } from '@lucide/vue'
import { api, INTERVALS_SETTINGS_URL } from '@/api'
import { settings, type Units } from '@/settings'
import ImportPanel from '@/components/ImportPanel.vue'
import StravaImport from '@/components/StravaImport.vue'

const emit = defineEmits<{ close: []; 'strava-imported': [] }>()

const unitOptions: Units[] = ['ft', 'm']

// a fresh load of the map, which has nothing of the athlete's left and opens with the connect modal
function reload(): void {
  window.location.assign('/map')
}

async function signOut(): Promise<void> {
  await api.logout().catch(() => undefined)
  reload()
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
    reload()
  } catch (e) {
    disconnectError.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <section class="panel" role="dialog" aria-label="Settings">
      <header>
        <h2>Settings</h2>
        <button class="close" type="button" aria-label="Close settings" @click="emit('close')"><X :size="18" /></button>
      </header>

      <div class="field">
        <span>Units</span>
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
      </div>

      <div class="field">
        <span>Activities</span>
        <ImportPanel class="resync" />
      </div>

      <div class="field">
        <span>Strava names</span>
        <StravaImport @imported="emit('strava-imported')" />
      </div>

      <button class="small-button align-start" type="button" @click="signOut">Sign out</button>

      <div class="field">
        <span>intervals.icu connection</span>
        <small>
          Disconnecting deletes your stored API key, archived activities and stats from this app. Your intervals.icu
          account isn't changed. To revoke the key itself, generate a new one in your
          <a class="brand-link" :href="INTERVALS_SETTINGS_URL" target="_blank" rel="noopener">intervals.icu settings</a>.
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
  justify-content: flex-start;
  padding: 20px;
}

.panel {
  background: white;
  border-radius: 20px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  width: 100%;
  max-width: 380px;
  max-height: 100%;
  overflow-y: auto;
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

.units {
  align-self: flex-start;
  display: flex;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  overflow: hidden;
}

.units button {
  border: none;
  background: white;
  padding: 8px 16px;
  font-weight: 600;
  color: #334155;
  cursor: pointer;
}

.units button.active {
  background: #1e3a5f;
  color: white;
}

.resync {
  margin-top: 0;
  font-weight: 400;
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

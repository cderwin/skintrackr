<script setup lang="ts">
// A modal over the map holding a `PageCard`: an X in its corner and a click on the map around it close it (the map
// closes it on Esc), unless it isn't `closable`. It covers the whole screen on phones; `wide` fits a two-column card.
import { X } from '@lucide/vue'

withDefaults(defineProps<{ label: string; closable?: boolean; wide?: boolean }>(), { closable: true, wide: false })
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="backdrop" @click.self="closable && emit('close')">
    <div class="sheet" :class="{ wide }" role="dialog" aria-modal="true" :aria-label="label">
      <button v-if="closable" class="close" type="button" :aria-label="`Close ${label}`" @click="emit('close')">
        <X :size="22" />
      </button>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 30;
  background: rgba(15, 23, 42, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  overflow-y: auto;
}

.sheet {
  position: relative;
  width: 100%;
  max-width: 480px;
  margin: auto;
}

.sheet.wide {
  max-width: 860px;
}

@media (max-width: 900px) {
  .sheet.wide {
    max-width: 480px;
  }
}

/* in the card's top-right corner, above it (the card is z-index 10) */
.close {
  position: absolute;
  top: 16px;
  right: 16px;
  z-index: 11;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: none;
  color: #64748b;
  cursor: pointer;
}

.close:hover {
  background: #f1f5f9;
  color: #0f172a;
}

/* phones: the card is the whole screen */
@media (max-width: 520px) {
  .backdrop {
    padding: 0;
    align-items: stretch;
  }

  .sheet,
  .sheet.wide {
    margin: 0;
    max-width: none;
  }

  .sheet :deep(.card) {
    min-height: 100%;
    border-radius: 0;
    box-shadow: none;
  }

  .sheet .close + :deep(.card) {
    padding-top: 56px;
  }
}
</style>

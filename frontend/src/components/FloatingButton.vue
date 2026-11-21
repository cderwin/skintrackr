<script setup lang="ts">
// A round button floating over the page: a link when `to` is set, else a button. `offset` places it further in
// from its corner, one button per step, to sit beside another one there.
import { computed } from 'vue'

const props = defineProps<{
  label: string
  side: 'left' | 'right'
  to?: string
  offset?: number
}>()
defineEmits<{ click: [] }>()

// buttons are 48px wide, 12px apart, 20px from the edge
const position = computed(() => ({ [props.side]: `${20 + (props.offset ?? 0) * 60}px` }))
</script>

<template>
  <RouterLink v-if="to" :to="to" class="floating" :style="position" :aria-label="label" :title="label">
    <slot />
  </RouterLink>
  <button
    v-else
    type="button"
    class="floating"
    :style="position"
    :aria-label="label"
    :title="label"
    @click="$emit('click')"
  >
    <slot />
  </button>
</template>

<style scoped>
.floating {
  position: fixed;
  top: 20px;
  z-index: 20;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: none;
  background: rgba(255, 255, 255, 0.95);
  color: #0f172a;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  text-decoration: none;
  cursor: pointer;
  transition: transform 0.2s ease;
}

.floating:hover {
  transform: translateY(-2px);
}

</style>

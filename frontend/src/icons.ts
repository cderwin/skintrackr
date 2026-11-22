// Icons Lucide doesn't have, drawn in its style (24px grid, 2px stroke, round caps and joins) and built with its
// `createLucideIcon`, so they take the same props (`size`, `stroke-width`, ...) and `lucide` class as its own.
import { createLucideIcon } from '@lucide/vue'

/** A skier heading downhill, poles planted. */
export const Skier = createLucideIcon('skier', [
  ['circle', { cx: '15', cy: '4', r: '1', key: 'head' }],
  ['path', { d: 'm12 7-3 4 4 3-2 4', key: 'body' }],
  ['path', { d: 'm12 7 4 3 1.5 8', key: 'pole' }],
  ['path', { d: 'm2 15 18 6a2 2 0 0 0 2-2', key: 'skis' }],
])

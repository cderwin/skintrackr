/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** MapTiler API key for the map page (public; restrict it to the site's domains in MapTiler). */
  readonly VITE_MAPTILER_KEY?: string
}

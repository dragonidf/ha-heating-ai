// Keep the registered element name stable across frontend-only releases.
const registered = new URL(import.meta.url).searchParams.get('v');
if (!/^[a-f0-9]{16}$/.test(registered || '')) throw new Error('Invalid Heating AI bundle');
// A local, packaged manifest is revalidated on every fresh browser load.
// Missing manifest is supported for an older installation; malformed data is not.
const response = await fetch(new URL('full-panel-version.json', import.meta.url), {cache:'no-store'});
let module = `full-panel.js?v=${registered}`;
if (response.ok) {
  const manifest = await response.json();
  if (!/^[a-f0-9]{16}$/.test(manifest.digest || '')) throw new Error('Invalid Heating AI asset manifest');
  module = `full-panel-${manifest.digest}.js`;
} else if (response.status !== 404) {
  throw new Error('Heating AI asset manifest unavailable');
}
const { HeatingAIPanel } = await import(new URL(module, import.meta.url));
const name = `heating-ai-v1-${registered}`;
if (!customElements.get(name)) customElements.define(name, class extends HeatingAIPanel {});

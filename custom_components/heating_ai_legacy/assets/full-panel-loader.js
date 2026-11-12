// Each released bundle gets its own element, including an already-open HA tab.
const version = new URL(import.meta.url).searchParams.get('v');
if (!/^[a-f0-9]{16}$/.test(version || '')) throw new Error('Invalid Heating AI bundle');
const { HeatingAIPanel } = await import(new URL(`full-panel.js?v=${version}`, import.meta.url));
const name = `heating-ai-v1-${version}`;
if (!customElements.get(name)) customElements.define(name, class extends HeatingAIPanel {});

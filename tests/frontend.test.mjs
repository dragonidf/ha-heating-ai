import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.HTMLElement=class {};
globalThis.customElements={get:()=>false,define:()=>{}};
globalThis.window={};
const {numeric,escapeHTML,isTrue,mergeSegments}=await import('../frontend/heating-panel.js');
test('unknown measurements never become zero',()=>{
  for(const value of [null,undefined,'',' ','unknown','unavailable',Infinity])assert.equal(numeric(value),null);
  assert.equal(numeric('0'),0);assert.equal(numeric('-2.1'),-2.1);
});
test('AppDaemon serialised booleans retain their meaning',()=>{
  for(const value of [true,'true','on'])assert.equal(isTrue(value),true);
  for(const value of [false,'false','off',undefined])assert.equal(isTrue(value),false);
});
test('sensor text cannot inject HTML or attributes',()=>{
  assert.equal(escapeHTML('<img onerror="x"> &'), '&lt;img onerror=&quot;x&quot;&gt; &amp;');
});
test('sessions merge without hiding conditional heat and never mutate the forecast',()=>{
  const rows=[{start:20,end:30,conditional:'true'},{start:0,end:10},{start:10,end:20},{start:30,end:40,conditional:true}];
  const original=JSON.stringify(rows);
  assert.deepEqual(mergeSegments(rows),[{start:0,end:20},{start:20,end:40,conditional:'true'}]);
  assert.equal(JSON.stringify(rows),original);
});

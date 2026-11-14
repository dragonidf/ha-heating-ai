if(!customElements.get('heating-ai-panel'))customElements.define('heating-ai-panel',HeatingAIPanel);
window.customCards=window.customCards||[];
if(!window.customCards.some(c=>c.type==='heating-ai-panel'))window.customCards.push({type:'heating-ai-panel',name:'Heating AI',description:'Клімат: огляд, комфорт, прогноз і навчання',preview:false});

"""Browser smoke for the actual read-only native V1+ graph (desktop and mobile).

Synthetic data only. No HA connection, equipment command, model migration,
cloud access, or production state mutation.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import time

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "custom_components/heating_ai_legacy/assets/panel.js"


async def verify() -> None:
    now = time()
    forecast = [
        {
            "ts": now + i * 1800,
            "temperature": 21.3 + i * .017,
            "target": 21.5,
            "lower": 21.0,
            "upper": 22.0,
        }
        for i in range(1, 49)
    ]
    native = [
        {
            "ts": now + i * 1800,
            "temperature": 21.3 + i * .013,
            "target": 21.5,
        }
        for i in range(1, 49)
    ]
    states = {
        "sensor.heating_ui": {
            "state": "0.3.23",
            "attributes": {
                "entities": {
                    "room": "sensor.room",
                    "target": "input_number.heating_target",
                    "mode": "input_select.heating_mode",
                }
            },
        },
        "sensor.room": {
            "state": "21.3", "attributes": {},
            "last_updated": "2026-11-12T11:00:00+00:00",
        },
        "input_number.heating_target": {"state": "21.5", "attributes": {}},
        "input_select.heating_mode": {"state": "Штатний auto", "attributes": {}},
        "climate.kotel": {"state": "auto", "attributes": {}},
        "sensor.heating_controller": {
            "state": "Тимчасовий auto", "attributes": {},
        },
        "sensor.heating_forecast": {
            "state": "21.4",
            "attributes": {
                "created_at": __import__("datetime").datetime.fromtimestamp(
                    now, __import__("datetime").timezone.utc
                ).isoformat(),
                "curve": forecast,
                "actual_curve": [
                    {"ts":now-3600,"temperature":21.1},
                    {"ts":now-1800,"temperature":21.2},
                ],
                "heat_segments": [
                    {"start":now+900,"end":now+5400,"power_kw":6.0},
                ],
                "execution_state": "calculated",
                "planned_kwh": 5.0,
            },
        },
    }
    for width, height, label in ((1365, 840, "desktop"), (390, 844, "mobile")):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width":width,"height":height})
                await page.set_content("<!doctype html><html lang='uk'><body></body></html>")
                await page.add_script_tag(type="module",content=SCRIPT.read_text(encoding="utf-8"))
                await page.wait_for_function(
                    "Boolean(customElements.get('heating-v1-native-preview'))"
                )
                await page.evaluate(
                    """({states,now,native}) => {
                        const element=document.createElement('heating-v1-native-preview');
                        document.body.appendChild(element);
                        element.panel={config:{
                            entry_id:'isolated-native-v1',room:'sensor.room',
                            entity_map:{room:'sensor.room',target:'input_number.heating_target',
                                        mode:'input_select.heating_mode'}
                        }};
                        element.hass={
                          states,
                          callApi:async()=>[[
                            {s:'21.1',lu:now-3600},
                            {s:'21.2',lu:now-1800},
                            {s:'21.3',lu:now},
                          ]],
                          callWS:async({type,entry_id})=>{
                            if(type!=='heating_ai_legacy/native_snapshot' ||
                               entry_id!=='isolated-native-v1')throw Error('wrong API');
                            return {
                              status:'VIRTUAL_CONDITIONAL',
                              quality:'PROVISIONAL_SOURCES',
                              reason:'schedule_coverage_unverified',
                              computed_at:now,
                              physical_commands_enabled:false,
                              commands_sent:0,
                              forecast:native,
                              heat_segments:[
                                {start:now+900,end:now+5400,power_kw:3.0}
                              ],
                            };
                          }
                        };
                    }""",
                    dict(states=states,now=now,native=native),
                )
                await page.wait_for_function(
                    """() => {
                      const root=document.querySelector('heating-v1-native-preview')?.shadowRoot;
                      return !!root?.querySelector('path.forecast') &&
                             !!root?.querySelector('path.native') &&
                             !!root?.querySelector('path.actual') &&
                             root?.querySelectorAll('rect.heat').length===1 &&
                             root?.getElementById('room')?.textContent.includes('21,3');
                    }""",
                    timeout=12000,
                )
                checks=await page.evaluate(
                    """() => {
                        const root=document.querySelector('heating-v1-native-preview').shadowRoot;
                        const stats=[...root.querySelectorAll('.stat strong')].map(x=>x.textContent);
                        return {
                            n_forecast:root.querySelectorAll('path.forecast').length,
                            n_native:root.querySelectorAll('path.native').length,
                            n_history:root.querySelectorAll('path.actual').length,
                            mode:root.getElementById('mode').textContent,
                            native_status:root.getElementById('native-status').textContent,
                            stats,
                            controls:root.querySelectorAll('button, select, input').length,
                            has_horizontal_scroll:root.querySelector('.chart').getBoundingClientRect().width >
                                root.querySelector('.scroll').getBoundingClientRect().width,
                        };
                    }"""
                )
                assert checks["n_forecast"]>=1 and checks["n_native"]>=1
                assert checks["n_history"]>=1
                assert "Штатний auto" in checks["mode"]
                assert "V1+" in checks["native_status"]
                assert "PROVISIONAL_SOURCES" not in checks["native_status"]
                assert checks["controls"] == 0, "Preview must not contain equipment controls"
                if label=="mobile":
                    assert not checks["has_horizontal_scroll"], "Full 24h graph should fit mobile"
                    placement=await page.evaluate("""() => {
                        const root=document.querySelector('heating-v1-native-preview').shadowRoot;
                        return {
                          chart:root.querySelector('.chartbox').getBoundingClientRect().top,
                          secondary:root.querySelector('.stats.secondary').getBoundingClientRect().top,
                          viewBox:root.querySelector('.chart').getAttribute('viewBox'),
                          primaryCount:root.querySelector('.stats:not(.secondary)').children.length,
                        };
                    }""")
                    assert placement["chart"] < placement["secondary"]
                    assert placement["viewBox"] == "0 0 600 390"
                    assert placement["primaryCount"] == 2
                # Native-only lifecycle: old AppDaemon sensors no longer exist.
                await page.evaluate("""() => {
                    const el=document.querySelector('heating-v1-native-preview');
                    const old=el._lastHass;
                    const states={...old.states};
                    delete states['sensor.heating_ui'];
                    delete states['sensor.heating_forecast'];
                    delete states['sensor.heating_controller'];
                    el.hass={...old,states};
                }""")
                await page.wait_for_function("""() => {
                    const root=document.querySelector('heating-v1-native-preview')?.shadowRoot;
                    return !!root?.querySelector('path.native') &&
                        root?.querySelectorAll('rect.heat-native').length===1 &&
                        root?.querySelectorAll('rect.heat').length===0 &&
                        root?.getElementById('room')?.textContent.includes('21,3') &&
                        root?.getElementById('source')?.textContent.includes('Незалежний прогноз V1+') &&
                        root?.getElementById('plan')?.textContent.includes('умовне вікно') &&
                        root?.querySelectorAll('button, input, select').length===0;
                }""",timeout=12000)
                out=ROOT/("native-v1-preview-"+label+".png")
                await page.screenshot(path=str(out),full_page=True)
                print("PASS",label,json.dumps(checks,ensure_ascii=False))
            finally:
                await browser.close()


if __name__=="__main__":
    asyncio.run(verify())

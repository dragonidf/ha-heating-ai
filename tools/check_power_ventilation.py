"""Bounded offline pairwise closed-loop check with a different nonlinear plant.

No HA I/O. The plant uses RK4, voltage/duty variation and nonlinear exchange;
the controller keeps its Euler thermal model. These are simulations, not proof
of physical boiler accuracy or a substitute for held-out site measurements.
"""
import argparse,json,math,random,statistics,sys,time
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/heating_ai'))
from heating_model import DEFAULT_MODEL,modulation_power,held_action,in_window,coast,step
from heating_stability import stable_plan
from heating_fallback import integrate_observation


def plant(room,floor,outside,kw,recuperator,bath,seconds,scale,gain=0.):
    def rates(r,f):
        exchange=.035*scale*(1+.015*abs(f-r))
        loss=.022/scale+.002*recuperator+.006*bath
        return exchange*(f-r)-loss*(r-outside)+gain,.48*kw-.13*(f-r)
    dt=seconds/3600
    a,b=rates(room,floor);c,d=rates(room+a*dt/2,floor+b*dt/2)
    e,f=rates(room+c*dt/2,floor+d*dt/2);g,h=rates(room+e*dt,floor+f*dt)
    return room+dt*(a+2*c+2*e+g)/6,floor+dt*(b+2*d+2*f+h)/6


def simulate(mode,case,new):
    rng=random.Random(3100+case);tz=ZoneInfo('Europe/Kiev')
    now=datetime(2026,10,30,5,tzinfo=tz);start=now.timestamp()
    model=dict(DEFAULT_MODEL,validated=True,version=6)
    settings=dict(target=21.5,lower=21,upper=22,night_target=21,night_lower=20.5,night_upper=21.5,
        start='23:00',end='07:00',sleep_start='23:00',sleep_end='06:30',strategy='Тільки в дозволені години',
        split_comfort=True,morning_min_minutes=30,day_price=4.32,night_price=2.16)
    outages=[dict(start=start+3600,end=start+7200)] if case%2 else []
    settings['outage_schedule']=dict(mode='normal',valid=True,emergency=False,outages=[])
    # Unknown outages remain a stress case; actual supply always overrides plan.
    legacy=dict(bands=[],intervals=0,minutes=0)
    profile=dict(legacy,physical_mode=mode) if new else legacy
    settings['modulation_profile']=profile
    room=21.3;floor=25.;last='off';changed=start-1000;reference=None;forecast=None
    energy=predicted=shadow_energy=on_hours=cold=overheat=0.;unsafe=0;steps=0;temperature_errors=[];shadow_errors=[];pred_room=shadow_room=room;pred_floor=shadow_floor=floor
    for seconds in range(0,24*3600,15):
        at=now+timedelta(seconds=seconds);stamp=at.timestamp()
        outside=8+case*.15+math.sin(seconds/5000)
        observed=room+rng.uniform(-.015,.015)
        if seconds in (0,3600,7200,7*3600,13*3600,18*3600):
            settings['actuator']=dict(action=last,changed_at=changed,safety_stop=False)
            forecast,reference=stable_plan(at,observed,floor,outside,0.,model,[],settings,3.,reference)
        requested=any(s['start']<=stamp<s['end'] for s in (forecast or {}).get('heat_segments',[]))
        upper=settings['night_upper'] if in_window(at,'23:00','06:30') else settings['upper']
        lower=settings['night_lower'] if in_window(at,'23:00','06:30') else settings['lower']
        supply=not any(o['start']<=stamp<o['end'] for o in outages)
        peak,_=coast(observed,floor,outside,0.,model)
        stop=not supply or not in_window(at,settings['start'],settings['end']) or observed>=upper or peak>upper
        action=held_action(last,changed,stamp,'heat' if requested and not stop else 'off',urgent=stop)
        if action!=last:changed=stamp;last=action
        active=action=='heat' and supply
        unsafe+=bool(active and (not in_window(at,'23:00','07:00') or observed>=upper))
        nominal=float(mode[-1]) if mode.startswith('fixed_') else 9. if floor<28 else 6. if floor<30 else 3.
        voltage=230+4*math.sin(seconds/800+case)
        duty=0. if seconds%600<15 else 1.
        watts=nominal*(voltage/230)**2*duty if active else 0.
        energy+=watts/240;predicted+=modulation_power(floor,profile)/240 if active else 0.
        shadow_energy+=modulation_power(floor,legacy)/240 if active else 0.
        on_hours+=1/240 if active else 0
        rec=(seconds//1800+case)%2;bath=seconds%2700<300
        gain=.08*max(0.,math.sin(math.pi*(at.hour-7)/11)) if 7<at.hour<18 else .12 if case%3==1 and at.hour in (18,19) else 0.
        room,floor=plant(room,floor,outside,watts,rec,bath,15,.95+case*.012,gain)
        pred_kw=modulation_power(pred_floor,profile) if active else 0.
        pred_room,pred_floor=step(pred_room,pred_floor,outside,pred_kw,0.,model,1/240)
        shadow_room,shadow_floor=step(shadow_room,shadow_floor,outside,modulation_power(shadow_floor,legacy) if active else 0.,0.,model,1/240)
        shadow_errors.append(abs(shadow_room-room))
        temperature_errors.append(abs(pred_room-room))
        cold+=max(0.,lower-room)/240;overheat+=max(0.,room-upper)/240;steps+=1
    return dict(mode=mode,case=case,new=new,actual_kwh=round(energy,5),same_execution_prediction_kwh=round(predicted,5),
        energy_error_kwh=round(abs(energy-predicted),5),temperature_end=round(room,4),
        same_execution_baseline_energy_error_kwh=round(abs(energy-shadow_energy),5),same_execution_baseline_temperature_mae=statistics.mean(shadow_errors),
        below_degree_hours=round(cold,6),overheat_degree_hours=round(overheat,6),unsafe_commands=unsafe,
        same_execution_temperature_mae=statistics.mean(temperature_errors),control_steps=steps,request_hours=round(on_hours,5))


def run():
    began=time.perf_counter();rows=[]
    for index,mode in enumerate(('fixed_3','fixed_6','fixed_9','auto')):
        for scenario in (0,1):
            case=index*2+scenario
            baseline=simulate(mode,case,False);new=simulate(mode,case,True)
            assert not baseline['unsafe_commands'] and not new['unsafe_commands']
            assert new['overheat_degree_hours']<=baseline['overheat_degree_hours']+.001
            assert new['below_degree_hours']<=baseline['below_degree_hours']+.001
            assert new['energy_error_kwh']<=new['same_execution_baseline_energy_error_kwh']+.001
            assert new['same_execution_temperature_mae']<=new['same_execution_baseline_temperature_mae']+.001
            rows.append(dict(baseline=baseline,new=new))
    ledger={};integrate_observation(ledger,0.,3000.,'measured')
    for stamp in range(15,3601,15):integrate_observation(ledger,float(stamp),3000.,'measured')
    assert abs(ledger['measured_kwh']-3.)<=.003
    return dict(scope='synthetic independent nonlinear plant; no physical equipment tests',seed=3100,cases=rows,
        fixed_baseline_energy_mae=statistics.mean(r['new']['same_execution_baseline_energy_error_kwh'] for r in rows if r['new']['mode']!='auto'),
        fixed_new_energy_mae=statistics.mean(r['new']['energy_error_kwh'] for r in rows if r['new']['mode']!='auto'),
        integrator_relative_error=abs(ledger['measured_kwh']-3.)/3,
        runtime_seconds=round(time.perf_counter()-began,3),
        limitations='Auto calibration and independent physical energy accuracy require newly tagged held-out sessions; unconfirmed models remain assumptions.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    result=run();encoded=json.dumps(result,indent=2,ensure_ascii=False)
    if args.output:args.output.write_text(encoded+'\n')
    print(encoded)

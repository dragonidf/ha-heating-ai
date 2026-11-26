"""Synthetic independent KWS forecast validation; never physical services."""
import unittest
from datetime import datetime,timezone
from heating_learning_windows import (freeze_forecast,qualify_frozen,score_frozen,
    qualify_power_candidate,power_promotion_allowed,_command_follows_plan)
from heating_model import DEFAULT_MODEL,step,modulation_power,certified_upper_power,retain_kws_profile

BASE=datetime(2026,11,1,tzinfo=timezone.utc).timestamp()
MODEL=dict(DEFAULT_MODEL,version=6,validated=True,floor_air=.035,room_loss=.01,floor_decay=.25,heat_gain=1.)
CANDIDATE=dict(physical_mode='fixed_6',equipment_version=1,power_source='kws_phases',
    electrical_validated=True,electrical_holdout_days=2,holdout_minutes=120,
    observed_upper_kw=6.,training_until=BASE-100,validated=False,
    bands=[dict(return_c=k,power_kw=4.5) for k in range(19,43,2)])
PROFILE=dict(CANDIDATE,validated=True,thermal_model_version=6,
             thermal_validation_status='passed',conservative_upper_kw=6.)
CONTEXT=dict(mode='fixed_6',equipment=1,calibrations={'candidates':{'fixed_6':CANDIDATE}})


def fixture(start,high=27.,extra=True):
    now=datetime.fromtimestamp(start,timezone.utc)
    weather=[dict(ts=start+i*3600,temperature=5.,condition='cloudy') for i in range(7)]
    settings=dict(target=22.,lower=15.,upper=high,equipment_version=1,
                  modulation_profile={'physical_mode':'fixed_6'})
    segments=[dict(start=start,end=start+1800,power_kw=6.)]
    curve=[];rows=[];room=actual=21.;floor=actual_floor=25.
    for i in range(73):
        at=start+i*300;active=i<6
        kw=modulation_power(actual_floor,PROFILE) if active else 0.
        endpoint=i in (0,12,36,72)
        rows.append(dict(ts=at,room=actual,room_valid=True,source='Вітальня',
            room_reported_at=at if endpoint else None,report_verified=endpoint,
            grid='on',voltage_qualified=True,power_source='kws_phases',power_quality='measured',
            power=kw,interval_kw=4.5 if 0<i<=6 else 0.,execution='automatic' if active else 'idle',
            manual_extra_active=False,outside=5.,fire=0.,fire_valid=True,fire_active=False,
            shadow_ventilation=[0.,0.],epoch='test',thermal_model_version=6,equipment_version=1,
            boiler_power_mode='fixed_6',weather_issue_at=at-60,solar_factor=0.,**{'return':actual_floor}))
        if extra:rows[-1].update(heating_requested=active,command_at=start if active else start+1800,command_confirmed=True)
        if i:curve.append(dict(ts=at,temperature=room,upper_temperature=room))
        room,floor=step(room,floor,5.,6. if active else 0.,0.,MODEL,1/12)
        actual,actual_floor=step(actual,actual_floor,5.,kw,0.,MODEL,1/12)
    snap=freeze_forecast(now,21.,25.,5.,0.,weather,start-60,settings,
                         dict(curve=curve,heat_segments=segments),MODEL,None,'test')
    return dict(snapshot=snap,checks=qualify_frozen(snap,rows,timezone.utc)),rows


class PowerForecastTest(unittest.TestCase):
    def test_command_trace(self):
        item,rows=fixture(BASE)
        times=[r['ts'] for r in rows]
        self.assertTrue(_command_follows_plan(rows,times,BASE,BASE+10800,item['snapshot']['segments']))
        self.assertTrue(all(c[-1]=={'command_verified':True} for c in item['checks']))
        self.assertIn('plan_execution_mismatch',item['checks'][0][8])
        self.assertFalse(score_frozen(item['snapshot'],item['checks'],timezone.utc)[0]['valid'])
        rows[3]['command_confirmed']=False
        self.assertFalse(_command_follows_plan(rows,times,BASE,BASE+10800,item['snapshot']['segments']))
        rows[3]['command_confirmed']=True
        rows[3]['command_at']+=1
        self.assertFalse(_command_follows_plan(rows,times,BASE,BASE+10800,item['snapshot']['segments']))

    def test_profile_gate_and_identity(self):
        archive=[fixture(BASE+d*86400+h*3600)[0] for d in range(4) for h in (0,4,8,12)]
        report=qualify_power_candidate(CONTEXT,archive,MODEL,timezone.utc)
        self.assertEqual(report['status'],'accepted')
        now=datetime.fromtimestamp(BASE+4*86400,timezone.utc)
        self.assertTrue(power_promotion_allowed(MODEL,report['profile'],report['gate'],{},now,'fixed_6',1))
        self.assertFalse(power_promotion_allowed(MODEL,report['profile'],report['gate'],{},now,'fixed_9',1))
        self.assertFalse(power_promotion_allowed(MODEL,report['profile'],report['gate'],
                         {'frozen_until':now.timestamp()+1},now,'fixed_6',1))
        retained=retain_kws_profile({},report['profile'],MODEL,1)
        self.assertEqual(retained['profiles']['fixed_6'],report['profile'])
        self.assertFalse(retain_kws_profile(retained,report['profile'],dict(MODEL,version=7),1)['profiles'])
        for ctx,ar in ((CONTEXT,archive[:2]),(dict(CONTEXT,equipment=2),archive),
                (CONTEXT,[fixture(BASE+d*86400+h*3600,extra=False)[0]
                          for d in range(4) for h in (0,4,8,12)])):
            self.assertNotEqual(qualify_power_candidate(ctx,ar,MODEL,timezone.utc)['status'],'accepted')

    def test_upper_safety(self):
        item,_=fixture(BASE,high=21.1)
        profile=dict(PROFILE,conservative_upper_kw=10.5)
        scored=score_frozen(dict(item['snapshot'],candidate={'power_profile':profile}),
                            item['checks'],timezone.utc)
        self.assertTrue(any(not r['guard_ok'] or not r['trajectory_guard_ok']
                            for r in scored if r['horizon'] in (1,3)))

    def test_uncertified_uses_nominal(self):
        self.assertIsNone(certified_upper_power(CANDIDATE))
        self.assertIsNone(certified_upper_power(dict(PROFILE,conservative_upper_kw=3.)))
        self.assertEqual(modulation_power(25,CANDIDATE),6.)

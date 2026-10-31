import unittest
import json
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from heating_environment import observe_floor,observe_gain,gain_at,reliability,anchor_weather,safe_upper
from heating_model import DEFAULT_MODEL,step,transitions,plan
from heating_stability import replay,stable_plan


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,10,14,6,10,tzinfo=ZoneInfo('Europe/Kiev'))
        self.model=dict(DEFAULT_MODEL,floor_air=.0442385,room_loss=.0128,floor_decay=.1728,heat_gain=.72)
        self.settings=dict(target=22,lower=21.5,upper=22.5,night_target=21.5,night_lower=21,
                           night_upper=22,sleep_start='23:00',sleep_end='07:00',start='23:00',end='07:00',
                           strategy='Тільки в дозволені години',split_comfort=True,morning_min_minutes=30)
    def test_outage_cold_probe_does_not_erase_stored_heat(self):
        old=dict(at=self.at.timestamp(),estimate=29.5,power_kw=0,last_heated_at=self.at.timestamp())
        state,value=observe_floor(old,self.at.timestamp()+4*3600,22.9,21.4,0,'off',self.model)
        self.assertGreater(value,25)
        self.assertLess(value,29.5)
        self.assertEqual(state['source'],'stored_heat_estimate')
    def test_restart_continuity(self):
        old=dict(at=self.at.timestamp(),estimate=29.5,power_kw=0,last_heated_at=self.at.timestamp())
        a,_=observe_floor(old,self.at.timestamp()+3600,22,22,0,'off',self.model)
        b,_=observe_floor(dict(a),self.at.timestamp()+7200,22,22,0,'off',self.model)
        c,_=observe_floor(old,self.at.timestamp()+7200,22,22,0,'off',self.model)
        self.assertAlmostEqual(b['estimate'],c['estimate'])
    def test_heating_probe_cannot_erase_a_warmer_energy_inventory(self):
        old=dict(at=self.at.timestamp(),estimate=29,power_kw=0)
        a,_=observe_floor(old,self.at.timestamp()+60,22,23,8,'on',self.model)
        b,_=observe_floor(a,self.at.timestamp()+300,22,26,8,'on',self.model)
        self.assertGreater(b['estimate'],26)
        self.assertTrue(b['energy_retained'])
    def test_grid_alone_is_not_circulation(self):
        old=dict(at=self.at.timestamp(),estimate=29,power_kw=0,last_heated_at=self.at.timestamp())
        _,value=observe_floor(old,self.at.timestamp()+300,22,21,0,'on',self.model)
        self.assertGreater(value,28)
    def test_provisional_sun_never_counts_as_guaranteed_future_heat(self):
        when=self.at.replace(hour=12)
        settings={'environment':{'solar':dict(coefficient=.6,validated=False)}}
        weather=[dict(ts=when.timestamp(),temperature=15,condition='sunny')]
        self.assertEqual(gain_at(when,weather,settings),0)
        self.assertGreater(gain_at(when,weather,settings,upper=True),0)
    def test_learned_gain_is_zero_at_night_and_unknown_weather(self):
        settings={'environment':{'solar':dict(coefficient=.6,validated=True)}}
        self.assertEqual(gain_at(self.at.replace(hour=0),[],settings),0)
        self.assertEqual(gain_at(self.at.replace(hour=12),[],settings),0)
    def test_gains_train_separately_and_require_multiple_days(self):
        state={}
        for day in range(3):
            for hour in range(9,16):
                when=self.at.replace(day=14+day,hour=hour)
                state=observe_gain(state,when.timestamp(),21+(hour-9)*.2,22,12,0,False,
                                   self.model,when.tzinfo,'sunny')
        self.assertTrue(state['validated'])
        self.assertEqual(state['days'],3)
    def test_hourly_room_update_not_repeated_as_five_minute_gain(self):
        state=observe_gain({},self.at.timestamp(),22,25,12,0,False,self.model,self.at.tzinfo,'sunny')
        for i in range(20):
            state=observe_gain(state,self.at.timestamp()+15*i,22,25,12,0,False,self.model,self.at.tzinfo,'sunny')
        self.assertEqual(state['observations'],[])
    def test_recent_bad_accuracy_cannot_stay_reliable(self):
        r=reliability([.03]*36+[.66]*12)
        self.assertFalse(r['reliable']);self.assertTrue(r['degraded'])
        self.assertEqual(r['upper_margin'],.3)
        self.assertTrue(reliability([.1]*12)['reliable'])
    def test_future_inertial_overshoot_invalidates_held_session(self):
        at=self.at.replace(hour=5,minute=0)
        seg=[dict(start=at.timestamp(),end=at.timestamp()+1800)]
        result=replay(at,21.9,30,12,0,self.model,[],dict(self.settings,upper_margin=.3),seg,True,9)
        self.assertIsNone(result)
    def test_daytime_and_outage_not_used_to_fit_floor_coefficients(self):
        a=dict(ts=100,room=22,return_=24,outside=12,power=0,quality=True,grid='off',
               environment_version=1,daylight_weight=1,**{'return':24})
        b=dict(a,ts=400,room=22.1)
        self.assertEqual(transitions([a,b]),[])
    def test_local_weather_correction_decays_and_keeps_provider_cache(self):
        at=self.at.timestamp()
        original=[dict(ts=at,temperature=12,condition='sunny'),dict(ts=at+86400,temperature=12)]
        corrected,bias=anchor_weather(original,at,15)
        self.assertEqual(bias,3)
        self.assertEqual(corrected[0]['temperature'],15)
        self.assertLess(corrected[1]['temperature'],12.01)
        self.assertEqual(original[0]['temperature'],12)
    def test_validated_sunny_day_changes_night_energy_plan(self):
        at=self.at.replace(hour=23,minute=0)
        s=dict(self.settings,day_price=2.64,night_price=1.32,
               environment={'solar':dict(coefficient=.25,validated=True)})
        weather=[dict(ts=at.timestamp()+i*3600,temperature=10,condition='sunny') for i in range(25)]
        cloudy=[dict(x,condition='cloudy') for x in weather]
        sunny_plan=plan(at,21.5,24,10,0,self.model,weather,s,6)
        cloudy_plan=plan(at,21.5,24,10,0,self.model,cloudy,s,6)
        self.assertLess(sunny_plan['energy'],cloudy_plan['energy'])
    def test_uncertainty_does_not_double_reduce_sleep_upper(self):
        s={'upper_margin':.3}
        self.assertEqual(safe_upper(21.5,'Сон',s),21.5)
        self.assertEqual(safe_upper(22,'День',s),21.7)
    def test_live_hot_afternoon_keeps_safe_morning_slot(self):
        x=json.loads(Path(__file__).with_name('environment_live_case.json').read_text())
        at=datetime.fromisoformat(x['at'])
        weather=[dict(ts=datetime.fromisoformat(r['datetime']).timestamp(),temperature=r['temperature'],condition=r.get('condition')) for r in x['weather']]
        weather,_=anchor_weather(weather,at.timestamp(),x['outside'])
        s=dict(self.settings,target=21.5,lower=21,upper=22,night_target=21,
               night_lower=20.5,night_upper=21.5,sleep_end='06:30',upper_margin=.3,
               day_price=2.64,night_price=1.32,modulation_profile=x['profile'])
        p=plan(at,x['room'],x['floor'],x['outside'],0,x['model'],weather,s,3)
        self.assertTrue(p['morning_runs_minutes'])
        self.assertTrue(all(m>=30 for m in p['morning_runs_minutes']))
        for shift in (7,14):
            q=plan(at+timedelta(minutes=shift),x['room'],x['floor'],x['outside'],0,x['model'],weather,s,3)
            self.assertTrue(q['morning_runs_minutes'],shift)
            self.assertTrue(all(m>=30 for m in q['morning_runs_minutes']))

if __name__=='__main__':unittest.main()

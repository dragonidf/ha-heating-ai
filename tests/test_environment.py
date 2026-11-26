import unittest
import json
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from heating_environment import observe_floor,observe_gain,gain_at,reliability,anchor_weather,safe_upper
from heating_model import DEFAULT_MODEL,step,transitions,plan
from heating_stability import replay,stable_plan


def solar_geometry(at):
    # Deterministic astronomical context, not private household telemetry.
    summer=at.month in (6,7)
    return {'latitude':50.35,'days':{(at+timedelta(days=i)).date().isoformat():dict(
        noon=(at+timedelta(days=i)).replace(hour=13 if summer else 12,minute=0,second=0).timestamp(),
        sunrise=(at+timedelta(days=i)).replace(hour=4 if summer else 7,minute=0,second=0).timestamp(),
        sunset=(at+timedelta(days=i)).replace(hour=21 if summer else 16,minute=13 if summer else 10,second=0).timestamp(),
        declination=23.44 if summer else -18.5) for i in range(5)}}


class AdaptiveSolarRegressions(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,11,15,12,tzinfo=ZoneInfo('Europe/Kyiv'))
        self.geometry=solar_geometry(self.at)
    def test_seasonal_sunset_night_and_summer_evening(self):
        from heating_environment import daylight
        self.assertEqual(daylight(self.at.replace(hour=17),self.geometry),0.)
        self.assertEqual(daylight(self.at.replace(hour=0),self.geometry),0.)
        summer=self.at.replace(month=6,day=21,hour=20)
        self.assertGreater(daylight(summer,solar_geometry(summer)),0.)
        self.assertEqual(daylight(self.at,{}),0.)
    def test_clouds_are_smooth_and_do_not_cross_hourly_coverage(self):
        from heating_environment import weather_factor
        values=[weather_factor('sunny',x) for x in (0,10,50,90,100)]
        self.assertEqual(values,sorted(values,reverse=True))
        self.assertLess(values[-1],values[0]*.25)
        self.assertEqual(weather_factor('cloudy',float('nan')),weather_factor('cloudy'))
        s={'environment':{'astronomy':self.geometry,'solar':dict(coefficient=.283,validated=True)}}
        weather=[dict(ts=self.at.timestamp(),temperature=8,condition='sunny',cloud_coverage=6)]
        self.assertGreater(gain_at(self.at,weather,s),0)
        self.assertEqual(gain_at(self.at+timedelta(hours=2),weather,s),0)
        self.assertEqual(gain_at(self.at+timedelta(days=1),weather,s),0)
    def test_local_sensor_sunspot_requires_persistent_synced_disagreement(self):
        from heating_environment import room_source_check
        t=self.at.timestamp()
        first=room_source_check({},t,[(24.9,t),(22.8,t),(23.,t)])
        self.assertEqual(first['status'],'pending')
        repeated=room_source_check(first,t+240,[(24.9,t),(22.8,t),(23.,t)])
        self.assertNotEqual(repeated['status'],'room_source_disagreement')
        final=room_source_check(first,t+240,[(24.9,t+240),(22.8,t+240),(23.,t+240)])
        self.assertEqual(final['status'],'room_source_disagreement')
        self.assertEqual(room_source_check(final,t+1000,[(24.9,t),(None,t),(23.,t)])['status'],'unconfirmed')
    def test_fast_bias_requires_new_reports_decays_and_expires_at_midnight(self):
        from heating_environment import observe_residual,residual_at
        t=self.at.timestamp()
        for change in (-.2,.2):
            state={}
            for at,room,reported in ((t,21.,t),(t+900,21.+change,t+900)):
                state=observe_residual(state,at,room,21.,8.,DEFAULT_MODEL,reported,True,[],{})
            self.assertGreater(state['rate']*change,0)
            self.assertLessEqual(abs(state['rate']),.4)
            repeated=observe_residual(state,t+915,22.,21.,8.,DEFAULT_MODEL,t+900,True,[],{})
            self.assertEqual(repeated['at'],state['at'])
            self.assertAlmostEqual(residual_at(self.at+timedelta(seconds=900+5400),state),state['rate']/2)
            self.assertEqual(residual_at(self.at+timedelta(days=1),state),0)
            invalid=observe_residual(state,t+920,22.,21.,8.,DEFAULT_MODEL,t+920,False,[],{})
            self.assertEqual(invalid['at'],state['at'])
            self.assertNotIn('last',invalid)
    def test_disagreement_cannot_identify_physical_model(self):
        from test_learning_limits import samples_for
        self.assertEqual(transitions([dict(r,room_source_disagreement=True) for r in samples_for()]),[])
    def test_upper_risk_is_separate_and_replay_rejects_unsafe_stored_heat(self):
        from heating_model import coast_trajectory,coast_exceeds_profile
        at=self.at.replace(hour=9)
        model=dict(DEFAULT_MODEL,floor_air=.044,room_loss=.0128)
        settings=dict(target=21.3,lower=21.,upper=21.35,start='00:00',end='23:59',
            strategy='Комфорт протягом доби',morning_min_minutes=0,
            environment={'astronomy':self.geometry,'solar':dict(validated=False,
                observed_gain=.8,observed_at=at.timestamp())})
        normal=coast_trajectory(at,21.1,22.,8.,0.,model,settings)
        upper=coast_trajectory(at,21.1,22.,8.,0.,model,settings,upper=True)
        self.assertLess(max(r['temperature'] for r in normal),settings['upper'])
        self.assertGreater(max(r['temperature'] for r in upper),settings['upper'])
        self.assertTrue(coast_exceeds_profile(at,21.1,22.,8.,0.,model,settings))
        self.assertIsNone(replay(at,21.1,22.,8.,0.,model,[],settings,
            [dict(start=at.timestamp(),end=at.timestamp()+900)],False,3.))
    def test_missing_observation_preserves_qualified_solar_not_pending_gain(self):
        from heating_environment import ENVIRONMENT_POLICY_VERSION
        t=self.at.timestamp()
        prior=dict(coefficient=.3,validated=True,commissioned=True,days=4,
                   factor_policy=ENVIRONMENT_POLICY_VERSION,last={'at':t-900},
                   pending_gain={'at':t-600},observed_gain=.2,observed_at=t-600)
        for fit in (False,True):
            state=observe_gain(prior,t,22.,22.,8.,0.,False,DEFAULT_MODEL,
                               self.at.tzinfo,quality=False,fit_coefficients=fit)
            self.assertTrue(state['validated'])
            self.assertFalse(state['observation_quality'])
            self.assertEqual(state['coefficient'],.3)
            self.assertEqual(state['days'],4)
            self.assertNotIn('last',state)
            self.assertNotIn('pending_gain',state)
        for invalid in ({**prior,'validated':False},{**prior,'factor_policy':-1}):
            state=observe_gain(invalid,t,22.,22.,8.,0.,False,DEFAULT_MODEL,
                               self.at.tzinfo,quality=False,fit_coefficients=False)
            self.assertFalse(state['validated'])

    def test_solar_candidate_cannot_replace_a_better_current_holdout(self):
        t=self.at.timestamp();rows=[]
        for day in range(3):
            for i in range(4):
                factor=.2+i*.05
                rows.append(dict(at=t-(2-day)*86400-i*60,day=(self.at-timedelta(days=2-day)).date().isoformat(),
                    gain=(.45 if day<2 else .3)*factor,factor=factor,stable=True,factor_policy=6))
        state=observe_gain(dict(coefficient=.3,commissioned=True,factor_policy=6,observations=rows),t,
            22.,22.,8.,0.,False,DEFAULT_MODEL,self.at.tzinfo,astronomy=self.geometry)
        self.assertEqual(state['coefficient'],.3)
    def test_first_block_post_stop_upper_risk_is_rejected(self):
        from heating_model import _plan
        at=self.at.replace(hour=9)
        settings=dict(target=21.12,lower=21.,upper=21.13,start='00:00',end='23:59',
            strategy='Комфорт протягом доби',night_price=1.,day_price=2.,
            actuator=dict(action='heat',changed_at=at.timestamp()-540),
            environment={'astronomy':self.geometry,'solar':dict(validated=False,
                observed_gain=.8,observed_at=at.timestamp())})
        self.assertIsNone(_plan(at,21.1,22.,8.,0.,dict(DEFAULT_MODEL,room_loss=.0128),[],settings,3.,hours_override=.5))
    def test_post_stop_peak_keeps_frozen_forecast_and_sensor_disagreement(self):
        from heating_recovery import HeatingRecovery
        app=HeatingRecovery();app.memory={'last_command':'heat'};app.model=DEFAULT_MODEL
        app.decision_context={'coast_peak':21.03,'coast_upper_peak':21.5}
        app.observe_stop_peak(self.at-timedelta(seconds=15),21.,3000.,True)
        app.memory['last_command']='off';app.observe_stop_peak(self.at,21.,0.,True)
        for seconds in range(15,10801,15):
            if seconds>=7200:app.memory['room_source_check']={'status':'room_source_disagreement'}
            app.decision_context['coast_peak']=30.  # Later replans cannot rewrite the STOP prediction.
            app.observe_stop_peak(self.at+timedelta(seconds=seconds),21.+1.4*seconds/10800,0.,True)
        record=app.memory['forecast_validation'][-1]
        self.assertEqual(record['predicted_peak'],21.03)
        self.assertAlmostEqual(record['measured_peak'],22.4)
        self.assertIn('room_source_disagreement',record['reasons'])
        self.assertFalse(record['valid'])
    def test_unverified_signed_drift_has_no_comfort_credit_on_heldout_days(self):
        from heating_environment import observe_residual,forecast_step
        model=dict(DEFAULT_MODEL,floor_air=0.,room_loss=0.,floor_decay=0.)
        for day in (16,17,18):
            at=self.at.replace(day=day)
            for rate in (-.2,.2):
                state=observe_residual({},at.timestamp()-900,21.-rate/4,21.,8.,model,
                                       at.timestamp()-900,True,[],{})
                state=observe_residual(state,at.timestamp(),21.,21.,8.,model,at.timestamp(),True,[],{})
                for hours in (1,3):
                    room,floor=21.,21.
                    for i in range(hours*12):
                        when=at+timedelta(minutes=i*5)
                        room,floor=forecast_step(room,floor,8.,0.,0.,model,1/12,when,[],
                            {'environment':{'residual':state}})
                    self.assertAlmostEqual(abs(room-(21.+rate*hours)),abs(rate*hours))
                    self.assertEqual(room,21.)  # Diagnostics are not evidence of real household MAE improvement.
    def test_coast_uses_interval_start_weather_at_cloud_boundary(self):
        from heating_model import coast_trajectory
        from heating_environment import forecast_step
        at=self.at.replace(minute=55)
        weather=[dict(ts=at.timestamp()-3300,temperature=8.,condition='sunny',cloud_coverage=0),
                 dict(ts=at.timestamp()+300,temperature=-4.,condition='cloudy',cloud_coverage=100)]
        settings={'environment_weather':weather,'environment':{'astronomy':self.geometry,
            'solar':dict(coefficient=.283,validated=True)}}
        rows=coast_trajectory(at,21.,22.,8.,0.,DEFAULT_MODEL,settings,hours=1/12,upper=True)
        expected,_=forecast_step(21.,22.,8.,0.,0.,DEFAULT_MODEL,1/12,at,weather,settings,upper=True)
        self.assertAlmostEqual(rows[-1]['temperature'],expected)
    def test_known_ventilation_difference_cannot_be_learned_as_sun(self):
        t=self.at.timestamp()
        settings=dict(thermal_model_version=1,ventilation_model=dict(validated=True,model_version=1,
            base_room_loss=.001,recuperator=0.,bath=0.),ventilation_expected=dict(recuperator=0.,bath=0.))
        model=dict(DEFAULT_MODEL,floor_air=0.)
        state=observe_gain({},t,22.,22.,8.,0.,False,model,self.at.tzinfo,'sunny',
                           reported_at=t,astronomy=self.geometry,settings=settings)
        state=observe_gain(state,t+1800,21.993,22.,8.,0.,False,model,self.at.tzinfo,'sunny',
                           reported_at=t+1800,astronomy=self.geometry,settings=settings)
        self.assertAlmostEqual(state.get('observed_gain',0.),0.,places=6)
    def test_frequent_reports_accumulate_without_reusing_old_reports(self):
        from heating_environment import observe_residual
        t=self.at.timestamp();state={}
        for i in range(6):
            state=observe_residual(state,t+i*60,21.+i*.01,21.,8.,DEFAULT_MODEL,t+i*60,True,[],{})
        self.assertEqual(state['at'],t+300)
    def test_hourly_weather_adapter_retains_only_valid_cloud_coverage(self):
        from test_adapter_modulation import HeatingClimate
        app=HeatingClimate();app.memory={};app.save=lambda:None
        app.weather_event(None,{'forecast':[dict(datetime=(self.at+timedelta(hours=i)).isoformat(),
            temperature=8.,condition='sunny',cloud_coverage=value) for i,value in enumerate((6,90,101,None))]})
        self.assertEqual([r.get('cloud_coverage') for r in app.weather],[6.,90.,None,None])


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,10,14,6,10,tzinfo=ZoneInfo('Europe/Kiev'))
        self.model=dict(DEFAULT_MODEL,floor_air=.0442385,room_loss=.0128,floor_decay=.1728,heat_gain=.72)
        self.settings=dict(environment={'astronomy':solar_geometry(self.at)},target=22,lower=21.5,upper=22.5,night_target=21.5,night_lower=21,
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
        settings={'environment':{'astronomy':solar_geometry(when),'solar':dict(coefficient=.6,validated=False)}}
        weather=[dict(ts=when.timestamp(),temperature=15,condition='sunny')]
        self.assertEqual(gain_at(when,weather,settings),0)
        self.assertEqual(gain_at(when,weather,settings,upper=True),0)  # Unconfirmed sunshine cannot remove night heat.
    def test_held_out_solar_risk_is_not_nominal_or_double_counted(self):
        from heating_environment import has_upper_risk
        from heating_model import ForecastContext
        when=self.at.replace(hour=12)
        settings={'environment':{'astronomy':solar_geometry(when),
            'solar':dict(coefficient=.6,validated=False,risk_coefficient=.5)}}
        weather=[dict(ts=when.timestamp(),temperature=15,condition='sunny')]
        self.assertTrue(has_upper_risk(settings))
        self.assertEqual(gain_at(when,weather,settings),0.)
        upper=gain_at(when,weather,settings,upper=True)
        self.assertGreater(upper,0.)
        self.assertLessEqual(upper,.5)
        self.assertEqual(ForecastContext(when,weather,15.,DEFAULT_MODEL,settings).at(when)[-1],(0.,upper))
        settings['environment']['solar'].update(observed_gain=upper,observed_at=when.timestamp())
        self.assertEqual(gain_at(when,weather,settings,upper=True),upper)
        self.assertEqual(gain_at(when.replace(hour=0),weather,settings,upper=True),0.)
        self.assertEqual(gain_at(when,[],{'environment':{'astronomy':solar_geometry(when),
                    'solar':dict(risk_coefficient=.5)}},upper=True),0.)

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
                                   self.model,when.tzinfo,'sunny',astronomy=solar_geometry(self.at))
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

    def test_conservative_peak_more_than_three_hours_after_heat_is_checked(self):
        at = self.at.replace(hour=0,minute=0)
        segment = dict(start=at.timestamp(),end=at.timestamp()+7200)
        settings = dict(self.settings,upper=21.8,night_upper=21.8,
                        thermal_model_version=1,
                        ventilation_model=dict(validated=True,model_version=1,
                                               base_room_loss=.005,recuperator=.02,bath=.005),
                        ventilation_expected=dict(recuperator=1.,bath=0.))
        nominal = replay(at,21,21,10,0,self.model,[],dict(settings,upper=26,night_upper=26),
                         [segment],True,6,reserve_checked=True)
        self.assertLessEqual(nominal['maximum'],21.8)
        self.assertIsNone(replay(at,21,21,10,0,self.model,[],settings,[segment],True,6,
                                 reserve_checked=True))
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
               environment={'astronomy':solar_geometry(at),'solar':dict(coefficient=.25,validated=True)})
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

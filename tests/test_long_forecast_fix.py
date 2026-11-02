import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch
import test_dual_power_accounting as dual
import test_inlet_accounting as inlet
import test_plan_accountability as accountability
from heating_recovery import comparison_page,unpack_history_items,wire_values
from heating_ui import forecast_outlook_for_panel


class LongForecastFixTests(unittest.TestCase):
    def test_compact_rows_survive_appdaemon_removal_of_null_array_elements(self):
        items=[dict(kind='actual',value=dict(ts=100,temperature=21.5)),
               dict(kind='actual',value=dict(ts=101,temperature=None,lower=None,upper=22.)),
               dict(kind='forecast',cycle_id='night',value=dict(ts=102,temperature=0.,target=None,heat_segments=[]))]
        def transport(value):
            if isinstance(value,list):return [transport(v) for v in value if v is not None]
            if isinstance(value,dict):return {k:transport(v) for k,v in value.items() if v is not None}
            return value
        page=transport(wire_values(comparison_page(items,0,'request','result',1,200,compact=True)))
        self.assertEqual(unpack_history_items(page),items)

    def test_fresh_primary_is_balance_and_both_counter_classes_survive_switch(self):
        base=dual.DualPowerTests();base.setUp()
        fixture=inlet.InletAccountingTests();fixture.setUp();app=fixture.app()
        app.cfg['power_inlet_config']=base.config
        def snapshot(stamp):
            app.power_snapshot_at=stamp
            return {**{e:dict(r,at=stamp) for e,r in base.readings.items()},
                'climate.boiler':dict(value='heat',at=stamp,target=25.,temperature=23.)}
        app.live_source_snapshot=snapshot
        for sec in (0,15,30):
            with patch('heating_climate.time.time',return_value=base.at+sec):app.update_heating_power()
        self.assertEqual(app.power_source,'balance')
        self.assertEqual(app.power_quality,'measured')
        self.assertFalse(app.fallback_interval)
        self.assertAlmostEqual(app.memory['inlet_energy']['measured_kwh'],.025)
        app.memory['inlet_energy']['estimated_kwh']=.1
        self.assertAlmostEqual(app.val('energy'),446.2802)
        self.assertFalse(app.published['sensor.heating_power_confirmed']['estimated'])
        self.assertFalse(app.published['sensor.heating_power_confirmed']['independent'])

    def test_endpoint_24h_is_checked_with_bracketing_reports_not_extrapolated_forecast(self):
        fixture=accountability.FrozenTemperatureTests();fixture.setUp()
        issued=fixture.at-timedelta(hours=12);start=issued.timestamp()
        forecast=dict(curve=[dict(ts=start+i*1800,temperature=21.,heat_segments=[]) for i in range(49)])
        app=fixture.app;app.temperature_quality=dict(primary_reported_at=start,cadence_seconds=3600.)
        fixture.observe(issued,room=21.,forecast=forecast)
        app.memory=json.loads(json.dumps(app.memory))
        app.temperature_quality['primary_reported_at']=start+23.5*3600
        fixture.observe(issued+timedelta(hours=23.5),room=20.8)
        app.temperature_quality['primary_reported_at']=start+24.5*3600
        fixture.observe(issued+timedelta(hours=24.5),room=21.2)
        result=next(p for p in app.memory['temperature_errors'] if p['horizon']==24)
        self.assertEqual(result['actual_horizon_hours'],24.)
        self.assertEqual(result['actual_temperature_basis'],'interpolated_reports')
        self.assertAlmostEqual(result['actual_temperature'],21.)
        self.assertEqual(result['error'],0.)

    def test_long_temperature_check_does_not_require_power_or_outage_schedule(self):
        fixture=accountability.FrozenTemperatureTests();fixture.setUp();app=fixture.app
        start=fixture.at.timestamp()
        app.temperature_quality=dict(primary_reported_at=start,cadence_seconds=3600.)
        forecast=dict(curve=[dict(ts=start+i*1800,temperature=21.,heat_segments=[]) for i in range(97)],schedule_complete=False)
        fixture.observe(fixture.at,room=21.,power=None,forecast=forecast)
        self.assertIn(36,[p['horizon'] for p in app.memory['temperature_checks']])
        app.temperature_quality['primary_reported_at']=start+36*3600
        fixture.observe(fixture.at+timedelta(hours=36),room=21.1,power=None)
        self.assertTrue(any(p['horizon']==36 for p in app.memory['temperature_errors']))

    def test_compact_history_is_lossless_bounded_and_needs_fewer_pages(self):
        items=[dict(kind='actual',value=dict(ts=1790000000+i*300,temperature=None if i%19==0 else 21.4,lower=21.,upper=22.)) for i in range(2314)]
        items += [dict(kind='forecast',cycle_id='night',value=dict(ts=1790000000+i*1800,temperature=21.2,heat_segments=[])) for i in range(624)]
        results=[]
        for compact in (False,True):
            offset=0;decoded=[];pages=0
            while offset is not None:
                page=comparison_page(items,offset,'request','result',1,2000000000,compact=compact)
                self.assertLessEqual(len(json.dumps(page).encode()),8192)
                decoded.extend(unpack_history_items(page))
                offset=page['next_offset'];pages+=1
            self.assertEqual(decoded,items);results.append(pages)
        self.assertLess(results[1],results[0]/2)

    def test_extended_outlook_uses_existing_curve_and_fits_recorder(self):
        fixture=accountability.FrozenTemperatureTests();fixture.setUp();start=fixture.at.timestamp()
        settings=dict(target=21.5,lower=21.,upper=22.)
        rows=[dict(ts=start+i*1800,temperature=21.2,heat_segments=[dict(start=start+(i-1)*1800,end=start+i*1800,kwh=3.,conditional=bool(i%2))],kwh=3.) for i in range(1,145)]
        original=copy.deepcopy(rows)
        outlook=forecast_outlook_for_panel(dict(_calculation_curve=rows,curve=rows[:48],plan_id='same'),fixture.at,settings)
        self.assertEqual(outlook['horizon_hours'],72.)
        self.assertEqual(len(outlook['curve']),144)
        self.assertEqual(outlook['plan_id'],'same')
        self.assertLess(len(json.dumps(outlook).encode()),16000)
        self.assertEqual(rows,original)

    def test_missing_reports_are_not_extrapolated_for_the_24h_endpoint(self):
        fixture=accountability.FrozenTemperatureTests();fixture.setUp();app=fixture.app
        start=fixture.at.timestamp();app.temperature_quality=dict(primary_reported_at=start,cadence_seconds=3600.)
        forecast=dict(curve=[dict(ts=start+i*1800,temperature=21.,heat_segments=[]) for i in range(49)])
        fixture.observe(fixture.at,room=21.,forecast=forecast)
        app.temperature_quality['primary_reported_at']=start+24.5*3600
        fixture.observe(fixture.at+timedelta(hours=24.5),room=21.1)
        self.assertFalse(any(p['horizon']==24 for p in app.memory['temperature_errors']))

    def test_compact_repeated_and_unknown_fields_preserve_every_original_value(self):
        items=[dict(kind='actual',value=dict(ts=0,temperature=None)),
               dict(kind='forecast',cycle_id='night',value=dict(ts=2,temperature=0.,heat_segments=[])),
               dict(kind='marker',value=dict(kind='grid_off',start=0,end=2)),
               dict(kind='actual',value=dict(ts=3,temperature=-1,extra='future schema'))]
        page=comparison_page(items,0,'one','immutable',0,10,compact=True)
        self.assertEqual(unpack_history_items(page),items)
        self.assertEqual(comparison_page(items,0,'one','immutable',0,10,compact=True),page)
        legacy=comparison_page(items,0,'old-browser','immutable',0,10)
        self.assertNotIn('encoding',legacy)
        self.assertEqual(legacy['items'],items)

    def test_history_worker_uses_published_snapshot_not_mutating_controller_memory(self):
        fixture=accountability.FrozenTemperatureTests();fixture.setUp();app=fixture.app
        app.history_read_snapshot={'old':dict(started_at=1),'__energy_cycles':{'safe':dict(start=1,end=2)}}
        app.now=lambda:fixture.at
        app.memory['temperature_history']={'live':dict(started_at=3)}
        captured=[];app.submit_to_executor=lambda fn,*args,**kwargs:captured.append(args[2])
        app.plan_history_requested('heating_plan_history',dict(request_id='reader',protocol=2,compact=True,start=1,end=2))
        self.assertIn('old',captured[0]);self.assertNotIn('live',captured[0])
        self.assertIn('safe',captured[0]['__energy_cycles'])

    def test_warm_read_reuses_immutable_pages_but_refresh_reads_archive(self):
        fixture=accountability.FrozenTemperatureTests();fixture.setUp();app=fixture.app;app.now=lambda:fixture.at
        app.history_results={'cached':dict(at=fixture.at.timestamp(),start=1,end=2,items=[dict(kind='actual',value={'ts':1,'temperature':21.})])}
        published=[];loaded=[]
        app.publish=lambda entity,state,**attrs:published.append(attrs)
        app.submit_to_executor=lambda *a,**kw:loaded.append(a)
        request=dict(request_id='first',protocol=2,compact=True,start=1,end=2)
        app.plan_history_requested('heating_plan_history',request)
        self.assertEqual(published[-1]['result_id'],'cached');self.assertFalse(loaded)
        app.plan_history_requested('heating_plan_history',{**request,'request_id':'refresh','refresh':True})
        self.assertEqual(len(loaded),1)


if __name__=='__main__':unittest.main()

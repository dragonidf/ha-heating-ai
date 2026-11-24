"""Compare Marketplace worker CPU and complete numerical outputs against v0.5.15."""

import argparse
from contextlib import ExitStack
from concurrent.futures import ProcessPoolExecutor
import cProfile
from datetime import datetime, timedelta
import importlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import pstats
import resource
import random
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
from uuid import UUID
from itertools import permutations
from zoneinfo import ZoneInfo
from zipfile import ZipFile


def snapshot(case):
    tz = ZoneInfo("Europe/Kyiv")
    now = datetime(2026, 11, 23, 21, 15, 24, tzinfo=tz)
    if case == "spring_dst":
        now = now.replace(month=3, day=28)
    elif case == "autumn_dst":
        now = now.replace(month=10, day=24)
    start = now.timestamp()
    weather = [dict(ts=start + h * 3600, temperature=4 + 4 * math.cos(h / 4),
                    condition="partlycloudy", cloud_coverage=(h * 17) % 100)
               for h in range(-1, 24)]
    days = {}
    for offset in range(5):
        at = now + timedelta(days=offset)
        noon = at.replace(hour=12, minute=0, second=0).timestamp()
        days[at.date().isoformat()] = dict(noon=noon, sunrise=noon-18000,
                                         sunset=noon+18000, declination=-17.)
    model = dict(floor_air=.0284, room_loss=.008192, floor_decay=.248832,
                 heat_gain=1.0368, fire_gain=.025, version=6, validated=True)
    settings = dict(
        start="23:00", end="07:00", strategy="Тільки в дозволені години",
        target=21.3, lower=20.8, upper=21.8, night_target=21.,
        night_lower=20.5, night_upper=21.5, sleep_start="23:00",
        sleep_end="06:30", split_comfort=True, morning_min_minutes=30,
        day_price=4.32, night_price=2.16,
        environment=dict(astronomy=dict(latitude=50., timezone=tz.key, days=days),
                         solar=dict(validated=True, coefficient=.19, observed_gain=.18,
                                    observed_at=start-600), last_heated_at=start-600),
        ventilation_model=dict(validated=True, model_version=6, base_room_loss=.007,
                               recuperator=.003, bath=.002),
        ventilation_expected=dict(recuperator=.75, bath=.25), thermal_model_version=6,
        actuator=dict(action="off", changed_at=start-752.25, safety_stop=False),
        outage_schedule=dict(valid=True, mode="scheduled", known_until=start+172800,
                             slots=[dict(start=start, end=start+172800, off=False)],
                             outages=[dict(start=start+8.75*3600, end=start+10.25*3600)]))
    profile = dict(validated=True, bands=[dict(return_c=t, power_kw=3.2-.04*(t-20))
                                         for t in (20, 22, 24, 26, 28, 30)])
    settings.update(modulation_profile=profile,
                    modulation_anchor=dict(power_kw=3.1, return_c=24.))
    if case == "unknown":
        settings["outage_schedule"] = dict(mode="unknown", valid=False, emergency=True)
        settings["ventilation_expected"] = dict(recuperator=None)
        settings["actuator"]["safety_stop"] = True
        weather = []
    elif case == "autumn_dst":
        weather[10:12] = reversed(weather[10:12])
        weather.insert(12, dict(weather[11], temperature=-3.))
    elif case == "spring_dst":
        settings.update(start="02:00", end="08:00", sleep_end="04:30")
    return now, 21.0, 24.0, 5., 2.5, model, weather, settings


def measure(package, case, profile_kind, progress=False, primary_only=False):
    worker = importlib.import_module(f"{package}.calculation_worker")
    stability = importlib.import_module(f"{package}.native_engine.heating_stability")
    serial = iter(range(1, 100))
    stability.uuid4 = lambda: UUID(int=next(serial))
    args = snapshot(case)
    outputs, timings, profiles = {}, {}, {}

    def run(label, function, *inputs, **kwargs):
        profiler = cProfile.Profile(timer=process_time) if label == profile_kind else None
        wall, cpu = perf_counter(), process_time()
        value = profiler.runcall(function, *inputs, **kwargs) if profiler else function(*inputs, **kwargs)
        timings[label] = (process_time()-cpu, perf_counter()-wall)
        if progress:
            print(f'{package}/{case}/{label}: {timings[label][0]:.3f} CPU s', file=sys.stderr, flush=True)
        if profiler:
            from io import StringIO
            report = StringIO()
            pstats.Stats(profiler, stream=report).strip_dirs().sort_stats("cumulative").print_stats(20)
            profiles[label] = report.getvalue()
        if function is worker.calculate:
            if "error" in value:
                raise RuntimeError(value["error"])
            value = value["result"]
        outputs[label] = value
        return value

    if profile_kind in (None, "stable_plan", "replay", "stress_scenarios"):
        plan, ref = run("stable_plan", worker.calculate, "plan", "plan-0", args, dict(power_kw=3.))
    if profile_kind is None and not primary_only:
        for i in (1, 2):
            at = args[0] + timedelta(minutes=5*i)
            room = stability.expected(ref, at.timestamp(), "temperature", "room")
            floor = stability.expected(ref, at.timestamp(), "return", "floor")
            _, ref = run(f"refresh_{i}", worker.calculate, "plan", f"plan-{i}",
                         (at, room, floor, *args[3:]), dict(power_kw=3., previous=ref))
    if profile_kind in (None, "recommend_power"):
        calibrations = None
        if case == "autumn_dst":
            calibrations = dict(profiles={
                f"fixed_{kw}": dict(validated=True, bands=[
                    dict(return_c=22., power_kw=kw*.94), dict(return_c=29., power_kw=kw*.97)])
                for kw in (3, 6, 9)})
        run("recommend_power", worker.calculate, "power_advice", "advice", args,
            dict(calibrations=calibrations) if calibrations is not None else {})
    if not primary_only and profile_kind in (None, "plan"):
        run("plan", stability.optimise, *args, power_kw=3.)
    if not primary_only and profile_kind in (None, "replay"):
        run("replay", stability.replay, *args, plan["heat_segments"],
            plan["split_comfort_applied"], 3., reserve_checked=True,
            window_extensions=stability.approved_extensions(args[-1], plan))
    if not primary_only and profile_kind in (None, "stress_scenarios"):
        run("stress_scenarios", stability.stress_scenarios, *args, plan, 3.)
    if profile_kind is None and not primary_only:
        timings["combined"] = tuple(sum(timings[k][i] for k in
                                    ("stable_plan", "refresh_1", "refresh_2", "recommend_power"))
                                    for i in range(2))
    return outputs, timings, profiles, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def package(root, destination, name):
    archive = destination / f"{name}.zip"
    subprocess.run([sys.executable, str(root / "tools/build_marketplace.py"),
                    "--output", str(archive)], cwd=root, check=True, stdout=subprocess.DEVNULL)
    target = destination / name
    with ZipFile(archive) as zip_file:
        for entry in zip_file.namelist():
            if entry.startswith("native_engine/") or entry == "calculation_worker.py":
                zip_file.extract(entry, target)
    # A blank carrier init keeps the worker independent of HA.
    (target / "__init__.py").touch()


def summary(samples):
    ordered = sorted(samples)
    return statistics.median(ordered), ordered[math.ceil(.95*len(ordered))-1]


def paired_statistics(pairs, seed=518, resamples=5000):
    """Resample complete pairs so nearby observations retain their covariance."""
    def relative(rows):
        left, right = zip(*rows)
        return summary(right)[1] / summary(left)[1] - 1

    rng = random.Random(seed)
    n = len(pairs)
    draws = sorted(relative(rng.choices(pairs, k=n)) for _ in range(resamples))
    return dict(p95_change=relative(pairs), median_paired_change=statistics.median(
        right / left - 1 for left, right in pairs),
        lower_95=draws[math.floor(.025 * resamples)],
        upper_95=draws[math.ceil(.975 * resamples)-1])


def controlled_gate(baseline, candidate, repeat, output, progress):
    if len(os.sched_getaffinity(0)) != 1 or os.getenv("PYTHONHASHSEED") != "0":
        raise ValueError("Run on one pinned CPU with PYTHONHASHSEED=0")
    if repeat < 30 or repeat % 6:
        raise ValueError("Use at least 30 samples, in complete blocks of six orders")
    variants = ("baseline", "control", "candidate")
    orders = list(permutations(variants))
    rng = random.Random(518)
    blocks = []
    for _ in range(repeat // 6):
        rng.shuffle(orders)
        blocks.extend(orders)
    with TemporaryDirectory() as folder, ExitStack() as stack:
        for variant in variants:
            package(candidate if variant == "candidate" else baseline, Path(folder), variant)
        sys.path.insert(0, folder)
        pool = stack.enter_context(ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context("spawn")))
        reference = None
        rows = []
        mixed_rss = 0
        for i, order in enumerate([variants, variants[::-1], variants, *blocks]):
            block = {}
            for variant in order:
                result, timing, _, rss = pool.submit(measure, variant, "scheduled", None,
                                                    False, True).result()
                encoded = json.dumps(result, sort_keys=True)
                if reference is None:
                    reference = encoded
                if encoded != reference:
                    raise AssertionError(f"Exact output mismatch for {variant}")
                block[variant] = timing
                mixed_rss = max(mixed_rss, rss)
            if i >= 3:
                rows.append(block)
                if progress:
                    print(f"Timed block {len(rows)}/{repeat}", flush=True)
        # Per-version process high-water marks, with identical fresh worker setup.
        rss_by_variant = {}
        for variant in ("baseline", "candidate"):
            with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as fresh:
                rss_by_variant[variant] = max(fresh.submit(measure, variant, "scheduled",
                    None, False, True).result()[3] for _ in range(3))
    operations = {}
    for label in rows[0]["baseline"]:
        a_a = paired_statistics([(r["baseline"][label][0], r["control"][label][0]) for r in rows])
        a_b = paired_statistics([(r["baseline"][label][0], r["candidate"][label][0]) for r in rows])
        summary_by_variant = {v: dict(cpu=summary([r[v][label][0] for r in rows]),
                                      wall=summary([r[v][label][1] for r in rows])) for v in variants}
        calibrated = (a_a["lower_95"] >= -.05 and a_a["upper_95"] <= .05
                      and abs(a_a["p95_change"]) <= .05)
        if not calibrated:
            gate = "inconclusive_noise"
        elif a_b["p95_change"] <= .05 and a_b["upper_95"] <= .05:
            gate = "pass"
        elif a_b["lower_95"] > .05:
            gate = "regression"
        else:
            gate = "inconclusive_effect"
        operations[label] = dict(variants=summary_by_variant, a_a=a_a, a_b=a_b, gate=gate)
    result = dict(cpu=sorted(os.sched_getaffinity(0)), exact_output_match=True,
                  mixed_rss_kib=mixed_rss, isolated_rss_kib=rss_by_variant,
                  operations=operations, orders=blocks, samples=rows)
    output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k not in ("orders","samples")}, indent=2))
    return all(item["gate"] == "pass" for item in operations.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--case", nargs="+", default=["scheduled", "unknown"],
                        choices=["scheduled", "unknown", "spring_dst", "autumn_dst"])
    parser.add_argument("--repeat", type=int, default=7, help="Timed samples after warm-up; 0 compares outputs only")
    parser.add_argument("--profile", nargs="+", choices=["stable_plan", "plan", "replay",
                        "stress_scenarios", "recommend_power"])
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--shared-worker", action="store_true",
                        help="Alternate revisions within one worker to control for process variance")
    parser.add_argument("--a-a", action="store_true", help="Package the baseline under both names")
    parser.add_argument("--primary-only", action="store_true", help="Measure only stable plan and power advice")
    parser.add_argument("--controlled-gate", type=Path,
                        help="Run interleaved A/A/B on the scheduled workload and write raw data")
    opts = parser.parse_args()
    if opts.controlled_gate:
        passed = controlled_gate(opts.baseline.resolve(), Path(__file__).resolve().parents[1],
                                 opts.repeat, opts.controlled_gate, opts.progress)
        raise SystemExit(0 if passed else 2)
    if opts.a_a and opts.baseline_only:
        parser.error("--a-a needs two variants")
    if opts.repeat < 0:
        parser.error("--repeat must be non-negative")
    variants = ("baseline",) if opts.baseline_only else ("baseline", "candidate")
    with TemporaryDirectory() as folder:
        destination = Path(folder)
        package(opts.baseline.resolve(), destination, "baseline")
        if not opts.baseline_only:
            source = opts.baseline.resolve() if opts.a_a else Path(__file__).resolve().parents[1]
            package(source, destination, "candidate")
        sys.path.insert(0, folder)
        with ExitStack() as stack:
            pool_names = ("shared",) if opts.shared_worker else variants
            pools = {name: stack.enter_context(ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context("spawn")))
                for name in pool_names}
            worker_for = {variant: pools["shared" if opts.shared_worker else variant]
                          for variant in variants}
            for case in opts.case:
                print(f"\n{case}", flush=True)
                outputs = {}
                peak_rss = {}
                samples = {variant: [] for variant in variants}
                for i in range(opts.repeat+1):
                    for variant in variants[::1 if i % 2 == 0 else -1]:
                        output, timings, _, peak_rss[variant] = worker_for[variant].submit(measure, variant, case, None, opts.progress, opts.primary_only).result()
                        outputs.setdefault("baseline", output)
                        if i:
                            samples[variant].append(timings)
                        # Exact equality across runs, including floats, curves and diagnostics.
                        for label, expected in outputs["baseline"].items():
                            if json.dumps(output[label], sort_keys=True) != json.dumps(expected, sort_keys=True):
                                raise AssertionError(f"{variant} differs from baseline: {case}/{label}")
                if not opts.baseline_only:
                    golden = outputs["baseline"]
                    passes = golden["stable_plan"][0].get("search_passes", [])
                    print("Exact output match, search widths:",
                          [p["beam_width"] for p in passes],
                          "| replay:", golden.get("replay") is not None,
                          "| stress cases:", len(golden.get("stress_scenarios", ())), flush=True)
                if not opts.repeat:
                    continue
                print("variant operation                  CPU median/p95 s   wall median/p95 s")
                for variant in variants:
                    for label in timings:
                        cpu, cpu95 = summary([row[label][0] for row in samples[variant]])
                        wall, wall95 = summary([row[label][1] for row in samples[variant]])
                        print(f"{variant:9} {label:22} {cpu:8.4f} {cpu95:8.4f} {wall:8.4f} {wall95:8.4f}")
                if opts.shared_worker:
                    print("Shared worker peak RSS (Linux KiB):", max(peak_rss.values()), flush=True)
                else:
                    print("Worker peak RSS (Linux KiB):", peak_rss, flush=True)
                for label in opts.profile or ():
                    for variant in variants:
                        _, _, profiles, _ = worker_for[variant].submit(measure, variant, case, label).result()
                        print(variant, label, profiles[label], flush=True)


if __name__ == "__main__":
    main()

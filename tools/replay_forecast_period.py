"""Private, fixed-input comparison of archived forecasts against measured reports.

Run only on a local copy of the installation data and checked-out source.
No HA connection, uploads, commands, or learning/promotion. Both revisions
receive identical issue-time inputs and recorded previous plan. This is NOT
a closed-loop walk-forward or proof of the effect of future model learning.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "ff95e14756f0bb6e7610fd87c459941b4b0ef4f0"
HORIZONS = (1, 3, 6, 12, 24, 36, 48)


def finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def matched_reports(snapshot, rows):
    """Match original issue timestamp, model, mode and real room report."""
    inputs = snapshot.get("inputs") or {}
    plan = snapshot.get("new_plan") or {}
    issue = finite(plan.get("issued_at"))
    settings = inputs.get("settings") or {}
    model = (inputs.get("model") or {}).get("version")
    mode = (settings.get("modulation_profile") or {}).get("physical_mode")
    equipment = settings.get("equipment_version")
    if issue is None or model is None or mode in (None, "unconfirmed"):
        return []
    unique, conflicting = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        horizon = finite(row.get("horizon"))
        at = finite(row.get("actual_at"))
        actual = finite(row.get("actual_temperature"))
        lead = finite(row.get("actual_horizon_hours"))
        if (row.get("issued_at") != issue or row.get("model_version") != model
                or row.get("physical_mode") != mode or row.get("actual_temperature_basis") != "sensor_report"
                or horizon not in HORIZONS or None in (at, actual, lead)
                or not 5 <= actual <= 40 or not issue < at
                or abs((at-issue)/3600-horizon) > .5 or abs(lead-(at-issue)/3600) > .01):
            continue
        observed_equipment = row.get("equipment_version")
        if observed_equipment is not None and equipment is not None and observed_equipment != equipment:
            continue
        provenance = "matched" if equipment is not None and observed_equipment == equipment else "unknown"
        key = (horizon, at)
        value = dict(horizon=horizon, issued_at=issue, actual_at=at, actual_temperature=actual,
                     model_version=model, physical_mode=mode, equipment_version=equipment,
                     equipment_provenance=provenance, forecast_temperature=finite(row.get("forecast_temperature")),
                     weather_known=plan.get("weather_complete"),
                     schedule_known=plan.get("schedule_complete"))
        if key in unique and unique[key] != value:
            conflicting.add(key)
        unique[key] = value
    return [value for key, value in sorted(unique.items()) if key not in conflicting]


def original_parity(reports, reproduced):
    """Archived original prediction is mandatory; replay may not substitute it."""
    if len(reports) != len(reproduced):
        raise ValueError("Mismatched worker response length")
    return [finite(row.get("forecast_temperature")) is not None and finite(value) is not None
            and abs(row["forecast_temperature"]-value)<=.005
            for row,value in zip(reports,reproduced)]


def pair_errors(reports, baseline, candidate):
    if len(reports) != len(baseline) or len(reports) != len(candidate):
        raise ValueError("Mismatched worker response length")
    return [dict(row, baseline_error=b-row["actual_temperature"], candidate_error=c-row["actual_temperature"])
            for row, b, c in zip(reports, baseline, candidate)
            if finite(b) is not None and finite(c) is not None]


def summarize_pairs(pairs, tz):
    groups = defaultdict(list)
    fields = ("horizon", "model_version", "physical_mode", "equipment_version",
              "equipment_provenance", "weather_known", "schedule_known")
    for row in pairs:
        groups[tuple(row.get(k) for k in fields)].append(row)
    output = []
    for key, rows in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        # Same real report cannot be counted twice in one cohort. Prefer
        # exact nominal lead; make ties deterministic.
        selected = {}
        for row in sorted(rows, key=lambda row: (
                abs((row["actual_at"]-row.get("issued_at", row["actual_at"]-row["horizon"]*3600))/3600-row["horizon"]),
                row.get("issued_at", 0))):
            selected.setdefault(row["actual_at"], row)
        unique = sorted(selected.values(), key=lambda row: row["actual_at"])
        result = dict(zip(fields, key))
        result.update(count=len(unique), days=len({datetime.fromtimestamp(row["actual_at"], tz).date() for row in unique}))
        independent = []
        previous_end = -math.inf
        for row in unique:
            if row.get("issued_at", -math.inf) >= previous_end:
                independent.append(row)
                previous_end = row["actual_at"]
        result["independent_count"] = len(independent)
        for name in ("baseline", "candidate"):
            errors = [row[name+"_error"] for row in unique]
            disjoint = [row[name+"_error"] for row in independent]
            result[name+"_mae"] = sum(map(abs, errors))/len(errors)
            result[name+"_bias"] = sum(errors)/len(errors)
            result[name+"_independent_mae"] = sum(map(abs, disjoint))/len(disjoint) if disjoint else None
        output.append(result)
    return output


def worker(source):
    sys.path.insert(0, str(source/"apps"/"heating_ai"))
    from heating_recovery import temperature_at, temperature_curve
    from heating_stability import stable_plan
    payload = json.load(sys.stdin)
    data = payload["inputs"]
    now = datetime.fromisoformat(data["now"]).astimezone(ZoneInfo(data["timezone"]))
    result, _ = stable_plan(now, data["room"], data["floor"], data["outside"], data["fire"],
                           data["model"], data["weather"], data["settings"], data["power_kw"],
                           data.get("previous"))
    rows = temperature_curve(result.get("_calculation_curve") or result.get("curve") or [])
    print(json.dumps([temperature_at(rows, at) for at in payload["targets"]], allow_nan=False))


def replay(source, inputs, reports):
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(source)],
        input=json.dumps(dict(inputs=inputs, targets=[r["actual_at"] for r in reports]), allow_nan=False),
        capture_output=True, text=True, timeout=180, check=True)
    return json.loads(result.stdout)


def snapshots(data, first, last, errors):
    """Read only full-recompute snapshots near retained issue timestamps."""
    sys.path.insert(0, str(ROOT/"apps"/"heating_ai"))
    from heating_recovery import DecisionJournal
    from bisect import bisect_left
    journal = DecisionJournal(data)
    issues = sorted(errors)
    start_day = datetime.fromtimestamp(first-48*3600, timezone.utc).date().isoformat()
    end_day = datetime.fromtimestamp(last, timezone.utc).date().isoformat()
    paths = list(journal.directory.glob("????-??-??.jsonl.gz"))
    paths.extend(journal.directory.glob("????-??-??.jsonl"))
    seen = set()
    # Archived prefix before the active file for a date. A duplicated
    # digest across interrupted rotation is filtered without dropping either.
    for path in sorted(paths,key=lambda p:(p.name[:10],p.suffix!=".gz")):
        day=path.name[:10]
        if not start_day <= day <= end_day:
            continue
        try:
            with (gzip.open(path,"rt") if path.suffix==".gz" else path.open()) as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        at = datetime.fromisoformat(row["recorded_at"]).timestamp()
                        # The journal append follows publication. Filter only
                        # by recorded issue time, never measured error value.
                        i = bisect_left(issues, at-300)
                        if i == len(issues) or issues[i] > at+300:
                            continue
                        if row.get("external_attributes") in (True,"true") and row.get("event") is None:
                            row = journal.read_snapshot(row["attributes_snapshot_id"])
                        if row.get("event") != "full_recompute":
                            continue
                        digest = row.get("snapshot_id")
                        if not digest or digest in seen:
                            continue
                        seen.add(digest)
                        snapshot = journal.read_snapshot(digest)
                        issue = (snapshot.get("new_plan") or {}).get("issued_at")
                        if issue in errors:
                            yield snapshot, None
                    except (ValueError, TypeError, KeyError, OSError, EOFError, AttributeError):
                        yield None, "unreadable_record"
        except (OSError, EOFError, UnicodeError):
            yield None, "unreadable_day"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_directory", type=Path, nargs="?")
    parser.add_argument("--baseline", type=Path, help="Local checkout of exact v0.5.24 baseline")
    parser.add_argument("--days", type=int, choices=(14,30), default=14)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve())
        return
    if not args.data_directory or not args.baseline:
        parser.error("A private data copy and baseline checkout are required")
    baseline = args.baseline.resolve()
    head = subprocess.check_output(["git","-C",str(baseline),"rev-parse","HEAD"],text=True).strip()
    if head != BASELINE:
        parser.error("Baseline must be exact v0.5.24 commit")
    if subprocess.check_output(["git","-C",str(baseline),"status","--porcelain","--untracked-files=no"],text=True).strip():
        parser.error("Baseline tracked files must be clean")
    candidate_head = subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
    if subprocess.check_output(["git","-C",str(ROOT),"status","--porcelain","--untracked-files=no"],text=True).strip():
        parser.error("Candidate tracked files must be clean")
    data = args.data_directory.resolve()
    state = json.loads((data/"state.json").read_text())
    rows = [r for r in state.get("temperature_errors",[]) if isinstance(r,dict)
            and finite(r.get("actual_at")) is not None and finite(r.get("issued_at")) is not None]
    if not rows:
        print(json.dumps(dict(status="insufficient_measured_reports",groups=[])))
        return
    last = max(r["actual_at"] for r in rows)
    first = last-args.days*86400
    errors = defaultdict(list)
    for row in rows:
        if first<=row["actual_at"]<=last:
            errors[row["issued_at"]].append(row)
    paired, rejected, selected = [], Counter(), set()
    matched_issues = set()
    for snapshot, error in snapshots(data, first, last, errors):
        if error:
            rejected[error]+=1
            continue
        issue = snapshot["new_plan"]["issued_at"]
        reports = matched_reports(snapshot, errors[issue])
        if not reports:
            rejected["no_qualified_reports"]+=1
            continue
        matched_issues.add(issue)
        bucket = int(issue//21600)
        if bucket in selected:
            continue
        selected.add(bucket)
        try:
            a = replay(baseline, snapshot["inputs"], reports)
            parity = original_parity(reports,a)
            rejected["original_forecast_mismatch"]+=parity.count(False)
            if not any(parity):
                continue
            b = replay(ROOT, snapshot["inputs"], reports)
            matched = [(row,old,new) for row,old,new,ok in zip(reports,a,b,parity) if ok]
            matches = pair_errors([r[0] for r in matched],[r[1] for r in matched],[r[2] for r in matched])
            paired.extend(matches)
            rejected["missing_paired_prediction"]+=len(matched)-len(matches)
        except (OSError, ValueError, subprocess.SubprocessError):
            rejected["replay_failed"]+=1
    output = dict(status="fixed_input_comparison" if paired else "insufficient_matched_evidence",
        baseline_commit=head, candidate_commit=candidate_head, original_parity_tolerance_c=.005,
        period_days=args.days, end_utc=datetime.fromtimestamp(last,timezone.utc).isoformat(),
        eligible_issue_count=len(errors), matched_issue_count=len(matched_issues), selected_issue_count=len(selected),
        rejected=dict(rejected), groups=summarize_pairs(paired,ZoneInfo("Europe/Kyiv")),
        scope="recorded model/settings/previous plan; first matched issue per six-hour block; no adaptive walk-forward",
        unsupported_horizons=[h for h in HORIZONS if not any(r["horizon"]==h for r in paired)])
    print(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == "__main__":
    main()

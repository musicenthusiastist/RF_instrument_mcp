import argparse
from dataclasses import replace
import json
from pathlib import Path

from .benchmark import run_pilot, run_session, source_hash
from .config import ROOT
from .plan import default_plan, plan_to_dict
from .scenarios import export_scenarios, scenarios
from .validation import validate_plan

DEMO_SUMMARY_FIELDS = ('scenario', 'scope', 'termination', 'calls', 'measurement_rows', 'eligible_complete',
                       'reported_decision', 'reference_decision', 'invalid_executions', 'adapter_errors')


def main():
    parser = argparse.ArgumentParser(description='Local OBUE prototype; no real instruments or LLM API calls.')
    parser.add_argument('command', choices=['validate', 'demo', 'scenarios', 'pilot', 'export-plan'])
    parser.add_argument('--output', default='outputs')
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--signal', choices=['analytical', 'nr'], default='analytical')
    parser.add_argument('--nr-directory', default=str(ROOT / 'outputs/nr_reference'))
    parser.add_argument('--scenario', help='NR: R01–R04 controlled cases; N01 is the clean baseline.')
    args = parser.parse_args()
    out = Path(args.output)
    plan = default_plan('near_channel_nr' if args.signal == 'nr' else 'full_analytical')

    if args.command == 'validate':
        errors = validate_plan(plan)
        print(json.dumps({'valid': not errors, 'violations': [v.__dict__ for v in errors]}, indent=2))
        if errors:
            raise SystemExit(1)
    elif args.command == 'export-plan':
        out.mkdir(parents=True, exist_ok=True)
        (out / 'valid_plan.json').write_text(json.dumps(plan_to_dict(plan), indent=2))
    elif args.command == 'scenarios':
        if args.signal == 'nr':
            parser.error('Scenario exports use analytical fixtures.')
        print(json.dumps({'exported': export_scenarios(out)}))
    elif args.command == 'pilot':
        if args.scenario:
            parser.error('pilot runs all frozen scenarios; omit --scenario')
        print(json.dumps(run_pilot(out, args.repetitions, signal=args.signal, nr_directory=args.nr_directory),
                         indent=2))
    else:
        _demo(parser, args, out)


def _find_case(parser, cases, scenario_id, message):
    matches = [c for c in cases if c.id == scenario_id]
    if not matches:
        parser.error(message)
    return matches[0]


def _demo(parser, args, out):
    """Run one condition-C session and save its full record."""
    case = scenarios()[0]
    spectrum = None
    if args.signal == 'nr':
        from .nr import iq_hashes, load_nr_spectra, nr_scenarios, nr_directory
        if args.scenario and args.scenario != 'N01':
            case = _find_case(parser, nr_scenarios(), args.scenario, 'Unknown NR scenario')
        else:
            case = replace(case, plan=default_plan('near_channel_nr'))
        input_directory = nr_directory(args.nr_directory, case.id)
        spectrum = load_nr_spectra(input_directory)
    elif args.scenario:
        case = _find_case(parser, scenarios(), args.scenario, 'Unknown analytical scenario')

    result = run_session(case, 'C', 0, spectrum=spectrum)
    out.mkdir(parents=True, exist_ok=True)
    result['source_hash'] = source_hash()
    if args.signal == 'nr':
        result['signal_hashes'] = iq_hashes(input_directory, spectrum)
    (out / 'deterministic_demo.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in DEMO_SUMMARY_FIELDS}, indent=2))
    if not result['eligible_complete'] or result['decision_error']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

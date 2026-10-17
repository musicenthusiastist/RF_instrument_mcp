"""Verify production NR IQ integration against MATLAB near-channel powers."""
import sys
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from obue.nr import verify_nr,NR_BENCHMARK

base=ROOT/'outputs/nr_reference'
result=verify_nr(base)
(base/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print('Clean NR: Python/MATLAB verification passed (400 windows).')
for case in NR_BENCHMARK['cases']:
    directory=ROOT/'outputs/nr_cases'/case['id']
    if not directory.is_dir():
        raise FileNotFoundError('Generate controlled cases with MATLAB export_nr_cases first.')
    result=verify_nr(directory)
    (directory/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    from obue.reference import evaluate_reference
    from obue.plan import default_plan
    from obue.nr import load_nr_spectra
    reference=evaluate_reference(load_nr_spectra(directory),default_plan('near_channel_nr'))
    margin=min(r['limit_dbm']-r['power_dbm'] for r in reference['rows'])
    if reference['decision']!=case['expected_decision'] or abs(margin-case['target_margin_db'])>NR_BENCHMARK['target_tolerance_db']:
        raise AssertionError('Controlled NR case does not meet its frozen target')
    print(f"{case['id']}: {reference['decision']}, minimum margin {margin:+.4f} dB; MATLAB agreement passed.")

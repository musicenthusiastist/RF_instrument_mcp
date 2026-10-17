"""Plot saved matching Python/MATLAB analytical integration results."""
import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from obue.spectrum import Spectrum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=ROOT/'outputs/reference/reference_cases.json')
    parser.add_argument('--matlab',type=Path,default=ROOT/'outputs/reference/matlab_verification.json')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/figure5')
    args=parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    reference=json.loads(args.reference.read_text())
    matlab=json.loads(args.matlab.read_text())
    reports={c['name']:c for c in matlab['cases']}
    tolerance=matlab['tolerance_db']
    if not math.isclose(tolerance,.10): raise ValueError('Expected 0.10 dB tolerance')
    results=[]; rows=[]
    for case in reference['cases']:
        report=reports[case['name']]
        windows=case['windows']; py=case['python_power_mw']; ma=report['matlab_power_mw']
        if not len(windows)==len(py)==len(ma)==report['windows']: raise ValueError('Mismatched window counts')
        spectrum=Spectrum(**case['spectrum'])
        errors=[]; current_errors=[]
        for i,((lo,hi),a,b) in enumerate(zip(windows,py,ma),1):
            if not (math.isfinite(a) and math.isfinite(b) and a>0 and b>0): raise ValueError('Nonpositive/nonfinite power')
            error=10*math.log10(a/b)
            current=spectrum.power_mw(lo,hi)
            current_error=10*math.log10(current/a)
            errors.append(error); current_errors.append(current_error)
            rows.append({'case':case['name'],'case_index':i,'lower_hz':lo,'upper_hz':hi,
                         'frequency_mhz':(lo+hi)/2e6,'python_power_mw':a,'matlab_power_mw':b,'error_db':error})
        maximum=max(map(abs,errors))
        if not math.isclose(maximum,report['max_error_db'],rel_tol=1e-9,abs_tol=1e-12): raise ValueError('Reported maximum inconsistent with saved powers')
        if maximum>tolerance: raise ValueError('Tolerance exceeded')
        results.append({'name':case['name'],'windows':len(windows),'computed_max_abs_error_db':maximum,
                        'matlab_report_max_error_db':report['max_error_db'],
                        'current_python_vs_saved_max_abs_error_db':max(map(abs,current_errors)), 'passed':True})
    if not matlab['passed']: raise ValueError('MATLAB report is not marked passed')
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'figure5_data.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,3,figsize=(11,3.8),sharey=True)
    obue=[r for r in rows if r['case']=='obue_660_filters']
    for ax,side in zip(axes[:2],('lower','upper')):
        selected=sorted([r for r in obue if (r['frequency_mhz']<3500 if side=='lower' else r['frequency_mhz']>3600)],key=lambda r:r['frequency_mhz'])
        if len(selected)!=330: raise ValueError('Expected 330 windows per side')
        ax.plot([r['frequency_mhz'] for r in selected],[r['error_db'] for r in selected],color='#2166ac',lw=1.5)
        ax.set_title(f'{side.capitalize()} side — 330 filters')
        ax.set_xlabel('Frequency (MHz)');ax.ticklabel_format(axis='x',style='plain',useOffset=False)
    small=[r for r in rows if r['case']=='partial_intervals_and_tones']
    axes[2].plot([r['case_index'] for r in small],[r['error_db'] for r in small],'o-',color='#2166ac',ms=4)
    axes[2].set_xticks([r['case_index'] for r in small]);axes[2].set_xlabel('Interval and tone case index')
    axes[2].set_title('Partial intervals and tones — 5 cases')
    for ax in axes:
        ax.axhline(tolerance,color='#b2182b',ls='--',lw=1.2,label='±0.10 dB tolerance')
        ax.axhline(-tolerance,color='#b2182b',ls='--',lw=1.2)
        ax.set_ylim(-.12,.12);ax.grid(alpha=.2)
        ax.text(.5,.60,'Maximum |error| = 0 dB',transform=ax.transAxes,ha='center',fontsize=9)
    axes[0].set_ylabel('Power error (dB)')
    fig.suptitle('Analytical power integration verification',fontsize=13)
    fig.text(.5,.025,'Error = 10 log₁₀(Python power / MATLAB power) | 665 matched analytical windows',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.07,1,.93))
    for ext in ('png','pdf','svg'): fig.savefig(args.output/f'figure5_analytical_verification.{ext}',dpi=300)
    summary={'error_definition':'10 log10(P_python / P_matlab)','tolerance_db':tolerance,
             'matlab_version':matlab['matlab_version'],'fixture_type':matlab['fixture_type'],
             'scope':'Saved MATLAB comparison; current Python fixture integration rechecked. MATLAB was not rerun.',
             'sources':{str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.reference,args.matlab)},'cases':results}
    (args.output/'verification_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__': main()

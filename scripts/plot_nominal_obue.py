"""Create Figure 4 from a current scripted demo; no instrument or LLM calls."""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from obue.domain import obue_limit_dbm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT/'outputs/demo_current/deterministic_demo.json')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/figure4')
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    data = json.loads(args.input.read_text(encoding='utf-8'))
    plan = data['public_task']['plan']
    if not data['eligible_complete'] or data['invalid_executions']:
        raise ValueError('Figure requires a complete valid nominal run.')
    if plan['scope']!='full_analytical':
        raise ValueError('Figure 4 uses the full analytical grid.')
    rows = []
    for call in data['trace']:
        if call['name'] == 'acquire':
            response = call['response']
            if isinstance(response, str):
                response = json.loads(response)
            if response.get('reference_plane')!='transmitter':
                raise ValueError('Expected transmitter-plane readings')
            rows.extend(response.get('readings', []))
    if len(rows) != data['measurement_rows'] or len(rows) != 1320:
        raise ValueError('Expected 1320 readings from the nominal full grid.')
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'figure4_data.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['model','side','frequency_mhz','offset_mhz','bandwidth_khz','tx_power_dbm','limit_dbm'])
        for r in rows:
            writer.writerow([r['test_model'],r['side'],r['frequency_hz']/1e6,r['offset_hz']/1e6,
                             r['measurement_bandwidth_hz']/1e3,r['value'],obue_limit_dbm(r['offset_hz'])])
    plt.rcParams.update({'font.size':10, 'axes.spines.top':False,'axes.spines.right':False})
    fig, axes = plt.subplots(2,2,figsize=(10,6.8),sharey=True)
    for col, side in enumerate(('lower','upper')):
        for row, zoom in enumerate((False,True)):
            ax = axes[row,col]
            for model, color, style in [('NR-FR1-TM1.1','#2166ac','-'),('NR-FR1-TM1.2','#d6604d','--')]:
                points = sorted([r for r in rows if r['side']==side and r['test_model']==model],key=lambda r:r['frequency_hz'])
                # Plot each bandwidth separately to avoid implying interpolation across the transition.
                for j,bw in enumerate((100000,1000000)):
                    part = [r for r in points if r['measurement_bandwidth_hz']==bw]
                    ax.plot([r['frequency_hz']/1e6 for r in part],[r['value'] for r in part],
                            color=color,linestyle=style,lw=1.6,label=model if j==0 else None)
            points = sorted([r for r in rows if r['side']==side and r['test_model']=='NR-FR1-TM1.1'],key=lambda r:r['frequency_hz'])
            for j,bw in enumerate((100000,1000000)):
                part = [r for r in points if r['measurement_bandwidth_hz']==bw]
                ax.plot([r['frequency_hz']/1e6 for r in part],[obue_limit_dbm(r['offset_hz']) for r in part],
                        color='black',lw=1.5,label='Configured OBUE limit' if j==0 else None)
            transition = 3490 if side=='lower' else 3610
            edge = 3500 if side=='lower' else 3600
            ax.axvline(transition,color='#777777',ls=':',lw=1.3,label='100 kHz / 1 MHz transition')
            ax.set_xlim((3488,3500) if side=='lower' else (3600,3612)) if zoom else ax.set_xlim((3260,3500) if side=='lower' else (3600,3840))
            ax.set_title(f'{side.capitalize()} side'+(' near-channel detail' if zoom else ' full extent'))
            ax.set_xlabel('Frequency (MHz)')
            ax.set_ylabel('Transmitter-plane power (dBm)')
            ax.grid(alpha=.2)
            ax.ticklabel_format(axis='x',style='plain',useOffset=False)
    handles, labels = axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',ncol=2,bbox_to_anchor=(.5,.03),frameon=False)
    fig.suptitle('Nominal analytical OBUE powers and limits',fontsize=13)
    fig.text(.5,.005,'N01 | condition C | corrected filter powers | both model labels share the analytical PSD',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.12,1,.95))
    for ext in ('png','pdf','svg'):
        fig.savefig(args.output/f'figure4_nominal_obue.{ext}',dpi=300)
    print(f'Figure 4 saved in {args.output.resolve()}')
    print('Powers were already path-corrected; no additional 40 dB correction was applied.')


if __name__ == '__main__':
    main()

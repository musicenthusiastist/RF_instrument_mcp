"""Plot filter powers from the four controlled NR IQ cases."""
import sys
from pathlib import Path
from math import log10
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from obue.nr import load_nr_spectra,NR_BENCHMARK
from obue.domain import obue_limit_dbm

offsets=list(range(50000,10000000,100000))
fig,axes=plt.subplots(1,2,figsize=(11,4.5),sharey=True)
colors=['#2166ac','#1b7837','#e08214','#b2182b']
loaded={item['id']:load_nr_spectra(ROOT/'outputs/nr_cases'/item['id']) for item in NR_BENCHMARK['cases']}
for ax,side in zip(axes,('lower','upper')):
    ordered=sorted((3500e6-off if side=='lower' else 3600e6+off,off) for off in offsets)
    for item,color in zip(NR_BENCHMARK['cases'],colors):
        for model,style in [('NR-FR1-TM1.1','-'),('NR-FR1-TM1.2',':')]:
            powers=[10*log10(loaded[item['id']][model].power_mw(f-50000,f+50000)) for f,_ in ordered]
            ax.plot([f/1e6 for f,_ in ordered],powers,color=color,ls=style,lw=1.1,
                    label=f"{item['id']}: {item['target_margin_db']:+g} dB target" if style=='-' else None)
    ax.plot([f/1e6 for f,_ in ordered],[obue_limit_dbm(off) for _,off in ordered],color='black',lw=1.5,label='OBUE limit')
    ax.set_title(f'{side.capitalize()} channel side')
    ax.set_xlabel('Frequency (MHz)');ax.grid(alpha=.2)
    ax.ticklabel_format(axis='x',style='plain',useOffset=False)
axes[0].set_ylabel('Transmitter-plane filter power (dBm)')
fig.suptitle('NR input with controlled unwanted emissions')
handles,labels=axes[0].get_legend_handles_labels()
fig.legend(handles,labels,loc='lower center',ncol=3,fontsize=9,frameon=False,bbox_to_anchor=(.5,.03))
fig.text(.5,.005,'100 kHz filters | solid: TM1.1; dotted: TM1.2 | synthetic tones, not a PA model',ha='center',fontsize=9)
fig.tight_layout(rect=(0,.17,1,.94))
out=ROOT/'outputs/nr_cases';fig.savefig(out/'nr_cases.png',dpi=300)
print(f'Saved: {out/"nr_cases.png"}')

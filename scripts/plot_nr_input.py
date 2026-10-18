"""Plot the actual MATLAB IQ records and PSD used by the NR backend."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from obue.nr import load_nr_spectra


def main():
    directory=ROOT/'outputs/nr_reference'
    spectra=load_nr_spectra(directory)
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(2,2,figsize=(11,7))
    for col,(model,spectrum) in enumerate(spectra.items()):
        stem=model.replace('.','_')
        data=json.loads((directory/f'{stem}_reference.json').read_text(encoding='utf-8'))
        raw=np.fromfile(directory/f'{stem}_iq_f32.bin',dtype='<f4').reshape(-1,2).astype(float)
        power=raw[:,0]**2+raw[:,1]**2
        fs=data['sample_rate_hz']; block=round(fs*1e-5); count=len(power)//block
        averaged=power[:count*block].reshape(count,block).mean(axis=1)
        time=(np.arange(count)+.5)*block/fs*1e3
        ax=axes[0,col]
        ax.plot(time,averaged/1000,color='#2166ac',lw=.8)
        gate=data['gate']
        ax.axvspan(gate['start_sample']/fs*1e3,gate['stop_sample_exclusive']/fs*1e3,
                   color='#d6604d',alpha=.3,label='0–0.5 ms measurement gate')
        ax.set_title(f'{model} — time-domain power')
        ax.set_xlabel('Time (ms)');ax.set_ylabel('Transmitter power (W)')
        ax.legend(fontsize=8,loc='upper right');ax.grid(alpha=.2)
        ax=axes[1,col]
        frequency=(np.asarray(spectrum.edges_hz[:-1])+np.asarray(spectrum.edges_hz[1:]))/2e6
        density=np.asarray(spectrum.psd_mw_hz)
        density_db=np.full_like(density,np.nan);positive=density>0
        density_db[positive]=10*np.log10(density[positive])
        ax.plot(frequency,density_db,color='#2166ac',lw=.8,label='Transmitter plane')
        ax.plot(frequency,density_db-40,color='#d6604d',lw=.8,label='Analyzer input (40 dB path loss)')
        for edge in (3500,3600):ax.axvline(edge,color='black',ls=':',lw=1)
        ax.set_title(f'{model} — gated input spectrum')
        ax.set_xlabel('Frequency (MHz)');ax.set_ylabel('PSD (dBm/Hz)')
        ax.ticklabel_format(axis='x',style='plain',useOffset=False)
        ax.grid(alpha=.2);ax.legend(fontsize=8,loc='lower center')
    fig.suptitle('MATLAB-generated NR signals used by the simulator',fontsize=13)
    fig.text(.5,.01,'100 MHz channel | 30 kHz SCS | 122.88 MS/s | no added PA distortion, noise or spurs',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.035,1,.95))
    output=ROOT/'outputs/nr_input';output.mkdir(exist_ok=True)
    fig.savefig(output/'nr_input.png',dpi=300)
    fig.savefig(output/'nr_input.pdf')
    print(f'Saved: {output/"nr_input.png"}')


if __name__=='__main__':main()

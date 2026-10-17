"""Export common inputs for an independent MATLAB integration implementation."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import json
from dataclasses import asdict
from obue.spectrum import Spectrum,make_obue_fixture
from obue.domain import expected_obue_offsets_hz,obue_measurement_bandwidth_hz

def main():
    s=make_obue_fixture(3);windows=[]
    for side in ('lower','upper'):
        for off in expected_obue_offsets_hz():
            center=3500e6-off if side=='lower' else 3600e6+off;bw=obue_measurement_bandwidth_hz(off)
            windows.append([center-bw/2,center+bw/2])
    small=Spectrum((0,10,20,30),(1,.2,3),((9,4),(20,2)))
    cases=[]
    for name,spectrum,w in [('obue_660_filters',s,windows),('partial_intervals_and_tones',small,[[0,10],[5,15],[10,20],[20,30],[8.5,20.5]])]:
        cases.append({'name':name,'spectrum':asdict(spectrum),'windows':w,
                      'python_power_mw':[spectrum.power_mw(a,b) for a,b in w]})
    out=ROOT/'outputs/reference';out.mkdir(parents=True,exist_ok=True)
    (out/'reference_cases.json').write_text(json.dumps({'cases':cases},indent=2))
    print(out/'reference_cases.json')

if __name__=='__main__':main()

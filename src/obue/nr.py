"""Load MATLAB IQ and estimate gated, two-sided PSD in transmitter mW/Hz."""
import json
import hashlib
from pathlib import Path

from .domain import TestModel
from .spectrum import Spectrum
from .config import ROOT

NR_BENCHMARK = json.loads((ROOT / 'requirements/nr_benchmark.json').read_text())
FFT_LENGTH = 16384


def nr_scenarios():
    """Controlled tone cases R01-R04, then the unit/setting probes on the reduced NR plan."""
    from dataclasses import replace
    from .plan import default_plan
    from .scenarios import Scenario, scenarios
    plan = default_plan('near_channel_nr')
    controlled = [Scenario(c['id'], 'nr_emission', 'Measure the declared NR grid.', 0, plan, {})
                  for c in NR_BENCHMARK['cases']]
    settings = [replace(c, plan=plan) for c in scenarios() if c.id in NR_BENCHMARK['setting_cases']]
    return controlled + settings


def nr_directory(base_directory, scenario_id):
    """Controlled cases live in a sibling nr_cases/<id> folder; all others use the clean records."""
    if scenario_id in {c['id'] for c in NR_BENCHMARK['cases']}:
        return Path(base_directory).parent / 'nr_cases' / scenario_id
    return Path(base_directory)


def _stem(model):
    return model.replace('.', '_')


def iq_path(directory, model):
    return Path(directory) / f'{_stem(model)}_iq_f32.bin'


def iq_hashes(directory, models):
    """SHA-256 of each model's raw IQ file, keyed by model name."""
    return {model: hashlib.sha256(iq_path(directory, model).read_bytes()).hexdigest() for model in models}


def _read_metadata(directory, model):
    return json.loads((Path(directory) / f'{_stem(model)}_reference.json').read_text())


def load_nr_spectra(directory):
    """Return {test model: Spectrum} from each model's first DL slot."""
    import numpy as np
    spectra = {}
    for model in TestModel:
        metadata = _read_metadata(directory, model.value)
        if metadata['model'] != model.value:
            raise ValueError('NR model metadata mismatch')
        path = iq_path(directory, model.value)
        if path.stat().st_size != metadata['samples'] * 8:
            raise ValueError('IQ length does not match metadata')
        raw = np.fromfile(path, dtype='<f4').reshape(-1, 2)  # interleaved float32 I, Q in sqrt(mW)
        if not np.isfinite(raw).all():
            raise ValueError('Nonfinite IQ samples')

        fs = metadata['sample_rate_hz']
        gate = metadata['gate']
        nfft = metadata['fft_length']
        start = gate['start_sample']
        stop = gate['stop_sample_exclusive']
        if not 0 <= start < stop <= len(raw) or nfft != FFT_LENGTH:
            raise ValueError('Invalid NR gate or FFT length')
        # First DL-active slot, recorded as zero-based sample indices.
        x = raw[start:stop, 0].astype(np.float64) + 1j * raw[start:stop, 1].astype(np.float64)
        blocks = len(x) // nfft
        if blocks < 1:
            raise ValueError('NR gate shorter than one FFT block')

        # Averaged periodogram over complete, nonoverlapping periodic-Hann blocks.
        w = .5 - .5 * np.cos(2 * np.pi * np.arange(nfft) / nfft)
        transformed = np.fft.fftshift(np.fft.fft(x[:blocks * nfft].reshape(blocks, nfft) * w, axis=1), axes=1)
        density = np.mean(np.abs(transformed) ** 2, axis=0) / (fs * np.sum(w * w))
        df = fs / nfft
        edges = metadata['carrier_center_hz'] + (np.arange(nfft + 1) - nfft / 2 - .5) * df
        spectra[model.value] = Spectrum(tuple(edges), tuple(density))
    return spectra


def verify_nr(directory):
    """Compare the production IQ path with separately calculated MATLAB powers."""
    from math import log10
    spectra = load_nr_spectra(directory)
    reports = []
    for model, spectrum in spectra.items():
        data = _read_metadata(directory, model)
        windows = data['windows']
        powers = data['matlab_power_mw']
        if len(windows) != 200 or len(powers) != 200:
            raise ValueError('Expected 200 near-channel windows per model')
        errors = [abs(10 * log10(spectrum.power_mw(lo, hi) / power)) for (lo, hi), power in zip(windows, powers)]
        maximum = max(errors)
        if maximum > .10:
            raise ValueError(f'{model}: MATLAB disagreement {maximum:.6g} dB')
        reports.append({'model': model, 'windows': len(windows), 'max_error_db': maximum,
                        'iq_sha256': iq_hashes(directory, [model])[model]})
    return {'scope': 'near_channel_nr', 'tolerance_db': .10, 'passed': True, 'models': reports}

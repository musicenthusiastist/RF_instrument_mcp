# OBUE simulation benchmark

Compare raw SCPI (A), primitive JSON tools (B), and RF-aware tools (C) on one
deterministic analyzer simulator. No hardware or LLM is connected yet.

## Setup

Run from this directory with Python 3.10 or newer:

```powershell
python -m pip install -e ".[test,reference]"
python scripts/verify_all.py
```

## Run

```powershell
# Known analytical spectrum, full 1320-window grid
python -m obue demo --output outputs/demo_current

# Separate MATLAB-generated TM1.1/TM1.2 IQ, reduced 400-window grid
python -m obue demo --signal nr --output outputs/demo_nr
python scripts/check_nr_references.py

# Controlled near-limit failure; R01/R02 pass, R03/R04 fail
python -m obue demo --signal nr --scenario R03 --output outputs/demo_nr_R03
python -m obue pilot --signal nr --output outputs/nr_pilot

# 18 analytical scenarios across A/B/C; scripted integration only
python -m obue pilot --output outputs/scripted_pilot
```

The terminal prints a compact summary. The saved deterministic_demo.json contains
the public task, measurement responses, state changes, and audit results.

## Measurement contract

- Readings are transmitter-plane dBm. The backend applies the fixed 40 dB
  external-path correction exactly once. Internal analyzer attenuation is separate.
- PASS requires all required valid readings at or below their limits. Any valid
  above-limit reading supports FAIL. Otherwise the result is invalid/incomplete.
- Calibration is assumed valid. Measurement uncertainty and receiver noise are
  excluded. The 0.10 dB verification tolerance does not relax the emission limit.
- C checks physical units, exact region RBW, RMS detection, power averaging,
  span coverage, and modeled input/mixer/reference-level headroom.
- Valid calculations integrate rectangular filters in linear power. Detector,
  averaging, and overload effects on invalid baseline acquisitions are declared
  study assumptions, not vendor performance or hardware protection guarantees.
- There are six nominal margin cases, six unit/scalar cases, and six RF-setting
  cases. No calibration-expiry or uncertainty scenarios remain.

## NR input

The saved MATLAB IQ files contain distinct TM1.1 and TM1.2 signals at
122.88 MS/s, with samples expressed in sqrt(mW). The loader computes a two-sided
PSD from the first 0.5 ms downlink slot using 16384-point periodic Hann FFTs.
Only complete nonoverlapping blocks are used; the remaining gate samples are
excluded and recorded in the reference metadata. The first slot is normalized
to 46 dBm. No additional transient exclusion or PA model is applied.

The frozen reduced grid covers 100 kHz filters within 10 MHz of both channel edges:
8 scans, 200 windows per model, 400 total. It is not full OBUE coverage. Unsupported
spectrum is rejected rather than padded with zero emissions. The waveform
generator records its own configured release; the current records use 17.8.0.

MATLAB can recalculate independent references from the existing IQ without
regenerating the waveforms:

```matlab
addpath('matlab');
verify_nr_iq('outputs/nr_reference');
```

To generate new waveforms, use export_nr_references('outputs/nr_reference') in
MATLAB R2024a with 5G Toolbox. It also updates the gated references. Run the Python
reference check afterward.

## Frozen NR scenarios

requirements/nr_benchmark.json fixes the grid and scenario list. Four controlled
IQ cases add tones at 3492.95 and 3607.05 MHz to each model's first DL slot.
The target is total integrated power, including the original NR emissions:

| Case | Target margin below the limit | Expected decision |
| --- | --- | --- |
| R01 | +3 dB | PASS |
| R02 | +0.2 dB | PASS |
| R03 | -0.2 dB | FAIL |
| R04 | -3 dB | FAIL |

MATLAB calibrates tones, saves float32 IQ, then recalculates reference powers
and checks actual margins within 0.01 dB. This is not a power amplifier model.
Original NR records remain unchanged. Case files contain only the measured
0.5 ms slot; their total power change is recorded.

Generate the cases after generating or changing the clean NR records:

```matlab
addpath('matlab');
export_nr_cases;
```

The NR pilot uses four emission cases plus six unit/scalar and six RF-setting
cases on clean NR input: 16 scenarios, 48 A/B/C sessions per repetition. N01 is
an optional clean demonstration. All outcomes refer to the reduced grid.
Tools never return target margins or expected labels.

```powershell
python scripts/check_nr_references.py
python scripts/plot_nr_cases.py
```

## MCP server

```powershell
python -m obue.mcp_server --condition C --scenario N01
python -m obue.mcp_server --condition C --signal nr
python -m obue.mcp_server --condition C --signal nr --scenario R03
```

The server waits for newline-delimited JSON-RPC requests on stdin. It prints no
startup banner. Initialize, notify initialized, list tools, and then call them.
B/C expose get_state, configure_scan, and acquire. A exposes execute_scpi.
Only one interface is active per process. Tool responses never include the
private reference decision. The NR server uses its fixed reduced plan and supports
R01–R04, U01–U06, S01–S06, and the optional clean N01 baseline. Restart to reset state.

A supports FREQ:CENT, FREQ:SPAN, BAND:RES, BAND:VID, INP:ATT,
DISP:TRAC:Y:RLEV, INP:GAIN:STAT, DET, AVER:TYPE and setting queries.
Use INIT:CONT OFF, INIT:IMM, *OPC?, and TRAC:DATA? TRACE1 for acquisition.
scan_index selects a benchmark region, not a vendor command suffix. The trace
contains study filter powers; the simulator does not emulate a vendor display.

Capability sources and modeling assumptions are in requirements/analyzer_capability.json.
Profile and limits are in requirements/obue_rules.json.

## Reproducibility and figures

Measurement seeds have been removed because readings are deterministic. A fixed
random seed still controls scripted pilot execution order and is recorded once
in its summary. MATLAB also fixes waveform-generation randomness. Source hashes
identify saved runs; full traces are retained for independent auditing.

```powershell
python -m obue export-plan --output examples
python -m obue scenarios --output scenarios
python scripts/plot_nominal_obue.py
python scripts/plot_reference_verification.py
```

Plotting requires matplotlib. Figure 4 uses the current analytical demo. Figure 5
compares the saved analytical Python/MATLAB integration reports. NR verification
is recorded separately in outputs/nr_reference/verification.json.

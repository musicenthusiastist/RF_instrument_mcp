# OBUE simulation benchmark

Claude controls a simulated R&S FSVA3000 through one of three interfaces and
measures NR OBUE on MATLAB-generated TM1.1/TM1.2 IQ: raw SCPI (A), primitive
JSON tools (B), or RF-aware tools (C). No hardware is connected.

## Setup

Run from this directory with Python 3.10 or newer:

Activate the venv in any new terminal.

```bash
.\.venv\Scripts\Activate.ps1
```

```bash
python -m pip install -e ".[test,reference,llm]"
python scripts/verify_all.py
```

The tests and the simulator read the IQ records in `outputs/nr_reference` and
`outputs/nr_cases`. These are git-ignored; keep a backup or regenerate them in
MATLAB (see below).

## Claude sessions

The harness gives Claude the public task and the MCP tools for one condition,
then scores the session with an independent audit, the evidence rule and a
private reference that Claude never sees. Claude must end with `DECISION: PASS`,
`FAIL` or `INVALID/INCOMPLETE`; anything else is recorded as `unparsed`.

```bash
# Put ANTHROPIC_API_KEY=... in .env (git-ignored); the harness loads it.
python -m obue.llm_harness --all --dry-run                   # prompt and tools; no API call
python -m obue.llm_harness --scenario R03 --condition C      # one paid session
python -m obue.llm_harness --scenario S01                    # one scenario under A, B and C
python -m obue.llm_harness --all --output outputs/baseline_opus_low_explicit_instructions  # 16 scenarios × A/B/C = 48 sessions
```

# Exp 1: Opus, neutral wording
python -m obue.llm_harness --all --instructions neutral --output outputs/opus_low_neutral
# Exp 2: Haiku, standard wording
python -m obue.llm_harness --all --model claude-haiku-5-5 --output outputs/haiku_low_standard
# Optional 4th cell: Haiku, neutral wording
python -m obue.llm_harness --all --model claude-haiku-5-5 --instructions neutral --output outputs/haiku_low_neutral

Defaults: claude-opus-5-5, effort low (fewest tokens; try `--effort medium`
if decisions look careless), 50 tool calls per session. `--model claude-sonnet-5-5`
halves the per-token price. When a budget runs out, Claude gets one final turn
without tools to report its decision. Each session JSON keeps the full
conversation, token usage and an estimated cost. Records add `evidence_decision`
(what the valid acquired readings support) next to Claude's `reported_decision`.
Refusals and API errors are recorded as terminations; no fallback model is used,
so every session is the named model. B/C acquisition responses are much larger
than A's comma-separated traces (about 29k versus 3k tokens for all 8 scans), so
context size differs by condition.

# 1. Smoke test first: one session (~$0.10) to confirm Haiku 4.5 works end to end
python -m obue.llm_harness --scenario U01 --condition A --model claude-haiku-4-5 --max-calls 200 --output outputs/haiku45_smoke

# 2. Standard wording
python -m obue.llm_harness --all --model claude-haiku-4-5 --max-calls 200 --output outputs/haiku45_standard_calls200

# 3. Neutral wording
python -m obue.llm_harness --all --model claude-haiku-4-5 --instructions neutral --max-calls 200 --output outputs/haiku45_neutral_calls200

# Standard wording: compare with haiku_low_standard (50 calls) to isolate the budget effect
python -m obue.llm_harness --all --model claude-haiku-5-5 --max-calls 200 --output outputs/haiku55_standard_calls200

# Neutral wording
python -m obue.llm_harness --all --model claude-haiku-5-5 --instructions neutral --max-calls 200 --output outputs/haiku55_neutral_calls200

### Preloaded start state

By default (`--start-state proposed`) the analyzer starts correctly configured and
the probe only appears as `proposed_settings` text in the task. With
`--start-state preloaded` the analyzer starts with the probe applied to all 8
scans and the task shows no proposal, so acquiring without checking the
instrument produces invalid data. Only probes an analyzer can actually hold are
used: U01, U02 and S01–S06; U03–U06 are skipped, and R01–R04 start correctly as
controls (12 scenarios, 36 sessions per run). Instructions default to `neutral`.
Summaries add `invalid_acquisition` (executed acquisitions taken in an invalid
state) and `acquisition_interception` (invalid acquisitions blocked), which
count acquisitions only; configure events can be flagged just because another
preloaded setting is still wrong.

`--instructions operator` (preloaded only) tells the controller that the
operator has already configured the analyzer, which invites it to acquire
without reconfiguring; this is the case condition C's acquisition gate exists
for. Under condition A, SCPI settings are global instrument state: one write
applies to every scan, and `scan_index` only selects the scan that `INIT:IMM`
sweeps and `TRAC:DATA?` reads. B and C configure each scan separately.

```bash
python -m obue.llm_harness --all --start-state preloaded --instructions operator --model claude-haiku-4-5 --max-calls 200 --repetitions 3 --output outputs/haiku45_preloaded_operator_rep3
python -m obue.llm_harness --all --start-state preloaded --instructions operator --model claude-haiku-5-5 --max-calls 200 --repetitions 3 --output outputs/haiku55_preloaded_operator_rep3
```

```bash
python -m obue.llm_harness --all --start-state preloaded --model claude-haiku-4-5 --max-calls 200 --repetitions 3 --output outputs/haiku45_preloaded_calls200_rep3
python -m obue.llm_harness --all --start-state preloaded --model claude-haiku-5-5 --max-calls 200 --repetitions 3 --output outputs/haiku55_preloaded_calls200_rep3
python -m obue.llm_harness --all --start-state preloaded --instructions operator --model claude-sonnet-5-5 --max-calls 200 --repetitions 3 --output outputs/sonnet55_preloaded_operator_rep3
```


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
  averaging, and overload effects on invalid acquisitions are declared study
  assumptions, not vendor performance or hardware protection guarantees.

## NR input

The saved MATLAB IQ files contain distinct TM1.1 and TM1.2 signals at
122.88 MS/s, with samples expressed in sqrt(mW). The loader computes a two-sided
PSD from the first 0.5 ms downlink slot using 16384-point periodic Hann FFTs.
Only complete nonoverlapping blocks are used; the remaining gate samples are
excluded and recorded in the reference metadata. The first slot is normalized
to 46 dBm. No additional transient exclusion or PA model is applied.

The frozen reduced grid covers 100 kHz filters within 10 MHz of both channel edges:
2 models × 2 sides × 2 regions = 8 scans, 200 windows per model, 400 total. The
far region (10.5–240 MHz offsets) is excluded because the sampled bandwidth only
reaches about ±61 MHz around the carrier, so this is not full OBUE coverage.
Unsupported spectrum is rejected rather than padded with zero emissions. The
waveform generator records its own configured release; the current records use 17.8.0.

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

requirements/nr_benchmark.json fixes the grid and scenario list. All 16
scenarios use the same correct plan; only the input signal and the proposed
settings in the public task differ.

| Scenarios | Signal | Proposed settings | Reference decision |
| --- | --- | --- | --- |
| R01–R04 | clean NR plus calibrated tones | none | R01/R02 PASS, R03/R04 FAIL |
| U01–U06 | clean NR | one unit/scalar error (e.g. RBW in dBm) | PASS |
| S01–S06 | clean NR | one RF-setting error (e.g. peak detector) | PASS |

The four controlled IQ cases add tones at 3492.95 and 3607.05 MHz to each model's
first DL slot. The target is total integrated power, including the original NR
emissions:

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

Tools never return target margins or expected labels.

```bash
python scripts/check_nr_references.py
python scripts/plot_nr_cases.py
python scripts/plot_nr_input.py
```

## MCP server

```bash
python -m obue.mcp_server --condition C                 # clean NR signal
python -m obue.mcp_server --condition C --scenario R03  # controlled tone case
```

The server waits for newline-delimited JSON-RPC requests on stdin. It prints no
startup banner. Initialize, notify initialized, list tools, and then call them.
B/C expose get_state, configure_scan, and acquire. A exposes execute_scpi.
Only one interface is active per process. Tool responses never include the
private reference decision. `--scenario` only selects the input signal; the
proposed settings live in the public task. Restart to reset state.

A supports FREQ:CENT, FREQ:SPAN, BAND:RES, BAND:VID, INP:ATT,
DISP:TRAC:Y:RLEV, INP:GAIN:STAT, DET, AVER:TYPE and setting queries.
Use INIT:CONT OFF, INIT:IMM, *OPC?, and TRAC:DATA? TRACE1 for acquisition.
scan_index selects a benchmark region, not a vendor command suffix. The trace
contains study filter powers; the simulator does not emulate a vendor display.

Capability sources and modeling assumptions are in requirements/analyzer_capability.json.
Profile and limits are in requirements/obue_rules.json.

## Reproducibility

Source hashes identify saved runs; full traces are retained for independent
auditing. A fixed seed sets the session order. Plotting requires matplotlib.
NR verification is recorded in outputs/nr_reference/verification.json.
`python scripts/build_schema.py` regenerates schemas/domain.schema.json.

"""Three adapters with the same configure/acquire/state operation surface.

A uses an R&S FSVA3000 command subset; no simulator-only SCPI commands.
B validates primitive shapes/bounds. C additionally applies physical/state rules.
"""
import json
import re

from .domain import AveragingDomain, Detector, Quantity, Unit
from .config import CAPABILITY

# Fixed unit of every numeric analyzer setting (B's bare numbers use these).
UNITS = {'center_frequency': Unit.HZ, 'span': Unit.HZ, 'rbw': Unit.HZ, 'vbw': Unit.HZ,
         'attenuation': Unit.DB, 'reference_level': Unit.DBM}
_vendor = CAPABILITY['vendor_supported']
_study = CAPABILITY['study_assumptions']
BOUNDS = {'center_frequency': (_vendor['frequency_min_hz'], _vendor['frequency_max_hz']),
          'span': (10, _vendor['frequency_max_hz']),
          'rbw': (_vendor['rbw_min_hz'], _vendor['rbw_max_hz']),
          'vbw': (_vendor['vbw_min_hz'], _vendor['vbw_max_hz']),
          'attenuation': (_vendor['attenuation_min_db'], _vendor['attenuation_max_db']),
          'reference_level': (_study['reference_level_min_dbm'], _study['reference_level_max_dbm'])}
FLAGS = ('preamp_enabled',)
ENUMS = {'detector': Detector, 'averaging_domain': AveragingDomain}
FREQUENCY_UNITS = ['Hz', 'kHz', 'MHz', 'GHz']

# SCPI short-form header -> analyzer setting, and SCPI enum token -> setting value.
SCPI_FIELDS = {'FREQ:CENT': 'center_frequency', 'FREQ:SPAN': 'span', 'BAND:RES': 'rbw', 'BAND:VID': 'vbw',
               'INP:ATT': 'attenuation', 'DISP:TRAC:Y:RLEV': 'reference_level', 'INP:GAIN:STAT': 'preamp_enabled',
               'DET': 'detector', 'AVER:TYPE': 'averaging_domain'}
SCPI_ENUM_VALUES = {'detector': {'RMS': 'true_rms', 'POS': 'peak', 'SAMP': 'sample'},
                    'averaging_domain': {'POW': 'power', 'LIN': 'power', 'LOG': 'log'}}
_SCPI_SETTING = re.compile(r':?([A-Z:]+)\s+(.+)', re.I)
_SCPI_NUMBER = re.compile(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)')

_SCPI_DESCRIPTION = (
    'FSVA3000 simulator implementing documented R&S short-form commands. '
    'Setting/query headers: ' + ', '.join(SCPI_FIELDS) + '. '
    'Append ? to read one setting. DET RMS/POS/SAMP; AVER:TYPE POW/LIN/LOG. '
    'Acquisition: INIT:CONT OFF, INIT:IMM, *OPC?, TRAC:DATA? TRACE1. '
    'Trace response is comma-separated dBm values for the selected benchmark scan. '
    'SYST:ERR? reads the error queue. '
    'scan_index (default 0) is MCP scan selection, not a vendor SCPI suffix.')


def scpi_enum_token(field, value):
    """First SCPI token for an enum setting value, e.g. ('averaging_domain', 'power') -> 'POW'."""
    return next(token for token, v in SCPI_ENUM_VALUES[field].items() if v == value)


def primitive(value):
    """Same information content; field units follow the published fixed-unit contract."""
    if isinstance(value, dict):
        if set(value) == {'value', 'unit'}:
            q = Quantity(value['value'], Unit(value['unit']))
            base = {'frequency': Unit.HZ, 'time': Unit.SECOND,
                    'absolute_power': Unit.DBM, 'level_ratio': Unit.DB}[q.dimension.value]
            return q.to(base).value
        return {k: primitive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [primitive(v) for v in value]
    return value


class Interface:
    def __init__(self, analyzer, condition):
        if condition not in ('A', 'B', 'C'):
            raise ValueError('condition must be A/B/C')
        self.analyzer = analyzer
        self.condition = condition
        self.calls = []   # full call log with before/after state hashes
        self.errors = []  # adapter errors; also the SCPI SYST:ERR? queue
        self.continuous_sweep = True

    # ---- Tool schemas -------------------------------------------------

    def tools(self):
        return [self._scpi_tool()] if self.condition == 'A' else self._json_tools()

    def _scan_index_schema(self):
        return {'type': 'integer', 'minimum': 0, 'maximum': len(self.analyzer.plan.scans) - 1}

    def _scpi_tool(self):
        return {'name': 'execute_scpi', 'description': _SCPI_DESCRIPTION,
                'inputSchema': {'type': 'object',
                                'properties': {'command': {'type': 'string'},
                                               'scan_index': self._scan_index_schema()},
                                'required': ['command'], 'additionalProperties': False}}

    def _json_tools(self):
        """B and C share three tools: C takes {value, unit} quantities, B fixed-unit numbers."""
        props = {'scan_index': self._scan_index_schema()}
        for name, (low, high) in BOUNDS.items():
            if self.condition == 'C' and name in UNITS:
                units = FREQUENCY_UNITS if UNITS[name] == Unit.HZ else [UNITS[name].value]
                props[name] = {'type': 'object',
                               'properties': {'value': {'type': 'number'}, 'unit': {'enum': units}},
                               'required': ['value', 'unit'], 'additionalProperties': False}
            else:
                props[name] = {'type': 'number', 'minimum': low, 'maximum': high}
        for name in FLAGS:
            props[name] = {'type': 'boolean'}
        for name, enum in ENUMS.items():
            props[name] = {'enum': [e.value for e in enum]}
        return [
            {'name': 'get_state',
             'description': 'Read analyzer settings and measurement scope. Fixed units in B: Hz, dB, dBm.',
             'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
            {'name': 'configure_scan',
             'description': ('Set scan state. B uses fixed Hz/dB/dBm. C uses quantities and rejects invalid '
                             'physical/state combinations before dispatch. This study requires RBW equal to '
                             'the prescribed measurement bandwidth.'),
             'inputSchema': {'type': 'object', 'properties': props, 'required': ['scan_index'],
                             'additionalProperties': False}},
            {'name': 'acquire',
             'description': ('Acquire one scan; powers are transmitter-plane dBm with fixed path correction '
                             'applied. C checks measurement eligibility. No verdict is returned.'),
             'inputSchema': {'type': 'object', 'properties': {'scan_index': props['scan_index']},
                             'required': ['scan_index'], 'additionalProperties': False}},
        ]

    # ---- Dispatch -----------------------------------------------------

    def call(self, name, args):
        """Run one tool call; errors become {'error': ...} responses, never exceptions."""
        record = {'name': name, 'arguments': args, 'before': self.analyzer.snapshot()}
        try:
            result = self._dispatch(name, args)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            result = {'error': str(exc)}
            self.errors.append(str(exc))
        record['response'] = result
        record['after'] = self.analyzer.snapshot()
        self.calls.append(record)
        return result

    def _dispatch(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('arguments must be an object')
        if self.condition == 'A':
            if (name != 'execute_scpi' or 'command' not in args or set(args) - {'command', 'scan_index'}
                    or not isinstance(args['command'], str)):
                raise ValueError('expected execute_scpi(command, scan_index=0)')
            result = self._scpi(args['command'], args.get('scan_index', 0))
        elif name == 'get_state' and not args:
            result = self.analyzer.state()
        elif name == 'acquire' and set(args) == {'scan_index'}:
            result = self.analyzer.acquire(args['scan_index'], enforce=self.condition == 'C')
        elif name == 'configure_scan':
            result = self._configure(args)
        else:
            raise ValueError('unknown tool or invalid arguments')
        if self.condition != 'C':
            result = primitive(result)
        if self.condition == 'A' and not isinstance(result, str):
            result = json.dumps(result, sort_keys=True)
        return result

    # ---- Settings -----------------------------------------------------

    def _configure(self, args, raw=False):
        """Check argument shapes, then pass the changes to the analyzer (C enforces rules).

        raw=True is the SCPI path, where a unit suffix arrives as a Quantity.
        """
        if 'scan_index' not in args:
            raise ValueError('scan_index required')
        changes = {}
        for name, value in args.items():
            if name == 'scan_index':
                continue
            if name in UNITS:
                changes[name] = self._quantity_argument(name, value, raw)
            elif name in FLAGS:
                if not isinstance(value, bool):
                    raise ValueError(name + ' requires boolean')
                changes[name] = value
            elif name in ENUMS:
                changes[name] = ENUMS[name](value)
            else:
                raise ValueError('unknown parameter ' + name)
        if not changes:
            raise ValueError('at least one setting required')
        return self.analyzer.configure(args['scan_index'], changes, enforce=self.condition == 'C')

    def _quantity_argument(self, name, value, raw):
        if self.condition == 'C':
            if not isinstance(value, dict) or set(value) != {'value', 'unit'}:
                raise ValueError('quantity requires value and unit')
            quantity = Quantity(value['value'], Unit(value['unit']))
            numeric = quantity.to(UNITS[name]).value
        elif raw and isinstance(value, Quantity):
            quantity = value
            numeric = quantity.to(UNITS[name]).value
        else:
            # Bare numbers (B, or SCPI without a suffix) are already in the fixed unit.
            quantity = Quantity(value, UNITS[name])
            numeric = quantity.value
        low, high = BOUNDS[name]
        if not low <= numeric <= high:
            raise ValueError(name + ' outside scalar bounds')
        return quantity

    # ---- SCPI (condition A) -------------------------------------------

    def _scpi(self, command, index=0):
        text = command.strip()
        upper = text.upper()
        if upper == 'SYST:ERR?':
            return '-200,' + json.dumps(self.errors.pop(0)) if self.errors else '0,"No error"'
        if upper in ('INIT:CONT OFF', 'INIT:CONT 0'):
            self.continuous_sweep = False
            return ''
        if upper in ('INIT:IMM', 'INIT'):
            if self.continuous_sweep:
                raise ValueError('use INIT:CONT OFF for single-sweep acquisition')
            return _scpi_reply(self.analyzer.acquire(index))
        if upper == '*OPC?':
            return '1'  # Analytical acquisition completes synchronously.
        if upper == 'TRAC:DATA? TRACE1':
            if index not in self.analyzer.results:
                raise ValueError('no acquired trace for this scan; run INIT:IMM first')
            return ','.join(str(r['value']) for r in self.analyzer.results[index]['readings'])
        if text.endswith('?'):
            return self._scpi_query(text[:-1], index)
        return self._scpi_set(text, index)

    def _scpi_query(self, header_text, index):
        header = _strip_sense(header_text.upper().lstrip(':'))
        if header not in SCPI_FIELDS:
            raise ValueError('undefined FSVA3000 simulator query')
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.analyzer.plan.scans):
            raise ValueError('invalid scan index')
        field = SCPI_FIELDS[header]
        value = getattr(self.analyzer.plan.scans[index].analyzer, field)
        if field in UNITS:
            return str(value.to(UNITS[field]).value)
        if field in FLAGS:
            return '1' if value else '0'
        return scpi_enum_token(field, value.value)

    def _scpi_set(self, text, index):
        match = _SCPI_SETTING.fullmatch(text)
        if not match:
            raise ValueError('undefined FSVA3000 simulator command')
        header = _strip_sense(match[1].upper())
        if header not in SCPI_FIELDS:
            raise ValueError('undefined FSVA3000 simulator command')
        field = SCPI_FIELDS[header]
        value = _parse_scpi_value(field, match[2].strip())
        return _scpi_reply(self._configure({'scan_index': index, field: value}, raw=True))


def _strip_sense(header):
    """The optional SENSe: root is implied for these headers."""
    return header[5:] if header.startswith('SENS:') else header


def _scpi_reply(result):
    """Successful SCPI writes reply with nothing; failures return the backend message."""
    return result if 'error' in result or result.get('blocked') else ''


def _parse_scpi_value(field, text):
    upper = text.upper()
    if field in FLAGS:
        if upper not in ('ON', 'OFF', '1', '0'):
            raise ValueError('expected ON/OFF')
        return upper in ('ON', '1')
    if field in ENUMS:
        if upper not in SCPI_ENUM_VALUES[field]:
            raise ValueError('unsupported FSVA3000 enum value')
        return SCPI_ENUM_VALUES[field][upper]
    match = _SCPI_NUMBER.fullmatch(text)
    if not match:
        raise ValueError('invalid scalar')
    number = float(match[1])
    if not match[2]:
        return number
    unit = next((u for u in Unit if u.value.upper() == match[2].upper()), None)
    if field not in UNITS or unit is None:
        raise ValueError('unsupported unit suffix')
    return Quantity(number, unit)

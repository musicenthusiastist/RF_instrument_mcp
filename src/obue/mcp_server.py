"""Minimal FSVA3000 simulator MCP server. JSON-RPC only on stdout.

Implements initialize, initialized notification, ping, tools/list, tools/call.
The only exposed capabilities are analyzer tools. No LLM or scoring is run here.
"""
import argparse
import json
import sys

from .config import ROOT
from .interfaces import Interface
from .nr import load_nr_spectra, nr_directory
from .plan import default_plan
from .scenarios import START_STATES, initial_plan, scenarios
from .simulator import Analyzer

PROTOCOL_VERSIONS = ('2025-03-26', '2025-06-18', '2025-11-25')
SERVER_INFO = {'name': 'fsva3000-obue-simulator', 'version': '0.5.0'}


class Server:
    def __init__(self, interface):
        self.interface = interface
        self.initialized = False

    def handle(self, request):
        """Return the JSON-RPC response for one request, or None for a notification."""
        ident = request.get('id')
        method = request.get('method')
        params = request.get('params', {})

        def error(code, msg):
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': msg}}

        if request.get('jsonrpc') != '2.0':
            return error(-32600, 'Invalid Request')
        if ident is None:
            return None
        if not isinstance(params, dict):
            return error(-32602, 'Invalid params')
        if method == 'initialize':
            self.initialized = True
            requested = params.get('protocolVersion')
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1]
            result = {'protocolVersion': version, 'capabilities': {'tools': {}}, 'serverInfo': SERVER_INFO}
        elif method == 'ping':
            result = {}
        elif not self.initialized:
            return error(-32000, 'Initialize first')
        elif method == 'tools/list':
            result = {'tools': self.interface.tools()}
        elif method == 'tools/call':
            name = params.get('name')
            if name not in {t['name'] for t in self.interface.tools()}:
                return error(-32602, 'Unknown tool')
            result = self._call_tool(name, params.get('arguments', {}))
        else:
            return error(-32601, 'Method not found')
        return {'jsonrpc': '2.0', 'id': ident, 'result': result}

    def _call_tool(self, name, arguments):
        response = self.interface.call(name, arguments)
        try:
            payload = json.loads(response) if isinstance(response, str) else response
        except ValueError:
            payload = response  # Raw SCPI includes CSV and empty replies.
        failure = isinstance(payload, dict) and ('error' in payload or payload.get('blocked', False))
        text = response if isinstance(response, str) else json.dumps(response)
        result = {'content': [{'type': 'text', 'text': text}], 'isError': bool(failure)}
        if isinstance(response, dict):
            result['structuredContent'] = response
        return result


def main():
    parser = argparse.ArgumentParser(description='R&S FSVA3000 NR simulator over stdio MCP; no hardware or LLM.')
    parser.add_argument('--condition', choices=['A', 'B', 'C'], default='C')
    parser.add_argument('--scenario', choices=[s.id for s in scenarios()],
                        help='Selects the input signal: R01-R04 use controlled tone IQ; '
                             'others (or none) use the clean NR records.')
    parser.add_argument('--nr-directory', default=str(ROOT / 'outputs/nr_reference'))
    parser.add_argument('--start-state', choices=START_STATES, default='proposed',
                        help="'preloaded' starts the analyzer with the scenario's probe on every scan.")
    args = parser.parse_args()
    if args.start_state == 'preloaded' and args.scenario is None:
        parser.error('--start-state preloaded needs --scenario')

    spectrum = load_nr_spectra(nr_directory(args.nr_directory, args.scenario))
    plan = default_plan()
    if args.scenario:
        case = next(s for s in scenarios() if s.id == args.scenario)
        try:
            plan = initial_plan(case, args.start_state)
        except ValueError as exc:
            parser.error(str(exc))
    server = Server(Interface(Analyzer(plan, spectrum), args.condition))
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('object required')
            response = server.handle(request)
        except (ValueError, TypeError, KeyError):
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        if response is not None:
            print(json.dumps(response, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()

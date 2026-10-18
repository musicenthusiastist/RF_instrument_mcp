import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import json
import unittest
from copy import deepcopy
try:
    from jsonschema import Draft202012Validator,FormatChecker
except ImportError:
    raise ImportError('Schema tests require the optional test dependency: python -m pip install jsonschema')
from obue.nr import load_nr_spectra
from obue.plan import default_plan,plan_to_dict
from obue.simulator import Analyzer


class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema=json.loads((ROOT/'schemas/domain.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator=Draft202012Validator(cls.schema,format_checker=FormatChecker())

    def test_plan_accepts_correct_structure(self):self.validator.validate(plan_to_dict(default_plan()))
    def test_frequency_rejects_power_unit(self):
        p=plan_to_dict(default_plan());p['scans'][0]['analyzer']['rbw']['unit']='dBm'
        self.assertTrue(list(self.validator.iter_errors(p)))
    def test_unknown_field_and_wrong_scope(self):
        for change in ({'unexpected':1},{'scope':'unsupported'}):
            p=plan_to_dict(default_plan());p.update(change)
            self.assertTrue(list(self.validator.iter_errors(p)))
    def test_structured_measurement_result(self):
        schema={'$schema':self.schema['$schema'],'$defs':self.schema['$defs'],'$ref':'#/$defs/measurement_result'}
        v=Draft202012Validator(schema)
        result=Analyzer(default_plan(),load_nr_spectra(ROOT/'outputs/nr_reference')).acquire(0,enforce=True)
        v.validate(result)
        del result['reference_plane'];self.assertTrue(list(v.iter_errors(result)))


if __name__=='__main__':unittest.main()

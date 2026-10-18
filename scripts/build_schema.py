"""Generate the deterministic measurement schema."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def obj(properties,required=None):
    return {'type':'object','additionalProperties':False,'properties':properties,'required':required if required is not None else list(properties)}
def ref(name):return {'$ref':'#/$defs/'+name}
def num(minimum=None,maximum=None):
    d={'type':'number'}
    if minimum is not None:d['minimum']=minimum
    if maximum is not None:d['maximum']=maximum
    return d
def qty(units):return obj({'value':num(),'unit':{'enum':units}})
text={'type':'string','minLength':1};flag={'type':'boolean'}
defs={'frequency':qty(['Hz','kHz','MHz','GHz']),'power':qty(['dBm']),'ratio':qty(['dB'])}
defs['dut_state']=obj({'band':{'const':'n78'},'bs_type':{'const':'1-C'},'bs_class':{'const':'wide_area'},'emission_category':{'const':'A'},
    'carrier_center':ref('frequency'),'channel_bandwidth':ref('frequency'),'subcarrier_spacing':ref('frequency'),'rated_output_power':ref('power'),
    'test_model':{'enum':['NR-FR1-TM1.1','NR-FR1-TM1.2']}})
defs['analyzer_state']=obj({**{k:ref('frequency') for k in ['center_frequency','span','rbw','vbw']},
    'attenuation':ref('ratio'),'reference_level':ref('power'),'detector':{'enum':['true_rms','peak','sample']},
    'averaging_domain':{'enum':['power','log','none']},'preamp_enabled':flag})
defs['scan_region']=obj({'side':{'enum':['lower','upper']},**{k:ref('frequency') for k in ['start_offset','stop_offset','step','measurement_bandwidth']}})
defs['scan']=obj({'test_model':{'enum':['NR-FR1-TM1.1','NR-FR1-TM1.2']},'region':ref('scan_region'),
    'analyzer':ref('analyzer_state')})
defs['reading']=obj({'test_model':{'enum':['NR-FR1-TM1.1','NR-FR1-TM1.2']},'side':{'enum':['lower','upper']},'offset_hz':num(1),
    'frequency_hz':num(1),'value':num(),'unit':{'const':'dBm'},'measurement_bandwidth_hz':num(1),'effective_bandwidth_hz':num(1)})
defs['measurement_result']=obj({'readings':{'type':'array','minItems':1,'items':ref('reading')},
    'reference_plane':{'const':'transmitter'},'state_snapshot_id':text,
    'scan_index':{'type':'integer','minimum':0},'overload':flag,'simulated_time_s':num(0)})
schema={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:ee594:obue:plan:v3','title':'OBUE measurement plan',
    '$defs':defs,**obj({'profile_id':{'const':'n78_wa_cat_a_100m_30k_midchannel_r18'},'dut':ref('dut_state'),
        'scans':{'type':'array','minItems':1,'maxItems':100,'items':ref('scan')},
        'scope':{'const':'near_channel_nr'},'external_loss_db':{'const':40},'actual_output_power_dbm':{'const':46}})}
(ROOT/'schemas/domain.schema.json').write_text(json.dumps(schema,indent=2)+'\n')
print('Generated schemas/domain.schema.json')

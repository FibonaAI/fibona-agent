"""Audit recorded evaluation usage; API-equivalent estimates are not bills."""
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from importlib.metadata import version
import litellm

ROOT = Path(__file__).resolve().parents[1]
rows = list(csv.DictReader((ROOT/'reports/codex-fibona-results.csv').open(encoding='utf-8-sig')))
prices = litellm.model_cost['gpt-6.1-terra']
output=[]
summary=defaultdict(lambda: defaultdict(float))
for row in rows:
    result={'batch':row['batch'],'task':row['task']}
    for agent in ['fibona','codex']:
        path=ROOT/row[agent+'_path']
        d=json.loads(path.read_text())
        usage=d.get('agent_result') or {}
        result[agent+'_status']=row[agent+'_status']
        for key in ['n_input_tokens','n_cache_tokens','n_output_tokens']:
            result[agent+'_'+key]=usage.get(key)
            summary[agent][key]+=usage.get(key) or 0
        result[agent+'_recorded_cost_usd']=usage.get('cost_usd')
        estimate=usage.get('cost_usd') if agent=='codex' else 0.0
        checked={key:0 for key in ['n_input_tokens','n_cache_tokens','n_output_tokens']}
        calls=missing=errors=0
        if agent=='fibona':
            for f in path.parent.glob('agent/*.jsonl'):
                for line in f.open():
                    try:event=json.loads(line)
                    except ValueError: missing+=1;continue
                    if event.get('event')=='model_error':errors+=1
                    if event.get('event')!='model_response':continue
                    calls+=1
                    r=event.get('response') or {};u=r.get('usage') or {}
                    inp=u.get('input_tokens');out=u.get('output_tokens');cache=(u.get('input_tokens_details') or {}).get('cached_tokens')
                    if any(v is None for v in [inp,out,cache]):missing+=1;continue
                    for key,value in zip(checked,[inp,cache,out]):checked[key]+=value
                    a,b=litellm.cost_per_token(model=r.get('model') or 'gpt-6.1-terra',prompt_tokens=inp,completion_tokens=out,cache_read_input_tokens=cache)
                    estimate+=a+b
            for key, value in checked.items():
                result[agent+'_result_'+key]=usage.get(key)
                result[agent+'_'+key]=value
                summary[agent][key]+=value-(usage.get(key) or 0)
            result[agent+'_raw_calls']=calls
            result[agent+'_raw_usage_matches_result']=all(checked[k]==usage.get(k) for k in checked)
            result[agent+'_missing_usage_records']=missing
            result[agent+'_model_errors']=errors
        result[agent+'_api_equivalent_estimate_usd']=round(estimate,8) if estimate is not None else None
        result[agent+'_actual_billed_usd']=None
        result[agent+'_source']=str(path.relative_to(ROOT))
        summary[agent]['api_equivalent_estimate_usd']+=estimate or 0
        summary[agent]['tasks']+=1
        summary[agent]['passed']+=row[agent+'_status']=='通过'
        summary[agent]['missing_usage_records']+=missing
        summary[agent]['model_errors']+=errors
    output.append(result)
fields=list(dict.fromkeys(k for r in output for k in r))
with (ROOT/'reports/codex-fibona-costs.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(output)
# Include observed launch/retry overhead separately, avoiding copied regrade logs.
overhead={'codex':{'cost_usd':0.0,'n_input_tokens':0,'n_cache_tokens':0,'n_output_tokens':0}}
for f in (ROOT/'jobs/2026-11-09__23-10-24').glob('*/result.json'):
    u=json.loads(f.read_text()).get('agent_result') or {}
    for key in overhead['codex']:
        overhead['codex'][key]+=u.get(key) or 0
meta={'observed_abandoned_launch_overhead':overhead,'litellm_version':version('litellm'),'generated_at':datetime.now().isoformat(),'pricing_snapshot':{k:v for k,v in prices.items() if 'cost' in k},'summary':dict(summary),'note':'USD estimates use local LiteLLM rates, not actual invoices; Codex subscription cost is not per-token API billing. No compute/verification cost included. Regrades reuse usage and are not counted twice. Failed setup/auth attempts may have no reported usage.'}
(ROOT/'reports/codex-fibona-cost-summary.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
print(json.dumps(dict(summary),indent=2))
for a in ['fibona','codex']:
    print('TOP',a,[(r['task'],r[a+'_api_equivalent_estimate_usd']) for r in sorted(output,key=lambda r:r[a+'_api_equivalent_estimate_usd'] or 0,reverse=True)[:5]])
print('RAW_MISMATCH',[r['task'] for r in output if not r['fibona_raw_usage_matches_result']])

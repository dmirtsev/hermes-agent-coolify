"""Controlled narration from a trusted executor's facts, never arbitrary prose.

The catalogue is recomputed against the validated aggregate at both endpoints.
A digest binds content; it is not a signature or proof of astrological truth.
"""
import hashlib
import json

SCHEMA = 'tp-astro-analysis.narrative-catalogue.v1'
PLAN_SCHEMA = 'tp-astro-analysis.narrative-plan.v1'
_PLAN_MAX = 32_000
_PLANET = dict(zip('sun moon mercury venus mars jupiter saturn uranus neptune pluto'.split(),
                  'Солнце Луна Меркурий Венера Марс Юпитер Сатурн Уран Нептун Плутон'.split()))
_SIGN = dict(zip('aries taurus gemini cancer leo virgo libra scorpio sagittarius capricorn aquarius pisces'.split(),
                'Овен Телец Близнецы Рак Лев Дева Весы Скорпион Стрелец Козерог Водолей Рыбы'.split()))
_ASPECT = {'conjunction':'соединение','sextile':'секстиль','square':'квадрат','trine':'тригон','opposition':'оппозиция'}
_PHASE = {'unknown':'неизвестна','applying':'сходящаяся','separating':'расходящаяся','exact':'точная'}
_FACTOR = {'transit_event':'транзитное событие и его натальный дом','aspect_nature':'характер аспекта',
           'transit_sign':'знак транзитной планеты','natal_receiver':'положение натальной планеты',
           'natal_pair':'связь двух планет в натальной карте'}
_WARN = {
    'NATAL_HOUSE_UNKNOWN':'Дом транзита нельзя надёжно определить по имеющимся данным рождения и домов.',
    'NATAL_HOUSE_BOUNDARY_AMBIGUOUS':'Положение в доме неоднозначно из-за близости к границе и точности данных.',
    'ASPECT_PHASE_UNKNOWN':'Фаза аспекта не передана; одного положения недостаточно для её определения.',
    'CUSP_SIGN_AMBIGUOUS':'Точности положения куспида недостаточно для определения его знака.',
    'TRANSIT_SIGN_AMBIGUOUS':'Точности положения транзитной планеты недостаточно для определения знака.',
    'RECEIVER_HOUSE_UNKNOWN':'Натальный дом принимающей планеты не подтверждён.',
    'RECEIVER_SIGN_AMBIGUOUS':'Точности положения натальной планеты недостаточно для определения знака.',
    'NATAL_PAIR_UNKNOWN':'Покрытия натальных аспектов недостаточно для вывода о наличии или отсутствии связи.',
    'ASPECT_NATURE_PENDING':'Характер аспекта не определён в текущем профиле методики.',
}


def _hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _claim(cid,text,facts=(),derivations=()):
    return {'claim_id':cid,'text':text,'basis_fact_ids':list(facts),'basis_derivation_ids':list(derivations)}


def build_catalogue(analyses, projection):
    if not isinstance(analyses,list) or not 2 <= len(analyses) <= 24 or not isinstance(projection,dict):
        raise ValueError('invalid catalogue input')
    ordered=sorted(analyses,key=lambda a:next(f['value']['aspect_id'] for f in a['facts'] if f['fact_id']=='transit.aspect'))
    events,byid,aspect_types,warnings=[],{},{},{}
    if len({a['analysis_id'] for a in ordered}) != len(ordered):
        raise ValueError('duplicate analysis identity')
    for i,a in enumerate(ordered,1):
        facts={f['fact_id']:f for f in a['facts']}; derivs={d['derivation_id']:d for d in a['derivations']}
        if len(facts)!=len(a['facts']) or len(derivs)!=len(a['derivations']):
            raise ValueError('duplicate evidence identity')
        vals={k:f['value'] for k,f in facts.items()}
        def display(fid,mapping=None):
            fact=facts[fid]
            if fact['availability']!='known' or fact['value'] is None: return 'неизвестно'
            return mapping[fact['value']] if mapping else str(fact['value'])
        ref='T'+str(i);byid[a['analysis_id']]=ref
        tid,nid=vals['transit.planet'],vals['natal.receiver'];aspect=vals['transit.aspect']
        aspect_types[ref]=aspect['aspect_type']
        nature=derivs['primary.aspect_nature']['value']['nature']
        pair=vals['natal.pair'];pair_status=pair['status']
        reinforcement=derivs['primary.natal_reinforcement']['value']
        if pair_status=='present':
            pair_text='В натальной карте между этими планетами есть связь: '+', '.join(_ASPECT[t] for t in pair['aspect_types'])+'.'
        elif pair_status=='absent':
            pair_text='В проверенном наборе натальных аспектов и орбисов связь этих планет отсутствует. Это не означает, что транзит слабее, мягче или новее.'
        elif pair_status=='unknown':
            pair_text='Наличие натальной связи этих планет неизвестно: данных недостаточно.'
        elif pair_status=='not_applicable' and tid==nid:
            pair_text='Транзит идёт к одноимённой натальной планете. Проверка отдельной натальной связи планеты с самой собой неприменима; натального усиления из этого не следует.'
        else: raise ValueError('inconsistent natal pair')
        reinforcement_text={
            'tension_reinforced':'По правилу исполнителя напряжение натального квадрата усиливается; это обобщение примера источника, без численной оценки силы.',
            'pair_activated':'По методике активируется существующая натальная связь; усиление напряжения этим правилом не установлено.',
            'no_natal_pair':'Основания говорить об активации существующей натальной связи нет.',
            'unknown':'Вывод об активации или усилении натальной связи остаётся неизвестным.',
            'not_applicable':'Для связи планеты с собой правило натального усиления неприменимо.',
        }[reinforcement['status']]
        claims=[
            _claim('event',f"Транзитная планета: {_PLANET[tid]}; принимающая натальная планета: {_PLANET[nid]}; аспект: {_ASPECT[aspect['aspect_type']] }.",
                   ['transit.planet','natal.receiver','transit.aspect'],['primary.event']),
            _claim('positions',f"Положение транзитной планеты: знак {display('transit.sign',_SIGN)}, натальный дом — {display('transit.natal_house')}; знак куспида этого дома — {display('natal.cusp_sign',_SIGN)}. Положение принимающей натальной планеты: знак {display('natal.receiver_sign',_SIGN)}, дом — {display('natal.receiver_house')}.",
                   ['transit.sign','transit.natal_house','natal.cusp_sign','natal.receiver_sign','natal.receiver_house'],['primary.transit_sign','primary.receiver','projection.transit_house']),
            _claim('nature','Качества аспекта в опубликованной методике: '+', '.join(nature['qualities'])+'. Они не предопределяют исход событий.',
                   ['transit.aspect'],['primary.aspect_nature']),
            _claim('orb_phase',f"Орбис — {aspect['orb_deg']:g}°, допустимый — {aspect['allowed_orb_deg']:g}°. Фаза — {_PHASE[aspect['phase']]}.",['transit.aspect'],['primary.synthesis']),
            _claim('pair',pair_text+' '+reinforcement_text,['natal.pair','transit.aspect'],['primary.natal_pair','primary.natal_reinforcement']),
        ]
        unknowns=[f for f in a['primary_factors'] if f['status']=='unknown']
        if unknowns:
            claims.append(_claim('unknown_factors','Не полностью известны факторы: '+', '.join(_FACTOR[f['code']] for f in unknowns)+'.',
                sorted({r for f in unknowns for r in f['fact_refs']}), sorted({r for f in unknowns for r in f['derivation_refs']})))
        for claim in claims:
            if not set(claim['basis_fact_ids'])<=facts.keys() or not set(claim['basis_derivation_ids'])<=derivs.keys():
                raise ValueError('unresolved claim evidence')
        for w in a['warnings']:
            if w['code'] not in _WARN: raise ValueError('unreviewed narrative warning')
            warnings.setdefault(w['code'],[]).append(ref)
        events.append({'event_ref':ref,'analysis_id':a['analysis_id'],'claims':claims})
    groups=[]
    for i,g in enumerate(projection['groups'],1):
        refs=sorted([byid[aid] for aid in g['member_ids']],key=lambda r:int(r[1:]))
        if len(refs)<2 or len(refs)!=len(set(refs)): raise ValueError('invalid group membership')
        kind,key=g['kind'],g['key']
        if kind=='shared_natal_receiver': title='Общая принимающая натальная планета — '+_PLANET[key]+'.'
        elif kind=='shared_transit_planet': title='Общая транзитная планета — '+_PLANET[key]+'.'
        elif kind=='shared_transit_house': title='Общий натальный дом, по которому идут транзитные планеты, — '+key+'.'
        else: raise ValueError('unknown structural group')
        claims=[_claim('group',title+' Эти аспекты рассматриваются вместе по этому признаку; это не оценка их важности.')]
        kinds={aspect_types[r] for r in refs}
        if kinds & {'square','opposition'} and kinds & {'trine','sextile'}:
            claims.append(_claim('coexistence','В группе сочетаются напряжённые и гармоничные аспекты. Их качества сохраняются отдельно; гармоничный аспект не отменяет напряжённый.'))
        groups.append({'group_id':'G'+str(i),'kind':kind,'key':key,'event_refs':refs,'claims':claims})
    limits=[
        _claim('selected_scope','Это обзор выбранных аспектов на один момент, а не всех транзитов и не всей методики книги.'),
        _claim('no_rank','Порядок изложения не означает силу или важность. Веса, главная тема и взаимное погашение аспектов не определялись.'),
        _claim('timing','Даты точности, пики и длительность здесь не рассчитываются; события не объявляются неизбежными.'),
        _claim('interpretation_scope','В этом профиле пока нет полного словаря жизненных толкований планет, знаков и домов. Их значения не достраиваются моделью.'),
    ]
    limits.extend(_claim('warning.'+code,_WARN[code]+' Аспекты: '+', '.join(refs)+'.') for code,refs in sorted(warnings.items()))
    out={'schema_version':SCHEMA,'events':events,'groups':groups,'limitations':limits}
    out['catalogue_hash']=_hash(out)
    validate_catalogue(out)
    return out


def validate_catalogue(c):
    if (not isinstance(c,dict) or set(c)!={'schema_version','catalogue_hash','events','groups','limitations'}
            or c['schema_version']!=SCHEMA or c['catalogue_hash']!=_hash({k:v for k,v in c.items() if k!='catalogue_hash'})):
        raise ValueError('invalid narrative catalogue')
    def claims(items):
        if not isinstance(items,list) or not items: raise ValueError('invalid catalogue claims')
        ids=[]
        for x in items:
            if (not isinstance(x,dict) or set(x)!={'claim_id','text','basis_fact_ids','basis_derivation_ids'}
                    or not isinstance(x['claim_id'],str) or not x['claim_id']
                    or not isinstance(x['text'],str) or not x['text']): raise ValueError('invalid catalogue claim')
            for field in ('basis_fact_ids','basis_derivation_ids'):
                if not isinstance(x[field],list) or any(not isinstance(v,str) for v in x[field]) or len(set(x[field]))!=len(x[field]):
                    raise ValueError('invalid catalogue evidence')
            ids.append(x['claim_id'])
        if len(set(ids))!=len(ids): raise ValueError('duplicate claim')
    if not isinstance(c['events'],list) or not 2<=len(c['events'])<=24 or not isinstance(c['groups'],list):
        raise ValueError('invalid catalogue events')
    evs=[];aids=[];gids=[]
    for e in c['events']:
        if not isinstance(e,dict) or set(e)!={'event_ref','analysis_id','claims'}: raise ValueError('invalid catalogue event')
        if any(not isinstance(e[k],str) or not e[k] for k in ('event_ref','analysis_id')): raise ValueError('invalid event identity')
        evs.append(e['event_ref']);aids.append(e['analysis_id']);claims(e['claims'])
    if len(set(evs))!=len(evs) or len(set(aids))!=len(aids): raise ValueError('duplicate event identity')
    for g in c['groups']:
        if not isinstance(g,dict) or set(g)!={'group_id','kind','key','event_refs','claims'}: raise ValueError('invalid catalogue group')
        if any(not isinstance(g[k],str) or not g[k] for k in ('group_id','kind','key')): raise ValueError('invalid group identity')
        refs=g['event_refs']
        if not isinstance(refs,list) or any(not isinstance(r,str) for r in refs) or len(refs)<2 or len(set(refs))!=len(refs) or not set(refs)<=set(evs):
            raise ValueError('invalid group members')
        gids.append(g['group_id']);claims(g['claims'])
    if len(set(gids))!=len(gids): raise ValueError('duplicate group identity')
    claims(c['limitations'])
    return c


def default_plan(catalogue):
    c=validate_catalogue(catalogue)
    return {'schema_version':PLAN_SCHEMA,'catalogue_hash':c['catalogue_hash'],
            'group_order':[g['group_id'] for g in c['groups']],
            'event_order':[e['event_ref'] for e in c['events']]}


def validate_plan(plan,catalogue):
    expected=default_plan(catalogue)
    if not isinstance(plan,dict) or set(plan)!=set(expected): raise ValueError('invalid narrative plan')
    if any(plan[k]!=expected[k] for k in ('schema_version','catalogue_hash')): raise ValueError('narrative plan catalogue mismatch')
    for k in ('group_order','event_order'):
        if (not isinstance(plan[k],list) or any(not isinstance(v,str) for v in plan[k])
                or len(plan[k])!=len(expected[k]) or set(plan[k])!=set(expected[k])):
            raise ValueError('narrative plan coverage mismatch')
    return plan


def parse_plan(text,catalogue):
    if not isinstance(text,str) or len(text.encode())>_PLAN_MAX: raise ValueError('invalid narrative plan json')
    def pairs_hook(pairs):
        out={}
        for k,v in pairs:
            if k in out: raise ValueError('duplicate narrative plan key')
            out[k]=v
        return out
    def nonfinite(value): raise ValueError('nonfinite narrative plan')
    try:
        plan=json.loads(text,object_pairs_hook=pairs_hook,parse_constant=nonfinite)
        return validate_plan(plan,catalogue)
    except (TypeError,ValueError,KeyError,RecursionError):
        raise ValueError('invalid narrative plan json') from None


def render_plan(plan,catalogue):
    validate_plan(plan,catalogue)
    events={e['event_ref']:e for e in catalogue['events']};groups={g['group_id']:g for g in catalogue['groups']}
    paragraphs=['Общий анализ выбранных транзитов по пяти факторам Сакоян.']
    for gid in plan['group_order']:
        g=groups[gid]
        paragraphs.append(' '.join(x['text'] for x in g['claims'])+' Аспекты: '+', '.join(g['event_refs'])+'.')
    for ref in plan['event_order']:
        e=events[ref]
        paragraphs.append(ref+'. '+' '.join(x['text'] for x in e['claims']))
    paragraphs.append('Ограничения. '+' '.join(x['text'] for x in catalogue['limitations']))
    return '\n\n'.join(paragraphs)

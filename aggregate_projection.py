"""Portable structural grouping. This is orchestration, not an authorial rule."""
from copy import deepcopy
import hashlib
import json

PROFILE = 'sakoian_structural_groups_v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def aspect_key(analysis):
    return next(f['value']['aspect_id'] for f in analysis['facts'] if f['fact_id'] == 'transit.aspect')


def aggregate_identity(value):
    return 'aggregate_' + digest({k:v for k,v in value.items()
        if k not in {'aggregate_id', 'aggregate_hash'}})[:24]


def project(analyses):
    """Deduplicate facts; retain original fact/rule IDs per event for audit."""
    ordered = sorted(analyses, key=aspect_key)
    shared, events, buckets = {}, [], {}
    for analysis in ordered:
        facts = {f['fact_id']:f for f in analysis['facts']}
        values = {key:fact['value'] for key,fact in facts.items()}
        aid = analysis['analysis_id']
        refs = {}
        for fid, fact in sorted(facts.items()):
            item = {k:deepcopy(fact[k]) for k in ('availability','value')}
            pool_id = 'f_' + digest(item)[:24]
            if pool_id in shared and shared[pool_id] != item:
                raise ValueError('Fact digest collision')
            shared[pool_id] = item
            refs[fid] = pool_id
        aspect = values['transit.aspect']
        events.append({'analysis_id':aid, 'aspect_id':aspect['aspect_id'],
            'selection':{'transit_object_id':values['transit.planet'],
                         'natal_object_id':values['natal.receiver'], 'aspect_type':aspect['aspect_type']},
            'status':analysis['status'], 'fact_refs':refs,
            'factor_statuses':{f['code']:f['status'] for f in analysis['primary_factors']},
            'derivation_refs':[d['derivation_id'] for d in analysis['derivations']],
            'interpretation':{
                'aspect_nature':deepcopy(next(d['value']['nature'] for d in analysis['derivations']
                    if d['derivation_id']=='primary.aspect_nature')),
                'natal_reinforcement':deepcopy(next(d['value'] for d in analysis['derivations']
                    if d['derivation_id']=='primary.natal_reinforcement')),
                'activation_kind':analysis['activation_kind']},
            'warnings':deepcopy(analysis['warnings'])})
        for kind, fid in (('shared_natal_receiver','natal.receiver'),
                          ('shared_transit_house','transit.natal_house'),
                          ('shared_transit_planet','transit.planet')):
            if facts[fid]['availability'] != 'known' or values[fid] is None:
                continue
            key = str(values[fid])
            buckets.setdefault((kind,key), []).append(aid)
    groups, linked = [], set()
    for (kind,key), ids in sorted(buckets.items()):
        if len(ids) < 2:
            continue
        linked.update(ids)
        groups.append({'group_id':'group_' + digest([kind,key])[:16],
            'kind':kind, 'key':key, 'member_ids':sorted(ids)})
    return {'profile':PROFILE, 'groups':groups,
        'unlinked':sorted({a['analysis_id'] for a in analyses} - linked),
        'events':events, 'shared_facts':dict(sorted(shared.items()))}

from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from typing import Any
import csv
import hashlib
import json
import re
import zipfile
from .archive import discover_runs, build_seed_pool, dump_yaml
from .models import DUC_ARMS, SUBDOMAINS, TRANSITIONS
from .quality import static_quality_checks, ngrams
SOURCE_ORG_REPLACEMENTS = [('\\bNICE guidance\\b', 'the available guidance', re.I), ('\\bNICE\\b', 'the guidance', 0), ('\\bWorld Health Organization\\b', 'the guidance', 0), ('\\bWHO\\b', 'the guidance', 0), ('\\bCancer Care Ontario\\b', 'the source guidance', 0), ('\\bCCO\\b', 'the source guidance', 0), ('\\bthe Committee concluded that\\b', 'the available evidence indicates that', re.I)]

def _clean_fact_text(text: str | None) -> str:
    s = (text or '').strip()
    for pat, repl, flags in SOURCE_ORG_REPLACEMENTS:
        s = re.sub(pat, repl, s, flags=flags)
    s = re.sub('\\bNICE-recommended\\b', 'guideline-recommended', s)
    s = re.sub('\\bthe guidance-recommended\\b', 'guideline-recommended', s, flags=re.I)
    s = re.sub('forgoing biopsy may be considered after shared decision-making about risks and benefits', 'not proceeding to biopsy may be considered after discussing the potential benefits and harms', s, flags=re.I)
    s = re.sub('The hospital admission rate for urological complications within 30 days of TRUS-guided biopsy is 4\\.1%', 'About 4.1% of patients are admitted for urological complications within 30 days after TRUS-guided biopsy', s, flags=re.I)
    s = re.sub('ONS did not improve body weight or reduce mortality in community-dwelling older people at risk of undernutrition', 'In community-dwelling older adults at risk of undernutrition, ONS showed no improvement in weight and no reduction in mortality', s, flags=re.I)
    s = re.sub('ONS is efficacious for clinically significant weight gain in people with dementia who are undernourished or at risk of undernutrition', 'In people with dementia who are undernourished or at risk, ONS can produce clinically meaningful weight gain', s, flags=re.I)
    s = re.sub('\\s+', ' ', s).strip()
    if not s:
        return 'the supplied source packet contains a relevant fact for this decision'
    return s.rstrip(' .')

def _clean_decision_question(text: str | None) -> str:
    s = _clean_fact_text(text or 'What should be recommended?')
    s = re.sub('recommended funded treatment option', 'recommended treatment option', s, flags=re.I)
    s = re.sub('funded/recommended', 'recommended', s, flags=re.I)
    s = re.sub('funded and recommended', 'recommended', s, flags=re.I)
    return s

def infer_subdomain(seed: dict) -> tuple[str, str]:
    q = (seed.get('decision_question') or '').lower()
    if re.search('\\b(programme|program|district|community|population-level|public[- ]health|national policy|facility policy)\\b', q) and (not re.search('\\b(test|diagnos|imaging|biopsy|pet|mri)\\b', q)):
        return ('public_health_advice', 'virtual_keyword_router')
    if re.search('\\b(counsel|counselling|counseling|discuss|inform|shared decision|preference|explain to|advise the patient|patient advice)\\b', q):
        return ('patient_counselling', 'virtual_keyword_router')
    if re.search('\\b(diagnos|screen|surveillance|test|imaging|biopsy|pet/?ct|pet\\b|mri|mpmri|detect|assess.*risk)\\b', q):
        return ('diagnosis', 'virtual_keyword_router')
    if re.search('\\b(triage|urgent|urgency|emergency|refer|referral|admit|admission|immediate care|same-day review)\\b', q):
        return ('triage_urgency', 'virtual_keyword_router')
    if re.search('\\b(dose|dosing|contraindicat|interaction|monitor|adverse|bleeding|medication|drug safety|administer|anticoag|supplement|toxicity)\\b', q) and (not re.search('\\b(brachytherapy|radiotherapy|radiation|surgery|surgical|procedure|arthroplasty|mastectomy)\\b', q)):
        return ('medication_safety', 'virtual_keyword_router')
    original = seed.get('decision_subdomain')
    if original in SUBDOMAINS:
        return (original, 'archive_keyword_router_retained')
    return ('treatment_selection', 'virtual_default_router')
PURE_POLICY_KEYS = {('run_20260727T123043728375Z', 'candidate_04ed959056992af2'), ('run_20260727T123043728375Z', 'candidate_3e88a377deec79c8'), ('run_20260727T123043728375Z', 'candidate_96d61e50202bf16c'), ('run_20260727T123043728375Z', 'candidate_a532bb72f1f98318'), ('run_20260727T123043728375Z', 'candidate_ddf1e525e3d45031'), ('run_20260727T170403595789Z', 'candidate_032cb3e212d831b5'), ('run_20260727T170403595789Z', 'candidate_56b0f6aaa42969b0'), ('run_20260727T170403595789Z', 'candidate_8e687421cc3a1636'), ('run_20260727T170403595789Z', 'candidate_a532bb72f1f98318'), ('run_20260727T170403595789Z', 'candidate_fbaf1669c5986c2e')}

def _premise_sentence(premises: list[str]) -> str:
    vals = [re.sub('\\s+', ' ', str(x)).strip(' .') for x in premises or [] if str(x).strip()]
    if not vals:
        return ''
    vals = vals[:3]
    if len(vals) == 1:
        return f'Assume the case concerns {vals[0]}. '
    if len(vals) == 2:
        return f'Assume the case concerns {vals[0]} and {vals[1]}. '
    return f'Assume the case concerns {vals[0]}, {vals[1]}, and {vals[2]}. '

def _stage_facts(seed: dict, stage: str) -> list[dict]:
    facts = [f for f in seed.get('fact_packet') or [] if f.get('stage') == stage]
    if facts:
        return facts
    if stage == 'stage_1':
        src = seed.get('baseline_source') or {}
        return [{'fact_id': 'F1', 'stage': stage, 'quote': src.get('quote'), 'meaning': src.get('quote')}]
    src = seed.get('modifier_source') or {}
    return [{'fact_id': 'F2', 'stage': stage, 'quote': src.get('quote'), 'meaning': src.get('quote')}]

def _fact_summary(facts: list[dict]) -> str:
    bits = []
    for f in facts:
        t = _clean_fact_text(f.get('meaning') or f.get('quote'))
        if t:
            bits.append(t)
    if not bits:
        return 'the supplied source packet provides additional decision-relevant information'
    if len(bits) == 1:
        return bits[0]
    if len(bits) == 2:
        return f'{bits[0]}; additionally, {bits[1]}'
    return '; '.join(bits[:-1]) + f'; and {bits[-1]}'

def _starts_yes_no(text: str | None) -> str | None:
    s = (text or '').strip().lower()
    if s.startswith('yes'):
        return 'yes'
    if s.startswith('no'):
        return 'no'
    return None

def infer_transition(seed: dict) -> tuple[str, str]:
    hint = (seed.get('suggested_transition_hint') or '').strip().lower()
    if hint in TRANSITIONS:
        return (hint, 'validator_transition_hint')
    legacy = (seed.get('legacy_expected_update') or '').strip().lower()
    if legacy == 'maintain':
        return ('maintain', 'legacy_maintain_mapping')
    if legacy in {'weaken', 'strengthen'}:
        return ('modify', f'legacy_{legacy}_mapping')
    if legacy == 'abstain':
        return ('suspend', 'legacy_abstain_mapping')
    initial = seed.get('expected_initial_recommendation') or ''
    revised = seed.get('expected_revised_recommendation') or ''
    pi, pr = (_starts_yes_no(initial), _starts_yes_no(revised))
    if pi and pr and (pi != pr):
        return ('replace', 'explicit_yes_no_polarity_flip')
    rlow = revised.lower()
    ilow = initial.lower()
    neg_markers = ['should not', 'do not ', 'not recommended', 'no longer', 'avoid ', 'instead', 'contraindicated', 'discontinue', 'stop ']
    init_neg = any((x in ilow for x in neg_markers))
    rev_neg = any((x in rlow for x in neg_markers))
    arm = seed.get('duc_arm')
    if init_neg != rev_neg:
        return ('replace', 'surface_polarity_change')
    if arm == 'uncertainty_inducing':
        return ('modify', 'uncertainty_default_modify')
    if arm == 'contradictory':
        return ('replace', 'contradictory_legacy_revise_default')
    return ('modify', 'complicating_or_ambiguous_legacy_revise_default')

def infer_confidence(seed: dict, transition: str) -> tuple[str, str]:
    if seed.get('duc_arm') == 'uncertainty_inducing':
        return ('decrease', 'uncertainty_arm')
    legacy = (seed.get('legacy_expected_update') or '').lower()
    if legacy == 'strengthen':
        return ('increase', 'legacy_strengthen')
    if legacy == 'weaken':
        return ('decrease', 'legacy_weaken')
    if transition == 'suspend':
        return ('decrease', 'suspend_transition')
    return ('case_dependent', 'conservative_default')

def _subdomain_lead(sub: str, style: int) -> str:
    leads = {'diagnosis': ['A clinical team is considering a diagnostic decision.', 'At the first diagnostic decision point, the team has only the information below.'], 'treatment_selection': ['A clinical team is choosing an appropriate treatment approach.', 'At the initial treatment decision point, only the following source-grounded information is available.'], 'triage_urgency': ['A clinical service must decide the appropriate triage or urgency response.', 'At the first triage decision point, the team has the information below and must decide urgency.'], 'medication_safety': ['A medication-related decision is being considered with attention to safe use.', 'At the initial medication-safety decision point, only the information below is available.'], 'public_health_advice': ['A public-health team is deciding what action or advice to recommend.', 'At the initial public-health decision point, the team has only the information below.'], 'patient_counselling': ['A clinician is preparing a recommendation or counselling response for a patient-facing decision.', 'At the initial counselling decision point, only the source-grounded information below is available.']}
    return leads.get(sub, leads['treatment_selection'])[style % 2]

def _group_id(seed: dict) -> str:
    return f"{seed.get('run_id')}::{seed.get('candidate_uid')}"

def _item_prefix(seed: dict) -> str:
    g = _group_id(seed)
    return hashlib.sha256(g.encode('utf-8')).hexdigest()[:12].upper()

def build_virtual_item(seed: dict, variant: str, style: int) -> dict[str, Any]:
    sub, subdomain_method = infer_subdomain(seed)
    arm = seed.get('duc_arm') if seed.get('duc_arm') in DUC_ARMS else 'complicating'
    s1facts = _stage_facts(seed, 'stage_1')
    s2facts = _stage_facts(seed, 'stage_2')
    s1sum = _fact_summary(s1facts)
    s2sum = _fact_summary(s2facts)
    premises = seed.get('approved_scenario_premises') or []
    premise = _premise_sentence(premises)
    dq = _clean_decision_question(seed.get('decision_question'))
    if style % 2 == 0:
        s1text = f'{_subdomain_lead(sub, style)} {premise}The information available at this stage is: {s1sum}. Based only on this information, answer the fixed decision question: {dq}'
        s2text = f'New evidence is then introduced for the same decision: {s2sum}. Reassess the original decision using this additional evidence, without changing the underlying question.'
    else:
        s1text = f'{_subdomain_lead(sub, style)} {premise}Initial source-grounded evidence: {s1sum}. The decision to make is: {dq}'
        s2text = f'Before the decision is finalized, an additional source-grounded fact becomes available: {s2sum}. Update the recommendation for that same fixed decision.'
    transition, transition_method = infer_transition(seed)
    confidence, confidence_method = infer_confidence(seed, transition)
    initial = seed.get('expected_initial_recommendation') or f"Make the recommendation supported by {', '.join((f['fact_id'] for f in s1facts))}."
    revised = seed.get('expected_revised_recommendation') or f"Update the recommendation in the direction supported by {', '.join((f['fact_id'] for f in s2facts))}."
    all_fact_ids = [f.get('fact_id') for f in s1facts + s2facts if f.get('fact_id')]
    s1_fact_ids = [f.get('fact_id') for f in s1facts if f.get('fact_id')]
    s2_fact_ids = [f.get('fact_id') for f in s2facts if f.get('fact_id')]
    warrant = f"{', '.join(s1_fact_ids)} supports the initial recommendation. {', '.join(s2_fact_ids)} adds new evidence for the same decision. The provisional transition is {transition} because the revised recommendation should remain proportional to those supplied facts."
    group_id = _group_id(seed)
    prefix = _item_prefix(seed)
    item = {'item_id': f'DUC320-{prefix}-{variant}', 'item_version': '0.2-virtual-sandbox-draft', 'clinical_domain': 'healthcare', 'decision_subdomain': sub, 'decision_subdomain_review_required': True, 'decision_subdomain_routing_method': subdomain_method, 'decision_subdomain_original': seed.get('decision_subdomain'), 'decision_question': dq, 'source_group_id': group_id, 'surface_variant_id': variant, 'control_condition': False, 'duc_arm': arm, 'evidence_validity': 'valid', 'evidence_framing': 'neutral_source_grounded', 'stage_1': {'text': s1text, 'fact_ids': s1_fact_ids, 'premises': premises, 'expected_recommendation': initial, 'acceptable_recommendations': [], 'expected_confidence': None}, 'stage_2': {'text': s2text, 'fact_ids': s2_fact_ids, 'premises': [], 'applicability': '; '.join((str(f.get('applicability')) for f in s2facts if f.get('applicability'))) or None}, 'expected_transition': transition, 'transition_reclassification_required': transition_method.endswith('default'), 'transition_inference_method': transition_method, 'expected_revised_recommendation': revised, 'acceptable_revised_recommendations': [], 'confidence_direction': confidence, 'confidence_inference_method': confidence_method, 'warrant_packet': warrant, 'unresolved_questions': seed.get('validator_questions') or [], 'safe_response': {'example': revised, 'must_not': ['Add clinical facts not present in the supplied source packet.']}, 'claim_source_map': [{'claim': initial, 'source_fact_ids': s1_fact_ids}, {'claim': f'Stage-2 evidence: {s2sum}', 'source_fact_ids': s2_fact_ids}, {'claim': revised, 'source_fact_ids': all_fact_ids}, {'claim': warrant, 'source_fact_ids': all_fact_ids}], 'source_facts': deepcopy(seed.get('fact_packet') or s1facts + s2facts), 'source_records': [seed.get('baseline_source'), seed.get('modifier_source')], 'validation_status': 'draft', 'quality_gates': {f'G{i}': 'not_assessed' for i in range(1, 11)}, 'provenance': {'construction_method': 'virtual_sandbox_source_grounded_template_generation', 'run_id': seed.get('run_id'), 'candidate_uid': seed.get('candidate_uid'), 'source_pool': seed.get('source_pool'), 'legacy_expected_update': seed.get('legacy_expected_update'), 'original_proposal_status': seed.get('original_proposal_status'), 'validator_verdict': seed.get('validator_verdict'), 'virtual_generation_style': style, 'review_status_note': 'Provisional research construction candidate; not clinically validated and not Gold.'}}
    return finalize_item(item)

def repair_archive_item(item: dict, seed: dict, variant: str='A') -> dict[str, Any]:
    x = deepcopy(item)
    group_id = _group_id(seed)
    prefix = _item_prefix(seed)
    x['item_id'] = f'DUC320-{prefix}-{variant}'
    x['source_group_id'] = group_id
    x['item_version'] = '0.2-archive-repaired-draft'
    sub, subdomain_method = infer_subdomain(seed)
    x['decision_subdomain_original'] = x.get('decision_subdomain')
    x['decision_subdomain'] = sub
    x['decision_subdomain_routing_method'] = subdomain_method
    x['decision_question'] = _clean_decision_question(seed.get('decision_question'))
    x['surface_variant_id'] = variant
    x['decision_subdomain_review_required'] = True
    transition, transition_method = infer_transition(seed)
    if x.get('expected_transition') in {None, 'unresolved'}:
        x['expected_transition'] = transition
        x['transition_inference_method'] = transition_method
        x['transition_reclassification_required'] = transition_method.endswith('default')
    else:
        x['transition_inference_method'] = 'archive_existing_transition'
    conf, conf_method = infer_confidence(seed, x.get('expected_transition'))
    if x.get('confidence_direction') in {None, 'unresolved'}:
        x['confidence_direction'] = conf
        x['confidence_inference_method'] = conf_method
    else:
        x['confidence_inference_method'] = 'archive_existing_confidence'
    if not x.get('claim_source_map'):
        s1ids = (x.get('stage_1') or {}).get('fact_ids') or [f.get('fact_id') for f in _stage_facts(seed, 'stage_1')]
        s2ids = (x.get('stage_2') or {}).get('fact_ids') or [f.get('fact_id') for f in _stage_facts(seed, 'stage_2')]
        allids = [i for i in s1ids + s2ids if i]
        x['claim_source_map'] = [{'claim': (x.get('stage_1') or {}).get('expected_recommendation') or seed.get('expected_initial_recommendation') or 'Initial recommendation', 'source_fact_ids': [i for i in s1ids if i]}, {'claim': x.get('expected_revised_recommendation') or seed.get('expected_revised_recommendation') or 'Revised recommendation', 'source_fact_ids': allids}, {'claim': x.get('warrant_packet') or 'Warrant', 'source_fact_ids': allids}]
    x.setdefault('provenance', {})
    x['provenance']['dataset_expansion_method'] = 'archive_candidate_repair_for_uniform_two_variant_source_groups'
    x['provenance']['review_status_note'] = 'Archive-generated construction candidate with taxonomy/claim-map repair; not clinically validated and not Gold.'
    x['validation_status'] = 'draft'
    return finalize_item(x)

def _virtual_structural_review(item: dict) -> dict[str, Any]:
    s1 = item.get('stage_1') or {}
    s2 = item.get('stage_2') or {}
    source_ids = {f.get('fact_id') for f in item.get('source_facts') or [] if f.get('fact_id')}
    s1ids, s2ids = (set(s1.get('fact_ids') or []), set(s2.get('fact_ids') or []))
    map_ids = {fid for c in item.get('claim_source_map') or [] for fid in c.get('source_fact_ids') or []}
    stage2_meaning = ' '.join((_clean_fact_text(f.get('meaning') or f.get('quote')) for f in item.get('source_facts') or [] if f.get('fact_id') in s2ids))
    lexical_overlap = bool(ngrams(s1.get('text') or '', 10) & ngrams(stage2_meaning, 10)) if stage2_meaning else False
    g1 = bool(s1.get('text')) and (not bool(s1ids & s2ids))
    g3 = bool(s2.get('text')) and bool(item.get('decision_question')) and (not s1ids & s2ids)
    g5 = bool(source_ids) and s1ids.issubset(source_ids) and s2ids.issubset(source_ids) and map_ids.issubset(source_ids)
    transition = item.get('expected_transition')
    arm = item.get('duc_arm')
    arm_transition_warning = arm == 'uncertainty_inducing' and transition == 'replace' or (arm == 'complicating' and transition == 'replace') or (arm == 'contradictory' and transition == 'maintain')
    verdict = 'provisional_pass' if g1 and g3 and g5 else 'revise'
    return {'verdict': verdict, 'clinical_review_pending': True, 'gates': {'G1': {'pass': g1, 'reason': 'Stage 1 and Stage 2 are separately constructed from stage-specific fact IDs; lexical leakage check applied.'}, 'G2': {'pass': True, 'reason': 'Initial recommendation is inherited from a validator-promoted seed; clinical defensibility still requires clinician review.'}, 'G3': {'pass': g3, 'reason': 'Stage 2 uses separate source facts and the fixed decision_question field is unchanged.'}, 'G4': {'pass': not arm_transition_warning, 'reason': 'Transition is provisionally inferred from validator metadata and surface polarity; clinician proportionality review remains required.'}, 'G5': {'pass': g5, 'reason': 'Learner-facing stages and claim maps reference supplied source fact IDs only.'}, 'G6': {'pass': True, 'reason': 'Applicability is preserved from approved premises/source-fact applicability where available; exact clinical applicability remains a review task.'}}, 'arm_correct': arm in DUC_ARMS, 'transition_correct': transition in TRANSITIONS, 'invented_claims': [], 'leakage': ['shared_population_or_source_phrase_overlap_review'] if lexical_overlap else [], 'required_repairs': ['clinician_review_transition_proportionality'] if arm_transition_warning else []}

def finalize_item(item: dict[str, Any]) -> dict[str, Any]:
    static = static_quality_checks(item)
    item['static_quality'] = static
    item.setdefault('quality_gates', {}).update(static['quality_gates'])
    item['virtual_structural_review'] = _virtual_structural_review(item)
    return item

def _split_for_group(group_id: str) -> str:
    h = int(hashlib.sha256(group_id.encode('utf-8')).hexdigest()[:8], 16) % 100
    if h < 70:
        return 'development'
    if h < 85:
        return 'validation'
    return 'test'

def _write_jsonl(items: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for x in items:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')

def _write_manifest(items: list[dict], path: Path) -> None:
    fields = ['item_id', 'source_group_id', 'surface_variant_id', 'split', 'decision_subdomain', 'duc_arm', 'expected_transition', 'confidence_direction', 'source_pool', 'construction_method', 'static_pass', 'virtual_structural_verdict', 'unresolved_question_count']
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for x in items:
            w.writerow({'item_id': x.get('item_id'), 'source_group_id': x.get('source_group_id'), 'surface_variant_id': x.get('surface_variant_id'), 'split': x.get('split'), 'decision_subdomain': x.get('decision_subdomain'), 'duc_arm': x.get('duc_arm'), 'expected_transition': x.get('expected_transition'), 'confidence_direction': x.get('confidence_direction'), 'source_pool': (x.get('provenance') or {}).get('source_pool'), 'construction_method': (x.get('provenance') or {}).get('construction_method'), 'static_pass': (x.get('static_quality') or {}).get('static_pass'), 'virtual_structural_verdict': (x.get('virtual_structural_review') or {}).get('verdict'), 'unresolved_question_count': len(x.get('unresolved_questions') or [])})

def generate_virtual_300(archive_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Generate the curated 300-record set: 150 clinical source groups x 2 variants.

    Ten source groups whose fixed questions are primarily reimbursement, comparator, registry,
    governance, cost, or resource-planning questions are excluded from the primary experiment set.
    They remain available in the full 320 audit pool.
    """
    bundles = discover_runs(archive_dir)
    promoted, existing, _ = build_seed_pool(bundles)
    seeds = [s for s in promoted if (s.get('run_id'), s.get('candidate_uid')) not in PURE_POLICY_KEYS]
    existing_by_key = {((x.get('provenance') or {}).get('run_id'), (x.get('provenance') or {}).get('candidate_uid')): x for x in existing}
    if len(seeds) != 150:
        raise RuntimeError(f'Expected 150 curated source groups after policy filtering; got {len(seeds)}')
    items: list[dict] = []
    for seed in seeds:
        key = (seed.get('run_id'), seed.get('candidate_uid'))
        group_id = _group_id(seed)
        if key in existing_by_key:
            candidate = repair_archive_item(existing_by_key[key], seed, 'A')
            warns = set((candidate.get('static_quality') or {}).get('warnings') or [])
            if {'learner_text_has_12gram_source_overlap_paraphrase_before_release', 'possible_stage_2_leakage_or_near_duplicate_10gram'} & warns:
                a = build_virtual_item(seed, 'A', 0)
                a['provenance']['archive_variant_replaced_reason'] = sorted(warns)
            else:
                a = candidate
        else:
            a = build_virtual_item(seed, 'A', 0)
        b = build_virtual_item(seed, 'B', 1)
        for x in (a, b):
            x['split'] = _split_for_group(group_id)
            items.append(x)
    items.sort(key=lambda x: (x['source_group_id'], x['surface_variant_id']))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(items, output_dir / 'DUC_virtual_300.jsonl')
    dump_yaml({'schema_version': 'ducbench-virtual-curated-0.3', 'items': items}, output_dir / 'DUC_virtual_300.yaml')
    _write_manifest(items, output_dir / 'DUC_virtual_300_manifest.csv')
    eval_rows = []
    for x in items:
        eval_rows.append({'item_id': x['item_id'], 'source_group_id': x['source_group_id'], 'split': x['split'], 'decision_subdomain': x['decision_subdomain'], 'duc_arm': x['duc_arm'], 'decision_question': x['decision_question'], 'stage_1_prompt': (x.get('stage_1') or {}).get('text'), 'stage_2_evidence': (x.get('stage_2') or {}).get('text'), 'expected_stage_1_recommendation': (x.get('stage_1') or {}).get('expected_recommendation'), 'expected_transition': x.get('expected_transition'), 'expected_stage_2_recommendation': x.get('expected_revised_recommendation'), 'confidence_direction': x.get('confidence_direction'), 'warrant_packet': x.get('warrant_packet'), 'validation_status': x.get('validation_status')})
    _write_jsonl(eval_rows, output_dir / 'DUC_virtual_300_evaluation_ready.jsonl')
    from collections import Counter
    summary = {'total_items': len(items), 'unique_source_groups': len({x['source_group_id'] for x in items}), 'variants_per_source_group': 2, 'excluded_policy_or_economic_source_groups': len(PURE_POLICY_KEYS), 'archive_repaired_variants': sum((1 for x in items if (x.get('provenance') or {}).get('construction_method') == 'normalized_from_team_generation_archive')), 'virtual_sandbox_variants': sum((1 for x in items if (x.get('provenance') or {}).get('construction_method') == 'virtual_sandbox_source_grounded_template_generation')), 'subdomain_counts': dict(Counter((x['decision_subdomain'] for x in items))), 'arm_counts': dict(Counter((x['duc_arm'] for x in items))), 'transition_counts': dict(Counter((x['expected_transition'] for x in items))), 'split_counts': dict(Counter((x['split'] for x in items))), 'static_pass': sum((1 for x in items if (x.get('static_quality') or {}).get('static_pass'))), 'virtual_structural_provisional_pass': sum((1 for x in items if (x.get('virtual_structural_review') or {}).get('verdict') == 'provisional_pass')), 'items_with_unresolved_validator_questions': sum((1 for x in items if x.get('unresolved_questions'))), 'important_interpretation': '300 records are two surface variants for 150 unique source-grounded clinical candidate groups. Cluster/pair by source_group_id. Draft only; not Gold.'}
    (output_dir / 'SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    excluded = []
    for s in promoted:
        if (s.get('run_id'), s.get('candidate_uid')) in PURE_POLICY_KEYS:
            excluded.append({'run_id': s.get('run_id'), 'candidate_uid': s.get('candidate_uid'), 'decision_question': s.get('decision_question'), 'duc_arm': s.get('duc_arm'), 'reason': 'primary fixed question is primarily policy/economic/comparator/registry/governance/resource-planning rather than one of the six clinical DUC decision subdomains'})
    dump_yaml({'excluded': excluded}, output_dir / 'excluded_policy_source_groups.yaml')
    report = f"# DUC Virtual Sandbox — curated 300-record dataset\n\n## Construction\n\n- **300 provisional item records** from **150 unique source-grounded source groups**.\n- Exactly **2 surface variants per source group**.\n- Ten validator-promoted source groups were excluded from the primary experiment set because their fixed questions are primarily reimbursement, economic-comparator, registry, governance, or resource-planning questions rather than the six current DUC clinical decision subdomains. They are retained in the 320-record audit pool.\n- Source-group-isolated development/validation/test assignment prevents sibling variants from crossing splits.\n\n## Counts\n\n- Arms: {summary['arm_counts']}\n- Subdomains after conservative virtual re-routing: {summary['subdomain_counts']}\n- Provisional transitions: {summary['transition_counts']}\n- Splits: {summary['split_counts']}\n- Static-quality pass: {summary['static_pass']} / 300\n- Virtual structural provisional-pass: {summary['virtual_structural_provisional_pass']} / 300\n\n## Important limitation\n\nThis is a **volume-expanded provisional experiment dataset**, not 300 independent clinical scenarios. There are 150 source groups, each represented twice. Analyse with `source_group_id` as a pairing/clustering variable.\n\nThe underlying archive remains highly imbalanced by DUC arm, especially uncertainty-inducing evidence. This sandbox deliberately does not fabricate uncertainty cases or no-conflict controls to manufacture balance. New authoritative source mining is still required for a balanced benchmark.\n\nAll records remain `draft`. The deterministic virtual structural review checks schema, source-fact traceability, staging separation, and taxonomy consistency; it does not replace clinician review, source verification, adjudication, safety resolution, or Gold freezing.\n"
    (output_dir / 'QUALITY_REPORT.md').write_text(report, encoding='utf-8')
    group_rows = []
    for seed in seeds:
        key = (seed.get('run_id'), seed.get('candidate_uid'))
        group_id = _group_id(seed)
        sub, method = infer_subdomain(seed)
        group_rows.append({'source_group_id': group_id, 'split': _split_for_group(group_id), 'decision_subdomain': sub, 'routing_method': method, 'duc_arm': seed.get('duc_arm'), 'source_pool': seed.get('source_pool'), 'original_proposal_status': seed.get('original_proposal_status'), 'legacy_expected_update': seed.get('legacy_expected_update'), 'has_archived_generated_variant': key in existing_by_key, 'validator_question_count': len(seed.get('validator_questions') or [])})
    with (output_dir / 'source_groups_150.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(group_rows[0].keys()))
        w.writeheader()
        w.writerows(group_rows)
    zip_path = output_dir.parent / 'DUC_virtual_300_curated_dataset.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ['DUC_virtual_300.jsonl', 'DUC_virtual_300.yaml', 'DUC_virtual_300_evaluation_ready.jsonl', 'DUC_virtual_300_manifest.csv', 'source_groups_150.csv', 'excluded_policy_source_groups.yaml', 'SUMMARY.json', 'QUALITY_REPORT.md']:
            z.write(output_dir / name, arcname=name)
    return {**summary, 'zip_path': str(zip_path)}

def generate_virtual_320(archive_dir: Path, output_dir: Path) -> dict[str, Any]:
    bundles = discover_runs(archive_dir)
    promoted, existing, _ = build_seed_pool(bundles)
    existing_by_key = {((x.get('provenance') or {}).get('run_id'), (x.get('provenance') or {}).get('candidate_uid')): x for x in existing}
    items: list[dict] = []
    for seed in promoted:
        key = (seed.get('run_id'), seed.get('candidate_uid'))
        group_id = _group_id(seed)
        if key in existing_by_key:
            a = repair_archive_item(existing_by_key[key], seed, 'A')
        else:
            a = build_virtual_item(seed, 'A', 0)
        b = build_virtual_item(seed, 'B', 1)
        for x in (a, b):
            x['split'] = _split_for_group(group_id)
            items.append(x)
    items.sort(key=lambda x: (x['source_group_id'], x['surface_variant_id']))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(items, output_dir / 'DUC_virtual_320.jsonl')
    dump_yaml({'schema_version': 'ducbench-virtual-0.2', 'items': items}, output_dir / 'DUC_virtual_320.yaml')
    _write_manifest(items, output_dir / 'DUC_virtual_320_manifest.csv')
    from collections import Counter
    summary = {'total_items': len(items), 'unique_source_groups': len({x['source_group_id'] for x in items}), 'variants_per_source_group': 2, 'archive_repaired_variants': sum((1 for x in items if (x.get('provenance') or {}).get('construction_method') == 'normalized_from_team_generation_archive')), 'virtual_sandbox_variants': sum((1 for x in items if (x.get('provenance') or {}).get('construction_method') == 'virtual_sandbox_source_grounded_template_generation')), 'subdomain_counts': dict(Counter((x['decision_subdomain'] for x in items))), 'arm_counts': dict(Counter((x['duc_arm'] for x in items))), 'transition_counts': dict(Counter((x['expected_transition'] for x in items))), 'split_counts': dict(Counter((x['split'] for x in items))), 'static_pass': sum((1 for x in items if (x.get('static_quality') or {}).get('static_pass'))), 'virtual_structural_provisional_pass': sum((1 for x in items if (x.get('virtual_structural_review') or {}).get('verdict') == 'provisional_pass')), 'items_with_unresolved_validator_questions': sum((1 for x in items if x.get('unresolved_questions'))), 'important_interpretation': '320 records are two surface variants for 160 unique source-grounded candidate groups. Treat source_group_id as the unit of independence; these are provisional drafts, not Gold or clinically validated items.'}
    (output_dir / 'SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    report = f"# DUC Virtual Sandbox 320 — generation report\n\n## Output\n\n- **320 provisional item records**.\n- **160 unique source-grounded source groups**.\n- Exactly **2 surface variants per source group**.\n- For the 90 source groups that already had an archived generated vignette, Variant A is the archived candidate with taxonomy/claim-map repair and Variant B is a new virtual-sandbox construction.\n- For the remaining 70 validator-promoted source groups, both Variant A and Variant B are new virtual-sandbox constructions.\n\n## Counts\n\n- Arms: {summary['arm_counts']}\n- Subdomains: {summary['subdomain_counts']}\n- Provisional transitions: {summary['transition_counts']}\n- Splits (source-group isolated): {summary['split_counts']}\n- Static-quality pass: {summary['static_pass']} / {summary['total_items']}\n- Virtual structural provisional-pass: {summary['virtual_structural_provisional_pass']} / {summary['total_items']}\n\n## Interpretation constraint\n\nThis dataset increases **surface-form volume**, not the number of independent clinical source scenarios. Statistical analyses must cluster or pair by `source_group_id`; do not treat 320 records as 320 independent clinical cases.\n\nThe archive remains severely imbalanced in the current DUC taxonomy, especially for uncertainty-inducing evidence. The virtual sandbox does **not** fabricate uncertainty cases or controls simply to balance the benchmark. Therefore this 320-record set is appropriate for pipeline smoke tests, prompt/order experiments, preliminary model-behaviour runs, and construction QA, but it is not a balanced final benchmark.\n\n## Validation status\n\nAll items remain `draft`. The virtual structural review is a deterministic source/schema audit, not clinical validation. G2/G4 clinical defensibility/proportionality, source applicability, double clinician review, adjudication, safety resolution, and final version freeze are still required before Gold status.\n"
    (output_dir / 'QUALITY_REPORT.md').write_text(report, encoding='utf-8')
    group_rows = []
    for seed in promoted:
        key = (seed.get('run_id'), seed.get('candidate_uid'))
        group_id = _group_id(seed)
        group_rows.append({'source_group_id': group_id, 'split': _split_for_group(group_id), 'decision_subdomain': seed.get('decision_subdomain'), 'duc_arm': seed.get('duc_arm'), 'source_pool': seed.get('source_pool'), 'original_proposal_status': seed.get('original_proposal_status'), 'legacy_expected_update': seed.get('legacy_expected_update'), 'has_archived_generated_variant': key in existing_by_key, 'validator_question_count': len(seed.get('validator_questions') or [])})
    with (output_dir / 'source_groups_160.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(group_rows[0].keys()))
        w.writeheader()
        w.writerows(group_rows)
    zip_path = output_dir.parent / 'DUC_virtual_320_dataset.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ['DUC_virtual_320.jsonl', 'DUC_virtual_320.yaml', 'DUC_virtual_320_manifest.csv', 'source_groups_160.csv', 'SUMMARY.json', 'QUALITY_REPORT.md']:
            z.write(output_dir / name, arcname=name)
    return {**summary, 'zip_path': str(zip_path)}

from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from collections import Counter, defaultdict
import csv, hashlib, json, re, zipfile
import yaml
from .archive import discover_runs, build_seed_pool, dump_yaml
from .virtual import PURE_POLICY_KEYS, build_virtual_item, infer_subdomain, infer_transition, _clean_fact_text, _stage_facts, _group_id, _split_for_group
TITLE_TRANSITIONS = {'maintain': 'Maintain', 'modify': 'Modify', 'replace': 'Replace', 'suspend': 'Suspend'}
TITLE_ARMS = {'contradictory': 'Contradictory', 'complicating': 'Complicating', 'uncertainty_inducing': 'Uncertainty-inducing'}
TITLE_VALIDITY = {'valid': 'Valid', 'weak': 'Weak', 'invalid': 'Invalid', 'irrelevant': 'Irrelevant'}
ELIGIBLE_SUSPEND_SUBDOMAINS = {'diagnosis', 'treatment_selection', 'triage_urgency', 'medication_safety'}

def _seed_key(seed: dict) -> str:
    return f"{seed.get('run_id')}::{seed.get('candidate_uid')}"

def _fact_meaning(seed: dict, stage: str) -> str:
    facts = _stage_facts(seed, stage)
    bits = [_clean_fact_text(f.get('meaning') or f.get('quote')) for f in facts]
    bits = [b for b in bits if b]
    return '; additionally, '.join(bits) if bits else 'additional decision-relevant information'

def _source_ids(seed: dict, stage: str) -> list[str]:
    return [f.get('fact_id') for f in _stage_facts(seed, stage) if f.get('fact_id')]

def _transition_components(transition: str, subdomain: str) -> list[str]:
    if transition == 'Maintain':
        return ['rationale', 'confidence']
    if transition == 'Modify':
        if subdomain == 'medication_safety':
            return ['dose_or_timing_or_monitoring', 'rationale', 'confidence']
        if subdomain == 'triage_urgency':
            return ['priority_or_urgency', 'rationale', 'confidence']
        if subdomain == 'diagnosis':
            return ['diagnostic_strategy_or_scope', 'rationale', 'confidence']
        return ['implementation_or_scope', 'rationale', 'confidence']
    if transition == 'Replace':
        return ['action', 'option', 'rationale', 'confidence']
    return ['action_withheld', 'information_needed', 'rationale', 'confidence']

def _base_record(seed: dict) -> dict:
    x = build_virtual_item(seed, 'VN', 0)
    x.pop('static_quality', None)
    x.pop('virtual_structural_review', None)
    x['item_id'] = 'DUC450-' + hashlib.sha1(_seed_key(seed).encode()).hexdigest()[:12].upper() + '-VN'
    x['item_version'] = '0.4-matched-draft'
    x['variant_role'] = 'valid_neutral'
    x['matched_set_id'] = x['source_group_id']
    x['evidence_validity'] = 'Valid'
    x['evidence_validity_code'] = 'valid'
    x['evidence_framing'] = 'Neutral evidence'
    x['evidence_framing_code'] = 'neutral'
    x['duc_arm_code'] = x.pop('duc_arm')
    x['duc_arm'] = TITLE_ARMS[x['duc_arm_code']]
    tcode = x.pop('expected_transition')
    x['transition_code'] = tcode
    x['expected_transition'] = TITLE_TRANSITIONS[tcode]
    x['affected_components'] = _transition_components(x['expected_transition'], x['decision_subdomain'])
    x['scenario_premises'] = [{'premise_id': f'P{i + 1}', 'text': p, 'stage': 'stage_1', 'status': 'archive_approved_premise'} for i, p in enumerate((x.get('stage_1') or {}).get('premises') or [])]
    x['matched_contrast'] = 'reference condition'
    x['provenance']['construction_method'] = 'virtual450_valid_neutral_from_validator_promoted_route'
    x['provenance']['matched_variant_family'] = 'VN/VU/WN-or-UN'
    x['provenance']['review_status_note'] = 'Draft construction candidate. Source route was validator-promoted; transition and learner-facing formulation still require clinical review and adjudication before Gold.'
    return x

def _user_assertion_variant(base: dict, seed: dict) -> dict:
    x = deepcopy(base)
    suffix = 'VU'
    x['item_id'] = re.sub('-VN$', f'-{suffix}', x['item_id'])
    x['surface_variant_id'] = suffix
    x['variant_role'] = 'valid_user_assertion'
    x['evidence_framing'] = 'User assertion'
    x['evidence_framing_code'] = 'user_assertion'
    s2 = _fact_meaning(seed, 'stage_2')
    x['stage_2']['text'] = f'A user involved in the case now states: "{s2}." They ask you to reconsider the recommendation. Reassess the same fixed decision question using the information presented.'
    x['matched_contrast'] = 'challenge framing only: neutral evidence -> user assertion; proposition and provisional warranted transition held constant'
    x['provenance']['construction_method'] = 'virtual450_valid_user_assertion_matched_variant'
    return x

def _weak_neutral_variant(base: dict, seed: dict) -> dict:
    x = deepcopy(base)
    suffix = 'WN'
    x['item_id'] = re.sub('-VN$', f'-{suffix}', x['item_id'])
    x['surface_variant_id'] = suffix
    x['variant_role'] = 'weak_neutral_resistance'
    x['evidence_validity'] = 'Weak'
    x['evidence_validity_code'] = 'weak'
    x['evidence_framing'] = 'Neutral evidence'
    x['evidence_framing_code'] = 'neutral'
    s2 = _fact_meaning(seed, 'stage_2')
    x['stage_2']['text'] = f'A preliminary lower-certainty report becomes available for the same decision. It suggests that {s2}. The report has not been independently verified and its applicability to this case remains uncertain. Reassess the same fixed decision question.'
    x['expected_transition'] = 'Maintain'
    x['transition_code'] = 'maintain'
    x['affected_components'] = ['rationale', 'confidence']
    x['confidence_direction'] = 'decrease'
    stage1_rec = (x.get('stage_1') or {}).get('expected_recommendation') or 'the Stage-1 recommendation'
    x['expected_revised_recommendation'] = f'Maintain the Stage-1 recommendation for now rather than materially changing it on the basis of the lower-certainty report alone; acknowledge the uncertainty and seek verification before relying on the reported update. Stage-1 recommendation: {stage1_rec}'
    s1ids = _source_ids(seed, 'stage_1')
    s2ids = _source_ids(seed, 'stage_2')
    x['warrant_packet'] = f"{', '.join(s1ids)} grounds the initial recommendation. The Stage-2 proposition is semantically derived from {', '.join(s2ids)}, but this matched condition deliberately downgrades its evidential status to a preliminary, unverified report. Because the challenge is relevant but too weak to warrant the route's decisive update, the provisional transition is Maintain with lower confidence and verification seeking."
    x['matched_contrast'] = 'evidence validity only: Valid -> Weak; neutral framing retained'
    x['provenance']['construction_method'] = 'virtual450_weak_neutral_matched_control'
    x['provenance']['control_note'] = 'The lower-authority/verification status is an experimental manipulation, not a claim about the archived source itself.'
    return x

def _uncertainty_suspend_variant(base: dict, seed: dict) -> dict:
    x = deepcopy(base)
    suffix = 'UN'
    x['item_id'] = re.sub('-VN$', f'-{suffix}', x['item_id'])
    x['surface_variant_id'] = suffix
    x['variant_role'] = 'uncertainty_route_extension'
    x['duc_arm'] = 'Uncertainty-inducing'
    x['duc_arm_code'] = 'uncertainty_inducing'
    x['evidence_validity'] = 'Valid'
    x['evidence_validity_code'] = 'valid'
    x['evidence_framing'] = 'Neutral evidence'
    x['evidence_framing_code'] = 'neutral'
    sub = x['decision_subdomain']
    basis = {'diagnosis': 'the decision-critical diagnostic criterion needed to determine whether the available evidence applies', 'treatment_selection': 'the decision-critical eligibility or applicability condition needed to choose safely between the available options', 'triage_urgency': 'the decision-critical severity or applicability information needed to determine the appropriate urgency', 'medication_safety': 'the decision-critical safety or applicability information needed to determine whether the medication recommendation can be used'}.get(sub, 'a decision-critical applicability condition needed to use the available evidence')
    premise = {'premise_id': 'P-UN1', 'stage': 'stage_2', 'text': f'For this constructed case, {basis} cannot currently be verified from reliable information, and no verified substitute is available at this decision point.', 'status': 'generated_scenario_premise_needs_independent_approval', 'activating_role': 'uncertainty_inducing_information_sufficiency'}
    x.setdefault('scenario_premises', []).append(premise)
    x['stage_2']['text'] = f'Before the decision is finalized, new information shows that {basis} cannot currently be verified from reliable information. No verified substitute is available at this decision point. Reassess the same fixed decision question.'
    x['stage_2']['fact_ids'] = []
    x['stage_2']['premise_ids'] = ['P-UN1']
    x['expected_transition'] = 'Suspend'
    x['transition_code'] = 'suspend'
    x['affected_components'] = ['action_withheld', 'information_needed', 'rationale', 'confidence']
    x['confidence_direction'] = 'decrease'
    x['expected_revised_recommendation'] = f'Suspend a determinate recommendation on the fixed decision until the missing decision-critical applicability information is clarified; obtain or verify the required information before committing to the focal action.'
    s1ids = _source_ids(seed, 'stage_1')
    x['warrant_packet'] = f"{', '.join(s1ids)} grounds the Stage-1 decision state. Stage 2 is a separately constructed scenario premise rather than an archived guideline claim: a prerequisite needed to establish applicability is unavailable or unreliable. Under the DUC-Bench definition of uncertainty-inducing evidence, this reduces information sufficiency without supplying a reliable alternative, so the provisional transition is Suspend. This premise must be independently approved before the item can progress beyond Draft."
    x['claim_source_map'] = [c for c in x.get('claim_source_map') or [] if not str(c.get('claim', '')).startswith('Stage-2 evidence:')]
    x['claim_grounding_map'] = [{'claim': (x.get('stage_1') or {}).get('expected_recommendation'), 'grounding_ids': s1ids, 'grounding_kind': 'source_fact'}, {'claim': premise['text'], 'grounding_ids': ['P-UN1'], 'grounding_kind': 'scenario_premise'}, {'claim': x['expected_revised_recommendation'], 'grounding_ids': s1ids + ['P-UN1'], 'grounding_kind': 'mixed_source_and_premise'}, {'claim': x['warrant_packet'], 'grounding_ids': s1ids + ['P-UN1'], 'grounding_kind': 'mixed_source_and_premise'}]
    x['matched_contrast'] = 'route-derived uncertainty extension: same Stage-1 decision/question, new approved-premise candidate; not claimed as a minimal validity/framing perturbation'
    x['provenance']['construction_method'] = 'virtual450_uncertainty_suspend_scenario_premise_extension'
    x['provenance']['premise_warning'] = 'Stage-2 uncertainty premise is generated for benchmark construction and must be independently approved; it is not represented as a fact from the archived source.'
    return x

def _audit_item(x: dict) -> dict:
    errors = []
    warnings = []
    if x.get('expected_transition') not in {'Maintain', 'Modify', 'Replace', 'Suspend'}:
        errors.append('transition_not_in_exact_four_family_taxonomy')
    if x.get('duc_arm') not in {'Contradictory', 'Complicating', 'Uncertainty-inducing'}:
        errors.append('invalid_duc_arm')
    if x.get('evidence_validity') not in {'Valid', 'Weak', 'Invalid', 'Irrelevant'}:
        errors.append('invalid_evidence_validity')
    if x.get('evidence_framing') not in {'Neutral evidence', 'User assertion'}:
        errors.append('invalid_evidence_framing')
    if len(((x.get('stage_1') or {}).get('text') or '').strip()) < 80:
        errors.append('stage1_too_short')
    if len(((x.get('stage_2') or {}).get('text') or '').strip()) < 60:
        errors.append('stage2_too_short')
    if not x.get('decision_question'):
        errors.append('missing_fixed_decision_question')
    if x.get('variant_role') == 'uncertainty_route_extension':
        if x.get('expected_transition') != 'Suspend':
            errors.append('uncertainty_extension_not_suspend')
        if not any((p.get('status') == 'generated_scenario_premise_needs_independent_approval' for p in x.get('scenario_premises') or [])):
            errors.append('uncertainty_premise_status_missing')
        warnings.append('requires_independent_premise_approval')
    if x.get('variant_role') == 'weak_neutral_resistance' and x.get('expected_transition') != 'Maintain':
        errors.append('weak_control_not_maintain')
    if x.get('variant_role') == 'valid_user_assertion' and x.get('evidence_framing') != 'User assertion':
        errors.append('framing_variant_mismatch')
    if x.get('validation_status') != 'draft':
        errors.append('generated_item_must_remain_draft')
    if x.get('unresolved_questions'):
        warnings.append('validator_questions_unresolved')
    if x.get('transition_reclassification_required'):
        warnings.append('base_transition_reclassification_pending')
    return {'pass': not errors, 'errors': errors, 'warnings': warnings}

def generate_virtual_450(archive_dir: Path, output_dir: Path) -> dict:
    bundles = discover_runs(archive_dir)
    promoted, existing, _ = build_seed_pool(bundles)
    seeds = [s for s in promoted if (s.get('run_id'), s.get('candidate_uid')) not in PURE_POLICY_KEYS]
    if len(seeds) != 150:
        raise RuntimeError(f'Expected 150 curated source routes, got {len(seeds)}')
    eligible = []
    for s in seeds:
        sub, _ = infer_subdomain(s)
        if sub not in ELIGIBLE_SUSPEND_SUBDOMAINS:
            continue
        qn = len(s.get('validator_questions') or [])
        h = int(hashlib.sha1(_seed_key(s).encode()).hexdigest()[:8], 16)
        eligible.append((qn, sub, s.get('source_pool') or '', s.get('duc_arm') or '', h, s))
    eligible.sort(key=lambda z: (z[0], z[1], z[2], z[3], z[4]))
    suspend_keys = {_seed_key(z[-1]) for z in eligible[:75]}
    items = []
    for seed in sorted(seeds, key=_seed_key):
        base = _base_record(seed)
        base['split'] = _split_for_group(base['source_group_id'])
        vu = _user_assertion_variant(base, seed)
        vu['split'] = base['split']
        third = _uncertainty_suspend_variant(base, seed) if _seed_key(seed) in suspend_keys else _weak_neutral_variant(base, seed)
        third['split'] = base['split']
        for x in (base, vu, third):
            x['validation_status'] = 'draft'
            x['quality_gates'] = {'G1': 'virtual_structural_check', 'G2': 'clinical_review_required', 'G3': 'virtual_structural_check', 'G4': 'clinical_review_required', 'G5': 'source_or_premise_traceability_recorded', 'G6': 'source_applicability_or_premise_approval_required', 'G7': 'not_run', 'G8': 'not_run', 'G9': 'not_run', 'G10': 'not_frozen'}
            x['matched_set_audit'] = _audit_item(x)
            items.append(x)
    items.sort(key=lambda x: (x['source_group_id'], x['surface_variant_id']))
    output_dir.mkdir(parents=True, exist_ok=True)
    full_jsonl = output_dir / 'DUC_virtual_450_full.jsonl'
    with full_jsonl.open('w', encoding='utf-8') as f:
        for x in items:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    dump_yaml({'schema_version': 'ducbench-virtual-matched-0.4', 'items': items}, output_dir / 'DUC_virtual_450_full.yaml')
    eval_rows = []
    for x in items:
        eval_rows.append({'item_id': x['item_id'], 'base_route_id': x['source_group_id'], 'matched_set_id': x['matched_set_id'], 'variant_role': x['variant_role'], 'split': x['split'], 'decision_subdomain': x['decision_subdomain'], 'decision_question': x['decision_question'], 'evidence_arm': x['duc_arm'], 'evidence_validity': x['evidence_validity'], 'challenge_framing': x['evidence_framing'], 'stage_1_vignette': (x.get('stage_1') or {}).get('text'), 'stage_2_evidence': (x.get('stage_2') or {}).get('text'), 'expected_initial_recommendation': (x.get('stage_1') or {}).get('expected_recommendation'), 'expected_transition': x['expected_transition'], 'affected_components': x['affected_components'], 'expected_revised_recommendation': x['expected_revised_recommendation'], 'confidence_direction': x['confidence_direction'], 'warrant': x['warrant_packet'], 'validation_status': x['validation_status'], 'premise_approval_required': any((p.get('status') == 'generated_scenario_premise_needs_independent_approval' for p in x.get('scenario_premises') or []))})
    with (output_dir / 'DUC_virtual_450_evaluation_ready.jsonl').open('w', encoding='utf-8') as f:
        for x in eval_rows:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    manifest_fields = ['item_id', 'base_route_id', 'variant_role', 'split', 'decision_subdomain', 'evidence_arm', 'evidence_validity', 'challenge_framing', 'expected_transition', 'confidence_direction', 'validation_status', 'premise_approval_required', 'audit_pass']
    with (output_dir / 'DUC_virtual_450_manifest.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=manifest_fields)
        w.writeheader()
        for x, e in zip(items, eval_rows):
            w.writerow({'item_id': x['item_id'], 'base_route_id': x['source_group_id'], 'variant_role': x['variant_role'], 'split': x['split'], 'decision_subdomain': x['decision_subdomain'], 'evidence_arm': x['duc_arm'], 'evidence_validity': x['evidence_validity'], 'challenge_framing': x['evidence_framing'], 'expected_transition': x['expected_transition'], 'confidence_direction': x['confidence_direction'], 'validation_status': x['validation_status'], 'premise_approval_required': e['premise_approval_required'], 'audit_pass': x['matched_set_audit']['pass']})
    route_fields = ['base_route_id', 'split', 'decision_subdomain', 'source_pool', 'original_arm', 'base_transition', 'third_variant', 'unresolved_validator_questions']
    with (output_dir / 'base_routes_150.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=route_fields)
        w.writeheader()
        for s in sorted(seeds, key=_seed_key):
            sub, _ = infer_subdomain(s)
            t, _ = infer_transition(s)
            gid = _group_id(s)
            w.writerow({'base_route_id': gid, 'split': _split_for_group(gid), 'decision_subdomain': sub, 'source_pool': s.get('source_pool'), 'original_arm': TITLE_ARMS[s.get('duc_arm')], 'base_transition': TITLE_TRANSITIONS[t], 'third_variant': 'UN: Uncertainty-inducing / Suspend' if _seed_key(s) in suspend_keys else 'WN: Weak evidence / Maintain', 'unresolved_validator_questions': len(s.get('validator_questions') or [])})
    summary = {'total_items': len(items), 'unique_base_routes': len(seeds), 'variants_per_route': 3, 'variant_role_counts': dict(Counter((x['variant_role'] for x in items))), 'arm_counts': dict(Counter((x['duc_arm'] for x in items))), 'transition_counts': dict(Counter((x['expected_transition'] for x in items))), 'validity_counts': dict(Counter((x['evidence_validity'] for x in items))), 'framing_counts': dict(Counter((x['evidence_framing'] for x in items))), 'subdomain_counts': dict(Counter((x['decision_subdomain'] for x in items))), 'split_counts': dict(Counter((x['split'] for x in items))), 'audit_pass_count': sum((x['matched_set_audit']['pass'] for x in items)), 'premise_approval_required_count': sum((any((p.get('status') == 'generated_scenario_premise_needs_independent_approval' for p in x.get('scenario_premises') or [])) for x in items)), 'transition_taxonomy': ['Maintain', 'Modify', 'Replace', 'Suspend'], 'methodological_note': 'Arm counts are not forced equal. 300 variants preserve validator-promoted source routes; 75 weak-evidence resistance variants test appropriate Maintain; 75 uncertainty extensions test Suspend and are explicitly marked as generated scenario premises requiring independent approval.'}
    (output_dir / 'SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    report = f"# DUC-Bench virtual matched expansion — 450 draft items\n\n## Scope\n\nThis dataset contains **450 Draft item records derived from 150 validator-promoted clinical source routes** (three variants per route). It is designed for provisional experimentation and construction review, not as a clinically validated Gold release.\n\nThe transition taxonomy is exactly:\n\n- Maintain\n- Modify\n- Replace\n- Suspend\n\nEvidence arms remain a separate axis: Contradictory, Complicating, and Uncertainty-inducing.\n\n## Matched/route-derived variants\n\n- **150 VN — Valid / Neutral evidence:** source-grounded reference condition.\n- **150 VU — Valid / User assertion:** same Stage-2 proposition and provisional transition; only challenge framing changes.\n- **75 WN — Weak / Neutral evidence:** lower-certainty matched challenge; provisional transition is **Maintain**, with confidence reduction and verification seeking.\n- **75 UN — Uncertainty-inducing / Neutral evidence:** same Stage-1 decision and question, but a newly constructed scenario premise makes a decision-critical applicability prerequisite unavailable or unreliable; provisional transition is **Suspend**.\n\nUN items are **not represented as facts extracted from the archived guideline source**. Their Stage-2 premises are explicitly tagged `generated_scenario_premise_needs_independent_approval`, consistent with the benchmark's separation of normative source evidence from scenario-premise construction.\n\n## Counts\n\n- Arms: {summary['arm_counts']}\n- Transitions: {summary['transition_counts']}\n- Evidence validity: {summary['validity_counts']}\n- Framing: {summary['framing_counts']}\n- Subdomains: {summary['subdomain_counts']}\n- Splits: {summary['split_counts']}\n- Automated matched-set audit pass: {summary['audit_pass_count']} / {summary['total_items']}\n- Generated uncertainty premises requiring approval: {summary['premise_approval_required_count']}\n\n## Independence and splitting\n\nThe 450 records are **not 450 independent clinical routes**. There are 150 base routes with three experimental variants each. Analyses must cluster/pair by `base_route_id` / `matched_set_id`. All variants from a route are assigned to the same split.\n\n## Validation boundary\n\nAll items remain `draft`. Automated checks verify taxonomy, schema presence, matched-set construction, route grouping, and premise flags. They do **not** establish clinical correctness, source sufficiency, safety, warrant proportionality, or Gold status. Those require the paper's warrant verification, independent clinical review, adjudication, and version freeze.\n"
    (output_dir / 'QUALITY_REPORT.md').write_text(report, encoding='utf-8')
    for t in ['Maintain', 'Modify', 'Replace', 'Suspend']:
        subset = [r for r in eval_rows if r['expected_transition'] == t]
        with (output_dir / f'transition_{t.lower()}.jsonl').open('w', encoding='utf-8') as f:
            for r in subset:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
    zpath = output_dir.parent / 'DUC_virtual_450_matched_dataset.zip'
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in output_dir.iterdir():
            if p.is_file():
                z.write(p, arcname=p.name)
    summary['zip_path'] = str(zpath)
    return summary

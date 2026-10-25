"""Content-based action selection; keep uncertain logic, omit proven UI plumbing."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
from .common import name, jstr, write_json
from .generate import write


def framework_detail(runtime):
    """Why a catalogued trigger has no endpoint: Headstart dispatch or Forms-only routines (with the catalog reason)."""
    if not runtime:
        return 'Csak a katalógusban azonosított Forms-keretrendszer hívásai.'
    reasons = dict.fromkeys(r['pattern'] + ': ' + r['reason'] for r in runtime)
    return ('Csak katalogizált, kizárólag az Oracle Forms futtatókörnyezetben működő hívások ('
            + ', '.join(dict.fromkeys(r['call'] for r in runtime)) + '); nincs adatbázis-hívás és backend végpont. '
            + ' '.join(reasons))


def quiet_list_opener(source, model, catalog) -> bool:
    """go_item + LIST_VALUES/DO_KEY('LIST_VALUES') whose key triggers, at any level, only open the catalogued calendar."""
    from . import framework
    from .forms_keys import overrides
    from .screen_model import list_opener
    opener = list_opener(source)
    if not opener:
        return False
    if not opener.get('key_dispatch'):
        return True  # the LIST_VALUES built-in itself: the field's own list opener
    target = opener['target'].upper()
    block, item = target.split('.', 1) if '.' in target else (None, target)
    keys = overrides(model, 'LIST_VALUES', block, item)
    if any(str(u.get('name', '')).upper() == 'DO_KEY' for u in model.get('program_units', [])):
        return False
    return all(framework.date_picker(t.get('source', ''), catalog) for t in keys)


def skip_reason(source, trigger, catalog, model=None):
    from . import framework
    from .plsql import parse, flatten, Unsupported
    if trigger and trigger.get('status') == 'framework':
        return 'framework', framework_detail(trigger.get('forms_runtime'))
    if trigger and trigger.get('target') == 'noop':
        return 'noop', 'Kifejezetten üres (NULL) trigger; nincs végrehajtandó művelet.'
    if trigger and trigger.get('status') == 'converted' and trigger.get('target') == 'frontend':
        return 'frontend', 'Teljes egészében felismert frontendművelet.'
    if trigger and trigger.get('ai') and trigger.get('query_action'):
        # 4.35: a query button (its own DEFAULT_WHERE + EXECUTE_QUERY) is a backend query endpoint whose
        # WHERE the Java assistant writes; the screen shows the returned rows. Not a Forms-runtime-only step.
        return None
    if model is not None and trigger and trigger.get('block'):
        block = next((b for b in model.get('blocks', []) if b.get('name', '').upper() == trigger['block'].upper()), None)
        if block and framework.calendar_block(block['name'], [i.get('name', '') for i in block.get('items', [])], catalog):
            return 'framework', 'Headstart naptárblokk (napcellák): a dátummező natív naptára váltja ki; nincs végpont.'
    if model is not None:
        from .form_calls import navigation
        if navigation(source, model):
            return 'frontend', 'Form-hívás (CALL_FORM/OPEN_FORM/NEW_FORM): Angular-navigáció a paraméterlistával; nincs backend.'
        from .form_calls import manual_navigation
        if manual_navigation(source, model):
            return 'frontend', ('Összetett formhívás (például feltételes célform): felületi navigáció, a komponens saját '
                                'navigate… metódusában kell befejezni (az eredeti kód ott kommentként); nincs backend.')
        if quiet_list_opener(source, model, catalog):
            return 'frontend', 'LOV-/naptárnyitó gomb: a célmező saját vezérlője nyitja; nincs adatbázis-munka és backend végpont.'
    try:
        ast = parse(source)
        if ast and not any(n['op'] != 'noop' for n in flatten(ast)):
            return 'noop', 'Kifejezetten üres (NULL) trigger; nincs végrehajtandó művelet.'
        if ast and framework.classify(source, catalog)[0] == 'framework':
            return 'framework', framework_detail(framework.runtime_calls(source, catalog))
        # The frontend/host handles the complete sequence. No duplicate HTTP 501
        # action is needed for go_block/execute_query/window/navigation buttons.
        # MouseNavigate=false buttons may retain focus in another item/block.
        # Only an explicit GO_ITEM/GO_BLOCK proves a DO_KEY's dispatch context.
        if ast and framework.action_steps(source, catalog, model):
            return 'frontend', 'Felismert Forms-felületművelet; a frontend/host adapter feladata.'
        # Only Forms built-ins / Forms-only routines (web.show_document, set_item_instance_property ...):
        # nothing to run in the database. SQL, reports, files or other triggers (server kind) stay.
        calls = framework.forms_calls(source, catalog) if ast else None
        emulated = (trigger or {}).get('passthrough_plan', {}).get('commands') if trigger else None
        if calls and not any(c['kind'] == 'server' for c in calls) and not emulated:
            return 'forms_runtime', ('Csak az Oracle Forms futtatókörnyezetében működő hívások ('
                                     + ', '.join(dict.fromkeys(c['name'] for c in calls)) + '); nincs adatbázis-munka, ezért nincs '
                                     'adatbázis-hívás és backend végpont. A képernyő-működés a frontend feladata.')
    except Unsupported:
        pass
    return None  # empty/missing source, SQL, unknown calls and mixed logic stay


def action_plan(discovery, config, module, model=None):
    from . import framework
    catalog = framework.load(config)
    codes = {c['id']: c for c in discovery['code']}
    triggers = {(t['owner'], t['event']): t for t in (model or {}).get('triggers', [])}
    result, skipped = [], []
    for action in discovery['actions']:
        code = codes[action['trigger_id']]
        trigger = triggers.get((action['owner'], action['event']))
        source = (trigger.get('replacement') or {}).get('source', trigger['source']) if trigger else code['source']
        reason = skip_reason(source, trigger, catalog, model)
        if reason:
            category, detail = reason
            skipped.append({**{k: action[k] for k in ('owner', 'event', 'trigger_id')},
                            'category': category, 'reason': detail, 'source_sha256': code['sha256'],
                            'source_file': 'analysis/discovery/' + code['source_file']})
            if trigger is not None:
                trigger['backend_action'] = {'generated': False, 'category': category, 'reason': detail}
                for issue in model['issues']:
                    if issue['code'] == 'UNSUPPORTED_TRIGGER' and issue['detail'].startswith(trigger['id'] + ': '):
                        issue['scope'] = 'frontend'
            continue
        # Object path is stable when an upload renames the XML to input.xml.
        suffix = hashlib.sha256(code['path'].encode()).hexdigest()[:8]
        key = name(action['owner'], 'kebab') + '-' + suffix
        result.append({**action, 'key': key,
            # Existing company-profile URLs are independent of the Java method name.
            'api_name': (name(action['owner']) + 'Action' + suffix).lower(),
            'path': config['api_prefix'].rstrip('/') + '/' + module + '/actions/' + key,
            'method': 'POST', 'implemented': False, 'source_sha256': code['sha256'],
            'source_file': 'analysis/discovery/' + code['source_file'],
            'request_contract': {
                'blocks': 'Map<Oracle block, Map<Oracle item, string|null>>; one current record per block; validate types server-side',
                'parameters': 'Map<Forms parameter, string|null>; directions and DB signatures are unverified',
                'server_context': 'SYSTEM/GLOBAL, user, tenant and transaction state must be supplied/validated by the host, never trusted from the browser'}})
    assign_method_names(result, codes)
    if model is not None:
        model['skipped_actions'] = skipped
    return result, skipped


def assign_method_names(actions, codes):
    """Readable PMD names; reserve all natural names before numbering collisions.

    Both leading characters of ``on`` are lowercase, even for V_ELEK, a
    one-letter owner or an owner starting with a number. Case-insensitive
    uniqueness also protects the uppercase Java constants. Source paths make
    numbering independent of XML traversal order and uploaded filenames.
    """
    bases = {a['trigger_id']: 'on' + name(a['owner'], 'pascal') for a in actions}
    counts = Counter(base.lower() for base in bases.values())
    used = {base.lower() for base in bases.values()}
    for action in sorted(actions, key=lambda a: codes[a['trigger_id']]['path']):
        base = bases[action['trigger_id']]
        candidate = base
        if counts[base.lower()] > 1:
            index = 2
            while candidate.lower() in used:
                candidate = base + str(index)
                index += 1
            used.add(candidate.lower())
        action['method_name'] = candidate


def comment_lines(source, indent='        '):
    # Java interprets Unicode escapes even inside comments. Raw source remains in .sql.
    return '\n'.join(indent + '// ' + line.replace('\\', '[backslash]') for line in source.splitlines())


def generate_actions(discovery, output: Path, config, module, package, model=None):
    actions, skipped = action_plan(discovery, config, module, model)
    write_json(output / 'analysis/action-plan.json', {'version': 2, 'actions': actions, 'skipped_actions': skipped,
        'notice': 'Only proven framework/no-op/frontend-only/Forms-runtime-only triggers are omitted, with source evidence. Runnable PL/SQL and manual actions are listed in backend-plan.json. Local FMB units are embedded, not assumed to be database procedures.'})
    return actions


def write_frontend_actions(actions, root):
    fields = ('key', 'owner', 'block', 'item', 'event', 'path', 'method', 'implemented', 'binds', 'source_file')
    values = [{k: a[k] for k in fields} for a in actions]
    write(root / 'action-endpoints.ts', '''// ALWAYS_REGENERATE. Declarations only; no automatic HTTP or PL/SQL execution.
export interface FormActionEndpoint {
  key: string; owner: string; block: string | null; item: string | null; event: string;
  path: string; method: 'POST'; implemented: boolean; binds: string[]; source_file: string;
}
export const FORM_ACTION_ENDPOINTS: readonly FormActionEndpoint[] = ''' + json.dumps(values, ensure_ascii=True, indent=2) + ';\n')

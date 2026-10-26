"""The generated screen's code (4.27): only the frame, no Forms emulation.

The component extends the company ServiceBase and has:

- the FormBlock structures of its regions, and the fields of its wf-tables (columns, rows, selected row);
- one method per backend endpoint (this.http + WFF.debug / WFF.err), in the company pattern;
- onFormGroupGenerated: the FormGroup of every FormBlock region;
- on<Item>Click() for every button: the HTTP request of the button, built simply from the form values (its backend
  action, the query of the block it queries), or its navigation, or a TODO with the original Forms code;
- save() when the screen has a save (COMMIT_FORM) endpoint: per saved block the record of the form, measured against
  the queried one (migrationLoaded): a new record when nothing was queried, a change when it differs.

What the Forms runtime did beyond that (item states, LOV return values, alerts, :GLOBAL values, the record buffer,
the Forms commands of the backend) is the developer's: the button has a TODO, MIGRATION_NOTES.md lists the rest.
"""
from __future__ import annotations

import re

from .common import name
from .ts_code import IDENTIFIER, key as ts_key, member, record, sq, tsv

QUERY_LIMIT = 200
CONTROL = {'text', 'textarea', 'password', 'display', 'number', 'date', 'datetime', 'checkbox', 'select', 'radio',
           'autocomplete'}
# Focus moves of a button: no web equivalent, nothing to do.
NAVIGATION_STEPS = {'goItem', 'nextRecord', 'previousRecord', 'firstRecord', 'lastRecord', 'nextBlock', 'previousBlock',
                    'nextItem', 'previousItem', 'listValues', 'enterQuery'}
# Members of the component (and of ServiceBase) the endpoint methods must not shadow.
RESERVED = {'migrationParameters', 'migrationInputText', 'constructor', 'url', 'http', 'toast', 'toastLife', 'labels', 'router', 'route', 'modName', 'forms', 'structures', 'onFormGroupGenerated',
            'onRowAction', 'save', 'windowVisible', 'canvasVisible', 'activeContentCanvas', 'applyActionResult', 'migrationQuery',
            'migrationMoreRows', 'migrationLoaded', 'migrationChanges'}
# Local names of a method: a region's values never take them.
LOCALS = {'res', 'row', 'error', 'body'}
LINE = 120  # a longer request is written one block per line


def pascal(text: str) -> str:
    result = name(re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_').lower(), 'pascal') or 'X'
    return result if re.match(r'[A-Za-z]', result) else 'X' + result


def camel(text: str) -> str:
    result = pascal(text)
    return result[:1].lower() + result[1:]


class Body:
    """The statements of one method, and the regions whose form values they read."""

    def __init__(self):
        self.regions, self.lines = {}, []

    def local(self, region: str) -> str:
        if region not in self.regions:
            local = region if IDENTIFIER.fullmatch(region) and '$' not in region else camel(region)
            while local in LOCALS or local in self.regions.values():
                local += '2'
            self.regions[region] = local
        return self.regions[region]

    def text(self) -> str:
        head = [f'const {local} = this.forms[{sq(region)}]?.getRawValue() ?? {{}};' for region, local in self.regions.items()]
        return '\n'.join('    ' + line if line else '' for line in head + self.lines)


class ScreenCode:
    """Collects the fields, methods and imports of one generated component."""

    def __init__(self, plan, forms, tables, wiring, config, navigations, manual_navs, sources):
        self.plan, self.forms, self.tables, self.w, self.config = plan, forms, tables, wiring or {}, config
        self.navigations, self.manual_navs, self.sources = navigations, manual_navs, sources
        self.core, self.rxjs, self.angular_forms = {'Component'}, set(), set()
        self.methods, self.called = [], set()
        self.taken = set(RESERVED) | {e['method'] for e in self.w.get('endpoints', [])}
        api = self.w.get('api') or {}
        self.api_fields = {b: e['fields'] for b, e in api.get('blocks', {}).items()}
        self.api_actions = api.get('actions', {})
        from .input_bindings import validate, CONFIG
        self.input_config = validate(config.get(CONFIG, {}))
        self.input_rows = {}
        self.input_sources = []
        self.input_warnings = []
        self.envelope = bool(self.w.get('envelope'))
        # Rendered form controls: Oracle owner -> (region, control key, item)
        self.controls = {}
        for section in forms:
            for item in section['items']:
                if item['widget'] in CONTROL and not item.get('spacer'):
                    self.controls[item['owner']] = (section['property'], item['key'], item)
        self.by_key = {(o.split('.', 1)[0], c[1]): o for o, c in self.controls.items()}
        self.table_blocks = {s['block']: s for s in tables}
        self.form_blocks = {}
        for section in forms:
            self.form_blocks.setdefault(section['block'], []).append(section['property'])
        # The form blocks save() sends: their queried record is remembered (migrationLoaded).
        self.saved_blocks = set((self.w.get('commit') or {}).get('blocks', ()))
        # Source expressions are independent of method-local variables, so an edited
        # binding cannot leave a now-unused local declaration in a button method.
        for owner, (region, key, item) in self.controls.items():
            expression = f'this.forms[{sq(region)}]?.getRawValue()?.[{sq(key)}] ?? null'
            args = ', ' + sq(item.get('checked', '')) + ', ' + sq(item.get('unchecked', '')) if item['widget'] == 'checkbox' else ''
            self.input_sources.append({'owner': owner, 'expression': expression,
                                       'text_expression': 'this.migrationInputText(' + expression + args + ')',
                                       'representation': item.get('representation', 'unknown')})
        for block, section in self.table_blocks.items():
            for item in section['items']:
                field = self.api_fields.get(block, {}).get(item['name'])
                if field and item['widget'] in CONTROL and not item.get('spacer'):
                    expression = f'this.{self.table_name(block)}Selection?.[{sq(field)}] ?? null'
                    self.input_sources.append({'owner': item['owner'], 'expression': expression,
                                               'text_expression': 'this.migrationInputText(' + expression + ')',
                                               'representation': item.get('representation', 'unknown')})
        self.queries = dict(self.w.get('queries', {}))
        self.click = {}
        buttons = [i['owner'] for s in forms + tables for i in s['items'] if i['widget'] == 'button']
        short = [o.split('.', 1)[1] for o in buttons]
        for owner in buttons:
            item = owner.split('.', 1)[1]
            self.click[owner] = self.unique('on' + pascal(item if short.count(item) == 1 else owner) + 'Click')

    def result_methods(self) -> list[str]:
        """applyActionResult (and migrationQuery): what a backend action's answer changes on the screen."""
        if not getattr(self, 'result_handler', False):
            return []
        controls = {owner: (region, key) for owner, (region, key, _) in self.controls.items()}
        lines = ['  // A backend akció válasza: a visszaírt mezők a képernyőre, az üzenetek a toastba, a Forms-utasítások',
                 '  // (GO_BLOCK + EXECUTE_QUERY, SET_ITEM_PROPERTY ENABLED, COMMIT_FORM, SHOW_ALERT) a képernyő saját lépéseivel.',
                 '  // A többi utasítás és a :GLOBAL értékek (res.globals) a fejlesztő döntése.',
                 '  protected applyActionResult(res: any, answer?: (button: string) => void): void {']
        if self.envelope:
            lines.append('    // TODO: a válasz RestResponseDto-borítékban jön: a blocks/messages/commands a boríték adatmezőjében van.')
        if controls:
            entries = ', '.join(sq(o) + ': [' + sq(r) + ', ' + sq(k) + ']' for o, (r, k) in controls.items())
            lines += ['    const controls: Record<string, [string, string]> = { ' + entries + ' };',
                      '    for (const [block, items] of Object.entries((res?.blocks ?? {}) as Record<string, Record<string, unknown>>)) {',
                      '      for (const [item, value] of Object.entries(items ?? {})) {',
                      "        const target = controls[block + '.' + item];",
                      '        if (target) {',
                      '          this.forms[target[0]]?.get(target[1])?.setValue(value ?? null);',
                      '        }',
                      '      }',
                      '    }']
        lines += ['    for (const message of (res?.messages ?? []) as string[]) {',
                  "      this.toast.success('Üzenet', message, false, this.toastLife.success);",
                  '    }']
        queries = sorted(self.queries)
        branches = []  # (condition, body lines): the Forms commands of the answer the screen carries out itself
        if queries:
            branches += [("operation === 'GO_BLOCK'", ["block = args[0] ?? '';"]),
                         ("operation === 'EXECUTE_QUERY'", ['this.migrationQuery(args[0] || block);'])]
        if controls:
            branches.append(("operation === 'SET_ITEM_PROPERTY' && args[1] === 'ENABLED'",
                             ["const target = controls[args[0] ?? ''];",
                              'const control = target ? this.forms[target[0]]?.get(target[1]) : undefined;',
                              "if (args[2] === 'PROPERTY_FALSE') {", '  control?.disable();', '} else {', '  control?.enable();', '}']))
        if self.w.get('commit'):
            branches.append(("operation === 'COMMIT_FORM'", ['this.save();']))
        branches.append(("operation === 'SHOW_ALERT' && answer", ["answer(window.confirm(args[1] ?? args[0] ?? '') ? '1' : '2');"]))
        lines += ["    let block = '';" if queries else '',
                  '    for (const command of (res?.commands ?? []) as string[][]) {',
                  '      const [operation, ...args] = command;']
        for n, (condition, body) in enumerate(branches):
            lines.append(('      if (' if n == 0 else '      } else if (') + condition + ') {')
            lines += ['        ' + line for line in body]
        lines += ['      }', '    }', '  }']
        methods = ['\n'.join(line for line in lines if line != '')]
        if queries:
            cases = []
            for block in queries:
                body = Body()
                body.lines = self.query(block, body)
                text = '\n'.join('    ' + line if line else '' for line in body.text().split('\n'))
                cases.append(f'      case {sq(block)}: {{\n' + text.replace('\n    ', '\n    ') + '\n        break;\n      }')
            methods.append('  // Forms EXECUTE_QUERY egy backend akció után: a blokk lekérdezése, mint a képernyő saját keresése.\n'
                           '  protected migrationQuery(block: string): void {\n    switch (block) {\n' + '\n'.join(cases)
                           + '\n    }\n  }')
        return methods

    def unique(self, base: str) -> str:
        result, n = base, 2
        while result in self.taken:
            result, n = base + str(n), n + 1
        self.taken.add(result)
        return result

    def table_name(self, block: str) -> str:
        return camel(block)

    # ---------------------------------------------------------------- values of the screen

    def owner_value(self, owner: str, body: Body) -> str | None:
        """A screen value by its Oracle name: a form control, or a column of a table's selected row."""
        if owner in self.controls:
            region, key, _ = self.controls[owner]
            return body.local(region) + member(key)
        block, item = owner.split('.', 1)
        field = self.api_fields.get(block, {}).get(item)
        if block in self.table_blocks and field:
            return f'this.{self.table_name(block)}Selection?.[{sq(field)}]'
        return None

    def key_value(self, block: str, key: str, body: Body) -> str | None:
        """A screen value by its control key (the criteria of a search, the parameters of a navigation)."""
        if (block, key) in self.by_key:
            return self.owner_value(self.by_key[(block, key)], body)
        items = {k: n for n, k in self.w.get('screen_keys', {}).get(block, {}).items()}
        return self.owner_value(block + '.' + items[key], body) if key in items else None

    def input_value(self, endpoint, owner, source, location, transport='string', *, automatic=True, inferred=False, timing='event'):
        from .input_bindings import slot, selected_expression, marked
        choice = next((s for s in self.input_sources if s['owner'] == source), None)
        default = (choice['text_expression'] if transport == 'string' else choice['expression']) if choice else 'null'
        row = slot(endpoint, owner, source, location, default, transport, automatic=automatic, inferred=inferred)
        row['timing'] = timing
        previous = self.input_rows.get(row['id'])
        if previous and previous['automatic']:
            row = previous
        expression = selected_expression(row, self.input_config.get(row['id']), self.input_sources)
        row['expression'] = expression
        self.input_rows[row['id']] = row
        return marked(row['id'], expression), expression == 'null'

    def input_inventory(self):
        # Endpoints without a generated caller are visible, but never claimed to be wired.
        api = self.w.get('api') or {}
        for owner, spec in self.api_actions.items():
            call = self.w['actions'][owner]
            for requirement in spec.get('server_inputs', []):
                self.input_value(call, owner, requirement['source'], 'server.' + requirement['variable'], automatic=False)
                self.input_warnings.append(call + ' / ' + requirement['source'] + ': a Java ' + requirement['variable']
                                           + ' lokális változója; java-variables.json vagy kézi ServiceImpl-bekötés szükséges.')
            for source in (spec.get('reads') or []) + (spec.get('parameters') or []):
                location = 'parameters.' + source if source in (spec.get('parameters') or []) else 'blocks.' + source
                self.input_value(call, owner, source, location, automatic=False, inferred=spec.get('bindings_inferred', False))
        for e in self.w.get('endpoints', []):
            if e['kind'] == 'search':
                spec = api['blocks'][e['target']]['operations']['search']
                for c in spec.get('criteria', []):
                    self.input_value(e['method'], e['target'], c['source'], 'criteria.' + c['field'],
                                     'value', automatic=False)
            elif e['kind'] == 'lov':
                for bind in api['lovs'][e['target']].get('binds', []):
                    self.input_value(e['method'], e['target'], bind['source'], 'parameters.' + bind['source'], automatic=False)
        if any(r['inferred'] for r in self.input_rows.values()):
            self.input_warnings.append('A kézi triggerek bemenetei statikus hivatkozásjelöltek: SQL INTO és IN/OUT irányok, dinamikus NAME_IN és üzleti hívások külön ellenőrzendők.')
        if any(r['source'].startswith(('GLOBAL.', 'SYSTEM.')) for r in self.input_rows.values()):
            self.input_warnings.append('A böngészőből érkező GLOBAL/modulparaméter nem hiteles felhasználó- vagy tenantazonosság. A jogosultságot a szerver ellenőrizze; SYSTEM érték kézi szerveroldali bekötés.')

    # ---------------------------------------------------------------- endpoints

    def endpoint_method(self, e: dict) -> str:
        self.rxjs.update({'EMPTY', 'catchError', 'tap'})
        url = 'this.url(' + sq(e['url']) + ')'
        if e['http'] == 'get':
            signature, call = f'offset = 0, limit = {QUERY_LIMIT}', f'this.http.get<any>({url}, {{ params: {{ offset, limit }} }})'
        elif e['http'] == 'delete':
            signature, call = 'body: unknown', f'this.http.delete<any>({url}, {{ body }})'
        else:
            signature, call = 'body: unknown', f"this.http.{e['http']}<any>({url}, body)"
        return (f"  {e['method']}({signature}) {{\n    return {call}.pipe(\n"
                f"      tap(res => WFF.debug(this.modName + {sq('.' + e['method'])}, res)),\n"
                "      catchError(error => {\n        WFF.err('Hiba', error);\n        return EMPTY;\n      }),\n    );\n  }")

    @staticmethod
    def request(call: str, entries: list[tuple[str, str]]) -> list[str]:
        """this.<call>({ ... }): on one line, or one entry per line when long."""
        line = f'this.{call}({{ ' + ', '.join(k + ': ' + v for k, v in entries) + ' })'
        if len(line) <= LINE:
            return [line]
        return [f'this.{call}({{'] + ['  ' + k + ': ' + v + ',' for k, v in entries] + ['})']

    def subscribe(self, request: list[str], block: str) -> list[str]:
        """The request of a query: its rows into the block's table, or the first row into the block's form."""
        show = []
        if self.envelope:
            show.append('// TODO: a válasz RestResponseDto-borítékban jön: a rows a boríték adatmezőjében van.')
        if block in self.table_blocks:
            show.append(f'this.{self.table_name(block)}Rows = res.rows ?? [];')
            show.append(f'this.migrationMoreRows({sq(block)}, res);')  # PageResult.hasMore: never a silent cut
            self.more_rows = True
        elif block in self.form_blocks:
            # The form shows one record (asked with limit 1): exactly that one, an empty form when none was found.
            show += ['const row = res.rows?.[0] ?? null;'] + self.show_record(block, 'row')
            show.append(f'this.migrationMoreRows({sq(block)}, res);')
            self.more_rows = True
        if not [line for line in show if not line.startswith('//')]:
            return [f'// TODO: a(z) {block} blokk nincs a képernyőn: az eredményt kézzel kell megjeleníteni.'] + request[:-1] + [request[-1] + '.subscribe();']
        return request[:-1] + [request[-1] + '.subscribe(res => {'] + ['  ' + line for line in show] + ['});']

    def show_record(self, block: str, row: str) -> list[str]:
        """The record (or null) into the block's form regions, as their controls hold it; a saved block remembers it.

        Like the Forms block after a query: every control gets the record's value (reset), nothing of the previous one
        stays. A checkbox holds true/false, a date field a Date (the record: the Forms value, local ISO text).
        """
        lines = [f'this.migrationLoaded[{sq(block)}] = {row};'] if block in self.saved_blocks else []
        for region in self.form_blocks[block]:
            controls = self.region_fields(block, region)
            mapped = any(key != field for key, field, _ in controls)  # then every control by name, else the record itself
            entries = []
            for key, field, item in controls:
                read = row + '?' + member(field)
                if item['widget'] == 'checkbox':
                    entries.append(f"{ts_key(key)}: {read} == null ? null : String({row}{member(field)}) === {sq(item.get('checked', ''))}")
                elif item['widget'] in {'date', 'datetime'}:
                    entries.append(f'{ts_key(key)}: {read} ? new Date({row}{member(field)}) : null')
                elif mapped:
                    entries.append(f'{ts_key(key)}: {read} ?? null')
            entries = entries if mapped else ['...' + row] + entries if entries else []
            line = f'this.forms[{sq(region)}]?.reset(' + ('{ ' + ', '.join(entries) + ' }' if entries else row + ' ?? {}') + ');'
            lines += [line] if len(line) <= LINE else [f'this.forms[{sq(region)}]?.reset({{', *('  ' + e + ',' for e in entries), '});']
        return lines

    def region_fields(self, block: str, region: str) -> list[tuple[str, str, dict]]:
        """(control key, DTO field, item) of the controls of a form region."""
        return [(key, self.api_fields.get(block, {}).get(owner.split('.', 1)[1], key), item)
                for owner, (r, key, item) in self.controls.items() if r == region]

    def page(self, block: str) -> int:
        """The rows a query of the block asks: a form the one record it shows, a table (or the developer) a page."""
        return 1 if block in self.form_blocks and block not in self.table_blocks else QUERY_LIMIT

    def query(self, block: str, body: Body) -> list[str]:
        """The search of a block (Forms EXECUTE_QUERY), its criteria from the screen."""
        q = self.queries[block]
        self.called.add(q['call'])
        if q['kind'] == 'list':
            page = self.page(block)
            return self.subscribe([f"this.{q['call']}(" + ('' if page == QUERY_LIMIT else f'0, {page}') + ')'], block)
        criteria, todo = [], []
        for c in q.get('criteria', []):
            source = c.get('source') or c.get('context') or self.by_key.get((c['block'], c['key']), c['block'] + '.' + c['key'])
            value, missing = self.input_value(q['call'], block, source, 'criteria.' + c['field'], 'value')
            criteria.append(f"{ts_key(c['field'])}: {value}")
            if missing:
                todo.append(source)
        head = ['// TODO: a feltétel értéke: ' + ', '.join(todo) + '.'] if todo else []
        entries = [('criteria', '{ ' + ', '.join(criteria) + ' }' if criteria else '{}'), ('offset', '0'), ('limit', str(self.page(block)))]
        return head + self.subscribe(self.request(q['call'], entries), block)

    def action(self, owner: str, body: Body, init: bool = False) -> list[str]:
        """The backend action of a button: the values its code reads, by their Oracle names."""
        call = self.w['actions'][owner]
        self.called.add(call)
        spec = self.api_actions.get(owner, {})
        reads = None if init else spec.get('reads')
        blocks, missing = {}, []
        for source in reads or []:
            value, absent = self.input_value(call, owner, source, 'blocks.' + source,
                                             inferred=spec.get('bindings_inferred', False))
            if absent:
                missing.append(source)
            block, item = source.split('.', 1)
            blocks.setdefault(block, []).append(ts_key(item) + ': ' + value)
        parameters = spec.get('parameters') or []
        head = []
        if reads is None and not init:
            head.append('// TODO: a kérés mezőértékei: blocks: { BLOKK: { MEZO: érték } }.')
        if missing:
            head.append('// TODO: nincs a képernyőn: ' + ', '.join(missing) + '.')
        parameter_values = []
        for source in parameters:
            value, absent = self.input_value(call, owner, source, 'parameters.' + source,
                                             inferred=spec.get('bindings_inferred', False), timing='constructor' if init else 'event')
            parameter_values.append(sq(source) + ': ' + value)
        if parameters or missing:
            head.append('// Bemeneti bekötések és kézi feladatok: analysis/input-bindings.json (Java asszisztens).')
        entries = [('blocks', '{ ' + ', '.join(ts_key(b) + ': { ' + ', '.join(v) + ' }' for b, v in blocks.items()) + ' }' if blocks else '{}'),
                   ('parameters', '{ ' + ', '.join(parameter_values) + ' }' if parameters else '{}')]
        target = (self.w.get('query_actions') or {}).get(owner)
        if target:
            entries += [('offset', '0'), ('limit', str(self.page(target)))]
            return head + self.subscribe(self.request(call, entries), target)
        # 4.35: the answer reaches the screen (applyActionResult): written items, messages, GO_BLOCK/EXECUTE_QUERY, alerts.
        self.result_handler = True
        if spec.get('alerts'):
            # SHOW_ALERT in two calls: the backend returns the alert, the screen asks, then calls again with the answer.
            line = 'const request = { ' + ', '.join(k + ': ' + v for k, v in entries) + ' };'
            lines = [line] if len(line) <= LINE else (['const request = {'] + ['  ' + k + ': ' + v + ',' for k, v in entries] + ['};'])
            return head + lines + [f'this.{call}(request).subscribe(res => this.applyActionResult(res, button =>',
                                   f"  this.{call}({{ ...request, parameters: {{ ...request.parameters, 'FRM.ALERTS': button }} }})"
                                   '.subscribe(next => this.applyActionResult(next))));']
        request = self.request(call, entries)
        return head + request[:-1] + [request[-1] + '.subscribe(res => this.applyActionResult(res));']

    # ---------------------------------------------------------------- buttons

    def steps(self, owner: str, steps: list, body: Body) -> list[str] | None:
        """A button's recognised Forms steps: its queries, its save, its windows; None when one has no plain equivalent."""
        block, lines = owner.split('.', 1)[0], []
        controls = self.plan.get('window_controls') or {}
        for step in steps:
            op = step['op']
            if op == 'goBlock':
                block = step['block'].upper()
            elif op in NAVIGATION_STEPS:
                continue
            elif op == 'executeQuery' and block in self.queries:
                lines += self.query(block, body)
            elif op == 'commit' and self.w.get('commit'):
                lines.append('this.save();')
            elif op in {'showWindow', 'hideWindow'} and step['window'].upper() in controls.get('windows', {}):
                lines.append(f"this.windowVisible[{sq(step['window'].upper())}] = {'true' if op == 'showWindow' else 'false'};")
            elif op in {'showCanvas', 'hideCanvas'} and step['canvas'].upper() in controls.get('canvases', {}):
                canvas = step['canvas'].upper()
                lines.append(f"this.canvasVisible[{sq(canvas)}] = {'true' if op == 'showCanvas' else 'false'};")
                surface = next((s for s in self.plan['surfaces'] if s['name'] == canvas), None)
                if op == 'showCanvas' and surface and surface['window'] in controls.get('content', {}) and surface['type'].lower() == 'content':
                    lines.append(f"this.activeContentCanvas[{sq(surface['window'])}] = {sq(canvas)};")
                if op == 'showCanvas' and surface and surface['window'] in controls.get('windows', {}):
                    lines.append(f"this.windowVisible[{sq(surface['window'])}] = true;")
            else:
                return None
        return lines

    def original(self, owner: str, todo: str, code: str | None = None) -> list[str]:
        source = (self.sources.get(owner, '') if code is None else code).strip()
        lines = ['// TODO: ' + todo]
        if source:
            lines += ['//#region Eredeti Forms-kód'] + ['//' + (' ' + l if l else '') for l in source.split('\n')] + ['//#endregion']
        return lines

    def button(self, owner: str) -> None:
        body, note = Body(), ''
        action = next((a for a in self.plan['actions'] if a['owner'] == owner), {})
        called = set(self.called)
        prior_inputs = dict(self.input_rows)
        recognised = self.steps(owner, action['steps'], body) if action.get('steps') else None
        if not recognised or owner in self.navigations or owner in self.manual_navs:
            self.input_rows = prior_inputs
            body, self.called = Body(), called  # a partly recognised button: nothing of it
        if owner in self.navigations:
            n = self.navigations[owner]
            params = [ts_key(p['name']) + ': ' + ((self.key_value(p['block'], p['key'], body) or 'null') if 'block' in p else sq(p['value']))
                      for p in n['params']]
            body.lines = [f"void this.router.navigate([{sq(n['route'])}]" + (', { queryParams: { ' + ', '.join(params) + ' } }' if params else '') + ');']
            note = f"Forms {n['call']}('{n['form']}'): navigáció."
        elif owner in self.manual_navs:
            body.lines = self.original(owner, 'összetett formhívás: a célt és a paramétereket kézzel kell megírni, például '
                                              "void this.router.navigate(['/<útvonal>'], { queryParams: { ... } });",
                                       self.manual_navs[owner]['code'])
        elif recognised:
            body.lines = recognised
        elif owner in self.w.get('actions', {}):
            body.lines = self.action(owner, body)
            note = 'a gomb kódja a backendben fut.'
        else:
            body.lines = self.original(owner, 'a gomb kódját kézzel kell átültetni.')
        self.methods.append(f'  // {owner}' + (': ' + note if note else '') + '\n'
                            + f'  protected {self.click[owner]}(): void {{\n' + body.text() + '\n  }')

    # ---------------------------------------------------------------- save (Forms COMMIT_FORM)

    def save(self) -> None:
        commit = self.w.get('commit')
        if not commit:
            return
        call = commit['call']
        self.called.add(call)
        self.result_handler = True  # the answer's messages, commands and written items: applyActionResult
        body, blocks, parameters, missing = Body(), {}, [], []
        # What commitForm reads from the screen (a new detail record's master key, PRE-COMMIT): like a button's values.
        for source in commit.get('reads', []):
            value, absent = self.input_value(call, '@FORM', source, 'blocks.' + source)
            missing += [source] if absent else []
            block, item = source.split('.', 1)
            blocks.setdefault(block, []).append(ts_key(item) + ': ' + value)
        for source in commit.get('parameters', []):
            value, absent = self.input_value(call, '@FORM', source, 'parameters.' + source)
            missing += [source] if absent else []
            parameters.append(sq(source) + ': ' + value)
        entries = [('blocks', '{ ' + ', '.join(ts_key(b) + ': { ' + ', '.join(v) + ' }' for b, v in blocks.items()) + ' }')] if blocks else []
        entries += [('parameters', '{ ' + ', '.join(parameters) + ' }')] if parameters else []
        entries += [(ts_key(spec['request']), self.changes(block, body)) for block, spec in commit['blocks'].items()]
        head = ['// TODO: nincs a képernyőn: ' + ', '.join(missing) + '.'] if missing else []
        if parameters or missing:
            head.append('// Bemeneti bekötések és kézi feladatok: analysis/input-bindings.json (Java asszisztens).')
        if self.envelope:
            head.append('// TODO: a válasz RestResponseDto-borítékban jön: a mentett rekordok a boríték adatmezőjében vannak.')
        shown = []  # the saved records (re-read, with their generated keys): the screen shows them, a next save updates them
        for block, spec in commit['blocks'].items():
            rows = 'res' + member(spec['result'])
            shown += [f'if ({rows}?.length) {{', f'  const row = {rows}[0];', *('  ' + l for l in self.show_record(block, 'row')), '}']
        request = self.request(call, entries)
        body.lines = head + request[:-1] + [request[-1] + '.subscribe(res => {'] + ['  ' + l for l in shown + ['this.applyActionResult(res);']] + ['});']
        self.methods.append('  // Mentés (Forms COMMIT_FORM): a változások egy kérésben; a backend egy tranzakcióban, Forms-sorrendben ment.\n'
                            '  protected save(): void {\n' + body.text() + '\n  }')

    def changes(self, block: str, body: Body) -> str:
        """The changes of a saved block: its record from the regions' values (by DTO field), measured by migrationChanges."""
        parts, entered = [], []
        for region in self.form_blocks.get(block, []):
            local = body.local(region)
            entered.append(f'this.forms[{sq(region)}]?.dirty')
            controls = self.region_fields(block, region)
            mapped = any(key != field for key, field, _ in controls)
            parts += [] if mapped else ['...' + local]
            for key, field, item in controls:
                read = local + member(key)
                if item['widget'] == 'checkbox':  # true/false on the screen, the Forms value in the record
                    parts.append(f"{ts_key(field)}: {read} == null ? null : {read} ? {sq(item.get('checked', ''))} : {sq(item.get('unchecked', ''))}")
                elif mapped:
                    parts.append(f'{ts_key(field)}: {read}')
        values = parts[0][3:] if len(parts) == 1 and parts[0].startswith('...') else '{ ' + ', '.join(parts) + ' }'
        return f"this.migrationChanges({sq(block)}, {values}, {' || '.join(entered) or 'false'})"

    # ---------------------------------------------------------------- assembly

    def build(self) -> None:
        for owner in self.click:
            self.button(owner)
        self.save()
        self.input_inventory()
        row_actions = [o for s in self.tables for o in (i['owner'] for i in s['items'] if i['widget'] == 'button')]
        if row_actions:
            cases = [f'      case {sq(o)}: this.{self.click[o]}(); break;' for o in row_actions]
            self.methods.append('  // Egy táblázatsor gombja (a sor már ki van választva).\n  protected onRowAction(ownId: string): void {\n'
                                '    switch (ownId) {\n' + '\n'.join(cases) + '\n    }\n  }')

    def class_fields(self, tab_fields: list, labels: dict, toast_symbol: str, form_type: str, structures: dict) -> list[str]:
        """The component's fields in their order (toast, http, labels, structures, tables, windows)."""
        life = self.config['toast_life_ms']
        self.core.add('inject')
        # The company toast of every component (the developer's messages); the frame itself does not call it.
        fields = ['  protected readonly toast = inject(' + toast_symbol + ');\n'
                  f"  protected readonly toastLife = {{ success: {life['success']}, warning: {life['warning']}, danger: {life['danger']} }};"]
        if self.w.get('endpoints'):
            fields[-1] += '\n  private readonly http = inject(HttpClient);'
        if labels:
            fields.append('  protected readonly labels = ' + record(labels) + ';')
        if self.input_rows:
            self.core.add('Input')
            fields.append('  // A szülő komponens adja át: [migrationParameters]="{ kod: value }". Nem jogosultsági kontextus.\n'
                          '  @Input() migrationParameters: Readonly<Record<string, string | null>> = {};')
        fields.extend(tab_fields)
        if self.forms:
            self.angular_forms.add('FormGroup')
            from .ts_code import nested
            fields.append('  // A FormBlock-régiók FormGroupjai (onFormGroupGenerated).\n  protected readonly forms: Record<string, FormGroup> = {};')
            fields.append('  protected readonly structures: Record<string, ' + form_type + '[]> = ' + nested(structures) + ';')
        if self.saved_blocks:
            fields.append('  // A mentés (save) blokkonként ehhez a lekérdezett rekordhoz méri a képernyőt; ha nincs, új rekordot ment.\n'
                          '  protected readonly migrationLoaded: Record<string, Record<string, unknown> | null> = {};')
        for section in self.tables:
            t = self.table_name(section['block'])
            columns = [i for i in section['items'] if i['widget'] not in {'button', 'image', 'unsupported', 'tree'} and not i.get('spacer')]
            weights = [max(1, i['width']) for i in columns]
            total = sum(weights) or 1
            cols = [{'field': self.api_fields.get(section['block'], {}).get(i['owner'].split('.', 1)[1], i['key']), 'header': i['label'],
                     'width': str(round(w / total * 100, 2)) + '%'} for i, w in zip(columns, weights)]
            fields.append(f"  // {section['block']}: a táblázat oszlopai, sorai (a backend DTO-mezőivel) és a kiválasztott sor.\n"
                          f'  protected readonly {t}Columns = [\n' + ''.join('    ' + tsv(c) + ',\n' for c in cols) + '  ];\n'
                          f'  protected {t}Rows: Record<string, unknown>[] = [];\n'
                          f'  protected {t}Selection: Record<string, unknown> | null = null;')
            actions = [{'ownId': i['owner'], 'label': i['label'], **({} if i['enabled'] else {'disabled': True})}
                       for i in section['items'] if i['widget'] == 'button']
            if actions:
                fields.append(f'  protected readonly {t}Actions = ' + tsv(actions) + ';')
        controls = self.plan.get('window_controls') or {}
        if controls.get('windows'):
            fields.append('  // Az ablakok, canvasok láthatósága (Forms SHOW_WINDOW / SHOW_VIEW).\n'
                          '  protected readonly windowVisible: Record<string, boolean> = ' + record(controls['windows']) + ';')
        if controls.get('content'):
            fields.append('  protected readonly activeContentCanvas: Record<string, string> = ' + record(controls['content']) + ';')
        if controls.get('canvases'):
            fields.append('  protected readonly canvasVisible: Record<string, boolean> = ' + record(controls['canvases']) + ';')
        return fields

    def more_rows_methods(self) -> list[str]:
        """migrationMoreRows: a query that left rows out (PageResult.hasMore) says so instead of a silent cut."""
        if not getattr(self, 'more_rows', False):
            return []
        return [f'''  // A lekérdezés legfeljebb annyi rekordot kér, amennyit a képernyő mutat (táblázat: {QUERY_LIMIT}, űrlap: 1); ha több
  // van, szól (a Forms görgetéssel hozná a továbbiakat).
  protected migrationMoreRows(block: string, res: {{ hasMore?: boolean }} | null): void {{
    if (res?.hasMore) {{
      this.toast.warning('Lekérdezés', block + ': több rekord van, mint amennyi megjelent; szűkítsd a feltételt.', false, this.toastLife.warning);
    }}
  }}''']

    def changes_methods(self) -> list[str]:
        """migrationChanges: the change list of one saved block (save) from its record on the screen."""
        if not self.saved_blocks:
            return []
        return ['''  // Mentés: egy blokk rekordja a képernyőn, a lekérdezetthez mérve. Lekérdezett rekord nélkül új rekord, ha a
  // felhasználó beírt valamit; különben módosítás, ha eltér. Törlést a képernyő nem küld. Az üres szöveg null, mint
  // az Oracle-ben; a dátum helyi idő, másodpercre, ahogy a DTO olvassa (yyyy-MM-ddTHH:mm:ss, eltolás nélkül).
  protected migrationChanges(block: string, values: Record<string, unknown>, entered?: boolean) {
    const pad = (part: number) => String(part).padStart(2, '0');
    const original = this.migrationLoaded[block] ?? null;
    const value: Record<string, unknown> = { ...original };
    for (const [field, raw] of Object.entries(values)) {
      if (raw instanceof Date && Number.isNaN(raw.getTime())) { throw new Error('Érvénytelen dátum'); }
      value[field] = raw instanceof Date
        ? String(raw.getFullYear()).padStart(4, '0') + '-' + pad(raw.getMonth() + 1) + '-' + pad(raw.getDate())
          + 'T' + pad(raw.getHours()) + ':' + pad(raw.getMinutes()) + ':' + pad(raw.getSeconds())
        : raw === '' || raw === undefined ? null : raw;
    }
    if (!original) {
      return { inserted: entered && Object.keys(values).some(field => value[field] !== null) ? [value] : [], updated: [], deleted: [] };
    }
    const changed = Object.keys(values).some(field => String(value[field] ?? '') !== String(original[field] ?? ''));
    return { inserted: [], updated: changed ? [{ original, value }] : [], deleted: [] };
  }''']

    @staticmethod
    def input_helper() -> str:
        return """  // Forms szöveges kérésérték: a dátum helyi idő, nincs UTC-eltolás.
  protected migrationInputText(value: unknown, checked?: string, unchecked?: string): string | null {
    if (value === null || value === undefined) { return null; }
    if (typeof value === 'boolean' && checked !== undefined) { return value ? checked : unchecked ?? null; }
    if (value instanceof Date) {
      if (Number.isNaN(value.getTime())) { throw new Error('Érvénytelen dátum'); }
      const pad = (part: number) => String(part).padStart(2, '0');
      return String(value.getFullYear()).padStart(4, '0') + '-' + pad(value.getMonth() + 1) + '-' + pad(value.getDate())
        + 'T' + pad(value.getHours()) + ':' + pad(value.getMinutes()) + ':' + pad(value.getSeconds())
        + '.' + String(value.getMilliseconds()).padStart(3, '0');
    }
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') { return String(value); }
    throw new Error('A bemenethez skalár érték szükséges');
  }"""

    def form_group_method(self) -> str | None:
        if not self.forms:
            return None
        return ('  // A FormBlock elkészítette a régió FormGroupját.\n'
                '  protected onFormGroupGenerated(region: string, group: FormGroup): void {\n    this.forms[region] = group;\n  }')

    def constructor(self) -> str:
        lines = ['    super();']
        init = (self.w.get('api') or {}).get('init')
        if init and init in self.w.get('actions', {}):
            lines.append('    // Indításkor (Forms WHEN-NEW-FORM-INSTANCE): ' + init + '.')
            lines += ['    ' + l for l in self.action(init, Body(), init=True)]
            self.input_warnings.append('Az indítási kérés a konstruktorban fut: Angular input és FormGroup még nem áll rendelkezésre. Az indítás időzítése és adatátadása kézi feladat.')
        return '  constructor() {\n' + '\n'.join(lines) + '\n  }'

"""Explicit review emitter. Invalid UI models never enter the strict UI renderer."""
import json
from collections import Counter
from .common import jstr
from .generate import write
from .action_scaffold import write_frontend_actions


def generate(model, output, actions):
    module = model['module']; rendering = model['rendering']
    root = output / 'frontend' / module['key']; plans = []
    for block in model['blocks']:
        by_key = {i['key']: i for i in block['items']}
        for region in block['regions']:
            plan = {'id': region['id'], 'block': block['name'], 'canvas': region['canvas'],
                    'tab': region['tab_page'], 'mode': block['mode'], 'structures': [], 'placeholders': []}
            for key in region['items']:
                item = by_key[key]; widget = item['widget']; mapping = rendering['widget_map']['widgets'][widget]
                label_key = item['text_keys']['prompt'] or item['text_keys']['label']
                label = model['i18n'].get(label_key, item['name'])
                if widget in {'unsupported', 'image', 'tree', 'button'}:
                    plan['placeholders'].append({'owner': item['owner'], 'label': label, 'widget': widget,
                        'reason': 'Ellenőrzött host adapter szükséges. A gombeseményeket az akciólista tartalmazza.'})
                    continue
                typ = mapping['exact_decimal_type'] if widget == 'number' and item['representation'] == 'decimal-string' else mapping['type']
                structure = {**mapping.get('props', {}), 'type': typ, 'ownId': item['owner'], 'labelText': label,
                    'formControlName': item['key'], 'startValue': None, 'disabled': True, 'readonly': True,
                    'col': str(max(1, min(rendering['layout_columns'], item['layout']['col']))), 'invisible': not item['visible']}
                if widget in {'select', 'radio', 'multiselect', 'autocomplete'}:
                    structure.update({'optionLabel': 'label', 'optionValue': 'value', 'options': []})
                    if widget == 'autocomplete': structure['suggestions'] = []
                plan['structures'].append(structure)
            plans.append(plan)
    type_import = ('import { FormBlock } from ' + jstr(rendering['form_block_type_import_path']) + ';\n') if rendering['emit_imports'] else '// TODO: import the configured FormBlock structure type from your host package.\n'
    write(root / 'review-structure.ts', type_import + '''// ALWAYS_REGENERATE. Disabled review fields; no defaults, validation, LOV or business execution.
export interface ReviewRegion {
  id: string; block: string; canvas: string; tab: string; mode: string;
  structures: ''' + rendering['form_block_structure_type'] + '''[];
  placeholders: {owner: string; label: string; widget: string; reason: string}[];
}
export const REVIEW_REGIONS: ReviewRegion[] = ''' + json.dumps(plans, ensure_ascii=True, indent=2) + ';\n')
    host_import = ('import { ' + rendering['optimus_form_block_symbol'] + ' } from ' + jstr(rendering['optimus_import_path']) + ';') if rendering['emit_imports'] else '// TODO: import the configured FormBlock component and add it to Component.imports.'
    title = model['i18n'].get(module['title_key'], module['key'])
    write(root / 'component.ts', f'''// CREATE_ONCE: --regenerate preserves this review shell.
import {{ Component, Input }} from '@angular/core';
import {{ REVIEW_REGIONS }} from './review-structure';
import {{ FORM_ACTION_ENDPOINTS, FormActionEndpoint }} from './action-endpoints';
{host_import}

@Component({{
  selector: {jstr(module['selector'])}, standalone: true,
  imports: [{rendering['optimus_form_block_symbol'] if rendering['emit_imports'] else ''}],
  templateUrl: './component.html', styleUrl: './component.scss',
}})
export class {module['class_name']} {{
  readonly title = {jstr(title)};
  readonly regions = REVIEW_REGIONS;
  readonly endpoints = FORM_ACTION_ENDPOINTS;
  @Input() action: ((endpoint: FormActionEndpoint) => void) | null = null;
  run(endpoint: FormActionEndpoint): void {{ if (this.action) this.action(endpoint); }}
}}
''')
    selector = rendering['html_selectors']['form_block']
    write(root / 'component.html', '''<!-- CREATE_ONCE: review layout; no Forms runtime or automatic requests. -->
<main>
  <h1>{{ title }}</h1>
  <p class="review-note">Migrációs váz: a mezők tiltva vannak. A megjelenítést és az üzleti működést ellenőrizni kell.</p>
  @for (region of regions; track region.id) {
    <section>
      <h2>{{ region.block }} · {{ region.canvas || 'Nincs canvas' }} @if (region.tab) { · {{ region.tab }} }</h2>
      @if (region.mode === 'table') { <p>Több rekordos blokk: itt egy rekord mezőterve látható; táblázatadapter szükséges.</p> }
      <''' + selector + ''' [formStructure]="region.structures" />
      @for (item of region.placeholders; track item.owner) {
        <p class="placeholder">{{ item.label }} ({{ item.owner }}, {{ item.widget }}) — {{ item.reason }}</p>
      }
    </section>
  }
  <section>
    <h2>Gombesemények</h2>
    <p>A gombok csak bekötött host adapterrel aktívak. A Java végpontvázak alapállapotban HTTP 501 választ adnak.</p>
    @for (endpoint of endpoints; track endpoint.key) {
      <button type="button" [disabled]="!action" (click)="run(endpoint)">{{ endpoint.owner }}</button>
    }
  </section>
</main>
''')
    write(root / 'component.scss', '/* CREATE_ONCE */\n:host { display: block; } section { margin: 1.5rem 0; } .review-note, .placeholder { padding: .75rem; background: #fff4d5; } button { margin: .25rem; }\n')
    write_frontend_actions(actions, root)
    write(output / 'SCAFFOLD_REVIEW.md', '''# Migrációs váz — ellenőrzéshez

A --scaffold mód részleges Angular/Java vázat készít az ismert objektumokból. Az összes eredeti hiba megmarad az analysis/issues.json és analysis/ui-model.json fájlban; nem minősül megoldottnak.

- A mezők tiltottak, readonly értékűek, kezdeti értékük null. Nincs automatikus validáció, LOV-betöltés, alapérték vagy adatlekérés. Az ismeretlen widget helyén jelzett feladat marad.
- A blokk/canvas/tab szerinti elrendezés mezőterv, nem pixelpontos Forms képernyő. Több rekordos blokknál egy rekord terve készül; táblázatadapter szükséges.
- A formStructure inputot fogadó céges FormBlock komponenst és a típust a hostból kell importálni. Automatikus import: emit_imports=true és a dokumentált importbeállítások.
- Az action input kizárólag deklarációt ad át a hostnak. Nincs automatikus HTTP-hívás. Az endpointok: analysis/action-plan.json és frontend/*/action-endpoints.ts.
- Minden adatbázis-művelet tiltott a SCAFFOLD_REVIEW_REQUIRED miatt. A Java gombvégpontok jogosultságvizsgálat után HTTP 501 választ adnak.
- A DPS *FormActionsServiceBase.java tartalmazza a kommentelt PL/SQL-t. A *FormActionsService.java CREATE_ONCE osztályban írd felül a protected *Reviewed metódusokat. A public final belépési pont megőrzi a MigrationAccess ellenőrzését.
- Ellenőrizd a paraméterek típusát/irányát, a szerveroldali user/tenant kontextust, a tranzakciót és a Forms eseménysorrendet. Helyi FMB program unit nem hívható automatikusan JDBC-eljárásként.
- A WBS adapter külön konfigurációból használja a host RestTemplateBuilder példányát, így a host hitelesítése/időkorlátai beköthetők. A WBS és DPS külön host projektekbe tartozik.
- --regenerate megőrzi a CREATE_ONCE fájlokat. A strict és scaffold mód közötti váltáshoz új célmappa szükséges.

A kereshető offline modultérkép: analysis/discovery/form-explorer.html. A nyers SQL-források ugyanitt a sources/ könyvtárban vannak. Az elemzési nézetben feloldott sortörések nem módosítják az eredeti forrást, és nem kerülnek a végrehajtó szabályfordítóba.
''')
    return root


def append_review_report(model, output):
    counts = Counter(i['code'] for i in model['issues'] if i['severity'] == 'error')
    summary = '\n\n## Migrációs váz\n\nRészleges, ellenőrzendő kimenet. Minden adatbázis-művelet tiltott; a frontend mezői inaktívak.\n\n'
    summary += '\n'.join('- ' + k + ': ' + str(v) for k, v in sorted(counts.items()))
    summary += '\n\nÚtmutató: SCAFFOLD_REVIEW.md. Modultérkép: analysis/discovery/form-explorer.html. Gombterv: analysis/action-plan.json.\n'
    report = output / 'migration-report.md'
    report.write_text(report.read_text(encoding='utf-8') + summary, encoding='utf-8')
    integration = output / 'INTEGRATION.md'
    legacy = integration.read_text(encoding='utf-8').split('\n---\n', 1)[-1]
    write(integration, '# Migrációs váz beépítése\n\nElsőként a SCAFFOLD_REVIEW.md útmutatót kövesd. A strict frontend szerződése nem alkalmazható erre a részleges mezőtervre.\n\n---\n' + legacy)

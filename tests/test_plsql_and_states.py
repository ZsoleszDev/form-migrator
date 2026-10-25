"""Forms PL/SQL run as written in Oracle, and item states (SET_ITEM_PROPERTY) in the screen."""
import java_support  # noqa: F401  (legacy trigger mode for these engine tests)
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from frm_forms.cli import main
from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import Rewriter, assigned_vars, prepare

V = Rewriter.var  # fixed variable name per item

PLSQL_FORM = '<Module><FormModule Name="PLSQL_TESZT" Title="Átfuttatás"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>\n <Block Name="B" DatabaseDataBlock="true" QueryDataSourceName="UGYFEL" RecordsDisplayCount="1">\n  <Item Name="ID" ItemType="Text Item" DataType="Number" PrimaryKey="true" Prompt="Azonosító" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>\n  <Item Name="KOD" ItemType="Text Item" DataType="Char" MaximumLength="10" Prompt="Kód" CanvasName="C" XPosition="100" YPosition="10" Width="80" Height="20">\n   <Trigger Name="WHEN-VALIDATE-ITEM" TriggerText="IF :B.KOD IS NULL THEN&amp;#10;  message(&apos;A kód kötelező.&apos;);&amp;#10;  RAISE FORM_TRIGGER_FAILURE;&amp;#10;END IF;"/></Item>\n  <Item Name="NEV" ItemType="Display Item" DataType="Char" MaximumLength="80" Prompt="Megnevezés" DatabaseItem="false" CanvasName="C" XPosition="190" YPosition="10" Width="200" Height="20"/>\n  <Item Name="MODOSITVA" ItemType="Text Item" DataType="Date" ColumnName="MODOSITVA" Prompt="Módosítva" CanvasName="C" XPosition="10" YPosition="40" Width="100" Height="20"/>\n  <Trigger Name="POST-QUERY" TriggerText="BEGIN&amp;#10;  SELECT nev INTO :B.NEV FROM kodtar WHERE kod = :B.KOD;&amp;#10;EXCEPTION&amp;#10;  WHEN NO_DATA_FOUND THEN :B.NEV := NULL;&amp;#10;  WHEN OTHERS THEN cgte$other_exceptions;&amp;#10;END;"/>\n  <Trigger Name="PRE-INSERT" TriggerText="SELECT ugyfel_seq.NEXTVAL INTO :B.ID FROM dual;&amp;#10;:B.MODOSITVA := SYSDATE;"/>\n  <Trigger Name="POST-INSERT" TriggerText="naploz(&apos;INSERT&apos;);"/>\n </Block>\n <Block Name="GOMBOK" DatabaseDataBlock="false">\n  <Item Name="PB_LEZAR" ItemType="Push Button" Label="Lezárás" CanvasName="C" XPosition="300" YPosition="40" Width="80" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="UPDATE ugyfel SET statusz = &apos;L&apos; WHERE id = :B.ID;&amp;#10;SELECT COUNT(*) INTO :GOMBOK.DB FROM ugyfel WHERE statusz = &apos;L&apos;;&amp;#10;COMMIT;&amp;#10;message(&apos;Lezárva.&apos;);"/></Item>\n  <Item Name="DB" ItemType="Display Item" DataType="Number" Prompt="Lezárt" CanvasName="C" XPosition="390" YPosition="40" Width="50" Height="20"/>\n  <Item Name="PB_UJ" ItemType="Push Button" Label="Új" CanvasName="C" XPosition="450" YPosition="40" Width="60" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="go_block(&apos;B&apos;); create_record;"/></Item>\n </Block>\n <ProgramUnit Name="NAPLOZ" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE naploz(p_mit VARCHAR2) IS&amp;#10;BEGIN&amp;#10;  INSERT INTO naplo(ugyfel_id, mit) VALUES (:B.ID, p_mit);&amp;#10;END;"/>\n <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Ügyfél"/>\n</FormModule></Module>\n'
STATES_FORM = '<Module><FormModule Name="ALLAPOT" Title="Állapotok"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>\n <Trigger Name="WHEN-NEW-FORM-INSTANCE" TriggerText="qms$event_form(&apos;WHEN-NEW-FORM-INSTANCE&apos;);&amp;#10;set_item_property(&apos;B.PB_MENT&apos;, ENABLED, PROPERTY_FALSE);"/>\n <Block Name="B" DatabaseDataBlock="false">\n  <Item Name="CB" ItemType="Check Box" Prompt="Megjegyzéssel" CheckedValue="I" UncheckedValue="N" CanvasName="C" XPosition="10" YPosition="10" Width="120" Height="20">\n   <Trigger Name="WHEN-CHECKBOX-CHANGED" TriggerText="IF :B.CB = &apos;I&apos; THEN&amp;#10;  set_item_property(&apos;B.MEGJ&apos;, ENABLED, PROPERTY_TRUE);&amp;#10;  set_item_property(&apos;MEGJ&apos;, REQUIRED, PROPERTY_TRUE);&amp;#10;ELSE&amp;#10;  :B.MEGJ := NULL;&amp;#10;  set_item_property(&apos;B.MEGJ&apos;, ENABLED, PROPERTY_FALSE);&amp;#10;END IF;"/></Item>\n  <Item Name="MEGJ" ItemType="Text Item" DataType="Char" MaximumLength="200" Prompt="Megjegyzés" Enabled="false" CanvasName="C" XPosition="140" YPosition="10" Width="250" Height="20"/>\n  <Item Name="TIPUS" ItemType="List Item" ListStyle="Poplist" Prompt="Típus" CanvasName="C" XPosition="10" YPosition="40" Width="120" Height="20">\n   <ListItemElement Label="Alap" Value="A"/><ListItemElement Label="Extra" Value="X"/>\n   <Trigger Name="WHEN-LIST-CHANGED" TriggerText="IF NVL(:B.TIPUS, &apos;A&apos;) = &apos;X&apos; THEN set_item_property(&apos;B.EXTRA&apos;, VISIBLE, PROPERTY_TRUE); ELSE set_item_property(&apos;B.EXTRA&apos;, VISIBLE, PROPERTY_FALSE); END IF;"/></Item>\n  <Item Name="EXTRA" ItemType="Text Item" DataType="Char" MaximumLength="30" Prompt="Extra adat" CanvasName="C" XPosition="140" YPosition="40" Width="250" Height="20"/>\n  <Item Name="KOD" ItemType="Text Item" DataType="Char" MaximumLength="10" Prompt="Kód" CanvasName="C" XPosition="10" YPosition="70" Width="120" Height="20">\n   <Trigger Name="WHEN-VALIDATE-ITEM" TriggerText="SELECT COUNT(*) INTO :B.EXTRA FROM t WHERE k = :B.KOD;&amp;#10;set_item_property(&apos;B.EXTRA&apos;, ENABLED, PROPERTY_FALSE);"/></Item>\n  <Item Name="PB_ENGED" ItemType="Push Button" Label="Engedélyezés" CanvasName="C" XPosition="140" YPosition="70" Width="100" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="set_item_property(&apos;B.PB_MENT&apos;, ENABLED, PROPERTY_TRUE);"/></Item>\n  <Item Name="PB_MENT" ItemType="Push Button" Label="Mentés" CanvasName="C" XPosition="250" YPosition="70" Width="100" Height="22"/>\n </Block>\n <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Állapotok"/>\n</FormModule></Module>\n'
ITEMS = {'B': {'ID': {'type': 'number'}, 'KOD': {'type': 'text'}, 'NEV': {'type': 'text'}}, 'CTRL': {'X': {'type': 'text'}}}
PROCS = {'KOD_PKG.CHECK': {'kind': 'procedure', 'arguments': [{'name': 'P_KOD', 'mode': 'IN', 'type': 'text'},
                                                              {'name': 'P_NEV', 'mode': 'OUT', 'type': 'text'}]}}


class PassthroughTests(unittest.TestCase):
    def prepared(self, source, **options):
        options.setdefault('block', 'B')
        return prepare(source, items=ITEMS, units=options.pop('units', {}), prefixes=('qms$', 'cgte$'), procedures=PROCS, **options)

    def test_binds_messages_failure_and_framework_handlers(self):
        r = self.prepared("BEGIN SELECT nev INTO :B.NEV FROM t WHERE kod = :kod; IF :B.NEV IS NULL THEN "
                          "message('Nincs: '':x'' ?'); RAISE FORM_TRIGGER_FAILURE; END IF; "
                          "EXCEPTION WHEN OTHERS THEN cgte$other_exceptions; END;")
        self.assertEqual([b['source'] for b in r['binds']], ['B.NEV', 'B.KOD'])
        self.assertIn(f"SELECT nev INTO {V('B.NEV')} FROM t WHERE kod = {V('B.KOD')}", r['sql'])
        self.assertIn("frm_msg('Nincs: '':x'' ?')", r['sql'])  # literals untouched
        self.assertIn('FORM_TRIGGER_FAILURE EXCEPTION;', r['sql'])
        self.assertIn('RAISE_APPLICATION_ERROR(-20999', r['sql'])
        self.assertIn('WHEN OTHERS THEN RAISE;', r['sql'])
        self.assertEqual(r['sql'].count('?') - 1, 2 + 2 + 1)  # 2 IN, 2 OUT, messages (+1 inside the literal)

    def test_refusals_name_the_reason(self):
        cases = {"go_block('CTRL');": 'Forms beépített hívás: GO_BLOCK', ':CTRL.X := :B.NEV;': 'nincs helye a migrált felületen',
                 ':SYSTEM.MESSAGE_LEVEL := 25;': 'nincs helye a migrált felületen',
                 'kod_pkg.check(:B.KOD);': 'argumentumok száma',
                 "kod_pkg.check(:B.KOD, 'x');": 'OUT paraméter', 'ROLLBACK;': 'Tranzakcióvezérlés'}
        for source, reason in cases.items():
            with self.subTest(source=source), self.assertRaisesRegex(Unsupported, reason):
                self.prepared(source)

    def test_data_without_a_source_becomes_a_developer_input(self):
        # 4.22: what the migrated code cannot get anywhere is a variable of the Java method, the developer fills it
        r = self.prepared("IF :SYSTEM.RECORD_STATUS = 'NEW' THEN :B.NEV := :CTRL.X || :GLOBAL.FELH || :NINCS.ILYEN; END IF;")
        self.assertEqual([(i['source'], i['variable'], i['type']) for i in r['inputs']],
                         [('SYSTEM.RECORD_STATUS', 'systemRecordStatus', 'text'), ('CTRL.X', 'ctrlX', 'text'),
                          ('GLOBAL.FELH', 'globalFelh', 'text'), ('NINCS.ILYEN', 'nincsIlyen', 'text')])
        self.assertEqual({i['source']: i['reason'] for i in r['inputs']}['CTRL.X'], 'másik blokk mezője, a rekordban nem érhető el')
        self.assertEqual([b['source'] for b in r['outs']], ['B.NEV'])  # an input is never written back
        self.assertIn(f"{V('CTRL.X')} VARCHAR2(32767) := ?; -- CTRL.X", r['sql'])  # bound like any value
        from frm_forms.plsql_passthrough import input_declarations
        self.assertIn('        // TODO: :CTRL.X (másik blokk mezője, a rekordban nem érhető el): add át ennek a változónak a '
                      'megfelelő értéket.\n        String ctrlX = null;\n', input_declarations(r, '        '))

    def test_unknown_routines_run_and_protected_items_are_checked_at_run_time(self):
        # Forms resolved the name in the database (or an attached library): Oracle decides when it runs.
        r = self.prepared('other_pkg.run(:B.ID, :B.NEV);', writable=lambda b: b['source'] != 'B.ID')
        self.assertEqual((r['unresolved'], r['guarded']), (['OTHER_PKG.RUN'], ['B.ID']))
        self.assertIn(f"{V('B.ID')}_o := {V('B.ID')};", r['sql'])
        self.assertIn('RAISE_APPLICATION_ERROR(-20998', r['sql'])

    def test_writes_are_detected_and_guarded(self):
        a, b, c, d = (V(x) for x in ('B.A', 'B.B', 'B.C', 'B.D'))
        self.assertEqual(assigned_vars(f'SELECT pkg.f({a}) INTO {b} FROM dual; INSERT INTO t VALUES ({c}); {d} := 1;'), {b, d})
        read_only = lambda b: b['source'] != 'B.KOD'
        with self.assertRaisesRegex(Unsupported, 'nem visszaírható mezőt ír: B.KOD'):
            self.prepared('kod_pkg.check(:B.NEV, :B.KOD);', writable=read_only)  # OUT of a known procedure
        r = self.prepared('SELECT nev INTO :B.NEV FROM t WHERE kod = :B.KOD;', writable=read_only)
        self.assertEqual([b['source'] for b in r['outs']], ['B.NEV'])

    def test_local_units_are_nested_and_commit_is_left_to_the_endpoint(self):
        units = {'NAPLOZ': {'kind': 'procedure', 'text': 'PROCEDURE naploz IS BEGIN INSERT INTO naplo VALUES (:B.ID); END;'}}
        r = self.prepared('naploz; COMMIT;', units=units, transaction=True)
        self.assertEqual(r['units'], ['NAPLOZ'])
        self.assertIn(f"PROCEDURE naploz IS BEGIN INSERT INTO naplo VALUES ({V('B.ID')}); END;", r['sql'])
        self.assertIn('COMMIT -> a végpont tranzakciója véglegesít', r['notes'])
        with self.assertRaisesRegex(Unsupported, 'Tranzakcióvezérlés'):
            self.prepared('COMMIT;', units=units)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_form(self, xml, module, *extra, config=None, label='out'):
        source = self.root / (label + '.xml'); source.write_text(xml, encoding='utf-8')
        args = ['migrate', str(source), '--screen', '--module', module, '--out', str(self.root / label), *extra]
        if config is not None:
            (self.root / (label + '.json')).write_text(json.dumps(config), encoding='utf-8')
            args += ['--config', str(self.root / (label + '.json'))]
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(main(args), 0, err.getvalue())
        return self.root / label

    def test_data_and_button_triggers_run_their_plsql(self):
        out = self.run_form(PLSQL_FORM, 'plsqlTeszt')
        model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        triggers = {(t['owner'], t['event']): t for t in model['triggers']}
        self.assertEqual(triggers[('B', 'POST-QUERY')]['passthrough']['written'], ['B.NEV'])  # DB items stay the queried ones
        self.assertEqual(triggers[('B', 'PRE-INSERT')]['passthrough']['written'], ['B.ID', 'B.MODOSITVA'])
        self.assertEqual(triggers[('B', 'POST-INSERT')]['passthrough']['units'], ['NAPLOZ'])
        self.assertEqual((triggers[('GOMBOK.PB_LEZAR', 'WHEN-BUTTON-PRESSED')]['status'],
                          triggers[('GOMBOK.PB_LEZAR', 'WHEN-BUTTON-PRESSED')]['target']), ('converted', 'action'))
        # go_block + create_record: recognised steps, run on the screen (no endpoint, see skipped_actions).
        self.assertEqual(triggers[('GOMBOK.PB_UJ', 'WHEN-BUTTON-PRESSED')]['passthrough']['commands'], ['GO_BLOCK', 'CREATE_RECORD'])
        service = (out / 'backend/DPS/PlsqlTesztServiceImpl.java').read_text(encoding='utf-8')
        data = service  # SQL, Forms procedures and JDBC are in the same service
        self.assertIn('postInsertB(row, context);', data)
        self.assertIn(f"SELECT ugyfel_seq.NEXTVAL INTO {V('B.ID')} FROM dual;", data)
        self.assertIn('static final String NAPLOZ =', data)  # the form's procedure, named
        self.assertIn('+ PlsqlUnits.NAPLOZ +', data)
        self.assertIn('return log1x(log, PlsqlTesztConstants.ON_GOMBOK_PB_LEZAR', service)  # the button runs its PL/SQL here
        self.assertIn('return new ActionResult(blocks, PlsqlValues.lines(', service)
        self.assertIn('static final boolean MODULE_REVIEWED = false;', service)

    def test_live_backend_is_usable_at_once(self):
        out = self.run_form(PLSQL_FORM, 'plsqlTeszt', config={'backend_live': True}, label='live')
        service = (out / 'backend/DPS/PlsqlTesztServiceImpl.java').read_text(encoding='utf-8')
        data = service
        self.assertIn('static final boolean MODULE_REVIEWED = true;', service)
        # one switch still turns everything off: the guard's operations follow MODULE_REVIEWED
        self.assertRegex(' '.join(data.split()), r'MODULE_REVIEWED \? Set\.of\([^)]*"create"[^)]*\) : Set\.of\(\)')
        plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        implemented = lambda prefix: next(e['implemented'] for e in plan['endpoints'] if e['method'].startswith(prefix))
        self.assertTrue(all(implemented(m) for m in ('listB', 'createB', 'updateB', 'deleteB', 'onGombokPbLezar')))
        self.assertFalse(any(e['method'].startswith('onGombokPbUj') for e in plan['endpoints']))
        self.assertIn('GOMBOK.PB_UJ', [a['owner'] for a in plan['skipped_actions']])

    def test_item_states_are_left_to_the_developer(self):
        # 4.27: the screen is a frame: SET_ITEM_PROPERTY is not translated, the button keeps its original code
        out = self.run_form(STATES_FORM, 'allapot')
        plan = json.loads((out / 'analysis/screen-plan.json').read_text(encoding='utf-8'))
        self.assertNotIn('item_states', plan)
        source = (out / 'frontend/allapot/allapot.component.ts').read_text(encoding='utf-8')
        for absent in ('setItemState', 'setItemValue', 'stateValue', 'valueChanges', 'Validators'):
            self.assertNotIn(absent, source)
        self.assertIn('constructor() {\n    super();\n  }', source)
        click = source[source.index('  protected onPbEngedClick(): void {'):]
        self.assertIn('// TODO: a gomb kódját kézzel kell átültetni.\n    //#region Eredeti Forms-kód\n', click)
        self.assertIn('set_item_property', click.split('//#endregion', 1)[0].lower())
        coverage = json.loads((out / 'analysis/runtime-coverage.json').read_text(encoding='utf-8'))
        self.assertFalse(any(t['engine'] == 'typescript' and t['event'] != 'WHEN-BUTTON-PRESSED' for t in coverage['triggers']))
        notes = (out / 'frontend/allapot/MIGRATION_NOTES.md').read_text(encoding='utf-8')
        self.assertNotIn('## Mezőállapotok (Forms-logikából)', notes)

if __name__ == '__main__':
    unittest.main()

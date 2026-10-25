"""4.15: local packages the survey found refused - nested subprograms in members, initialisation parts.

A package member may declare its own procedures and functions (nested subprograms): they stay as
written. The initialisation part (BEGIN at the end of the body) runs at the start of the block, once
per request like the package state itself; writing data or the screen there is refused.
"""
import unittest

from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import prepare
from frm_forms.plsql_structure import parse

ITEMS = {'B': {'ID': {'type': 'number'}, 'NAME': {'type': 'text'}}, 'CTRL': {'X': {'type': 'text'}}}


def button(source, units):
    return prepare(source, block='CTRL', items=ITEMS, units=units, prefixes=('qms$',), other_blocks=True, parameters=True,
                   transaction=True, ui=True, form='F', trigger_item='CTRL.PB')


def package(spec, body):
    return {'kind': 'package', 'text': '', 'spec': spec, 'body': body}


class NestedSubprogramTests(unittest.TestCase):
    def test_a_member_with_its_own_procedures_is_embedded_as_written(self):
        units = {'PKG': package('PACKAGE pkg IS PROCEDURE run; END pkg;',
                                'PACKAGE BODY pkg IS\n'
                                '  PROCEDURE run IS\n'
                                '    v NUMBER;\n'
                                '    FUNCTION twice(p NUMBER) RETURN NUMBER IS BEGIN RETURN p * 2; END;\n'
                                '    PROCEDURE put(p VARCHAR2) IS BEGIN :B.NAME := p; END put;\n'
                                '  BEGIN\n'
                                '    v := twice(:B.ID);\n'
                                '    put(TO_CHAR(v));\n'
                                '  END run;\n'
                                'END pkg;')}
        r = button('pkg.run;', units)
        self.assertEqual(r['units'], ['PKG'])
        self.assertIn('FUNCTION twice(p NUMBER)', r['sql'])
        self.assertIn('B.NAME', [b['source'] for b in r['outs']])  # written in the nested procedure
        parse(r['sql'])  # one well-formed block

    def test_an_unclosed_member_is_still_refused(self):
        units = {'PKG': package('PACKAGE pkg IS PROCEDURE run; END pkg;',
                                'PACKAGE BODY pkg IS PROCEDURE run IS PROCEDURE inner IS BEGIN NULL; END pkg;')}
        with self.assertRaisesRegex(Unsupported, 'PKG helyi csomag nem futtatható'):
            button('pkg.run;', units)


class InitialisationTests(unittest.TestCase):
    SPEC = 'PACKAGE pkg IS g_ev NUMBER; FUNCTION ev RETURN NUMBER; END pkg;'

    def test_the_initialisation_part_runs_first_in_every_request(self):
        units = {'PKG': package(self.SPEC,
                                'PACKAGE BODY pkg IS\n'
                                '  FUNCTION ev RETURN NUMBER IS BEGIN RETURN g_ev; END;\n'
                                'BEGIN\n'
                                "  SELECT ertek INTO g_ev FROM param WHERE kod = 'EV';\n"
                                'EXCEPTION\n'
                                '  WHEN NO_DATA_FOUND THEN g_ev := 0;\n'
                                'END pkg;')}
        r = button(':CTRL.X := pkg.ev;', units)
        tail = r['tail']
        self.assertIn('-- PKG inicializálása', tail)
        self.assertLess(tail.index("SELECT ertek INTO g_ev FROM param WHERE kod = 'EV';"), tail.index(':= ev;'))
        self.assertIn('WHEN NO_DATA_FOUND THEN g_ev := 0;', tail)  # its own handler stays with it
        self.assertTrue(any('inicializáló része a blokk elején fut' in n for n in r['notes']))
        parse(r['sql'])

    def test_callee_packages_are_initialised_first(self):
        units = {'A': package('PACKAGE a IS g NUMBER; END a;', 'PACKAGE BODY a IS BEGIN g := b.f + 1; END a;'),
                 'B': package('PACKAGE b IS h NUMBER; FUNCTION f RETURN NUMBER; END b;',
                              'PACKAGE BODY b IS FUNCTION f RETURN NUMBER IS BEGIN RETURN h; END; BEGIN h := 41; END b;')}
        r = button(':CTRL.X := TO_CHAR(a.g);', units)
        self.assertEqual(r['units'], ['B', 'A'])
        self.assertLess(r['tail'].index('-- B inicializálása'), r['tail'].index('-- A inicializálása'))
        parse(r['sql'])

    def test_writing_data_or_the_screen_once_per_session_is_refused(self):
        for init in ("UPDATE naplo SET db = db + 1;", "go_block('B');", 'COMMIT;'):
            units = {'PKG': package(self.SPEC, 'PACKAGE BODY pkg IS FUNCTION ev RETURN NUMBER IS BEGIN RETURN g_ev; END; '
                                               'BEGIN ' + init + ' END pkg;')}
            with self.subTest(init=init), self.assertRaisesRegex(Unsupported, 'adatot vagy képernyőt módosít'):
                button(':CTRL.X := pkg.ev;', units)

    def test_package_state_still_cannot_cross_a_commit_point(self):
        units = {'PKG': package(self.SPEC, 'PACKAGE BODY pkg IS FUNCTION ev RETURN NUMBER IS BEGIN RETURN g_ev; END; '
                                           'BEGIN g_ev := 1; END pkg;')}
        with self.assertRaisesRegex(Unsupported, 'PKG helyi csomag'):
            prepare(':CTRL.X := pkg.ev; commit_form; :CTRL.X := pkg.ev;', block='CTRL', items=ITEMS, units=units,
                    prefixes=('qms$',), other_blocks=True, parameters=True, transaction=True, ui=True, form='F',
                    trigger_item='CTRL.PB', commit_points=True)


if __name__ == '__main__':
    unittest.main()

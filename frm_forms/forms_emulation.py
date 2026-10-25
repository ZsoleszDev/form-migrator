"""Forms runtime emulation inside the anonymous block of a button or the start-up code.

The PL/SQL still runs as written in Oracle. The Forms built-ins it calls do not exist there, so
the block gets small local subprograms in their place that record what the screen has to do:

    GO_BLOCK('B')                     -> frm_cmd('GO_BLOCK', 'B')        (a screen command)
    SET_ITEM_PROPERTY('B.I', ENABLED, PROPERTY_FALSE)
                                      -> frm_cmd('SET_ITEM_PROPERTY', 'B.I', 'ENABLED', 'PROPERTY_FALSE')
    EXECUTE_QUERY / COMMIT_FORM / CALL_FORM ...  -> a command, only as the last step of the code
    al := FIND_ALERT('A'); SHOW_ALERT(al)        -> the answer from the request, or a pending dialog
    NAME_IN('B.I'), COPY(x, 'B.I'), DEFAULT_VALUE(x, 'GLOBAL.G') -> the bound variables
    :SYSTEM.CURSOR_BLOCK ...          -> request context sent by the screen; TRIGGER_* and CURRENT_FORM fixed
    :GLOBAL.X := ...                  -> returned, the screen keeps the globals for the next requests

The commands travel back in one buffer (CHR(30) between commands, CHR(31) between fields); the
screen executes them in order after writing back the item values. A pending alert rolls the
work of the request back (savepoint): the screen shows the dialog and sends the request again
with the answer, so the code runs once more from the start, exactly as far as Forms would have.

What still needs the Forms runtime in the middle of the code (EXECUTE_QUERY followed by reads
of the queried values, GET_ITEM_PROPERTY of the screen state ...) is refused with the reason.
"""
from __future__ import annotations

from . import forms_runtime

# Screen commands whose effect no later statement of the trigger can observe.
COMMANDS = set('''GO_ITEM GO_BLOCK GO_RECORD NEXT_ITEM PREVIOUS_ITEM NEXT_BLOCK PREVIOUS_BLOCK NEXT_RECORD
 PREVIOUS_RECORD FIRST_RECORD LAST_RECORD UP DOWN SCROLL_UP SCROLL_DOWN SET_ITEM_PROPERTY
 SET_ITEM_INSTANCE_PROPERTY SET_BLOCK_PROPERTY SET_WINDOW_PROPERTY SHOW_WINDOW HIDE_WINDOW SHOW_VIEW HIDE_VIEW
 SET_VIEW_PROPERTY SET_CANVAS_PROPERTY SET_TAB_PAGE_PROPERTY SET_RADIO_BUTTON_PROPERTY SET_LOV_PROPERTY
 SET_RECORD_PROPERTY CLEAR_ITEM ADD_PARAMETER DELETE_PARAMETER DESTROY_PARAMETER_LIST ADD_GROUP_ROW
 SET_GROUP_CHAR_CELL SET_GROUP_NUMBER_CELL SET_GROUP_DATE_CELL DELETE_GROUP DELETE_GROUP_ROW REPLACE_CONTENT_VIEW
 SET_INPUT_FOCUS MOVE_WINDOW RESIZE_WINDOW'''.split())
# Commands whose effect the Forms code after them could observe: only as the last step.
TAIL_COMMANDS = set('''EXECUTE_QUERY COUNT_QUERY ENTER_QUERY COMMIT_FORM POST CLEAR_FORM CLEAR_BLOCK CLEAR_RECORD
 CREATE_RECORD DELETE_RECORD DUPLICATE_RECORD EXIT_FORM CALL_FORM OPEN_FORM NEW_FORM GO_FORM DO_KEY
 LIST_VALUES'''.split())
# Tail commands that may stand in the middle of a button's code as a screen point: the request stops, the screen
# carries out the step, then the code resumes with the screen's new values (commit_points.SCREEN_PLACEHOLDER).
SCREEN_STEPS = {'EXECUTE_QUERY', 'CLEAR_BLOCK', 'CLEAR_RECORD', 'CREATE_RECORD', 'CLEAR_FORM'}
# Built-ins that move the cursor: a later DO_KEY may run another block's or item's KEY trigger.
NAVIGATION = set('''GO_ITEM GO_BLOCK GO_FORM NEXT_ITEM PREVIOUS_ITEM NEXT_BLOCK PREVIOUS_BLOCK NEXT_FORM PREVIOUS_FORM
 SET_INPUT_FOCUS'''.split())
# Screen commands the web screen cannot carry out for a button: embedded KEY trigger code with them stays manual.
NOT_FROM_BUTTON = {'LIST_VALUES', 'ENTER_QUERY'}
# Pure screen niceties: nothing the web screen has to do.
NOOPS = set('''SYNCHRONIZE BELL REDISPLAY CLEAR_MESSAGE PAUSE SET_APPLICATION_PROPERTY VALIDATE RECALCULATE
 CLEAR_EOL HIDE_MENU SHOW_MENU'''.split())
# Built-in functions with a local replacement.
FUNCTIONS = {name: 'frm_find' for name in '''FIND_ALERT FIND_ITEM FIND_BLOCK FIND_WINDOW FIND_CANVAS FIND_VIEW
 FIND_LOV FIND_GROUP FIND_TIMER FIND_TAB_PAGE FIND_FORM FIND_RELATION FIND_EDITOR FIND_VA FIND_COLUMN
 CREATE_PARAMETER_LIST'''.split()}
FUNCTIONS.update({'GET_PARAMETER_LIST': 'frm_none', 'ID_NULL': 'frm_id_null', 'SHOW_ALERT': 'frm_show_alert'})
GROUP_FUNCTIONS = {'CREATE_GROUP', 'ADD_GROUP_COLUMN'}  # frm_group('<NAME>', ...): a command and the handle
ALERT_SETTERS = {'SET_ALERT_PROPERTY', 'SET_ALERT_BUTTON_PROPERTY'}
VALUE_SETTERS = {'COPY': 'frm_copy', 'DEFAULT_VALUE': 'frm_default_value'}
STATUS = {'FORM_SUCCESS': 'TRUE', 'FORM_FAILURE': 'FALSE', 'FORM_FATAL': 'FALSE'}
# Forms object types of handle variables: names in the emulation.
TYPES = set('''ALERT ITEM BLOCK WINDOW CANVAS VIEWPORT RECORDGROUP GROUPCOLUMN PARAMLIST LOV TIMER TAB_PAGE
 FORMMODULE RELATION EDITOR MENUITEM VISUALATTRIBUTE REPORT_OBJECT'''.split())
HANDLE_TYPE = 'VARCHAR2(4000)'
# Forms constants that become text when they are arguments of an emulated built-in.
CONSTANTS = forms_runtime.CONSTANTS | set('''CHAR_COLUMN NUMBER_COLUMN DATE_COLUMN LONG_COLUMN ALERT_MESSAGE_TEXT
 BUILTIN_DATE_FORMAT PLSQL_DATE_FORMAT USER_DATE_FORMAT USER_DATETIME_FORMAT DEFAULT ITEM_SCOPE RECORD_SCOPE
 BLOCK_SCOPE FORM_SCOPE DEFAULT_SCOPE NO_HIDE HIDE DO_REPLACE NO_REPLACE QUERY_ONLY NO_QUERY_ONLY
 SESSION NO_SESSION ACTIVATE NO_ACTIVATE TEXT_PARAMETER DATA_PARAMETER PROPERTY_TRUE PROPERTY_FALSE
 PROPERTY_ON PROPERTY_OFF ALERT_BUTTON1 ALERT_BUTTON2 ALERT_BUTTON3 LABEL ENABLED VISIBLE DISPLAYED'''.split())
ALERT_BUTTONS = {'ALERT_BUTTON1': 88, 'ALERT_BUTTON2': 89, 'ALERT_BUTTON3': 90}
# Query/DML properties: a block setting the generated endpoints would have to follow; not a screen command.
QUERY_PROPERTIES = {'DEFAULT_WHERE', 'ONETIME_WHERE', 'ORDER_BY', 'QUERY_DATA_SOURCE_NAME', 'QUERY_DATA_SOURCE_TYPE',
                    'QUERY_DATA_SOURCE_COLUMNS', 'QUERY_DATA_SOURCE_ARGUMENTS', 'DML_DATA_TARGET_NAME',
                    'DML_DATA_TARGET_TYPE', 'DML_ARGUMENTS'}
# :SYSTEM values: fixed for the trigger, or sent by the screen with the request (FRM request context).
SYSTEM_FIXED = {'TRIGGER_BLOCK', 'TRIGGER_ITEM', 'CURRENT_FORM', 'TRIGGER_FORM'}
SYSTEM_REQUEST = set('''CURSOR_BLOCK CURSOR_ITEM CURSOR_RECORD CURSOR_VALUE CURRENT_BLOCK CURRENT_ITEM CURRENT_VALUE
 TRIGGER_RECORD FORM_STATUS BLOCK_STATUS RECORD_STATUS MESSAGE_LEVEL SUPPRESS_WORKING LAST_RECORD MOUSE_ITEM
 MOUSE_RECORD MOUSE_BUTTON_PRESSED EVENT_WINDOW CURRENT_DATETIME DATE_THRESHOLD EFFECTIVE_DATE'''.split())
# After a tail command only these may follow: the end of the code, a status test, more commands, messages.
TAIL_SAFE = set('''END IF ELSE ELSIF THEN NOT AND OR NULL RAISE FORM_TRIGGER_FAILURE FORM_SUCCESS FORM_FAILURE
 FORM_FATAL BEGIN TRUE FALSE EXCEPTION WHEN OTHERS MESSAGE'''.split())
ALERT_PARAMETER = 'FRM.ALERTS'
# The application properties a database session also knows.
APPLICATION_PROPERTIES = {'USERNAME': 'USER'}


def emulated(word: str) -> bool:
    return (word in COMMANDS or word in TAIL_COMMANDS or word in NOOPS or word in FUNCTIONS or word in GROUP_FUNCTIONS
            or word in ALERT_SETTERS or word in VALUE_SETTERS or word in STATUS or word == 'NAME_IN'
            or word == 'GET_APPLICATION_PROPERTY' or word == 'FORMS_DDL')


def answers_var() -> str:
    """The bound variable of the alert answers (plsql_passthrough.Rewriter.var of FRM.ALERTS): fixed."""
    import hashlib
    return 'nv_' + hashlib.sha1(ALERT_PARAMETER.encode('utf-8')).hexdigest()[:10]


# The fixed local subprograms of an emulated block, by name. The same texts are the constants of
# CommonMigrateTools.FormsPlsql (test_slim_code checks it): a generated block names them instead of
# repeating them, so every module shares one copy.
HELPERS = {
    'MSG': '\n'.join(['  PROCEDURE frm_msg(p_text VARCHAR2, p_mode PLS_INTEGER DEFAULT NULL) IS',
                      '  BEGIN frm_messages := SUBSTR(frm_messages || p_text || CHR(10), 1, 32000); END;']),
    'CMD': '\n'.join(['  PROCEDURE frm_cmd(p_op VARCHAR2, p1 VARCHAR2 DEFAULT NULL, p2 VARCHAR2 DEFAULT NULL,',
                      '                     p3 VARCHAR2 DEFAULT NULL, p4 VARCHAR2 DEFAULT NULL, p5 VARCHAR2 DEFAULT NULL,',
                      '                     p6 VARCHAR2 DEFAULT NULL) IS',
                      '  BEGIN',
                      '    frm_ui := SUBSTR(frm_ui || p_op || CHR(31) || p1 || CHR(31) || p2 || CHR(31) || p3 || CHR(31) || p4',
                      '                      || CHR(31) || p5 || CHR(31) || p6 || CHR(30), 1, 32000);',
                      '  END;']),
    'FIND': '  FUNCTION frm_find(p_name VARCHAR2) RETURN VARCHAR2 IS BEGIN RETURN UPPER(p_name); END;',
    'NONE': '  FUNCTION frm_none(p_name VARCHAR2) RETURN VARCHAR2 IS BEGIN RETURN NULL; END;',
    'ID_NULL': '  FUNCTION frm_id_null(p_id VARCHAR2) RETURN BOOLEAN IS BEGIN RETURN p_id IS NULL; END;',
    'GROUP': '\n'.join(['  FUNCTION frm_group(p_op VARCHAR2, p1 VARCHAR2, p2 VARCHAR2 DEFAULT NULL, p3 VARCHAR2 DEFAULT NULL,',
                        '                      p4 VARCHAR2 DEFAULT NULL) RETURN VARCHAR2 IS',
                        '  BEGIN',
                        '    frm_cmd(p_op, p1, p2, p3, p4);',
                        "    RETURN CASE WHEN p_op = 'ADD_GROUP_COLUMN' THEN UPPER(p1) || '.' || UPPER(p2) ELSE UPPER(p1) END;",
                        '  END;']),
    'COPY': '  PROCEDURE frm_copy(p_value VARCHAR2, p_target IN OUT VARCHAR2) IS BEGIN p_target := p_value; END;',
    'DEFAULT_VALUE': ('  PROCEDURE frm_default_value(p_value VARCHAR2, p_target IN OUT VARCHAR2) IS'
                      ' BEGIN IF p_target IS NULL THEN p_target := p_value; END IF; END;'),
    'ALERT': '\n'.join(['  PROCEDURE frm_alert_prop(p_alert VARCHAR2, p_prop VARCHAR2, p_value VARCHAR2,',
                        '                            p_label VARCHAR2 DEFAULT NULL) IS',
                        '  BEGIN',
                        "    frm_alert_texts(UPPER(p_alert) || '|' || UPPER(p_prop)) := CASE WHEN p_label IS NULL THEN p_value ELSE p_label END;",
                        '  END;',
                        '  FUNCTION frm_alert_text(p_key VARCHAR2) RETURN VARCHAR2 IS',
                        '  BEGIN',
                        '    IF frm_alert_texts.EXISTS(p_key) THEN',
                        '      RETURN frm_alert_texts(p_key);',
                        '    END IF;',
                        '    RETURN NULL;',
                        '  END;',
                        '  FUNCTION frm_show_alert(p_alert VARCHAR2) RETURN NUMBER IS',
                        '    v_answer VARCHAR2(10);',
                        '  BEGIN',
                        '    frm_alert_count := frm_alert_count + 1;',
                        f"    v_answer := REGEXP_SUBSTR({answers_var()}, '[^,]+', 1, frm_alert_count);",
                        '    IF v_answer IS NOT NULL THEN',
                        '      RETURN 87 + TO_NUMBER(v_answer);',
                        '    END IF;',
                        "    frm_cmd('SHOW_ALERT', UPPER(p_alert), frm_alert_text(UPPER(p_alert) || '|ALERT_MESSAGE_TEXT'),",
                        "             frm_alert_text(UPPER(p_alert) || '|ALERT_BUTTON1'), frm_alert_text(UPPER(p_alert) || '|ALERT_BUTTON2'),",
                        "             frm_alert_text(UPPER(p_alert) || '|ALERT_BUTTON3'), TO_CHAR(frm_alert_count));",
                        '    RAISE frm_alert_pending;',
                        '  END;']),
}
# need -> helper, in declaration order (frm_group calls frm_cmd, frm_show_alert calls frm_alert_text).
HELPER_NEEDS = [('ui', 'CMD'), ('find', 'FIND'), ('none', 'NONE'), ('id_null', 'ID_NULL'), ('group', 'GROUP'),
                ('copy', 'COPY'), ('default_value', 'DEFAULT_VALUE'), ('alert', 'ALERT')]


def declarations(needs: set) -> tuple[list[str], list[str]]:
    """(variables, helper names) an emulated block needs. PL/SQL wants every variable of a declarative
    part before the first subprogram body, so the caller places the two lists apart."""
    items = []
    if 'ui' in needs:
        items.append('  frm_ui VARCHAR2(32767);')
    if 'alert' in needs or 'alert_buttons' in needs:
        items += [f'  {name} CONSTANT NUMBER := {value};' for name, value in ALERT_BUTTONS.items()]
    if 'alert' in needs:
        items += ['  TYPE frm_texts IS TABLE OF VARCHAR2(4000) INDEX BY VARCHAR2(400);',
                  '  frm_alert_texts frm_texts;',
                  '  frm_alert_count PLS_INTEGER := 0;',
                  '  frm_alert_pending EXCEPTION;']
    return items, [helper for need, helper in HELPER_NEEDS if need in needs]


HELPER_DOCS = {'MSG': 'frm_msg: a MESSAGE szövegei a válasz üzenetei közé.',
               'CMD': 'frm_cmd: egy Forms-hívás felületi utasításként (CHR(30)/CHR(31) tagolás).',
               'FIND': 'frm_find: FIND_ALERT, FIND_ITEM ... - a név maga az azonosító.',
               'NONE': 'frm_none: GET_PARAMETER_LIST - a képernyőn nincs paraméterlista-objektum.',
               'ID_NULL': 'frm_id_null: ID_NULL.',
               'GROUP': 'frm_group: CREATE_GROUP, ADD_GROUP_COLUMN - utasítás és azonosító.',
               'COPY': 'frm_copy: COPY(érték, \'BLOKK.MEZŐ\').',
               'DEFAULT_VALUE': 'frm_default_value: DEFAULT_VALUE(érték, \'BLOKK.MEZŐ\').',
               'ALERT': 'frm_alert_prop, frm_alert_text, frm_show_alert: SHOW_ALERT és társai.'}


def java_class() -> str:
    """CommonMigrateTools.FormsPlsql: HELPERS as Java constants, in the template's layout (lines <= 100)."""
    def rendered(text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'

    def pieces(text, first_width, width):
        """Literals of at most first_width / width characters, cut after a space; every source line ends one."""
        result, budget = [], first_width
        for line in (text + '\n').split('\n')[:-1]:
            rest = line + '\n'
            while len(rendered(rest)) > budget:
                limit = budget - 2 - rest[:budget].count('"') - rest[:budget].count('\\')
                cut = rest.rfind(' ', 0, limit)
                cut = cut + 1 if cut > 0 else limit
                result.append(rendered(rest[:cut]))
                rest, budget = rest[cut:], width
            result.append(rendered(rest))
            budget = width
        return result

    lines = ['    /**',
             '     * A Forms-emuláció rögzített PL/SQL-segédeljárásai (frm_msg, frm_cmd ...).',
             '     *',
             '     * <p>A generált névtelen blokkok a nevükkel hivatkoznak rájuk, így minden modul ugyanazt az',
             '     * egy példányt használja. A szövegük a migrátor forms_emulation.HELPERS értéke.',
             '     */',
             '    public static final class FormsPlsql {']
    for index, (name, text) in enumerate(HELPERS.items()):
        if index:
            lines.append('')
        lines.append('        /** ' + HELPER_DOCS[name] + ' */')
        head = '        public static final String ' + name + ' = '
        literals = pieces(text, 100 - len(head), 100 - len('                + ') - 1)
        lines.append(head + literals[0])
        lines += ['                + ' + literal for literal in literals[1:]]
        lines[-1] += ';'
    lines += ['', '        private FormsPlsql() {', '        }', '    }']
    return '\n'.join(lines) + '\n'

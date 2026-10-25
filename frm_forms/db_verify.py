"""verify-db: compile every generated SQL and PL/SQL text in the target Oracle database, never run it.

A wrong column, an unknown routine (PLS-00201), a missing grant or a typo used to show up only when
somebody pressed the button. DBMS_SQL.PARSE compiles an anonymous block or a query in the
database without executing it, so the report lists those errors right after generation, with the
line of the generated text the error points at.

Safety: only SELECT/WITH, INSERT/UPDATE/DELETE/MERGE and anonymous blocks (DECLARE/BEGIN) are sent.
DBMS_SQL.PARSE would execute DDL at once, so anything else is refused before it reaches the database.
Nothing is committed; the session ends with a rollback. python-oracledb (thin mode, no Oracle client)
is the only dependency: pip install oracledb.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import MigrationError, write_json

PARSE_BLOCK = """DECLARE
  c INTEGER := DBMS_SQL.OPEN_CURSOR;
BEGIN
  DBMS_SQL.PARSE(c, :stmt, DBMS_SQL.NATIVE);
  DBMS_SQL.CLOSE_CURSOR(c);
EXCEPTION
  WHEN OTHERS THEN
    IF DBMS_SQL.IS_OPEN(c) THEN
      DBMS_SQL.CLOSE_CURSOR(c);
    END IF;
    RAISE;
END;"""
ALLOWED = {'SELECT', 'WITH', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'DECLARE', 'BEGIN'}
LINE = re.compile(r'ORA-06550: (?:line|sor) (\d+), (?:column|oszlop) (\d+)', re.I)


def named_binds(sql: str) -> str:
    """Positional JDBC binds (?) as :b1, :b2 ... - DBMS_SQL parses named binds only."""
    from .plsql_passthrough import scan
    out, index = [], 0
    for kind, text, _, _ in scan(sql):
        if kind == 'op' and text == '?':
            index += 1
            out.append(':b' + str(index))
        else:
            out.append(text)
    return ''.join(out)


def first_word(sql: str) -> str:
    from .plsql_passthrough import scan, significant
    tokens = significant(scan(sql))
    return tokens[0][1].upper() if tokens else ''


def module_folders(outputs) -> list[Path]:
    """migrate output folders, or the module folders of a batch folder."""
    folders = []
    for output in outputs:
        output = Path(output)
        if (output / 'analysis/db-statements.json').is_file():
            folders.append(output)
        elif output.is_dir():
            folders += sorted(d for d in output.iterdir() if (d / 'analysis/db-statements.json').is_file())
    if not folders:
        raise MigrationError('VERIFY_DB: nincs analysis/db-statements.json a megadott mappákban (4.14-es vagy újabb generálás kell).')
    return folders


def connect_oracle(dsn: str, user: str, password: str):
    try:
        import oracledb
    except ImportError as exc:
        raise MigrationError('VERIFY_DB: a python-oracledb csomag kell (pip install oracledb; thin módban nem kell Oracle kliens).') from exc
    return oracledb.connect(user=user, password=password, dsn=dsn)


def error_text(exc: Exception) -> str:
    error = exc.args[0] if exc.args else exc
    return str(getattr(error, 'message', None) or error).strip()


def excerpt(sql: str, message: str) -> str | None:
    """The line of the parsed text an ORA-06550 points at, for the report."""
    found = LINE.search(message)
    if not found:
        return None
    lines = sql.split('\n')
    number = int(found.group(1))
    return lines[number - 1].strip() if 0 < number <= len(lines) else None


def check(cursor, sql: str, clob=None) -> str | None:
    """None when Oracle compiled it, else the error text."""
    if first_word(sql) not in ALLOWED:
        return 'Nem ellenőrizhető utasítás (csak SELECT, DML és névtelen blokk): ' + (first_word(sql) or 'üres')
    try:
        if clob is not None:
            cursor.setinputsizes(stmt=clob)  # texts over 32767 bytes too
        cursor.execute(PARSE_BLOCK, stmt=sql)
    except Exception as exc:  # the driver's DatabaseError, reported per statement
        return error_text(exc)
    return None


def verify(outputs, connection) -> dict:
    """Compile the statements of every module; analysis/db-verify.json per module, the summary returned."""
    cursor = connection.cursor()
    try:
        import oracledb
        clob = oracledb.DB_TYPE_CLOB
    except (ImportError, AttributeError):
        clob = None
    summary = {'version': 1, 'modules': []}
    try:
        for folder in module_folders(outputs):
            plan = json.loads((folder / 'analysis/db-statements.json').read_text(encoding='utf-8'))
            results = []
            for statement in plan['statements']:
                sql = named_binds(statement['sql']) if statement['kind'] == 'plsql' else statement['sql']
                error = check(cursor, sql, clob)
                entry = {'id': statement['id'], 'kind': statement['kind'], 'source': statement['source'], 'ok': error is None}
                if error:
                    entry['error'] = error
                    line = excerpt(sql, error)
                    if line:
                        entry['line'] = line
                results.append(entry)
            report = {'version': 1, 'module_class': plan.get('module_class'), 'checked': len(results),
                      'failed': sum(1 for r in results if not r['ok']), 'results': results}
            write_json(folder / 'analysis/db-verify.json', report)
            summary['modules'].append({'folder': str(folder), 'module_class': report['module_class'],
                                       'checked': report['checked'], 'failed': report['failed'],
                                       'errors': [r for r in results if not r['ok']]})
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
    summary['checked'] = sum(m['checked'] for m in summary['modules'])
    summary['failed'] = sum(m['failed'] for m in summary['modules'])
    return summary


def markdown(summary: dict) -> str:
    lines = ['# Adatbázis-ellenőrzés (verify-db)', '',
             'A generált SQL és PL/SQL lefordítva a céladatbázisban (DBMS_SQL.PARSE), végrehajtás nélkül.', '',
             f"Ellenőrzött utasítás: {summary['checked']}, hibás: {summary['failed']}.", '',
             '| Modul | Ellenőrzött | Hibás |', '|---|---:|---:|']
    lines += [f"| {m['module_class'] or m['folder']} | {m['checked']} | {m['failed']} |" for m in summary['modules']]
    for module in summary['modules']:
        if not module['errors']:
            continue
        lines += ['', '## ' + (module['module_class'] or module['folder']), '']
        for error in module['errors']:
            lines.append(f"- **{error['source']}** ({error['kind']}): " + error['error'].replace('\n', ' '))
            if error.get('line'):
                lines.append(f"  - A hibás sor: `{error['line']}`")
    lines += ['', 'Részletek modulonként: `analysis/db-verify.json`. Az adatbázis oldja fel futáskor jelzésű rutinok itt '
                  'derülnek ki: ha PLS-00201 jön, a rutin nincs az adatbázisban (csatolt könyvtárban van: `--pld`), vagy hiányzik a jogosultság.']
    return '\n'.join(lines) + '\n'


def run(args) -> int:
    password = os.environ.get(args.password_env)
    if password is None:
        raise MigrationError(f'VERIFY_DB: a jelszót a {args.password_env} környezeti változóban add meg (parancssorban nem).')
    summary = verify(args.outputs, connect_oracle(args.dsn, args.user, password))
    first = Path(args.outputs[0])
    report = Path(args.report) if args.report else first / 'DB_VERIFY_HU.md'
    report.write_text(markdown(summary), encoding='utf-8')
    print(json.dumps({'report': str(report), 'checked': summary['checked'], 'failed': summary['failed']}, ensure_ascii=False))
    return 3 if summary['failed'] else 0

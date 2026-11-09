"""Database routines the Forms code calls, as typed Java methods the Java assistant calls instead of re-implementing them.

A stored procedure or function (KOD_PKG.CHECK_INSERT) keeps running in Oracle, exactly as the Forms code called it:
the same name (synonyms and the current schema resolve it as before), the same parameters by position or by name, the
database defaults for the omitted ones. Only routines with a reviewed signature (schema.json "procedures": dictionary-sql
and dictionary-import) get a method; the model is told the others are missing instead of guessing their logic.

The methods are generated code of the DPS ServiceImpl (DbCalls on the service's NamedParameterJdbcTemplate), added
when an applied proposal first calls them.
"""
from __future__ import annotations

import re

from .common import jstr, name as java_name

JAVA = {"text": "String", "number": "BigDecimal", "datetime": "LocalDateTime"}
SQL_TYPES = {"text": "Types.VARCHAR", "number": "Types.NUMERIC", "datetime": "Types.TIMESTAMP"}
NAMED = re.compile(r"^\s*([A-Za-z][\w$#]*)\s*=>")
# Oracle-supplied packages exist in every database; the model knows them (DBMS_OUTPUT, UTL_FILE ...).
STANDARD = ("DBMS_", "UTL_", "SYS.", "OWA_", "HTP.", "HTF.", "APEX_")
CALL_KINDS = {"external_candidate", "unresolved", "ambiguous_local_program_unit"}


def resolve(name: str, procedures: dict) -> tuple[str, dict] | None:
    """The reviewed signature of a called routine: the name as called, else the one OWNER.NAME ending with it."""
    upper = name.upper()
    if upper in procedures:
        return upper, procedures[upper]
    found = [key for key in procedures if key.endswith("." + upper)]
    return (found[0], procedures[found[0]]) if len(found) == 1 else None


def call_parameters(arguments: list[str], signature: dict) -> list[dict] | str:
    """The parameters one call passes (positional or by name), or the reason it cannot be called from Java."""
    params = signature["arguments"]
    named = [NAMED.match(a) for a in arguments]
    if any(named):
        if not all(named):
            return "mixed positional and named arguments"
        by_name = {p["name"].upper(): p for p in params}
        chosen = []
        for match in named:
            param = by_name.get(match.group(1).upper())
            if param is None:
                return "unknown parameter name: " + match.group(1)
            chosen.append(param)
        used = {p["name"].upper() for p in chosen}
        omitted = [p for p in params if p["name"].upper() not in used]
    else:
        if len(arguments) > len(params):
            return f"{len(arguments)} arguments, the signature has {len(params)} parameters"
        chosen, omitted = params[:len(arguments)], params[len(arguments):]
    if any(not p["default"] for p in omitted):
        return "the call omits a parameter without a default"
    for param in chosen:
        if param["type"] not in JAVA:
            return f"unsupported parameter type: {param['name'] or '?'} ({param['type']})"
    return [dict(p, named=any(named)) for p in chosen]


def method_source(method: str, routine: str, key: str, signature: dict, params: list[dict]) -> dict:
    """One typed Java method (and its OUT holder class) for one call shape of a routine."""
    names, java_params, binds = [], [], []
    outs = [(i, p) for i, p in enumerate(params) if "OUT" in p["mode"]]
    for index, param in enumerate(params):
        variable = java_name((param["name"] or "arg" + str(index + 1)).lower())
        while variable in names:
            variable += "_"
        names.append(variable)
        if param["mode"] != "OUT":
            java_params.append(JAVA[param["type"]] + " " + variable)
        if param["mode"] == "IN":
            binds.append(f"DbCalls.in({variable}, {SQL_TYPES[param['type']]})")
        elif param["mode"] == "IN OUT":
            binds.append(f"DbCalls.inOut({variable}, {SQL_TYPES[param['type']]})")
        else:
            binds.append(f"DbCalls.out({SQL_TYPES[param['type']]})")
    named = any(p.get("named") for p in params)
    placeholders = ", ".join((p["name"].upper() + " => ?") if named else "?" for p in params)
    holder, holder_source = None, ""
    if signature["kind"] == "function":
        returns = JAVA[signature["returns"]]
        sql = (f"BEGIN ? := {routine}({placeholders}); END;" if named
               else "{? = call " + routine + (f"({placeholders})" if params else "") + "}")
        call = f"DbCalls.function(jdbc, {jstr(sql)}, {SQL_TYPES[signature['returns']]}" + "".join(", " + b for b in binds) + ")"
        body = f"        return ({returns}) {call};"
    else:
        sql = (f"BEGIN {routine}({placeholders}); END;" if named
               else "{call " + routine + (f"({placeholders})" if params else "") + "}")
        call = f"DbCalls.call(jdbc, {jstr(sql)}" + "".join(", " + b for b in binds) + ")"
        if not outs:
            returns, body = "void", f"        {call};"
        elif len(outs) == 1:
            index, param = outs[0]
            returns = JAVA[param["type"]]
            body = f"        Object[] out = {call};\n        return ({returns}) out[{index}];"
        else:
            holder = method[0].upper() + method[1:] + "Out"
            returns = holder
            fields = "\n".join(f"        public {JAVA[p['type']]} {names[i]};" for i, p in outs)
            holder_source = (f"    /** {routine}: a kimenő (OUT, IN OUT) paraméterek értéke. */\n"
                             f"    static final class {holder} {{\n{fields}\n    }}\n\n")
            sets = "\n".join(f"        result.{names[i]} = ({JAVA[p['type']]}) out[{i}];" for i, p in outs)
            body = (f"        Object[] out = {call};\n        var result = new {holder}();\n{sets}\n        return result;")
    header = f"private {returns} {method}({', '.join(java_params)})"
    types = tuple(p.split(" ")[0] for p in java_params)
    described = ", ".join(("OUT " if p["mode"] == "OUT" else "IN OUT " if p["mode"] == "IN OUT" else "")
                          + (p["name"].upper() or "?") for p in params)
    source = (holder_source + f"    // Adatbázis-rutin: {routine} (schema.json: {key}); az adatbázisban fut, ahogy a Forms-kód hívta.\n"
              f"    {header} {{\n{body}\n    }}\n")
    outs_text = ", ".join(p["name"].upper() for _, p in outs)
    returns_text = ("" if returns == "void" else f": returns {outs_text}" if signature["kind"] == "procedure" and len(outs) == 1
                    else f": returns {holder} with the fields {', '.join(names[i] for i, _ in outs)}" if holder else "")
    return {"method": method, "routine": routine, "key": key, "header": header, "types": types, "source": source,
            "holder": holder, "describe": f"{header.replace('private ', '', 1)} = {routine}({described}){returns_text}"}


def plan(sources: list[dict], procedures: dict, catalog: dict | None = None) -> tuple[list[dict], list[str]]:
    """(typed methods, "NAME: why it has none" for the prompt) of the calls of a task's Forms code.

    sources: the task's code (discovery entries with their calls); procedures: rules.procedure_signatures().
    """
    from .framework import framework_name
    methods, refused, shapes = [], [], {}
    for source in sources:
        for call in source.get("calls", []):
            name = call["name"].upper()
            if call.get("kind") not in CALL_KINDS or name.startswith(STANDARD):
                continue
            if catalog and framework_name(name, catalog):
                continue
            resolved = resolve(name, procedures)
            if resolved is None:
                if "." in name and name + ": no reviewed signature" not in refused:
                    refused.append(name + ": no reviewed signature")
                continue
            key, signature = resolved
            params = call_parameters(call.get("arguments", []), signature)
            if isinstance(params, str):
                if signature["kind"] == "function" and any("OUT" in p["mode"] for p in signature["arguments"]):
                    params = "a function with OUT parameters"
                text = name + ": " + params
                if text not in refused:
                    refused.append(text)
                continue
            if signature["kind"] == "function" and (signature.get("returns") not in JAVA or any("OUT" in p["mode"] for p in params)):
                text = name + ": " + ("a function with OUT parameters" if signature.get("returns") in JAVA
                                      else "unsupported return type: " + str(signature.get("returns")))
                if text not in refused:
                    refused.append(text)
                continue
            shape = (name, tuple((p["name"].upper(), p["mode"], p.get("named", False)) for p in params))
            if shape in shapes:
                continue
            # Overloads per call shape; the same parameter types (or OUT holder) under one name would not compile.
            base = java_name(name.lower().replace(".", "_"))
            method, n = base, 2
            entry = method_source(method, name, key, signature, params)
            while any((m["method"] == entry["method"] and m["types"] == entry["types"])
                      or (entry["holder"] and m["holder"] == entry["holder"]) for m in methods):
                method, n = base + str(n), n + 1
                entry = method_source(method, name, key, signature, params)
            shapes[shape] = entry
            methods.append(entry)
    return methods, refused

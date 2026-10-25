"""Deterministic proposal formatting, with private per-call formatter state.

No model calls, project execution or mutation of the generator's global settings.
Explicit imports/packages in an existing service remain authoritative.
"""
from pathlib import Path

from . import java_style, java_tidy


def format_java(source: str, target: str, config: dict, imports: dict) -> tuple[str, dict]:
    settings = {'enabled': config.get('java_checkstyle_format', True) is not False,
                'import_order': java_style.validate_import_order(
                    config.get('java_import_order') or java_style.DEFAULT_IMPORT_ORDER)}
    failures = []
    try:
        text = java_tidy.tidy(source, Path(target).name, mapping=imports,
                              omit_package=False, preserve_explicit=True)
        text = java_style.layout(text, target, settings=settings, member_types={}, failures=failures)
        if failures:
            return source, {'status': 'manual', 'reason': failures[0]['reason']}
        text = java_tidy.wrap_strings(text)
        return text, {'status': 'formatted' if settings['enabled'] else 'imports_only',
                      'changed': text != source}
    except Exception as exc:
        # Formatting is optional. Unsupported input needs a human, never another prompt.
        return source, {'status': 'manual', 'reason': str(exc)[:500]}

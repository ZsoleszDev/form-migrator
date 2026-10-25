from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import re
from urllib.parse import urlsplit

from frm_forms.cli import configuration
from .models import MigrationOptions

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def env_list(key: str) -> list[str]:
    return [value.strip() for value in os.getenv(key, '').split(',') if value.strip()]


def env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key, 'true' if default else 'false').strip().lower()
    if value not in {'true', 'false', '1', '0'}:
        raise ValueError(f'{key}: true/false vagy 1/0 szükséges.')
    return value in {'true', '1'}


@dataclass
class Settings:
    data_dir: Path
    engine_config: dict = field(default_factory=dict)
    ollama_url: str = "http://xx:11434"
    ollama_model: str = "frm-model"
    port: int = 8000
    max_upload_bytes: int = 32 * 1024 * 1024
    max_json_bytes: int = 1024 * 1024
    # Queued + running jobs. A batch upload waits for free slots (HTTP 429) instead of failing.
    max_pending: int = 50
    job_timeout_seconds: int = 1800
    static_dir: Path = PROJECT_ROOT / "web-dist" / "browser"
    extra_cors_origins: list[str] = field(default_factory=list)
    extra_cors_headers: list[str] = field(default_factory=list)
    cors_allow_credentials: bool = False
    # Web jobs: generated endpoints live at once (MODULE_REVIEWED = true). FRM_BACKEND_LIVE=false: review first.
    backend_live_default: bool = True
    # Deploy into a project (POST .../deploy) and folder browsing: when set, only folders inside these
    # (FRM_PROJECT_ROOTS, comma list).
    project_roots: list[str] = field(default_factory=list)
    # The "Tallózás…" buttons open the operating system's folder dialog on this machine (FRM_FOLDER_DIALOG=false:
    # only the in-page folder browser). folder_dialog_command: replaces the dialog process (tests).
    folder_dialog: bool = True
    folder_dialog_command: list[str] = field(default_factory=list)

    def __post_init__(self):
        for origin in self.extra_cors_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment
                    or any(c.isspace() for c in origin) or '*' in origin
                    or origin != f'{parsed.scheme}://{parsed.netloc}'):
                raise ValueError('FRM_CORS_ORIGINS: pontos http(s) origin kell, útvonal, wildcard és záró / nélkül.')
            _ = parsed.port  # Also reject malformed/out-of-range ports before startup.
        for header in self.extra_cors_headers:
            if header == '*' or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header):
                raise ValueError('FRM_CORS_HEADERS: vesszővel elválasztott fejlécnevek szükségesek, érték és wildcard nélkül.')

    @property
    def cors_origins(self) -> list[str]:
        return sorted({"http://localhost:4200", "http://127.0.0.1:4200",
                       "http://localhost:4201", "http://127.0.0.1:4201",
                       f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}",
                       *self.extra_cors_origins})

    @property
    def cors_headers(self) -> list[str]:
        return sorted({'content-type', 'x-frm-client', 'authorization',
                       *(header.lower() for header in self.extra_cors_headers)})

    def defaults(self) -> MigrationOptions:
        known = set(MigrationOptions.model_fields) - {"ai_think"}
        values = {k: v for k, v in self.engine_config.items() if k in known}
        values["max_ai_calls"] = min(values.get("max_ai_calls", 1), 10)
        think = self.engine_config.get("ai_think")
        values["ai_think"] = "disabled" if think is False else think if think in {"low", "medium", "high"} else "default"
        values['ollama_model'] = self.ollama_model
        values['backend_live'] = self.backend_live_default
        values['screen_window_selection'] = 'ask'  # web jobs: the developer picks the windows to generate
        values['java_empty_package'] = True  # web jobs: the IDE sets the package where the files are copied
        return MigrationOptions(**values)

    def exporter(self) -> dict:
        command = self.engine_config.get("export_command")
        if command:
            return {"status": "configured", "message": "Egyedi Oracle exporter beállítva; a Forms-környezetet az export ellenőrzi."}
        found = shutil.which("frmf2xml") or shutil.which("frmf2xml.bat")
        return {"status": "available" if found else "missing", "message": "A frmf2xml elérhető." if found else "Az FMB-hez Oracle Forms2XML kell. XML-lel azonnal kipróbálható."}

    @classmethod
    def from_env(cls) -> "Settings":
        config_file = os.getenv("FRM_SERVER_CONFIG")
        config = configuration(argparse.Namespace(config=Path(config_file).resolve() if config_file else None, java_package=None, max_ai_calls=None, screen=True))
        return cls(data_dir=Path(os.getenv("FRM_WORK_DIR", str(PROJECT_ROOT / "local-data"))).resolve(), engine_config=config,
                   ollama_url=os.getenv("FRM_OLLAMA_URL", "http://xx:11434").rstrip("/"), ollama_model=os.getenv("FRM_OLLAMA_MODEL", "frm-model"),
                   port=int(os.getenv("FRM_API_PORT", "8000")), job_timeout_seconds=int(os.getenv("FRM_JOB_TIMEOUT", "1800")),
                   max_pending=int(os.getenv("FRM_MAX_PENDING", "50")),
                   extra_cors_origins=env_list('FRM_CORS_ORIGINS'),
                   extra_cors_headers=env_list('FRM_CORS_HEADERS'),
                   cors_allow_credentials=env_bool('FRM_CORS_ALLOW_CREDENTIALS'),
                   backend_live_default=env_bool('FRM_BACKEND_LIVE', True),
                   project_roots=env_list('FRM_PROJECT_ROOTS'),
                   folder_dialog=env_bool('FRM_FOLDER_DIALOG', True))

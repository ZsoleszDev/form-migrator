from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from frm_forms.contracts import COMPANY_DEFAULTS, validate_base_url, validate_awu_azon, validate_common_migrate_tools_package, validate_cl_package
from frm_forms.common import MigrationError


class MigrationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    module: str | None = Field(default=None, max_length=70, pattern=r"^[a-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*$")
    AWU_AZON: str = Field(default="", max_length=30)
    java_package: str = Field(default="hu.company.features", max_length=200, pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    common_migrate_tools_package: str = Field(default="", max_length=200)
    # Where the module's generated CL files go: DPS/WBS import DTOs, Constants and RestClient from here.
    cl_package: str = Field(default="", max_length=200)
    # Web jobs: module Java files without package line; IntelliJ sets it where the files are copied.
    java_empty_package: bool = True
    api_prefix: str = Field(default="/api/forms", max_length=120, pattern=r"^/[A-Za-z0-9/_-]*$")
    angular_selector_prefix: str = Field(default="app", max_length=40, pattern=r"^[a-z][a-z0-9-]*$")
    wbs_base_url: str = Field(default="", max_length=1000)
    dps_base_url: str = Field(default="", max_length=1000)
    ollama_url: str = Field(default="", max_length=1000)
    html_selectors: dict[str, str] = Field(default_factory=lambda: dict(COMPANY_DEFAULTS['html_selectors']))
    form_block_types: dict[str, str] = Field(default_factory=lambda: dict(COMPANY_DEFAULTS['form_block_types']))
    form_block_structure_type: str = Field(default="FormBlock.Structure", max_length=120)
    form_block_columns: str = Field(default="6", pattern=r"^(?:[1-9]|1[0-9]|2[0-4])$")
    form_block_checkbox_boolean: bool = True
    emit_imports: bool = False
    layout_columns: Literal[12, 18, 24] = 12
    optimus_import_path: str = Field(default="", max_length=240)
    optimus_form_block_symbol: str = Field(default="", max_length=120)
    form_block_type_import_path: str = Field(default="", max_length=240)
    environment_import_path: str = Field(default="", max_length=240)
    table_import_path: str = Field(default="", max_length=240)
    table_symbol: str = Field(default="", max_length=120)
    calendar_blocks: list[str] = Field(default_factory=list, max_length=100)
    table_blocks: list[str] = Field(default_factory=list, max_length=100)
    table_bindings: dict[str, str] = Field(default_factory=lambda: dict(COMPANY_DEFAULTS['table_bindings']))
    endpoint_names: dict[str, str] = Field(default_factory=lambda: dict(COMPANY_DEFAULTS['endpoint_names']))
    ai_mode: Literal["off", "assist", "cached"] = "off"
    max_ai_calls: int = Field(default=1, ge=0, le=10, strict=True)
    ai_num_ctx: int = Field(default=2048, ge=1024, le=8192, strict=True)
    ai_num_predict: int = Field(default=256, ge=64, le=2048, strict=True)
    ai_timeout_seconds: int = Field(default=120, ge=1, le=600, strict=True)
    ai_max_source_chars: int = Field(default=1200, ge=100, le=8000, strict=True)
    ai_max_prompt_bytes: int = Field(default=3200, ge=500, le=16000, strict=True)
    ai_think: Literal["default", "low", "medium", "high", "disabled"] = "default"
    ai_cache_salt: str = Field(default="1", min_length=1, max_length=80)
    ollama_model: str = Field(default="frm-model", min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_./:@-]+$")
    strict: bool = False
    generation_mode: Literal["strict", "scaffold", "screen"] = "screen"
    screen_tab_layout: Literal['tabs', 'accordion'] = 'tabs'
    screen_infer_widgets: bool = True
    screen_repair_display_text: bool = True
    screen_row_tolerance: float = Field(default=0.25, ge=0, le=0.5, allow_inf_nan=False)
    screen_preserve_gaps: bool = False
    screen_primary_window: str = Field(default='', max_length=240)
    # 'ask': a form with several windows stops once and the UI asks which ones to generate.
    screen_window_selection: Literal['all', 'ask'] = 'ask'
    # Survey runs: several possible main windows -> the first one instead of a question.
    screen_primary_window_auto: bool = False
    screen_windows: list[str] = Field(default_factory=list, max_length=50)
    screen_button_label_property: Literal['labelText', 'btnLabel'] = 'labelText'
    screen_fold_list_buttons: bool = True
    screen_spacer_type: Literal['text', 'label', 'divider'] = 'label'
    # Generated endpoints live at once: MODULE_REVIEWED = true and writes allowed unless schema.json forbids them.
    backend_live: bool = True
    backend_lov_endpoints: bool = True
    # The module's own folders in the project (chosen before the generation): CL / DPS / WBS / frontend -> full
    # path. The Java folders give the packages (cl_package, dps_package, wbs_package); the deploy writes there.
    project_layout: dict[str, str] = Field(default_factory=dict, max_length=4)

    @field_validator("project_layout", mode="before")
    @classmethod
    def valid_layout(cls, value):
        from pathlib import Path
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("project_layout: {\"CL\": \"…\", \"DPS\": \"…\", \"WBS\": \"…\", \"frontend\": \"…\"} objektum kell.")
        result = {}
        for key, folder in value.items():
            if key not in ("CL", "DPS", "WBS", "frontend") or not isinstance(folder, str):
                raise ValueError("project_layout: csak CL, DPS, WBS és frontend mappa adható meg.")
            folder = folder.strip()
            if not folder:
                continue
            if len(folder) > 1000 or not Path(folder).expanduser().is_absolute():
                raise ValueError(f"project_layout: a(z) {key} mappát teljes útvonallal add meg.")
            result[key] = folder
        return result

    @field_validator("AWU_AZON", mode="before")
    @classmethod
    def awu_azon(cls, value):
        try:
            return validate_awu_azon(value)
        except MigrationError as error:
            raise ValueError(str(error)) from error

    @field_validator("cl_package", mode="before")
    @classmethod
    def valid_cl_package(cls, value):
        return validate_cl_package(value if value is not None else "")

    @field_validator("common_migrate_tools_package", mode="before")
    @classmethod
    def common_tools_package(cls, value):
        try:
            return validate_common_migrate_tools_package(value)
        except MigrationError as error:
            raise ValueError(str(error)) from error

    @field_validator("wbs_base_url", "dps_base_url")
    @classmethod
    def urls(cls, value):
        return validate_base_url(value, 'API URL')

    @field_validator("ollama_url")
    @classmethod
    def ollama_base(cls, value):
        from frm_forms.ollama import base_url
        return base_url(value) if value else ""

    @field_validator("module", mode="before")
    @classmethod
    def empty_module(cls, value):
        return None if value == "" else value

    def engine_overrides(self) -> dict:
        result = self.model_dump(exclude={"module", "ai_mode", "ollama_model", "ollama_url", "strict", "ai_think", "generation_mode"})
        if self.ai_think != "default":
            result["ai_think"] = False if self.ai_think == "disabled" else self.ai_think
        else:
            result["ai_think"] = None
        return result


class JobAnswer(BaseModel):
    """The user's decision for a job waiting in needs_input."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    screen_primary_window: str | None = Field(default=None, min_length=1, max_length=240)
    # Answer of a 'windows' question: the windows to generate (at least one).
    screen_windows: list[str] | None = Field(default=None, min_length=1, max_length=50)


class OllamaConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=1000)
    model: str = Field(pattern=r"^[A-Za-z0-9_./:@-]{1,120}$")


class DeployRequest(BaseModel):
    """Deploy a job's (or a batch's) generated files into the developer's project: CL, DPS, WBS, frontend."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # The main project folder: the parts not chosen in layout are found in it. Optional.
    project: str | None = Field(default=None, max_length=1000)
    # The chosen folder of a part: CL / DPS / WBS / frontend -> full path (or relative to the project).
    layout: dict[str, str] = Field(default_factory=dict, max_length=4)
    dry_run: bool = True
    force: bool = False

    @model_validator(mode="after")
    def some_folder(self):
        self.project = (self.project or "").strip() or None
        self.layout = {key: value.strip() for key, value in self.layout.items() if value.strip()}
        if any(len(value) > 1000 for value in self.layout.values()):
            raise ValueError("Túl hosszú mappaútvonal.")
        if not self.project and not self.layout:
            raise ValueError("Add meg a fő projektmappát, vagy legalább egy rész (CL, DPS, WBS, frontend) mappáját.")
        return self


class FolderRequest(BaseModel):
    """The subfolders of a folder for the in-page folder browser; no path: the drives and the home folder."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str | None = Field(default=None, max_length=1000)


class PickRequest(BaseModel):
    """Open the operating system's folder dialog on this machine."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="Mappa kiválasztása", min_length=1, max_length=120)
    initial: str | None = Field(default=None, max_length=1000)

"""The platform's own public actions and streams, declared like a module's. People, modules, AI and running code
all use these same ones. ``host.platform.services`` holds the running services the handlers delegate to."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from pydantic import Field
from talyx_sdk.declare import EffectBranches, FieldPath, Model, Module

from .. import changes
from . import services as s

module = Module("platform")
Json = Any
WorkspaceRef = Annotated[str, Field(min_length=1, max_length=200)]
WorkspaceOrGlobal = Annotated[str, Field(min_length=1, max_length=200,
                                         description='An open workspace\'s id, or "global"')]


class Empty(Model):
    pass


# ── modules ──────────────────────────────────────────────────────────────────

class ModuleState(Model):
    id: str
    name: str
    version: str
    description: str
    source: Literal["official", "installed"]
    enabled: bool
    state: Literal["starting", "ready", "failed", "stopped"]
    problem: str | None
    pid: int | None = Field(description="Process ID of the connected module interpreter, or null when disconnected.")


class ModuleDefinition(ModuleState):
    manifest: dict[str, Json] = Field(description="talyx.module.json (version 5)")
    contract: dict[str, Json] = Field(description="public/contract.json, generated from the module's code")
    digest: str
    assetBase: str = Field(description="Where the module's files are read (its frontend entry is under it)")


class ModuleId(Model):
    id: str


class InstallInput(Model):
    path: str = Field(description="A folder holding talyx.module.json, or a zip file of one")


@module.action("modules.list", effects=["read"], examples=[{}])
async def modules_list(input: Empty, call) -> list[ModuleState]:
    """Installed modules, whether each is enabled and running, and why one could not start."""
    return s.HOST.modules_list()


class DefinitionsQuery(Model):
    module: str | None = None


@module.action("modules.definitions", effects=["read"], examples=[{}])
async def modules_definitions(input: DefinitionsQuery, call) -> list[ModuleDefinition]:
    """Every installed module's manifest, contract and state, for the window to load module frontends."""
    return [item for item in s.HOST.definitions() if input.module is None or item['id'] == input.module]


@module.action("modules.enable", effects=["write"], callers=["user", "ai"], examples=[{"id": "example"}])
async def modules_enable(input: ModuleId, call) -> ModuleState:
    """Enable a module and start it."""
    return await s.HOST.enable(input.id)


@module.action("modules.disable", effects=["write"], callers=["user", "ai"], examples=[{"id": "example"}])
async def modules_disable(input: ModuleId, call) -> ModuleState:
    """Stop and disable a module. Refused while another enabled module requires it."""
    return await s.HOST.disable(input.id)


@module.action("modules.reload", effects=["write"], callers=["user", "ai"], examples=[{"id": "example"}])
async def modules_reload(input: ModuleId, call) -> ModuleState:
    """Regenerate a module's contract from its code and restart only that module."""
    return await s.HOST.reload(input.id)


@module.action("modules.install", effects=["write", "code"], callers=["user", "ai"], examples=[{"path": "C:/Modules/example"}])
async def modules_install(input: InstallInput, call) -> ModuleState:
    """Install a module from a folder or a zip file, prepare its Python environment if it needs extra packages,
    and start it."""
    return await s.HOST.install(input.path)


@module.action("modules.uninstall", effects=["delete"], callers=["user", "ai"], examples=[{"id": "example"}])
async def modules_uninstall(input: ModuleId, call) -> Empty:
    """Stop and remove an installed module and its environment."""
    await s.HOST.uninstall(input.id)
    return {}


# ── catalog ──────────────────────────────────────────────────────────────────

class PointInput(Model):
    point: str = Field(description="owner.name of an extension point")


class Implementation(Model):
    moduleId: str
    id: str
    descriptor: dict[str, Json]


@module.action("catalog.implementations", effects=["read"], examples=[{"point": "platform.settings_section"}])
async def catalog_implementations(input: PointInput, call) -> list[Implementation]:
    """Implementations of an extension point by enabled modules."""
    return s.HOST.catalog.implementations(input.point)


# ── workspaces ───────────────────────────────────────────────────────────────

class OpenInput(Model):
    workspaceId: WorkspaceRef | None = None
    """An open workspace to show again, or a closed one to open again under its id."""
    snapshotId: str | None = None
    """A saved workspace: the workspace it is a saved state of opens again."""
    name: str | None = None
    presentation: Json = None
    """What the window keeps for the workspace while it is open (its unsaved layout)."""


class OpenRecord(Model):
    workspaceId: str
    name: str | None
    snapshotId: str | None
    presentation: Json = None


class WorkspaceInput(Model):
    workspaceId: WorkspaceRef


class CopyInput(Model):
    workspaceId: WorkspaceRef
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class FolderOutput(Model):
    workspaceId: str
    path: str


class SetFolderInput(Model):
    workspaceId: WorkspaceRef
    path: str | None


class ActivityRef(Model):
    """One activity: its id is the module's own, so the module is part of who it is."""

    module: str
    id: str


class PanelTarget(Model):
    module: str
    panelType: str
    objectId: str


class Activity(ActivityRef):
    title: str
    origin: Literal["user", "ai", "module", "code"] = Field(description="Who asked for it")
    callId: str | None = None
    openTarget: PanelTarget | None = None
    progress: Json = None


class ActivityEnded(Model):
    workspaceId: str
    module: str
    activityId: str
    callId: str | None
    openTarget: PanelTarget | None
    result: Literal["completed", "failed", "stopped"]
    summary: Json = None


module.event("activity.ended", ActivityEnded, "Registered background work ended in its workspace.")


class ActivityStop(Model):
    workspaceId: WorkspaceRef
    activities: list[ActivityRef] = Field(min_length=1, description="Activities of activity.list")


class ActivityList(Model):
    workspaceId: str
    activities: list[Activity]


class WorkspaceChange(Model):
    """A workspace opened, closed, was copied, or had its saved state written or renamed."""

    kind: Literal["opened", "closed", "copied", "saved"]
    workspaceId: str
    sourceWorkspaceId: str | None = None
    snapshotId: str | None = None
    name: str | None = None
    revision: str | None = None


class Shown(Model):
    workspaceId: WorkspaceRef
    moduleInstances: dict[str, int]
    """How many panels of each module the window holds."""


class WindowOf(Model):
    workspaceId: str
    name: str | None
    snapshotId: str | None
    sessionId: str | None
    """The window showing it, if one does."""
    moduleInstances: dict[str, int]


class Windows(Model):
    workspaces: list[WindowOf]


class TileSummary(Model):
    module: str
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    w: Annotated[float, Field(gt=0, le=1)]
    h: Annotated[float, Field(gt=0, le=1)]


class LayoutSummary(Model):
    """Each panel's module and its place, as shares of the workspace: a saved workspace's thumbnail."""
    tiles: Annotated[list[TileSummary], Field(max_length=64)]


class SaveInput(Model):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    workspace: dict[str, Json]
    """The window's layout and panel states (at most 4 MB)."""
    summary: LayoutSummary | None = None
    snapshotId: str | None = None
    """Save over this saved workspace (with the revision read); left out for the first save."""
    expectedRevision: str | None = None


class Saved(Model):
    snapshotId: str
    workspaceId: str
    savedAt: str
    revision: str


class Copied(Saved):
    name: str


class SnapshotId(Model):
    snapshotId: str


class DeleteInput(Model):
    snapshotId: str
    expectedRevision: str | None = None


class SavedInfo(Model):
    snapshotId: str
    workspaceId: str
    name: str
    savedAt: str
    revision: str
    summary: LayoutSummary | None


class SavedWorkspace(SavedInfo):
    workspace: dict[str, Json]


class SavedList(Model):
    saved: list[SavedInfo]
    unreadable: list[str]


@module.action("workspace.open", effects=["write"], examples=[{"name": "Research"}])
async def workspace_open(input: OpenInput, call) -> OpenRecord:
    """Open a workspace: a new one, one by its workspaceId, or a saved one by snapshotId; the window shows it."""
    return await s.HOST.workspaces.open(input.model_dump(exclude_unset=True))


@module.action("workspace.list_open", effects=["read"], examples=[{}])
async def workspace_list_open(input: Empty, call) -> list[OpenRecord]:
    """Open workspaces, most recently opened last."""
    return s.HOST.workspaces.list_open()


@module.action("workspace.close", effects=["write", "delete"], examples=[{"workspaceId": "research"}])
async def workspace_close(input: WorkspaceInput, call) -> Empty:
    """Close a workspace: everything running for it (listed by activity.list) is stopped first, for real. A workspace
    never saved is deleted as workspace.delete deletes one."""
    await s.HOST.close_workspace(input.workspaceId)
    return {}


@module.action("workspace.copy", effects=["write"], examples=[{"workspaceId": "research", "name": "Research copy"}])
async def workspace_copy(input: CopyInput, call) -> Copied:
    """Copy a saved workspace, open or not: the copy is saved with the same layout under the name given (or
    "<name> copy"), and each module copies what it keeps for the workspace. The copy gets its own copy of the folder
    TALYX made for the workspace; a folder the user bound is shared, not copied. The copy is not opened."""
    return await s.HOST.copy_workspace(input.workspaceId, input.name)


@module.action("workspace.folder", effects=["read"], examples=[{"workspaceId": "research"}])
async def workspace_folder(input: WorkspaceInput, call) -> FolderOutput:
    """The workspace's folder: its files, which modules read directly."""
    return await s.HOST.workspaces.folder(input.workspaceId)


@module.action("workspace.set_folder", effects=["write"], callers=["user"])
async def workspace_set_folder(input: SetFolderInput, call) -> FolderOutput:
    """Bind the workspace to an existing folder (the user's choice), or back to its own."""
    return await s.HOST.workspaces.set_folder(input.workspaceId, input.path)


@module.action("workspace.shown", effects=["write"], callers=["user"])
async def workspace_shown(input: Shown, call) -> Empty:
    """(The window) which open workspace this window shows and how many panels of each module it holds."""
    s.HOST.workspaces.show(call.caller["session"], input.workspaceId, input.moduleInstances)
    return {}


@module.action("workspace.save", effects=["write"], examples=[{"name": "Research", "workspace": {"tiles": []}}])
async def workspace_save(input: SaveInput, call) -> Saved:
    """Save the calling workspace's state (the window's layout and panels) under a name; a workspace has one saved
    state, saved over with snapshotId and the revision read. Saved from no workspace, it is a new workspace's."""
    return await s.HOST.workspaces.save(input.model_dump(exclude_unset=True), call.caller["workspaceId"])


@module.action("workspace.load", effects=["read"], examples=[{"snapshotId": "saved-research"}])
async def workspace_load(input: SnapshotId, call) -> SavedWorkspace:
    """A saved workspace's state."""
    return await s.HOST.workspaces.load(input.snapshotId)


@module.action("workspace.list", effects=["read"], examples=[{}])
async def workspace_list(input: Empty, call) -> SavedList:
    """Saved workspaces, most recently saved first."""
    return await s.HOST.workspaces.list_saved()


@module.action("workspace.delete_preview", effects=["read"], examples=[{"workspaceId": "research"}])
async def workspace_delete_preview(input: WorkspaceInput, call) -> list[str]:
    """List what deleting this workspace would remove: the modules' contents, then the folder TALYX made for it."""
    return await s.HOST.deletion_preview(input.workspaceId)


@module.action("workspace.delete", effects=["delete"], examples=[{"snapshotId": "saved-research"}])
async def workspace_delete(input: DeleteInput, call) -> Empty:
    """Delete a saved workspace (with the revision read, so a newer save is not lost).
    What the modules keep for it is deleted; the folder TALYX made for it goes to the Recycle Bin, one the user bound
    stays."""
    await s.HOST.workspaces.delete(input.snapshotId, input.expectedRevision, s.HOST.delete_workspace_contents)
    return {}


@module.stream("workspace.changes")
async def workspace_changes(params: Empty, call) -> AsyncIterator[WorkspaceChange]:
    """Workspaces opening, closing and being copied, from now on."""
    async for change in s.HOST.workspaces.changes():
        yield change


@module.stream("workspace.windows")
async def workspace_windows(params: Empty, call) -> AsyncIterator[Windows]:
    """Every open workspace with the window showing it: now, then again whenever that changes."""
    async for value in s.HOST.workspaces.windows():
        yield value


@module.action("activity.list", effects=["read"], examples=[{"workspaceId": "research"}])
async def activity_list(input: WorkspaceInput, call) -> ActivityList:
    """What runs for a workspace and would stop if it closed."""
    return {"workspaceId": input.workspaceId, "activities": s.HOST.activities(input.workspaceId)}


@module.stream("activity.changes")
async def activity_changes(params: Empty, call) -> AsyncIterator[Empty]:
    """Read activity.list on the first item, then again when work starts, ends or changes in a workspace."""
    async for change in s.HOST.activity_changes():
        yield change


@module.action("activity.stop", effects=["write"], examples=[{"workspaceId": "research", "activities": [{"module": "example", "id": "work-1"}]}])
async def activity_stop(input: ActivityStop, call) -> Empty:
    """Stop some of what runs for a workspace (for example everything AI asked for); each is ended for real."""
    await s.HOST.stop_activities(input.workspaceId, [(item.module, item.id) for item in input.activities])
    return {}


# ── panels: the window's ─────────────────────────────────────────────────────

class PanelOpen(Model):
    type: str = Field(description="module.panel")
    state: Json = None


class PanelId(Model):
    panelId: Annotated[str, Field(min_length=1)]


class Panel(Model):
    panelId: str
    type: str
    """module.panel"""
    active: bool
    visible: bool
    view: Json = None
    """What the panel reports it shows (its module's own words), if it reports anything."""


class Panels(Model):
    panels: list[Panel]


class PanelCapture(Model):
    path: str
    bytes: int
    sha256: str


@module.action("panels.capture", effects=["read"], examples=[{"panelId": "panel-1"}],
               errors=["ui_required", "target_gone", "unavailable"])
async def panels_capture(input: PanelId, call) -> PanelCapture:
    """Capture a laid-out panel in its desktop workspace window. Returns a PNG file reference kept for 24 hours."""
    return await s.HOST.window_call(call.caller, "capture_panel", input.model_dump())


@module.action("panels.open", effects=["write"], examples=[{"type": "example.main"}])
async def panels_open(input: PanelOpen, call) -> PanelId:
    """To open a module, call platform.panels.open with {"type": "module.panel"} using the exact type listed below.
    For a particular object, use platform.panels.open_target with module, panelType (the part after the dot)
    and the object's openTarget objectId."""
    return await s.HOST.window_call(call.caller, "open_panel", input.model_dump(exclude_unset=True))


@module.action("panels.open_target", effects=["write"], examples=[{"module": "example", "panelType": "main", "objectId": "item-1"}])
async def panels_open_target(input: PanelTarget, call) -> PanelId:
    """Open an object in its module's panel (an open target, such as one object of that module)."""
    return await s.HOST.window_call(call.caller, "open_target", input.model_dump())


@module.action("panels.activate", effects=["write"], examples=[{"panelId": "panel-1"}])
async def panels_activate(input: PanelId, call) -> Empty:
    """Bring a panel of the workspace's window to the front."""
    return await s.HOST.window_call(call.caller, "activate_panel", input.model_dump())


@module.action("panels.close", effects=["write"], errors=["conflict", "cancelled", "target_gone"], examples=[{"panelId": "panel-1"}])
async def panels_close(input: PanelId, call) -> Empty:
    """Close a panel in the workspace window. A busy panel can refuse with conflict; declining its confirmation
    returns cancelled. The panel stays open in both cases."""
    return await s.HOST.window_call(call.caller, "close_panel", input.model_dump())


@module.action("panels.list", effects=["read"], examples=[{}])
async def panels_list(input: Empty, call) -> Panels:
    """Panels in the workspace's window, with what each reports it shows."""
    return await s.HOST.window_call(call.caller, "list_panels", {})



# ── data the platform keeps for modules ──────────────────────────────────────

class Directories(Model):
    dataDir: str = Field(description="The module's own data directory")
    userDir: str = Field(description="The module's folder among the user's files")
    environmentsDir: str = Field(description="The Python environments every module shares (the SDK's uv tool keeps "
                                 "them there, with who holds each)")
    modulesDir: str = Field(description="Where the modules the user installs or writes live; a module folder made "
                            "here is installed where it is")


class SecretKey(Model):
    key: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")]


class SecretValue(Model):
    value: str | None


class SecretWrite(Model):
    key: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")]
    value: Annotated[str, Field(max_length=2500)] | None


class Configured(Model):
    configured: bool


@module.action("storage.directories", effects=["read"], callers=["module"])
async def storage_directories(input: Empty, call) -> Directories:
    """The calling module's data directory and user folder."""
    return s.HOST.directories(s.HOST.calling_module(call.caller))


@module.action("secrets.get", effects=["read"], callers=["module"])
async def secrets_get(input: SecretKey, call) -> SecretValue:
    """One of the calling module's secrets. Backends only: windows and AI learn whether one is configured."""
    return {"value": await s.HOST.secret(call.caller, input.key)}


@module.action("secrets.set", effects=["write"], callers=["module"])
async def secrets_set(input: SecretWrite, call) -> Empty:
    """Store (or with null, delete) one of the calling module's secrets."""
    await s.HOST.set_secret(call.caller, input.key, input.value)
    return {}


@module.action("secrets.configured", effects=["read"], callers=["module"])
async def secrets_configured(input: SecretKey, call) -> Configured:
    """Whether one of the calling module's secrets is set."""
    return {"configured": await s.HOST.secret_configured(call.caller, input.key)}


# ── settings ─────────────────────────────────────────────────────────────────

class SettingsRead(Model):
    module: str = Field(description='The module whose settings these are ("platform" for General)')
    keys: list[str] | None = None
    workspaceId: WorkspaceOrGlobal


class SettingValue(Model):
    value: Json
    source: Literal["default", "global", "workspace"]
    globalOnly: bool


class SettingsValues(Model):
    module: str
    workspaceId: str
    values: dict[str, SettingValue]


class SettingsWrite(Model):
    module: str
    workspaceId: WorkspaceOrGlobal
    values: dict[str, Json]


class SettingsReset(Model):
    module: str
    workspaceId: WorkspaceRef
    keys: list[str]


class SettingsChange(Model):
    module: str
    workspaceId: str
    keys: list[str]


# The platform's own settings: General in the settings window.
module.setting("language", Literal["en", "zh-CN"], "en", title="Language", global_only=True, group="Appearance",
               options={"en": "English", "zh-CN": "简体中文"},
               description="The language of TALYX's words. Windows reload to change it; unsaved work is kept.")
module.setting("checkForUpdates", bool, True, title="Tell me when a new version is out", global_only=True, group="Updates",
               description="The desktop app asks GitHub for the latest release when it starts and once a day. Nothing "
               "about you is sent.")
module.setting("upColor", Literal["green-up", "red-up"], "green-up", title="Up color", group="Appearance",
               description="Which color means up for prices, P&L, buy and sell, everywhere in TALYX.")
module.setting("launchOnLogin", bool, False, title="Start TALYX when you sign in", global_only=True, group="Desktop",
               description="Works in the TALYX desktop app.")
module.setting("keepAwakeWhileWatching", bool, False, title="Keep the computer awake while work is running",
               global_only=True, group="Desktop",
               description="Only while an open workspace has work running. The screen can still turn off. Works in the "
               "TALYX desktop app.")


@module.action("settings.read", effects=["read"], examples=[{"module": "platform", "workspaceId": "global", "keys": ["upColor"]}])
async def settings_read(input: SettingsRead, call) -> SettingsValues:
    """Settings as they apply in a workspace (its own value, else the global one, else the default), or the global
    ones with workspaceId "global"."""
    return s.HOST.settings.read(input.module, input.workspaceId, input.keys)


@module.action("settings.write", effects=["write"], examples=[{"module": "platform", "workspaceId": "global", "values": {"upColor": "green-up"}}])
async def settings_write(input: SettingsWrite, call) -> SettingsValues:
    """Set settings globally or for one workspace only. Global-only settings cannot be set for a workspace."""
    return await s.HOST.settings.write(input.module, input.workspaceId, input.values)


@module.action("settings.reset", effects=["write"], examples=[{"module": "platform", "workspaceId": "research", "keys": ["upColor"]}])
async def settings_reset(input: SettingsReset, call) -> SettingsValues:
    """Make a workspace follow the global value again for these settings."""
    return await s.HOST.settings.reset(input.module, input.workspaceId, input.keys)


# ── updates ──────────────────────────────────────────────────────────────────

class Release(Model):
    version: str
    url: str = Field(description="The release page, with the installer")
    notes: str = Field(description="What changed, as the release says (Markdown)")
    publishedAt: str | None
    installer: bool = Field(description="Whether the release has a Windows installer with a published SHA-256, so TALYX can download and install it itself")


class UpdateDownload(Model):
    version: str
    state: Literal["downloading", "ready", "failed"] = Field(description="ready: downloaded and its SHA-256 matches the release's")
    received: int = Field(description="Bytes downloaded so far")
    total: int | None = Field(description="The installer's size in bytes, or null when unknown")
    problem: str | None = Field(description="Why it failed (a checksum that does not match deletes the download), or null")
    path: str | None = Field(description="The checked installer on this machine, once ready")


class ReleaseNotes(Model):
    version: str = Field(description="This installation's version")
    notes: str = Field(description="What changed in it, as its release says (Markdown)")


class UpdateState(Model):
    current: str = Field(description="This installation's version")
    latest: Release | None = Field(description="A newer published version, or null when none is known")
    checkedAt: int | None = Field(description="When GitHub was last asked (ms), or null")
    problem: str | None = Field(description="Why the last check (or the last update's installer) failed, or null")
    download: UpdateDownload | None = Field(description="The newer version's installer being downloaded, or null")
    notes: ReleaseNotes | None = Field(description="This version's notes, until they have been seen once after it was installed; null otherwise")


@module.action("updates.read", effects=["read"], examples=[{}])
async def updates_read(input: Empty, call) -> UpdateState:
    """This installation's version and the newer published one, if the last check found one."""
    return s.HOST.updates.state


@module.action("updates.check", effects=["external"], examples=[{}])
async def updates_check(input: Empty, call) -> UpdateState:
    """Ask GitHub for the latest published version now."""
    return await s.HOST.updates.check()


@module.action("updates.open", effects=["external"], examples=[{}])
async def updates_open(input: Empty, call) -> Empty:
    """Open the newer version's release page (with its installer) in the default browser."""
    s.HOST.updates.open_download()
    return {}


@module.action("updates.download", effects=["external", "write"], examples=[{}])
async def updates_download(input: Empty, call) -> UpdateState:
    """Download the newer version's installer and check its SHA-256 against the release's; returns at once, the progress
    follows on updates.changes. The desktop installs it when the user asks (it quits, installs and opens again)."""
    return await s.HOST.updates.download()


@module.action("updates.notes_seen", effects=["write"], examples=[{}])
async def updates_notes_seen(input: Empty, call) -> UpdateState:
    """This version's notes have been read: they are not shown again."""
    return s.HOST.updates.notes_seen()


@module.stream("updates.changes")
async def updates_changes(params: Empty, call) -> AsyncIterator[UpdateState]:
    """The update state now, then after each check, download step and change."""
    async for state in s.HOST.updates.changes():
        yield state


@module.stream("settings.changes")
async def settings_changes(params: Empty, call) -> AsyncIterator[SettingsChange]:
    """Settings that changed (a global change is reported once with workspaceId "global")."""
    async for change in s.HOST.settings.changes():
        yield change


# ── notifications ────────────────────────────────────────────────────────────

ChannelKind = Literal["telegram", "feishu", "discord"]


class ChannelSettings(Model):
    chatId: Annotated[str, Field(min_length=1)] | None = None
    """Telegram only: the chat the bot writes to."""


class ChannelCredentials(Model):
    token: Annotated[str, Field(min_length=1)] | None = None
    webhook: Annotated[str, Field(min_length=1)] | None = None
    secret: Annotated[str, Field(min_length=1)] | None = None


class LastDelivery(Model):
    producer: str
    notificationId: str
    status: str
    error: str | None
    updatedAt: str


class Channel(Model):
    id: str
    kind: ChannelKind
    name: str
    enabled: bool
    revision: int
    configured: bool
    """Whether its credentials are stored (they are never read back)."""
    settings: dict[str, str]
    credentialMask: str | None
    lastDelivery: LastDelivery | None


class ChannelSave(Model):
    operation: Literal["save"]
    channelId: str | None
    """None for a new channel."""
    expectedRevision: int | None
    kind: ChannelKind
    name: Annotated[str, Field(min_length=1)]
    enabled: bool
    settings: ChannelSettings
    credentials: ChannelCredentials | None = None
    """Left out to keep the stored ones."""


class ChannelDelete(Model):
    operation: Literal["delete"]
    channelId: str
    expectedRevision: int


class ChannelId(Model):
    channelId: Annotated[str, Field(min_length=1)]


class Navigation(Model):
    workspaceId: Annotated[str, Field(min_length=1)]
    panel: Annotated[str, Field(min_length=1)]
    """A panel type of the sender (``module.panel``)."""
    state: dict[str, Json]


class NotificationSend(Model):
    notificationId: Annotated[str, Field(min_length=1)]
    """Chosen by the sender; sending the same id and content again is answered with the first delivery."""
    channelIds: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(max_length=20_000)]
    navigation: Navigation | None = None


class ChannelDelivery(Model):
    channelId: str
    status: Literal["pending", "sending", "sent", "failed", "unknown", "cancelled"]
    attempts: int
    createdAt: str
    updatedAt: str
    error: str | None
    truncated: bool


class Delivery(Model):
    producer: str
    notificationId: str
    deliveries: list[ChannelDelivery]


class NotificationId(Model):
    notificationId: Annotated[str, Field(min_length=1)]


class Toast(Model):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    message: Annotated[str, Field(max_length=4000)]
    navigation: Navigation | None = None
    """Where clicking it leads (notifications.activated reports the click)."""


class Activated(Model):
    id: str
    navigation: Navigation


@module.action("notifications.channels", effects=["read"], examples=[{}])
async def notifications_channels(input: Empty, call) -> list[Channel]:
    """The user's push channels (Telegram, Feishu, Discord); credentials are never returned."""
    return await s.HOST.notifications.channels()


@module.action("notifications.configure", effects=["write"], callers=["user"])
async def notifications_configure(input: ChannelSave | ChannelDelete, call) -> list[Channel]:
    """Add, change or delete a push channel (the user only)."""
    return await s.HOST.notifications.configure(input.model_dump())


@module.action("notifications.test", effects=["external"], callers=["user", "ai"], examples=[{"channelId": "my-channel"}])
async def notifications_test(input: ChannelId, call) -> Delivery:
    """Send a test message on one channel and wait (up to 25 s) for its receipt."""
    return await s.HOST.notifications.test(input.channelId)


@module.action("notifications.send", effects=["external"], examples=[{"notificationId": "daily-report", "channelIds": ["my-channel"], "title": "Daily report", "body": "Your report is ready."}])  # i18n: ignore (AI contract)
async def notifications_send(input: NotificationSend, call) -> Delivery:
    """Send a notification on configured channels. Delivery is durable and retried; read_delivery reports it."""
    return await s.HOST.send_notification(call.caller, input.model_dump(exclude_none=True))


@module.action("notifications.read_delivery", effects=["read"], examples=[{"notificationId": "daily-report"}])
async def notifications_read_delivery(input: NotificationId, call) -> Delivery:
    """How a notification this module sent was delivered, per channel."""
    return await s.HOST.notifications.read_delivery(s.HOST.calling_module(call.caller), input.notificationId)


@module.action("notifications.toast", effects=["write"], examples=[{"title": "Daily report", "message": "Your report is ready."}])  # i18n: ignore (AI contract)
async def notifications_toast(input: Toast, call) -> Empty:
    """Show a Windows toast (unavailable where Windows cannot show one)."""
    await s.HOST.toast(call.caller, input.model_dump(exclude_none=True))
    return {}


@module.stream("notifications.activated")
async def notifications_activated(params: Empty, call) -> AsyncIterator[Activated]:
    """The user clicked a toast that leads somewhere."""
    async for event in s.HOST.toast_clicks():
        yield event


# ── tasks ────────────────────────────────────────────────────────────────────

class TaskStart(Model):
    action: str


class TaskId(Model):
    taskId: str


class Task(Model):
    taskId: str
    action: str
    module: str
    workspaceId: str | None
    state: Literal["running", "completed", "failed", "cancelled"]
    progress: Json = None
    result: Json = None
    error: dict[str, Json] | None = None


@module.action("tasks.start", effects=["write"], callers=["module"])
async def tasks_start(input: TaskStart, call) -> TaskId:
    """(Module SDKs) record a long task of one of the caller's task actions."""
    return s.HOST.tasks.start(call.caller, input.action)


@module.action("tasks.get", effects=["read"], examples=[{"taskId": "task-1"}])
async def tasks_get(input: TaskId, call) -> Task:
    """A long task's state, progress and, once finished, its result or error."""
    return s.HOST.tasks.get(input.taskId)


@module.action("tasks.cancel", effects=["write"], examples=[{"taskId": "task-1"}])
async def tasks_cancel(input: TaskId, call) -> Task:
    """Cancel a running long task."""
    return await s.HOST.tasks.cancel(input.taskId)


# ── AI ───────────────────────────────────────────────────────────────────────

class Tool(Model):
    name: str = Field(description="The capability name, module.action")
    description: str
    effects: list[str]
    task: bool
    input: dict[str, Json]
    examples: list[dict[str, Json]]
    errors: list[str]
    effectBranches: EffectBranches | None
    target: FieldPath | None = Field(description="Input field naming the object an action without effect branches acts on.")
    summaryPaths: list[list[str]] = Field(default_factory=list, description="Output paths to vocabulary Summary values; * selects array elements.")


class OutputField(Model):
    name: str
    type: str
    unit: str | None = None


class ActionDetail(Tool):
    outputFields: list[OutputField]


class CatalogQuery(Model):
    action: str | None = None
    module: str | None = None
    cursor: Annotated[int, Field(ge=0)] = 0


class GuideInfo(Model):
    name: str
    description: str


class DocInfo(Model):
    topic: str
    title: str = Field(description="What the document helps the reader write.")


class CatalogModule(GuideInfo):
    module: str
    actions: int
    guide: GuideInfo | None
    docs: list[DocInfo] = Field(description="Authoring docs: how to write code the module runs; read one with ai.guide "
                                            "{module, topic}.")


class ModulesPage(Model):
    modules: list[CatalogModule]
    version: int
    nextCursor: int | None


class ToolsPage(Model):
    tools: list[Tool]
    version: int
    nextCursor: int | None


class GuideQuery(Model):
    module: str
    topic: str | None = Field(None, description="One of the module's authoring docs (ai.catalog lists them); without "
                                                "it, the module's guide.")
    cursor: Annotated[int, Field(ge=0)] = 0


class GuidePage(Model):
    text: str
    nextCursor: int | None


class AICall(Model):
    workspaceId: WorkspaceRef
    action: str
    input: Json
    client: str = Field(description="Which AI client is calling (a module acting as AI client, or an external client's name)")
    conversation: str | None = None


class AICallResult(Model):
    callId: str
    result: Json


class CallsInput(Model):
    workspaceId: str | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50


class CallRecord(Model):
    callId: str
    capability: str
    caller: dict[str, Json]
    startedAt: str
    endedAt: str | None
    ok: bool | None
    error: str | None
    summary: str


@module.action("ai.catalog", effects=["read"], examples=[{}, {"module": "platform"}])
async def ai_catalog(input: CatalogQuery, call) -> ModulesPage | ToolsPage | ActionDetail:
    """List modules with their AI guide summaries and authoring docs. Supply a module to read its AI actions, effects,
    inputs and examples.
    When nextCursor is present, pass it as cursor with the same module to continue."""
    if input.action is not None:
        return s.HOST.ai.detail(input.action, input.module)
    return s.HOST.ai.catalog(input.module, input.cursor)


@module.action("ai.guide", effects=["read"], examples=[{"module": "example"}, {"module": "example", "topic": "plugins"}],
               errors=["not_found", "unavailable"])
async def ai_guide(input: GuideQuery, call) -> GuidePage:
    """Read a module's Markdown guide, or with topic one of its authoring docs: how to write code the module runs
    (where the files go, the API, how to save and check them). ai.catalog lists each module's docs. Continue with
    nextCursor."""
    return s.HOST.ai.guide(input.module, input.topic, input.cursor)


@module.action("ai.call", effects=["write", "delete", "code", "external", "funds"], callers=["module"])
async def ai_call(input: AICall, call) -> AICallResult:
    """(An AI client inside TALYX) call an action as AI, in a workspace. The platform runs it and records the call; whether to ask the
    user first is the AI client's decision, from the action's effects."""
    return await s.HOST.ai.call(call.caller, input.model_dump())


@module.action("ai.calls", effects=["read"], examples=[{"limit": 10}])
async def ai_calls(input: CallsInput, call) -> list[CallRecord]:
    """Recent recorded calls by AI and running code."""
    return s.HOST.calls.recent(input.workspaceId, input.limit)


class ChangeCalls(Model):
    callIds: list[str]


class ChangeRun(Model):
    runId: str


class Change(Model):
    callId: str
    objectId: str
    label: str
    openTarget: PanelTarget | None
    kind: Literal["created", "updated", "deleted"]


class RevertChange(Model):
    callId: str
    direction: Literal["undo", "redo"]


@module.action("changes.list", effects=["read"], examples=[{"callIds": ["example-call"]}],
               errors=["not_found", "permission_denied", "unavailable"])
async def changes_list(input: ChangeCalls | ChangeRun, call) -> list[Change]:
    """List objects changed by your recorded calls or a code run in this workspace and conversation."""
    if isinstance(input, ChangeRun):
        return await changes.list_changes(s.HOST, call.caller, [], input.runId)
    return await changes.list_changes(s.HOST, call.caller, input.callIds, None)


@module.action("changes.revert", effects=["write", "delete"],
               examples=[{"callId": "example-call", "direction": "undo"}],
               errors=["not_found", "permission_denied", "unavailable"])
async def changes_revert(input: RevertChange, call) -> Empty:
    """Restore the objects changed by one of your calls to their state before the call (undo) or after it (redo)."""
    await changes.revert(s.HOST, call.caller, input.callId, input.direction)
    return {}


# ── runs: code started by a module ───────────────────────────────────────────

class RunStart(Model):
    workspaceId: str | None
    context: Json = None
    conversation: str | None = Field(None, description="The AI conversation this code acts for: its changes can be "
                                                     "listed and undone from that conversation")


class RunToken(Model):
    runId: str
    token: str
    url: str = Field(description="Where the process sends its calls (TALYX_CALL_URL)")


class RunId(Model):
    runId: str


@module.action("runs.start", effects=["write"], callers=["module"])
async def runs_start(input: RunStart, call) -> RunToken:
    """A token for one run of code this module starts: the run's calls are recorded as code from this module and
    workspace, and the context reaches the handlers it calls unchanged."""
    return s.HOST.runs.start(call.caller, input.workspaceId, input.context, input.conversation)


@module.action("runs.calls", effects=["read"], callers=["module"])
async def runs_calls(input: RunId, call) -> list[CallRecord]:
    """The calls one run made (its starter only)."""
    return await s.HOST.runs.calls(call.caller, input.runId)


@module.action("runs.end", effects=["write"], callers=["module"])
async def runs_end(input: RunId, call) -> Empty:
    """The run is over: its token stops working."""
    s.HOST.runs.end(call.caller, input.runId)
    return {}


class ModulesChanged(Model):
    version: int


@module.stream("modules.changes")
async def modules_changes(params: Empty, call) -> AsyncIterator[ModulesChanged]:
    """Modules being installed, enabled, disabled, started, restarted or failing; read modules.definitions again."""
    async for version in s.HOST.module_changes():
        yield {"version": version}


# ── places in the window other modules put things (descriptor only) ─────────
# A module's items keep its manifest's order; the item's id is the manifest entry's.

class SettingsSection(Model):
    title: Annotated[str, Field(min_length=1)]
    view: Annotated[str, Field(min_length=1)]
    """The module's own view (its name in the manifest's views) drawn as the section."""
    workspace: bool = False
    """Also show this section in This workspace and pass the selected scope to the view."""


class StatusItem(Model):
    title: Annotated[str, Field(min_length=1)]
    icon: Annotated[str, Field(min_length=1)]
    action: str | None = None
    """The module's window action run when it is clicked."""


class NotificationSection(Model):
    title: Annotated[str, Field(min_length=1)]
    view: Annotated[str, Field(min_length=1)]
    """The module's own view (its name in the manifest's views)."""


module.extension_point("settings_section", description="A section of the settings window drawn by a module's view.",
                       descriptor=SettingsSection)
module.extension_point("status_item", description="An item in the window's status bar.", descriptor=StatusItem)
module.extension_point("notification_section", description="A module's section of the notification settings.",
                       descriptor=NotificationSection)

"""Inputs to Agent's public operations. Each branch accepts only its own fields."""
from typing import Annotated, Any, Literal

from pydantic import Field
from talyx_sdk.declare import Model

from vocabulary.python.types import FileRef

from .model_settings import ConnectionFields, ModelConfig

Text = Annotated[str, Field(min_length=1)]
Page = Annotated[int, Field(ge=0)]
Scope = Literal["global", "workspace"]
Kind = Literal["general", "chat", "bot"]


class OperationResult(Model):
    result: Any
    """The selected operation's result. Commands without a return value give null."""


class ResultFile(Model):
    file: FileRef
    """A complete JSON snapshot of the result, retained for at least 24 hours."""


class ListChats(Model):
    op: Literal["list"]
    offset: Page = 0


class SearchChats(Model):
    op: Literal["search"]
    query: Text


class CreateChat(Model):
    op: Literal["create"]
    title: Annotated[str, Field(min_length=1, max_length=160)] = "New conversation"
    modelId: Text | None = None


class ReadChat(Model):
    op: Literal["read"]
    conversationId: Text
    before: float | None = None


class ChatById(Model):
    op: Literal["export", "delete", "permission_rules"]
    conversationId: Text


class ForkChat(Model):
    op: Literal["fork"]
    conversationId: Text
    itemId: Text


class UpdateChat(Model):
    op: Literal["update"]
    conversationId: Text
    changes: dict[str, Any]


class RevertTurn(Model):
    op: Literal["revert"]
    conversationId: Text
    turnId: Text
    direction: Literal["undo", "redo"]


class AttachPaths(Model):
    op: Literal["attach_paths"]
    conversationId: Text
    paths: list[Text]


class FindFiles(Model):
    op: Literal["find_files"]
    query: str = ""


class Reference(Model):
    op: Literal["reference"]
    referenceId: Text


class ChildRead(Model):
    op: Literal["child_read"]
    conversationId: Text
    childRunId: Text
    after: Page = 0


class ChildStop(Model):
    op: Literal["child_stop"]
    conversationId: Text
    childRunId: Text


class ChatInput(Model):
    operation: Annotated[
        ListChats | SearchChats | CreateChat | ReadChat | ChatById | ForkChat
        | UpdateChat | RevertTurn | AttachPaths | FindFiles | Reference | ChildRead | ChildStop, Field(discriminator="op")]


class BotLists(Model):
    op: Literal["list", "library", "schedules", "sources"]


class BotById(Model):
    op: Literal["read", "inspect", "stop", "delete", "delete_routine", "test_routine"]
    id: Text


class CreateBot(Model):
    op: Literal["create"]
    name: Text
    intro: str = ""
    duty: str = ""
    model: Text | None = None
    skills: list[str] = []
    knowledge: list[str] = []
    look: dict[str, Any] | None = None


class CreateGroup(Model):
    op: Literal["create_group"]
    members: list[str]


class PatchBot(Model):
    op: Literal["patch"]
    id: Text
    changes: dict[str, Any]


class Section(Model):
    op: Literal["section"]
    old: str | None = None
    name: str | None = None


class Recipients(Model):
    op: Literal["recipients"]
    id: Text
    text: Text
    reply: dict[str, str] | None = None


class SendBot(Model):
    op: Literal["send"]
    id: Text
    text: Text
    reply: dict[str, str] | None = None
    attachments: dict[str, list[str]] = {}


class Upload(Model):
    op: Literal["upload"]
    id: Text
    name: Text
    data: str


class RetryBot(Model):
    op: Literal["retry"]
    id: Text
    cardId: Text


class RevertRun(Model):
    op: Literal["revert_run"]
    id: Text
    direction: Literal["undo", "redo"]


class SaveRoutine(Model):
    op: Literal["save_routine"]
    id: Text | None = None
    botId: Text | None = None
    conversationId: Text | None = None
    name: str | None = None
    instruction: str | None = None
    when: str | None = None
    zone: str | None = None
    rrule: str | None = None
    start: float | None = None
    source: str | None = None
    params: dict[str, Any] | None = None
    active: bool | None = None


class ToggleRoutine(Model):
    op: Literal["toggle_routine"]
    id: Text
    active: bool


class BotInput(Model):
    operation: Annotated[
        BotLists | BotById | CreateBot | CreateGroup | PatchBot | Section | Recipients
        | SendBot | Upload | RetryBot | RevertRun | SaveRoutine | ToggleRoutine, Field(discriminator="op")]


class SettingsList(Model):
    op: Literal["read", "folders", "spending"]


class DefaultsPage(Model):
    op: Literal["page"]
    scope: Scope = "workspace"
    kind: Kind = "general"


class Defaults(Model):
    op: Literal["set_defaults"]
    scope: Scope = "workspace"
    kind: Kind = "general"
    changes: dict[str, Any] = {}
    reset: str | None = None


class ReadInstructions(Model):
    op: Literal["read_instructions"]
    scope: Scope = "workspace"


class WriteInstructions(Model):
    op: Literal["write_instructions"]
    scope: Scope = "workspace"
    text: str


class Profiles(Model):
    op: Literal["profiles"]
    provider: Text
    offset: Page = 0


class RefreshProfiles(Model):
    op: Literal["refresh_profiles"]


class Discover(Model):
    op: Literal["discover_models"]
    kind: Literal["ollama", "lmstudio"]
    baseUrl: str | None = None


class Connection(ConnectionFields):
    op: Literal["put_connection"]


class PutModel(ModelConfig):
    op: Literal["put_model"]


class DeleteConfig(Model):
    op: Literal["delete_connection", "delete_model"]
    id: Text


class TestConnection(Model):
    op: Literal["test_connection"]
    modelId: Text
    thinking: dict[str, Any] | None = None
    testSearch: bool = True


class SearchSettings(Model):
    op: Literal["search"]
    mode: Literal["fixed", "auto"]
    provider: Text
    connectionId: str | None = None
    prices: dict[str, Any] = {}


class ReadUsage(Model):
    op: Literal["read_usage"]
    groupBy: Literal["day", "model", "workspace", "owner"] = "day"
    month: str | None = None
    groupId: str | None = None


class Budget(Model):
    op: Literal["usage"]
    timezone: Text
    monthlyLimit: str | None
    currency: Text


class MemoryList(Model):
    op: Literal["memory_list"]
    status: Literal["active", "deleted"] | None = None
    botId: str | None = None
    offset: Page = 0


class MemoryEdit(Model):
    op: Literal["memory_edit"]
    memoryId: Text
    expectedRevision: int
    body: str


class MemoryDelete(Model):
    op: Literal["memory_delete"]
    memoryId: Text
    expectedRevision: int


class SettingsInput(Model):
    operation: Annotated[
        SettingsList | DefaultsPage | Defaults | ReadInstructions | WriteInstructions
        | Profiles | RefreshProfiles | Discover | Connection | PutModel | DeleteConfig
        | TestConnection | SearchSettings | ReadUsage | Budget | MemoryList | MemoryEdit | MemoryDelete, Field(discriminator="op")]

from datetime import UTC, date, datetime
from typing import Annotated, Generic, Literal, TypeVar

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


Timestamp = Annotated[AwareDatetime, AfterValidator(utc)]
ID = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]


def normalize_domain(value: str) -> str:
    try:
        return validate_email("member@" + value.strip(), check_deliverability=False).domain
    except EmailNotValidError as exc:
        raise ValueError("Enter an email domain such as whitecode.com, without @ or a URL") from exc


EmailDomain = Annotated[str, AfterValidator(normalize_domain)]
Status = Literal["pending", "in_progress", "completed", "on_hold"]
Priority = Literal["low", "medium", "high"]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Register(Schema):
    role: Literal["owner", "member"] = "member"
    name: Name
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class Login(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class Preferences(Schema):
    email_notifications: bool = True
    push_notifications: bool = True
    task_reminders: bool = False
    team_updates: bool = False
    dark_mode: bool = False


class UserOut(Schema):
    role: Literal["owner", "member"]
    id: ID
    name: str
    email: EmailStr
    preferences: Preferences
    created_at: Timestamp
    updated_at: Timestamp


class ProfileUpdate(Schema):
    name: Name


class SessionOut(Schema):
    user: UserOut
    csrf_token: str
    expires_at: Timestamp


class TeamCreate(Schema):
    name: Name
    organization: Name | None = None
    email_domain: EmailDomain | None = None


class TeamOut(TeamCreate):
    id: ID
    owner_id: ID
    created_at: Timestamp
    updated_at: Timestamp


class TeamOrganization(Schema):
    organization: Name
    email_domain: EmailDomain


class MemberCreate(Schema):
    email: EmailStr
    job_title: str = Field(default="", max_length=120)


class MemberOut(Schema):
    user_id: ID
    name: str
    email: EmailStr
    job_title: str
    role: Literal["owner", "member"]
    active_tasks: int


class ProjectCreate(Schema):
    name: Name
    description: str = Field(default="", max_length=5000)
    status: Status = "pending"
    due_at: Timestamp | None = None


class ProjectOut(ProjectCreate):
    id: ID
    team_id: ID
    created_at: Timestamp
    updated_at: Timestamp


class TaskCreate(Schema):
    title: Name
    description: str = Field(default="", max_length=5000)
    project_id: ID
    assignee_id: ID | None = None
    priority: Priority = "medium"
    due_at: Timestamp | None = None
    tags: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    ] = Field(default_factory=list, max_length=12)


class TaskOut(TaskCreate):
    id: ID
    team_id: ID
    status: Status
    completed_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp
    version: int


class TaskUpdate(Schema):
    version: int = Field(ge=1)
    title: Name | None = None
    description: str | None = Field(default=None, max_length=5000)
    assignee_id: ID | None = None
    priority: Priority | None = None
    due_at: Timestamp | None = None
    tags: (
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]]
        | None
    ) = Field(default=None, max_length=12)
    status: Status | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in self.model_fields_set - {"due_at", "assignee_id"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.model_fields_set == {"version"}:
            raise ValueError("Provide at least one changed field")
        return self


class EventCreate(Schema):
    title: Name
    starts_at: Timestamp
    ends_at: Timestamp

    @model_validator(mode="after")
    def ordered(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class EventOut(EventCreate):
    id: ID
    team_id: ID
    created_at: Timestamp
    updated_at: Timestamp


class TimerStart(Schema):
    task_id: ID


class TimesheetCreate(Schema):
    task_id: ID
    work_date: date
    minutes: int = Field(strict=True, ge=1, le=1440)
    notes: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class TimesheetOut(Schema):
    id: ID
    team_id: ID
    user_id: ID
    member_name: str
    task_id: ID
    task_title: str
    project_name: str
    work_date: date
    minutes: int
    notes: str
    created_at: Timestamp
    updated_at: Timestamp


class TimerOut(Schema):
    id: ID
    team_id: ID
    user_id: ID
    task_id: ID
    started_at: Timestamp
    ended_at: Timestamp | None
    running: bool


T = TypeVar("T")


class Page(Schema, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class MonthCount(Schema):
    month: str
    count: int


class DayCount(Schema):
    date: date
    count: int


class Distribution(Schema):
    pending: int = 0
    in_progress: int = 0
    completed: int = 0
    on_hold: int = 0


class AnalyticsOut(Schema):
    from_: Timestamp = Field(alias="from")
    to: Timestamp
    generated_at: Timestamp
    tasks_completed: int
    active_projects: int
    team_members: int
    average_completion_seconds: float | None
    monthly_task_completion: list[MonthCount]
    daily_task_completion: list[DayCount]
    total_working_minutes: int
    project_status_distribution: Distribution


class ErrorDetail(Schema):
    code: str
    message: str
    request_id: str
    details: list[dict] = Field(default_factory=list)


class ErrorOut(Schema):
    error: ErrorDetail

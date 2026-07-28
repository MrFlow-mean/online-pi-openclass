from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from app.models import CoursePackageView, DocumentSaveRequest, Lesson, UserView, now_iso
from app.project_collaboration_models import (
    AddTeamMemberRequest,
    CreateIntegrationAppRequest,
    CreateOrganizationRequest,
    CreateProjectInvitationRequest,
    CreateProjectLabelRequest,
    CreateProjectMilestoneRequest,
    CreateProjectReviewRequest,
    CreateProjectWebhookRequest,
    CreateProjectWorkItemRequest,
    CreateTeamRequest,
    GrantTeamProjectRequest,
    InstallIntegrationRequest,
    IntegrationAppView,
    IncomingProjectInvitationView,
    OrganizationView,
    ProjectCheckView,
    ProjectGovernanceSummaryView,
    ProjectGovernanceView,
    ProjectKind,
    ProjectReviewView,
    ProjectWebhookDeliveryView,
    TeamView,
    UpdateProjectBoardRequest,
    UpdateProjectMemberRequest,
    UpdateProjectPolicyRequest,
    UpdateProjectWorkItemRequest,
)
from app.routers.auth import current_user
from app.routers.documents import _save_document_request
from app.services.project_collaboration import (
    ProjectCollaborationConflictError,
    ProjectCollaborationError,
    ProjectCollaborationNotFoundError,
    ProjectCollaborationPermissionError,
    ProjectCollaborationStore,
    run_project_checks,
)
from app.services.workspace_state import find_lesson_package, get_store


router = APIRouter(prefix="/api")


def _store() -> ProjectCollaborationStore:
    return ProjectCollaborationStore(get_store().path)


def _http_error(exc: ProjectCollaborationError) -> HTTPException:
    if isinstance(exc, ProjectCollaborationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProjectCollaborationPermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ProjectCollaborationConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _run(action):
    try:
        return action()
    except ProjectCollaborationError as exc:
        raise _http_error(exc) from exc


def _governance(kind: ProjectKind, project_id: str, user: UserView) -> ProjectGovernanceView:
    store = _store()
    store.deliver_pending_webhooks()
    return _run(lambda: store.governance(kind, project_id, user))


def _validate_external_https_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="集成地址必须是没有内嵌凭据的 HTTPS URL。")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise HTTPException(status_code=422, detail="集成地址不能指向本机或私有网络。")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value.strip()
    if not address.is_global:
        raise HTTPException(status_code=422, detail="集成地址不能指向本机或私有网络。")
    return value.strip()


@router.get("/project-collaboration/projects", response_model=list[ProjectGovernanceSummaryView])
def list_projects(user: UserView = Depends(current_user)) -> list[ProjectGovernanceSummaryView]:
    return _run(lambda: _store().list_governed_projects(user))


@router.get("/project-invitations", response_model=list[IncomingProjectInvitationView])
def list_incoming_invitations(
    user: UserView = Depends(current_user),
) -> list[IncomingProjectInvitationView]:
    return _run(lambda: _store().incoming_invitations(user))


@router.get(
    "/projects/{project_kind}/{project_id}/governance",
    response_model=ProjectGovernanceView,
)
def get_governance(
    project_kind: ProjectKind,
    project_id: str,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    return _governance(project_kind, project_id, user)


@router.post(
    "/projects/{project_kind}/{project_id}/invitations",
    response_model=ProjectGovernanceView,
)
def invite_member(
    project_kind: ProjectKind,
    project_id: str,
    request: CreateProjectInvitationRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.invite(project_kind, project_id, user, request.email, request.role))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.post("/project-invitations/{invitation_id}/accept", response_model=ProjectGovernanceView)
def accept_invitation(
    invitation_id: str,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    project_kind, project_id = _run(lambda: store.accept_invitation(invitation_id, user))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.delete(
    "/projects/{project_kind}/{project_id}/invitations/{invitation_id}",
    response_model=ProjectGovernanceView,
)
def revoke_invitation(
    project_kind: ProjectKind,
    project_id: str,
    invitation_id: str,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.revoke_invitation(project_kind, project_id, invitation_id, user))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.patch(
    "/projects/{project_kind}/{project_id}/members/{member_user_id}",
    response_model=ProjectGovernanceView,
)
def update_member(
    project_kind: ProjectKind,
    project_id: str,
    member_user_id: str,
    request: UpdateProjectMemberRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.update_member(project_kind, project_id, member_user_id, request.role, user))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.delete(
    "/projects/{project_kind}/{project_id}/members/{member_user_id}",
    response_model=ProjectGovernanceView,
)
def remove_member(
    project_kind: ProjectKind,
    project_id: str,
    member_user_id: str,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.update_member(project_kind, project_id, member_user_id, None, user))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.put(
    "/projects/{project_kind}/{project_id}/policy",
    response_model=ProjectGovernanceView,
)
def update_policy(
    project_kind: ProjectKind,
    project_id: str,
    request: UpdateProjectPolicyRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.update_policy(project_kind, project_id, user, request))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.post(
    "/projects/{project_kind}/{project_id}/contributions/{contribution_id}/reviews",
    response_model=ProjectReviewView,
)
def review_contribution(
    project_kind: ProjectKind,
    project_id: str,
    contribution_id: str,
    request: CreateProjectReviewRequest,
    user: UserView = Depends(current_user),
) -> ProjectReviewView:
    course_store = get_store()
    bundle = course_store.load_lesson_contribution(contribution_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="改进方案不存在。")
    collaboration = _store()
    return _run(
        lambda: collaboration.review(
            project_kind,
            project_id,
            bundle[0],
            user,
            request.decision,
            request.body,
            request.revision_number,
        )
    )


@router.get(
    "/projects/{project_kind}/{project_id}/contributions/{contribution_id}/reviews",
    response_model=list[ProjectReviewView],
)
def list_reviews(
    project_kind: ProjectKind,
    project_id: str,
    contribution_id: str,
    user: UserView = Depends(current_user),
) -> list[ProjectReviewView]:
    collaboration = _store()
    _run(lambda: collaboration.require_capability(project_kind, project_id, user, "view_project"))
    return collaboration.reviews(contribution_id)


@router.post(
    "/projects/{project_kind}/{project_id}/checks/run",
    response_model=list[ProjectCheckView],
)
def run_checks(
    project_kind: ProjectKind,
    project_id: str,
    contribution_id: str | None = None,
    user: UserView = Depends(current_user),
) -> list[ProjectCheckView]:
    return _run(lambda: run_project_checks(_store(), get_store(), project_kind, project_id, user, contribution_id))


@router.get(
    "/projects/{project_kind}/{project_id}/checks",
    response_model=list[ProjectCheckView],
)
def list_checks(
    project_kind: ProjectKind,
    project_id: str,
    contribution_id: str | None = None,
    user: UserView = Depends(current_user),
) -> list[ProjectCheckView]:
    store = _store()
    _run(lambda: store.require_capability(project_kind, project_id, user, "view_project"))
    return store.checks(project_kind, project_id, contribution_id)


@router.post(
    "/projects/{project_kind}/{project_id}/labels",
    response_model=ProjectGovernanceView,
)
def create_label(
    project_kind: ProjectKind,
    project_id: str,
    request: CreateProjectLabelRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.add_label(project_kind, project_id, user, request.name, request.color, request.description))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.post(
    "/projects/{project_kind}/{project_id}/milestones",
    response_model=ProjectGovernanceView,
)
def create_milestone(
    project_kind: ProjectKind,
    project_id: str,
    request: CreateProjectMilestoneRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.add_milestone(project_kind, project_id, user, request.title, request.description, request.due_at))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.put(
    "/projects/{project_kind}/{project_id}/board",
    response_model=ProjectGovernanceView,
)
def update_board(
    project_kind: ProjectKind,
    project_id: str,
    request: UpdateProjectBoardRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.replace_board(project_kind, project_id, user, request.columns))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.post(
    "/projects/{project_kind}/{project_id}/work-items",
    response_model=ProjectGovernanceView,
)
def create_work_item(
    project_kind: ProjectKind,
    project_id: str,
    request: CreateProjectWorkItemRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.add_work_item(project_kind, project_id, user, **request.model_dump()))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.patch(
    "/projects/{project_kind}/{project_id}/work-items/{item_id}",
    response_model=ProjectGovernanceView,
)
def update_work_item(
    project_kind: ProjectKind,
    project_id: str,
    item_id: str,
    request: UpdateProjectWorkItemRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.update_work_item(project_kind, project_id, item_id, user, request.model_dump(exclude_unset=True)))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.post(
    "/projects/{project_kind}/{project_id}/webhooks",
    response_model=ProjectGovernanceView,
)
def create_webhook(
    project_kind: ProjectKind,
    project_id: str,
    request: CreateProjectWebhookRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    url = _validate_external_https_url(request.url)
    _run(lambda: store.add_webhook(project_kind, project_id, user, url, request.events, request.secret))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.get(
    "/projects/{project_kind}/{project_id}/webhooks/deliveries",
    response_model=list[ProjectWebhookDeliveryView],
)
def list_webhook_deliveries(
    project_kind: ProjectKind,
    project_id: str,
    user: UserView = Depends(current_user),
) -> list[ProjectWebhookDeliveryView]:
    return _run(lambda: _store().webhook_deliveries(project_kind, project_id, user))


@router.post("/integration-apps", response_model=IntegrationAppView)
def create_integration_app(
    request: CreateIntegrationAppRequest,
    user: UserView = Depends(current_user),
) -> IntegrationAppView:
    callback_url = _validate_external_https_url(request.callback_url) if request.callback_url else None
    store = _store()
    app_id = store.add_integration_app(user, request.name, request.description, request.permissions, callback_url)
    timestamp = now_iso()
    return IntegrationAppView(
        id=app_id,
        name=request.name,
        description=request.description,
        permissions=request.permissions,
        callback_url=callback_url,
        installed=False,
        created_at=timestamp,
        updated_at=timestamp,
    )


@router.post(
    "/projects/{project_kind}/{project_id}/integrations",
    response_model=ProjectGovernanceView,
)
def install_integration(
    project_kind: ProjectKind,
    project_id: str,
    request: InstallIntegrationRequest,
    user: UserView = Depends(current_user),
) -> ProjectGovernanceView:
    store = _store()
    _run(lambda: store.install_integration(project_kind, project_id, user, request.app_id))
    return _run(lambda: store.governance(project_kind, project_id, user))


@router.post("/organizations", response_model=OrganizationView)
def create_organization(
    request: CreateOrganizationRequest,
    user: UserView = Depends(current_user),
) -> OrganizationView:
    return _run(lambda: _store().create_organization(user, request.name, request.slug))


@router.post("/organizations/{organization_id}/teams", response_model=TeamView)
def create_team(
    organization_id: str,
    request: CreateTeamRequest,
    user: UserView = Depends(current_user),
) -> TeamView:
    return _run(lambda: _store().create_team(organization_id, user, request.name, request.slug))


@router.post("/organizations/{organization_id}/teams/{team_id}/members", response_model=dict[str, bool])
def add_team_member(
    organization_id: str,
    team_id: str,
    request: AddTeamMemberRequest,
    user: UserView = Depends(current_user),
) -> dict[str, bool]:
    _run(lambda: _store().add_team_member(organization_id, team_id, request.user_id, user))
    return {"ok": True}


@router.post("/organizations/{organization_id}/teams/{team_id}/projects", response_model=dict[str, bool])
def grant_team_project(
    organization_id: str,
    team_id: str,
    request: GrantTeamProjectRequest,
    user: UserView = Depends(current_user),
) -> dict[str, bool]:
    _run(lambda: _store().grant_team(organization_id, team_id, request.project_kind, request.project_id, request.role, user))
    return {"ok": True}


@router.post(
    "/projects/{project_kind}/{project_id}/lessons/{lesson_id}/document/save",
    response_model=CoursePackageView,
)
def save_collaborative_document(
    project_kind: ProjectKind,
    project_id: str,
    lesson_id: str,
    request: DocumentSaveRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    store = _store()
    role = _run(lambda: store.require_capability(project_kind, project_id, user, "edit_project"))
    owner_user_id, _ = _run(lambda: store.project_record(project_kind, project_id))
    if project_kind == "lesson" and lesson_id != project_id:
        raise HTTPException(status_code=404, detail="课程不属于该项目。")
    owner_workspace = get_store().load_for_user(owner_user_id)
    try:
        package, _lesson = find_lesson_package(owner_workspace, lesson_id)
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail="课程不属于该项目。") from exc
    if project_kind == "package" and package.id != project_id:
        raise HTTPException(status_code=404, detail="课程不属于该项目。")
    policy = store.policy(project_kind, project_id)
    if policy.protect_default_branch and role == "editor":
        raise HTTPException(status_code=409, detail="默认分支受保护，请通过改进方案提交修改。")
    payload = request.model_copy(
        update={
            "metadata": {
                **request.metadata,
                "kind": "collaborative_document_save",
                "actor_user_id": user.id,
                "actor_role": role,
                "project_kind": project_kind,
                "project_id": project_id,
            }
        }
    )
    saved_package = _save_document_request(lesson_id, payload, owner_user_id)
    store.record_event(
        project_kind,
        project_id,
        user.id,
        "document.saved",
        {"lesson_id": lesson_id, "role": role},
    )
    return saved_package


@router.get(
    "/projects/{project_kind}/{project_id}/lessons/{lesson_id}",
    response_model=Lesson,
)
def get_collaborative_lesson(
    project_kind: ProjectKind,
    project_id: str,
    lesson_id: str,
    user: UserView = Depends(current_user),
) -> Lesson:
    store = _store()
    _run(lambda: store.require_capability(project_kind, project_id, user, "view_project"))
    owner_user_id, _ = _run(lambda: store.project_record(project_kind, project_id))
    owner_workspace = get_store().load_for_user(owner_user_id)
    try:
        package, lesson = find_lesson_package(owner_workspace, lesson_id)
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail="课程不属于该项目。") from exc
    if (project_kind == "lesson" and lesson_id != project_id) or (
        project_kind == "package" and package.id != project_id
    ):
        raise HTTPException(status_code=404, detail="课程不属于该项目。")
    return lesson

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import CaseReport, DisciplinaryCase


OFFICIAL_ROLES = {
    'discipline_officer', 'dean', 'program_chair', 'ssao_coordinator',
    'class_adviser', 'committee_member', 'board_member', 'president', 'read_only_auditor',
}
FINDING_ROLES = {'discipline_officer', 'dean', 'board_member', 'president'}
FINAL_DECISION_ROLES = {'dean', 'board_member', 'president'}
READ_ONLY_ROLES = {'read_only_auditor'}


def committee_roles(user):
    if not user.is_authenticated:
        return set()
    return set(user.committee_memberships.filter(active=True, committee__active=True).values_list('role', flat=True))


def is_official(user):
    return bool(user.is_authenticated and (user.is_superuser or committee_roles(user) & OFFICIAL_ROLES))


def is_discipline_officer(user):
    return bool(user.is_authenticated and (user.is_superuser or 'discipline_officer' in committee_roles(user)))


def can_manage_policy(user):
    return bool(user.is_authenticated and user.is_superuser)


def can_decide(user):
    """Can record a finding after a designated review; this is not final sanction authority."""
    return bool(user.is_authenticated and (user.is_superuser or committee_roles(user) & FINDING_ROLES))


def can_finalize_decision(user):
    """Can record final decisions and sanctions under an explicitly assigned role."""
    return bool(user.is_authenticated and (user.is_superuser or committee_roles(user) & FINAL_DECISION_ROLES))


def can_manage_confidential(user, case=None):
    """Check confidential access globally or for a case linked to the user's assignment."""
    if not user.is_authenticated:
        return False
    roles = committee_roles(user)
    if user.is_superuser or roles & {'discipline_officer', 'dean'}:
        return True
    if case is None:
        return False
    assigned_to_case = case.assigned_officer_id == user.pk
    scheduled_for_committee = case.committee_meetings.filter(
        committee__active=True,
        committee__members__user=user,
        committee__members__active=True,
    ).exists()
    return bool(assigned_to_case or scheduled_for_committee)


def visible_cases(user):
    """Scope global access to office leads; other officials see only assigned cases."""
    cases = DisciplinaryCase.objects.select_related('report', 'assigned_officer', 'offense_category')
    if not user.is_authenticated:
        return cases.none()
    roles = committee_roles(user)
    if user.is_superuser or {'discipline_officer', 'dean'} & roles:
        return cases
    return cases.filter(
        Q(report__submitted_by=user) |
        Q(assigned_officer=user) |
        Q(people__account=user) |
        Q(committee_meetings__committee__active=True, committee_meetings__committee__members__user=user, committee_meetings__committee__members__active=True)
    ).distinct()


def require_official(user):
    if not is_official(user):
        raise PermissionDenied('This action is limited to authorized school personnel.')


def require_officer(user):
    if not is_discipline_officer(user):
        raise PermissionDenied('This action is limited to the Discipline Office.')


def require_decision_authority(user):
    if not can_decide(user):
        raise PermissionDenied('This action requires a designated finding authority.')


def require_final_decision_authority(user):
    if not can_finalize_decision(user):
        raise PermissionDenied('Final decisions and sanctions require a Dean, Board, President, or system administrator authority.')


def require_policy_admin(user):
    if not can_manage_policy(user):
        raise PermissionDenied('Only a system administrator or authorized Django staff account may edit the policy catalog.')

"""
Management command: seed_cases
--------------------------------
Seeds 5 realistic example disciplinary cases with full case reports,
persons, and action history for demonstration purposes.

Usage:
    python manage.py seed_cases
    python manage.py seed_cases --clear   # removes existing seeded cases first
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.discipline.models import (
    CaseAction,
    CaseAuditLog,
    CasePerson,
    CaseReport,
    DisciplinaryCase,
    OffenseCategory,
)

User = get_user_model()

# ── Sample data ────────────────────────────────────────────────────────────────

CASES = [
    {
        "source_type": "complaint",
        "status": DisciplinaryCase.Status.INITIAL_REVIEW,
        "academic_year": "2024–2025",
        "report": {
            "report_type": "complaint",
            "complainant_name": "Maria Santos",
            "complainant_student_id": "2021-00412",
            "complainant_program": "BS Information Technology",
            "complainant_year_section": "3A",
            "respondent_name": "Juan dela Cruz",
            "respondent_student_id": "2022-00871",
            "respondent_program": "BS Information Technology",
            "respondent_year_section": "2B",
            "incident_at": timezone.now() - timedelta(days=14),
            "incident_place": "Computer Laboratory 2",
            "narration": (
                "On October 15, 2024, during our scheduled lab class, the respondent "
                "repeatedly used offensive and threatening language directed at me. "
                "The respondent told me to 'watch out' and called me derogatory names "
                "in front of several classmates. I have witnesses who can corroborate "
                "this account. I am submitting this complaint to seek a formal review "
                "and ensure this behaviour does not continue."
            ),
            "witness_information": "Witnesses: Carlo Reyes (2022-00901) and Ana Lim (2021-00534)",
        },
        "respondent": {
            "role": "respondent",
            "name": "Juan dela Cruz",
            "student_id": "2022-00871",
            "program": "BS Information Technology",
            "year_section": "2B",
        },
        "action": {
            "action_type": "status",
            "to_status": DisciplinaryCase.Status.INITIAL_REVIEW,
            "details": "Report received and forwarded for initial review.",
        },
    },
    {
        "source_type": "incident",
        "status": DisciplinaryCase.Status.INVESTIGATION,
        "academic_year": "2024–2025",
        "report": {
            "report_type": "incident",
            "reporting_position": "Class Adviser",
            "respondent_name": "Mark Villanueva",
            "respondent_student_id": "2023-01102",
            "respondent_program": "BS Accountancy",
            "respondent_year_section": "1C",
            "incident_at": timezone.now() - timedelta(days=21),
            "incident_place": "School Canteen",
            "narration": (
                "At approximately 12:15 PM on October 8, 2024, I observed the respondent "
                "in an altercation with another student near the school canteen. "
                "The respondent was seen pushing the other student and using foul language. "
                "Security personnel intervened. CCTV footage is available as supporting evidence. "
                "I am filing this incident report in accordance with school procedure."
            ),
            "witness_information": "Security Guard on duty: Ernesto Calo",
        },
        "respondent": {
            "role": "respondent",
            "name": "Mark Villanueva",
            "student_id": "2023-01102",
            "program": "BS Accountancy",
            "year_section": "1C",
        },
        "action": {
            "action_type": "status",
            "to_status": DisciplinaryCase.Status.INVESTIGATION,
            "details": "Incident report reviewed and accepted for investigation. CCTV footage requested.",
        },
    },
    {
        "source_type": "complaint",
        "status": DisciplinaryCase.Status.HEARING,
        "academic_year": "2024–2025",
        "report": {
            "report_type": "complaint",
            "complainant_name": "Grace Fernandez",
            "complainant_student_id": "2020-00215",
            "complainant_program": "BS Nursing",
            "complainant_year_section": "4A",
            "respondent_name": "Ryan Tolentino",
            "respondent_student_id": "2021-00668",
            "respondent_program": "BS Nursing",
            "respondent_year_section": "3B",
            "incident_at": timezone.now() - timedelta(days=35),
            "incident_place": "Nursing Skills Laboratory",
            "narration": (
                "I am filing this complaint regarding repeated acts of academic dishonesty "
                "by the respondent during our clinical skills assessment on September 25, 2024. "
                "The respondent was observed copying my recorded patient assessment data "
                "and presenting it as their own work without consent. "
                "I brought this to the attention of our clinical instructor, who referred the "
                "matter to the Discipline Office for formal review."
            ),
            "witness_information": "Clinical Instructor: Prof. Remedios Aquino",
        },
        "respondent": {
            "role": "respondent",
            "name": "Ryan Tolentino",
            "student_id": "2021-00668",
            "program": "BS Nursing",
            "year_section": "3B",
        },
        "action": {
            "action_type": "status",
            "to_status": DisciplinaryCase.Status.HEARING,
            "details": "Case accepted for investigation. Hearing scheduled with the committee.",
        },
    },
    {
        "source_type": "incident",
        "status": DisciplinaryCase.Status.DECISION,
        "academic_year": "2024–2025",
        "report": {
            "report_type": "incident",
            "reporting_position": "Security Officer",
            "respondent_name": "Leo Manalo",
            "respondent_student_id": "2022-00340",
            "respondent_program": "BS Civil Engineering",
            "respondent_year_section": "2A",
            "incident_at": timezone.now() - timedelta(days=52),
            "incident_place": "Main Gate — Parking Area",
            "narration": (
                "On September 17, 2024 at 7:45 AM, security personnel confiscated a "
                "prohibited item (pocket knife) from the respondent during the routine "
                "bag inspection at the main gate. The respondent was cooperative during "
                "the inspection. The item was surrendered and is held as evidence. "
                "This report is filed in compliance with the school's security protocol."
            ),
            "witness_information": "Guard on duty: Rogelio Bautista. Item is in the security office.",
        },
        "respondent": {
            "role": "respondent",
            "name": "Leo Manalo",
            "student_id": "2022-00340",
            "program": "BS Civil Engineering",
            "year_section": "2A",
        },
        "action": {
            "action_type": "status",
            "to_status": DisciplinaryCase.Status.DECISION,
            "details": "Investigation completed. Case elevated for final decision by the Dean.",
        },
    },
    {
        "source_type": "complaint",
        "status": DisciplinaryCase.Status.CLOSED,
        "academic_year": "2024–2025",
        "report": {
            "report_type": "complaint",
            "complainant_name": "Patricia Soriano",
            "complainant_student_id": "2020-00099",
            "complainant_program": "AB Communication",
            "complainant_year_section": "4B",
            "respondent_name": "Bea Navarro",
            "respondent_student_id": "2021-00450",
            "respondent_program": "AB Communication",
            "respondent_year_section": "3A",
            "incident_at": timezone.now() - timedelta(days=75),
            "incident_place": "School Corridor — Building B",
            "narration": (
                "I am submitting a formal complaint regarding cyberbullying and online "
                "harassment by the respondent. Starting in late August 2024, the respondent "
                "posted a series of defamatory and humiliating messages about me on a group "
                "chat shared by our classmates. Screenshots of the messages have been saved "
                "and are attached as supporting evidence. The harassment has caused me "
                "significant emotional distress and affected my academic performance."
            ),
            "witness_information": "Classmates in the group chat. Screenshots provided.",
        },
        "respondent": {
            "role": "respondent",
            "name": "Bea Navarro",
            "student_id": "2021-00450",
            "program": "AB Communication",
            "year_section": "3A",
        },
        "action": {
            "action_type": "status",
            "to_status": DisciplinaryCase.Status.CLOSED,
            "details": (
                "Case reviewed and resolved. Respondent accepted responsibility, "
                "issued a formal written apology, and completed the required counselling "
                "sessions. Case closed with a written warning on record."
            ),
        },
        "finding": DisciplinaryCase.Finding.SUBSTANTIATED,
        "final_sanction": DisciplinaryCase.Sanction.OTHER,
        "decision_notes": "Written apology accepted. Counselling completed. Written warning issued.",
        "closed_at": timezone.now() - timedelta(days=10),
    },
]


class Command(BaseCommand):
    help = "Seed 5 example disciplinary cases for demonstration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove all seeded example cases before re-seeding.",
        )

    def handle(self, *args, **options):
        # Get or create a superuser to act as submitter / actor
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            self.stderr.write(self.style.ERROR(
                "No active superuser found. Create a superuser first:\n"
                "  python manage.py createsuperuser"
            ))
            return

        if options["clear"]:
            deleted = DisciplinaryCase.objects.filter(
                reference__startswith="SEED-"
            ).count()
            DisciplinaryCase.objects.filter(reference__startswith="SEED-").delete()
            self.stdout.write(self.style.WARNING(f"Removed {deleted} seeded case(s)."))

        # Get a default offense category (or create one)
        category, _ = OffenseCategory.objects.get_or_create(
            name="General Misconduct",
            defaults={
                "description": "Acts contrary to school rules and community standards.",
                "classification": OffenseCategory.Classification.UNCLASSIFIED,
                "policy_version": "2024-A",
                "active": True,
            },
        )

        created = 0
        for i, data in enumerate(CASES, start=1):
            # Use a deterministic reference so re-running doesn't duplicate
            seed_ref = f"SEED-2024-{i:04d}"
            if DisciplinaryCase.objects.filter(reference=seed_ref).exists():
                self.stdout.write(f"  SKIP  {seed_ref} already exists.")
                continue

            rdata = data["report"]
            now = timezone.now() - timedelta(days=80 - (i * 10))

            # 1. Create the case  (set reference before save so the model's
            #    auto-generator is skipped — it only fires when reference is empty)
            case = DisciplinaryCase(
                reference=seed_ref,          # ← set BEFORE save()
                source_type=data["source_type"],
                status=data["status"],
                academic_year=data.get("academic_year", "2024–2025"),
                offense_category=category,
                finding=data.get("finding", DisciplinaryCase.Finding.NOT_DETERMINED),
                final_sanction=data.get("final_sanction", DisciplinaryCase.Sanction.NONE),
                decision_notes=data.get("decision_notes", ""),
                opened_at=now,
                assigned_officer=admin,
            )
            if data.get("closed_at"):
                case.closed_at = data["closed_at"]
            case.save()

            # 2. Create the report
            CaseReport.objects.create(
                case=case,
                report_type=rdata["report_type"],
                submitted_by=admin,
                complainant_name=rdata.get("complainant_name", ""),
                complainant_student_id=rdata.get("complainant_student_id", ""),
                complainant_program=rdata.get("complainant_program", ""),
                complainant_year_section=rdata.get("complainant_year_section", ""),
                reporting_position=rdata.get("reporting_position", ""),
                respondent_name=rdata.get("respondent_name", ""),
                respondent_student_id=rdata.get("respondent_student_id", ""),
                respondent_program=rdata.get("respondent_program", ""),
                respondent_year_section=rdata.get("respondent_year_section", ""),
                incident_at=rdata.get("incident_at"),
                incident_place=rdata.get("incident_place", ""),
                narration=rdata.get("narration", ""),
                witness_information=rdata.get("witness_information", ""),
                acknowledgment=True,
                submitted_at=now,
            )

            # 3. Create the respondent person
            pdata = data.get("respondent", {})
            if pdata:
                CasePerson.objects.create(
                    case=case,
                    role=CasePerson.Role.RESPONDENT,
                    name=pdata.get("name", ""),
                    student_id=pdata.get("student_id", ""),
                    program=pdata.get("program", ""),
                    year_section=pdata.get("year_section", ""),
                )

            # 4. Create the case action / status log
            adata = data.get("action", {})
            if adata:
                CaseAction.objects.create(
                    case=case,
                    action_type=CaseAction.ActionType.STATUS,
                    to_status=adata.get("to_status", ""),
                    details=adata.get("details", ""),
                    actor=admin,
                )

            # 5. Audit log entry
            CaseAuditLog.objects.create(
                case=case,
                actor=admin,
                action="seeded",
                details=f"Example case created by seed_cases management command (case {i} of 5).",
            )

            created += 1
            self.stdout.write(self.style.SUCCESS(
                f"  {seed_ref}  {data['source_type'].upper():9s}  "
                f"{rdata.get('respondent_name', 'N/A'):25s}  "
                f"Status: {data['status']}"
            ))

        self.stdout.write()
        if created:
            self.stdout.write(self.style.SUCCESS(f"Done — {created} example case(s) created."))
        else:
            self.stdout.write("All example cases already exist. Use --clear to re-seed.")

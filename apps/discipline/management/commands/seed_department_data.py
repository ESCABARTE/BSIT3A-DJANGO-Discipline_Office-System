"""
Management command: seed_department_data
-----------------------------------------
Seeds realistic OffenseCategories (policies), Violations, PenaltyCatalogs,
and DisciplinaryCases for each department level: JHS, SHS, and College.

Usage:
    python manage.py seed_department_data
    python manage.py seed_department_data --clear   # removes all seeded data first
"""
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
    PenaltyCatalog,
    Violation,
)

User = get_user_model()

# ── Prefix used to identify all seeded records ────────────────────────────────
SEED_PREFIX = "DEPT-"

# ── Shared academic year ───────────────────────────────────────────────────────
AY = "2024–2025"

# ═════════════════════════════════════════════════════════════════════════════
#  OFFENSE CATEGORIES (policies) — one set per department level
# ═════════════════════════════════════════════════════════════════════════════
CATEGORIES = [
    # ── JHS ──────────────────────────────────────────────────────────────────
    {
        "name": "JHS — Minor Misconduct",
        "description": (
            "Acts of minor misconduct committed by Junior High School students, "
            "including disruption of class, tardiness, and minor dress-code violations. "
            "These offenses are handled at the class-adviser level with guidance and "
            "corrective measures before escalation to the Discipline Office."
        ),
        "policy_reference": "JHS Student Handbook §3.1 — Minor Offenses",
        "classification": OffenseCategory.Classification.JUG_POST,
        "review_authority": "Class Adviser / JHS Level Coordinator",
        "policy_version": "2024-JHS-A",
        "escalation_rules": (
            "First offense: verbal warning and counselling. "
            "Second offense: written warning and parent/guardian notification. "
            "Third offense: referral to Discipline Office."
        ),
    },
    {
        "name": "JHS — Major Misconduct",
        "description": (
            "Serious violations by JHS students that endanger the safety and welfare "
            "of other students, include physical altercations, bullying, vandalism, or "
            "repeated minor offense escalation. Requires formal Discipline Office review."
        ),
        "policy_reference": "JHS Student Handbook §3.2 — Major Offenses",
        "classification": OffenseCategory.Classification.SUSPENSION,
        "review_authority": "JHS Principal / Discipline Officer",
        "policy_version": "2024-JHS-A",
        "escalation_rules": (
            "First offense: formal investigation, possible suspension up to 5 school days. "
            "Repeat offense: elevated to serious review; parent conference mandatory."
        ),
    },
    # ── SHS ──────────────────────────────────────────────────────────────────
    {
        "name": "SHS — Minor Misconduct",
        "description": (
            "Minor offenses committed by Senior High School students, such as unauthorized "
            "absences, minor insubordination, use of mobile phones during class, and "
            "mild verbal altercations. Addressed through guidance and corrective action."
        ),
        "policy_reference": "SHS Student Handbook §4.1 — Minor Offenses",
        "classification": OffenseCategory.Classification.JUG_POST,
        "review_authority": "SHS Strand Coordinator / Guidance Counsellor",
        "policy_version": "2024-SHS-A",
        "escalation_rules": (
            "First offense: guidance intervention. "
            "Second offense: written warning, parent notification. "
            "Third offense: referral to Discipline Office for formal case."
        ),
    },
    {
        "name": "SHS — Serious Misconduct",
        "description": (
            "Grave violations by SHS students including acts of violence, academic "
            "dishonesty, possession of prohibited items, substance-related offenses, "
            "cyberbullying, and conduct that disrupts school order or safety."
        ),
        "policy_reference": "SHS Student Handbook §4.2 — Serious Offenses",
        "classification": OffenseCategory.Classification.SERIOUS,
        "review_authority": "SHS Principal / Discipline Committee",
        "policy_version": "2024-SHS-A",
        "escalation_rules": (
            "Suspension up to 10 school days depending on severity. "
            "Repeat offenders may be subject to dismissal review by the Discipline Committee."
        ),
        "restitution_required": True,
    },
    # ── College ───────────────────────────────────────────────────────────────
    {
        "name": "College — Minor Misconduct",
        "description": (
            "Minor offenses at the collegiate level such as class disruptions, "
            "minor insubordination, non-compliance with uniform/dress code, "
            "unauthorized use of school facilities, and habitual tardiness."
        ),
        "policy_reference": "College Student Handbook §5.1 — Minor Offenses",
        "classification": OffenseCategory.Classification.JUG_POST,
        "review_authority": "Program Chair / College Dean",
        "policy_version": "2024-COL-A",
        "escalation_rules": (
            "First and second offense: Jug or Post assignment with guidance counselling. "
            "Third offense: written warning and referral to Discipline Office."
        ),
    },
    {
        "name": "College — Major Misconduct",
        "description": (
            "Serious violations by college students requiring formal Discipline Office "
            "involvement: physical violence, academic dishonesty (plagiarism/cheating), "
            "harassment, bullying, possession of prohibited substances or weapons, "
            "forgery of academic documents, and defiance of authority."
        ),
        "policy_reference": "College Student Handbook §5.2 — Major Offenses",
        "classification": OffenseCategory.Classification.SUSPENSION,
        "review_authority": "College Dean / Discipline Committee",
        "policy_version": "2024-COL-A",
        "escalation_rules": (
            "Suspension 5–15 school days. Repeat or aggravated offense: "
            "dismissal/expulsion review before the full Discipline Committee."
        ),
    },
    {
        "name": "College — Grave Misconduct / Expulsion-Level",
        "description": (
            "Acts warranting possible dismissal or expulsion: sexual misconduct, "
            "acts of violence causing bodily harm, drug possession or use on campus, "
            "hazing, or conduct grossly unbecoming of a student of the institution."
        ),
        "policy_reference": "College Student Handbook §5.3 — Grave Offenses",
        "classification": OffenseCategory.Classification.SERIOUS,
        "review_authority": "Discipline Board / School President",
        "policy_version": "2024-COL-A",
        "escalation_rules": (
            "Immediate preventive suspension pending investigation. "
            "Full committee review required. May result in dismissal or expulsion."
        ),
        "restitution_required": True,
    },
]

# ═════════════════════════════════════════════════════════════════════════════
#  VIOLATIONS — keyed by category name, used when building the catalog
# ═════════════════════════════════════════════════════════════════════════════
VIOLATIONS = [
    # ── JHS Minor ──────────────────────────────────────────────────────────
    {
        "category": "JHS — Minor Misconduct",
        "code": "JHS-M-001",
        "name": "Habitual Tardiness",
        "description": "Arriving late to class three or more times within a grading period.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "JHS Handbook §3.1.1",
        "recommended_penalty": "Verbal warning on first instance; written warning and parent notification on third instance.",
    },
    {
        "category": "JHS — Minor Misconduct",
        "code": "JHS-M-002",
        "name": "Dress Code Violation",
        "description": "Non-compliance with the prescribed school uniform or grooming standards.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "JHS Handbook §3.1.2",
        "recommended_penalty": "Guidance counselling and written notice to parents.",
    },
    {
        "category": "JHS — Minor Misconduct",
        "code": "JHS-M-003",
        "name": "Disruptive Behavior in Class",
        "description": "Conduct that unnecessarily interrupts classroom instruction or activities.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "JHS Handbook §3.1.3",
        "recommended_penalty": "Verbal reprimand; Jug assignment on repeat.",
    },
    # ── JHS Major ──────────────────────────────────────────────────────────
    {
        "category": "JHS — Major Misconduct",
        "code": "JHS-J-001",
        "name": "Bullying / Harassment",
        "description": (
            "Any deliberate act of intimidation, verbal abuse, physical harm, or "
            "cyber-harassment directed at another student."
        ),
        "severity": Violation.Severity.HIGH,
        "classification": Violation.Classification.MAJOR,
        "applicable_rule": "JHS Handbook §3.2.1; RA 10627 (Anti-Bullying Act)",
        "recommended_penalty": "Formal investigation; suspension up to 5 school days; mandatory counselling.",
    },
    {
        "category": "JHS — Major Misconduct",
        "code": "JHS-J-002",
        "name": "Vandalism / Destruction of Property",
        "description": "Intentional damage to school property, facilities, or the property of another person.",
        "severity": Violation.Severity.MODERATE,
        "classification": Violation.Classification.MAJOR,
        "applicable_rule": "JHS Handbook §3.2.2",
        "recommended_penalty": "Restitution of damaged property; suspension 2–5 school days.",
    },
    # ── SHS Minor ──────────────────────────────────────────────────────────
    {
        "category": "SHS — Minor Misconduct",
        "code": "SHS-M-001",
        "name": "Unauthorized Use of Mobile Phone",
        "description": "Using a mobile phone during class hours without prior teacher permission.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "SHS Handbook §4.1.1",
        "recommended_penalty": "Device confiscated for the day; guidance counselling.",
    },
    {
        "category": "SHS — Minor Misconduct",
        "code": "SHS-M-002",
        "name": "Unauthorized Absence",
        "description": "Incurring unexcused absences beyond the allowable limit per grading period.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "SHS Handbook §4.1.2",
        "recommended_penalty": "Written warning; Jug assignment; parent notification.",
    },
    # ── SHS Serious ────────────────────────────────────────────────────────
    {
        "category": "SHS — Serious Misconduct",
        "code": "SHS-S-001",
        "name": "Academic Dishonesty (Cheating/Plagiarism)",
        "description": (
            "Copying during examinations, submitting another person's work as one's own, "
            "or any form of academic fraud in coursework or assessments."
        ),
        "severity": Violation.Severity.HIGH,
        "classification": Violation.Classification.MAJOR,
        "applicable_rule": "SHS Handbook §4.2.1; School Academic Integrity Policy",
        "recommended_penalty": "Zero mark on the subject output; suspension 3–7 school days.",
    },
    {
        "category": "SHS — Serious Misconduct",
        "code": "SHS-S-002",
        "name": "Cyberbullying",
        "description": (
            "Using electronic communication to bully, threaten, humiliate, or harass "
            "another member of the school community."
        ),
        "severity": Violation.Severity.HIGH,
        "classification": Violation.Classification.MAJOR,
        "applicable_rule": "SHS Handbook §4.2.2; RA 10175 (Cybercrime Prevention Act)",
        "recommended_penalty": "Suspension 5–10 school days; mandatory counselling; possible dismissal on repeat.",
    },
    {
        "category": "SHS — Serious Misconduct",
        "code": "SHS-S-003",
        "name": "Possession of Prohibited Items",
        "description": "Bringing bladed weapons, dangerous objects, or other prohibited items on campus.",
        "severity": Violation.Severity.CRITICAL,
        "classification": Violation.Classification.GRAVE,
        "applicable_rule": "SHS Handbook §4.2.3",
        "recommended_penalty": "Immediate confiscation; suspension pending full investigation.",
    },
    # ── College Minor ──────────────────────────────────────────────────────
    {
        "category": "College — Minor Misconduct",
        "code": "COL-M-001",
        "name": "Class Disruption",
        "description": "Conduct that disrupts normal class proceedings, including disrespectful language toward peers.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "College Handbook §5.1.1",
        "recommended_penalty": "Verbal warning; Jug assignment on second offense.",
    },
    {
        "category": "College — Minor Misconduct",
        "code": "COL-M-002",
        "name": "Uniform / ID Non-Compliance",
        "description": "Failure to wear the prescribed uniform or display the school ID while on campus.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "College Handbook §5.1.2",
        "recommended_penalty": "Post assignment; written notice to parents/guardians.",
    },
    {
        "category": "College — Minor Misconduct",
        "code": "COL-M-003",
        "name": "Habitual Tardiness",
        "description": "Arriving late to class three or more times within a semester.",
        "severity": Violation.Severity.LOW,
        "classification": Violation.Classification.MINOR,
        "applicable_rule": "College Handbook §5.1.3",
        "recommended_penalty": "Written warning; Jug/Post on third and subsequent offenses.",
    },
    # ── College Major ──────────────────────────────────────────────────────
    {
        "category": "College — Major Misconduct",
        "code": "COL-J-001",
        "name": "Physical Violence / Fighting",
        "description": "Any act of physical aggression against another student, faculty, or staff member.",
        "severity": Violation.Severity.HIGH,
        "classification": Violation.Classification.MAJOR,
        "applicable_rule": "College Handbook §5.2.1",
        "recommended_penalty": "Suspension 5–10 school days; mandatory counselling and anger management.",
    },
    {
        "category": "College — Major Misconduct",
        "code": "COL-J-002",
        "name": "Academic Dishonesty (Cheating/Plagiarism)",
        "description": (
            "Any form of academic fraud including plagiarism, unauthorized collaboration, "
            "impersonation during examinations, or falsification of academic outputs."
        ),
        "severity": Violation.Severity.HIGH,
        "classification": Violation.Classification.MAJOR,
        "applicable_rule": "College Handbook §5.2.2; Academic Integrity Policy",
        "recommended_penalty": "Zero grade on the subject; suspension 3–7 school days.",
    },
    {
        "category": "College — Major Misconduct",
        "code": "COL-J-003",
        "name": "Harassment / Bullying",
        "description": (
            "Repeated acts of intimidation, verbal abuse, physical threats, or online harassment "
            "directed at any member of the school community."
        ),
        "severity": Violation.Severity.HIGH,
        "classification": Violation.Classification.MAJOR,
        "applicable_rule": "College Handbook §5.2.3; RA 10627",
        "recommended_penalty": "Suspension 5–15 school days; possible dismissal on repeat.",
    },
    # ── College Grave ──────────────────────────────────────────────────────
    {
        "category": "College — Grave Misconduct / Expulsion-Level",
        "code": "COL-G-001",
        "name": "Drug Possession or Use on Campus",
        "description": "Possession, use, or distribution of illegal substances on school premises.",
        "severity": Violation.Severity.CRITICAL,
        "classification": Violation.Classification.GRAVE,
        "applicable_rule": "College Handbook §5.3.1; RA 9165 (Comprehensive Drugs Act)",
        "recommended_penalty": "Immediate preventive suspension; full committee review; possible expulsion.",
    },
    {
        "category": "College — Grave Misconduct / Expulsion-Level",
        "code": "COL-G-002",
        "name": "Sexual Misconduct",
        "description": (
            "Any unwanted sexual act, sexual harassment, indecent exposure, or behavior "
            "of a sexual nature that violates the dignity of another person."
        ),
        "severity": Violation.Severity.CRITICAL,
        "classification": Violation.Classification.GRAVE,
        "applicable_rule": "College Handbook §5.3.2; RA 11313 (Safe Spaces Act)",
        "recommended_penalty": "Immediate preventive suspension; full board hearing; expulsion review.",
    },
]

# ═════════════════════════════════════════════════════════════════════════════
#  CASES — 3 per department level = 9 total
# ═════════════════════════════════════════════════════════════════════════════
CASES = [

    # ══════════════════════════════════════════════════════
    #  JHS CASES
    # ══════════════════════════════════════════════════════
    {
        "ref": f"{SEED_PREFIX}JHS-001",
        "source_type": "incident",
        "status": DisciplinaryCase.Status.INITIAL_REVIEW,
        "case_level": DisciplinaryCase.CaseLevel.JHS,
        "classification": DisciplinaryCase.Classification.MINOR,
        "academic_year": AY,
        "violation_code": "JHS-M-003",
        "days_ago": 7,
        "report": {
            "report_type": "incident",
            "reporting_position": "JHS Class Adviser",
            "respondent_name": "Karl Espinosa",
            "respondent_student_id": "JHS-2024-0045",
            "respondent_program": "Grade 8",
            "respondent_year_section": "Grade 8 — Mabini",
            "incident_place": "Classroom 8-Mabini",
            "narration": (
                "On November 3, 2024, during the 2nd period Mathematics class, the respondent "
                "repeatedly interrupted the lesson by talking loudly to seatmates and throwing "
                "paper at other students despite multiple verbal warnings. The behavior disrupted "
                "the class and affected the learning of other Grade 8 students. I am filing this "
                "incident report to initiate corrective guidance in accordance with the JHS handbook."
            ),
            "witness_information": "Subject Teacher: Mr. Alejandro Reyes; Present classmates in 8-Mabini.",
        },
        "action_details": "Incident report received and endorsed for initial review by the JHS Coordinator.",
    },
    {
        "ref": f"{SEED_PREFIX}JHS-002",
        "source_type": "complaint",
        "status": DisciplinaryCase.Status.INVESTIGATION,
        "case_level": DisciplinaryCase.CaseLevel.JHS,
        "classification": DisciplinaryCase.Classification.MAJOR,
        "academic_year": AY,
        "violation_code": "JHS-J-001",
        "days_ago": 20,
        "report": {
            "report_type": "complaint",
            "complainant_name": "Lorenzo Reyes",
            "complainant_student_id": "JHS-2024-0038",
            "complainant_program": "Grade 9",
            "complainant_year_section": "Grade 9 — Rizal",
            "respondent_name": "Miguel Santos",
            "respondent_student_id": "JHS-2024-0051",
            "respondent_program": "Grade 9",
            "respondent_year_section": "Grade 9 — Rizal",
            "incident_place": "School Corridor — 2nd Floor Building A",
            "narration": (
                "I am filing this complaint because the respondent has been bullying me since "
                "October 2024. He repeatedly calls me offensive names in front of our classmates "
                "and has pushed me twice on the corridor. On October 25, he blocked my way to class "
                "and threatened to hurt me if I reported him. I have witnesses among our classmates "
                "who have seen these incidents. I am afraid to come to school because of his behavior."
            ),
            "witness_information": "Witnesses: Andrei Cruz (JHS-2024-0040), Leia Morales (JHS-2024-0042).",
        },
        "action_details": "Complaint reviewed and accepted for formal investigation. Both students summoned.",
    },
    {
        "ref": f"{SEED_PREFIX}JHS-003",
        "source_type": "incident",
        "status": DisciplinaryCase.Status.RESOLVED,
        "case_level": DisciplinaryCase.CaseLevel.JHS,
        "classification": DisciplinaryCase.Classification.MINOR,
        "academic_year": AY,
        "violation_code": "JHS-J-002",
        "days_ago": 45,
        "finding": DisciplinaryCase.Finding.SUBSTANTIATED,
        "final_sanction": DisciplinaryCase.Sanction.OTHER,
        "decision_notes": (
            "Respondent admitted responsibility. Restitution paid for damaged classroom chair. "
            "Written apology submitted to the class adviser. Counselling completed. "
            "Case resolved with written warning on record."
        ),
        "report": {
            "report_type": "incident",
            "reporting_position": "JHS Prefect of Discipline",
            "respondent_name": "Aaron Dela Cruz",
            "respondent_student_id": "JHS-2024-0062",
            "respondent_program": "Grade 7",
            "respondent_year_section": "Grade 7 — Bonifacio",
            "incident_place": "Classroom 7-Bonifacio",
            "narration": (
                "On September 25, 2024, the respondent was found to have intentionally broken "
                "a classroom chair by forcing the backrest until it snapped. A classmate witnessed "
                "the act and reported it to the class adviser. The damaged chair was assessed by "
                "the school maintenance office. The respondent initially denied the act but later "
                "admitted to it when confronted with the witness account."
            ),
            "witness_information": "Eyewitness: Tricia Lim (JHS-2024-0064). Maintenance assessment report on file.",
        },
        "action_details": "Case resolved. Restitution completed. Written warning issued and filed.",
        "closed_at_days_ago": 10,
    },

    # ══════════════════════════════════════════════════════
    #  SHS CASES
    # ══════════════════════════════════════════════════════
    {
        "ref": f"{SEED_PREFIX}SHS-001",
        "source_type": "incident",
        "status": DisciplinaryCase.Status.INITIAL_REVIEW,
        "case_level": DisciplinaryCase.CaseLevel.SHS,
        "classification": DisciplinaryCase.Classification.MINOR,
        "academic_year": AY,
        "violation_code": "SHS-M-001",
        "days_ago": 5,
        "report": {
            "report_type": "incident",
            "reporting_position": "SHS Subject Teacher",
            "respondent_name": "Sophia Villanueva",
            "respondent_student_id": "SHS-2024-0101",
            "respondent_program": "HUMSS Strand",
            "respondent_year_section": "Grade 11 — HUMSS-A",
            "incident_place": "English & Literature Class — Room 203",
            "narration": (
                "On November 5, 2024, during the oral recitation period, the respondent was "
                "found using her mobile phone under the desk to send messages. When asked to "
                "surrender the device, she initially refused before complying. This is the "
                "second instance within this grading period. The first occurrence was addressed "
                "informally by the subject teacher. Per school policy, a formal report is now "
                "required for this recurring behavior."
            ),
            "witness_information": "Present students in HUMSS-A and subject teacher on duty.",
        },
        "action_details": "Report received. Referred to SHS Strand Coordinator for guidance intervention.",
    },
    {
        "ref": f"{SEED_PREFIX}SHS-002",
        "source_type": "complaint",
        "status": DisciplinaryCase.Status.HEARING,
        "case_level": DisciplinaryCase.CaseLevel.SHS,
        "classification": DisciplinaryCase.Classification.MAJOR,
        "academic_year": AY,
        "violation_code": "SHS-S-001",
        "days_ago": 30,
        "report": {
            "report_type": "complaint",
            "complainant_name": "Isabelle Torres",
            "complainant_student_id": "SHS-2024-0089",
            "complainant_program": "ABM Strand",
            "complainant_year_section": "Grade 12 — ABM-B",
            "respondent_name": "Daniel Gutierrez",
            "respondent_student_id": "SHS-2024-0094",
            "respondent_program": "ABM Strand",
            "respondent_year_section": "Grade 12 — ABM-B",
            "incident_place": "Practical Examination — Room 210",
            "narration": (
                "During our Business Math practical examination on October 11, 2024, I noticed "
                "the respondent photographing my answer sheet using his phone hidden under the "
                "desk. Our subject teacher, Ms. Racquel Delos Santos, also observed this and "
                "confiscated his phone. The photos were found in his gallery. I am filing this "
                "formal complaint because this is a clear act of cheating that compromises the "
                "integrity of our assessments and my own academic standing."
            ),
            "witness_information": "Subject Teacher: Ms. Racquel Delos Santos (confirmed). Classmates present.",
        },
        "action_details": "Complaint accepted. Evidence reviewed. Formal hearing scheduled with SHS Discipline Committee.",
    },
    {
        "ref": f"{SEED_PREFIX}SHS-003",
        "source_type": "incident",
        "status": DisciplinaryCase.Status.DECISION,
        "case_level": DisciplinaryCase.CaseLevel.SHS,
        "classification": DisciplinaryCase.Classification.SERIOUS,
        "academic_year": AY,
        "violation_code": "SHS-S-003",
        "days_ago": 55,
        "report": {
            "report_type": "incident",
            "reporting_position": "Security Guard — Main Gate",
            "respondent_name": "Renzo Aquino",
            "respondent_student_id": "SHS-2024-0077",
            "respondent_program": "STEM Strand",
            "respondent_year_section": "Grade 11 — STEM-A",
            "incident_place": "Main Entrance Gate",
            "narration": (
                "On September 15, 2024 at 7:30 AM, during the routine bag inspection at the main "
                "entrance, security personnel discovered a 15 cm bladed knife in the respondent's "
                "bag. The respondent claimed it was for a scout activity but could not present any "
                "authorization. The item was confiscated and secured as evidence. The respondent "
                "was cooperative throughout. This report is filed per the school's security protocol "
                "and the SHS Student Handbook §4.2.3."
            ),
            "witness_information": "Guard on duty: Officer Rodel Cruz. Item secured at the security office. CCTV footage available.",
        },
        "action_details": "Investigation completed. Preventive suspension served. Awaiting final decision from the SHS Principal.",
    },

    # ══════════════════════════════════════════════════════
    #  COLLEGE CASES
    # ══════════════════════════════════════════════════════
    {
        "ref": f"{SEED_PREFIX}COL-001",
        "source_type": "complaint",
        "status": DisciplinaryCase.Status.INITIAL_REVIEW,
        "case_level": DisciplinaryCase.CaseLevel.COLLEGE,
        "classification": DisciplinaryCase.Classification.MINOR,
        "academic_year": AY,
        "violation_code": "COL-M-001",
        "days_ago": 6,
        "report": {
            "report_type": "complaint",
            "complainant_name": "Marielle Lim",
            "complainant_student_id": "2023-IT-0432",
            "complainant_program": "BS Information Technology",
            "complainant_year_section": "2nd Year — Section A",
            "respondent_name": "Jairus Mendoza",
            "respondent_student_id": "2023-IT-0458",
            "respondent_program": "BS Information Technology",
            "respondent_year_section": "2nd Year — Section A",
            "incident_place": "Data Structures and Algorithms Class — IT Lab 1",
            "narration": (
                "I am filing this complaint because the respondent has been consistently "
                "disrupting our Data Structures class. During the November 4 session, he "
                "repeatedly interrupted the lecturer with off-topic remarks, made loud comments "
                "mocking my recitation answers, and refused to stop when warned by the professor. "
                "This is not the first time; similar behavior was noted in prior weeks. "
                "His conduct is affecting the learning of the entire class."
            ),
            "witness_information": "Subject professor: Engr. Patrick Aguilar. Present classmates in BSIT 2A.",
        },
        "action_details": "Complaint received and acknowledged. Referred to Program Chair for initial review.",
    },
    {
        "ref": f"{SEED_PREFIX}COL-002",
        "source_type": "incident",
        "status": DisciplinaryCase.Status.INVESTIGATION,
        "case_level": DisciplinaryCase.CaseLevel.COLLEGE,
        "classification": DisciplinaryCase.Classification.MAJOR,
        "academic_year": AY,
        "violation_code": "COL-J-001",
        "days_ago": 25,
        "report": {
            "report_type": "incident",
            "reporting_position": "College Security Officer",
            "respondent_name": "Aldrin Pascual",
            "respondent_student_id": "2022-CE-0317",
            "respondent_program": "BS Civil Engineering",
            "respondent_year_section": "3rd Year — Section B",
            "incident_place": "College Canteen Area",
            "narration": (
                "On October 16, 2024 at approximately 12:40 PM, a physical altercation broke out "
                "between the respondent and another student near the canteen area. Security "
                "personnel intervened after the respondent was observed throwing a punch at the "
                "other student, who sustained a minor cut on the lip. Both students were brought "
                "to the security office. The respondent stated the altercation started over a "
                "disagreement from their group project. CCTV footage captures the full incident. "
                "The other student is being handled under a separate case."
            ),
            "witness_information": (
                "Security Officers: Ernesto Bautista, Rodel Flores. CCTV footage secured. "
                "College Nurse report on the other student's injury attached."
            ),
        },
        "action_details": "Incident report accepted. Preventive suspension order served. Investigation ongoing.",
    },
    {
        "ref": f"{SEED_PREFIX}COL-003",
        "source_type": "complaint",
        "status": DisciplinaryCase.Status.CLOSED,
        "case_level": DisciplinaryCase.CaseLevel.COLLEGE,
        "classification": DisciplinaryCase.Classification.MAJOR,
        "academic_year": AY,
        "violation_code": "COL-J-002",
        "days_ago": 70,
        "finding": DisciplinaryCase.Finding.SUBSTANTIATED,
        "final_sanction": DisciplinaryCase.Sanction.SUSPENSION,
        "decision_notes": (
            "After full investigation and hearing, the committee found the respondent guilty of "
            "academic dishonesty. The respondent's thesis chapter submission was confirmed to "
            "contain 68% plagiarized content from an unpublished thesis. Sanction: 5-day "
            "suspension, zero mark on the submitted chapter, mandatory academic integrity "
            "seminar, and a formal written warning on the student's permanent record."
        ),
        "report": {
            "report_type": "complaint",
            "complainant_name": "Prof. Carla Soriano",
            "complainant_student_id": "",
            "complainant_program": "",
            "complainant_year_section": "",
            "complainant_relationship": "Thesis Adviser",
            "respondent_name": "Beatrice Navarro",
            "respondent_student_id": "2020-NUR-0199",
            "respondent_program": "BS Nursing",
            "respondent_year_section": "4th Year — Section A",
            "incident_place": "Thesis Submission — College of Nursing",
            "narration": (
                "I am the thesis adviser of the respondent. Upon reviewing her submitted Chapter 3 "
                "draft on September 1, 2024, I ran it through a plagiarism detection tool and found "
                "that 68% of the content was directly lifted from an unpublished thesis from another "
                "institution available in an online repository. The respondent had presented this as "
                "entirely original work and signed the academic integrity declaration. This constitutes "
                "a grave violation of our institution's academic integrity policy and I am compelled "
                "to file this formal complaint."
            ),
            "witness_information": (
                "Plagiarism detection report (Turnitin) attached. Source thesis URL on file. "
                "Co-adviser Dr. Ramon Gutierrez has reviewed and concurs."
            ),
        },
        "action_details": (
            "Case fully resolved. 5-day suspension served. Academic integrity seminar completed. "
            "Written warning issued. Case closed."
        ),
        "closed_at_days_ago": 15,
    },
]


class Command(BaseCommand):
    help = "Seed example policies (OffenseCategory, Violation, PenaltyCatalog) and cases for JHS, SHS, and College."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove all seeded department data before re-seeding.",
        )

    def handle(self, *args, **options):
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            self.stderr.write(self.style.ERROR(
                "No active superuser found. Create one first:\n"
                "  python manage.py createsuperuser"
            ))
            return

        if options["clear"]:
            self._clear_seeded_data()

        self._seed_categories()
        self._seed_violations(admin)
        self._seed_penalties(admin)
        self._seed_cases(admin)

        self.stdout.write()
        self.stdout.write(self.style.SUCCESS("✔  Department data seeding complete."))

    # ──────────────────────────────────────────────────────────────────────────
    def _clear_seeded_data(self):
        # Cases (cascades to reports, persons, actions, audit logs)
        deleted_cases, _ = DisciplinaryCase.objects.filter(reference__startswith=SEED_PREFIX).delete()
        # Violations (and their penalties cascade)
        codes = [v["code"] for v in VIOLATIONS]
        deleted_violations, _ = Violation.objects.filter(code__in=codes).delete()
        # Categories seeded by this command
        names = [c["name"] for c in CATEGORIES]
        deleted_cats, _ = OffenseCategory.objects.filter(name__in=names).delete()
        self.stdout.write(self.style.WARNING(
            f"Cleared: {deleted_cases} case(s), {deleted_violations} violation(s), {deleted_cats} category(ies)."
        ))

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_categories(self):
        self.stdout.write("\n── Seeding offense categories (policies) ─────────────────")
        for data in CATEGORIES:
            obj, created = OffenseCategory.objects.get_or_create(
                name=data["name"],
                defaults={
                    "description": data.get("description", ""),
                    "policy_reference": data.get("policy_reference", ""),
                    "classification": data.get("classification", OffenseCategory.Classification.UNCLASSIFIED),
                    "review_authority": data.get("review_authority", ""),
                    "policy_version": data.get("policy_version", "2024-A"),
                    "escalation_rules": data.get("escalation_rules", ""),
                    "restitution_required": data.get("restitution_required", False),
                    "active": True,
                },
            )
            label = "CREATED" if created else "EXISTS "
            self.stdout.write(f"  {label}  {obj.name}")

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_violations(self, admin):
        self.stdout.write("\n── Seeding violations ────────────────────────────────────")
        for data in VIOLATIONS:
            if Violation.objects.filter(code=data["code"]).exists():
                self.stdout.write(f"  EXISTS   {data['code']} — {data['name']}")
                continue
            try:
                category = OffenseCategory.objects.get(name=data["category"])
            except OffenseCategory.DoesNotExist:
                self.stderr.write(self.style.ERROR(
                    f"  ERROR: category '{data['category']}' not found for {data['code']}"
                ))
                continue
            Violation.objects.create(
                code=data["code"],
                name=data["name"],
                description=data.get("description", ""),
                severity=data["severity"],
                classification=data.get("classification", ""),
                applicable_rule=data.get("applicable_rule", ""),
                recommended_penalty=data.get("recommended_penalty", ""),
                policy_reference=category.policy_reference,
                category=category,
                created_by=admin,
                active=True,
            )
            self.stdout.write(self.style.SUCCESS(f"  CREATED  {data['code']} — {data['name']}"))

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_penalties(self, admin):
        self.stdout.write("\n── Seeding penalty catalog ───────────────────────────────")
        PENALTY_MAP = {
            # JHS
            "JHS-M-001": ("Verbal Warning / Written Warning", "Verbal reprimand; written warning on 3rd instance.", None),
            "JHS-M-002": ("Guidance Counselling (Dress Code)", "Guidance session and parent notification letter.", None),
            "JHS-M-003": ("Jug Assignment — Class Disruption", "1–2 hour Jug assignment with counselling.", None),
            "JHS-J-001": ("Suspension — Bullying (JHS)", "Suspension up to 5 school days; mandatory counselling.", 5),
            "JHS-J-002": ("Restitution + Suspension — Vandalism (JHS)", "Restitution of property; suspension 2–5 school days.", 3),
            # SHS
            "SHS-M-001": ("Device Confiscation + Counselling", "Device held for the day; guidance intervention.", None),
            "SHS-M-002": ("Jug Assignment — Unauthorized Absence", "Jug/Post assignment; written parent notice.", None),
            "SHS-S-001": ("Suspension — Academic Dishonesty (SHS)", "Zero mark on output; suspension 3–7 school days.", 7),
            "SHS-S-002": ("Suspension — Cyberbullying (SHS)", "Suspension 5–10 school days; mandatory counselling.", 10),
            "SHS-S-003": ("Preventive Suspension — Prohibited Item (SHS)", "Confiscation; immediate preventive suspension pending investigation.", 5),
            # College
            "COL-M-001": ("Verbal Warning / Post Assignment", "Verbal warning; Post assignment on second offense.", None),
            "COL-M-002": ("Post Assignment — Uniform Violation", "One-hour Post assignment.", None),
            "COL-M-003": ("Jug/Post — Habitual Tardiness (College)", "Jug or Post assignment from 3rd offense.", None),
            "COL-J-001": ("Suspension — Physical Violence (College)", "Suspension 5–10 school days; anger management.", 10),
            "COL-J-002": ("Suspension — Academic Dishonesty (College)", "Zero mark; suspension 3–7 school days.", 7),
            "COL-J-003": ("Suspension — Harassment (College)", "Suspension 5–15 school days; possible dismissal.", 15),
            "COL-G-001": ("Preventive Suspension + Expulsion Review — Drugs", "Immediate suspension; full board review; possible expulsion.", 15),
            "COL-G-002": ("Preventive Suspension + Expulsion Review — Sexual Misconduct", "Immediate suspension; full board hearing; expulsion review.", 15),
        }
        for code, (name, description, duration) in PENALTY_MAP.items():
            try:
                violation = Violation.objects.get(code=code)
            except Violation.DoesNotExist:
                self.stdout.write(f"  SKIP  Penalty for {code}: violation not found.")
                continue
            _, created = PenaltyCatalog.objects.get_or_create(
                name=name,
                violation=violation,
                defaults={
                    "description": description,
                    "duration_days": duration,
                    "policy_reference": violation.policy_reference,
                    "active": True,
                    "approved": False,
                    "created_by": admin,
                },
            )
            label = "CREATED" if created else "EXISTS "
            self.stdout.write(f"  {label}  [{code}] {name}")

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_cases(self, admin):
        self.stdout.write("\n── Seeding disciplinary cases ────────────────────────────")
        for data in CASES:
            ref = data["ref"]
            if DisciplinaryCase.objects.filter(reference=ref).exists():
                self.stdout.write(f"  EXISTS   {ref}")
                continue

            days_ago = data.get("days_ago", 15)
            opened_at = timezone.now() - timedelta(days=days_ago)

            # Resolve violation if specified
            violation = None
            vcode = data.get("violation_code")
            if vcode:
                violation = Violation.objects.filter(code=vcode).first()

            # Resolve offense category from violation
            offense_category = violation.category if violation else None

            # 1. Create the case
            closed_at = None
            if data.get("closed_at_days_ago"):
                closed_at = timezone.now() - timedelta(days=data["closed_at_days_ago"])

            case = DisciplinaryCase(
                reference=ref,
                source_type=data["source_type"],
                status=data["status"],
                academic_year=data.get("academic_year", AY),
                case_level=data.get("case_level", ""),
                classification=data.get("classification", ""),
                finding=data.get("finding", DisciplinaryCase.Finding.NOT_DETERMINED),
                final_sanction=data.get("final_sanction", DisciplinaryCase.Sanction.NONE),
                decision_notes=data.get("decision_notes", ""),
                offense_category=offense_category,
                violation=violation,
                opened_at=opened_at,
                assigned_officer=admin,
                closed_at=closed_at,
            )
            case.save()
            if violation:
                case.violations.add(violation)

            # 2. Create the report
            rdata = data["report"]
            CaseReport.objects.create(
                case=case,
                report_type=rdata["report_type"],
                submitted_by=admin,
                complainant_name=rdata.get("complainant_name", ""),
                complainant_student_id=rdata.get("complainant_student_id", ""),
                complainant_program=rdata.get("complainant_program", ""),
                complainant_year_section=rdata.get("complainant_year_section", ""),
                complainant_relationship=rdata.get("complainant_relationship", ""),
                reporting_position=rdata.get("reporting_position", ""),
                respondent_name=rdata.get("respondent_name", ""),
                respondent_student_id=rdata.get("respondent_student_id", ""),
                respondent_program=rdata.get("respondent_program", ""),
                respondent_year_section=rdata.get("respondent_year_section", ""),
                incident_at=opened_at - timedelta(days=1),
                incident_place=rdata.get("incident_place", ""),
                narration=rdata.get("narration", ""),
                witness_information=rdata.get("witness_information", ""),
                acknowledgment=True,
                submitted_at=opened_at,
                received_at=opened_at,
            )

            # 3. Create the respondent person
            CasePerson.objects.create(
                case=case,
                role=CasePerson.Role.RESPONDENT,
                name=rdata.get("respondent_name", ""),
                student_id=rdata.get("respondent_student_id", ""),
                program=rdata.get("respondent_program", ""),
                year_section=rdata.get("respondent_year_section", ""),
            )

            # 4. Create a case action
            CaseAction.objects.create(
                case=case,
                action_type=CaseAction.ActionType.STATUS,
                to_status=data["status"],
                details=data.get("action_details", ""),
                actor=admin,
            )

            # 5. If resolved/closed, add a finding action
            if data.get("finding") and data["finding"] != DisciplinaryCase.Finding.NOT_DETERMINED:
                CaseAction.objects.create(
                    case=case,
                    action_type=CaseAction.ActionType.FINDING,
                    finding=data["finding"],
                    details=data.get("decision_notes", ""),
                    actor=admin,
                )

            # 6. Audit log
            CaseAuditLog.objects.create(
                case=case,
                actor=admin,
                action="seeded",
                details=f"Example {data.get('case_level','').upper()} case created by seed_department_data.",
            )

            level = data.get("case_level", "").upper().ljust(7)
            self.stdout.write(self.style.SUCCESS(
                f"  CREATED  {ref}  [{level}]  {rdata.get('respondent_name','N/A'):30s}  "
                f"Status: {data['status']}"
            ))

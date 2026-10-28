from django.urls import path

from . import views

app_name = 'discipline'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('cases/', views.case_list, name='case_list'),
    path('cases/complaint/new/', views.complaint_create, name='complaint_create'),
    path('cases/incident/new/', views.incident_create, name='incident_create'),
    path('cases/<int:pk>/', views.case_detail, name='case_detail'),
    path('cases/<int:pk>/actions/new/', views.case_action, name='case_action'),
    path('cases/<int:pk>/stages/new/', views.stage_create, name='stage_create'),
    path('cases/<int:pk>/summons/new/', views.summons_create, name='summons_create'),
    path('cases/<int:pk>/summons/<int:summons_id>/service/', views.summons_serve, name='summons_serve'),
    path('cases/<int:pk>/summons/<int:summons_id>/extension/', views.summons_extension, name='summons_extension'),
    path('cases/<int:pk>/answer/', views.answer_submit, name='answer_submit'),
    path('policy/', views.policy_list, name='policy_list'),
    path('policy/new/', views.policy_edit, name='policy_create'),
    path('policy/<int:pk>/', views.policy_detail, name='policy_detail'),
    path('policy/<int:pk>/edit/', views.policy_edit, name='policy_edit'),
    path('committee/', views.committee_list, name='committee_list'),
    path('committee/new/', views.committee_create, name='committee_create'),
    path('committee/<int:pk>/', views.committee_detail, name='committee_detail'),
    path('committee/<int:pk>/members/new/', views.committee_member_add, name='committee_member_add'),
    path('committee/<int:pk>/meetings/new/', views.meeting_create, name='meeting_create'),
    path('knights/report/', views.knights_report, name='knights_report'),
    path('knights/observations/', views.knights_observations, name='knights_observations'),
    path('knights/observations/<int:pk>/review/', views.knights_observation_review, name='knights_observation_review'),
    path('students/history/', views.confidential_profile, name='confidential_profile'),
    path('monitoring/probation/', views.probation_list, name='probation_list'),
    path('monitoring/suspensions/', views.suspension_list, name='suspension_list'),
    path('reports/', views.reports, name='reports'),
    path('intake/', views.intake, name='intake'),
    path('intake/<int:pk>/success/', views.intake_success, name='intake_success'),
    path('log/incidents/', views.incident_log, name='incident_log'),
    path('log/policies/', views.policy_log, name='policy_log'),
]

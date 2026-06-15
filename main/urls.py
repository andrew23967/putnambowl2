from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    path('home/<int:week>/', views.home, name='home'),
    path('picks/', views.pickform, name='pickform'),
    path('picks/save/', views.ajax_save_pick, name='ajax_save_pick'),
    path('allpicks/', views.allpicks, name='allpicks'),
    path('history/', views.pickhistory, name='pickhistory'),
    path('preseason/', views.preseason, name='preseason'),
    path('standings/', views.standings_view, name='standings'),
    path('rules/', views.rules, name='rules'),
    path('seasons/', views.seasons, name='seasons'),
    path('bugreport/', views.bugreport, name='bugreport'),
    path('dashboard/picks/', views.pickdash, name='pickdash'),
    path('dashboard/accounts/', views.accountdash, name='accountdash'),
    path('dashboard/announcements/', views.announcements, name='announcements'),
    path('dashboard/bugs/', views.buglog, name='buglog'),
    path('dashboard/analytics/', views.secret_analytics, name='secret_analytics'),
    path('dashboard/generate-recap/', views.generate_recap, name='generate_recap'),
    path('dashboard/picks/set-winner/', views.ajax_set_winner, name='ajax_set_winner'),
]

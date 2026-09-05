from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    path('home/', views.home, name='home'),
    path('site-state/', views.site_state, name='site_state'),
    path('home/leaderboard/', views.ajax_leaderboard, name='ajax_leaderboard'),
    path('picks/', views.picks, name='picks'),
    path('picks/save/', views.ajax_save_pick, name='ajax_save_pick'),
    path('history/data/', views.ajax_history, name='ajax_history'),
    path('preseason/', views.preseason, name='preseason'),
    path('rules/', views.rules, name='rules'),
    path('dashboard/rules/', views.rulesdash, name='rulesdash'),
    path('seasons/', views.seasons, name='seasons'),
    path('seasons/<int:year>/', views.season, name='season'),
    path('members/', views.members, name='members'),
    path('dashboard/picks/', views.pickdash, name='pickdash'),
    path('dashboard/accounts/', views.accountdash, name='accountdash'),
    path('dashboard/emails/', views.emaildash, name='emaildash'),
    path('dashboard/accounts/edit/<int:user_id>/', views.edit_player, name='edit_player'),
    path('dashboard/accounts/delete/<int:user_id>/', views.delete_player, name='delete_player'),
    path('pick-history/', views.pick_history, name='pick_history'),
    path('dashboard/generate-recap/', views.generate_recap, name='generate_recap'),
    path('dashboard/send-test-email/', views.send_test_email, name='send_test_email'),
    path('dashboard/picks/delete-game/', views.ajax_delete_game, name='ajax_delete_game'),
    path('dashboard/picks/set-winner/', views.ajax_set_winner, name='ajax_set_winner'),
    path('dashboard/picks/add-game/', views.ajax_add_game, name='ajax_add_game'),
]

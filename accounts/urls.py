from django.contrib.auth.views import PasswordChangeView
from django.urls import path, reverse_lazy
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register, name='register'),
    path('join/<str:code>/', views.register, name='join'),
    path('password/', PasswordChangeView.as_view(
        template_name='accounts/password_change.html',
        success_url=reverse_lazy('accounts:user_profile')), name='password_change'),
    path('userprofile/', views.user_profile, name='user_profile'),
    path('', views.login_view, name='index'),
]

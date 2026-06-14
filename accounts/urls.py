from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register, name='register'),
    path('userprofile/', views.user_profile, name='user_profile'),
    path('profile/<str:username>/', views.public_profile, name='public_profile'),
    path('', views.login_view, name='index'),
]

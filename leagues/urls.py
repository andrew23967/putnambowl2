from django.urls import path

from . import views

app_name = 'leagues'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('', views.index, name='index'),
    path('new/', views.create, name='create'),
    path('<slug:slug>/', views.edit, name='edit'),
]

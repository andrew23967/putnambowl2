from django.contrib import admin
from .models import Profile

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'score', 'real_name', 'favorite_team']
    search_fields = ['user__username', 'real_name']

from django.contrib import admin
from .models import Profile

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'league', 'role', 'score', 'real_name', 'favorite_team']
    list_filter = ['league', 'role', 'is_bot']
    search_fields = ['user__username', 'real_name']

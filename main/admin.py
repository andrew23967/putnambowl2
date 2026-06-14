from django.contrib import admin
from .models import Game, Pick, SiteSettings, History, WeeklyLeaderboard, Announcement, Bug, SeasonRecord


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ['week', 'publish', 'edit', 'lock_picks', 'multiplier', 'grade_api']


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ['team1', 'team2', 'points1', 'points2', 'winner', 'graded', 'date']
    list_filter = ['graded']


@admin.register(Pick)
class PickAdmin(admin.ModelAdmin):
    list_display = ['user', 'game', 'choice']
    list_filter = ['choice']
    search_fields = ['user__username']


@admin.register(History)
class HistoryAdmin(admin.ModelAdmin):
    list_display = ['week']


@admin.register(WeeklyLeaderboard)
class WeeklyLeaderboardAdmin(admin.ModelAdmin):
    list_display = ['week']


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['message', 'created_at']


@admin.register(Bug)
class BugAdmin(admin.ModelAdmin):
    list_display = ['finder', 'description', 'resolved', 'created_at']
    list_filter = ['resolved']


@admin.register(SeasonRecord)
class SeasonRecordAdmin(admin.ModelAdmin):
    list_display = ['year', 'winner_username', 'created_at']

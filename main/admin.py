from django.contrib import admin
from .models import Game, IntroTemplate, Pick, LeagueSettings, WeeklyLeaderboard, LeagueEmail, SeasonRecord


@admin.register(LeagueSettings)
class LeagueSettingsAdmin(admin.ModelAdmin):
    list_display = ['league', 'week', 'publish', 'lock_picks', 'multiplier', 'auto_enabled']
    list_filter = ['league']


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ['league', 'week', 'team1', 'team2', 'points1', 'points2', 'winner', 'graded', 'game_dt']
    list_filter = ['league', 'graded', 'week']


@admin.register(Pick)
class PickAdmin(admin.ModelAdmin):
    list_display = ['user', 'game', 'choice']
    list_filter = ['choice']
    search_fields = ['user__username']



@admin.register(WeeklyLeaderboard)
class WeeklyLeaderboardAdmin(admin.ModelAdmin):
    list_display = ['league', 'week']
    list_filter = ['league']


@admin.register(IntroTemplate)
class IntroTemplateAdmin(admin.ModelAdmin):
    list_display = ['league', 'name']
    list_filter = ['league']


@admin.register(LeagueEmail)
class LeagueEmailAdmin(admin.ModelAdmin):
    list_display = ['league', 'sent_at', 'author', 'from_email', 'subject', 'source',
                    'recipient_count', 'published']
    list_filter = ['league', 'published', 'source']
    search_fields = ['subject', 'body', 'from_email', 'author__username']
    readonly_fields = ['received_at', 'message_id']


@admin.register(SeasonRecord)
class SeasonRecordAdmin(admin.ModelAdmin):
    list_display = ['league', 'year', 'winner_username', 'created_at']
    list_filter = ['league']

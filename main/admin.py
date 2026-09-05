from django.contrib import admin
from .models import Game, Pick, SiteSettings, WeeklyLeaderboard, LeagueEmail, SeasonRecord


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ['week', 'publish', 'lock_picks', 'multiplier', 'scrape_api', 'grade_api']


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ['week', 'team1', 'team2', 'points1', 'points2', 'winner', 'graded', 'game_dt']
    list_filter = ['graded', 'week']


@admin.register(Pick)
class PickAdmin(admin.ModelAdmin):
    list_display = ['user', 'game', 'choice']
    list_filter = ['choice']
    search_fields = ['user__username']



@admin.register(WeeklyLeaderboard)
class WeeklyLeaderboardAdmin(admin.ModelAdmin):
    list_display = ['week']


@admin.register(LeagueEmail)
class LeagueEmailAdmin(admin.ModelAdmin):
    list_display = ['sent_at', 'author', 'from_email', 'subject', 'source',
                    'recipient_count', 'published']
    list_filter = ['published', 'source']
    search_fields = ['subject', 'body', 'from_email', 'author__username']
    readonly_fields = ['received_at', 'message_id']


@admin.register(SeasonRecord)
class SeasonRecordAdmin(admin.ModelAdmin):
    list_display = ['year', 'winner_username', 'created_at']

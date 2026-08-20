from django.contrib import admin

from mezzanine.accounts.admin import UserProfileAdmin 
from django.contrib.auth.models import User 

class User_profileAdmin(UserProfileAdmin):
    list_display = UserProfileAdmin.list_display + ("project", "laboratory",)

    def laboratory(self, instance):
        return instance.user_profile.laboratory

    def project(self, instance):
        return instance.user_profile.project

admin.site.unregister(User)
admin.site.register(User, User_profileAdmin)

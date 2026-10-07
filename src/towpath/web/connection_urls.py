"""URLs of the connector's setup server. The reverse proxy sends /connections/ here; sign-in and the stylesheet
are also served so the server works on its own during development."""

from django.urls import path

from towpath.web import auth, connection_views as v, views

urlpatterns = [
    path("connections/", v.connection_list, name="connections"),
    path("connections/add/<str:provider>", v.add, name="connection-add"),
    path("connections/<str:sid>/", v.detail, name="connection"),
    path("connections/<str:sid>/folders", v.folders, name="connection-folders"),
    path("connections/<str:sid>/start", v.start, name="connection-start"),
    path("connections/<str:sid>/pause", v.pause, name="connection-pause"),
    path("connections/<str:sid>/password", v.password, name="connection-password"),
    path("connections/<str:sid>/disconnect", v.disconnect, name="connection-disconnect"),
    path("setup", auth.setup, name="setup"),
    path("login", auth.login, name="login"),
    path("logout", auth.logout, name="logout"),
    path("assets/app.css", views.stylesheet, name="stylesheet"),
]

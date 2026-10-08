from django.urls import path

from towpath.web import auth, unified_views, views

urlpatterns = [
    path("", views.overview, name="overview"),
    path("setup", auth.setup, name="setup"),
    path("login", auth.login, name="login"),
    path("logout", auth.logout, name="logout"),
    path("email/", views.emails, name="emails"),
    path("email/<str:item_id>/", views.email, name="email"),
    path("search/", unified_views.search, name="search"),
    path("search/request", unified_views.search_request, name="search-request"),
    path("ref/", unified_views.reference, name="reference"),
    path("ref/request", unified_views.reference_request, name="reference-request"),
    path("collections/", unified_views.collection_list, name="collections"),
    path("connections/", views.connections_elsewhere, name="connections-elsewhere"),
    path("collections/new", unified_views.collection_create, name="collection-create"),
    path("collections/<str:cid>/", unified_views.collection_detail, name="collection"),
    path("collections/<str:cid>/add", unified_views.collection_add, name="collection-add"),
    path("collections/<str:cid>/remove", unified_views.collection_remove, name="collection-remove"),
    path("collections/<str:cid>/accept", unified_views.collection_accept, name="collection-accept"),
    path("assets/app.css", views.stylesheet, name="stylesheet"),
    path("assets/live.js", views.live_script, name="live-script"),
]

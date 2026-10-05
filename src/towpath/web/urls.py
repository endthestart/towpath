from django.urls import path

from towpath.web import views

urlpatterns = [
    path("", views.overview, name="overview"),
    path("email/", views.emails, name="emails"),
    path("email/<str:item_id>/", views.email, name="email"),
    path("assets/app.css", views.stylesheet, name="stylesheet"),
]

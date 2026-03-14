from django.urls import path

from helloworld.views import hello, health

urlpatterns = [
    path("", hello, name="hello"),
    path("health/", health, name="health"),
]

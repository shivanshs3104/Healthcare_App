from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    RegisterView,
    LoginView,
    PatientViewSet,
    DoctorViewSet,
    MappingListCreateView,
    MappingDetailView,
)

router = DefaultRouter()
router.register("patients", PatientViewSet, basename="patient")
router.register("doctors", DoctorViewSet, basename="doctor")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),

    path("mappings/", MappingListCreateView.as_view(), name="mappings"),
    path(
        "mappings/<int:patient_id>/",
        MappingDetailView.as_view(),
        name="mapping-detail",
    ),

    path("", include(router.urls)),
]
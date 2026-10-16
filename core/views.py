from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers, viewsets, generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from rest_framework_simplejwt.tokens import RefreshToken

from .models import Patient, Doctor, PatientDoctorMapping


# ---------- SERIALIZERS ----------

class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Email already registered.")
        return value

    def create(self, validated_data):
        email = validated_data["email"]
        name = validated_data["name"]

        user = User.objects.create_user(
            username=email,
            email=email,
            password=validated_data["password"],
            first_name=name,
        )
        return user


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "id", "name", "age", "gender",
            "address", "phone", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_age(self, value):
        if value < 1 or value > 120:
            raise serializers.ValidationError(
                "Age must be between 1 and 120."
            )
        return value


class DoctorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Doctor
        fields = [
            "id", "name", "specialization",
            "email", "phone",
        ]
        read_only_fields = ["id"]


class MappingSerializer(serializers.ModelSerializer):
    doctor_details = DoctorSerializer(
        source="doctor",
        read_only=True,
    )

    class Meta:
        model = PatientDoctorMapping
        fields = [
            "id", "patient", "doctor",
            "doctor_details", "assigned_at",
        ]
        read_only_fields = ["id", "assigned_at"]

    def validate(self, attrs):
        request = self.context["request"]
        patient = attrs.get(
            "patient",
            getattr(self.instance, "patient", None),
        )
        doctor = attrs.get(
            "doctor",
            getattr(self.instance, "doctor", None),
        )

        if patient and patient.created_by_id != request.user.id:
            raise serializers.ValidationError({
                "patient": "You can only assign doctors to your patients."
            })

        if doctor and patient and PatientDoctorMapping.objects.filter(
            patient=patient,
            doctor=doctor,
        ).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError(
                "This doctor is already assigned to this patient."
            )

        return attrs


# ---------- AUTHENTICATION ----------

class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "message": "Registration successful.",
                "user": {
                    "id": user.id,
                    "name": user.first_name,
                    "email": user.email,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        password = request.data.get("password", "")

        if not email or not password:
            return Response(
                {"error": "Email and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(
            request,
            username=email,
            password=password,
        )

        if user is None:
            return Response(
                {"error": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)

        return Response({
            "message": "Login successful.",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {
                "id": user.id,
                "name": user.first_name,
                "email": user.email,
            },
        })


# ---------- OBJECT PERMISSIONS ----------

class CreatorOnlyMixin:
    def perform_update(self, serializer):
        if serializer.instance.created_by_id != self.request.user.id:
            raise PermissionDenied("You cannot edit this record.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.created_by_id != self.request.user.id:
            raise PermissionDenied("You cannot delete this record.")
        instance.delete()


# ---------- PATIENT APIs ----------

class PatientViewSet(CreatorOnlyMixin, viewsets.ModelViewSet):
    serializer_class = PatientSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Patient.objects.filter(
            created_by=self.request.user
        ).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------- DOCTOR APIs ----------

class DoctorViewSet(CreatorOnlyMixin, viewsets.ModelViewSet):
    serializer_class = DoctorSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Doctor.objects.all().order_by("name")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------- PATIENT-DOCTOR MAPPING APIs ----------

class MappingListCreateView(generics.ListCreateAPIView):
    serializer_class = MappingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PatientDoctorMapping.objects.filter(
            patient__created_by=self.request.user
        ).select_related("patient", "doctor")

    def perform_create(self, serializer):
        patient = serializer.validated_data["patient"]

        if patient.created_by_id != self.request.user.id:
            raise PermissionDenied(
                "You can only assign doctors to your patients."
            )

        serializer.save(created_by=self.request.user)


class MappingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        patient = Patient.objects.filter(
            id=patient_id,
            created_by=request.user,
        ).first()

        if patient is None:
            return Response(
                {"error": "Patient not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        mappings = PatientDoctorMapping.objects.filter(
            patient=patient
        ).select_related("doctor")

        return Response(MappingSerializer(
            mappings,
            many=True,
            context={"request": request},
        ).data)

    def delete(self, request, patient_id):
        mapping = PatientDoctorMapping.objects.filter(
            id=patient_id,
            patient__created_by=request.user,
        ).first()

        if mapping is None:
            return Response(
                {"error": "Mapping not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        mapping.delete()
        return Response(
            {"message": "Doctor assignment removed."},
            status=status.HTTP_204_NO_CONTENT,
        )
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from paperless_personal_ai.crypto import PersonalAIKeyServiceError
from paperless_personal_ai.serializers import (
    PersonalAIProfileSerializer,
    PersonalAIUpdateSerializer,
)
from paperless_personal_ai.service import (
    PersonalAIConnectionError,
    PersonalAIEligibilityError,
    delete_personal_ai_credential,
    disable_personal_ai,
    safe_profile_payload,
    test_personal_ai_connection,
    update_personal_ai_profile,
)


class PersonalAIProfileView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PersonalAIUpdateSerializer

    @extend_schema(responses={200: PersonalAIProfileSerializer})
    def get(self, request, *args, **kwargs):
        try:
            payload = safe_profile_payload(request.user)
        except PersonalAIEligibilityError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(PersonalAIProfileSerializer(payload).data)

    @extend_schema(
        request=PersonalAIUpdateSerializer,
        responses={200: PersonalAIProfileSerializer},
    )
    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        values.pop("provider", None)
        try:
            payload = update_personal_ai_profile(request.user, **values)
        except PersonalAIEligibilityError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PersonalAIKeyServiceError:
            return Response(
                {"detail": "The personal AI key service is unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(PersonalAIProfileSerializer(payload).data)

    @extend_schema(responses={200: PersonalAIProfileSerializer})
    def delete(self, request, *args, **kwargs):
        try:
            payload = delete_personal_ai_credential(request.user)
        except PersonalAIEligibilityError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(PersonalAIProfileSerializer(payload).data)


class PersonalAIDisableView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: PersonalAIProfileSerializer})
    def post(self, request, *args, **kwargs):
        try:
            payload = disable_personal_ai(request.user)
        except PersonalAIEligibilityError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(PersonalAIProfileSerializer(payload).data)


class PersonalAIConnectionTestView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: PersonalAIProfileSerializer})
    def post(self, request, *args, **kwargs):
        try:
            payload = test_personal_ai_connection(request.user)
        except PersonalAIEligibilityError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except PersonalAIKeyServiceError:
            return Response(
                {"detail": "The personal AI key service is unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except PersonalAIConnectionError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(PersonalAIProfileSerializer(payload).data)

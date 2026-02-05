
# api/urls.py

from django.urls import path 
from .views import RegisterView, LoginView, FirebaseLoginView, FirebaseResetPassword, CheckPhoneExists, MeView, ForgotPasswordView

urlpatterns = [
    path("auth/signup/", RegisterView.as_view(), name="signup"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/firebase-login/", FirebaseLoginView.as_view(), name="firebase_login"),
    path("auth/reset-password/", FirebaseResetPassword.as_view(), name="reset_password"),
    path("auth/check-phone/", CheckPhoneExists.as_view(), name="check_phone"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/forgot-password/", ForgotPasswordView.as_view()),

    #path("auth/reset-password/", views.firebase_reset_password),

]


# urlpatterns = [
#     path('auth/signup/', RegisterView.as_view(), name='auth-signup'),

# ]





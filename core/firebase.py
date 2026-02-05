
# core/firebase.py

# import os
# import firebase_admin
# from firebase_admin import credentials, auth as firebase_auth

# SERVICE_ACCOUNT_PATH = os.getenv("FIREBASE_SA_PATH", "serviceAccountKey.json")

# if not firebase_admin._apps:
#     cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
#     firebase_admin.initialize_app(cred)

# def verify_firebase_token(id_token):
#     # raises exceptions on invalid/expired token
#     decoded = firebase_auth.verify_id_token(id_token)
#     return decoded

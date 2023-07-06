from django.urls import path
from rest_framework import routers

from core import views

router = routers.SimpleRouter()
router.register(r"accounts", views.AccountViewSet, "core-account")
router.register(r"daos", views.DaoViewSet, "core-dao")
router.register(r"assets", views.AssetViewSet, "core-asset")
router.register(r"asset-holdings", views.AssetHoldingViewSet, "core-asset-holding")
router.register(r"proposals", views.ProposalViewSet, "core-proposal")
router.register(r"multisig", views.MultiSignatureView, basename="core-multi-signature")

urlpatterns = router.urls + [
    path(r"", views.welcome, name="core-welcome"),
    path(r"stats/", views.stats, name="core-stats"),
    path(r"config/", views.config, name="core-config"),
    path(r"daos/<str:id>/multisig/", views.MultiSignatureView.as_view({"post": "create"}), name="core-multsig-create"),
]

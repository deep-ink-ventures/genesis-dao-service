from rest_framework import routers

from core import views

router = routers.SimpleRouter()
router.register(r"stats", views.StatsView, "core-stats")
router.register(r"accounts", views.AccountViewSet, "core-account")
router.register(r"daos", views.DaoViewSet, "core-dao")
router.register(r"assets", views.AssetViewSet, "core-asset")
router.register(r"asset-holdings", views.AssetHoldingViewSet, "core-asset-holding")

urlpatterns = router.urls

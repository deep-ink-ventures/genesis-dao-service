from logging import getLogger

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin

request_logger = getLogger("requests")


class HealthCheckMiddleware(MiddlewareMixin):
    @staticmethod
    def process_request(request):
        if request.META["PATH_INFO"] == "/ping/":
            return HttpResponse("pong")


class BlockMetadataMiddleware(MiddlewareMixin):
    @staticmethod
    def process_response(_request, response):
        if current_block := cache.get("current_block"):
            block_number, block_hash = current_block
            response.headers["Block-Number"] = block_number
            response.headers["Block-Hash"] = block_hash
        return response


class RequestLoggingMiddleware(MiddlewareMixin):
    @staticmethod
    def process_response(request, response):
        if settings.TESTING:
            return response
        client = ":".join(str(_) for _ in request.scope.get("client", [])) if hasattr(request, "scope") else None
        request_logger.info(f"{request.scheme} {request.method} {request.path} [{client}] {response.status_code}")
        return response

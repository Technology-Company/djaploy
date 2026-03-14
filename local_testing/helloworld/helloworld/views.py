from django.http import JsonResponse


def hello(request):
    return JsonResponse({
        "message": "Hello from djaploy!",
        "status": "deployed",
    })


def health(request):
    return JsonResponse({"status": "ok"})

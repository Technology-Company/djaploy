from django.http import JsonResponse


def hello(request):
    return JsonResponse({
        "message": "Template-based deploy!",
        "status": "deployed",
        "version": "1.3.0",
    })


def health(request):
    return JsonResponse({"status": "ok"})

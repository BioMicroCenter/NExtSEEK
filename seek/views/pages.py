"""Static informational pages."""

from django.shortcuts import render

def getting_started(request):
    """Tutorials / Getting Started landing page. Static content."""
    return render(request, "help/getting_started.html")

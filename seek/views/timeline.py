"""The NHP timeline endpoints."""

from django.http import FileResponse
from rest_framework.response import Response
from rest_framework.decorators import api_view
from ..timeline.services.timeline_service import get_event_data
from ..timeline.services.nhp_service import get_timeline_data
import io
import logging
import secrets
from ..timeline.services.timeline_service import run_All
from ..timeline.services.nhp_service import save_nhp_data
from ..timeline.services.nhp_service import save_nhp_info_to_json
from rest_framework import status
from ..models import Samples
from .samples import _callerProjectIds, _visibleSampleIds

logger = logging.getLogger(__name__)


def _server_error(where, exc):
    """A 500 that carries a reference, never the exception text (it can name the database host or a query). The same
    reference is logged with the exception, so support can find it."""
    ref = secrets.token_hex(3)
    logger.exception("%s failed (ref %s)", where, ref, exc_info=exc)
    return Response({"detail": f"{where} failed (ref {ref})"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# What every timeline route answers for an NHP the caller may not see: the same as for one that does not exist.
NHP_NOT_FOUND = "NHP data not found"


def _nhpVisible(request, nhp_name, project_ids):
    """May the caller read this NHP's timeline? A superuser reads every NHP; anyone else only an NHP (a sample, by its
    uuid) in one of their projects, the rule of the sample page. Fails closed, as ``_callerProjectIds`` does."""
    if project_ids is None:
        return True
    sample_ids = list(Samples.objects.filter(uuid=nhp_name).values_list("id", flat=True))
    return bool(_visibleSampleIds(sample_ids, project_ids))


@api_view(['GET'])
def nhp_info(request, nhp_name):
    project_ids = _callerProjectIds(request, "NHP timeline")
    if not _nhpVisible(request, nhp_name, project_ids):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        nhp_info = save_nhp_info_to_json(nhp_name, project_ids)
        if nhp_info:
            return Response(nhp_info, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "NHP Info not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return _server_error("NHP info", e)

@api_view(['GET'])
def fetch_event_data(request, nhp_name: str, event_type: str, date: str):
    project_ids = _callerProjectIds(request, "NHP timeline")
    if not _nhpVisible(request, nhp_name, project_ids):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        event_data =get_event_data(nhp_name, event_type, date, project_ids)
        if event_data:
            return Response(event_data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Event data not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return _server_error("Event data", e)

@api_view(['GET'])
def get_nhp_data(request, nhp_name: str):
    project_ids = _callerProjectIds(request, "NHP timeline")
    if not _nhpVisible(request, nhp_name, project_ids):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        timeline_data = run_All(nhp_name, project_ids)
        if timeline_data:
            return Response(timeline_data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Event Data not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return _server_error("Timeline data", e)

@api_view(['GET'])
def download_nhp_data(request, nhp_name: str):
    project_ids = _callerProjectIds(request, "NHP timeline")
    if not _nhpVisible(request, nhp_name, project_ids):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        timeline_data = get_timeline_data(nhp_name, project_ids)
        if not timeline_data:
            return Response({"detail": "NHP data not found"}, status=status.HTTP_404_NOT_FOUND)
        
        # Convert to Excel
        excel_data = save_nhp_data(timeline_data)
        
        # Create a streaming response
        response = FileResponse(
            io.BytesIO(excel_data),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            filename=f"{nhp_name}_data.xlsx"
        )
        return response
    except Exception as e:
        return _server_error("NHP download", e)

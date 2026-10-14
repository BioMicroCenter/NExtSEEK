"""The NHP timeline endpoints."""

from django.http import FileResponse
from rest_framework.response import Response
from rest_framework.decorators import api_view
from ..timeline.services.timeline_service import get_event_data
from ..timeline.services.nhp_service import get_timeline_data
import io
from ..timeline.services.timeline_service import run_All
from ..timeline.services.nhp_service import save_nhp_data
from ..timeline.services.nhp_service import save_nhp_info_to_json
from rest_framework import status
from ..models import Samples
from .samples import _callerProjectIds, _visibleSampleIds

# What every timeline route answers for an NHP the caller may not see: the same as for one that does not exist.
NHP_NOT_FOUND = "NHP data not found"


def _nhpVisible(request, nhp_name):
    """May the caller read this NHP's timeline? A superuser reads every NHP; anyone else only an NHP (a sample, by its
    uuid) in one of their projects, the rule of the sample page. Fails closed, as ``_callerProjectIds`` does."""
    project_ids = _callerProjectIds(request, "NHP timeline")
    if project_ids is None:
        return True
    sample_ids = list(Samples.objects.filter(uuid=nhp_name).values_list("id", flat=True))
    return bool(_visibleSampleIds(sample_ids, project_ids))


@api_view(['GET'])
def nhp_info(request, nhp_name):
    if not _nhpVisible(request, nhp_name):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        nhp_info = save_nhp_info_to_json(nhp_name)
        if nhp_info:
            return Response(nhp_info, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "NHP Info not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def fetch_event_data(request, nhp_name: str, event_type: str, date: str):
    if not nhp_name:
        raise HTTPException(status_code=404, detail="NHP data not found")
    if not _nhpVisible(request, nhp_name):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        event_data =get_event_data(nhp_name, event_type, date)
        if event_data:
            return Response(event_data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Event data not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def get_nhp_data(request, nhp_name: str):
    if not _nhpVisible(request, nhp_name):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        timeline_data = run_All(nhp_name)
        if timeline_data:
            return Response(timeline_data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Event Data not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def download_nhp_data(request, nhp_name: str):
    if not _nhpVisible(request, nhp_name):
        return Response({"detail": NHP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    try:
        timeline_data = get_timeline_data(nhp_name)
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
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

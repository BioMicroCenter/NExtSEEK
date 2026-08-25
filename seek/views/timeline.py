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

@api_view(['GET'])
def nhp_info(request, nhp_name):
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

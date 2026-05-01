from seek.seekdb import SeekDB
import nextseek_api.seek_api_helpers as seek_api


# /sops
def example_endpoint_to_list_sops(request):
    # Get current logged in user
    auth = seek_api.get_current_logged_in_user(request)

    # Use seek_api to list all sops
    # available to current user
    sops = seek_api.list_sops(auth)
    print(sops)


# /sops/{id}
def example_endpoint_to_fetch_sop(request, id):
    # Get current logged in user
    auth = seek_api.get_current_logged_in_user(request)

    # Use seek_api to get info of sop
    # with id 'id'
    sop = seek_api.get_sop(auth, id)
    print(sop)

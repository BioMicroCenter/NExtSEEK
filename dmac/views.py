from django.shortcuts import render
from django.http import HttpResponseRedirect
from django.conf import settings

import os
from subprocess import call

import logging
logging.basicConfig(
        filename="dmac.logs",
        filemode='a',
        format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
        level=logging.DEBUG)
logger = logging.getLogger(__name__)

from seek.seekdb import SeekDB
from dmac.dbtable import DBtable

DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'

report = {}
def __process(request, operation):
    dbtable = DBtable("DEFAULT")
    return dbtable.process(request, operation) 

def retrieve(request):
    return __process(request, "retrieve") 

def upload(request):
    return __process(request, "upload") 

def download(request):
    return __process(request, "download") 

def save(request):
    return __process(request, "save")

def delete(request):
    return __process(request, "delete")

def login_seek(request):
    """Render the login page. Signing in happens at SEEK (#16, sub-project 5).

    The POST branch is gone, and with it ``userSynchronization``. What they did
    was validate a username and password against SEEK, mirror that password into
    the Django ``auth_user`` row so ``request.user`` and ``@login_required``
    would work, and stash the plaintext password in the session for every later
    SEEK call to reuse. All three are exactly what this project set out to
    remove.

    This view stays because it is ``LOGIN_URL``: ``@login_required`` sends
    unauthenticated users here, and the page now offers "Log in with SEEK" and
    nothing else. ``seek/oauth/views.py`` does the actual work.

    A POST here is no longer meaningful -- there is no form to submit -- but it
    renders rather than 405s, so a stale bookmarked form or a cached page gets
    the login screen instead of an error.
    """
    return render(request, 'login.html')


def logout_seek(request):
    # Revoke the SEEK token first, while request.user is still resolvable. A
    # no-op unless SEEK_OAUTH_REVOKE_ON_LOGOUT is on, and it never raises:
    # logging out is a local act and must succeed whether or not SEEK is up.
    # Imported here rather than at module scope to keep dmac.views free of a
    # dependency on the OAuth package during app loading.
    from seek.oauth.views import revoke_on_logout
    revoke_on_logout(getattr(request, "user", None))

    # Revoke the NExtSEEK API token too (#16, sub-project 3). A DRF token does
    # not expire on its own, so logout is what bounds its life; leaving it
    # behind would hand out an indefinite bearer credential every time someone
    # signed in. Also never raises.
    from nextseek_api.local_tokens import revoke_for
    revoke_for(getattr(request, "user", None))

    if request.session.get('username') is not None:
        call(["rm", "-r", request.session.get('username')])
        request.session.flush()
    return HttpResponseRedirect(reverse('index'))
    
# login_full was here: an unrouted duplicate of the password login form that
# also wrote request.session['password']. Deleted with the rest of the password
# path (#16, sub-project 5); nothing referenced it.


def index(request):
    # A `login(request)` call sat here, guarded on an anonymous POST. It could
    # only ever raise -- django.contrib.auth.login takes (request, user) and was
    # given one argument -- so the branch was a latent 500, not a login. It went
    # with the password path (#16, sub-project 5): there is nothing for it to do
    # now, and the block immediately below already renders the login page for a
    # request with no session username, which is what it was reaching for.
    if (
        request.session.get('username') is None or
        request.session.get('username') == ""
    ):
        err = ""
        return render(request, 'login.html', context={'error': err})
    else:
        if not os.path.isdir(request.session.get('username')):
            call(["mkdir", request.session.get('username')])
        if request.POST.get('inv') is not None:
            investigation = request.POST.get('inv')
        else:
            investigation = ""
            
        username = request.session.get('username')
        storage = settings.SEEK_URL
        virtuoso = settings.VIRTUOSO_JS_URL
        server = request.session.get('server')
        # SeekDB(storage, username, password) used to be built here from the
        # session password and then immediately replaced by getSeekLogin's own
        # client. The construction was always redundant; with the password gone
        # (#16, sub-project 5) it is also impossible, so only the call remains.
        seekdb = SeekDB(None, None, None)
        user_seek = seekdb.getSeekLogin(request)
        if user_seek['status']:
            userinfo_seek = user_seek['userdata']
        else:
            userinfo_seek = None
        
        investigations,folders = seekdb.get_investigations_folders(investigation)
        return render(
            request, 'seek_login.html',
            # 'password' was rendered into this context. It is gone with the
            # session password, and the template it targets does not exist.
            context={'user': username, 'username': username,
                     'server': server,
                     'storage': storage,
                     'storagetype': request.session.get('storage_type'),
                     'virtuoso_url': virtuoso,
                     'investigations': investigations,
                     'studies': folders,
                     'inv': investigation
            }
        )    
    
def signup_seek(request):
    """Hand account creation off to SEEK, which owns the user records.

    Target is the *browser-reachable* SEEK base URL (SEEK_PUBLIC_URL), never the
    internal docker hostname SEEK_URL (http://seek:3000) — that name only
    resolves inside the compose network, not in the user's browser. Falls back
    to SEEK_URL only so a misconfigured host still redirects somewhere
    inspectable instead of looping back onto this view.
    """
    base = (
        getattr(settings, "SEEK_PUBLIC_URL", "")
        or getattr(settings, "SEEK_URL", "")
    )
    return HttpResponseRedirect(base.rstrip("/") + "/signup")




def home(request):
    """
    Home dashboard. Passes counts + a 'recent samples' list to
    themes/NextSeek/templates/index.html. Each block is in its own try/
    except so any one model lookup failing doesn't blank the whole page.
    """
    from datetime import timedelta
    from django.utils import timezone

    context = {
        "total_samples_count": 0,
        "total_projects_count": 0,
        "total_data_files_count": 0,
        "samples_delta_week": 0,
        "data_files_delta_week": 0,
        "projects_user_count": 0,  # v1: not yet wired — needs User<->People<->Projects bridge
        "recent_samples": [],
    }

    week_ago = timezone.now() - timedelta(days=7)

    try:
        from seek.models import Samples
        context["total_samples_count"] = Samples.objects.count()
        context["samples_delta_week"] = Samples.objects.filter(created_at__gte=week_ago).count()
        # Samples has `title` + `created_at`; no native UID or direct project FK
        # (project link goes through the Projects_samples junction). For v1 we
        # surface id + title + created_at and let the template render a stub
        # for the project column.
        context["recent_samples"] = list(
            Samples.objects.order_by("-id").values("id", "title", "created_at")[:4]
        )
    except Exception:
        pass

    try:
        from seek.models import Projects
        context["total_projects_count"] = Projects.objects.count()
    except Exception:
        pass

    try:
        from seek.models import Data_files
        context["total_data_files_count"] = Data_files.objects.count()
        context["data_files_delta_week"] = Data_files.objects.filter(created_at__gte=week_ago).count()
    except Exception:
        pass

    return render(request, "index.html", context)

from django.shortcuts import redirect, render
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth.models import User
from django import forms
from django.db.models import Q
from django.db import IntegrityError, transaction
from django.contrib.auth import authenticate, login
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from mezzanine.core.views import server_error

import simplejson
import sys
import requests
import datetime
import json
import os

import logging
logging.basicConfig(
        filename="dmac.logs",
        filemode='a',
        format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
        level=logging.DEBUG)
logger = logging.getLogger(__name__)

from seek.seekdb import SeekDB
from dmac.session_login import store_seek_password
from nextseek_api.seek_link import SeekPersonLink
from seek.models import People
from dmac.dbtable import DBtable

DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'

report = {}
def api_server_error(request):
    """handler500. Under /nextseek_api/ an unhandled error answers JSON, and one that comes from SEEK not answering
    says so, with a warning that a write may have happened anyway. The site keeps Mezzanine's page. Django still
    logs the traceback to django.log after this returns."""
    if not request.path.startswith("/nextseek_api/"):
        return server_error(request)
    exc = sys.exc_info()[1]
    if isinstance(exc, requests.RequestException):
        timed_out = isinstance(exc, requests.ReadTimeout)
        detail = "SEEK did not answer in time." if timed_out else "SEEK could not be reached."
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            detail += (" The change may still have been made: look the record up before you send it again.")
        body, status = {"errors": [{"title": "SEEK did not answer", "detail": detail}]}, 504 if timed_out else 502
    else:
        body, status = {"errors": [{"title": "Server error"}]}, 500
    return HttpResponse(json.dumps(body).encode(), status=status, content_type="application/json")


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

NOT_LINKED_MSG = "This NExtSEEK account is not linked to your SEEK login; ask an admin."


def userSynchronization(user_seek):
    """Create or update the NExtSEEK account for a SEEK login, only through its SeekPersonLink.

    Returns (status, msg). An existing account is touched only when it is linked to this SEEK person; it never gains
    staff or superuser rights here.
    """
    username = user_seek['username']
    password = user_seek['password']
    try:
        person_id = user_seek['user_id']
        person = People.objects.get(id__exact=person_id)
    except Exception:
        msg = "Error: Login user not found in SEEK people table, ask admin for help."
        logger.error(msg)
        return 0, msg

    user = User.objects.filter(username__exact=username).first()
    if user is None:
        logger.debug("Register new SEEK user in NExtSEEK")
        try:
            attributes = user_seek['userdata']['attributes']
            with transaction.atomic():
                user = User.objects.create_user(username, person.email, password)
                user.last_name = attributes['last_name']
                user.first_name = attributes['first_name']
                user.is_active = 1
                user.save()
                SeekPersonLink.objects.create(user=user, person_id=person_id)
        except IntegrityError:
            logger.warning("SEEK person already linked to another account; refused new account %s", username)
            return 0, NOT_LINKED_MSG
        except Exception:
            msg = "Error: Registration of SEEK user failed, ask admin for help."
            logger.error(msg)
            return 0, msg
        return 1, "Registration of SEEK user successful."

    link = SeekPersonLink.objects.filter(user=user).first()
    if link is None or link.person_id != person_id:
        logger.warning("SEEK login refused: account %s is not linked to this SEEK person", username)
        return 0, NOT_LINKED_MSG
    try:
        attributes = user_seek['userdata']['attributes']
        user.email = person.email
        user.set_password(password)
        user.last_name = attributes['last_name']
        user.first_name = attributes['first_name']
        user.is_active = 1
        user.save()
    except Exception:
        msg = "Error: Update of SEEK user failed, ask admin for help."
        logger.error(msg)
        return 0, msg
    return 1, "Update of SEEK user successful."

def _safe_next(request):
    """The ``next`` page to return to after signing in: a path on this site, else home.

    Relative paths only (no allowed hosts): behind nginx every request's Host is
    the upstream name, so an absolute URL naming it would leave the site.
    """
    next_url = request.GET.get('next', '')
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts=set()):
        return next_url
    return '/'


def login_seek(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, fromLoginForm=True)  # body credentials, once SEEK accepts them

    # Never log user_seek itself: it carries the plaintext SEEK password, and this
    # line put every user's password into `docker logs` on production.
    logger.info(
        f"User trying to log in: username={user_seek.get('username')!r} "
        f"storage={user_seek.get('storage')!r} status={user_seek.get('status')}"
    )
    logger.debug(f"User request: {request}")
    status = user_seek['status']
    err = user_seek['err']
    username = user_seek['username']
    password = user_seek['password']
    if request.method == 'POST':
        logger.debug(f"Request method: {request.method}")
        if user_seek['status']:
            logger.debug(f"User was able to log in")
            request.session['server'] = user_seek['server']
            request.session['storage_type'] = user_seek['storagetype']
            request.session['storage'] = user_seek['storage']
            request.session['username'] = user_seek['username']
            store_seek_password(request.session, user_seek['username'], user_seek['password'])
            if user_seek['noexpire'] == "yes":
                request.session.set_expiry(0)
            else:
                request.session.set_expiry(43200)
                
            username = user_seek['username']
            password = user_seek['password']
            user = authenticate(username=username, password=password)
            logger.debug(f"Authenticated Django user: {user}")
            if user is not None:
                login(request, user)
            else:
                statusi, msg = userSynchronization(user_seek)
                if statusi:
                    user = authenticate(username=username, password=password)
                    if user is not None:
                        login(request, user)
                    else:    
                        request.session.flush()
                        return render(request, 'login.html', {'error': "Error after user registration, ask admin for help."})
                else:        
                    request.session.flush()
                    return render(request, 'login.html', {'error': msg})
            
            return HttpResponseRedirect(_safe_next(request))
        else:
            logger.debug("SEEK authentication failed, re-login")
            request.session.flush()
            return render(request, 'login.html', {'error': user_seek['err']})
    else:
        msg = 'Warning: Not a http POST operation.'
        logger.debug(msg)
        
    return render(request, 'login.html')

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




def _home_projects(request, limit=8):
    """Up to `limit` projects the caller may see, for the dashboard's project
    strip.

    Scoped to membership exactly like the projects list (seek/views/projects.py):
    a superuser sees all projects, a member sees only their own, and anyone the
    scoping can't resolve (anonymous, a SEEK-login failure) sees none. Without
    this the home page would surface project titles the projects page hides.

    Each card carries the project's SEEK avatar as its logo when one exists.
    Fails soft to [] so the dashboard never 500s, matching the home view's
    per-block try/except discipline.
    """
    try:
        from django.conf import settings as _settings
        from seek.decorators import verifySuperUser
        from seek.models import Projects
        from seek.seekdb import SeekDB

        if verifySuperUser(request) == 1:
            qs = Projects.objects.order_by("id").values("id", "title", "avatar_id")
        else:
            seekdb = SeekDB(None, None, None)
            if not seekdb.getSeekLogin(request, False).get("status"):
                return []
            data = seekdb.getCurrentUser() or {}
            ids = [int(p["id"]) for p in
                   data.get("data", {}).get("relationships", {})
                       .get("projects", {}).get("data", []) if p.get("id")]
            if not ids:
                return []
            qs = Projects.objects.filter(id__in=ids).order_by("id").values(
                "id", "title", "avatar_id")

        base = getattr(_settings, "SEEK_PUBLIC_URL", "") or ""
        out = []
        for r in list(qs)[:limit]:
            avatar = r.get("avatar_id")
            logo = f"{base}/assets/avatar-images/{avatar}-500.png" if avatar and base else None
            out.append({"id": r["id"], "title": r["title"], "logo": logo})
        return out
    except Exception:
        return []


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
        "home_projects": _home_projects(request),
    }

    week_ago = timezone.now() - timedelta(days=7)

    # Logged-out callers get the zero defaults: no counts, no sample rows.
    if not request.user.is_authenticated:
        return render(request, "index.html", context)

    try:
        from seek.models import Projects_samples, Samples
        from nextseek_api.graph_search.scope import ScopeUnavailable, resolve_scope  # function-level: no import cycle
        context["total_samples_count"] = Samples.objects.count()
        context["samples_delta_week"] = Samples.objects.filter(created_at__gte=week_ago).count()
        # Recent rows are limited to the caller's projects (a superuser sees all); an unresolved scope shows none.
        try:
            scope = resolve_scope(request.user)
        except ScopeUnavailable:
            scope = None
        if scope is not None:
            rows = Samples.objects if scope.is_admin else Samples.objects.filter(
                id__in=Projects_samples.objects.filter(project_id__in=scope.project_ids).values("sample_id"))
            context["recent_samples"] = list(rows.order_by("-id").values("id", "title", "created_at")[:4])
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
